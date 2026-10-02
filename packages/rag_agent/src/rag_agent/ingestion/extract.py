"""Conservative extraction: preserve original source objects and locators."""
import hashlib
import json
import re
from ..schemas import Assertion, EvidenceDetail


def object_id(prefix, payload):
    return prefix + hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()[:28]


def field_evidence(doc, doc_id, version, pointer, value):
    return EvidenceDetail(
        evidence_id=f"{doc_id}:v{version}:field:{object_id('', pointer)}",
        document_id=doc_id, version=version, title=doc.title, url=doc.source_url,
        excerpt=json.dumps(value, ensure_ascii=False, sort_keys=True),
        locator={"kind": "json_pointer", "pointer": pointer},
        published_at=doc.published_at, collected_at=doc.collected_at,
    )


def extract(doc, doc_id, version, chunk_evidence):
    assertions, evidence = [], []
    entity = doc.identifiers[0].value if doc.identifiers else doc_id

    def add(field, value, pointer, origin="source_structured", ent=entity, source_value=None):
        if field == "cvss":
            if not isinstance(value, dict) or value.get("version") not in {"2.0", "3.0", "3.1", "4.0"}:
                raise ValueError("invalid CVSS structure/version in source record")
            score = value.get("score")
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0 <= score <= 10:
                raise ValueError("invalid CVSS score in source record")
        ev = field_evidence(doc, doc_id, version, pointer, value if source_value is None else source_value)
        evidence.append(ev)
        assertion = Assertion(document_id=doc_id, version=version, entity_id=ent, field=field,
                              value=value, evidence_ids=[ev.evidence_id], origin=origin,
                              verification="source_recorded", recorded_at=doc.collected_at)
        assertion.assertion_id = object_id("assert_", assertion.model_dump(mode="json", exclude={"assertion_id"}))
        assertions.append(assertion)

    if doc.identifiers:
        evidence.append(field_evidence(doc, doc_id, version, "/identifiers", [i.model_dump() for i in doc.identifiers]))
    if doc.product_entities:
        evidence.append(field_evidence(doc, doc_id, version, "/product_entities", doc.product_entities))
    raw = doc.raw_payload or {}
    cve = raw.get("cve", {})
    if isinstance(cve, dict):
        for metric_type, records in cve.get("metrics", {}).items():
            for i, record in enumerate(records):
                data = record.get("cvssData", {})
                if data.get("baseScore") is not None and data.get("version"):
                    add("cvss", {"version": data["version"], "score": data["baseScore"],
                                 "vector": data.get("vectorString"), "severity": data.get("baseSeverity", record.get("baseSeverity")),
                                 "source": record.get("source"), "type": record.get("type")},
                        f"/raw_payload/cve/metrics/{metric_type}/{i}", source_value=record)
        if cve.get("configurations"):
            # Preserve all AND/OR/negate/nested applicability; no asset-match claim.
            add("affected_version", {"raw_conditions": cve["configurations"], "parsed": False,
                                     "warning": "CPE boolean applicability is recorded, not resolved"}, "/raw_payload/cve/configurations", source_value=cve["configurations"])
    # Backend adapters may deliver source-recorded fields with exact JSON pointers.
    for i, item in enumerate(raw.get("source_fields", [])):
        if item.get("field") in {"cvss", "affected_version", "fixed_version", "mitigation", "poc_reference"} and "value" in item:
            add(item["field"], item["value"], f"/raw_payload/source_fields/{i}/value", ent=item.get("entity_id", entity))
    for ev in chunk_evidence:
        for match in re.finditer(r"(?:fixed\s+in(?:\s+version)?|修复版本\s*[:：为])\s*(v?\d+\.\d+(?:\.\d+)?)", ev.excerpt, re.I):
            assertion = Assertion(document_id=doc_id, version=version, entity_id=entity,
                                  field="fixed_version", value=match.group(1), evidence_ids=[ev.evidence_id],
                                  origin="rule_extracted", verification="source_recorded", recorded_at=doc.collected_at)
            assertion.assertion_id = object_id("assert_", assertion.model_dump(mode="json", exclude={"assertion_id"}))
            if not any(a.field == assertion.field and a.value == assertion.value for a in assertions):
                assertions.append(assertion)
    return assertions, evidence
