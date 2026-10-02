from datetime import datetime, timedelta, timezone
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from backend.collectors.registry import SOURCE_DEFINITIONS
from backend.core.config import get_settings
from backend.models import AgentInvocation, DataSource, Document, KnowledgeEvent, Outbox, TaskRun
from backend.services.semantic import EmbeddingError, embedding_endpoint


def utc_now():
    return datetime.now(timezone.utc)


def seed_sources(db: Session):
    for adapter, definition in SOURCE_DEFINITIONS.items():
        existing = db.scalar(select(DataSource).where(DataSource.adapter == adapter))
        if existing is None:
            db.add(DataSource(name=definition["name"], category=definition["category"],
                              adapter=adapter, config={}))
        elif existing.name.endswith("（待实现）"):
            # Rename only untouched scaffold placeholders; preserve user-renamed sources.
            existing.name = definition["name"]


def create_task(db: Session, source: DataSource, idempotency_key: str | None = None,
                enforce_rate_limit: bool = False) -> TaskRun:
    if not SOURCE_DEFINITIONS[source.adapter]["implemented"]:
        raise HTTPException(501, "collector_not_implemented")
    # 来源行锁防止手动触发与定时调度并发创建；部分唯一索引提供最终保障。
    db.execute(select(DataSource.id).where(DataSource.id == source.id).with_for_update())
    if idempotency_key:
        previous = db.scalar(select(TaskRun).where(TaskRun.idempotency_key == idempotency_key))
        if previous:
            if previous.source_id != source.id:
                raise HTTPException(409, "idempotency_key_conflict")
            return previous
    active = db.scalar(select(TaskRun).where(
        TaskRun.source_id == source.id, TaskRun.status.in_(("queued", "running"))
    ))
    if active:
        return active
    if enforce_rate_limit and source.adapter != "offline":
        last_success = db.scalar(select(TaskRun.finished_at).where(
            TaskRun.source_id == source.id, TaskRun.status == "succeeded",
            TaskRun.finished_at.is_not(None)).order_by(TaskRun.finished_at.desc()).limit(1))
        if last_success and last_success > utc_now() - timedelta(seconds=60):
            raise HTTPException(429, "source_rate_limited")
    task = TaskRun(id=uuid4(), source_id=source.id, kind="offline_demo" if source.adapter == "offline" else "collect",
                   idempotency_key=idempotency_key)
    db.add(task)
    db.flush()
    db.add(Outbox(task_id=task.id))
    return task


def maintain_jobs(db: Session, celery_app) -> dict:
    now = utc_now()
    expired_calls = db.scalars(select(AgentInvocation).where(AgentInvocation.status == "running",
        AgentInvocation.created_at < now - timedelta(seconds=180)).with_for_update(skip_locked=True).limit(20)).all()
    for call in expired_calls:
        call.status, call.error_code, call.finished_at = "failed", "agent_lease_expired", now
    expired = db.scalars(select(TaskRun).where(
        TaskRun.status == "running", TaskRun.lease_until < now
    ).with_for_update(skip_locked=True)).all()
    for task in expired:
        task.lease_token, task.lease_until = None, None
        if task.attempts >= 3:
            task.status, task.error_code, task.finished_at = "failed", "worker_lease_expired", now
        else:
            task.status = "queued"
            outbox = db.scalar(select(Outbox).where(Outbox.task_id == task.id).with_for_update())
            outbox.sent_at, outbox.available_at = None, now
    sources = db.scalars(select(DataSource).where(
        DataSource.enabled.is_(True), DataSource.adapter != "offline",
        or_(DataSource.next_run_at.is_(None), DataSource.next_run_at <= now)
    ).with_for_update(skip_locked=True)).all()
    for source in sources:
        if SOURCE_DEFINITIONS[source.adapter]["implemented"]:
            create_task(db, source)
            source.next_run_at = now + timedelta(seconds=source.interval_seconds)
    db.flush()
    pending = db.scalars(select(Outbox).join(TaskRun, TaskRun.id == Outbox.task_id).where(
        TaskRun.status == "queued",
        or_(Outbox.available_at.is_(None), Outbox.available_at <= now),
        or_(Outbox.sent_at.is_(None), Outbox.sent_at < now - timedelta(seconds=60)),
    ).limit(20).with_for_update(of=Outbox, skip_locked=True)).all()
    sent = 0
    for event in pending:
        event.attempts += 1
        try:
            celery_app.send_task("backend.collect", args=[str(event.task_id)], retry=False)
        except Exception:
            # 不记录可能包含连接凭据的异常原文；失败消息保留在 outbox，等待下次发送。
            event.available_at = now + timedelta(seconds=min(event.attempts * 5, 60))
        else:
            event.sent_at, event.available_at = now, None
            sent += 1
    return {"sent": sent, "recovered": len(expired),
            "index": maintain_index_events(db, celery_app)}


def maintain_index_events(db: Session, celery_app) -> dict:
    """At-least-once dispatch; pending events stay pending when publishing fails."""
    settings = get_settings()
    if getattr(settings, "index_provider", "internal") != "internal":
        return {"sent": 0, "reason": "external_index_provider"}
    # Logical invalidation makes every local search exclude the retained vector history.
    deletions = db.scalars(select(KnowledgeEvent).where(KnowledgeEvent.operation == "delete",
        KnowledgeEvent.status == "pending").with_for_update(skip_locked=True).limit(20)).all()
    for deletion in deletions:
        document = db.get(Document, deletion.document_id)
        if document is not None and document.deleted_at is not None:
            deletion.status = "invalidated"
            deletion.detail = {"method": "logical_invalidation", "vectors_retained_for_history": True}
    if not settings.embedding_configured:
        return {"sent": 0, "reason": "embedding_not_configured"}
    try:
        embedding_endpoint(settings)
    except EmbeddingError as error:
        return {"sent": 0, "reason": error.code}
    now = utc_now()
    expired = db.scalars(select(KnowledgeEvent).where(
        KnowledgeEvent.status == "indexing", KnowledgeEvent.lease_until < now,
    ).limit(20).with_for_update(skip_locked=True)).all()
    for event in expired:
        event.lease_token = event.lease_until = None
        if event.attempts >= 3:
            event.status = "failed"
            event.detail = {"error_code": "index_worker_lease_expired"}
        else:
            event.status = "pending"
            event.available_at = now
    pending = db.scalars(select(KnowledgeEvent).where(
        KnowledgeEvent.status.in_(("pending", "queued")),
        or_(KnowledgeEvent.available_at.is_(None), KnowledgeEvent.available_at <= now),
        or_(KnowledgeEvent.status == "pending", KnowledgeEvent.last_sent_at.is_(None),
            KnowledgeEvent.last_sent_at < now - timedelta(seconds=60)),
    ).order_by(KnowledgeEvent.created_at).limit(5).with_for_update(skip_locked=True)).all()
    sent = 0
    for event in pending:
        document = db.get(Document, event.document_id)
        if document is None or document.deleted_at is not None or document.content_hash != event.content_hash:
            event.status = "superseded"
            event.detail = {"reason": "document_version_changed"}
            continue
        try:
            celery_app.send_task("backend.index_event", args=[str(event.id)], retry=False)
        except Exception:
            event.available_at = now + timedelta(seconds=10)
        else:
            event.status = "queued"
            event.last_sent_at = now
            event.available_at = None
            sent += 1
    return {"sent": sent, "recovered": len(expired)}
