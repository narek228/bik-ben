"""Helius DAS API - free tier. Key required, 1M free credits/month, DAS 2 req/s.

Used for on-chain facts no DEX aggregator exposes: mint authorities, supply
distribution and top holder concentration.
"""
from __future__ import annotations

import logging
from typing import Any

from ..cache import cache
from ..config import settings
from ..http_client import HttpClient, UpstreamError
from .base import HolderInfo, Provenance, TokenSecurity, safe_float, safe_int

log = logging.getLogger(__name__)

DAS_URL = "https://mainnet.helius-rpc.com"


class HeliusClient:
    def __init__(self) -> None:
        rate = settings.helius_das_rps
        self._client = HttpClient(provider="helius", base_url=DAS_URL, rate=rate, burst=max(rate, 1.0))

    async def __aenter__(self) -> "HeliusClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.__aexit__(*exc)

    @property
    def enabled(self) -> bool:
        return bool(settings.enable_helius and settings.helius_api_key)

    def _headers(self) -> dict[str, str]:
        return {"api-key": settings.helius_api_key}

    async def _rpc(self, method: str, params: list[Any]) -> Any:
        payload = {"jsonrpc": "2.0", "id": "meme-hunter", "method": method, "params": params}
        data = await self._client.post_json("/", payload, headers=self._headers())
        if isinstance(data, dict) and data.get("error"):
            raise UpstreamError("helius", f"rpc error: {data['error']}")
        return (data or {}).get("result")

    async def get_asset(self, mint: str) -> dict[str, Any] | None:
        """Full asset metadata including authorities and supply."""
        cache_key = f"helius:asset:{mint}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return cached
        result = await self._rpc("getAsset", [mint])
        await cache.set(cache_key, result or {}, ttl_seconds=900)
        return result

    async def security(self, mint: str) -> TokenSecurity:
        """Authority-level safety flags for a mint."""
        asset = await self.get_asset(mint) or {}
        token_info = asset.get("token_info") or {}
        authorities = asset.get("authorities") or []

        mint_auth_active = False
        freeze_auth_active = False
        for authority in authorities:
            authority_type = authority.get("authorityType")
            if authority_type == "mintTokens" and authority.get("address"):
                mint_auth_active = True
            if authority_type == "freezeAccount" and authority.get("address"):
                freeze_auth_active = True

        extensions = token_info.get("extensions") or {}
        transfer_fee = extensions.get("transferFeeConfig") or {}
        transfer_hook = extensions.get("transferHook") or {}
        metadata_pointer = extensions.get("metadataPointer") or {}

        security = TokenSecurity(
            mint=mint,
            mint_authority_active=mint_auth_active,
            freeze_authority_active=freeze_auth_active,
            metadata_mutable=bool(metadata_pointer.get("authority")),
            transfer_fee_present=bool(transfer_fee.get("transferFeeBasisPoints")),
            transfer_hook_present=bool(transfer_hook.get("programId")),
            mintable=mint_auth_active,
            closable=freeze_auth_active,
            total_supply=safe_float(token_info.get("supply")),
            provenance=Provenance(source="helius", url=f"https://explorer.helius.com/address/{mint}"),
        )

        if mint_auth_active:
            security.red_flags.append("Mint authority active - supply can be inflated")
        if freeze_auth_active:
            security.red_flags.append("Freeze authority active - transfers can be blocked")
        if security.metadata_mutable:
            security.red_flags.append("Metadata pointer is mutable - name/ticker can change")
        if security.transfer_fee_present:
            bps = safe_int(transfer_fee.get("transferFeeBasisPoints")) or 0
            security.red_flags.append(f"Transfer fee {bps} bps - sells are taxed")
        if security.transfer_hook_present:
            security.red_flags.append("Transfer hook present - custom transfer logic")

        return security

    async def top_holders(self, mint: str, limit: int = 20) -> HolderInfo:
        """Top holder concentration via DAS getTokenLargestAccounts."""
        cache_key = f"helius:top-holders:{mint}"
        cached = await cache.get(cache_key)
        if cached is not None:
            info = HolderInfo(**cached)
            info.provenance = Provenance(source="helius", url=f"https://explorer.helius.com/address/{mint}")
            return info

        result = await self._rpc("getTokenLargestAccounts", [mint]) or []
        asset = await self.get_asset(mint) or {}
        token_info = asset.get("token_info") or {}
        supply = safe_float(token_info.get("supply")) or 0.0

        amounts = []
        for entry in result[:limit]:
            amount = safe_float((entry.get("amount") or {}).get("amount"))
            if amount:
                amounts.append(amount)

        top10_pct = None
        top1_pct = None
        if supply > 0 and amounts:
            top10_pct = sum(amounts[:10]) / supply * 100
            top1_pct = amounts[0] / supply * 100

        info = HolderInfo(
            mint=mint,
            top10_pct=top10_pct,
            top1_pct=top1_pct,
            provenance=Provenance(source="helius", url=f"https://explorer.helius.com/address/{mint}"),
        )
        await cache.set(cache_key, info.__dict__, ttl_seconds=600)
        return info

    async def supply_distribution(self, mint: str) -> dict[str, Any]:
        """Supply breakdown by owner type as reported by DAS."""
        asset = await self.get_asset(mint) or {}
        return asset.get("supply") or token_info_of(asset)


def token_info_of(asset: dict[str, Any]) -> dict[str, Any]:
    return asset.get("token_info") or {}


class HeliusDisabledClient:
    """Stub used when HELIUS_API_KEY is absent - every call returns empty data."""

    @property
    def enabled(self) -> bool:
        return False

    async def __aenter__(self) -> "HeliusDisabledClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def security(self, mint: str) -> TokenSecurity:
        return TokenSecurity(mint=mint)

    async def top_holders(self, mint: str, limit: int = 20) -> HolderInfo:
        return HolderInfo(mint=mint)

    async def get_asset(self, mint: str) -> dict[str, Any] | None:
        return None


def build_helius_client() -> HeliusClient | HeliusDisabledClient:
    if settings.enable_helius and settings.helius_api_key:
        return HeliusClient()
    log.info("Helius disabled - no API key configured")
    return HeliusDisabledClient()
