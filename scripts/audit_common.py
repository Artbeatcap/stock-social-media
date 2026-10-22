"""
Shared helpers for catalyst recall and featured-mover quality audits.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytz

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

HIT_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45

_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with",
    "by", "from", "as", "is", "was", "are", "were", "be", "been", "its", "it", "s", "t",
}


def now_ny() -> datetime:
    return datetime.now(tz=NY)


def is_weekday(dt: Optional[datetime] = None) -> bool:
    dt = dt or now_ny()
    return dt.weekday() < 5


def prior_weekday(d: date) -> date:
    cur = d
    while cur.weekday() >= 5:
        cur -= timedelta(days=1)
    return cur


def effective_audit_trade_date(as_of: Optional[datetime] = None) -> date:
    """Trade date for audits; weekends map to the previous Friday."""
    as_of = as_of or now_ny()
    d = as_of.date()
    if d.weekday() >= 5:
        while d.weekday() != 4:
            d -= timedelta(days=1)
    return d


def audit_as_of_for_trade_date(trade_date: date, as_of: Optional[datetime] = None) -> datetime:
    """
    Cap the audit window at `now` when running before the 9:15pm ET recall window
    on the trade date itself (avoids a future-dated 24h news window).
    """
    as_of = as_of or now_ny()
    end_of_trade = NY.localize(datetime.combine(trade_date, datetime.strptime("21:15", "%H:%M").time()))
    if as_of.date() == trade_date and as_of < end_of_trade:
        return as_of
    if as_of.date() > trade_date:
        return NY.localize(datetime.combine(trade_date, datetime.strptime("23:59", "%H:%M").time()))
    return end_of_trade


def telemetry_day_dir(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def ensure_telemetry_dirs() -> None:
    for path in (TELEMETRY_ROOT, REPORTS_DIR, RESEARCH_LOG_DIR):
        path.mkdir(parents=True, exist_ok=True)
        gitkeep = path / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.write_text("", encoding="utf-8")


def _normalize_mover(raw: Dict[str, Any], side: str) -> Dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0.0),
        "catalyst": str(raw.get("catalyst") or raw.get("why") or "").strip(),
        "side": side,
        "price": raw.get("price") or raw.get("last"),
        "volume": raw.get("volume"),
    }


def load_movers_from_telemetry(trade_date: date) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Load gainers/losers JSON for a trade date. Returns (movers, meta)."""
    day_dir = telemetry_day_dir(trade_date)
    meta: Dict[str, Any] = {"source": "telemetry", "trade_date": trade_date.isoformat(), "day_dir": str(day_dir)}
    gainers_path = day_dir / "top_gainers.json"
    losers_path = day_dir / "top_losers.json"
    movers: List[Dict[str, Any]] = []
    if gainers_path.exists():
        gainers = json.loads(gainers_path.read_text(encoding="utf-8"))
        movers.extend(_normalize_mover(m, "gainer") for m in gainers if m.get("ticker") or m.get("symbol"))
    if losers_path.exists():
        losers = json.loads(losers_path.read_text(encoding="utf-8"))
        movers.extend(_normalize_mover(m, "loser") for m in losers if m.get("ticker") or m.get("symbol"))
    meta["gainers_file"] = gainers_path.exists()
    meta["losers_file"] = losers_path.exists()
    return movers, meta


def bootstrap_telemetry_if_missing(trade_date: date) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build mover list from market_internals + Massive news when prod snapshots are absent."""
    movers, meta = load_movers_from_telemetry(trade_date)
    if movers:
        return movers, meta

    meta = {"source": "bootstrap", "trade_date": trade_date.isoformat()}
    try:
        from market_internals import get_market_internals
        from massive_client import enrich_movers_with_news

        internals = get_market_internals()
        raw_movers: List[Dict[str, Any]] = []
        for side, key in (("gainer", "gainers"), ("loser", "losers")):
            for item in (internals.get("movers") or {}).get(key) or []:
                raw_movers.append(
                    {
                        "ticker": item.get("symbol"),
                        "change_percentage": item.get("pct"),
                        "side": side,
                    }
                )
        enriched = enrich_movers_with_news(
            [
                {
                    "symbol": m["ticker"],
                    "ticker": m["ticker"],
                    "change_percentage": m["change_percentage"],
                }
                for m in raw_movers
            ],
            limit=len(raw_movers) or 10,
        )
        by_ticker = {e["ticker"]: e for e in enriched}
        for m in raw_movers:
            e = by_ticker.get(str(m["ticker"]).upper(), {})
            movers.append(
                {
                    "ticker": str(m["ticker"]).upper(),
                    "change_percentage": float(m.get("change_percentage") or 0.0),
                    "catalyst": str(e.get("catalyst") or ""),
                    "side": m.get("side", "gainer"),
                }
            )
        meta["bootstrap_count"] = len(movers)
    except Exception as exc:
        meta["bootstrap_error"] = str(exc)
    return movers, meta


def load_postmarket_run(trade_date: date) -> Dict[str, Any]:
    path = telemetry_day_dir(trade_date) / "postmarket_run.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]+", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS and len(t) > 1}


def token_overlap(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def infer_sentiment_from_text(text: str) -> str:
    lowered = (text or "").lower()
    negative = (
        "miss", "cut", "downgrade", "lawsuit", "probe", "fall", "drop", "sink", "plunge",
        "warn", "loss", "decline", "layoff", "bankruptcy", "fraud", "sec ", "investigation",
    )
    positive = (
        "beat", "raise", "upgrade", "surge", "soar", "jump", "record", "profit", "win",
        "approval", "deal", "partnership", "buyback", "dividend", "guidance up",
    )
    neg_hits = sum(1 for w in negative if w in lowered)
    pos_hits = sum(1 for w in positive if w in lowered)
    if pos_hits > neg_hits:
        return "positive"
    if neg_hits > pos_hits:
        return "negative"
    return "neutral"


def sentiment_matches(side: str, sentiment: str, headline: str) -> bool:
    sent = (sentiment or "").lower()
    if not sent or sent == "neutral":
        sent = infer_sentiment_from_text(headline)
    if sent == "neutral":
        return True
    if side == "gainer":
        return sent == "positive"
    return sent == "negative"


def recency_score(published_ts: int, window_start: datetime, window_end: datetime) -> float:
    if published_ts <= 0:
        return 0.0
    start = int(window_start.timestamp())
    end = int(window_end.timestamp())
    if end <= start:
        return 0.0
    if published_ts < start or published_ts > end:
        return 0.0
    return (published_ts - start) / (end - start)


def score_headline(
    headline_item: Dict[str, Any],
    pipeline_catalyst: str,
    side: str,
    window_start: datetime,
    window_end: datetime,
) -> float:
    headline = str(headline_item.get("headline") or headline_item.get("title") or "")
    ts = int(headline_item.get("datetime") or 0)
    rec = recency_score(ts, window_start, window_end)
    sent_ok = 1.0 if sentiment_matches(side, str(headline_item.get("sentiment") or ""), headline) else 0.0
    overlap = token_overlap(pipeline_catalyst, headline)
    return RECENCY_WEIGHT * rec + SENTIMENT_WEIGHT * sent_ok + 0.25 * overlap


def catalyst_hit(pipeline_catalyst: str, headlines: Iterable[Dict[str, Any]]) -> Tuple[bool, Optional[str]]:
    if not pipeline_catalyst:
        return False, None
    best_headline = None
    best_overlap = 0.0
    for item in headlines:
        headline = str(item.get("headline") or item.get("title") or "")
        overlap = token_overlap(pipeline_catalyst, headline)
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = headline
    return best_overlap >= HIT_OVERLAP_THRESHOLD, best_headline


def filter_news_to_window(items: List[Dict[str, Any]], window_start: datetime, window_end: datetime) -> List[Dict[str, Any]]:
    start_ts = int(window_start.timestamp())
    end_ts = int(window_end.timestamp())
    filtered = []
    for item in items:
        ts = int(item.get("datetime") or 0)
        if start_ts <= ts <= end_ts:
            filtered.append(item)
    return filtered
