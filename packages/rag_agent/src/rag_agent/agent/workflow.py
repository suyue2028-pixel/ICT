"""Bounded plans and per-requirement evidence checks over actual records.

Rule checks own entities, source identities, dates and structured facts. LLM
relevance/entailment can contribute meaning, never create a source or a tool.
"""
from __future__ import annotations
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
import re
import hashlib
from typing import Literal
from pydantic import Field
from ..schemas import Model, RequirementCoverage, RetrievalSummary
from .coverage import requested_topics

EXTERNAL = {'nvd_lookup','kev_lookup','osv_lookup','knowledge_lookup','web_search'}
REGISTERED = {'exact_lookup','hybrid_search','related_evidence','get_assertions'} | EXTERNAL


class Requirement(Model):
    id: str = Field(pattern=r'^[a-z][a-z0-9_]{0,39}$')
    label: str = Field(min_length=1,max_length=120)
    field: Literal['overview','cvss','affected_version','mitigation','exploitation','concept'] = 'concept'
    entity: str | None = None


def requirements(question, ids):
    result=[]
    if ids:
        patterns = [
            ('overview','漏洞说明','overview',r'是什么|什么漏洞|介绍|描述|what|describe'),
            ('cvss','严重程度与来源评分','cvss',r'严重|评分|CVSS|优先级|severity|score|priority'),
            ('affected_version','受影响产品与版本','affected_version',r'影响|产品|版本|affected|version|product'),
            ('exploitation','已知在野利用情况','exploitation',r'利用|在野|KEV|exploit'),
            ('mitigation','修复或缓解依据','mitigation',r'修复|补丁|缓解|防护|fixed|patch|mitigat|remediat'),
        ]
        for key,label,field,pattern in patterns:
            if re.search(pattern,question,re.I): result.append(Requirement(id=key,label=label,field=field))
        if not result: result=[Requirement(id='overview',label='漏洞说明',field='overview')]
        # Each target owns its requirements. A document about one CVE cannot
        # satisfy a comparison covering a different CVE.
        if len(ids)>1:
            result=[Requirement(id=f'{item.id}_{target.lower().replace("-","_")[:14]}_{hashlib.sha256(target.encode()).hexdigest()[:6]}',
                label=f'{target} · {item.label}'[:120],field=item.field,entity=target)
                for target in ids for item in result]
        else:
            result=[item.model_copy(update={'entity':ids[0]}) for item in result]
    else:
        result=[Requirement(id=t['id'],label=t['label'],field='concept') for t in requested_topics(question)]
        if not result:
            result=[Requirement(id='concept',label='问题中的安全机制与解释',field='concept')]
            if re.search(r'防范|防护|如何防|缓解|mitigat|protect',question,re.I):
                result.append(Requirement(id='mitigation',label='有来源支持的防护措施',field='concept'))
        if package_entities(question):
            result.append(Requirement(id='package_applicability',label='指定软件包版本的适用性',field='affected_version'))
    return result[:40]


def freshness_required(question):
    return bool(re.search(r'最新|现在|目前|已知.*利用|在野|KEV|latest|current|exploit',question,re.I))


def package_entities(question):
    # Only explicit ecosystem + package + version, no model-invented substitutes.
    match=re.search(r'\b(PyPI|npm|Go|Maven|crates\.io)\s*[:：]?\s*([A-Za-z0-9@/_.-]+)\s+(?:v|version\s*)?([0-9][A-Za-z0-9_.+~-]*)',question,re.I)
    if not match: return {}
    ecosystem=next(x for x in ('PyPI','npm','Go','Maven','crates.io') if x.casefold()==match[1].casefold())
    return {'ecosystem':ecosystem,'package':match[2],'version':match[3]}


def knowledge_topics(question, missing_ids):
    result=[]
    if re.search(r'投毒|poison',question,re.I): result.append('poisoning')
    if re.search(r'工具|权限|agent|智能体|agency',question,re.I): result.append('agency')
    if re.search(r'提示.*注入|prompt injection',question,re.I) or 'prompt_injection' in missing_ids:
        result.append('injection')
    return list(dict.fromkeys(result))[:2]


def deduplicate(evidence, snapshot, limit=8):
    """Same canonical source + same excerpt is one item, not independent proof."""
    result=[]; seen=set()
    for item in evidence:
        doc=snapshot.documents.get(item.document_id)
        canonical=(doc.source_url if doc else item.url).rstrip('/')
        key=(canonical,' '.join(item.excerpt.split()))
        if key in seen: continue
        seen.add(key);result.append(item)
        if len(result)>=limit:break
    return result


def semantic_subqueries(question, information_needs):
    """Reuse classified needs for bounded local retrieval, never new entities.

    The full question remains a fallback. Classifier text cannot introduce a
    CVE identifier or a URL into this natural-language retrieval plan.
    """
    queries=[]
    for need in information_needs:
        query=need.strip()
        if not query or re.search(r'CVE[-－]|https?://',query,re.I):
            continue
        if query not in queries and query!=question:
            queries.append(query)
        if len(queries)==2:
            break
    return [*queries,question]


def evaluate(state, *, final=False, verified=False, claims=None, sources=None):
    """No confidence percentages. Candidate coverage cannot claim final support."""
    rows=[]
    evidence=sources if sources is not None else state.get('evidence',[])
    by_id={e.evidence_id:e for e in evidence}
    assignments=state.get('relevance_assignments',{})
    facts=state.get('assertions',[])
    wanted=state.get('requirements',requirements(state['resolved_question'],state['identifiers']))
    cited={i for c in (claims or []) for i in c.evidence_ids}
    for req in wanted:
        matched=[];status='missing';reason='当前没有通过对应需求核验的来源证据。'
        target_ids={req.entity} if req.entity else set(state['identifiers'])
        if req.field=='overview':
            for e in evidence:
                doc=state['snapshot'].documents.get(e.document_id)
                if doc and target_ids.intersection(i.value.upper() for i in doc.identifiers): matched.append(e.evidence_id)
            if matched:
                status='partial';reason='找到对应漏洞的原始描述；生成结论仍需支持核验。'
                if not final:
                    status='supported';reason='找到对应编号的原始描述；最终结论仍单独核验。'
                    if state.get('fresh_required') and not state.get('external_checks',{}).get(('nvd_lookup',req.entity)):
                        status='partial';reason='已保存描述尚未完成本次时效核查。'
                if final and verified and cited.intersection(matched):status='supported';reason='结论与对应漏洞来源通过核验。'
                elif final and cited.intersection(matched) and all(c.text.startswith('来源《') for c in (claims or [])):
                    status='supported';reason='对应编号的原始描述直接引用；未宣称模型生成或额外事实。'
        elif req.field!='concept':
            fields={'mitigation':{'fixed_version','mitigation'}}.get(req.field,{req.field})
            relevantfacts=[a for a in facts if a.field in fields and (not req.entity or a.entity_id.upper()==req.entity)]
            validfacts=[a for a in relevantfacts if set(a.evidence_ids).issubset(by_id)]
            # Before synthesis, field evidence lives in the trusted snapshot.
            if not final:
                docs={e.document_id for e in evidence}
                validfacts=[a for a in relevantfacts if a.document_id in docs]
            matched=list(dict.fromkeys(i for a in validfacts for i in a.evidence_ids))
            if matched:
                status='supported';reason='来源存在对应结构化字段；不推导未解析的产品适用性。'
                if req.field=='affected_version' and any(isinstance(a.value,dict) and (a.value.get('parsed') is False or a.value.get('items')) for a in validfacts):
                    status='partial';reason='来源记录了版本条件，完整布尔适用性或版本范围尚未求解。'
                if req.field=='mitigation' and all(a.field=='mitigation' and isinstance(a.value,dict)
                        and set(a.value).issubset({'references','reference_tags','type'}) for a in validfacts):
                    status='partial';reason='来源只有修复参考链接，尚未核查链接中的实际措施或适用版本。'
                if any(c.get('field') in fields and (not req.entity or c.get('entity_id',req.entity)==req.entity)
                        for c in state.get('conflicts',[])):
                    status='conflicting';reason='同一事实上下文存在不同来源值，差异已保留，尚未裁决。'
                if req.field=='exploitation':
                    checked=state.get('external_checks',{}).get(('kev_lookup',req.entity))
                    if not checked and len(state['identifiers'])==1:
                        checked=state.get('external_checked',{}).get('kev_lookup')
                    if state.get('fresh_required') and not checked and status!='conflicting':
                        status='partial';reason='存在利用信息，但本次未完成最新目录核查。'
                elif state.get('fresh_required') and req.entity and status!='conflicting' and not state.get('external_checks',{}).get(('nvd_lookup',req.entity)):
                    status='partial';reason='当前字段来自已保存的资料，本次未完成对应编号的最新 NVD 核查。'
        else:
            matched=[e.evidence_id for e in evidence if req.id in getattr(assignments.get(e.evidence_id),'topic_ids',[])]
            if matched:
                status='partial';reason='有相关资料；尚不足以证明最终结论覆盖此需求。'
                if not final:
                    status='supported';reason='相关性核查确认资料包含该需求；最终生成结论仍需独立核验。'
                if final and verified and any(req.id in c.topic_ids and set(c.evidence_ids).intersection(matched) for c in (claims or [])):
                    status='supported';reason='对应结论有实际来源引用并通过支持核验。'
                if state.get('fresh_required'):
                    status='partial';reason='资料支持一般机制说明；该问题要求最新情况，固定知识来源不构成完整最新情报核查。'
        if final: matched=[i for i in matched if i in by_id]
        if status in {'supported','partial','conflicting'} and not matched:
            status='missing';reason='支持该需求的引用未进入最终可核验结果。'
        if req.field=='exploitation' and not matched and 'kev_no_entry_is_not_proof_of_no_exploitation' in state.get('limitations',[]):
            reason='本次 KEV 查询无条目；不能据此断言没有在野利用。'
        rows.append(RequirementCoverage(requirement_id=req.id,label=req.label,status=status,
            evidence_ids=list(dict.fromkeys(matched))[:50],reason=reason))
    return rows


def next_tools(state, registered):
    missing={r.requirement_id for r in state.get('requirement_coverage',[]) if r.status!='supported'}
    attempted=state.get('attempted_tools',set())
    result=[]
    ids=state['identifiers']
    if ids:
        for identifier in ids:
            fields={req.field for req in state.get('requirements',[]) if req.id in missing and req.entity in {None,identifier}}
            if ('exploitation' in fields or state.get('fresh_required')) and 'kev_lookup' in registered:
                result.append(('kev_lookup',{'identifier':identifier}))
            if (fields-{'exploitation'} or not state.get('evidence') or state.get('fresh_required')) and 'nvd_lookup' in registered:
                result.append(('nvd_lookup',{'identifier':identifier}))
    else:
        package=package_entities(state['resolved_question'])
        if package and 'osv_lookup' in registered:
            result.append(('osv_lookup',package))
        for topic in knowledge_topics(state['resolved_question'],missing):
            if 'knowledge_lookup' in registered:result.append(('knowledge_lookup',{'topic':topic}))
    if (missing or not state.get('evidence') or state.get('fresh_required')) and 'web_search' in registered:
        # One bounded query per request; missing labels narrow the actual search.
        # Daily questions never enter this workflow.
        if not any(name=='web_search' for name,_ in attempted):
            labels=[row.label for row in state.get('requirement_coverage',[]) if row.status!='supported']
            query=(state['resolved_question']+' '+' '.join(labels))[:2000]
            call=('web_search',{'query':query})
            if not ids or (state.get('retrieval_round',1)>=2 and not state.get('evidence')):
                result.insert(0,call)
            else:
                result.append(call)
    available=[(name,args) for name,args in result if (name,tuple(sorted(args.items()))) not in attempted]
    # Semantic planning can prioritize eligible tools. Entity rules and source
    # registry still decide eligibility; a model cannot add a URL or a tool.
    preferred=getattr(state.get('plan'),'tools',[])
    available.sort(key=lambda call: preferred.index(call[0]) if call[0] in preferred else len(preferred))
    return available[:2]
