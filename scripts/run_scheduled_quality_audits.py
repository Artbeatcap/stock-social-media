#!/usr/bin/env python3
"""
Dispatch catalyst quality audits by Eastern Time window or explicit task.

Environment:
  CATALYST_QUALITY_AUDIT_TASK = featured | recall | all
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.catalyst_audit_common import ny_now, resolve_audit_trade_date, should_skip_weekend  # noqa: E402
from scripts.catalyst_recall_audit import build_report as run_recall  # noqa: E402
from scripts.featured_mover_deep_dive import append_featured_section as run_featured  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

WINDOW_MINUTES = 45


def _in_window(now: datetime, hour: int, minute: int) -> bool:
    start = datetime.combine(now.date(), time(hour, minute), tzinfo=now.tzinfo)
    end = start.timestamp() + WINDOW_MINUTES * 60
    return start.timestamp() <= now.timestamp() <= end


def tasks_for_now(now: datetime, explicit: str | None) -> list[str]:
    task = (explicit or os.getenv("CATALYST_QUALITY_AUDIT_TASK") or "").strip().lower()
    if task == "all":
        return ["featured", "recall"]
    if task in {"featured", "recall"}:
        return [task]
    out: list[str] = []
    if _in_window(now, 17, 30):
        out.append("featured")
    if _in_window(now, 21, 15):
        out.append("recall")
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Run scheduled catalyst quality audits")
    parser.add_argument("--task", choices=["featured", "recall", "all"], help="Override task selection")
    parser.add_argument("--trade-date", help="YYYY-MM-DD")
    parser.add_argument("--force-weekend", action="store_true")
    args = parser.parse_args()

    now = ny_now()
    if should_skip_weekend(now, force=args.force_weekend) and not (
        args.task or os.getenv("CATALYST_QUALITY_AUDIT_TASK")
    ):
        logger.info("Weekend with no explicit task — nothing to run")
        return 0

    if args.trade_date:
        from datetime import date

        trade_date = date.fromisoformat(args.trade_date)
    else:
        trade_date = resolve_audit_trade_date(now)

    tasks = tasks_for_now(now, args.task)
    if not tasks:
        logger.info("No audit window matched at %s — set --task or CATALYST_QUALITY_AUDIT_TASK", now.isoformat())
        return 0

    logger.info("Running tasks %s for trade_date=%s", tasks, trade_date)
    if "featured" in tasks:
        run_featured(trade_date, force_weekend=args.force_weekend)
    if "recall" in tasks:
        run_recall(trade_date, force_weekend=args.force_weekend)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
