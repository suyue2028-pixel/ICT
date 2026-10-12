"""Synthetic online-branch safety regressions; no network or real model calls.

Live is the double's branch selector only. All records live in pytest temp DBs.
"""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone

import pytest

from rag_agent.agent.semantic import safe_unverified_answer
from rag_agent.config import Settings
from rag_agent.integration.backend import BackendError
from rag_agent.schemas import DocumentInput, QueryRequest
from rag_agent.service import RAGService


class SyntheticSemanticModel:
    mode='live'
    def __init__(self,decision,extra=None):
        self.decision=decision;self.extra=extra or {};self.calls=[]
    async def complete(self,purpose,payload,budget,timeout):
        budget.llm();self.calls.append((purpose,deepcopy(payload)))
        if purpose=='classify':return deepcopy(self.decision)
        if purpose not in self.extra:raise AssertionError('Unexpected synthetic model operation: '+purpose)
        value=self.extra[purpose]
        return value(payload) if callable(value) else deepcopy(value)


def decision(*,ids=(),products=(),needs=('安全机制与依据',),fresh=False):
    return {'intent':'security','entities':{'cve_ids':list(ids),'products':list(products)},
        'information_needs':list(needs),'freshness_required':fresh,'conversation_answer':None}


def make_service(tmp_path,llm):
    return RAGService(Settings(db_path=tmp_path/'synthetic-safety.db',embedding_provider='mock',
        llm_mode='extractive',max_llm_calls=8,max_tool_calls=10,max_retrieval_rounds=3,
        request_timeout=20,allow_unverified_concepts=True),llm=llm)


@pytest.mark.parametrize('history',[
    [{'role':'user','content':'比较 CVE-2099-1111 和 CVE-2099-2222 的风险'}],
    [{'role':'user','content':'查询 CVE-2099-1111'},
     {'role':'user','content':'换个话题聊晚餐吃什么'}],
])
async def test_ambiguous_or_topic_switched_history_cannot_silently_select_old_cve(tmp_path,history):
    llm=SyntheticSemanticModel(decision(ids=['CVE-2099-1111'],needs=['修复依据']))
    service=make_service(tmp_path,llm)
    external=[]
    async def executor(name,args,state):
        external.append((name,args));return []
    result=await service.query(QueryRequest(question='这个漏洞怎么修复？',history=history),external_executor=executor)
    assert result.question_type=='clarification'
    assert result.status=='insufficient_evidence' and not result.sources
    assert result.metrics.tool_calls==0 and external==[]
    assert [purpose for purpose,_ in llm.calls]==['classify']
    assert 'intent_classification_failed' not in result.warnings


async def test_product_vulnerability_and_upgrade_request_never_gets_unverified_fallback(tmp_path):
    llm=SyntheticSemanticModel(decision(products=['Ollama'],needs=['漏洞和修复建议']),
        {'general_explain':{'answer':'Ollama 0.1.2 存在命令执行问题，建议升级到 0.2.0。'}})
    service=make_service(tmp_path,llm)
    external=[]
    async def executor(name,args,state):
        external.append(name)
        if name=='web_search':raise BackendError('search_not_configured',status_code=503)
        return []
    result=await service.query(QueryRequest(question='Ollama 的工具有哪些漏洞和修复建议？'),external_executor=executor)
    assert external.count('web_search')==1
    assert not result.sources and not result.facts and not result.claims
    assert result.retrieval_mode_override is None and result.status=='insufficient_evidence'
    assert 'general_explain' not in [purpose for purpose,_ in llm.calls]
    assert '0.1.2' not in result.answer and '0.2.0' not in result.answer


@pytest.mark.parametrize('answer',[
    'Ollama 0.1.2 存在命令执行问题，建议升级到 0.2.0。',
    '建议从 0.1.2 升级为 0.2.0 可以解决这个问题。',
    '安装 0.2.0 或更高版本即可安全使用。',
])
def test_unverified_answer_rejects_specific_versions_and_upgrade_recommendations(answer):
    assert not safe_unverified_answer(answer)


async def test_fresh_dual_cve_empty_external_sources_still_attempt_web_once(tmp_path):
    ids=['CVE-2099-1111','CVE-2099-2222']
    llm=SyntheticSemanticModel(decision(ids=ids,needs=['当前漏洞说明和风险'],fresh=True))
    service=make_service(tmp_path,llm)
    actual=[]
    async def executor(name,args,state):
        actual.append((name,deepcopy(args)));return []
    result=await service.query(QueryRequest(question='现在比较 CVE-2099-1111 和 CVE-2099-2222 的风险'),external_executor=executor)
    assert [name for name,_ in actual].count('web_search')==1
    assert any(name=='nvd_lookup' for name,_ in actual)
    assert not result.sources and result.status=='insufficient_evidence'
    trace=service.get_run_summary(result.trace_id)['tools']
    web=[row for row in trace if row['tool']=='web_search']
    assert len(web)==1 and web[0]['status'] in {'completed','empty'}
    assert result.metrics.tool_calls<=10 and result.retrieval_summary.rounds<=3
    assert 'general_explain' not in [purpose for purpose,_ in llm.calls]


async def test_empty_local_search_imports_synthetic_original_and_answers_with_citation(tmp_path):
    def assess(payload):
        return {'selections':[{'evidence_id':row['evidence_id'],'relevance':'direct',
            'topic_ids':['permissions'],'reason':'合成来源明确描述工具权限边界。'} for row in payload['evidence']]}
    def synthesize(payload):
        return {'claims':[{'text':'工具应只获得任务所需权限，避免越权读取其他用户文件。',
            'evidence_ids':[payload['evidence'][0]['evidence_id']],'kind':'fact','topic_ids':['permissions']}]}
    llm=SyntheticSemanticModel(decision(needs=['工具权限与访问边界']),
        {'assess':assess,'synthesize':synthesize,'verify':{'supported_claim_indexes':[0]}})
    service=make_service(tmp_path,llm)
    assert not service.store.snapshot().documents
    calls=[]
    async def executor(name,args,state):
        calls.append(name)
        if name!='web_search':return []
        doc=DocumentInput(external_id='synthetic-web-permissions',source_name='Synthetic web source',
            source_kind='web',source_url='https://example.org/synthetic-web-permissions',doc_type='blog',
            title='Synthetic AI agent permission research',
            text='工具应只获得任务所需权限，避免越权读取其他用户文件。 Synthetic fictional fixture; not a real web search result.',
            collected_at=datetime.now(timezone.utc),raw_payload={'content_completeness':'summary',
                'metadata':{'content_origin':'synthetic_provider_snippet','independent_fetch':False}})
        ingested=await service.ingest([doc])
        assert ingested.inserted==1 and ingested.failed==0
        snapshot=await asyncio.to_thread(service.store.snapshot)
        state['snapshot']=snapshot
        return [snapshot.evidence[row['chunk_id']] for row in snapshot.chunks
                if row['document_id'] in ingested.document_ids]
    result=await service.query(QueryRequest(question='AI Agent 工具权限有哪些安全风险？'),external_executor=executor)
    assert calls.count('web_search')==1
    assert result.status in {'ok','partial'} and result.sources and result.claims
    assert result.retrieval_mode_override is None
    assert '工具应只获得任务所需权限' in result.answer
    assert '模型通用解释' not in result.answer and 'general_explain' not in [purpose for purpose,_ in llm.calls]
    snapshot=service.store.snapshot()
    for item in result.sources:
        assert item.document_id in snapshot.documents
        assert item.excerpt in snapshot.documents[item.document_id].text
    tools=service.get_run_summary(result.trace_id)['tools']
    assert next(row for row in tools if row['tool']=='hybrid_search')['status']=='empty'
    assert next(row for row in tools if row['tool']=='web_search')['status']=='completed'


def test_public_unverified_answer_keeps_notice_and_applies_budget_with_missing_requirements():
    from rag_agent.agent.semantic import UNVERIFIED_NOTICE
    from rag_agent.integration.api import adapt_answer
    from rag_agent.schemas import QueryResponse,RequirementCoverage
    text=UNVERIFIED_NOTICE+'\n'+('输入与工具权限之间需要保持可验证的边界。'*50)
    result=QueryResponse(status='partial',answer=text,trace_id='synthetic-unverified-budget',mode='live',
        question_type='general_explanation',retrieval_mode_override='llm_unverified',limitations=[UNVERIFIED_NOTICE],
        requirement_coverage=[RequirementCoverage(requirement_id='permissions',label='工具权限',
            status='missing',evidence_ids=[],reason='当前尚无经过核验的来源证据。')])
    public=adapt_answer(result,{}, {'tools':[],'nodes':[]},max_answer_tokens=64)
    assert len(public.answer.encode('utf-8'))<=64*4
    assert UNVERIFIED_NOTICE in public.answer and UNVERIFIED_NOTICE in public.limitations
    assert 'answer_budget_truncated' in public.partial_errors
    assert public.requirement_coverage[0].status=='missing' and public.requirement_coverage[0].evidence_ids==[]
    assert not public.coverage.answered_topics and not public.sources and not public.evidence and not public.cve_ids
