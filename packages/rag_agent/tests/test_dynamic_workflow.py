"""Dynamic retrieval decisions and import boundary tests, using synthetic data.

No upstream API or live model is called. Mocked records are never real evidence.
"""
from datetime import datetime, timezone
from types import SimpleNamespace
import time

import pytest

from rag_agent.agent.state import Budget
from rag_agent.agent.workflow import (deduplicate, evaluate, next_tools, package_entities,
                                      requirements, freshness_required)
from rag_agent.config import Settings
from rag_agent.integration.backend import BackendError, BackendSettings
from rag_agent.integration.intelligence import IntelligenceExecutor
from rag_agent.schemas import Assertion, Claim, DocumentInput, EvidenceDetail, Filters, Identifier, QueryRequest
from rag_agent.service import RAGService

CVE = "CVE-2099-12345"
NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


def document(source_url="https://source.example/advisory", identifier=CVE):
    return DocumentInput(source_name="synthetic source", source_kind="nvd", source_url=source_url,
        doc_type="vulnerability", title=f"Synthetic {identifier}", text="Synthetic agent security source.",
        collected_at=NOW, identifiers=[Identifier(namespace="CVE", value=identifier)])


def evidence(eid="e1", docid="d1", url="https://source.example/advisory", excerpt="Synthetic agent source."):
    return EvidenceDetail(evidence_id=eid, document_id=docid, version=1, title="Synthetic source",
        url=url, excerpt=excerpt, locator={"kind": "char_range", "start": 0, "end": len(excerpt)}, collected_at=NOW)


def state(question="CVE-2099-12345 目前是否在野利用"):
    return {"resolved_question": question, "identifiers": [CVE], "evidence": [], "assertions": [],
        "snapshot": SimpleNamespace(documents={}, chunks=[], evidence={}, assertions=[]),
        "external_checked": {}, "limitations": [], "fresh_required": True,
        "explicit_filters": Filters(), "budget": Budget(time.monotonic() + 10),
        "attempted_tools": set(), "requirements": requirements(question, [CVE]), "relevance_assignments": {}}


def test_kev_no_entry_cannot_become_negative_exploitation_fact():
    data = state()
    data["limitations"] = ["kev_no_entry_is_not_proof_of_no_exploitation"]
    data["external_checked"] = {"kev_lookup": {"checked_at": NOW.isoformat(), "cache_hit": False}}
    rows = evaluate(data, final=True, verified=True)
    exploit = next(r for r in rows if r.requirement_id == "exploitation")
    assert exploit.status == "missing" and not exploit.evidence_ids
    assert "不能" in exploit.reason and "没有在野利用" in exploit.reason


def test_same_source_excerpt_is_one_item_not_independent_corroboration():
    first = evidence("one", "doc-one", excerpt="Agent tool permission boundary.")
    second = evidence("two", "doc-two", url="https://source.example/advisory/", excerpt="Agent  tool\npermission boundary.")
    third = evidence("three", "doc-three", url="https://other-publisher.example/advisory", excerpt="Agent tool permission boundary.")
    snapshot = SimpleNamespace(documents={"doc-one": document(),
        "doc-two": document("https://source.example/advisory/"),
        "doc-three": document("https://other-publisher.example/advisory")})
    result = deduplicate([first, second, third], snapshot)
    assert [e.evidence_id for e in result] == ["one", "three"]


def test_affected_cpe_record_remains_partial_and_conflicts_remain_explicit():
    data = state("CVE-2099-12345 的受影响版本与CVSS评分")
    e = evidence()
    data["evidence"] = [e]
    data["snapshot"].documents = {"d1": document()}
    data["assertions"] = [Assertion(document_id="d1", version=1, entity_id=CVE,
        field="affected_version", value={"parsed": False, "cpe_conditions": ["raw conditions"]},
        evidence_ids=["e1"], origin="source_structured", verification="source_recorded", recorded_at=NOW),
        Assertion(document_id="d1", version=1, entity_id=CVE, field="cvss", value={"score": 7.5, "source": "synthetic"},
        evidence_ids=["e1"], origin="source_structured", verification="source_recorded", recorded_at=NOW)]
    data["conflicts"] = [{"field": "cvss"}]
    rows = evaluate(data)
    assert next(r for r in rows if r.requirement_id == "affected_version").status == "partial"
    assert next(r for r in rows if r.requirement_id == "cvss").status == "conflicting"


def test_tool_selection_uses_registry_and_never_repeats_identical_lookup():
    data = state()
    data["requirement_coverage"] = evaluate(data)
    assert next_tools(data, {"exact_lookup", "hybrid_search"}) == []
    tools = next_tools(data, {"kev_lookup", "nvd_lookup", "shell"})
    assert [name for name, _ in tools] == ["kev_lookup", "nvd_lookup"]
    data["attempted_tools"] = {(name, tuple(sorted(args.items()))) for name, args in tools}
    assert next_tools(data, {"kev_lookup", "nvd_lookup"}) == []


@pytest.mark.parametrize("question,expected", [
    ("OSV PyPI langchain 0.1.0 的风险", {"ecosystem": "PyPI", "package": "langchain", "version": "0.1.0"}),
    ("npm @scope/tool version 1.2.0 是否有漏洞", {"ecosystem": "npm", "package": "@scope/tool", "version": "1.2.0"}),
    ("查询 langchain 漏洞", {}),
    ("OSV PyPI langchain 漏洞", {}),
    ("OSV npm 的风险", {}),
])
def test_package_entity_extraction_never_invents_missing_values(question, expected):
    assert package_entities(question) == expected


def test_freshness_is_explicit_for_kev_and_not_assumed_for_old_overview():
    assert freshness_required("CVE-2099-12345 当前在野利用情况")
    assert not freshness_required("CVE-2099-12345 是什么漏洞")


class FakeIndex:
    def __init__(self):
        self.calls = []
    async def upsert(self, row):
        self.calls.append(row)
    def mappings(self, is_demo):
        assert is_demo is False
        return {}


def executor_and_state(tmp_path):
    index = FakeIndex()
    settings = BackendSettings(base_url="http://127.0.0.1:8010", tool_token="synthetic-tool", write_token="synthetic-write",
        db_path=tmp_path / "synthetic-index.db", rag_settings=Settings(embedding_provider="mock"))
    snapshot = SimpleNamespace(documents={}, chunks=[], evidence={}, assertions=[])
    service = SimpleNamespace(store=SimpleNamespace(snapshot=lambda *args: snapshot))
    executor = IntelligenceExecutor(service, index, settings)
    data = state()
    data["budget"].tool("nvd_lookup")
    return executor, index, data


async def test_executor_rejects_unregistered_tool_and_wrong_requested_identifier(tmp_path):
    executor, index, data = executor_and_state(tmp_path)
    calls = []
    async def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {}
    executor.client._request = request
    for name, args in [("shell", {"command": "env"}), ("nvd_lookup", {"identifier": "CVE-2099-99999"})]:
        with pytest.raises(BackendError):
            await executor(name, args, data)
    assert calls == [] and index.calls == []


async def test_executor_rejects_demo_record_before_import(tmp_path):
    executor, index, data = executor_and_state(tmp_path)
    async def request(*args, **kwargs):
        return {"schema_version": "1", "tool": "nvd_lookup", "status": "completed",
                "documents": [{"document_id": "synthetic-demo", "is_demo": True}]}
    executor.client._request = request
    with pytest.raises(BackendError, match="intelligence_scope_mismatch"):
        await executor("nvd_lookup", {"identifier": CVE}, data)
    assert index.calls == []


async def test_executor_retains_cached_check_time_and_absence_limitation(tmp_path):
    executor, index, data = executor_and_state(tmp_path)
    async def request(method, path, **kwargs):
        assert path == "/api/v1/internal/intelligence/lookup" and kwargs["write"] is True
        assert kwargs["body"] == {"tool": "kev_lookup", "identifier": CVE}
        return {"schema_version": "1", "tool": "kev_lookup", "status": "empty", "documents": [],
                "checked_at": NOW.isoformat(), "cache_hit": True,
                "limitation": "kev_no_entry_is_not_proof_of_no_exploitation"}
    executor.client._request = request
    assert await executor("kev_lookup", {"identifier": CVE}, data) == []
    assert data["external_checked"]["kev_lookup"] == {"checked_at": NOW.isoformat(), "cache_hit": True}
    assert data["limitations"] == ["kev_no_entry_is_not_proof_of_no_exploitation"]
    assert index.calls == [] and data["retrieval_counts"]["imported_documents"] == 0


async def test_executor_rejects_wrong_returned_cve_before_import(tmp_path):
    executor, index, data = executor_and_state(tmp_path)
    async def request(*args, **kwargs):
        return {"schema_version": "1", "tool": "nvd_lookup", "status": "completed", "documents": [{
            "document_id": "synthetic-other", "is_demo": False, "raw_id": "CVE-2099-99999",
            "revision": {"vulnerability_id": "CVE-2099-99999", "aliases": ["CVE-2099-99999"]}}]}
    executor.client._request = request
    with pytest.raises(BackendError, match="intelligence_entity_mismatch"):
        await executor("nvd_lookup", {"identifier": CVE}, data)
    assert index.calls == []


async def test_executor_rejects_arguments_overriding_registered_tool(tmp_path):
    executor, index, data = executor_and_state(tmp_path)
    calls = []
    async def request(*args, **kwargs):
        calls.append((args, kwargs))
        return {"schema_version": "1", "tool": "nvd_lookup", "status": "empty", "documents": []}
    executor.client._request = request
    with pytest.raises(BackendError):
        await executor("nvd_lookup", {"identifier": CVE, "tool": "osv_lookup"}, data)
    assert calls == [] and index.calls == []


async def test_imported_knowledge_uses_ranked_passage_not_first_paragraph(tmp_path):
    executor,index,data=executor_and_state(tmp_path)
    data['identifiers']=[]
    data['resolved_question']='智能体工具调用日志如何审计'
    data['requirement_coverage']=[SimpleNamespace(requirement_id='audit_logging',label='调用日志',status='missing')]
    later=evidence('ranked-later','rag-import',excerpt='Synthetic logging of tool invocations.')
    index.mappings=lambda scope:{('rag-import',1):{'document_id':'synthetic-backend-document'}}
    snapshot=SimpleNamespace(documents={},chunks=[],evidence={},assertions=[])
    executor.service.store.snapshot=lambda *args:snapshot
    recorded=[]
    async def search(scoped,question,filters,limit,diagnostics=None):
        recorded.append((scoped,question,limit))
        return [later]
    executor.service.retriever=SimpleNamespace(search=search)
    async def request(*args,**kwargs):
        return {'schema_version':'1','tool':'knowledge_lookup','status':'completed',
            'documents':[{'document_id':'synthetic-backend-document','is_demo':False}],
            'checked_at':NOW.isoformat(),'cache_hit':False}
    executor.client._request=request
    result=await executor('knowledge_lookup',{'topic':'agency'},data)
    assert result==[later] and recorded[0][0] is snapshot
    assert 'audit logs' in recorded[0][1] and recorded[0][2]==4


@pytest.mark.parametrize("question", ["通过 OSV 查询 PyPI langchain 漏洞", "通过 OSV 查询 npm 的包漏洞"])
async def test_osv_missing_package_or_version_requests_clarification(tmp_path, question):
    service = RAGService(Settings(db_path=tmp_path / "clarification.db", embedding_provider="mock", external_tools_enabled=True))
    result = await service.query(QueryRequest(question=question))
    assert result.question_type == "clarification"
    assert any(label in result.answer.lower() for label in ("版本", "package", "包名"))
    assert not result.sources and result.metrics.tool_calls == 0
