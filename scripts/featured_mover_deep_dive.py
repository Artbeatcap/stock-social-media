#!/usr/bin/env python3
"""
Featured-mover research log: multi-day news, 1m move timing, options snapshot.
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from catalyst_audit_common import (  # noqa: E402
    NY,
    ensure_telemetry_layout,
    load_audit_movers,
    research_log_dir,
    resolve_audit_trade_date,
)
from massive_client import (  # noqa: E402
    get_minute_bars_for_date,
    get_news_for_ticker,
    get_options_chain_snapshot,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _find_largest_intraday_move(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(bars) < 2:
        return None
    best = None
    best_abs = 0.0
    for prev, cur in zip(bars, bars[1:]):
        prev_close = float(prev.get("close") or 0)
        cur_close = float(cur.get("close") or 0)
        if prev_close <= 0:
            continue
        pct = (cur_close - prev_close) / prev_close * 100.0
        if abs(pct) > best_abs:
            best_abs = abs(pct)
            best = {
                "time_et": cur.get("time_et"),
                "pct_change_1m": round(pct, 2),
                "close": cur_close,
            }
    return best


def _news_last_7_days(ticker: str) -> List[Dict[str, Any]]:
    cutoff = datetime.now(tz=NY) - timedelta(days=7)
    items = get_news_for_ticker(ticker, limit=50)
    return [i for i in items if int(i.get("datetime") or 0) >= int(cutoff.timestamp())]


def _format_news_bullets(items: List[Dict[str, Any]], limit: int = 8) -> List[str]:
    lines: List[str] = []
    for item in items[:limit]:
        ts = int(item.get("datetime") or 0)
        when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M ET") if ts else "?"
        headline = (item.get("headline") or "").strip()
        if headline:
            lines.append(f"- {when}: {headline}")
    return lines or ["- (no Massive headlines in the last 7 days)"]


def _options_highlights(chain: Dict[str, Any]) -> List[str]:
    if not chain.get("ok"):
        return ["- Options chain unavailable (often HTTP 403 on lower Massive tiers)."]
    contracts = chain.get("contracts") or []
    if not contracts:
        return ["- Options chain returned no contracts."]

    by_exp: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
    for c in contracts:
        exp = c.get("expiration") or "unknown"
        by_exp[exp].append(c)

    lines: List[str] = []
    top_oi = sorted(contracts, key=lambda c: int(c.get("open_interest") or 0), reverse=True)[:3]
    top_iv = sorted(contracts, key=lambda c: float(c.get("implied_volatility") or 0), reverse=True)[:3]

    if top_oi:
        lines.append("- **Highest OI (snapshot):**")
        for c in top_oi:
            lines.append(
                f"  - {c.get('contract_type')} {c.get('strike')} exp {c.get('expiration')}: "
                f"OI {c.get('open_interest')}, IV {float(c.get('implied_volatility') or 0):.2f}"
            )
    if top_iv:
        lines.append("- **Highest IV (snapshot):**")
        for c in top_iv:
            lines.append(
                f"  - {c.get('contract_type')} {c.get('strike')} exp {c.get('expiration')}: "
                f"IV {float(c.get('implied_volatility') or 0):.2f}, vol {c.get('volume')}"
            )
    return lines


def append_featured_section(trade_date: str | None = None) -> Path:
    ensure_telemetry_layout()
    trade_date = trade_date or resolve_audit_trade_date()
    gainers, losers, meta = load_audit_movers(trade_date)

    pool = gainers + losers
    ticker = str(meta.get("featured_stock") or "").upper()
    featured_row: Dict[str, Any] = {}
    if ticker:
        for row in pool:
            if row.get("ticker") == ticker:
                featured_row = row
                break
    if not ticker and pool:
        featured_row = max(pool, key=lambda r: abs(float(r.get("change_percentage") or 0)))
        ticker = str(featured_row.get("ticker") or "").upper()

    log_path = research_log_dir() / f"{trade_date}.md"
    existing = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

    if not ticker:
        section = f"\n## Featured: (none)\n\nNo movers found for session {trade_date}.\n"
        log_path.write_text(existing + section, encoding="utf-8")
        return log_path

    pct = float(featured_row.get("change_percentage") or 0)
    pipeline_catalyst = (featured_row.get("catalyst") or "").strip()

    news_items = _news_last_7_days(ticker)
    minute_bars = get_minute_bars_for_date(ticker, trade_date)
    largest_1m = _find_largest_intraday_move(minute_bars)
    open_move = None
    if minute_bars:
        first = minute_bars[0]
        open_move = {"time_et": first.get("time_et"), "close": first.get("close")}

    chain = get_options_chain_snapshot(ticker, limit=80)

    section_lines = [
        "",
        f"## Featured: ${ticker}",
        "",
        f"- Session: **{trade_date}** | Move: **{pct:+.2f}%**",
        f"- Pipeline catalyst: {pipeline_catalyst or '(empty)'}",
        f"- Telemetry featured_stock: `{meta.get('featured_stock')}`"
        + (" (fallback: largest |%|)" if not meta.get("featured_stock") else ""),
        "",
        "### Multi-day narrative (Massive, 7d)",
        *_format_news_bullets(news_items),
        "",
        "### Intraday timing (1m bars)",
    ]
    if not minute_bars:
        section_lines.append(f"- No 1m bars returned for {trade_date}.")
    else:
        if open_move:
            section_lines.append(f"- First bar: {open_move['time_et']} close ${float(open_move['close']):.2f}")
        if largest_1m:
            section_lines.append(
                f"- Largest 1m step: **{largest_1m['pct_change_1m']:+.2f}%** at {largest_1m['time_et']}"
            )
        section_lines.append(f"- Bars loaded: {len(minute_bars)}")

    section_lines.extend(["", "### Options chain (snapshot)"])
    section_lines.extend(_options_highlights(chain))
    section_lines.append("")

    marker = f"## Featured: ${ticker}"
    if marker in existing:
        before, _, after = existing.partition(marker)
        # Drop prior section for same ticker until next ## heading
        rest = after
        if "\n## " in rest:
            rest = rest.split("\n## ", 1)[1]
            rest = "## " + rest if rest else ""
        else:
            rest = ""
        log_path.write_text(before.rstrip() + "\n" + "\n".join(section_lines) + rest, encoding="utf-8")
    else:
        header = f"# Research log — {trade_date}\n\n" if not existing.strip() else ""
        log_path.write_text(existing.rstrip() + "\n" + header + "\n".join(section_lines), encoding="utf-8")

    logger.info("Appended featured section for %s → %s", ticker, log_path)
    return log_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep-dive research log")
    parser.add_argument("--trade-date", help="Session date YYYY-MM-DD")
    args = parser.parse_args()
    try:
        append_featured_section(args.trade_date)
        return 0
    except Exception as exc:
        logger.exception("Featured deep dive failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
