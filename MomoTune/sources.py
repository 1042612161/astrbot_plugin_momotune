"""网易云兼容 API、MetingAPI 与音频下载。"""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

import httpx

from .models import Song, SourceError

HTTP_TIMEOUT = 15.0
DOWNLOAD_MAX_BYTES = 15 * 1024 * 1024


def _text(value: object, fallback: str = "") -> str:
    if isinstance(value, str):
        return value.strip() or fallback
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    return fallback


def _integer(value: object) -> int | None:
    try:
        return int(value) if value is not None and not isinstance(value, bool) else None
    except (TypeError, ValueError):
        return None


def _artists(value: object) -> str:
    if isinstance(value, list):
        return "/".join(
            _text(item.get("name"))
            for item in value
            if isinstance(item, dict) and _text(item.get("name"))
        )
    return _text(value)


def _quality_br(quality: str) -> int:
    return {
        "standard": 128,
        "higher": 320,
        "exhigh": 320,
        "lossless": 2000,
        "hires": 2000,
        "jyeffect": 320,
        "sky": 2000,
        "jymaster": 2000,
    }.get(quality, 320)


class NcmClient:
    """把 MomoTune 兼容 API 和 MetingAPI 统一为同一歌曲接口。"""

    def __init__(self, base: str, cookie: str, quality: str, proxy: str) -> None:
        self.base = base.rstrip("/")
        self.cookie = cookie.strip()
        self.quality = quality
        self.proxy = proxy.strip() or None
        self.meting = "meting" in self.base.lower()
        self._meting_songs: dict[str, Song] = {}

    async def _request(
        self,
        path: str = "",
        *,
        follow_redirects: bool = True,
        **params: object,
    ) -> object:
        query = {key: str(value) for key, value in params.items() if value is not None}
        if self.cookie and not self.meting:
            query["cookie"] = self.cookie
        async with httpx.AsyncClient(
            timeout=HTTP_TIMEOUT,
            follow_redirects=follow_redirects,
            proxy=self.proxy,
        ) as client:
            response = await client.get(f"{self.base}{path}", params=query)
        if not follow_redirects and response.status_code in (301, 302, 303, 307, 308):
            return {"url": response.headers.get("location", "")}
        if response.status_code >= 400:
            raise SourceError(
                f"网易云接口暂时不可用（HTTP {response.status_code}）。"
            )
        try:
            return response.json()
        except ValueError:
            return response.text

    @staticmethod
    def _id_from_url(url: object) -> str:
        try:
            return parse_qs(urlparse(_text(url)).query).get("id", [""])[0]
        except Exception:
            return ""

    @classmethod
    def _parse_compat(cls, raw: object) -> Song | None:
        if not isinstance(raw, dict) or raw.get("id") is None:
            return None
        artists = raw.get("ar", raw.get("artists"))
        album = raw.get("al", raw.get("album"))
        album = album if isinstance(album, dict) else {}
        return Song(
            song_id=_text(raw.get("id")),
            name=_text(raw.get("name"), "未知曲目"),
            artist=_artists(artists) or "未知歌手",
            album=_text(album.get("name")),
            pic_url=_text(album.get("picUrl")) or None,
            duration_ms=_integer(raw.get("dt", raw.get("duration"))),
        )

    @classmethod
    def _parse_meting(cls, raw: object) -> Song | None:
        if not isinstance(raw, dict):
            return None
        song_id = cls._id_from_url(raw.get("url")) or _text(raw.get("id"))
        if not song_id:
            return None
        return Song(
            song_id=song_id,
            name=_text(raw.get("name"), "未知曲目"),
            artist=_text(raw.get("artist"), "未知歌手"),
            album=_text(raw.get("album")),
            pic_url=_text(raw.get("pic")) or None,
            duration_ms=_integer(raw.get("duration")),
        )

    async def search(self, keyword: str, limit: int) -> list[Song]:
        if self.meting:
            payload = await self._request(
                "",
                server="netease",
                type="search",
                id=keyword,
                limit=limit,
            )
            rows = payload if isinstance(payload, list) else []
            songs = [
                song for raw in rows if (song := self._parse_meting(raw)) is not None
            ][:limit]
            self._meting_songs.update({song.song_id: song for song in songs})
            return songs

        payload = await self._request(
            "/cloudsearch",
            keywords=keyword,
            limit=limit,
        )
        result = payload.get("result", {}) if isinstance(payload, dict) else {}
        rows = result.get("songs", []) if isinstance(result, dict) else []
        return [
            song for raw in rows if (song := self._parse_compat(raw)) is not None
        ][:limit]

    async def detail(self, song_id: str) -> Song | None:
        if self.meting:
            if song_id in self._meting_songs:
                return self._meting_songs[song_id]
            payload = await self._request(
                "",
                server="netease",
                type="search",
                id=song_id,
                limit=1,
            )
            rows = payload if isinstance(payload, list) else []
            song = self._parse_meting(rows[0]) if rows else None
            if song:
                self._meting_songs[song.song_id] = song
            return song

        payload = await self._request("/song/detail", ids=song_id)
        rows = payload.get("songs", []) if isinstance(payload, dict) else []
        return self._parse_compat(rows[0]) if rows else None

    async def play_url(self, song_id: str) -> str | None:
        if self.meting:
            payload = await self._request(
                "",
                follow_redirects=False,
                server="netease",
                type="url",
                id=song_id,
                br=_quality_br(self.quality),
            )
            if isinstance(payload, str):
                return payload.strip() or None
            if isinstance(payload, dict):
                return _text(payload.get("url")) or None
            return None

        payload = await self._request(
            "/song/url/v1",
            id=song_id,
            level=self.quality,
        )
        rows = payload.get("data", []) if isinstance(payload, dict) else []
        if not rows or not isinstance(rows[0], dict):
            return None
        if _integer(rows[0].get("code")) not in (None, 200):
            return None
        return _text(rows[0].get("url")) or None


async def download_audio(url: str, proxy: str) -> bytes:
    """下载音频并执行 15 MB 上限，避免异常响应耗尽内存。"""

    async with httpx.AsyncClient(
        timeout=HTTP_TIMEOUT,
        follow_redirects=True,
        proxy=proxy.strip() or None,
    ) as client:
        async with client.stream("GET", url) as response:
            if response.status_code >= 400:
                raise SourceError(f"音频下载失败（HTTP {response.status_code}）。")
            data = bytearray()
            async for chunk in response.aiter_bytes():
                data.extend(chunk)
                if len(data) > DOWNLOAD_MAX_BYTES:
                    raise SourceError("音频文件超过 15 MB 大小限制。")
            return bytes(data)
