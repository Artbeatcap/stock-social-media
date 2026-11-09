#!/usr/bin/env python3
"""
Dispatch catalyst quality audits based on ET schedule or explicit task override.

Env:
  CATALYST_QUALITY_AUDIT_TASK=featured|recall|all

Cloud cron (`0 13 * * *` UTC) should set ``all`` to run both checks against the
prior US session when telemetry was not written locally.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
if str(REPO_ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

from catalyst_audit_common import FEATURED_AUDIT_TIME, NY, RECALL_AUDIT_TIME, resolve_audit_trade_date  # noqa: E402
from catalyst_recall_audit import run_recall_audit  # noqa: E402
from featured_mover_deep_dive import append_featured_section  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WINDOW = timedelta(minutes=45)


def _in_window(now: datetime, target: time) -> bool:
    start = (datetime.combine(now.date(), target, tzinfo=NY) - WINDOW / 2).astimezone(NY)
    end = (datetime.combine(now.date(), target, tzinfo=NY) + WINDOW / 2).astimezone(NY)
    return start <= now <= end


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument(
        "--task",
        choices=("featured", "recall", "all"),
        default=os.getenv("CATALYST_QUALITY_AUDIT_TASK", "").lower() or None,
        help="Force a task (default: detect schedule or env CATALYST_QUALITY_AUDIT_TASK)",
    )
    parser.add_argument("--trade-date", help="Override session date YYYY-MM-DD")
    parser.add_argument("--force-weekend", action="store_true", help="Allow runs on Sat/Sun")
    args = parser.parse_args()

    now = datetime.now(tz=NY)
    env_task = os.getenv("CATALYST_QUALITY_AUDIT_TASK", "").lower()
    forced_task = (args.task or env_task or "").lower()
    if now.weekday() >= 5 and not args.force_weekend and forced_task != "all":
        logger.info("Weekend — skipping (pass --force-weekend or --task all)")
        return 0

    task = args.task or (env_task if env_task in ("featured", "recall", "all") else None)
    if not task:
        tasks: list[str] = []
        if _in_window(now, FEATURED_AUDIT_TIME):
            tasks.append("featured")
        if _in_window(now, RECALL_AUDIT_TIME):
            tasks.append("recall")
        if not tasks:
            logger.info("Outside audit windows; nothing to do")
            return 0
        run_plan = tasks
    elif task == "all":
        run_plan = ["featured", "recall"]
    else:
        run_plan = [task]

    trade_date = args.trade_date or resolve_audit_trade_date(now)
    logger.info("Running %s for trade_date=%s", run_plan, trade_date)

    status = 0
    if "featured" in run_plan:
        append_featured_section(trade_date)
    if "recall" in run_plan:
        try:
            run_recall_audit(trade_date)
        except Exception as exc:
            logger.error("Recall audit error: %s", exc)
            status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
