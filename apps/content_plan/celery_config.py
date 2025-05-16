import os
from celery import Celery, Task
from celery.signals import after_setup_logger
import logging
import re
from dotenv import load_dotenv
from kombu import Exchange, Queue

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Create Celery app with Redis backend
broker_url = os.environ.get('CELERY_BROKER_URL', os.environ.get('broker_url', 'redis://localhost:6379/0'))
result_backend = os.environ.get('CELERY_RESULT_BACKEND', os.environ.get('result_backend', 'redis://localhost:6379/0'))

celery = Celery('content_plan',
                broker=broker_url,
                backend=result_backend,
                include=['apps.content_plan.tasks'])

# Task settings
celery.conf.task_serializer = 'json'
celery.conf.result_serializer = 'json'
celery.conf.accept_content = ['json']
celery.conf.task_time_limit = 3600  # 1 hour
celery.conf.task_soft_time_limit = 3300  # 55 minutes
celery.conf.worker_prefetch_multiplier = 1  # One task per worker at a time
celery.conf.task_acks_late = True  # Tasks are acknowledged after execution
celery.conf.task_reject_on_worker_lost = True
celery.conf.task_default_queue = 'content_plan'

# Define custom queue for initial processing only
# Theme selection will be handled by direct threads, not Celery
content_plan_exchange = Exchange('content_plan', type='direct')
celery.conf.task_queues = (
    Queue('content_plan', content_plan_exchange, routing_key='content_plan'),
)

# Define task routing
celery.conf.task_routes = {
    'apps.content_plan.tasks.process_workflow_task': {'queue': 'content_plan'},
}

# Configure task retries
celery.conf.task_acks_on_failure_or_timeout = False

# Called when Celery logger is set up
@after_setup_logger.connect
def setup_loggers(logger, *args, **kwargs):
    logging.info("Setting up Celery logging")

class FlaskTask(Task):
    """Task with Flask app context"""
    abstract = True
    
    def __call__(self, *args, **kwargs):
        from apps import create_app
        with create_app().app_context():
            return self.run(*args, **kwargs)

def init_celery(app):
    """Initialize Celery with Flask app context"""
    class ContextTask(Task):
        def __call__(self, *args, **kwargs):
            with app.app_context():
                return self.run(*args, **kwargs)
    
    celery.Task = ContextTask
    return celery