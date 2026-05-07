#!/usr/bin/env python3
"""
Standalone script to send daily market brief via cron job
Run this script from the trading app directory
"""

import os
import sys
from datetime import datetime

# Add the current directory to Python path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Set up Flask app context
from app import app, db
from market_brief_generator import send_market_brief_to_subscribers

def main():
    """Main function to run the market brief sending"""
    print(f"[{datetime.now()}] Starting daily market brief sending...")
    
    try:
        with app.app_context():
            success_count = send_market_brief_to_subscribers()
            print(f"[{datetime.now()}] Successfully sent brief to {success_count} subscribers")
            return 0
    except Exception as e:
        print(f"[{datetime.now()}] Error sending market brief: {str(e)}")
        return 1

if __name__ == "__main__":
    exit(main()) 