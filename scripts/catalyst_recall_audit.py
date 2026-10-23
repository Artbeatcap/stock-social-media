#!/usr/bin/env python3
"""Compare pipeline catalyst strings to Massive headlines (24h recall audit)."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from catalyst_audit_lib import (
    bootstrap_telemetry_if_missing,
    is_recall_hit,
    load_movers_for_trade_date,
    rank_headlines,
    recall_window_for_trade_date,
)
from massive_client import get_news_for_ticker
from validation_telemetry import REPORTS_DIR, ensure_telemetry_dirs, resolve_audit_trade_date

import pytz

NY = pytz.timezone("America/New_York")
logger = logging.getLogger(__name__)


def _audit_mover(mover: dict, side: str, trade_date) -> dict:
    ticker = str(mover.get("ticker") or "").upper()
    pct = float(mover.get("change_percentage") or 0)
    pipeline = str(mover.get("catalyst") or "").strip()
    want_positive = side == "gainer"

    window_start, window_end = recall_window_for_trade_date(trade_date)
    raw_news = get_news_for_ticker(ticker, limit=25)
    articles = [
        {
            "headline": item.get("headline"),
            "published_utc": datetime.fromtimestamp(item["datetime"], tz=pytz.UTC).isoformat()
            if item.get("datetime")
            else "",
        }
        for item in raw_news
    ]
    top5 = rank_headlines(articles, window_start, window_end, want_positive, limit=5)
    best = top5[0]["title"] if top5 else ""
    hit = is_recall_hit(pipeline, best, [row["title"] for row in top5])
    return {
        "ticker": ticker,
        "side": side,
        "pct": pct,
        "pipeline_catalyst": pipeline or "(none)",
        "massive_best": best or "(no headlines in 24h window)",
        "top5": top5,
        "result": "hit" if hit else "miss",
    }


def build_report(trade_date, rows: list[dict]) -> str:
    hits = sum(1 for r in rows if r["result"] == "hit")
    lines = [
        f"# Catalyst recall audit — {trade_date.isoformat()}",
        "",
        f"Generated: {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}",
        f"Summary: **{hits}/{len(rows)} hits** (token overlap ≥ 35% vs top Massive headline)",
        "",
        "| Ticker | Move | Pipeline catalyst | Massive (best) | Result |",
        "| --- | ---: | --- | --- | --- |",
    ]
    for row in rows:
        lines.append(
            f"| {row['ticker']} | {row['pct']:+.2f}% | {row['pipeline_catalyst'][:80]} | "
            f"{row['massive_best'][:80]} | **{row['result']}** |"
        )
    lines.append("")
    lines.append("## Detail")
    for row in rows:
        lines.append(f"### {row['ticker']} ({row['pct']:+.2f}%) — {row['result']}")
        lines.append(f"- **Pipeline:** {row['pipeline_catalyst']}")
        lines.append(f"- **Massive best:** {row['massive_best']}")
        if row["top5"]:
            lines.append("- **Top scored headlines (24h):**")
            for item in row["top5"]:
                lines.append(f"  - ({item['score']:.2f}) {item['title']}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def run_audit(trade_date=None) -> Path:
    ensure_telemetry_dirs()
    trade_date = trade_date or resolve_audit_trade_date()
    gainers, losers, _ = load_movers_for_trade_date(trade_date)
    if not gainers and not losers:
        gainers, losers, _ = bootstrap_telemetry_if_missing(trade_date)

    rows: list[dict] = []
    for mover in gainers:
        if mover.get("ticker"):
            rows.append(_audit_mover(mover, "gainer", trade_date))
    for mover in losers:
        if mover.get("ticker"):
            rows.append(_audit_mover(mover, "loser", trade_date))

    report = build_report(trade_date, rows)
    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    out_path.write_text(report, encoding="utf-8")
    logger.info("Wrote %s (%d movers, %d hits)", out_path, len(rows), sum(1 for r in rows if r["result"] == "hit"))
    return out_path


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Catalyst recall audit vs Massive headlines")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: resolved session)")
    args = parser.parse_args()
    trade_date = None
    if args.date:
        trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()
    path = run_audit(trade_date)
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
