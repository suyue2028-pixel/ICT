"""Source checks and frozen manual evaluation are separate measurements.

No network/model calls; neither source agreement nor a valid URL proves a fact.
"""
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
from urllib.parse import urlsplit
from uuid import UUID

from fastapi import HTTPException
from sqlalchemy import func, select
from backend.models import (DataSource, Document, Evidence, EnrichmentQualityBatch,
                            EnrichmentQualityReview)
from backend.services.enrichment import cpe_components
from backend.services.jobs import utc_now

DIMENSIONS = ("affected", "cwe", "cvss", "remediation", "analysis", "poc")
EVALUATOR_VERSION = "enrichment-quality-v1"
LIMITATIONS = [
    "Automatic checks measure structure/provenance, not semantic correctness or link availability.",
    "Human/independent annotation declarations require team verification; AI labels do not count.",
    "Precision/recall use normalized field facts in sampled documents, not Internet/vulnerability coverage.",
    "Empty gold lists are reviewed absences; missing field labels are not negatives.",
    "No CVSS score recomputation, version ordering, asset applicability or POC execution.",
]


def canonical(value):
    def normalize(item):
        if isinstance(item, dict):
            return {key: normalize(val) for key, val in item.items()}
        if isinstance(item, list):
            return [normalize(val) for val in item]
        if isinstance(item, float) and math.isfinite(item) and item.is_integer():
            return int(item)
        return item
    return json.dumps(normalize(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def unique(values):
    return [json.loads(key) for key in sorted({canonical(value) for value in values})]


def fact_units(field, value):
    """Stable source facts; malformed known values remain predictions, never disappear."""
    if not isinstance(value, dict):
        return [{"unparsed_value": value}]
    if field == "cwe" and isinstance(value.get("ids"), list):
        values = value["ids"]
    elif field == "cvss" and isinstance(value.get("vectors"), list):
        values = [{key: row.get(key) for key in ("version", "vector", "score")}
                  if isinstance(row, dict) else {"unparsed_value": row} for row in value["vectors"]]
        for item in values:
            if type(item.get("score")) in (int, float) and math.isfinite(item["score"]):
                item["score"] = float(item["score"])
    elif field in ("remediation", "analysis", "poc") and isinstance(value.get("references"), list):
        values = [row["url"].strip() if isinstance(row, dict) and isinstance(row.get("url"), str)
                  else {"unparsed_value": row} for row in value["references"]]
    elif field == "affected" and ("cpe_matches" in value or "items" in value):
        values = []
        for row in value.get("cpe_matches", []) if isinstance(value.get("cpe_matches", []), list) else [value["cpe_matches"]]:
            values.append({"kind": "cpe", **{key: row[key] for key in ("criteria", "versionStartIncluding",
                "versionStartExcluding", "versionEndIncluding", "versionEndExcluding") if key in row}}
                if isinstance(row, dict) else {"unparsed_value": row})
        for row in value.get("items", []) if isinstance(value.get("items", []), list) else [value["items"]]:
            if not isinstance(row, dict):
                values.append({"unparsed_value": row})
                continue
            package = row.get("package", {})
            values.append({"kind": "osv", "package": {key: package.get(key) for key in ("name", "ecosystem")}
                if isinstance(package, dict) else package,
                "ranges": row.get("ranges", []), "versions": sorted(row.get("versions", []))
                if isinstance(row.get("versions", []), list) and all(isinstance(v, str) for v in row.get("versions", []))
                else row.get("versions")})
    else:
        values = [{"unparsed_value": value}]
    return unique(values)


def method_filter(statement, method):
    if method == "source_reported":
        return statement.where(Evidence.method == "source_reported")
    if method == "external":
        return statement.where(Evidence.method.startswith("external:"))
    return statement


def document_query(is_demo=False, ai_relevant=True, source_id=None, document_type="vulnerability"):
    query = select(Document).where(Document.is_demo == is_demo, Document.deleted_at.is_(None),
                                   Document.ai_relevant == ai_relevant)
    if source_id:
        query = query.where(Document.source_id == source_id)
    if document_type != "all":
        query = query.where(Document.document_type == document_type)
    return query


def evidence_groups(db, documents, method):
    groups = defaultdict(list)
    if not documents:
        return groups
    query = select(Evidence).join(Document, Document.id == Evidence.document_id).where(
        Evidence.document_id.in_([doc.id for doc in documents]),
        Evidence.content_hash == Document.content_hash, Evidence.field.in_(DIMENSIONS))
    for row in db.scalars(method_filter(query, method).order_by(Evidence.id)):
        groups[str(row.document_id)].append(row)
    return groups


def prediction_rows(rows):
    return [{"evidence_id": str(row.id), "field": row.field, "state": row.state,
             "value": row.value, "method": row.method, "source_url": row.source_url,
             "locator": row.locator, "excerpt": row.excerpt} for row in sorted(rows, key=lambda row: str(row.id))]


def prediction_hash(rows):
    return digest(prediction_rows(rows))


def predicted_units(rows):
    values = defaultdict(list)
    for row in rows:
        if row.state == "known":
            value = row.value.get("value") if row.method.startswith("external:") and isinstance(row.value, dict) else row.value
            values[row.field].extend(fact_units(row.field, value))
    return {name: unique(values[name]) for name in DIMENSIONS}


def valid_url(value):
    if not isinstance(value, str) or len(value) > 8192:
        return False
    try:
        parsed = urlsplit(value)
        parsed.port
        return parsed.scheme in ("http", "https") and bool(parsed.hostname) and not parsed.username and not parsed.password
    except (TypeError, ValueError):
        return False


def audit_document(doc, rows):
    issues = []
    def flag(row, code, severity="error"):
        issues.append({"document_id": str(doc.id), "content_hash": doc.content_hash,
            "field": row.field, "evidence_id": str(row.id), "method": row.method,
            "code": code, "severity": severity})
    for row in rows:
        if row.state != "known":
            continue
        external = row.method.startswith("external:")
        value = row.value.get("value") if external and isinstance(row.value, dict) else row.value
        if not row.locator or not row.excerpt:
            flag(row, "missing_provenance")
        if not valid_url(row.source_url):
            flag(row, "invalid_source_url")
        if not external:
            expected = doc.raw_payload.get("source_fields", {}).get(row.field)
            if expected is None or canonical(expected) != canonical(value):
                flag(row, "source_value_mismatch")
            if row.source_url != doc.source_url or row.locator != "source_fields." + row.field:
                flag(row, "source_locator_mismatch")
            if row.excerpt and row.excerpt not in json.dumps(doc.raw_payload, ensure_ascii=False):
                flag(row, "source_excerpt_mismatch")
        elif not isinstance(row.value, dict) or not row.value.get("evidence"):
            flag(row, "external_citations_missing")
        elif row.value.get("review_state") == "pending_review":
            flag(row, "model_result_pending_review", "warning")
        if not isinstance(value, dict):
            flag(row, "unsupported_field_shape")
            continue
        if not fact_units(row.field, value):
            flag(row, "known_without_facts")
        if row.field == "cwe":
            if not isinstance(value.get("ids"), list) or any(not isinstance(v, str) or not re.fullmatch(r"CWE-[1-9]\d*", v) for v in value.get("ids", [])):
                flag(row, "invalid_cwe")
        elif row.field == "cvss":
            vectors = value.get("vectors")
            if not isinstance(vectors, list):
                flag(row, "invalid_cvss_shape")
                continue
            for vector in vectors:
                if not isinstance(vector, dict):
                    flag(row, "invalid_cvss_shape")
                    continue
                score = vector.get("score")
                if score is None:
                    flag(row, "cvss_score_unknown", "warning")
                elif type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 10:
                    flag(row, "cvss_score_out_of_range")
                version, text = vector.get("version"), vector.get("vector")
                if text is None:
                    flag(row, "cvss_vector_unknown", "warning")
                elif not isinstance(text, str) or not text.startswith("AV:" if version == "2.0" else f"CVSS:{version}/"):
                    flag(row, "cvss_version_vector_mismatch")
        elif row.field in ("remediation", "analysis", "poc"):
            refs = value.get("references")
            if not isinstance(refs, list) or any(not isinstance(ref, dict) or not valid_url(ref.get("url")) for ref in (refs or [])):
                flag(row, "invalid_reference_url")
        elif row.field == "affected":
            matches, packages = value.get("cpe_matches", []), value.get("items", [])
            if not isinstance(matches, list) or not isinstance(packages, list) or not ("cpe_matches" in value or "items" in value):
                flag(row, "invalid_affected_shape")
                continue
            for match in matches:
                if not isinstance(match, dict) or cpe_components(match.get("criteria")) is None:
                    flag(row, "invalid_cpe")
                    continue
                for prefix in ("versionStart", "versionEnd"):
                    bounds = [match[key] for key in (prefix + "Including", prefix + "Excluding") if key in match]
                    if len(bounds) > 1 or any(not isinstance(bound, str) or not bound.strip() for bound in bounds):
                        flag(row, "ambiguous_or_invalid_version_bounds")
            for package in packages:
                if not isinstance(package, dict) or not isinstance(package.get("package"), dict) or not package["package"].get("name"):
                    flag(row, "invalid_osv_package")
                    continue
                ranges = package.get("ranges", [])
                if not isinstance(ranges, list):
                    flag(row, "invalid_osv_ranges")
                    continue
                for interval in ranges:
                    events = interval.get("events", []) if isinstance(interval, dict) else None
                    if not isinstance(events, list) or any(not isinstance(event, dict) or len(event) != 1 or
                            not set(event) <= {"introduced", "fixed", "last_affected", "limit"} or
                            any(not isinstance(v, str) or not v for v in event.values()) for event in (events or [])):
                        flag(row, "invalid_osv_events")
    return issues


def monitoring(db, is_demo=False, ai_relevant=True, source_id=None, document_type="vulnerability",
               prediction_method="source_reported", limit=1000):
    query = document_query(is_demo, ai_relevant, source_id, document_type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    documents = list(db.scalars(query.order_by(Document.id).limit(limit)))
    groups = evidence_groups(db, documents, prediction_method)
    issues, coverage = [], {name: Counter() for name in DIMENSIONS}
    for doc in documents:
        rows = groups[str(doc.id)]
        issues.extend(audit_document(doc, rows))
        for name in DIMENSIONS:
            matching = [row for row in rows if row.field == name]
            states = {row.state for row in matching}
            state = next((candidate for candidate in ("known", "pending_review", "failed", "unprocessed", "not_found", "unknown")
                          if candidate in states), "unprocessed" if prediction_method == "external" else "unknown")
            coverage[name][state] += 1
    counts = Counter(item["code"] for item in issues)
    return {"evaluator_version": EVALUATOR_VERSION, "measured_at": utc_now(),
        "scope": {"is_demo": is_demo, "ai_relevant": ai_relevant, "source_id": source_id,
                  "document_type": document_type, "prediction_method": prediction_method},
        "total_documents": total, "scanned_documents": len(documents), "scan_limit": limit,
        "scan_complete": total <= limit, "dimensions": {name: {**dict(values),
            "known_ratio": values["known"] / len(documents) if documents else None} for name, values in coverage.items()},
        "automatic_checks": {"error_count": sum(i["severity"] == "error" for i in issues),
            "warning_count": sum(i["severity"] == "warning" for i in issues),
            "documents_with_errors": len({i["document_id"] for i in issues if i["severity"] == "error"}),
            "issue_codes": dict(counts), "semantic_accuracy": None},
        "issues": issues, "accuracy": None, "recall": None, "limitations": LIMITATIONS}


def create_batch(db, request):
    query = document_query(request.is_demo, request.ai_relevant, request.source_id, request.document_type)
    total = db.scalar(select(func.count()).select_from(query.subquery()))
    candidates = list(db.scalars(query.order_by(Document.id).limit(5000)))
    by_source = defaultdict(list)
    for doc in candidates:
        by_source[str(doc.source_id)].append(doc)
    for key, values in by_source.items():
        values.sort(key=lambda doc: digest([request.seed, str(doc.id)]))
        by_source[key] = values[:request.per_source]
    source_ids = sorted(by_source, key=lambda key: digest([request.seed, key]))
    selected = []
    for index in range(request.per_source):
        for key in source_ids:
            if index < len(by_source[key]) and len(selected) < request.max_cases:
                selected.append(by_source[key][index])
    if not selected:
        raise HTTPException(422, "quality_sample_empty")
    groups = evidence_groups(db, selected, request.prediction_method)
    sources = {source.id: source for source in db.scalars(select(DataSource).where(DataSource.id.in_([d.source_id for d in selected])))}
    cases = []
    for doc in selected:
        rows, source = groups[str(doc.id)], sources[doc.source_id]
        cases.append({"document_id": str(doc.id), "content_hash": doc.content_hash,
            "prediction_hash": prediction_hash(rows), "source_id": str(source.id), "adapter": source.adapter,
            "source_name": source.name, "document_type": doc.document_type, "title": doc.title,
            "source_url": doc.source_url, "content_completeness": doc.content_completeness,
            "content": doc.content, "raw_payload": doc.raw_payload,
            "predicted_fields": prediction_rows(rows), "predicted_units": predicted_units(rows),
            "automatic_issues": audit_document(doc, rows)})
    if len(canonical(cases).encode("utf-8")) > 16 * 1024 * 1024:
        raise HTTPException(413, "quality_sample_too_large_reduce_cases")
    selection = {**request.model_dump(mode="json"), "evaluator_version": EVALUATOR_VERSION,
        "candidate_documents": total, "candidate_scan_limit": 5000, "candidate_scan_complete": total <= 5000,
        "sampling": "seeded per-source round-robin, not a random sample of Internet intelligence",
        "source_counts": dict(Counter(case["adapter"] for case in cases))}
    row = EnrichmentQualityBatch(is_demo=request.is_demo, sample_size=len(cases),
        selection=selection, cases=cases, fingerprint=digest({"selection": selection, "cases": cases}))
    db.add(row)
    db.flush()
    return row


def current_case_flags(db, batch):
    ids = [UUID(case["document_id"]) for case in batch.cases]
    documents = {str(doc.id): doc for doc in db.scalars(select(Document).where(Document.id.in_(ids)))}
    groups = evidence_groups(db, list(documents.values()), batch.selection["prediction_method"])
    return {case["document_id"]: bool((doc := documents.get(case["document_id"])) and
        doc.deleted_at is None and doc.is_demo == batch.is_demo and doc.content_hash == case["content_hash"] and
        doc.ai_relevant == batch.selection["ai_relevant"] and
        prediction_hash(groups[case["document_id"]]) == case["prediction_hash"])
        for case in batch.cases}


def save_review(db, batch, body):
    # Document lock also serializes concurrent reviewers and external enrichment writes.
    doc = db.scalar(select(Document).where(Document.id == body.document_id).with_for_update())
    case = next((case for case in batch.cases if case["document_id"] == str(body.document_id)), None)
    if case is None:
        raise HTTPException(422, "quality_case_not_in_batch")
    if case["content_hash"] != body.content_hash or case["prediction_hash"] != body.prediction_hash:
        raise HTTPException(409, "quality_snapshot_mismatch")
    if doc is None or not current_case_flags(db, batch)[str(body.document_id)]:
        raise HTTPException(409, "quality_case_changed_create_new_batch")
    values = body.model_dump(mode="json")
    values["batch_id"] = batch.id
    values["document_id"] = body.document_id
    # Canonicalization rejects non-finite JSON numbers before PostgreSQL persistence.
    try:
        canonical(values["field_reviews"])
    except (ValueError, TypeError):
        raise HTTPException(422, "quality_invalid_expected_value") from None
    existing = db.scalar(select(EnrichmentQualityReview).where(
        EnrichmentQualityReview.batch_id == batch.id, EnrichmentQualityReview.document_id == body.document_id))
    if existing:
        if any(getattr(existing, name) != value for name, value in values.items()):
            raise HTTPException(409, "quality_review_immutable_create_new_batch")
        return existing, False
    row = EnrichmentQualityReview(**values)
    db.add(row)
    db.flush()
    return row, True


def score_counts(counts):
    tp, fp, fn = (counts.get(key, 0) for key in ("tp", "fp", "fn"))
    fields = counts.get("reviewed_fields", 0)
    return {**{key: counts.get(key, 0) for key in ("reviewed_fields", "exact_fields", "reviewed_absences", "expected_positive_fields")},
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "recall": tp / (tp + fn) if tp + fn else None,
        "exact_field_accuracy": counts.get("exact_fields", 0) / fields if fields else None}


def batch_report(db, batch, current_only=False):
    reviews = {str(row.document_id): row for row in db.scalars(select(EnrichmentQualityReview).where(
        EnrichmentQualityReview.batch_id == batch.id))}
    flags = current_case_flags(db, batch)
    totals, dimensions = Counter(), {name: Counter() for name in DIMENSIONS}
    eligible, excluded_ai, excluded_stale = 0, 0, 0
    for case in batch.cases:
        row = reviews.get(case["document_id"])
        if row is None:
            continue
        if row.annotation_origin != "human" or not row.independent:
            excluded_ai += 1
            continue
        if current_only and not flags[case["document_id"]]:
            excluded_stale += 1
            continue
        eligible += 1
        for name, label in row.field_reviews.items():
            expected = {canonical(unit) for unit in label["expected"]}
            predicted = {canonical(unit) for unit in case["predicted_units"][name]}
            counts = Counter(tp=len(expected & predicted), fp=len(predicted - expected), fn=len(expected - predicted),
                reviewed_fields=1, exact_fields=int(expected == predicted),
                reviewed_absences=int(not expected), expected_positive_fields=int(bool(expected)))
            totals.update(counts)
            dimensions[name].update(counts)
    field_count = totals["reviewed_fields"]
    return {"batch_id": batch.id, "fingerprint": batch.fingerprint, "evaluator_version": EVALUATOR_VERSION,
        "is_demo": batch.is_demo, "current_only": current_only,
        "status": "awaiting_independent_review" if not eligible else
            "reviewed_sample" if field_count == batch.sample_size * len(DIMENSIONS) else "partially_reviewed",
        "sample_documents": batch.sample_size, "submitted_review_documents": len(reviews),
        "independently_reviewed_documents": eligible, "excluded_ai_or_nonindependent": excluded_ai,
        "excluded_stale_reviews": excluded_stale, "stale_cases": sum(not value for value in flags.values()),
        "reviewed_field_ratio": field_count / (batch.sample_size * len(DIMENSIONS)) if batch.sample_size else None,
        "overall": score_counts(totals), "dimensions": {name: score_counts(values) for name, values in dimensions.items()},
        "vulnerability_coverage_recall": None, "competition_threshold_verified": False,
        "limitations": LIMITATIONS}
