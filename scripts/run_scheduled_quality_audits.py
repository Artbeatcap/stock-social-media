#!/usr/bin/env python3
"""
Dispatch catalyst quality audits on VPS timers or cloud cron.

Env:
  CATALYST_QUALITY_AUDIT_TASK=recall|featured|all
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import pytz

NY = pytz.timezone("America/New_York")

FEATURED_WINDOW = (time(17, 0), time(17, 45))
RECALL_WINDOW = (time(21, 15), time(22, 0))

logger = logging.getLogger(__name__)


def _in_window(now: datetime, start: time, end: time) -> bool:
    t = now.timetz() if hasattr(now, "timetz") else now.time()
    return start <= t <= end


def tasks_for_now(now: datetime, forced: str | None) -> list[str]:
    if forced in ("all", "recall", "featured"):
        if forced == "all":
            return ["featured", "recall"]
        return [forced]

    if now.weekday() >= 5:
        return []

    tasks: list[str] = []
    if _in_window(now, *FEATURED_WINDOW):
        tasks.append("featured")
    if _in_window(now, *RECALL_WINDOW):
        tasks.append("recall")
    return tasks


def main() -> int:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument(
        "--task",
        choices=("recall", "featured", "all"),
        help="Force a task (skips weekday/time windows)",
    )
    args = parser.parse_args()

    forced = args.task or os.environ.get("CATALYST_QUALITY_AUDIT_TASK", "").strip().lower() or None
    now = datetime.now(NY)
    tasks = tasks_for_now(now, forced)

    if not tasks:
        logger.info("No audit tasks due at %s (forced=%s)", now.isoformat(), forced)
        return 0

    scripts_dir = Path(__file__).resolve().parent
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))

    from catalyst_recall_audit import run_audit as run_recall
    from featured_mover_deep_dive import run_deep_dive

    if "featured" in tasks:
        logger.info("Running featured mover deep dive")
        run_deep_dive()
    if "recall" in tasks:
        logger.info("Running catalyst recall audit")
        run_recall()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
