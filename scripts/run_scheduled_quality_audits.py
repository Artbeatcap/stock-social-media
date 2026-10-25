#!/usr/bin/env python3
"""
Dispatch catalyst quality audits by US/Eastern time window or explicit task.

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

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = Path(__file__).resolve().parent
for p in (str(ROOT), str(SCRIPTS)):
    if p not in sys.path:
        sys.path.insert(0, p)

from catalyst_audit_common import NY, resolve_audit_trade_date, should_skip_weekend
from catalyst_recall_audit import run_recall_audit
from featured_mover_deep_dive import run_featured_deep_dive

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FEATURED_ET = (17, 30)
RECALL_ET = (21, 15)
WINDOW_MINUTES = 45


def _in_window(now: datetime, target_hour: int, target_minute: int) -> bool:
    start = now.replace(hour=target_hour, minute=target_minute, second=0, microsecond=0)
    end = start.timestamp() + WINDOW_MINUTES * 60
    return start.timestamp() <= now.timestamp() < end


def tasks_for_now(now: datetime, explicit: str | None) -> list[str]:
    task = (explicit or os.getenv("CATALYST_QUALITY_AUDIT_TASK") or "").strip().lower()
    if task == "all":
        return ["featured", "recall"]
    if task in {"featured", "recall"}:
        return [task]

    out: list[str] = []
    if _in_window(now, *FEATURED_ET):
        out.append("featured")
    if _in_window(now, *RECALL_ET):
        out.append("recall")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument(
        "--task",
        choices=["featured", "recall", "all"],
        help="Override auto window detection",
    )
    parser.add_argument("--trade-date", help="YYYY-MM-DD")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    now = datetime.now(tz=NY)
    if should_skip_weekend(now) and not args.force_weekend and not (
        args.task or os.getenv("CATALYST_QUALITY_AUDIT_TASK")
    ):
        logger.info("Weekend with no task override — exiting")
        return 0

    tasks = tasks_for_now(now, args.task)
    if not tasks:
        logger.info("No audit task due at %s ET", now.strftime("%H:%M"))
        return 0

    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()
    else:
        trade_date = resolve_audit_trade_date(now)

    logger.info("Running tasks %s for trade date %s", tasks, trade_date)

    status = 0
    if "featured" in tasks:
        if not run_featured_deep_dive(trade_date):
            status = 1
    if "recall" in tasks:
        run_recall_audit(trade_date, force=args.force_weekend)

    return status


if __name__ == "__main__":
    raise SystemExit(main())
