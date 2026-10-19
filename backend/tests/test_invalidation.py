"""Logical deletion never removes business rows and cannot silently reappear."""
from backend.api.v1.documents import invalidate_document
from backend.api.v1.ai import keyword_search
from backend.models import DocumentRevision, KnowledgeEvent
from backend.schemas.contracts import QuestionRequest
from backend.services.jobs import maintain_index_events
from backend.core.config import get_settings
import pytest
from sqlalchemy import select
from test_agent_upgrade import db, evidence_document


@pytest.mark.parametrize("index_provider,expected_status", [
    ("internal", "invalidated"),
    ("external", "pending"),
])
def test_logical_invalidation_is_idempotent_and_retains_history(db, monkeypatch, index_provider, expected_status):
    settings = get_settings().model_copy(update={"index_provider": index_provider,
        "embedding_api_key": None})
    monkeypatch.setattr("backend.services.jobs.get_settings", lambda: settings)
    _, doc = evidence_document(db)
    revision = db.scalar(select(DocumentRevision).where(DocumentRevision.document_id == doc.id))
    first = invalidate_document(doc.id, db)
    repeat = invalidate_document(doc.id, db)
    assert first["event_id"] == repeat["event_id"]
    assert db.get(DocumentRevision, revision.id) is not None
    assert not keyword_search(QuestionRequest(question="unsafe"), db, True).citations
    maintain_index_events(db, None)
    event = db.get(KnowledgeEvent, first["event_id"])
    # External consumers must acknowledge deletion themselves; local maintenance cannot claim it.
    assert event.operation == "delete" and event.status == expected_status


def test_external_index_tombstone_requires_agent_confirmation(db, monkeypatch):
    settings = get_settings().model_copy(update={"index_provider": "external"})
    monkeypatch.setattr("backend.services.jobs.get_settings", lambda: settings)
    _, document = evidence_document(db)
    result = invalidate_document(document.id, db)
    assert maintain_index_events(db, None) == {"sent": 0, "reason": "external_index_provider"}
    event = db.get(KnowledgeEvent, result["event_id"])
    assert event.operation == "delete" and event.status == "pending"
    assert document.deleted_at is not None
