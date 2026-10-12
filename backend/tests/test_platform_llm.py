"""Mocked provider transport tests; these do not assert real vendor API success."""
import asyncio
import hashlib
import json
from datetime import datetime, timedelta, timezone

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import SecretStr, ValidationError
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from sqlalchemy.pool import StaticPool

from backend.api.v1 import auth, llm
from backend.core.config import Settings
from backend.db.session import get_db
from backend.models import AccessToken
from backend.schemas.agent import AgentAnswer
from backend.schemas.llm import CompletionRequest
from backend.services.platform_llm import PlatformLLM, PlatformModelError
from backend.services import agent_gateway
from backend.services.llm_prompts import SYSTEM, CONVERSATION_SYSTEM


def settings(**updates):
    values = {"postgres_password": "test-only-db", "redis_password": "test-only-redis",
        "admin_api_key": "test-only-admin", "agent_tool_token": "test-only-agent",
        "llm_api_key": "test-only-platform", "llm_api_key_file": None,
        "deepseek_api_key": None, "dashscope_api_key": None,
        "llm_provider": "deepseek", "llm_model": "deepseek-flash", "llm_base_url": None}
    return Settings(_env_file=None, **{**values, **updates})


def completion(**updates):
    return CompletionRequest(purpose="converse", payload={"question": "你好", "history": []}, **updates)


def envelope(result=None):
    return {"choices": [{"finish_reason": "stop", "message": {
        "content": json.dumps(result or {"answer": "你好"}, ensure_ascii=False)}}],
        "usage": {"prompt_tokens": 50, "completion_tokens": 10, "total_tokens": 60}}


def execute(adapter, request=None):
    return asyncio.run(adapter.complete(request or completion()))


def test_trusted_prompt_matches_bounded_plan_and_scoped_generation_protocol():
    adapter = PlatformLLM(settings())
    plan = adapter._body(CompletionRequest(purpose="plan", payload={
        "question": "比较两种风险机制", "hard_filters": {"product": "explicit-product"}}))
    assert plan["messages"][0]["content"] == SYSTEM
    payload = json.loads(plan["messages"][1]["content"])
    assert payload["hard_filters"] == {"product": "explicit-product"}
    assert payload["purpose"] == "plan"
    for field in ("route", "subqueries", "expand_relations", "question_type", "fresh_required", "tools", "preferred_sources"):
        assert field in SYSTEM
    assert '未传 registered_tools 时 tools=[]' in SYSTEM
    assert '不得增加 requirements、cve_ids' in SYSTEM
    assert 'answer_requirements.coverage_topics' in SYSTEM
    assert '通用 concept 也是有效需求 ID' in SYSTEM
    assert '普通问题用空数组' not in SYSTEM
    assert 'CISA KEV 查询无条目' in SYSTEM
    assert '不能证明未被利用' in SYSTEM
    assert '最高分' in SYSTEM and '评分主体差异' in SYSTEM


def test_conversation_identity_and_capability_prompt_are_server_controlled():
    adapter = PlatformLLM(settings(llm_provider="qwen", llm_model="qwen-plus"))
    body = adapter._body(CompletionRequest(purpose="converse", payload={
        "question": "你是什么模型", "configured_model": "untrusted-model-claim", "history": []}))
    assert body["messages"][0]["content"] == CONVERSATION_SYSTEM
    assert json.loads(body["messages"][1]["content"])["configured_model"] == "qwen-plus"
    assert '已注册工具' in CONVERSATION_SYSTEM
    assert '实际工具配置' in CONVERSATION_SYSTEM


def test_verify_prompt_requires_complete_zero_based_integer_object():
    payload = {"question": "测试问题", "claims": [{"text": "测试结论", "evidence_ids": ["test-evidence"]}], "evidence": []}
    body = PlatformLLM(settings())._body(CompletionRequest(purpose="verify", payload=payload))
    assert body["messages"][0]["content"] == SYSTEM
    assert json.loads(body["messages"][-1]["content"])["claims"] == payload["claims"]
    assert '根对象只允许 supported_claim_indexes 一个字段' in SYSTEM
    assert '从0开始的纯 JSON 整数' in SYSTEM
    assert '不能是字符串、浮点数、布尔值' in SYSTEM
    assert '0 <= index < claims 条数' in SYSTEM
    assert '{"supported_claim_indexes":[0]}' in SYSTEM
    assert '不能返回 [1]' in SYSTEM
    assert '不能只返回裸数组' in SYSTEM


def test_retrieval_context_is_bounded_check_metadata_not_new_fact_or_freshness_proof():
    context = {"fresh_required": True, "checks": [{"tool": "kev_lookup",
        "identifier": "CVE-2099-12345", "checked_at": "2099-01-01T00:00:00Z", "cache_hit": True}]}
    body = PlatformLLM(settings())._body(CompletionRequest(purpose="synthesize", payload={
        "question": "当前是否有在野利用", "evidence": [], "retrieval_context": context}))
    payload = json.loads(body["messages"][1]["content"])
    assert payload["retrieval_context"] == context
    assert body["messages"][0]["content"] == SYSTEM
    assert '保留原 checked_at，不能声称刚刚重新查询' in SYSTEM
    assert '不能冒充来源发布时间、官方更新时间' in SYSTEM
    assert '不证明证据已覆盖所有最新事实' in SYSTEM
    assert '不得仅凭 checks 元数据创造引用' in SYSTEM
    assert '一次 KEV 目录核查不能声称覆盖所有未知利用事件' in SYSTEM


def test_synthesis_contract_does_not_promote_display_assertions_or_topic_objects_to_output():
    projection = {"assertion_id": "test-assertion", "entity_id": "test-entity", "field": "mitigation",
        "value": "test-only source value", "evidence_ids": ["test-evidence"],
        "origin": "source_structured", "verification": "source_recorded"}
    topics = [{"id": "audit_logging", "label": "调用日志与可追溯性"}]
    body = PlatformLLM(settings())._body(CompletionRequest(purpose="synthesize", payload={
        "question": "工具审计有哪些风险", "stored_assertions": [projection],
        "answer_requirements": {"coverage_topics": topics}, "evidence": []}))
    payload = json.loads(body["messages"][1]["content"])
    assert payload["stored_assertions"] == [projection]
    assert payload["answer_requirements"]["coverage_topics"] == topics
    assert set(projection).isdisjoint({"document_id", "version", "recorded_at"})
    assert 'synthesize 根对象只允许 claims 字段' in SYSTEM
    assert '每项只允许 text、evidence_ids、kind、topic_ids 四个字段' in SYSTEM
    assert 'claims 必须是数组，最多8项' in SYSTEM
    assert 'evidence_id 字符串组成的数组' in SYSTEM
    assert 'topic_ids 必须是最多8个主题 id 字符串组成的数组' in SYSTEM
    assert '不能复制 coverage_topics 的 {id,label} 对象或使用 label' in SYSTEM
    assert 'kind=fact 是有来源的文字结论，不表示需要另行输出 facts' in SYSTEM
    assert '不是完整的 Assertion 对象' in SYSTEM
    assert '不能复制到 facts，也不得自行补造缺失字段' in SYSTEM


@pytest.mark.parametrize("provider,model,vendor_option", [
    ("deepseek", "deepseek-flash", "thinking"), ("qwen", "qwen-plus", "enable_thinking")])
def test_provider_switch_retains_json_history_and_usage(provider, model, vendor_option):
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["response_format"] == {"type": "json_object"}
        assert body["model"] == model and vendor_option in body
        assert ("thinking" in body) != ("enable_thinking" in body)
        payload = json.loads(body["messages"][-1]["content"])
        assert payload["history"] == [{"role": "user", "content": "你好"},
                                      {"role": "assistant", "content": "你好，有什么问题？"}]
        assert body["messages"][1:3] == payload["history"]
        assert "JSON" in body["messages"][0]["content"]
        return httpx.Response(200, json=envelope())
    req = completion(max_answer_tokens=80)
    req.payload["history"] = [{"role": "user", "content": "你好"},
                              {"role": "assistant", "content": "你好，有什么问题？"}]
    result = execute(PlatformLLM(settings(llm_provider=provider, llm_model=model),
        transport=httpx.MockTransport(handler)), req)
    assert result.attempts == 1 and result.provider == provider
    assert result.usage.total_tokens == 60 and calls[0]["max_tokens"] == 80


@pytest.mark.parametrize("status,code,attempts,public_status", [
    (401, "platform_model_auth_failed", 1, 502),
    (403, "platform_model_auth_failed", 1, 502),
    (429, "platform_model_rate_limited", 2, 503),
    (503, "platform_model_unavailable", 2, 502),
    (400, "platform_model_capability_unsupported", 1, 502)])
def test_errors_are_typed_secret_safe_and_attempts_counted(status, code, attempts, public_status):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(status, json={"error": "DO_NOT_FORWARD_PROVIDER_SECRET"})
    adapter = PlatformLLM(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(PlatformModelError) as failed:
        execute(adapter, completion(max_attempts=2))
    assert failed.value.code == code and failed.value.status_code == public_status
    assert failed.value.attempts == attempts == len(calls)
    assert "DO_NOT_FORWARD" not in str(failed.value)


def test_one_retry_success_and_no_retry_when_agent_budget_one():
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(429) if len(calls) % 2 else httpx.Response(200, json=envelope())
    adapter = PlatformLLM(settings(), transport=httpx.MockTransport(handler))
    assert execute(adapter, completion(max_attempts=2)).attempts == 2
    with pytest.raises(PlatformModelError) as failed:
        execute(adapter, completion(max_attempts=1))
    assert failed.value.attempts == 1 and len(calls) == 3


def test_timeout_no_raw_exception_and_native_tools_explicitly_fail():
    def handler(request):
        raise httpx.ReadTimeout("DO_NOT_LEAK_TRANSPORT_BODY", request=request)
    with pytest.raises(PlatformModelError) as failed:
        execute(PlatformLLM(settings(), transport=httpx.MockTransport(handler)))
    assert failed.value.code == "platform_model_timeout" and failed.value.attempts == 1
    adapter = PlatformLLM(settings(), transport=httpx.MockTransport(handler))
    with pytest.raises(PlatformModelError) as failed:
        execute(adapter, completion(required_capabilities=["native_tool_calls"]))
    assert failed.value.code == "platform_model_capability_unsupported" and failed.value.attempts == 0


@pytest.mark.parametrize("kind", ["length", "tools", "invalid_json", "array", "key"])
def test_response_format_rejects_truncation_tools_nonobject_and_key(kind):
    response = envelope()
    choice = response["choices"][0]
    if kind == "length": choice["finish_reason"] = "length"
    if kind == "tools": choice["message"]["tool_calls"] = [{"name": "invented"}]
    if kind == "invalid_json": choice["message"]["content"] = "```json bad"
    if kind == "array": choice["message"]["content"] = "[]"
    if kind == "key": choice["message"]["content"] = '{"answer":"test-only-platform"}'
    with pytest.raises(PlatformModelError) as failed:
        execute(PlatformLLM(settings(), transport=httpx.MockTransport(lambda _: httpx.Response(200, json=response))))
    assert failed.value.code == ("platform_model_capability_unsupported" if kind == "tools"
                                 else "platform_model_format_invalid")


def test_config_key_file_permissions_unknown_model_and_request_controls(tmp_path):
    path = tmp_path / "private-key"
    path.write_text("test-only-file-key")
    path.chmod(0o600)
    s = settings(llm_api_key=None, llm_api_key_file=path)
    assert s.platform_llm_configured and PlatformLLM(s)._key() == "test-only-file-key"
    path.chmod(0o644)
    assert not s.platform_llm_configured
    with pytest.raises(PlatformModelError) as failed:
        execute(PlatformLLM(s))
    assert failed.value.attempts == 0 and failed.value.code == "platform_model_not_configured"
    with pytest.raises(PlatformModelError) as failed:
        execute(PlatformLLM(settings(llm_model="unknown-unsupported-model")))
    assert failed.value.code == "platform_model_capability_unsupported"
    with pytest.raises(ValidationError):
        CompletionRequest(purpose="converse", payload={}, api_key="forbidden")
    with pytest.raises(ValidationError):
        settings(llm_base_url="http://localhost:9000")


def test_internal_gateway_user_auth_and_model_auth_are_different(monkeypatch):
    s = settings()
    monkeypatch.setattr("backend.core.security.get_settings", lambda: s)
    monkeypatch.setattr(llm, "get_settings", lambda: s)
    adapter = PlatformLLM(s, transport=httpx.MockTransport(lambda _: httpx.Response(401,
        json={"error": "DO_NOT_FORWARD_PROVIDER_SECRET"})))
    monkeypatch.setattr(llm, "PlatformLLM", lambda _: adapter)
    app = FastAPI()
    app.include_router(llm.router, prefix="/api/v1")
    with TestClient(app) as client:
        url = "/api/v1/internal/llm/complete"
        request = completion().model_dump()
        assert client.post(url, json=request, headers={"Authorization": "Bearer user-only"}).status_code == 401
        response = client.post(url, json=request, headers={"X-Agent-Token": "test-only-agent"})
        assert response.status_code == 502
        assert response.json() == {"detail": {"code": "platform_model_auth_failed", "attempts": 1}}
        assert "DO_NOT_FORWARD" not in response.text and "test-only-platform" not in response.text
        assert client.get("/api/v1/internal/llm/capabilities").status_code == 401


@pytest.mark.parametrize("code,status", [
    ("platform_model_auth_failed", 502), ("platform_model_rate_limited", 503),
    ("platform_model_timeout", 504), ("platform_model_unavailable", 502)])
def test_public_gateway_retains_model_failure_category(monkeypatch, code, status):
    real_client = httpx.Client
    monkeypatch.setattr(agent_gateway.httpx, "Client", lambda **kwargs:
        real_client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"detail": code})), **kwargs))
    config = settings(agent_service_url="https://agent.example.invalid")
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failed:
        agent_gateway.call_agent(config, {"request_id": "test-only-model-error"})
    assert failed.value.status_code == status and failed.value.detail == code


def test_requirement_metadata_cannot_claim_missing_citations():
    body = {"schema_version": "1", "status": "insufficient_evidence", "answer": "没有可核验依据",
        "sources": [], "evidence": [], "cve_ids": [], "risk_level": "UNKNOWN", "tool_calls": [],
        "retrieval_mode": "hybrid", "question_type": "security_analysis",
        "requirement_coverage": [{"requirement_id": "permissions", "label": "工具权限",
            "status": "missing", "evidence_ids": [], "reason": "暂无资料"}]}
    agent_gateway.validate_answer(None, AgentAnswer.model_validate(body), False, query="工具权限风险")
    body["requirement_coverage"][0]["evidence_ids"] = ["1"]
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as failed:
        agent_gateway.validate_answer(None, AgentAnswer.model_validate(body), False, query="工具权限风险")
    assert failed.value.detail == "agent_invalid_requirement_coverage"


def test_auth_me_valid_expired_revoked_and_admin_not_user(monkeypatch):
    """Real auth SQL against isolated SQLite tokens; no production DB mutations."""
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    AccessToken.__table__.create(engine)
    s = settings()
    monkeypatch.setattr("backend.core.security.get_settings", lambda: s)
    with Session(engine) as db:
        value = "test-only-user"
        row = AccessToken(owner_id="test-owner", token_hash=hashlib.sha256(value.encode()).hexdigest(),
            expires_at=datetime.now(timezone.utc) + timedelta(hours=1), revoked=False)
        db.add(row); db.commit()
        app = FastAPI()
        app.include_router(auth.router, prefix="/api/v1")
        app.dependency_overrides[get_db] = lambda: db
        with TestClient(app) as client:
            header = {"Authorization": "Bearer " + value}
            valid = client.get("/api/v1/auth/me", headers=header)
            assert valid.status_code == 200 and valid.json() == {"owner_id": "test-owner", "authenticated": True}
            assert valid.headers["cache-control"] == "no-store" and "api_key" not in valid.text
            assert client.get("/api/v1/auth/me").status_code == 401
            assert client.get("/api/v1/auth/me", headers={"X-API-Key": "test-only-admin"}).status_code == 401
            assert client.post("/api/v1/auth/tokens", json={"owner_id": "test"}, headers=header).status_code == 401
            row.revoked = True; db.commit()
            assert client.get("/api/v1/auth/me", headers=header).status_code == 401
            row.revoked = False; row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1); db.commit()
            invalid = client.get("/api/v1/auth/me", headers=header)
            assert invalid.status_code == 401 and invalid.json()["detail"] == "invalid_user_token"
    engine.dispose()
