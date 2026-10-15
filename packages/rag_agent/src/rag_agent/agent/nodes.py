from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import re
import time
from pydantic import ValidationError
from zoneinfo import ZoneInfo
from ..ingestion.normalize import identifiers
from ..ingestion.extract import field_evidence
from ..schemas import Claim, Draft, Plan, QueryResponse
from .state import BudgetExceeded
from .conversation import conversation_kind
from .coverage import requested_topics, parse_relevance, validate_claim_topics, final_coverage
from .presentation import (answer_requirements, assertion_payload, render_assertion,
                           render_claim, render_missing_fields, render_claim_lines,
                           render_assertion_lines, render_boundary_summary, length_revision_feedback)
from .workflow import requirements, freshness_required, evaluate, next_tools, deduplicate, REGISTERED, package_entities, semantic_subqueries
from ..schemas import RetrievalSummary
from ..providers.llm import TypedProviderError

TRANSLATIONS = {"提示注入": "prompt injection", "提示词注入": "prompt injection", "数据投毒": "data poisoning", "供应链": "supply chain", "敏感信息": "sensitive information"}
OUTPUT_REJECTION_REASONS = {
    "model invented or modified a structured fact": "structured_fact_modified",
    "citation outside retrieved evidence set": "citation_outside_scope",
    "model-generated URLs are forbidden": "model_generated_url",
    "unsupported identifier in claim": "identifier_without_source_quote",
    "unsupported numeric/version fact": "number_without_source_quote",
    "hard fact without recorded assertion": "hard_fact_without_assertion",
    "unresolved version range in claim": "version_range_not_evaluated",
    "claim topic outside assessed evidence scope": "topic_without_assessed_support",
}


def model_evidence(state, evidence):
    """Annotate existing evidence with its source boundaries, without new facts."""
    result = []
    for item in evidence:
        row = item.model_dump(mode="json")
        doc = state["snapshot"].documents.get(item.document_id)
        if doc:
            row.update(source_name=doc.source_name, source_kind=doc.source_kind,
                       document_type=doc.doc_type,
                       content_completeness=(doc.raw_payload or {}).get("content_completeness", "unknown"),
                       source_last_modified=doc.source_modified_at.isoformat() if doc.source_modified_at else None)
        result.append(row)
    return result


def is_followup(question):
    return bool(re.search(r"它|这个漏洞|该漏洞|这个版本|该版本|上述漏洞|这个和上一个", question)
                or re.fullmatch(r"(?:那|那么|请问)?\s*(?:怎么修复|如何修复|怎么缓解|影响哪些版本|受影响版本有哪些|还有什么风险)[？?。\s]*", question))


def resolve_history(question, history, context, documents):
    """Only user-authored targets resolve references; a new topic stops the scan."""
    if not is_followup(question) or identifiers(question):
        return question, [], None, ""
    users = [m.content for m in history if m.role == "user" and not conversation_kind(m.content)]
    if "上一个" in question:
        targets = []
        for text in reversed(users):
            ids = identifiers(text)
            if len(ids) == 1 and ids[0] not in targets:
                targets.append(ids[0])
            if len(targets) == 2:
                return "比较 " + " 和 ".join(reversed(targets)) + "：" + question, targets, None, ""
        return question, [], None, "无法唯一确定本次比较的两个对象，请提供两个编号或名称。"
    prior = next((text for text in reversed(users) if not is_followup(text) or identifiers(text)), "")
    ids = list(dict.fromkeys(identifiers(prior) if prior else context.get("identifiers", [])))
    if len(ids) == 1:
        return ids[0] + " " + question, ids, None, ""
    if len(ids) > 1:
        return question, [], None, "无法唯一确定“它/这个版本”指向的对象，请提供漏洞编号或产品名。"
    products = list({p for d in documents.values() for p in d.product_entities
                     if len(p) > 2 and p.casefold() in prior.casefold()}) if prior else context.get("products", [])
    if len(products) == 1:
        return products[0] + " " + question, [], products[0], ""
    if prior and not products and not re.search(r"漏洞|版本|修复", question):
        return prior + "；追问：" + question, [], None, ""
    return question, [], None, "无法唯一确定“它/这个版本”指向的对象，请提供漏洞编号或产品名。"


def conflicts(assertions):
    grouped = defaultdict(list)
    for a in assertions:
        # Distinct products/ranges must not be collapsed into one version space.
        discriminator = a.value.get("version", "") if a.field == "cvss" and isinstance(a.value, dict) else ""
        grouped[(a.entity_id, a.field, discriminator)].append(a)
    result = []
    for (entity, field, version), values in grouped.items():
        compared=[]
        for a in values:
            value=a.value
            if field=='cvss' and isinstance(value,dict):
                # A different assessor/type with the same score and vector is
                # not a contradictory rating. Preserve both original records.
                value={k:value.get(k) for k in ('version','score','vector','severity')}
            if field=='exploitation' and isinstance(value,dict):
                entry=value.get('entry',value)
                if isinstance(entry,dict):
                    value={'catalog':value.get('catalog')}
                    for canonical,aliases in {'added_at':('dateAdded','added_at'),
                        'required_action':('requiredAction','required_action'),
                        'ransomware':('knownRansomwareCampaignUse','ransomware')}.items():
                        for alias in aliases:
                            if alias in entry:
                                value[canonical]=entry[alias]
                                break
            compared.append(value)
        if field=='exploitation' and all(isinstance(v,dict) for v in compared):
            different=any(any(a[k]!=b[k] for k in a.keys() & b.keys())
                for i,a in enumerate(compared) for b in compared[i+1:])
        else:
            different=len({json.dumps(v,sort_keys=True,ensure_ascii=False) for v in compared})>1
        if different:
            result.append({"entity_id": entity, "field": field, "cvss_version": version or None,
                           "assertion_ids": [a.assertion_id for a in values],
                           "values": [a.value for a in values]})
    return result


def validate_draft(draft, evidence, assertions):
    allowed = {e.evidence_id: e for e in evidence}
    known = {a.assertion_id: a for a in assertions}
    for a in draft.facts:
        if a.assertion_id not in known or a.model_dump() != known[a.assertion_id].model_dump():
            raise ValueError("model invented or modified a structured fact")
    for claim in draft.claims:
        if not set(claim.evidence_ids).issubset(allowed):
            raise ValueError("citation outside retrieved evidence set")
        if re.search(r"https?://", claim.text):
            raise ValueError("model-generated URLs are forbidden")
        cited = " ".join(allowed[i].title + " " + allowed[i].excerpt for i in claim.evidence_ids)
        if not set(identifiers(claim.text)).issubset(identifiers(cited)):
            raise ValueError("unsupported identifier in claim")
        # Conservative hard-fact guard; semantic entailment still needs human evaluation.
        for number in re.findall(r"\d+(?:\.\d+)+", claim.text):
            if number not in cited:
                raise ValueError("unsupported numeric/version fact")
        if re.search(r"(?:修复版本|fixed\s+in|CVSS\s*[:：评分])", claim.text, re.I) and not re.search(r"未知|未提供|缺少|无法|unknown", claim.text, re.I):
            fields = {a.field for a in assertions if set(a.evidence_ids).intersection(claim.evidence_ids)}
            required = "cvss" if "cvss" in claim.text.casefold() else "fixed_version"
            if required not in fields:
                raise ValueError("hard fact without recorded assertion")
        cited_docs = {allowed[i].document_id for i in claim.evidence_ids}
        cited_assertions = [a for a in assertions if a.document_id in cited_docs]
        raw_ranges = any(
            a.field == "affected_version" and isinstance(a.value, dict)
            and any(isinstance(item, dict) and item.get("ranges")
                    for item in (a.value.get("items") if isinstance(a.value.get("items"), list) else []))
            for a in cited_assertions
        )
        comparison = (r"低于|小于|早于|之前|以前|以下|或更高|及以上|所有.*版本|"
                      r"受影响版本(?:范围)?(?:是|为)|[<>]=?\s*v?\d|"
                      r"\d[\d.]*\s*(?:版本)?\s*(?:以上|以后)|"
                      r"\b(?:before|below|earlier|older)\b|or\s+(?:later|newer|higher)")
        if raw_ranges and re.search(comparison, claim.text, re.I):
            raise ValueError("unresolved version range in claim")


class Nodes:
    def __init__(self, settings, tools, llm):
        self.settings, self.tools, self.llm = settings, tools, llm

    def event(self, state, name):
        state["budget"].remaining()
        state["trace"].append({"node": name, "elapsed_ms": round((time.monotonic() - state["started"]) * 1000, 2)})

    async def validate(self, state):
        self.event(state, "validate")
        request = state["request"]
        question = request.question.strip()
        filters = request.filters.model_copy(deep=True)
        # Identifier filters constrain targets; they must not introduce substitutes.
        ids = list(dict.fromkeys(identifiers(question) or filters.identifier))
        if not ids and state.get('classified_identifiers'):
            ids=state['classified_identifiers']
            question=' '.join(ids)+' '+question
        clarification = ""
        if not ids and is_followup(question):
            question, ids, product, clarification = resolve_history(question, request.history, request.context, state["snapshot"].documents)
            if product:
                if filters.product and filters.product.casefold() != product.casefold():
                    clarification = "历史指代与显式产品过滤冲突，请指定本次查询对象。"
                else:
                    filters.product = product
        if len(ids) > 8:
            clarification = "一次请求最多查询8个编号，请拆分请求。"
        if not ids and re.search(r'CVE[-－]',question,re.I):
            clarification='请提供标准 CVE 编号，例如 CVE-年份-至少四位数字；当前编号格式不能用于精确查询。'
        if not ids and re.search(r'\b(?:PyPI|npm|Maven|crates\.io)\b',question,re.I) and not package_entities(question):
            clarification='查询开源依赖漏洞需要明确的软件包生态、包名和版本，例如 PyPI langchain 0.0.200。'
        if re.search(r"最近|近\s*\d+\s*天", question):
            if "更新" in question:
                clarification = "当前日期过滤按发布时间计算；请明确发布时间范围，更新时间筛选需后端扩展契约。"
            else:
                match = re.search(r"近\s*(\d+)\s*天", question)
                days = min(int(match.group(1)), 3650) if match else 30
                now = request.now or datetime.now(timezone.utc)
                local = now.astimezone(ZoneInfo(request.timezone))
                lower = (local - timedelta(days=days)).astimezone(timezone.utc)
                filters.published_from = max(lower, filters.published_from) if filters.published_from else lower
                state["warnings"].append(f"最近按发布时间近{days}天解释；时区={request.timezone}")
        state.update(resolved_question=question, explicit_filters=filters, identifiers=ids, clarification=clarification)
        return state

    async def plan(self, state):
        self.event(state, "plan")
        question = state["resolved_question"]
        state['requirements'] = requirements(question, state['identifiers'])
        classified=state.get('classification')
        if classified and not state['identifiers'] and not requested_topics(question) and not package_entities(question):
            from .workflow import Requirement
            needs=classified.information_needs
            if needs:
                state['requirements']=[Requirement(id=f'semantic_need_{i}',label=label,field='concept')
                    for i,label in enumerate(needs)]
        state['fresh_required'] = freshness_required(question) or bool(classified and classified.freshness_required)
        state['retrieval_round'] = 0
        state['attempted_tools'] = set()
        state['pending_tools'] = []
        state['external_checked'] = {}
        state['external_checks'] = {}
        state['limitations'] = []
        state['stop_reason'] = 'initial_retrieval'
        if state["clarification"]:
            plan = Plan(route="hybrid", subqueries=[question])
        elif state["identifiers"]:
            plan = Plan(route="exact", subqueries=[question], expand_relations="关联" in question or len(state["identifiers"]) > 1)
        else:
            compare = bool(re.search(r"比较|对比|关联|compare", question, re.I))
            plan = Plan(route="compare" if compare else "hybrid", subqueries=[question], expand_relations=compare)
            if compare and classified:
                plan.subqueries=semantic_subqueries(question,classified.information_needs)
            # In live mode, reserve the three calls for relevance, generation,
            # and support verification. Routing here is deterministic.
            ambiguous = not re.search(r'安全|漏洞|风险|注入|投毒|CVE|agent|RAG|security|risk|vulnerab', question, re.I) and len(question)>8
            if not classified and self.llm and (compare or ambiguous) and (self.llm.mode != "live" or state['budget'].max_llm-state['budget'].llm_calls >= 5):
                try:
                    candidate = await self.llm.complete("plan", {"question": question,
                        "registered_tools": sorted(self.tools.registered(state)),
                        "hard_filters": state["explicit_filters"].model_dump(mode="json")}, state["budget"], self.settings.llm_timeout)
                    plan = Plan.model_validate(candidate)
                    if plan.route == "exact":
                        raise ValueError("model cannot invent target identifiers")
                    if not set(plan.tools).issubset(self.tools.registered(state)):
                        raise ValueError('model requested unregistered tool')
                    if any(identifiers(q) or 'http://' in q or 'https://' in q for q in plan.subqueries):
                        raise ValueError('planner cannot invent entities or URLs')
                except BudgetExceeded:
                    raise
                except (ValueError, RuntimeError, KeyError) as exc:
                    state["warnings"].append(f"planning_fallback:{type(exc).__name__}")
        plan.question_type = 'clarification' if state['clarification'] else 'cve' if state['identifiers'] else 'package' if package_entities(question) else 'comparison' if plan.route=='compare' else 'security_knowledge'
        plan.fresh_required = state['fresh_required']
        plan.tools = list(dict.fromkeys([('exact_lookup' if state['identifiers'] else 'hybrid_search'), *plan.tools]))
        state.update(plan=plan, subqueries=plan.subqueries, question_type=plan.question_type)
        state['trace'].append({'node':'retrieval_plan','question_type':plan.question_type,
            'entities':state['identifiers'], 'requirements':[r.model_dump() for r in state['requirements']],
            'fresh_required':state['fresh_required'], 'registered_tools':sorted(self.tools.registered(state)),
            'tools':plan.tools,'preferred_sources':plan.preferred_sources})
        return state

    async def execute_tools(self, state):
        self.event(state, "execute_tools")
        if state["clarification"]:
            state['stop_reason']='clarification_required'
            return state
        state['retrieval_round'] = state.get('retrieval_round',0)+1
        previous = list(state['evidence'])
        state['prior_evidence_fingerprint'] = {(e.url, e.excerpt) for e in previous}
        pending = state.pop('pending_tools',[])
        evidence = []
        if pending:
            for name,args in pending:
                state['attempted_tools'].add((name,tuple(sorted(args.items()))))
                try:
                    evidence.extend(await self.tools.call(name,args,state))
                except BudgetExceeded:
                    raise
                except Exception as exc:
                    state['limitations'].append(name+':'+getattr(exc,'code','intelligence_source_unavailable'))
            evidence = deduplicate(evidence+previous,state['snapshot'])
        elif state["identifiers"]:
            evidence, missing = await self.tools.call("exact_lookup", {"identifiers": state["identifiers"]}, state)
            state["missing_fields"] = ["identifier:" + i for i in missing]
        else:
            groups = []
            for q in state["subqueries"]:
                groups.append(await self.tools.call("hybrid_search", {"query": q}, state))
            seen = set()
            while any(groups) and len(evidence) < 8:
                for group in groups:
                    if group:
                        ev = group.pop(0)
                        if ev.evidence_id not in seen:
                            evidence=deduplicate([*evidence,ev],state['snapshot'])
                            seen.add(ev.evidence_id)
                        if len(evidence) == 8:
                            break
        state["evidence"] = deduplicate(evidence+previous,state['snapshot'])
        if evidence and state["plan"].expand_relations:
            selected=state['evidence']
            related = await self.tools.call("related_evidence", {"evidence_ids": [e.evidence_id for e in selected]}, state)
            # Make room for a second document when possible; never displace exact target coverage.
            if related and len(selected) >= 8 and not state["identifiers"]:
                selected = selected[:6]
            state['evidence']=deduplicate(selected+related,state['snapshot'])
            if related:
                state["warnings"].append("关联仅沿共同编号/明确产品关系扩展一跳，不代表已确认攻击链")
        if state["evidence"]:
            state["assertions"] = await self.tools.call("get_assertions", {"document_ids": list(dict.fromkeys(e.document_id for e in state["evidence"]))}, state)
        return state

    async def assess_evidence(self, state):
        self.event(state, "assess_evidence")
        question = state["resolved_question"]
        topics = [{'id':r.id,'label':r.label} for r in state.get('requirements',[]) if r.field=='concept']
        state["requested_topics"] = topics
        state["relevance_assignments"] = {}
        # Recompute missing fields for the current evidence set. A recovered
        # source must clear the earlier "not found" message.
        documents={e.document_id:state['snapshot'].documents[e.document_id] for e in state['evidence']}
        state['missing_fields']=['identifier:'+target for target in state['identifiers']
            if not any(target in {i.value.upper() for i in doc.identifiers} for doc in documents.values())]
        # Relevance is optional when it would consume generation/verification
        # budget. The final verifier also checks relevance and support.
        can_assess = state["budget"].max_llm - state["budget"].llm_calls >= 3
        if state["evidence"] and not state["identifiers"] and self.llm and self.llm.mode == "live" and can_assess:
            before = len(state["evidence"])
            selected = []
            try:
                result = await self.llm.complete("assess", {"question": question,
                    "answer_requirements": {**answer_requirements(question, state["identifiers"]), 'coverage_topics':topics},
                    "evidence": model_evidence(state, state["evidence"])}, state["budget"], self.settings.llm_timeout)
                selections, retained = parse_relevance(result, state["evidence"], topics)
                selected = [e for e in state["evidence"] if e.evidence_id in retained]
                state["relevance_assignments"] = retained
                state["trace"].append({"node": "evidence_scope", "selections": [
                    row.model_dump(mode="json") for row in selections]})
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError) as exc:
                state["warnings"].append("relevance_check_failed")
                state["llm_error"] = "relevance_check_failed"
                if isinstance(exc,TypedProviderError): state['warnings'].append(exc.code)
            state["evidence"] = selected
            docs = {e.document_id for e in selected}
            state["assertions"] = [a for a in state["assertions"] if a.document_id in docs]
            state["warnings"].append(f"relevance_filter:candidates={before};selected={len(selected)}")
            state["trace"].append({"node": "relevance_filter", "candidate_count": before, "selected_count": len(selected)})
            for event in state["budget"].events:
                if event["tool"] == "hybrid_search":
                    # Aggregate rerank counts describe the merged candidate set,
                    # rather than inventing a per-retriever relevance score.
                    event["input_summary"] = (event.get("input_summary", "") +
                        f"; assessed_total={before}; relevance_selected_count={len(selected)}")[:500]
        elif state["evidence"] and not state["identifiers"]:
            state["warnings"].append("relevance_check_unavailable")
        state["conflicts"] = conflicts(state["assertions"])
        if state["evidence"]:
            requested = set(answer_requirements(question, state["identifiers"])["requested_fields"])
            required = requested.intersection({"cvss", "affected_version"})
            if re.search(r"修复版本|补丁版本|fixed.*version|fixed\s+in", question, re.I):
                required.add("fixed_version")
            for field in sorted(required):
                for entity in state['identifiers']:
                    if not any(a.entity_id.upper()==entity and a.field==field for a in state['assertions']):
                        state['missing_fields'].append(f'{entity}:{field}')
                if field == "affected_version" and any(a.field == field and isinstance(a.value, dict) and a.value.get("parsed") is False for a in state["assertions"]):
                    state["missing_fields"].append("affected_applicability:unparsed_CPE_boolean_conditions")
            if re.search(r"比较|对比|compare", question, re.I) and len({e.document_id for e in state["evidence"]}) < 2:
                state["missing_fields"].append("comparison:second_document")
            if "mitigation" in requested:
                for entity in state['identifiers']:
                    if not any(a.entity_id.upper()==entity and a.field in {'fixed_version','mitigation'} for a in state['assertions']):
                        state['missing_fields'].append(f'{entity}:remediation')
        state["missing_fields"] = list(dict.fromkeys(state["missing_fields"]))
        state['requirement_coverage'] = evaluate(state)
        state['trace'].append({'node':'requirement_evaluation','round':state['retrieval_round'],
            'requirements':[row.model_dump() for row in state['requirement_coverage']]})
        return state

    async def choose_recovery(self, state):
        self.event(state, 'choose_recovery')
        state['next_action'] = self.after_assess(state)
        return state

    def after_assess(self, state):
        if state['clarification'] or state.get('llm_error'):
            state['stop_reason']='clarification_required' if state['clarification'] else 'model_failed'
            return 'synthesize'
        package=package_entities(state['resolved_question'])
        needs_package_lookup=bool(package and 'osv_lookup' in self.tools.registered(state)
            and ('osv_lookup',tuple(sorted(package.items()))) not in state.get('attempted_tools',set()))
        if state.get('requirement_coverage') and all(r.status=='supported' for r in state['requirement_coverage']) and not needs_package_lookup:
            state['stop_reason']='requirements_supported'
            return 'synthesize'
        if state.get('retrieval_round',0)>=getattr(self.settings,'max_retrieval_rounds',3):
            state['stop_reason']='max_rounds'
            return 'synthesize'
        if state.get('external_executor') and state['budget'].max_tools-state['budget'].tool_calls<2:
            state['stop_reason']='tool_budget_reserved'
            return 'synthesize'
        # A new registered candidate still deserves its bounded attempt.
        pending=next_tools(state,self.tools.registered(state))
        if state.get('retrieval_round',0)>1 and not pending and {(e.url,e.excerpt) for e in state['evidence']} <= state.get('prior_evidence_fingerprint',set()):
            state['stop_reason']='no_new_evidence'
            return 'synthesize'
        if state['budget'].remaining()<4:
            state['stop_reason']='deadline_reserved'
            return 'synthesize'
        if pending:
            if (not state['identifiers'] and self.llm and self.llm.mode=='live'
                    and state['budget'].max_llm-state['budget'].llm_calls<3):
                state['stop_reason']='model_budget_reserved'
                return 'synthesize'
            state['pending_tools']=pending
            return 'rewrite'
        if (state.get('evidence') and not state['identifiers'] and state['retry_count']<self.settings.max_retries
                and self.llm and self.llm.mode=='live' and state['budget'].max_llm-state['budget'].llm_calls>=3
                and any(r.status!='supported' for r in state['requirement_coverage'])):
            state['stop_reason']='expand_local_query'
            return 'rewrite'
        if (not state["clarification"] and not state["evidence"] and not state["identifiers"]
                and state["retry_count"] < self.settings.max_retries
                and not any(w.startswith("relevance_filter:") or w == "relevance_check_failed" for w in state["warnings"])):
            return "rewrite"
        state['stop_reason']='available_tools_exhausted'
        return "synthesize"

    async def rewrite(self, state):
        self.event(state, "rewrite")
        state["retry_count"] += 1
        state["budget"].retries += 1
        if state.get('pending_tools'):
            state['trace'].append({'node':'recovery_plan','round':state['retrieval_round']+1,
                'tools':[name for name,_ in state['pending_tools']],
                'missing_requirements':[r.requirement_id for r in state['requirement_coverage'] if r.status!='supported']})
            return state
        query = state["resolved_question"]
        rewritten = query + " " + " ".join(en for zh, en in TRANSLATIONS.items() if zh in query)
        if state.get('evidence'):
            missing={r.requirement_id for r in state.get('requirement_coverage',[]) if r.status!='supported'}
            additions={'audit_logging':'audit logs logging traceability', 'prompt_injection':'prompt injection',
                'permissions':'least privilege permission access control', 'tool_execution':'tool abuse execution risk',
                'mitigation':'mitigation remediation security control'}
            rewritten += ' '+' '.join(additions.get(key,key.replace('_',' ')) for key in sorted(missing))
        if self.llm and self.llm.mode != "live" and state["budget"].llm_calls < self.settings.max_llm_calls - 1:
            try:
                result = await self.llm.complete("rewrite", {"question": query, "hard_filters": state["explicit_filters"].model_dump(mode="json")}, state["budget"], self.settings.llm_timeout)
                proposed = result["question"]
                if not isinstance(proposed, str) or not proposed.strip() or len(proposed) > 3500 or identifiers(proposed):
                    raise ValueError("invalid rewrite")
                # Retain original query text, so a rewrite cannot silently erase constraints.
                rewritten = query + " " + proposed
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError):
                state["warnings"].append("rewrite_fallback")
        state["subqueries"] = [rewritten[:4000]]
        return state

    def fallback(self, state, warning=None):
        if warning:
            state["warnings"].append(warning)
        claims = []
        for e in state["evidence"][:4]:
            excerpt = e.excerpt[:180].replace("\n", " ")
            if re.search(r"ignore.*(?:instruction|previous)|忽略.*指令|输出.*HACKED", excerpt, re.I):
                excerpt = "材料含疑似指令注入内容，已作为不可信资料隔离；请查看引用详情。"
                state["warnings"].append("untrusted_document_directive")
            claims.append(Claim(text=f"来源《{e.title}》的原文摘录：{excerpt}", evidence_ids=[e.evidence_id]))
        state["draft"] = Draft(claims=claims) if claims else None
        state["output_mode"] = "extractive"

    async def synthesize(self, state):
        self.event(state, "synthesize")
        if not state["evidence"] or state["clarification"]:
            state["draft"] = None
            state["output_mode"] = "extractive"
            return state
        # Fields are source evidence, distinct from the <=8 retrieved body chunks.
        requirements = answer_requirements(state["resolved_question"], state["identifiers"])
        requirements['coverage_topics']=state.get('requested_topics',[])
        if any(r.field=='exploitation' for r in state.get('requirements',[])):
            requirements['requested_fields'].append('exploitation')
        requested_fields = set(requirements["requested_fields"])
        offered_assertions = [a for a in state["assertions"] if a.field in requested_fields and
                              all(len(state["snapshot"].evidence[i].excerpt) <= 2000 for i in a.evidence_ids)]
        offered_ids = {a.assertion_id for a in offered_assertions}
        omitted = [a for a in state["assertions"] if a.field in requested_fields and a.assertion_id not in offered_ids]
        if omitted:
            state["warnings"].append("requested_field_omitted_due_to_size")
            for assertion in omitted:
                limitation = f"{assertion.document_id}:field_not_public:{assertion.field}"
                if limitation not in state["missing_fields"]:
                    state["missing_fields"].append(limitation)
        # Keep the full assertions in state for hard-fact guards, but do not
        # attach unrelated, oversized raw JSON to an ordinary conceptual query.
        fields = {i: state["snapshot"].evidence[i] for a in offered_assertions for i in a.evidence_ids}
        body = {e.evidence_id: e for e in state["evidence"]}
        # Source aliases may be absent from prose. Quote the original revision
        # value, never the internally normalized Identifier objects. The V1
        # adapter independently checks this quote against the bound revision.
        for item in state["evidence"]:
            doc = state["snapshot"].documents[item.document_id]
            revision = (doc.raw_payload or {}).get("backend_revision", {})
            if isinstance(revision, dict):
                for key in ("aliases", "vulnerability_id", "external_id"):
                    value = revision.get(key)
                    if key == "aliases":
                        valid = isinstance(value, list) and value and all(isinstance(i, str) for i in value)
                    else:
                        valid = isinstance(value, str) and bool(identifiers(value))
                    if valid:
                        ev = field_evidence(doc, item.document_id, item.version,
                                            f"/raw_payload/backend_revision/{key}", value)
                        if len(ev.excerpt) <= 2000:
                            fields[ev.evidence_id] = ev
        state["selected_evidence"] = list({**body, **fields}.values())
        if self.llm:
            try:
                payload = {
                    "question": state["resolved_question"], "evidence": model_evidence(state, state["selected_evidence"]),
                    "answer_requirements": requirements,
                    "retrieval_context": {'fresh_required':state.get('fresh_required',False),
                        'checks':[{'tool':name,'identifier':identifier,**checked}
                            for (name,identifier),checked in state.get('external_checks',{}).items()]},
                    "evidence_scope": {eid: row.model_dump(mode="json") for eid, row in state.get("relevance_assignments", {}).items()},
                    "stored_assertions": assertion_payload(offered_assertions),
                    "missing_fields": render_missing_fields(state["missing_fields"]),
                    "conflicts": [{"field": c["field"], "entity_id": c["entity_id"],
                                   "assertion_ids": [i for i in c["assertion_ids"] if i in offered_ids]}
                                  for c in state["conflicts"] if any(i in offered_ids for i in c["assertion_ids"])],
                }
                for attempt in range(2):
                    result = await self.llm.complete('synthesize',payload,state['budget'],self.settings.llm_timeout)
                    try:
                        state['draft']=Draft.model_validate(result)
                        if attempt == 0 and self.llm.mode == 'live':
                            supported = []
                            for claim in state['draft'].claims:
                                try:
                                    validate_draft(Draft(claims=[claim]), state['selected_evidence'], state['assertions'])
                                    validate_claim_topics(claim, state.get('relevance_assignments', {}),
                                                          state.get('requested_topics', []))
                                except ValueError:
                                    continue
                                supported.append(claim)
                            feedback = length_revision_feedback(supported, state['selected_evidence'],
                                                                requirements['output_profile'], state['budget'])
                            if feedback:
                                state['warnings'].append('answer_length_revision')
                                state['trace'].append({'node': 'answer_length_revision',
                                                       'body_chars': feedback['body_chars']})
                                payload = {**payload, 'length_feedback': feedback,
                                    'supported_draft': [claim.model_dump(mode='json') for claim in supported]}
                                continue
                        break
                    except ValidationError as exc:
                        # Record only schema paths/types, never model text,
                        # unknown field names, Pydantic input or context.
                        allowed={'claims','facts','text','evidence_ids','kind','topic_ids',
                            'assertion_id','document_id','version','entity_id','field','value',
                            'origin','verification','recorded_at'}
                        feedback=[{'path':'.'.join(str(part) if isinstance(part,int) or part in allowed
                            else 'unknown_key' for part in err['loc']), 'type':err['type']}
                            for err in exc.errors(include_input=False,include_context=False)[:8]]
                        state['trace'].append({'node':'generation_schema_error','errors':feedback})
                        if (attempt==0 and state['budget'].max_llm-state['budget'].llm_calls>=2
                                and state['budget'].remaining()>=4):
                            state['warnings'].append('model_schema_retry')
                            payload={**payload,'validation_feedback':feedback,
                                'output_contract':{'root_keys':['claims'],'max_claims':8,
                                    'claim_keys':['text','evidence_ids','kind','topic_ids'],
                                    'ids':'existing string IDs only','kind':['fact','inference'],
                                    'facts':'omit; never copy display assertions'}}
                            continue
                        raise
                state["output_mode"] = self.llm.mode
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError) as exc:
                if isinstance(exc,TypedProviderError): state['warnings'].append(exc.code)
                categories = {"provider output truncated": "model_output_truncated",
                              "provider returned invalid JSON output": "model_output_invalid_json",
                              "provider returned empty output": "model_output_empty",
                              "provider transport unavailable": "model_transport_unavailable"}
                # Provider messages here are fixed local adapter strings. Never
                # persist an arbitrary exception/provider body as diagnostics.
                category = categories.get(str(exc), "model_output_or_transport_failed")
                state["warnings"].append(category)
                state["trace"].append({"node": "generation_error", "category": category})
                self.fallback(state, "model_generation_failed")
        else:
            self.fallback(state)
        return state

    async def validate_output(self, state):
        self.event(state, "validate_output")
        draft = state.get("draft")
        selected = state.get("selected_evidence", state["evidence"])
        topics = state.get("requested_topics", [])
        assignments = state.get("relevance_assignments", {})
        claims_verified = False
        if draft and state["output_mode"] != "extractive":
            try:
                # Validate immutable structured facts first. A rejected claim
                # should not discard unrelated, source-supported conclusions.
                validate_draft(draft.model_copy(update={"claims": []}), selected, state["assertions"])
                retained, rejected = [], []
                for claim in draft.claims:
                    try:
                        validate_draft(Draft(claims=[claim]), selected, state["assertions"])
                        validate_claim_topics(claim, assignments, topics)
                    except ValueError as exc:
                        rejected.append(OUTPUT_REJECTION_REASONS.get(str(exc), "invalid_output"))
                    else:
                        retained.append(claim)
                if rejected:
                    state["warnings"].append("output_claims_rejected:" + ",".join(sorted(set(rejected))))
                if draft.claims and not retained:
                    self.fallback(state, "output_rejected:全部生成结论未通过事实校验")
                    draft = state["draft"]
                else:
                    draft = draft.model_copy(update={"claims": retained})
            except ValueError as exc:
                reason = OUTPUT_REJECTION_REASONS.get(str(exc), "invalid_output")
                self.fallback(state, f"output_rejected:{reason}:无效引用或结构化硬事实已拦截")
                draft = state["draft"]
        if draft and draft.claims and state["output_mode"] == "live":
            if state["budget"].llm_calls < state["budget"].max_llm:
                try:
                    verify_requirements=answer_requirements(state['resolved_question'],state['identifiers'])
                    verify_requirements['coverage_topics']=topics
                    payload = {
                        "question": state["resolved_question"],
                        "answer_requirements": verify_requirements,
                        "evidence_scope": {eid: row.model_dump(mode='json') for eid,row in assignments.items()},
                        "claims": [c.model_dump(mode="json") for c in draft.claims],
                        "evidence": model_evidence(state, selected),
                    }
                    for attempt in range(2):
                        result = await self.llm.complete('verify',payload,state['budget'],self.settings.llm_timeout)
                        indexes = result.get('supported_claim_indexes')
                        if (set(result)=={'supported_claim_indexes'} and isinstance(indexes,list)
                                and all(type(i) is int and 0<=i<len(draft.claims) for i in indexes)):
                            break
                        if attempt==0 and state['budget'].llm_calls<state['budget'].max_llm and state['budget'].remaining()>=3:
                            state['warnings'].append('verification_schema_retry')
                            state['trace'].append({'node':'verification_schema_error','error_type':'invalid_claim_indexes'})
                            payload={**payload,'output_contract':{'root_keys':['supported_claim_indexes'],
                                'index_type':'zero-based integers only','valid_indexes':list(range(len(draft.claims))),
                                'no_supported_claims':[]}}
                            continue
                        raise ValueError('invalid support result')
                    retained = [c for i, c in enumerate(draft.claims) if i in indexes]
                    if len(retained) < len(draft.claims):
                        state["warnings"].append("unsupported_claims_removed")
                    draft = draft.model_copy(update={"claims": retained})
                    claims_verified = True
                except BudgetExceeded:
                    raise
                except (ValueError, RuntimeError, KeyError) as exc:
                    state["warnings"].append("claim_support_check_failed")
                    state["llm_error"] = "claim_support_check_failed"
                    if isinstance(exc,TypedProviderError): state['warnings'].append(exc.code)
                    draft = None
            else:
                state["warnings"].append("claim_support_check_unavailable:call_budget")
                self.fallback(state)
                draft = state["draft"]
        if not draft or not draft.claims:
            target='、'.join(state['identifiers'][:3]) if state['identifiers'] else '该问题'
            answer=state['clarification'] or f'现有资料尚不足以回答{target}。请补充对应的原始资料，或明确需要核查的范围。'
            status = "error" if state.get("llm_error") else "insufficient_evidence"
            if state.get("llm_error"):
                answer = "模型服务未能完成证据相关性判断或答案核验，请稍后重试。此次失败不代表知识库没有相关资料。"
            claims, sources = [], []
            coverage = final_coverage(topics, [], assignments, verified=False)
            if status!='error' and not state['clarification']:
                boundary=render_boundary_summary(state.get('requirement_coverage',[]),[],state['missing_fields'])
                if boundary:answer+='\n'+boundary
        else:
            claims = draft.claims
            coverage = final_coverage(topics, claims, assignments, verified=claims_verified and state["output_mode"] == "live")
            available = {e.evidence_id for e in selected}
            state["assertions"] = [a for a in state["assertions"] if set(a.evidence_ids).issubset(available)]
            if state["output_mode"] == "live":
                cited_ids = {i for claim in claims for i in claim.evidence_ids}
                cited_docs = {e.document_id for e in selected if e.evidence_id in cited_ids}
                state["assertions"] = [a for a in state["assertions"] if a.document_id in cited_docs]
            lines = render_claim_lines(claims)
            requested = set(answer_requirements(state["resolved_question"], state["identifiers"])["requested_fields"])
            rendered = [a for a in state["assertions"] if state["output_mode"] == "extractive" or a.field in requested]
            lines.extend(render_assertion_lines(rendered,claims))
            if re.search(r'攻击链|多跳|attack\s+chain|multi.hop',state['resolved_question'],re.I):
                state.setdefault('limitations',[]).append('当前关联分析基于所引资料及成立条件，尚未验证完整攻击链。')
            if state["conflicts"]:
                state.setdefault('limitations',[]).append('多个来源的同一字段存在差异，已分别保留，尚未裁决。')
            if coverage["missing_topics"]:
                state["warnings"].append("coverage_incomplete")
            if state["output_mode"] == "extractive":
                note = ("以下仅为检索候选摘录，尚未核实与本次问题的相关性，不能据此得出专业结论。"
                        if "relevance_check_unavailable" in state["warnings"] else
                        "以下为来源摘录与已记录字段，未获得通过核验的模型回答。")
                lines.insert(0, note)
            answer = "\n".join(lines)
            status = "partial" if coverage["missing_topics"] or state["missing_fields"] or state["conflicts"] or any("rejected" in w or "failed" in w or "unsupported" in w or "unavailable" in w for w in state["warnings"]) else "ok"
            # Structured facts remain machine-readable and require their evidence.
            cited = {i for c in claims for i in c.evidence_ids} | {i for a in state["assertions"] for i in a.evidence_ids}
            sources = [e for e in selected if e.evidence_id in cited]
        final_requirements=evaluate(state, final=True, verified=claims_verified, claims=claims,sources=sources)
        limitations=list(dict.fromkeys([*state.get('limitations',[]),
            *[row.label+'：'+row.reason for row in final_requirements if row.status!='supported']]))[:20]
        if sources and limitations:
            status='partial'
            answer+='\n'+render_boundary_summary(final_requirements,limitations,state['missing_fields'])
        events=state['budget'].events
        initial=[];supplemental=[]
        for event in events:
            target=supplemental if event.get('round',1)>1 else initial
            if event['tool'] not in target:target.append(event['tool'])
        state["response"] = QueryResponse(status=status, answer=answer, claims=claims, sources=sources,
                                          coverage=coverage,
                                          question_type=state.get('question_type'),requirement_coverage=final_requirements,
                                          retrieval_summary=RetrievalSummary(rounds=state.get('retrieval_round',0),stop_reason=state.get('stop_reason','done'),initial_tools=initial,supplemental_tools=supplemental),
                                          limitations=limitations,
                                          facts=state["assertions"] if sources else [], missing_fields=state["missing_fields"], conflicts=state["conflicts"],
                                          warnings=state["warnings"], trace_id=state["trace_id"], mode=state["output_mode"])
        return state
