#!/usr/bin/env python3
"""
Run catalyst quality audits when their systemd timers fire (or on demand).

Weekdays:
  - featured: 5:30 PM ET — featured mover deep dive (after post-market email)
  - recall:   9:15 PM ET — catalyst recall audit

Usage:
  python scripts/run_scheduled_quality_audits.py              # run audits for current ET window
  python scripts/run_scheduled_quality_audits.py --task all   # run both (requires telemetry)
  python scripts/run_scheduled_quality_audits.py --task recall --date 2026-08-04
"""

from __future__ import annotations

import argparse
import importlib.util
import logging
import sys
from datetime import datetime
from pathlib import Path

import pytz

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")


def _load_script_module(filename: str):
    path = REPO_ROOT / "scripts" / filename
    name = filename.replace(".py", "")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


catalyst_recall_audit = _load_script_module("catalyst_recall_audit.py")
featured_mover_deep_dive = _load_script_module("featured_mover_deep_dive.py")
run_audit = catalyst_recall_audit.run_audit
run_deep_dive = featured_mover_deep_dive.run_deep_dive

FEATURED_HOUR = 17
FEATURED_MINUTE = 30
RECALL_HOUR = 21
RECALL_MINUTE = 15
WINDOW_MINUTES = 45


def _in_window(now: datetime, hour: int, minute: int) -> bool:
    start = hour * 60 + minute
    current = now.hour * 60 + now.minute
    return start <= current < start + WINDOW_MINUTES


def _tasks_for_now(now: datetime, force: str | None) -> list[str]:
    if force == "all":
        return ["featured", "recall"]
    if force in ("featured", "recall"):
        return [force]

    tasks: list[str] = []
    if _in_window(now, FEATURED_HOUR, FEATURED_MINUTE):
        tasks.append("featured")
    if _in_window(now, RECALL_HOUR, RECALL_MINUTE):
        tasks.append("recall")
    return tasks


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument("--date", help="Trade date YYYY-MM-DD (default: today ET)")
    parser.add_argument(
        "--task",
        choices=["featured", "recall", "all"],
        help="Force a specific audit instead of matching the current ET schedule window",
    )
    args = parser.parse_args()

    now = datetime.now(NY)
    if now.weekday() >= 5:
        logger.info("Weekend — skipping catalyst quality audits")
        return 0

    tasks = _tasks_for_now(now, args.task)
    if not tasks:
        logger.info(
            "No audit scheduled for %s ET (featured at 17:30, recall at 21:15)",
            now.strftime("%H:%M"),
        )
        return 0

    trade_date = args.date or now.date().isoformat()
    exit_code = 0

    if "featured" in tasks:
        try:
            path = run_deep_dive(trade_date)
            logger.info("Featured mover deep dive complete: %s", path)
        except Exception as exc:
            logger.error("Featured mover deep dive failed: %s", exc)
            exit_code = 1

    if "recall" in tasks:
        try:
            path = run_audit(trade_date)
            logger.info("Catalyst recall audit complete: %s", path)
        except Exception as exc:
            logger.error("Catalyst recall audit failed: %s", exc)
            exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
