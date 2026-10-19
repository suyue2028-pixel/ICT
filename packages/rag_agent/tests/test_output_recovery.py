"""Synthetic structured-output and private HTTP error regression tests."""
from types import SimpleNamespace
import time
from uuid import uuid4
from datetime import datetime,timezone

from fastapi.testclient import TestClient
import pytest
from rag_agent.agent.nodes import Nodes
from rag_agent.agent.state import Budget
from rag_agent.config import Settings
from rag_agent.integration.api import create_app
from rag_agent.providers.llm import TypedProviderError
from rag_agent.schemas import DocumentInput, EvidenceDetail


class ShapeLLM:
    mode='live'
    def __init__(self):self.calls=[]
    async def complete(self,purpose,payload,budget,timeout):
        budget.llm()
        self.calls.append(payload)
        claim={'text':'Synthetic supported mechanism','evidence_ids':['e1'],'kind':'fact','topic_ids':[]}
        if len(self.calls)==1:
            return {'claims':[claim], 'sensitive_unexpected_field_DO_NOT_LOG':'private synthetic value'}
        return {'claims':[claim]}


async def test_one_schema_retry_reserves_verifier_and_hides_invalid_input():
    doc=DocumentInput(source_name='synthetic',source_kind='standard',source_url='https://fixture.example',
        title='Synthetic mechanism',doc_type='standard',text='Synthetic supported mechanism',collected_at=datetime(2099,1,1,tzinfo=timezone.utc))
    ev=EvidenceDetail(evidence_id='e1',document_id='d1',version=1,title=doc.title,url=doc.source_url,
        excerpt=doc.text,locator={'kind':'char_range','start':0,'end':len(doc.text)},collected_at=doc.collected_at)
    data={'resolved_question':'Explain the synthetic mechanism','identifiers':[],
        'snapshot':SimpleNamespace(documents={'d1':doc},evidence={'e1':ev}),
        'evidence':[ev],'assertions':[],'requirements':[],'missing_fields':[],'conflicts':[],
        'warnings':[],'trace':[],'clarification':'','started':time.monotonic(),'budget':Budget(time.monotonic()+10,max_llm=3)}
    llm=ShapeLLM()
    await Nodes(Settings(embedding_provider='mock'),None,llm).synthesize(data)
    assert data['output_mode']=='live' and len(data['draft'].claims)==1
    assert data['budget'].llm_calls==2 and data['budget'].max_llm-data['budget'].llm_calls==1
    assert llm.calls[1]['validation_feedback']==[{'path':'unknown_key','type':'extra_forbidden'}]
    assert 'sensitive_unexpected' not in str(data['trace']) and 'private synthetic value' not in str(data['trace'])


async def test_schema_retry_does_not_consume_reserved_verifier_budget():
    doc=DocumentInput(source_name='synthetic',source_kind='standard',source_url='https://fixture.example',
        title='Synthetic',doc_type='standard',text='Synthetic evidence',collected_at=datetime(2099,1,1,tzinfo=timezone.utc))
    ev=EvidenceDetail(evidence_id='e1',document_id='d1',version=1,title=doc.title,url=doc.source_url,
        excerpt=doc.text,locator={'kind':'char_range','start':0,'end':len(doc.text)},collected_at=doc.collected_at)
    data={'resolved_question':'Synthetic mechanism','identifiers':[],
        'snapshot':SimpleNamespace(documents={'d1':doc},evidence={'e1':ev}),
        'evidence':[ev],'assertions':[],'requirements':[],'missing_fields':[],'conflicts':[],
        'warnings':[],'trace':[],'clarification':'','started':time.monotonic(),'budget':Budget(time.monotonic()+10,max_llm=2)}
    llm=ShapeLLM()
    await Nodes(Settings(embedding_provider='mock'),None,llm).synthesize(data)
    assert len(llm.calls)==1 and data['output_mode']=='extractive'
    assert data['budget'].llm_calls==1


@pytest.mark.parametrize('code,status',[
    ('platform_model_auth_failed',502),('platform_model_timeout',504),('platform_model_rate_limited',503)])
@pytest.mark.parametrize('question',['你好','CVE-2099-1234 是什么'])
def test_real_http_adapter_classifies_mock_provider_faults(service,code,status,question):
    class FaultLLM:
        mode='live'
        async def complete(self,purpose,payload,budget,timeout):
            budget.llm()
            raise TypedProviderError(code,attempts=1)
    class Index:
        def mappings(self,is_demo):
            snapshot=service.store.snapshot()
            return {(did,version):{} for did,version in snapshot.versions.items()}
    llm=FaultLLM()
    service.llm=llm;service.nodes.llm=llm
    app=create_app(service,Index(),'synthetic-service')
    body={'schema_version':'1','query':question,'session_id':str(uuid4()),'is_demo':False,
        'request_id':'synthetic-request','invocation_id':str(uuid4()),'history':[],
        'limits':{'max_tool_calls':10,'max_answer_tokens':1200},'state_owner':'backend'}
    with TestClient(app) as client:
        result=client.post('/v1/query',json=body,headers={'Authorization':'Bearer synthetic-service'})
    assert result.status_code==status and result.json()['detail']==code
    assert result.status_code!=401 and 'user_token' not in str(result.json())
