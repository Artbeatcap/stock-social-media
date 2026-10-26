#!/usr/bin/env python3
"""Daily catalyst recall audit — compare pipeline catalysts to Massive headlines."""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    REPORTS_DIR,
    audit_as_of_for_trade_date,
    evaluate_mover_recall,
    load_movers_for_trade_date,
    resolve_audit_trade_date,
    should_run_weekday,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def build_report(trade_date, force: bool = False) -> Path:
    if not should_run_weekday(force_weekend=force):
        logger.info("Skipping recall audit — weekend")
        return None

    gainers, losers, source = load_movers_for_trade_date(trade_date)
    movers = gainers + losers
    as_of = audit_as_of_for_trade_date(trade_date)

    rows = [evaluate_mover_recall(m, trade_date=trade_date, as_of=as_of) for m in movers]
    hits = sum(1 for r in rows if r.verdict == "HIT")
    total = len(rows)

    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    out_path = REPORTS_DIR / f"catalyst_recall_{trade_date.isoformat()}.md"
    lines = [
        f"# Catalyst recall audit — {trade_date.isoformat()}",
        "",
        f"- Generated: {datetime.now(tz=NY).strftime('%Y-%m-%d %H:%M ET')}",
        f"- Movers source: `{source}`",
        f"- News window end: {as_of.strftime('%Y-%m-%d %H:%M ET')}",
        f"- Summary: **{hits}/{total} hits** (≥35% token overlap vs best Massive headline)",
        "",
    ]
    for row in rows:
        lines.append(row.to_markdown())
        lines.append("")

    out_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    logger.info("Wrote %s (%s/%s hits)", out_path, hits, total)
    return out_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Catalyst recall quality audit")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: resolve_audit_trade_date)")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    trade_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else resolve_audit_trade_date()
    )
    path = build_report(trade_date, force=args.force_weekend)
    if path is None:
        return 0
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
