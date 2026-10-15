import json
import time
import httpx
from fastapi.testclient import TestClient
import pytest
from rag_agent.config import Settings
from rag_agent.providers.llm import BackendLLM, RemoteLLM, TypedProviderError
from rag_agent.agent.state import Budget, BudgetExceeded
from rag_agent.agent.semantic import SemanticClassification
from rag_agent.agent.semantic_prompts import CLASSIFICATION_SYSTEM, GENERAL_EXPLANATION_SYSTEM, SYNTHESIS_LENGTH_SYSTEM


def test_demo_adapter_contract(service, monkeypatch):
    from examples import demo_api
    monkeypatch.setattr(demo_api, "service", lambda: service)
    with TestClient(demo_api.app) as client:
        assert client.get("/health").json()["documents"] == 3
        result = client.post("/demo/query", json={"question": "CVE-2099-1234"})
        assert result.status_code == 200
        body = result.json()
        assert body["metrics"]["embedding_mode"] == "mock"
        assert body["mode"] == "extractive"
        evidence_id = body["sources"][0]["evidence_id"]
        assert client.get("/demo/evidence/" + evidence_id).json()["evidence_id"] == evidence_id
        assert client.get("/demo/evidence/not-present").status_code == 404
        assert client.post("/demo/query", json={"question": "q", "filters": {"sql": "x"}}).status_code == 422
        assert client.post("/api/v1/qa", json={"question": "q"}).status_code == 404


async def test_http_retry_count_and_json_adapter(monkeypatch):
    requests = []
    def handler(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(503)
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps({"question": "revised"})}}]})
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(llm_mode="live", llm_transport="direct", llm_key="synthetic-test-key")
    budget = Budget(time.monotonic() + 2)
    result = await RemoteLLM(settings).complete("rewrite", {"question": "q"}, budget, 1)
    assert result["question"] == "revised" and budget.llm_calls == 2
    payload = json.loads(requests[0].content)
    assert payload["response_format"] == {"type": "json_object"}
    assert requests[0].url.path == "/chat/completions"


async def test_http_retry_cannot_exceed_shared_budget(monkeypatch):
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(lambda r: httpx.Response(503)), **kw))
    budget = Budget(time.monotonic() + 2, max_llm=1)
    with pytest.raises(BudgetExceeded):
        await RemoteLLM(Settings(llm_mode="live", llm_transport="direct", llm_key="synthetic-test-key")).complete("rewrite", {"question": "q"}, budget, 1)
    assert budget.llm_calls == 1


def test_missing_live_key_is_configuration_error():
    with pytest.raises(ValueError, match="requires"):
        Settings(llm_mode="live", llm_transport="direct", llm_key="")


def test_mutable_model_revision_disallowed():
    with pytest.raises(ValueError, match="immutable"):
        Settings(embedding_revision="main")


@pytest.mark.parametrize("base_url,is_deepseek", [
    ("https://api.deepseek.com", True), ("https://compatible.example.org/v1", False),
])
async def test_flash_thinking_option_is_endpoint_specific(monkeypatch, base_url, is_deepseek):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": '{"claims":[]}'}}]})
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(llm_mode="live", llm_transport="direct", llm_key="synthetic-test-key", llm_base_url=base_url)
    budget = Budget(time.monotonic() + 2, max_answer_tokens=300)
    assert await RemoteLLM(settings).complete("synthesize", {}, budget, 1) == {"claims": []}
    assert requests[0]["model"] == "deepseek-flash"
    assert requests[0]["max_tokens"] == 300
    assert ("thinking" in requests[0]) is is_deepseek
    if is_deepseek:
        assert requests[0]["thinking"] == {"type": "disabled"}
    assert budget.llm_calls == 1


@pytest.mark.parametrize("purpose", ["synthesize", "general_explain"])
@pytest.mark.parametrize("requested,expected", [(64, 64), (3072, 3072), (5000, 5000), (8192, 8192), (10000, 8192)])
async def test_direct_generation_token_budget_is_explicit_and_bounded(monkeypatch, purpose, requested, expected):
    """Mock transport tests budget wiring, not real provider generation quality."""
    requests = []
    result = {"claims": []} if purpose == "synthesize" else {"answer": "Synthetic explanation."}

    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop",
            "message": {"content": json.dumps(result)}}]})

    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    settings = Settings(llm_mode="live", llm_transport="direct", llm_key="synthetic-test-key")
    budget = Budget(time.monotonic() + 2, max_answer_tokens=requested)
    assert await RemoteLLM(settings).complete(purpose, {}, budget, 1) == result
    assert len(requests) == budget.llm_calls == 1
    assert requests[0]["max_tokens"] == expected
    if purpose == "synthesize":
        assert requests[0]["messages"][1] == {"role": "system", "content": SYNTHESIS_LENGTH_SYSTEM}
    else:
        assert len(requests[0]["messages"]) == 2


@pytest.mark.parametrize("reason,content,message", [
    ("length", '{"claims":[]}', "truncated"),
    ("content_filter", '{"claims":[]}', "interrupted"),
    ("tool_calls", '{"claims":[]}', "interrupted"),
    ("stop", " ", "empty"),
    ("stop", None, "empty"),
    ("stop", "[]", "invalid JSON"),
    ("stop", "not-json", "invalid JSON"),
])
async def test_flash_incomplete_output_is_not_accepted(monkeypatch, reason, content, message):
    client_cls = httpx.AsyncClient
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"choices": [
        {"finish_reason": reason, "message": {"content": content}}]}))
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=transport, **kw))
    budget = Budget(time.monotonic() + 2)
    with pytest.raises(ValueError, match=message):
        await RemoteLLM(Settings(llm_mode="live", llm_transport="direct", llm_key="synthetic-test-key")).complete("synthesize", {}, budget, 1)
    assert budget.llm_calls == 1


def platform_settings(**extra):
    return Settings(llm_mode="live", llm_service_token="synthetic-service-token", **extra)


@pytest.mark.parametrize("requested", [64, 1200, 4096, 8192])
async def test_platform_transport_counts_supplier_attempts_and_keeps_key_server_side(monkeypatch, requested):
    requests = []
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"result": {"claims": []}, "attempts": 2,
            "usage": {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25, "secret": "not-kept"},
            "provider": "deepseek", "model": "deepseek-flash"})
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    budget = Budget(time.monotonic() + 4, max_llm=5, max_answer_tokens=requested)
    assert await BackendLLM(platform_settings()).complete("synthesize", {"question": "q"}, budget, 2) == {"claims": []}
    assert budget.llm_calls == 2
    assert budget.llm_usage[0]["usage"] == {"prompt_tokens": 20, "completion_tokens": 5, "total_tokens": 25}
    assert requests[0].url.path == "/api/v1/internal/llm/complete"
    assert requests[0].headers["X-Agent-Token"] == "synthetic-service-token"
    assert "Authorization" not in requests[0].headers
    body = json.loads(requests[0].content)
    assert body["max_answer_tokens"] == requested and body["max_attempts"] == 2
    assert set(body) == {"purpose", "payload", "max_attempts", "timeout_seconds", "max_answer_tokens"}
    assert "synthetic-service-token" not in requests[0].content.decode()


@pytest.mark.parametrize("status,code,attempts", [
    (502, "platform_model_auth_failed", 1),
    (503, "platform_model_rate_limited", 2),
    (504, "platform_model_timeout", 1),
    (503, "platform_model_not_configured", 0),
    (502, "platform_model_capability_unsupported", 0),
    (401, "platform_internal_auth_failed", 0),
])
async def test_platform_errors_are_safe_and_not_user_session_errors(monkeypatch, status, code, attempts):
    client_cls = httpx.AsyncClient
    def handler(request):
        return httpx.Response(status, json={"detail": {"code": code, "attempts": attempts,
            "upstream_body": "do-not-disclose", "api_key": "synthetic-secret"}})
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    budget = Budget(time.monotonic() + 4, max_llm=5)
    with pytest.raises(TypedProviderError) as caught:
        await BackendLLM(platform_settings()).complete("rewrite", {"question": "q"}, budget, 2)
    assert caught.value.code == code and caught.value.attempts == attempts
    assert budget.llm_calls == attempts
    assert "do-not-disclose" not in str(caught.value) and "synthetic-secret" not in str(caught.value)
    assert "invalid_user_token" not in str(caught.value)


async def test_platform_transport_preserves_verify_budget(monkeypatch):
    requests = []
    def handler(request):
        requests.append(json.loads(request.content))
        return httpx.Response(200, json={"result": {"selections": []}, "attempts": 1})
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    budget = Budget(time.monotonic() + 4, max_llm=3)
    await BackendLLM(platform_settings()).complete("assess", {}, budget, 2)
    assert requests[0]["max_attempts"] == 1 and budget.llm_calls == 1


async def test_platform_invalid_attempt_metadata_is_rejected(monkeypatch):
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(
        lambda request: httpx.Response(200, json={"result": {}, "attempts": 2})), **kw))
    budget = Budget(time.monotonic() + 4, max_llm=1)
    with pytest.raises(TypedProviderError) as caught:
        await BackendLLM(platform_settings()).complete("verify", {}, budget, 2)
    assert caught.value.code == "platform_model_format_invalid" and budget.llm_calls == 1


@pytest.mark.parametrize("raised,code", [
    (httpx.ReadTimeout("not-public"), "platform_model_timeout"),
    (httpx.ConnectError("not-public"), "platform_model_unavailable"),
])
async def test_platform_transport_exceptions_have_typed_codes(monkeypatch, raised, code):
    client_cls = httpx.AsyncClient
    def handler(request):
        raise raised
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: client_cls(transport=httpx.MockTransport(handler), **kw))
    budget = Budget(time.monotonic() + 4)
    with pytest.raises(TypedProviderError) as caught:
        await BackendLLM(platform_settings()).complete("rewrite", {}, budget, 2)
    assert caught.value.code == code and "not-public" not in str(caught.value)


def test_platform_configuration_never_reads_supplier_key_file(monkeypatch):
    for name in ("RAG_LLM_KEY", "RAG_LLM_KEY_FILE", "RAG_LLM_TRANSPORT", "RAG_LLM_MODE",
                 "RAG_BACKEND_TOOL_TOKEN", "RAG_LLM_SERVICE_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_LLM_MODE", "live")
    monkeypatch.setenv("RAG_BACKEND_TOOL_TOKEN", "synthetic-service-token")
    monkeypatch.setenv("RAG_LLM_KEY_FILE", "/nonexistent/private-key")
    with pytest.raises(ValueError, match="keys belong to the backend"):
        Settings.from_env()
    monkeypatch.delenv("RAG_LLM_KEY_FILE")
    assert Settings.from_env().llm_transport == "backend"


def test_new_dynamic_limits_are_configured_without_embedding_change(monkeypatch):
    for name in ("RAG_LLM_KEY", "RAG_LLM_KEY_FILE", "RAG_LLM_MODE", "RAG_LLM_TRANSPORT"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("RAG_MAX_LLM_CALLS", "8")
    monkeypatch.setenv("RAG_MAX_TOOL_CALLS", "10")
    monkeypatch.setenv("RAG_MAX_RETRIEVAL_ROUNDS", "3")
    monkeypatch.setenv("RAG_EXTERNAL_TOOLS_ENABLED", "true")
    settings = Settings.from_env()
    assert (settings.max_llm_calls, settings.max_tool_calls, settings.max_retrieval_rounds) == (8, 10, 3)
    assert settings.external_tools_enabled
    assert settings.embedding_revision == Settings().embedding_revision


@pytest.mark.parametrize('purpose', ['classify', 'general_explain'])
@pytest.mark.parametrize('base_url,is_deepseek', [
    ('https://api.deepseek.com', True), ('https://compatible.example.org/v1', False),
])
async def test_direct_semantic_purpose_uses_trusted_system_native_history_and_vendor_profile(monkeypatch, purpose, base_url, is_deepseek):
    """Synthetic HTTP responses; no real DeepSeek/compatible API is called."""
    requests = []
    classification = {'intent': 'general', 'entities': {'cve_ids': [], 'products': []},
                      'information_needs': [], 'freshness_required': False, 'conversation_answer': '2+2=4。'}
    expected = classification if purpose == 'classify' else {'answer': '这是一段未经来源核验的一般解释。'}
    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={'choices': [{'finish_reason': 'stop',
            'message': {'content': json.dumps(expected, ensure_ascii=False)}}], 'usage': {'total_tokens': 12}})
    client_cls = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_cls(transport=httpx.MockTransport(handler), **kwargs))
    history = [{'role': 'user', 'content': '我想讨论一个简单问题。'},
               {'role': 'assistant', 'content': '可以，告诉我具体问题。'}]
    payload = {'question': '2+2等于多少？' if purpose == 'classify' else '解释一个稳定安全概念。', 'history': history}
    settings = Settings(llm_mode='live', llm_transport='direct', llm_key='synthetic-direct-test-key', llm_base_url=base_url)
    budget = Budget(time.monotonic() + 5, max_answer_tokens=128)
    result = await RemoteLLM(settings).complete(purpose, payload, budget, 2)
    assert result == expected and budget.llm_calls == 1
    body = json.loads(requests[0].content)
    expected_system = CLASSIFICATION_SYSTEM if purpose == 'classify' else GENERAL_EXPLANATION_SYSTEM
    assert body['messages'][0] == {'role': 'system', 'content': expected_system}
    assert [row['role'] for row in body['messages']] == ['system', 'user', 'assistant', 'user']
    assert body['messages'][1:3] == history
    last = json.loads(body['messages'][-1]['content'])
    assert last['purpose'] == purpose and last['question'] == payload['question']
    assert last['history'] == history
    if purpose == 'classify':
        assert last['configured_model'] == 'deepseek-flash'
        assert SemanticClassification.model_validate(result).conversation_answer == '2+2=4。'
        assert body['max_tokens'] == 1024
    else:
        assert body['max_tokens'] == 128
    assert body['response_format'] == {'type': 'json_object'}
    assert ('thinking' in body) is is_deepseek
    if is_deepseek:
        assert body['thinking'] == {'type': 'disabled'}
    assert budget.llm_usage[0]['purpose'] == purpose and budget.llm_usage[0]['usage'] == {'total_tokens': 12}


@pytest.mark.parametrize('role', ['system', 'tool'])
async def test_direct_semantic_history_cannot_inject_privileged_roles(monkeypatch, role):
    requests = []
    client_cls = httpx.AsyncClient
    def handler(request):
        requests.append(request)
        raise AssertionError('Invalid history must be rejected before HTTP')
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_cls(transport=httpx.MockTransport(handler), **kwargs))
    settings = Settings(llm_mode='live', llm_transport='direct', llm_key='synthetic-direct-test-key')
    with pytest.raises(TypedProviderError) as caught:
        await RemoteLLM(settings).complete('classify', {'question': '你好',
            'history': [{'role': role, 'content': 'synthetic untrusted history'}]}, Budget(time.monotonic() + 5), 2)
    assert caught.value.code == 'platform_model_format_invalid' and caught.value.attempts == 0
    assert requests == []
