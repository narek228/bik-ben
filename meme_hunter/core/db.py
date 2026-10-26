"""Async engine, session factory and schema bootstrap."""
from __future__ import annotations

import logging
from typing import Any, AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from .config import settings
from .models import Base

log = logging.getLogger(__name__)

_engine: Any = None
_session_factory: Any = None


def _async_url(url: str) -> str:
    """Normalise a DATABASE_URL into an async SQLAlchemy URL."""
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+asyncpg://", 1)
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+asyncpg://", 1)
    if url.startswith("sqlite://"):
        return url.replace("sqlite://", "sqlite+aiosqlite://", 1)
    return url


def get_engine() -> Any:
    global _engine
    if _engine is None:
        url = _async_url(settings.database_url)
        kwargs: dict[str, Any] = {"echo": False, "pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["poolclass"] = NullPool
        else:
            kwargs["pool_size"] = 5
            kwargs["max_overflow"] = 10
        _engine = create_async_engine(url, **kwargs)
        log.info("database engine created")
    return _engine


def get_session_factory() -> Any:
    global _session_factory
    if _session_factory is None:
        _session_factory = async_sessionmaker(
            bind=get_engine(), class_=AsyncSession, expire_on_commit=False, autoflush=False)
    return _session_factory


async def init_db() -> None:
    """Create tables if they do not exist. Safe to call on every startup."""
    engine = get_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    log.info("database schema ready")


async def close_db() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None


async def session_scope() -> AsyncIterator[AsyncSession]:
    """Async context manager yielding a session with commit/rollback handling."""
    factory = get_session_factory()
    async with factory() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
