import json
import time
import httpx
from fastapi.testclient import TestClient
import pytest
from rag_agent.config import Settings
from rag_agent.providers.llm import RemoteLLM
from rag_agent.agent.state import Budget, BudgetExceeded


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
    settings = Settings(llm_mode="live", llm_key="synthetic-test-key")
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
        await RemoteLLM(Settings(llm_mode="live", llm_key="synthetic-test-key")).complete("rewrite", {"question": "q"}, budget, 1)
    assert budget.llm_calls == 1


def test_missing_live_key_is_configuration_error():
    with pytest.raises(ValueError, match="requires"):
        Settings(llm_mode="live", llm_key="")


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
    settings = Settings(llm_mode="live", llm_key="synthetic-test-key", llm_base_url=base_url)
    budget = Budget(time.monotonic() + 2, max_answer_tokens=300)
    assert await RemoteLLM(settings).complete("synthesize", {}, budget, 1) == {"claims": []}
    assert requests[0]["model"] == "deepseek-flash"
    assert requests[0]["max_tokens"] == 300
    assert ("thinking" in requests[0]) is is_deepseek
    if is_deepseek:
        assert requests[0]["thinking"] == {"type": "disabled"}
    assert budget.llm_calls == 1


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
        await RemoteLLM(Settings(llm_mode="live", llm_key="synthetic-test-key")).complete("synthesize", {}, budget, 1)
    assert budget.llm_calls == 1
