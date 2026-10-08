"""Fictional regression corpus. It is never added to the running knowledge base."""
from datetime import datetime, timezone
import pytest
from rag_agent.config import Settings
from rag_agent.schemas import DocumentInput, QueryRequest, Message
from rag_agent.service import RAGService
from rag_agent.agent.nodes import resolve_history
from rag_agent.retrieval.hybrid import tokenize, contains_term
from rag_agent.integration.api import _bounded_answer


def corpus():
    return [DocumentInput(external_id=name, source_name="fictional-regression", source_kind="test",
        source_url="https://example.org/fictional/" + name, doc_type="blog", title="Fictional " + name,
        text=text, collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc)) for name, text in [
        ("access", "Agent access control failure permits unauthorized tool execution. Authorization must check the caller permissions."),
        ("audit", "Agent tool audit logs omitted the tool call arguments, preventing investigation of unauthorized actions."),
        ("policy", "安全管理办法要求备案、个人信息保护以及违法内容处置。"),
        ("poison", "Data poisoning corrupts training examples and can introduce model backdoors."),
        ("management", "Management security information concerns generic compliance risk."),
    ]]


@pytest.mark.parametrize("question,expected", [
    ("访问控制相关情报有哪些？", "access"),
    ("智能体工具审计有什么风险？", "audit"),
    ("数据投毒有什么风险？", "poison"),
    ("authorization permissions", "access"),
])
async def test_cross_language_topic_retrieval(tmp_path, question, expected):
    s = RAGService(Settings(db_path=tmp_path / "corpus.db", embedding_provider="mock"))
    await s.ingest(corpus())
    r = await s.query(QueryRequest(question=question))
    assert r.sources and {e.title for e in r.sources} == {"Fictional " + expected}


def test_generic_words_cannot_authorize_retrieval():
    assert tokenize("有什么安全风险，请根据相关情报说明") == []
    assert not contains_term("management", "agent")


@pytest.mark.parametrize("question", ["它影响哪些版本？", "怎么修复？"])
def test_followup_retains_explicit_target_across_short_turns(question):
    history = [Message(role="user", content="CVE-2099-1234 的风险"),
               Message(role="assistant", content="不可当作证据的回答"),
               Message(role="user", content="它影响哪些版本？")]
    resolved, ids, _, clarification = resolve_history(question, history, {}, {})
    assert ids == ["CVE-2099-1234"] and not clarification and resolved.startswith(ids[0])


def test_topic_switch_stops_old_identifier_resolution():
    history = [Message(role="user", content="CVE-2099-1234"), Message(role="user", content="数据投毒有哪些风险？")]
    resolved, ids, _, clarification = resolve_history("怎么修复？", history, {}, {})
    assert not ids and clarification
    resolved, ids, _, clarification = resolve_history("数据投毒有哪些风险？", history, {}, {})
    assert resolved == "数据投毒有哪些风险？" and not ids and not clarification


def test_comparison_requires_two_explicit_targets():
    history = [Message(role="user", content="CVE-2099-1234"), Message(role="user", content="CVE-2099-5678")]
    resolved, ids, _, clarification = resolve_history("这个和上一个有什么区别？", history, {}, {})
    assert set(ids) == {"CVE-2099-1234", "CVE-2099-5678"} and not clarification


class LiveTestDouble:
    """No external HTTP calls; explicit double for live-stage control flow."""
    mode = "live"
    def __init__(self, selection="all", supported=True):
        self.selection, self.supported, self.requests = selection, supported, []
    async def complete(self, purpose, payload, budget, timeout):
        budget.llm()
        self.requests.append(purpose)
        if purpose == "assess":
            ids = [e["evidence_id"] for e in payload["evidence"]]
            return {"evidence_ids": ids if self.selection == "all" else [] if self.selection == "empty" else ["forged"]}
        if purpose == "verify":
            return {"supported_claim_indexes": [0] if self.supported else []}
        if purpose == "rewrite":
            return {"question": payload["question"]}
        e = payload["evidence"][0]
        return {"claims": [{"text": e["excerpt"], "evidence_ids": [e["evidence_id"]]}]}


@pytest.mark.parametrize("selection", ["empty", "forged"])
async def test_relevance_rejection_never_generates(tmp_path, selection):
    llm = LiveTestDouble(selection)
    s = RAGService(Settings(db_path=tmp_path / "reject.db", embedding_provider="mock"), llm=llm)
    await s.ingest(corpus())
    r = await s.query(QueryRequest(question="访问控制风险"))
    assert r.status == "insufficient_evidence" and not r.sources and not r.facts
    assert llm.requests == ["assess"]


async def test_semantically_unsupported_claim_removed(tmp_path):
    llm = LiveTestDouble(supported=False)
    s = RAGService(Settings(db_path=tmp_path / "support.db", embedding_provider="mock"), llm=llm)
    await s.ingest(corpus())
    r = await s.query(QueryRequest(question="访问控制风险"))
    assert r.status == "insufficient_evidence" and not r.sources and not r.claims
    assert llm.requests == ["assess", "synthesize", "verify"] and r.metrics.llm_calls == 3


async def test_live_stages_preserve_valid_supported_answer(tmp_path):
    llm = LiveTestDouble()
    s = RAGService(Settings(db_path=tmp_path / "supported.db", embedding_provider="mock"), llm=llm)
    await s.ingest(corpus())
    r = await s.query(QueryRequest(question="访问控制风险"))
    assert r.status == "ok" and r.sources and r.mode == "live" and r.metrics.llm_calls == 3


def test_utf8_bound_never_emits_half_claim_or_half_reference():
    text, truncated = _bounded_answer("这是一句完整的结论。 [1]\n" + "很长的第二句" * 30 + " [2]", 100)
    assert truncated and "[1]" in text and "[2]" not in text
    text, truncated = _bounded_answer("长句" * 100 + " [1]", 80)
    assert truncated and "请缩小查询范围" in text and "[" not in text
