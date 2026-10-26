"""Async rate limiters: a token bucket per upstream API."""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field


@dataclass
class TokenBucket:
    rate: float
    capacity: float
    _tokens: float = field(init=False)
    _updated_at: float = field(init=False)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, init=False)

    def __post_init__(self) -> None:
        self._tokens = self.capacity
        self._updated_at = time.monotonic()

    async def acquire(self, tokens: float = 1.0) -> None:
        while True:
            async with self._lock:
                now = time.monotonic()
                elapsed = now - self._updated_at
                self._updated_at = now
                self._tokens = min(self.capacity, self._tokens + elapsed * self.rate)
                if self._tokens >= tokens:
                    self._tokens -= tokens
                    return
                deficit = tokens - self._tokens
                wait = deficit / self.rate if self.rate > 0 else 1.0
            await asyncio.sleep(wait)


class RateLimiterRegistry:
    def __init__(self) -> None:
        self._buckets: dict[str, TokenBucket] = {}

    def get(self, name: str, rate: float, burst: float | None = None) -> TokenBucket:
        if name not in self._buckets:
            self._buckets[name] = TokenBucket(rate=rate, capacity=burst if burst is not None else max(rate, 1.0))
        return self._buckets[name]

    async def acquire(self, name: str, rate: float, burst: float | None = None, tokens: float = 1.0) -> None:
        await self.get(name, rate, burst).acquire(tokens)


limiters = RateLimiterRegistry()
