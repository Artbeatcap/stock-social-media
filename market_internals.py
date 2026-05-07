"""
market_internals.py
===================
Data enrichment layer for the post-market Twitter post generator.
Powered by the Massive (Polygon.io) API.

Fetches:
  • Sector performance       — top/bottom 3 of the 11 S&P sectors via SPDR ETFs
  • Top movers + A/D breadth — single market-wide pass, filtered to a quality
                               universe (price >= $5, volume >= 1M)
  • 50DMA trend signal       — SPY's distance from its 50-day moving average

Total API cost: ~4 HTTP requests per refresh (cached 5 min):
  1. Today's grouped daily (~12k stocks in one call)
  2. Yesterday's grouped daily (for day-over-day join)
  3. SPY 50-day SMA
  4. SPY previous-day bar

Sectors, top movers, and breadth all derive from calls 1+2 — no separate
/v3/snapshot call (which returns HTTP 400 against Massive).

Design principles:
  • Each metric is independent — a failure in one does not block the others.
  • Quality filter is non-negotiable. The raw Polygon `gainers` endpoint
    returns penny-stock noise (recent IPOs, splits, halts). We filter to
    actively-traded names so the LLM gets useful tickers, not garbage.
  • Today + yesterday grouped daily are joined to compute true day-over-day
    A/D and % change — both metrics share the same two API calls.
  • Schema is stable on partial failure (None / [] for missing data).

Usage:
    from market_internals import get_market_internals, format_internals_for_prompt

    data = get_market_internals()
    prompt_block = format_internals_for_prompt(data)
    # Inject prompt_block into your Twitter generator's system prompt.

CLI:
    python market_internals.py            # pretty-printed prompt block
    python market_internals.py --json     # raw JSON for piping
    python market_internals.py --debug    # show API call traces

Env:
    POLYGON_API_KEY    (or MASSIVE_API_KEY)   — required
    POLYGON_BASE_URL or MASSIVE_BASE_URL      — optional override (default api.polygon.io)
    QUALITY_MIN_PRICE                         — default 5.0
    QUALITY_MIN_VOLUME                        — default 1000000
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────────────────────────────────────

POLYGON_BASE = (
    os.getenv("POLYGON_BASE_URL")
    or os.getenv("MASSIVE_BASE_URL")
    or "https://api.polygon.io"
)
if "massive" in POLYGON_BASE.lower():
    POLYGON_KEY = os.getenv("MASSIVE_API_KEY") or os.getenv("POLYGON_API_KEY")
else:
    POLYGON_KEY = os.getenv("POLYGON_API_KEY") or os.getenv("MASSIVE_API_KEY")

# 11 SPDR sector ETFs — the standard practitioner set for S&P sector breakdown.
SECTOR_ETFS: Dict[str, str] = {
    "XLK":  "Technology",
    "XLF":  "Financials",
    "XLV":  "Health Care",
    "XLE":  "Energy",
    "XLY":  "Consumer Discretionary",
    "XLP":  "Consumer Staples",
    "XLI":  "Industrials",
    "XLU":  "Utilities",
    "XLB":  "Materials",
    "XLRE": "Real Estate",
    "XLC":  "Communication Services",
}

# Quality filter for the breadth + movers universe.
# Too tight: miss interesting names. Too loose: penny-stock noise.
QUALITY_MIN_PRICE = float(os.getenv("QUALITY_MIN_PRICE", "5.0"))
QUALITY_MIN_VOLUME = int(os.getenv("QUALITY_MIN_VOLUME", "1000000"))

# Polygon usually responds in <1s; 15s is generous.
HTTP_TIMEOUT = int(os.getenv("POLYGON_HTTP_TIMEOUT", "15"))

# In-process cache. Internals don't change minute-to-minute; 5 min is plenty.
_CACHE: Dict[str, Tuple[float, Any]] = {}
_CACHE_TTL_SECONDS = 300


def _cache_get(key: str) -> Optional[Any]:
    if key not in _CACHE:
        return None
    ts, val = _CACHE[key]
    if time.time() - ts > _CACHE_TTL_SECONDS:
        return None
    return val


def _cache_set(key: str, val: Any) -> None:
    _CACHE[key] = (time.time(), val)


# ──────────────────────────────────────────────────────────────────────────────
# Polygon HTTP wrapper
# ──────────────────────────────────────────────────────────────────────────────

class PolygonError(Exception):
    """Wraps any failure to fetch or parse a Polygon response."""


def _polygon_get(path: str, params: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """GET wrapper for Polygon. Adds API key, raises PolygonError on failure."""
    if not POLYGON_KEY:
        raise PolygonError("POLYGON_API_KEY (or MASSIVE_API_KEY) is not set in env")

    params = dict(params or {})
    params["apiKey"] = POLYGON_KEY
    url = f"{POLYGON_BASE}{path}"

    try:
        resp = requests.get(url, params=params, timeout=HTTP_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        raise PolygonError(f"HTTP error on {path}: {e}") from e
    except ValueError as e:
        raise PolygonError(f"Bad JSON from {path}: {e}") from e

    status = data.get("status")
    if status not in (None, "OK", "DELAYED"):
        raise PolygonError(
            f"Polygon non-OK status on {path}: {status}: "
            f"{data.get('error') or data.get('message')}"
        )

    return data


# ──────────────────────────────────────────────────────────────────────────────
# Trading-day helpers
# ──────────────────────────────────────────────────────────────────────────────

def _walk_back_to_trading_day(
    start: datetime,
    max_days: int = 7,
) -> Tuple[Optional[str], List[Dict[str, Any]]]:
    """
    Walk backwards from `start` until the grouped daily endpoint returns data
    (i.e., a real trading day, not a weekend or holiday).
    Returns (date_str, results) or (None, []) if nothing found within max_days.
    """
    for i in range(max_days):
        d = start - timedelta(days=i)
        date_str = d.strftime("%Y-%m-%d")
        try:
            data = _polygon_get(
                f"/v2/aggs/grouped/locale/us/market/stocks/{date_str}",
                params={"adjusted": "true"},
            )
            results = data.get("results") or []
            if results:
                logger.debug(f"Trading day found: {date_str} ({len(results)} tickers)")
                return date_str, results
        except PolygonError as e:
            logger.debug(f"No data for {date_str}: {e}")
            continue
    return None, []


def _fetch_grouped_daily_pair() -> Tuple[Dict[str, Dict[str, Any]], Dict[str, float], Optional[str]]:
    """
    Fetch today's grouped daily + yesterday's grouped daily, joined as maps.
    Cached — used by BOTH sector performance and movers/breadth so we make
    these two API calls only once per refresh.

    Returns:
      (today_map_keyed_by_ticker, yest_close_keyed_by_ticker, session_date_str)

    On failure returns ({}, {}, None) — callers must check.
    """
    cached = _cache_get("grouped_daily_pair")
    if cached is not None:
        return cached

    # Most recent trading day
    today_date, today_rows = _walk_back_to_trading_day(datetime.now())
    if not today_rows or not today_date:
        logger.warning("No recent trading day found")
        return ({}, {}, None)

    # Prior trading day
    today_dt = datetime.strptime(today_date, "%Y-%m-%d")
    _, yest_rows = _walk_back_to_trading_day(today_dt - timedelta(days=1))
    if not yest_rows:
        logger.warning(f"No prior trading day found before {today_date}")
        return ({}, {}, None)

    today_map: Dict[str, Dict[str, Any]] = {r["T"]: r for r in today_rows if "T" in r}
    yest_close: Dict[str, float] = {}
    for r in yest_rows:
        t = r.get("T")
        c = r.get("c")
        if t and c is not None:
            try:
                yest_close[t] = float(c)
            except (TypeError, ValueError):
                continue

    result = (today_map, yest_close, today_date)
    _cache_set("grouped_daily_pair", result)
    return result


# ──────────────────────────────────────────────────────────────────────────────
# Sector performance
# ──────────────────────────────────────────────────────────────────────────────

def fetch_sector_performance() -> List[Dict[str, Any]]:
    """
    Returns sectors sorted by day % change, descending.
    Each entry: {"etf": "XLK", "name": "Technology", "pct": 1.85}
    Derived from the same grouped daily data used for movers/breadth — no
    additional API calls, and avoids the /v3/snapshot endpoint which returned
    HTTP 400 against Massive's wrapper.
    """
    cached = _cache_get("sectors")
    if cached is not None:
        return cached

    today_map, yest_close, _ = _fetch_grouped_daily_pair()
    if not today_map or not yest_close:
        return []

    results: List[Dict[str, Any]] = []
    for etf, name in SECTOR_ETFS.items():
        today_row = today_map.get(etf)
        prev = yest_close.get(etf)
        if not today_row or not prev or prev <= 0:
            continue
        close = today_row.get("c")
        if close is None:
            continue
        try:
            pct = ((float(close) - prev) / prev) * 100
        except (TypeError, ValueError, ZeroDivisionError):
            continue

        results.append({
            "etf": etf,
            "name": name,
            "pct": round(float(pct), 2),
        })

    results.sort(key=lambda x: x["pct"], reverse=True)
    _cache_set("sectors", results)
    return results


# ──────────────────────────────────────────────────────────────────────────────
# Top movers + breadth (single-pass, market-wide)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_movers_and_breadth(
    n: int = 5,
    min_price: float = QUALITY_MIN_PRICE,
    min_volume: int = QUALITY_MIN_VOLUME,
) -> Dict[str, Any]:
    """
    Single-pass fetch of top movers + market breadth.
    Joins today's grouped daily with yesterday's, filters to a quality
    universe, then computes both top movers and A/D from the same dataset.

    Returns:
      {
        "movers": {"gainers": [...], "losers": [...]},
        "breadth": {"advancers", "decliners", "unchanged", "ad_ratio",
                    "universe_size", "scope", "session_date"}
      }
    """
    cache_key = f"movers_breadth_{n}_{min_price}_{min_volume}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    out: Dict[str, Any] = {
        "movers": {"gainers": [], "losers": []},
        "breadth": {
            "advancers": None,
            "decliners": None,
            "unchanged": None,
            "ad_ratio": None,
            "universe_size": None,
            "scope": f"price≥${min_price:.0f}, vol≥{min_volume:,}",
            "session_date": None,
        },
    }

    # 1-3. Fetch + join today/yesterday grouped daily (cached, shared with sectors)
    today_map, yest_close, today_date = _fetch_grouped_daily_pair()
    if not today_map or not yest_close:
        return out

    out["breadth"]["session_date"] = today_date

    # 4. Quality-filter, compute pct_change
    universe: List[Dict[str, Any]] = []
    for ticker, today_row in today_map.items():
        # Skip warrants/units/preferred (TBLAW, JOBY.WS, ERNAW etc.).
        # These dominate raw "gainers" lists with noise. Heuristic:
        #   - dot in ticker → unit/warrant (e.g. JOBY.WS)
        #   - length > 5 → typically warrant (e.g. TBLAW, ERNAW, SABSW)
        if "." in ticker or len(ticker) > 5:
            continue

        close = today_row.get("c")
        vol = today_row.get("v")
        if close is None or vol is None:
            continue
        try:
            close_f = float(close)
            vol_f = float(vol)
        except (TypeError, ValueError):
            continue

        if close_f < min_price or vol_f < min_volume:
            continue

        prev = yest_close.get(ticker)
        if not prev or prev <= 0:
            continue

        pct = ((close_f - prev) / prev) * 100
        universe.append({
            "ticker": ticker,
            "close": round(close_f, 2),
            "pct": round(pct, 2),
            "volume": int(vol_f),
        })

    if not universe:
        logger.warning("Quality universe is empty after filtering")
        return out

    # 5. Compute breadth
    advancers = sum(1 for u in universe if u["pct"] > 0)
    decliners = sum(1 for u in universe if u["pct"] < 0)
    unchanged = sum(1 for u in universe if u["pct"] == 0)
    ad_ratio = round(advancers / decliners, 2) if decliners > 0 else None

    out["breadth"].update({
        "advancers": advancers,
        "decliners": decliners,
        "unchanged": unchanged,
        "ad_ratio": ad_ratio,
        "universe_size": len(universe),
    })

    # 6. Top movers
    universe.sort(key=lambda x: x["pct"], reverse=True)
    out["movers"] = {
        "gainers": [
            {"symbol": u["ticker"], "pct": u["pct"], "last": u["close"]}
            for u in universe[:n]
        ],
        "losers": [
            {"symbol": u["ticker"], "pct": u["pct"], "last": u["close"]}
            for u in reversed(universe[-n:])
        ],
    }

    _cache_set(cache_key, out)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# 50DMA trend signal (SPY)
# ──────────────────────────────────────────────────────────────────────────────

def fetch_50dma_signal(ticker: str = "SPY") -> Dict[str, Any]:
    """
    SPY's relationship to its 50-day SMA. Pragmatic breadth-trend signal —
    cheap (2 API calls) and meaningful for fintwit. For true % of constituents
    above 50DMA, you'd need a nightly precompute job.

    Returns:
      {
        "ticker": "SPY",
        "price": 724.51,
        "sma_50": 712.34,
        "pct_above": 1.71,       # signed; negative if below
        "above_50dma": True,
      }
    """
    cached = _cache_get(f"50dma_{ticker}")
    if cached is not None:
        return cached

    out: Dict[str, Any] = {
        "ticker": ticker,
        "price": None,
        "sma_50": None,
        "pct_above": None,
        "above_50dma": None,
    }

    # 50DMA value
    try:
        data = _polygon_get(
            f"/v1/indicators/sma/{ticker}",
            params={
                "timespan": "day",
                "window": 50,
                "series_type": "close",
                "order": "desc",
                "limit": 1,
            },
        )
    except PolygonError as e:
        logger.error(f"50DMA fetch failed for {ticker}: {e}")
        return out

    sma_values = (data.get("results") or {}).get("values") or []
    if not sma_values:
        return out

    try:
        sma = float(sma_values[0]["value"])
    except (KeyError, TypeError, ValueError):
        return out

    out["sma_50"] = round(sma, 2)

    # Current price (prev-day close — fine for post-market context)
    try:
        prev_data = _polygon_get(f"/v2/aggs/ticker/{ticker}/prev")
        prev_results = prev_data.get("results") or []
        price = float(prev_results[0]["c"]) if prev_results else None
    except (PolygonError, KeyError, TypeError, ValueError) as e:
        logger.warning(f"Prev-day bar failed for {ticker}: {e}")
        price = None

    if price is None:
        return out

    pct_above = ((price - sma) / sma) * 100
    out.update({
        "price": round(price, 2),
        "pct_above": round(pct_above, 2),
        "above_50dma": price > sma,
    })

    _cache_set(f"50dma_{ticker}", out)
    return out


# ──────────────────────────────────────────────────────────────────────────────
# Orchestration
# ──────────────────────────────────────────────────────────────────────────────

def get_market_internals() -> Dict[str, Any]:
    """
    Single entry point. Each component is independent — a failure in one does
    not block the others. Schema is stable on partial failure.
    """
    out: Dict[str, Any] = {
        "as_of": datetime.now().isoformat(timespec="seconds"),
        "sectors": [],
        "movers": {"gainers": [], "losers": []},
        "breadth": {},
        "trend": {},
    }

    try:
        out["sectors"] = fetch_sector_performance()
    except Exception as e:
        logger.error(f"Sector fetch failed: {e}")

    try:
        mb = fetch_movers_and_breadth(n=5)
        out["movers"] = mb["movers"]
        out["breadth"] = mb["breadth"]
    except Exception as e:
        logger.error(f"Movers/breadth fetch failed: {e}")

    try:
        out["trend"] = fetch_50dma_signal("SPY")
    except Exception as e:
        logger.error(f"50DMA signal failed: {e}")

    return out


# ──────────────────────────────────────────────────────────────────────────────
# Prompt formatting
# ──────────────────────────────────────────────────────────────────────────────

def format_internals_for_prompt(data: Dict[str, Any]) -> str:
    """
    Render market internals into a compact text block for LLM prompt injection.
    Designed to be parseable by the model and readable by a human.
    """
    lines: List[str] = []

    sectors = data.get("sectors") or []
    if sectors:
        top = sectors[:3]
        bottom = list(reversed(sectors[-3:]))
        lines.append("SECTORS (S&P 500, day %):")
        lines.append(
            "  Leaders: " + ", ".join(
                f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in top
            )
        )
        lines.append(
            "  Laggards: " + ", ".join(
                f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in bottom
            )
        )

    breadth = data.get("breadth") or {}
    breadth_lines: List[str] = []
    if breadth.get("advancers") is not None and breadth.get("decliners") is not None:
        bl = (
            f"A/D: {breadth['advancers']} advancers / "
            f"{breadth['decliners']} decliners"
        )
        if breadth.get("ad_ratio") is not None:
            bl += f"  (ratio {breadth['ad_ratio']})"
        if breadth.get("universe_size"):
            bl += f"  [{breadth['universe_size']:,} actively-traded names]"
        breadth_lines.append(bl)

    trend = data.get("trend") or {}
    if trend.get("pct_above") is not None:
        direction = "above" if trend["above_50dma"] else "below"
        breadth_lines.append(
            f"SPY {direction} 50DMA by {abs(trend['pct_above']):.2f}% "
            f"(price ${trend['price']}, 50DMA ${trend['sma_50']})"
        )

    if breadth_lines:
        lines.append("BREADTH / TREND:")
        for bl in breadth_lines:
            lines.append(f"  {bl}")

    movers = data.get("movers") or {}
    gainers = movers.get("gainers") or []
    losers = movers.get("losers") or []
    if gainers or losers:
        lines.append("TOP MOVERS:")
        if gainers:
            lines.append(
                "  Gainers: " + ", ".join(
                    f"{m['symbol']} {m['pct']:+.2f}%" for m in gainers
                )
            )
        if losers:
            lines.append(
                "  Losers:  " + ", ".join(
                    f"{m['symbol']} {m['pct']:+.2f}%" for m in losers
                )
            )

    return "\n".join(lines) if lines else "(market internals unavailable)"


# ──────────────────────────────────────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────────────────────────────────────

def _main() -> None:
    parser = argparse.ArgumentParser(description="Fetch market internals from Polygon.")
    parser.add_argument("--json", action="store_true", help="Output raw JSON.")
    parser.add_argument("--debug", action="store_true", help="Show API call traces.")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    if not POLYGON_KEY:
        print("ERROR: POLYGON_API_KEY (or MASSIVE_API_KEY) is not set")
        raise SystemExit(1)

    data = get_market_internals()

    if args.json:
        print(json.dumps(data, indent=2, default=str))
    else:
        print(format_internals_for_prompt(data))


if __name__ == "__main__":
    _main()
