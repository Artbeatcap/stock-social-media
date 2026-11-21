#!/usr/bin/env python3
"""Featured mover research log: multi-day news, intraday move timing, options flags."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    TELEMETRY_ROOT,
    bootstrap_telemetry_if_missing,
    featured_ticker_from_postmarket,
    fetch_ticker_news,
    resolve_audit_trade_date,
)

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _find_largest_intraday_move(bars: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if len(bars) < 2:
        return None
    best = None
    best_abs = 0.0
    for idx in range(1, len(bars)):
        prev_close = float(bars[idx - 1].get("close") or 0)
        cur_close = float(bars[idx].get("close") or 0)
        if prev_close <= 0:
            continue
        move_pct = (cur_close - prev_close) / prev_close * 100.0
        if abs(move_pct) > best_abs:
            best_abs = abs(move_pct)
            best = {
                "time_et": bars[idx].get("time_et"),
                "move_pct": move_pct,
                "close": cur_close,
                "volume": bars[idx].get("volume"),
            }
    return best


def _options_flags(chain: Dict[str, Any], *, spot: Optional[float]) -> List[str]:
    flags: List[str] = []
    if not chain.get("ok"):
        err = chain.get("error") or "unavailable"
        flags.append(f"Options snapshot unavailable ({err}). Tier may block `/v3/snapshot/options`.")
        return flags

    contracts = chain.get("contracts") or []
    if not contracts:
        flags.append("Options chain returned zero contracts.")
        return flags

    ivs = [c.get("implied_volatility") or 0 for c in contracts if c.get("implied_volatility")]
    ois = [c.get("open_interest") or 0 for c in contracts]
    vols = [c.get("volume") or 0 for c in contracts]

    if ivs:
        avg_iv = sum(ivs) / len(ivs)
        hot = sorted(contracts, key=lambda c: c.get("implied_volatility") or 0, reverse=True)[:3]
        for row in hot:
            iv = row.get("implied_volatility") or 0
            if iv >= avg_iv * 1.35 and iv > 0:
                flags.append(
                    f"Elevated IV: {row.get('contract_type')} {row.get('strike')} "
                    f"exp {row.get('expiration')} IV={iv:.2f} (avg {avg_iv:.2f})"
                )

    if ois:
        top_oi = max(contracts, key=lambda c: c.get("open_interest") or 0)
        if (top_oi.get("open_interest") or 0) >= max(ois) * 0.5 and top_oi.get("open_interest", 0) > 100:
            flags.append(
                f"OI concentration: {top_oi.get('contract_type')} {top_oi.get('strike')} "
                f"OI={top_oi.get('open_interest')} vol={top_oi.get('volume')}"
            )

    if spot and spot > 0:
        near = sorted(
            contracts,
            key=lambda c: abs((c.get("strike") or 0) - spot),
        )[:5]
        liquid = [c for c in near if (c.get("volume") or 0) > 50]
        for row in liquid:
            flags.append(
                f"Active near-ATM: {row.get('contract_type')} {row.get('strike')} "
                f"vol={row.get('volume')} IV={row.get('implied_volatility')}"
            )

    return flags[:8]


def run_featured_deep_dive(trade_date=None) -> Path:
    from massive_client import get_minute_bars_for_date, get_options_chain_snapshot, get_snapshot

    trade_date = trade_date or resolve_audit_trade_date()
    gainers, losers, source = bootstrap_telemetry_if_missing(trade_date)
    ticker, featured_source = featured_ticker_from_postmarket(trade_date, gainers, losers)

    log_dir = TELEMETRY_ROOT / "research_log"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"{trade_date.isoformat()}.md"

    if not ticker:
        section = f"""## Featured: (none)

_No featured ticker for {trade_date.isoformat()} (source: {source})._
"""
        _append_section(log_path, section)
        return log_path

    now = datetime.now(NY)
    news_start = now - timedelta(days=7)
    news_items = fetch_ticker_news(
        ticker,
        limit=25,
        published_gte=news_start,
        published_lte=now,
    )

    bars = get_minute_bars_for_date(ticker, trade_date)
    move = _find_largest_intraday_move(bars)
    quote = get_snapshot(ticker)
    spot = quote.get("last")

    chain = get_options_chain_snapshot(ticker, limit=80)
    option_notes = _options_flags(chain, spot=spot)

    mover_row = next(
        (m for m in gainers + losers if str(m.get("ticker")).upper() == ticker),
        {},
    )
    pipeline_catalyst = mover_row.get("catalyst") or ""

    lines = [
        f"## Featured: ${ticker}",
        "",
        f"_Trade date {trade_date.isoformat()} · telemetry `{source}` · featured source `{featured_source}`_",
        "",
    ]
    if mover_row:
        lines.append(
            f"- Session move: **{float(mover_row.get('change_percentage') or 0):+.2f}%**"
        )
    if pipeline_catalyst:
        lines.append(f"- Pipeline catalyst: {pipeline_catalyst}")
    lines.append("")

    lines.append("### News (Massive, last 7 days)")
    if not news_items:
        lines.append("_No articles returned._")
    else:
        for item in news_items[:12]:
            ts = item.get("datetime")
            when = datetime.fromtimestamp(ts, tz=NY).strftime("%Y-%m-%d %H:%M") if ts else "?"
            lines.append(f"- **{when}** — {item.get('headline', '')}")
    lines.append("")

    lines.append("### Intraday timing (1m bars, session date)")
    if not bars:
        lines.append("_No minute bars for this session (halt, holiday, or API gap)._")
    elif move:
        lines.append(
            f"Largest 1-minute move: **{move['move_pct']:+.2f}%** at {move['time_et']} "
            f"(close ${move['close']:.2f}, vol {move.get('volume')})"
        )
        if news_items:
            lines.append("")
            lines.append("_Cross-check: compare move timestamp to news timestamps above._")
    lines.append("")

    lines.append("### Options snapshot")
    if option_notes:
        for note in option_notes:
            lines.append(f"- {note}")
    else:
        lines.append("_No unusual IV/OI patterns flagged in returned chain._")
    lines.append("")

    _append_section(log_path, "\n".join(lines) + "\n")
    logger.info("Appended featured section for %s to %s", ticker, log_path)
    return log_path


def _append_section(path: Path, section: str) -> None:
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        header = f"# Research log — {path.stem}\n\n"
        if not existing.startswith("# Research log"):
            existing = header + existing
    else:
        existing = f"# Research log — {path.stem}\n\n"

    marker = section.split("\n", 1)[0].strip()
    if marker and marker in existing:
        before, _, after = existing.partition(marker)
        # Drop prior section with same heading through next ## or EOF
        rest = after
        cut = rest.find("\n## ")
        if cut != -1:
            rest = rest[cut + 1 :]
        else:
            rest = ""
        existing = before.rstrip() + "\n\n"

    path.write_text(existing.rstrip() + "\n\n" + section, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Featured mover deep dive research log")
    parser.add_argument("--trade-date", help="YYYY-MM-DD session date")
    args = parser.parse_args()
    trade_date = None
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    run_featured_deep_dive(trade_date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
