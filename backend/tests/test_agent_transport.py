"""Explicit mocked HTTP, never a production fallback or a real Agent achievement."""
import json
from types import SimpleNamespace
from uuid import uuid4
import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from backend.core.config import get_settings
from backend.services import agent_gateway
from backend.api.v1.ai import acknowledge_event
from backend.api.v1.documents import invalidate_document
from backend.api.v1.indexing import claim_events, ClaimRequest
from backend.collectors.extended import ExtendedCollector
from backend.models import KnowledgeEvent
from backend.schemas.contracts import KnowledgeAck
from backend.services.jobs import utc_now
from sqlalchemy import select
from test_agent_upgrade import db, client, evidence_document, answer


def transport(monkeypatch, handler):
    real_client = httpx.Client
    monkeypatch.setattr(agent_gateway.httpx, "Client", lambda **kw:
        real_client(transport=httpx.MockTransport(handler), **kw))


def config():
    return get_settings().model_copy(update={"agent_service_url": "https://agent.example.invalid",
        "agent_service_token": SecretStr("test-only-service-token")})


def test_http_query_and_version_health(db, monkeypatch):
    source, doc = evidence_document(db)
    expected = answer(source, doc).model_dump(mode="json")
    calls = []
    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer test-only-service-token"
        if request.url.path == "/v1/health":
            return httpx.Response(200, json={"schema_version": "1", "status": "ok"})
        assert json.loads(request.content)["query"] == "test-only question"
        return httpx.Response(200, json=expected)
    transport(monkeypatch, handler)
    result = agent_gateway.call_agent(config(), {"query": "test-only question", "request_id": "test-request"})
    assert result.status == "completed" and agent_gateway.check_health(config())["status"] == "ok"
    assert len(calls) == 2


@pytest.mark.parametrize("mode,expected", [("missing_version", "agent_invalid_response"),
    ("huge", "agent_response_too_large"), ("timeout", "agent_timeout"),
    ("redirect", "agent_unavailable"), ("rate_limit", "agent_rate_limited")])
def test_http_agent_failure_classification(monkeypatch, mode, expected):
    def handler(request):
        if mode == "timeout":
            raise httpx.ReadTimeout("test-only timeout", request=request)
        if mode == "huge":
            return httpx.Response(200, content=b"x" * 500001)
        if mode == "redirect":
            return httpx.Response(302, headers={"Location": "https://other.example.invalid"})
        if mode == "rate_limit":
            return httpx.Response(429)
        return httpx.Response(200, json={"status": "insufficient_evidence"})
    transport(monkeypatch, handler)
    with pytest.raises(HTTPException) as error:
        agent_gateway.call_agent(config(), {"request_id": "test-only"})
    assert error.value.detail == expected


def test_external_index_proof_rejects_wrong_version_and_confirms_delete(monkeypatch):
    event = SimpleNamespace(document_id=uuid4(), content_hash="a" * 64, operation="add")
    result = {"schema_version": "1", "document_id": str(event.document_id), "content_hash": "b" * 64,
        "is_demo": True, "index_id": "test-index", "queryable": True}
    transport(monkeypatch, lambda request: httpx.Response(200, json=result))
    with pytest.raises(HTTPException) as error:
        agent_gateway.verify_external_index(config(), event, True, "test-index")
    assert error.value.detail == "external_index_not_queryable"
    result["content_hash"] = event.content_hash
    agent_gateway.verify_external_index(config(), event, True, "test-index")
    event.operation, result["queryable"] = "delete", False
    with pytest.raises(HTTPException):
        agent_gateway.verify_external_index(config(), event, True, "test-index")
    result["removed"] = True
    agent_gateway.verify_external_index(config(), event, True, "test-index")


def test_non_relevant_delete_event_is_claimed_and_expired_ack_rejected(db, monkeypatch):
    from datetime import timedelta
    _, doc = evidence_document(db)
    doc.ai_relevant = False
    created = invalidate_document(doc.id, db)
    event = db.get(KnowledgeEvent, created["event_id"])
    event.created_at = utc_now() - timedelta(days=2000)
    db.flush()
    s = config().model_copy(update={"index_provider": "external"})
    monkeypatch.setattr("backend.api.v1.indexing.get_settings", lambda: s)
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: s)
    claimed = claim_events(ClaimRequest(is_demo=True, limit=1), db)["items"]
    assert claimed[0]["event_id"] == event.id and event.operation == "delete"
    event.lease_until = utc_now() - timedelta(seconds=1)
    db.flush()
    with pytest.raises(HTTPException) as error:
        acknowledge_event(event.id, KnowledgeAck(status="invalidated", detail={"index_id": "test"},
            lease_token=event.lease_token), db)
    assert error.value.detail == "index_lease_invalid"
    assert event.status == "indexing"


def test_read_tool_credential_cannot_write_and_trace_redacts(db, client):
    settings = get_settings()
    assert settings.agent_tool_token
    headers = {"X-Agent-Token": settings.agent_tool_token.get_secret_value()}
    assert client.get("/api/v1/tools/documents", headers=headers).status_code == 200
    assert client.post("/api/v1/internal/knowledge-events/claim", json={}, headers=headers).status_code == 401
    source, doc = evidence_document(db)
    response = answer(source, doc)
    from backend.schemas.agent import ToolTrace
    response.tool_trace = [ToolTrace(tool="cve_tool", status="completed", duration_ms=5,
        input_summary="token=test-only-secret; " + settings.admin_api_key.get_secret_value())]
    safe = agent_gateway.safe_trace(response, settings)
    assert "test-only-secret" not in safe[0]["input_summary"]
    assert settings.admin_api_key.get_secret_value() not in str(safe)


def test_empty_inconsistent_nvd_page_and_arxiv_incremental_overlap():
    class NVD:
        def request(self, *args, **kw):
            return 200, {}, b'{"vulnerabilities":[],"totalResults":2,"resultsPerPage":1}'
    with pytest.raises(ValueError, match="nvd_inconsistent_page"):
        ExtendedCollector("nvd", {})._nvd({}, NVD(), SimpleNamespace(nvd_api_key=None))
    requests = []
    class Atom:
        def request(self, *args, **kw):
            requests.append(kw["params"])
            return 200, {}, b'''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
              <id>http://arxiv.org/abs/2601.00001v1</id><title>Test-only paper</title><summary>Fictional AI abstract</summary>
              <updated>2026-01-03T00:00:00Z</updated><published>2026-01-01T00:00:00Z</published></entry></feed>'''
    collector = ExtendedCollector("arxiv", {"max_items": 1})
    first = collector._arxiv({"since": "2026-01-02T00:00:00Z"}, Atom(), None)
    assert first.more and first.next_cursor["start"] == 1
    collector._arxiv(first.next_cursor, Atom(), None)
    assert requests[1]["start"] == 1
    final = collector._arxiv({"since": "2026-01-04T00:00:00Z"}, Atom(), None)
    assert not final.more and final.next_cursor["start"] == 0


def test_replay_current_event_and_versioned_tool_metadata(db, client):
    from backend.collectors.base import CollectionBatch
    from backend.schemas.contracts import RawRecord
    from backend.services.pipeline import ingest_batch
    from backend.models import TaskRun
    from backend.api.v1.ai import replay_event
    source, doc = evidence_document(db)
    old_hash = doc.content_hash
    old_title = doc.title
    task = TaskRun(source_id=source.id, status="running", attempts=1)
    db.add(task)
    db.flush()
    row = RawRecord(external_id=doc.external_id, title="Updated fictional title", document_type="article",
        content="Updated fictional AI issue, CWE-502.", source_url=doc.source_url, is_demo=True)
    ingest_batch(db, source, CollectionBatch([row], {}), task)
    db.flush()
    header = {"X-Agent-Token": get_settings().agent_tool_token.get_secret_value()}
    historical = client.get(f"/api/v1/tools/documents/{doc.id}",
        params={"is_demo": True, "content_hash": old_hash}, headers=header).json()["data"]
    assert historical["title"] == old_title and historical["is_current"] is False
    event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id,
        KnowledgeEvent.content_hash == doc.content_hash))
    event.status = "indexed"
    db.flush()
    assert replay_event(event.id, db)["status"] == "pending"
    old = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id,
        KnowledgeEvent.content_hash == old_hash))
    with pytest.raises(HTTPException) as error:
        replay_event(old.id, db)
    assert error.value.detail == "event_not_current"
