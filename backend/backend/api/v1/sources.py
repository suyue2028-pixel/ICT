from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException
from sqlalchemy import select

from backend.api.dependencies import Database, require_record
from backend.collectors.registry import SOURCE_DEFINITIONS
from backend.collectors.live import SourceConfig
from backend.core.security import require_admin
from backend.models import DataSource, TaskRun
from backend.schemas.contracts import SourceCreate, SourcePatch, SourceRead, TaskRead
from backend.services.jobs import create_task

router = APIRouter(prefix="/sources", tags=["数据源"])
Admin = Depends(require_admin)


def validate_config(adapter: str, config: dict) -> dict:
    if adapter == "offline":
        if config:
            raise HTTPException(422, "offline_config_must_be_empty")
        return {}
    try:
        if adapter in {"nvd", "arxiv", "trailofbits", "nist", "govuk", "crossref", "huggingface", "china_policy", "nvidia"}:
            from backend.collectors.extended import ExtendedConfig
            return ExtendedConfig.model_validate(config).model_dump()
        return SourceConfig.validate_for(adapter, config).model_dump()
    except (ValueError, TypeError):
        raise HTTPException(422, "invalid_source_config")


@router.get("", response_model=list[SourceRead])
def list_sources(db: Database):
    return db.scalars(select(DataSource).order_by(DataSource.created_at)).all()


@router.post("", response_model=SourceRead, status_code=201, dependencies=[Admin])
def add_source(payload: SourceCreate, db: Database):
    definition = SOURCE_DEFINITIONS[payload.adapter]
    if payload.adapter == "offline" and payload.enabled:
        raise HTTPException(422, "offline_not_schedulable")
    from backend.services.freshness import DEFAULT_INTERVALS
    seconds = (payload.interval_seconds if "interval_seconds" in payload.model_fields_set
               else DEFAULT_INTERVALS.get(payload.adapter, 3600))
    validate_interval(payload.adapter, seconds)
    source = DataSource(name=payload.name, category=definition["category"],
                        adapter=payload.adapter, interval_seconds=seconds,
                        enabled=payload.enabled,
                        config=validate_config(payload.adapter, payload.config))
    db.add(source)
    db.commit()
    return source


@router.patch("/{source_id}", response_model=SourceRead, dependencies=[Admin])
def update_source(source_id: UUID, payload: SourcePatch, db: Database):
    source = db.scalar(select(DataSource).where(DataSource.id == source_id).with_for_update())
    if source is None:
        raise HTTPException(404, "record_not_found")
    active = db.scalar(select(TaskRun.id).where(
        TaskRun.source_id == source_id, TaskRun.status.in_(("queued", "running"))))
    if active:
        raise HTTPException(409, "source_has_active_task")
    if source.adapter == "offline" and payload.enabled:
        raise HTTPException(422, "offline_not_schedulable")
    if payload.interval_seconds is not None:
        validate_interval(source.adapter, payload.interval_seconds)
    for key, value in payload.model_dump(exclude_none=True).items():
        if key == "config":
            value = validate_config(source.adapter, value)
            source.cursor = {}
        setattr(source, key, value)
    if payload.enabled:
        from backend.services.jobs import utc_now
        source.next_run_at = utc_now()
    elif payload.interval_seconds is not None and source.enabled:
        from datetime import timedelta
        from backend.services.jobs import utc_now
        proposed = utc_now() + timedelta(seconds=source.interval_seconds)
        source.next_run_at = min(source.next_run_at, proposed) if source.next_run_at else utc_now()
    db.commit()
    return source


def validate_interval(adapter, seconds):
    if adapter != "offline" and seconds > 172800:
        raise HTTPException(422, "source_interval_exceeds_48_hours")
    if adapter == "arxiv" and seconds < 86400:
        raise HTTPException(422, "arxiv_daily_cache_interval_required")


@router.post("/{source_id}/collect", response_model=TaskRead, status_code=202, dependencies=[Admin])
def collect_source(source_id: UUID, db: Database,
                   idempotency_key: Annotated[str | None, Header(max_length=128)] = None):
    source = require_record(db, DataSource, source_id)
    task = create_task(db, source, idempotency_key, enforce_rate_limit=True)
    db.commit()
    return task
