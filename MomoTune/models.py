"""MomoTune 领域模型。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Song:
    """统一两种网易云 API 返回结构后的歌曲信息。"""

    song_id: str
    name: str = "未知曲目"
    artist: str = "未知歌手"
    album: str = ""
    pic_url: str | None = None
    duration_ms: int | None = None


class SourceError(RuntimeError):
    """可直接展示给用户的音源错误。"""
