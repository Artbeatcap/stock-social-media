#!/usr/bin/env python3
"""Dispatch catalyst quality audits (featured deep dive + recall)."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
for path in (ROOT, SCRIPTS):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from validation_telemetry import (
    FEATURED_AUDIT_ET,
    RECALL_AUDIT_ET,
    in_audit_window,
    is_us_market_weekday,
    resolve_audit_trade_date,
)

logger = logging.getLogger(__name__)


def _should_run(task: str, force: bool) -> bool:
    if force:
        return True
    if task == "featured":
        return in_audit_window(FEATURED_AUDIT_ET)
    if task == "recall":
        return in_audit_window(RECALL_AUDIT_ET)
    return False


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", choices=("featured", "recall", "all"), default=None)
    parser.add_argument("--force", action="store_true", help="Ignore ET schedule windows")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    args = parser.parse_args()

    task = args.task or os.getenv("CATALYST_QUALITY_AUDIT_TASK", "").strip().lower() or None
    if not task:
        logger.error("No task specified; set --task or CATALYST_QUALITY_AUDIT_TASK")
        return 2

    trade_date = resolve_audit_trade_date()
    if args.date:
        trade_date = datetime.strptime(args.date, "%Y-%m-%d").date()

    forced = args.force or task == "all"
    if not forced and not is_us_market_weekday(datetime.now().date()):
        logger.info("Weekend — skipping audits (use --force or set task env to run)")
        return 0

    ran = False
    if task in ("featured", "all") and _should_run("featured", forced):
        from featured_mover_deep_dive import run_deep_dive

        run_deep_dive(trade_date)
        ran = True
    elif task in ("featured", "all"):
        logger.info("Outside featured window (5:30pm ET); skipped")

    if task in ("recall", "all") and _should_run("recall", forced):
        from catalyst_recall_audit import run_audit

        run_audit(trade_date)
        ran = True
    elif task in ("recall", "all"):
        logger.info("Outside recall window (9:15pm ET); skipped")

    if not ran:
        logger.warning("No audits executed for task=%s (schedule window)", task)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
