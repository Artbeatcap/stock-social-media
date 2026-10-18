"""Shared helpers for catalyst recall and featured-mover quality audits."""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

RECALL_HIT_OVERLAP = 0.35
RECALL_RECENCY_WEIGHT = 0.55
RECALL_SENTIMENT_WEIGHT = 0.45

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def token_overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def effective_audit_trade_date(now: Optional[datetime] = None) -> date:
    """Weekend cloud runs default to the prior Friday session."""
    now = now or datetime.now(NY)
    d = now.date()
    if d.weekday() >= 5:
        return d - timedelta(days=d.weekday() - 4)
    return d


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """
    End of the 24h news window for recall audit.
    Before 9:15pm ET on the trade date, cap at ``now`` (no future window).
    """
    now = now or datetime.now(NY)
    recall_cutoff = NY.localize(datetime.combine(trade_date, time(21, 15)))
    if now.date() == trade_date and now < recall_cutoff:
        return now
    return recall_cutoff


def news_window_start(as_of: datetime, hours: int = 24) -> datetime:
    return as_of - timedelta(hours=hours)


def sentiment_for_ticker(item: dict, ticker: str) -> str:
    insights = item.get("insights") or []
    ticker = ticker.upper()
    for row in insights:
        if str(row.get("ticker") or "").upper() == ticker:
            return str(row.get("sentiment") or "").lower()
    if insights:
        return str(insights[0].get("sentiment") or "").lower()
    return str(item.get("sentiment") or "").lower()


def sentiment_match_score(sentiment: str, is_gainer: bool) -> float:
    wanted = "positive" if is_gainer else "negative"
    if sentiment == wanted:
        return 1.0
    if sentiment in ("neutral", ""):
        return 0.5
    return 0.0


def recency_score(published: datetime, window_start: datetime, as_of: datetime) -> float:
    span = (as_of - window_start).total_seconds()
    if span <= 0:
        return 0.0
    age = (as_of - published).total_seconds()
    if age < 0:
        return 0.0
    return max(0.0, 1.0 - age / span)


def score_headline(
    headline: str,
    published: datetime,
    sentiment: str,
    *,
    is_gainer: bool,
    window_start: datetime,
    as_of: datetime,
) -> float:
    return (
        RECALL_RECENCY_WEIGHT * recency_score(published, window_start, as_of)
        + RECALL_SENTIMENT_WEIGHT * sentiment_match_score(sentiment, is_gainer)
    )


def parse_published_utc(raw: Any) -> Optional[datetime]:
    if not raw:
        return None
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=pytz.UTC).astimezone(NY)
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(NY)
    except ValueError:
        return None


def day_telemetry_dir(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def load_movers_for_trade_date(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    day = day_telemetry_dir(trade_date)
    gainers: List[Dict[str, Any]] = []
    losers: List[Dict[str, Any]] = []
    for name, bucket in (("top_gainers.json", gainers), ("top_losers.json", losers)):
        path = day / name
        if path.is_file():
            bucket.extend(json.loads(path.read_text(encoding="utf-8")))
    if gainers or losers:
        return gainers, losers

    for run_file in ("postmarket_run.json", "premarket_run.json"):
        path = day / run_file
        if not path.is_file():
            continue
        data = json.loads(path.read_text(encoding="utf-8"))
        gainers = list(data.get("top_gainers") or [])
        losers = list(data.get("top_losers") or [])
        if gainers or losers:
            return gainers, losers
    return [], []


def load_postmarket_featured(trade_date: date) -> Optional[str]:
    path = day_telemetry_dir(trade_date) / "postmarket_run.json"
    if not path.is_file():
        return None
    data = json.loads(path.read_text(encoding="utf-8"))
    featured = data.get("featured_stock")
    return str(featured).upper() if featured else None


def bootstrap_telemetry_if_missing(trade_date: date) -> bool:
    """
    Build top_gainers/losers from market_internals + Massive news when prod snapshots absent.
    Returns True if movers were written.
    """
    gainers, losers = load_movers_for_trade_date(trade_date)
    if gainers or losers:
        return False

    try:
        from market_internals import fetch_movers_and_breadth
        from massive_client import enrich_movers_with_news
    except ImportError:
        return False

    mb = fetch_movers_and_breadth(n=5)
    raw_gainers = [
        {"symbol": m["symbol"], "change_percentage": m["pct"], "last": m.get("last")}
        for m in (mb.get("movers") or {}).get("gainers") or []
    ]
    raw_losers = [
        {"symbol": m["symbol"], "change_percentage": m["pct"], "last": m.get("last")}
        for m in (mb.get("movers") or {}).get("losers") or []
    ]
    enriched_g = enrich_movers_with_news(raw_gainers, limit=len(raw_gainers) or 5)
    enriched_l = enrich_movers_with_news(raw_losers, limit=len(raw_losers) or 5)

    day = day_telemetry_dir(trade_date)
    day.mkdir(parents=True, exist_ok=True)
    (day / "top_gainers.json").write_text(json.dumps(enriched_g, indent=2), encoding="utf-8")
    (day / "top_losers.json").write_text(json.dumps(enriched_l, indent=2), encoding="utf-8")
    meta = {
        "bootstrapped_at": datetime.now(NY).isoformat(),
        "source": "market_internals+enrich_movers_with_news",
        "trade_date": trade_date.isoformat(),
    }
    (day / "bootstrap_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return True
