"""Async HTTP client with retries, backoff and a per-provider circuit breaker."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Any

import httpx

from .config import settings
from .ratelimit import limiters

log = logging.getLogger(__name__)


class UpstreamError(RuntimeError):
    def __init__(self, provider: str, message: str, status_code: int | None = None) -> None:
        super().__init__(f"{provider}: {message}")
        self.provider = provider
        self.status_code = status_code


class CircuitOpen(UpstreamError):
    pass


class CircuitBreaker:
    def __init__(self, name: str, failure_threshold: int = 5, reset_seconds: float = 60.0) -> None:
        self.name = name
        self.failure_threshold = failure_threshold
        self.reset_seconds = reset_seconds
        self.failures = 0
        self.opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.reset_seconds:
            self.opened_at = None
            self.failures = 0
            return False
        return True

    def record_success(self) -> None:
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        self.failures += 1
        if self.failures >= self.failure_threshold and self.opened_at is None:
            self.opened_at = time.monotonic()
            log.warning("circuit breaker opened for %s", self.name)


class HttpClient:
    """Shared httpx.AsyncClient wrapper. Use as an async context manager."""

    def __init__(self, provider: str, base_url: str = "", rate: float = 1.0, burst: float = 1.0) -> None:
        self.provider = provider
        self.base_url = base_url.rstrip("/")
        self.rate = rate
        self.burst = burst
        self.breaker = CircuitBreaker(provider)
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "HttpClient":
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(settings.http_timeout_seconds),
            headers={"User-Agent": settings.http_user_agent, "Accept": "application/json"},
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def get_json(self, url: str, params: dict[str, Any] | None = None,
                      headers: dict[str, str] | None = None, provider: str | None = None) -> Any:
        return await self._request("GET", url, params=params, headers=headers, provider=provider)

    async def get_text(self, url: str, headers: dict[str, str] | None = None,
                       provider: str | None = None) -> str:
        """Fetch a non-JSON body (RSS/XML). Same retry and breaker behaviour."""
        name = provider or self.provider
        if self.breaker.is_open:
            raise CircuitOpen(name, "circuit breaker open")
        target = url if url.startswith("http") else f"{self.base_url}{url}"
        last_error: Exception | None = None

        for attempt in range(1, settings.http_max_retries + 1):
            try:
                await limiters.acquire(name, self.rate, self.burst)
                if self._client is None:
                    raise RuntimeError("HttpClient used outside its async context manager")
                response = await self._client.request("GET", target, headers=headers)

                if response.status_code == 429:
                    self.breaker.record_failure()
                    raise UpstreamError(name, "rate limited (429)", 429)
                if response.status_code >= 500:
                    self.breaker.record_failure()
                    raise UpstreamError(name, f"server error {response.status_code}", response.status_code)
                if response.status_code >= 400:
                    raise UpstreamError(name, f"client error {response.status_code}", response.status_code)

                self.breaker.record_success()
                return response.text

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self.breaker.record_failure()
            except UpstreamError as exc:
                last_error = exc
                if exc.status_code and exc.status_code < 500 and exc.status_code != 429:
                    raise

            if attempt < settings.http_max_retries:
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.4)
                log.warning("%s attempt %s/%s failed: %s", name, attempt, settings.http_max_retries, last_error)
                await asyncio.sleep(backoff)

        raise UpstreamError(name, f"failed after {settings.http_max_retries} attempts: {last_error}")

    async def _request(self, method: str, url: str, params: dict[str, Any] | None = None,
                       headers: dict[str, str] | None = None, provider: str | None = None) -> Any:
        name = provider or self.provider
        if self.breaker.is_open:
            raise CircuitOpen(name, "circuit breaker open")
        target = url if url.startswith("http") else f"{self.base_url}{url}"
        last_error: Exception | None = None

        for attempt in range(1, settings.http_max_retries + 1):
            try:
                await limiters.acquire(name, self.rate, self.burst)
                if self._client is None:
                    raise RuntimeError("HttpClient used outside its async context manager")
                response = await self._client.request(method, target, params=params, headers=headers)

                if response.status_code == 429:
                    self.breaker.record_failure()
                    raise UpstreamError(name, "rate limited (429)", 429)
                if response.status_code >= 500:
                    self.breaker.record_failure()
                    raise UpstreamError(name, f"server error {response.status_code}", response.status_code)
                if response.status_code >= 400:
                    raise UpstreamError(name, f"client error {response.status_code}", response.status_code)

                self.breaker.record_success()
                if not response.content:
                    return {}
                return response.json()

            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_error = exc
                self.breaker.record_failure()
            except UpstreamError as exc:
                last_error = exc
                if exc.status_code and exc.status_code < 500 and exc.status_code != 429:
                    raise
            except ValueError as exc:
                raise UpstreamError(name, f"invalid JSON response: {exc}") from exc

            if attempt < settings.http_max_retries:
                backoff = min(2 ** (attempt - 1), 8) + random.uniform(0, 0.4)
                log.warning("%s attempt %s/%s failed: %s", name, attempt, settings.http_max_retries, last_error)
                await asyncio.sleep(backoff)

        raise UpstreamError(name, f"failed after {settings.http_max_retries} attempts: {last_error}")
