import os
from celery import Celery
from celery.signals import after_setup_logger
import logging

# Get Redis URL from environment
redis_url = os.environ.get('CELERY_BROKER_URL', 'redis://localhost:6379/0')
logging.info(f"Using Redis URL: {redis_url}")

# Initialize Celery
celery = Celery(
    'content_plan',
    broker=redis_url,
    backend=redis_url
)

# Configure Celery with more robust settings
celery.conf.update(
    # Basic settings
    task_serializer='json',
    accept_content=['json'],
    result_serializer='json',
    timezone='UTC',
    enable_utc=True,
    
    # Task settings
    task_track_started=True,
    task_time_limit=3600,
    task_soft_time_limit=3300,
    worker_prefetch_multiplier=1,
    worker_max_tasks_per_child=100,  # Restart worker after 100 tasks
    worker_max_memory_per_child=200000,  # 200MB memory limit per worker
    
    # Redis connection settings
    broker_connection_retry=True,
    broker_connection_retry_on_startup=True,
    broker_connection_max_retries=100,
    broker_connection_timeout=30,
    broker_pool_limit=10,
    broker_heartbeat=10,
    
    # Result backend settings
    result_backend_transport_options={
        'retry_policy': {
            'timeout': 5.0,
            'max_retries': 3,
        },
        'global_keyprefix': 'celery_results'
    },
    
    # Redis specific settings
    redis_socket_timeout=30,
    redis_socket_connect_timeout=30,
    redis_retry_on_timeout=True,
    redis_max_connections=10,
    
    # Worker settings
    worker_concurrency=4,  # Reduced from default 16
    worker_enable_remote_control=True,
    worker_send_task_events=True,
    task_send_sent_event=True
)

@after_setup_logger.connect
def setup_loggers(logger, *args, **kwargs):
    """Configure logging for Celery"""
    import logging
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    ))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)  # Changed to DEBUG for more verbose logging

def init_celery(app):
    """Initialize Celery with Flask app"""
    celery.conf.update(app.config)
    app.extensions['celery'] = celery
    return celery