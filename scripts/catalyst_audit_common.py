"""
Shared helpers for catalyst recall and featured-mover quality audits.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytz

from massive_client import enrich_movers_with_news, get_news_for_ticker

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
TELEMETRY_ROOT = Path(__file__).resolve().parent.parent / "validation_telemetry"

RECALL_AUDIT_ET = time(21, 15)
FEATURED_AUDIT_ET = time(17, 30)
HIT_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45

_STOPWORDS = frozenset(
    """
    a an the and or but if then else for to of in on at by from with as is are was were be been
    being have has had do does did will would could should may might must can this that these those
    it its they them their we our you your he she his her stock shares company inc corp ltd llc
    """.split()
)


def previous_trading_day(day: date) -> date:
    cursor = day - timedelta(days=1)
    while cursor.weekday() >= 5:
        cursor -= timedelta(days=1)
    return cursor


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """Pick the equity session date these audits should evaluate."""
    now = now or datetime.now(NY)
    if now.tzinfo is None:
        now = NY.localize(now)
    else:
        now = now.astimezone(NY)

    session_day = now.date()
    featured_cutoff = NY.localize(datetime.combine(session_day, FEATURED_AUDIT_ET))
    if now < featured_cutoff:
        session_day = previous_trading_day(session_day)

    while session_day.weekday() >= 5:
        session_day = previous_trading_day(session_day)
    return session_day


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """
    Upper bound for the 24h catalyst recall news window.
    Same-day runs before 9:15pm ET cap at now; otherwise end at 9:15pm ET on trade_date.
    """
    now = now or datetime.now(NY)
    if now.tzinfo is None:
        now = NY.localize(now)
    else:
        now = now.astimezone(NY)

    recall_end = NY.localize(datetime.combine(trade_date, RECALL_AUDIT_ET))
    if now.date() == trade_date and now < recall_end:
        return now
    return recall_end


def telemetry_day_dir(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def normalize_mover_row(row: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
    pct = row.get("change_percentage")
    if pct is None:
        pct = row.get("pct")
    try:
        change_pct = float(pct)
    except (TypeError, ValueError):
        change_pct = 0.0
    catalyst = str(row.get("catalyst") or row.get("why") or "").strip()
    return {
        "ticker": ticker,
        "change_percentage": change_pct,
        "catalyst": catalyst,
    }


def load_telemetry_movers(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    day_dir = telemetry_day_dir(trade_date)
    gainers_raw = _read_json(day_dir / "top_gainers.json") or []
    losers_raw = _read_json(day_dir / "top_losers.json") or []
    postmarket = _read_json(day_dir / "postmarket_run.json")
    gainers = [normalize_mover_row(r) for r in gainers_raw if isinstance(r, dict)]
    losers = [normalize_mover_row(r) for r in losers_raw if isinstance(r, dict)]
    return gainers, losers, postmarket if isinstance(postmarket, dict) else None


def bootstrap_telemetry_if_missing(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], Optional[Dict[str, Any]]]:
    gainers, losers, postmarket = load_telemetry_movers(trade_date)
    if gainers or losers:
        return gainers, losers, postmarket

    logger.info("No telemetry snapshots for %s; bootstrapping from market_internals + Massive news", trade_date)
    try:
        from market_internals import get_market_internals
    except ImportError as exc:
        logger.error("market_internals unavailable: %s", exc)
        return [], [], None

    internals = get_market_internals()
    movers = (internals or {}).get("movers") or {}
    raw_gainers = movers.get("gainers") or []
    raw_losers = movers.get("losers") or []

    def _to_quote(row: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "symbol": row.get("symbol") or row.get("ticker"),
            "change_percentage": row.get("pct") or row.get("change_percentage"),
            "last": row.get("last") or row.get("close"),
            "volume": row.get("volume"),
        }

    enriched = enrich_movers_with_news(
        [_to_quote(r) for r in raw_gainers + raw_losers if isinstance(r, dict)],
        limit=max(len(raw_gainers) + len(raw_losers), 10),
    )
    by_ticker = {m["ticker"]: m for m in enriched}
    gainers = [normalize_mover_row(by_ticker.get(str(r.get("symbol", "")).upper(), r)) for r in raw_gainers]
    losers = [normalize_mover_row(by_ticker.get(str(r.get("symbol", "")).upper(), r)) for r in raw_losers]
    for row in gainers + losers:
        if not row.get("catalyst"):
            match = by_ticker.get(row["ticker"])
            if match:
                row["catalyst"] = match.get("catalyst") or ""

    day_dir = telemetry_day_dir(trade_date)
    _write_json(day_dir / "top_gainers.json", gainers)
    _write_json(day_dir / "top_losers.json", losers)
    _write_json(
        day_dir / "bootstrap_meta.json",
        {"source": "market_internals+massive", "trade_date": trade_date.isoformat()},
    )
    return gainers, losers, postmarket


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    *,
    trade_date: Optional[date] = None,
) -> None:
    """Persist mover catalysts from a successful email send for downstream audits."""
    trade_date = trade_date or resolve_audit_trade_date()
    day_dir = telemetry_day_dir(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    gainers = [normalize_mover_row(m) for m in context.get("top_gainers") or []]
    losers = [normalize_mover_row(m) for m in context.get("top_losers") or []]
    _write_json(day_dir / "top_gainers.json", gainers)
    _write_json(day_dir / "top_losers.json", losers)

    featured = post_data.get("featured_stock")
    featured_mover = context.get("featured_mover") if isinstance(context.get("featured_mover"), dict) else None
    payload = {
        "time_period": time_period,
        "trade_date": trade_date.isoformat(),
        "sent_at_et": datetime.now(NY).strftime("%Y-%m-%d %H:%M:%S %Z"),
        "featured_stock": featured,
        "featured_mover": featured_mover,
        "post_style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
    }
    filename = f"{time_period}_run.json"
    _write_json(day_dir / filename, payload)


def tokenize(text: str) -> set[str]:
    tokens = re.findall(r"[a-z0-9]{2,}", (text or "").lower())
    return {t for t in tokens if t not in _STOPWORDS}


def token_overlap(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def expected_sentiment(change_pct: float) -> str:
    return "positive" if change_pct >= 0 else "negative"


def _news_timestamp(item: Dict[str, Any]) -> int:
    value = item.get("datetime")
    if isinstance(value, (int, float)):
        return int(value)
    published = item.get("published_utc") or item.get("published")
    if isinstance(published, str):
        try:
            return int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    return 0


def fetch_news_in_window(
    ticker: str,
    window_start: datetime,
    window_end: datetime,
    *,
    fetch_limit: int = 25,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    start_ts = int(window_start.timestamp())
    end_ts = int(window_end.timestamp())
    raw_items = get_news_for_ticker(ticker, limit=fetch_limit)
    filtered: List[Dict[str, Any]] = []
    for item in raw_items:
        ts = _news_timestamp(item)
        if start_ts <= ts <= end_ts:
            filtered.append(item)
    filtered.sort(key=_news_timestamp, reverse=True)
    return filtered[:top_n]


def score_headline(
    item: Dict[str, Any],
    *,
    as_of: datetime,
    expected: str,
) -> float:
    ts = _news_timestamp(item)
    if ts <= 0:
        recency_score = 0.0
    else:
        age_hours = max(0.0, (as_of.timestamp() - ts) / 3600.0)
        recency_score = max(0.0, 1.0 - min(age_hours / 24.0, 1.0))

    sentiment = str(item.get("sentiment") or "").lower()
    if not sentiment:
        sentiment_score = 0.5
    elif sentiment == expected:
        sentiment_score = 1.0
    elif sentiment in {"positive", "negative"}:
        sentiment_score = 0.0
    else:
        sentiment_score = 0.5

    return RECENCY_WEIGHT * recency_score + SENTIMENT_WEIGHT * sentiment_score


def pick_best_headline(
    headlines: Iterable[Dict[str, Any]],
    *,
    as_of: datetime,
    expected: str,
) -> Tuple[Optional[Dict[str, Any]], float]:
    best_item: Optional[Dict[str, Any]] = None
    best_score = -1.0
    for item in headlines:
        score = score_headline(item, as_of=as_of, expected=expected)
        if score > best_score:
            best_score = score
            best_item = item
    return best_item, best_score


def evaluate_catalyst_recall(
    pipeline_catalyst: str,
    headlines: List[Dict[str, Any]],
    *,
    change_pct: float,
    as_of: datetime,
) -> Tuple[str, str, float, float]:
    """
    Returns verdict (hit/miss), best Massive headline, overlap ratio, headline score.
    """
    expected = expected_sentiment(change_pct)
    if not pipeline_catalyst.strip():
        return "miss", "", 0.0, 0.0
    if not headlines:
        return "miss", "", 0.0, 0.0

    best_overlap = 0.0
    best_headline_text = ""
    for item in headlines:
        headline = str(item.get("headline") or "")
        overlap = token_overlap(pipeline_catalyst, headline)
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline_text = headline

    best_item, best_score = pick_best_headline(headlines, as_of=as_of, expected=expected)
    if best_item and not best_headline_text:
        best_headline_text = str(best_item.get("headline") or "")

    verdict = "hit" if best_overlap >= HIT_OVERLAP_THRESHOLD else "miss"
    return verdict, best_headline_text, best_overlap, best_score


def featured_from_postmarket(postmarket: Optional[Dict[str, Any]]) -> Optional[str]:
    if not postmarket:
        return None
    featured = postmarket.get("featured_stock")
    if featured:
        return str(featured).upper()
    mover = postmarket.get("featured_mover")
    if isinstance(mover, dict) and mover.get("ticker"):
        return str(mover["ticker"]).upper()
    return None


def pick_fallback_featured_mover(gainers: List[Dict[str, Any]], losers: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    combined = [m for m in gainers + losers if m.get("ticker")]
    if not combined:
        return None
    return max(combined, key=lambda m: abs(float(m.get("change_percentage") or 0.0)))
