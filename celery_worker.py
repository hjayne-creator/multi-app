from apps import create_app
from apps.content_plan.tasks import celery

# Create the Flask app
app = create_app()

# This file is used by Render to start the Celery worker
# It imports and re-exports the Celery instance from your tasks module 