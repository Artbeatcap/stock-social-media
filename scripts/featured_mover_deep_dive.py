#!/usr/bin/env python3
"""Featured mover deep dive — multi-day news, 1m move timing, options snapshot."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from massive_client import get_minute_aggregates, get_news_for_ticker, get_options_chain_snapshot  # noqa: E402
from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    TELEMETRY_ROOT,
    bootstrap_telemetry_if_missing,
    featured_from_postmarket,
    pick_fallback_featured_mover,
    resolve_audit_trade_date,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("featured_mover_deep_dive")

RTH_OPEN = (9, 30)
RTH_CLOSE = (16, 0)


def _in_rth(ts_ms: int) -> bool:
    dt = datetime.fromtimestamp(ts_ms / 1000, tz=NY)
    open_t = dt.replace(hour=RTH_OPEN[0], minute=RTH_OPEN[1], second=0, microsecond=0)
    close_t = dt.replace(hour=RTH_CLOSE[0], minute=RTH_CLOSE[1], second=0, microsecond=0)
    return open_t <= dt <= close_t


def analyze_minute_bars(bars: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not bars:
        return {"bar_count": 0}

    max_jump: Tuple[float, Dict[str, Any]] = (0.0, {})
    session_open: Optional[float] = None
    max_from_open: Tuple[float, Dict[str, Any]] = (0.0, {})

    for bar in bars:
        o, c = float(bar.get("open") or 0), float(bar.get("close") or 0)
        if o <= 0:
            continue
        jump_pct = (c - o) / o * 100.0
        if abs(jump_pct) > abs(max_jump[0]):
            max_jump = (jump_pct, bar)

        if _in_rth(int(bar.get("ts_ms") or 0)):
            if session_open is None:
                session_open = o
            if session_open and session_open > 0:
                move = (c - session_open) / session_open * 100.0
                if abs(move) > abs(max_from_open[0]):
                    max_from_open = (move, bar)

    out: Dict[str, Any] = {"bar_count": len(bars)}
    if max_jump[1]:
        out["largest_1m_jump_pct"] = max_jump[0]
        out["largest_1m_jump_at"] = max_jump[1].get("et")
    if max_from_open[1]:
        out["max_from_rth_open_pct"] = max_from_open[0]
        out["max_from_rth_open_at"] = max_from_open[1].get("et")
    return out


def summarize_options(contracts: List[Dict[str, Any]]) -> List[str]:
    if not contracts:
        return ["_(no options contracts returned)_"]
    ranked_iv = sorted(contracts, key=lambda c: float(c.get("implied_volatility") or 0), reverse=True)
    ranked_oi = sorted(contracts, key=lambda c: int(c.get("open_interest") or 0), reverse=True)
    lines = []
    if ranked_iv and ranked_iv[0].get("implied_volatility"):
        top = ranked_iv[0]
        lines.append(
            f"- Highest IV: {top.get('implied_volatility'):.2f} on "
            f"{top.get('contract_type')} {top.get('strike')} exp {top.get('expiry')}"
        )
    if ranked_oi and ranked_oi[0].get("open_interest"):
        top = ranked_oi[0]
        lines.append(
            f"- Highest OI: {top.get('open_interest')} on "
            f"{top.get('contract_type')} {top.get('strike')} exp {top.get('expiry')}"
        )
    if len(lines) == 0:
        lines.append("_(contracts returned but IV/OI fields empty)_")
    return lines


def news_last_n_days(ticker: str, days: int = 7, *, limit: int = 25) -> List[Dict[str, Any]]:
    cutoff = datetime.now(NY) - timedelta(days=days)
    items = get_news_for_ticker(ticker, limit=limit)
    filtered = []
    for item in items:
        ts = int(item.get("datetime") or 0)
        if ts >= int(cutoff.timestamp()):
            filtered.append(item)
    return filtered


def run_featured_deep_dive(trade_date=None, *, now: datetime | None = None) -> Path:
    now = now or datetime.now(NY)
    trade_date = trade_date or resolve_audit_trade_date(now)
    gainers, losers, postmarket = bootstrap_telemetry_if_missing(trade_date)

    featured_ticker = featured_from_postmarket(postmarket)
    featured_mover = None
    if featured_ticker:
        for row in gainers + losers:
            if row.get("ticker") == featured_ticker:
                featured_mover = row
                break
    if not featured_mover:
        featured_mover = pick_fallback_featured_mover(gainers, losers)
        featured_ticker = (featured_mover or {}).get("ticker")
        source_note = "postmarket_run.json missing featured_stock — used largest |%| mover"
    else:
        source_note = "featured_stock from postmarket_run.json"

    log_dir = TELEMETRY_ROOT / "research_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{trade_date.isoformat()}.md"

    if not featured_ticker:
        section = "\n".join(
            [
                f"## Featured: _(none)_",
                "",
                f"_No movers available for {trade_date.isoformat()}._",
                "",
            ]
        )
        _append_section(log_path, section, trade_date, now)
        return log_path

    pct = float((featured_mover or {}).get("change_percentage") or 0.0)
    pipeline_catalyst = str((featured_mover or {}).get("catalyst") or "").strip()

    news_items = news_last_n_days(featured_ticker, days=7)
    bars = get_minute_aggregates(featured_ticker, trade_date)
    bar_stats = analyze_minute_bars(bars)
    contracts, options_note = get_options_chain_snapshot(featured_ticker)

    news_lines = []
    if news_items:
        for item in news_items[:12]:
            ts = int(item.get("datetime") or 0)
            when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M ET") if ts else "?"
            news_lines.append(f"- {when}: {item.get('headline', '').strip()}")
    else:
        news_lines.append("- _(no Massive headlines in last 7 days)_")

    bar_lines = [
        f"- 1m bars loaded: {bar_stats.get('bar_count', 0)}",
    ]
    if "largest_1m_jump_pct" in bar_stats:
        bar_lines.append(
            f"- Largest 1m jump: {bar_stats['largest_1m_jump_pct']:+.2f}% at {bar_stats.get('largest_1m_jump_at')}"
        )
    if "max_from_rth_open_pct" in bar_stats:
        bar_lines.append(
            f"- Max from RTH open: {bar_stats['max_from_rth_open_pct']:+.2f}% at {bar_stats.get('max_from_rth_open_at')}"
        )

    options_lines = summarize_options(contracts)
    if options_note:
        options_lines.insert(0, f"- _Note: {options_note}_")

    section = "\n".join(
        [
            f"## Featured: ${featured_ticker}",
            "",
            f"_Generated {now.strftime('%Y-%m-%d %H:%M:%S %Z')} · trade date {trade_date.isoformat()}_",
            f"_{source_note}_",
            "",
            f"- **Move:** {pct:+.2f}%",
            f"- **Pipeline catalyst:** {pipeline_catalyst or '_(empty)_'}",
            "",
            "### 7-day narrative (Massive)",
            *news_lines,
            "",
            "### Intraday timing (1m bars)",
            *bar_lines,
            "",
            "### Options snapshot",
            *options_lines,
            "",
        ]
    )
    _append_section(log_path, section, trade_date, now)
    logger.info("Appended featured deep dive for %s to %s", featured_ticker, log_path)
    return log_path


def _append_section(log_path: Path, section: str, trade_date, now: datetime) -> None:
    if log_path.is_file():
        existing = log_path.read_text(encoding="utf-8")
        if f"## Featured: $" in existing and section.lstrip().startswith("## Featured:"):
            # Replace prior featured block for idempotent re-runs the same day.
            parts = existing.split("\n## Featured:")
            header = parts[0].rstrip()
            log_path.write_text(header + "\n\n" + section.lstrip(), encoding="utf-8")
            return
        log_path.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")
        return

    header = "\n".join(
        [
            f"# Research log — {trade_date.isoformat()}",
            "",
            f"_Started {now.strftime('%Y-%m-%d %H:%M:%S %Z')}_",
            "",
        ]
    )
    log_path.write_text(header + section, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive")
    parser.add_argument("--trade-date", help="YYYY-MM-DD session date")
    args = parser.parse_args()
    trade_date = None
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    run_featured_deep_dive(trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
