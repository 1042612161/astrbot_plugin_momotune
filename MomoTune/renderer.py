"""使用 pytakumi 渲染 MomoTune 搜索及播放卡片。"""

from __future__ import annotations

import asyncio
import base64
from html import escape
from pathlib import Path

import httpx

from .models import Song

try:
    from pytakumi import html_to_pic
except ImportError:  # pragma: no cover - AstrBot 按 requirements.txt 安装依赖
    html_to_pic = None

ASSETS = Path(__file__).resolve().parent / "assets"
TEMPLATE = ASSETS / "templates" / "search_list.html"
FONT = ASSETS / "fonts" / "LXGWWenKai-Regular.ttf"
FONT_NAME = "MomoTuneWenKai"
MAX_COVER_BYTES = 2 * 1024 * 1024
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


async def _cover_data_uri(client: httpx.AsyncClient, url: str | None) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return PLACEHOLDER
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return PLACEHOLDER
    if response.status_code >= 400 or len(response.content) > MAX_COVER_BYTES:
        return PLACEHOLDER
    content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    encoded = base64.b64encode(response.content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


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
    proxy: str = "",
) -> bytes:
    """下载封面并用固定模板输出 PNG 字节。"""

    if html_to_pic is None:
        raise RuntimeError("未安装 pytakumi，请安装 requirements.txt")
    async with httpx.AsyncClient(
        timeout=8,
        follow_redirects=True,
        proxy=proxy.strip() or None,
    ) as client:
        covers = await asyncio.gather(
            *(_cover_data_uri(client, song.pic_url) for song in songs)
        )
    rows = "".join(
        _song_row(song, cover, index)
        for index, (song, cover) in enumerate(zip(songs, covers), 1)
    )
    html = TEMPLATE.read_text(encoding="utf-8")
    html = html.replace("{{TITLE}}", escape(title))
    html = html.replace("{{HINT}}", escape(hint))
    html = html.replace("{{ROWS}}", rows)
    html = html.replace("{{HERO_COVER}}", covers[0] if covers else PLACEHOLDER)
    fonts = [{"data": FONT.read_bytes(), "name": FONT_NAME}] if FONT.is_file() else None
    return await asyncio.to_thread(
        html_to_pic,
        html,
        width=680,
        fonts=fonts,
        font_families=[FONT_NAME],
        lang="zh",
    )
