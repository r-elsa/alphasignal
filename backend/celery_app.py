"""The job queue that connects the backend to the agent workers."""
import os

from celery import Celery
from dotenv import load_dotenv

load_dotenv()

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery("alphasignal", broker=REDIS_URL, backend=REDIS_URL)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    result_expires=3600,
    worker_prefetch_multiplier=1,  # a busy worker doesn't hold jobs another worker could run
    task_publish_retry=False,      # if Redis is down, say so instead of hanging
    broker_connection_retry_on_startup=True,
    broker_transport_options={"socket_connect_timeout": 3},
    redis_socket_connect_timeout=3,
)
