#!/usr/bin/env python3
"""
Weekly Market Brief Sender (Hybrid)

If a source JSON is provided, uses it. Otherwise builds a Weekly Brief with:
 - Week of <Mon–Fri> date range
 - Week-ahead catalysts (Finnhub ➜ Alpha Vantage fallback)
 - Movers snapshot (Alpha Vantage TOP_GAINERS_LOSERS, budget-friendly)
Sends the email and writes a preview HTML for /brief/weekly.
"""

import os
import sys
import json
import argparse
import logging
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from pytz import timezone
NY = timezone("America/New_York")

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from emailer import render_weekly_brief, send_weekly_brief_direct, send_weekly_brief
from market_brief_generator import build_weekly_brief, BriefDataUnavailable

logger = logging.getLogger("weekly_brief_sender")
logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")

def load_weekly_context_from_json(path: Path) -> dict:
    """Load weekly context from a JSON file if present"""
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)

def get_weekly_subscribers() -> List[str]:
    """Get weekly subscriber list from database (Pro users + weekly subscribers) or environment fallback"""
    try:
        # Try to get subscribers from database first
        from app import app, db
        from models import User
        
        with app.app_context():
            # Get Pro users (active or trialing) + users with weekly subscription enabled
            subscribers = User.query.filter(
                db.or_(
                    # Pro users should get weekly brief
                    User.subscription_status.in_(['active', 'trialing']),
                    # Users who explicitly subscribed to weekly
                    User.is_subscribed_weekly == True
                ),
                User.email_verified == True
            ).all()
            
            emails = [user.email for user in subscribers if user.email]
            logger.info(f"📧 Found {len(emails)} weekly subscribers (Pro users + weekly subscribers)")
            return emails
            
    except Exception as e:
        logger.warning(f"Database query failed ({e}), falling back to environment variables")
        
        # Fallback to environment variable
        weekly_to = os.getenv("WEEKLY_TO") or os.getenv("NEWSLETTER_TO")
        if not weekly_to:
            logger.error("No WEEKLY_TO or NEWSLETTER_TO configured")
            return []
        return [e.strip() for e in weekly_to.split(',') if e.strip()]

def render_weekly_email(context: dict) -> tuple[str, str]:
    """Render weekly email HTML and text via weekly templates (fallback to morning if needed)."""
    try:
        return render_weekly_brief(context)
    except Exception as e:
        logger.warning(f"render_weekly_brief failed ({e}); falling back to morning templates.")
        from emailer import render_morning_brief
        return render_morning_brief(context)

def save_preview(html_content: str, label: str = "Weekly"):
    """Save latest weekly brief for the Flask preview route"""
    try:
        base = Path(__file__).resolve().parent
        upload = base / "static" / "uploads"
        upload.mkdir(parents=True, exist_ok=True)
        (upload / "brief_weekly_latest.html").write_text(html_content, encoding="utf-8")
        (upload / "brief_latest_date.txt").write_text(datetime.now(tz=NY).strftime("%Y-%m-%d %H:%M ET"), encoding="utf-8")
        logger.info(f"Saved {label} preview to {upload}")
    except Exception as e:
        logger.warning(f"Could not save preview: {e}")

def main():
    parser = argparse.ArgumentParser(description="Send Weekly Market Brief")
    parser.add_argument("--source", help="Path to weekly JSON file (optional)", required=False)
    parser.add_argument("--dry-run", action="store_true", help="Write files but do not send")
    parser.add_argument("--test-email", help="Send to a single email for testing")
    args = parser.parse_args()

    context: dict
    if args.source:
        src = Path(args.source)
        if not src.exists():
            logger.error(f"Source file not found: {src}")
            sys.exit(1)
        context = load_weekly_context_from_json(src)
        logger.info(f"Loaded weekly context from {src}")
    else:
        logger.info("No --source provided; building weekly brief with hybrid pipeline.")
        try:
            context = build_weekly_brief()
        except BriefDataUnavailable as err:
            logger.error(f"Weekly brief aborted: {err}")
            sys.exit(1)

    # Subject
    dr = context.get("date_range", {})
    label = dr.get("label") or datetime.now(tz=NY).strftime("Week of %Y-%m-%d")
    subject = context.get("subject") or f"Options Plunge — Weekly Brief — {label}"

    # Render templates
    try:
        html, text = render_weekly_email(context)
    except Exception as e:
        logger.error(f"Template rendering failed: {e}")
        sys.exit(1)

    # Test vs production
    if args.test_email:
        recipients = [args.test_email]
        logger.info(f"Sending weekly brief to test recipient: {args.test_email}")
    else:
        recipients = get_weekly_subscribers()
        if not recipients:
            logger.error("No recipients configured")
            sys.exit(1)
        confirm = os.getenv("CONFIRM_SEND")
        if confirm != "1":
            logger.error("Set CONFIRM_SEND=1 to confirm weekly send")
            sys.exit(1)

    # Dry run?
    if args.dry_run:
        outdir = Path("out/weekly") / datetime.now(tz=NY).strftime("%Y-%m-%d")
        outdir.mkdir(parents=True, exist_ok=True)
        (outdir / "weekly.html").write_text(html, encoding="utf-8")
        (outdir / "weekly.txt").write_text(text, encoding="utf-8")
        logger.info(f"Dry-run files saved to {outdir}")
        save_preview(html, label="Weekly (dry-run)")
        return

    # Send
    try:
        success = send_weekly_brief(html, text, subject, recipients) or send_weekly_brief_direct(html, text, subject, recipients)
        if not success:
            logger.error("Weekly brief send failed")
            sys.exit(1)
        logger.info(f"Weekly brief sent to {len(recipients)} recipients")
        save_preview(html, label="Weekly")
    except Exception as e:
        logger.error(f"Error sending weekly brief: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()