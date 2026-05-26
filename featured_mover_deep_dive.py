#!/usr/bin/env python3
"""
Featured-mover deep dive — weekdays ~5:30pm ET (after post-market email).

Reads ``featured_stock`` from today's sent postmarket telemetry, pulls:
  • Massive news (7 days) for multi-day narrative
  • 1-minute bars (today) to locate the largest intraday move window
  • Options chain snapshot for IV / OI flags

Appends a ``## Featured: $TICKER`` section to ``validation_telemetry/research/YYYY-MM-DD.md``.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

import pytz

from massive_client import (
    find_largest_move_window,
    get_minute_bars,
    get_news_for_ticker,
    get_options_chain_snapshot,
)
from validation_telemetry.store import append_featured_research, load_daily_telemetry

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


def _format_news_timeline(articles: List[dict[str, Any]], limit: int = 8) -> List[str]:
    lines: list[str] = []
    for item in articles[:limit]:
        ts = int(item.get("datetime") or 0)
        when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M ET") if ts else "unknown"
        headline = str(item.get("headline") or "").strip()
        sentiment = item.get("sentiment") or "n/a"
        lines.append(f"- **{when}** ({sentiment}) — {headline}")
    return lines


def _news_near_move(
    articles: List[dict[str, Any]],
    move_start_iso: Optional[str],
    *,
    hours: float = 2.0,
) -> List[str]:
    if not move_start_iso or not articles:
        return []
    try:
        move_dt = datetime.fromisoformat(move_start_iso)
        if move_dt.tzinfo is None:
            move_dt = NY.localize(move_dt)
    except ValueError:
        return []

    window_sec = hours * 3600
    hits: list[str] = []
    for item in articles:
        ts = int(item.get("datetime") or 0)
        if not ts:
            continue
        article_dt = datetime.fromtimestamp(ts, tz=NY)
        delta = abs((article_dt - move_dt).total_seconds())
        if delta <= window_sec:
            hits.append(
                f"- **{article_dt.strftime('%H:%M ET')}** — {item.get('headline', '')[:120]}"
            )
    return hits


def build_featured_section(
    ticker: str,
    *,
    post_excerpt: Optional[str] = None,
    day: Optional[str] = None,
) -> str:
    day = day or datetime.now(tz=NY).date().isoformat()
    symbol = ticker.upper().strip().lstrip("$")

    news_7d = get_news_for_ticker(symbol, limit=12, days_back=7)
    bars = get_minute_bars(symbol)
    move = find_largest_move_window(bars, window_minutes=15)
    options = get_options_chain_snapshot(symbol)

    lines = [
        f"## Featured: ${symbol}",
        "",
        f"_Deep dive appended {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}_",
        "",
    ]
    if post_excerpt:
        lines.extend(["### Post excerpt", "", f"> {post_excerpt[:280]}", ""])

    lines.append("### 7-day news narrative")
    if news_7d:
        lines.extend(_format_news_timeline(news_7d))
    else:
        lines.append("- _(no Massive articles in the last 7 days)_")
    lines.append("")

    lines.append("### Intraday price move (1m bars, today)")
    if move:
        lines.append(
            f"- Largest ~15m window: **{move['pct_change']:+.2f}%** "
            f"({move['start_ts']} → {move['end_ts']}), vol {move.get('volume', 0):,}"
        )
        near = _news_near_move(news_7d, move.get("start_ts"))
        if near:
            lines.append("- Headlines within ±2h of move start:")
            lines.extend(near)
        else:
            lines.append("- No headlines within ±2h of the move window start.")
    else:
        lines.append("- _(minute bars unavailable or flat session)_")
    lines.append("")

    lines.append("### Options chain flags")
    if not options.get("ok"):
        err = options.get("error") or "unavailable"
        lines.append(f"- _Options data not available ({err}). Tier upgrade may be required._")
    elif options.get("flags"):
        for flag in options["flags"]:
            lines.append(f"- {flag}")
    else:
        lines.append("- No unusual IV/OI flags in returned chain snapshot.")
    top_contracts = sorted(
        options.get("contracts") or [],
        key=lambda c: (c.get("open_interest", 0), c.get("volume", 0)),
        reverse=True,
    )[:5]
    if top_contracts:
        lines.append("")
        lines.append("| Contract | Strike | Exp | IV | OI | Vol |")
        lines.append("|----------|--------|-----|----|----|-----|")
        for c in top_contracts:
            lines.append(
                f"| {c.get('type', '')} | {c.get('strike')} | {c.get('expiration')} | "
                f"{c.get('iv', 0):.2f} | {c.get('open_interest', 0):,} | {c.get('volume', 0):,} |"
            )
    lines.append("")
    return "\n".join(lines)


def resolve_featured_ticker(record: dict[str, Any]) -> Optional[str]:
    postmarket = (record.get("posts") or {}).get("postmarket") or {}
    featured = postmarket.get("featured_stock")
    if featured:
        return str(featured).upper().lstrip("$")

    featured_mover = record.get("featured_mover") or {}
    ticker = featured_mover.get("ticker") or featured_mover.get("symbol")
    if ticker:
        return str(ticker).upper()

    # Fallback: biggest absolute mover of the day
    movers = list(record.get("top_gainers") or []) + list(record.get("top_losers") or [])
    if not movers:
        return None
    best = max(movers, key=lambda m: abs(float(m.get("change_percentage") or 0)))
    return str(best.get("ticker") or "").upper() or None


def run_deep_dive(day: Optional[str] = None, ticker: Optional[str] = None) -> Optional[str]:
    day = day or datetime.now(tz=NY).date().isoformat()
    record = load_daily_telemetry(day)
    symbol = (ticker or resolve_featured_ticker(record) or "").upper().lstrip("$")
    if not symbol:
        logger.error("No featured ticker for %s — postmarket post may not have been sent yet", day)
        return None

    post = (record.get("posts") or {}).get("postmarket") or {}
    section = build_featured_section(
        symbol,
        post_excerpt=post.get("post"),
        day=day,
    )
    path = append_featured_research(section, day=day)
    logger.info("Appended featured deep dive for %s to %s", symbol, path)
    return symbol


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Featured mover deep dive research log")
    parser.add_argument("--date", help="Trading day YYYY-MM-DD (default: today ET)")
    parser.add_argument("--ticker", help="Override featured ticker")
    args = parser.parse_args()

    symbol = run_deep_dive(args.date, args.ticker)
    if not symbol:
        return 1
    print(f"Featured deep dive written for {symbol}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
