"""NCM-plugin 双源 API 适配层（网易云 + 酷狗）。"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Literal

import httpx

from gsuid_core.logger import logger

MusicSource = Literal["ncm", "kugou"]
JsonObject = dict[str, object]

NCM: MusicSource = "ncm"
KUGOU: MusicSource = "kugou"
SOURCE_LABELS: dict[MusicSource, str] = {NCM: "网易云", KUGOU: "酷狗"}

HTTP_TIMEOUT = 12.0
DOWNLOAD_MAX_BYTES = 15 * 1024 * 1024
_NCM_TO_KUGOU_QUALITY: dict[str, str] = {
    "standard": "128",
    "higher": "320",
    "exhigh": "320",
    "lossless": "flac",
    "hires": "high",
    "jyeffect": "320",
    "sky": "high",
    "jymaster": "high",
}


@dataclass(frozen=True, slots=True)
class Song:
    """两个音乐源统一后的曲目结构。"""

    source: MusicSource
    song_id: str
    name: str = "未知曲目"
    artist: str = "未知歌手"
    album: str = ""
    pic_url: str | None = None
    duration_ms: int | None = None
    extra: dict[str, str] = field(default_factory=dict)


class SourceError(RuntimeError):
    """可直接提示用户的后端错误。"""


def _field(data: JsonObject, key: str) -> object | None:
    return data[key] if key in data else None


def _object(value: object, message: str) -> JsonObject:
    if not isinstance(value, dict):
        raise SourceError(message)
    return {str(key): item for key, item in value.items()}


def _text(value: object | None, fallback: str = "") -> str:
    if isinstance(value, str):
        value = value.strip()
        return value or fallback
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return fallback


def _integer(value: object | None) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _first(data: JsonObject, *keys: str) -> object | None:
    for key in keys:
        value = _field(data, key)
        if value not in (None, ""):
            return value
    return None


def _artist_names(value: object | None) -> str:
    if not isinstance(value, list):
        return ""
    names: list[str] = []
    for item in value:
        if isinstance(item, dict):
            obj = _object(item, "歌手数据格式异常。")
            name = _text(_field(obj, "name"))
            if name:
                names.append(name)
    return "/".join(names)


def _song(
    source: MusicSource,
    song_id: object,
    name: object | None,
    artist: object | None,
    album: object | None = None,
    pic_url: object | None = None,
    duration_ms: object | None = None,
    extra: dict[str, str] | None = None,
) -> Song:
    return Song(
        source=source,
        song_id=_text(song_id),
        name=_text(name, "未知曲目"),
        artist=_text(artist, "未知歌手"),
        album=_text(album),
        pic_url=_text(pic_url) or None,
        duration_ms=_integer(duration_ms),
        extra=extra or {},
    )


class BaseSource:
    name: MusicSource

    def __init__(self, base: str, cookie: str, quality: str) -> None:
        self.base = base.rstrip("/")
        self.cookie = cookie.strip()
        self.quality = quality

    @property
    def label(self) -> str:
        return SOURCE_LABELS[self.name]

    async def _get(self, path: str, **params: str | int) -> JsonObject:
        query = {key: str(value) for key, value in params.items() if value is not None}
        if self.cookie:
            query["cookie"] = self.cookie
        async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
            response = await client.get(f"{self.base}{path}", params=query)
        if response.status_code >= 400:
            raise SourceError(f"{self.label}接口暂时不可用（HTTP {response.status_code}）。")
        return _object(response.json(), f"{self.label}接口返回了非预期的数据结构。")

    async def search(self, keyword: str, limit: int) -> list[Song]:
        raise NotImplementedError

    async def play_url(self, song: Song) -> str | None:
        raise NotImplementedError

    async def detail(self, song_id: str) -> Song | None:
        return None


class NcmSource(BaseSource):
    name = NCM

    @staticmethod
    def _parse(raw: object) -> Song | None:
        if not isinstance(raw, dict):
            return None
        data = _object(raw, "网易云歌曲数据格式异常。")
        song_id = _field(data, "id")
        if song_id is None:
            return None
        artists = _first(data, "ar", "artists")
        artist = _artist_names(artists)
        album_value = _first(data, "al", "album")
        album = _object(album_value, "网易云专辑数据格式异常。") if isinstance(album_value, dict) else {}
        return _song(
            NCM,
            song_id,
            _field(data, "name"),
            artist,
            _field(album, "name"),
            _field(album, "picUrl"),
            _first(data, "dt", "duration"),
            {"fee": _text(_field(data, "fee"))},
        )

    async def search(self, keyword: str, limit: int) -> list[Song]:
        payload = await self._get("/cloudsearch", keywords=keyword, limit=limit)
        result_value = _field(payload, "result")
        result = _object(result_value, "网易云搜索结果格式异常。") if isinstance(result_value, dict) else {}
        songs = _field(result, "songs")
        if not isinstance(songs, list):
            return []
        return [song for raw in songs if (song := self._parse(raw)) is not None][:limit]

    async def detail(self, song_id: str) -> Song | None:
        payload = await self._get("/song/detail", ids=song_id)
        songs = _field(payload, "songs")
        if not isinstance(songs, list) or not songs:
            return None
        return self._parse(songs[0])

    async def play_url(self, song: Song) -> str | None:
        payload = await self._get("/song/url/v1", id=song.song_id, level=self.quality)
        data = _field(payload, "data")
        if not isinstance(data, list) or not data:
            return None
        first = _object(data[0], "网易云播放链接格式异常。") if isinstance(data[0], dict) else {}
        code = _integer(_field(first, "code"))
        if code is not None and code != 200:
            logger.debug(f"[MomoTune] 网易云 {song.song_id} 返回 code={code}")
            return None
        url = _text(_field(first, "url"))
        return url or None


class KugouSource(BaseSource):
    name = KUGOU

    def __init__(self, base: str, cookie: str, quality: str) -> None:
        super().__init__(base, cookie, quality)
        self._dfid: str | None = None
        self._dfid_lock = asyncio.Lock()

    @property
    def _kugou_quality(self) -> str:
        if self.quality in _NCM_TO_KUGOU_QUALITY:
            return _NCM_TO_KUGOU_QUALITY[self.quality]
        return "320"

    async def _ensure_dfid(self) -> None:
        if self._dfid:
            return
        async with self._dfid_lock:
            if self._dfid:
                return
            payload = await self._get("/register/dev")
            data_value = _field(payload, "data")
            if not isinstance(data_value, dict):
                return
            data = _object(data_value, "酷狗设备注册数据格式异常。")
            dfid = _text(_first(data, "dfid", "dfid_new"))
            if dfid:
                self._dfid = dfid

    @staticmethod
    def _clean(value: object | None) -> str:
        return _text(value).replace("<em>", "").replace("</em>", "").strip()

    @classmethod
    def _parse(cls, raw: object) -> Song | None:
        if not isinstance(raw, dict):
            return None
        data = _object(raw, "酷狗歌曲数据格式异常。")
        song_hash = _first(data, "FileHash", "hash", "Hash")
        if song_hash is None:
            return None
        seconds = _first(data, "Duration", "duration")
        pic = cls._clean(_first(data, "Image", "sizable_cover", "img"))
        if pic:
            pic = pic.replace("{size}", "240")
        extra: dict[str, str] = {}
        album_id = _text(_first(data, "AlbumID", "album_id"))
        album_audio_id = _text(_first(data, "album_audio_id", "AlbumAudioID", "mixsongid"))
        if album_id:
            extra["album_id"] = album_id
        if album_audio_id:
            extra["album_audio_id"] = album_audio_id
        return _song(
            KUGOU,
            song_hash,
            cls._clean(_first(data, "SongName", "OriSongName", "filename")),
            cls._clean(_first(data, "SingerName", "singername")),
            cls._clean(_first(data, "AlbumName", "albumname")),
            pic,
            (_integer(seconds) or 0) * 1000,
            extra,
        )

    async def search(self, keyword: str, limit: int) -> list[Song]:
        payload = await self._get("/search", keywords=keyword, pagesize=limit, type="song")
        if _integer(_field(payload, "error_code")) == 152:
            raise SourceError("酷狗接口需要登录凭据，请在 NCM-plugin 后端配置酷狗 cookie。")
        data_value = _field(payload, "data")
        data = _object(data_value, "酷狗搜索结果格式异常。") if isinstance(data_value, dict) else {}
        lists = _first(data, "lists", "info")
        if not isinstance(lists, list):
            return []
        return [song for raw in lists if (song := self._parse(raw)) is not None][:limit]

    async def play_url(self, song: Song) -> str | None:
        await self._ensure_dfid()
        payload = await self._get(
            "/song/url",
            hash=song.song_id,
            quality=self._kugou_quality,
            album_id=song.extra["album_id"] if "album_id" in song.extra else "",
            album_audio_id=(
                song.extra["album_audio_id"] if "album_audio_id" in song.extra else ""
            ),
        )
        for key in ("url", "backupUrl"):
            value = _field(payload, key)
            if isinstance(value, list) and value:
                return _text(value[0]) or None
            if isinstance(value, str) and value:
                return value
        data_value = _field(payload, "data")
        if isinstance(data_value, dict):
            data = _object(data_value, "酷狗播放链接格式异常。")
            for key in ("url", "play_url", "backupUrl"):
                value = _field(data, key)
                if isinstance(value, list) and value:
                    return _text(value[0]) or None
                if isinstance(value, str) and value:
                    return value
        return None


async def download(url: str) -> bytes:
    """下载音频，限制最大体积，避免异常响应耗尽内存。"""
    async with httpx.AsyncClient(timeout=HTTP_TIMEOUT, follow_redirects=True) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise SourceError(f"音频下载失败（HTTP {response.status_code}）。")
            chunks: list[bytes] = []
            total = 0
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > DOWNLOAD_MAX_BYTES:
                    raise SourceError("音频文件超过 15 MB 大小限制。")
                chunks.append(chunk)
            return b"".join(chunks)
