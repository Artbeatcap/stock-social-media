# 🚀 Options Plunge
# *AI Trading Analysis*

A comprehensive trading platform that combines AI-powered trade analysis with professional options calculators and real-time market data. This unified platform integrates the best features of trading journals, options analysis, and live market tools.

## ✨ Key Features

### 📊 **Trading Journal & Analysis**
- **AI-Powered Trade Analysis**: Get detailed feedback on your trades using OpenAI GPT
- **Comprehensive Trade Tracking**: Support for stocks, options, and credit spreads
- **Daily Journal**: Track emotions, market conditions, and daily performance
- **Performance Analytics**: Win rate, P&L tracking, and detailed statistics
- **Credit Spreads Support**: Full support for bull put and bear call spreads

### 🧮 **Professional Options Tools**
- **Live Options Calculator**: Real-time options chains with P&L analysis
- **Black-Scholes Pricing**: Theoretical option pricing and Greeks calculation
- **Stock Information Lookup**: Comprehensive stock data and charts
- **Greeks Analysis**: Delta, Gamma, Theta, and Vega calculations
- **P&L Scenarios**: Visual profit/loss charts at expiration

### 📈 **Advanced Features**
- **Multi-leg Options**: Credit spreads with automatic metrics calculation
- **Risk Management**: Position sizing and risk/reward analysis
- **Real-time Data**: Live stock prices and options chains via Yahoo Finance
- **Mobile Responsive**: Beautiful UI that works on all devices
- **User Management**: Secure authentication and personalized settings

## 🛠️ Installation

### Prerequisites
- Python 3.8+
- pip package manager

### Quick Setup

1. **Clone the repository**
   ```bash
   git clone <repository-url>
   cd ai-trading-analysis
   ```

2. **Create a virtual environment**
   ```bash
   python3 -m venv venv
   source venv/bin/activate  # On Windows use "venv\\Scripts\\activate"
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```
   This installs all required packages, including **SciPy** for mathematical functions.

4. **Set up environment variables**
   ```bash
   cp env_example.txt .env
   # Edit .env with your configuration
   ```

5. **Initialize the database**
   ```bash
   python -c "from app import app, db; app.app_context().push(); db.create_all()"
   ```

6. **Run the application**
   ```bash
   python app.py
   ```

7. **Access the platform**
   Open your browser to `http://localhost:5000`

## 🔧 Configuration

### Environment Variables

Create a `.env` file with the following variables:

```env
# Flask Configuration
SECRET_KEY=your-secret-key-here
FLASK_ENV=development

# Database (PostgreSQL only)
DATABASE_URL=postgresql://user:pass@host:5432/dbname

# OpenAI API (for AI analysis)
OPENAI_API_KEY=your-openai-api-key

# Tradier Configuration
TRADIER_API_TOKEN=your-tradier-token

# Email Configuration (optional)
MAIL_SERVER=smtp.gmail.com
MAIL_PORT=587
MAIL_USE_TLS=True
MAIL_USERNAME=your-email@gmail.com
MAIL_PASSWORD=your-app-password
# Mail sender configuration
MAIL_DEFAULT_SENDER_NAME=Options Plunge Support
MAIL_DEFAULT_SENDER_EMAIL=support@optionsplunge.com

# File Upload Settings
UPLOAD_FOLDER=static/uploads
MAX_CONTENT_LENGTH=16777216  # 16MB
ALLOWED_EXTENSIONS=png,jpg,jpeg,gif,pdf
```

## 📱 Usage Guide

### Getting Started

You can try the platform without logging in. The options calculator, "Add Trade",
the trades tab and journal pages are fully accessible to guests. Journal entries
you create while logged out are stored anonymously. Log in if you want your
trades or journals saved to your account or to access AI-powered analysis.

1. **Register an Account**
   - Create your trading account with email verification
   - Set up your trading preferences and risk parameters

2. **Add Your First Trade**
   - Navigate to "Add Trade" from the sidebar
   - Choose from stocks, options, or credit spreads
   - Fill in entry details, risk management, and notes

3. **Use Trading Tools**
   - Access professional calculators from the "Trading Tools" menu
   - Analyze options chains with live data
   - Calculate theoretical prices with Black-Scholes

### Trading Tools Overview

#### 🎯 **Options Calculator**
- Enter stock symbol to get live options chains
- Select expiration dates and analyze strikes
- Click "Analyze" on any option for detailed P&L scenarios
- View Greeks, breakeven points, and risk metrics

#### 📊 **Black-Scholes Calculator**
- Input stock price, strike, time to expiration, volatility
- Get theoretical option price and all Greeks
- Perfect for academic analysis and pricing verification

#### 🔍 **Stock Lookup**
- Search any stock symbol for comprehensive information
- View real-time prices, financial metrics, and charts
- Get 30-day price history with interactive charts

### Credit Spreads Trading

The platform fully supports credit spread strategies:

#### **Bull Put Spreads (Credit Put Spreads)**
- Sell higher strike put, buy lower strike put
- Automatic calculation of max profit, max loss, breakeven
- Risk management with position sizing

#### **Bear Call Spreads (Credit Call Spreads)**
- Sell lower strike call, buy higher strike call
- Complete P&L tracking including early closure scenarios
- Time decay analysis and profit targets

### AI Analysis Features

- **Trade Analysis**: Get detailed feedback on closed trades
- **Daily Performance**: AI analysis of daily trading performance
- **Pattern Recognition**: Identify strengths and weaknesses
- **Improvement Suggestions**: Actionable advice for better trading
- **Potential Setup Guidance**: Example entries and exits from a world-class day trader perspective

## 🏗️ Technical Architecture

### Backend Stack
- **Flask**: Web framework with SQLAlchemy ORM
- **PostgreSQL**: Primary database (required). No SQLite fallback.
- **OpenAI API**: AI-powered trade analysis
- **Yahoo Finance**: Real-time market data
- **NumPy/SciPy**: Mathematical calculations for options pricing

### Frontend Stack
- **Bootstrap 5**: Responsive UI framework
- **Plotly.js**: Interactive charts and visualizations
- **Font Awesome**: Professional icons
- **Custom CSS**: Beautiful gradient designs and animations

### Key Components
- **Models**: Trade, User, TradingJournal, TradeAnalysis
- **Forms**: WTForms with validation for all data entry
- **AI Analysis**: Sophisticated prompts for trade feedback
- **Options Pricing**: Black-Scholes implementation with Greeks
- **Data Integration**: Real-time market data fetching

## 📊 Database Schema

### Core Tables
- **Users**: Authentication and user preferences
- **Trades**: Comprehensive trade tracking with spread support
- **TradingJournal**: Daily performance and emotional tracking
- **TradeAnalysis**: AI-generated trade feedback
- **UserSettings**: Personalized configuration

### Spread-Specific Fields
- `is_spread`: Boolean flag for multi-leg trades
- `spread_type`: Type of spread (credit_put_spread, credit_call_spread)
- `long_strike`, `short_strike`: Strike prices for both legs
- `long_premium`, `short_premium`: Premiums for both legs
- `net_credit`: Total credit received
- `max_profit`, `max_loss`, `breakeven_price`: Calculated metrics

## 🚀 Deployment

### Production Deployment

1. **Set up production environment**
   ```bash
   export FLASK_ENV=production
   export DATABASE_URL=postgresql://user:pass@host:port/dbname
   ```

2. **Use a production WSGI server**
   ```bash
   pip install gunicorn
   gunicorn -w 4 -b 0.0.0.0:8000 app:app
   ```

3. **Set up reverse proxy (Nginx)**
   ```nginx
   server {
       listen 80;
       server_name your-domain.com;
       
       location / {
           proxy_pass http://127.0.0.1:8000;
           proxy_set_header Host $host;
           proxy_set_header X-Real-IP $remote_addr;
       }
   }
   ```

## 🧪 Testing

Before running the tests, create and activate a virtual environment, then install
the project dependencies:

```bash
python3 -m venv venv
source venv/bin/activate  # On Windows use "venv\\Scripts\\activate"
pip install -r requirements.txt
```

Run the test suite with:

```bash
pytest
```

The CI workflow (`.github/workflows/ci.yml`) mirrors these steps by installing the
dependencies and executing `pytest` on every push and pull request.

## 🤝 Contributing

We welcome contributions! Please follow these steps:

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/amazing-feature`)
3. Commit your changes (`git commit -m 'Add amazing feature'`)
4. Push to the branch (`git push origin feature/amazing-feature`)
5. Open a Pull Request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🆘 Support

- **Documentation**: Check this README and inline code comments
- **Issues**: Report bugs via GitHub Issues
- **Email**: Contact support for urgent matters

## 🎯 Roadmap

### Upcoming Features
- **Position Sizing Calculator**: Advanced risk management tools
- **Portfolio Analytics**: Multi-account tracking
- **Paper Trading**: Practice mode with virtual money
- **Mobile App**: Native iOS/Android applications
- **Advanced Spreads**: Iron condors, butterflies, straddles
- **Backtesting**: Historical strategy analysis
- **Social Features**: Share trades and strategies

### Recent Updates
- ✅ Integrated options calculator with live data
- ✅ Added Black-Scholes pricing calculator
- ✅ Implemented credit spreads support
- ✅ Enhanced mobile responsive design
- ✅ Added comprehensive stock lookup tool

## Linux host security

If you deploy the Flask stack or systemd timers from this tree onto Linux servers, use the **[CVE-2026-31431 (“Copy Fail”) runbook](security/CVE-2026-31431-copy-fail-runbook.md)** for kernel inventory, `CONFIG` checks, patching, and temporary mitigations.

---

**Built with ❤️ for traders who want to improve their performance through data-driven analysis and professional tools.** 