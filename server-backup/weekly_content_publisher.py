#!/usr/bin/env python3
"""
POST weekly brief context to ChartSignl ingest (cross-app contract).

Env:
  CHARTSIGNL_INGEST_URL          e.g. https://api.chartsignl.com/api/internal/weekly-brief/ingest
  WEEKLY_CONTENT_INGEST_SECRET   shared secret (sent as x-ingest-secret header)

Non-fatal: callers should not abort the OP weekly job on publish failure.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

import requests

logger = logging.getLogger("weekly_content_publisher")

CONTRACT_KEYS = (
    "sections",
    "tape",
    "sectors",
    "movers",
    "levels",
    "week_ahead_bullets",
    "earnings_bullets",
)

METADATA_KEYS = (
    "week_label",
    "preheader",
    "date_range",
    "generated_at",
    "data_stale",
)

DEFAULT_INGEST_URL = "https://api.chartsignl.com/api/internal/weekly-brief/ingest"
POST_TIMEOUT_SEC = 30


def _omit_nulls(value: Any) -> Any:
    """JSON null fails ChartSignl optional string fields; drop nulls instead."""
    if isinstance(value, dict):
        return {k: _omit_nulls(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [_omit_nulls(v) for v in value]
    return value


def build_ingest_artifact(ctx: dict) -> dict:
    """Extract neutral ChartSignl contract keys from full OP weekly context."""
    artifact: Dict[str, Any] = {"schema_version": 1}
    for key in CONTRACT_KEYS:
        if key in ctx:
            artifact[key] = ctx[key]
    for key in METADATA_KEYS:
        if key in ctx:
            artifact[key] = ctx[key]
    if "data_stale" not in artifact:
        artifact["data_stale"] = False
    # ChartSignl validates week_label; fall back from date_range.label if needed
    if not artifact.get("week_label"):
        label = (ctx.get("date_range") or {}).get("label")
        if label:
            artifact["week_label"] = label
    return _omit_nulls(artifact)


def summarize_contract_keys(ctx: dict) -> str:
    """One-line summary for dry-run logging."""
    parts = []
    for key in CONTRACT_KEYS:
        val = ctx.get(key)
        if key == "sections" and isinstance(val, dict):
            parts.append(f"sections={len(val)} fields")
        elif isinstance(val, list):
            parts.append(f"{key}={len(val)}")
        elif isinstance(val, dict):
            parts.append(f"{key}={len(val)} keys")
        elif val:
            parts.append(f"{key}=ok")
        else:
            parts.append(f"{key}=empty")
    return ", ".join(parts)


def _ingest_config() -> tuple[Optional[str], Optional[str]]:
    url = (os.getenv("CHARTSIGNL_INGEST_URL") or DEFAULT_INGEST_URL).strip()
    secret = (os.getenv("WEEKLY_CONTENT_INGEST_SECRET") or "").strip()
    if not secret:
        return None, None
    return url, secret


def publish_weekly_content(ctx: dict) -> bool:
    """
    POST ingest artifact to ChartSignl. Returns True on 2xx, False otherwise.
    Never raises — OP weekly send must not depend on ChartSignl availability.
    """
    url, secret = _ingest_config()
    if not url or not secret:
        logger.warning(
            "ChartSignl weekly ingest skipped (set CHARTSIGNL_INGEST_URL and "
            "WEEKLY_CONTENT_INGEST_SECRET to enable)"
        )
        return False

    artifact = build_ingest_artifact(ctx)
    try:
        resp = requests.post(
            url,
            json=artifact,
            headers={
                "x-ingest-secret": secret,
                "Content-Type": "application/json",
            },
            timeout=POST_TIMEOUT_SEC,
        )
        if resp.status_code >= 400:
            logger.warning(
                "ChartSignl weekly ingest failed: HTTP %s — %s",
                resp.status_code,
                (resp.text or "")[:500],
            )
            return False
        logger.info("ChartSignl weekly ingest succeeded (HTTP %s)", resp.status_code)
        return True
    except Exception as exc:
        logger.warning("ChartSignl weekly ingest failed (non-fatal): %s", exc)
        return False
