#!/usr/bin/env python3
"""Featured mover research log: multi-day news, intraday move, options snapshot."""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime, timedelta, time
from pathlib import Path
from typing import Any, Optional

import pytz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from massive_client import (  # noqa: E402
    _as_float,
    _request,
    get_news_for_ticker,
)
from scripts.catalyst_audit_common import (  # noqa: E402
    RESEARCH_LOG_DIR,
    bootstrap_telemetry_if_missing,
    ensure_telemetry_dirs,
    now_ny,
    resolve_audit_trade_date,
)

NY = pytz.timezone("America/New_York")


def get_minute_bars(ticker: str, trade_date: date) -> list[dict[str, Any]]:
    data = _request(
        f"/v2/aggs/ticker/{ticker.upper()}/range/1/minute/{trade_date.isoformat()}/{trade_date.isoformat()}",
        {"adjusted": "true", "sort": "asc", "limit": 50000},
    )
    bars = []
    for item in data.get("results", []) or []:
        ts_ms = int(item.get("t") or 0)
        bars.append(
            {
                "ts": datetime.fromtimestamp(ts_ms / 1000, tz=NY),
                "open": _as_float(item.get("o")),
                "close": _as_float(item.get("c")),
                "volume": int(item.get("v") or 0),
            }
        )
    return bars


def analyze_intraday(bars: list[dict[str, Any]]) -> dict[str, Any]:
    if not bars:
        return {"bar_count": 0}
    session_open = bars[0]["open"] or bars[0]["close"]
    max_jump = {"pct": 0.0, "at": None, "from": 0.0, "to": 0.0}
    max_from_open = {"pct": 0.0, "at": None, "price": 0.0}
    prev_close = session_open
    for bar in bars:
        if prev_close:
            jump = (bar["close"] - prev_close) / prev_close * 100.0
            if abs(jump) > abs(max_jump["pct"]):
                max_jump = {
                    "pct": jump,
                    "at": bar["ts"],
                    "from": prev_close,
                    "to": bar["close"],
                }
        if session_open:
            from_open = (bar["close"] - session_open) / session_open * 100.0
            if abs(from_open) > abs(max_from_open["pct"]):
                max_from_open = {"pct": from_open, "at": bar["ts"], "price": bar["close"]}
        prev_close = bar["close"]
    return {
        "bar_count": len(bars),
        "session_open": session_open,
        "max_1m_jump": max_jump,
        "max_from_open": max_from_open,
    }


def fetch_options_notes(ticker: str) -> str:
    data = _request(f"/v3/snapshot/options/{ticker.upper()}", {"limit": 50})
    if not data:
        return "Options snapshot unavailable (HTTP error or tier limit — often 403 on lower tiers)."
    results = data.get("results") or []
    if not results:
        return "Options snapshot returned no contracts."
    ivs = []
    oi_vals = []
    for row in results[:50]:
        details = row.get("details") or row
        greeks = row.get("greeks") or {}
        iv = greeks.get("implied_volatility") or row.get("implied_volatility")
        oi = details.get("open_interest") or row.get("open_interest")
        if iv is not None:
            ivs.append(float(iv))
        if oi is not None:
            oi_vals.append(int(oi))
    parts = [f"{len(results)} contracts in snapshot"]
    if ivs:
        parts.append(f"IV range {min(ivs):.2f}–{max(ivs):.2f}")
    if oi_vals:
        top_oi = sorted(oi_vals, reverse=True)[:3]
        parts.append(f"top OI values {top_oi}")
    return "; ".join(parts)


def news_last_n_days(ticker: str, trade_date: date, days: int = 7, limit: int = 30) -> list[dict[str, Any]]:
    cutoff = NY.localize(datetime.combine(trade_date, time(23, 59, 59)))
    start = cutoff - timedelta(days=days)
    items = get_news_for_ticker(ticker, limit=limit)
    out = []
    for item in items:
        ts = datetime.fromtimestamp(int(item.get("datetime") or 0), tz=NY)
        if start <= ts <= cutoff:
            out.append(item)
    out.sort(key=lambda x: int(x.get("datetime") or 0), reverse=True)
    return out


def resolve_featured_ticker(trade_date: date) -> tuple[str, dict[str, Any]]:
    gainers, losers, post = bootstrap_telemetry_if_missing(trade_date)
    featured = (post or {}).get("featured_stock")
    if featured:
        return str(featured).upper(), post
    pool = gainers + losers
    if not pool:
        return "", post
    best = max(pool, key=lambda m: abs(m["change_percentage"]))
    return best["ticker"], post


def append_research_section(trade_date: date, force: bool = False) -> Path:
    ensure_telemetry_dirs()
    ticker, post = resolve_featured_ticker(trade_date)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"

    marker = f"## Featured: ${ticker}"
    if log_path.exists() and not force:
        existing = log_path.read_text(encoding="utf-8")
        if marker in existing:
            print(f"Section already present in {log_path}")
            return log_path

    if not ticker:
        raise SystemExit("No featured ticker available for deep dive")

    news = news_last_n_days(ticker, trade_date, days=7)
    bars = get_minute_bars(ticker, trade_date)
    intraday = analyze_intraday(bars)
    options_note = fetch_options_notes(ticker)

    lines = [
        marker,
        "",
        f"- Post featured ticker: `{post.get('featured_stock') or ticker}`"
        + (" (fallback: largest |move|)" if post.get("bootstrapped") else ""),
        "",
        "### 7-day news narrative",
        "",
    ]
    if not news:
        lines.append("_No Massive headlines in the last 7 days._")
    else:
        for item in news[:12]:
            ts = datetime.fromtimestamp(int(item.get("datetime") or 0), tz=NY)
            headline = item.get("headline") or ""
            sent = item.get("sentiment") or "n/a"
            lines.append(f"- **{ts.strftime('%Y-%m-%d %H:%M ET')}** ({sent}) — {headline}")
    lines.extend(["", "### Intraday price action (1m bars)", ""])
    if intraday.get("bar_count", 0) == 0:
        lines.append("_No 1-minute aggregates for session._")
    else:
        jump = intraday.get("max_1m_jump") or {}
        frm = intraday.get("max_from_open") or {}
        if jump.get("at"):
            lines.append(
                f"- Largest 1m jump: **{jump['pct']:+.2f}%** at "
                f"{jump['at'].strftime('%H:%M ET')} ({jump['from']:.2f} → {jump['to']:.2f})"
            )
        if frm.get("at"):
            lines.append(
                f"- Max move from open: **{frm['pct']:+.2f}%** at "
                f"{frm['at'].strftime('%H:%M ET')} (price {frm['price']:.2f})"
            )
        lines.append(f"- Bars loaded: {intraday['bar_count']}")

    lines.extend(["", "### Options snapshot", "", f"- {options_note}", ""])

    header = [
        f"# Research log — {trade_date.isoformat()}",
        "",
        f"_Updated {now_ny().strftime('%Y-%m-%d %H:%M %Z')}_",
        "",
    ]
    log_path.write_text("\n".join(header + lines) + "\n", encoding="utf-8")

    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--trade-date", type=str, help="YYYY-MM-DD session")
    parser.add_argument("--force", action="store_true", help="Replace existing featured section")
    args = parser.parse_args()

    trade_date = (
        datetime.strptime(args.trade_date, "%Y-%m-%d").date()
        if args.trade_date
        else resolve_audit_trade_date()
    )
    path = append_research_section(trade_date, force=args.force)
    print(f"Updated {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
