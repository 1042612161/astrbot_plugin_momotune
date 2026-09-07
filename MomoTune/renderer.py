"""使用 pytakumi 渲染 MomoTune 搜索及播放卡片。"""

from __future__ import annotations

import asyncio
import base64
import gc
from html import escape
import inspect
from pathlib import Path

import httpx

from .config import RendererConfig
from .models import Song

try:
    from pytakumi import Renderer, html_to_pic
except ImportError:  # pragma: no cover - AstrBot 按 requirements.txt 安装依赖
    Renderer = None
    html_to_pic = None

ASSETS = Path(__file__).resolve().parent / "assets"
FONT_NAME = "MomoTuneWenKai"
PLACEHOLDER = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' "
    "width='160' height='160'%3E%3Crect width='100%25' height='100%25' "
    "rx='24' fill='%23ffd6e7'/%3E%3Ctext x='50%25' y='58%25' "
    "text-anchor='middle' font-size='64' fill='%23ff6b9a'%3E♪%3C/text%3E"
    "%3C/svg%3E"
)


def _duration(value: int | None) -> str:
    if value is None or value < 0:
        return "--:--"
    seconds = value // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


async def _cover_data_uri(
    client: httpx.AsyncClient,
    url: str | None,
    max_cover_bytes: int,
) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return PLACEHOLDER
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return PLACEHOLDER
    if response.status_code >= 400 or len(response.content) > max_cover_bytes:
        return PLACEHOLDER
    content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    encoded = base64.b64encode(response.content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


class RendererManager:
    """管理插件级 pytakumi Renderer，并串行化原生渲染调用。"""

    def __init__(self, config: RendererConfig) -> None:
        self._renderer = None
        self._lock = asyncio.Lock()
        self._closed = False
        self.template_path = ASSETS / "templates" / config.template_filename
        self.font_path = ASSETS / "fonts" / config.font_filename
        self.max_cover_bytes = config.max_cover_bytes

    def initialize(self) -> None:
        """创建 Renderer 并只注册一次插件字体。"""

        if self._closed:
            raise RuntimeError("pytakumi Renderer 已关闭")
        if self._renderer is not None:
            return
        if Renderer is None or html_to_pic is None:
            raise RuntimeError("未安装 pytakumi，请安装 requirements.txt")
        self._renderer = Renderer()
        if self.font_path.is_file():
            self._renderer.register_font(
                self.font_path.read_bytes(), name=FONT_NAME
            )

    async def render(self, html: str) -> bytes:
        """使用同一个 Renderer 输出 PNG，避免并发访问原生对象。"""

        async with self._lock:
            self.initialize()
            return await asyncio.to_thread(
                html_to_pic,
                html,
                width=680,
                renderer=self._renderer,
                font_families=[FONT_NAME],
                lang="zh",
            )

    async def close(self) -> None:
        """关闭 Renderer（若版本提供接口），并释放原生对象引用。"""

        async with self._lock:
            renderer = self._renderer
            self._renderer = None
            self._closed = True
            if renderer is not None:
                close = getattr(renderer, "close", None)
                if callable(close):
                    result = close()
                    if inspect.isawaitable(result):
                        await result
                del renderer
            # pytakumi 0.1.x 没有公开 close()，通过释放最后引用交给原生对象析构。
            gc.collect()


def _song_row(song: Song, cover: str, index: int) -> str:
    return (
        f'<article class="song-row"><span class="index">{index}</span>'
        f'<img class="cover" src="{cover}" alt="" />'
        '<div class="song-info">'
        f'<div class="song-name">{escape(song.name)}</div>'
        f'<div class="song-artist">{escape(song.artist)}</div>'
        f'<div class="song-album">{escape(song.album or "单曲")}</div>'
        "</div>"
        f'<span class="duration">{_duration(song.duration_ms)}</span>'
        '<span class="source">NCM</span></article>'
    )


async def render_card(
    songs: list[Song],
    title: str,
    hint: str,
    renderer_manager: RendererManager,
    proxy: str = "",
) -> bytes:
    """下载封面并用固定模板输出 PNG 字节。"""

    if not renderer_manager.template_path.is_file():
        raise RuntimeError(
            f"卡片模板不存在：{renderer_manager.template_path.name}"
        )
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        proxy=proxy.strip() or None,
    ) as client:
        covers = await asyncio.gather(
            *(
                _cover_data_uri(
                    client, song.pic_url, renderer_manager.max_cover_bytes
                )
                for song in songs
            )
        )
    rows = "".join(
        _song_row(song, cover, index)
        for index, (song, cover) in enumerate(zip(songs, covers), 1)
    )
    html = renderer_manager.template_path.read_text(encoding="utf-8")
    html = html.replace("{{TITLE}}", escape(title))
    html = html.replace("{{HINT}}", escape(hint))
    html = html.replace("{{ROWS}}", rows)
    html = html.replace("{{HERO_COVER}}", covers[0] if covers else PLACEHOLDER)
    return await renderer_manager.render(html)
