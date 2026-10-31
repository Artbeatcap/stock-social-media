"""Shared utilities for daily catalyst quality audits (recall + featured deep dive)."""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time as dt_time, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

from massive_client import (
    NY,
    _as_float,
    _as_int,
    _request,
    enrich_movers_with_news,
    get_news_for_ticker,
)

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"
REPORTS_DIR = TELEMETRY_ROOT / "reports"
RESEARCH_LOG_DIR = TELEMETRY_ROOT / "research_log"

HIT_OVERLAP_THRESHOLD = 0.35
RECALL_HEADLINE_RECENCY_WEIGHT = 0.55
RECALL_HEADLINE_SENTIMENT_WEIGHT = 0.45

FEATURED_DEEP_DIVE_CUTOFF = dt_time(17, 30)
RECALL_AUDIT_CUTOFF = dt_time(21, 15)

ET = NY


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """Trading session date to audit (prior session before 5:30pm ET; skip weekends)."""
    now = now or datetime.now(tz=ET)
    trade = now.date()
    if now.time() < FEATURED_DEEP_DIVE_CUTOFF:
        trade -= timedelta(days=1)
    while trade.weekday() >= 5:
        trade -= timedelta(days=1)
    return trade


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """End of the recall audit 24h news window (9:15pm ET on trade date).

    When re-running on the trade date before 9:15pm ET, cap at ``now`` so the window
    does not extend into the future.
    """
    now = now or datetime.now(tz=ET)
    end = ET.localize(datetime.combine(trade_date, RECALL_AUDIT_CUTOFF))
    if now.date() == trade_date and now < end:
        return now
    return end


def telemetry_dir_for(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def ensure_telemetry_dirs() -> None:
    for directory in (TELEMETRY_ROOT, REPORTS_DIR, RESEARCH_LOG_DIR):
        directory.mkdir(parents=True, exist_ok=True)
        gitkeep = directory / ".gitkeep"
        if not gitkeep.exists():
            gitkeep.touch()


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
) -> None:
    """Persist mover lists and post metadata after a successful email send."""
    ensure_telemetry_dirs()
    day_dir = telemetry_dir_for(datetime.now(tz=ET).date())
    day_dir.mkdir(parents=True, exist_ok=True)

    snapshot = {
        "saved_at": datetime.now(tz=ET).isoformat(),
        "time_period": time_period,
        "featured_stock": post_data.get("featured_stock"),
        "featured_mover": context.get("featured_mover"),
        "post": post_data.get("post"),
        "style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
    }
    run_name = "premarket_run.json" if time_period == "premarket" else "postmarket_run.json"
    (day_dir / run_name).write_text(json.dumps(snapshot, indent=2), encoding="utf-8")

    gainers = context.get("top_gainers") or []
    losers = context.get("top_losers") or []
    (day_dir / "top_gainers.json").write_text(json.dumps(gainers, indent=2), encoding="utf-8")
    (day_dir / "top_losers.json").write_text(json.dumps(losers, indent=2), encoding="utf-8")
    logger.info("Wrote validation telemetry to %s", day_dir)


def _load_json(path: Path) -> Any:
    if not path.is_file():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_movers_for_trade_date(trade_date: date) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    day_dir = telemetry_dir_for(trade_date)
    gainers = _load_json(day_dir / "top_gainers.json") or []
    losers = _load_json(day_dir / "top_losers.json") or []
    return gainers, losers


def _normalize_internals_row(row: Dict[str, Any]) -> Dict[str, Any]:
    symbol = row.get("symbol") or row.get("ticker") or ""
    pct = row.get("pct") if "pct" in row else row.get("change_percentage", 0)
    return {
        "ticker": str(symbol).upper(),
        "change_percentage": _as_float(pct),
        "price": _as_float(row.get("last") or row.get("price") or row.get("close")),
    }


def bootstrap_telemetry_if_missing(trade_date: date) -> bool:
    """Build mover JSON from market_internals + Massive news when prod snapshots are absent."""
    gainers, losers = load_movers_for_trade_date(trade_date)
    if gainers or losers:
        return False

    logger.info("Bootstrapping telemetry for %s (no snapshot on disk)", trade_date)
    raw_g: List[Dict[str, Any]] = []
    raw_l: List[Dict[str, Any]] = []
    try:
        from market_internals import fetch_movers_and_breadth

        mb = fetch_movers_and_breadth(n=5)
        movers = mb.get("movers") or {}
        raw_g = [_normalize_internals_row(r) for r in movers.get("gainers") or []]
        raw_l = [_normalize_internals_row(r) for r in movers.get("losers") or []]
    except Exception as exc:
        logger.warning("market_internals bootstrap failed: %s", exc)

    if not raw_g and not raw_l:
        from massive_client import fetch_top_movers_with_news

        movers = fetch_top_movers_with_news(limit=10)
        raw_g = [m for m in movers if _as_float(m.get("change_percentage")) > 0][:5]
        raw_l = sorted(
            [m for m in movers if _as_float(m.get("change_percentage")) < 0],
            key=lambda item: abs(_as_float(item.get("change_percentage"))),
            reverse=True,
        )[:5]
    else:
        raw_g = enrich_movers_with_news(raw_g, limit=5)
        raw_l = enrich_movers_with_news(raw_l, limit=5)

    day_dir = telemetry_dir_for(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / "top_gainers.json").write_text(json.dumps(raw_g, indent=2), encoding="utf-8")
    (day_dir / "top_losers.json").write_text(json.dumps(raw_l, indent=2), encoding="utf-8")
    return True


def load_postmarket_run(trade_date: date) -> Optional[Dict[str, Any]]:
    return _load_json(telemetry_dir_for(trade_date) / "postmarket_run.json")


def resolve_featured_mover(trade_date: date) -> Tuple[Optional[Dict[str, Any]], str]:
    """Return (mover dict, source note) for featured deep dive."""
    post = load_postmarket_run(trade_date) or {}
    featured_mover = post.get("featured_mover")
    if isinstance(featured_mover, dict) and featured_mover.get("ticker"):
        return featured_mover, "postmarket_run.json featured_mover"

    ticker = post.get("featured_stock")
    if ticker:
        gainers, losers = load_movers_for_trade_date(trade_date)
        for row in gainers + losers:
            if str(row.get("ticker", "")).upper() == str(ticker).upper():
                return row, "postmarket_run.json featured_stock matched movers list"
        return {
            "ticker": str(ticker).upper(),
            "change_percentage": None,
            "catalyst": "",
        }, "postmarket_run.json featured_stock only"

    gainers, losers = load_movers_for_trade_date(trade_date)
    combined = (gainers or []) + (losers or [])
    if not combined:
        return None, "no movers"
    best = max(combined, key=lambda m: abs(_as_float(m.get("change_percentage"))))
    return best, "fallback largest |%| mover"


def tokenize(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9]+", (text or "").lower())
    stop = {
        "the", "a", "an", "and", "or", "for", "to", "in", "on", "of", "at", "by",
        "with", "its", "from", "after", "before", "over", "under", "vs", "via",
    }
    return {word for word in words if len(word) > 2 and word not in stop}


def token_overlap(a: str, b: str) -> float:
    left, right = tokenize(a), tokenize(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left)


def parse_news_timestamp(item: Dict[str, Any]) -> Optional[datetime]:
    for key in ("published_utc", "published"):
        raw = item.get(key)
        if isinstance(raw, str):
            try:
                return datetime.fromisoformat(raw.replace("Z", "+00:00")).astimezone(ET)
            except ValueError:
                pass
    ts = item.get("datetime")
    if isinstance(ts, (int, float)):
        return datetime.fromtimestamp(ts, tz=ET)
    return None


def fetch_ticker_news_window(
    ticker: str,
    start: datetime,
    end: datetime,
    limit: int = 20,
) -> List[Dict[str, Any]]:
    items = get_news_for_ticker(ticker, limit=max(limit, 50))
    in_window: List[Dict[str, Any]] = []
    for item in items:
        ts = parse_news_timestamp(item)
        if ts is None:
            continue
        if start <= ts <= end:
            in_window.append(item)
    return in_window


def sentiment_match_score(headline_sentiment: str, is_gainer: bool) -> float:
    wanted = "positive" if is_gainer else "negative"
    sentiment = (headline_sentiment or "").lower()
    if sentiment == wanted:
        return 1.0
    if sentiment in ("neutral", ""):
        return 0.5
    return 0.0


def score_headline_for_mover(
    headline: Dict[str, Any],
    is_gainer: bool,
    window_end: datetime,
) -> float:
    ts = parse_news_timestamp(headline)
    if ts is None:
        return 0.0
    age_hours = max((window_end - ts).total_seconds() / 3600.0, 0.25)
    recency_norm = min((1.0 / age_hours) * 6.0, 1.0)
    sent = sentiment_match_score(str(headline.get("sentiment") or ""), is_gainer)
    return (
        RECALL_HEADLINE_RECENCY_WEIGHT * recency_norm
        + RECALL_HEADLINE_SENTIMENT_WEIGHT * sent
    )


def rank_top_headlines(
    news: List[Dict[str, Any]],
    is_gainer: bool,
    window_end: datetime,
    n: int = 5,
) -> List[Tuple[Dict[str, Any], float]]:
    scored = [(headline, score_headline_for_mover(headline, is_gainer, window_end)) for headline in news]
    scored.sort(key=lambda pair: pair[1], reverse=True)
    return scored[:n]


def evaluate_catalyst_recall(
    pipeline_catalyst: str,
    top_headlines: List[Tuple[Dict[str, Any], float]],
) -> Tuple[str, bool, float, str]:
    if not (pipeline_catalyst or "").strip():
        return "", False, 0.0, "miss (empty pipeline catalyst)"

    best_headline = ""
    best_overlap = 0.0
    for headline, _score in top_headlines:
        title = str(headline.get("headline") or headline.get("title") or "")
        overlap = token_overlap(pipeline_catalyst, title)
        if overlap > best_overlap:
            best_overlap = overlap
            best_headline = title

    hit = best_overlap >= HIT_OVERLAP_THRESHOLD
    return best_headline, hit, best_overlap, "hit" if hit else "miss"


def get_minute_bars_for_date(ticker: str, trade_date: date) -> List[Dict[str, Any]]:
    data = _request(
        f"/v2/aggs/ticker/{ticker.upper()}/range/1/minute/"
        f"{trade_date.isoformat()}/{trade_date.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    bars: List[Dict[str, Any]] = []
    for item in data.get("results", []) or []:
        ts = datetime.fromtimestamp(_as_int(item.get("t")) / 1000, tz=ET)
        bars.append(
            {
                "time": ts,
                "open": _as_float(item.get("o")),
                "close": _as_float(item.get("c")),
                "volume": _as_int(item.get("v")),
            }
        )
    return bars


def analyze_price_move(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not bars:
        return {"bar_count": 0}

    session_open = bars[0]["open"]
    prev_close = bars[0]["open"]
    largest_jump: Dict[str, Any] = {"pct": 0.0, "time": None}
    max_from_open: Dict[str, Any] = {"pct": 0.0, "time": None}

    for bar in bars:
        if prev_close:
            jump_pct = (bar["close"] - prev_close) / prev_close * 100.0
            if abs(jump_pct) > abs(_as_float(largest_jump["pct"])):
                largest_jump = {"pct": round(jump_pct, 2), "time": bar["time"]}
        prev_close = bar["close"]

        if session_open:
            from_open_pct = (bar["close"] - session_open) / session_open * 100.0
            if abs(from_open_pct) > abs(_as_float(max_from_open["pct"])):
                max_from_open = {"pct": round(from_open_pct, 2), "time": bar["time"]}

    return {
        "bar_count": len(bars),
        "largest_1m_jump": largest_jump,
        "max_from_open": max_from_open,
    }


def fetch_options_chain(underlying: str, limit: int = 50) -> Tuple[Optional[List[Dict[str, Any]]], Optional[str]]:
    data = _request(f"/v3/snapshot/options/{underlying.upper()}", {"limit": limit})
    if not data:
        return None, "Massive options snapshot empty or HTTP error (tier may not include options)"
    if data.get("status") in ("NOT_AUTHORIZED", "ERROR"):
        return None, f"Options snapshot not authorized: {data.get('status')}"
    results = data.get("results") or data.get("options") or []
    if not results:
        return [], None
    return results, None


def summarize_options_unusual(
    chain: List[Dict[str, Any]],
    underlying_price: float,
    top_n: int = 5,
) -> List[str]:
    """Flag contracts with elevated OI or IV near the money."""
    if not chain:
        return ["No options contracts returned."]

    scored: List[Tuple[float, str]] = []
    for contract in chain:
        details = contract.get("details") or contract
        strike = _as_float(details.get("strike_price") or contract.get("strike_price"))
        oi = _as_int(contract.get("open_interest") or details.get("open_interest"))
        iv = _as_float(
            contract.get("implied_volatility")
            or (contract.get("greeks") or {}).get("implied_volatility")
        )
        if underlying_price > 0 and strike > 0:
            moneyness = abs(strike - underlying_price) / underlying_price
        else:
            moneyness = 1.0
        score = oi * 0.001 + iv * 10.0 - moneyness * 5.0
        ctype = details.get("contract_type") or contract.get("contract_type") or "?"
        exp = details.get("expiration_date") or contract.get("expiration_date") or "?"
        line = (
            f"{ctype} {strike} exp {exp}: OI={oi:,}, IV={iv:.2f}"
            if iv
            else f"{ctype} {strike} exp {exp}: OI={oi:,}"
        )
        scored.append((score, line))

    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [line for _score, line in scored[:top_n]]


def is_us_weekday(d: date) -> bool:
    return d.weekday() < 5
