from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select

from backend.api.dependencies import Database, PageNumber, PageSize, paginate, require_record
from backend.models import Document, DocumentRevision, Evidence, KnowledgeEvent
from backend.core.security import require_admin
from backend.services.jobs import utc_now
from backend.schemas.contracts import DocumentRead, Page

router = APIRouter(prefix="/documents", tags=["原始文档"])


@router.get("", response_model=Page[DocumentRead])
def list_documents(db: Database, page: PageNumber = 1, page_size: PageSize = 20,
                   is_demo: bool = False, source_id: UUID | None = None,
                   document_type: str | None = None, ai_relevant: bool | None = None):
    query = select(Document).where(Document.is_demo == is_demo, Document.deleted_at.is_(None))
    if source_id:
        query = query.where(Document.source_id == source_id)
    if document_type:
        query = query.where(Document.document_type == document_type)
    if ai_relevant is not None:
        query = query.where(Document.ai_relevant == ai_relevant)
    return paginate(db, query.order_by(Document.created_at.desc()), DocumentRead, page, page_size)


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
    if event is None:
        document.deleted_at = utc_now()
        event = KnowledgeEvent(document_id=document.id, content_hash=document.content_hash,
            operation="delete", status="pending", detail={"reason": "logical_invalidation", "is_demo": document.is_demo})
        db.add(event)
        db.flush()
    db.commit()
    return {"document_id": document.id, "event_id": event.id, "status": event.status, "deleted_at": document.deleted_at}


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
    return {"document_id": document.id, "content_hash": document.content_hash,
            "source": source, "external": external}
