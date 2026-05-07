#!/usr/bin/env python3
"""
Brief routes for the Flask app
"""

from flask import current_app
from pathlib import Path

def register_brief_routes(app):
    """Register brief routes with the Flask app"""
    
    @app.route("/brief/latest")
    def latest_brief():
        """Serve the latest market brief from static files"""
        # Build absolute paths relative to the Flask application's root
        base_dir = Path(current_app.root_path)
        brief_file = base_dir / 'static' / 'uploads' / 'brief_latest.html'
        date_file = base_dir / 'static' / 'uploads' / 'brief_latest_date.txt'
        
        if not brief_file.exists():
            return "No brief available", 404
        
        try:
            # Read the brief content
            with open(brief_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Read the date
            date_str = "Unknown"
            if date_file.exists():
                with open(date_file, 'r') as f:
                    date_str = f.read().strip()
            
            return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}
            
        except Exception as e:
            return f"Error reading brief: {str(e)}", 500

    @app.route("/brief/weekly")
    def weekly_brief():
        """Serve the latest weekly brief from static files"""
        # For now, use the same daily brief as weekly
        base_dir = Path(current_app.root_path)
        brief_file = base_dir / 'static' / 'uploads' / 'brief_weekly_latest.html'
        date_file = base_dir / 'static' / 'uploads' / 'brief_latest_date.txt'
        
        if not brief_file.exists():
            return "No weekly brief available", 404
        
        try:
            # Read the brief content
            with open(brief_file, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            # Modify the title to indicate it's a weekly brief
            html_content = html_content.replace('Morning Market Brief', 'Weekly Market Brief')
            
            return html_content, 200, {'Content-Type': 'text/html; charset=utf-8'}
            
        except Exception as e:
            return f"Error reading weekly brief: {str(e)}", 500

    print("✅ Brief routes registered successfully")

