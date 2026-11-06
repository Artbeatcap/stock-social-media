# Production validation telemetry

Post-market emails that send successfully write a snapshot of movers and the generated post to:

`validation_telemetry/postmarket_sent/YYYY-MM-DD.json`

(ET calendar date at send time.)

## Scheduled audits

Run from the repo root with `MASSIVE_API_KEY` set (same as `massive_client.py`).

**Catalyst recall (weekdays ~9:15pm ET)** — compares each pipeline `catalyst` on top gainers/losers to the top Massive headlines in the last 24h:

```bash
cd /path/to/stock-social-media
python3 validation_telemetry/catalyst_recall_audit.py
```

Output: `validation_telemetry/reports/catalyst_recall_YYYY-MM-DD.md`

**Featured mover deep dive (weekdays ~5:30pm ET)** — resolves `featured_stock` from the sent post (same logic as the postmarket emailer), then pulls 7d news, today’s 1m bars, and an options snapshot:

```bash
python3 validation_telemetry/featured_mover_deep_dive.py
```

Appends `## Featured: $TICKER` to `validation_telemetry/reports/daily_research_log.md`.

Example cron on a host in `America/New_York`:

```
30 17 * * 1-5 cd /path/to/stock-social-media && /usr/bin/python3 validation_telemetry/featured_mover_deep_dive.py >> /var/log/featured_dive.log 2>&1
15 21 * * 1-5 cd /path/to/stock-social-media && /usr/bin/python3 validation_telemetry/catalyst_recall_audit.py >> /var/log/catalyst_recall.log 2>&1
```

Weekend runs exit 0 unless you pass `--skip-weekend-check` on either script.
