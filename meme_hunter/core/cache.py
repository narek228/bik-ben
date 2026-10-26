"""Redis cache with an in-memory fallback (used when REDIS_URL is absent)."""
from __future__ import annotations

import json
import logging
import time
from typing import Any

log = logging.getLogger(__name__)


class Cache:
    def __init__(self, redis_url: str = "") -> None:
        self.redis_url = redis_url
        self._redis: Any = None
        self._memory: dict[str, tuple[float, str]] = {}

    async def connect(self) -> None:
        if not self.redis_url:
            log.info("REDIS_URL not set - using in-memory cache")
            return
        try:
            import redis.asyncio as aioredis

            self._redis = aioredis.from_url(self.redis_url, encoding="utf-8", decode_responses=True)
            await self._redis.ping()
            log.info("redis connected")
        except Exception as exc:
            log.warning("redis unavailable (%s) - falling back to in-memory cache", exc)
            self._redis = None

    async def close(self) -> None:
        if self._redis is not None:
            await self._redis.aclose()
            self._redis = None

    async def get(self, key: str) -> Any | None:
        if self._redis is not None:
            raw = await self._redis.get(key)
            if raw is None:
                return None
            try:
                return json.loads(raw)
            except json.JSONDecodeError:
                return raw
        entry = self._memory.get(key)
        if entry is None:
            return None
        expires_at, raw = entry
        if expires_at < time.monotonic():
            self._memory.pop(key, None)
            return None
        return json.loads(raw)

    async def set(self, key: str, value: Any, ttl_seconds: int = 300) -> None:
        raw = json.dumps(value, default=str)
        if self._redis is not None:
            await self._redis.set(key, raw, ex=ttl_seconds)
            return
        self._memory[key] = (time.monotonic() + ttl_seconds, raw)

    async def setnx(self, key: str, value: Any, ttl_seconds: int = 300) -> bool:
        if await self.get(key) is not None:
            return False
        await self.set(key, value, ttl_seconds)
        return True

    async def delete(self, key: str) -> None:
        if self._redis is not None:
            await self._redis.delete(key)
        else:
            self._memory.pop(key, None)


cache = Cache()
