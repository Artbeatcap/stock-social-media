"""
Shared helpers for catalyst recall and featured-mover quality audits.

Production telemetry lands under validation_telemetry/YYYY-MM-DD/ when
twitter_auto_emailer sends successfully (write_run_snapshot).
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytz

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parent.parent
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
NY = pytz.timezone("America/New_York")

RECALL_HIT_OVERLAP = 0.35
RECALL_RECENCY_WEIGHT = 0.55
RECALL_SENTIMENT_WEIGHT = 0.45

POSITIVE_SENTIMENT = frozenset({"positive", "bullish", "somewhat-bullish"})
NEGATIVE_SENTIMENT = frozenset({"negative", "bearish", "somewhat-bearish"})


def _ensure_repo_on_path() -> None:
    root = str(REPO_ROOT)
    import sys

    if root not in sys.path:
        sys.path.insert(0, root)


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """Use prior US session before 5:30pm ET; roll back over weekends."""
    now = now or datetime.now(NY)
    session = now.date()
    cutoff = now.replace(hour=17, minute=30, second=0, microsecond=0)
    if now < cutoff:
        session = session - timedelta(days=1)
    while session.weekday() >= 5:
        session -= timedelta(days=1)
    return session


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """
    Recall news window ends at 9:15pm ET on trade_date, unless we are still
    before that moment on the same calendar day (then cap at now).
    """
    now = now or datetime.now(NY)
    end = NY.localize(datetime.combine(trade_date, time(21, 15)))
    if trade_date == now.date() and now < end:
        return now
    return end


def recall_news_window(trade_date: date, now: Optional[datetime] = None) -> Tuple[datetime, datetime]:
    end = audit_as_of_for_trade_date(trade_date, now)
    start = end - timedelta(hours=24)
    return start, end


def telemetry_day_dir(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("Failed to read %s: %s", path, exc)
        return None


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _token_set(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9]+", (text or "").lower()) if len(w) > 2}


def token_overlap(a: str, b: str) -> float:
    left, right = _token_set(a), _token_set(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def _normalize_mover(raw: Dict[str, Any]) -> Dict[str, Any]:
    ticker = str(raw.get("ticker") or raw.get("symbol") or "").upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0),
        "catalyst": str(raw.get("catalyst") or raw.get("why") or "").strip(),
        "price": raw.get("price") or raw.get("last"),
        "volume": raw.get("volume"),
    }


def _merge_catalysts(base: List[Dict[str, Any]], context: Dict[str, Any]) -> List[Dict[str, Any]]:
    by_ticker: Dict[str, str] = {}
    for bucket in (
        context.get("top_gainers") or [],
        context.get("top_losers") or [],
    ):
        for row in bucket:
            t = str(row.get("ticker") or "").upper()
            cat = row.get("catalyst")
            if t and cat:
                by_ticker[t] = str(cat)
    featured = context.get("featured_mover") or {}
    ft = str(featured.get("ticker") or "").upper()
    if ft and featured.get("catalyst"):
        by_ticker[ft] = str(featured.get("catalyst"))

    out: List[Dict[str, Any]] = []
    for row in base:
        mover = dict(row)
        if not mover.get("catalyst"):
            mover["catalyst"] = by_ticker.get(mover.get("ticker", ""), "")
        out.append(mover)
    return out


def _movers_from_internals(post_data: Dict[str, Any]) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    internals = post_data.get("market_internals") or {}
    movers = internals.get("movers") or {}
    gainers = [
        _normalize_mover({"ticker": m.get("symbol"), "change_percentage": m.get("pct"), "price": m.get("last")})
        for m in movers.get("gainers") or []
        if m.get("symbol")
    ]
    losers = [
        _normalize_mover({"ticker": m.get("symbol"), "change_percentage": m.get("pct"), "price": m.get("last")})
        for m in movers.get("losers") or []
        if m.get("symbol")
    ]
    return gainers, losers


def _enrich_missing_catalysts(movers: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    _ensure_repo_on_path()
    from massive_client import enrich_movers_with_news

    rows = [dict(m) for m in movers]
    need = [m for m in rows if not m.get("catalyst")]
    if not need:
        return rows
    enriched = enrich_movers_with_news(
        [
            {
                "symbol": m["ticker"],
                "ticker": m["ticker"],
                "change_percentage": m.get("change_percentage", 0),
                "last": m.get("price"),
            }
            for m in need
        ],
        limit=len(need),
    )
    by_ticker = {e["ticker"]: e.get("catalyst", "") for e in enriched}
    for m in rows:
        if not m.get("catalyst"):
            m["catalyst"] = by_ticker.get(m["ticker"], "")
    return rows


def movers_for_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    if time_period == "postmarket" and post_data.get("market_internals"):
        gainers, losers = _movers_from_internals(post_data)
    else:
        gainers = [_normalize_mover(m) for m in context.get("top_gainers") or []]
        losers = [_normalize_mover(m) for m in context.get("top_losers") or []]

    gainers = _merge_catalysts(gainers, context)
    losers = _merge_catalysts(losers, context)
    gainers = _enrich_missing_catalysts(gainers)
    losers = _enrich_missing_catalysts(losers)
    return gainers, losers


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
) -> Path:
    """Persist mover + post metadata for downstream quality audits."""
    trade_date = datetime.now(NY).date()
    day_dir = telemetry_day_dir(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    gainers, losers = movers_for_snapshot(time_period, context, post_data)
    _write_json(day_dir / "top_gainers.json", gainers)
    _write_json(day_dir / "top_losers.json", losers)

    snapshot = {
        "time_period": time_period,
        "written_at": datetime.now(NY).isoformat(),
        "featured_stock": post_data.get("featured_stock"),
        "style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
        "post_preview": (post_data.get("post") or post_data.get("short_version") or "")[:500],
        "market_summary": (post_data.get("market_summary") or "")[:2000],
        "has_market_internals": bool(post_data.get("market_internals")),
    }
    run_path = day_dir / f"{time_period}_run.json"
    _write_json(run_path, snapshot)
    logger.info("Wrote validation telemetry to %s", day_dir)
    return day_dir


def load_telemetry_movers(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], bool]:
    day_dir = telemetry_day_dir(trade_date)
    gainers = _read_json(day_dir / "top_gainers.json") or []
    losers = _read_json(day_dir / "top_losers.json") or []
    found = bool(gainers or losers)
    return [_normalize_mover(g) for g in gainers], [_normalize_mover(l) for l in losers], found


def load_postmarket_run(trade_date: date) -> Optional[Dict[str, Any]]:
    return _read_json(telemetry_day_dir(trade_date) / "postmarket_run.json")


def bootstrap_telemetry_if_missing(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]], str]:
    gainers, losers, found = load_telemetry_movers(trade_date)
    if found:
        return gainers, losers, "production"

    _ensure_repo_on_path()
    note_parts = ["bootstrapped"]
    try:
        from market_internals import get_market_internals

        internals = get_market_internals()
        g, l = _movers_from_internals({"market_internals": internals})
        if g or l:
            gainers, losers = g, l
            note_parts.append("market_internals")
    except Exception as exc:
        logger.warning("market_internals bootstrap failed: %s", exc)

    if not gainers and not losers:
        from massive_client import fetch_top_movers_with_news

        movers = fetch_top_movers_with_news(limit=10)
        gainers = [_normalize_mover(m) for m in movers if m.get("change_percentage", 0) > 0][:5]
        losers = [_normalize_mover(m) for m in movers if m.get("change_percentage", 0) < 0][:5]
        note_parts.append("massive_snapshot_movers")

    gainers = _enrich_missing_catalysts(gainers)
    losers = _enrich_missing_catalysts(losers)
    return gainers, losers, "+".join(note_parts)


def fetch_ticker_news(
    ticker: str,
    *,
    limit: int = 10,
    published_gte: Optional[datetime] = None,
    published_lte: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    _ensure_repo_on_path()
    from massive_client import get_news_for_ticker_window

    return get_news_for_ticker_window(
        ticker,
        limit=limit,
        published_gte=published_gte,
        published_lte=published_lte,
    )


def _parse_published(item: Dict[str, Any]) -> Optional[datetime]:
    raw = item.get("published_utc") or item.get("datetime")
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw, tz=NY)
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(NY)
        except ValueError:
            return None
    return None


def score_headline(
    item: Dict[str, Any],
    *,
    mover_pct: float,
    window_start: datetime,
    window_end: datetime,
) -> float:
    headline = item.get("headline") or item.get("title") or ""
    published = _parse_published(item)
    if published is None:
        recency = 0.0
    else:
        span = max((window_end - window_start).total_seconds(), 1.0)
        age = max((window_end - published).total_seconds(), 0.0)
        recency = max(0.0, min(1.0, 1.0 - age / span))

    sentiment = str(item.get("sentiment") or "").lower()
    wants_positive = mover_pct >= 0
    if wants_positive:
        sentiment_match = 1.0 if sentiment in POSITIVE_SENTIMENT else (0.5 if not sentiment else 0.2)
    else:
        sentiment_match = 1.0 if sentiment in NEGATIVE_SENTIMENT else (0.5 if not sentiment else 0.2)

    return RECALL_RECENCY_WEIGHT * recency + RECALL_SENTIMENT_WEIGHT * sentiment_match


def rank_top_headlines(
    items: List[Dict[str, Any]],
    *,
    mover_pct: float,
    window_start: datetime,
    window_end: datetime,
    top_n: int = 5,
) -> List[Dict[str, Any]]:
    scored = []
    for item in items:
        scored.append(
            {
                **item,
                "_score": score_headline(
                    item,
                    mover_pct=mover_pct,
                    window_start=window_start,
                    window_end=window_end,
                ),
            }
        )
    scored.sort(key=lambda row: row["_score"], reverse=True)
    return scored[:top_n]


def catalyst_recall_verdict(
    pipeline_catalyst: str,
    massive_headlines: List[Dict[str, Any]],
) -> Tuple[str, str, float]:
    """
    Returns (verdict hit|miss|empty, best_massive_headline, best_overlap).
    """
    pipeline = (pipeline_catalyst or "").strip()
    if not pipeline:
        return "miss", "", 0.0

    best_headline = ""
    best_overlap = 0.0
    for item in massive_headlines:
        headline = str(item.get("headline") or item.get("title") or "")
        if not headline:
            continue
        overlap = token_overlap(pipeline, headline)
        if pipeline.lower() in headline.lower():
            overlap = max(overlap, 0.9)
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = headline

    if best_overlap >= RECALL_HIT_OVERLAP:
        return "hit", best_headline, best_overlap
    return "miss", best_headline, best_overlap


def is_us_weekday(when: Optional[datetime] = None) -> bool:
    when = when or datetime.now(NY)
    return when.weekday() < 5


def featured_ticker_from_postmarket(trade_date: date, gainers: List[Dict], losers: List[Dict]) -> Tuple[str, str]:
    run = load_postmarket_run(trade_date) or {}
    featured = str(run.get("featured_stock") or "").strip().upper()
    if featured:
        return featured, "postmarket_run.featured_stock"

    candidates = gainers + losers
    if not candidates:
        return "", "none"
    best = max(candidates, key=lambda m: abs(float(m.get("change_percentage") or 0)))
    return str(best.get("ticker") or "").upper(), "fallback_largest_move"
