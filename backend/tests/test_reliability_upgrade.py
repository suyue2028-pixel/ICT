"""Regression checks use rollback-only PostgreSQL transactions and explicit HTTP doubles."""
from datetime import timedelta
from types import SimpleNamespace
from uuid import uuid4
import importlib.util
from pathlib import Path
import pytest
from sqlalchemy import select, func
from fastapi import HTTPException
from test_agent_upgrade import db, client, token, evidence_document, answer
from backend.core.config import get_settings
from backend.models import AgentInvocation, RuntimeSample, KnowledgeEvent
from backend.services import agent_gateway
from backend.services.jobs import utc_now
from backend.services.observation import start_observation, capture_samples, observation_summary
from backend.services.enrichment import cpe_components, normalize_affected


def configure(monkeypatch, **updates):
    settings = get_settings().model_copy(update={"agent_service_url": "https://agent.example.invalid", **updates})
    monkeypatch.setattr("backend.api.v1.agent.get_settings", lambda: settings)
    return settings


def test_other_user_cannot_continue_or_read_invocation(db, client, monkeypatch):
    configure(monkeypatch)
    src, doc = evidence_document(db)
    called = []
    def fake(*args):
        called.append(True)
        return answer(src, doc)
    monkeypatch.setattr(agent_gateway, "call_agent", fake)
    owner, other = token(db, "owner-a"), token(db, "owner-b")
    result = client.post("/api/v1/agent/query", json={"query": "test", "is_demo": True}, headers=owner).json()
    assert client.post("/api/v1/agent/query", headers=other,
        json={"query": "continue", "is_demo": True, "session_id": result["session_id"]}).status_code == 403
    assert client.get("/api/v1/agent/invocations/" + result["invocation_id"], headers=other).status_code == 403
    assert client.get("/api/v1/agent/sessions", headers=other).json()["items"] == []
    assert len(called) == 1


@pytest.mark.parametrize("kind", ["ordinary", "read_service", "write_service"])
def test_non_admin_cannot_manage_or_retry(db, client, kind):
    settings = get_settings()
    headers = token(db) if kind == "ordinary" else {"X-Agent-Token":
        getattr(settings, "agent_tool_token" if kind == "read_service" else "agent_write_token").get_secret_value()}
    for method, path, body in [("post", "/sources", {}), ("post", "/tasks/" + str(uuid4()) + "/retry", None),
        ("post", "/knowledge-events/" + str(uuid4()) + "/retry", None),
        ("post", "/ops/observations", {"hours": 48})]:
        r = getattr(client, method)("/api/v1" + path, headers=headers, json=body)
        assert r.status_code == 401


def test_minute_budget_and_conflicting_idempotency(db, client, monkeypatch):
    configure(monkeypatch, user_queries_per_minute=1)
    src, doc = evidence_document(db)
    calls = []
    def fake(*args):
        calls.append(True)
        return answer(src, doc)
    monkeypatch.setattr(agent_gateway, "call_agent", fake)
    headers = {**token(db, "rate-" + str(uuid4())), "Idempotency-Key": "test-key"}
    body = {"query": "test", "is_demo": True}
    assert client.post("/api/v1/agent/query", headers=headers, json=body).status_code == 200
    assert client.post("/api/v1/agent/query", headers=headers, json=body).status_code == 200
    assert client.post("/api/v1/agent/query", headers=headers, json={**body, "query": "different"}).status_code == 409
    assert client.post("/api/v1/agent/query", headers=token(db, "rate-" + str(uuid4())), json={}).status_code == 422
    assert client.post("/api/v1/agent/query", headers={k:v for k,v in headers.items() if k != "Idempotency-Key"}, json=body).status_code == 429
    assert len(calls) == 1


@pytest.mark.parametrize("code,status", [("agent_timeout", 504), ("agent_unavailable", 502), ("agent_invalid_response", 502)])
def test_failed_transport_persists_and_can_retry_with_new_key(db, client, monkeypatch, code, status):
    configure(monkeypatch)
    owner = "failure-" + str(uuid4())
    headers = {**token(db, owner), "Idempotency-Key": "failed"}
    def fail(*args):
        raise HTTPException(status, code)
    monkeypatch.setattr(agent_gateway, "call_agent", fail)
    body = {"query": "test"}
    r = client.post("/api/v1/agent/query", headers=headers, json=body)
    assert r.status_code == status and r.json()["error"]["code"] == code and r.json()["request_id"]
    row = db.scalar(select(AgentInvocation).where(AgentInvocation.owner_id == owner))
    assert row.status == "failed" and row.error_code == code and row.finished_at
    assert client.post("/api/v1/agent/query", headers=headers, json=body).status_code == 409
    client.post("/api/v1/agent/query", headers={**headers, "Idempotency-Key": "new"}, json=body)
    assert db.scalar(select(func.count()).select_from(AgentInvocation).where(AgentInvocation.owner_id == owner)) == 2


def test_invalid_and_overlong_bearer_rejected(client):
    for value in ("invalid", "a" * 257):
        assert client.get("/api/v1/agent/sessions", headers={"Authorization": "Bearer " + value}).status_code == 401


def test_shared_http_contract_accepts_backend_payload_and_agent_answer(db, monkeypatch):
    import os
    root = Path(__file__).resolve().parents[1]
    configured = os.environ.get("ICT_AGENT_CONTRACT_FILE")
    candidates = [Path(configured)] if configured else [
        root.parent / "packages/rag_agent/src/rag_agent/integration/contracts.py",
        root.parent / "ICT-team/packages/rag_agent/src/rag_agent/integration/contracts.py"]
    path = next((p for p in candidates if p.is_file()), candidates[0])
    if not path.exists():
        pytest.skip("Local cross-repository integration contract file not present")
    spec = importlib.util.spec_from_file_location("local_agent_contract", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Pydantic resolves annotations against the defining module's namespace.
    import sys
    sys.modules[spec.name] = module
    module.BackendQuery.model_rebuild()
    module.AgentAnswer.model_rebuild()
    payload = {"schema_version": "1", "query": "test", "session_id": str(uuid4()), "is_demo": True,
        "request_id": "test-request", "invocation_id": str(uuid4()), "history": [],
        "limits": {"max_tool_calls": 10, "max_answer_tokens": 1200}, "state_owner": "backend"}
    assert module.BackendQuery.model_validate(payload).state_owner == "backend"
    src, doc = evidence_document(db)
    assert module.AgentAnswer.model_validate(answer(src, doc).model_dump(mode="json")).status == "completed"


def test_observation_idempotent_samples_and_restart_gap(db):
    now = utc_now() + timedelta(days=1000)  # isolated from a live observation window
    run = start_observation(db, now, 48)
    assert start_observation(db, now, 48).id == run.id
    assert capture_samples(db, now, {"api": True, "agent": True}) == 1
    assert capture_samples(db, now + timedelta(seconds=10), {"api": True}) == 0
    assert capture_samples(db, now + timedelta(seconds=300), {"api": True, "agent": False}) == 1
    rows = list(db.scalars(select(RuntimeSample).where(RuntimeSample.observation_id == run.id).order_by(RuntimeSample.measured_at)))
    report = observation_summary(run, rows, now + timedelta(seconds=305))
    assert report["status"] == "observing" and report["gap_seconds"] == 300 and report["component_failure_samples"] == 1
    final = observation_summary(run, rows, now + timedelta(hours=49))
    assert final["status"] == "finished_with_gaps_or_failures"


def test_empty_finished_observation_is_never_success():
    now = utc_now()
    row = SimpleNamespace(id=uuid4(), started_at=now - timedelta(hours=49), ends_at=now - timedelta(hours=1), sample_seconds=60)
    result = observation_summary(row, [], now)
    assert result["samples"] == 0 and result["status"] == "finished_with_gaps_or_failures"


def test_cpe_escape_bounds_and_boolean_conditions_are_not_flattened():
    cpe = r"cpe:2.3:a:fictional:tool\:kit:*:*:*:*:*:*:*:*"
    parsed = cpe_components(cpe)
    assert parsed["product"]["display"] == "tool:kit" and parsed["version"]["kind"] == "any"
    conditions = [{"operator": "AND", "negate": True, "nodes": []}]
    result = normalize_affected({"cpe_matches": [{"criteria": cpe, "versionEndExcluding": "2.0"}]}, {"raw": {"configurations": conditions}})
    item = result["items"][0]
    assert item["version_bounds"] == {"versionEndExcluding": "2.0"} and item["fixed_version"] is None
    assert item["applicability"] == "not_evaluated" and result["applicability_conditions"] == conditions
    assert cpe_components("invalid") is None
    assert normalize_affected({}, {})["state"] == "unknown"


def test_osv_fixed_only_uses_explicit_fixed_events():
    affected = {"items": [{"package": {"name": "Fictional"}, "ranges": [{"type": "SEMVER", "events": [
        {"introduced": "0"}, {"last_affected": "1.9"}, {"fixed": "2.0"}, {"limit": "3.0"}]}]}]}
    result = normalize_affected(affected, {})["items"][0]
    assert result["fixed_versions"] == ["2.0"] and result["ranges"] == affected["items"][0]["ranges"]


def test_unqueryable_index_ack_stays_indexing(db, monkeypatch):
    from backend.api.v1.indexing import claim_events, ClaimRequest
    from backend.api.v1.ai import acknowledge_event
    from backend.schemas.contracts import KnowledgeAck
    _, doc = evidence_document(db)
    event = db.scalar(select(KnowledgeEvent).where(KnowledgeEvent.document_id == doc.id))
    event.created_at = utc_now() - timedelta(days=3000)
    db.flush()
    settings = configure(monkeypatch, index_provider="external")
    monkeypatch.setattr("backend.api.v1.indexing.get_settings", lambda: settings)
    monkeypatch.setattr("backend.api.v1.ai.get_settings", lambda: settings)
    claim_events(ClaimRequest(is_demo=True, limit=1), db)
    def fail(*args):
        raise HTTPException(422, "external_index_not_queryable")
    monkeypatch.setattr(agent_gateway, "verify_external_index", fail)
    with pytest.raises(HTTPException):
        acknowledge_event(event.id, KnowledgeAck(status="indexed", detail={"index_id": "test"}, lease_token=event.lease_token), db)
    assert event.status == "indexing" and doc.indexed_at is None


def test_exported_agent_response_contract_matches_runtime_model():
    import json
    from backend.schemas.agent import AgentQueryResponse
    path = Path(__file__).resolve().parents[1] / "docs/contracts/AgentQueryResponse.schema.json"
    assert json.loads(path.read_text(encoding="utf-8")) == AgentQueryResponse.model_json_schema()


def test_observation_admin_and_strict_request(db, client):
    headers = {"X-API-Key": get_settings().admin_api_key.get_secret_value()}
    assert client.post("/api/v1/ops/observations", headers=headers, json={"hours": True}).status_code == 422
    assert client.post("/api/v1/ops/observations", headers=headers, json={"hours": 48, "extra": "forbidden"}).status_code == 422
