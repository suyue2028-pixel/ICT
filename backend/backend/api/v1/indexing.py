"""External RAG leases events; internal Celery dispatch is disabled in this mode."""
from datetime import timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import or_, select
from backend.api.dependencies import Database
from backend.core.config import get_settings
from backend.core.security import require_internal_write
from backend.models import Document, KnowledgeEvent
from backend.services.jobs import utc_now

router = APIRouter(prefix="/internal/knowledge-events", tags=["外部 RAG 事件租约"],
                   dependencies=[Depends(require_internal_write)])


class ClaimRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    is_demo: bool = False
    limit: int = Field(default=5, ge=1, le=20)


@router.post("/claim")
def claim_events(body: ClaimRequest, db: Database):
    if get_settings().index_provider != "external":
        raise HTTPException(409, "internal_index_provider_active")
    now = utc_now()
    expired = db.scalars(select(KnowledgeEvent).where(KnowledgeEvent.status == "indexing",
        KnowledgeEvent.lease_until < now).with_for_update(skip_locked=True).limit(20)).all()
    for event in expired:
        event.status = "failed" if event.attempts >= 3 else "pending"
        event.lease_token = event.lease_until = None
        event.detail = {"error_code": "external_index_lease_expired"}
    db.flush()
    events = db.scalars(select(KnowledgeEvent).join(Document).where(
        Document.is_demo == body.is_demo,
        or_(Document.ai_relevant.is_(True), KnowledgeEvent.operation == "delete"),
        KnowledgeEvent.status == "pending", KnowledgeEvent.attempts < 3,
        or_(KnowledgeEvent.available_at.is_(None), KnowledgeEvent.available_at <= now))
        .order_by(KnowledgeEvent.created_at).limit(body.limit)
        .with_for_update(of=KnowledgeEvent, skip_locked=True)).all()
    items = []
    for event in events:
        doc = db.get(Document, event.document_id)
        if (doc.content_hash != event.content_hash or
                (doc.deleted_at is not None and event.operation != "delete") or
                (event.operation == "delete" and doc.deleted_at is None and doc.ai_relevant)):
            event.status, event.detail = "superseded", {"reason": "document_version_changed"}
            continue
        event.status, event.lease_token, event.lease_until = "indexing", uuid4(), now + timedelta(minutes=15)
        event.attempts += 1
        items.append({"event_id": event.id, "document_id": doc.id, "content_hash": event.content_hash,
            "operation": event.operation, "is_demo": doc.is_demo, "lease_token": event.lease_token,
            "lease_until": event.lease_until, "document_url": f"/api/v1/tools/documents/{doc.id}"})
    db.commit()
    return {"schema_version": "1", "provider": "external", "items": items}
