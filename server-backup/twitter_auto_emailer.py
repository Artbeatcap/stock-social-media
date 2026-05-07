"""
Automated Twitter Post Generator with Email Delivery
Generates pre-market (8am) and post-market (5pm) posts based on live market data
Emails posts to clarencebellwork@gmail.com
"""

import os
import sys
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple
import pytz
from openai import OpenAI
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

# Add parent directory to path for imports
sys.path.append('/home/tradingapp/trading-analysis')

# Import market data functions
try:
    from market_brief_generator import (
        fetch_stock_prices_strict,
        fetch_top_movers_av,
        fetch_news,
        has_valid_price_data
    )
    DATA_AVAILABLE = True
except ImportError:
    DATA_AVAILABLE = False
    print("Warning: Could not import market data functions")

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# SendGrid configuration
SENDGRID_KEY = os.getenv('SENDGRID_KEY')
FROM_EMAIL = os.getenv('EMAIL_FROM', 'support@optionsplunge.com')
FROM_NAME = os.getenv('EMAIL_FROM_NAME', 'Options Plunge')
TO_EMAIL = 'clarencebellwork@gmail.com'

NY = pytz.timezone('America/New_York')
TWITTER_CHAR_LIMIT = 280


def get_market_context() -> Dict[str, Any]:
    """Fetch current market data for context"""
    context = {
        'timestamp': datetime.now(NY).strftime('%Y-%m-%d %H:%M ET'),
        'date': datetime.now(NY).strftime('%A, %B %d, %Y'),
        'spy_price': None,
        'spy_change': None,
        'spy_change_pct': None,
        'qqq_price': None,
        'qqq_change': None,
        'qqq_change_pct': None,
        'vix_price': None,
        'market_direction': 'mixed',
        'top_gainers': [],
        'top_losers': [],
        'headlines': []
    }
    
    if not DATA_AVAILABLE:
        logger.warning("Market data functions not available")
        return context
    
    try:
        # Get stock prices
        prices = fetch_stock_prices_strict()
        if prices and has_valid_price_data(prices):
            spy = prices.get('spy', {})
            qqq = prices.get('qqq', {})
            vix = prices.get('vix', {})
            
            context['spy_price'] = spy.get('current_price')
            context['spy_change'] = spy.get('change')
            context['spy_change_pct'] = spy.get('change_percent')
            context['qqq_price'] = qqq.get('current_price')
            context['qqq_change'] = qqq.get('change')
            context['qqq_change_pct'] = qqq.get('change_percent')
            context['vix_price'] = vix.get('current_price')
            
            # Determine market direction
            if spy.get('change', 0) > 0 and qqq.get('change', 0) > 0:
                context['market_direction'] = 'bullish'
            elif spy.get('change', 0) < 0 and qqq.get('change', 0) < 0:
                context['market_direction'] = 'bearish'
            else:
                context['market_direction'] = 'mixed'
        
        # Get top movers
        movers = fetch_top_movers_av()
        if movers:
            # Separate gainers and losers
            gainers = [m for m in movers if m.get('change_percentage', 0) > 0]
            losers = [m for m in movers if m.get('change_percentage', 0) < 0]
            
            context['top_gainers'] = gainers[:5]  # Top 5 gainers
            context['top_losers'] = sorted(losers, key=lambda x: x.get('change_percentage', 0))[:5]  # Top 5 losers
        
        # Get headlines
        news = fetch_news()
        if news:
            context['headlines'] = news[:5]  # Top 5 headlines
            
    except Exception as e:
        logger.error(f"Error fetching market context: {e}")
    
    return context


def identify_featured_stock(context: Dict[str, Any], time_period: str = 'premarket') -> Tuple[str, str, float, str]:
    """
    Identify the most notable stock to feature in the post
    
    Returns:
        Tuple of (ticker, company_name, change_pct, reason)
    """
    # Check for major retail stocks with big moves
    retail_favorites = ['TSLA', 'AAPL', 'NVDA', 'AMD', 'AMZN', 'MSFT', 'META', 'GOOGL', 'NFLX', 'DIS']
    
    # Combine gainers and losers
    all_movers = context.get('top_gainers', []) + context.get('top_losers', [])
    
    if not all_movers:
        return None, None, None, None
    
    # First, look for retail favorites with significant moves
    for mover in all_movers:
        ticker = mover.get('ticker', '').upper()
        change_pct = mover.get('change_percentage', 0)
        
        if ticker in retail_favorites and abs(change_pct) >= 2.0:
            reason = 'major retail stock with significant move'
            return ticker, mover.get('name', ticker), change_pct, reason
    
    # If no retail favorites, find the biggest mover
    biggest_mover = max(all_movers, key=lambda x: abs(x.get('change_percentage', 0)))
    ticker = biggest_mover.get('ticker', '')
    change_pct = biggest_mover.get('change_percentage', 0)
    
    if abs(change_pct) >= 5.0:
        reason = 'largest mover'
        return ticker, biggest_mover.get('name', ticker), change_pct, reason
    
    return None, None, None, None


def generate_premarket_post(context: Dict[str, Any]) -> Dict[str, str]:
    """Generate pre-market post with live market data"""
    
    spy_price = context.get('spy_price', 0)
    spy_change_pct = context.get('spy_change_pct', 0)
    vix_price = context.get('vix_price', 0)
    market_direction = context.get('market_direction', 'mixed')
    
    # Identify featured stock
    featured_ticker, featured_name, featured_change, featured_reason = identify_featured_stock(context, 'premarket')
    
    # Build market context for GPT
    featured_stock_str = f"{featured_ticker} ({featured_change:+.2f}%) - {featured_reason}" if featured_ticker else "None"
    market_summary = f"""Market Context:
- SPY: ${spy_price:.2f} ({spy_change_pct:+.2f}%)
- VIX: {vix_price:.2f}
- Market Direction: {market_direction}
- Featured Stock: {featured_stock_str}
- Top Gainers: {', '.join([f"{m['ticker']} (+{m['change_percentage']:.1f}%)" for m in context.get('top_gainers', [])[:3]])}
- Top Losers: {', '.join([f"{m['ticker']} ({m['change_percentage']:.1f}%)" for m in context.get('top_losers', [])[:3]])}
"""
    
    if not openai_client:
        logger.error("OpenAI client not configured")
        return generate_fallback_premarket(context, featured_ticker, featured_change)
    
    try:
        # Randomly select one of the 3 styles
        import random
        style_choice = random.choice(['reflective', 'risk', 'learning'])
        
        if style_choice == 'reflective':
            prompt = f"""Write a pre-market Twitter post (MAX 280 chars) for traders in a reflective, questioning style.

{market_summary}

Style: Reflective Question
- Ask a thought-provoking question about trading psychology
- Reference specific market conditions (SPY price, direction, or featured stock if notable)
- Make traders pause and think before acting
- Human, conversational tone
- Include 1-2 relevant hashtags at the end
- Must be under 280 characters INCLUDING hashtags

Example structure: "[Market observation]. Before you [action], ask yourself: [thought-provoking question]? [Insight]. #trading #premarket"

{f'If there\'s a significant mover ({featured_ticker} at {featured_change:+.2f}%), consider mentioning it.' if featured_ticker else 'Focus on general market conditions and trading psychology.'}
"""
        
        elif style_choice == 'risk':
            prompt = f"""Write a pre-market Twitter post (MAX 280 chars) for traders focusing on risk management.

{market_summary}

Style: Risk Management
- Emphasize capital preservation and defining risk
- Reference SPY price level and/or featured stock
- Remind traders to plan before the open
- Practical, protective tone
- Include 1-2 relevant hashtags at the end
- Must be under 280 characters INCLUDING hashtags

Example structure: "SPY at [price] premarket. [Risk principle]. Define your risk BEFORE the open. [Wisdom about survival]. #daytrading #riskmanagement"

{f'If there\'s a significant mover ({featured_ticker} at {featured_change:+.2f}%), consider mentioning the risk of chasing.' if featured_ticker else 'Focus on general risk management principles.'}
"""
        
        else:  # learning
            prompt = f"""Write a pre-market Twitter post (MAX 280 chars) for traders as a teaching moment.

{market_summary}

Style: Learning Moment
- Teach something about premarket behavior or market mechanics
- Use "Here's the thing:" or similar conversational phrase
- Share insight about liquidity, gaps, or timing
- Educational but relatable tone
- Include 1-2 relevant hashtags at the end
- Must be under 280 characters INCLUDING hashtags

Example structure: "[Observation]. Here's the thing: [market mechanic/wisdom]. [Practical advice]. #trading101"

{f'If there\'s a significant mover ({featured_ticker} at {featured_change:+.2f}%), you could use it as a teaching example.' if featured_ticker else 'Focus on general premarket mechanics and trader education.'}
"""
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an experienced trader creating concise, thoughtful Twitter posts. Your posts must be under 280 characters and sound human, not robotic."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.9
        )
        
        post = response.choices[0].message.content.strip()
        
        # Ensure it's under 280 chars
        if len(post) > 280:
            logger.warning(f"Post too long ({len(post)} chars), truncating")
            post = post[:277] + "..."
        
        return {
            'post': post,
            'style': style_choice,
            'char_count': len(post),
            'featured_stock': featured_ticker,
            'market_summary': market_summary
        }
        
    except Exception as e:
        logger.error(f"Error generating premarket post: {e}")
        return generate_fallback_premarket(context, featured_ticker, featured_change)


def generate_postmarket_post(context: Dict[str, Any]) -> Dict[str, str]:
    """Generate post-market post with live market data"""
    
    spy_price = context.get('spy_price', 0)
    spy_change_pct = context.get('spy_change_pct', 0)
    market_direction = context.get('market_direction', 'mixed')
    
    # Identify featured stock (biggest mover of the day)
    featured_ticker, featured_name, featured_change, featured_reason = identify_featured_stock(context, 'postmarket')
    
    # Build market context for GPT
    featured_stock_str = f"{featured_ticker} ({featured_change:+.2f}%) - {featured_reason}" if featured_ticker else "None"
    market_summary = f"""Market Context:
- SPY: ${spy_price:.2f} ({spy_change_pct:+.2f}%) - Closed {'up' if spy_change_pct > 0 else 'down' if spy_change_pct < 0 else 'flat'}
- Market Direction: {market_direction}
- Featured Stock: {featured_stock_str}
- Top Performers: {', '.join([f"{m['ticker']} (+{m['change_percentage']:.1f}%)" for m in context.get('top_gainers', [])[:3]])}
- Worst Performers: {', '.join([f"{m['ticker']} ({m['change_percentage']:.1f}%)" for m in context.get('top_losers', [])[:3]])}
"""
    
    if not openai_client:
        logger.error("OpenAI client not configured")
        return generate_fallback_postmarket(context, featured_ticker, featured_change)
    
    try:
        # Randomly select one of the 3 styles
        import random
        style_choice = random.choice(['reflection', 'lesson', 'tactical'])
        
        if style_choice == 'reflection':
            prompt = f"""Write a post-market Twitter post (MAX 280 chars) for traders with a reflective tone.

{market_summary}

Style: Reflection
- Acknowledge the day is done (win or lose)
- Emphasize consistency and process over results
- Supportive, growth-oriented tone
- Include 1-2 relevant hashtags at the end
- Must be under 280 characters INCLUDING hashtags

Example structure: "Market closed [direction]. Win or lose, [encouraging insight]. Did you [process question]? That's what matters. #tradingjourney"

You can reference how SPY closed or a notable mover if relevant.
"""
        
        elif style_choice == 'lesson':
            prompt = f"""Write a post-market Twitter post (MAX 280 chars) extracting a lesson from today's trading.

{market_summary}

Style: Lesson from the Day
- Extract a tactical or psychological lesson from the day's action
- Mention how market behaved (choppy, trending, volatile, etc.)
- Share what type of trader succeeded today
- Practical wisdom tone
- Include 1-2 relevant hashtags at the end
- Must be under 280 characters INCLUDING hashtags

Example structure: "Today's lesson: [Market behavior]. [Type of day] rewards [approach], punishes [approach]. [Principle]. #trading"

{f'If {featured_ticker} had a big move ({featured_change:+.2f}%), consider using it as an example.' if featured_ticker else 'Focus on general market lessons from today\'s action.'}
"""
        
        else:  # tactical
            prompt = f"""Write a post-market Twitter post (MAX 280 chars) with a tactical market review.

{market_summary}

Style: Tactical Review
- Brief tactical observation about today's price action
- Note what worked vs what didn't
- Emphasize matching strategy to market type
- Analytical but accessible tone
- Include 1-2 relevant hashtags at the end
- Must be under 280 characters INCLUDING hashtags

Example structure: "SPY finished at [price]. Today's takeaway: [tactical observation]. [What worked]. Your strategy should match the tape. #daytrading"

{f'Consider mentioning {featured_ticker}\'s {featured_change:+.2f}% move if it illustrates a point.' if featured_ticker else 'Focus on general tactical observations from today\'s market action.'}
"""
        
        response = openai_client.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "You are an experienced trader creating concise, insightful Twitter posts. Your posts must be under 280 characters and sound human, not robotic."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=100,
            temperature=0.9
        )
        
        post = response.choices[0].message.content.strip()
        
        # Ensure it's under 280 chars
        if len(post) > 280:
            logger.warning(f"Post too long ({len(post)} chars), truncating")
            post = post[:277] + "..."
        
        return {
            'post': post,
            'style': style_choice,
            'char_count': len(post),
            'featured_stock': featured_ticker,
            'market_summary': market_summary
        }
        
    except Exception as e:
        logger.error(f"Error generating postmarket post: {e}")
        return generate_fallback_postmarket(context, featured_ticker, featured_change)


def generate_fallback_premarket(context: Dict[str, Any], ticker: str = None, change: float = None) -> Dict[str, str]:
    """Generate fallback premarket post if AI fails"""
    spy_price = context.get('spy_price', 0)
    spy_change_pct = context.get('spy_change_pct', 0)
    
    if ticker and abs(change) >= 3.0:
        post = f"{ticker} {change:+.2f}% premarket. Before you chase, ask yourself: Is this the start of a move or just noise? Patience often beats speed. #trading #premarket"
    else:
        post = f"SPY at ${spy_price:.2f} premarket. Define your risk BEFORE the open, not after. The traders who survive protect capital first. #daytrading"
    
    return {
        'post': post,
        'style': 'fallback',
        'char_count': len(post),
        'featured_stock': ticker,
        'market_summary': 'Fallback post used'
    }


def generate_fallback_postmarket(context: Dict[str, Any], ticker: str = None, change: float = None) -> Dict[str, str]:
    """Generate fallback postmarket post if AI fails"""
    spy_change_pct = context.get('spy_change_pct', 0)
    
    if ticker and abs(change) >= 3.0:
        post = f"{ticker} moved {change:+.2f}% today. Big moves test discipline. Did you follow your plan or chase? That's the real lesson. #trading"
    else:
        post = f"Market closed. Win or lose, you showed up. That consistency matters more than today's P&L. Did you follow your plan? #tradingjourney"
    
    return {
        'post': post,
        'style': 'fallback',
        'char_count': len(post),
        'featured_stock': ticker,
        'market_summary': 'Fallback post used'
    }


def send_email(subject: str, html_content: str, text_content: str) -> bool:
    """Send email via SendGrid"""
    
    if not SENDGRID_KEY:
        logger.error("SENDGRID_KEY not configured")
        return False
    
    try:
        sg = SendGridAPIClient(api_key=SENDGRID_KEY)
        
        message = Mail(
            from_email=Email(FROM_EMAIL, FROM_NAME),
            to_emails=To(TO_EMAIL),
            subject=subject,
            plain_text_content=Content("text/plain", text_content),
            html_content=Content("text/html", html_content)
        )
        
        response = sg.send(message)
        
        if response.status_code in (200, 202):
            logger.info(f"Email sent successfully to {TO_EMAIL}")
            return True
        else:
            logger.error(f"SendGrid error: {response.status_code}")
            return False
            
    except Exception as e:
        logger.error(f"Error sending email: {e}")
        return False


def create_email_html(post_data: Dict[str, str], context: Dict[str, Any], time_period: str) -> str:
    """Create HTML email content"""
    
    post = post_data['post']
    style = post_data['style']
    char_count = post_data['char_count']
    featured_stock = post_data.get('featured_stock')
    
    # Time-specific header
    if time_period == 'premarket':
        header = f"Pre-Market Twitter Post for {context['date']}"
        emoji = "📈"
    else:
        header = f"Post-Market Twitter Post for {context['date']}"
        emoji = "📉"
    
    # Build featured stock section
    featured_html = ""
    if featured_stock:
        featured_html = f"""
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h3 style="margin: 0 0 10px 0; color: #856404;">Featured Stock: {featured_stock}</h3>
            <p style="margin: 0; color: #856404;">This stock was selected due to significant price movement and is mentioned in the post.</p>
        </div>
        """
    
    # Build market data section
    market_html = f"""
    <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
        <h3 style="margin: 0 0 10px 0; color: #2c3e50;">Market Data</h3>
        <ul style="margin: 0; padding-left: 20px;">
            <li>SPY: ${context.get('spy_price', 0):.2f} ({context.get('spy_change_pct', 0):+.2f}%)</li>
            <li>QQQ: ${context.get('qqq_price', 0):.2f} ({context.get('qqq_change_pct', 0):+.2f}%)</li>
            <li>VIX: {context.get('vix_price', 0):.2f}</li>
            <li>Direction: {context.get('market_direction', 'mixed').upper()}</li>
        </ul>
    </div>
    """
    
    # Build movers section
    gainers = context.get('top_gainers', [])[:3]
    losers = context.get('top_losers', [])[:3]
    
    movers_html = ""
    if gainers or losers:
        movers_html = "<div style='background-color: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0;'>"
        movers_html += "<h3 style='margin: 0 0 10px 0; color: #004085;'>Top Movers</h3>"
        
        if gainers:
            movers_html += "<p style='margin: 5px 0; font-weight: bold; color: #28a745;'>Gainers:</p><ul style='margin: 5px 0; padding-left: 20px;'>"
            for g in gainers:
                movers_html += f"<li style='color: #28a745;'>{g['ticker']}: +{g['change_percentage']:.2f}%</li>"
            movers_html += "</ul>"
        
        if losers:
            movers_html += "<p style='margin: 5px 0; font-weight: bold; color: #dc3545;'>Losers:</p><ul style='margin: 5px 0; padding-left: 20px;'>"
            for l in losers:
                movers_html += f"<li style='color: #dc3545;'>{l['ticker']}: {l['change_percentage']:.2f}%</li>"
            movers_html += "</ul>"
        
        movers_html += "</div>"
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
    </head>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333; max-width: 600px; margin: 0 auto; padding: 20px;">
        <div style="background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); color: white; padding: 30px; border-radius: 10px; text-align: center; margin-bottom: 30px;">
            <h1 style="margin: 0; font-size: 28px;">{emoji} {header}</h1>
            <p style="margin: 10px 0 0 0; font-size: 14px; opacity: 0.9;">{context['timestamp']}</p>
        </div>
        
        <div style="background-color: #ffffff; border: 2px solid #3498db; border-radius: 10px; padding: 25px; margin: 20px 0;">
            <h2 style="margin: 0 0 15px 0; color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px;">
                Your Twitter Post
            </h2>
            <div style="background-color: #f8f9fa; padding: 20px; border-left: 4px solid #3498db; font-size: 16px; line-height: 1.8;">
                {post}
            </div>
            <div style="margin-top: 15px; padding: 10px; background-color: #e7f3ff; border-radius: 5px;">
                <p style="margin: 0; font-size: 14px; color: #004085;">
                    <strong>Style:</strong> {style.title()} | 
                    <strong>Characters:</strong> {char_count}/280 | 
                    <strong>Status:</strong> {'✅ Ready to post' if char_count <= 280 else '⚠️ Too long'}
                </p>
            </div>
        </div>
        
        {featured_html}
        {market_html}
        {movers_html}
        
        <div style="background-color: #fff3cd; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #ffc107;">
            <h3 style="margin: 0 0 10px 0; color: #856404;">📝 Instructions</h3>
            <ol style="margin: 0; padding-left: 20px; color: #856404;">
                <li>Review the post above</li>
                <li>Copy the text (it's ready to paste!)</li>
                <li>Log into Twitter/X</li>
                <li>Paste and post at {time_period.replace('pre', 'Pre-').replace('post', 'Post-')} time</li>
                <li>Track engagement (likes, retweets, replies)</li>
            </ol>
        </div>
        
        <div style="background-color: #d4edda; padding: 15px; border-radius: 5px; margin: 20px 0; border-left: 4px solid #28a745;">
            <h3 style="margin: 0 0 10px 0; color: #155724;">💡 Why This Post?</h3>
            <p style="margin: 0; color: #155724; font-size: 14px;">
                This <strong>{style}</strong> style post was selected to help traders {'prepare for the day ahead with mindfulness and strategy' if time_period == 'premarket' else 'reflect on their trading day and extract lessons for growth'}. 
                {f'The post features <strong>{featured_stock}</strong> because it had significant movement today.' if featured_stock else 'The post focuses on broader market principles and trader psychology.'}
            </p>
        </div>
        
        <div style="text-align: center; margin: 30px 0; padding: 20px; background-color: #f8f9fa; border-radius: 5px;">
            <p style="margin: 0; color: #6c757d; font-size: 14px;">
                Generated by Options Plunge Twitter Post Generator<br>
                Automated daily at 8:00 AM and 5:00 PM ET
            </p>
        </div>
    </body>
    </html>
    """
    
    return html


def create_email_text(post_data: Dict[str, str], context: Dict[str, Any], time_period: str) -> str:
    """Create plain text email content"""
    
    post = post_data['post']
    style = post_data['style']
    char_count = post_data['char_count']
    featured_stock = post_data.get('featured_stock')
    
    text = f"""
{'='*70}
{time_period.upper()} TWITTER POST - {context['date']}
Generated: {context['timestamp']}
{'='*70}

YOUR TWITTER POST:
{'-'*70}
{post}
{'-'*70}

POST DETAILS:
- Style: {style.title()}
- Characters: {char_count}/280
- Status: {'Ready to post' if char_count <= 280 else 'Too long - needs editing'}
{f"- Featured Stock: {featured_stock}" if featured_stock else ""}

MARKET DATA:
- SPY: ${context.get('spy_price', 0):.2f} ({context.get('spy_change_pct', 0):+.2f}%)
- QQQ: ${context.get('qqq_price', 0):.2f} ({context.get('qqq_change_pct', 0):+.2f}%)
- VIX: {context.get('vix_price', 0):.2f}
- Direction: {context.get('market_direction', 'mixed').upper()}

TOP GAINERS:
"""
    
    for g in context.get('top_gainers', [])[:3]:
        text += f"  {g['ticker']}: +{g['change_percentage']:.2f}%\n"
    
    text += "\nTOP LOSERS:\n"
    for l in context.get('top_losers', [])[:3]:
        text += f"  {l['ticker']}: {l['change_percentage']:.2f}%\n"
    
    text += f"""
{'='*70}
INSTRUCTIONS:
1. Copy the post text above
2. Log into Twitter/X
3. Paste and post
4. Track engagement

{'='*70}
Powered by Options Plunge - Automated Twitter Marketing
{'='*70}
    """
    
    return text


def main(time_period: str = None):
    """
    Main function to generate and email Twitter posts
    
    Args:
        time_period: 'premarket' or 'postmarket' (auto-detected if None)
    """
    
    # Auto-detect time period if not specified
    if not time_period:
        current_hour = datetime.now(NY).hour
        if current_hour < 12:
            time_period = 'premarket'
        else:
            time_period = 'postmarket'
    
    logger.info(f"Starting {time_period} Twitter post generation")
    
    # Fetch market context
    logger.info("Fetching market data...")
    context = get_market_context()
    
    # Generate post
    logger.info(f"Generating {time_period} post...")
    if time_period == 'premarket':
        post_data = generate_premarket_post(context)
        subject = f"Pre-Market Twitter Post - {context['date']}"
    else:
        post_data = generate_postmarket_post(context)
        subject = f"Post-Market Twitter Post - {context['date']}"
    
    logger.info(f"Generated {post_data['style']} post ({post_data['char_count']} chars)")
    
    # Create email content
    logger.info("Creating email content...")
    html_content = create_email_html(post_data, context, time_period)
    text_content = create_email_text(post_data, context, time_period)
    
    # Send email
    logger.info(f"Sending email to {TO_EMAIL}...")
    success = send_email(subject, html_content, text_content)
    
    if success:
        logger.info("✅ Twitter post email sent successfully!")
        print(f"\n✅ SUCCESS: {time_period.title()} post emailed to {TO_EMAIL}")
        print(f"Post: {post_data['post']}")
        print(f"Style: {post_data['style']}")
        print(f"Characters: {post_data['char_count']}/280")
        if post_data.get('featured_stock'):
            print(f"Featured: {post_data['featured_stock']}")
    else:
        logger.error("❌ Failed to send email")
        print(f"\n❌ FAILED: Could not send email")
    
    return success


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='Generate Twitter posts for trading')
    parser.add_argument('--time', choices=['premarket', 'postmarket'], 
                       help='Time period (auto-detected if not specified)')
    args = parser.parse_args()
    
    main(args.time)

