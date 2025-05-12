from apps.content_plan.tasks import celery

# This file is used by Render to start the Celery worker
# It simply imports and re-exports the Celery instance from your tasks module 