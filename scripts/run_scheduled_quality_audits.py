#!/usr/bin/env python3
"""
Dispatch catalyst quality audits on weekday schedules (US/Eastern).

Cloud automation cron (13:00 UTC) should set CATALYST_QUALITY_AUDIT_TASK=all
to run both featured + recall in one shot.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import NY, is_us_weekday  # noqa: E402
from scripts.catalyst_recall_audit import run_recall_audit  # noqa: E402
from scripts.featured_mover_deep_dive import run_featured_deep_dive  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

FEATURED_TARGET = time(17, 30)
RECALL_TARGET = time(21, 15)
WINDOW_MINUTES = 45


def _in_window(now: datetime, target: time) -> bool:
    anchor = datetime.combine(now.date(), target)
    if now.tzinfo is not None:
        anchor = NY.localize(anchor.replace(tzinfo=None))
    start = anchor - timedelta(minutes=WINDOW_MINUTES)
    end = anchor + timedelta(minutes=WINDOW_MINUTES)
    return start <= now <= end


def resolve_tasks(now: datetime, *, task_env: str, force_all: bool) -> list[str]:
    task_env = (task_env or "").strip().lower()
    if force_all or task_env == "all":
        return ["featured", "recall"]
    if task_env in {"featured", "recall"}:
        return [task_env]
    tasks: list[str] = []
    if _in_window(now, FEATURED_TARGET):
        tasks.append("featured")
    if _in_window(now, RECALL_TARGET):
        tasks.append("recall")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument(
        "--task",
        choices=["featured", "recall", "all"],
        help="Force a task (overrides schedule windows unless omitted)",
    )
    parser.add_argument("--force-weekend", action="store_true", help="Run even on Sat/Sun")
    args = parser.parse_args()

    now = datetime.now(NY)
    task_env = os.getenv("CATALYST_QUALITY_AUDIT_TASK", "")
    if args.task:
        task_env = args.task

    if not args.force_weekend and not is_us_weekday(now) and task_env not in {"featured", "recall", "all"}:
        logger.info("Weekend — skipping audits (use --force-weekend or set CATALYST_QUALITY_AUDIT_TASK)")
        return 0

    tasks = resolve_tasks(now, task_env=task_env, force_all=task_env == "all")
    if not tasks:
        logger.info(
            "No audit window matched at %s ET (set CATALYST_QUALITY_AUDIT_TASK=all to force)",
            now.strftime("%H:%M"),
        )
        return 0

    if "featured" in tasks:
        path = run_featured_deep_dive()
        logger.info("Featured deep dive → %s", path)
    if "recall" in tasks:
        path = run_recall_audit()
        logger.info("Catalyst recall → %s", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
