#!/usr/bin/env python3
"""
Catalyst recall audit — weekdays ~9:15pm ET.

For each ticker in today's production telemetry top_gainers / top_losers,
compare the pipeline-cited catalyst against Massive headlines from the last 24h.
Writes validation_telemetry/reports/catalyst_recall_YYYY-MM-DD.md
"""

from __future__ import annotations

import argparse
import logging
import sys
from datetime import datetime, timedelta
from pathlib import Path

import pytz

try:
    from dotenv import load_dotenv

    load_dotenv()
except Exception:
    pass

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from massive_client import get_news_for_ticker_since  # noqa: E402
from validation_telemetry.scoring import audit_mover_catalyst  # noqa: E402
from validation_telemetry.store import (  # noqa: E402
    load_daily_telemetry,
    reports_dir,
    session_date_et,
)

logger = logging.getLogger(__name__)
NY = pytz.timezone("America/New_York")


def _movers_from_telemetry(telemetry: dict) -> list[tuple[str, dict]]:
    rows: list[tuple[str, dict]] = []
    for mover in telemetry.get("top_gainers") or []:
        rows.append(("gainer", mover))
    for mover in telemetry.get("top_losers") or []:
        rows.append(("loser", mover))
    return rows


def run_audit(session_date: str | None = None) -> Path:
    session = session_date or session_date_et()
    telemetry = load_daily_telemetry(session, "postmarket")
    if not telemetry:
        telemetry = load_daily_telemetry(session, "premarket")
    if not telemetry:
        raise FileNotFoundError(
            f"No telemetry for {session}. Expected validation_telemetry/daily/{session}_postmarket.json"
        )

    now = datetime.now(tz=NY)
    since_ts = int((now - timedelta(hours=24)).timestamp())
    movers = _movers_from_telemetry(telemetry)
    if not movers:
        raise ValueError(f"Telemetry for {session} has no top_gainers/top_losers")

    audits = []
    for side, mover in movers:
        ticker = mover["ticker"]
        news = get_news_for_ticker_since(ticker, since_ts=since_ts, limit=20)[:5]
        audits.append(
            audit_mover_catalyst(
                mover,
                side=side,
                news_items=news,
                now_ts=int(now.timestamp()),
            )
        )

    hits = sum(1 for row in audits if row["result"] == "hit")
    misses = sum(1 for row in audits if row["result"] == "miss")
    no_catalyst = sum(1 for row in audits if row["result"] == "no_catalyst")

    lines = [
        f"# Catalyst Recall Audit — {session}",
        "",
        f"_Generated {now.strftime('%Y-%m-%d %H:%M %Z')}_",
        "",
        "## Summary",
        "",
        f"- Tickers audited: **{len(audits)}**",
        f"- Hits: **{hits}** | Misses: **{misses}** | No pipeline catalyst: **{no_catalyst}**",
        f"- Recall rate (excluding no-catalyst): **{hits / max(hits + misses, 1) * 100:.0f}%**",
        "",
        "## Results",
        "",
        "| Ticker | Move | Pipeline catalyst | Best Massive headline | Result |",
        "| --- | ---: | --- | --- | --- |",
    ]

    for row in audits:
        pct = row.get("pct_move")
        move_str = f"{pct:+.2f}%" if pct is not None else "—"
        pipe = _truncate(row["pipeline_catalyst"], 80)
        massive = _truncate(row["best_massive_headline"], 80)
        lines.append(
            f"| {row['ticker']} | {move_str} | {pipe} | {massive} | **{row['result']}** |"
        )

    lines.extend(["", "## Detail", ""])
    for row in audits:
        lines.append(f"### {row['ticker']} ({row['side']}, {row.get('pct_move'):+.2f}%)")
        lines.append("")
        lines.append(f"- **Pipeline said:** {row['pipeline_catalyst']}")
        lines.append(f"- **Verdict:** {row['result']}")
        lines.append("- **Top Massive headlines (24h, scored):**")
        if row["top_massive_headlines"]:
            for idx, headline in enumerate(row["top_massive_headlines"], start=1):
                lines.append(
                    f"  {idx}. ({headline['score']}) [{headline['sentiment']}] {headline['headline']}"
                )
        else:
            lines.append("  - (none in last 24h)")
        lines.append("")

    report_path = reports_dir() / f"catalyst_recall_{session}.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    logger.info("Wrote catalyst recall report to %s", report_path)
    return report_path


def _truncate(text: str, max_len: int) -> str:
    text = (text or "").replace("|", "/").replace("\n", " ")
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Run catalyst recall audit against Massive news")
    parser.add_argument("--date", help="Session date YYYY-MM-DD (default: today ET)")
    args = parser.parse_args()
    report = run_audit(args.date)
    print(f"Catalyst recall report: {report}")


if __name__ == "__main__":
    main()
