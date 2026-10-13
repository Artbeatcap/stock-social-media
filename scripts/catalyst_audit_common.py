"""
Shared helpers for catalyst recall and featured-mover quality audits.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

HIT_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "by",
    "with", "from", "as", "is", "are", "was", "were", "be", "its", "it", "s",
    "after", "before", "over", "up", "down", "vs", "amid", "about", "into",
}


def trade_date_today() -> str:
    return datetime.now(NY).strftime("%Y-%m-%d")


def audit_as_of_for_trade_date(trade_date: str, now: Optional[datetime] = None) -> datetime:
    """
    When auditing before 9:15pm ET on the trade date, cap the news window at ``now``
    so we do not score against headlines that were not published yet at audit time.
    """
    now = now or datetime.now(NY)
    day = datetime.strptime(trade_date, "%Y-%m-%d").date()
    recall_cutoff = NY.localize(
        datetime.combine(day, datetime.strptime("21:15", "%H:%M").time())
    )
    if now.date() == day and now < recall_cutoff:
        return now
    return recall_cutoff


def _token_set(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{3,}", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS}


def token_overlap_ratio(a: str, b: str) -> float:
    sa, sb = _token_set(a), _token_set(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def normalize_mover(raw: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct", 0)
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0),
        "catalyst": str(raw.get("catalyst") or "").strip(),
        "price": raw.get("price") or raw.get("last"),
        "volume": raw.get("volume"),
    }


def load_movers_from_telemetry(trade_date: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    from validation_telemetry import load_movers

    gainers = [normalize_mover(m) for m in load_movers("gainers", trade_date=trade_date)]
    losers = [normalize_mover(m) for m in load_movers("losers", trade_date=trade_date)]
    return gainers, losers


def load_postmarket_run(trade_date: str) -> Optional[Dict[str, Any]]:
    from validation_telemetry import load_run_snapshot

    return load_run_snapshot("postmarket", trade_date=trade_date)


def bootstrap_telemetry_if_missing(trade_date: str) -> bool:
    from validation_telemetry import bootstrap_telemetry_if_missing as boot

    return boot(trade_date) is not None


def filter_news_last_hours(
    items: List[Dict[str, Any]],
    hours: float,
    as_of: datetime,
) -> List[Dict[str, Any]]:
    cutoff = as_of - timedelta(hours=hours)
    filtered: List[Dict[str, Any]] = []
    for item in items:
        ts = item.get("published_dt")
        if ts is None:
            continue
        if ts.tzinfo is None:
            ts = NY.localize(ts)
        else:
            ts = ts.astimezone(NY)
        if cutoff <= ts <= as_of:
            filtered.append(item)
    return filtered


def parse_news_items(raw_items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    parsed: List[Dict[str, Any]] = []
    for item in raw_items:
        published = item.get("published_utc") or item.get("datetime")
        published_dt: Optional[datetime] = None
        if isinstance(published, str):
            try:
                published_dt = datetime.fromisoformat(published.replace("Z", "+00:00")).astimezone(NY)
            except ValueError:
                pass
        elif isinstance(published, (int, float)):
            published_dt = datetime.fromtimestamp(published, tz=NY)
        headline = item.get("title") or item.get("headline") or ""
        sentiment = str(item.get("sentiment") or "").lower()
        if not sentiment:
            insights = item.get("insights") or []
            if isinstance(insights, list) and insights:
                sentiment = str(insights[0].get("sentiment") or "").lower()
        parsed.append(
            {
                "headline": headline,
                "sentiment": sentiment,
                "published_dt": published_dt,
                "url": item.get("article_url") or item.get("url") or "",
                "publisher": item.get("publisher"),
            }
        )
    return parsed


def score_headline(
    headline: Dict[str, Any],
    as_of: datetime,
    want_positive: bool,
    window_hours: float = 24.0,
) -> float:
    published = headline.get("published_dt")
    if not published:
        recency = 0.0
    else:
        age_hours = max(0.0, (as_of - published).total_seconds() / 3600.0)
        recency = max(0.0, 1.0 - (age_hours / window_hours))

    sentiment = headline.get("sentiment") or ""
    if want_positive:
        sentiment_score = 1.0 if sentiment == "positive" else (0.5 if sentiment in ("neutral", "") else 0.0)
    else:
        sentiment_score = 1.0 if sentiment == "negative" else (0.5 if sentiment in ("neutral", "") else 0.0)

    return RECENCY_WEIGHT * recency + SENTIMENT_WEIGHT * sentiment_score


def rank_top_headlines(
    headlines: List[Dict[str, Any]],
    as_of: datetime,
    want_positive: bool,
    limit: int = 5,
) -> List[Dict[str, Any]]:
    scored = []
    for h in headlines:
        scored.append({**h, "score": score_headline(h, as_of, want_positive)})
    scored.sort(key=lambda x: x["score"], reverse=True)
    return scored[:limit]


def classify_recall(
    pipeline_catalyst: str,
    ranked_headlines: List[Dict[str, Any]],
) -> Tuple[str, str, float]:
    """Return verdict (hit/miss), best Massive headline, overlap ratio."""
    if not pipeline_catalyst:
        best = ranked_headlines[0]["headline"] if ranked_headlines else "(no headlines)"
        return "miss", best, 0.0

    best_headline = ""
    best_overlap = 0.0
    for h in ranked_headlines:
        overlap = token_overlap_ratio(pipeline_catalyst, h.get("headline", ""))
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = h.get("headline", "")

    if not ranked_headlines:
        return "miss", "(no headlines in window)", 0.0

    verdict = "hit" if best_overlap >= HIT_OVERLAP_THRESHOLD else "miss"
    if not best_headline and ranked_headlines:
        best_headline = ranked_headlines[0].get("headline", "")
    return verdict, best_headline, best_overlap


def is_weekday_et(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(NY)
    return now.weekday() < 5


def et_window_match(now: datetime, hour: int, minute: int, window_minutes: int = 45) -> bool:
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    delta = abs((now - target).total_seconds()) / 60.0
    return delta <= window_minutes
