"""MomoTune 点歌命令、候选列表和音频发送。"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx

from gsuid_core.bot import Bot
from gsuid_core.logger import logger
from gsuid_core.models import Event
from gsuid_core.segment import MessageSegment
from gsuid_core.sv import SV

from ..momotune_config import MOMOTUNE_CONFIG
from .render import render_song_card
from .sources import (
    KUGOU,
    NCM,
    BaseSource,
    KugouSource,
    MusicSource,
    NcmSource,
    Song,
    SourceError,
    download,
)

PENDING_TTL_SECONDS = 300.0


@dataclass(frozen=True, slots=True)
class TuneSettings:
    ncm_api_base: str
    kugou_api_base: str
    search_limit: int
    quality: str
    ncm_cookie: str
    kugou_cookie: str


@dataclass(frozen=True, slots=True)
class PendingSelection:
    songs: tuple[Song, ...]
    created_at: float


def _setting_text(key: str, default: str) -> str:
    value = MOMOTUNE_CONFIG.get_config(key, default).data
    return value if isinstance(value, str) and value.strip() else default


def _setting_int(key: str, default: int) -> int:
    value = MOMOTUNE_CONFIG.get_config(key, default).data
    if isinstance(value, int) and not isinstance(value, bool):
        return max(1, min(30, value))
    return default


def _settings() -> TuneSettings:
    return TuneSettings(
        ncm_api_base=_setting_text("ncm_api_base", "http://127.0.0.1:3030"),
        kugou_api_base=_setting_text("ncm_kugou_api_base", "http://127.0.0.1:3040"),
        search_limit=_setting_int("ncm_search_limit", 10),
        quality=_setting_text("ncm_quality", "exhigh"),
        ncm_cookie=_setting_text("ncm_cookie", ""),
        kugou_cookie=_setting_text("ncm_kugou_cookie", ""),
    )


class SourceRegistry:
    """配置变更后自动重建 source，同时保留酷狗 dfid。"""

    def __init__(self) -> None:
        self._fingerprint: tuple[str, str, str, str, str] | None = None
        self._sources: dict[MusicSource, BaseSource] = {}

    def snapshot(self) -> tuple[TuneSettings, dict[MusicSource, BaseSource]]:
        settings = _settings()
        fingerprint = (
            settings.ncm_api_base,
            settings.kugou_api_base,
            settings.quality,
            settings.ncm_cookie,
            settings.kugou_cookie,
        )
        if fingerprint != self._fingerprint:
            self._sources = {
                NCM: NcmSource(settings.ncm_api_base, settings.ncm_cookie, settings.quality),
                KUGOU: KugouSource(settings.kugou_api_base, settings.kugou_cookie, settings.quality),
            }
            self._fingerprint = fingerprint
        return settings, self._sources


_REGISTRY = SourceRegistry()
_PENDING: dict[str, PendingSelection] = {}


def _pending_key(ev: Event) -> str:
    scope = ev.group_id if ev.group_id is not None else f"direct:{ev.user_id}"
    return f"{ev.bot_id}:{ev.bot_self_id}:{scope}:{ev.user_id}"


def _set_pending(ev: Event, songs: list[Song]) -> None:
    now = time.monotonic()
    for key, pending in tuple(_PENDING.items()):
        if now - pending.created_at > PENDING_TTL_SECONDS:
            del _PENDING[key]
    _PENDING[_pending_key(ev)] = PendingSelection(tuple(songs), now)


def _get_pending(ev: Event) -> tuple[Song, ...] | None:
    pending = _PENDING.get(_pending_key(ev))
    if pending is None:
        return None
    if time.monotonic() - pending.created_at > PENDING_TTL_SECONDS:
        del _PENDING[_pending_key(ev)]
        return None
    return pending.songs


def _clear_pending(ev: Event) -> None:
    _PENDING.pop(_pending_key(ev), None)


def _merge_detail(song: Song, detail: Song | None) -> Song:
    if detail is None:
        return song
    if song.name != "未知曲目" and song.pic_url is not None:
        return song
    return Song(
        source=song.source,
        song_id=song.song_id,
        name=detail.name if song.name == "未知曲目" else song.name,
        artist=detail.artist if song.artist == "未知歌手" else song.artist,
        album=detail.album if not song.album else song.album,
        pic_url=detail.pic_url if song.pic_url is None else song.pic_url,
        duration_ms=detail.duration_ms if song.duration_ms is None else song.duration_ms,
        extra=song.extra,
    )


async def _play_song(bot: Bot, ev: Event, song: Song, source: BaseSource) -> tuple[bool, str]:
    try:
        play_url = await source.play_url(song)
    except SourceError as exc:
        msg = str(exc)
        await bot.send(msg)
        return False, msg
    except httpx.HTTPError as exc:
        logger.warning(f"[MomoTune] {source.label}获取播放链接失败: {exc}")
        msg = f"{source.label}暂时无法获取播放链接，请稍后再试。"
        await bot.send(msg)
        return False, msg

    if not play_url:
        msg = "这首歌暂时没有可用的播放链接（可能受版权限制），换一首试试吧。"
        await bot.send(msg)
        return False, msg

    if song.name == "未知曲目" or song.pic_url is None:
        try:
            song = _merge_detail(song, await source.detail(song.song_id))
        except (SourceError, httpx.HTTPError) as exc:
            logger.debug(f"[MomoTune] 获取歌曲详情失败 {song.song_id}: {exc}")

    try:
        card = await render_song_card(
            [song],
            title="正在播放",
            hint=f"{source.label} · MomoTune 为你选中的旋律",
        )
        await bot.send(MessageSegment.image(card))
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        logger.warning(f"[MomoTune] 渲染歌曲卡片失败 {song.song_id}: {exc}")

    try:
        audio = await download(play_url)
    except (SourceError, httpx.HTTPError) as exc:
        logger.warning(f"[MomoTune] 下载音频失败 {song.song_id}: {exc}")
        msg = "下载音频失败，请稍后再试。"
        await bot.send(msg)
        return False, msg
    await bot.send(MessageSegment.record(audio))
    return True, f"《{song.name}》- {song.artist}"


async def _handle_song_request(bot: Bot, ev: Event, source_name: MusicSource) -> None:
    settings, sources = _REGISTRY.snapshot()
    source = sources[source_name]
    raw = ev.text.strip()
    command = ev.command or ("酷狗点歌" if source_name == KUGOU else "点歌")
    if not raw:
        await bot.send(f"请输入歌名，例如：{command} 晴天")
        return

    if source_name == NCM and raw.isdigit():
        _clear_pending(ev)
        await _play_song(bot, ev, Song(source=NCM, song_id=raw), source)
        return

    try:
        results = await source.search(raw, settings.search_limit)
    except SourceError as exc:
        await bot.send(str(exc))
        return
    except httpx.HTTPError as exc:
        logger.warning(f"[MomoTune] {source.label}搜索失败: {exc}")
        await bot.send(f"{source.label}搜索「{raw}」失败，请稍后再试。")
        return

    if not results:
        await bot.send(f"{source.label}没有找到「{raw}」相关的歌曲。")
        return
    if len(results) == 1:
        _clear_pending(ev)
        await _play_song(bot, ev, results[0], source)
        return

    try:
        card = await render_song_card(
            results,
            title=f"{source.label}点歌候选",
            hint=f"回复数字 1～{len(results)} 播放对应曲目 · 选择在 {PENDING_TTL_SECONDS // 60:.0f} 分钟内有效",
        )
    except (OSError, RuntimeError, httpx.HTTPError) as exc:
        logger.warning(f"[MomoTune] 渲染搜索结果失败: {exc}")
        await bot.send("渲染搜索结果失败，请稍后再试。")
        return

    _set_pending(ev, results)
    await bot.send(MessageSegment.image(card))
    await bot.send(f"回复数字 1～{len(results)} 播放对应曲目（{PENDING_TTL_SECONDS // 60:.0f} 分钟内有效）")


music_sv = SV("MomoTune点歌", priority=5, area="ALL")
pick_sv = SV("MomoTune选歌", priority=15, area="ALL")

NCM_COMMANDS = ("点歌", "唱歌", "来一首")
KUGOU_COMMANDS = ("酷狗点歌", "酷狗唱歌", "酷狗来一首")


@music_sv.on_command(
    NCM_COMMANDS,
    block=True,
    prefix=False,
)
async def ncm_song(bot: Bot, ev: Event) -> None:
    await _handle_song_request(bot, ev, NCM)


@music_sv.on_command(
    KUGOU_COMMANDS,
    block=True,
    prefix=False,
)
async def kugou_song(bot: Bot, ev: Event) -> None:
    await _handle_song_request(bot, ev, KUGOU)


@pick_sv.on_message(prefix=False)
async def pick_song(bot: Bot, ev: Event) -> None:
    text = ev.raw_text.strip()
    if not text.isdigit():
        return
    pending = _get_pending(ev)
    if pending is None:
        return
    choice = int(text)
    if choice < 1 or choice > len(pending):
        await bot.send(f"请回复 1～{len(pending)} 之间的数字。")
        return
    song = pending[choice - 1]
    _clear_pending(ev)
    _, sources = _REGISTRY.snapshot()
    await _play_song(bot, ev, song, sources[song.source])


logger.info("[MomoTune] 网易云 / 酷狗点歌触发器已注册")


# ─── AI Core 工具集成 ──────────────────────────────────────────────────────────
try:
    from pydantic_ai import RunContext
    from gsuid_core.ai_core.models import ToolContext
    from gsuid_core.ai_core.register import ai_tools

    @ai_tools(
        category="common",
        capability_domain="音乐播放",
        covers=[
            "网易云音乐点歌与歌曲播放",
            "酷狗音乐点歌与歌曲播放",
            "按歌名或歌手播放歌曲音频与卡片",
            "根据用户需求点播音乐",
        ],
        aliases=[
            "音乐·点歌",
            "音乐·播放歌曲",
            "音乐·网易云放歌",
            "音乐·酷狗放歌",
        ],
        context_tags=["音乐", "点歌", "娱乐"],
    )
    async def play_music(
        ctx: RunContext[ToolContext],
        song_name: str,
        artist: str = "",
        source: str = "ncm",
    ) -> str:
        """搜索并直接播放指定歌曲。调用本工具后，机器人会直接将歌曲卡片与音频语音发送到当前聊天中。
当用户要求点歌、放歌、听歌、来一首歌、或希望播放某位歌手的特定歌曲时调用。

Args:
    song_name: 歌曲名称或关键词，例如“晴天”、“海阔天空”。若已知网易云歌曲ID也可直接填入数字ID（如“347230”）。
    artist: 可选，歌手名称，例如“周杰伦”、“陈奕迅”，用于更精准命中。
    source: 音乐平台源，"ncm"（网易云音乐，默认）或 "kugou"（酷狗音乐）。

Returns:
    播放状态说明。若成功，卡片与音频已直接发送给用户；AI 无需再重复发送音频，可直接自然回复用户。
"""
        bot = ctx.deps.bot
        ev = ctx.deps.ev
        if bot is None or ev is None:
            return "错误：当前会话上下文缺失，无法发送音乐。"

        src_name = KUGOU if source.lower() in ("kugou", "kg", "酷狗") else NCM
        settings, sources = _REGISTRY.snapshot()
        source_obj = sources[src_name]

        clean_name = song_name.strip()
        if not clean_name:
            return "错误：未指定歌名。"

        # 1. 网易云纯数字 ID 直接播放
        if src_name == NCM and clean_name.isdigit():
            ok, info = await _play_song(bot, ev, Song(source=NCM, song_id=clean_name), source_obj)
            if ok:
                return f"已成功为用户播放网易云歌曲（ID: {clean_name}）。歌曲卡片与音频已发送到聊天中。"
            return f"播放失败：{info}"

        # 2. 构造搜索关键词并检索
        query = f"{artist.strip()} {clean_name}".strip() if artist.strip() and artist.strip() not in clean_name else clean_name
        try:
            results = await source_obj.search(query, settings.search_limit)
        except SourceError as exc:
            return f"搜索失败：{exc}"
        except httpx.HTTPError as exc:
            logger.warning(f"[MomoTune] {source_obj.label}搜索失败: {exc}")
            return f"搜索失败：连接{source_obj.label}服务超时或异常，请稍后重试。"

        if not results:
            return f"在{source_obj.label}未找到「{query}」相关的歌曲。建议更换歌名或尝试另一个平台（如 kugou）。"

        # 3. 挑选最佳匹配
        best_song = results[0]
        if artist.strip():
            art_lower = artist.strip().lower()
            for s in results:
                if art_lower in s.artist.lower():
                    best_song = s
                    break

        # 4. 执行播放并发送
        ok, info = await _play_song(bot, ev, best_song, source_obj)
        if ok:
            return f"已成功为用户播放歌曲：《{best_song.name}》- {best_song.artist}（来源：{source_obj.label}）。歌曲卡片与音频语音已直接发送给用户，AI 可以在对话中告知用户歌曲已送达。"
        return f"找到歌曲《{best_song.name}》- {best_song.artist}，但在播放时失败：{info}"

    logger.info("[MomoTune] MomoTune AI 音乐播放工具已注册")
except ImportError as e:
    logger.warning(f"[MomoTune] 未能加载 AI Core 模块，跳过 AI 工具注册: {e}")
