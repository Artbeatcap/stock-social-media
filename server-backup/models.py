from flask_sqlalchemy import SQLAlchemy
from flask_login import UserMixin
from werkzeug.security import generate_password_hash, check_password_hash
from datetime import datetime, timedelta
import secrets
import json
import os
import requests
from decimal import Decimal, ROUND_HALF_UP
from config import Config  # <‑‑ grabs TRADIER_API_TOKEN

db = SQLAlchemy()


class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(64), unique=True, nullable=False)
    email = db.Column(db.String(120), unique=True, nullable=False)
    password_hash = db.Column(db.String(256))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    reset_token = db.Column(db.String(100), unique=True)
    reset_token_expiration = db.Column(db.DateTime)

    # User preferences
    default_risk_percent = db.Column(db.Float, default=2.0)  # Default risk per trade
    account_size = db.Column(db.Float)  # For position sizing calculations
    
    # New settings fields
    display_name = db.Column(db.String(64))
    dark_mode = db.Column(db.Boolean, default=False)
    daily_brief_email = db.Column(db.Boolean, default=True)
    timezone = db.Column(db.String(64), default='UTC')
    api_key = db.Column(db.String(64))  # optional for future API features

    # Email verification fields
    email_verified = db.Column(db.Boolean, default=False)
    email_verification_token = db.Column(db.String(100), unique=True)
    token_generated_at = db.Column(db.DateTime)
    password_reset_token = db.Column(db.String(100), unique=True)
    password_reset_token_generated_at = db.Column(db.DateTime)

    # Subscription fields
    subscription_status = db.Column(db.String(20), default='free')  # free, trialing, active, canceled
    plan_type = db.Column(db.String(20), default='none')  # none, monthly, annual
    stripe_customer_id = db.Column(db.String(100), unique=True)
    stripe_subscription_id = db.Column(db.String(100), unique=True)
    trial_end = db.Column(db.DateTime)  # When trial expires
    had_trial = db.Column(db.Boolean, default=False)  # Prevent repeat trials
    
    # Email subscription fields
    is_subscribed_daily = db.Column(db.Boolean, default=False)
    is_subscribed_weekly = db.Column(db.Boolean, default=True)

    # Token expires after 24 hours
    TOKEN_EXPIRATION_HOURS = 24  # Changed from 5 minutes to 24 hours
    # Password reset token expires after 1 hour
    PASSWORD_RESET_EXPIRATION_HOURS = 1

    def set_password(self, password):
        self.password_hash = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password_hash, password)

    def get_win_rate(self):
        """Calculate user's win rate"""
        closed_trades = (
            Trade.query.filter_by(user_id=self.id)
            .filter(Trade.exit_price.isnot(None))
            .all()
        )
        if not closed_trades:
            return 0
        winning_trades = [
            t for t in closed_trades if t.profit_loss and t.profit_loss > 0
        ]
        return len(winning_trades) / len(closed_trades) * 100

    def get_total_pnl(self):
        """Calculate total P&L"""
        closed_trades = (
            Trade.query.filter_by(user_id=self.id)
            .filter(Trade.profit_loss.isnot(None))
            .all()
        )
        return sum(trade.profit_loss for trade in closed_trades if trade.profit_loss)

    def get_recent_trades(self, limit=10):
        """Get recent trades"""
        return (
            Trade.query.filter_by(user_id=self.id)
            .order_by(Trade.entry_date.desc())
            .limit(limit)
            .all()
        )

    def generate_email_verification_token(self):
        """Generate a new email verification token"""
        self.email_verification_token = secrets.token_urlsafe(32)
        self.token_generated_at = datetime.utcnow()
        return self.email_verification_token

    @classmethod
    def verify_email_token(cls, token):
        """Verify email token and return user if valid"""
        user = cls.query.filter_by(email_verification_token=token).first()
        if user and not user.token_expired():
            return user
        return None
    
    def verify_email(self, token):
        """Verify email with the provided token"""
        if self.email_verification_token == token and not self.token_expired():
            self.email_verified = True
            self.email_verification_token = None
            self.token_generated_at = None
            return True
        return False

    def token_expired(self):
        """Check if the email verification token has expired"""
        if not self.token_generated_at:
            return True
        expiration_time = self.token_generated_at + timedelta(
            hours=self.TOKEN_EXPIRATION_HOURS
        )
        return datetime.utcnow() > expiration_time

    def get_token_expiration_time(self):
        """Get remaining time until token expires (in minutes)"""
        if not self.token_generated_at:
            return 0
        expiration_time = self.token_generated_at + timedelta(
            hours=self.TOKEN_EXPIRATION_HOURS
        )
        remaining = expiration_time - datetime.utcnow()
        return max(0, remaining.total_seconds() / 60)

    def generate_password_reset_token(self):
        """Generate a new password reset token"""
        self.password_reset_token = secrets.token_urlsafe(32)
        self.password_reset_token_generated_at = datetime.utcnow()
        return self.password_reset_token

    def password_reset_token_expired(self):
        """Check if the password reset token has expired"""
        if not self.password_reset_token_generated_at:
            return True
        expiration_time = self.password_reset_token_generated_at + timedelta(
            hours=self.PASSWORD_RESET_EXPIRATION_HOURS
        )
        return datetime.utcnow() > expiration_time

    def verify_password_reset_token(self, token):
        """Verify password reset token"""
        if (
            self.password_reset_token == token
            and not self.password_reset_token_expired()
        ):
            return True
        return False

    def get_reset_password_token(self, expires_in=3600):
        token = secrets.token_urlsafe(32)
        self.reset_token = token
        self.reset_token_expiration = datetime.utcnow() + timedelta(seconds=expires_in)
        db.session.commit()
        return token

    @staticmethod
    def verify_reset_password_token(token):
        user = User.query.filter_by(reset_token=token).first()
        if user is None or user.reset_token_expiration < datetime.utcnow():
            return None
        return user

    def has_pro_access(self):
        """Check if user has Pro access"""
        # Require email verification for Pro features
        if not self.email_verified:
            return False
        
        # Check if user is in trial period
        if self.trial_end and datetime.utcnow() < self.trial_end:
            return True
        
        # Check if user has active subscription
        return self.subscription_status in ['active', 'trialing']
    
    def __repr__(self):
        return f"<User {self.username}>"


# ---------------------------------------------------------------
def _r(value, places=2):
    """Round half‑up like Excel (finance‑friendly)."""
    return float(
        Decimal(value).quantize(Decimal(10) ** -places, rounding=ROUND_HALF_UP)
    )


class Trade(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # Planned trade flag
    is_planned = db.Column(db.Boolean, default=False, nullable=False)

    # ────────────────────────────────────────────────────────────
    # NEW: live quote via Tradier REST
    # ────────────────────────────────────────────────────────────
    def get_current_market_price(self):
        """
        Return the latest trade price (or bid/ask mid) from Tradier.
        Works for stocks/ETFs and OCC‑formatted option symbols.
        Falls back to entry_price on API failure.
        """
        token = Config.TRADIER_API_TOKEN or os.getenv("TRADIER_API_TOKEN")
        if not token:
            return self.entry_price  # graceful fallback

        url = "https://api.tradier.com/v1/markets/quotes"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
        try:
            resp = requests.get(url, headers=headers, params={"symbols": self.symbol})
            resp.raise_for_status()
            data = resp.json()["quotes"]["quote"]
            if isinstance(data, list):
                data = data[0]

            last = data.get("last") or 0
            if last == 0:  # illiquid option
                bid, ask = data.get("bid"), data.get("ask")
                if bid and ask:
                    last = (bid + ask) / 2

            return float(last) if last else self.entry_price

        except Exception:
            return self.entry_price  # network / JSON error fallback

    # Basic trade information
    symbol = db.Column(db.String(10), nullable=False)
    trade_type = db.Column(
        db.String(20), nullable=False
    )  # 'long', 'short', 'option_call', 'option_put'
    entry_date = db.Column(db.DateTime, nullable=True)  # Nullable for planned trades
    exit_date = db.Column(db.DateTime)

    # Entry details
    entry_price = db.Column(db.Float, nullable=True)  # Nullable for planned trades
    quantity = db.Column(db.Integer, nullable=True)  # Nullable for planned trades
    entry_reason = db.Column(db.Text)  # Strategy/setup description

    # Exit details
    exit_price = db.Column(db.Float)
    exit_reason = db.Column(db.Text)  # Why you exited

    # Options-specific fields
    strike_price = db.Column(
        db.Float
    )  # Strike price for options (or short leg strike for spreads)
    expiration_date = db.Column(db.Date)  # Expiration date for options
    option_type = db.Column(db.String(10))  # 'call' or 'put' (derived from trade_type)
    premium_paid = db.Column(
        db.Float
    )  # Premium paid per contract (or net credit/debit for spreads)
    implied_volatility = db.Column(db.Float)  # IV at entry (optional)

    # Multi-leg options (spreads) fields
    is_spread = db.Column(db.Boolean, default=False)  # Whether this is a spread trade
    spread_type = db.Column(
        db.String(30)
    )  # 'credit_put_spread', 'credit_call_spread', etc.
    long_strike = db.Column(db.Float)  # Long leg strike price
    short_strike = db.Column(db.Float)  # Short leg strike price
    long_premium = db.Column(db.Float)  # Premium paid for long leg
    short_premium = db.Column(db.Float)  # Premium received for short leg
    net_credit = db.Column(
        db.Float
    )  # Net credit received (positive) or debit paid (negative)
    max_profit = db.Column(db.Float)  # Maximum profit potential
    max_loss = db.Column(db.Float)  # Maximum loss potential
    breakeven_price = db.Column(db.Float)  # Breakeven price for the spread

    # Greeks (optional advanced fields)
    delta = db.Column(db.Float)
    gamma = db.Column(db.Float)
    theta = db.Column(db.Float)
    vega = db.Column(db.Float)

    # Options market data
    underlying_price_at_entry = db.Column(
        db.Float
    )  # Stock price when option was bought
    underlying_price_at_exit = db.Column(db.Float)  # Stock price when option was sold

    # P&L (calculated automatically)
    profit_loss = db.Column(db.Float)
    profit_loss_percent = db.Column(db.Float)

    # Trade context
    market_condition = db.Column(
        db.String(50)
    )  # 'trending_up', 'trending_down', 'ranging', etc.
    setup_type = db.Column(db.String(100))  # 'breakout', 'pullback', 'reversal', etc.
    timeframe = db.Column(db.String(20))  # 'day_trade', 'swing', 'position'

    # Risk management
    stop_loss = db.Column(db.Float)
    take_profit = db.Column(db.Float)
    risk_amount = db.Column(db.Float)  # Dollar amount risked

    # Additional notes
    notes = db.Column(db.Text)
    tags = db.Column(db.String(200))  # Comma-separated tags
    
    # Chart data fields
    chart_annotations = db.Column(db.Text, nullable=True)  # Plotly shapes JSON
    chart_snapshot_path = db.Column(db.Text, nullable=True)  # Relative path to saved PNG

    # Chart screenshots
    entry_chart_image = db.Column(db.String(200))  # Path to entry chart screenshot
    exit_chart_image = db.Column(db.String(200))  # Path to exit chart screenshot

    # Analysis flags
    is_analyzed = db.Column(db.Boolean, default=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationships
    user = db.relationship("User", backref=db.backref("trades", lazy=True))

    def is_option_trade(self):
        """Check if this is an options trade"""
        return self.trade_type in [
            "option_call",
            "option_put",
            "credit_put_spread",
            "credit_call_spread",
        ]

    def is_spread_trade(self):
        """Check if this is a spread trade"""
        return (
            self.trade_type in ["credit_put_spread", "credit_call_spread"]
            or self.is_spread
        )

    def get_contract_multiplier(self):
        """Get contract multiplier (100 for stock options, 1 for stocks)"""
        return 100 if self.is_option_trade() else 1

    def calculate_spread_metrics(self):
        """Calculate spread-specific metrics"""
        if not self.is_spread_trade() or not self.long_strike or not self.short_strike:
            return

        strike_width = abs(self.short_strike - self.long_strike)

        if self.trade_type == "credit_put_spread":
            # Bull Put Spread: Sell higher strike put, buy lower strike put
            self.max_profit = (
                self.net_credit * self.quantity * 100 if self.net_credit else 0
            )
            self.max_loss = (strike_width * self.quantity * 100) - self.max_profit
            self.breakeven_price = self.short_strike - (self.net_credit or 0)

        elif self.trade_type == "credit_call_spread":
            # Bear Call Spread: Sell lower strike call, buy higher strike call
            self.max_profit = (
                self.net_credit * self.quantity * 100 if self.net_credit else 0
            )
            self.max_loss = (strike_width * self.quantity * 100) - self.max_profit
            self.breakeven_price = self.short_strike + (self.net_credit or 0)

    def calculate_spread_pnl(self):
        """Calculate P&L for spread trades"""
        # exit_price can legitimately be 0 if the spread expires worthless.
        # The previous check used ``not self.exit_price`` which incorrectly
        # treated an exit price of 0 as ``False`` and skipped the calculation.
        # We only want to bail out when ``exit_price`` is ``None``.
        if not self.is_spread_trade() or self.exit_price is None:
            return

        if self.trade_type == "credit_put_spread":
            # P&L = Net credit received - cost to close
            # If expired worthless, keep full credit
            # If assigned, pay intrinsic value
            if (
                self.underlying_price_at_exit
                and self.underlying_price_at_exit >= self.short_strike
            ):
                # Expired worthless - keep full credit
                self.profit_loss = self.net_credit * self.quantity * 100
            else:
                # Calculate based on exit premium
                self.profit_loss = (
                    (self.net_credit - self.exit_price) * self.quantity * 100
                )

        elif self.trade_type == "credit_call_spread":
            # Similar logic for call spreads
            if (
                self.underlying_price_at_exit
                and self.underlying_price_at_exit <= self.short_strike
            ):
                # Expired worthless - keep full credit
                self.profit_loss = self.net_credit * self.quantity * 100
            else:
                # Calculate based on exit premium
                self.profit_loss = (
                    (self.net_credit - self.exit_price) * self.quantity * 100
                )

        # Calculate percentage return
        if self.net_credit and self.net_credit > 0:
            cost_basis = self.max_loss  # Risk amount
            self.profit_loss_percent = (
                (self.profit_loss / cost_basis) * 100 if cost_basis > 0 else 0
            )

    def calculate_pnl(self):
        """Calculate P&L for both open and closed trades"""
        if self.exit_price is not None:
            # Closed trade - use actual exit price
            if self.is_spread_trade():
                self.calculate_spread_pnl()
            elif self.is_option_trade():
                # For single options: P&L = (exit_price - entry_price) * quantity * 100
                self.profit_loss = (
                    (self.exit_price - self.entry_price) * self.quantity * 100
                )
                # Percentage return based on premium paid
                if self.entry_price > 0:
                    cost_basis = self.entry_price * self.quantity * 100
                    if cost_basis > 0:  # Additional safety check
                        self.profit_loss_percent = (self.profit_loss / cost_basis) * 100
                    else:
                        self.profit_loss_percent = 0.0
                else:
                    self.profit_loss_percent = 0.0
            else:
                # For stocks
                if self.trade_type == "long":
                    self.profit_loss = (
                        self.exit_price - self.entry_price
                    ) * self.quantity
                elif self.trade_type == "short":
                    self.profit_loss = (
                        self.entry_price - self.exit_price
                    ) * self.quantity

                # Calculate percentage return with safety checks
                if self.entry_price > 0 and self.quantity > 0:
                    cost_basis = self.entry_price * self.quantity
                    if cost_basis > 0:
                        self.profit_loss_percent = (self.profit_loss / cost_basis) * 100
                    else:
                        self.profit_loss_percent = 0.0
                else:
                    self.profit_loss_percent = 0.0
        else:
            # Open trade - calculate unrealized P&L
            self.calculate_unrealized_pnl()
    def calculate_unrealized_pnl(self):
        """Always mark open positions to the latest market price."""
        if self.exit_price is not None:
            return  # closed trade handled elsewhere

        live = self.get_current_market_price()
        if live is None:
            return

        if self.is_spread_trade():
            # For open spread positions we don't have an exit price yet.  Use
            # the current market price as the cost to close the spread so we can
            # mark to market.  ``net_credit`` represents the credit received at
            # entry so the unrealised P&L is that credit minus the cost to close
            # (``live``).  If we don't have the necessary data just skip the
            # calculation.
            if live is None or self.net_credit is None:
                return

            pnl = (self.net_credit - live) * self.quantity * 100

            if self.max_loss:
                cost_basis = self.max_loss
            else:
                strike_width = 0
                if self.short_strike and self.long_strike:
                    strike_width = abs(self.short_strike - self.long_strike) * self.quantity * 100
                cost_basis = strike_width - (self.net_credit * self.quantity * 100)

            pct = _r((pnl / cost_basis) * 100, 2) if cost_basis else 0
        elif self.is_option_trade():
            pnl = (live - self.entry_price) * self.quantity * 100
            cost = self.entry_price * self.quantity * 100
            pct = _r((pnl / cost) * 100, 2) if cost else 0
        else:  # stock / ETF
            direction = 1 if self.trade_type == "long" else -1
            pnl = direction * (live - self.entry_price) * self.quantity
            cost = self.entry_price * self.quantity
            pct = _r((pnl / cost) * 100, 2) if cost else 0

        self.profit_loss = _r(pnl, 2)
        self.profit_loss_percent = pct

    def calculate_current_intrinsic_value(self, current_stock_price):
        """Calculate current intrinsic value based on current stock price"""
        if not self.is_option_trade() or not self.strike_price:
            return 0

        if self.trade_type == "option_call":
            return max(0, current_stock_price - self.strike_price)
        elif self.trade_type == "option_put":
            return max(0, self.strike_price - current_stock_price)
        return 0

    def is_open_position(self):
        """Check if this is an open position (no exit price)"""
        return self.exit_price is None

    def is_planned_trade(self):
        """Check if this is a planned trade (not yet entered)"""
        return self.is_planned

    def get_unrealized_pnl_with_live_price(
        self, current_option_price=None, current_stock_price=None
    ):
        """Calculate unrealized P&L with provided live prices"""
        if not self.is_open_position():
            return self.profit_loss, self.profit_loss_percent

        if self.is_option_trade() and current_option_price is not None:
            # Use live option price
            unrealized_pnl = (
                (current_option_price - self.entry_price) * self.quantity * 100
            )
            cost_basis = self.entry_price * self.quantity * 100
            unrealized_pnl_percent = (
                (unrealized_pnl / cost_basis) * 100 if cost_basis > 0 else 0
            )
            return unrealized_pnl, unrealized_pnl_percent

        elif not self.is_option_trade() and current_stock_price is not None:
            # Use live stock price
            if self.trade_type == "long":
                unrealized_pnl = (
                    current_stock_price - self.entry_price
                ) * self.quantity
            elif self.trade_type == "short":
                unrealized_pnl = (
                    self.entry_price - current_stock_price
                ) * self.quantity
            else:
                return 0, 0

            cost_basis = self.entry_price * self.quantity
            unrealized_pnl_percent = (
                (unrealized_pnl / cost_basis) * 100 if cost_basis > 0 else 0
            )
            return unrealized_pnl, unrealized_pnl_percent

        # Fallback to stored values
        return self.profit_loss or 0, self.profit_loss_percent or 0

    def get_days_to_expiration(self):
        """Calculate days to expiration for options"""
        if self.is_option_trade() and self.expiration_date:
            today = datetime.now().date()
            if self.expiration_date > today:
                return (self.expiration_date - today).days
            else:
                return 0  # Expired
        return None

    def get_moneyness(self):
        """Calculate option moneyness"""
        if (
            not self.is_option_trade()
            or not self.strike_price
            or not self.underlying_price_at_entry
        ):
            return None

        if self.trade_type == "option_call":
            if self.underlying_price_at_entry > self.strike_price:
                return "ITM"  # In the money
            elif self.underlying_price_at_entry == self.strike_price:
                return "ATM"  # At the money
            else:
                return "OTM"  # Out of the money
        elif self.trade_type == "option_put":
            if self.underlying_price_at_entry < self.strike_price:
                return "ITM"
            elif self.underlying_price_at_entry == self.strike_price:
                return "ATM"
            else:
                return "OTM"
        return None

    def get_intrinsic_value(self):
        """Calculate intrinsic value of option"""
        if not self.is_option_trade() or not self.strike_price:
            return None

        underlying_price = (
            self.underlying_price_at_exit or self.underlying_price_at_entry
        )
        if not underlying_price:
            return None

        if self.trade_type == "option_call":
            return max(0, underlying_price - self.strike_price)
        elif self.trade_type == "option_put":
            return max(0, self.strike_price - underlying_price)
        return None

    def get_time_value(self):
        """Calculate time value of option"""
        if not self.is_option_trade():
            return None

        intrinsic_value = self.get_intrinsic_value()
        if intrinsic_value is not None and self.entry_price:
            return max(0, self.entry_price - intrinsic_value)
        return None

    def get_risk_reward_ratio(self):
        """Calculate risk/reward ratio"""
        if self.stop_loss and self.take_profit:
            if self.is_option_trade():
                # For options, risk is premium paid, reward is difference to target
                risk = self.entry_price
                reward = abs(self.take_profit - self.entry_price)
            else:
                if self.trade_type == "long":
                    risk = abs(self.entry_price - self.stop_loss)
                    reward = abs(self.take_profit - self.entry_price)
                else:  # short
                    risk = abs(self.stop_loss - self.entry_price)
                    reward = abs(self.entry_price - self.take_profit)

            if risk > 0:
                return reward / risk
        return None

    def is_winner(self):
        """Check if trade was profitable"""
        return self.profit_loss and self.profit_loss > 0

    def get_hold_time(self):
        """Calculate how long the trade was held"""
        if self.exit_date:
            return self.exit_date - self.entry_date
        return datetime.utcnow() - self.entry_date

    def to_dict(self):
        return {
            "id": self.id,
            "symbol": self.symbol,
            "trade_type": self.trade_type,
            "entry_date": self.entry_date.isoformat() if self.entry_date else None,
            "exit_date": self.exit_date.isoformat() if self.exit_date else None,
            "entry_price": self.entry_price,
            "exit_price": self.exit_price,
            "quantity": self.quantity,
            "profit_loss": self.profit_loss,
            "profit_loss_percent": self.profit_loss_percent,
            "setup_type": self.setup_type,
            "market_condition": self.market_condition,
            "timeframe": self.timeframe,
            "entry_reason": self.entry_reason,
            "exit_reason": self.exit_reason,
            "notes": self.notes,
            "tags": self.tags,
            "is_analyzed": self.is_analyzed,
            "risk_reward_ratio": self.get_risk_reward_ratio(),
            # Options-specific fields
            "is_option_trade": self.is_option_trade(),
            "is_spread_trade": self.is_spread_trade(),
            "strike_price": self.strike_price,
            "expiration_date": (
                self.expiration_date.isoformat() if self.expiration_date else None
            ),
            "premium_paid": self.premium_paid,
            "implied_volatility": self.implied_volatility,
            "underlying_price_at_entry": self.underlying_price_at_entry,
            "underlying_price_at_exit": self.underlying_price_at_exit,
            "days_to_expiration": self.get_days_to_expiration(),
            "moneyness": self.get_moneyness(),
            "intrinsic_value": self.get_intrinsic_value(),
            "time_value": self.get_time_value(),
            "delta": self.delta,
            "gamma": self.gamma,
            "theta": self.theta,
            "vega": self.vega,
            # Spread-specific fields
            "spread_type": self.spread_type,
            "long_strike": self.long_strike,
            "short_strike": self.short_strike,
            "long_premium": self.long_premium,
            "short_premium": self.short_premium,
            "net_credit": self.net_credit,
            "max_profit": self.max_profit,
            "max_loss": self.max_loss,
            "breakeven_price": self.breakeven_price,
        }

    def __repr__(self):
        return f"<Trade {self.symbol} - {self.trade_type}>"


class TradeAnalysis(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    trade_id = db.Column(db.Integer, db.ForeignKey("trade.id"), nullable=False)
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=False)

    # AI Analysis Results
    overall_score = db.Column(db.Integer)  # 1-10 score

    # Structured Analysis (stored as JSON)
    strengths = db.Column(db.Text)  # JSON array of strengths
    weaknesses = db.Column(db.Text)  # JSON array of weaknesses
    improvement_areas = db.Column(db.Text)  # JSON array of areas to focus on
    actionable_drills = db.Column(db.Text)  # JSON array of specific drills/exercises

    # Detailed Analysis
    entry_analysis = db.Column(db.Text)  # Detailed entry analysis
    exit_analysis = db.Column(db.Text)  # Detailed exit analysis
    risk_analysis = db.Column(db.Text)  # Position sizing, risk/reward analysis
    market_context = db.Column(db.Text)  # How well trade aligned with market conditions
    options_analysis = db.Column(
        db.Text
    )  # Options-specific analysis (Greeks, time decay, etc.)
    chart_analysis = db.Column(db.Text)  # Analysis of uploaded chart screenshots

    # Recommendations and Learning
    recommendations = db.Column(db.Text)  # JSON array of specific recommendations
    key_lessons = db.Column(db.Text)  # JSON array of key takeaways
    future_setups = db.Column(db.Text)  # Suggestions for similar future trades

    # Metadata
    analysis_date = db.Column(db.DateTime, default=datetime.utcnow)
    ai_model_used = db.Column(db.String(50))  # Track which AI model was used

    # Relationships
    trade = db.relationship(
        "Trade", backref=db.backref("analysis", uselist=False, lazy=True)
    )
    user = db.relationship("User", backref=db.backref("trade_analyses", lazy=True))

    # Helper methods for JSON fields
    def get_strengths(self):
        return json.loads(self.strengths) if self.strengths else []

    def set_strengths(self, strengths_list):
        self.strengths = json.dumps(strengths_list)

    def get_weaknesses(self):
        return json.loads(self.weaknesses) if self.weaknesses else []

    def set_weaknesses(self, weaknesses_list):
        self.weaknesses = json.dumps(weaknesses_list)

    def get_improvement_areas(self):
        return json.loads(self.improvement_areas) if self.improvement_areas else []

    def set_improvement_areas(self, areas_list):
        self.improvement_areas = json.dumps(areas_list)

    def get_actionable_drills(self):
        return json.loads(self.actionable_drills) if self.actionable_drills else []

    def set_actionable_drills(self, drills_list):
        self.actionable_drills = json.dumps(drills_list)

    def get_recommendations(self):
        return json.loads(self.recommendations) if self.recommendations else []

    def set_recommendations(self, recommendations_list):
        self.recommendations = json.dumps(recommendations_list)

    def get_key_lessons(self):
        return json.loads(self.key_lessons) if self.key_lessons else []

    def set_key_lessons(self, lessons_list):
        self.key_lessons = json.dumps(lessons_list)

    def get_future_setups(self):
        """Return future setup suggestions as a list"""
        return json.loads(self.future_setups) if self.future_setups else []

    def set_future_setups(self, setups_list):
        """Store future setup suggestions from a list"""
        self.future_setups = json.dumps(setups_list)

    def __repr__(self):
        return f"<TradeAnalysis for Trade {self.trade_id}>"


class TradingJournal(db.Model):
    """Daily trading journal entries"""

    id = db.Column(db.Integer, primary_key=True)
    # user_id is optional so guests can post journals
    user_id = db.Column(db.Integer, db.ForeignKey("user.id"), nullable=True)

    journal_date = db.Column(db.Date, nullable=False)

    __table_args__ = (
        db.UniqueConstraint('user_id', 'journal_date', name='uix_user_journal_date'),
    )

    # Daily reflections
    daily_pnl = db.Column(db.Float)
    
    # Market and Trading Notes
    market_notes = db.Column(db.Text)  # Market conditions, key levels, catalysts, etc.
    trading_notes = db.Column(db.Text)  # Trades taken, decisions made, execution quality, etc.
    emotions = db.Column(db.Text)  # How you felt during trading, emotional state, etc.
    tomorrow_plan = db.Column(db.Text)  # Plans for tomorrow, setups to watch, etc.
    
    # Morning Planning
    market_outlook = db.Column(db.Text)  # Morning market analysis
    daily_goals = db.Column(db.Text)  # Goals for the day

    # End of day reflection
    what_went_well = db.Column(db.Text)
    what_went_wrong = db.Column(db.Text)
    lessons_learned = db.Column(db.Text)
    tomorrow_focus = db.Column(db.Text)

    # Emotional and psychological state
    emotional_state = db.Column(
        db.String(50)
    )  # 'confident', 'nervous', 'greedy', 'fearful', etc.
    stress_level = db.Column(db.Integer)  # 1-10 scale
    discipline_score = db.Column(db.Integer)  # 1-10 self-assessment of discipline

    # AI Analysis of the day
    ai_daily_feedback = db.Column(db.Text)
    daily_score = db.Column(db.Integer)  # 1-10 daily performance score

    # Market conditions
    market_trend = db.Column(db.String(50))  # Overall market direction
    volatility = db.Column(db.String(20))  # 'low', 'medium', 'high'

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationship
    user = db.relationship("User", backref=db.backref("journal_entries", lazy=True))

    def get_day_trades(self):
        """Get all trades for this journal date"""
        return (
            Trade.query.filter_by(user_id=self.user_id)
            .filter(db.func.date(Trade.entry_date) == self.journal_date)
            .all()
        )

    def __repr__(self):
        return f"<TradingJournal {self.journal_date}>"


class UserSettings(db.Model):
    """User preferences and settings"""

    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(
        db.Integer, db.ForeignKey("user.id"), nullable=False, unique=True
    )

    # Analysis preferences
    auto_analyze_trades = db.Column(db.Boolean, default=True)
    auto_create_journal = db.Column(
        db.Boolean, default=True
    )  # Auto-create journal entries from trades
    analysis_detail_level = db.Column(
        db.String(20), default="detailed"
    )  # 'brief', 'detailed', 'comprehensive'

    # Notification preferences
    daily_journal_reminder = db.Column(db.Boolean, default=True)
    weekly_summary = db.Column(db.Boolean, default=True)

    # Display preferences
    default_chart_timeframe = db.Column(db.String(10), default="1D")
    trades_per_page = db.Column(db.Integer, default=20)

    # Risk management defaults
    default_risk_percent = db.Column(db.Float, default=2.0)
    max_daily_loss = db.Column(db.Float)
    max_position_size = db.Column(db.Float)

    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(
        db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow
    )

    # Relationship
    user = db.relationship(
        "User", backref=db.backref("settings", uselist=False, lazy=True)
    )

    def __repr__(self):
        return f"<UserSettings for User {self.user_id}>"


class MarketBriefSubscriber(db.Model):
    """Subscribers for the daily market brief"""
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(120), nullable=False)
    email = db.Column(db.String(200), unique=True, nullable=False)
    confirmed = db.Column(db.Boolean, default=False)
    token = db.Column(db.String(64), unique=True)  # for double-opt-in
    subscribed_at = db.Column(db.DateTime, default=datetime.utcnow)
    confirmed_at = db.Column(db.DateTime)
    last_brief_sent = db.Column(db.DateTime)  # track when last brief was sent
    is_active = db.Column(db.Boolean, default=True)  # for soft unsubscribes

    def __repr__(self):
        return f"<MarketBriefSubscriber {self.email}>"
    
    def generate_confirmation_token(self):
        """Generate a new confirmation token"""
        self.token = secrets.token_urlsafe(32)
        return self.token
    
    def confirm_subscription(self):
        """Confirm the subscription"""
        self.confirmed = True
        self.confirmed_at = datetime.utcnow()
        self.token = None  # Clear the token after confirmation
    
    def unsubscribe(self):
        """Soft unsubscribe - mark as inactive"""
        self.is_active = False

class EmailDelivery(db.Model):
    """Model for tracking email deliveries"""
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False)
    kind = db.Column(db.String(16), nullable=False)  # 'daily' or 'weekly'
    subject = db.Column(db.Text, nullable=False)
    sent_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    provider_id = db.Column(db.String(100))

class MarketBrief(db.Model):
    """Model for storing historical market briefs"""
    id = db.Column(db.Integer, primary_key=True)
    brief_type = db.Column(db.String(16), nullable=False)  # 'daily' or 'weekly'
    date = db.Column(db.Date, nullable=False)
    subject = db.Column(db.String(200), nullable=False)
    html_content = db.Column(db.Text, nullable=False)
    text_content = db.Column(db.Text, nullable=False)
    json_data = db.Column(db.Text)  # Store the original JSON data
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    
    # Index for efficient queries
    __table_args__ = (
        db.Index('idx_brief_type_date', 'brief_type', 'date'),
    )
    
    def __repr__(self):
        return f'<MarketBrief {self.brief_type} {self.date}>'


# === New Models: Plans, PlanAlerts, Execution, UsageCounter ===

class Plan(db.Model):
    __tablename__ = 'plans'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    symbol = db.Column(db.String(32), nullable=False, index=True)
    scenario_direction = db.Column(db.String(8))  # bull|bear
    invalidation = db.Column(db.Float)
    targets = db.Column(db.JSON)  # jsonb in Postgres
    playbook_tags = db.Column(db.JSON)
    rr_expected = db.Column(db.Float)
    sizing = db.Column(db.JSON)
    draft_payload = db.Column(db.JSON)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

class PlanAlert(db.Model):
    __tablename__ = 'plan_alerts'
    id = db.Column(db.Integer, primary_key=True)
    plan_id = db.Column(db.Integer, db.ForeignKey('plans.id'), nullable=False, index=True)
    type = db.Column(db.String(32))  # on_trigger|on_invalidation|on_target
    channel = db.Column(db.String(16))  # email|sms|inapp
    status = db.Column(db.String(16), default='active')
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Execution(db.Model):
    __tablename__ = 'executions'
    execution_id = db.Column(db.String(64), primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    broker = db.Column(db.String(16), nullable=False)
    broker_trade_id = db.Column(db.String(64))
    account_id = db.Column(db.String(64))
    timestamp_utc = db.Column(db.DateTime, index=True, nullable=False)
    symbol = db.Column(db.String(32), index=True, nullable=False)
    asset_type = db.Column(db.String(16), nullable=False)  # equity|option
    side = db.Column(db.String(8), nullable=False)
    quantity = db.Column(db.Float, nullable=False)
    price = db.Column(db.Float, nullable=False)
    commission = db.Column(db.Float, default=0)
    fees = db.Column(db.Float, default=0)
    currency = db.Column(db.String(8), default='USD')
    # Options
    right = db.Column(db.String(1))
    strike = db.Column(db.Float)
    expiration = db.Column(db.Date)
    multiplier = db.Column(db.Integer)
    occ = db.Column(db.String(64))

class UsageCounter(db.Model):
    __tablename__ = 'usage_counters'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=False, index=True)
    key = db.Column(db.String(64), nullable=False)
    week_start = db.Column(db.Date, nullable=False)
    count = db.Column(db.Integer, default=0)
    __table_args__ = (db.UniqueConstraint('user_id','key','week_start', name='uix_usage_user_key_week'),)
