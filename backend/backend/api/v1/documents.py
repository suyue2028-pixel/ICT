from uuid import UUID
from datetime import datetime
from typing import Literal
from typing import Annotated
import json

from fastapi import APIRouter, Depends, Query, Path
from sqlalchemy import or_, select

from backend.api.dependencies import Database, PageNumber, PageSize, paginate, require_record
from backend.models import Document, DocumentRevision, Evidence, KnowledgeEvent
from backend.core.security import require_admin
from backend.services.jobs import utc_now
from backend.schemas.contracts import DocumentRead, Page
from backend.schemas.traceability import DocumentAssociations, DocumentVersionProof
from backend.services.freshness import apply_time_filter, TIME_COLUMNS
from backend.services.enrichment import structured_enrichment

router = APIRouter(prefix="/documents", tags=["原始文档"])


@router.get("", response_model=Page[DocumentRead])
def list_documents(db: Database, page: PageNumber = 1, page_size: PageSize = 20,
                   is_demo: bool = False, source_id: UUID | None = None,
                   document_type: str | None = None, ai_relevant: bool | None = None,
                   keyword: str | None = Query(default=None, max_length=200),
                   time_basis: Literal["published", "updated", "discovered", "changed"] = "published",
                   recent_hours: int | None = Query(default=None, ge=1, le=720),
                   time_from: datetime | None = None, time_to: datetime | None = None):
    query = select(Document).where(Document.is_demo == is_demo, Document.deleted_at.is_(None))
    if source_id:
        query = query.where(Document.source_id == source_id)
    if document_type:
        query = query.where(Document.document_type == document_type)
    if ai_relevant is not None:
        query = query.where(Document.ai_relevant == ai_relevant)
    if keyword:
        query = query.where(or_(Document.title.icontains(keyword, autoescape=True),
                               Document.content.icontains(keyword, autoescape=True),
                               Document.external_id.icontains(keyword, autoescape=True)))
    query = apply_time_filter(query, time_basis, recent_hours, time_from, time_to)
    order = TIME_COLUMNS[time_basis] if any(v is not None for v in (recent_hours, time_from, time_to)) else Document.created_at
    return paginate(db, query.order_by(order.desc().nulls_last(), Document.id), DocumentRead, page, page_size)


@router.get("/{document_id}")
def document_detail(document_id: UUID, db: Database):
    document = require_record(db, Document, document_id)
    revisions = db.scalars(select(DocumentRevision).where(DocumentRevision.document_id == document_id)
                          .order_by(DocumentRevision.created_at)).all()
    return {**DocumentRead.model_validate(document).model_dump(mode="json"), "content": document.content,
            "deleted_at": document.deleted_at,
            "raw_payload": document.raw_payload,
            "revisions": [{"content_hash": row.content_hash, "payload": row.payload,
                           "created_at": row.created_at} for row in revisions]}


@router.delete("/{document_id}", status_code=202, dependencies=[Depends(require_admin)])
def invalidate_document(document_id: UUID, db: Database):
    """Logical deletion only; preserve documents/revisions/evidence and publish a tombstone."""
    document = db.scalar(select(Document).where(Document.id == document_id).with_for_update())
    if document is None:
        from fastapi import HTTPException
        raise HTTPException(404, "record_not_found")
    event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == document.id,
        KnowledgeEvent.content_hash == document.content_hash, KnowledgeEvent.operation == "delete"))
    already_deleted = document.deleted_at is not None
    document.deleted_at = document.deleted_at or utc_now()
    if event is None:
        event = KnowledgeEvent(document_id=document.id, content_hash=document.content_hash,
            operation="delete", status="pending", detail={"reason": "logical_invalidation", "is_demo": document.is_demo})
        db.add(event)
        db.flush()
    elif not already_deleted and event.status not in {"pending", "queued", "indexing"}:
        from backend.services.reclassification import reset_event
        reset_event(event, {"reason": "logical_invalidation", "is_demo": document.is_demo})
    db.commit()
    return {"document_id": document.id, "event_id": event.id, "status": event.status, "deleted_at": document.deleted_at}


@router.get("/{document_id}/versions/{content_hash}", response_model=DocumentVersionProof)
def document_version(document_id: UUID, content_hash: Annotated[str, Path(pattern=r"^[a-f0-9]{64}$")], db: Database):
    from backend.services.traceability import original_version, version_associations
    document = require_record(db, Document, document_id)
    revision = original_version(db, document, content_hash)
    return {"document_id": document.id, "content_hash": revision.content_hash,
        "is_demo": document.is_demo, "is_current": document.deleted_at is None and document.content_hash == content_hash,
        "deleted_at": document.deleted_at, "version_created_at": revision.created_at,
        "source_url": revision.payload.get("source_url"), "title": revision.payload.get("title"),
        "content": revision.payload.get("content"), "revision": revision.payload,
        "revision_json": json.dumps(revision.payload, ensure_ascii=False),
        "associations": version_associations(db, document, revision),
        "offset_unit": "unicode_code_points"}


@router.get("/{document_id}/associations", response_model=DocumentAssociations)
def document_associations(document_id: UUID, db: Database,
        content_hash: str | None = Query(default=None, pattern=r"^[a-f0-9]{64}$")):
    from backend.services.traceability import original_version, version_associations
    document = require_record(db, Document, document_id)
    revision = original_version(db, document, content_hash or document.content_hash)
    return {"document_id": document.id, "content_hash": revision.content_hash, "is_demo": document.is_demo,
        "is_current": document.deleted_at is None and document.content_hash == revision.content_hash,
        "items": version_associations(db, document, revision),
        "scope": "Explicit identifiers and conservative exact fallbacks; co-mentioned article identifiers are separate entities."}


@router.get("/{document_id}/enrichment")
def document_enrichment(document_id: UUID, db: Database):
    """Source absence is unknown; uncalled external enrichers remain unprocessed."""
    document = require_record(db, Document, document_id)
    rows = db.scalars(select(Evidence).where(Evidence.document_id == document_id,
        Evidence.content_hash == document.content_hash)).all()
    dimensions = ("affected", "cwe", "cvss", "remediation", "analysis", "papers", "poc", "exploitation")
    source = {}
    external = {}
    for dimension in dimensions:
        matching = [row for row in rows if row.field == dimension]
        source_row = next((row for row in matching if row.method == "source_reported"), None)
        source[dimension] = {"state": source_row.state if source_row else "unknown",
                             "value": source_row.value if source_row else None,
                             "evidence_id": source_row.id if source_row else None}
        external_rows = [row for row in matching if row.method.startswith("external:")]
        external[dimension] = ([{"provider": row.method.removeprefix("external:"),
                                 "state": row.state, "value": row.value,
                                 "evidence_id": row.id} for row in external_rows]
                               if external_rows else [{"state": "unprocessed"}])
    from backend.services.eight_enrichment import summary_dimensions
    from backend.models import TaskRun
    latest = db.scalar(select(TaskRun).where(TaskRun.kind == "enrich",
        TaskRun.result["document_id"].as_string() == str(document.id),
        TaskRun.result["content_hash"].as_string() == document.content_hash).order_by(TaskRun.created_at.desc()).limit(1))
    return {"document_id": document.id, "content_hash": document.content_hash,
            "source": source, "external": external,
            "summary": summary_dimensions(rows, latest),
            "normalized": structured_enrichment(document, rows)}


@router.post("/{document_id}/enrich", status_code=202, dependencies=[Depends(require_admin)])
def enrich_document(document_id: UUID, db: Database, force: bool = False):
    """异步核验现有资料的8类富化；缺失/不适用明确记录，不生成无证据值。"""
    from backend.services.eight_enrichment import create_enrichment_task
    from fastapi.responses import JSONResponse
    task, created = create_enrichment_task(db, require_record(db, Document, document_id), force=force)
    db.commit()
    if task is None:
        return JSONResponse(status_code=200, content={"document_id": str(document_id), "status": "already_processed"})
    return {"id": task.id, "task_id": task.id, "status": task.status, "kind": task.kind,
            "created": created, "document_id": document_id}
