"""RSS feeds for narrative tracking. Free, no key, no quota.

Narratives are not scored directly - they are matched against token names and
symbols so a story can explain why a token is moving.
"""
from __future__ import annotations

import logging
import re
from typing import Any
from xml.etree import ElementTree

from ..cache import cache
from ..config import settings
from ..http_client import HttpClient
from .base import NarrativeItem, Provenance

log = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


class RssNarrativeClient:
    def __init__(self) -> None:
        self._client = HttpClient(provider="rss", rate=0.2, burst=1.0)

    async def __aenter__(self) -> "RssNarrativeClient":
        await self._client.__aenter__()
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._client.__aexit__(*exc)

    @property
    def enabled(self) -> bool:
        return settings.enable_narratives and bool(settings.rss_feeds)

    async def fetch_feed(self, url: str) -> list[NarrativeItem]:
        """Parse one RSS feed into narrative items."""
        cache_key = f"rss:{url}"
        cached = await cache.get(cache_key)
        if cached is not None:
            return [NarrativeItem(**item) for item in cached]

        payload = await self._client.get_text(
            url, provider="rss",
            headers={"Accept": "application/rss+xml, application/xml, text/xml, */*"})
        items = _parse_rss(payload)
        await cache.set(cache_key, [i.__dict__ for i in items], ttl_seconds=600)
        return items

    async def collect(self) -> list[NarrativeItem]:
        """Fetch every configured feed and merge by headline keyword."""
        merged: dict[str, NarrativeItem] = {}
        for url in settings.rss_feeds:
            try:
                items = await self.fetch_feed(url)
            except Exception as exc:  # a dead feed must not break the others
                log.warning("rss feed failed (%s): %s", url, exc)
                continue
            for item in items:
                existing = merged.get(item.key)
                if existing is None:
                    merged[item.key] = item
                    continue
                existing.mentions += item.mentions
                existing.links = list(dict.fromkeys(existing.links + item.links))[:8]
                existing.sources = list(dict.fromkeys(existing.sources + item.sources))
        return list(merged.values())


def _parse_rss(payload: str) -> list[NarrativeItem]:
    if not payload or not payload.strip().startswith("<"):
        return []
    try:
        root = ElementTree.fromstring(payload)
    except ElementTree.ParseError as exc:
        log.warning("rss parse failed: %s", exc)
        return []

    out: list[NarrativeItem] = []
    for node in root.iter("item"):
        title = _text(node.find("title"))
        link = _text(node.find("link"))
        published = _text(node.find("pubDate"))
        if not title:
            continue
        out.append(NarrativeItem(
            key=_keyword(title),
            label=title,
            mentions=1,
            links=[link] if link else [],
            sources=[_source_from_link(link)],
            published_at=published,
            provenance=Provenance(source="rss", url=link, confidence="medium"),
        ))
    return out


def _source_from_link(link: str) -> str:
    if not link:
        return "rss"
    match = re.match(r"https?://([^/]+)", link)
    return match.group(1) if match else "rss"


def _text(node: Any) -> str:
    if node is None or node.text is None:
        return ""
    return _WS_RE.sub(" ", _TAG_RE.sub("", node.text)).strip()


def _keyword(title: str) -> str:
    """Reduce a headline to a comparable keyword cluster."""
    stop = {"the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
            "is", "are", "be", "as", "at", "by", "from", "it", "its", "this", "that"}
    words = re.findall(r"[A-Za-z0-9$]{3,}", title.lower())
    picked = [w for w in words if w not in stop]
    return "-".join(picked[:3]) if picked else "misc"


class RssDisabledClient:
    enabled = False

    async def __aenter__(self) -> "RssDisabledClient":
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def collect(self) -> list[NarrativeItem]:
        return []


def build_rss_client() -> RssNarrativeClient | RssDisabledClient:
    if settings.enable_narratives and settings.rss_feeds:
        return RssNarrativeClient()
    return RssDisabledClient()
