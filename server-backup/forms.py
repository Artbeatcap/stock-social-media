from flask_wtf import FlaskForm
from flask_wtf.file import FileField, FileAllowed
from wtforms import StringField, PasswordField, SubmitField, TextAreaField, FloatField, IntegerField, DateTimeField, SelectField, DateField, BooleanField
from wtforms.validators import DataRequired, Email, Length, EqualTo, ValidationError, Optional, NumberRange
from models import User
from datetime import datetime, date

class LoginForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired()])
    password = PasswordField('Password', validators=[DataRequired()])
    submit = SubmitField('Sign In')

class RegistrationForm(FlaskForm):
    username = StringField('Username', validators=[DataRequired(), Length(min=3, max=64)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    password = PasswordField('Password', validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField('Repeat Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Register')

    def validate_username(self, username):
        user = User.query.filter_by(username=username.data).first()
        if user is not None:
            raise ValidationError('Please use a different username.')

    def validate_email(self, email):
        user = User.query.filter_by(email=email.data).first()
        if user is not None:
            raise ValidationError('Please use a different email address.')

class TradeForm(FlaskForm):
    symbol = StringField('Symbol', validators=[DataRequired(), Length(min=1, max=10)], 
                        render_kw={"placeholder": "e.g., AAPL"})
    trade_type = SelectField('Trade Type', choices=[
        ('long', 'Long Stock'),
        ('short', 'Short Stock'),
        ('option_call', 'Call Option'),
        ('option_put', 'Put Option'),
        ('credit_put_spread', 'Credit Put Spread (Bull Put)'),
        ('credit_call_spread', 'Credit Call Spread (Bear Call)')
    ], validators=[DataRequired()])
    
    # Planned trade flag
    is_planned = BooleanField('Planned Trade (not entered yet)', 
                             render_kw={"class": "form-check-input"})
    
    entry_date = DateTimeField('Entry Date & Time', validators=[Optional()], 
                              default=datetime.now, format='%Y-%m-%d %H:%M')
    entry_price = FloatField('Entry Price ($)', validators=[Optional(), NumberRange(min=0.01)],
                            render_kw={"step": "0.01", "placeholder": "0.00"})
    quantity = IntegerField('Quantity/Shares', validators=[Optional(), NumberRange(min=1)],
                           render_kw={"placeholder": "100"})
    
    # Options-specific fields
    strike_price = FloatField('Strike Price ($)', validators=[Optional(), NumberRange(min=0.01)],
                             render_kw={"step": "0.01", "placeholder": "Strike price"})
    expiration_date = DateField('Expiration Date', validators=[Optional()],
                               render_kw={"placeholder": "Options expiration"})
    premium_paid = FloatField('Premium Per Contract ($)', validators=[Optional(), NumberRange(min=0.01)],
                             render_kw={"step": "0.01", "placeholder": "Premium paid"})
    underlying_price_at_entry = FloatField('Underlying Price at Entry ($)', validators=[Optional(), NumberRange(min=0.01)],
                                          render_kw={"step": "0.01", "placeholder": "Stock price when bought"})
    underlying_price_at_exit = FloatField('Underlying Price at Exit ($)', validators=[Optional(), NumberRange(min=0.01)],
                                         render_kw={"step": "0.01", "placeholder": "Stock price when sold"})
    implied_volatility = FloatField('Implied Volatility (%)', validators=[Optional(), NumberRange(min=0, max=1000)],
                                   render_kw={"step": "0.1", "placeholder": "IV%"})
    
    # Greeks (optional advanced fields)
    delta = FloatField('Delta', validators=[Optional(), NumberRange(min=-1, max=1)],
                      render_kw={"step": "0.01", "placeholder": "Delta value"})
    gamma = FloatField('Gamma', validators=[Optional(), NumberRange(min=0, max=10)],
                      render_kw={"step": "0.001", "placeholder": "Gamma value"})
    theta = FloatField('Theta', validators=[Optional(), NumberRange(min=-100, max=100)],
                      render_kw={"step": "0.01", "placeholder": "Theta value"})
    vega = FloatField('Vega', validators=[Optional(), NumberRange(min=0, max=100)],
                     render_kw={"step": "0.01", "placeholder": "Vega value"})
    
    # Spread-specific fields (for credit spreads)
    long_strike = FloatField('Long Strike Price ($)', validators=[Optional(), NumberRange(min=0.01)],
                            render_kw={"step": "0.01", "placeholder": "Protective leg strike"})
    short_strike = FloatField('Short Strike Price ($)', validators=[Optional(), NumberRange(min=0.01)],
                             render_kw={"step": "0.01", "placeholder": "Sold leg strike"})
    long_premium = FloatField('Long Premium ($)', validators=[Optional(), NumberRange(min=0.01)],
                             render_kw={"step": "0.01", "placeholder": "Premium paid for long leg"})
    short_premium = FloatField('Short Premium ($)', validators=[Optional(), NumberRange(min=0.01)],
                              render_kw={"step": "0.01", "placeholder": "Premium received for short leg"})
    net_credit = FloatField('Net Credit ($)', validators=[Optional(), NumberRange(min=0.01)],
                           render_kw={"step": "0.01", "placeholder": "Net credit received"})
    
    # Risk Management
    stop_loss = FloatField('Stop Loss ($)', validators=[Optional(), NumberRange(min=0.01)],
                          render_kw={"step": "0.01", "placeholder": "Optional"})
    take_profit = FloatField('Take Profit ($)', validators=[Optional(), NumberRange(min=0.01)],
                            render_kw={"step": "0.01", "placeholder": "Optional"})
    risk_amount = FloatField('Risk Amount ($)', validators=[Optional(), NumberRange(min=0.01)],
                            render_kw={"step": "0.01", "placeholder": "How much $ at risk"})
    
    # Exit Details
    exit_date = DateTimeField('Exit Date & Time', validators=[Optional()], format='%Y-%m-%d %H:%M')
    exit_price = FloatField('Exit Price ($)', validators=[Optional(), NumberRange(min=0.01)],
                           render_kw={"step": "0.01"})
    
    # Trade Context
    setup_type = SelectField('Setup Type', choices=[
        ('', 'Select Setup...'),
        ('breakout', 'Breakout'),
        ('pullback', 'Pullback/Retest'),
        ('reversal', 'Reversal'),
        ('continuation', 'Continuation'),
        ('support_resistance', 'Support/Resistance'),
        ('pattern', 'Chart Pattern'),
        ('momentum', 'Momentum Play'),
        ('gap_play', 'Gap Play'),
        ('earnings', 'Earnings Play'),
        ('news', 'News Play'),
        ('volatility_play', 'Volatility Play'),
        ('time_decay', 'Time Decay Strategy'),
        ('other', 'Other')
    ], validators=[Optional()])
    
    market_condition = SelectField('Market Condition', choices=[
        ('', 'Select Market...'),
        ('trending_up', 'Strong Uptrend'),
        ('trending_down', 'Strong Downtrend'),
        ('ranging', 'Ranging/Sideways'),
        ('volatile', 'High Volatility'),
        ('low_vol', 'Low Volatility'),
        ('choppy', 'Choppy/Uncertain')
    ], validators=[Optional()])
    
    timeframe = SelectField('Trade Timeframe', choices=[
        ('day_trade', 'Day Trade (Intraday)'),
        ('swing', 'Swing Trade (2-10 days)'),
        ('position', 'Position Trade (Weeks/Months)')
    ], validators=[DataRequired()])
    
    # Reasoning and Notes
    entry_reason = TextAreaField('Entry Reason/Setup Description', 
                                validators=[Optional(), Length(max=500)],
                                render_kw={"rows": "3", "placeholder": "Why did you enter this trade? What was your thesis?"})
    exit_reason = TextAreaField('Exit Reason', 
                               validators=[Optional(), Length(max=500)],
                               render_kw={"rows": "2", "placeholder": "Why did you exit? Plan vs actual execution"})
    notes = TextAreaField('Additional Notes', 
                         validators=[Optional(), Length(max=1000)],
                         render_kw={"rows": "3", "placeholder": "Any additional observations or notes"})
    tags = StringField('Tags', validators=[Optional(), Length(max=200)],
                      render_kw={"placeholder": "earnings, momentum, FOMO (comma-separated)"})
    
    # Chart Screenshots
    entry_chart_image = FileField('Entry Chart Screenshot', 
                                 validators=[Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')],
                                 render_kw={"accept": "image/*"})
    exit_chart_image = FileField('Exit Chart Screenshot', 
                                validators=[Optional(), FileAllowed(['jpg', 'jpeg', 'png', 'gif'], 'Images only!')],
                                render_kw={"accept": "image/*"})
    
    submit = SubmitField('Save Trade')
    
    def validate_strike_price(self, strike_price):
        """Validate that strike price is provided for options trades"""
        if self.trade_type.data in ['option_call', 'option_put'] and not strike_price.data:
            raise ValidationError('Strike price is required for options trades.')
    
    def validate_expiration_date(self, expiration_date):
        """Validate that expiration date is provided for options trades"""
        if self.trade_type.data in ['option_call', 'option_put'] and not expiration_date.data:
            raise ValidationError('Expiration date is required for options trades.')
        if self.trade_type.data in ['credit_put_spread', 'credit_call_spread'] and not expiration_date.data:
            raise ValidationError('Expiration date is required for spread trades.')
        if expiration_date.data and expiration_date.data < date.today():
            raise ValidationError('Expiration date cannot be in the past.')
    
    def validate_long_strike(self, long_strike):
        """Validate long strike for spread trades"""
        if self.trade_type.data in ['credit_put_spread', 'credit_call_spread']:
            if not long_strike.data:
                raise ValidationError('Long strike is required for spread trades.')
            if self.short_strike.data and long_strike.data:
                if self.trade_type.data == 'credit_put_spread' and long_strike.data >= self.short_strike.data:
                    raise ValidationError('For put spreads, long strike must be lower than short strike.')
                if self.trade_type.data == 'credit_call_spread' and long_strike.data <= self.short_strike.data:
                    raise ValidationError('For call spreads, long strike must be higher than short strike.')
    
    def validate_short_strike(self, short_strike):
        """Validate short strike for spread trades"""
        if self.trade_type.data in ['credit_put_spread', 'credit_call_spread'] and not short_strike.data:
            raise ValidationError('Short strike is required for spread trades.')
    
    def validate_net_credit(self, net_credit):
        """Validate net credit for spread trades"""
        if self.trade_type.data in ['credit_put_spread', 'credit_call_spread']:
            if not net_credit.data:
                raise ValidationError('Net credit is required for credit spread trades.')
            if net_credit.data <= 0:
                raise ValidationError('Net credit must be positive for credit spreads.')
    
    def validate(self, extra_validators=None):
        """Custom validation for planned vs actual trades"""
        # Run standard validation first
        ok = super().validate(extra_validators)
        if not ok:
            return False
        
        # For non-planned trades, entry fields are required
        if not self.is_planned.data:
            required_fields = []
            if not self.entry_date.data:
                required_fields.append(('entry_date', 'Entry date is required for actual trades'))
            if not self.entry_price.data:
                required_fields.append(('entry_price', 'Entry price is required for actual trades'))
            if not self.quantity.data:
                required_fields.append(('quantity', 'Quantity is required for actual trades'))
            
            # Add errors for missing required fields
            for field_name, error_msg in required_fields:
                field = getattr(self, field_name)
                field.errors.append(error_msg)
            
            return len(required_fields) == 0
        
        return True

class QuickTradeForm(FlaskForm):
    """Simplified form for quick trade entry"""
    symbol = StringField('Symbol', validators=[DataRequired(), Length(min=1, max=10)],
                        render_kw={"placeholder": "AAPL"})
    trade_type = SelectField('Type', choices=[
        ('long', 'Long'),
        ('short', 'Short'),
        ('option_call', 'Call'),
        ('option_put', 'Put')
    ], validators=[DataRequired()])
    entry_price = FloatField('Entry $', validators=[DataRequired(), NumberRange(min=0.01)],
                            render_kw={"step": "0.01"})
    quantity = IntegerField('Qty', validators=[DataRequired(), NumberRange(min=1)])
    setup_type = StringField('Setup', validators=[Optional(), Length(max=100)],
                            render_kw={"placeholder": "breakout, pullback, etc."})
    submit = SubmitField('Add Trade')

class JournalForm(FlaskForm):
    journal_date = DateField('Date', validators=[DataRequired()], default=date.today)
    daily_pnl = FloatField('Daily P&L ($)', validators=[Optional()],
                          render_kw={"step": "0.01", "placeholder": "Total P&L for the day"})
    
    # Market and Trading Notes
    market_notes = TextAreaField('Market Notes', 
                                validators=[Optional(), Length(max=1000)],
                                render_kw={"rows": "4", "placeholder": "Market conditions, key levels, catalysts, etc."})
    trading_notes = TextAreaField('Trading Notes', 
                                 validators=[Optional(), Length(max=1000)],
                                 render_kw={"rows": "4", "placeholder": "Trades taken, decisions made, execution quality, etc."})
    emotions = TextAreaField('Emotions', 
                            validators=[Optional(), Length(max=1000)],
                            render_kw={"rows": "3", "placeholder": "How you felt during trading, emotional state, etc."})
    lessons_learned = TextAreaField('Lessons Learned', 
                                   validators=[Optional(), Length(max=1000)],
                                   render_kw={"rows": "3", "placeholder": "What you learned today, mistakes to avoid, etc."})
    tomorrow_plan = TextAreaField('Tomorrow\'s Plan', 
                                 validators=[Optional(), Length(max=1000)],
                                 render_kw={"rows": "3", "placeholder": "Plans for tomorrow, setups to watch, etc."})
    daily_score = IntegerField('Daily Score', validators=[Optional(), NumberRange(min=1, max=10)],
                              render_kw={"min": "1", "max": "10", "placeholder": "1-10"})
    
    # Morning Planning
    market_outlook = TextAreaField('Morning Market Analysis', 
                                  validators=[Optional(), Length(max=1000)],
                                  render_kw={"rows": "4", "placeholder": "What's your outlook on the market today? Key levels, news, etc."})
    daily_goals = TextAreaField('Daily Goals', 
                               validators=[Optional(), Length(max=500)],
                               render_kw={"rows": "3", "placeholder": "What are your specific goals for today's trading?"})
    
    # End of Day Reflection
    what_went_well = TextAreaField('What Went Well', 
                                  validators=[Optional(), Length(max=1000)],
                                  render_kw={"rows": "4", "placeholder": "What did you execute well today?"})
    what_went_wrong = TextAreaField('What Went Wrong', 
                                   validators=[Optional(), Length(max=1000)],
                                   render_kw={"rows": "4", "placeholder": "What mistakes did you make? What could be improved?"})
    tomorrow_focus = TextAreaField('Tomorrow\'s Focus', 
                                  validators=[Optional(), Length(max=500)],
                                  render_kw={"rows": "2", "placeholder": "What will you focus on improving tomorrow?"})
    
    # Psychology and State
    emotional_state = SelectField('Emotional State', choices=[
        ('', 'Select...'),
        ('confident', 'Confident & Clear'),
        ('calm', 'Calm & Focused'),
        ('neutral', 'Neutral'),
        ('nervous', 'Nervous'),
        ('anxious', 'Anxious'),
        ('greedy', 'Greedy/Overconfident'),
        ('fearful', 'Fearful/Hesitant'),
        ('frustrated', 'Frustrated'),
        ('euphoric', 'Euphoric/Overexcited'),
        ('disciplined', 'Disciplined'),
        ('impulsive', 'Impulsive'),
        ('confused', 'Confused/Uncertain')
    ], validators=[Optional()])
    
    stress_level = SelectField('Stress Level', choices=[
        (0, 'Select...'),
        (1, '1 - Very Relaxed'),
        (2, '2 - Relaxed'),
        (3, '3 - Slightly Tense'),
        (4, '4 - Moderate'),
        (5, '5 - Noticeable Stress'),
        (6, '6 - Stressed'),
        (7, '7 - Very Stressed'),
        (8, '8 - High Stress'),
        (9, '9 - Extreme Stress'),
        (10, '10 - Overwhelming')
    ], validators=[Optional()], coerce=int)
    
    discipline_score = SelectField('Discipline Score', choices=[
        (0, 'Rate your discipline...'),
        (1, '1 - Very Poor'),
        (2, '2 - Poor'),
        (3, '3 - Below Average'),
        (4, '4 - Slightly Below'),
        (5, '5 - Average'),
        (6, '6 - Above Average'),
        (7, '7 - Good'),
        (8, '8 - Very Good'),
        (9, '9 - Excellent'),
        (10, '10 - Perfect Discipline')
    ], validators=[Optional()], coerce=int)
    
    # Market Context
    market_trend = SelectField('Overall Market Trend', choices=[
        ('', 'Select...'),
        ('strong_bull', 'Strong Bull Market'),
        ('bull', 'Bullish'),
        ('neutral', 'Neutral/Mixed'),
        ('bear', 'Bearish'),
        ('strong_bear', 'Strong Bear Market'),
        ('volatile', 'Highly Volatile'),
        ('low_vol', 'Low Volatility')
    ], validators=[Optional()])
    
    volatility = SelectField('Market Volatility', choices=[
        ('', 'Select...'),
        ('low', 'Low Volatility'),
        ('medium', 'Medium Volatility'),
        ('high', 'High Volatility'),
        ('extreme', 'Extreme Volatility')
    ], validators=[Optional()])
    
    submit = SubmitField('Save Journal Entry')

class EditTradeForm(TradeForm):
    """Form for editing existing trades with calculate P&L button"""
    calculate_pnl = SubmitField('Calculate P&L')
    submit = SubmitField('Update Trade')

class AnalyzeTradeForm(FlaskForm):
    """Form for manually triggering trade analysis"""
    trade_id = IntegerField('Trade ID', validators=[DataRequired()])
    submit = SubmitField('Analyze Trade')

class SettingsForm(FlaskForm):
    """Enhanced user settings form with new fields"""
    # Account and display
    display_name = StringField('Display Name', validators=[Length(max=64)],
                              render_kw={"placeholder": "How you want to be displayed"})
    dark_mode = BooleanField('Enable dark mode')
    daily_brief_email = BooleanField('Receive morning market brief emails')
    timezone = SelectField('Time Zone', choices=[
        ('UTC', 'UTC'),
        ('America/New_York', 'America/New_York'),
        ('America/Chicago', 'America/Chicago'),
        ('America/Denver', 'America/Denver'),
        ('America/Los_Angeles', 'America/Los_Angeles')
    ], validate_choice=False, default='UTC')
    
    # API and account settings
    api_key = StringField('API Key', validators=[Optional(), Length(max=64)],
                         render_kw={"placeholder": "Optional API key for future features"})
    account_size = FloatField('Account Size ($)', validators=[Optional(), NumberRange(min=0)],
                             render_kw={"step": "0.01", "placeholder": "Optional: for position sizing"})
    default_risk_percent = FloatField('Default Risk Per Trade (%)', 
                                     validators=[Optional(), NumberRange(min=0.1, max=50)],
                                     render_kw={"step": "0.1", "placeholder": "2.0"})
    
    # Analysis preferences
    auto_analyze_trades = BooleanField('Auto-analyze closed trades')
    analysis_detail_level = SelectField(
        'Analysis Detail Level',
        choices=[('brief', 'Brief'), ('detailed', 'Detailed'), ('comprehensive', 'Comprehensive')],
        validators=[Optional()],
        validate_choice=False,
        default='detailed'
    )
    
    # Risk management
    max_daily_loss = FloatField('Max Daily Loss ($)', validators=[Optional(), NumberRange(min=0)],
                               render_kw={"step": "0.01", "placeholder": "Optional daily stop"})
    max_position_size = FloatField('Max Position Size ($)', validators=[Optional(), NumberRange(min=0)],
                                  render_kw={"step": "0.01", "placeholder": "Optional position limit"})
    
    # Display preferences
    trades_per_page = SelectField(
        'Trades Per Page',
        choices=[(10, '10'), (20, '20'), (50, '50'), (100, '100')],
        coerce=int,
        validators=[Optional()],
        validate_choice=False,
        default=20
    )
    
    submit = SubmitField('Save Changes')

class UserSettingsForm(FlaskForm):
    """User preferences and settings"""
    # Account info
    account_size = FloatField('Account Size ($)', validators=[Optional(), NumberRange(min=0)],
                             render_kw={"step": "0.01", "placeholder": "Optional: for position sizing"})
    default_risk_percent = FloatField('Default Risk Per Trade (%)', 
                                     validators=[Optional(), NumberRange(min=0.1, max=50)],
                                     render_kw={"step": "0.1", "placeholder": "2.0"})
    
    # Analysis preferences
    auto_analyze_trades = BooleanField('Auto-analyze closed trades')
    analysis_detail_level = SelectField('Analysis Detail Level', choices=[
        ('brief', 'Brief'),
        ('detailed', 'Detailed'),
        ('comprehensive', 'Comprehensive')
    ])
    
    # Risk management
    max_daily_loss = FloatField('Max Daily Loss ($)', validators=[Optional(), NumberRange(min=0)],
                               render_kw={"step": "0.01", "placeholder": "Optional daily stop"})
    max_position_size = FloatField('Max Position Size ($)', validators=[Optional(), NumberRange(min=0)],
                                  render_kw={"step": "0.01", "placeholder": "Optional position limit"})
    
    # Display preferences
    trades_per_page = SelectField('Trades Per Page', choices=[
        (10, '10'),
        (20, '20'),
        (50, '50'),
        (100, '100')
    ], coerce=int)
    
    submit = SubmitField('Save Settings')

class BulkAnalysisForm(FlaskForm):
    """Form for analyzing multiple trades at once"""
    trade_id = SelectField(
        "Analyze Individual Trade",
        coerce=int,
        choices=[],
        validators=[Optional()],
    )
    analyze_all_unanalyzed = BooleanField('Analyze all unanalyzed trades')
    analyze_recent = BooleanField('Analyze trades from last 30 days')
    submit = SubmitField('Start AI Analysis')

class ResetPasswordRequestForm(FlaskForm):
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Reset Password')

class ResetPasswordForm(FlaskForm):
    password = PasswordField('New Password', validators=[DataRequired(), Length(min=8)])
    password2 = PasswordField('Repeat Password', validators=[DataRequired(), EqualTo('password')])
    submit = SubmitField('Reset Password')

class MarketBriefSignupForm(FlaskForm):
    """Signup form for the free morning market brief"""
    name = StringField('Name', validators=[DataRequired(), Length(max=120)])
    email = StringField('Email', validators=[DataRequired(), Email()])
    submit = SubmitField('Get My Free Brief')

    def validate_email(self, email):
        from models import MarketBriefSubscriber
        subscriber = MarketBriefSubscriber.query.filter_by(email=email.data).first()
        if subscriber and subscriber.confirmed:
            raise ValidationError('This email is already subscribed and confirmed.')
