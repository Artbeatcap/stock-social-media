#!/usr/bin/env python3
"""
Dispatch catalyst quality audits based on US/Eastern schedule or explicit task.

Env:
  CATALYST_QUALITY_AUDIT_TASK — featured | recall | all
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

from scripts.catalyst_audit_common import (  # noqa: E402
    NY,
    in_scheduled_window,
    resolve_audit_trade_date,
    should_run_weekday,
)
from scripts.catalyst_recall_audit import build_report as run_recall  # noqa: E402
from scripts.featured_mover_deep_dive import append_research_log as run_featured  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


def pick_tasks(now: datetime, explicit: str | None) -> list[str]:
    task = (explicit or os.getenv("CATALYST_QUALITY_AUDIT_TASK") or "").strip().lower()
    if task == "all":
        return ["featured", "recall"]
    if task in ("featured", "recall"):
        return [task]

    tasks: list[str] = []
    if in_scheduled_window(now, 17, 30):
        tasks.append("featured")
    if in_scheduled_window(now, 21, 15):
        tasks.append("recall")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument("--task", choices=["featured", "recall", "all"], help="Override schedule")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    now = datetime.now(tz=NY)
    if not should_run_weekday(now, force_weekend=args.force_weekend):
        logger.info("Weekday skip — no audits")
        return 0

    trade_date = (
        datetime.strptime(args.date, "%Y-%m-%d").date()
        if args.date
        else resolve_audit_trade_date(now)
    )
    tasks = pick_tasks(now, args.task)
    if not tasks:
        logger.info("Outside featured (5:30pm) and recall (9:15pm) windows — nothing to run")
        return 0

    logger.info("Running tasks %s for trade_date=%s", tasks, trade_date)
    if "featured" in tasks:
        run_featured(trade_date, force=args.force_weekend)
    if "recall" in tasks:
        run_recall(trade_date, force=args.force_weekend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
