#!/usr/bin/env python3
"""Featured-mover deep dive — multi-day news, intraday move timing, options flags."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from massive_client import (  # noqa: E402
    get_minute_bars_for_session,
    get_options_chain_snapshot,
)
from scripts.catalyst_audit_common import (  # noqa: E402
    FEATURED_RECALL_WINDOW,
    NY,
    RESEARCH_LOG_DIR,
    audit_as_of_for_trade_date,
    load_featured_ticker,
    load_movers_for_trade_date,
    news_in_window,
    resolve_audit_trade_date,
    should_run_weekday,
    telemetry_day_dir,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def _largest_minute_move(bars: List[Dict[str, Any]]) -> Optional[Tuple[str, float, float]]:
    if len(bars) < 2:
        return None
    best_ts = None
    best_pct = 0.0
    best_close = 0.0
    prev_close = bars[0].get("close") or bars[0].get("open")
    for bar in bars[1:]:
        close = bar.get("close")
        if not close or not prev_close:
            prev_close = close
            continue
        pct = (close - prev_close) / prev_close * 100.0
        if abs(pct) > abs(best_pct):
            best_pct = pct
            best_ts = bar.get("timestamp")
            best_close = close
        prev_close = close
    if best_ts is None:
        return None
    et = datetime.fromisoformat(best_ts).astimezone(NY)
    return et.strftime("%H:%M ET"), best_pct, best_close


def _nearest_headline(news: List[Dict[str, Any]], move_et: datetime, window_minutes: int = 90) -> Optional[str]:
    best = None
    best_delta = None
    for item in news:
        published = item.get("published_dt")
        if not isinstance(published, datetime):
            continue
        delta = abs((published - move_et).total_seconds())
        if delta <= window_minutes * 60 and (best_delta is None or delta < best_delta):
            best_delta = delta
            best = str(item.get("headline") or "")
    return best


def _options_flags(chain: Dict[str, Any], spot: float) -> List[str]:
    if not chain.get("available"):
        note = chain.get("note") or "options snapshot unavailable (API tier or symbol)"
        return [note]
    contracts = chain.get("contracts") or []
    if not contracts:
        return ["no contracts returned"]

    by_oi = sorted(contracts, key=lambda c: c.get("open_interest") or 0, reverse=True)
    top_oi = by_oi[:3]
    high_iv = sorted(
        [c for c in contracts if (c.get("implied_volatility") or 0) > 0],
        key=lambda c: c.get("implied_volatility") or 0,
        reverse=True,
    )[:3]

    flags: List[str] = []
    for c in top_oi:
        if (c.get("open_interest") or 0) >= 5000:
            flags.append(
                f"OI buildup: {c.get('contract_type')} {c.get('strike')} exp {c.get('expiry')} "
                f"(OI {c.get('open_interest'):,}, vol {c.get('volume'):,})"
            )
    for c in high_iv:
        iv = c.get("implied_volatility") or 0
        if iv >= 1.0:
            flags.append(
                f"Elevated IV: {c.get('contract_type')} {c.get('strike')} IV {iv:.2f} "
                f"(exp {c.get('expiry')})"
            )
    if spot > 0:
        atm = sorted(
            contracts,
            key=lambda c: abs((c.get("strike") or 0) - spot),
        )[:1]
        if atm and (atm[0].get("volume") or 0) >= 1000:
            c = atm[0]
            flags.append(
                f"ATM activity: {c.get('contract_type')} {c.get('strike')} vol {c.get('volume'):,}"
            )
    return flags or ["no unusual IV/OI patterns flagged"]


def _pipeline_catalyst_for(ticker: str, trade_date) -> str:
    gainers, losers, _ = load_movers_for_trade_date(trade_date)
    for row in gainers + losers:
        if row.get("ticker") == ticker:
            return str(row.get("catalyst") or "")
    return ""


def build_section(trade_date, ticker: Optional[str] = None) -> str:
    ticker = (ticker or load_featured_ticker(trade_date) or "").upper()
    if not ticker:
        return f"## Featured: (none)\n\nNo featured_stock in telemetry for {trade_date.isoformat()}.\n"

    as_of = audit_as_of_for_trade_date(trade_date)
    window_start = as_of - FEATURED_RECALL_WINDOW
    news = news_in_window(ticker, window_start=window_start, as_of=as_of, limit=40)
    bars = get_minute_bars_for_session(ticker, trade_date)
    move = _largest_minute_move(bars)
    spot = move[2] if move else 0.0
    chain = get_options_chain_snapshot(ticker)

    lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Trade date {trade_date.isoformat()} · generated {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}_",
        "",
        f"**Pipeline catalyst (movers list):** {_pipeline_catalyst_for(ticker, trade_date) or '(none)'}",
        "",
        "### 7-day narrative (Massive)",
    ]
    if news:
        for item in news[:12]:
            pub = item.get("published_dt")
            stamp = pub.strftime("%Y-%m-%d %H:%M ET") if isinstance(pub, datetime) else "?"
            lines.append(f"- {stamp} — {item.get('headline')}")
    else:
        lines.append("- (no headlines in lookback window)")

    lines.extend(["", "### Intraday move (1m bars, session)"])
    if move:
        move_time, move_pct, close = move
        lines.append(f"- Largest 1m bar: **{move_time}** ({move_pct:+.2f}% → ${close:.2f})")
        move_dt = datetime.strptime(move_time.replace(" ET", ""), "%H:%M").replace(
            year=trade_date.year,
            month=trade_date.month,
            day=trade_date.day,
            tzinfo=NY,
        )
        near = _nearest_headline(news, move_dt)
        if near:
            lines.append(f"- Nearest headline (±90m): {near}")
    else:
        lines.append("- (minute bars unavailable)")

    lines.extend(["", "### Options snapshot"])
    for flag in _options_flags(chain, spot):
        lines.append(f"- {flag}")

    lines.append("")
    return "\n".join(lines)


def append_research_log(trade_date, ticker: Optional[str] = None, force: bool = False) -> Path:
    if not should_run_weekday(force_weekend=force):
        logger.info("Skipping featured deep dive — weekend")
        return None

    RESEARCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESEARCH_LOG_DIR / f"{trade_date.isoformat()}.md"
    section = build_section(trade_date, ticker=ticker)

    header = f"# Research log — {trade_date.isoformat()}\n\n"
    if log_path.is_file():
        existing = log_path.read_text(encoding="utf-8")
        if f"## Featured: ${(ticker or load_featured_ticker(trade_date) or '').upper()}" in existing:
            logger.info("Featured section already present in %s", log_path)
            return log_path
        body = existing.rstrip() + "\n\n" + section
    else:
        body = header + section

    log_path.write_text(body, encoding="utf-8")
    logger.info("Updated %s", log_path)
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    parser.add_argument("--ticker", help="Override featured ticker")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    trade_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else resolve_audit_trade_date()
    )
    path = append_research_log(trade_date, ticker=args.ticker, force=args.force_weekend)
    if path:
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
