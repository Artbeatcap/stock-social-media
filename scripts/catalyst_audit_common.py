"""
Shared helpers for catalyst recall and featured-mover quality audits.

Production telemetry lives under validation_telemetry/YYYY-MM-DD/ and is written
by twitter_auto_emailer after a successful send.
"""

from __future__ import annotations

import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

import pytz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

NY = pytz.timezone("America/New_York")
TELEMETRY_ROOT = ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

HIT_TOKEN_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45

logger = logging.getLogger(__name__)


def telemetry_dir_for(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def _is_weekday(d: date) -> bool:
    return d.weekday() < 5


def previous_trading_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while not _is_weekday(cur):
        cur -= timedelta(days=1)
    return cur


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """
    Default trade date for audits. Before 5:30pm ET, use the prior session
    (skipping weekends). After 5:30pm on a weekday, use today.
    """
    now = now or datetime.now(tz=NY)
    local_date = now.date()
    cutoff = now.replace(hour=17, minute=30, second=0, microsecond=0)
    if _is_weekday(local_date) and now >= cutoff:
        return local_date
    return previous_trading_day(local_date)


def audit_as_of_for_trade_date(
    trade_date: date,
    now: Optional[datetime] = None,
    recall_cutoff_hour: int = 21,
    recall_cutoff_minute: int = 15,
) -> datetime:
    """
    End timestamp for the recall audit's 24h news window. When run before
    9:15pm ET on the trade date, cap at ``now`` so we do not look into the future.
    """
    now = now or datetime.now(tz=NY)
    session_end = NY.localize(
        datetime.combine(trade_date, datetime.min.time()).replace(
            hour=recall_cutoff_hour, minute=recall_cutoff_minute
        )
    )
    if now.date() == trade_date and now < session_end:
        return now
    return session_end


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
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _normalize_mover_row(raw: Dict[str, Any], side: str) -> Dict[str, Any]:
    ticker = str(
        raw.get("ticker") or raw.get("symbol") or ""
    ).upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    return {
        "ticker": ticker,
        "change_percentage": float(pct or 0.0),
        "catalyst": str(raw.get("catalyst") or "").strip(),
        "price": raw.get("price") or raw.get("last"),
        "side": side,
    }


def _catalyst_map(rows: Iterable[Dict[str, Any]]) -> Dict[str, str]:
    out: Dict[str, str] = {}
    for row in rows:
        ticker = str(row.get("ticker") or row.get("symbol") or "").upper()
        cat = str(row.get("catalyst") or "").strip()
        if ticker and cat:
            out[ticker] = cat
    return out


def movers_from_telemetry(trade_date: date) -> Tuple[List[Dict[str, Any]], str]:
    """Load gainers/losers for a trade date. Returns (movers, source_label)."""
    day_dir = telemetry_dir_for(trade_date)
    gainers = _read_json(day_dir / "top_gainers.json") or []
    losers = _read_json(day_dir / "top_losers.json") or []
    if gainers or losers:
        movers = [_normalize_mover_row(m, "gainer") for m in gainers]
        movers += [_normalize_mover_row(m, "loser") for m in losers]
        return movers, "telemetry"

    return [], "missing"


def bootstrap_telemetry_if_missing(trade_date: date) -> Tuple[List[Dict[str, Any]], str]:
    movers, source = movers_from_telemetry(trade_date)
    if movers:
        return movers, source

    try:
        from market_internals import get_market_internals
        from massive_client import enrich_movers_with_news
    except ImportError as exc:
        logger.error("Bootstrap imports failed: %s", exc)
        return [], "bootstrap_failed"

    internals = get_market_internals()
    raw_movers: List[Dict[str, Any]] = []
    for side, label in (("gainers", "gainer"), ("losers", "loser")):
        for m in (internals.get("movers") or {}).get(side) or []:
            raw_movers.append(
                {
                    "ticker": m.get("symbol"),
                    "change_percentage": m.get("pct"),
                    "price": m.get("last"),
                    "side": label,
                }
            )

    if not raw_movers:
        enriched = enrich_movers_with_news([], limit=0)
        return [], "bootstrap_empty"

    enriched = enrich_movers_with_news(
        [
            {
                "symbol": m["ticker"],
                "ticker": m["ticker"],
                "change_percentage": m["change_percentage"],
                "last": m.get("price"),
            }
            for m in raw_movers
        ],
        limit=len(raw_movers),
    )
    cat_map = _catalyst_map(enriched)
    movers = []
    for m in raw_movers:
        ticker = m["ticker"]
        movers.append(
            {
                "ticker": ticker,
                "change_percentage": m["change_percentage"],
                "catalyst": cat_map.get(ticker, ""),
                "price": m.get("price"),
                "side": m["side"],
            }
        )

    day_dir = telemetry_dir_for(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    _write_json(
        day_dir / "top_gainers.json",
        [m for m in movers if m["side"] == "gainer"],
    )
    _write_json(
        day_dir / "top_losers.json",
        [m for m in movers if m["side"] == "loser"],
    )
    return movers, "bootstrapped"


def featured_ticker_from_telemetry(trade_date: date) -> Tuple[Optional[str], str]:
    day_dir = telemetry_dir_for(trade_date)
    post = _read_json(day_dir / "postmarket_run.json") or {}
    ticker = post.get("featured_stock")
    if ticker:
        return str(ticker).upper(), "postmarket_run"
    return None, "missing"


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    sent_at: Optional[datetime] = None,
) -> Path:
    """
    Persist telemetry for quality audits after a successful email send.
    """
    sent_at = sent_at or datetime.now(tz=NY)
    trade_date = sent_at.date()
    day_dir = telemetry_dir_for(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    catalyst_by_ticker = _catalyst_map(
        (context.get("top_gainers") or [])
        + (context.get("top_losers") or [])
        + ([context["featured_mover"]] if context.get("featured_mover") else [])
    )

    gainers: List[Dict[str, Any]] = []
    losers: List[Dict[str, Any]] = []

    internals = post_data.get("market_internals") or {}
    if time_period == "postmarket" and internals.get("movers"):
        for m in internals["movers"].get("gainers") or []:
            ticker = str(m.get("symbol", "")).upper()
            gainers.append(
                {
                    "ticker": ticker,
                    "change_percentage": m.get("pct"),
                    "price": m.get("last"),
                    "catalyst": catalyst_by_ticker.get(ticker, ""),
                }
            )
        for m in internals["movers"].get("losers") or []:
            ticker = str(m.get("symbol", "")).upper()
            losers.append(
                {
                    "ticker": ticker,
                    "change_percentage": m.get("pct"),
                    "price": m.get("last"),
                    "catalyst": catalyst_by_ticker.get(ticker, ""),
                }
            )
    else:
        gainers = list(context.get("top_gainers") or [])
        losers = list(context.get("top_losers") or [])

    _write_json(day_dir / "top_gainers.json", gainers)
    _write_json(day_dir / "top_losers.json", losers)

    run_doc = {
        "time_period": time_period,
        "sent_at": sent_at.isoformat(),
        "featured_stock": post_data.get("featured_stock"),
        "post_style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
        "market_direction": context.get("market_direction"),
        "top_gainers_count": len(gainers),
        "top_losers_count": len(losers),
    }
    run_name = f"{time_period}_run.json"
    _write_json(day_dir / run_name, run_doc)
    return day_dir


# ── Text / scoring helpers ──────────────────────────────────────────────────

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def token_overlap(a: str, b: str) -> float:
    ta, tb = set(tokenize(a)), set(tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def wanted_sentiment(side: str) -> str:
    return "positive" if side == "gainer" else "negative"


def sentiment_match_score(item_sentiment: str, wanted: str) -> float:
    s = (item_sentiment or "").lower()
    if not s:
        return 0.5
    if s == wanted:
        return 1.0
    if s in {"neutral", "unknown"}:
        return 0.5
    return 0.0


def score_headline(
    item: Dict[str, Any],
    window_start: datetime,
    window_end: datetime,
    wanted_sentiment: str,
) -> float:
    ts = int(item.get("datetime") or 0)
    published = datetime.fromtimestamp(ts, tz=NY)
    if published < window_start or published > window_end:
        return -1.0

    span = max((window_end - window_start).total_seconds(), 1.0)
    age = (window_end - published).total_seconds()
    recency = max(0.0, 1.0 - age / span)
    sent = sentiment_match_score(str(item.get("sentiment") or ""), wanted_sentiment)
    return RECENCY_WEIGHT * recency + SENTIMENT_WEIGHT * sent


def pick_top_headlines(
    news_items: List[Dict[str, Any]],
    window_start: datetime,
    window_end: datetime,
    side: str,
    limit: int = 5,
) -> List[Tuple[float, Dict[str, Any]]]:
    wanted = wanted_sentiment(side)
    scored: List[Tuple[float, Dict[str, Any]]] = []
    for item in news_items:
        score = score_headline(item, window_start, window_end, wanted)
        if score >= 0:
            scored.append((score, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return scored[:limit]


def classify_recall_hit(
    pipeline_catalyst: str,
    top_headlines: List[Tuple[float, Dict[str, Any]]],
) -> Tuple[bool, str, float]:
    if not pipeline_catalyst.strip():
        return False, "(no pipeline catalyst recorded)", 0.0

    best_headline = ""
    best_overlap = 0.0
    for _score, item in top_headlines:
        headline = str(item.get("headline") or "")
        overlap = token_overlap(pipeline_catalyst, headline)
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = headline

    if not best_headline and top_headlines:
        best_headline = str(top_headlines[0][1].get("headline") or "")

    hit = best_overlap >= HIT_TOKEN_OVERLAP_THRESHOLD
    return hit, best_headline, best_overlap


def should_skip_weekend(now: Optional[datetime] = None) -> bool:
    now = now or datetime.now(tz=NY)
    if _is_weekday(now.date()):
        return False
    if os.getenv("CATALYST_QUALITY_AUDIT_TASK"):
        return False
    return True
