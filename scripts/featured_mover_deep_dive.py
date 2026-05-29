#!/usr/bin/env python3
"""
Featured-mover deep dive — weekdays 5:30pm ET (after post-market email).

Pulls multi-day news, intraday minute bars, and options chain data for the
featured stock from today's postmarket telemetry and appends a research log
section to validation_telemetry/research_log/YYYY-MM-DD.md.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from massive_client import (  # noqa: E402
    get_minute_aggregates,
    get_news_for_ticker_window,
    get_options_chain_snapshot,
)
from validation_telemetry import load_featured_stock, load_run_snapshot, research_log_dir  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
RTH_OPEN = time(9, 30)
RTH_CLOSE = time(16, 0)


def _parse_news_timestamp(item: dict[str, Any]) -> Optional[datetime]:
    ts = int(item.get("datetime") or 0)
    if not ts:
        return None
    return datetime.fromtimestamp(ts, tz=NY)


def format_news_timeline(headlines: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    for item in headlines[:15]:
        published = _parse_news_timestamp(item)
        stamp = published.strftime("%Y-%m-%d %H:%M ET") if published else "unknown time"
        sentiment = item.get("sentiment") or "n/a"
        lines.append(f"- [{stamp}] ({sentiment}) {item.get('headline', '')}")
    if not lines:
        lines.append("- No Massive headlines in the last 7 days.")
    return lines


def find_largest_intraday_move(
    bars: list[dict[str, Any]],
) -> tuple[Optional[dict[str, Any]], Optional[float]]:
    if len(bars) < 2:
        return None, None

    best_bar: Optional[dict[str, Any]] = None
    best_change = 0.0
    for prev, curr in zip(bars, bars[1:]):
        prev_close = float(prev.get("close") or 0.0)
        curr_close = float(curr.get("close") or 0.0)
        if prev_close <= 0:
            continue
        change_pct = abs((curr_close - prev_close) / prev_close * 100.0)
        if change_pct > best_change:
            best_change = change_pct
            best_bar = curr
    return best_bar, best_change


def filter_rth_bars(bars: list[dict[str, Any]], trade_date: date) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for bar in bars:
        ts_ms = int(bar.get("t") or 0)
        if not ts_ms:
            continue
        dt = datetime.fromtimestamp(ts_ms / 1000, tz=NY)
        if dt.date() != trade_date:
            continue
        if RTH_OPEN <= dt.time() <= RTH_CLOSE:
            filtered.append(bar)
    return filtered


def cross_reference_news(
    move_bar: Optional[dict[str, Any]],
    headlines: list[dict[str, Any]],
) -> list[str]:
    if not move_bar:
        return ["- Could not identify a dominant 1-minute price move during RTH."]

    move_dt = datetime.fromisoformat(str(move_bar["datetime"]))
    lines = [f"- Largest 1m move at **{move_dt.strftime('%H:%M ET')}** "
               f"(close ${float(move_bar['close']):.2f}, volume {int(move_bar.get('volume') or 0):,})."]

    nearby: list[tuple[float, dict[str, Any]]] = []
    for item in headlines:
        published = _parse_news_timestamp(item)
        if not published:
            continue
        delta_hours = abs((published - move_dt).total_seconds()) / 3600.0
        if delta_hours <= 6.0:
            nearby.append((delta_hours, item))

    if not nearby:
        lines.append("- No Massive headlines within ±6 hours of the largest minute move.")
        return lines

    nearby.sort(key=lambda pair: pair[0])
    lines.append("- Headlines near the price move:")
    for delta_hours, item in nearby[:5]:
        published = _parse_news_timestamp(item)
        stamp = published.strftime("%H:%M ET") if published else "?"
        lines.append(
            f"  - {stamp} ({delta_hours:.1f}h away): {item.get('headline', '')}"
        )
    return lines


def analyze_options_chain(chain: dict[str, Any]) -> list[str]:
    if not chain.get("ok"):
        error = chain.get("error") or "options snapshot unavailable"
        return [f"- Options chain unavailable ({error})."]

    contracts = chain.get("contracts") or []
    if not contracts:
        return ["- Options chain returned no contracts."]

    flagged: list[tuple[float, str]] = []
    for contract in contracts:
        details = contract.get("details") or contract
        greeks = contract.get("greeks") or {}
        day = contract.get("day") or {}
        oi = int(details.get("open_interest") or contract.get("open_interest") or 0)
        iv = float(greeks.get("implied_volatility") or contract.get("implied_volatility") or 0.0)
        strike = float(details.get("strike_price") or contract.get("strike_price") or 0.0)
        ctype = str(details.get("contract_type") or contract.get("contract_type") or "?")
        volume = int(day.get("volume") or contract.get("volume") or 0)

        score = 0.0
        notes: list[str] = []
        if oi >= 5000:
            score += oi / 5000.0
            notes.append(f"OI {oi:,}")
        if volume >= 1000:
            score += volume / 1000.0
            notes.append(f"vol {volume:,}")
        if iv >= 0.8:
            score += iv
            notes.append(f"IV {iv:.0%}")

        if score >= 2.0 and notes:
            flagged.append(
                (
                    score,
                    f"{ctype.upper()} ${strike:.2f}: " + ", ".join(notes),
                )
            )

    if not flagged:
        return ["- No unusual IV/OI spikes detected in returned chain snapshot."]

    flagged.sort(reverse=True, key=lambda pair: pair[0])
    lines = ["- Unusual options activity flags:"]
    for _, text in flagged[:8]:
        lines.append(f"  - {text}")
    return lines


def build_section(
    ticker: str,
    trade_date: str,
    run: Optional[dict[str, Any]],
) -> str:
    trade_day = date.fromisoformat(trade_date)
    headlines = get_news_for_ticker_window(ticker, hours=24 * 7, limit=30)
    bars = get_minute_aggregates(ticker, trade_day, trade_day)
    rth_bars = filter_rth_bars(bars, trade_day)
    move_bar, move_pct = find_largest_intraday_move(rth_bars)
    chain = get_options_chain_snapshot(ticker, limit=100)

    post_excerpt = ""
    if run and run.get("post"):
        post_excerpt = str(run["post"]).strip().replace("\n", " ")[:240]

    lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Generated {datetime.now(NY).isoformat(timespec='seconds')}_",
        "",
    ]
    if post_excerpt:
        lines.extend([f"**Post excerpt:** {post_excerpt}", ""])

    lines.extend(["### 7-day news narrative", ""])
    lines.extend(format_news_timeline(headlines))
    lines.extend(["", "### Intraday move vs news timing", ""])
    lines.extend(cross_reference_news(move_bar, headlines))
    if move_pct is not None:
        lines.append(f"- Minute-to-minute magnitude: **{move_pct:.2f}%**")
    lines.extend(["", "### Options chain flags", ""])
    lines.extend(analyze_options_chain(chain))
    lines.append("")
    return "\n".join(lines)


def append_research_log(section: str, trade_date: str) -> Path:
    log_path = research_log_dir() / f"{trade_date}.md"
    header = f"# Featured Mover Research Log — {trade_date}\n\n"
    if not log_path.exists():
        content = header + section
        if not content.endswith("\n"):
            content += "\n"
        log_path.write_text(content, encoding="utf-8")
    else:
        with log_path.open("a", encoding="utf-8") as handle:
            handle.write(section)
            if not section.endswith("\n"):
                handle.write("\n")
    logger.info("Appended featured mover section to %s", log_path)
    return log_path


def run_deep_dive(trade_date: Optional[str] = None, ticker: Optional[str] = None) -> Path:
    trade_date = trade_date or datetime.now(NY).date().isoformat()
    run = load_run_snapshot("postmarket", trade_date=trade_date)
    featured = (ticker or load_featured_stock(trade_date=trade_date) or "").upper()

    if not featured:
        raise FileNotFoundError(
            f"No featured_stock in postmarket telemetry for {trade_date}. "
            "Run the post-market email first or pass --ticker."
        )

    section = build_section(featured, trade_date, run)
    return append_research_log(section, trade_date)


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive audit")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today ET)")
    parser.add_argument("--ticker", help="Override featured ticker symbol")
    args = parser.parse_args()
    try:
        run_deep_dive(args.date, args.ticker)
        return 0
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1
    except Exception as exc:
        logger.exception("Featured mover deep dive failed: %s", exc)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
