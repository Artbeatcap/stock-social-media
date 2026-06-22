"""
Massive market data adapter.

The public helpers in this module intentionally return the same shapes the
legacy Tradier/Finnhub callers used, so downstream brief and Twitter generation
code can switch providers without template rewrites.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any, Dict, Iterable, List, Optional

import pytz
import requests

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
MASSIVE_BASE_URL = os.getenv("MASSIVE_BASE_URL", "https://api.massive.com").rstrip("/")
MASSIVE_API_KEY = os.getenv("MASSIVE_API_KEY")

INDEX_SYMBOLS = {"VIX": "I:VIX", "SPX": "I:SPX", "NDX": "I:NDX", "DJI": "I:DJI"}
DEFAULT_QUOTE_SYMBOLS = ["SPY", "QQQ", "IWM", "VIX"]
DEFAULT_MOVER_UNIVERSE = [
    "NVDA", "TSLA", "AMD", "AAPL", "MSFT", "META", "GOOGL", "AMZN", "NFLX", "DIS",
    "AVGO", "INTC", "MU", "SMCI", "PLTR", "SOUN", "SOFI", "RIVN", "LCID", "F",
]


def _api_key() -> Optional[str]:
    return os.getenv("MASSIVE_API_KEY") or MASSIVE_API_KEY


def _request(path: str, params: Optional[dict[str, Any]] = None, timeout: int = 12) -> dict[str, Any]:
    key = _api_key()
    if not key:
        logger.warning("MASSIVE_API_KEY not configured")
        return {}

    query = dict(params or {})
    query.setdefault("apiKey", key)
    try:
        resp = requests.get(f"{MASSIVE_BASE_URL}{path}", params=query, timeout=timeout)
        if resp.status_code != 200:
            logger.warning("Massive HTTP %s for %s: %s", resp.status_code, path, resp.text[:200])
            return {}
        return resp.json() or {}
    except Exception as exc:
        logger.warning("Massive request failed for %s: %s", path, exc)
        return {}


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _as_int(value: Any, default: int = 0) -> int:
    try:
        if value is None:
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _chunk(items: Iterable[str], size: int = 50) -> Iterable[list[str]]:
    batch: list[str] = []
    for item in items:
        batch.append(item)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def _snapshot_payload(data: dict[str, Any]) -> dict[str, Any]:
    if isinstance(data.get("ticker"), dict):
        return data["ticker"]
    if isinstance(data.get("results"), dict):
        return data["results"]
    return data


def _extract_last_trade(snapshot: dict[str, Any]) -> dict[str, Any]:
    value = (
        snapshot.get("lastTrade")
        or snapshot.get("last_trade")
        or snapshot.get("lastTradePrice")
        or snapshot.get("last")
        or {}
    )
    if isinstance(value, dict):
        return value
    return {"price": value}


def _extract_day(snapshot: dict[str, Any], key: str) -> dict[str, Any]:
    value = snapshot.get(key) or snapshot.get(key.replace("_", "")) or {}
    return value if isinstance(value, dict) else {}


def _normalize_quote(symbol: str, raw: dict[str, Any], is_index: bool = False) -> dict[str, Any]:
    snapshot = _snapshot_payload(raw)
    ticker = (
        snapshot.get("ticker")
        or snapshot.get("symbol")
        or snapshot.get("T")
        or symbol
    )
    ticker = str(ticker).replace("I:", "").upper()

    day = _extract_day(snapshot, "day")
    prev_day = _extract_day(snapshot, "prevDay") or _extract_day(snapshot, "previous_day")
    last_trade = _extract_last_trade(snapshot)
    min_bar = _extract_day(snapshot, "min")

    last = (
        snapshot.get("last")
        or snapshot.get("value")
        or last_trade.get("p")
        or last_trade.get("price")
        or day.get("c")
        or min_bar.get("c")
    )
    prevclose = (
        snapshot.get("prevclose")
        or snapshot.get("previous_close")
        or prev_day.get("c")
        or day.get("o")
    )
    last_f = _as_float(last)
    prev_f = _as_float(prevclose)
    change = _as_float(snapshot.get("todaysChange"), last_f - prev_f if prev_f else 0.0)
    change_pct = _as_float(
        snapshot.get("todaysChangePerc"),
        (change / prev_f * 100.0) if prev_f else 0.0,
    )

    return {
        "symbol": ticker,
        "last": last_f,
        "change": change,
        "change_percentage": change_pct,
        "volume": _as_int(snapshot.get("volume") or day.get("v")),
        "prevclose": prev_f,
        "type": "index" if is_index else "stock",
    }


def get_snapshot(symbol: str) -> dict[str, Any]:
    """Return one normalized stock snapshot using legacy quote keys."""
    sym = symbol.upper()
    data = _request(f"/v2/snapshot/locale/us/markets/stocks/tickers/{sym}")
    return _normalize_quote(sym, data) if data else {}


def get_index_snapshot(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Return normalized index snapshots keyed by bare symbols, e.g. VIX."""
    out: dict[str, dict[str, Any]] = {}
    for symbol in symbols:
        bare = symbol.replace("I:", "").upper()
        massive_symbol = INDEX_SYMBOLS.get(bare, f"I:{bare}")
        data = (
            _request("/v3/snapshot/indices", {"ticker": massive_symbol})
            or _request(f"/v2/snapshot/locale/us/markets/indices/tickers/{massive_symbol}")
        )
        if not data:
            logger.warning("Massive index snapshot empty for %s", bare)
            continue
        results = data.get("results")
        if isinstance(results, list) and results:
            raw = results[0]
        else:
            raw = data
        quote = _normalize_quote(bare, raw, is_index=True)
        if quote.get("last"):
            out[bare] = quote
        else:
            logger.warning("Massive index snapshot missing last price for %s", bare)
    return out


def get_quotes_unified(symbols: list[str]) -> dict[str, dict[str, Any]]:
    """Route stocks and indices to their Massive endpoints and normalize output."""
    cleaned = [s.strip().upper() for s in symbols if s and s.strip()]
    stock_symbols = [s for s in cleaned if s.replace("I:", "") not in INDEX_SYMBOLS]
    index_symbols = [s for s in cleaned if s.replace("I:", "") in INDEX_SYMBOLS]

    quotes: dict[str, dict[str, Any]] = {}
    for group in _chunk(stock_symbols, 50):
        data = _request("/v2/snapshot/locale/us/markets/stocks/tickers", {"tickers": ",".join(group)})
        tickers = data.get("tickers") or data.get("results") or []
        if isinstance(tickers, dict):
            tickers = [tickers]
        if tickers:
            for raw in tickers:
                quote = _normalize_quote(str(raw.get("ticker") or raw.get("T") or ""), raw)
                if quote.get("symbol"):
                    quotes[quote["symbol"]] = quote
        else:
            for symbol in group:
                quote = get_snapshot(symbol)
                if quote:
                    quotes[symbol] = quote

    quotes.update(get_index_snapshot(index_symbols))
    return quotes


def get_quotes_list_tradier_quotes(symbols: list[str]) -> list[dict[str, Any]]:
    """Return a flat list shaped like Tradier's quote dictionaries."""
    quotes = get_quotes_unified(symbols)
    return [quotes[symbol] for symbol in quotes]


def get_quotes_list(symbols: list[str]) -> list[dict[str, Any]]:
    return get_quotes_list_tradier_quotes(symbols)


def fetch_spy_qqq_data() -> Dict[str, Any]:
    """Fetch SPY/QQQ/IWM/VIX data with the Twitter script's expected keys."""
    return get_quotes_unified(DEFAULT_QUOTE_SYMBOLS)


def fetch_stock_prices_tradier() -> Dict[str, Dict[str, float]]:
    """Return lower-case price map compatible with market_brief_generator."""
    quotes = get_quotes_unified(DEFAULT_QUOTE_SYMBOLS)
    prices: dict[str, dict[str, float]] = {}
    for symbol, quote in quotes.items():
        prices[symbol.lower()] = {
            "current_price": _as_float(quote.get("last")),
            "change": _as_float(quote.get("change")),
            "change_percent": _as_float(quote.get("change_percentage")),
        }
    if "vix" not in prices:
        logger.warning("Massive VIX quote unavailable; index coverage may be tier-gated")
    return prices


def get_top_movers(limit: int = 10) -> list[dict[str, Any]]:
    """Fetch Massive top gainers/losers, falling back to a liquid watchlist scan."""
    movers: list[dict[str, Any]] = []
    for direction in ("gainers", "losers"):
        data = _request(f"/v2/snapshot/locale/us/markets/stocks/{direction}")
        tickers = data.get("tickers") or data.get("results") or []
        if isinstance(tickers, dict):
            tickers = [tickers]
        for raw in tickers:
            quote = _normalize_quote(str(raw.get("ticker") or raw.get("T") or ""), raw)
            if quote.get("symbol"):
                movers.append(quote)

    if not movers:
        movers = get_quotes_list(DEFAULT_MOVER_UNIVERSE)

    movers = [m for m in movers if m.get("type") == "stock" and m.get("last", 0) > 0]
    movers.sort(key=lambda item: abs(_as_float(item.get("change_percentage"))), reverse=True)
    return movers[:limit]


def _normalize_news_item(item: dict[str, Any]) -> dict[str, Any]:
    published = item.get("published_utc") or item.get("published") or item.get("datetime")
    timestamp = int(datetime.now(tz=NY).timestamp())
    if isinstance(published, str):
        try:
            timestamp = int(datetime.fromisoformat(published.replace("Z", "+00:00")).timestamp())
        except ValueError:
            pass
    elif isinstance(published, (int, float)):
        timestamp = int(published)

    insights = item.get("insights") or []
    sentiment = ""
    if isinstance(insights, list) and insights:
        sentiment = str(insights[0].get("sentiment") or "").lower()

    return {
        "headline": item.get("title") or item.get("headline") or "",
        "summary": item.get("description") or item.get("summary") or "",
        "source": item.get("publisher", {}).get("name") if isinstance(item.get("publisher"), dict) else item.get("source", "Massive"),
        "url": item.get("article_url") or item.get("url") or "",
        "datetime": timestamp,
        "sentiment": sentiment,
        "tickers": item.get("tickers") or [],
    }


def get_news_for_ticker(ticker: str, limit: int = 5) -> list[dict[str, Any]]:
    data = _request(
        "/v2/reference/news",
        {
            "ticker": ticker.upper(),
            "limit": limit,
            "order": "desc",
            "sort": "published_utc",
        },
    )
    return [_normalize_news_item(item) for item in data.get("results", [])[:limit]]


def get_news_for_ticker_since(
    ticker: str,
    *,
    since_ts: int,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Return ticker news published at or after *since_ts* (unix seconds, ET-aware ok)."""
    items = get_news_for_ticker(ticker, limit=limit)
    return [item for item in items if int(item.get("datetime") or 0) >= since_ts]


def get_minute_bars(ticker: str, session_date: str, limit: int = 50000) -> list[dict[str, Any]]:
    """Fetch 1-minute OHLCV bars for a single session date (YYYY-MM-DD)."""
    sym = ticker.upper()
    data = _request(
        f"/v2/aggs/ticker/{sym}/range/1/minute/{session_date}/{session_date}",
        {"adjusted": "true", "sort": "asc", "limit": limit},
    )
    bars: list[dict[str, Any]] = []
    for item in data.get("results", []) or []:
        ts_ms = _as_int(item.get("t"))
        open_px = _as_float(item.get("o"))
        close_px = _as_float(item.get("c"))
        bars.append(
            {
                "timestamp_ms": ts_ms,
                "timestamp": datetime.fromtimestamp(ts_ms / 1000, tz=NY).isoformat(),
                "open": open_px,
                "high": _as_float(item.get("h")),
                "low": _as_float(item.get("l")),
                "close": close_px,
                "volume": _as_int(item.get("v")),
                "pct_change": ((close_px - open_px) / open_px * 100.0) if open_px else 0.0,
            }
        )
    return bars


def get_options_chain_snapshot(underlying: str, limit: int = 50) -> dict[str, Any]:
    """Fetch options chain snapshot; returns empty contracts on entitlement errors."""
    sym = underlying.upper()
    data = _request(f"/v3/snapshot/options/{sym}", {"limit": limit})
    if not data:
        return {"ok": False, "contracts": [], "error": "empty response"}

    results = data.get("results") or []
    contracts: list[dict[str, Any]] = []
    for item in results:
        details = item.get("details") or {}
        day = item.get("day") or {}
        greeks = item.get("greeks") or {}
        contracts.append(
            {
                "ticker": details.get("ticker") or item.get("ticker"),
                "strike": _as_float(details.get("strike_price")),
                "expiration": details.get("expiration_date") or "",
                "contract_type": str(details.get("contract_type") or "").lower(),
                "open_interest": _as_int(item.get("open_interest") or day.get("open_interest")),
                "volume": _as_int(day.get("volume")),
                "iv": _as_float(greeks.get("implied_volatility") or item.get("implied_volatility")),
                "last": _as_float(day.get("close") or item.get("last_quote", {}).get("midpoint")),
            }
        )
    return {"ok": True, "contracts": contracts, "count": len(contracts)}


def flag_unusual_options(contracts: list[dict[str, Any]], top_n: int = 5) -> list[dict[str, Any]]:
    """Flag strikes with elevated OI or IV relative to the chain median."""
    if not contracts:
        return []

    oi_values = sorted(c.oi for c in [_ContractMetrics(c) for c in contracts] if c.oi > 0)
    iv_values = sorted(c.iv for c in [_ContractMetrics(c) for c in contracts] if c.iv > 0)
    if not oi_values and not iv_values:
        return []

    oi_median = oi_values[len(oi_values) // 2] if oi_values else 0
    iv_median = iv_values[len(iv_values) // 2] if iv_values else 0.0

    flagged: list[dict[str, Any]] = []
    for raw in contracts:
        cm = _ContractMetrics(raw)
        reasons: list[str] = []
        if oi_median and cm.oi >= max(oi_median * 2, oi_median + 500):
            reasons.append(f"OI {cm.oi:,} vs median {oi_median:,}")
        if iv_median and cm.iv >= iv_median * 1.5:
            reasons.append(f"IV {cm.iv:.2f} vs median {iv_median:.2f}")
        if reasons:
            flagged.append({**raw, "flags": reasons, "flag_score": len(reasons)})

    flagged.sort(key=lambda row: (row.get("flag_score", 0), row.get("open_interest", 0)), reverse=True)
    return flagged[:top_n]


class _ContractMetrics:
    def __init__(self, raw: dict[str, Any]) -> None:
        self.oi = _as_int(raw.get("open_interest"))
        self.iv = _as_float(raw.get("iv"))

def get_market_news(limit: int = 20) -> list[dict[str, Any]]:
    data = _request(
        "/v2/reference/news",
        {
            "ticker": "SPY",
            "limit": limit,
            "order": "desc",
            "sort": "published_utc",
        },
    )
    return [_normalize_news_item(item) for item in data.get("results", [])[:limit]]


def enrich_movers_with_news(movers: list[dict[str, Any]], limit: int = 10) -> list[dict[str, Any]]:
    enriched: list[dict[str, Any]] = []
    for mover in movers[:limit]:
        symbol = str(mover.get("symbol") or mover.get("ticker") or "").upper()
        change_pct = _as_float(mover.get("change_percentage"))
        news_items = get_news_for_ticker(symbol, limit=5) if symbol else []
        wanted = "positive" if change_pct >= 0 else "negative"
        catalyst = ""
        for item in news_items:
            if item.get("sentiment") == wanted:
                catalyst = str(item.get("headline") or "")[:140]
                break
        if not catalyst and news_items:
            catalyst = str(news_items[0].get("headline") or "")[:140]
        enriched.append(
            {
                "ticker": symbol,
                "symbol": symbol,
                "change_percentage": change_pct,
                "price": _as_float(mover.get("last") or mover.get("price")),
                "last": _as_float(mover.get("last") or mover.get("price")),
                "catalyst": catalyst,
                "volume": _as_int(mover.get("volume")),
            }
        )
    return enriched


def fetch_top_movers_with_news(limit: int = 10) -> List[Dict[str, Any]]:
    return enrich_movers_with_news(get_top_movers(limit=max(limit * 2, 20)), limit=limit)


def fetch_gapping_stocks_tradier() -> dict[str, list[dict[str, str]]]:
    movers = fetch_top_movers_with_news(limit=20)
    formatted = [
        {
            "ticker": item["ticker"],
            "move": f"{_as_float(item.get('change_percentage')):+.2f}%",
            "why": str(item.get("catalyst") or ""),
        }
        for item in movers
        if abs(_as_float(item.get("change_percentage"))) >= 1.0
    ]
    now = datetime.now(tz=NY).time()
    if now >= datetime.strptime("16:00", "%H:%M").time() or now < datetime.strptime("07:00", "%H:%M").time():
        return {"after_hours": formatted, "premarket": []}
    if now < datetime.strptime("09:30", "%H:%M").time():
        return {"after_hours": [], "premarket": formatted}
    return {"after_hours": [], "premarket": []}


def get_daily_history(symbol: str, start_d: date, end_d: date) -> list[dict[str, Any]]:
    massive_symbol = INDEX_SYMBOLS.get(symbol.upper(), symbol.upper())
    data = _request(
        f"/v2/aggs/ticker/{massive_symbol}/range/1/day/{start_d.isoformat()}/{end_d.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 5000},
    )
    bars = []
    for item in data.get("results", []) or []:
        bars.append(
            {
                "date": datetime.fromtimestamp(item.get("t", 0) / 1000, tz=NY).date().isoformat(),
                "open": _as_float(item.get("o")),
                "high": _as_float(item.get("h")),
                "low": _as_float(item.get("l")),
                "close": _as_float(item.get("c")),
                "volume": _as_int(item.get("v")),
            }
        )
    return bars


def get_extended_hours_volume(symbol: str, start_dt: datetime, end_dt: datetime) -> int:
    massive_symbol = INDEX_SYMBOLS.get(symbol.upper(), symbol.upper())
    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)
    data = _request(
        f"/v2/aggs/ticker/{massive_symbol}/range/1/minute/{start_dt.date().isoformat()}/{end_dt.date().isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    volume = 0
    for item in data.get("results", []) or []:
        ts = _as_int(item.get("t"))
        if start_ms <= ts <= end_ms:
            volume += _as_int(item.get("v"))
    return volume


def fetch_stock_news(ticker: str) -> List[Dict[str, Any]]:
    return get_news_for_ticker(ticker, limit=3)


def fetch_news() -> List[Dict[str, Any]]:
    return get_market_news(limit=20)


def _tradier_history_daily(symbol: str, start_d: date, end_d: date) -> list[dict[str, Any]]:
    return get_daily_history(symbol, start_d, end_d)


def _tradier_timesales_volume(symbol: str, start_dt: datetime, end_dt: datetime) -> int:
    return get_extended_hours_volume(symbol, start_dt, end_dt)


def healthcheck() -> bool:
    quotes = fetch_spy_qqq_data()
    ok = bool(quotes.get("SPY") and quotes.get("QQQ"))
    if not quotes.get("VIX"):
        logger.warning("Massive healthcheck: VIX is blank; index coverage may be tier-gated")
    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    print("Massive quotes:")
    for symbol, quote in fetch_spy_qqq_data().items():
        print(f"{symbol}: last={quote.get('last')} change={quote.get('change')} pct={quote.get('change_percentage')}")
    print("\nTop movers:")
    for mover in fetch_top_movers_with_news(limit=5):
        print(f"{mover.get('ticker')}: {mover.get('change_percentage'):+.2f}% {mover.get('catalyst') or ''}")
    if not healthcheck():
        raise SystemExit(1)
