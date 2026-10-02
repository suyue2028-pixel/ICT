"""Logical deletion never removes business rows and cannot silently reappear."""
from backend.api.v1.documents import invalidate_document
from backend.api.v1.ai import keyword_search
from backend.models import DocumentRevision, KnowledgeEvent
from backend.schemas.contracts import QuestionRequest
from backend.services.jobs import maintain_index_events
from sqlalchemy import select
from test_agent_upgrade import db, evidence_document


def test_logical_invalidation_is_idempotent_and_retains_history(db):
    _, doc = evidence_document(db)
    revision = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == doc.id))
    first = invalidate_document(doc.id, db)
    repeat = invalidate_document(doc.id, db)
    assert first["event_id"] == repeat["event_id"]
    assert db.get(DocumentRevision, revision.id) is not None
    assert not keyword_search(QuestionRequest(question="unsafe"), db, True).citations
    maintain_index_events(db, None)
    event = db.get(KnowledgeEvent, first["event_id"])
    assert event.operation == "delete" and event.status == "invalidated"
