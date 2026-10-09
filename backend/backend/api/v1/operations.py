"""Sanitized local monitoring, measured latencies and explicit data coverage."""
from datetime import timedelta
from fastapi import APIRouter, Depends, Query
from uuid import UUID
from pydantic import BaseModel, Field, ConfigDict
from backend.models import RuntimeObservation, RuntimeSample
from backend.services.observation import start_observation, observation_summary
from backend.api.dependencies import require_record
from sqlalchemy import func, select
from backend.api.dependencies import Database
from backend.core.config import get_settings
from backend.core.security import require_admin
from backend.models import AgentInvocation, DataSource, Document, Evidence, KnowledgeEvent, Outbox, TaskRun
from backend.services.jobs import utc_now
from backend.services.freshness import freshness_snapshot, source_observation

router = APIRouter(prefix="/ops", tags=["运行监控"], dependencies=[Depends(require_admin)])


class ObservationRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    hours: int = Field(default=48, ge=1, le=168, strict=True)


@router.post("/observations", status_code=202)
def begin_observation(body: ObservationRequest, db: Database):
    """启动/复用持久化观察窗口；由现有唯一 beat 每分钟采样。"""
    row = start_observation(db, utc_now(), body.hours)
    db.commit()
    return {"observation_id": row.id, "started_at": row.started_at, "ends_at": row.ends_at,
            "status": "observing", "sample_seconds": row.sample_seconds}


@router.get("/observations/{observation_id}")
def read_observation(observation_id: UUID, db: Database):
    row = require_record(db, RuntimeObservation, observation_id)
    samples = list(db.scalars(select(RuntimeSample).where(RuntimeSample.observation_id == row.id)
        .order_by(RuntimeSample.measured_at)))
    return observation_summary(row, samples, utc_now())


@router.get("/observations/{observation_id}/samples")
def observation_samples(observation_id: UUID, db: Database, page: int = Query(1, ge=1),
                        page_size: int = Query(20, ge=1, le=100)):
    require_record(db, RuntimeObservation, observation_id)
    query = select(RuntimeSample).where(RuntimeSample.observation_id == observation_id)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    rows = db.scalars(query.order_by(RuntimeSample.measured_at).offset((page - 1) * page_size).limit(page_size)).all()
    return {"items": [{"measured_at": r.measured_at, "detail": r.detail} for r in rows],
            "page": page, "page_size": page_size, "total": total}


@router.get("/freshness")
def freshness(db: Database, hours: int = Query(default=48, ge=1, le=720),
              ai_relevant: bool | None = True):
    """真实来源时效、最近48小时数据、退避与索引积压；需要管理鉴权。"""
    return freshness_snapshot(db, utc_now(), hours, ai_relevant)


def distribution(values):
    if not values:
        return {"count": 0, "p50": None, "p95": None, "max": None}
    values = sorted(values)
    import math
    return {"count": len(values), "p50": values[max(0, math.ceil(len(values) * .5) - 1)],
            "p95": values[max(0, math.ceil(len(values) * .95) - 1)], "max": values[-1]}


@router.get("/metrics")
def metrics(db: Database):
    now = utc_now()
    sources = db.scalars(select(DataSource)).all()
    docs = db.scalars(select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None))).all()
    coverage = []
    for source in sources:
        if source.adapter == "offline":
            continue
        matching = [d for d in docs if d.source_id == source.id]
        latest = db.scalar(select(TaskRun).where(TaskRun.source_id == source.id).order_by(TaskRun.created_at.desc()).limit(1))
        coverage.append({"adapter": source.adapter, "category": source.category, "enabled": source.enabled,
            "documents": len(matching), "ai_relevant_documents": sum(d.ai_relevant for d in matching),
            "last_success_at": db.scalar(select(func.max(TaskRun.finished_at)).where(
                TaskRun.source_id == source.id, TaskRun.status == "succeeded")),
            "last_task_status": latest.status if latest else None,
            "last_error_code": latest.error_code if latest else None})
        coverage[-1].update(source_observation(db, source, now))
    fresh, historical, missing, negative, imprecise = [], [], 0, 0, 0
    for doc in docs:
        if doc.published_at is None:
            missing += 1
            continue
        if doc.metadata_json.get("date_precision") is not None:
            imprecise += 1
            continue
        delay = (doc.first_collected_at - doc.published_at).total_seconds()
        if delay < 0:
            negative += 1
        elif delay > 86400:
            historical.append(delay)
        else:
            fresh.append(delay)
    known = dict(db.execute(select(Evidence.field, func.count()).join(Document).where(
        Document.is_demo.is_(False), Document.ai_relevant.is_(True), Document.deleted_at.is_(None),
        Evidence.content_hash == Document.content_hash, Evidence.state == "known")
        .group_by(Evidence.field)).all())
    events = dict(db.execute(select(KnowledgeEvent.status, func.count()).join(Document).where(
        Document.is_demo.is_(False)).group_by(KnowledgeEvent.status)).all())
    durations = list(db.scalars(select(AgentInvocation.duration_ms).where(AgentInvocation.duration_ms.is_not(None))))
    outbox = db.scalar(select(func.count()).select_from(Outbox).where(Outbox.sent_at.is_(None)))
    alerts = []
    if outbox:
        alerts.append({"code": "outbox_pending", "count": outbox})
    if events.get("failed"):
        alerts.append({"code": "index_failed", "count": events["failed"]})
    for row in coverage:
        if row["enabled"] and row["stale"]:
            alerts.append({"code": "source_stale", "adapter": row["adapter"]})
    settings = get_settings()
    return {"measured_at": now, "real_source_coverage": coverage,
        "configured_category_count": len({r["category"] for r in coverage}),
        "collected_category_count": len({r["category"] for r in coverage if r["documents"]}),
        "ai_relevant_category_count": len({r["category"] for r in coverage if r["ai_relevant_documents"]}),
        "document_types": dict(db.execute(select(Document.document_type, func.count()).where(
            Document.is_demo.is_(False), Document.deleted_at.is_(None)).group_by(Document.document_type)).all()),
        "known_evidence_fields": known, "knowledge_event_states": events,
        "publish_to_first_collection_seconds": {"recent_delivery_cohort": distribution(fresh),
            "historical_backfill_cohort": distribution(historical), "missing_published_time": missing,
            "negative_time_count": negative, "definition": "delay <=24h separated from historical backfill; not proof of SLA"},
        "imprecise_publication_time_count": imprecise,
        "agent_complete_duration_ms": distribution(durations), "agent_first_token_ms": None,
        "outbox_pending": outbox, "alerts": alerts,
        "providers": {"index": settings.index_provider, "embedding_configured": settings.embedding_configured,
                      "agent_configured": bool(settings.agent_service_url)},
        "accuracy": None, "recall": None,
        "freshness": freshness_snapshot(db, now),
        "measurement_limit": "No independently reviewed labelled evaluation or long-running SLA yet"}
