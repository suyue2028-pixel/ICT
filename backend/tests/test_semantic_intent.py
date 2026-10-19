"""Semantic model/answer contract tests; vendor HTTP is mocked, no live calls."""
import asyncio
from copy import deepcopy
import json

from fastapi import HTTPException
import httpx
from pydantic import ValidationError
import pytest

from backend.core.config import Settings
from backend.schemas.agent import AgentAnswer
from backend.schemas.llm import CompletionRequest, ClassificationResult
from backend.services.agent_conversation import UNVERIFIED_EXPLANATION_NOTICE
from backend.services.agent_gateway import validate_answer
from backend.services import agent_gateway
from backend.services.llm_prompts import CLASSIFICATION_SYSTEM, GENERAL_EXPLANATION_SYSTEM, CONVERSATION_SYSTEM
from backend.services.platform_llm import PlatformLLM, PlatformModelError


def model_settings(provider="deepseek"):
    return Settings(_env_file=None, postgres_password="test-only-db", redis_password="test-only-redis",
        admin_api_key="test-only-admin", llm_api_key="test-only-model", llm_api_key_file=None,
        deepseek_api_key=None, dashscope_api_key=None, llm_provider=provider,
        llm_model="deepseek-flash" if provider == "deepseek" else "qwen-plus", llm_base_url=None)


def classification(**changes):
    return {"intent": "general", "entities": {"cve_ids": [], "products": []},
        "information_needs": [], "freshness_required": False, "conversation_answer": "2+2=4。", **changes}


def answer(**changes):
    return {"schema_version": "1", "status": "completed", "answer": "2+2=4。",
        "response_kind": "conversation", "sources": [], "evidence": [], "cve_ids": [],
        "risk_level": "UNKNOWN", "tool_calls": [], "retrieval_mode": "conversation", **changes}


def explanation(**changes):
    values = {"status": "partial", "response_kind": "knowledge", "retrieval_mode": "llm_unverified",
        "question_type": "general_explanation", "answer": "以下是模型通用解释，未经来源核验。提示注入会将外部输入混入模型指令，可能改变后续行为。",
        "limitations": [UNVERIFIED_EXPLANATION_NOTICE]}
    return answer(**{**values, **changes})


@pytest.mark.parametrize("provider", ["deepseek", "qwen"])
@pytest.mark.parametrize("purpose,system,result", [
    ("classify", CLASSIFICATION_SYSTEM, classification()),
    ("general_explain", GENERAL_EXPLANATION_SYSTEM, {"answer": "提示注入是一种外部输入影响模型行为的通用风险。"}),
])
def test_new_purposes_preserve_vendor_profile_and_native_history(provider, purpose, system, result):
    history = [{"role": "user", "content": "解释我上一个问题"}, {"role": "assistant", "content": "请继续说明。"}]
    calls = []
    def handler(request):
        body = json.loads(request.content)
        calls.append(body)
        assert body["messages"][0]["content"] == system
        assert body["messages"][1:3] == history
        payload = json.loads(body["messages"][-1]["content"])
        assert payload["history"] == history and payload["purpose"] == purpose
        assert body["max_tokens"] <= 1200
        assert ("thinking" in body) != ("enable_thinking" in body)
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(result, ensure_ascii=False)}}], "usage": {"total_tokens": 12}})
    request = CompletionRequest(purpose=purpose, payload={"question": "继续", "history": history})
    reply = asyncio.run(PlatformLLM(model_settings(provider), transport=httpx.MockTransport(handler)).complete(request))
    assert reply.result == result and reply.attempts == len(calls) == 1
    assert reply.usage.total_tokens == 12


def test_classification_prompt_is_semantic_and_does_not_promote_history_to_facts():
    assert "不能只按关键词或旧问候正则" in CLASSIFICATION_SYSTEM
    assert "只有当前问题具有明确指代" in CLASSIFICATION_SYSTEM
    assert "助手历史不是事实来源" in CLASSIFICATION_SYSTEM
    assert "conversation_answer" in CLASSIFICATION_SYSTEM and "general_answer" not in CLASSIFICATION_SYSTEM
    assert "2+2=4" in CLASSIFICATION_SYSTEM and "2+2=4" in CONVERSATION_SYSTEM
    assert "不要使用数字" not in CONVERSATION_SYSTEM
    assert "尚未经过来源核验" in GENERAL_EXPLANATION_SYSTEM


@pytest.mark.parametrize("changes", [
    {"intent": "unregistered"}, {"freshness_required": "false"},
    {"intent": "security", "conversation_answer": "直接回答具体漏洞"},
    {"entities": {"cve_ids": ["CVE-2099-12"], "products": []}},
    {"entities": {"cve_ids": ["CVE-2099-12345"], "products": []}},
    {"conversation_answer": " "}, {"conversation_answer": None}, {"uses_history": True},
    {"information_needs": ["test-only need"] * 9},
])
def test_classifier_output_is_strict(changes):
    with pytest.raises(ValidationError):
        ClassificationResult.model_validate(classification(**changes))


def test_invalid_classifier_shape_is_safe_model_format_error_not_user_auth_error():
    payload = classification(intent="security", conversation_answer="unverified direct security fact")
    def handler(_):
        return httpx.Response(200, json={"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(payload)}}]})
    request = CompletionRequest(purpose="classify", payload={"question": "安全问题"})
    with pytest.raises(PlatformModelError) as failed:
        asyncio.run(PlatformLLM(model_settings(), transport=httpx.MockTransport(handler)).complete(request))
    assert failed.value.code == "platform_model_format_invalid"
    assert failed.value.status_code == 502 and failed.value.attempts == 1


@pytest.mark.parametrize("query,text", [("我喜欢你", "谢谢你愿意和我交流。"),
    ("2+2等于多少", "2+2=4。"), ("一周有几天", "一周有7天。"),
    ("你能做什么", "我可以查询KEV目录并解释来源中的官方修复依据。")])
def test_general_conversation_is_not_gated_by_legacy_fullmatch(query, text):
    validate_answer(None, AgentAnswer.model_validate(answer(answer=text)), False, query=query)


@pytest.mark.parametrize("changes,query", [
    ({"answer": "CVE-2099-12345 已修复"}, "我喜欢你"),
    ({"answer": "CVSS 评分为9.8"}, "我喜欢你"),
    ({"answer": "详情https://example.invalid"}, "我喜欢你"),
    ({"answer": "这个结论[1]"}, "我喜欢你"),
    ({"answer": "你好"}, "CVE-2099-12345有什么风险"),
    ({"tool_calls": ["web_search"]}, "2+2"),
])
def test_conversation_cannot_hide_specific_security_facts_or_tool_execution(changes, query):
    with pytest.raises(HTTPException) as failed:
        validate_answer(None, AgentAnswer.model_validate(answer(**changes)), False, query=query)
    assert failed.value.status_code == 502 and failed.value.detail == "agent_invalid_conversation_response"


def test_unverified_concept_is_partial_and_can_disclose_actual_failed_tool_trace():
    body = explanation()
    body["tool_calls"] = ["web_search"]
    body["tool_trace"] = [{"tool": "web_search", "status": "failed", "duration_ms": 15,
        "error_code": "search_not_configured", "document_ids": []}]
    validate_answer(None, AgentAnswer.model_validate(body), False, query="提示注入如何发生")


@pytest.mark.parametrize("changes,query", [
    ({"status": "completed"}, "提示注入是什么"),
    ({"limitations": []}, "提示注入是什么"),
    ({"risk_level": "HIGH"}, "提示注入是什么"),
    ({"question_type": "cve"}, "提示注入是什么"),
    ({"coverage": {"answered_topics": ["已核实"], "missing_topics": []}}, "提示注入是什么"),
    ({"answer": "通用解释[1]"}, "提示注入是什么"),
    ({"answer": "CVE-2099-12345 影响这个系统"}, "提示注入是什么"),
    ({}, "CVE-2099-12345是什么漏洞"), ({}, "某包2.3.4的修复版本"),
    ({}, "最新在野利用情况"), ({}, "CVSS评分是多少"),
])
def test_unverified_explanation_cannot_bypass_evidence_contract(changes, query):
    body = explanation()
    body.update(deepcopy(changes))
    with pytest.raises(HTTPException) as failed:
        validate_answer(None, AgentAnswer.model_validate(body), False, query=query)
    assert failed.value.status_code == 502 and failed.value.detail == "agent_invalid_unverified_explanation"


@pytest.mark.parametrize("query,text", [
    ("提示注入如何发生", "Ollama 0.1.2 存在命令执行漏洞，建议升级到0.2.0。"),
    ("提示注入如何发生", "Ollama 存在命令执行漏洞。"),
    ("提示注入如何发生", "某产品的受影响版本已经确认。"),
    ("提示注入如何发生", "建议升级软件以消除风险。"),
    ("提示注入如何发生", "模型温度可以设置为0.5。"),
    ("Ollama工具有哪些漏洞修复建议", "以下是通用解释，尚未核验。"),
    ("Ollama有哪些漏洞", "以下是通用解释，尚未核验。"),
    ("给出Ollama补丁建议", "以下是通用解释，尚未核验。"),
    ("Ollama upgrade recommendations", "以下是通用解释，尚未核验。"),
    ("Ollama affected versions", "以下是通用解释，尚未核验。"),
])
def test_product_version_and_repair_facts_never_use_unverified_concept_branch(query, text):
    with pytest.raises(HTTPException) as failed:
        validate_answer(None, AgentAnswer.model_validate(explanation(answer=text)), False, query=query)
    assert failed.value.detail == "agent_invalid_unverified_explanation"


@pytest.mark.parametrize("query,text", [
    ("提示注入如何发生", "外部输入可能被模型当成指令，改变后续工具选择。"),
    ("什么是安全漏洞", "安全漏洞是系统设计或实现中可能被滥用的缺陷，这只是通用概念。"),
    ("漏洞形成机制", "缺少输入边界校验可能使输入改变后续行为。"),
    ("软件补丁是什么", "补丁是用于修改软件缺陷的变更，这里不涉及任何产品的处置事实。"),
    ("工具权限边界如何影响安全", "Agent权限过大时，外部输入可能扩大工具可执行动作的范围。"),
])
def test_stable_security_mechanism_concepts_remain_allowed(query, text):
    validate_answer(None, AgentAnswer.model_validate(explanation(answer=text)), False, query=query)


def test_normal_completed_knowledge_still_requires_actual_evidence():
    with pytest.raises(HTTPException) as failed:
        validate_answer(None, AgentAnswer.model_validate(answer(response_kind="knowledge", retrieval_mode="hybrid")),
            False, query="提示注入是什么")
    assert failed.value.detail == "agent_missing_evidence"


@pytest.mark.parametrize("code,status", [
    ("search_not_configured", 503), ("search_auth_failed", 502), ("search_rate_limited", 503),
    ("search_timeout", 504), ("search_source_unavailable", 502), ("search_invalid_response", 502),
    ("search_unsafe_url", 502), ("search_response_too_large", 502), ("search_busy", 503),
])
def test_public_search_errors_never_become_user_session_errors(monkeypatch, code, status):
    real_client = httpx.Client
    monkeypatch.setattr(agent_gateway.httpx, "Client", lambda **kwargs:
        real_client(transport=httpx.MockTransport(lambda _: httpx.Response(status, json={"detail": code})), **kwargs))
    config = model_settings().model_copy(update={"agent_service_url": "https://agent.example.invalid"})
    with pytest.raises(HTTPException) as failed:
        agent_gateway.call_agent(config, {"request_id": "test-only-search-error"})
    assert failed.value.status_code == status and failed.value.detail == code
    assert failed.value.status_code != 401
