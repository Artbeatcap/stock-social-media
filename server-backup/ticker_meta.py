#!/usr/bin/env python3
"""
Polygon/Massive ticker reference lookup with a 30-day on-disk cache.

Fail closed: a lookup error returns None so callers exclude the ticker
instead of waving it through unvetted.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("ticker_meta")

CACHE_TTL = timedelta(days=30)
MAX_WORKERS = 5
_LOCK = threading.Lock()

_CACHE_PATH = Path(
    os.getenv(
        "TICKER_METADATA_CACHE",
        str(Path(__file__).resolve().parent / "data" / "ticker_metadata_cache.json"),
    )
)


class TickerMeta:
    __slots__ = (
        "ticker",
        "name",
        "type",
        "market",
        "active",
        "primary_exchange",
        "market_cap",
    )

    def __init__(
        self,
        ticker: str,
        name: str,
        type: str,
        market: str,
        active: bool,
        primary_exchange: str,
        market_cap: Optional[float],
    ) -> None:
        self.ticker = ticker
        self.name = name
        self.type = type
        self.market = market
        self.active = active
        self.primary_exchange = primary_exchange
        self.market_cap = market_cap

    def to_dict(self) -> Dict[str, Any]:
        return {
            "ticker": self.ticker,
            "name": self.name,
            "type": self.type,
            "market": self.market,
            "active": self.active,
            "primary_exchange": self.primary_exchange,
            "market_cap": self.market_cap,
        }

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> "TickerMeta":
        cap = raw.get("market_cap")
        try:
            cap_f = float(cap) if cap is not None else None
        except (TypeError, ValueError):
            cap_f = None
        return cls(
            ticker=str(raw.get("ticker") or "").upper(),
            name=str(raw.get("name") or ""),
            type=str(raw.get("type") or ""),
            market=str(raw.get("market") or ""),
            active=bool(raw.get("active", True)),
            primary_exchange=str(raw.get("primary_exchange") or ""),
            market_cap=cap_f,
        )


def _load_cache() -> Dict[str, Any]:
    try:
        if not _CACHE_PATH.exists():
            return {}
        return json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("ticker meta cache unreadable: %s", exc)
        return {}


def _save_cache(cache: Dict[str, Any]) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _CACHE_PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache), encoding="utf-8")
        tmp.replace(_CACHE_PATH)
    except Exception as exc:
        logger.warning("ticker meta cache write failed: %s", exc)


def _fresh(entry: Dict[str, Any]) -> bool:
    fetched = entry.get("fetched_at")
    if not fetched:
        return False
    try:
        ts = datetime.fromisoformat(str(fetched).replace("Z", "+00:00"))
    except ValueError:
        return False
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - ts < CACHE_TTL


def _fetch_one_polygon(ticker: str) -> Optional[TickerMeta]:
    from market_internals import PolygonError, _polygon_get

    try:
        data = _polygon_get(f"/v3/reference/tickers/{ticker}")
    except PolygonError as exc:
        logger.warning("ticker meta lookup failed for %s: %s", ticker, exc)
        return None
    row = data.get("results") or {}
    if not row:
        logger.warning("ticker meta empty for %s — excluding", ticker)
        return None
    cap = row.get("market_cap")
    try:
        cap_f = float(cap) if cap is not None else None
    except (TypeError, ValueError):
        cap_f = None
    return TickerMeta(
        ticker=str(row.get("ticker") or ticker).upper(),
        name=str(row.get("name") or ""),
        type=str(row.get("type") or ""),
        market=str(row.get("market") or ""),
        active=bool(row.get("active", True)),
        primary_exchange=str(row.get("primary_exchange") or ""),
        market_cap=cap_f,
    )


def get_ticker_meta(
    tickers: List[str],
    fetch_one: Optional[Callable[[str], Optional[TickerMeta]]] = None,
) -> Dict[str, TickerMeta]:
    """
    Return meta keyed by ticker. Missing / failed lookups are omitted (fail closed).
    """
    fetch_one = fetch_one or _fetch_one_polygon
    wanted = [t.upper().strip() for t in tickers if t and str(t).strip()]
    wanted = list(dict.fromkeys(wanted))
    out: Dict[str, TickerMeta] = {}
    misses: List[str] = []

    with _LOCK:
        cache = _load_cache()
        for ticker in wanted:
            entry = cache.get(ticker)
            if entry and _fresh(entry) and entry.get("meta"):
                out[ticker] = TickerMeta.from_dict(entry["meta"])
            else:
                misses.append(ticker)

    if misses:
        fetched: Dict[str, TickerMeta] = {}
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
            futs = {pool.submit(fetch_one, t): t for t in misses}
            for fut in as_completed(futs):
                ticker = futs[fut]
                try:
                    meta = fut.result()
                except Exception as exc:
                    logger.warning("ticker meta lookup error for %s: %s", ticker, exc)
                    meta = None
                if meta is None:
                    continue
                fetched[ticker] = meta
        with _LOCK:
            cache = _load_cache()
            now = datetime.now(timezone.utc).isoformat()
            for ticker, meta in fetched.items():
                cache[ticker] = {"fetched_at": now, "meta": meta.to_dict()}
                out[ticker] = meta
            _save_cache(cache)

    return out


def ticker_had_recent_split(ticker: str, since_iso: str) -> bool:
    """True if Polygon reports a split on/after since_iso. Errors → True (exclude)."""
    from market_internals import PolygonError, _polygon_get

    try:
        data = _polygon_get(
            "/v3/reference/splits",
            params={"ticker": ticker.upper(), "execution_date.gte": since_iso, "limit": 1},
        )
    except PolygonError as exc:
        logger.warning("split lookup failed for %s: %s — excluding", ticker, exc)
        return True
    results = data.get("results") or []
    return bool(results)
