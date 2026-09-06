"""候选歌曲的会话内短期状态。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .models import Song


@dataclass(frozen=True, slots=True)
class PendingSelection:
    created_at: float
    songs: tuple[Song, ...]


class SelectionStore:
    """按平台、会话和用户隔离候选，并在读取时执行 TTL 清理。"""

    def __init__(self, ttl_seconds: float = 60.0) -> None:
        self.ttl_seconds = ttl_seconds
        self._items: dict[str, PendingSelection] = {}

    def set(self, key: str, songs: list[Song]) -> None:
        self._remove_expired()
        self._items[key] = PendingSelection(time.monotonic(), tuple(songs))

    def get(self, key: str) -> tuple[Song, ...] | None:
        item = self._items.get(key)
        if item is None:
            return None
        if time.monotonic() - item.created_at > self.ttl_seconds:
            self._items.pop(key, None)
            return None
        return item.songs

    def clear(self, key: str) -> None:
        self._items.pop(key, None)

    def _remove_expired(self) -> None:
        now = time.monotonic()
        expired = [
            key
            for key, item in self._items.items()
            if now - item.created_at > self.ttl_seconds
        ]
        for key in expired:
            self._items.pop(key, None)
