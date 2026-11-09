#!/usr/bin/env python3
"""
Compare pipeline catalyst strings against Massive headlines (24h window).
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from catalyst_audit_common import (  # noqa: E402
    NY,
    audit_as_of_for_trade_date,
    catalyst_recall_hit,
    ensure_telemetry_layout,
    load_audit_movers,
    news_window_start,
    pick_best_headline,
    reports_dir,
    resolve_audit_trade_date,
)
from massive_client import get_news_for_ticker_since  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def run_recall_audit(trade_date: str | None = None) -> Path:
    ensure_telemetry_layout()
    now = datetime.now(tz=NY)
    trade_date = trade_date or resolve_audit_trade_date(now)
    as_of = audit_as_of_for_trade_date(trade_date, now)
    window_start = news_window_start(as_of)

    gainers, losers, meta = load_audit_movers(trade_date)
    rows = gainers + losers

    lines = [
        f"# Catalyst recall audit — {trade_date}",
        "",
        f"- Generated: {now.strftime('%Y-%m-%d %H:%M ET')}",
        f"- News window: {window_start.strftime('%Y-%m-%d %H:%M ET')} → {as_of.strftime('%Y-%m-%d %H:%M ET')}",
        f"- Telemetry source: `{meta.get('source', 'production snapshot')}`",
        f"- Hit rule: ≥35% token overlap vs top-5 Massive headlines",
        "",
        "| Ticker | Move | Pipeline catalyst | Massive (best of 5) | Overlap | Verdict |",
        "| --- | ---: | --- | --- | ---: | --- |",
    ]

    hits = 0
    evaluated = 0

    for row in rows:
        ticker = str(row.get("ticker") or "").upper()
        if not ticker:
            continue
        pct = float(row.get("change_percentage") or 0.0)
        pipeline = (row.get("catalyst") or "").strip()

        headlines = get_news_for_ticker_since(ticker, window_start, limit=25)
        # Keep client-side cap at five for scoring display
        top5 = headlines[:5]
        best, _ = pick_best_headline(top5, window_start, as_of, pct)
        massive_headline = (best or {}).get("headline") or "(no headlines in window)"

        is_hit, overlap, _ = catalyst_recall_hit(pipeline, top5)
        evaluated += 1
        if is_hit:
            hits += 1

        if not pipeline:
            verdict = "miss (empty catalyst)"
        elif not top5:
            verdict = "miss (no news)"
        elif is_hit:
            verdict = "hit"
        else:
            verdict = "miss"

        def esc(text: str) -> str:
            return (text or "").replace("|", "\\|").replace("\n", " ")

        lines.append(
            f"| {ticker} | {pct:+.2f}% | {esc(pipeline) or '—'} | {esc(massive_headline)} | "
            f"{overlap:.0%} | {verdict} |"
        )

    rate = (hits / evaluated * 100.0) if evaluated else 0.0
    lines.extend(
        [
            "",
            f"**Summary:** {hits}/{evaluated} hits ({rate:.0f}%)",
        ]
    )

    out_path = reports_dir() / f"catalyst_recall_{trade_date}.md"
    out_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Wrote %s (%s/%s hits)", out_path, hits, evaluated)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive headlines")
    parser.add_argument("--trade-date", help="Session date YYYY-MM-DD (default: resolve_audit_trade_date)")
    args = parser.parse_args()
    try:
        run_recall_audit(args.trade_date)
        return 0
    except Exception as exc:
        logger.exception("Recall audit failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
