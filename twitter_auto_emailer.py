"""
Automated Twitter Post Generator with Email Delivery
Generates pre-market (8am) and post-market (5pm) posts based on live market data
Emails posts to clarencebellwork@gmail.com
"""

import os
import sys
import json
import re
import requests
from datetime import datetime, timedelta
from typing import Dict, Any, List, Tuple
import pytz
from openai import OpenAI
import logging
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail, Email, To, Content

# Load environment variables from .env file
try:
    from dotenv import load_dotenv
    load_dotenv()
    logger = logging.getLogger(__name__)
    logger.info("Environment variables loaded from .env file")
except ImportError:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning("python-dotenv not installed, using system environment variables")
except Exception as e:
    import logging
    logger = logging.getLogger(__name__)
    logger.warning(f"Error loading .env file: {e}, using system environment variables")

# Add parent directory to path for imports (for optional backward compatibility)
sys.path.append('/home/tradingapp/trading-analysis')

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

try:
    from massive_client import (
        fetch_spy_qqq_data as massive_fetch_spy_qqq_data,
        fetch_top_movers_with_news as massive_fetch_top_movers_with_news,
    )
except Exception as e:
    massive_fetch_spy_qqq_data = None
    massive_fetch_top_movers_with_news = None
    logger.warning(f"Massive client unavailable: {e}")

try:
    from tweet_generator import generate_post_market_tweet
except Exception as e:
    generate_post_market_tweet = None
    logger.warning(f"tweet_generator unavailable: {e}")

try:
    from market_internals import get_market_internals, format_internals_for_prompt
except Exception as e:
    get_market_internals = None
    format_internals_for_prompt = None
    logger.warning(f"market_internals unavailable: {e}")

try:
    from validation_telemetry.store import merge_movers, record_sent_post
except Exception as e:
    merge_movers = None
    record_sent_post = None
    logger.warning(f"validation_telemetry unavailable: {e}")

# Initialize OpenAI client
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
openai_client = OpenAI(api_key=OPENAI_API_KEY) if OPENAI_API_KEY else None

# API Configuration - Check both TRADIER_API_KEY and TRADIER_API_TOKEN for compatibility
TRADIER_TOKEN = os.getenv('TRADIER_API_KEY') or os.getenv('TRADIER_API_TOKEN')
FINNHUB_TOKEN = os.getenv('FINNHUB_TOKEN')

# SendGrid configuration
SENDGRID_KEY = os.getenv('SENDGRID_KEY')
FROM_EMAIL = os.getenv('EMAIL_FROM', 'support@optionsplunge.com')
FROM_NAME = os.getenv('EMAIL_FROM_NAME', 'Options Plunge')
TO_EMAIL = 'clarencebellwork@gmail.com'

NY = pytz.timezone('America/New_York')
TWITTER_CHAR_LIMIT = 280


def validate_no_fabricated_tickers(text: str, allowed_tickers: set) -> tuple[bool, list[str]]:
    """
    Returns (is_valid, list_of_unauthorized_tickers).
    Allowlist must include indices plus every ticker present in fetched movers data.
    """
    safe_indices = {'SPY', 'QQQ', 'IWM', 'VIX'}
    stoplist = {
        'PMI', 'ETF', 'CPI', 'PPI', 'GDP', 'AI', 'USD', 'EU', 'UK', 'US',
        'CEO', 'CFO', 'IPO', 'YOY', 'QOQ', 'EPS', 'ATR', 'EMA', 'RSI',
        'FOMC', 'ISM'
    }
    allowed = {ticker.upper() for ticker in allowed_tickers}
    tokens = re.findall(r'\b[A-Z]{2,5}\b', text)
    suspicious = [
        ticker for ticker in tokens
        if ticker not in allowed
        and ticker not in safe_indices
        and ticker not in stoplist
    ]
    return len(suspicious) == 0, suspicious


# ==================== DATA FETCHING ====================

def fetch_spy_qqq_data() -> Dict[str, Any]:
    """Fetch current SPY and QQQ price data from Tradier."""
    if massive_fetch_spy_qqq_data:
        massive_quotes = massive_fetch_spy_qqq_data()
        if massive_quotes:
            if not massive_quotes.get("VIX"):
                logger.warning("Massive VIX quote unavailable; index coverage may be tier-gated")
            return massive_quotes

    if not TRADIER_TOKEN:
        logger.warning("TRADIER_API_KEY not configured")
        return {}
    
    try:
        headers = {
            'Authorization': f'Bearer {TRADIER_TOKEN}',
            'Accept': 'application/json'
        }
        
        symbols = ['SPY', 'QQQ', 'IWM', 'VIX']
        quotes = {}
        
        url = 'https://api.tradier.com/v1/markets/quotes'
        params = {'symbols': ','.join(symbols), 'greeks': 'false'}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            quote_list = data.get('quotes', {}).get('quote', [])
            
            if not isinstance(quote_list, list):
                quote_list = [quote_list]
            
            for quote in quote_list:
                symbol = quote.get('symbol', '').upper()
                quotes[symbol] = {
                    'last': quote.get('last', 0),
                    'change': quote.get('change', 0),
                    'change_percentage': quote.get('change_percentage', 0),
                    'volume': quote.get('volume', 0),
                    'prevclose': quote.get('prevclose', 0)
                }
        
        return quotes
    except Exception as e:
        logger.error(f"Error fetching Tradier data: {e}")
        return {}


def fetch_economic_data_today() -> List[Dict[str, str]]:
    """Fetch today's key economic releases from Finnhub."""
    if not FINNHUB_TOKEN:
        logger.warning("FINNHUB_TOKEN not configured")
        return []
    
    try:
        today = datetime.now(tz=NY).date().isoformat()
        url = "https://finnhub.io/api/v1/calendar/economic"
        params = {"from": today, "to": today, "token": FINNHUB_TOKEN}
        
        response = requests.get(url, params=params, timeout=10)
        
        if response.status_code == 200:
            data = response.json()
            events = data.get("economicCalendar", [])
            
            # Filter for important events
            key_terms = ("cpi", "ppi", "payroll", "employment", "claims", 
                        "pmi", "ism", "gdp", "confidence", "retail sales", "fomc")
            
            important_events = []
            for ev in events:
                name = (ev.get("event") or "").strip()
                if any(k in name.lower() for k in key_terms):
                    important_events.append({
                        "event": name,
                        "actual": ev.get("actual", ""),
                        "estimate": ev.get("estimate", ""),
                        "previous": ev.get("previous", ""),
                        "impact": ev.get("impact", "")
                    })
            
            return important_events
    except Exception as e:
        logger.error(f"Error fetching economic data: {e}")
        return []


def fetch_top_movers_with_news(limit: int = 10) -> List[Dict[str, Any]]:
    """Fetch top movers with news catalysts from Tradier and Finnhub."""
    if massive_fetch_top_movers_with_news:
        massive_movers = massive_fetch_top_movers_with_news(limit=limit)
        if massive_movers:
            return massive_movers

    if not TRADIER_TOKEN or not FINNHUB_TOKEN:
        logger.warning("TRADIER_API_KEY or FINNHUB_TOKEN not configured for movers")
        return []
    
    try:
        # Major retail stocks to track
        retail_stocks = ['NVDA', 'TSLA', 'AMD', 'AAPL', 'MSFT', 'META', 'GOOGL', 'AMZN', 'NFLX', 'DIS', 
                        'AVGO', 'INTC', 'MU', 'SMCI', 'PLTR', 'SOUN', 'SOFI', 'RIVN', 'LCID', 'F']
        
        headers = {
            'Authorization': f'Bearer {TRADIER_TOKEN}',
            'Accept': 'application/json'
        }
        
        url = 'https://api.tradier.com/v1/markets/quotes'
        params = {'symbols': ','.join(retail_stocks), 'greeks': 'false'}
        
        response = requests.get(url, headers=headers, params=params, timeout=10)
        
        if response.status_code != 200:
            return []
        
        data = response.json()
        quote_list = data.get('quotes', {}).get('quote', [])
        
        if not isinstance(quote_list, list):
            quote_list = [quote_list]
        
        movers = []
        for quote in quote_list:
            symbol = quote.get('symbol', '').upper()
            change_pct = quote.get('change_percentage', 0)
            
            # Only include significant movers (>2% move)
            if abs(change_pct) >= 2.0:
                # Fetch news for this stock
                catalyst = None
                try:
                    news_url = f"https://finnhub.io/api/v1/company-news"
                    news_params = {
                        "symbol": symbol,
                        "from": (datetime.now(tz=NY) - timedelta(days=1)).strftime('%Y-%m-%d'),
                        "to": datetime.now(tz=NY).strftime('%Y-%m-%d'),
                        "token": FINNHUB_TOKEN
                    }
                    news_response = requests.get(news_url, params=news_params, timeout=5)
                    
                    if news_response.status_code == 200:
                        news_items = news_response.json()
                        if news_items and len(news_items) > 0:
                            # Get most recent relevant news
                            for news in news_items[:3]:
                                headline = news.get('headline', '').lower()
                                # Identify catalyst types
                                if any(term in headline for term in ['earnings', 'beat', 'miss', 'guidance', 'revenue']):
                                    catalyst = news.get('headline', '')[:100]  # Truncate long headlines
                                    break
                                elif any(term in headline for term in ['delivery', 'production', 'sales']):
                                    catalyst = news.get('headline', '')[:100]
                                    break
                                elif any(term in headline for term in ['upgrade', 'downgrade', 'price target']):
                                    catalyst = news.get('headline', '')[:100]
                                    break
                except Exception as e:
                    logger.debug(f"Could not fetch news for {symbol}: {e}")
                
                movers.append({
                    'ticker': symbol,
                    'change_percentage': change_pct,
                    'price': quote.get('last', 0),
                    'catalyst': catalyst,
                    'volume': quote.get('volume', 0)
                })
        
        # Sort by absolute change percentage
        movers.sort(key=lambda x: abs(x['change_percentage']), reverse=True)
        
        return movers[:limit]
    except Exception as e:
        logger.error(f"Error fetching top movers with news: {e}")
        return []


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
        'headlines': [],
        'economic_events': []
    }
    
    try:
        # Fetch SPY/QQQ data from Tradier
        quotes = fetch_spy_qqq_data()
        
        if quotes:
            spy = quotes.get('SPY', {})
            qqq = quotes.get('QQQ', {})
            vix = quotes.get('VIX', {})
            
            context['spy_price'] = spy.get('last', 0)
            context['spy_change'] = spy.get('change', 0)
            context['spy_change_pct'] = spy.get('change_percentage', 0)
            context['spy_volume'] = spy.get('volume', 0)
            context['qqq_price'] = qqq.get('last', 0)
            context['qqq_change'] = qqq.get('change', 0)
            context['qqq_change_pct'] = qqq.get('change_percentage', 0)
            context['qqq_volume'] = qqq.get('volume', 0)
            context['vix_price'] = vix.get('last', 0)
            
            # Determine market direction
            if spy.get('change', 0) > 0 and qqq.get('change', 0) > 0:
                context['market_direction'] = 'bullish'
            elif spy.get('change', 0) < 0 and qqq.get('change', 0) < 0:
                context['market_direction'] = 'bearish'
            else:
                context['market_direction'] = 'mixed'
        
        # Fetch economic events from Finnhub
        econ_events = fetch_economic_data_today()
        if econ_events:
            context['economic_events'] = econ_events
        
        # Fetch top movers with news catalysts
        movers = fetch_top_movers_with_news(limit=10)
        if movers:
            gainers = [m for m in movers if m.get('change_percentage', 0) > 0]
            losers = [m for m in movers if m.get('change_percentage', 0) < 0]
            context['top_gainers'] = gainers[:5]
            context['top_losers'] = sorted(losers, key=lambda x: abs(x.get('change_percentage', 0)), reverse=True)[:5]
            
            # Find featured mover with catalyst
            for mover in movers:
                if mover.get('catalyst'):
                    context['featured_mover'] = mover
                    break
            
    except Exception as e:
        logger.error(f"Error fetching market context: {e}")

    if merge_movers is not None:
        try:
            merge_movers(context)
        except Exception as e:
            logger.warning(f"Could not persist mover telemetry: {e}")
    
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
    
    spy_price = context.get('spy_price', 0) or 0
    spy_change_pct = context.get('spy_change_pct', 0) or 0
    qqq_price = context.get('qqq_price', 0) or 0
    qqq_change_pct = context.get('qqq_change_pct', 0) or 0
    vix_price = context.get('vix_price', 0) or 0
    market_direction = context.get('market_direction', 'mixed')
    econ_events = context.get('economic_events', [])
    
    # Identify featured stock
    featured_ticker, featured_name, featured_change, featured_reason = identify_featured_stock(context, 'premarket')
    
    # Build market data context for GPT
    data_summary = f"""Market Data (Pre-Market):
- SPY: ${spy_price:.2f} ({spy_change_pct:+.2f}%)
- QQQ: ${qqq_price:.2f} ({qqq_change_pct:+.2f}%)
- VIX: {vix_price:.2f}
"""
    
    if econ_events:
        events_str = ', '.join([e['event'] for e in econ_events[:2]])
        data_summary += f"\nKey Economic Events Today: {events_str}"
    
    # Add individual stock movers with catalysts
    featured_mover = context.get('featured_mover')
    top_gainers = context.get('top_gainers', [])[:3]
    top_losers = context.get('top_losers', [])[:3]
    
    if featured_mover:
        catalyst_text = f" on {featured_mover.get('catalyst', 'news')}" if featured_mover.get('catalyst') else ""
        data_summary += f"\nFeatured Mover: {featured_mover['ticker']} {featured_mover['change_percentage']:+.2f}%{catalyst_text}"
    
    if top_gainers:
        gainers_text = ', '.join([
            f"{m['ticker']} {m['change_percentage']:+.1f}%" + 
            (f" on {m.get('catalyst', '')[:50]}" if m.get('catalyst') else "")
            for m in top_gainers
        ])
        data_summary += f"\nTop Gainers: {gainers_text}"
    
    if top_losers:
        losers_text = ', '.join([
            f"{m['ticker']} {m['change_percentage']:.1f}%" + 
            (f" on {m.get('catalyst', '')[:50]}" if m.get('catalyst') else "")
            for m in top_losers
        ])
        data_summary += f"\nTop Losers: {losers_text}"
    
    # DATA SUFFICIENCY GATE - refuse to call the LLM when we have no real movers.
    # Empty movers + ticker-mention prompt = guaranteed fabrication.
    has_movers = bool(top_gainers) or bool(top_losers) or bool(featured_mover)
    has_indices = bool(spy_price) and bool(qqq_price)

    if not has_indices:
        logger.error("No SPY/QQQ data - refusing to generate. Aborting.")
        return generate_fallback_premarket(context, None, None)

    if not has_movers:
        logger.warning("No movers data - using index-only fallback to prevent fabrication.")
        return generate_fallback_premarket(context, None, None)
    
    if not openai_client:
        logger.error("OpenAI client not configured")
        return generate_fallback_premarket(context, featured_ticker, featured_change)
    
    try:
        # Randomly select one of the 3 styles
        import random
        style_choice = random.choice(['reflective', 'risk', 'learning'])
        
        # Build style-specific prompt guidance
        if style_choice == 'reflective':
            style_guidance = """Style: Reflective Question
- Ask a thought-provoking question about trading psychology
- Reference specific market conditions (SPY/QQQ prices or percentages)
- Make traders pause and think before acting
- Human, conversational tone"""
        elif style_choice == 'risk':
            style_guidance = """Style: Risk Management
- Emphasize capital preservation and defining risk
- Reference SPY/QQQ price levels
- Remind traders to plan before the open
- Practical, protective tone"""
        else:  # learning
            style_guidance = """Style: Learning Moment
- Teach something about premarket behavior or market mechanics
- Use "Here's the thing:" or similar conversational phrase
- Share insight about liquidity, gaps, or timing
- Educational but relatable tone"""
        
        prompt = f"""You are a disciplined options trader who shares market insights on Twitter. Generate a pre-market post that:

{data_summary}

{style_guidance}

Requirements:
- Emphasize patience, discipline over impulsiveness
- Include SPY/QQQ prices/percentages and VIX naturally
- ONLY reference movers, tickers, sectors, percentages, or catalysts that appear 
  explicitly in the "Market Data" block above. If a fact is not in the data, do 
  not mention it.
- If no movers are listed in the data, write only about index levels and 
  volatility - do NOT invent or guess at individual stocks.
- Trading philosophy: "sometimes best trade is no trade"
- Focus on trend-following rather than scalping in choppy conditions
- NO hashtags in either version

Generate TWO versions:
1. LONG version (200-250 words): Full analysis with market context
2. SHORT version (under 280 characters): Concise, punchy version for Twitter

CRITICAL for SHORT version:
- Must be UNDER 280 characters (aim for 260-275)
- Include specific numbers (SPY/QQQ prices or percentages)
- Keep the core trading wisdom
- Make every word count
- DO NOT include hashtags - keep it clean and professional

Return ONLY a JSON object with this exact format:
{{
  "long": "your long version here",
  "short": "your short version here (under 280 chars)"
}}
"""
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an experienced trader creating thoughtful Twitter posts. You must return valid JSON with 'long' and 'short' fields."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON response
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].split("```")[0].strip()
        
        result = json.loads(content)
        allowed = {
            m.get('ticker', '').upper()
            for m in (top_gainers + top_losers)
            if m.get('ticker')
        }
        if featured_mover and featured_mover.get('ticker'):
            allowed.add(featured_mover.get('ticker').upper())
        
        for version_key in ('long', 'short'):
            text = result.get(version_key, '')
            is_valid, bad = validate_no_fabricated_tickers(text, allowed)
            if not is_valid:
                logger.error(
                    f"FABRICATION DETECTED in {version_key} version: "
                    f"unauthorized tickers={bad}. Refusing to send. Text: {text[:200]}"
                )
                return generate_fallback_premarket(context, None, None)
        
        # Validate short version length
        short_post = result.get('short', '')
        if len(short_post) > 280:
            logger.warning(f"Short version is {len(short_post)} characters, truncating...")
            short_post = short_post[:277] + "..."
        
        return {
            'post': short_post,  # Keep 'post' key for backward compatibility
            'long_version': result.get('long', ''),
            'short_version': short_post,
            'style': style_choice,
            'char_count': len(short_post),
            'char_count_long': len(result.get('long', '')),
            'featured_stock': featured_ticker,
            'market_summary': data_summary
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON response: {e}")
        try:
            logger.error(f"Response content: {content[:200]}")
        except NameError:
            logger.error("No response content available")
        return generate_fallback_premarket(context, featured_ticker, featured_change)
    except Exception as e:
        logger.error(f"Error generating premarket post: {e}")
        return generate_fallback_premarket(context, featured_ticker, featured_change)


def generate_postmarket_post(context: Dict[str, Any]) -> Dict[str, str]:
    """Generate post-market post with live market data.

    Primary path: the three-pass generator in `tweet_generator.py` (Polygon
    market internals -> draft -> voice rewrite -> hard validator). Falls back
    to the legacy two-version flow only if the new path is unavailable, so the
    n8n workflow keeps emitting an email even if Polygon/voice/etc. is down.
    """

    if generate_post_market_tweet is not None:
        try:
            internals_data = {}
            internals_block = ""
            if get_market_internals is not None and format_internals_for_prompt is not None:
                internals_data = get_market_internals()
                internals_block = format_internals_for_prompt(internals_data)

            tweet = generate_post_market_tweet(
                market_data_block=internals_block or None,
            )
            tweet = (tweet or "").strip()
            if tweet:
                mover_symbols = [
                    m.get("symbol", "").upper()
                    for side in ("gainers", "losers")
                    for m in (internals_data.get("movers", {}) or {}).get(side, [])
                    if m.get("symbol")
                ]
                featured_from_tweet = next(
                    (
                        symbol
                        for symbol in mover_symbols
                        if re.search(rf"(?<![A-Z])\$?{re.escape(symbol)}\b", tweet)
                    ),
                    None,
                )
                post_payload = {
                    'post': tweet,
                    'long_version': tweet,
                    'short_version': tweet,
                    'style': 'voice-validated',
                    'char_count': len(tweet),
                    'char_count_long': len(tweet),
                    'featured_stock': featured_from_tweet,
                    'market_summary': 'Generated via tweet_generator (Polygon internals + voice + validator)',
                    'market_internals': internals_data,
                    'internals_block': internals_block,
                }
                return {
                    **post_payload,
                }
            logger.warning("tweet_generator returned empty output; falling back to legacy postmarket flow")
        except Exception as e:
            logger.error(f"tweet_generator failed, falling back to legacy postmarket flow: {e}")

    spy_price = context.get('spy_price', 0) or 0
    spy_change_pct = context.get('spy_change_pct', 0) or 0
    spy_volume = context.get('spy_volume', 0) or 0
    qqq_price = context.get('qqq_price', 0) or 0
    qqq_change_pct = context.get('qqq_change_pct', 0) or 0
    qqq_volume = context.get('qqq_volume', 0) or 0
    vix_price = context.get('vix_price', 0) or 0

    market_direction = context.get('market_direction', 'mixed')
    econ_events = context.get('economic_events', [])
    
    # Identify featured stock (biggest mover of the day)
    featured_ticker, featured_name, featured_change, featured_reason = identify_featured_stock(context, 'postmarket')
    
    # Build market data context for GPT
    data_summary = f"""Market Data (Post-Market Close):
- SPY: ${spy_price:.2f} ({spy_change_pct:+.2f}%){' - volume: ' + f'{spy_volume:,}' if spy_volume > 0 else ''}
- QQQ: ${qqq_price:.2f} ({qqq_change_pct:+.2f}%){' - volume: ' + f'{qqq_volume:,}' if qqq_volume > 0 else ''}
- VIX: {vix_price:.2f}
"""
    
    if econ_events:
        events_str = ', '.join([f"{e['event']}: {e.get('actual', 'N/A')}" for e in econ_events[:2]])
        data_summary += f"\nEconomic Data: {events_str}"
    
    # Add individual stock movers with catalysts
    featured_mover = context.get('featured_mover')
    top_gainers = context.get('top_gainers', [])[:3]
    top_losers = context.get('top_losers', [])[:3]
    
    if featured_mover:
        catalyst_text = f" on {featured_mover.get('catalyst', 'news')}" if featured_mover.get('catalyst') else ""
        data_summary += f"\nFeatured Mover: {featured_mover['ticker']} {featured_mover['change_percentage']:+.2f}%{catalyst_text}"
    
    if top_gainers:
        gainers_text = ', '.join([
            f"{m['ticker']} {m['change_percentage']:+.1f}%" + 
            (f" on {m.get('catalyst', '')[:50]}" if m.get('catalyst') else "")
            for m in top_gainers
        ])
        data_summary += f"\nTop Gainers: {gainers_text}"
    
    if top_losers:
        losers_text = ', '.join([
            f"{m['ticker']} {m['change_percentage']:.1f}%" + 
            (f" on {m.get('catalyst', '')[:50]}" if m.get('catalyst') else "")
            for m in top_losers
        ])
        data_summary += f"\nTop Losers: {losers_text}"
    
    # DATA SUFFICIENCY GATE - refuse to call the LLM when we have no real movers.
    # Empty movers + ticker-mention prompt = guaranteed fabrication.
    has_movers = bool(top_gainers) or bool(top_losers) or bool(featured_mover)
    has_indices = bool(spy_price) and bool(qqq_price)

    if not has_indices:
        logger.error("No SPY/QQQ data - refusing to generate. Aborting.")
        return generate_fallback_postmarket(context, None, None)

    if not has_movers:
        logger.warning("No movers data - using index-only fallback to prevent fabrication.")
        return generate_fallback_postmarket(context, None, None)
    
    if not openai_client:
        logger.error("OpenAI client not configured")
        return generate_fallback_postmarket(context, featured_ticker, featured_change)
    
    try:
        # Randomly select one of the 3 styles
        import random
        style_choice = random.choice(['reflection', 'lesson', 'tactical'])
        
        # Build style-specific prompt guidance
        if style_choice == 'reflection':
            style_guidance = """Style: Reflection
- Acknowledge the day is done (win or lose)
- Emphasize consistency and process over results
- Supportive, growth-oriented tone"""
        elif style_choice == 'lesson':
            style_guidance = """Style: Lesson from the Day
- Extract a tactical or psychological lesson from the day's action
- Mention how market behaved (choppy, trending, volatile, etc.)
- Share what type of trader succeeded today
- Practical wisdom tone"""
        else:  # tactical
            style_guidance = """Style: Tactical Review
- Brief tactical observation about today's price action
- Note what worked vs what didn't
- Emphasize matching strategy to market type
- Analytical but accessible tone"""
        
        prompt = f"""You are a disciplined options trader who shares market insights on Twitter. Generate a post-market analysis that:

{data_summary}

{style_guidance}

Requirements:
- Emphasize patience, discipline over impulsiveness
- Include SPY/QQQ prices/percentages and VIX naturally
- ONLY reference movers, tickers, sectors, percentages, or catalysts that appear 
  explicitly in the "Market Data" block above. If a fact is not in the data, do 
  not mention it.
- If no movers are listed in the data, write only about index levels and 
  volatility - do NOT invent or guess at individual stocks.
- Trading philosophy: "sometimes best trade is no trade"
- Focus on trend-following rather than scalping in choppy conditions
- NO hashtags in either version

Generate TWO versions:
1. LONG version (200-250 words): Full analysis of the day's action
2. SHORT version (under 280 characters): Key takeaway with data

CRITICAL for SHORT version:
- Must be UNDER 280 characters (aim for 260-275)
- Include specific numbers (SPY/QQQ performance, key levels, or sector data)
- Capture the day's lesson
- Make every word count
- DO NOT include hashtags - keep it clean and professional

Return ONLY a JSON object with this exact format:
{{
  "long": "your long version here",
  "short": "your short version here (under 280 chars)"
}}
"""
        
        response = openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": "You are an experienced trader creating insightful Twitter posts. You must return valid JSON with 'long' and 'short' fields."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
            max_tokens=500
        )
        
        content = response.choices[0].message.content.strip()
        
        # Parse JSON response
        if content.startswith("```json"):
            content = content.split("```json")[1].split("```")[0].strip()
        elif content.startswith("```"):
            content = content.split("```")[1].split("```")[0].strip()
        
        result = json.loads(content)
        allowed = {
            m.get('ticker', '').upper()
            for m in (top_gainers + top_losers)
            if m.get('ticker')
        }
        if featured_mover and featured_mover.get('ticker'):
            allowed.add(featured_mover.get('ticker').upper())
        
        for version_key in ('long', 'short'):
            text = result.get(version_key, '')
            is_valid, bad = validate_no_fabricated_tickers(text, allowed)
            if not is_valid:
                logger.error(
                    f"FABRICATION DETECTED in {version_key} version: "
                    f"unauthorized tickers={bad}. Refusing to send. Text: {text[:200]}"
                )
                return generate_fallback_postmarket(context, None, None)
        
        # Validate short version length
        short_post = result.get('short', '')
        if len(short_post) > 280:
            logger.warning(f"Short version is {len(short_post)} characters, truncating...")
            short_post = short_post[:277] + "..."
        
        return {
            'post': short_post,  # Keep 'post' key for backward compatibility
            'long_version': result.get('long', ''),
            'short_version': short_post,
            'style': style_choice,
            'char_count': len(short_post),
            'char_count_long': len(result.get('long', '')),
            'featured_stock': featured_ticker,
            'market_summary': data_summary
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"Error parsing JSON response: {e}")
        try:
            logger.error(f"Response content: {content[:200]}")
        except NameError:
            logger.error("No response content available")
        return generate_fallback_postmarket(context, featured_ticker, featured_change)
    except Exception as e:
        logger.error(f"Error generating postmarket post: {e}")
        return generate_fallback_postmarket(context, featured_ticker, featured_change)


def generate_fallback_premarket(context: Dict[str, Any], ticker: str = None, change: float = None) -> Dict[str, str]:
    """Generate fallback premarket post if AI fails"""
    spy_price = context.get('spy_price', 0) or 0
    spy_change_pct = context.get('spy_change_pct', 0) or 0
    qqq_price = context.get('qqq_price', 0) or 0
    qqq_change_pct = context.get('qqq_change_pct', 0) or 0
    
    if ticker and abs(change) >= 3.0:
        short_post = f"{ticker} {change:+.2f}% premarket. Before you chase, ask yourself: Is this the start of a move or just noise? Patience often beats speed. #trading #premarket"
        long_post = f"Premarket action shows {ticker} moving {change:+.2f}%. Before you chase, ask yourself: Is this the start of a meaningful move or just noise? The disciplined trader waits for confirmation rather than jumping in on emotion. Patience often beats speed in this game. Sometimes the best trade is no trade at all."
    else:
        short_post = f"SPY at ${spy_price:.2f} ({spy_change_pct:+.2f}%), QQQ at ${qqq_price:.2f} ({qqq_change_pct:+.2f}%) premarket. Define your risk BEFORE the open, not after. The traders who survive protect capital first. #daytrading"
        long_post = f"Premarket shows SPY at ${spy_price:.2f} ({spy_change_pct:+.2f}%) and QQQ at ${qqq_price:.2f} ({qqq_change_pct:+.2f}%). Before the market opens, define your risk. The traders who survive and thrive are those who protect capital first. Don't wait until after the open to figure out your stops and position sizes. Discipline starts before the bell rings."
    
    return {
        'post': short_post,  # Keep 'post' key for backward compatibility
        'long_version': long_post,
        'short_version': short_post,
        'style': 'fallback',
        'char_count': len(short_post),
        'char_count_long': len(long_post),
        'featured_stock': ticker,
        'market_summary': 'Fallback post used'
    }


def generate_fallback_postmarket(context: Dict[str, Any], ticker: str = None, change: float = None) -> Dict[str, str]:
    """Generate fallback postmarket post if AI fails"""
    spy_price = context.get('spy_price', 0) or 0
    spy_change_pct = context.get('spy_change_pct', 0) or 0
    qqq_price = context.get('qqq_price', 0) or 0
    qqq_change_pct = context.get('qqq_change_pct', 0) or 0
    
    if ticker and abs(change) >= 3.0:
        short_post = f"{ticker} moved {change:+.2f}% today. Big moves test discipline. Did you follow your plan or chase? That's the real lesson. #trading"
        long_post = f"Today's action saw {ticker} move {change:+.2f}%. Big moves like this test a trader's discipline. Did you follow your plan, or did you chase the move? That's the real lesson here. The market will always present opportunities, but the disciplined trader knows when to act and when to sit on their hands. Process over profits, always."
    else:
        short_post = f"Market closed: SPY {spy_change_pct:+.2f}%, QQQ {qqq_change_pct:+.2f}%. Win or lose, you showed up. That consistency matters more than today's P&L. Did you follow your plan? #tradingjourney"
        long_post = f"Market closed with SPY finishing {spy_change_pct:+.2f}% and QQQ {qqq_change_pct:+.2f}%. Win or lose, you showed up today. That consistency matters more than today's P&L. Did you follow your plan? Did you stick to your process? That's what separates the professionals from the gamblers. The market will have good days and bad days, but your discipline should remain constant."
    
    return {
        'post': short_post,  # Keep 'post' key for backward compatibility
        'long_version': long_post,
        'short_version': short_post,
        'style': 'fallback',
        'char_count': len(short_post),
        'char_count_long': len(long_post),
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
    
    post = post_data.get('post') or post_data.get('short_version', '')
    long_version = post_data.get('long_version', '')
    short_version = post_data.get('short_version') or post
    style = post_data['style']
    char_count = len(short_version)
    char_count_long = len(long_version)
    word_count_long = len(long_version.split()) if long_version else 0
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
    spy_price = context.get('spy_price', 0) or 0
    spy_change_pct = context.get('spy_change_pct', 0) or 0
    qqq_price = context.get('qqq_price', 0) or 0
    qqq_change_pct = context.get('qqq_change_pct', 0) or 0
    vix_price = context.get('vix_price', 0) or 0
    internals = post_data.get('market_internals') or {}
    internals_block = post_data.get('internals_block') or ""
    use_internals = time_period == 'postmarket' and style == 'voice-validated' and bool(internals)

    if use_internals:
        sectors = internals.get('sectors') or []
        breadth = internals.get('breadth') or {}
        trend = internals.get('trend') or {}
        sector_items = ""
        if sectors:
            leaders = sectors[:3]
            laggards = list(reversed(sectors[-3:]))
            sector_items += "<li><strong>Sector Leaders:</strong> " + ", ".join(
                f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in leaders
            ) + "</li>"
            sector_items += "<li><strong>Sector Laggards:</strong> " + ", ".join(
                f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in laggards
            ) + "</li>"
        if breadth.get('advancers') is not None and breadth.get('decliners') is not None:
            breadth_text = f"{breadth['advancers']} advancers / {breadth['decliners']} decliners"
            if breadth.get('ad_ratio') is not None:
                breadth_text += f" (ratio {breadth['ad_ratio']})"
            if breadth.get('universe_size'):
                breadth_text += f" across {breadth['universe_size']:,} active names"
            sector_items += f"<li><strong>Breadth:</strong> {breadth_text}</li>"
        if trend.get('pct_above') is not None:
            direction = "above" if trend.get('above_50dma') else "below"
            sector_items += (
                f"<li><strong>Trend:</strong> SPY {direction} 50DMA by "
                f"{abs(trend['pct_above']):.2f}% (price ${trend['price']}, 50DMA ${trend['sma_50']})</li>"
            )
        market_html = f"""
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h3 style="margin: 0 0 10px 0; color: #2c3e50;">Market Internals</h3>
            <ul style="margin: 0; padding-left: 20px;">
                {sector_items}
            </ul>
        </div>
        """
    else:
        market_html = f"""
        <div style="background-color: #f8f9fa; padding: 15px; border-radius: 5px; margin: 20px 0;">
            <h3 style="margin: 0 0 10px 0; color: #2c3e50;">Market Data</h3>
            <ul style="margin: 0; padding-left: 20px;">
                <li>SPY: ${spy_price:.2f} ({spy_change_pct:+.2f}%)</li>
                <li>QQQ: ${qqq_price:.2f} ({qqq_change_pct:+.2f}%)</li>
                <li>VIX: {vix_price:.2f}</li>
                <li>Direction: {context.get('market_direction', 'mixed').upper()}</li>
            </ul>
        </div>
        """
    
    # Build movers section
    if use_internals:
        movers = internals.get('movers') or {}
        gainers = [
            {'ticker': m.get('symbol'), 'change_percentage': m.get('pct')}
            for m in (movers.get('gainers') or [])[:3]
        ]
        losers = [
            {'ticker': m.get('symbol'), 'change_percentage': m.get('pct')}
            for m in (movers.get('losers') or [])[:3]
        ]
    else:
        gainers = context.get('top_gainers', [])[:3]
        losers = context.get('top_losers', [])[:3]
    
    movers_html = ""
    if gainers or losers:
        movers_html = "<div style='background-color: #e7f3ff; padding: 15px; border-radius: 5px; margin: 20px 0;'>"
        movers_html += "<h3 style='margin: 0 0 10px 0; color: #004085;'>Top Movers</h3>"
        
        if gainers:
            movers_html += "<p style='margin: 5px 0; font-weight: bold; color: #28a745;'>Gainers:</p><ul style='margin: 5px 0; padding-left: 20px;'>"
            for g in gainers:
                catalyst_text = f" - {g.get('catalyst', '')[:60]}" if g.get('catalyst') else ""
                movers_html += f"<li style='color: #28a745;'>{g['ticker']}: +{g['change_percentage']:.2f}%{catalyst_text}</li>"
            movers_html += "</ul>"
        
        if losers:
            movers_html += "<p style='margin: 5px 0; font-weight: bold; color: #dc3545;'>Losers:</p><ul style='margin: 5px 0; padding-left: 20px;'>"
            for l in losers:
                catalyst_text = f" - {l.get('catalyst', '')[:60]}" if l.get('catalyst') else ""
                movers_html += f"<li style='color: #dc3545;'>{l['ticker']}: {l['change_percentage']:.2f}%{catalyst_text}</li>"
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
        
        <div style="background-color: #ffffff; border: 2px solid #2196f3; border-radius: 10px; padding: 25px; margin: 20px 0;">
            <h2 style="margin: 0 0 20px 0; color: #2c3e50; border-bottom: 2px solid #2196f3; padding-bottom: 10px;">
                📱 SHORT Version (Twitter-Ready)
            </h2>
            <div style="background-color: #e3f2fd; padding: 20px; border-left: 4px solid #2196f3; border-radius: 5px; font-size: 16px; line-height: 1.8; white-space: pre-wrap;">
{short_version}
            </div>
            <div style="margin-top: 15px; padding: 10px; background-color: #e7f3ff; border-radius: 5px;">
                <p style="margin: 0; font-size: 14px; color: #004085;">
                    <strong>Style:</strong> {style.title()} | 
                    <strong>Characters:</strong> {char_count}/280 | 
                    <strong>Status:</strong> {'✅ Ready to post' if char_count <= 280 else '⚠️ Too long'}
                </p>
            </div>
        </div>
        
        <div style="background-color: #ffffff; border: 2px solid #4caf50; border-radius: 10px; padding: 25px; margin: 20px 0;">
            <h2 style="margin: 0 0 20px 0; color: #2c3e50; border-bottom: 2px solid #4caf50; padding-bottom: 10px;">
                📄 LONG Version (Thread/Article)
            </h2>
            <div style="background-color: #e8f5e9; padding: 20px; border-left: 4px solid #4caf50; border-radius: 5px; font-size: 15px; line-height: 1.8; white-space: pre-wrap;">
{long_version if long_version else 'Long version not available'}
            </div>
            <div style="margin-top: 15px; padding: 10px; background-color: #e8f5e9; border-radius: 5px;">
                <p style="margin: 0; font-size: 14px; color: #2e7d32;">
                    <strong>Words:</strong> {word_count_long} | 
                    <strong>Characters:</strong> {char_count_long} | 
                    <strong>Use for:</strong> Twitter threads, newsletter, blog posts
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
    internals = post_data.get('market_internals') or {}
    use_internals = time_period == 'postmarket' and style == 'voice-validated' and bool(internals)

    if use_internals:
        sectors = internals.get('sectors') or []
        breadth = internals.get('breadth') or {}
        trend = internals.get('trend') or {}
        movers = internals.get('movers') or {}
        market_lines = ["MARKET INTERNALS:"]
        if sectors:
            leaders = sectors[:3]
            laggards = list(reversed(sectors[-3:]))
            market_lines.append(
                "- Sector Leaders: " + ", ".join(
                    f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in leaders
                )
            )
            market_lines.append(
                "- Sector Laggards: " + ", ".join(
                    f"{s['name']} ({s['etf']}) {s['pct']:+.2f}%" for s in laggards
                )
            )
        if breadth.get('advancers') is not None and breadth.get('decliners') is not None:
            breadth_text = f"{breadth['advancers']} advancers / {breadth['decliners']} decliners"
            if breadth.get('ad_ratio') is not None:
                breadth_text += f" (ratio {breadth['ad_ratio']})"
            if breadth.get('universe_size'):
                breadth_text += f" across {breadth['universe_size']:,} active names"
            market_lines.append(f"- Breadth: {breadth_text}")
        if trend.get('pct_above') is not None:
            direction = "above" if trend.get('above_50dma') else "below"
            market_lines.append(
                f"- Trend: SPY {direction} 50DMA by {abs(trend['pct_above']):.2f}% "
                f"(price ${trend['price']}, 50DMA ${trend['sma_50']})"
            )
        market_section = "\n".join(market_lines)
        gainers_for_text = [
            {'ticker': m.get('symbol'), 'change_percentage': m.get('pct')}
            for m in (movers.get('gainers') or [])[:3]
        ]
        losers_for_text = [
            {'ticker': m.get('symbol'), 'change_percentage': m.get('pct')}
            for m in (movers.get('losers') or [])[:3]
        ]
    else:
        market_section = f"""MARKET DATA:
- SPY: ${(context.get('spy_price', 0) or 0):.2f} ({(context.get('spy_change_pct', 0) or 0):+.2f}%)
- QQQ: ${(context.get('qqq_price', 0) or 0):.2f} ({(context.get('qqq_change_pct', 0) or 0):+.2f}%)
- VIX: {(context.get('vix_price', 0) or 0):.2f}
- Direction: {context.get('market_direction', 'mixed').upper()}"""
        gainers_for_text = context.get('top_gainers', [])[:3]
        losers_for_text = context.get('top_losers', [])[:3]
    
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

{market_section}

TOP GAINERS:
"""
    
    for g in gainers_for_text:
        text += f"  {g['ticker']}: +{g['change_percentage']:.2f}%\n"
    
    text += "\nTOP LOSERS:\n"
    for l in losers_for_text:
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
        if record_sent_post is not None:
            try:
                record_sent_post(time_period, post_data, context)
                logger.info("Recorded %s post in validation_telemetry", time_period)
            except Exception as e:
                logger.warning(f"Could not record sent post telemetry: {e}")
        logger.info("✅ Twitter post email sent successfully!")
        print(f"\nSUCCESS: {time_period.title()} post emailed to {TO_EMAIL}")
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

