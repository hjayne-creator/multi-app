from apps.content_plan.celery_config import celery, flask_app

# This file is used by Render to start the Celery worker
# It imports and re-exports the Celery instance from your tasks module 