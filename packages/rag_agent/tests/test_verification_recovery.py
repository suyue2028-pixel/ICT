"""Bounded verification-format repair; synthetic decisions, no supplier calls."""
import pytest
from rag_agent.schemas import QueryRequest
from rag_agent.providers.llm import TypedProviderError
from test_semantic_routing import ControlledSemanticLLM, classification, professional_responses, isolated_service
from rag_agent.integration.backend import BackendError


@pytest.mark.parametrize('bad', [
    {'supported_claim_indexes': [1]},
    {'supported_claim_indexes': ['0']},
    {'supported_claim_indexes': [0], 'untrusted_extra': 'discard'},
])
async def test_invalid_verify_index_format_gets_one_bounded_repair(service, bad):
    responses=professional_responses(classification('security',ids=['CVE-2099-1234']))
    attempts=[]
    def verify(payload):
        attempts.append(payload)
        return bad if len(attempts)==1 else {'supported_claim_indexes':[0]}
    responses['verify']=verify
    llm=ControlledSemanticLLM(responses)
    service.llm=service.nodes.llm=llm
    result=await service.query(QueryRequest(question='CVE-2099-1234 是什么漏洞？'))
    assert result.status in {'ok','partial'} and result.sources and result.mode=='live'
    assert [p for p,_ in llm.requests]==['classify','synthesize','verify','verify']
    assert attempts[1]['output_contract']['valid_indexes']==[0]
    assert 'verification_schema_retry' in result.warnings


async def test_verification_auth_failure_is_not_format_retry(service):
    responses=professional_responses(classification('security',ids=['CVE-2099-1234']))
    responses['verify']=TypedProviderError('platform_model_auth_failed')
    llm=ControlledSemanticLLM(responses)
    service.llm=service.nodes.llm=llm
    result=await service.query(QueryRequest(question='CVE-2099-1234 是什么漏洞？'))
    assert result.status=='error'
    assert [p for p,_ in llm.requests]==['classify','synthesize','verify']
    assert 'platform_model_auth_failed' in result.warnings
    assert 'verification_schema_retry' not in result.warnings


async def test_verification_repair_obeys_shared_model_budget(service):
    responses=professional_responses(classification('security',ids=['CVE-2099-1234']))
    responses['verify']={'supported_claim_indexes':['0']}
    llm=ControlledSemanticLLM(responses)
    service.settings.max_llm_calls=3
    service.llm=service.nodes.llm=llm
    result=await service.query(QueryRequest(question='CVE-2099-1234 是什么漏洞？'))
    assert result.status=='error' and result.metrics.llm_calls==3
    assert [p for p,_ in llm.requests]==['classify','synthesize','verify']
    assert 'verification_schema_retry' not in result.warnings


async def test_unverified_explanation_repairs_format_once_without_accepting_versions(tmp_path):
    attempts=[]
    def explanation(payload):
        attempts.append(payload)
        return {'answer':'Ollama 0.1.2存在问题，建议升级到0.2.0。'} if len(attempts)==1 else {
            'answer':'提示注入是外部输入干扰既有指令边界的风险。这只是通用概念说明。'}
    llm=ControlledSemanticLLM({'classify':classification('security',needs=['一般机制']),
        'general_explain':explanation})
    service=isolated_service(tmp_path,llm)
    async def unavailable(*args):raise BackendError('search_not_configured')
    result=await service.query(QueryRequest(question='什么是提示注入风险？'),external_executor=unavailable)
    assert result.retrieval_mode_override=='llm_unverified' and not result.sources
    assert len(attempts)==2 and result.metrics.llm_calls==3
    assert 'general_explanation_schema_retry' in result.warnings
    assert 'Ollama' not in result.answer and '0.1.2' not in result.answer
    assert 'output_contract' in attempts[1]


async def test_unverified_explanation_auth_failure_is_not_repaired(tmp_path):
    llm=ControlledSemanticLLM({'classify':classification('security',needs=['一般机制']),
        'general_explain':TypedProviderError('platform_model_auth_failed')})
    service=isolated_service(tmp_path,llm)
    async def unavailable(*args):raise BackendError('search_not_configured')
    result=await service.query(QueryRequest(question='什么是提示注入风险？'),external_executor=unavailable)
    assert result.status=='error' and result.metrics.llm_calls==2
    assert 'general_explanation_schema_retry' not in result.warnings
