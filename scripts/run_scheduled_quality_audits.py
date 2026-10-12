#!/usr/bin/env python3
"""
Dispatch catalyst quality audits by ET schedule or explicit task override.

Weekday windows (America/New_York):
  - featured: 17:30 ± 45 min
  - recall:   21:15 ± 45 min

Env:
  CATALYST_QUALITY_AUDIT_TASK = featured | recall | all
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.audit_common import effective_audit_trade_date, is_weekday, now_ny  # noqa: E402
from scripts.catalyst_recall_audit import run_recall_audit  # noqa: E402
from scripts.featured_mover_deep_dive import append_featured_section  # noqa: E402

WINDOW_MINUTES = 45


def _in_window(target_hm: tuple[int, int], dt: datetime) -> bool:
    target = dt.replace(hour=target_hm[0], minute=target_hm[1], second=0, microsecond=0)
    delta_min = abs((dt - target).total_seconds()) / 60.0
    return delta_min <= WINDOW_MINUTES


def tasks_for_now(explicit: str | None) -> list[str]:
    if explicit:
        task = explicit.strip().lower()
        if task == "all":
            return ["featured", "recall"]
        if task in ("featured", "recall"):
            return [task]
        raise ValueError(f"Unknown task: {explicit}")

    dt = now_ny()
    if not is_weekday(dt):
        return []

    out: list[str] = []
    if _in_window((17, 30), dt):
        out.append("featured")
    if _in_window((21, 15), dt):
        out.append("recall")
    return out


def main() -> int:
    explicit = os.getenv("CATALYST_QUALITY_AUDIT_TASK")
    if len(sys.argv) > 1 and sys.argv[1] == "--task" and len(sys.argv) > 2:
        explicit = sys.argv[2]

    trade_date = effective_audit_trade_date()
    tasks = tasks_for_now(explicit)
    if not tasks:
        print("No audit tasks due for current ET window (set CATALYST_QUALITY_AUDIT_TASK=all to force).")
        return 0

    for task in tasks:
        if task == "featured":
            path = append_featured_section(trade_date)
            print(f"featured -> {path}")
        elif task == "recall":
            path = run_recall_audit(trade_date)
            print(f"recall -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
