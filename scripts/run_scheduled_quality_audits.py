#!/usr/bin/env python3
"""
Dispatch catalyst quality audits (featured deep dive + catalyst recall).

Production systemd timers target 5:30pm ET and 9:15pm ET on weekdays. Cloud
automation may set CATALYST_QUALITY_AUDIT_TASK=all to run both in one job.
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import time, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.catalyst_audit_common import is_weekday, now_ny, resolve_audit_trade_date  # noqa: E402
from scripts.catalyst_recall_audit import build_report as build_recall_report  # noqa: E402
from scripts.featured_mover_deep_dive import append_research_section  # noqa: E402

FEATURED_WINDOW = time(17, 30)
RECALL_WINDOW = time(21, 15)
WINDOW_MINUTES = 45


def in_window(target: time) -> bool:
    now = now_ny()
    if not is_weekday(now.date()):
        return False
    start = (now.replace(hour=target.hour, minute=target.minute, second=0, microsecond=0))
    end = start + timedelta(minutes=WINDOW_MINUTES)
    return start <= now <= end


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--task",
        choices=("auto", "featured", "recall", "all"),
        default=os.getenv("CATALYST_QUALITY_AUDIT_TASK", "auto"),
    )
    parser.add_argument("--force-weekend", action="store_true")
    parser.add_argument("--force", action="store_true", help="Overwrite existing outputs")
    args = parser.parse_args()

    now = now_ny()
    if not is_weekday(now.date()) and not args.force_weekend and args.task == "auto":
        print("Weekend — skipping (use --force-weekend or set task explicitly)")
        return 0

    trade_date = resolve_audit_trade_date()
    task = args.task
    if task == "auto":
        do_featured = in_window(FEATURED_WINDOW)
        do_recall = in_window(RECALL_WINDOW)
        if not do_featured and not do_recall:
            print("Outside audit windows; nothing to do (set --task all to force)")
            return 0
    elif task == "all":
        do_featured = do_recall = True
    elif task == "featured":
        do_featured, do_recall = True, False
    else:
        do_featured, do_recall = False, True

    if do_featured:
        path = append_research_section(trade_date, force=args.force)
        print(f"Featured deep dive → {path}")
    if do_recall:
        path = build_recall_report(trade_date, force=args.force)
        print(f"Catalyst recall → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
