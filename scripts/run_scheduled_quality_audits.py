#!/usr/bin/env python3
"""
Dispatch catalyst quality audits by ET schedule or explicit task override.

Production VPS timers:
  - catalyst-featured-deep-dive.timer  → weekdays 17:30 ET
  - catalyst-recall-audit.timer        → weekdays 21:15 ET

Cloud automation cron (13:00 UTC) sets CATALYST_QUALITY_AUDIT_TASK=all.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.catalyst_audit_common import FEATURED_AUDIT_ET, NY, RECALL_AUDIT_ET  # noqa: E402
from scripts.catalyst_recall_audit import run_recall_audit  # noqa: E402
from scripts.featured_mover_deep_dive import run_featured_deep_dive  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("run_scheduled_quality_audits")

WINDOW = timedelta(minutes=45)


def _in_window(now: datetime, target: time) -> bool:
    start = NY.localize(datetime.combine(now.date(), target))
    end = start + WINDOW
    return start <= now < end


def should_run_weekday(now: datetime, *, force_weekend: bool) -> bool:
    if force_weekend:
        return True
    if os.getenv("CATALYST_QUALITY_AUDIT_TASK"):
        return True
    return now.weekday() < 5


def resolve_tasks(now: datetime, explicit: str | None) -> set[str]:
    task = (explicit or os.getenv("CATALYST_QUALITY_AUDIT_TASK") or "").strip().lower()
    if task in {"all", "both"}:
        return {"featured", "recall"}
    if task in {"featured", "recall"}:
        return {task}
    tasks: set[str] = set()
    if _in_window(now, FEATURED_AUDIT_ET):
        tasks.add("featured")
    if _in_window(now, RECALL_AUDIT_ET):
        tasks.add("recall")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument("--task", choices=["featured", "recall", "all"], help="Force a task (overrides time window)")
    parser.add_argument("--force-weekend", action="store_true", help="Run even on Sat/Sun")
    args = parser.parse_args()

    now = datetime.now(NY)
    if not should_run_weekday(now, force_weekend=args.force_weekend):
        logger.info("Weekend — skipping (set CATALYST_QUALITY_AUDIT_TASK or --force-weekend to override)")
        return 0

    tasks = resolve_tasks(now, args.task)
    if not tasks:
        logger.info(
            "No audit window active at %s ET (featured %s, recall %s)",
            now.strftime("%H:%M"),
            FEATURED_AUDIT_ET.strftime("%H:%M"),
            RECALL_AUDIT_ET.strftime("%H:%M"),
        )
        return 0

    if "featured" in tasks:
        logger.info("Running featured mover deep dive")
        run_featured_deep_dive(now=now)
    if "recall" in tasks:
        logger.info("Running catalyst recall audit")
        run_recall_audit(now=now)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
