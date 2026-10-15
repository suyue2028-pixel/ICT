from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select

from backend.api.dependencies import Database, PageNumber, PageSize, paginate, require_record
from backend.core.security import require_admin
from backend.models import DataSource, TaskRun, TaskStep
from backend.schemas.contracts import Page, TaskRead
from backend.services.jobs import create_task

router = APIRouter(prefix="/tasks", tags=["后台任务"])


@router.get("", response_model=Page[TaskRead])
def list_tasks(db: Database, page: PageNumber = 1, page_size: PageSize = 20,
               source_id: UUID | None = None, status: str | None = None,
               is_demo: bool | None = None):
    query = select(TaskRun).order_by(TaskRun.created_at.desc())
    if source_id:
        query = query.where(TaskRun.source_id == source_id)
    if status:
        query = query.where(TaskRun.status == status)
    if is_demo is not None:
        query = query.where(TaskRun.kind == ("offline_demo" if is_demo else "collect"))
    return paginate(db, query, TaskRead, page, page_size)


@router.get("/{task_id}")
def task_detail(task_id: UUID, db: Database):
    task = require_record(db, TaskRun, task_id)
    steps = db.scalars(select(TaskStep).where(TaskStep.task_id == task_id)
                       .order_by(TaskStep.created_at)).all()
    return {**TaskRead.model_validate(task).model_dump(mode="json"), "steps": [
        {"name": step.name, "attempt": step.attempt, "status": step.status, "detail": step.detail}
        for step in steps
    ]}


@router.post("/{task_id}/retry", response_model=TaskRead, status_code=202,
             dependencies=[Depends(require_admin)])
def retry_task(task_id: UUID, db: Database):
    previous = require_record(db, TaskRun, task_id)
    if previous.status != "failed":
        raise HTTPException(409, "only_failed_task_can_retry")
    if previous.kind == "enrich":
        from backend.models import Document
        from backend.services.eight_enrichment import create_enrichment_task
        task, _ = create_enrichment_task(db, require_record(db, Document, UUID(previous.result["document_id"])), force=True)
    else:
        task = create_task(db, require_record(db, DataSource, previous.source_id))
    db.commit()
    return task
