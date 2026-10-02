from celery import Celery

from backend.core.config import get_settings

settings = get_settings()
celery_app = Celery(
    "backend_development", broker=settings.redis_url(0), backend=settings.redis_url(1),
    include=["backend.tasks.jobs"],
)
celery_app.conf.update(
    timezone="UTC", enable_utc=True, task_serializer="json", result_serializer="json",
    accept_content=["json"], task_acks_late=True, task_reject_on_worker_lost=True,
    worker_prefetch_multiplier=1, task_soft_time_limit=240, task_time_limit=270,
    broker_connection_retry_on_startup=True, broker_connection_timeout=3,
    broker_transport_options={"visibility_timeout": 360, "socket_timeout": 3, "socket_connect_timeout": 3},
    task_publish_retry=False, result_expires=3600,
    beat_schedule={
        "maintain-outbox-and-schedules": {"task": "backend.maintenance", "schedule": 5.0},
        "development-verification-heartbeat": {
            "task": "backend.verify_tick", "schedule": float(settings.demo_heartbeat_seconds),
        },
    },
)
