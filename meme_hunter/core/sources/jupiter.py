"""Jupiter Price API v3 - keyless access on the public price endpoint.

Used as an independent price sanity check against the DEX aggregator, so a
single bad pool cannot silently poison a score.
"""
from __future__ import annotations

import logging

from ..cache import cache
from ..config import settings
from ..http_client import HttpClient, UpstreamError
from .base import Provenance, safe_float

log = logging.getLogger(__name__)

PRICE_URL = "https://api.jup.ag/price/v3"


class JupiterPriceClient:
    def __init__(self) -> None:
        rate = settings.jupiter_rps
        self._client = HttpClient(provider="jupiter", base_url=PRICE_URL, rate=rate, burst=max(rate, 1.0))

    async def __aenter__(self) -> "JupiterPriceClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.__aexit__(*exc)

    async def price(self, mint: str) -> float | None:
        """Current USD price for one mint, or None when unavailable."""
        cache_key = f"jup:price:{mint}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached

        data = await self._client.get_json("/price", params={"ids": mint})
        entry = (data.get(mint) or {}) if isinstance(data, dict) else {}
        price = safe_float(entry.get("price"))
        if price is None:
            price = safe_float(entry.get("usdPrice"))
        if price is not None:
            await cache.set(cache_key, price, ttl_seconds=20)
        return price

    async def prices(self, mints: list[str]) -> dict[str, float]:
        """Batch price lookup. Returns only the mints that resolved."""
        if not mints:
            return {}
        out: dict[str, float] = {}
        chunk = 50
        for index in range(0, len(mints), chunk):
            batch = mints[index:index + chunk]
            try:
                data = await self._client.get_json("/price", params={"ids": ",".join(batch)})
            except UpstreamError as exc:
                log.warning("jupiter batch price failed: %s", exc)
                continue
            for mint in batch:
                entry = (data.get(mint) or {}) if isinstance(data, dict) else {}
                value = safe_float(entry.get("price")) or safe_float(entry.get("usdPrice"))
                if value is not None:
                    out[mint] = value
        return out

    async def provenance(self, mint: str) -> Provenance:
        return Provenance(source="jupiter", url=f"https://api.jup.ag/price/v3?ids={mint}")
