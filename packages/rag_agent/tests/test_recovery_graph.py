"""Full LangGraph recovery regressions with explicitly synthetic sources.

Unlike direct after_assess() checks, these execute the compiled graph and prove
that pending tool decisions survive the node/conditional transition. No real
intelligence API, model or existing knowledge database is accessed.
"""
from collections import Counter
from datetime import datetime, timezone

import pytest

from rag_agent.config import Settings
from rag_agent.schemas import DocumentInput, Identifier, QueryRequest
from rag_agent.service import RAGService

NOW = datetime(2099, 1, 1, tzinfo=timezone.utc)


def isolated_service(tmp_path):
    return RAGService(Settings(db_path=tmp_path / "synthetic-recovery.db", embedding_provider="mock",
                               llm_mode="extractive", max_llm_calls=0, max_retrieval_rounds=3,
                               max_tool_calls=10, request_timeout=20))


async def test_graph_executes_selected_external_fault_once_and_discloses_it(tmp_path):
    service = isolated_service(tmp_path)
    calls = Counter()

    async def synthetic_source_fault(name, args, state):
        calls[name] += 1
        error = RuntimeError("explicit synthetic source fault")
        error.code = "synthetic_source_timeout"
        raise error

    result = await service.query(QueryRequest(question="CVE-2099-8888 目前是否在野利用？"),
                                 external_executor=synthetic_source_fault)
    summary = service.get_run_summary(result.trace_id)
    assert calls == {"kev_lookup": 1, "nvd_lookup": 1, "web_search": 1}
    assert [item["tool"] for item in summary["tools"]].count("exact_lookup") == 1
    assert all(item["status"] == "failed" for item in summary["tools"] if item["tool"] in calls)
    assert any("synthetic_source_timeout" in item for item in result.limitations)
    assert result.retrieval_summary.rounds == 3
    assert result.retrieval_summary.stop_reason == "max_rounds"
    assert not result.sources


async def test_graph_empty_supplement_stops_without_repeating_initial_retrieval(tmp_path):
    service = isolated_service(tmp_path)
    calls = []

    async def synthetic_empty(name, args, state):
        calls.append((name, args.get("identifier", args.get("query"))))
        return []

    result = await service.query(QueryRequest(question="CVE-2099-8888 的最新介绍和评分是什么？"),
                                 external_executor=synthetic_empty)
    summary = service.get_run_summary(result.trace_id)
    assert len(calls) == 3 and len(set(calls)) == 3
    assert sum(name == 'web_search' for name, _ in calls) == 1
    assert result.retrieval_summary.rounds == 3
    assert result.retrieval_summary.stop_reason == "max_rounds"
    assert result.metrics.tool_calls == 4
    assert [item["tool"] for item in summary["tools"]].count("exact_lookup") == 1
    assert not result.sources


async def test_graph_new_evidence_allows_third_round_within_tool_budget(tmp_path):
    service = isolated_service(tmp_path)
    calls = []

    async def synthetic_incremental(name, args, state):
        target = args["identifier"]
        calls.append((name, target))
        if name == "kev_lookup":
            return []
        assert name == "nvd_lookup"
        doc = DocumentInput(external_id="synthetic-recovery-" + target, source_name="synthetic graph fixture",
                            source_kind="test", source_url="https://example.org/synthetic/recovery/" + target,
                            doc_type="vulnerability", title="Synthetic graph source " + target,
                            text="Synthetic description for " + target + ". This fictional record has no verified score or exploitation claim.",
                            collected_at=NOW, identifiers=[Identifier(namespace="CVE", value=target)])
        ingest = await service.ingest([doc])
        assert not ingest.failed
        # Match the trusted import boundary: update the real temporary snapshot
        # before returning records belonging to its actual stored document.
        state["snapshot"] = service.store.snapshot()
        state.setdefault("external_checks", {})[(name, target)] = {"synthetic": True}
        document_ids = set(ingest.document_ids)
        return [item for item in state["snapshot"].evidence.values()
                if item.document_id in document_ids and item.locator.get("kind") == "char_range"]

    result = await service.query(QueryRequest(question="比较 CVE-2099-8001 和 CVE-2099-8002 的最新介绍、评分与在野利用情况。"),
                                 external_executor=synthetic_incremental)
    summary = service.get_run_summary(result.trace_id)
    assert len(calls) == 4 and len(set(calls)) == 4
    assert result.retrieval_summary.rounds == 3
    assert result.retrieval_summary.stop_reason == "max_rounds"
    assert result.metrics.tool_calls <= 10
    assert [item["tool"] for item in summary["tools"]].count("exact_lookup") == 1
    source_ids = {identifier.value for doc in service.store.snapshot().documents.values()
                  for identifier in doc.identifiers}
    assert source_ids == {"CVE-2099-8001", "CVE-2099-8002"}
    assert len({item.document_id for item in result.sources}) == 2
    assert all("synthetic" in item.url for item in result.sources)
