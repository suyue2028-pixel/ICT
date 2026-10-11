from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import re
import time
from zoneinfo import ZoneInfo
from ..ingestion.normalize import identifiers
from ..ingestion.extract import field_evidence
from ..schemas import Claim, Draft, Plan, QueryResponse
from .state import BudgetExceeded
from .conversation import conversation_kind
from .coverage import requested_topics, parse_relevance, validate_claim_topics, final_coverage
from .presentation import (answer_requirements, assertion_payload, render_assertion,
                           render_claim, render_missing_fields)

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
        unique = {json.dumps(a.value, sort_keys=True, ensure_ascii=False) for a in values}
        if len(unique) > 1:
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
        if state["clarification"]:
            plan = Plan(route="hybrid", subqueries=[question])
        elif state["identifiers"]:
            plan = Plan(route="exact", subqueries=[question], expand_relations="关联" in question or len(state["identifiers"]) > 1)
        else:
            compare = bool(re.search(r"比较|对比|关联|compare", question, re.I))
            plan = Plan(route="compare" if compare else "hybrid", subqueries=[question], expand_relations=compare)
            # In live mode, reserve the three calls for relevance, generation,
            # and support verification. Routing here is deterministic.
            if self.llm and compare and self.llm.mode != "live":
                try:
                    candidate = await self.llm.complete("plan", {"question": question, "hard_filters": state["explicit_filters"].model_dump(mode="json")}, state["budget"], self.settings.llm_timeout)
                    plan = Plan.model_validate(candidate)
                    if plan.route == "exact":
                        raise ValueError("model cannot invent target identifiers")
                except BudgetExceeded:
                    raise
                except (ValueError, RuntimeError, KeyError) as exc:
                    state["warnings"].append(f"planning_fallback:{type(exc).__name__}")
        state.update(plan=plan, subqueries=plan.subqueries)
        return state

    async def execute_tools(self, state):
        self.event(state, "execute_tools")
        if state["clarification"]:
            return state
        evidence = []
        if state["identifiers"]:
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
                            evidence.append(ev)
                            seen.add(ev.evidence_id)
                        if len(evidence) == 8:
                            break
        state["evidence"] = evidence
        if evidence and state["plan"].expand_relations:
            related = await self.tools.call("related_evidence", {"evidence_ids": [e.evidence_id for e in evidence]}, state)
            # Make room for a second document when possible; never displace exact target coverage.
            if related and len(evidence) >= 8 and not state["identifiers"]:
                evidence = evidence[:6]
            unique = {e.evidence_id: e for e in evidence + related}
            state["evidence"] = list(unique.values())[:8]
            if related:
                state["warnings"].append("关联仅沿共同编号/明确产品关系扩展一跳，不代表已确认攻击链")
        if state["evidence"]:
            state["assertions"] = await self.tools.call("get_assertions", {"document_ids": list(dict.fromkeys(e.document_id for e in state["evidence"]))}, state)
        return state

    async def assess_evidence(self, state):
        self.event(state, "assess_evidence")
        question = state["resolved_question"]
        topics = requested_topics(question)
        state["requested_topics"] = topics
        state["relevance_assignments"] = {}
        # Relevance is optional when it would consume generation/verification
        # budget. The final verifier also checks relevance and support.
        can_assess = state["budget"].max_llm - state["budget"].llm_calls >= 3
        if state["evidence"] and not state["identifiers"] and self.llm and self.llm.mode == "live" and can_assess:
            before = len(state["evidence"])
            selected = []
            try:
                result = await self.llm.complete("assess", {"question": question,
                    "answer_requirements": answer_requirements(question, state["identifiers"]),
                    "evidence": model_evidence(state, state["evidence"])}, state["budget"], self.settings.llm_timeout)
                selections, retained = parse_relevance(result, state["evidence"], topics)
                selected = [e for e in state["evidence"] if e.evidence_id in retained]
                state["relevance_assignments"] = retained
                state["trace"].append({"node": "evidence_scope", "selections": [
                    row.model_dump(mode="json") for row in selections]})
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError):
                state["warnings"].append("relevance_check_failed")
                state["llm_error"] = "relevance_check_failed"
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
                for doc_id in dict.fromkeys(e.document_id for e in state["evidence"]):
                    if not any(a.document_id == doc_id and a.field == field for a in state["assertions"]):
                        state["missing_fields"].append(f"{doc_id}:{field}")
                if field == "affected_version" and any(a.field == field and isinstance(a.value, dict) and a.value.get("parsed") is False for a in state["assertions"]):
                    state["missing_fields"].append("affected_applicability:unparsed_CPE_boolean_conditions")
            if re.search(r"比较|对比|compare", question, re.I) and len({e.document_id for e in state["evidence"]}) < 2:
                state["missing_fields"].append("comparison:second_document")
            if "mitigation" in requested:
                for doc_id in dict.fromkeys(e.document_id for e in state["evidence"]):
                    if not any(a.document_id == doc_id and a.field in {"fixed_version", "mitigation"} for a in state["assertions"]):
                        state["missing_fields"].append(f"{doc_id}:remediation")
        state["missing_fields"] = list(dict.fromkeys(state["missing_fields"]))
        return state

    def after_assess(self, state):
        if (not state["clarification"] and not state["evidence"] and not state["identifiers"]
                and state["retry_count"] < self.settings.max_retries
                and not any(w.startswith("relevance_filter:") or w == "relevance_check_failed" for w in state["warnings"])):
            return "rewrite"
        return "synthesize"

    async def rewrite(self, state):
        self.event(state, "rewrite")
        state["retry_count"] += 1
        state["budget"].retries += 1
        query = state["resolved_question"]
        rewritten = query + " " + " ".join(en for zh, en in TRANSLATIONS.items() if zh in query)
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
                result = await self.llm.complete("synthesize", {
                    "question": state["resolved_question"], "evidence": model_evidence(state, state["selected_evidence"]),
                    "answer_requirements": requirements,
                    "evidence_scope": {eid: row.model_dump(mode="json") for eid, row in state.get("relevance_assignments", {}).items()},
                    "stored_assertions": assertion_payload(offered_assertions),
                    "missing_fields": render_missing_fields(state["missing_fields"]),
                    "conflicts": [{"field": c["field"], "entity_id": c["entity_id"],
                                   "assertion_ids": [i for i in c["assertion_ids"] if i in offered_ids]}
                                  for c in state["conflicts"] if any(i in offered_ids for i in c["assertion_ids"])],
                }, state["budget"], self.settings.llm_timeout)
                state["draft"] = Draft.model_validate(result)
                state["output_mode"] = self.llm.mode
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError) as exc:
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
                    result = await self.llm.complete("verify", {
                        "question": state["resolved_question"],
                        "answer_requirements": answer_requirements(state["resolved_question"], state["identifiers"]),
                        "claims": [c.model_dump(mode="json") for c in draft.claims],
                        "evidence": model_evidence(state, selected),
                    }, state["budget"], self.settings.llm_timeout)
                    indexes = result.get("supported_claim_indexes")
                    if not isinstance(indexes, list) or any(type(i) is not int or not 0 <= i < len(draft.claims) for i in indexes):
                        raise ValueError("invalid support result")
                    retained = [c for i, c in enumerate(draft.claims) if i in indexes]
                    if len(retained) < len(draft.claims):
                        state["warnings"].append("unsupported_claims_removed")
                    draft = draft.model_copy(update={"claims": retained})
                    claims_verified = True
                except BudgetExceeded:
                    raise
                except (ValueError, RuntimeError, KeyError):
                    state["warnings"].append("claim_support_check_failed")
                    state["llm_error"] = "claim_support_check_failed"
                    draft = None
            else:
                state["warnings"].append("claim_support_check_unavailable:call_budget")
                self.fallback(state)
                draft = state["draft"]
        if not draft or not draft.claims:
            searched = "已检索当前资料" + ("并尝试一次查询扩展" if state["retry_count"] else "")
            answer = state["clarification"] or f"{searched}，仍未找到能直接支持该问题的来源证据。可以提供具体产品或漏洞编号缩小范围，或补充相关规范与案例。"
            if state["missing_fields"]:
                answer += " 未确认事项：" + "；".join(render_missing_fields(state["missing_fields"]))
            status = "error" if state.get("llm_error") else "insufficient_evidence"
            if state.get("llm_error"):
                answer = "模型服务未能完成证据相关性判断或答案核验，请稍后重试。此次失败不代表知识库没有相关资料。"
            claims, sources = [], []
            coverage = final_coverage(topics, [], assignments, verified=False)
            if coverage["missing_topics"] and status != "error":
                answer += " 尚未核实的范围：" + "；".join(coverage["missing_topics"]) + "。"
        else:
            claims = draft.claims
            coverage = final_coverage(topics, claims, assignments, verified=claims_verified and state["output_mode"] == "live")
            available = {e.evidence_id for e in selected}
            state["assertions"] = [a for a in state["assertions"] if set(a.evidence_ids).issubset(available)]
            if state["output_mode"] == "live":
                cited_ids = {i for claim in claims for i in claim.evidence_ids}
                cited_docs = {e.document_id for e in selected if e.evidence_id in cited_ids}
                state["assertions"] = [a for a in state["assertions"] if a.document_id in cited_docs]
            lines = [f"{render_claim(c)} [{','.join(c.evidence_ids)}]" for c in claims]
            requested = set(answer_requirements(state["resolved_question"], state["identifiers"])["requested_fields"])
            rendered = [a for a in state["assertions"] if state["output_mode"] == "extractive" or a.field in requested]
            for a in rendered[:6]:
                lines.append(f"{render_assertion(a)} [{','.join(a.evidence_ids)}]")
            if re.search(r"比较|对比|关联|compare", state["resolved_question"], re.I):
                lines.append("以上为多文档证据对照；当前结果不构成已验证的攻击链或多跳推理结论。")
            if state["conflicts"]:
                lines.append("多个来源的字段存在差异，已分别保留断言；尚未判定哪个来源正确。")
            if state["missing_fields"]:
                lines.append("未确认事项：" + "；".join(render_missing_fields(state["missing_fields"])))
            if coverage["missing_topics"]:
                state["warnings"].append("coverage_incomplete")
                lines.insert(0, "现有资料仅支持以下局部分析，不能视为完整的风险分类。")
                lines.append("尚未核实的范围：" + "；".join(coverage["missing_topics"]) + "。可补充相应规范或案例后继续分析。")
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
        state["response"] = QueryResponse(status=status, answer=answer, claims=claims, sources=sources,
                                          coverage=coverage,
                                          facts=state["assertions"] if sources else [], missing_fields=state["missing_fields"], conflicts=state["conflicts"],
                                          warnings=state["warnings"], trace_id=state["trace_id"], mode=state["output_mode"])
        return state
