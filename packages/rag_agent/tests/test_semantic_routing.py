"""LLM-first routing regressions with controlled synthetic model decisions.

The double advertises mode='live' solely to exercise the online production
branch. It never sends HTTP or calls a real model. All CVE-2099/source records
are explicit fictional fixtures, and these tests do not measure model quality.
"""
from copy import deepcopy

import pytest

from rag_agent.config import Settings
from rag_agent.integration.backend import BackendError
from rag_agent.providers.llm import TypedProviderError
from rag_agent.schemas import QueryRequest
from rag_agent.service import RAGService
from rag_agent.agent.semantic import UNVERIFIED_NOTICE


class ControlledSemanticLLM:
    mode = "live"  # Synthetic double selects the online workflow, not real API use.

    def __init__(self, responses):
        self.responses = responses
        self.requests = []

    async def complete(self, purpose, payload, budget, timeout):
        budget.llm()
        self.requests.append((purpose, deepcopy(payload)))
        if purpose not in self.responses:
            raise AssertionError("Unexpected synthetic model operation: " + purpose)
        response = self.responses[purpose]
        if isinstance(response, Exception):
            raise response
        return response(payload) if callable(response) else deepcopy(response)


def classification(intent, *, ids=(), answer=None, freshness=False, needs=()):
    return {"intent": intent, "entities": {"cve_ids": list(ids), "products": []},
            "information_needs": list(needs), "freshness_required": freshness,
            "conversation_answer": answer}


def isolated_service(tmp_path, llm, *, allow_unverified=True):
    return RAGService(Settings(db_path=tmp_path / "synthetic-semantic.db", embedding_provider="mock",
                               llm_mode="extractive", max_llm_calls=6, max_tool_calls=10,
                               max_retrieval_rounds=3, request_timeout=20,
                               allow_unverified_concepts=allow_unverified), llm=llm)


def reject_snapshot(monkeypatch, service):
    def unexpected(*args, **kwargs):
        raise AssertionError("Daily/classification-failed query must not load a RAG snapshot")
    monkeypatch.setattr(service.store, "snapshot", unexpected)


def professional_responses(decision):
    def draft(payload):
        eid = payload["evidence"][0]["evidence_id"]
        return {"claims": [{"text": "来源记载了该合成漏洞的描述。", "evidence_ids": [eid],
                            "kind": "fact", "topic_ids": []}]}
    return {"classify": decision, "synthesize": draft, "verify": {"supported_claim_indexes": [0]}}


@pytest.mark.parametrize("question,answer", [
    ("我喜欢你", "谢谢你愿意这么说，我们可以继续聊。"),
    ("你好", "你好，今天想聊什么？"),
    ("你是谁", "我是这个平台的助手，可以交流和帮助查找资料。"),
    ("2+2等于多少", "2+2=4。"),
])
async def test_daily_always_classifies_first_and_never_loads_scope_or_snapshot(tmp_path, monkeypatch, question, answer):
    llm = ControlledSemanticLLM({"classify": classification("general", answer=answer)})
    service = isolated_service(tmp_path, llm)
    reject_snapshot(monkeypatch, service)
    async def scope_loader():
        raise AssertionError("Daily query must not load backend document scope")
    async def external(*args):
        raise AssertionError("Daily query must not execute an intelligence/search tool")
    response = await service.query(QueryRequest(question=question), scope_loader=scope_loader, external_executor=external)
    assert [purpose for purpose, _ in llm.requests] == ["classify"]
    assert llm.requests[0][1]["question"] == question
    assert response.response_kind == "conversation" and response.question_type == "general"
    assert response.status == "ok" and response.answer == answer
    assert response.metrics.llm_calls == 1 and response.metrics.tool_calls == 0
    assert not response.sources and not response.claims and not response.facts
    summary = service.get_run_summary(response.trace_id)
    assert summary["tools"] == []
    assert [row["node"] for row in summary["nodes"]] == ["classify"]


@pytest.mark.parametrize("intent", ["security", "general"])
async def test_mixed_greeting_and_explicit_cve_remains_professional_after_classification(service, intent):
    decision = classification(intent, ids=["CVE-2099-1234"] if intent == "security" else [],
                              answer="你好，我们可以继续分析。" if intent == "general" else None,
                              needs=["漏洞说明"])
    llm = ControlledSemanticLLM(professional_responses(decision))
    service.llm = service.nodes.llm = llm
    response = await service.query(QueryRequest(question="你好，帮我分析 CVE-2099-1234 是什么漏洞。"))
    assert [purpose for purpose, _ in llm.requests] == ["classify", "synthesize", "verify"]
    assert response.response_kind == "knowledge" and response.question_type == "cve"
    assert response.sources
    assert {row["tool"] for row in service.get_run_summary(response.trace_id)["tools"]} == {"exact_lookup", "get_assertions"}
    if intent == "general":
        assert "classification_entity_guard" in response.warnings


@pytest.mark.parametrize("bad_response", [
    {"intent": "general"},
    TypedProviderError("platform_model_auth_failed", attempts=1, status_code=502),
])
async def test_classification_failure_is_model_error_not_evidence_shortage(tmp_path, monkeypatch, bad_response):
    llm = ControlledSemanticLLM({"classify": bad_response})
    service = isolated_service(tmp_path, llm)
    reject_snapshot(monkeypatch, service)
    response = await service.query(QueryRequest(question="我喜欢你"))
    assert [purpose for purpose, _ in llm.requests] == ["classify"]
    assert response.status == "error" and "intent_classification_failed" in response.warnings
    assert response.status != "insufficient_evidence" and "分类" in response.answer
    assert not response.sources and response.metrics.tool_calls == 0
    if isinstance(bad_response, TypedProviderError):
        assert "platform_model_auth_failed" in response.warnings


async def test_followup_uses_real_user_history_target_and_preserves_scope(service):
    llm = ControlledSemanticLLM(professional_responses(classification("security", ids=["CVE-2099-1234"], needs=["漏洞说明"])))
    service.llm = service.nodes.llm = llm
    before = service.store.snapshot()
    response = await service.query(QueryRequest(question="这个漏洞是什么问题？", history=[
        {"role": "user", "content": "请查询 CVE-2099-1234。"},
        {"role": "assistant", "content": "此处是合成对话历史，不构成事实依据。"},
    ]))
    assert llm.requests[0][0] == "classify" and len(llm.requests[0][1]["history"]) == 2
    assert response.question_type == "cve" and response.sources
    plan = next(row for row in service.get_run_summary(response.trace_id)["nodes"] if row["node"] == "retrieval_plan")
    assert plan["entities"] == ["CVE-2099-1234"]
    after = service.store.snapshot()
    assert before.versions == after.versions and before.signature == after.signature
    assert all(source.document_id in before.documents for source in response.sources)


@pytest.mark.parametrize("history,proposed", [
    ([{"role": "user", "content": "CVE-2099-1234"}], "CVE-2099-9999"),
    ([{"role": "assistant", "content": "CVE-2099-1234"}], "CVE-2099-1234"),
])
async def test_classifier_cannot_invent_targets_or_promote_assistant_history(tmp_path, monkeypatch, history, proposed):
    llm = ControlledSemanticLLM({"classify": classification("security", ids=[proposed], needs=["修复依据"])})
    service = isolated_service(tmp_path, llm)
    reject_snapshot(monkeypatch, service)
    response = await service.query(QueryRequest(question="这个漏洞怎么修复？", history=history))
    assert response.status == "error" and "intent_classification_failed" in response.warnings
    assert [purpose for purpose, _ in llm.requests] == ["classify"]
    assert not response.sources and response.metrics.tool_calls == 0


@pytest.mark.parametrize("identifier", [None, "CVE-2099-9999"])
async def test_empty_professional_search_failure_allows_only_controlled_concept_explanation(tmp_path, identifier):
    question = "什么是提示注入风险？" if identifier is None else identifier + " 是什么漏洞？"
    decision = classification("security", ids=[identifier] if identifier else [], needs=["问题的机制与依据"])
    responses = {"classify": decision}
    if identifier is None:
        responses["general_explain"] = {"answer": "提示注入涉及输入内容干扰模型原本的指令边界。这只是一般概念说明。"}
    llm = ControlledSemanticLLM(responses)
    service = isolated_service(tmp_path, llm)
    external_calls = []
    async def unavailable(name, args, state):
        external_calls.append(name)
        if name == "web_search":
            raise BackendError("search_not_configured", status_code=503)
        return []
    response = await service.query(QueryRequest(question=question), external_executor=unavailable)
    summary = service.get_run_summary(response.trace_id)
    assert external_calls.count("web_search") == 1
    search = [row for row in summary["tools"] if row["tool"] == "web_search"]
    assert len(search) == 1 and search[0]["status"] == "failed"
    assert any("search_not_configured" in item for item in response.limitations)
    assert not response.sources and not response.claims and not response.facts
    assert response.metrics.tool_calls <= 10
    if identifier is None:
        assert [purpose for purpose, _ in llm.requests] == ["classify", "general_explain"]
        assert response.status == "partial" and response.retrieval_mode_override == "llm_unverified"
        assert response.question_type == "general_explanation" and UNVERIFIED_NOTICE in response.limitations
        assert response.answer.startswith(UNVERIFIED_NOTICE)
    else:
        assert [purpose for purpose, _ in llm.requests] == ["classify"]
        assert response.status == "insufficient_evidence" and response.retrieval_mode_override is None


async def test_unverified_concept_explanation_can_be_disabled(tmp_path):
    llm = ControlledSemanticLLM({"classify": classification("security", needs=["安全概念机制"])})
    service = isolated_service(tmp_path, llm, allow_unverified=False)
    async def unavailable(name, args, state):
        if name == "web_search":
            raise BackendError("search_not_configured", status_code=503)
        return []
    response = await service.query(QueryRequest(question="什么是提示注入风险？"), external_executor=unavailable)
    assert [purpose for purpose, _ in llm.requests] == ["classify"]
    assert response.status == "insufficient_evidence" and response.retrieval_mode_override is None
    assert not response.sources
