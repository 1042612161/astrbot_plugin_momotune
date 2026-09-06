"""MomoTune AstrBot 插件入口。"""

from __future__ import annotations

import re

import httpx
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.components import Image

from .MomoTune import (
    NcmClient,
    RendererManager,
    SelectionStore,
    Song,
    SourceError,
    render_card,
)

COMMAND_PATTERN = re.compile(r"^/?(?:点歌|唱歌|来一首)\s*")


class MomoTunePlugin(Star):
    """网易云搜索、候选选歌、卡片与语音播放。"""

    def __init__(
        self,
        context: Context,
        config: AstrBotConfig | None = None,
    ) -> None:
        super().__init__(context)
        self.config = config if config is not None else {}
        self.selections = SelectionStore(ttl_seconds=self._selection_ttl_seconds())
        self.renderer = RendererManager()

    async def initialize(self) -> None:
        self.renderer.initialize()

    async def terminate(self) -> None:
        await self.renderer.close()
        await super().terminate()

    def _config(self, key: str, default: object) -> object:
        value = self.config.get(key, default)
        return value if value not in (None, "") else default

    def _proxy(self) -> str:
        return str(self._config("proxy", ""))

    def _source(self) -> NcmClient:
        return NcmClient(
            base=str(self._config("ncm_api_base", "https://api.ames.cc.cd")),
            cookie=str(self._config("ncm_cookie", "")),
            quality=str(self._config("ncm_quality", "exhigh")),
            proxy=self._proxy(),
        )

    def _search_limit(self) -> int:
        try:
            return max(1, min(30, int(self._config("ncm_search_limit", 10))))
        except (TypeError, ValueError):
            return 10

    def _selection_ttl_seconds(self) -> int:
        try:
            return max(
                30,
                min(300, int(self._config("selection_ttl_seconds", 60))),
            )
        except (TypeError, ValueError):
            return 60

    def _sync_selection_ttl(self) -> int:
        """应用最新配置，使 WebUI 修改后的值无需改动业务代码。"""

        ttl_seconds = self._selection_ttl_seconds()
        self.selections.ttl_seconds = float(ttl_seconds)
        return ttl_seconds

    @staticmethod
    def _selection_key(event: AstrMessageEvent) -> str:
        return (
            f"{event.get_platform_id()}:"
            f"{event.unified_msg_origin}:"
            f"{event.get_sender_id()}"
        )

    @staticmethod
    def _merge_detail(song: Song, detail: Song | None) -> Song:
        if detail is None:
            return song
        return Song(
            song_id=song.song_id,
            name=detail.name if song.name == "未知曲目" else song.name,
            artist=detail.artist if song.artist == "未知歌手" else song.artist,
            album=song.album or detail.album,
            pic_url=song.pic_url or detail.pic_url,
            duration_ms=(
                song.duration_ms
                if song.duration_ms is not None
                else detail.duration_ms
            ),
        )

    async def _play(self, event: AstrMessageEvent, song: Song) -> None:
        source = self._source()
        try:
            play_url = await source.play_url(song.song_id)
        except (SourceError, httpx.HTTPError) as exc:
            await event.send(event.plain_result(str(exc)))
            return
        if not play_url:
            await event.send(
                event.plain_result(
                    "这首歌暂时没有可用的播放链接（可能受版权限制），换一首试试吧。"
                )
            )
            return

        if song.name == "未知曲目" or song.pic_url is None:
            try:
                song = self._merge_detail(song, await source.detail(song.song_id))
            except (SourceError, httpx.HTTPError) as exc:
                logger.debug(f"[MomoTune] 获取歌曲详情失败 {song.song_id}: {exc}")

        try:
            card = await render_card(
                [song],
                "正在播放",
                "网易云 · MomoTune 为你选中的旋律",
                self._proxy(),
                self.renderer,
            )
            await event.send(event.chain_result([Image.fromBytes(card)]))
        except (OSError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning(f"[MomoTune] 渲染歌曲卡片失败 {song.song_id}: {exc}")

        try:
            await self._send_audio_url(event, play_url)
        except Exception as exc:
            logger.warning(f"[MomoTune] 音频发送失败 {song.song_id}: {exc}")
            await event.send(event.plain_result("音频发送失败，请稍后再试。"))

    @staticmethod
    async def _send_audio_url(event: AstrMessageEvent, url: str) -> None:
        """通过 aiocqhttp 的原始 OneBot API 发送 URL，避免 AstrBot WAV 转换。"""

        bot = getattr(event, "bot", None)
        if bot is None or not hasattr(bot, "call_action"):
            raise RuntimeError("当前事件不是 aiocqhttp OneBot 事件")
        group_id = str(event.get_group_id() or "").strip()
        self_id = str(event.get_self_id() or "").strip()
        routing = {"self_id": int(self_id)} if self_id.isdigit() else {}
        if group_id:
            if not group_id.isdigit():
                raise RuntimeError("无效的群号")
            await bot.call_action(
                "send_group_msg",
                group_id=int(group_id),
                message=[{"type": "record", "data": {"file": url}}],
                **routing,
            )
            return
        user_id = str(event.get_sender_id() or "").strip()
        if not user_id.isdigit():
            raise RuntimeError("无效的用户号")
        await bot.call_action(
            "send_private_msg",
            user_id=int(user_id),
            message=[{"type": "record", "data": {"file": url}}],
            **routing,
        )

    async def _search(self, keyword: str) -> list[Song]:
        return await self._source().search(keyword, self._search_limit())

    @filter.command("点歌", alias={"唱歌", "来一首"})
    async def ncm_song(self, event: AstrMessageEvent):
        """搜索网易云歌曲并播放；支持歌曲名、歌手名或数字歌曲 ID。"""

        keyword = COMMAND_PATTERN.sub("", event.message_str.strip(), count=1).strip()
        if not keyword:
            yield event.plain_result("请输入歌名，例如：点歌 晴天")
            return
        key = self._selection_key(event)
        ttl_seconds = self._sync_selection_ttl()
        if keyword.isdigit():
            self.selections.clear(key)
            await self._play(event, Song(keyword))
            return
        try:
            songs = await self._search(keyword)
        except (SourceError, httpx.HTTPError) as exc:
            yield event.plain_result(f"搜索网易云「{keyword}」失败：{exc}")
            return
        if not songs:
            yield event.plain_result(f"网易云没有找到「{keyword}」相关的歌曲。")
            return
        if len(songs) == 1:
            self.selections.clear(key)
            await self._play(event, songs[0])
            return
        try:
            card = await render_card(
                songs,
                "网易云点歌候选",
                f"回复数字 1～{len(songs)} 播放对应曲目 · 选择在 {ttl_seconds} 秒内有效",
                self._proxy(),
                self.renderer,
            )
        except (OSError, RuntimeError, httpx.HTTPError) as exc:
            logger.warning(f"[MomoTune] 渲染搜索结果失败: {exc}")
            yield event.plain_result("渲染搜索结果失败，请稍后再试。")
            return
        self.selections.set(key, songs)
        yield event.chain_result([Image.fromBytes(card)])
        yield event.plain_result(
            f"回复数字 1～{len(songs)} 播放对应曲目（{ttl_seconds} 秒内有效）"
        )

    @filter.regex(r"^\d{1,2}$")
    async def pick_song(self, event: AstrMessageEvent):
        """在最近一次搜索结果中按数字选择歌曲。"""

        key = self._selection_key(event)
        self._sync_selection_ttl()
        songs = self.selections.get(key)
        if songs is None:
            return
        choice = int(event.message_str.strip())
        if not 1 <= choice <= len(songs):
            yield event.plain_result(f"请回复 1～{len(songs)} 之间的数字。")
            return
        self.selections.clear(key)
        await self._play(event, songs[choice - 1])

    @filter.llm_tool(name="play_music")
    async def play_music(
        self,
        event: AstrMessageEvent,
        song_name: str,
        artist: str = "",
    ):
        """搜索并直接播放网易云歌曲。

        Args:
            song_name(string): 歌曲名称、关键词或网易云歌曲 ID。
            artist(string): 可选的歌手名称，用于选择更准确的版本。
        """

        query = (
            f"{artist.strip()} {song_name.strip()}".strip()
            if artist.strip()
            else song_name.strip()
        )
        if not query:
            yield event.plain_result("未指定歌名。")
            return
        if query.isdigit():
            await self._play(event, Song(query))
            yield event.plain_result(f"已尝试播放网易云歌曲 ID {query}。")
            return
        try:
            songs = await self._search(query)
        except (SourceError, httpx.HTTPError) as exc:
            yield event.plain_result(f"搜索失败：{exc}")
            return
        if not songs:
            yield event.plain_result(f"未找到「{query}」相关歌曲。")
            return
        selected = songs[0]
        if artist.strip():
            artist_key = artist.strip().lower()
            selected = next(
                (song for song in songs if artist_key in song.artist.lower()),
                selected,
            )
        await self._play(event, selected)
