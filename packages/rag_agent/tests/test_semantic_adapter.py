"""Unverified explanation transport guards using synthetic semantic decisions.

Reuse the controlled test double; no real backend, model or search is called.
The success case retains nonempty internal requirement/retrieval models to
cover serialization across the independent public contract model classes.
"""
from datetime import datetime, timezone

import pytest

from rag_agent.agent.semantic import UNVERIFIED_NOTICE
from rag_agent.integration.api import adapt_answer
from rag_agent.integration.backend import BackendError
from rag_agent.integration.contracts import RequirementCoverage as PublicRequirementCoverage
from rag_agent.integration.contracts import RetrievalSummary as PublicRetrievalSummary
from rag_agent.schemas import AnswerCoverage, Assertion, Claim, EvidenceDetail, QueryRequest
from test_semantic_routing import ControlledSemanticLLM, classification, isolated_service


@pytest.fixture
async def unverified_result(tmp_path):
    llm = ControlledSemanticLLM({
        'classify': classification('security', needs=['概念的一般解释']),
        'general_explain': {'answer': '这里仅提供稳定概念的一般解释，不能替代来源核验。'},
    })
    service = isolated_service(tmp_path, llm)
    async def unavailable(name, args, state):
        if name == 'web_search':
            raise BackendError('search_not_configured', status_code=503)
        return []
    result = await service.query(QueryRequest(question='什么是量子密钥分发？'), external_executor=unavailable)
    assert [purpose for purpose, _ in llm.requests] == ['classify', 'general_explain']
    assert result.retrieval_mode_override == 'llm_unverified'
    assert result.requirement_coverage and result.retrieval_summary.rounds > 0
    assert 'web_search' in result.retrieval_summary.supplemental_tools
    return result, service.get_run_summary(result.trace_id)


async def test_unverified_adapter_serializes_nonempty_requirements_and_retrieval_without_source_credit(unverified_result):
    result, summary = unverified_result
    public = adapt_answer(result, {}, summary, configured_llm_mode='live')
    assert public.status == 'partial' and public.retrieval_mode == 'llm_unverified'
    assert public.response_kind == 'knowledge' and public.question_type == 'general_explanation'
    assert public.sources == public.evidence == public.cve_ids == []
    assert public.risk_level == 'UNKNOWN' and not public.coverage.answered_topics
    assert 'facts' not in public.model_dump()
    assert UNVERIFIED_NOTICE in public.limitations and public.answer.startswith(UNVERIFIED_NOTICE)
    assert public.requirement_coverage and isinstance(public.requirement_coverage[0], PublicRequirementCoverage)
    assert all(row.status == 'missing' and row.evidence_ids == [] for row in public.requirement_coverage)
    assert isinstance(public.retrieval_summary, PublicRetrievalSummary)
    assert public.retrieval_summary.rounds == result.retrieval_summary.rounds
    assert public.retrieval_summary.supplemental_tools == result.retrieval_summary.supplemental_tools
    assert public.tool_calls.count('web_search') == 1
    assert next(row for row in public.tool_trace if row.tool == 'web_search').status == 'failed'


@pytest.mark.parametrize('change', ['missing_notice', 'false_verified_notice', 'wrong_status', 'answered_topic_credit'])
async def test_unverified_adapter_rejects_missing_or_false_label_and_claimed_coverage(unverified_result, change):
    result, summary = unverified_result
    updates = {
        'missing_notice': {'limitations': []},
        'false_verified_notice': {'limitations': ['已由实时来源核验。']},
        'wrong_status': {'status': 'ok'},
        'answered_topic_credit': {'coverage': AnswerCoverage(answered_topics=['已验证的概念'], missing_topics=[])},
    }
    with pytest.raises(ValueError, match='invalid unverified explanation'):
        adapt_answer(result.model_copy(update=updates[change]), {}, summary)


@pytest.mark.parametrize('answer', [
    '这条结论有引用 [1]。', '请访问 https://example.org/synthetic。',
    'CVE-2099-1234 是已确认的漏洞。', 'CVSS评分为9.8。',
])
async def test_unverified_adapter_rejects_citation_url_or_specific_vulnerability_fact(unverified_result, answer):
    result, summary = unverified_result
    with pytest.raises(ValueError, match='invalid unverified explanation'):
        adapt_answer(result.model_copy(update={'answer': UNVERIFIED_NOTICE + '\n' + answer}), {}, summary)


@pytest.mark.parametrize('field', ['sources', 'claims', 'facts'])
async def test_unverified_adapter_rejects_injected_source_claim_or_structured_fact(unverified_result, field):
    result, summary = unverified_result
    now = datetime(2099, 1, 1, tzinfo=timezone.utc)
    additions = {
        'sources': [EvidenceDetail(evidence_id='synthetic-evidence', document_id='synthetic-document', version=1,
            title='Synthetic source', url='https://example.org/synthetic', excerpt='Synthetic excerpt',
            locator={'kind': 'char_range', 'start': 0, 'end': 17}, collected_at=now)],
        'claims': [Claim(text='Synthetic unsupported claim', evidence_ids=['synthetic-evidence'])],
        'facts': [Assertion(document_id='synthetic-document', version=1, entity_id='CVE-2099-1234', field='cvss',
            value={'score': 9.8, 'synthetic': True}, evidence_ids=['synthetic-evidence'], origin='llm_candidate',
            verification='needs_review', recorded_at=now)],
    }
    with pytest.raises(ValueError, match='invalid unverified explanation'):
        adapt_answer(result.model_copy(update={field: additions[field]}), {}, summary)


async def test_unverified_adapter_revokes_unmapped_requirement_credit(unverified_result):
    result, summary = unverified_result
    row = result.requirement_coverage[0].model_copy(update={'status': 'supported', 'evidence_ids': ['synthetic-invented']})
    public = adapt_answer(result.model_copy(update={'requirement_coverage': [row]}), {}, summary)
    assert public.requirement_coverage[0].status == 'missing'
    assert public.requirement_coverage[0].evidence_ids == []
    assert not public.coverage.answered_topics and not public.sources and not public.evidence
