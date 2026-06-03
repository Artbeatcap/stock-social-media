"""Read/write production validation telemetry for catalyst quality audits."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytz

logger = logging.getLogger(__name__)

NY = pytz.timezone("America/New_York")
REPO_ROOT = Path(__file__).resolve().parent
TELEMETRY_ROOT = Path(
    os.getenv("VALIDATION_TELEMETRY_DIR", REPO_ROOT / "validation_telemetry")
)


def _today() -> str:
    return datetime.now(NY).date().isoformat()


def day_dir(trade_date: Optional[str] = None) -> Path:
    return TELEMETRY_ROOT / (trade_date or _today())


def reports_dir() -> Path:
    path = TELEMETRY_ROOT / "reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def research_log_dir() -> Path:
    path = TELEMETRY_ROOT / "research_log"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    logger.info("Wrote telemetry snapshot: %s", path)


def write_run_snapshot(
    time_period: str,
    context: Dict[str, Any],
    post_data: Dict[str, Any],
    *,
    trade_date: Optional[str] = None,
) -> Path:
    """Persist movers, catalysts, and post metadata after a successful email run."""
    date_str = trade_date or _today()
    out_dir = day_dir(date_str)
    payload = {
        "date": date_str,
        "time_period": time_period,
        "recorded_at": datetime.now(NY).isoformat(timespec="seconds"),
        "top_gainers": context.get("top_gainers") or [],
        "top_losers": context.get("top_losers") or [],
        "featured_mover": context.get("featured_mover"),
        "featured_stock": post_data.get("featured_stock"),
        "post": post_data.get("post"),
        "style": post_data.get("style"),
        "market_internals": post_data.get("market_internals"),
    }
    path = out_dir / f"{time_period}_run.json"
    _write_json(path, payload)

    gainers_path = out_dir / "top_gainers.json"
    losers_path = out_dir / "top_losers.json"
    _write_json(gainers_path, {"date": date_str, "movers": payload["top_gainers"]})
    _write_json(losers_path, {"date": date_str, "movers": payload["top_losers"]})
    return path


def load_run_snapshot(
    time_period: str = "postmarket",
    *,
    trade_date: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    path = day_dir(trade_date) / f"{time_period}_run.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def load_movers(side: str, *, trade_date: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load top_gainers or top_losers for a trade date."""
    filename = f"top_{side}.json" if side in ("gainers", "losers") else side
    path = day_dir(trade_date) / filename
    if not path.exists():
        run = load_run_snapshot(trade_date=trade_date)
        if not run:
            return []
        key = "top_gainers" if "gainers" in side else "top_losers"
        return run.get(key) or []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("movers") or data.get(side) or []


def load_featured_stock(*, trade_date: Optional[str] = None) -> Optional[str]:
    run = load_run_snapshot("postmarket", trade_date=trade_date)
    if not run:
        return None
    featured = run.get("featured_stock")
    return str(featured).upper() if featured else None
