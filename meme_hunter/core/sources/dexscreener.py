"""DexScreener public API - no key required."""
from __future__ import annotations

import logging
from typing import Any

from ..cache import cache
from ..config import settings
from ..http_client import HttpClient, UpstreamError
from .base import PoolInfo, Provenance, safe_float, safe_int

log = logging.getLogger(__name__)

BASE_URL = "https://api.dexscreener.com"


class DexScreenerClient:
    def __init__(self) -> None:
        rate = settings.dexscreener_rpm / 60.0
        self._client = HttpClient(provider="dexscreener", base_url=BASE_URL, rate=rate, burst=max(rate, 1.0))

    async def __aenter__(self) -> "DexScreenerClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.__aexit__(*exc)

    async def token_pools(self, mint: str) -> list[PoolInfo]:
        cache_key = f"dex:token-pairs:{mint}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return [_pool_from_dict(item) for item in cached]
        data = await self._client.get_json(f"/token-pairs/v1/{mint}")
        pairs = data if isinstance(data, list) else [data]
        pools = [_pool_from_dict(p) for p in pairs if isinstance(p, dict)]
        await cache.set(cache_key, [p.__dict__ for p in pools], ttl_seconds=45)
        return pools

    async def best_pool(self, mint: str) -> PoolInfo | None:
        pools = await self.token_pools(mint)
        if not pools:
            return None
        return max(pools, key=lambda p: p.liquidity_usd or 0.0)

    async def search(self, query: str) -> list[PoolInfo]:
        data = await self._client.get_json("/latest/dex/search", params={"q": query})
        return [_pool_from_dict(p) for p in (data.get("pairs") or []) if isinstance(p, dict)]

    async def boosted_tokens(self) -> list[PoolInfo]:
        data = await self._client.get_json("/token-boosts/latest/v1")
        items = data.get("tokens") or []
        out: list[PoolInfo] = []
        for item in items:
            if not isinstance(item, dict) or item.get("chainId") != "solana":
                continue
            address = item.get("tokenAddress")
            if not address:
                continue
            try:
                out.extend(await self.token_pools(address))
            except UpstreamError as exc:
                log.warning("boosted token lookup failed for %s: %s", address, exc)
        return out

    async def newest_pairs(self) -> list[PoolInfo]:
        data = await self._client.get_json("/token-profiles/latest/v1")
        items = data.get("tokens") or []
        out: list[PoolInfo] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            address = item.get("tokenAddress")
            if not address:
                continue
            try:
                out.extend(await self.token_pools(address))
            except UpstreamError as exc:
                log.warning("profile token lookup failed for %s: %s", address, exc)
        return out


def _pool_from_dict(raw: dict[str, Any]) -> PoolInfo:
    base = raw.get("baseToken") or {}
    quote = raw.get("quoteToken") or {}
    liquidity = raw.get("liquidity") or {}
    volume = raw.get("volume") or {}
    change = raw.get("priceChange") or {}
    txns = raw.get("txns") or {}
    info = raw.get("info") or {}

    def _txn(window: str, side: str) -> int | None:
        block = txns.get(window) or {}
        return safe_int(block.get(side))

    website = ""
    twitter = ""
    for site in (info.get("websites") or []):
        if isinstance(site, dict) and site.get("url"):
            website = site["url"]
            break
    for social in (info.get("socials") or []):
        if isinstance(social, dict) and social.get("type") == "twitter" and social.get("url"):
            twitter = social["url"]
            break

    chain = raw.get("chainId") or "solana"
    pair_address = raw.get("pairAddress") or ""
    return PoolInfo(
        chain=chain,
        dex=raw.get("dexId") or "",
        pair_address=pair_address,
        base_mint=base.get("address") or "",
        base_symbol=base.get("symbol") or "",
        base_name=base.get("name") or "",
        quote_symbol=quote.get("symbol") or "",
        price_usd=safe_float(raw.get("priceUsd")),
        price_native=safe_float(raw.get("priceNative")),
        market_cap=safe_float(raw.get("marketCap")) or safe_float(raw.get("fdv")),
        fdv=safe_float(raw.get("fdv")),
        liquidity_usd=safe_float(liquidity.get("usd")),
        volume_5m=safe_float(volume.get("m5")),
        volume_1h=safe_float(volume.get("h1")),
        volume_24h=safe_float(volume.get("h24")),
        change_5m=safe_float(change.get("m5")),
        change_1h=safe_float(change.get("h1")),
        change_24h=safe_float(change.get("h24")),
        buys_5m=_txn("m5", "buys"),
        sells_5m=_txn("m5", "sells"),
        buys_1h=_txn("h1", "buys"),
        sells_1h=_txn("h1", "sells"),
        buys_24h=_txn("h24", "buys"),
        sells_24h=_txn("h24", "sells"),
        pair_created_at=safe_int(raw.get("pairCreatedAt")),
        website=website,
        twitter=twitter,
        provenance=Provenance(source="dexscreener",
                               url=f"https://dexscreener.com/{chain}/{pair_address}"),
    )
