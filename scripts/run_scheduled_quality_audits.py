#!/usr/bin/env python3
"""
Dispatch catalyst quality audits by Eastern Time schedule or explicit task override.

Weekdays:
  - ~5:30pm ET: featured-mover deep dive
  - ~9:15pm ET: catalyst recall audit

Cloud cron (e.g. 13:00 UTC) should set CATALYST_QUALITY_AUDIT_TASK=all.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import NY, et_window_match, is_weekday_et  # noqa: E402
from scripts.catalyst_recall_audit import run_audit as run_recall_audit  # noqa: E402
from scripts.featured_mover_deep_dive import run_deep_dive  # noqa: E402
from validation_telemetry import bootstrap_telemetry_if_missing, effective_audit_trade_date  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def resolve_tasks(now: datetime, explicit: str | None) -> list[str]:
    task = (explicit or os.getenv("CATALYST_QUALITY_AUDIT_TASK") or "").strip().lower()
    if task in ("featured", "recall", "all"):
        return ["featured", "recall"] if task == "all" else [task]

    tasks: list[str] = []
    if et_window_match(now, 17, 30):
        tasks.append("featured")
    if et_window_match(now, 21, 15):
        tasks.append("recall")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument(
        "--date",
        default=None,
        help="Trade date YYYY-MM-DD (default: effective audit trade date in ET)",
    )
    parser.add_argument(
        "--task",
        choices=["featured", "recall", "all"],
        help="Force a task (overrides schedule; also set CATALYST_QUALITY_AUDIT_TASK)",
    )
    parser.add_argument("--force-weekend", action="store_true", help="Run even on Sat/Sun")
    args = parser.parse_args()

    now = datetime.now(NY)
    forced = bool(args.task or os.getenv("CATALYST_QUALITY_AUDIT_TASK"))
    if not args.force_weekend and not forced and not is_weekday_et(now):
        logger.info("Weekend — skipping audits (set --task or CATALYST_QUALITY_AUDIT_TASK to override)")
        return 0

    tasks = resolve_tasks(now, args.task)
    if not tasks:
        logger.info(
            "No audit window matched at %s ET (use --task featured|recall|all)",
            now.strftime("%H:%M"),
        )
        return 0

    trade_date = args.date or effective_audit_trade_date(now)
    bootstrap_telemetry_if_missing(trade_date)

    for task in tasks:
        if task == "featured":
            path = run_deep_dive(trade_date)
            logger.info("Featured deep dive: %s", path)
        elif task == "recall":
            path = run_recall_audit(trade_date)
            logger.info("Catalyst recall: %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
