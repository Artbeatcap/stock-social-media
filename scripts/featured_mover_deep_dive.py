#!/usr/bin/env python3
"""
Featured-mover deep dive — multi-day news, intraday move timing, options snapshot.
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    RESEARCH_LOG_DIR,
    bootstrap_telemetry_if_missing,
    load_postmarket_run,
    parse_news_items,
)
from validation_telemetry import effective_audit_trade_date  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def _format_news_bullets(items: List[Dict[str, Any]], max_items: int = 12) -> List[str]:
    lines: List[str] = []
    for item in items[:max_items]:
        when = ""
        if item.get("published_dt"):
            when = item["published_dt"].strftime("%Y-%m-%d %H:%M ET")
        lines.append(f"- **{when}** — {item.get('headline', '')}")
    if not lines:
        lines.append("- _(no Massive headlines in lookback)_")
    return lines


def _analyze_intraday_move(ticker: str, trade_date: str) -> List[str]:
    from massive_client import get_intraday_minute_bars

    bars = get_intraday_minute_bars(ticker, trade_date)
    if not bars:
        return ["- _(no 1m bars for session — check API tier or holiday)_"]

    max_bar = max(bars, key=lambda b: abs(b.get("pct_change_from_prev", 0)))
    max_abs = max(bars, key=lambda b: abs(b.get("return_from_open", 0)))

    lines = [
        f"- Session bars: **{len(bars)}** one-minute candles",
        f"- Largest 1m jump: **{max_bar.get('pct_change_from_prev', 0):+.2f}%** at "
        f"{max_bar.get('time_et', 'n/a')} (close ${max_bar.get('close', 0):.2f})",
        f"- Max move from open: **{max_abs.get('return_from_open', 0):+.2f}%** at "
        f"{max_abs.get('time_et', 'n/a')}",
    ]
    return lines


def _analyze_options(ticker: str) -> List[str]:
    from massive_client import get_options_chain_snapshot

    result = get_options_chain_snapshot(ticker, limit=80)
    if result.get("error"):
        return [
            f"- Options snapshot unavailable: {result['error']}",
            "- _(Massive/Polygon options may require a higher tier — verify entitlements.)_",
        ]

    contracts = result.get("contracts") or []
    if not contracts:
        return ["- _(empty options chain snapshot)_"]

    by_oi = sorted(contracts, key=lambda c: c.get("open_interest", 0) or 0, reverse=True)[:5]
    by_iv = sorted(contracts, key=lambda c: c.get("implied_volatility", 0) or 0, reverse=True)[:5]

    lines = [f"- Contracts sampled: **{len(contracts)}**"]
    lines.append("- Top open interest:")
    for c in by_oi:
        lines.append(
            f"  - {c.get('contract_type', '?').upper()} ${c.get('strike', 0):.2f} "
            f"exp {c.get('expiration', '?')}: OI **{c.get('open_interest', 0):,}**, "
            f"IV {c.get('implied_volatility', 0):.1%}"
        )
    lines.append("- Highest implied volatility (snapshot):")
    for c in by_iv:
        lines.append(
            f"  - {c.get('contract_type', '?').upper()} ${c.get('strike', 0):.2f} "
            f"exp {c.get('expiration', '?')}: IV **{c.get('implied_volatility', 0):.1%}**, "
            f"OI {c.get('open_interest', 0):,}"
        )

    flagged = [
        c for c in contracts
        if (c.get("open_interest") or 0) >= 5000 and (c.get("implied_volatility") or 0) >= 0.8
    ]
    if flagged:
        lines.append("- **Flags:** elevated OI (≥5k) with IV ≥80%:")
        for c in flagged[:5]:
            lines.append(
                f"  - {c.get('contract_type', '?').upper()} ${c.get('strike', 0):.2f} "
                f"(OI {c.get('open_interest', 0):,}, IV {c.get('implied_volatility', 0):.1%})"
            )
    return lines


def build_section(ticker: str, trade_date: str, featured_from_post: Optional[str]) -> str:
    from massive_client import get_news_for_ticker

    raw_7d = get_news_for_ticker(ticker, limit=40)
    parsed = parse_news_items(
        [
            {
                "title": i.get("headline"),
                "published_utc": datetime.fromtimestamp(i["datetime"], tz=NY).isoformat()
                if isinstance(i.get("datetime"), int)
                else None,
                "sentiment": i.get("sentiment"),
                "url": i.get("url"),
            }
            for i in raw_7d
        ]
    )
    from datetime import timedelta

    cutoff = datetime.now(NY) - timedelta(days=7)
    news_7d = [n for n in parsed if n.get("published_dt") and n["published_dt"] >= cutoff]

    lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Research log entry for {trade_date} — generated {datetime.now(NY).strftime('%Y-%m-%d %H:%M ET')}_",
        "",
    ]
    if featured_from_post:
        lines.append(f"Post-market email featured **{featured_from_post}**.")
        lines.append("")

    lines.append("### 7-day narrative (Massive headlines)")
    lines.extend(_format_news_bullets(news_7d))
    lines.append("")
    lines.append("### Intraday move (1m bars, today)")
    lines.extend(_analyze_intraday_move(ticker, trade_date))
    lines.append("")
    lines.append("### Options chain snapshot")
    lines.extend(_analyze_options(ticker))
    lines.append("")
    return "\n".join(lines)


def run_deep_dive(trade_date: str) -> Path:
    bootstrap_telemetry_if_missing(trade_date)
    post = load_postmarket_run(trade_date) or {}
    ticker = (post.get("featured_stock") or "").strip().upper()

    if not ticker:
        gainers_path = REPO_ROOT / "validation_telemetry" / trade_date / "top_gainers.json"
        if gainers_path.is_file():
            import json

            gainers = json.loads(gainers_path.read_text(encoding="utf-8"))
            if gainers:
                ticker = str(gainers[0].get("ticker") or "").upper()

    RESEARCH_LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = RESEARCH_LOG_DIR / f"{trade_date}.md"

    if not ticker:
        section = (
            f"## Featured: (none)\n\n_No featured_stock in postmarket_run.json for {trade_date}._\n"
        )
    else:
        section = build_section(ticker, trade_date, post.get("featured_stock"))

    header = f"# Daily research log — {trade_date}\n\n"
    if log_path.is_file():
        existing = log_path.read_text(encoding="utf-8")
        if f"## Featured: ${ticker}" in existing and ticker:
            logger.info("Section for %s already present in %s", ticker, log_path)
            return log_path
        log_path.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")
    else:
        log_path.write_text(header + section, encoding="utf-8")

    logger.info("Appended featured deep dive to %s", log_path)
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive research log")
    parser.add_argument(
        "--date",
        default=None,
        help="Trade date YYYY-MM-DD (default: effective audit trade date in ET)",
    )
    args = parser.parse_args()
    run_deep_dive(args.date or effective_audit_trade_date())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
