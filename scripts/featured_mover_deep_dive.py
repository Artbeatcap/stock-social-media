#!/usr/bin/env python3
"""Featured-mover research log: news narrative, intraday move, options flags."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalyst_audit_lib import bootstrap_telemetry_if_missing, load_movers_for_trade_date
from massive_client import (
    get_minute_bars_for_date,
    get_news_for_ticker,
    get_options_chain_snapshot,
    get_snapshot,
    summarize_options_flags,
)
from validation_telemetry import RESEARCH_LOG_DIR, ensure_telemetry_dirs, resolve_audit_trade_date

NY = pytz.timezone("America/New_York")
logger = logging.getLogger(__name__)


def _largest_minute_move(bars: list[dict]) -> tuple[str, float]:
    if len(bars) < 2:
        return ("n/a", 0.0)
    best_time = bars[0]["time_et"]
    best_pct = 0.0
    for prev, cur in zip(bars, bars[1:]):
        prev_close = float(prev.get("close") or 0)
        cur_close = float(cur.get("close") or 0)
        if prev_close <= 0:
            continue
        pct = abs((cur_close - prev_close) / prev_close * 100)
        if pct > best_pct:
            best_pct = pct
            best_time = cur["time_et"]
    return best_time, best_pct


def _news_near_move(
    articles: list[dict],
    move_time_et: str,
    window_minutes: int = 90,
) -> list[str]:
    if move_time_et == "n/a":
        return []
    try:
        move_dt = datetime.strptime(move_time_et, "%Y-%m-%d %H:%M:%S ET")
        move_dt = NY.localize(move_dt)
    except ValueError:
        return []
    hits: list[str] = []
    for item in articles:
        ts = item.get("datetime")
        if not ts:
            continue
        pub = datetime.fromtimestamp(ts, tz=NY)
        delta = abs((pub - move_dt).total_seconds()) / 60
        if delta <= window_minutes:
            hits.append(f"{pub.strftime('%H:%M ET')} — {item.get('headline', '')}")
    return hits[:5]


def build_section(trade_date, ticker: str, post_meta: dict) -> str:
    sym = ticker.upper()
    news = get_news_for_ticker(sym, limit=30)
    week_ago = datetime.now(tz=NY) - timedelta(days=7)
    narrative = [
        f"- {datetime.fromtimestamp(n['datetime'], tz=NY).strftime('%Y-%m-%d %H:%M ET')}: {n.get('headline', '')}"
        for n in news
        if n.get("datetime") and datetime.fromtimestamp(n["datetime"], tz=NY) >= week_ago
    ][:12]

    bars = get_minute_bars_for_date(sym, trade_date)
    move_time, move_pct = _largest_minute_move(bars)
    near_news = _news_near_move(news, move_time)

    quote = get_snapshot(sym)
    spot = float(quote.get("last") or 0)
    chain = get_options_chain_snapshot(sym, limit=80)
    option_flags = summarize_options_flags(chain, spot)

    lines = [
        f"## Featured: ${sym}",
        "",
        f"_Trade date {trade_date.isoformat()} — log generated {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}_",
        "",
    ]
    if post_meta.get("bootstrapped"):
        lines.append("> Telemetry bootstrapped from live market internals (no postmarket snapshot on disk).")
        lines.append("")
    lines.append("### Multi-day narrative (Massive, 7d)")
    if narrative:
        lines.extend(narrative)
    else:
        lines.append("- No ticker-tagged headlines in the last 7 days.")
    lines.append("")
    lines.append("### Intraday price action (1m bars, session)")
    if bars:
        lines.append(f"- Largest 1-minute move: **{move_pct:.2f}%** at **{move_time}**")
        if near_news:
            lines.append("- Headlines within ±90m of that bar:")
            lines.extend([f"  - {line}" for line in near_news])
        else:
            lines.append("- No Massive headlines within ±90m of the largest 1m bar.")
    else:
        lines.append("- Minute aggregates unavailable for this session.")
    lines.append("")
    lines.append("### Options snapshot")
    lines.extend([f"- {flag}" for flag in option_flags])
    lines.append("")
    return "\n".join(lines)


def run_deep_dive(trade_date=None) -> Path:
    ensure_telemetry_dirs()
    trade_date = trade_date or resolve_audit_trade_date()
    _, _, post = load_movers_for_trade_date(trade_date)
    if not post:
        _, _, post = bootstrap_telemetry_if_missing(trade_date)

    ticker = post.get("featured_stock") or (post.get("featured_mover") or {}).get("ticker")
    if not ticker:
        gainers, losers, _ = bootstrap_telemetry_if_missing(trade_date)
        ticker = (gainers[0]["ticker"] if gainers else None) or (losers[0]["ticker"] if losers else None)
    if not ticker:
        raise RuntimeError(f"No featured ticker for {trade_date}")

    section = build_section(trade_date, str(ticker), post)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"
    if log_path.is_file():
        existing = log_path.read_text(encoding="utf-8")
        marker = f"## Featured: ${str(ticker).upper()}"
        if marker in existing:
            before, _, after = existing.partition(marker)
            after = after.split("\n## Featured:", 1)[-1] if "\n## Featured:" in after else ""
            if after.startswith("$"):
                after = ""
            content = before.rstrip() + "\n\n" + section
            if after.strip():
                content += "\n## Featured:" + after
        else:
            content = existing.rstrip() + "\n\n" + section
    else:
        content = f"# Research log — {trade_date.isoformat()}\n\n" + section

    log_path.write_text(content.rstrip() + "\n", encoding="utf-8")
    logger.info("Appended featured deep dive for %s to %s", ticker, log_path)
    return log_path


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Featured mover deep dive research log")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    args = parser.parse_args()
    trade_date = None
    if args.date:
        trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    path = run_deep_dive(trade_date)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
