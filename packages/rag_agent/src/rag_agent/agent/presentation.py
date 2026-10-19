"""Question-specific answer requirements and faithful source-field presentation.

These helpers neither fetch information nor derive vulnerability facts. In
particular, OSV range events are shown as recorded events, and CPE conditions
are not treated as a successful match against a user's deployment. Citation
identities remain with the caller; presentation must not replace them.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from typing import Any

from ..schemas import Assertion, Claim
from .coverage import requested_topics


def answer_requirements(question: str, identifiers: list[str]) -> dict[str, Any]:
    """Build generation instructions from the question, never from model memory."""
    patterns = {
        "comparison": r"比较|对比|优先.*修复|修复.*优先|compare|prioriti[sz]",
        "remediation": r"修复|补丁|缓解|防范|防护|防御|如何.*应对|fixed|patch|mitigat|remediat",
        "affected": r"受影响|影响.*(?:产品|版本|范围|系统|资产)|哪些版本|affected|applicab",
        "risk": r"风险|威胁|危害|严重|评分|利用|攻击|cvss|exploit|risk",
        "definition": r"什么是|是什么|解释|原理|机制|如何发生|what\s+is|explain|mechanism",
        "overview": r"有哪些|哪几类|分类|概览|综述|概况|overview|categories",
        "relation": r"关联|攻击链|多跳|跨文档|结合.*资料|attack\s+chain|multi.hop",
    }
    facets = [name for name, pattern in patterns.items()
              if re.search(pattern, question, re.I)]
    query_type = facets[0] if facets else "definition"
    points = {
        "comparison": ["逐个说明比较对象，使用相同的比较维度", "给出每项结论成立的条件和来源差异",
                       "说明排序依据；没有部署、暴露或利用证据时不要断言绝对优先级"],
        "remediation": ["指出需要处置的具体产品、组件或行为", "将来源中的处置建议写成可执行动作",
                        "区分来源提供的修复版本、缓解措施和未核实参考链接"],
        "affected": ["列出来源记录的产品或组件", "说明版本、配置和环境条件",
                     "未解析的 CPE 条件和 OSV 原始事件不能直接证明用户资产受影响"],
        "risk": ["指出风险对象和触发路径", "解释可能导致的具体后果", "说明结论的适用条件与证据边界"],
        "definition": ["先用一句话解释具体对象", "说明机制中的输入、行为与结果",
                       "有来源支持时给出具体应用场景或例子"],
        "overview": ["按资料支持的风险或主题分类", "每类解释发生方式和后果",
                     "说明资料覆盖范围，不把局部资料冒充完整分类"],
        "relation": ["明确关联的对象、组件及成立条件，并同时引用关系两端的依据",
                     "区分资料明确记载的关联与条件性推断；共享关键词不能证明攻击链"],
    }
    selected = facets or [query_type]
    required_points = list(dict.fromkeys(point for facet in selected for point in points[facet]))
    requested_fields = []
    if re.search(r"评分|cvss|严重程度|严重等级|严重性", question, re.I):
        requested_fields.append("cvss")
    if "affected" in selected:
        requested_fields.append("affected_version")
    if "remediation" in selected:
        requested_fields.extend(["mitigation", "fixed_version"])
    return {
        "query_type": query_type,
        "coverage_topics": requested_topics(question),
        "facets": selected,
        "target_identifiers": list(dict.fromkeys(identifiers)),
        "required_points": required_points,
        "requested_fields": list(dict.fromkeys(requested_fields)),
        "output_profile": output_profile(question, identifiers, selected),
        "answer_style": [
            "直接回答当前问题；按需要说明结论、机制、条件、影响或动作，避免无关字段堆叠",
            "未要求简答时，分自然段充分解释概念、原因和成立条件，不把每个方面压缩成一句标签式结论",
            "每条结论写清具体对象及行为，避免只写加强防护、完善机制、提高意识等空泛建议",
            "重要事实关联实际 evidence_id，推断单独标记，不输出内部推理过程",
            "证据缺少某项时明确缺少什么；不要为凑齐结构自行补充事实、场景或动作",
        ],
        "hard_fact_policy": [
            "产品、版本、CVSS、修复信息及利用状态只使用本次取得的证据与存储断言",
            "CVSS 按版本与评分主体分别说明，不将最高分直接认定为唯一正确评分",
            "没有 CISA KEV 查询证据时不得声称已核验 KEV；请求失败也不等于未列入",
            "来源采集时间不等于官方更新时间，无法满足最新要求时明确时效限制",
        ],
    }


def output_profile(question: str, identifiers: list[str], facets: list[str]) -> dict[str, Any]:
    """Match depth to the question; these bounds never authorize new facts."""
    brief=bool(re.search(r'一句话|只要结论|简短|简要|brief|one sentence',question,re.I))
    detailed=not brief and bool(len(identifiers)>1 or re.search(
        r'详细|深入|完整分析|逐步|攻击链|多跳|跨文档|deep|detailed|step.by.step',question,re.I))
    labels={'definition':['核心结论','机制与条件'],'risk':['核心结论','触发条件与影响'],
        'remediation':['处置建议','适用条件'],'affected':['影响范围','适用条件'],
        'comparison':['比较结论','逐对象对照'],'overview':['主题要点'],
        'relation':['关联依据','成立条件']}
    sections=list(dict.fromkeys(label for facet in facets for label in labels.get(facet,[])))
    one=bool(re.search(r'一句话|只要结论|one sentence',question,re.I))
    return {'detail_level':'concise' if brief else 'detailed' if detailed else 'explanatory',
        'max_claims':1 if one else 2 if brief else 8,
        'max_claim_chars':160 if brief else 800,
        'min_answer_chars':0 if brief else 2500,
        'max_answer_chars':160 if one else 320 if brief else 3500 if detailed else 3000,
        'sections':['核心结论'] if one else sections,
        'show_source_links':True,
        'explain_relation':'relation' in facets or 'comparison' in facets}


def length_revision_feedback(claims, evidence, profile, budget):
    """Length is a conditional target, with a call reserved for source verification."""
    minimum = profile.get('min_answer_chars', 0)
    maximum = profile.get('max_answer_chars', 3000)
    characters = sum(len(claim.text) for claim in claims)
    if (not minimum or not claims or minimum <= characters <= maximum
            or budget.max_answer_tokens < 4096
            or sum(len(item.excerpt) for item in evidence) < minimum
            or budget.max_llm - budget.llm_calls < 2 or budget.remaining() < 12):
        return None
    target = (minimum + maximum) // 2
    paragraphs = min(profile['max_claims'], max(len(claims),
        (target + profile['max_claim_chars'] - 1) // profile['max_claim_chars']))
    return {'body_chars': characters, 'min_body_chars': minimum, 'max_body_chars': maximum,
        'suggested_body_chars': target, 'suggested_paragraphs': paragraphs,
        'suggested_chars_per_paragraph': (target + paragraphs - 1) // paragraphs,
        'max_claims': profile['max_claims'], 'max_claim_chars': profile['max_claim_chars'],
        'instruction': '在现有证据确实支持的范围内重新组织并展开解释正文。'
            '按 suggested_paragraphs 和 suggested_chars_per_paragraph 分配篇幅，'
            '优先保持 supported_draft 已有的主题及引用范围，逐段充分展开，'
            '不要重新加入先前没有通过引用及主题规则的内容；保留成立条件、真实引用和推断标记。'
            '优先解释已有事实的机制、条件和动作，不新增没有来源的事实、版本、案例或建议，'
            '不重复结论凑字数；资料不足时允许短答。字数只计算正文，不含引用列表或原文摘录。'}


def render_claim_lines(claims: list[Claim]) -> list[str]:
    """Merge identical display sentences, retaining every original citation."""
    grouped={}
    for claim in claims:
        text=render_claim(claim)
        key=(claim.kind,' '.join(text.split()))
        if key not in grouped:grouped[key]=(text,[])
        ids=grouped[key][1]
        ids.extend(i for i in claim.evidence_ids if i not in ids)
    return [text+' ['+','.join(ids)+']' for text,ids in grouped.values()]


def render_assertion_lines(assertions: list[Assertion], claims: list[Claim], limit=6) -> list[str]:
    """Display exact duplicate source facts once; source/score differences stay."""
    grouped={}
    for item in assertions:
        text=render_assertion(item)
        if any(c.kind=='fact' and text in c.text and set(item.evidence_ids).issubset(c.evidence_ids) for c in claims):
            continue
        key=(item.entity_id,item.field,json.dumps(item.value,ensure_ascii=False,sort_keys=True),
             item.origin,item.verification)
        if key not in grouped:grouped[key]=(text,[])
        ids=grouped[key][1]
        ids.extend(i for i in item.evidence_ids if i not in ids)
    rows=list(grouped.values())
    lines=[text+' ['+','.join(ids)+']' for text,ids in rows[:limit]]
    if len(rows)>limit:lines.append('其他来源字段保留在原文证据中，可展开核对。')
    return lines


def render_boundary_summary(coverage, limitations, missing_fields) -> str:
    """One short public notice; full reasons remain in structured metadata."""
    labels=list(dict.fromkeys(row.label for row in coverage if row.status!='supported'))
    if not labels:labels=render_missing_fields(missing_fields)
    if labels:
        suffix='；其他限制见证据核对' if len(labels)>3 else ''
        return '【资料边界】尚未完整核实：'+'；'.join(labels[:3])+suffix+'。'
    return '【资料边界】存在资料或运行限制，详见证据核对。' if limitations else ''


def _scalar(value: Any) -> str:
    """Keep source scalars, excluding container reprs and synthetic null values."""
    if isinstance(value, str):
        return " ".join(value.split())
    if type(value) in (int, float):
        return str(value)
    return ""


def _join(values: list[str], limit: int = 6) -> str:
    values = list(dict.fromkeys(value for value in values if value))
    text = "；".join(values[:limit])
    if len(values) > limit:
        text += f"；另有 {len(values) - limit} 项，请查看对应引用"
    return text


def _product(value: Any) -> str:
    if not isinstance(value, dict):
        return _scalar(value)
    name = _scalar(value.get("name")) or _scalar(value.get("product"))
    ecosystem = _scalar(value.get("ecosystem"))
    return name + (f"（{ecosystem}）" if ecosystem else "")


def _range_events(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    labels = {"introduced": "引入边界", "fixed": "修复边界",
              "last_affected": "最后受影响边界", "limit": "范围限制边界"}
    events = []
    for event in value.get("events", []) if isinstance(value.get("events"), list) else []:
        if isinstance(event, dict):
            events.extend(f"{label} {_scalar(event[key])}" for key, label in labels.items()
                          if _scalar(event.get(key)))
    kind = _scalar(value.get("type"))
    text = _join(events)
    return (f"{kind} 范围事件：" if kind else "范围事件：") + text if text else ""


def _affected_entry(value: Any) -> str:
    if not isinstance(value, dict):
        return _scalar(value)
    pieces = []
    product = _product(value.get("package")) or _product(value.get("product"))
    if product:
        pieces.append(f"产品／软件包：{product}")
    versions = value.get("versions", [])
    if isinstance(versions, list):
        shown = _join([_scalar(version) for version in versions])
        if shown:
            pieces.append("来源列出的版本：" + shown)
    ranges = value.get("ranges", [])
    if isinstance(ranges, list):
        shown = _join([_range_events(interval) for interval in ranges])
        if shown:
            pieces.append(shown)
    return "；".join(pieces)


def _affected(value: Any) -> str:
    if isinstance(value, dict) and value.get("parsed") is False:
        return "来源已记录原始 CPE 条件，但尚未解析其布尔组合；无法据此判断实际部署是否受影响"
    if isinstance(value, dict) and ("cpe_matches" in value or "raw_conditions" in value):
        return "来源已记录 CPE 适用条件，尚未完成与实际部署的匹配；请查看引用中的完整条件"
    entries = value.get("items", []) if isinstance(value, dict) else value
    if isinstance(entries, list):
        shown = _join([_affected_entry(entry) for entry in entries])
    else:
        shown = _affected_entry(entries)
    if not shown and isinstance(value, dict):
        shown = _affected_entry(value)
    if shown:
        return "来源记录的影响范围：" + shown
    return "影响范围已记录，但当前展示无法解释其结构；不能据此断言用户资产受影响，请查看引用"


def _fixed(value: Any) -> str:
    if isinstance(value, dict):
        product = _product(value.get("product")) or _product(value.get("package"))
        versions = value.get("versions")
        version = _scalar(value.get("version")) or _scalar(value.get("fixed_version"))
        if isinstance(versions, list):
            version = _join([_scalar(item) for item in versions])
        return (f"{product}：" if product else "") + version if version else ""
    if isinstance(value, list):
        return _join([_fixed(item) for item in value])
    return _scalar(value)


def _mitigation(value: Any) -> str:
    if isinstance(value, dict):
        pieces = []
        for key in ("text", "description", "instructions", "actions", "recommendations", "mitigations"):
            entry = value.get(key)
            if isinstance(entry, list):
                pieces.extend(_scalar(item) for item in entry)
            else:
                pieces.append(_scalar(entry))
        text = _join(pieces)
        references = value.get("references")
        if isinstance(references, list) and references:
            note = (f"来源提供 {len(references)} 条处置参考记录；"
                    "参考链接正文尚未核查，不能仅凭链接确认具体处置措施")
            return (text + "；" if text else "") + note
        return text
    if isinstance(value, list):
        return _join([_mitigation(item) for item in value])
    return _scalar(value)


def _entity_label(entity_id: str) -> str:
    """Show source entities, hiding document storage identities from prose."""
    if re.fullmatch(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", entity_id, re.I):
        return ""
    if re.fullmatch(r"(?:doc_?|backend_|document_)[0-9a-f]{16,}", entity_id, re.I):
        return ""
    return entity_id


def render_assertion(assertion: Assertion) -> str:
    """Render an existing assertion; the caller attaches its unchanged citation."""
    field, value = assertion.field, assertion.value
    if field == "cvss" and isinstance(value, dict):
        version = _scalar(value.get("version")) or "版本未注明"
        score = _scalar(value.get("score")) or "未提供分值"
        source = _scalar(value.get("source")) or "评分主体未注明"
        text = f"来源记录的 CVSS {version} 评分：{score}（评分主体：{source}）"
        severity = _scalar(value.get("severity"))
        if severity:
            text += f"；来源标注等级：{severity}"
    elif field in {"affected_version", "affected"}:
        text = _affected(value)
    elif field == "fixed_version":
        fixed = _fixed(value)
        text = "来源记录的修复版本：" + fixed if fixed else "修复版本字段已记录，但当前结构无法解释；请查看引用"
    elif field in {"mitigation", "remediation"}:
        advice = _mitigation(value)
        text = "来源记录的处置信息：" + advice if advice else "处置字段已记录，但未获得可展示的具体措施；请查看引用"
    elif field in {"product", "products", "affected_products"}:
        entries = value if isinstance(value, list) else [value]
        products = _join([_product(entry) for entry in entries])
        text = "来源记录的产品：" + products if products else "产品字段尚无法解释；请查看引用"
    elif field == "poc_reference":
        text = "来源记录了 POC 参考信息；尚未验证其可用性、适用版本或运行结果，请查看引用"
    else:
        text = "来源保留了一个结构化字段，当前展示未解释其结构；请查看对应引用"
    entity = _entity_label(assertion.entity_id)
    if entity:
        text = f"{entity}：{text}"
    if assertion.verification == "needs_review" or assertion.origin == "llm_candidate":
        text = "【待核实】" + text
    return text


def render_missing_fields(fields: list[str]) -> list[str]:
    """Translate known limitations without exposing internal document UUIDs."""
    labels = {
        "fixed_version": "缺少可核实的修复版本",
        "mitigation": "缺少有来源支持的具体缓解措施",
        "remediation": "缺少有来源支持的修复或缓解措施",
        "cvss": "缺少可核实的 CVSS 评分及其版本、评分主体",
        "affected_version": "缺少可核实的受影响版本或适用条件",
        "affected": "缺少可核实的影响范围",
        "product": "缺少明确的产品信息",
        "products": "缺少明确的产品信息",
        "exploitation": "缺少可核实的利用情报，不能据此断言未被利用",
        "kev": "缺少本次可核实的 CISA KEV 目录查询结果",
        "published_at": "缺少来源发布时间",
        "source_modified_at": "缺少官方记录更新时间",
    }
    result = []
    for field in fields:
        if ":field_not_public:" in field:
            name = {"affected_version": "受影响版本", "cvss": "评分", "fixed_version": "修复版本",
                    "mitigation": "缓解措施"}.get(field.rsplit(":", 1)[-1], "请求字段")
            text = f"{name}原始字段超过引用长度限制，当前回答未使用完整字段，需核对原始资料"
        elif field.startswith("identifier:"):
            identifier = field[len("identifier:"):]
            if re.fullmatch(r"(?:CVE-\d{4}-\d{4,}|GHSA-[A-Za-z0-9-]+)", identifier, re.I):
                text = f"当前查询范围内未找到 {identifier} 的可核验证据"
            else:
                text = "当前查询范围内未找到目标编号的可核验证据"
        elif field == "affected_applicability:unparsed_CPE_boolean_conditions":
            text = "尚未解析 CPE 布尔适用条件，无法判断实际部署是否受影响"
        elif field == "comparison:second_document":
            text = "缺少第二个比较对象的可核验证据，无法完成完整对照"
        else:
            text = labels.get(field.rsplit(":", 1)[-1], "部分必要信息尚未获得可核实证据")
        if text not in result:
            result.append(text)
    return result


def render_claim(claim: Claim) -> str:
    """Make inference visible, leaving fact content and citation IDs unchanged."""
    if claim.kind == "inference" and not claim.text.startswith("【推断】"):
        return "【推断】" + claim.text
    return claim.text


def assertion_payload(assertions: list[Assertion]) -> list[dict[str, Any]]:
    """Copy source assertions for generation; values are never normalized here."""
    return [deepcopy(assertion.model_dump(mode="json", include={
        "assertion_id", "entity_id", "field", "value", "evidence_ids", "origin", "verification",
    })) for assertion in assertions]
