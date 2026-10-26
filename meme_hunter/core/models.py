"""SQLAlchemy ORM models."""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Token(Base):
    """A tracked token and its latest observed metrics."""

    __tablename__ = "tokens"

    mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    symbol: Mapped[str] = mapped_column(String(32), default="")
    name: Mapped[str] = mapped_column(String(128), default="")
    dex: Mapped[str] = mapped_column(String(32), default="")
    pair_address: Mapped[str] = mapped_column(String(64), default="", index=True)
    chain: Mapped[str] = mapped_column(String(16), default="solana")

    price_usd: Mapped[float | None] = mapped_column(Float, default=None)
    market_cap: Mapped[float | None] = mapped_column(Float, default=None)
    liquidity_usd: Mapped[float | None] = mapped_column(Float, default=None)
    volume_24h: Mapped[float | None] = mapped_column(Float, default=None)
    volume_1h: Mapped[float | None] = mapped_column(Float, default=None)
    volume_5m: Mapped[float | None] = mapped_column(Float, default=None)
    change_5m: Mapped[float | None] = mapped_column(Float, default=None)
    change_1h: Mapped[float | None] = mapped_column(Float, default=None)
    change_24h: Mapped[float | None] = mapped_column(Float, default=None)
    buys_1h: Mapped[int | None] = mapped_column(Integer, default=None)
    sells_1h: Mapped[int | None] = mapped_column(Integer, default=None)
    holders: Mapped[int | None] = mapped_column(Integer, default=None)
    top10_pct: Mapped[float | None] = mapped_column(Float, default=None)
    pair_created_at: Mapped[int | None] = mapped_column(Integer, default=None)

    website: Mapped[str] = mapped_column(String(256), default="")
    twitter: Mapped[str] = mapped_column(String(256), default="")

    prospect_score: Mapped[int | None] = mapped_column(Integer, default=None)
    risk_score: Mapped[int | None] = mapped_column(Integer, default=None)
    data_completeness: Mapped[str] = mapped_column(String(16), default="partial")
    last_scored_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    snapshots: Mapped[list["TokenSnapshot"]] = relationship(
        back_populates="token", cascade="all, delete-orphan")
    signals: Mapped[list["Signal"]] = relationship(
        back_populates="token", cascade="all, delete-orphan")

    @property
    def age_minutes(self) -> float | None:
        if not self.pair_created_at:
            return None
        created = datetime.fromtimestamp(self.pair_created_at / 1000, tz=timezone.utc)
        return (utcnow() - created).total_seconds() / 60.0


class TokenSnapshot(Base):
    """Historical point of a token - the basis for momentum and holder growth."""

    __tablename__ = "token_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), index=True)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    price_usd: Mapped[float | None] = mapped_column(Float, default=None)
    market_cap: Mapped[float | None] = mapped_column(Float, default=None)
    liquidity_usd: Mapped[float | None] = mapped_column(Float, default=None)
    volume_1h: Mapped[float | None] = mapped_column(Float, default=None)
    volume_24h: Mapped[float | None] = mapped_column(Float, default=None)
    holders: Mapped[int | None] = mapped_column(Integer, default=None)

    token: Mapped[Token] = relationship(back_populates="snapshots")


Index("ix_snapshot_mint_time", TokenSnapshot.mint, TokenSnapshot.captured_at)


class Signal(Base):
    """A generated alert about a token, with the evidence that produced it."""

    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(ForeignKey("tokens.mint", ondelete="CASCADE"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)

    prospect_score: Mapped[int] = mapped_column(Integer, default=0)
    risk_score: Mapped[int] = mapped_column(Integer, default=0)
    reasons: Mapped[str] = mapped_column(Text, default="")
    skip_reasons: Mapped[str] = mapped_column(Text, default="")
    payload: Mapped[str] = mapped_column(Text, default="{}")
    sent: Mapped[bool] = mapped_column(Boolean, default=False)

    token: Mapped[Token] = relationship(back_populates="signals")


class SmartWallet(Base):
    """A wallet tracked for realised performance - never labelled insider without data."""

    __tablename__ = "smart_wallets"

    address: Mapped[str] = mapped_column(String(64), primary_key=True)
    first_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    trades_count: Mapped[int] = mapped_column(Integer, default=0)
    wins: Mapped[int] = mapped_column(Integer, default=0)
    losses: Mapped[int] = mapped_column(Integer, default=0)
    realised_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    realised_pnl_pct: Mapped[float] = mapped_column(Float, default=0.0)
    avg_hold_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    early_buys: Mapped[int] = mapped_column(Integer, default=0)
    cluster_id: Mapped[str | None] = mapped_column(String(64), default=None, index=True)
    confidence: Mapped[str] = mapped_column(String(16), default="low")


class WalletTrade(Base):
    __tablename__ = "wallet_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    wallet: Mapped[str] = mapped_column(ForeignKey("smart_wallets.address", ondelete="CASCADE"), index=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    side: Mapped[str] = mapped_column(String(8))
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    price_usd: Mapped[float] = mapped_column(Float, default=0.0)
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    token_age_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    realised_pnl_pct: Mapped[float | None] = mapped_column(Float, default=None)


Index("ix_trade_wallet_mint", WalletTrade.wallet, WalletTrade.mint)


class Narrative(Base):
    """A tracked story/keyword cluster with a measured mention velocity."""

    __tablename__ = "narratives"

    key: Mapped[str] = mapped_column(String(128), primary_key=True)
    label: Mapped[str] = mapped_column(String(160), default="")
    mentions: Mapped[int] = mapped_column(Integer, default=0)
    previous_mentions: Mapped[int] = mapped_column(Integer, default=0)
    sources: Mapped[str] = mapped_column(Text, default="")
    links: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    @property
    def velocity(self) -> float:
        if not self.previous_mentions:
            return 0.0
        return (self.mentions - self.previous_mentions) / max(self.previous_mentions, 1)


class PaperTrade(Base):
    """A simulated trade. No wallet, no keys, no real execution."""

    __tablename__ = "paper_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mint: Mapped[str] = mapped_column(String(64), index=True)
    symbol: Mapped[str] = mapped_column(String(32), default="")
    side: Mapped[str] = mapped_column(String(8), default="long")
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)

    entry_price: Mapped[float] = mapped_column(Float, default=0.0)
    exit_price: Mapped[float | None] = mapped_column(Float, default=None)
    amount_usd: Mapped[float] = mapped_column(Float, default=0.0)
    tokens: Mapped[float] = mapped_column(Float, default=0.0)
    fees_usd: Mapped[float] = mapped_column(Float, default=0.0)
    slippage_usd: Mapped[float] = mapped_column(Float, default=0.0)
    pnl_usd: Mapped[float | None] = mapped_column(Float, default=None)
    pnl_pct: Mapped[float | None] = mapped_column(Float, default=None)

    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    note: Mapped[str] = mapped_column(Text, default="")


class WatchlistItem(Base):
    __tablename__ = "watchlist"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mint: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    note: Mapped[str] = mapped_column(Text, default="")

    __table_args__ = (UniqueConstraint("user_id", "mint", name="uq_watchlist_user_mint"),)
