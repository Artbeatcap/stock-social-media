#!/usr/bin/env python3
"""Dispatch catalyst quality audits by ET schedule or explicit task override."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, time as dt_time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import ET, is_us_weekday, resolve_audit_trade_date  # noqa: E402
from scripts.catalyst_recall_audit import run_recall_audit  # noqa: E402
from scripts.featured_mover_deep_dive import run_featured_deep_dive  # noqa: E402

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FEATURED_TIME = dt_time(17, 30)
RECALL_TIME = dt_time(21, 15)
WINDOW_MINUTES = 45


def _in_window(now: datetime, target: dt_time) -> bool:
    anchor = ET.localize(datetime.combine(now.date(), target))
    start = anchor - timedelta(minutes=WINDOW_MINUTES)
    end = anchor + timedelta(minutes=WINDOW_MINUTES)
    return start <= now <= end


def tasks_for_now(now: datetime, explicit: str | None) -> set[str]:
    if explicit:
        task = explicit.strip().lower()
        if task == "all":
            return {"featured", "recall"}
        if task in ("featured", "recall"):
            return {task}
        raise ValueError(f"Unknown task {explicit!r}; use featured, recall, or all")

    tasks: set[str] = set()
    if _in_window(now, FEATURED_TIME):
        tasks.add("featured")
    if _in_window(now, RECALL_TIME):
        tasks.add("recall")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument(
        "--task",
        choices=["featured", "recall", "all"],
        help="Override schedule (default: env CATALYST_QUALITY_AUDIT_TASK or time window)",
    )
    parser.add_argument("--trade-date", help="YYYY-MM-DD session override")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    explicit = args.task or os.getenv("CATALYST_QUALITY_AUDIT_TASK")
    now = datetime.now(tz=ET)
    trade_date = resolve_audit_trade_date(now)
    if args.trade_date:
        trade_date = datetime.strptime(args.trade_date, "%Y-%m-%d").date()

    if not args.force_weekend and not is_us_weekday(trade_date):
        logger.info("Skipping audits for non-weekday trade date %s", trade_date)
        return 0

    try:
        tasks = tasks_for_now(now, explicit)
    except ValueError as exc:
        logger.error("%s", exc)
        return 2

    if not tasks:
        logger.info(
            "No audit tasks due at %s ET (set --task or CATALYST_QUALITY_AUDIT_TASK=all)",
            now.strftime("%H:%M"),
        )
        return 0

    if "featured" in tasks:
        run_featured_deep_dive(trade_date=trade_date)
    if "recall" in tasks:
        run_recall_audit(trade_date=trade_date)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
