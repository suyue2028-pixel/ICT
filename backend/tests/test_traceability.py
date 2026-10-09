"""Closed-loop proof and recovery regressions; synthetic rows roll back."""
import json
from datetime import timedelta
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select

from test_agent_upgrade import db, client, token, evidence_document, answer
from test_pipeline_integration import source, ingest, record
from backend.core.config import get_settings
from backend.models import (AssociationEvidence, Document, DocumentRevision, KnowledgeEvent,
    Outbox, TaskRun, VulnerabilityAlias, VulnerabilityDocument)
from backend.services import agent_gateway
from backend.services.jobs import create_task, maintain_jobs, utc_now
from backend.services.traceability import locate_quote


def test_association_proof_idempotent_and_multi_cve_article_separate(db, client):
    src = source(db)
    row = record("proof-multi", "article", ["CVE-2099-50101", "CVE-2099-50102"])
    ingest(db, src, [row])
    doc = db.scalar(select(Document).where(Document.source_id == src.id))
    ingest(db, src, [row])
    assert db.scalar(select(func.count()).select_from(AssociationEvidence).where(
        AssociationEvidence.document_id == doc.id)) == 2
    proof = client.get(f"/api/v1/documents/{doc.id}/associations").json()
    assert len({p["vulnerability_id"] for p in proof["items"]}) == 2
    assert all(p["active_link"] and len(p["group_identifiers"]) == 1 for p in proof["items"])
    assert all(p["origin"] == "recorded_at_ingestion" for p in proof["items"])


def test_updated_document_drops_current_link_but_retains_old_proof(db, client):
    src = source(db)
    first = record("changed-proof", "article", ["CVE-2099-50201"])
    ingest(db, src, [first])
    doc = db.scalar(select(Document).where(Document.source_id == src.id))
    old = doc.content_hash
    ingest(db, src, [first.model_copy(update={"aliases": ["CVE-2099-50202"], "content": "Changed AI security article"})])
    aliases = list(db.scalars(select(VulnerabilityAlias.value).join(VulnerabilityDocument,
        VulnerabilityDocument.vulnerability_id == VulnerabilityAlias.vulnerability_id).where(
        VulnerabilityDocument.document_id == doc.id)))
    assert aliases == ["CVE-2099-50202"]
    historic = client.get(f"/api/v1/documents/{doc.id}/versions/{old}").json()
    assert not historic["is_current"] and historic["associations"][0]["identifier"] == "CVE-2099-50201"
    assert not historic["associations"][0]["active_link"]
    assert client.get(f"/api/v1/documents/{doc.id}/versions/" + "0" * 64).status_code == 404


@pytest.mark.parametrize("locator", ["content:chars:0:5", "content:chars:999:1000", "invented", "revision_json:chars:0:5"])
def test_invalid_locator_is_rejected_even_when_quote_exists(db, locator):
    src, doc = evidence_document(db)
    result = answer(src, doc)
    result.evidence[0].locator = locator
    with pytest.raises(HTTPException) as error:
        agent_gateway.validate_answer(db, result, True)
    assert error.value.detail == "agent_citation_invalid"


def test_unicode_and_json_offsets_are_exact():
    payload = {"content": "中文😀证据", "source_fields": {"cwe": {"ids": ["CWE-20"]}}}
    assert locate_quote(payload, "😀证据", "content:chars:2:5")["start"] == 2
    text = json.dumps(payload, ensure_ascii=False)
    start = text.index("CWE-20")
    assert locate_quote(payload, "CWE-20", f"revision_json:chars:{start}:{start+6}")["locator_verified"]


def test_answer_evidence_owner_current_and_historical(db, client, monkeypatch):
    src, doc = evidence_document(db)
    monkeypatch.setattr("backend.api.v1.agent.get_settings", lambda: get_settings().model_copy(
        update={"agent_service_url": "https://example.invalid"}))
    def fake(*args):
        response = answer(src, doc)
        response._rag_headers = {"X-RAG-Answer-Mode": "mock", "X-RAG-LLM-Calls": "0"}
        return response
    monkeypatch.setattr(agent_gateway, "call_agent", fake)
    h = token(db)
    result = client.post("/api/v1/agent/query", headers=h,
        json={"query": "Fictional test", "is_demo": True}).json()
    url = f"/api/v1/agent/invocations/{result['invocation_id']}/evidence"
    proof = client.get(url, headers=h).json()
    assert proof["all_quotes_verified"] and proof["all_versions_current"]
    assert proof["generation_mode"] == "mock" and proof["claim_support_status"] == "not_independently_reviewed"
    assert proof["evidence"][0]["associations"][0]["active_link"]
    assert client.get(url).status_code == 401
    assert client.get(url, headers=token(db, "outsider")).status_code == 403
    doc.deleted_at = utc_now()
    db.flush()
    historic = client.get(url, headers=h).json()
    assert historic["all_quotes_verified"] and not historic["all_versions_current"]
    assert not historic["evidence"][0]["associations"][0]["active_link"]


def test_legacy_proof_is_explicitly_reconstructed(db, client):
    src, doc = evidence_document(db)
    from sqlalchemy import delete
    db.execute(delete(AssociationEvidence).where(AssociationEvidence.document_id == doc.id))
    proof = client.get(f"/api/v1/documents/{doc.id}/associations").json()
    assert proof["items"][0]["origin"] == "reconstructed_from_revision"
    assert proof["items"][0]["recorded_at"] is None


def test_broker_failure_and_expired_worker_recover_without_cursor_advance(db, monkeypatch):
    src = source(db)
    task = create_task(db, src)
    db.flush()
    class Broker:
        fail = True
        def send_task(self, *args, **kwargs):
            if self.fail:
                raise ConnectionError("test-only unavailable")
    broker = Broker()
    monkeypatch.setattr("backend.services.jobs.maintain_index_events", lambda *args: {"sent": 0})
    assert maintain_jobs(db, broker)["sent"] == 0
    box = db.scalar(select(Outbox).where(Outbox.task_id == task.id))
    assert box.sent_at is None and box.available_at and src.cursor == {"sentinel": "old"}
    box.available_at, broker.fail = utc_now() - timedelta(seconds=1), False
    assert maintain_jobs(db, broker)["sent"] == 1 and box.sent_at
    task.status, task.attempts = "running", 1
    task.lease_token, task.lease_until = uuid4(), utc_now() - timedelta(seconds=1)
    db.flush()
    assert maintain_jobs(db, broker)["recovered"] == 1
    assert task.status == "queued" and task.lease_token is None
    assert src.cursor == {"sentinel": "old"}
