"""Synthetic triage examples and rollback-only retrieval invalidation tests."""
from datetime import timedelta
from copy import deepcopy
import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_agent_upgrade import db, client, evidence_document, answer
from backend.core.config import get_settings
from backend.models import DocumentRevision, KnowledgeEvent
from backend.schemas.contracts import RawRecord, KnowledgeAck
from backend.services.pipeline import relevance, ingest_batch
from backend.services.reclassification import apply_assessment
from backend.services import agent_gateway
from backend.services.jobs import utc_now, maintain_index_events
from backend.api.v1.indexing import claim_events, ClaimRequest
from backend.api.v1.ai import acknowledge_event
from backend.collectors.base import CollectionBatch
from backend.models import TaskRun


@pytest.mark.parametrize("body,expected", [
    ("The grant programme supports AI courses, Cyber security jobs and Quantum computing apprenticeships.", False),
    ("Merlin is a Fiat-Shamir cryptographic transcript tool. Secure hashes prevent attacks.", False),
    ("Nemo is a cartoon fish. The aquarium has security cameras.", False),
    ("Riva builds boats with improved safety. This article describes the shipyard.", False),
    ("NVIDIA Merlin has an unsafe deserialization vulnerability in its recommendation pipeline.", True),
    ("NVIDIA NeMo Framework is affected by a remote code execution security vulnerability.", True),
    ("Riva speech recognition has a security vulnerability affecting the ASR service.", True),
    ("LLM-105 is an energetic material. Molecular force fields predict its safety under pressure.", False),
    ("A neural network predicts mechanical properties of injection molded plastic parts.", False),
    ("A neural network optimizes hydrate inhibitor injection rates for pipeline safety.", False),
    ("AI cyber security guidance explains how to protect model weights.", True),
    ("This policy regulates the use of artificial intelligence and sets out AI risks.", True),
    ("The artificial intelligence risk management framework improves trustworthy systems.", True),
    ("We use AI in our security reviews to detect vulnerabilities in code.", True),
    ("Malware detection based on machine learning improves mobile security.", True),
    ("Face recognition uses deep learning to implement door access and home security.", True),
    ("A PUF resists machine learning-based modeling attacks on hardware.", True),
    ("Language models may leak private user data through their generated responses.", True),
    ("LLMs exhibit instruction hierarchy violations and adversarial prompt injections.", True),
    ("Prompt-injection detectors fail on malicious instructions in retrieved documents.", True),
    ("AI is used for forecasting energy consumption. National energy security improves.", False),
    ("We offer AI art styles and a free image generator. Sign up today.", False),
])
def test_topic_relation_and_polysemy(body, expected):
    record = RawRecord(external_id="fictional-relevance", title="Synthetic regression example", content=body,
        document_type="policy" if "policy" in body.lower() or "framework" in body.lower() else "article",
        source_url="https://example.invalid/relevance", is_demo=True, content_completeness="summary")
    assert relevance(record)["relevant"] is expected


def test_long_abstract_keeps_model_security_and_adjacent_scopes_are_explicit():
    prefix = "LLMs perform inference. " + "We describe experimental controls and benchmark settings. " * 20
    record = RawRecord(external_id="fictional-long", title="Synthetic long abstract",
        content=prefix + "Steering creates a prompt-injection attack surface.", document_type="paper",
        source_url="https://example.invalid/long", is_demo=True, content_completeness="summary")
    assert relevance(record)["relevant"]
    record.content = "A PUF resists machine learning-based modeling attacks."
    assert relevance(record)["topic_scope"] == "ai_enabled_attacks"
    record.content = "Face recognition uses deep learning for door access and home security."
    assert relevance(record)["topic_scope"] == "ai_for_physical_security"


def assessment(doc, relevant):
    result = relevance(RawRecord.model_validate(doc.raw_payload))
    return {**result, "relevant": relevant, "reason_code": "synthetic_scope_test"}


def test_demotion_keeps_source_and_versions_and_is_idempotent(db):
    _, doc = evidence_document(db)
    digest, payload, before_versions = doc.content_hash, deepcopy(doc.raw_payload), db.scalar(select(func.count()).select_from(DocumentRevision))
    initial = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id))
    initial.status = "indexed"
    result = apply_assessment(db, doc, assessment(doc, False))
    assert result["scope_changed"] and result["operation"] == "delete"
    assert initial.status == "superseded" and doc.deleted_at is None
    assert doc.raw_payload == payload and doc.content_hash == digest
    assert db.scalar(select(func.count()).select_from(DocumentRevision)) == before_versions
    event_count = db.scalar(select(func.count()).select_from(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id))
    assert not apply_assessment(db, doc, assessment(doc, False))["scope_changed"]
    assert db.scalar(select(func.count()).select_from(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id)) == event_count


def test_external_scope_delete_ack_and_promotion_fence_stale_leases(db, monkeypatch):
    _, doc = evidence_document(db)
    settings = get_settings().model_copy(update={"index_provider": "external"})
    monkeypatch.setattr("backend.api.v1.indexing.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: settings)
    calls = []
    monkeypatch.setattr(agent_gateway, "verify_external_index", lambda *args: calls.append(args))
    apply_assessment(db, doc, assessment(doc, False))
    delete = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id, KnowledgeEvent.operation == "delete"))
    delete.created_at = utc_now() - timedelta(days=1000)
    db.flush()
    claimed = claim_events(ClaimRequest(is_demo=True, limit=1), db)["items"]
    assert claimed[0]["event_id"] == delete.id
    old_lease = delete.lease_token
    assert acknowledge_event(delete.id, KnowledgeAck(status="invalidated", detail={"index_id": "synthetic-delete"}, lease_token=old_lease), db)["status"] == "invalidated"
    assert calls and doc.deleted_at is None
    promoted = apply_assessment(db, doc, assessment(doc, True))
    assert promoted["operation"] == "update" and delete.status == "superseded"
    with pytest.raises(HTTPException):
        acknowledge_event(delete.id, KnowledgeAck(status="invalidated", detail={"index_id": "synthetic-delete"}, lease_token=old_lease), db)
    update_event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id, KnowledgeEvent.operation == "update"))
    update_event.created_at = utc_now() - timedelta(days=1001)
    db.flush()
    claimed = claim_events(ClaimRequest(is_demo=True, limit=1), db)["items"]
    assert claimed[0]["event_id"] == update_event.id
    update_lease = update_event.lease_token
    apply_assessment(db, doc, assessment(doc, False))
    with pytest.raises(HTTPException):
        acknowledge_event(update_event.id, KnowledgeAck(status="indexed", detail={"index_id": "synthetic-index"}, lease_token=update_lease), db)


@pytest.mark.parametrize("provider,expected_status", [("internal", "invalidated"), ("external", "pending")])
def test_scope_delete_and_new_answer_reject_excluded_sources(db, monkeypatch, provider, expected_status):
    settings = get_settings().model_copy(update={"index_provider": provider})
    monkeypatch.setattr("backend.services.jobs.get_settings", lambda: settings)
    src, doc = evidence_document(db)
    result = answer(src, doc)
    apply_assessment(db, doc, assessment(doc, False))
    with pytest.raises(HTTPException) as error:
        agent_gateway.validate_answer(db, result, True)
    assert error.value.detail == "agent_source_scope_or_version_invalid"
    maintain_index_events(db, None)
    deletion = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id, KnowledgeEvent.operation == "delete"))
    assert deletion.status == expected_status and doc.deleted_at is None


def test_unchanged_ingest_publishes_reclassification_and_retains_hash(db):
    source, doc = evidence_document(db)
    digest = doc.content_hash
    doc.relevance, doc.ai_relevant = {"method": "previous-rule", "relevant": False}, False
    task = TaskRun(source_id=source.id, attempts=1, status="running")
    db.add(task); db.flush()
    result = ingest_batch(db, source, CollectionBatch([RawRecord.model_validate(doc.raw_payload)], {}), task)
    assert result["unchanged"] == 1 and doc.ai_relevant and doc.content_hash == digest
    assert db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id, KnowledgeEvent.operation == "update")).status == "pending"
