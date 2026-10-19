"""Bounded query requirements, not a taxonomy asserted as source facts.

Coverage is only credited to retained, cited, model-verified claims. A topical
match or a retrieved document alone never means the question was answered.
"""
from __future__ import annotations

import re

from pydantic import Field

from ..schemas import Claim, Model


def requested_topics(question: str) -> list[dict[str, str]]:
    # This is a requirement outline for compound tool questions. Other queries
    # continue using the existing facets/structured-field checks.
    if not re.search(r"工具|\b(?:tools?|toolkit)\b", question, re.I):
        return []
    topics = [
        {"id": "permissions", "label": "工具权限与访问边界"},
        {"id": "tool_execution", "label": "工具调用滥用与执行风险"},
        {"id": "audit_logging", "label": "调用日志与可追溯性"},
        {"id": "prompt_injection", "label": "提示注入对工具调用的影响"},
    ]
    focused = set()
    for topic, pattern in {
        "permissions": r"权限|访问边界|文件访问|沙箱|permission|access control|file access|sandbox",
        "tool_execution": r"调用滥用|执行风险|任意执行|tool abuse|execution risk|arbitrary execution",
        "audit_logging": r"日志|可追溯|可追踪|audit log|audit trail|logging|traceability",
        "prompt_injection": r"提示.*注入|prompt injection",
    }.items():
        if re.search(pattern, question, re.I):
            focused.add(topic)
    # Narrow questions should not be marked incomplete for unasked dimensions.
    if focused and not re.search(r"全面|完整|各方面|综述|overview|comprehensive", question, re.I):
        topics = [topic for topic in topics if topic["id"] in focused]
    elif not focused and not re.search(r"审计|风险|威胁|问题|audit|risk|threat", question, re.I):
        return []
    if re.search(r"防护|防范|缓解|处置|应对|mitigat|remediat|protect", question, re.I):
        topics.append({"id": "mitigation", "label": "有来源支持的防护措施"})
    return topics


class RelevanceSelection(Model):
    evidence_id: str
    relevance: str = Field(pattern=r"^(direct|supporting|background|unrelated)$")
    topic_ids: list[str] = Field(default_factory=list, max_length=8)
    reason: str = Field(min_length=1, max_length=160)


class RelevanceResult(Model):
    selections: list[RelevanceSelection] = Field(max_length=8)


def parse_relevance(result, evidence, topics):
    parsed = RelevanceResult.model_validate(result)
    allowed = {e.evidence_id for e in evidence}
    by_id = {e.evidence_id: e for e in evidence}
    topic_ids = {topic["id"] for topic in topics}
    seen, retained = set(), {}
    for row in parsed.selections:
        if row.evidence_id not in allowed or row.evidence_id in seen or not set(row.topic_ids).issubset(topic_ids):
            raise ValueError("invalid relevance selection")
        seen.add(row.evidence_id)
        text = by_id[row.evidence_id].title + " " + by_id[row.evidence_id].excerpt
        # A malicious saved prompt/configuration is not by itself evidence for
        # the specific prompt-injection or audit-log mechanism. These two scope
        # credits require an explicit source anchor, without filtering recall.
        anchors = {
            "prompt_injection": r"prompt[ -]injection|提示(?:词)?注入",
            "audit_logging": r"audit[ -]logs?|audit trail|invocation log|logging|日志|可追溯|可追踪",
        }
        row.topic_ids = [topic for topic in row.topic_ids if topic not in anchors or
                         re.search(anchors[topic], text, re.I)]
        # With compound requirements, support must identify a specific scope.
        if row.relevance in {"direct", "supporting"} and (not topics or row.topic_ids):
            retained[row.evidence_id] = row
    if seen != allowed:
        raise ValueError("incomplete relevance selection")
    return parsed.selections, retained


def validate_claim_topics(claim: Claim, assignments: dict, topics: list[dict]):
    valid = {t["id"] for t in topics}
    supported = {topic for eid in claim.evidence_ids for topic in
                 getattr(assignments.get(eid), "topic_ids", [])}
    if not set(claim.topic_ids).issubset(valid & supported):
        raise ValueError("claim topic outside assessed evidence scope")


def final_coverage(topics, claims, assignments, *, verified):
    answered = set()
    if verified:
        for claim in claims:
            valid_claim_topics = {topic for eid in claim.evidence_ids for topic in
                                  getattr(assignments.get(eid), "topic_ids", [])}
            answered.update(set(claim.topic_ids) & valid_claim_topics)
    return {
        "answered_topics": [t["label"] for t in topics if t["id"] in answered],
        "missing_topics": [t["label"] for t in topics if t["id"] not in answered],
    }
