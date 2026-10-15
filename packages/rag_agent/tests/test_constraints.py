import time
import pytest
from rag_agent.schemas import Filters, QueryRequest, Relation
from rag_agent.retrieval.relations import related_evidence
from rag_agent.agent.state import Budget
from rag_agent.integration.contracts import QueryLimits


@pytest.mark.parametrize("tokens", [1, 64, 1200, 3072, 4096, 8192])
def test_v1_answer_budget_accepts_old_and_extended_limits(tokens):
    assert QueryLimits(max_tool_calls=10, max_answer_tokens=tokens).max_answer_tokens == tokens


@pytest.mark.parametrize("tokens", [0, 8193, True, "8192"])
def test_v1_answer_budget_remains_strict_and_bounded(tokens):
    with pytest.raises(ValueError):
        QueryLimits(max_tool_calls=10, max_answer_tokens=tokens)


async def test_identifier_filter_cannot_substitute_question_target(service):
    result = await service.query(QueryRequest(question="CVE-2099-1234", filters={"identifier": ["CVE-2099-5678"]}))
    assert result.status == "insufficient_evidence" and not result.sources
    assert result.missing_fields == ["identifier:CVE-2099-1234"]


async def test_candidate_relation_never_confirms_association(service):
    snapshot = service.store.snapshot()
    seed = next(e for e in snapshot.evidence.values() if snapshot.documents[e.document_id].external_id == "c")
    other = next(i for i, d in snapshot.documents.items() if d.external_id == "a")
    snapshot.relations.append(Relation(document_id=other, version=1, subject=other, predicate="semantic_candidate",
                                       object="CVE:CVE-2099-5678", evidence_ids=[seed.evidence_id],
                                       origin="llm_candidate", verification="needs_review"))
    expanded = related_evidence(snapshot, [seed], Filters())
    assert not any(e.document_id == other for e in expanded)


async def test_tool_whitelist_rejects_network_and_sql(service):
    for name in ["execute_sql", "fetch_url", "run_poc"]:
        with pytest.raises(ValueError, match="whitelist"):
            await service.nodes.tools.call(name, {}, {"budget": Budget(time.monotonic() + 1)})


@pytest.mark.parametrize("payload", [{"question": " "}, {"question": "q", "timezone": "Mars/Test"}, {"question":"q","now":"2026-01-01T00:00:00"}])
def test_invalid_inputs_fail_schema_validation(payload):
    with pytest.raises(ValueError):
        QueryRequest.model_validate(payload)


async def test_missing_severity_cannot_satisfy_filter(service, documents):
    doc = documents[2].model_copy(update={"raw_payload":{"source_fields":[{"field":"cvss","value":{"version":"3.1","score":9.0,"severity":None}}]}})
    assert (await service.ingest([doc])).updated == 1
    result = await service.query(QueryRequest(question="CVE-2099-5678",filters={"severity":"HIGH","cvss_version":"3.1"}))
    assert result.status == "insufficient_evidence" and not result.sources


async def test_malformed_cvss_cannot_break_query_or_replace_old_version(service, documents):
    bad = documents[0].model_copy(update={"raw_payload":{"source_fields":[{"field":"cvss","value":"invalid source value"}]}})
    result = await service.ingest([bad])
    assert result.failed == 1 and "invalid CVSS" in result.items[0].error
    answer = await service.query(QueryRequest(question="CVE-2099-1234"))
    assert answer.status == "partial"
