"""Central configuration. Secrets come from environment variables only."""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int(name: str, default: int) -> int:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _float(name: str, default: float) -> float:
    raw = os.getenv(name)
    if not raw or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [item.strip() for item in os.getenv(name, default).split(",") if item.strip()]


@dataclass(frozen=True)
class Settings:
    telegram_bot_token: str = field(default_factory=lambda: os.getenv("TELEGRAM_BOT_TOKEN", ""))
    admin_user_ids: list[int] = field(default_factory=lambda: [int(x) for x in _list("ADMIN_USER_IDS") if x.isdigit()])

    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    redis_url: str = field(default_factory=lambda: os.getenv("REDIS_URL", ""))

    helius_api_key: str = field(default_factory=lambda: os.getenv("HELIUS_API_KEY", ""))

    enable_narratives: bool = field(default_factory=lambda: _bool("ENABLE_NARRATIVES", True))
    enable_helius: bool = field(default_factory=lambda: _bool("ENABLE_HELIUS", True))

    min_liquidity_usd: float = field(default_factory=lambda: _float("MIN_LIQUIDITY_USD", 8000.0))
    max_market_cap_usd: float = field(default_factory=lambda: _float("MAX_MARKET_CAP_USD", 1_500_000.0))
    min_volume_24h_usd: float = field(default_factory=lambda: _float("MIN_VOLUME_24H_USD", 10_000.0))
    max_token_age_minutes: int = field(default_factory=lambda: _int("MAX_TOKEN_AGE_MINUTES", 2880))
    min_holders: int = field(default_factory=lambda: _int("MIN_HOLDERS", 150))

    min_prospect_score: int = field(default_factory=lambda: _int("MIN_PROSPECT_SCORE", 62))
    max_risk_score: int = field(default_factory=lambda: _int("MAX_RISK_SCORE", 55))
    signal_cooldown_seconds: int = field(default_factory=lambda: _int("SIGNAL_COOLDOWN_SECONDS", 1800))

    http_timeout_seconds: float = field(default_factory=lambda: _float("HTTP_TIMEOUT_SECONDS", 20.0))
    http_max_retries: int = field(default_factory=lambda: _int("HTTP_MAX_RETRIES", 3))
    http_user_agent: str = field(default_factory=lambda: os.getenv(
        "HTTP_USER_AGENT", "MemeHunterAI/0.1 (analytics-only)"))
    dexscreener_rpm: int = field(default_factory=lambda: _int("DEXSCREENER_RPM", 240))
    helius_das_rps: float = field(default_factory=lambda: _float("HELIUS_DAS_RPS", 1.8))
    jupiter_rps: float = field(default_factory=lambda: _float("JUPITER_RPS", 0.45))

    paper_initial_capital_usd: float = field(default_factory=lambda: _float("PAPER_INITIAL_CAPITAL_USD", 1000.0))
    paper_max_position_pct: float = field(default_factory=lambda: _float("PAPER_MAX_POSITION_PCT", 0.30))
    paper_fee_bps: float = field(default_factory=lambda: _float("PAPER_FEE_BPS", 60.0))

    discovery_interval_seconds: int = field(default_factory=lambda: _int("DISCOVERY_INTERVAL_SECONDS", 180))
    watcher_interval_seconds: int = field(default_factory=lambda: _int("WATCHER_INTERVAL_SECONDS", 300))
    max_tracked_tokens: int = field(default_factory=lambda: _int("MAX_TRACKED_TOKENS", 300))

    rss_feeds: list[str] = field(default_factory=lambda: _list(
        "RSS_FEEDS",
        "https://cointelegraph.com/rss,https://decrypt.co/feed,https://cryptoslate.com/feed/"))

    log_level: str = field(default_factory=lambda: os.getenv("LOG_LEVEL", "INFO"))
    environment: str = field(default_factory=lambda: os.getenv("ENVIRONMENT", "development"))

    @property
    def is_production(self) -> bool:
        return self.environment.lower() in {"production", "prod"}

    def missing_requirements(self) -> list[str]:
        missing: list[str] = []
        if not self.telegram_bot_token:
            missing.append("TELEGRAM_BOT_TOKEN")
        if not self.database_url:
            missing.append("DATABASE_URL")
        if self.enable_helius and not self.helius_api_key:
            missing.append("HELIUS_API_KEY (or set ENABLE_HELIUS=false)")
        return missing

    def validate(self) -> None:
        missing = self.missing_requirements()
        if missing:
            raise RuntimeError(f"Missing required configuration: {', '.join(missing)}")


settings = Settings()
