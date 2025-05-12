from apps import create_app
from apps.content_plan.tasks import celery
import os
import logging
import redis
from urllib.parse import urlparse
import time

# Configure logging
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def test_redis_connection(url, max_retries=5, retry_delay=5):
    """Test Redis connection with retries"""
    for attempt in range(max_retries):
        try:
            logger.info(f"Attempting Redis connection (attempt {attempt + 1}/{max_retries})")
            r = redis.from_url(url, socket_timeout=5, socket_connect_timeout=5)
            r.ping()
            logger.info("Successfully connected to Redis")
            return True
        except redis.ConnectionError as e:
            logger.error(f"Redis connection failed (attempt {attempt + 1}): {str(e)}")
            if attempt < max_retries - 1:
                logger.info(f"Retrying in {retry_delay} seconds...")
                time.sleep(retry_delay)
        except Exception as e:
            logger.error(f"Unexpected error testing Redis connection: {str(e)}")
            return False
    return False

# Create the Flask app
app = create_app()

# Create Flask application context
app.app_context().push()

# Get Redis URL and test connection
redis_url = os.environ.get('CELERY_BROKER_URL', '')
if not redis_url:
    logger.error("CELERY_BROKER_URL not set in environment variables")
else:
    parsed_url = urlparse(redis_url)
    logger.info(f"Redis Configuration:")
    logger.info(f"Host: {parsed_url.hostname}")
    logger.info(f"Port: {parsed_url.port}")
    logger.info(f"Database: {parsed_url.path}")
    
    # Test Redis connection
    if not test_redis_connection(redis_url):
        logger.error("Failed to establish Redis connection after all retries")
    else:
        logger.info("Redis connection test successful")

# Ensure configuration is loaded
if not app.config.get('OPENAI_API_KEY'):
    app.config['OPENAI_API_KEY'] = os.environ.get('OPENAI_API_KEY')
if not app.config.get('SERPAPI_API_KEY'):
    app.config['SERPAPI_API_KEY'] = os.environ.get('SERPAPI_API_KEY')

logger.info("Initializing Celery worker with configuration:")
logger.info(f"OPENAI_API_KEY set: {bool(app.config.get('OPENAI_API_KEY'))}")
logger.info(f"SERPAPI_API_KEY set: {bool(app.config.get('SERPAPI_API_KEY'))}")
logger.info(f"Database URI: {app.config.get('SQLALCHEMY_DATABASE_URI')}")

# Configure Celery to use the same Flask app context
def celery_init_app(app):
    class FlaskTask(celery.Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)

    celery.Task = FlaskTask
    return celery

# Initialize Celery with Flask context
celery = celery_init_app(app)

if __name__ == '__main__':
    logger.info("Starting Celery worker...")
    celery.worker_main(['worker', '--loglevel=debug'])

# This file is used by Render to start the Celery worker
# It imports and re-exports the Celery instance from your tasks module 