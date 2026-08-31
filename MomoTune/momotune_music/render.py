"""MomoTune 搜索结果卡片渲染。"""

from __future__ import annotations

import asyncio
import base64
from html import escape
from pathlib import Path

import httpx

from gsuid_core.utils.html_render import _ensure_renderer, render_html_to_bytes

from .sources import Song

TEMPLATE_PATH = Path(__file__).resolve().parents[2] / "templates" / "search_list.html"
FONT_PATH = Path(__file__).resolve().parents[2] / "resources" / "fonts" / "LXGWWenKai-Regular.ttf"
FONT_NAME = "MomoTuneWenKai"
_MAX_COVER_BYTES = 2 * 1024 * 1024
_PLACEHOLDER = (
    "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='160' height='160'%3E"
    "%3Crect width='100%25' height='100%25' rx='24' fill='%23ffd6e7'/%3E"
    "%3Ctext x='50%25' y='58%25' text-anchor='middle' font-size='64' fill='%23ff6b9a'%3E♪%3C/text%3E%3C/svg%3E"
)


def _duration(value: int | None) -> str:
    if value is None or value < 0:
        return "--:--"
    seconds = value // 1000
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


async def _cover_data_uri(client: httpx.AsyncClient, url: str | None) -> str:
    if not url or not url.startswith(("http://", "https://")):
        return _PLACEHOLDER
    try:
        response = await client.get(url)
    except httpx.HTTPError:
        return _PLACEHOLDER
    if response.status_code >= 400 or len(response.content) > _MAX_COVER_BYTES:
        return _PLACEHOLDER
    content_type = response.headers.get("content-type", "image/jpeg").split(";", 1)[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"
    encoded = base64.b64encode(response.content).decode("ascii")
    return f"data:{content_type};base64,{encoded}"


def _row(song: Song, cover: str, index: int | None) -> str:
    number = f'<span class="index">{index}</span>' if index is not None else ""
    source = f'<span class="source">{escape(song.source.upper())}</span>'
    return (
        '<article class="song-row">'
        f"{number}"
        f'<img class="cover" src="{cover}" alt="" />'
        '<div class="song-info">'
        f'<div class="song-name">{escape(song.name)}</div>'
        f'<div class="song-artist">{escape(song.artist or "未知歌手")}</div>'
        f'<div class="song-album">{escape(song.album or "单曲")}</div>'
        "</div>"
        f'<span class="duration">{_duration(song.duration_ms)}</span>'
        f"{source}"
        "</article>"
    )


async def render_song_card(
    songs: list[Song],
    *,
    title: str,
    hint: str,
) -> bytes:
    """渲染搜索列表或单曲信息卡片。"""
    if FONT_PATH.is_file():
        _ensure_renderer(extra_fonts=[(FONT_PATH.read_bytes(), FONT_NAME)])
    async with httpx.AsyncClient(timeout=8, follow_redirects=True) as client:
        covers = await asyncio.gather(*(_cover_data_uri(client, song.pic_url) for song in songs))
    rows = "".join(_row(song, cover, index) for index, (song, cover) in enumerate(zip(songs, covers), 1))
    html = TEMPLATE_PATH.read_text(encoding="utf-8")
    html = html.replace("{{TITLE}}", escape(title))
    html = html.replace("{{ROWS}}", rows)
    html = html.replace("{{HINT}}", escape(hint))
    html = html.replace("{{HERO_COVER}}", covers[0] if covers else _PLACEHOLDER)
    return await render_html_to_bytes(
        html,
        max_width=680,
        dpi=96,
        default_font_size=15,
        font_name=FONT_NAME,
        allow_refit=True,
        image_format="png",
        lang="zh",
        root_max_width=680,
    )
