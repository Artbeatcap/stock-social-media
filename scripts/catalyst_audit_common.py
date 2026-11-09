"""
Shared helpers for catalyst recall / featured-mover quality audits.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
RECALL_AUDIT_TIME = time(21, 15)
FEATURED_AUDIT_TIME = time(17, 30)
HIT_OVERLAP_THRESHOLD = 0.35

REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"

_STOPWORDS = {
    "a", "an", "the", "and", "or", "for", "to", "of", "in", "on", "at", "by", "with",
    "from", "as", "is", "are", "was", "were", "be", "been", "its", "it", "s", "after",
    "before", "over", "under", "vs", "et", "st", "inc", "corp", "ltd", "llc", "co",
    "q", "q1", "q2", "q3", "q4", "fy", "yoy",
}

_POSITIVE_HINTS = (
    "beat", "beats", "surge", "soar", "jump", "rally", "gain", "gains", "upgrade",
    "raised", "raises", "raise", "record", "strong", "growth", "positive", "bullish",
    "profit", "profits", "exceed", "exceeds", "outperform", "approval", "deal",
    "acquisition", "buyout", "upgraded",
)

_NEGATIVE_HINTS = (
    "miss", "misses", "missed", "drop", "drops", "fall", "falls", "plunge", "plunges",
    "sink", "sinks", "cut", "cuts", "downgrade", "downgraded", "warning", "weak",
    "loss", "losses", "lawsuit", "investigation", "fraud", "bankruptcy", "layoff",
    "layoffs", "recall", "halt", "delayed", "delay", "negative", "bearish", "decline",
)


def telemetry_day_dir(trade_date: str) -> Path:
    return TELEMETRY_ROOT / trade_date


def reports_dir() -> Path:
    return TELEMETRY_ROOT / "reports"


def research_log_dir() -> Path:
    return TELEMETRY_ROOT / "research_log"


def _is_weekend(d: date) -> bool:
    return d.weekday() >= 5


def _prior_trading_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while _is_weekend(cur):
        cur -= timedelta(days=1)
    return cur


def resolve_audit_trade_date(now: Optional[datetime] = None) -> str:
    """
    Default trade date for audits. Before 5:30pm ET, use the prior US session
    (so a Monday-morning cloud run grades Friday's post-market email).
    """
    now = now or datetime.now(tz=NY)
    today = now.date()
    if now.time() < FEATURED_AUDIT_TIME:
        session = _prior_trading_day(today)
    else:
        session = today if not _is_weekend(today) else _prior_trading_day(today)
    return session.isoformat()


def audit_as_of_for_trade_date(trade_date: str, now: Optional[datetime] = None) -> datetime:
    """
    Upper bound for the 24h news window on recall audits.
    Same calendar day before 9:15pm ET → cap at now; otherwise 9:15pm ET on trade_date.
    """
    now = now or datetime.now(tz=NY)
    session = datetime.strptime(trade_date, "%Y-%m-%d").date()
    recall_end = NY.localize(datetime.combine(session, RECALL_AUDIT_TIME))
    if now.date() == session and now < recall_end:
        return now
    return recall_end


def news_window_start(as_of: datetime) -> datetime:
    return as_of - timedelta(hours=24)


def ensure_telemetry_layout() -> None:
    for path in (TELEMETRY_ROOT, reports_dir(), research_log_dir()):
        path.mkdir(parents=True, exist_ok=True)


def _tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {w for w in words if len(w) > 1 and w not in _STOPWORDS}


def headline_overlap_score(pipeline_text: str, headline: str) -> float:
    a = _tokenize(pipeline_text)
    b = _tokenize(headline)
    if not a or not b:
        return 0.0
    return len(a & b) / len(a)


def classify_headline_sentiment(headline: str) -> str:
    text = (headline or "").lower()
    pos = sum(1 for term in _POSITIVE_HINTS if term in text)
    neg = sum(1 for term in _NEGATIVE_HINTS if term in text)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def sentiment_matches_move(sentiment: str, change_pct: float) -> bool:
    if change_pct >= 0:
        return sentiment in ("positive", "neutral")
    return sentiment in ("negative", "neutral")


def score_headline(
    headline: str,
    published_ts: int,
    window_start: datetime,
    as_of: datetime,
    change_pct: float,
) -> float:
    """55% recency inside the 24h window + 45% sentiment match vs move direction."""
    window_seconds = max((as_of - window_start).total_seconds(), 1.0)
    age = max(0.0, as_of.timestamp() - float(published_ts))
    recency = max(0.0, 1.0 - min(age, window_seconds) / window_seconds)
    sentiment = classify_headline_sentiment(headline)
    sentiment_score = 1.0 if sentiment_matches_move(sentiment, change_pct) else 0.0
    return 0.55 * recency + 0.45 * sentiment_score


def pick_best_headline(
    headlines: List[Dict[str, Any]],
    window_start: datetime,
    as_of: datetime,
    change_pct: float,
) -> Tuple[Optional[Dict[str, Any]], float]:
    best: Optional[Dict[str, Any]] = None
    best_score = -1.0
    for item in headlines[:5]:
        headline = str(item.get("headline") or item.get("title") or "").strip()
        if not headline:
            continue
        published = int(item.get("datetime") or item.get("published_ts") or 0)
        score = score_headline(headline, published, window_start, as_of, change_pct)
        if score > best_score:
            best_score = score
            best = {**item, "headline": headline, "score": round(score, 3)}
    return best, best_score


def catalyst_recall_hit(pipeline_catalyst: str, headlines: List[Dict[str, Any]]) -> Tuple[bool, float, Optional[str]]:
    if not (pipeline_catalyst or "").strip():
        return False, 0.0, None
    best_headline = None
    best_overlap = 0.0
    for item in headlines[:5]:
        headline = str(item.get("headline") or item.get("title") or "")
        overlap = headline_overlap_score(pipeline_catalyst, headline)
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = headline
    return best_overlap >= HIT_OVERLAP_THRESHOLD, best_overlap, best_headline


def _normalize_mover_row(row: Dict[str, Any], side: str) -> Dict[str, Any]:
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    pct = row.get("change_percentage")
    if pct is None:
        pct = row.get("pct")
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0.0),
        "catalyst": (row.get("catalyst") or row.get("why") or "").strip(),
        "side": side,
        "price": row.get("price") or row.get("last"),
        "volume": row.get("volume"),
    }


def _internals_movers_to_rows(internals: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    movers = internals.get("movers") or {}
    gainers = [_normalize_mover_row(m, "gainer") for m in movers.get("gainers") or []]
    losers = [_normalize_mover_row(m, "loser") for m in movers.get("losers") or []]
    return gainers, losers


def merge_catalysts(
    base_rows: Iterable[Dict[str, Any]],
    catalyst_by_ticker: Dict[str, str],
) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for row in base_rows:
        ticker = row["ticker"]
        catalyst = catalyst_by_ticker.get(ticker) or row.get("catalyst") or ""
        merged.append({**row, "catalyst": catalyst})
    return merged


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def bootstrap_telemetry_if_missing(trade_date: str) -> None:
    day_dir = telemetry_day_dir(trade_date)
    post_path = day_dir / "postmarket_run.json"
    if post_path.exists():
        return

    try:
        from market_internals import fetch_movers_and_breadth_for_session
        from massive_client import enrich_movers_with_news
    except Exception as exc:
        logger.error("Cannot bootstrap telemetry: %s", exc)
        return

    mb = fetch_movers_and_breadth_for_session(trade_date, n=5)
    gainers_base, losers_base = _internals_movers_to_rows(mb)
    raw = []
    for row in gainers_base + losers_base:
        raw.append(
            {
                "ticker": row["ticker"],
                "symbol": row["ticker"],
                "change_percentage": row["change_percentage"],
                "last": row.get("price"),
            }
        )
    enriched = enrich_movers_with_news(raw, limit=10)
    catalyst_by = {r["ticker"]: r.get("catalyst") or "" for r in enriched}
    gainers = merge_catalysts(gainers_base, catalyst_by)
    losers = merge_catalysts(losers_base, catalyst_by)

    all_movers = gainers + losers
    featured = None
    if all_movers:
        featured = max(all_movers, key=lambda r: abs(r.get("change_percentage") or 0)).get("ticker")

    snapshot = {
        "trade_date": trade_date,
        "source": "bootstrap_telemetry_if_missing",
        "bootstrapped_at": datetime.now(tz=NY).isoformat(),
        "featured_stock": featured,
        "top_gainers": gainers,
        "top_losers": losers,
        "breadth": mb.get("breadth"),
    }
    write_json(post_path, snapshot)
    write_json(day_dir / "top_gainers.json", gainers)
    write_json(day_dir / "top_losers.json", losers)
    logger.info("Bootstrapped telemetry for %s → %s", trade_date, post_path)


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    *,
    email_sent: bool = True,
) -> None:
    """Persist mover/catalyst attribution after a successful email send."""
    if not email_sent:
        return

    ensure_telemetry_layout()
    trade_date = datetime.now(tz=NY).date().isoformat()
    day_dir = telemetry_day_dir(trade_date)

    catalyst_by: Dict[str, str] = {}
    for row in (context.get("top_gainers") or []) + (context.get("top_losers") or []):
        ticker = str(row.get("ticker") or "").upper()
        if ticker:
            catalyst_by[ticker] = str(row.get("catalyst") or "").strip()

    gainers = [_normalize_mover_row(m, "gainer") for m in (context.get("top_gainers") or [])]
    losers = [_normalize_mover_row(m, "loser") for m in (context.get("top_losers") or [])]

    internals = post_data.get("market_internals") or {}
    if internals.get("movers"):
        ig, il = _internals_movers_to_rows(internals)
        if ig:
            gainers = merge_catalysts(ig, catalyst_by)
        if il:
            losers = merge_catalysts(il, catalyst_by)

    featured_stock = post_data.get("featured_stock")
    featured_mover = context.get("featured_mover") or {}
    if not featured_stock and featured_mover.get("ticker"):
        featured_stock = featured_mover.get("ticker")

    payload = {
        "trade_date": trade_date,
        "time_period": time_period,
        "recorded_at": datetime.now(tz=NY).isoformat(),
        "email_sent": True,
        "featured_stock": featured_stock,
        "featured_mover": featured_mover,
        "top_gainers": gainers,
        "top_losers": losers,
        "post_style": post_data.get("style"),
        "market_internals_session": (internals.get("breadth") or {}).get("session_date"),
    }

    fname = f"{time_period}_run.json"
    write_json(day_dir / fname, payload)
    write_json(day_dir / "top_gainers.json", gainers)
    write_json(day_dir / "top_losers.json", losers)


def load_audit_movers(trade_date: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Dict[str, Any]]:
    bootstrap_telemetry_if_missing(trade_date)
    day_dir = telemetry_day_dir(trade_date)
    post_path = day_dir / "postmarket_run.json"
    meta: Dict[str, Any] = {}
    if post_path.exists():
        meta = load_json(post_path)
        return meta.get("top_gainers") or [], meta.get("top_losers") or [], meta
    gainers = load_json(day_dir / "top_gainers.json") if (day_dir / "top_gainers.json").exists() else []
    losers = load_json(day_dir / "top_losers.json") if (day_dir / "top_losers.json").exists() else []
    return gainers, losers, meta


def resolve_featured_ticker(meta: Dict[str, Any], gainers: List[Dict[str, Any]], losers: List[Dict[str, Any]]) -> Tuple[str, Dict[str, Any]]:
    ticker = str(meta.get("featured_stock") or "").upper()
    pool = gainers + losers
    if ticker:
        for row in pool:
            if row.get("ticker") == ticker:
                return ticker, row
        return ticker, {}
    if not pool:
        return "", {}
    row = max(pool, key=lambda r: abs(float(r.get("change_percentage") or 0)))
    return str(row.get("ticker") or "").upper(), row
