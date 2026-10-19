"""Coverage boundaries; all records in these tests are synthetic."""
from types import SimpleNamespace
from datetime import datetime, timezone
from rag_agent.agent.workflow import requirements, evaluate
from rag_agent.schemas import Assertion, Claim, DocumentInput, Identifier, EvidenceDetail
from rag_agent.agent.nodes import Nodes, conflicts
from rag_agent.agent.state import Budget
from rag_agent.config import Settings
import time

FIRST='CVE-2099-12345'
SECOND='CVE-2099-67890'
NOW=datetime(2099,1,1,tzinfo=timezone.utc)

def fixture(question, ids):
    doc=DocumentInput(source_name='synthetic',source_kind='nvd',source_url='https://fixture.example',
        title=FIRST,text='Synthetic original description',doc_type='vulnerability',
        collected_at=NOW,identifiers=[Identifier(namespace='CVE',value=FIRST)])
    ev=EvidenceDetail(evidence_id='e1',document_id='d1',version=1,title=FIRST,url=doc.source_url,
        excerpt=doc.text,collected_at=NOW,locator={'kind':'char_range','start':0,'end':len(doc.text)})
    return {'resolved_question':question,'identifiers':ids,'evidence':[ev],
        'snapshot':SimpleNamespace(documents={'d1':doc}),'requirements':requirements(question,ids),
        'assertions':[],'conflicts':[],'external_checked':{},'external_checks':{},'limitations':[],
        'relevance_assignments':{},'fresh_required':False}

def test_each_cve_needs_its_own_source():
    state=fixture(f'比较 {FIRST} 和 {SECOND} 是什么漏洞',[FIRST,SECOND])
    claims=[Claim(text='来源《synthetic》的原文摘录',evidence_ids=['e1'])]
    result=evaluate(state,final=True,claims=claims,sources=state['evidence'])
    assert len(result)==2
    assert result[0].status=='supported'
    assert result[1].status=='missing' and not result[1].evidence_ids

def test_reference_url_does_not_prove_a_fix():
    state=fixture(FIRST+' 如何修复',[FIRST])
    state['assertions']=[Assertion(document_id='d1',version=1,entity_id=FIRST,field='mitigation',
        value={'references':[{'url':'https://fixture.example/fix','tags':['Patch']}]},evidence_ids=['e1'],
        origin='source_structured',verification='source_recorded',recorded_at=NOW)]
    result=evaluate(state)
    assert result[0].status=='partial'
    assert '参考链接' in result[0].reason

def test_all_eight_cves_keep_all_five_requirements_and_bounded_ids():
    ids=[f'CVE-2099-{10000+i}' for i in range(8)]
    rows=requirements('是什么漏洞、CVSS、受影响版本、在野利用及修复',ids)
    assert len(rows)==40 and len({r.id for r in rows})==40
    assert all(len(r.id)<=40 and r.entity in ids for r in rows)

def test_latest_general_knowledge_cannot_claim_complete_freshness():
    state=fixture('最新提示注入风险',[])
    state['fresh_required']=True
    state['relevance_assignments']={'e1':SimpleNamespace(topic_ids=['concept'])}
    result=evaluate(state,final=True,verified=True,
        claims=[Claim(text='Synthetic mechanism',evidence_ids=['e1'],topic_ids=['concept'])],sources=state['evidence'])
    assert result[0].status=='partial' and '最新' in result[0].reason

def assertion(field,value):
    return Assertion(document_id='d1',version=1,entity_id=FIRST,field=field,value=value,
        evidence_ids=['e1'],origin='source_structured',verification='source_recorded',recorded_at=NOW)

def test_same_rating_different_assessor_is_not_a_conflicting_value():
    a=assertion('cvss',{'version':'3.1','score':10.0,'vector':'SYNTHETIC','severity':'CRITICAL','source':'one','type':'Primary'})
    b=assertion('cvss',{**a.value,'source':'two','type':'Secondary'})
    assert not conflicts([a,b])
    b=b.model_copy(update={'value':{**b.value,'score':9.0}})
    assert conflicts([a,b])

def test_same_kev_entry_and_partial_republication_are_not_conflicting():
    entry={'dateAdded':'2099-01-01','requiredAction':'Synthetic action','knownRansomwareCampaignUse':'Unknown'}
    a=assertion('exploitation',{'catalog':'CISA KEV','entry':entry})
    b=assertion('exploitation',{'catalog':'CISA KEV','added_at':entry['dateAdded'],'required_action':entry['requiredAction']})
    assert not conflicts([a,b])
    b=b.model_copy(update={'value':{**b.value,'added_at':'2099-02-01'}})
    assert conflicts([a,b])

async def test_recovered_field_clears_earlier_not_found_message():
    state=fixture(FIRST+' CVSS评分',[FIRST])
    state.update(missing_fields=['identifier:'+FIRST,FIRST+':cvss'],warnings=[],trace=[],
        started=time.monotonic(),budget=Budget(time.monotonic()+10),retrieval_round=2)
    state['assertions']=[assertion('cvss',{'version':'3.1','score':7.5,'source':'synthetic'})]
    nodes=Nodes(Settings(embedding_provider='mock'),None,None)
    await nodes.assess_evidence(state)
    assert state['missing_fields']==[]
