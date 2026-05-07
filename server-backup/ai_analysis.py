"""
Options Plunge Analysis Module

This module provides AI-powered analysis of trading performance using OpenAI's GPT models.
It analyzes individual trades, daily performance, and provides actionable feedback.
"""

from openai import OpenAI
import os
from datetime import datetime, timedelta
import json
import re
from models import TradeAnalysis, db
from io import BytesIO
from PIL import Image


class TradingAIAnalyzer:
    """AI-powered trading analysis using OpenAI GPT models"""

    def __init__(self):
        """Initialize the AI analyzer without enforcing the API key."""
        self.api_key = os.getenv("OPENAI_API_KEY")
        self.model = "gpt-5-nano"  # Default AI model for analysis
        self.client = None

    def _ensure_api_key(self):
        """Validate the OpenAI API key. Return True if available."""
        print(f"DEBUG: _ensure_api_key called, current api_key: {self.api_key[:10] if self.api_key else 'None'}...")
        if not self.api_key:
            self.api_key = os.getenv("OPENAI_API_KEY")
            print(f"DEBUG: Got api_key from env: {self.api_key[:10] if self.api_key else 'None'}...")
        if not self.api_key:
            print("DEBUG: No API key found")
            return False
        if self.client is None:
            print("DEBUG: Creating OpenAI client")
            try:
                # Clear any proxy environment variables before creating client
                import os
                proxy_vars = ['HTTP_PROXY', 'HTTPS_PROXY', 'http_proxy', 'https_proxy', 'proxies']
                for var in proxy_vars:
                    if var in os.environ:
                        print(f"DEBUG: Removing {var} from environment")
                        del os.environ[var]
                
                # Create client with only the API key
                print(f"DEBUG: API key length: {len(self.api_key) if self.api_key else 0}")
                print(f"DEBUG: API key starts with: {self.api_key[:10] if self.api_key else 'None'}...")
                
                # Try creating client with minimal parameters
                client_kwargs = {'api_key': self.api_key}
                print(f"DEBUG: Client kwargs: {client_kwargs}")
                
                # Try to isolate the client creation
                import openai
                print(f"DEBUG: OpenAI module version: {openai.__version__}")
                print(f"DEBUG: OpenAI module path: {openai.__file__}")
                
                # Try creating client with explicit parameters and no kwargs
                print("DEBUG: Attempting to create client with explicit parameters")
                
                # Let's inspect the OpenAI client class
                print(f"DEBUG: OpenAI client class: {openai.OpenAI}")
                print(f"DEBUG: OpenAI client __init__ signature: {openai.OpenAI.__init__.__code__.co_varnames}")
                
                # Try creating client with minimal parameters
                try:
                    # Check if there are any global configurations affecting the client
                    print("DEBUG: Checking for global configurations...")
                    
                    # Try a different approach - use requests directly if OpenAI client fails
                    print("DEBUG: Attempting to create OpenAI client...")
                    self.client = openai.OpenAI(api_key=self.api_key)
                    print("DEBUG: OpenAI client created successfully")
                    
                except TypeError as e:
                    print(f"DEBUG: TypeError with OpenAI client: {e}")
                    print("DEBUG: Falling back to direct API calls using requests")
                    
                    # Create a mock client that uses requests directly
                    class MockOpenAIClient:
                        def __init__(self, api_key):
                            self.api_key = api_key
                            self.base_url = "https://api.openai.com/v1"
                        
                        @property
                        def chat(self):
                            return MockChatCompletions(self.api_key, self.base_url)
                    
                    class MockChatCompletions:
                        def __init__(self, api_key, base_url):
                            self.api_key = api_key
                            self.base_url = base_url
                        
                        @property
                        def completions(self):
                            return self
                        
                        def create(self, **kwargs):
                            import requests
                            import json
                            
                            headers = {
                                "Authorization": f"Bearer {self.api_key}",
                                "Content-Type": "application/json"
                            }
                            
                            response = requests.post(
                                f"{self.base_url}/chat/completions",
                                headers=headers,
                                json=kwargs,
                                timeout=30
                            )
                            
                            if response.status_code == 200:
                                return MockResponse(response.json())
                            else:
                                raise Exception(f"API request failed: {response.status_code} - {response.text}")
                    
                    class MockResponse:
                        def __init__(self, data):
                            self.data = data
                        
                        @property
                        def choices(self):
                            return [MockChoice(self.data.get('choices', [{}])[0])]
                    
                    class MockChoice:
                        def __init__(self, choice_data):
                            self.choice_data = choice_data
                        
                        @property
                        def message(self):
                            return MockMessage(self.choice_data.get('message', {}))
                    
                    class MockMessage:
                        def __init__(self, message_data):
                            self.message_data = message_data
                        
                        @property
                        def content(self):
                            return self.message_data.get('content', '')
                    
                    self.client = MockOpenAIClient(self.api_key)
                    print("DEBUG: Mock OpenAI client created successfully")
            except Exception as e:
                print(f"DEBUG: Error creating OpenAI client: {str(e)}")
                print(f"DEBUG: Error type: {type(e)}")
                raise e
        print("DEBUG: API key validation successful")
        return True

    def analyze_trade(self, trade):
        """
        Analyze a single trade and provide detailed feedback

        Args:
            trade: Trade object from the database

        Returns:
            TradeAnalysis object or None if analysis fails
        """
        try:
            print(f"DEBUG: analyze_trade called for trade {trade.id}")
            if not self._ensure_api_key():
                print("DEBUG: API key validation failed, returning error dict")
                return {"error": "OPENAI_API_KEY environment variable not set. Please add your OpenAI API key to the .env file."}
            print("DEBUG: API key validation passed, proceeding with analysis")
            # Prepare trade data for analysis
            trade_data = self._prepare_trade_data(trade)

            # Generate analysis prompt
            prompt = self._create_trade_analysis_prompt(trade_data)

            # Try to include chart images if available
            entry_image_filename = getattr(trade, "entry_chart_image", None)
            exit_image_filename = getattr(trade, "exit_chart_image", None)

            entry_data_url = None
            exit_data_url = None

            if entry_image_filename:
                entry_data_url = self._prepare_image_data_url(entry_image_filename)
            if exit_image_filename:
                exit_data_url = self._prepare_image_data_url(exit_image_filename)

            using_images = bool(entry_data_url or exit_data_url)

            if using_images:
                # Compose mixed content with images for a vision-capable model
                content_parts = [
                    {
                        "type": "text",
                        "text": (
                            "You are a trading coach. Analyze the trade using BOTH the structured fields "
                            "and the chart images if provided. Prioritize identifying timeframe, trend, key levels, patterns, "
                            "indicator states, entry/exit quality, and improvements."
                        ),
                    },
                    {"type": "text", "text": prompt},
                ]

                if entry_data_url:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": entry_data_url},
                    })
                if exit_data_url:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": exit_data_url},
                    })

                # Use a vision-capable model when images are present
                vision_model = os.getenv("ANALYSIS_VISION_MODEL", "gpt-5-nano")
                response = self.client.chat.completions.create(
                    model=vision_model,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt()},
                        {"role": "user", "content": content_parts},
                    ],
                    max_tokens=2000,
                    temperature=0.7,
                )
            else:
                # Text-only analysis (existing behavior)
                response = self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": self._get_system_prompt()},
                        {"role": "user", "content": prompt},
                    ],
                    max_tokens=2000,
                    temperature=0.7,
                )

            analysis_text = response.choices[0].message.content

            # Parse the analysis
            parsed_analysis = self._parse_analysis(analysis_text)

            # Create or update TradeAnalysis record
            analysis = TradeAnalysis.query.filter_by(trade_id=trade.id).first()
            if not analysis:
                analysis = TradeAnalysis(
                    trade_id=trade.id, user_id=trade.user_id, ai_model_used=self.model
                )

            # Update analysis fields
            analysis.overall_score = parsed_analysis.get("overall_score", 5)
            analysis.entry_analysis = parsed_analysis.get("entry_analysis", "")
            analysis.exit_analysis = parsed_analysis.get("exit_analysis", "")
            analysis.risk_analysis = parsed_analysis.get("risk_analysis", "")
            analysis.market_context = parsed_analysis.get("market_context", "")
            analysis.options_analysis = parsed_analysis.get("options_analysis", "")

            # Set JSON fields
            analysis.set_strengths(parsed_analysis.get("strengths", []))
            analysis.set_weaknesses(parsed_analysis.get("weaknesses", []))
            analysis.set_improvement_areas(parsed_analysis.get("improvement_areas", []))
            analysis.set_actionable_drills(parsed_analysis.get("actionable_drills", []))
            analysis.set_recommendations(parsed_analysis.get("recommendations", []))
            analysis.set_key_lessons(parsed_analysis.get("key_lessons", []))
            analysis.set_future_setups(parsed_analysis.get("future_setups", []))

            # Save to database
            db.session.add(analysis)
            trade.is_analyzed = True
            db.session.commit()

            return analysis

        except Exception as e:
            print(f"DEBUG: Exception in analyze_trade: {str(e)}")
            print(f"DEBUG: Exception type: {type(e)}")
            return None

    def _prepare_image_data_url(self, filename: str) -> str | None:
        """Load an uploaded image from static/uploads and return a base64 data URL.

        If the image is wider than 1600px, downscale to 1280px width to reduce tokens.
        Returns None if the file cannot be loaded.
        """
        try:
            # Resolve absolute path in uploads
            uploads_dir = os.path.join(os.getcwd(), "static", "uploads")
            image_path = os.path.join(uploads_dir, filename)

            if not os.path.exists(image_path):
                print(f"DEBUG: Image not found at path: {image_path}")
                return None

            # Open and optionally downscale
            with Image.open(image_path) as img:
                img = img.convert("RGB")
                width, height = img.size
                if width and width > 1600:
                    # Maintain aspect ratio; target max width 1280
                    img.thumbnail((1280, int(1280 * height / max(width, 1))), Image.LANCZOS)

                # Encode to PNG in-memory
                buffer = BytesIO()
                img.save(buffer, format="PNG", optimize=True)
                buffer.seek(0)

            import base64
            b64 = base64.b64encode(buffer.read()).decode("utf-8")
            return f"data:image/png;base64,{b64}"
        except Exception as e:
            print(f"DEBUG: Failed to prepare image data URL for {filename}: {e}")
            return None

    def analyze_daily_performance(self, journal_entry, trades):
        """
        Analyze daily trading performance

        Args:
            journal_entry: TradingJournal object
            trades: List of Trade objects for the day

        Returns:
            Dict with analysis results
        """
        try:
            if not self._ensure_api_key():
                return {"error": "OPENAI_API_KEY environment variable not set. Please add your OpenAI API key to the .env file."}
            # Prepare daily data
            daily_data = self._prepare_daily_data(journal_entry, trades)

            # Generate daily analysis prompt
            prompt = self._create_daily_analysis_prompt(daily_data)

            # Get AI analysis
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._get_daily_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
                temperature=0.7,
            )

            analysis_text = response.choices[0].message.content

            # Parse daily analysis
            parsed_analysis = self._parse_daily_analysis(analysis_text)

            return {
                "feedback": analysis_text,
                "daily_score": parsed_analysis.get("daily_score", 5),
                "key_insights": parsed_analysis.get("key_insights", []),
                "tomorrow_focus": parsed_analysis.get("tomorrow_focus", []),
            }

        except Exception as e:
            print(f"Error analyzing daily performance: {str(e)}")
            return None

    def analyze_planned_trade(self, trade, user_settings=None):
        """
        Analyze a planned trade and provide entry/exit recommendations

        Args:
            trade: Trade object (planned trade)
            user_settings: UserSettings object for risk defaults

        Returns:
            TradeAnalysis object or None if analysis fails
        """
        try:
            print(f"DEBUG: analyze_planned_trade called for trade {trade.id}")
            if not self._ensure_api_key():
                print("DEBUG: API key validation failed, returning error dict")
                return {"error": "OPENAI_API_KEY environment variable not set. Please add your OpenAI API key to the .env file."}
            print("DEBUG: API key validation passed, proceeding with planned trade analysis")
            
            # Get current market price for context
            current_price = trade.get_current_market_price()
            
            # Prepare planned trade data for analysis
            planned_data = self._prepare_planned_trade_data(trade, user_settings, current_price)

            # Generate analysis prompt
            prompt = self._create_planned_trade_analysis_prompt(planned_data)

            # Get AI analysis
            response = self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": self._get_planned_trade_system_prompt()},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=2000,
                temperature=0.7,
            )

            analysis_text = response.choices[0].message.content

            # Parse the analysis
            parsed_analysis = self._parse_planned_analysis(analysis_text)

            # Create or update TradeAnalysis record
            analysis = TradeAnalysis.query.filter_by(trade_id=trade.id).first()
            if not analysis:
                analysis = TradeAnalysis(
                    trade_id=trade.id, user_id=trade.user_id, ai_model_used=self.model
                )

            # Update analysis fields with planned trade specific data
            analysis.overall_score = parsed_analysis.get("overall_score", 5)
            analysis.entry_analysis = parsed_analysis.get("entry_analysis", "")
            analysis.exit_analysis = parsed_analysis.get("exit_analysis", "")
            analysis.risk_analysis = parsed_analysis.get("risk_analysis", "")
            analysis.market_context = parsed_analysis.get("market_context", "")
            analysis.options_analysis = parsed_analysis.get("options_analysis", "")

            # Set JSON fields for planned trade recommendations
            analysis.set_recommendations(parsed_analysis.get("recommendations", []))
            analysis.set_key_lessons(parsed_analysis.get("key_lessons", []))
            analysis.set_future_setups(parsed_analysis.get("future_setups", []))

            # Save to database
            db.session.add(analysis)
            db.session.commit()

            return analysis

        except Exception as e:
            print(f"DEBUG: Exception in analyze_planned_trade: {str(e)}")
            print(f"DEBUG: Exception type: {type(e)}")
            return None

    def _prepare_trade_data(self, trade):
        """Prepare trade data for AI analysis"""
        data = {
            "symbol": trade.symbol,
            "trade_type": trade.trade_type,
            "entry_date": (
                trade.entry_date.strftime("%Y-%m-%d %H:%M")
                if trade.entry_date
                else None
            ),
            "exit_date": (
                trade.exit_date.strftime("%Y-%m-%d %H:%M") if trade.exit_date else None
            ),
            "entry_price": trade.entry_price,
            "exit_price": trade.exit_price,
            "quantity": trade.quantity,
            "profit_loss": trade.profit_loss,
            "profit_loss_percent": trade.profit_loss_percent,
            "setup_type": trade.setup_type,
            "market_condition": trade.market_condition,
            "timeframe": trade.timeframe,
            "entry_reason": trade.entry_reason,
            "exit_reason": trade.exit_reason,
            "notes": trade.notes,
            "tags": trade.tags,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "risk_amount": trade.risk_amount,
            "hold_time": str(trade.get_hold_time()) if trade.exit_date else None,
            "risk_reward_ratio": trade.get_risk_reward_ratio(),
            "is_winner": trade.is_winner() if trade.exit_price else None,
        }

        # Add options-specific data
        if trade.is_option_trade():
            data.update(
                {
                    "is_option_trade": True,
                    "is_spread_trade": trade.is_spread_trade(),
                    "strike_price": trade.strike_price,
                    "expiration_date": (
                        trade.expiration_date.strftime("%Y-%m-%d")
                        if trade.expiration_date
                        else None
                    ),
                    "option_type": trade.option_type,
                    "premium_paid": trade.premium_paid,
                    "implied_volatility": trade.implied_volatility,
                    "underlying_price_at_entry": trade.underlying_price_at_entry,
                    "underlying_price_at_exit": trade.underlying_price_at_exit,
                    "days_to_expiration": trade.get_days_to_expiration(),
                    "moneyness": trade.get_moneyness(),
                    "intrinsic_value": trade.get_intrinsic_value(),
                    "time_value": trade.get_time_value(),
                    "delta": trade.delta,
                    "gamma": trade.gamma,
                    "theta": trade.theta,
                    "vega": trade.vega,
                }
            )

            # Add spread-specific data
            if trade.is_spread_trade():
                data.update(
                    {
                        "spread_type": trade.spread_type,
                        "long_strike": trade.long_strike,
                        "short_strike": trade.short_strike,
                        "long_premium": trade.long_premium,
                        "short_premium": trade.short_premium,
                        "net_credit": trade.net_credit,
                        "max_profit": trade.max_profit,
                        "max_loss": trade.max_loss,
                        "breakeven_price": trade.breakeven_price,
                    }
                )

        return data

    def _prepare_planned_trade_data(self, trade, user_settings, current_price):
        """Prepare planned trade data for AI analysis"""
        data = {
            "symbol": trade.symbol,
            "trade_type": trade.trade_type,
            "setup_type": trade.setup_type,
            "market_condition": trade.market_condition,
            "timeframe": trade.timeframe,
            "entry_reason": trade.entry_reason,
            "notes": trade.notes,
            "tags": trade.tags,
            "stop_loss": trade.stop_loss,
            "take_profit": trade.take_profit,
            "risk_amount": trade.risk_amount,
            "current_price": current_price,
        }

        # Add user risk defaults
        if user_settings:
            data.update({
                "account_size": user_settings.account_size,
                "default_risk_percent": user_settings.default_risk_percent,
                "max_daily_loss": user_settings.max_daily_loss,
                "max_position_size": user_settings.max_position_size,
            })

        # Add options-specific data if applicable
        if trade.is_option_trade():
            data.update({
                "is_option_trade": True,
                "is_spread_trade": trade.is_spread_trade(),
                "strike_price": trade.strike_price,
                "expiration_date": (
                    trade.expiration_date.strftime("%Y-%m-%d")
                    if trade.expiration_date
                    else None
                ),
                "option_type": trade.option_type,
                "premium_paid": trade.premium_paid,
                "implied_volatility": trade.implied_volatility,
                "underlying_price_at_entry": trade.underlying_price_at_entry,
                "days_to_expiration": trade.get_days_to_expiration(),
                "delta": trade.delta,
                "gamma": trade.gamma,
                "theta": trade.theta,
                "vega": trade.vega,
            })

            # Add spread-specific data
            if trade.is_spread_trade():
                data.update({
                    "spread_type": trade.spread_type,
                    "long_strike": trade.long_strike,
                    "short_strike": trade.short_strike,
                    "long_premium": trade.long_premium,
                    "short_premium": trade.short_premium,
                    "net_credit": trade.net_credit,
                    "max_profit": trade.max_profit,
                    "max_loss": trade.max_loss,
                    "breakeven_price": trade.breakeven_price,
                })

        return data

    def _prepare_daily_data(self, journal_entry, trades):
        """Prepare daily data for AI analysis"""
        return {
            "date": journal_entry.journal_date.strftime("%Y-%m-%d"),
            "daily_pnl": journal_entry.daily_pnl,
            "market_outlook": journal_entry.market_outlook,
            "daily_goals": journal_entry.daily_goals,
            "what_went_well": journal_entry.what_went_well,
            "what_went_wrong": journal_entry.what_went_wrong,
            "emotional_state": journal_entry.emotional_state,
            "stress_level": journal_entry.stress_level,
            "discipline_score": journal_entry.discipline_score,
            "market_trend": journal_entry.market_trend,
            "volatility": journal_entry.volatility,
            "trades_count": len(trades),
            "winning_trades": len([t for t in trades if t.is_winner()]),
            "losing_trades": len(
                [t for t in trades if t.profit_loss is not None and t.profit_loss < 0]
            ),
            "total_pnl": sum(t.profit_loss for t in trades if t.profit_loss is not None),
            "trade_types": [t.trade_type for t in trades],
            "setups": [t.setup_type for t in trades if t.setup_type],
        }

    def _get_system_prompt(self):
        """Get the system prompt for trade analysis"""
        return """You are a world-class day trader and trading coach with decades of experience in stocks, options, and derivatives trading. Your role is to provide detailed, actionable analysis of individual trades to help traders improve their performance and refine their entry and exit techniques. Offer guidance as if mentoring them on future setups.

Key areas to analyze:
1. ENTRY ANALYSIS: Timing, setup quality, market context, risk/reward assessment
2. EXIT ANALYSIS: Exit timing, profit-taking vs stop-loss execution, missed opportunities
3. RISK MANAGEMENT: Position sizing, stop-loss placement, risk/reward ratios
4. OPTIONS ANALYSIS: For options trades, analyze Greeks, time decay, volatility, moneyness
5. SPREAD ANALYSIS: For credit spreads, analyze strike selection, credit received, risk management
6. MARKET CONTEXT: How well the trade aligned with market conditions
7. PSYCHOLOGY: Emotional factors, discipline, plan adherence

Provide specific, actionable feedback that helps the trader improve. Be constructive but honest about mistakes. Focus on education and skill development."""

    def _get_daily_system_prompt(self):
        """Get the system prompt for daily analysis"""
        return """You are a world-class day trader reviewing daily performance. Focus on execution quality, emotional control, and how well the trader adapted to market conditions. Provide actionable areas for improvement and tips for the next trading day."""

    def _get_planned_trade_system_prompt(self):
        """Get the system prompt for planned trade analysis"""
        return """You are a world-class trading coach and strategist with decades of experience in stocks, options, and derivatives trading. Your role is to analyze planned trades and provide specific entry/exit recommendations, risk management guidance, and position sizing advice.

Key areas to focus on:
1. ENTRY STRATEGY: Specific entry zones, timing, and price levels based on technical analysis
2. RISK MANAGEMENT: Stop loss placement, position sizing, and risk/reward ratios
3. EXIT STRATEGY: Take profit levels, trailing stops, and exit conditions
4. MARKET CONTEXT: How the setup aligns with current market conditions
5. POSITION SIZING: Recommended position size based on account size and risk tolerance
6. OPTIONS ANALYSIS: For options trades, analyze strike selection, expiration, and Greeks

Provide actionable, specific recommendations that the trader can implement immediately. Be practical and focus on risk management first."""

    def _create_trade_analysis_prompt(self, trade_data):
        """Create the analysis prompt for a single trade"""
        prompt = f"""Analyze this trade in detail:

TRADE SUMMARY:
- Symbol: {trade_data['symbol']}
- Type: {trade_data['trade_type']}
- Entry: ${trade_data['entry_price']} on {trade_data['entry_date']}
- Exit: ${trade_data['exit_price']} on {trade_data['exit_date']} (if closed)
- Quantity: {trade_data['quantity']}
- P&L: ${trade_data['profit_loss']} ({trade_data['profit_loss_percent']}%)
- Setup: {trade_data['setup_type']}
- Market Condition: {trade_data['market_condition']}
- Timeframe: {trade_data['timeframe']}
- Hold Time: {trade_data['hold_time']}

TRADE REASONING:
Entry Reason: {trade_data['entry_reason']}
Exit Reason: {trade_data['exit_reason']}
Notes: {trade_data['notes']}
Tags: {trade_data['tags']}

RISK MANAGEMENT:
- Stop Loss: ${trade_data['stop_loss']}
- Take Profit: ${trade_data['take_profit']}
- Risk Amount: ${trade_data['risk_amount']}
- Risk/Reward Ratio: {trade_data['risk_reward_ratio']}
"""

        # Add options-specific analysis
        if trade_data.get("is_option_trade"):
            prompt += f"""
OPTIONS DETAILS:
- Strike Price: ${trade_data['strike_price']}
- Expiration: {trade_data['expiration_date']}
- Option Type: {trade_data['option_type']}
- Premium Paid: ${trade_data['premium_paid']}
- Implied Volatility: {trade_data['implied_volatility']}%
- Underlying at Entry: ${trade_data['underlying_price_at_entry']}
- Underlying at Exit: ${trade_data['underlying_price_at_exit']}
- Days to Expiration: {trade_data['days_to_expiration']}
- Moneyness: {trade_data['moneyness']}
- Intrinsic Value: ${trade_data['intrinsic_value']}
- Time Value: ${trade_data['time_value']}
- Delta: {trade_data['delta']}
- Gamma: {trade_data['gamma']}
- Theta: {trade_data['theta']}
- Vega: {trade_data['vega']}
"""

        # Add spread-specific analysis
        if trade_data.get("is_spread_trade"):
            prompt += f"""
SPREAD DETAILS:
- Spread Type: {trade_data['spread_type']}
- Short Strike: ${trade_data['short_strike']} (Premium: ${trade_data['short_premium']})
- Long Strike: ${trade_data['long_strike']} (Premium: ${trade_data['long_premium']})
- Net Credit: ${trade_data['net_credit']}
- Max Profit: ${trade_data['max_profit']}
- Max Loss: ${trade_data['max_loss']}
- Breakeven: ${trade_data['breakeven_price']}
"""

        prompt += """
Please provide a comprehensive analysis with:

1. OVERALL SCORE (1-10): Rate the trade execution quality
2. STRENGTHS: What was done well (list 2-4 points)
3. WEAKNESSES: What could be improved (list 2-4 points)
4. ENTRY ANALYSIS: Detailed analysis of entry timing and setup
5. EXIT ANALYSIS: Analysis of exit execution (if applicable)
6. RISK ANALYSIS: Assessment of risk management
7. MARKET CONTEXT: How well the trade fit market conditions
8. OPTIONS ANALYSIS: Options-specific feedback (if applicable)
9. IMPROVEMENT AREAS: Specific areas to focus on (list 2-3 points)
10. ACTIONABLE DRILLS: Specific exercises to improve (list 2-3 points)
11. RECOMMENDATIONS: Specific advice for similar future trades
12. KEY LESSONS: Main takeaways from this trade
13. FUTURE SETUPS: Example entry and exit ideas for similar setups

Format your response clearly with section headers."""

        return prompt

    def _create_planned_trade_analysis_prompt(self, planned_data):
        """Create the analysis prompt for a planned trade"""
        prompt = f"""Analyze this planned trade and provide specific entry/exit recommendations:

PLANNED TRADE SUMMARY:
- Symbol: {planned_data['symbol']}
- Type: {planned_data['trade_type']}
- Setup: {planned_data['setup_type']}
- Timeframe: {planned_data['timeframe']}
- Market Condition: {planned_data['market_condition']}
- Current Price: ${planned_data['current_price']:.2f} (if available)

TRADE REASONING:
Entry Reason: {planned_data['entry_reason']}
Notes: {planned_data['notes']}
Tags: {planned_data['tags']}

RISK MANAGEMENT:
- Planned Stop Loss: ${planned_data['stop_loss'] or 'Not set'}
- Planned Take Profit: ${planned_data['take_profit'] or 'Not set'}
- Risk Amount: ${planned_data['risk_amount'] or 'Not set'}

USER SETTINGS:
- Account Size: ${planned_data.get('account_size', 'Not set')}
- Default Risk %: {planned_data.get('default_risk_percent', 'Not set')}%
- Max Daily Loss: ${planned_data.get('max_daily_loss', 'Not set')}
- Max Position Size: ${planned_data.get('max_position_size', 'Not set')}
"""

        # Add options-specific analysis
        if planned_data.get("is_option_trade"):
            prompt += f"""
OPTIONS DETAILS:
- Strike Price: ${planned_data['strike_price'] or 'Not set'}
- Expiration: {planned_data['expiration_date'] or 'Not set'}
- Option Type: {planned_data['option_type'] or 'Not set'}
- Premium: ${planned_data['premium_paid'] or 'Not set'}
- Implied Volatility: {planned_data['implied_volatility'] or 'Not set'}%
- Days to Expiration: {planned_data['days_to_expiration'] or 'Not set'}
- Delta: {planned_data['delta'] or 'Not set'}
- Gamma: {planned_data['gamma'] or 'Not set'}
- Theta: {planned_data['theta'] or 'Not set'}
- Vega: {planned_data['vega'] or 'Not set'}
"""

        # Add spread-specific analysis
        if planned_data.get("is_spread_trade"):
            prompt += f"""
SPREAD DETAILS:
- Spread Type: {planned_data['spread_type']}
- Short Strike: ${planned_data['short_strike'] or 'Not set'}
- Long Strike: ${planned_data['long_strike'] or 'Not set'}
- Net Credit: ${planned_data['net_credit'] or 'Not set'}
- Max Profit: ${planned_data['max_profit'] or 'Not set'}
- Max Loss: ${planned_data['max_loss'] or 'Not set'}
- Breakeven: ${planned_data['breakeven_price'] or 'Not set'}
"""

        prompt += """
Please provide a comprehensive trading plan with:

1. ENTRY STRATEGY: Specific entry zones, price levels, and timing
2. STOP LOSS PLACEMENT: Where to place stops and why
3. TAKE PROFIT LEVELS: 1R, 2R, 3R targets with specific prices
4. POSITION SIZING: Recommended quantity based on account size and risk
5. RISK ANALYSIS: Risk/reward ratio and risk management
6. MARKET CONTEXT: How this setup fits current market conditions
7. OPTIONS ANALYSIS: Strike selection and Greeks analysis (if applicable)
8. RECOMMENDATIONS: Specific actionable advice for execution
9. KEY LESSONS: Important considerations for this trade
10. FUTURE SETUPS: Similar setups to watch for

Format your response clearly with section headers and specific price levels where possible."""

        return prompt

    def _create_daily_analysis_prompt(self, daily_data):
        """Create the analysis prompt for daily performance"""
        return f"""Analyze this trading day:

DATE: {daily_data['date']}
DAILY P&L: ${daily_data['daily_pnl']}
TRADES: {daily_data['trades_count']} total ({daily_data['winning_trades']} wins, {daily_data['losing_trades']} losses)

MORNING PLAN:
Market Outlook: {daily_data['market_outlook']}
Daily Goals: {daily_data['daily_goals']}

END OF DAY REFLECTION:
What Went Well: {daily_data['what_went_well']}
What Went Wrong: {daily_data['what_went_wrong']}

PSYCHOLOGY:
Emotional State: {daily_data['emotional_state']}
Stress Level: {daily_data['stress_level']}/10
Discipline Score: {daily_data['discipline_score']}/10

MARKET CONDITIONS:
Trend: {daily_data['market_trend']}
Volatility: {daily_data['volatility']}

TRADE TYPES: {', '.join(daily_data['trade_types'])}
SETUPS: {', '.join(daily_data['setups'])}

Provide feedback on:
1. Overall execution quality
2. Emotional management
3. Plan adherence
4. Market adaptation
5. Areas for tomorrow's improvement
6. Daily score (1-10)

Be specific and actionable."""

    def _parse_analysis(self, analysis_text):
        """Parse the AI analysis response into structured data"""
        parsed = {}

        # Extract overall score
        score_match = re.search(
            r"(?:OVERALL SCORE|SCORE).*?(\d+)", analysis_text, re.IGNORECASE
        )
        if score_match:
            parsed["overall_score"] = int(score_match.group(1))

        # Extract sections using regex
        sections = {
            "strengths": r"STRENGTHS?:?\s*(.*?)(?=\n\d+\.|WEAKNESSES?|$)",
            "weaknesses": r"WEAKNESSES?:?\s*(.*?)(?=\n\d+\.|ENTRY ANALYSIS|$)",
            "entry_analysis": r"ENTRY ANALYSIS:?\s*(.*?)(?=\n\d+\.|EXIT ANALYSIS|$)",
            "exit_analysis": r"EXIT ANALYSIS:?\s*(.*?)(?=\n\d+\.|RISK ANALYSIS|$)",
            "risk_analysis": r"RISK ANALYSIS:?\s*(.*?)(?=\n\d+\.|MARKET CONTEXT|$)",
            "market_context": r"MARKET CONTEXT:?\s*(.*?)(?=\n\d+\.|OPTIONS ANALYSIS|$)",
            "options_analysis": r"OPTIONS ANALYSIS:?\s*(.*?)(?=\n\d+\.|IMPROVEMENT|$)",
            "improvement_areas": r"IMPROVEMENT AREAS?:?\s*(.*?)(?=\n\d+\.|ACTIONABLE|$)",
            "actionable_drills": r"ACTIONABLE DRILLS?:?\s*(.*?)(?=\n\d+\.|RECOMMENDATIONS|$)",
            "recommendations": r"RECOMMENDATIONS?:?\s*(.*?)(?=\n\d+\.|KEY LESSONS|$)",
            "key_lessons": r"KEY LESSONS?:?\s*(.*?)(?=\n\d+\.|FUTURE SETUPS|$)",
            "future_setups": r"FUTURE SETUPS?:?\s*(.*?)(?=\n\d+\.|$)",
        }

        for key, pattern in sections.items():
            match = re.search(pattern, analysis_text, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1).strip()
                if key in [
                    "strengths",
                    "weaknesses",
                    "improvement_areas",
                    "actionable_drills",
                    "recommendations",
                    "key_lessons",
                    "future_setups",
                ]:
                    # Parse as list
                    items = [
                        item.strip("- ").strip()
                        for item in content.split("\n")
                        if item.strip() and not item.strip().startswith("OVERALL")
                    ]
                    parsed[key] = [
                        item for item in items if len(item) > 10
                    ]  # Filter out short/empty items
                else:
                    # Parse as text
                    parsed[key] = content

        return parsed

    def _parse_planned_analysis(self, analysis_text):
        """Parse the AI planned trade analysis response into structured data"""
        parsed = {}

        # Extract overall score
        score_match = re.search(
            r"(?:OVERALL SCORE|SCORE).*?(\d+)", analysis_text, re.IGNORECASE
        )
        if score_match:
            parsed["overall_score"] = int(score_match.group(1))

        # Extract sections using regex for planned trade specific analysis
        sections = {
            "entry_analysis": r"ENTRY STRATEGY:?\s*(.*?)(?=\n\d+\.|STOP LOSS|$)",
            "exit_analysis": r"TAKE PROFIT LEVELS?:?\s*(.*?)(?=\n\d+\.|POSITION SIZING|$)",
            "risk_analysis": r"(?:STOP LOSS PLACEMENT|RISK ANALYSIS):?\s*(.*?)(?=\n\d+\.|POSITION SIZING|MARKET CONTEXT|$)",
            "market_context": r"MARKET CONTEXT:?\s*(.*?)(?=\n\d+\.|OPTIONS ANALYSIS|RECOMMENDATIONS|$)",
            "options_analysis": r"OPTIONS ANALYSIS:?\s*(.*?)(?=\n\d+\.|RECOMMENDATIONS|KEY LESSONS|$)",
            "recommendations": r"RECOMMENDATIONS?:?\s*(.*?)(?=\n\d+\.|KEY LESSONS|FUTURE SETUPS|$)",
            "key_lessons": r"KEY LESSONS?:?\s*(.*?)(?=\n\d+\.|FUTURE SETUPS|$)",
            "future_setups": r"FUTURE SETUPS?:?\s*(.*?)(?=\n\d+\.|$)",
        }

        for key, pattern in sections.items():
            match = re.search(pattern, analysis_text, re.IGNORECASE | re.DOTALL)
            if match:
                content = match.group(1).strip()
                if key in ["recommendations", "key_lessons", "future_setups"]:
                    # Parse as list
                    items = [
                        item.strip("- ").strip()
                        for item in content.split("\n")
                        if item.strip() and not item.strip().startswith("OVERALL")
                    ]
                    parsed[key] = [
                        item for item in items if len(item) > 10
                    ]  # Filter out short/empty items
                else:
                    # Parse as text
                    parsed[key] = content

        return parsed

    def _parse_daily_analysis(self, analysis_text):
        """Parse daily analysis response"""
        parsed = {}

        # Extract daily score
        score_match = re.search(
            r"(?:DAILY SCORE|SCORE).*?(\d+)", analysis_text, re.IGNORECASE
        )
        if score_match:
            parsed["daily_score"] = int(score_match.group(1))

        return parsed
