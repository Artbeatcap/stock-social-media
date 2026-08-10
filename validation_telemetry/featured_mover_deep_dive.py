#!/usr/bin/env python3
"""
Featured-mover deep dive — weekdays ~5:30pm ET (after post-market email).

Pulls 7-day news, today's 1m price bars, and options chain for the featured
stock from the sent post-market telemetry. Appends a ## Featured: $TICKER section
to validation_telemetry/research_log/YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pytz

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from massive_client import (  # noqa: E402
    flag_unusual_options,
    get_minute_bars,
    get_news_for_ticker_since,
    get_options_chain_snapshot,
)
from validation_telemetry.store import (  # noqa: E402
    load_run_snapshot,
    research_log_dir,
    session_date_et,
)

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


def _find_largest_move_bar(bars: list[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if not bars:
        return None
    return max(bars, key=lambda bar: abs(float(bar.get("pct_change") or 0)))


def _news_near_move(
    news_items: list[dict[str, Any]],
    move_ts: int,
    window_minutes: int = 60,
) -> list[dict[str, Any]]:
    window_sec = window_minutes * 60
    nearby: list[dict[str, Any]] = []
    for item in news_items:
        published = int(item.get("datetime") or 0)
        if not published:
            continue
        delta = abs(published - move_ts)
        if delta <= window_sec:
            nearby.append({**item, "minutes_from_move": round(delta / 60.0, 1)})
    nearby.sort(key=lambda row: row.get("minutes_from_move", 999))
    return nearby


def _format_news_timeline(news_items: list[dict[str, Any]], limit: int = 10) -> list[str]:
    lines: list[str] = []
    for item in news_items[:limit]:
        ts = int(item.get("datetime") or 0)
        when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M ET") if ts else "unknown"
        headline = item.get("headline") or item.get("title") or "(no title)"
        sentiment = item.get("sentiment") or "unknown"
        lines.append(f"- **{when}** [{sentiment}] {headline}")
    return lines


def run_deep_dive(trade_date: str | None = None) -> Path:
    session = trade_date or session_date_et()
    telemetry = load_run_snapshot("postmarket", trade_date=session)
    if not telemetry:
        raise FileNotFoundError(
            f"No postmarket telemetry for {session}. Run twitter_auto_emailer --time postmarket first."
        )

    ticker = (telemetry.get("featured_stock") or "").upper()
    if not ticker:
        featured_mover = telemetry.get("featured_mover") or {}
        ticker = str(featured_mover.get("ticker") or "").upper()
    if not ticker:
        raise ValueError(f"No featured_stock in telemetry for {session}")

    now = datetime.now(tz=NY)
    seven_days_ago = int((now - timedelta(days=7)).timestamp())
    news_7d = get_news_for_ticker_since(ticker, since_ts=seven_days_ago, limit=50)
    news_24h = [
        n for n in news_7d
        if int(n.get("datetime") or 0) >= int((now - timedelta(hours=24)).timestamp())
    ]

    bars = get_minute_bars(ticker, session)
    move_bar = _find_largest_move_bar(bars)
    move_ts = int(move_bar["timestamp_ms"] / 1000) if move_bar else 0
    nearby_news = _news_near_move(news_24h or news_7d, move_ts) if move_ts else []

    options = get_options_chain_snapshot(ticker, limit=80)
    unusual = flag_unusual_options(options.get("contracts") or []) if options.get("ok") else []

    section_lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Deep dive generated {now.strftime('%Y-%m-%d %H:%M %Z')}_",
        "",
        "### Post context",
        "",
        f"- Featured in post-market email: **{ticker}**",
        f"- Post style: {telemetry.get('style') or 'unknown'}",
        f"- Pipeline catalyst (if any): {(telemetry.get('featured_mover') or {}).get('catalyst') or '(none)'}",
        "",
        "### 7-day news narrative",
        "",
    ]
    if news_7d:
        section_lines.extend(_format_news_timeline(news_7d))
    else:
        section_lines.append("- No Massive headlines in the last 7 days.")

    section_lines.extend(["", "### Intraday price move (1m bars, today)", ""])
    if move_bar:
        section_lines.extend(
            [
                f"- Largest 1m bar: **{move_bar['timestamp']}**",
                f"- Move: {move_bar['pct_change']:+.2f}% (O {move_bar['open']:.2f} → C {move_bar['close']:.2f})",
                f"- Volume: {move_bar.get('volume', 0):,}",
            ]
        )
        if nearby_news:
            section_lines.append("- Headlines within ±60m of that bar:")
            for item in nearby_news[:5]:
                lines_headline = item.get("headline") or item.get("title")
                section_lines.append(
                    f"  - {item.get('minutes_from_move')}m away: {lines_headline}"
                )
        else:
            section_lines.append("- No headlines within ±60m of the largest 1m bar.")
    else:
        section_lines.append("- No 1m bars returned for today (holiday, halts, or API tier).")

    section_lines.extend(["", "### Options chain flags", ""])
    if not options.get("ok"):
        section_lines.append(
            f"- Options data unavailable: {options.get('error', 'entitlement or empty')}"
        )
    elif unusual:
        for contract in unusual:
            ctype = contract.get("contract_type", "?")
            strike = contract.get("strike")
            exp = contract.get("expiration")
            flags = "; ".join(contract.get("flags") or [])
            section_lines.append(f"- **{ctype} ${strike} {exp}** — {flags}")
    else:
        section_lines.append("- No unusual IV/OI spikes detected in returned chain snapshot.")

    section_lines.extend(["", "### Catalyst phrasing notes", ""])
    if news_7d and move_bar and nearby_news:
        section_lines.append(
            "Price action aligns with nearby headlines — tomorrow's post can reference the multi-day "
            "narrative above rather than a single truncated headline."
        )
    elif news_7d and not nearby_news:
        section_lines.append(
            "News exists but not tightly timed to the intraday spike — consider softer catalyst language "
            "('amid ongoing…') instead of implying a single breaking headline caused the move."
        )
    else:
        section_lines.append(
            "Thin news coverage — lean on price/volume context and sector internals rather than inventing a catalyst."
        )

    log_path = research_log_dir() / f"{session}.md"
    section_text = "\n".join(section_lines)
    if log_path.exists():
        existing = log_path.read_text(encoding="utf-8").rstrip()
        marker = f"## Featured: ${ticker}"
        if marker in existing:
            parts = existing.split(marker)
            existing = parts[0].rstrip()
        content = existing + "\n\n" + section_text + "\n"
    else:
        header = [
            f"# Research Log — {session}",
            "",
            f"_Started {now.strftime('%Y-%m-%d %H:%M %Z')}_",
            "",
        ]
        content = "\n".join(header) + section_text + "\n"

    log_path.write_text(content, encoding="utf-8")
    logger.info("Appended featured deep dive for %s to %s", ticker, log_path)
    return log_path


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Featured-mover deep dive research log")
    parser.add_argument("--date", help="Session date YYYY-MM-DD (default: today ET)")
    args = parser.parse_args()
    path = run_deep_dive(args.date)
    print(f"Research log updated: {path}")


if __name__ == "__main__":
    main()
