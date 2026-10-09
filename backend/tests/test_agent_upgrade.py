"""Mock service responses are labelled substitutes; PostgreSQL test rows roll back."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import hashlib
import httpx
import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session
from backend.core.config import get_settings
from backend.db.session import get_engine, get_db
from backend.main import app
from backend.models import AccessToken, Conversation, DataSource, Document, KnowledgeEvent
from backend.schemas.agent import AgentAnswer, AgentQuery
from backend.schemas.contracts import KnowledgeAck
from backend.api.v1.ai import acknowledge_event
from backend.api.v1.indexing import claim_events, ClaimRequest
from backend.collectors.extended import nvd_record, arxiv_records, ExtendedCollector, ExtendedConfig
from backend.services import agent_gateway
from backend.services.jobs import utc_now, maintain_index_events
from backend.services.pipeline import ingest_batch
from backend.collectors.base import CollectionBatch
from backend.schemas.contracts import RawRecord
from backend.models import TaskRun


@pytest.fixture
def db():
    with get_engine().connect() as connection:
        transaction = connection.begin()
        with Session(connection, join_transaction_mode="create_savepoint") as session:
            yield session
        transaction.rollback()


@pytest.fixture
def client(db):
    app.dependency_overrides[get_db] = lambda: db
    with TestClient(app) as value:
        yield value
    app.dependency_overrides.clear()


def token(db, owner="test-owner"):
    value = "test-only-" + str(uuid4())
    db.add(AccessToken(owner_id=owner, token_hash=hashlib.sha256(value.encode()).hexdigest(),
                       expires_at=utc_now() + timedelta(hours=1), revoked=False))
    db.flush()
    return {"Authorization": "Bearer " + value}


def evidence_document(db):
    source = DataSource(name="test-agent-" + str(uuid4()), adapter="offline", category="offline", config={})
    db.add(source)
    db.flush()
    task = TaskRun(source_id=source.id, attempts=1, status="running")
    db.add(task)
    db.flush()
    row = RawRecord(external_id="agent-test", title="Fictional AI test", document_type="article",
        content="Fictional AI model uses unsafe deserialization CWE-502.", source_url="https://example.invalid/agent-test",
        is_demo=True, aliases=["CVE-2099-98765"])
    ingest_batch(db, source, CollectionBatch([row], {}), task)
    task.status = "succeeded"
    db.flush()
    return source, db.scalar(select(Document).where(Document.source_id == source.id))


def answer(source, doc):
    return AgentAnswer.model_validate({"schema_version": "1", "status": "completed",
        "answer": "Fictional model has the cited issue; this is a test substitute.",
        "sources": [{"document_id": str(doc.id), "content_hash": doc.content_hash,
            "title": doc.title, "url": doc.source_url, "source_type": source.category, "source_name": source.name}],
        "cve_ids": ["CVE-2099-98765"], "risk_level": "UNKNOWN",
        "evidence": [{"document_id": str(doc.id), "content_hash": doc.content_hash,
            "content": "unsafe deserialization CWE-502", "source": doc.source_url, "locator": "content"}],
        "tool_calls": ["cve_tool"], "retrieval_mode": "structured"})


def test_agent_gateway_two_turns_idempotency_and_scope(db, client, monkeypatch):
    src, doc = evidence_document(db)
    headers = token(db)
    settings = get_settings().model_copy(update={"agent_service_url": "https://agent.example.invalid"})
    monkeypatch.setattr("backend.api.v1.agent.get_settings", lambda: settings)
    requests = []

    def fake_call(settings, payload):
        requests.append(payload)
        result = answer(src, doc)
        result._rag_headers = {"X-RAG-Answer-Mode": "mock", "X-RAG-LLM-Calls": "2"}
        return result
    monkeypatch.setattr(agent_gateway, "call_agent", fake_call)
    body = {"query": "What is this fictional issue?", "is_demo": True}
    first = client.post("/api/v1/agent/query", json=body, headers={**headers, "Idempotency-Key": "first"})
    assert first.status_code == 200, first.text
    assert first.json()["generation_mode"] == "mock" and first.json()["llm_http_calls"] == 2
    repeat = client.post("/api/v1/agent/query", json=body, headers={**headers, "Idempotency-Key": "first"})
    assert repeat.json()["invocation_id"] == first.json()["invocation_id"] and len(requests) == 1
    assert repeat.headers["X-RAG-Answer-Mode"] == "mock" and repeat.headers["X-RAG-LLM-Calls"] == "2"
    invocation = client.get("/api/v1/agent/invocations/" + first.json()["invocation_id"], headers=headers).json()
    assert invocation["generation_mode"] == "mock" and invocation["llm_http_calls"] == 2
    session = first.json()["session_id"]
    second = client.post("/api/v1/agent/query", json={"query": "How should it be handled?",
        "session_id": session, "is_demo": True}, headers=headers)
    assert second.status_code == 200 and len(requests[1]["history"]) == 2
    assert client.get("/api/v1/agent/sessions/" + session, headers=headers).json()["turn_count"] == 2
    assert client.get("/api/v1/agent/sessions/" + session, headers=token(db, "another-user")).status_code == 403
    wrong_scope = client.post("/api/v1/agent/query", json={"query": "scope", "session_id": session}, headers=headers)
    assert wrong_scope.status_code == 409


def test_agent_missing_service_is_explicit_and_admin_is_not_user(db, client, monkeypatch):
    s = get_settings().model_copy(update={"agent_service_url": None})
    monkeypatch.setattr("backend.api.v1.agent.get_settings", lambda: s)
    assert client.post("/api/v1/agent/query", json={"query": "q"},
        headers={"X-API-Key": s.admin_api_key.get_secret_value()}).status_code == 401
    response = client.post("/api/v1/agent/query", json={"query": "q"}, headers=token(db))
    assert response.status_code == 503 and response.json()["error"]["code"] == "agent_service_not_configured"


def test_agent_rejects_fabricated_citation_and_demo_mix(db):
    src, doc = evidence_document(db)
    response = answer(src, doc)
    agent_gateway.validate_answer(db, response, True)
    with pytest.raises(HTTPException):
        agent_gateway.validate_answer(db, response, False)
    response.evidence[0].content = "fabricated quotation"
    with pytest.raises(HTTPException) as error:
        agent_gateway.validate_answer(db, response, True)
    assert error.value.detail == "agent_citation_invalid"


def test_agent_failure_record_and_daily_budget(db, client, monkeypatch):
    s = get_settings().model_copy(update={"agent_service_url": "https://agent.example.invalid", "user_queries_per_day": 1})
    monkeypatch.setattr("backend.api.v1.agent.get_settings", lambda: s)
    def fail(*args):
        raise HTTPException(504, "agent_timeout")
    monkeypatch.setattr(agent_gateway, "call_agent", fail)
    headers = token(db)
    first = client.post("/api/v1/agent/query", json={"query": "q"}, headers=headers)
    assert first.status_code == 504
    assert client.post("/api/v1/agent/query", json={"query": "q"}, headers=headers).status_code == 429


def test_tokens_expiry_revocation_and_strict_contract(db, client):
    h = token(db)
    row = db.scalar(select(AccessToken).where(AccessToken.token_hash == hashlib.sha256(h["Authorization"].split()[1].encode()).hexdigest()))
    row.revoked = True
    db.flush()
    assert client.get("/api/v1/agent/sessions", headers=h).status_code == 401
    row.revoked = False
    row.expires_at = utc_now() - timedelta(seconds=1)
    db.flush()
    assert client.get("/api/v1/agent/sessions", headers=h).status_code == 401
    with pytest.raises(ValidationError):
        AgentQuery(query="   ")
    with pytest.raises(ValidationError):
        AgentQuery(query="x", arbitrary_url="http://localhost")


def test_external_index_claim_and_ack_requires_verified_current_version(db, monkeypatch):
    src, doc = evidence_document(db)
    settings = get_settings().model_copy(update={"index_provider": "external", "agent_service_url": "https://agent.example.invalid"})
    monkeypatch.setattr("backend.api.v1.indexing.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: settings)
    event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id))
    # Isolate this transaction's claim from previously saved demo events.
    event.created_at = utc_now() - timedelta(days=1000)
    db.flush()
    items = claim_events(ClaimRequest(is_demo=True, limit=1), db)["items"]
    assert items[0]["event_id"] == event.id and event.status == "indexing"
    assert not claim_events(ClaimRequest(is_demo=True, limit=1), db)["items"] or event.status == "indexing"
    with pytest.raises(HTTPException):
        acknowledge_event(event.id, KnowledgeAck(status="indexed", detail={"index_id": "test"}), db)
    calls = []
    monkeypatch.setattr(agent_gateway, "verify_external_index", lambda *args: calls.append(args))
    result = acknowledge_event(event.id, KnowledgeAck(status="indexed", detail={"index_id": "test"},
        lease_token=event.lease_token), db)
    assert result["status"] == "indexed" and calls
    assert acknowledge_event(event.id, KnowledgeAck(status="indexed", detail={"index_id": "test"}), db)["status"] == "indexed"


def test_external_mode_disables_internal_dispatch(monkeypatch, db):
    monkeypatch.setattr("backend.services.jobs.get_settings", lambda: SimpleNamespace(index_provider="external"))
    assert maintain_index_events(db, None) == {"sent": 0, "reason": "external_index_provider"}


def test_tools_are_scoped_and_nvd_is_not_osv(db, client):
    src, doc = evidence_document(db)
    h = {"X-API-Key": get_settings().admin_api_key.get_secret_value()}
    assert client.get("/api/v1/tools/cves?query=CVE").status_code == 401
    details = client.get("/api/v1/tools/cves/CVE-2099-98765?is_demo=true", headers=h)
    assert details.status_code == 200 and details.json()["data"]["references"][0]["content_hash"] == doc.content_hash
    assert client.get("/api/v1/tools/cves/invalid", headers=h).status_code == 422
    assert client.get("/api/v1/tools/cves/CVE-2099-98765", headers=h).status_code == 404
    assert client.get("/api/v1/tools/nvd?query=agent-test&is_demo=true", headers=h).json()["data"]["total"] == 0
    assert client.get(f"/api/v1/tools/documents/{doc.id}", headers=h).status_code == 404


def test_nvd_parser_preserves_versioned_scores_and_all_six_dimensions():
    row = nvd_record({"cve": {"id": "CVE-2099-11111", "descriptions": [{"lang": "en", "value": "AI issue"}],
        "published": "2026-01-01T00:00:00Z", "metrics": {"cvssMetricV31": [{"cvssData": {
            "version": "3.1", "vectorString": "test-only-vector", "baseScore": 9.1, "baseSeverity": "CRITICAL"}}]},
        "weaknesses": [{"description": [{"value": "CWE-502"}]}],
        "configurations": [{"nodes": [{"cpeMatch": [{"vulnerable": True, "criteria": "cpe:test", "versionEndExcluding": "2.0"}]}]}],
        "references": [{"url": "https://example.invalid/fix", "tags": ["Patch"]},
            {"url": "https://example.invalid/analysis", "tags": ["Technical Description"]},
            {"url": "https://example.invalid/poc", "tags": ["Exploit"]}]}})
    assert not row.is_demo and row.source_fields["cvss"]["vectors"][0]["score"] == 9.1
    assert set(row.source_fields) == {"affected", "cwe", "cvss", "remediation", "analysis", "poc"}
    assert row.source_fields["affected"]["cpe_matches"][0]["versionEndExcluding"] == "2.0"


def test_arxiv_metadata_is_abstract_not_full_text():
    payload = b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2601.00001v1</id>
        <title>Fictional AI security paper</title><summary>Test-only prompt injection abstract</summary>
        <published>2026-01-01T00:00:00Z</published><author><name>Test Author</name></author></entry></feed>'''
    row = arxiv_records(payload)[0]
    assert row.content_completeness == "summary" and row.document_type == "paper"
    assert row.metadata_json["authors"] == ["Test Author"]
    with pytest.raises(ValidationError):
        ExtendedConfig(keywords=["../../etc"])


def test_nvd_incremental_cursor_does_not_jump_past_unprocessed_pages():
    collector = ExtendedCollector("nvd", {"keywords": ["ollama"], "page_size": 1})
    requests = []
    class Transport:
        def request(self, *args, **kwargs):
            requests.append(kwargs["params"])
            return 200, {}, b'{"vulnerabilities":[{"cve":{"id":"CVE-2099-12345"}}], "resultsPerPage":1, "totalResults":2}'
    batch = collector._nvd_window({}, Transport(), SimpleNamespace(nvd_api_key=None))
    assert batch.more and batch.next_cursor["window_end"] == requests[0]["lastModEndDate"]
    assert batch.next_cursor["start_index"] == 1
