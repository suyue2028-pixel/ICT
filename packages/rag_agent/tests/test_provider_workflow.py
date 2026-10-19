"""Provider-labelled HTTP doubles preserve the existing synthetic RAG index.

These are not real DeepSeek/Qwen calls; real DeepSeek is verified separately.
"""
import json
import httpx
import pytest
from rag_agent.config import Settings
from rag_agent.providers.llm import BackendLLM
from rag_agent.schemas import QueryRequest


@pytest.mark.parametrize('provider,model',[('deepseek','deepseek-flash'),('qwen','qwen-plus')])
async def test_backend_provider_switch_preserves_tools_and_source_identity(service,monkeypatch,provider,model):
    before=service.store.snapshot()
    requests=[]
    def response(request):
        payload=json.loads(request.content)
        requests.append(payload['purpose'])
        if payload['purpose']=='classify':
            result={'intent':'security','entities':{'cve_ids':['CVE-2099-1234'],'products':[]},
                'information_needs':['漏洞说明'],'freshness_required':False,'conversation_answer':None}
        elif payload['purpose']=='synthesize':
            eid=payload['payload']['evidence'][0]['evidence_id']
            result={'claims':[{'text':'来源提供该合成漏洞的描述。','evidence_ids':[eid],
                'kind':'fact','topic_ids':[]}]}
        elif payload['purpose']=='verify':
            result={'supported_claim_indexes':[0]}
        else:raise AssertionError('Unexpected model operation in exact-CVE test')
        return httpx.Response(200,json={'result':result,'attempts':1,'usage':{'total_tokens':10},
            'provider':provider,'model':model})
    client=httpx.AsyncClient
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kwargs:client(transport=httpx.MockTransport(response),**kwargs))
    llm=BackendLLM(Settings(llm_mode='live',llm_transport='backend',embedding_provider='mock',
        llm_gateway_url='http://127.0.0.1:8010',llm_service_token='synthetic-internal'))
    service.llm=llm;service.nodes.llm=llm
    answer=await service.query(QueryRequest(question='CVE-2099-1234 是什么漏洞'))
    after=service.store.snapshot()
    assert answer.mode=='live' and answer.sources
    assert requests==['classify','synthesize','verify']
    assert answer.metrics.llm_calls==3
    assert {r['tool'] for r in service.get_run_summary(answer.trace_id)['tools']}=={'exact_lookup','get_assertions'}
    assert before.signature==after.signature and before.versions==after.versions
    assert set(before.documents)==set(after.documents)
    assert all(e.url==after.documents[e.document_id].source_url for e in answer.sources)
    usage=service.get_run_summary(answer.trace_id)['llm_usage']
    assert [row['purpose'] for row in usage]==requests
    assert all(row['provider']==provider and row['model']==model for row in usage)
