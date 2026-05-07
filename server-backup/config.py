import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    SECRET_KEY = os.environ.get('SECRET_KEY') or 'trading-analysis-secret-key-change-in-production'

    # Database Configuration
    # Use DATABASE_URL if set, otherwise fall back to SQLite for demo purposes
    _database_url = os.environ.get('DATABASE_URL')
    if not _database_url:
        # Fall back to SQLite for demo/development
        SQLALCHEMY_DATABASE_URI = 'sqlite:///demo.db'
        print("Warning: DATABASE_URL not set, using SQLite for demo purposes")
    else:
        SQLALCHEMY_DATABASE_URI = _database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    # OpenAI Configuration
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')

    # Finnhub Configuration (for market news/quotes)
    FINNHUB_TOKEN = os.environ.get('FINNHUB_TOKEN')

    # Tradier Configuration
    TRADIER_API_TOKEN = os.environ.get('TRADIER_API_TOKEN')

    # Mail Configuration (for user registration/password reset and newsletter)
    MAIL_SERVER = os.environ.get('MAIL_SERVER') or 'smtp.gmail.com'
    MAIL_PORT = int(os.environ.get('MAIL_PORT') or 587)
    MAIL_USE_TLS = os.environ.get('MAIL_USE_TLS', 'true').lower() in ['true', 'on', '1']
    MAIL_USERNAME = os.environ.get('MAIL_USERNAME')
    MAIL_PASSWORD = os.environ.get('MAIL_PASSWORD')
    # Ensure emails are sent in production unless explicitly disabled
    MAIL_SUPPRESS_SEND = os.environ.get('MAIL_SUPPRESS_SEND', 'false').lower() in ['true', 'on', '1']
    # Optional SendGrid API key for email sending; if present, will be preferred
    SENDGRID_KEY = os.environ.get('SENDGRID_KEY')
    # Mail sender configuration with name and email
    MAIL_DEFAULT_SENDER_NAME = os.environ.get('MAIL_DEFAULT_SENDER_NAME', 'Options Plunge Support')
    MAIL_DEFAULT_SENDER_EMAIL = os.environ.get('MAIL_DEFAULT_SENDER_EMAIL', 'support@optionsplunge.com')
    MAIL_DEFAULT_SENDER = (MAIL_DEFAULT_SENDER_NAME, MAIL_DEFAULT_SENDER_EMAIL)
    ADMIN_EMAIL = os.environ.get('ADMIN_EMAIL')  # For newsletter notifications

    # App Configuration
    TRADES_PER_PAGE = 20
    ANALYSIS_MODEL = 'gpt-5-nano'  # Default AI model for analysis

    # Market Brief Pipeline Configuration
    MODEL_BRIEF_STAGE_A = os.environ.get('MODEL_BRIEF_STAGE_A', 'gpt-4o-mini')
    MODEL_BRIEF_STAGE_B = os.environ.get('MODEL_BRIEF_STAGE_B', 'gpt-4o-mini')
    MAX_INPUT_TOKENS_SOFT = int(os.environ.get('MAX_INPUT_TOKENS_SOFT', '80000'))
    MAX_OUTPUT_TOKENS = int(os.environ.get('MAX_OUTPUT_TOKENS', '1200'))
    BRIEF_POLISH = os.environ.get('BRIEF_POLISH', 'true').lower() in ['true', 'on', '1']

    # URL Configuration for email links
    # For local development, don't set SERVER_NAME to allow all hosts
    SERVER_NAME = os.environ.get('SERVER_NAME') if os.environ.get('FLASK_ENV') == 'production' else None
    PREFERRED_URL_SCHEME = os.environ.get('PREFERRED_URL_SCHEME', 'http')
    
    # File Upload Configuration
    UPLOAD_FOLDER = 'static/uploads'
    MAX_CONTENT_LENGTH = 16 * 1024 * 1024  # 16MB max file size
    ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif'} 