from datetime import timedelta
from time import perf_counter
from uuid import UUID, uuid4

from sqlalchemy import select, text

from backend.collectors.registry import get_collector
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, KnowledgeEvent, Outbox, TaskRun, TaskStep, VerificationProbe
from backend.core.config import get_settings
from backend.services.semantic import EmbeddingError, embed_texts, split_text, store_chunks
from backend.services.jobs import maintain_jobs, utc_now
from backend.services.pipeline import ingest_batch
from backend.tasks.celery_app import celery_app


@celery_app.task(name="backend.collect")
def collect(task_id: str):
    task_uuid, token = UUID(task_id), uuid4()
    with get_session_factory()() as db, db.begin():
        task = db.scalar(select(TaskRun).where(TaskRun.id == task_uuid).with_for_update())
        if task is None or task.status != "queued" or task.attempts >= 3:
            return {"ignored": True}
        outbox = db.scalar(select(Outbox).where(Outbox.task_id == task_uuid))
        if outbox.available_at is not None and outbox.available_at > utc_now():
            return {"ignored": True}
        task.status, task.lease_token = "running", token
        task.started_at, task.lease_until = utc_now(), utc_now() + timedelta(seconds=360)
        task.attempts += 1
        source = db.get(DataSource, task.source_id)
        adapter, cursor, config = source.adapter, dict(source.cursor), dict(source.config)
    stage = "collect"
    stage_started = perf_counter()
    try:
        batch = get_collector(adapter, config).collect(cursor)
        with get_session_factory()() as db, db.begin():
            task = db.scalar(select(TaskRun).where(TaskRun.id == task_uuid).with_for_update())
            if task.lease_token != token or task.status != "running":
                return {"ignored": True}
            db.add(TaskStep(task_id=task.id, name="collect", attempt=task.attempts,
                            status="succeeded", detail={"received": len(batch.records),
                                                        "duration_ms": round((perf_counter() - stage_started) * 1000, 3)}))
        stage = "pipeline"
        stage_started = perf_counter()
        with get_session_factory()() as db, db.begin():
            task = db.scalar(select(TaskRun).where(TaskRun.id == task_uuid).with_for_update())
            if task.lease_token != token or task.status != "running":
                return {"ignored": True}
            source = db.scalar(select(DataSource).where(DataSource.id == task.source_id).with_for_update())
            # 框架阶段串行提交归一化实体，避免跨来源同一漏洞并发创建。
            db.execute(text("SELECT pg_advisory_xact_lock(7429031)"))
            task.result = ingest_batch(db, source, batch, task)
            source.next_run_at = utc_now() + timedelta(seconds=60 if batch.more else source.interval_seconds)
            task.status, task.finished_at = "succeeded", utc_now()
            task.lease_token, task.lease_until = None, None
            result = task.result
        return result
    except Exception as error:
        # 保存稳定错误标识；禁止将可能带密码的连接异常原文写入接口或日志。
        import httpx
        if isinstance(error, httpx.HTTPStatusError):
            error_code = f"source_http_{error.response.status_code}"
        elif isinstance(error, httpx.RequestError):
            error_code = "source_network_error"
        else:
            error_code = f"processing_{type(error).__name__}"[:100]
        with get_session_factory()() as db, db.begin():
            task = db.scalar(select(TaskRun).where(TaskRun.id == task_uuid).with_for_update())
            if task.lease_token == token and task.status == "running":
                task.error_code = error_code
                task.lease_token, task.lease_until = None, None
                db.add(TaskStep(task_id=task.id, name=stage, attempt=task.attempts,
                                status="failed", detail={"error_code": error_code,
                                                         "duration_ms": round((perf_counter() - stage_started) * 1000, 3)}))
                if task.attempts < 3:
                    task.status = "queued"
                    outbox = db.scalar(select(Outbox).where(Outbox.task_id == task.id).with_for_update())
                    outbox.sent_at = None
                    outbox.available_at = utc_now() + timedelta(seconds=10 * (2 ** (task.attempts - 1)))
                else:
                    task.status, task.finished_at = "failed", utc_now()
        return {"failed_or_retrying": True, "error_code": error_code}


@celery_app.task(name="backend.maintenance")
def maintenance():
    with get_session_factory()() as db, db.begin():
        return maintain_jobs(db, celery_app)


@celery_app.task(name="backend.verify_tick")
def verify_tick():
    """仅验证 beat→Redis→worker→PostgreSQL，不能计为业务采集任务。"""
    with get_session_factory()() as db, db.begin():
        probe = db.scalar(select(VerificationProbe).where(
            VerificationProbe.key == "beat-heartbeat"
        ).with_for_update())
        if probe is None:
            db.add(VerificationProbe(key="beat-heartbeat", count=1))
        else:
            probe.count += 1
    return {"verification_only": True}


@celery_app.task(name="backend.index_event", soft_time_limit=720, time_limit=780)
def index_event(event_id: str):
    """Index one current document version; duplicate deliveries cannot commit twice."""
    event_uuid, token = UUID(event_id), uuid4()
    settings = get_settings()
    if getattr(settings, "index_provider", "internal") != "internal" or not settings.embedding_configured:
        return {"ignored": True, "reason": "embedding_not_configured"}
    with get_session_factory()() as db, db.begin():
        event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.id == event_uuid).with_for_update())
        if event is None or event.status != "queued" or event.attempts >= 3:
            return {"ignored": True}
        document = db.get(Document, event.document_id)
        if document is None or document.deleted_at is not None or document.content_hash != event.content_hash:
            event.status, event.detail = "superseded", {"reason": "document_version_changed"}
            return {"ignored": True, "reason": "superseded"}
        event.status, event.lease_token = "indexing", token
        event.lease_until = utc_now() + timedelta(seconds=900)
        event.attempts += 1
        source_text, expected_hash = document.content, document.content_hash
    try:
        chunks = split_text(source_text)
        if not chunks:
            raise EmbeddingError("embedding_empty_document")
        vectors = embed_texts([chunk.excerpt for chunk in chunks], settings)
        with get_session_factory()() as db, db.begin():
            event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.id == event_uuid).with_for_update())
            if event.lease_token != token or event.status != "indexing":
                return {"ignored": True}
            document = db.scalar(select(Document).where(Document.id == event.document_id).with_for_update())
            if document is None or document.deleted_at is not None or document.content_hash != expected_hash:
                event.status, event.detail = "superseded", {"reason": "document_version_changed"}
                event.lease_token = event.lease_until = None
                return {"ignored": True, "reason": "superseded"}
            count = store_chunks(db, document, chunks, vectors, settings)
            event.status = "indexed"
            event.detail = {"index_id": f"postgres-array:{event.id}", "chunks": count,
                            "model": settings.embedding_model, "dimensions": settings.embedding_dimensions,
                            "method": "exact_cosine"}
            event.lease_token = event.lease_until = event.available_at = None
        return {"indexed": count}
    except Exception as error:
        code = error.code if isinstance(error, EmbeddingError) else f"index_{type(error).__name__}"[:100]
        with get_session_factory()() as db, db.begin():
            event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.id == event_uuid).with_for_update())
            if event.lease_token == token and event.status == "indexing":
                event.status = "pending" if event.attempts < 3 else "failed"
                event.detail = {"error_code": code}
                event.available_at = (utc_now() + timedelta(seconds=10 * (2 ** (event.attempts - 1)))
                                      if event.status == "pending" else None)
                event.lease_token = event.lease_until = None
        return {"failed_or_retrying": True, "error_code": code}
