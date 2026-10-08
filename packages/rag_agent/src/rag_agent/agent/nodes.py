from __future__ import annotations
from collections import defaultdict
from datetime import datetime, timedelta, timezone
import json
import re
import time
from zoneinfo import ZoneInfo
from ..ingestion.normalize import identifiers
from ..schemas import Claim, Draft, Plan, QueryResponse
from .state import BudgetExceeded

TRANSLATIONS = {"提示注入": "prompt injection", "提示词注入": "prompt injection", "数据投毒": "data poisoning", "供应链": "supply chain", "敏感信息": "sensitive information"}


def is_followup(question):
    return bool(re.search(r"它|这个漏洞|该漏洞|这个版本|该版本|上述漏洞|这个和上一个", question)
                or re.fullmatch(r"(?:那|那么|请问)?\s*(?:怎么修复|如何修复|怎么缓解|影响哪些版本|受影响版本有哪些|还有什么风险)[？?。\s]*", question))


def resolve_history(question, history, context, documents):
    """Only user-authored targets resolve references; a new topic stops the scan."""
    if not is_followup(question) or identifiers(question):
        return question, [], None, ""
    users = [m.content for m in history if m.role == "user"]
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
            if self.llm and compare:
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
        # Reserve one call for generation. No model-produced ID can expand scope.
        if state["evidence"] and not state["identifiers"] and self.llm and self.llm.mode == "live":
            before = len(state["evidence"])
            selected = []
            try:
                if state["budget"].llm_calls >= state["budget"].max_llm - 1:
                    raise ValueError("no assessment budget")
                result = await self.llm.complete("assess", {"question": question,
                    "evidence": [e.model_dump(mode="json") for e in state["evidence"]]}, state["budget"], self.settings.llm_timeout)
                ids = result.get("evidence_ids")
                allowed = {e.evidence_id for e in state["evidence"]}
                if not isinstance(ids, list) or any(not isinstance(i, str) for i in ids) or not set(ids).issubset(allowed):
                    raise ValueError("invalid relevance selection")
                selected = [e for e in state["evidence"] if e.evidence_id in ids]
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError):
                state["warnings"].append("relevance_check_failed")
            state["evidence"] = selected
            docs = {e.document_id for e in selected}
            state["assertions"] = [a for a in state["assertions"] if a.document_id in docs]
            state["warnings"].append(f"relevance_filter:candidates={before};selected={len(selected)}")
            state["trace"].append({"node": "relevance_filter", "candidate_count": before, "selected_count": len(selected)})
        state["conflicts"] = conflicts(state["assertions"])
        if state["evidence"]:
            for pattern, field in [(r"修复|补丁|fixed|patch", "fixed_version"), (r"评分|cvss", "cvss"), (r"影响范围|受影响|影响哪些版本|affected", "affected_version")]:
                if re.search(pattern, question, re.I):
                    for doc_id in dict.fromkeys(e.document_id for e in state["evidence"]):
                        if not any(a.document_id == doc_id and a.field == field for a in state["assertions"]):
                            state["missing_fields"].append(f"{doc_id}:{field}")
                    if field == "affected_version" and any(a.field == field and isinstance(a.value, dict) and a.value.get("parsed") is False for a in state["assertions"]):
                        state["missing_fields"].append("affected_applicability:unparsed_CPE_boolean_conditions")
            if re.search(r"比较|对比|compare", question, re.I) and len({e.document_id for e in state["evidence"]}) < 2:
                state["missing_fields"].append("comparison:second_document")
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
        if self.llm and state["budget"].llm_calls < self.settings.max_llm_calls - 1:
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
        fields = {i: state["snapshot"].evidence[i] for a in state["assertions"] for i in a.evidence_ids}
        body = {e.evidence_id: e for e in state["evidence"]}
        state["selected_evidence"] = list({**body, **fields}.values())
        if self.llm:
            try:
                result = await self.llm.complete("synthesize", {
                    "question": state["resolved_question"], "evidence": [e.model_dump(mode="json") for e in state["selected_evidence"]],
                    "missing_fields": state["missing_fields"], "conflicts": state["conflicts"],
                }, state["budget"], self.settings.llm_timeout)
                state["draft"] = Draft.model_validate(result)
                state["output_mode"] = self.llm.mode
            except BudgetExceeded:
                raise
            except (ValueError, RuntimeError, KeyError):
                self.fallback(state, "generation_failed:返回有证据的摘录，未伪装模型生成")
        else:
            self.fallback(state)
        return state

    async def validate_output(self, state):
        self.event(state, "validate_output")
        draft = state.get("draft")
        selected = state.get("selected_evidence", state["evidence"])
        if draft and state["output_mode"] != "extractive":
            try:
                validate_draft(draft, selected, state["assertions"])
            except ValueError:
                self.fallback(state, "output_rejected:无效引用或结构化硬事实已拦截")
                draft = state["draft"]
        if draft and draft.claims and state["output_mode"] == "live":
            if state["budget"].llm_calls < state["budget"].max_llm:
                try:
                    result = await self.llm.complete("verify", {
                        "question": state["resolved_question"],
                        "claims": [c.model_dump(mode="json") for c in draft.claims],
                        "evidence": [e.model_dump(mode="json") for e in selected],
                    }, state["budget"], self.settings.llm_timeout)
                    indexes = result.get("supported_claim_indexes")
                    if not isinstance(indexes, list) or any(type(i) is not int or not 0 <= i < len(draft.claims) for i in indexes):
                        raise ValueError("invalid support result")
                    retained = [c for i, c in enumerate(draft.claims) if i in indexes]
                    if len(retained) < len(draft.claims):
                        state["warnings"].append("unsupported_claims_removed")
                    draft = draft.model_copy(update={"claims": retained})
                except BudgetExceeded:
                    raise
                except (ValueError, RuntimeError, KeyError):
                    state["warnings"].append("claim_support_check_failed")
                    draft = None
            else:
                state["warnings"].append("claim_support_check_unavailable:call_budget")
        if not draft or not draft.claims:
            answer = state["clarification"] or "当前知识库没有足以直接回答这个问题的可核验证据。请补充相关资料，或指定漏洞编号、产品及查询范围。"
            if state["missing_fields"]:
                answer += " 缺失：" + "、".join(state["missing_fields"])
            status = "insufficient_evidence"
            claims, sources = [], []
        else:
            claims = draft.claims
            if state["output_mode"] == "live":
                cited_ids = {i for claim in claims for i in claim.evidence_ids}
                cited_docs = {e.document_id for e in selected if e.evidence_id in cited_ids}
                state["assertions"] = [a for a in state["assertions"] if a.document_id in cited_docs]
            lines = [f"{c.text} [{','.join(c.evidence_ids)}]" for c in claims]
            requested = set()
            for pattern, field in [(r"修复|补丁|fixed|patch", "fixed_version"), (r"评分|cvss", "cvss"), (r"影响.*版本|受影响|affected", "affected_version")]:
                if re.search(pattern, state["resolved_question"], re.I):
                    requested.add(field)
            rendered = [a for a in state["assertions"] if state["output_mode"] == "extractive" or a.field in requested]
            for a in rendered[:6]:
                if a.field == "cvss":
                    value = a.value
                    recorded = f"CVSS {value.get('version')} 评分 {value.get('score')}（来源主体：{value.get('source', '未注明')}）"
                elif a.field == "affected_version" and isinstance(a.value, dict) and a.value.get("parsed") is False:
                    recorded = "受影响范围的原始 CPE 布尔条件已记录，尚未解析，不能据此断言资产受影响"
                else:
                    recorded = a.field + "：" + json.dumps(a.value, ensure_ascii=False)[:200]
                lines.append(f"已记录 {a.entity_id} 的 {recorded} [{','.join(a.evidence_ids)}]")
            if re.search(r"比较|对比|关联|compare", state["resolved_question"], re.I):
                lines.append("以上为多文档证据对照；当前结果不构成已验证的攻击链或多跳推理结论。")
            if state["conflicts"]:
                lines.append("多个来源的字段存在差异，已分别保留断言；尚未判定哪个来源正确。")
            if state["missing_fields"]:
                lines.append("证据未提供以下内容（未知）：" + "、".join(state["missing_fields"]))
            if state["output_mode"] == "extractive":
                lines.insert(0, "以下为来源摘录与已记录字段，尚未进行远程模型语义生成。")
            answer = "\n".join(lines)
            status = "partial" if state["missing_fields"] or state["conflicts"] or any("rejected" in w or "failed" in w or "unsupported" in w or "unavailable" in w for w in state["warnings"]) else "ok"
            # Structured facts remain machine-readable and require their evidence.
            cited = {i for c in claims for i in c.evidence_ids} | {i for a in state["assertions"] for i in a.evidence_ids}
            sources = [e for e in selected if e.evidence_id in cited]
        state["response"] = QueryResponse(status=status, answer=answer, claims=claims, sources=sources,
                                          facts=state["assertions"] if sources else [], missing_fields=state["missing_fields"], conflicts=state["conflicts"],
                                          warnings=state["warnings"], trace_id=state["trace_id"], mode=state["output_mode"])
        return state
