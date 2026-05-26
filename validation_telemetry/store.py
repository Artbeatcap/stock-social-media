"""
Read/write daily production telemetry used by catalyst quality audits.

Layout (under repo root by default):
  validation_telemetry/YYYY-MM-DD.json
  validation_telemetry/reports/catalyst_recall_YYYY-MM-DD.md
  validation_telemetry/research/YYYY-MM-DD.md
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

NY = pytz.timezone("America/New_York")


def default_telemetry_dir() -> Path:
    env = os.getenv("VALIDATION_TELEMETRY_DIR")
    if env:
        return Path(env).expanduser().resolve()
    return Path(__file__).resolve().parent


def _day_path(day: str, root: Optional[Path] = None) -> Path:
    return (root or default_telemetry_dir()) / f"{day}.json"


def _empty_record(day: str) -> dict[str, Any]:
    return {
        "date": day,
        "updated_at": None,
        "top_gainers": [],
        "top_losers": [],
        "posts": {},
    }


def load_daily_telemetry(day: Optional[str] = None, root: Optional[Path] = None) -> dict[str, Any]:
    """Load telemetry for ``day`` (YYYY-MM-DD, default today ET)."""
    day = day or datetime.now(tz=NY).date().isoformat()
    path = _day_path(day, root)
    if not path.exists():
        return _empty_record(day)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("date", day)
            data.setdefault("top_gainers", [])
            data.setdefault("top_losers", [])
            data.setdefault("posts", {})
            return data
    except (json.JSONDecodeError, OSError):
        pass
    return _empty_record(day)


def save_daily_telemetry(record: dict[str, Any], root: Optional[Path] = None) -> Path:
    root = root or default_telemetry_dir()
    root.mkdir(parents=True, exist_ok=True)
    day = record.get("date") or datetime.now(tz=NY).date().isoformat()
    record["date"] = day
    record["updated_at"] = datetime.now(tz=NY).isoformat()
    path = _day_path(day, root)
    path.write_text(json.dumps(record, indent=2, default=str), encoding="utf-8")
    return path


def merge_movers(
    context: dict[str, Any],
    day: Optional[str] = None,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Persist top gainers/losers (with catalyst fields) from market context."""
    day = day or datetime.now(tz=NY).date().isoformat()
    record = load_daily_telemetry(day, root)
    record["top_gainers"] = list(context.get("top_gainers") or [])
    record["top_losers"] = list(context.get("top_losers") or [])
    if context.get("featured_mover"):
        record["featured_mover"] = context["featured_mover"]
    save_daily_telemetry(record, root)
    return record


def record_sent_post(
    time_period: str,
    post_data: dict[str, Any],
    context: Optional[dict[str, Any]] = None,
    day: Optional[str] = None,
    root: Optional[Path] = None,
) -> dict[str, Any]:
    """Record a sent premarket/postmarket post and refresh movers snapshot."""
    day = day or datetime.now(tz=NY).date().isoformat()
    record = load_daily_telemetry(day, root)
    if context:
        record["top_gainers"] = list(context.get("top_gainers") or record.get("top_gainers") or [])
        record["top_losers"] = list(context.get("top_losers") or record.get("top_losers") or [])
        if context.get("featured_mover"):
            record["featured_mover"] = context["featured_mover"]

    posts = record.setdefault("posts", {})
    posts[time_period] = {
        "sent_at": datetime.now(tz=NY).isoformat(),
        "featured_stock": post_data.get("featured_stock"),
        "style": post_data.get("style"),
        "char_count": post_data.get("char_count"),
        "post": post_data.get("post"),
        "market_summary": post_data.get("market_summary"),
    }
    save_daily_telemetry(record, root)
    return record


def reports_dir(root: Optional[Path] = None) -> Path:
    path = (root or default_telemetry_dir()) / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_dir(root: Optional[Path] = None) -> Path:
    path = (root or default_telemetry_dir()) / "research"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_log_path(day: Optional[str] = None, root: Optional[Path] = None) -> Path:
    day = day or datetime.now(tz=NY).date().isoformat()
    return research_dir(root) / f"{day}.md"


def append_featured_research(section_md: str, day: Optional[str] = None, root: Optional[Path] = None) -> Path:
    """Append a ## Featured: $TICKER block to the daily research log."""
    day = day or datetime.now(tz=NY).date().isoformat()
    path = research_log_path(day, root)
    header = f"# Research log — {day}\n\n"
    if not path.exists():
        path.write_text(header, encoding="utf-8")
    elif not path.read_text(encoding="utf-8").startswith("# Research log"):
        existing = path.read_text(encoding="utf-8")
        path.write_text(header + existing, encoding="utf-8")

    text = path.read_text(encoding="utf-8")
    if section_md.strip() not in text:
        if not text.endswith("\n"):
            text += "\n"
        text += section_md.strip() + "\n\n"
        path.write_text(text, encoding="utf-8")
    return path


def all_movers_for_audit(record: dict[str, Any]) -> List[dict[str, Any]]:
    """Union gainers and losers with a ``direction`` field for scoring."""
    out: list[dict[str, Any]] = []
    for mover in record.get("top_gainers") or []:
        item = dict(mover)
        item["direction"] = "gainer"
        out.append(item)
    for mover in record.get("top_losers") or []:
        item = dict(mover)
        item["direction"] = "loser"
        out.append(item)
    return out
