"""in-memory TTL cache - avoids re-hitting APIs for data that doesn't change fast."""

import asyncio
import time
from typing import Any


class TTLCache:
    def __init__(self, ttl_seconds: int = 300):
        self._ttl  = ttl_seconds
        self._data: dict[str, tuple[Any, float]] = {}
        self._lock = asyncio.Lock()

    async def get(self, key: str) -> Any | None:
        async with self._lock:
            entry = self._data.get(key)
            if entry is None:
                return None
            value, exp = entry
            if time.monotonic() > exp:
                del self._data[key]
                return None
            return value

    async def set(self, key: str, value: Any) -> None:
        async with self._lock:
            self._data[key] = (value, time.monotonic() + self._ttl)

    async def get_or_set(self, key: str, coro_fn) -> Any:
        """return cached value or await coro_fn() and cache the result."""
        cached = await self.get(key)
        if cached is not None:
            return cached
        value = await coro_fn()
        await self.set(key, value)
        return value

    def invalidate(self, key: str) -> None:
        self._data.pop(key, None)

    def clear(self) -> None:
        self._data.clear()


# module-level caches
handle_cache  = TTLCache(ttl_seconds=300)   # trader lookups: 5m
pnl_cache     = TTLCache(ttl_seconds=120)   # pnl: 2m
board_cache   = TTLCache(ttl_seconds=60)    # leaderboards: 1m
thesis_cache  = TTLCache(ttl_seconds=90)    # theses: 90s
