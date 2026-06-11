#!/usr/bin/env python3
"""
Featured-mover deep dive — weekdays 5:30pm ET.

After the post-market email, enrich the featured ticker with multi-day news,
intraday price timing, and options-chain context for tomorrow's phrasing.
"""

from __future__ import annotations

import argparse
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import pytz

from massive_client import get_news_for_ticker, get_options_chain, get_stock_aggregates
from validation_telemetry import RESEARCH_DIR, ensure_dirs, load_telemetry, today_iso

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")


def _parse_ts(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(NY)
    except ValueError:
        return None


def find_largest_intraday_move(bars: List[Dict[str, Any]]) -> Tuple[Optional[Dict[str, Any]], float]:
    if len(bars) < 2:
        return None, 0.0
    best_bar = None
    best_move = 0.0
    for prev, curr in zip(bars, bars[1:]):
        prev_close = float(prev.get("c") or prev.get("o") or 0)
        curr_close = float(curr.get("c") or 0)
        if not prev_close:
            continue
        move_pct = abs((curr_close - prev_close) / prev_close * 100.0)
        if move_pct > best_move:
            best_move = move_pct
            best_bar = curr
    return best_bar, best_move


def news_near_timestamp(
    headlines: List[Dict[str, Any]],
    target: Optional[datetime],
    window_minutes: int = 60,
) -> List[Dict[str, Any]]:
    if not target:
        return []
    matches = []
    for item in headlines:
        published = _parse_ts(str(item.get("published_utc") or ""))
        if not published:
            ts = int(item.get("datetime") or 0)
            published = datetime.fromtimestamp(ts, tz=NY) if ts else None
        if not published:
            continue
        delta = abs((published - target).total_seconds()) / 60.0
        if delta <= window_minutes:
            matches.append({**item, "minutes_from_move": round(delta, 1)})
    matches.sort(key=lambda row: row.get("minutes_from_move", 999))
    return matches


def summarize_options(contracts: List[Dict[str, Any]]) -> List[str]:
    if not contracts:
        return ["No options contracts returned."]
    by_oi = sorted(contracts, key=lambda row: row.get("open_interest", 0), reverse=True)
    by_iv = sorted(contracts, key=lambda row: row.get("implied_volatility", 0), reverse=True)
    by_vol = sorted(contracts, key=lambda row: row.get("volume", 0), reverse=True)

    lines = []
    top_oi = by_oi[0]
    lines.append(
        f"Highest OI: {top_oi.get('contract_type', '?').upper()} "
        f"${top_oi.get('strike', 0):.2f} exp {top_oi.get('expiration', '?')} "
        f"(OI {top_oi.get('open_interest', 0):,})"
    )
    top_iv = by_iv[0]
    if top_iv.get("implied_volatility"):
        lines.append(
            f"Highest IV: {top_iv.get('contract_type', '?').upper()} "
            f"${top_iv.get('strike', 0):.2f} IV {top_iv.get('implied_volatility', 0):.1%}"
        )
    unusual = [row for row in by_vol[:5] if row.get("volume", 0) >= 500]
    if unusual:
        sample = unusual[0]
        lines.append(
            f"Active volume: {sample.get('contract_type', '?').upper()} "
            f"${sample.get('strike', 0):.2f} ({sample.get('volume', 0):,} contracts)"
        )
    return lines


def build_section(ticker: str, session_date: str, telemetry: Dict[str, Any]) -> str:
    postmarket = (telemetry.get("posts") or {}).get("postmarket", {})
    featured = postmarket.get("featured_stock") or ticker
    ticker = str(featured).upper()

    headlines = get_news_for_ticker(ticker, limit=50)
    bars = get_stock_aggregates(
        ticker,
        multiplier=1,
        timespan="minute",
        from_date=session_date,
        to_date=session_date,
    )
    move_bar, move_pct = find_largest_intraday_move(bars)
    move_time = None
    if move_bar:
        move_time = _parse_ts(str(move_bar.get("timestamp") or ""))
        if not move_time and move_bar.get("t"):
            move_time = datetime.fromtimestamp(int(move_bar["t"]) / 1000, tz=NY)

    nearby_news = news_near_timestamp(headlines, move_time)
    options = get_options_chain(ticker, limit=50)

    lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Session {session_date}, generated {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}_",
        "",
        "### Pipeline context",
        f"- Post-market featured stock: **{ticker}**",
    ]
    if postmarket.get("post"):
        snippet = str(postmarket["post"]).replace("\n", " ")[:220]
        lines.append(f"- Tweet snippet: {snippet}")
    catalyst = ""
    for bucket in ("top_gainers", "top_losers"):
        for mover in telemetry.get(bucket, []):
            if str(mover.get("ticker", "")).upper() == ticker:
                catalyst = str(mover.get("catalyst") or "")
                break
        if catalyst:
            break
    if catalyst:
        lines.append(f"- Pipeline catalyst: {catalyst}")

    lines.extend(["", "### 7-day news narrative (Massive)"])
    if not headlines:
        lines.append("- No recent headlines returned.")
    else:
        for idx, item in enumerate(headlines[:12], start=1):
            published = item.get("published_utc") or ""
            if not published and item.get("datetime"):
                published = datetime.fromtimestamp(int(item["datetime"]), tz=NY).isoformat()
            lines.append(
                f"{idx}. [{published[:10]}] ({item.get('sentiment') or 'n/a'}) {item.get('headline', '')}"
            )

    lines.extend(["", "### Intraday move timing (1m bars)"])
    if not bars:
        lines.append("- No intraday bars available for today.")
    elif move_bar and move_time:
        lines.append(
            f"- Largest 1-minute move: **{move_pct:.2f}%** at {move_time.strftime('%H:%M ET')} "
            f"(close ${float(move_bar.get('c', 0)):.2f})"
        )
        if nearby_news:
            lines.append("- Headlines within ±60 minutes of that move:")
            for item in nearby_news[:3]:
                lines.append(
                    f"  - ({item['minutes_from_move']}m) {item.get('headline', '')}"
                )
        else:
            lines.append("- No Massive headlines landed within 60 minutes of the largest 1m bar.")
    else:
        lines.append("- Could not identify a meaningful 1-minute inflection point.")

    lines.extend(["", "### Options chain"])
    if not options.get("ok"):
        lines.append(
            "- Options snapshot unavailable (likely Massive tier limitation / 403). "
            "Upgrade plan for IV/OI cross-checks."
        )
        if options.get("error"):
            lines.append(f"- API message: {options['error']}")
    else:
        for bullet in summarize_options(options.get("contracts", [])):
            lines.append(f"- {bullet}")

    lines.append("")
    return "\n".join(lines)


def append_research_log(section: str, session_date: str) -> str:
    ensure_dirs()
    path = RESEARCH_DIR / f"{session_date}.md"
    if path.exists():
        existing = path.read_text(encoding="utf-8")
        if f"## Featured: ${section.split('## Featured: $', 1)[1].split()[0]}" in existing:
            logger.info("Featured section already present in %s; skipping duplicate append", path)
            return str(path)
        content = existing.rstrip() + "\n\n" + section
    else:
        header = f"# Research Log — {session_date}\n\n"
        content = header + section
    path.write_text(content, encoding="utf-8")
    return str(path)


def main() -> int:
    parser = argparse.ArgumentParser(description="Append featured-mover deep dive to the daily research log.")
    parser.add_argument("--date", default=today_iso(), help="Session date (YYYY-MM-DD)")
    parser.add_argument("--ticker", default="", help="Override featured ticker")
    args = parser.parse_args()

    telemetry = load_telemetry(args.date)
    ticker = args.ticker.upper() if args.ticker else ""
    if not ticker:
        ticker = str((telemetry.get("posts") or {}).get("postmarket", {}).get("featured_stock") or "")
    if not ticker:
        logger.error(
            "No featured_stock in telemetry for %s. Run post-market email first or pass --ticker.",
            args.date,
        )
        return 1

    section = build_section(ticker, args.date, telemetry)
    path = append_research_log(section, args.date)
    logger.info("Appended featured deep dive to %s", path)
    print(section)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
