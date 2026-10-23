"""Shared scoring and telemetry loading for catalyst quality audits."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from telemetry_snapshot import load_json
from validation_telemetry import (
    audit_as_of_for_trade_date,
    day_dir,
    ensure_telemetry_dirs,
    resolve_audit_trade_date,
)

NY = pytz.timezone("America/New_York")

TOKEN_STOP = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "with", "from", "as", "is", "are", "was", "were", "its", "it", "s", "vs",
}

NEGATIVE_HINTS = (
    "miss", "misses", "cut", "cuts", "downgrade", "downgrades", "falls", "fall",
    "drops", "drop", "plunge", "plunges", "slump", "slumps", "warning", "lawsuit",
    "probe", "investigation", "recall", "bankruptcy", "layoff", "layoffs",
)
POSITIVE_HINTS = (
    "beat", "beats", "raise", "raises", "upgrade", "upgrades", "surge", "surges",
    "soar", "soars", "jump", "jumps", "rally", "rallies", "record", "approval",
    "deal", "partnership", "buyback", "dividend",
)

HIT_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 2 and w not in TOKEN_STOP}


def token_overlap(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta)


def headline_sentiment_score(headline: str, want_positive: bool) -> float:
    text = (headline or "").lower()
    pos = sum(1 for hint in POSITIVE_HINTS if hint in text)
    neg = sum(1 for hint in NEGATIVE_HINTS if hint in text)
    if want_positive:
        if pos and not neg:
            return 1.0
        if pos > neg:
            return 0.75
        if neg > pos:
            return 0.15
        return 0.5
    if neg and not pos:
        return 1.0
    if neg > pos:
        return 0.75
    if pos > neg:
        return 0.15
    return 0.5


def parse_published_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value, tz=pytz.UTC)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def recency_score(published: Optional[datetime], window_start: datetime, window_end: datetime) -> float:
    if published is None:
        return 0.0
    if published.tzinfo is None:
        published = pytz.UTC.localize(published)
    start = window_start.astimezone(pytz.UTC)
    end = window_end.astimezone(pytz.UTC)
    pub = published.astimezone(pytz.UTC)
    if pub < start or pub > end:
        return 0.0
    span = (end - start).total_seconds()
    if span <= 0:
        return 1.0
    age = (end - pub).total_seconds()
    return max(0.0, min(1.0, 1.0 - age / span))


def score_headline(
    headline: str,
    published: Optional[datetime],
    window_start: datetime,
    window_end: datetime,
    want_positive: bool,
) -> float:
    return (
        RECENCY_WEIGHT * recency_score(published, window_start, window_end)
        + SENTIMENT_WEIGHT * headline_sentiment_score(headline, want_positive)
    )


def rank_headlines(
    articles: List[Dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    want_positive: bool,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    scored: List[Dict[str, Any]] = []
    for item in articles:
        title = str(item.get("headline") or item.get("title") or "")
        published = parse_published_utc(item.get("published_utc") or item.get("datetime"))
        if published and (published < window_start or published > window_end):
            continue
        scored.append(
            {
                "title": title,
                "published_utc": published.isoformat() if published else "",
                "score": score_headline(title, published, window_start, window_end, want_positive),
                "sentiment_guess": headline_sentiment_score(title, want_positive),
            }
        )
    scored.sort(key=lambda row: row["score"], reverse=True)
    return scored[:limit]


def is_recall_hit(pipeline_catalyst: str, best_headline: str, top_headlines: List[str]) -> bool:
    if not pipeline_catalyst or not pipeline_catalyst.strip():
        return False
    if token_overlap(pipeline_catalyst, best_headline) >= HIT_OVERLAP_THRESHOLD:
        return True
    return any(token_overlap(pipeline_catalyst, h) >= HIT_OVERLAP_THRESHOLD for h in top_headlines)


def load_movers_for_trade_date(trade_date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    ensure_telemetry_dirs()
    ddir = day_dir(trade_date)
    gainers = load_json(ddir / "top_gainers.json") or []
    losers = load_json(ddir / "top_losers.json") or []
    post = load_json(ddir / "postmarket_run.json") or {}
    return gainers, losers, post


def bootstrap_telemetry_if_missing(trade_date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    gainers, losers, post = load_movers_for_trade_date(trade_date)
    if gainers or losers:
        return gainers, losers, post

    try:
        from market_internals import get_market_internals
        from massive_client import enrich_movers_with_news

        internals = get_market_internals()
        movers = internals.get("movers") or {}
        raw_g = [
            {
                "symbol": m["symbol"],
                "change_percentage": m["pct"],
                "last": m.get("last"),
            }
            for m in movers.get("gainers") or []
        ]
        raw_l = [
            {
                "symbol": m["symbol"],
                "change_percentage": m["pct"],
                "last": m.get("last"),
            }
            for m in movers.get("losers") or []
        ]
        enriched = enrich_movers_with_news(raw_g + raw_l, limit=len(raw_g) + len(raw_l))
        by_ticker = {r["ticker"]: r for r in enriched}
        gainers = [
            by_ticker.get(
                str(m["symbol"]).upper(),
                {"ticker": m["symbol"], "change_percentage": m["change_percentage"]},
            )
            for m in raw_g
        ]
        losers = [
            by_ticker.get(
                str(m["symbol"]).upper(),
                {"ticker": m["symbol"], "change_percentage": m["change_percentage"]},
            )
            for m in raw_l
        ]
        post = {"featured_stock": gainers[0]["ticker"] if gainers else None, "bootstrapped": True}
    except Exception:
        gainers, losers, post = [], [], {}

    return gainers, losers, post


def recall_window_for_trade_date(trade_date) -> Tuple[datetime, datetime]:
    end = audit_as_of_for_trade_date(trade_date)
    start = end - timedelta(hours=24)
    return start, end
