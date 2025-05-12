import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

class Config:
    # Flask configuration
    SECRET_KEY = os.environ.get('SECRET_KEY', 'dev')
    
    # Database configuration
    SQLALCHEMY_DATABASE_URI = os.environ.get('DATABASE_URL')
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    
    # Celery configuration
    CELERY_BROKER_URL = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    CELERY_RESULT_BACKEND = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
    
    # API Keys
    OPENAI_API_KEY = os.environ.get('OPENAI_API_KEY')
    SERPAPI_API_KEY = os.environ.get('SERPAPI_API_KEY')
    
    # Other configuration
    DEBUG = os.environ.get('FLASK_DEBUG', 'False').lower() == 'true' 