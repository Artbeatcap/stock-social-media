#!/usr/bin/env python3
"""
Weekly Market Brief Scheduler

Default: Sundays at 08:00 AM ET.
Override with env WEEKLY_CRON (e.g., "sun 08:00" or a full Quartz expression).
"""

import os
import sys
import logging
from datetime import datetime
import pytz
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from send_weekly_brief import main as send_weekly_brief

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def start_weekly_brief_scheduler():
    """Start the weekly brief scheduler."""
    try:
        # Set timezone to Eastern Time
        tz = pytz.timezone("America/New_York")
        
        # Create scheduler
        scheduler = BackgroundScheduler(timezone=tz)
        
        # Default: Sunday 08:00 ET
        default_trigger = CronTrigger(day_of_week='sun', hour=8, minute=0, timezone=tz)
        # Allow override via WEEKLY_CRON (e.g., "sun 08:00")
        cron_expr = os.getenv("WEEKLY_CRON", "").strip()
        trigger = default_trigger
        if cron_expr:
            try:
                # Accept formats like "sun 08:00" or full "0 15 18 ? * FRI *"
                if " " in cron_expr and ":" in cron_expr and cron_expr.count(" ") == 1:
                    dow, hm = cron_expr.split(" ")
                    hh, mm = hm.split(":")
                    trigger = CronTrigger(day_of_week=dow, hour=int(hh), minute=int(mm), timezone=tz)
                else:
                    # Assume Quartz-style expression
                    trigger = CronTrigger.from_crontab(cron_expr, timezone=tz)
            except Exception as e:
                print(f"WEEKLY_CRON invalid ({e}); using default Sunday 08:00 ET.")
        
        # Add weekly brief job
        scheduler.add_job(func=send_weekly_brief_job,
                          trigger=trigger,
                          id="weekly_brief_job",
                          name="Weekly Market Brief",
                          replace_existing=True,
                          max_instances=1,
                          misfire_grace_time=3600)
        
        # Start scheduler
        scheduler.start()
        
        # --- Self-check: print the next scheduled run in ET ---
        try:
            job = scheduler.get_job("weekly_brief_job")
            if job and job.next_run_time:
                # Ensure we display ET even if APScheduler returns another tz
                next_et = job.next_run_time.astimezone(tz)
                print(f"🕒 Next Weekly Brief run scheduled for: {next_et.strftime('%Y-%m-%d %H:%M %Z')}")
            else:
                print("🕒 Next Weekly Brief run: (not yet computed)")
        except Exception as e:
            print(f"⚠️ Could not determine next run time: {e}")
        
        logger.info("✅ Weekly brief scheduler started")
        
        return scheduler
        
    except Exception as e:
        logger.error(f"❌ Failed to start weekly brief scheduler: {e}")
        return None

def send_weekly_brief_job():
    """Job function to send weekly brief."""
    try:
        logger.info("🚀 Starting weekly brief job...")
        
        # Set confirmation flag
        os.environ['CONFIRM_SEND'] = '1'
        
        # Import and run the main function (no source file needed - uses hybrid builder)
        import sys
        sys.argv = ['send_weekly_brief.py']
        
        # Run the weekly brief sender
        send_weekly_brief()
        
        logger.info("✅ Weekly brief job completed successfully")
        
    except Exception as e:
        logger.error(f"❌ Weekly brief job failed: {e}")

def main():
    """Main function to run the scheduler."""
    print("📅 Weekly Market Brief Scheduler starting… (default Sunday 08:00 ET)")
    sched = start_weekly_brief_scheduler()
    try:
        while True:
            import time
            time.sleep(60)
    except KeyboardInterrupt:
        print("\n🛑 Stopping scheduler...")
        sched.shutdown()
        print("✅ Scheduler stopped")

if __name__ == "__main__":
    main()