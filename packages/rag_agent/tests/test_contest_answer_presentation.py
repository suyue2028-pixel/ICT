"""Contest-oriented output contracts; synthetic facts, no vendor or search calls."""
from types import SimpleNamespace
from datetime import datetime,timezone
import time
import pytest
from rag_agent.schemas import Assertion, Claim
from rag_agent.agent.presentation import (answer_requirements, render_assertion,
    render_assertion_lines, render_claim_lines, render_boundary_summary)
from rag_agent.agent.state import Budget
from rag_agent.schemas import Plan,Filters
from rag_agent.agent.workflow import semantic_subqueries


@pytest.mark.parametrize('q,ids,level,count,relation',[
    ('什么是提示注入？',[],'explanatory',6,False),
    ('智能体工具审计有什么风险，如何防护？',[],'explanatory',6,False),
    ('请详细说明提示注入如何影响工具调用。',[],'detailed',8,False),
    ('一句话说明数据投毒。',[],'concise',1,False),
    ('简要说明这个漏洞的影响。',[],'concise',2,False),
    ('比较两个漏洞的影响及修复依据。',['CVE-2099-1234','CVE-2099-5678'],'detailed',8,True),
    ('结合多份资料说明攻击链及成立条件。',[],'detailed',8,True),
    ('分别介绍这两个漏洞。',['CVE-2099-1234','CVE-2099-5678'],'detailed',8,False),
])
def test_output_profile_matches_intent_without_forcing_relation(q,ids,level,count,relation):
    profile=answer_requirements(q,ids)['output_profile']
    assert profile['detail_level']==level and profile['max_claims']==count
    assert profile['explain_relation'] is relation and profile['show_source_links'] is True
    assert profile['max_claim_chars']=={'detailed':320,'explanatory':220,'concise':160}[level]


def test_source_relation_is_a_question_specific_requirement():
    r=answer_requirements('说明攻击链，注明资料关联依据和成立条件。',[])
    assert 'relation' in r['facets'] and '关联依据' in r['output_profile']['sections']
    assert any('关系两端' in point for point in r['required_points'])
    assert '关联依据' not in answer_requirements('如何缓解提示注入？',[])['output_profile']['sections']


def fact(eid,*,score=9.8,source='synthetic-A',entity='CVE-2099-1234'):
    return Assertion(document_id='synthetic-doc',version=1,entity_id=entity,field='cvss',
        value={'version':'3.1','score':score,'source':source},evidence_ids=[eid],
        origin='source_structured',verification='source_recorded',recorded_at=datetime.now(timezone.utc))


def test_identical_claim_display_merges_but_keeps_citations_and_kind():
    claims=[Claim(text='同一个合成结论。',evidence_ids=['a'],kind='fact'),
        Claim(text='同一个合成结论。',evidence_ids=['b'],kind='fact'),
        Claim(text='同一个合成结论。',evidence_ids=['c'],kind='inference')]
    lines=render_claim_lines(claims)
    assert lines==['同一个合成结论。 [a,b]','【推断】同一个合成结论。 [c]']
    assert claims[0].evidence_ids==['a'] and claims[1].evidence_ids==['b']


def test_only_exact_fact_duplicates_merge_distinct_assessors_scores_and_targets_stay():
    assertions=[fact('a'),fact('b'),fact('c',score=8.1),fact('d',source='synthetic-B'),
        fact('e',entity='CVE-2099-5678')]
    lines=render_assertion_lines(assertions,[])
    assert len(lines)==4 and lines[0].endswith('[a,b]')
    assert any('8.1' in line for line in lines) and any('synthetic-B' in line for line in lines)
    assert any('CVE-2099-5678' in line for line in lines)
    assert len(assertions)==5


def test_fact_already_displayed_verbatim_with_its_quote_is_not_appended_twice():
    assertion=fact('a');claim=Claim(text=render_assertion(assertion),evidence_ids=['a'],kind='fact')
    assert render_assertion_lines([assertion],[claim])==[]
    wrong_scope=claim.model_copy(update={'evidence_ids':['other']})
    assert render_assertion_lines([assertion],[wrong_scope])


def test_fact_overflow_is_disclosed_and_original_records_retained():
    facts=[fact(str(i),source=f'synthetic-{i}') for i in range(8)]
    lines=render_assertion_lines(facts,[],limit=6)
    assert len(lines)==7 and '原文证据' in lines[-1] and len(facts)==8


def test_reference_only_note_does_not_undo_a_separately_recorded_fixed_version():
    base=fact('a')
    link=base.model_copy(update={'field':'mitigation','value':{'references':[
        {'url':'https://example.invalid/synthetic-patch'}]}})
    fixed=base.model_copy(update={'field':'fixed_version','value':'1.2.3'})
    lines=render_assertion_lines([link,fixed],[])
    assert '参考链接正文尚未核查' in lines[0]
    assert '具体修复版本尚需' not in lines[0]
    assert '来源记录的修复版本：1.2.3' in lines[1]


def test_one_short_boundary_notice_retains_full_structured_reasons():
    rows=[SimpleNamespace(label=f'需求{i}',status='missing') for i in range(5)]
    limits=['synthetic long reason '+str(i) for i in range(5)]
    before=list(limits);notice=render_boundary_summary(rows,limits,[])
    assert notice.count('【资料边界】')==1 and len(notice)<80
    assert '需求0' in notice and '其他限制' in notice and limits==before
    assert render_boundary_summary([],[],[])==''


async def test_relation_expansion_seeds_only_current_deduplicated_evidence(service,monkeypatch):
    snapshot=service.store.snapshot();first=next(e for e in snapshot.evidence.values() if e.locator.get('kind')=='char_range')
    duplicate=first.model_copy(update={'evidence_id':first.evidence_id+'-synthetic-duplicate'})
    tools=service.nodes.tools;original=tools.call;seen=[]
    async def spy(name,args,state):
        if name=='hybrid_search':return [first,duplicate]
        if name=='related_evidence':seen.extend(args['evidence_ids'])
        return await original(name,args,state)
    monkeypatch.setattr(tools,'call',spy)
    state={'snapshot':snapshot,'budget':Budget(time.monotonic()+10),'trace':[],'started':time.monotonic(),
        'plan':Plan(route='compare',subqueries=['合成资料关联'],expand_relations=True),'evidence':[],
        'identifiers':[],'subqueries':['合成资料关联'],'attempted_tools':set(),'limitations':[],
        'explicit_filters':Filters(),'warnings':[],'clarification':''}
    result=await service.nodes.execute_tools(state)
    assert seen==[first.evidence_id]
    assert duplicate.evidence_id not in {e.evidence_id for e in result['evidence']}


def test_relation_queries_reuse_two_needs_and_keep_full_question_without_new_cve_or_url():
    question='比较合成对象A和对象B的风险'
    assert semantic_subqueries(question,[' 对象A的机制 ','对象A的机制','对象B的前提','其他需求'])==[
        '对象A的机制','对象B的前提',question]
    assert semantic_subqueries(question,['CVE-2099-1234','https://example.invalid/fake',''])==[question]


async def test_classified_relation_plan_queries_both_sides_without_another_llm_call(service):
    state={'resolved_question':'比较合成对象A与对象B的关联条件','identifiers':[],
        'classification':SimpleNamespace(information_needs=['对象A的机制','对象B的前提'],freshness_required=False),
        'clarification':'','explicit_filters':Filters(),'trace':[],
        'budget':Budget(time.monotonic()+10),'started':time.monotonic()}
    result=await service.nodes.plan(state)
    assert result['subqueries']==['对象A的机制','对象B的前提',state['resolved_question']]
    assert result['budget'].llm_calls==0 and result['plan'].expand_relations
