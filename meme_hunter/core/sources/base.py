"""Shared dataclasses for source data. Every field carries provenance."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class Provenance:
    source: str
    fetched_at: str = field(default_factory=now_iso)
    url: str = ""
    confidence: str = "high"  # high | medium | low


@dataclass
class PoolInfo:
    chain: str
    dex: str
    pair_address: str
    base_mint: str
    base_symbol: str
    base_name: str
    quote_symbol: str
    price_usd: float | None = None
    price_native: float | None = None
    market_cap: float | None = None
    fdv: float | None = None
    liquidity_usd: float | None = None
    volume_5m: float | None = None
    volume_1h: float | None = None
    volume_24h: float | None = None
    change_5m: float | None = None
    change_1h: float | None = None
    change_24h: float | None = None
    buys_5m: int | None = None
    sells_5m: int | None = None
    buys_1h: int | None = None
    sells_1h: int | None = None
    buys_24h: int | None = None
    sells_24h: int | None = None
    pair_created_at: int | None = None
    website: str = ""
    twitter: str = ""
    provenance: Provenance | None = None

    @property
    def age_minutes(self) -> float | None:
        if not self.pair_created_at:
            return None
        created = datetime.fromtimestamp(self.pair_created_at / 1000, tz=timezone.utc)
        return (datetime.now(timezone.utc) - created).total_seconds() / 60.0

    @property
    def buy_sell_ratio(self) -> float | None:
        if not self.buys_1h or not self.sells_1h:
            return None
        return self.buys_1h / max(self.sells_1h, 1)


@dataclass
class HolderInfo:
    mint: str
    total_holders: int | None = None
    top10_pct: float | None = None
    top1_pct: float | None = None
    dev_pct: float | None = None
    lp_tokens: float | None = None
    lp_locked_pct: float | None = None
    clusters: int | None = None
    provenance: Provenance | None = None


@dataclass
class TokenSecurity:
    mint: str
    mint_authority_active: bool = False
    freeze_authority_active: bool = False
    metadata_mutable: bool = False
    transfer_fee_present: bool = False
    transfer_hook_present: bool = False
    mintable: bool = False
    closable: bool = False
    total_supply: float | None = None
    red_flags: list[str] = field(default_factory=list)
    provenance: Provenance | None = None

    @property
    def passed(self) -> bool:
        return not self.red_flags


@dataclass
class NarrativeItem:
    key: str
    label: str
    mentions: int = 0
    links: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    published_at: str = ""
    provenance: Provenance | None = None


@dataclass
class WalletActivity:
    address: str
    mint: str
    side: str
    amount_usd: float
    price_usd: float
    token_age_minutes: float
    timestamp: datetime
    provenance: Provenance | None = None


def safe_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        return float(value)
    except (TypeError, ValueError):
        return None


def safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
