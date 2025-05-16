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

# Define custom queues
content_plan_exchange = Exchange('content_plan', type='direct')
celery.conf.task_queues = (
    Queue('content_plan', content_plan_exchange, routing_key='content_plan'),
    Queue('content_plan.theme_selection', content_plan_exchange, routing_key='content_plan.theme_selection'),
)

# Define task routing
celery.conf.task_routes = {
    'apps.content_plan.tasks.process_workflow_task': {'queue': 'content_plan'},
    'apps.content_plan.tasks.continue_workflow_after_selection_task': {'queue': 'content_plan.theme_selection'},
}

# Configure task retries
celery.conf.task_acks_on_failure_or_timeout = False
celery.conf.broker_transport_options = {
    'visibility_timeout': 4 * 3600,  # 4 hours
    'max_retries': 5,
    'interval_start': 0,
    'interval_step': 2,
    'interval_max': 30,
}

class FlaskTask(Task):
    """Base task for Flask integration."""
    abstract = True
    
    def __call__(self, *args, **kwargs):
        # Create the Flask context if needed
        if not self.flask_app:
            from apps import create_app
            self.flask_app = create_app()
            
        with self.flask_app.app_context():
            return self.run(*args, **kwargs)
            
    def after_return(self, status, retval, task_id, args, kwargs, einfo):
        """Handler called after the task returns."""
        if status == 'FAILURE':
            logger.error(f"Task {self.name}[{task_id}] failed: {retval if isinstance(retval, dict) else 'Unknown error'}")
            if einfo:
                logger.error(f"Exception info: {einfo}")
                
    def on_failure(self, exc, task_id, args, kwargs, einfo):
        """Error handler."""
        logger.error(f"Task {self.name}[{task_id}] failed: {exc}")
        logger.error(f"Args: {args}, Kwargs: {kwargs}")
        if einfo:
            logger.error(f"Exception traceback: {einfo}")
            
    flask_app = None

def init_celery(app=None):
    """Initialize Celery with a Flask app."""
    logger.info("Initializing Celery with Flask app")
    
    # Update the base task class to work with Flask context
    celery.Task = FlaskTask
    
    # If app provided, store it on the task class
    if app:
        celery.Task.flask_app = app
        
    # Log Celery configuration
    logger.info(f"Celery broker URL: {celery.conf.broker_url}")
    logger.info(f"Celery result backend: {celery.conf.result_backend}")
    logger.info(f"Celery task routes: {celery.conf.task_routes}")
    
    return celery

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