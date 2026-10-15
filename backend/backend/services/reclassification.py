"""Atomic triage changes and fenced retrieval-scope updates, without source deletion."""
from sqlalchemy import select
from backend.models import KnowledgeEvent


def reset_event(event, detail):
    event.status, event.attempts, event.detail = "pending", 0, detail
    event.lease_token = event.lease_until = event.available_at = event.last_sent_at = None


def apply_assessment(db, document, assessment):
    """Caller holds the document lock; source hash, evidence and revisions are immutable here."""
    previous = document.ai_relevant
    changed = document.relevance != assessment or previous != assessment["relevant"]
    document.relevance, document.ai_relevant = assessment, assessment["relevant"]
    if previous == document.ai_relevant:
        return {"changed": changed, "scope_changed": False, "event_id": None}
    events = list(db.scalars(select(KnowledgeEvent).where(KnowledgeEvent.document_id == document.id,
        KnowledgeEvent.content_hash == document.content_hash).with_for_update()))
    operation = "update" if document.ai_relevant else "delete"
    selected = next((row for row in events if row.operation == operation), None)
    for row in events:
        if row is selected:
            continue
        row.status = "superseded"
        row.detail = {**row.detail, "superseded_reason": "retrieval_scope_changed", "triage_method": assessment["method"]}
        row.lease_token = row.lease_until = row.available_at = row.last_sent_at = None
    detail = {"reason": "relevance_reclassification", "triage_method": assessment["method"],
        "previous_ai_relevant": previous, "ai_relevant": document.ai_relevant, "is_demo": document.is_demo,
        "source_document_retained": True, "topic_scope": assessment.get("topic_scope")}
    if selected is None:
        selected = KnowledgeEvent(document_id=document.id, content_hash=document.content_hash, operation=operation)
        db.add(selected)
    reset_event(selected, detail)
    document.indexed_at = None
    db.flush()
    return {"changed": changed, "scope_changed": True, "event_id": str(selected.id), "operation": operation}
