"""
Shared helpers for catalyst recall and featured-mover quality audits.

Reads production telemetry under validation_telemetry/ when present; otherwise
bootstraps movers from market_internals + Massive headline enrichment.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any, Iterable, Optional

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parents[1]
TELEMETRY_ROOT = REPO_ROOT / "validation_telemetry"

HIT_OVERLAP_THRESHOLD = 0.35
RECENCY_WEIGHT = 0.55
SENTIMENT_WEIGHT = 0.45

POSITIVE_WORDS = frozenset(
    {
        "beat",
        "beats",
        "surge",
        "surges",
        "soar",
        "soars",
        "jump",
        "jumps",
        "gain",
        "gains",
        "rally",
        "rallies",
        "upgrade",
        "upgrades",
        "raise",
        "raises",
        "record",
        "profit",
        "profits",
        "growth",
        "strong",
        "bullish",
        "positive",
        "approval",
        "approved",
        "win",
        "wins",
        "deal",
        "partnership",
        "buyout",
        "acquisition",
    }
)
NEGATIVE_WORDS = frozenset(
    {
        "miss",
        "misses",
        "fall",
        "falls",
        "drop",
        "drops",
        "plunge",
        "plunges",
        "sink",
        "sinks",
        "cut",
        "cuts",
        "downgrade",
        "downgrades",
        "loss",
        "losses",
        "weak",
        "bearish",
        "negative",
        "reject",
        "rejected",
        "delay",
        "delayed",
        "lawsuit",
        "investigation",
        "fraud",
        "bankruptcy",
        "warning",
        "recall",
        "split",
        "reverse",
    }
)

_TOKEN_RE = re.compile(r"[a-z0-9]{3,}", re.I)


def ny_now() -> datetime:
    return datetime.now(tz=NY)


def is_us_market_weekday(d: date) -> bool:
    return d.weekday() < 5


def previous_trading_day(d: date) -> date:
    cur = d - timedelta(days=1)
    while not is_us_market_weekday(cur):
        cur -= timedelta(days=1)
    return cur


def resolve_audit_trade_date(now: Optional[datetime] = None) -> date:
    """
    Default trade date for audits. Before 5:30pm ET, use the prior session
    (weekends skipped). After 5:30pm ET on a weekday, use today.
    """
    now = now or ny_now()
    local_date = now.date()
    cutoff = datetime.combine(local_date, time(17, 30), tzinfo=NY)
    if now < cutoff:
        return previous_trading_day(local_date)
    if not is_us_market_weekday(local_date):
        return previous_trading_day(local_date)
    return local_date


def audit_as_of_for_trade_date(trade_date: date, now: Optional[datetime] = None) -> datetime:
    """
    End of the news lookback window for recall audits on trade_date.
    Same calendar day before 9:15pm ET → now; otherwise 9:15pm ET on trade_date.
    """
    now = now or ny_now()
    end_default = datetime.combine(trade_date, time(21, 15), tzinfo=NY)
    if now.date() == trade_date and now < end_default:
        return now
    return end_default


def telemetry_dir_for(trade_date: date) -> Path:
    return TELEMETRY_ROOT / trade_date.isoformat()


def _read_json(path: Path) -> Any:
    if not path.is_file():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        logger.warning("Invalid JSON in %s: %s", path, exc)
        return None


def write_run_snapshot(
    *,
    trade_date: date,
    time_period: str,
    post_data: dict[str, Any],
    context: dict[str, Any],
    sent_at: Optional[datetime] = None,
) -> Path:
    """Persist telemetry after a successful email send (production ground truth)."""
    sent_at = sent_at or ny_now()
    day_dir = telemetry_dir_for(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)

    gainers = context.get("top_gainers") or []
    losers = context.get("top_losers") or []
    featured = post_data.get("featured_stock") or ""
    featured_mover = context.get("featured_mover") or {}

    payload = {
        "trade_date": trade_date.isoformat(),
        "time_period": time_period,
        "sent_at": sent_at.isoformat(),
        "featured_stock": str(featured).upper() if featured else "",
        "featured_mover": featured_mover,
        "top_gainers": gainers,
        "top_losers": losers,
        "post_excerpt": (post_data.get("post") or "")[:500],
        "market_internals": post_data.get("market_internals"),
    }
    run_name = f"{time_period}_run.json"
    run_path = day_dir / run_name
    run_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    (day_dir / "top_gainers.json").write_text(
        json.dumps(gainers, indent=2), encoding="utf-8"
    )
    (day_dir / "top_losers.json").write_text(
        json.dumps(losers, indent=2), encoding="utf-8"
    )
    return run_path


def _normalize_mover(raw: dict[str, Any], side: str) -> dict[str, Any]:
    ticker = str(
        raw.get("ticker") or raw.get("symbol") or raw.get("T") or ""
    ).upper()
    pct = raw.get("change_percentage")
    if pct is None:
        pct = raw.get("pct")
    try:
        change_pct = float(pct)
    except (TypeError, ValueError):
        change_pct = 0.0
    catalyst = str(raw.get("catalyst") or raw.get("why") or "").strip()
    return {
        "ticker": ticker,
        "change_percentage": change_pct,
        "catalyst": catalyst,
        "side": side,
    }


def load_movers_from_telemetry(trade_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    day_dir = telemetry_dir_for(trade_date)
    gainers = _read_json(day_dir / "top_gainers.json")
    losers = _read_json(day_dir / "top_losers.json")
    if isinstance(gainers, list) and isinstance(losers, list) and (gainers or losers):
        return (
            [_normalize_mover(m, "gainer") for m in gainers if m.get("ticker") or m.get("symbol")],
            [_normalize_mover(m, "loser") for m in losers if m.get("ticker") or m.get("symbol")],
        )

    run = _read_json(day_dir / "postmarket_run.json") or _read_json(day_dir / "premarket_run.json")
    if isinstance(run, dict):
        g = run.get("top_gainers") or []
        l = run.get("top_losers") or []
        if g or l:
            return (
                [_normalize_mover(m, "gainer") for m in g],
                [_normalize_mover(m, "loser") for m in l],
            )
    return [], []


def bootstrap_telemetry_if_missing(trade_date: date) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    gainers, losers = load_movers_from_telemetry(trade_date)
    if gainers or losers:
        return gainers, losers

    logger.info("No telemetry for %s — bootstrapping from market_internals", trade_date)
    try:
        from market_internals import fetch_movers_and_breadth
        from massive_client import enrich_movers_with_news, fetch_top_movers_with_news

        mb = fetch_movers_and_breadth(n=5)
        movers = mb.get("movers") or {}
        raw_g = movers.get("gainers") or []
        raw_l = movers.get("losers") or []
        if raw_g or raw_l:
            enriched_g = enrich_movers_with_news(
                [
                    {"symbol": m["symbol"], "change_percentage": m["pct"], "last": m.get("last", 0)}
                    for m in raw_g
                ],
                limit=len(raw_g) or 5,
            )
            enriched_l = enrich_movers_with_news(
                [
                    {"symbol": m["symbol"], "change_percentage": m["pct"], "last": m.get("last", 0)}
                    for m in raw_l
                ],
                limit=len(raw_l) or 5,
            )
            gainers = [_normalize_mover(m, "gainer") for m in enriched_g]
            losers = [_normalize_mover(m, "loser") for m in enriched_l]
        else:
            logger.warning("market_internals empty — falling back to Massive snapshot movers")
            snapshot = fetch_top_movers_with_news(limit=10)
            gainers = [
                _normalize_mover(m, "gainer")
                for m in snapshot
                if float(m.get("change_percentage") or 0) >= 0
            ][:5]
            losers = [
                _normalize_mover(m, "loser")
                for m in snapshot
                if float(m.get("change_percentage") or 0) < 0
            ][:5]
            if not losers:
                losers = sorted(
                    [_normalize_mover(m, "loser") for m in fetch_top_movers_with_news(limit=20)],
                    key=lambda x: float(x.get("change_percentage") or 0),
                )[:5]
    except Exception as exc:
        logger.error("Bootstrap failed: %s", exc)
        gainers, losers = [], []

    day_dir = telemetry_dir_for(trade_date)
    day_dir.mkdir(parents=True, exist_ok=True)
    if gainers:
        (day_dir / "top_gainers.json").write_text(json.dumps(gainers, indent=2), encoding="utf-8")
    if losers:
        (day_dir / "top_losers.json").write_text(json.dumps(losers, indent=2), encoding="utf-8")
    return gainers, losers


def tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in _TOKEN_RE.finditer(text or "")}


def token_overlap(a: str, b: str) -> float:
    ta, tb = tokenize(a), tokenize(b)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _headline_sentiment_label(headline: str, api_sentiment: str) -> str:
    api = (api_sentiment or "").lower()
    if api in {"positive", "negative", "neutral"}:
        return api
    words = tokenize(headline)
    pos = len(words & POSITIVE_WORDS)
    neg = len(words & NEGATIVE_WORDS)
    if pos > neg:
        return "positive"
    if neg > pos:
        return "negative"
    return "neutral"


def sentiment_match_score(headline: str, api_sentiment: str, is_gainer: bool) -> float:
    label = _headline_sentiment_label(headline, api_sentiment)
    wanted = "positive" if is_gainer else "negative"
    if label == wanted:
        return 1.0
    if label == "neutral":
        return 0.55
    return 0.15


def recency_score(published: datetime, window_start: datetime, window_end: datetime) -> float:
    span = (window_end - window_start).total_seconds()
    if span <= 0:
        return 0.0
    age = (window_end - published).total_seconds()
    age = max(0.0, min(age, span))
    return 1.0 - (age / span)


def score_headline(
    headline: str,
    published: datetime,
    api_sentiment: str,
    *,
    window_start: datetime,
    window_end: datetime,
    is_gainer: bool,
) -> float:
    if published < window_start or published > window_end:
        return -1.0
    rec = recency_score(published, window_start, window_end)
    sent = sentiment_match_score(headline, api_sentiment, is_gainer)
    return RECENCY_WEIGHT * rec + SENTIMENT_WEIGHT * sent


def parse_published_utc(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        ts = float(value)
        if ts > 1e12:
            ts /= 1000.0
        return datetime.fromtimestamp(ts, tz=pytz.UTC).astimezone(NY)
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(NY)
        except ValueError:
            return None
    return None


def fetch_ticker_news(
    ticker: str,
    *,
    limit: int = 10,
    published_gte: Optional[datetime] = None,
    published_lte: Optional[datetime] = None,
) -> list[dict[str, Any]]:
    from massive_client import _normalize_news_item, _request

    params: dict[str, Any] = {
        "ticker": ticker.upper(),
        "limit": max(limit, 10),
        "order": "desc",
        "sort": "published_utc",
    }
    if published_gte is not None:
        params["published_utc.gte"] = published_gte.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    if published_lte is not None:
        params["published_utc.lte"] = published_lte.astimezone(pytz.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")

    data = _request("/v2/reference/news", params)
    items = [_normalize_news_item(item) for item in data.get("results", [])[: limit * 2]]
    out: list[dict[str, Any]] = []
    for item in items:
        published = parse_published_utc(item.get("datetime"))
        if published is None:
            continue
        if published_gte and published < published_gte:
            continue
        if published_lte and published > published_lte:
            continue
        out.append(
            {
                "headline": item.get("headline") or "",
                "published": published,
                "sentiment": item.get("sentiment") or "",
                "url": item.get("url") or "",
            }
        )
    out.sort(key=lambda x: x["published"], reverse=True)
    return out[:limit]


def rank_headlines_for_mover(
    ticker: str,
    *,
    window_start: datetime,
    window_end: datetime,
    is_gainer: bool,
    limit: int = 5,
) -> list[dict[str, Any]]:
    articles = fetch_ticker_news(
        ticker,
        limit=20,
        published_gte=window_start,
        published_lte=window_end,
    )
    scored: list[dict[str, Any]] = []
    for art in articles:
        score = score_headline(
            art["headline"],
            art["published"],
            art.get("sentiment", ""),
            window_start=window_start,
            window_end=window_end,
            is_gainer=is_gainer,
        )
        if score < 0:
            continue
        scored.append({**art, "score": round(score, 4)})
    scored.sort(key=lambda x: (-x["score"], x["published"]), reverse=False)
    return scored[:limit]


def evaluate_catalyst_recall(
    pipeline_catalyst: str,
    ranked_headlines: list[dict[str, Any]],
) -> dict[str, Any]:
    best = ranked_headlines[0] if ranked_headlines else None
    best_headline = best["headline"] if best else ""
    overlap = token_overlap(pipeline_catalyst, best_headline) if pipeline_catalyst else 0.0
    if pipeline_catalyst and ranked_headlines:
        for cand in ranked_headlines:
            ov = token_overlap(pipeline_catalyst, cand["headline"])
            if ov > overlap:
                overlap = ov
                best_headline = cand["headline"]
                best = cand
    hit = bool(pipeline_catalyst) and overlap >= HIT_OVERLAP_THRESHOLD
    return {
        "pipeline_catalyst": pipeline_catalyst or "(empty)",
        "massive_best": best_headline or "(no headlines in window)",
        "overlap": round(overlap, 3),
        "hit": hit,
        "top_headlines": ranked_headlines,
    }


def load_featured_stock(trade_date: date) -> tuple[str, dict[str, Any]]:
    run = _read_json(telemetry_dir_for(trade_date) / "postmarket_run.json")
    if isinstance(run, dict):
        featured = str(run.get("featured_stock") or "").upper()
        if featured:
            return featured, run
        fm = run.get("featured_mover") or {}
        if fm.get("ticker"):
            return str(fm["ticker"]).upper(), run
    gainers, losers = bootstrap_telemetry_if_missing(trade_date)
    combined = gainers + losers
    if not combined:
        return "", {}
    best = max(combined, key=lambda m: abs(float(m.get("change_percentage") or 0)))
    return str(best.get("ticker") or "").upper(), {"fallback": "largest_abs_move"}


def fetch_minute_bars(ticker: str, trade_date: date) -> list[dict[str, Any]]:
    from massive_client import _request

    symbol = ticker.upper()
    data = _request(
        f"/v2/aggs/ticker/{symbol}/range/1/minute/{trade_date.isoformat()}/{trade_date.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    bars: list[dict[str, Any]] = []
    for item in data.get("results") or []:
        ts_ms = int(item.get("t") or 0)
        ts = datetime.fromtimestamp(ts_ms / 1000, tz=NY)
        bars.append(
            {
                "ts": ts,
                "open": float(item.get("o") or 0),
                "high": float(item.get("h") or 0),
                "low": float(item.get("l") or 0),
                "close": float(item.get("c") or 0),
                "volume": int(item.get("v") or 0),
            }
        )
    return bars


def analyze_intraday_moves(bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {}
    rth_open = datetime.combine(bars[0]["ts"].date(), time(9, 30), tzinfo=NY)
    rth_bars = [b for b in bars if b["ts"] >= rth_open]
    ref_bar = rth_bars[0] if rth_bars else bars[0]
    ref_price = ref_bar["open"] or ref_bar["close"]

    max_1m: Optional[dict[str, Any]] = None
    prev_close = bars[0]["open"]
    for b in bars:
        if prev_close > 0:
            pct = (b["close"] - prev_close) / prev_close * 100.0
            if max_1m is None or abs(pct) > abs(max_1m["pct"]):
                max_1m = {"ts": b["ts"], "pct": round(pct, 2)}
        prev_close = b["close"]

    max_from_open: Optional[dict[str, Any]] = None
    if ref_price > 0:
        for b in rth_bars or bars:
            pct = (b["close"] - ref_price) / ref_price * 100.0
            if max_from_open is None or abs(pct) > abs(max_from_open["pct"]):
                max_from_open = {"ts": b["ts"], "pct": round(pct, 2)}

    return {
        "bar_count": len(bars),
        "largest_1m_move": max_1m,
        "max_from_rth_open": max_from_open,
        "rth_open_ts": rth_open.isoformat(),
    }


def fetch_options_snapshot(underlying: str) -> dict[str, Any]:
    from massive_client import _request

    data = _request(f"/v3/snapshot/options/{underlying.upper()}", {"limit": 50})
    if not data:
        return {"ok": False, "error": "empty_response"}
    if data.get("status") == "NOT_AUTHORIZED" or "results" not in data:
        return {"ok": False, "error": "tier_or_auth", "raw_keys": list(data.keys())}
    results = data.get("results") or []
    flagged: list[dict[str, Any]] = []
    for contract in results[:50]:
        details = contract.get("details") or {}
        day = contract.get("day") or {}
        greeks = contract.get("greeks") or {}
        iv = greeks.get("implied_volatility") or contract.get("implied_volatility")
        oi = details.get("open_interest") or contract.get("open_interest")
        vol = day.get("volume") or contract.get("volume")
        if vol and int(vol) >= 500:
            flagged.append(
                {
                    "strike": details.get("strike_price"),
                    "type": details.get("contract_type"),
                    "exp": details.get("expiration_date"),
                    "volume": vol,
                    "open_interest": oi,
                    "iv": iv,
                }
            )
    flagged.sort(key=lambda x: int(x.get("volume") or 0), reverse=True)
    return {"ok": True, "contracts_scanned": len(results), "unusual": flagged[:8]}


def ensure_report_dirs() -> None:
    (TELEMETRY_ROOT / "reports").mkdir(parents=True, exist_ok=True)
    (TELEMETRY_ROOT / "research_log").mkdir(parents=True, exist_ok=True)


def should_skip_weekend(now: Optional[datetime] = None, force: bool = False) -> bool:
    if force:
        return False
    return (now or ny_now()).weekday() >= 5
