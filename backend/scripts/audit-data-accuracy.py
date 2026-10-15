"""Read-only source fidelity audit; never creates human labels or changes intelligence."""
import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
from urllib.parse import quote, urlsplit

import httpx
from sqlalchemy import select, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.core.config import get_settings
from backend.db.session import get_session_factory
from backend.models import DataSource, Document, DocumentRevision, Evidence, EnrichmentQualityReview
from backend.collectors.live import Transport, plain, timestamp
from backend.services.enrichment_quality import DIMENSIONS, canonical, fact_units, monitoring, predicted_units
from backend.services.pipeline import payload_hash

REFERENCES = {
    "osv": "https://google.github.io/osv.dev/get-v1-vulns/",
    "nvd": "https://nvd.nist.gov/developers/vulnerabilities",
    "cvss": "https://www.first.org/cvss/v3.1/specification-document",
}
ALLOWED_HOSTS = {"api.osv.dev", "services.nvd.nist.gov", "api.crossref.org", "export.arxiv.org",
    "arxiv.org", "www.nvidia.com", "www.gov.uk", "www.gov.cn", "airc.nist.gov", "csrc.nist.gov",
    "discuss.huggingface.co", "blog.trailofbits.com", "isc.sans.edu", "sec.cloudapps.cisco.com"}


def v3_base(vector):
    """FIRST v3.1 section 7.1, 7.4 and Appendix A; base metrics only."""
    if not isinstance(vector, str) or not vector.startswith(("CVSS:3.0/", "CVSS:3.1/")):
        return None
    parts = [part.split(":", 1) for part in vector.split("/")[1:]]
    if any(len(part) != 2 for part in parts) or len({part[0] for part in parts}) != len(parts):
        raise ValueError("invalid_vector")
    values = dict(parts)
    scope = values["S"]
    if scope not in {"U", "C"}:
        raise ValueError("invalid_scope")
    av = {"N": .85, "A": .62, "L": .55, "P": .2}[values["AV"]]
    ac = {"L": .77, "H": .44}[values["AC"]]
    pr = {"N": .85, "L": .62 if scope == "U" else .68, "H": .27 if scope == "U" else .5}[values["PR"]]
    ui = {"N": .85, "R": .62}[values["UI"]]
    impacts = [{"N": 0, "L": .22, "H": .56}[values[name]] for name in ("C", "I", "A")]
    iss = 1 - (1 - impacts[0]) * (1 - impacts[1]) * (1 - impacts[2])
    impact = 6.42 * iss if scope == "U" else 7.52 * (iss - .029) - 3.25 * (iss - .02) ** 15
    if impact <= 0:
        return 0.0
    score = min((impact + 8.22 * av * ac * pr * ui) * (1 if scope == "U" else 1.08), 10)
    integer = round(score * 100000)
    return integer / 100000 if integer % 10000 == 0 else (integer // 10000 + 1) / 10


def self_check():
    assert v3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:H/I:H/A:H") == 9.8
    assert v3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:H") == 7.5
    assert v3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:U/C:N/I:N/A:N") == 0
    assert v3_base("CVSS:3.1/AV:N/AC:L/PR:N/UI:N/S:C/C:H/I:H/A:H") == 10
    assert v3_base("CVSS:3.0/AV:N/AC:L/PR:N/UI:R/S:C/C:L/I:L/A:N") == 6.1
    assert v3_base("CVSS:4.0/AV:N") is None


def native_fields(adapter, raw):
    """Read native fields independently; do not call the production collectors."""
    if adapter == "osv":
        refs = raw.get("references", [])
        vectors = []
        for row in raw.get("severity", []):
            vector = row.get("score")
            version = vector.split("/")[0][5:] if isinstance(vector, str) and vector.startswith("CVSS:") else (
                "2.0" if row.get("type") == "CVSS_V2" else "unknown")
            vectors.append({"version": version, "vector": vector, "score": None})
        return {"affected": {"items": raw.get("affected", [])},
            "cwe": {"ids": (raw.get("database_specific") or {}).get("cwe_ids", [])},
            "cvss": {"vectors": vectors},
            "remediation": {"references": [row for row in refs if row.get("type") == "FIX"]},
            "analysis": {"references": [row for row in refs if row.get("type") in {"ARTICLE", "REPORT", "ADVISORY"}]}}
    if adapter != "nvd":
        return None
    matches = []
    def visit(value):
        if isinstance(value, list):
            for item in value:
                visit(item)
        elif isinstance(value, dict):
            for row in value.get("cpeMatch", []):
                if row.get("vulnerable"):
                    matches.append({key: row[key] for key in ("criteria", "versionStartIncluding", "versionStartExcluding",
                        "versionEndIncluding", "versionEndExcluding") if key in row})
            for key, item in value.items():
                if key != "cpeMatch":
                    visit(item)
    visit(raw.get("configurations", []))
    vectors = [{"version": row.get("cvssData", {}).get("version", "unknown"),
                "vector": row.get("cvssData", {}).get("vectorString"),
                "score": row.get("cvssData", {}).get("baseScore")}
               for group in raw.get("metrics", {}).values() for row in group]
    cwes = sorted({item["value"] for row in raw.get("weaknesses", []) for item in row.get("description", [])
                   if re.fullmatch(r"CWE-\d+", item.get("value", ""))})
    refs = raw.get("references", [])
    return {"affected": {"cpe_matches": matches}, "cwe": {"ids": cwes}, "cvss": {"vectors": vectors},
            **{field: {"references": [row for row in refs if set(row.get("tags", [])) & tags]}
               for field, tags in {"remediation": {"Patch"}, "analysis": {"Technical Description", "Third Party Advisory"},
                                   "poc": {"Exploit"}}.items()}}


def units(fields):
    return {name: {canonical(value) for value in fact_units(name, item)}
            if any(item.values()) else set() for name, item in fields.items()}


def compare_fields(expected, actual):
    gold = units(expected)
    details = {}
    for field, expected_units in gold.items():
        predicted = {canonical(value) for value in actual.get(field, [])}
        if expected_units or predicted:
            details[field] = {"matches": expected_units == predicted, "matched_facts": len(expected_units & predicted),
                "extra_facts": len(predicted - expected_units), "missing_facts": len(expected_units - predicted)}
    return details


def load_snapshot(seed):
    report = {"integrity": Counter(), "native_field_agreement": Counter(), "cvss_base_check": Counter(),
              "source_inventory": {}, "findings": [], "native_disagreements": []}
    by_source = defaultdict(list)
    with get_session_factory()() as db:
        db.connection(execution_options={"isolation_level": "REPEATABLE READ"})
        db.execute(text("SET TRANSACTION READ ONLY"))
        report["structural_scan"] = monitoring(db, document_type="all", include_issues=False)
        report["human_review_records"] = len(list(db.scalars(select(EnrichmentQualityReview.id))))
        sources = {row.id: row for row in db.scalars(select(DataSource).where(DataSource.adapter != "offline"))}
        documents = list(db.scalars(select(Document).where(Document.is_demo.is_(False), Document.deleted_at.is_(None),
                                                          Document.source_id.in_(sources))))
        evidence = defaultdict(list)
        for row in db.scalars(select(Evidence).join(Document, Evidence.document_id == Document.id).where(
                Document.is_demo.is_(False), Document.deleted_at.is_(None), Evidence.content_hash == Document.content_hash,
                Evidence.method == "source_reported", Evidence.field.in_(DIMENSIONS))):
            evidence[row.document_id].append(row)
        revisions = {row.document_id: row for row in db.scalars(select(DocumentRevision).join(Document,
            DocumentRevision.document_id == Document.id).where(Document.is_demo.is_(False), Document.deleted_at.is_(None),
                DocumentRevision.content_hash == Document.content_hash))}
        for doc in documents:
            source = sources[doc.source_id]
            report["integrity"]["documents_checked"] += 1
            identity = {"document_id": str(doc.id), "adapter": source.adapter, "external_id": doc.external_id,
                        "content_hash": doc.content_hash, "source_url": doc.source_url}
            issues = []
            if payload_hash(doc.raw_payload) != doc.content_hash:
                issues.append("document_hash_mismatch")
            revision = revisions.get(doc.id)
            if revision is None:
                issues.append("current_revision_missing")
            elif payload_hash(revision.payload) != doc.content_hash or revision.payload != doc.raw_payload:
                issues.append("current_revision_payload_mismatch")
            for name in ("title", "content", "source_url", "document_type", "is_demo"):
                if getattr(doc, name) != doc.raw_payload.get(name):
                    issues.append(name + "_differs_from_saved_payload")
            if doc.published_at and doc.published_at > doc.first_collected_at:
                issues.append("published_after_first_collection_review_time")
            raw = doc.raw_payload.get("raw") or {}
            if isinstance(raw, dict) and raw.get("withdrawn"):
                issues.append("withdrawn_osv_record_retained")
            if isinstance(raw, dict) and raw.get("vulnStatus") == "Rejected":
                issues.append("rejected_nvd_record_retained")
            for code in issues:
                report["integrity"][code] += 1
                report["findings"].append({**identity, "code": code})
            if not issues:
                report["integrity"]["no_integrity_findings"] += 1
            prediction = predicted_units(evidence[doc.id])
            expected = native_fields(source.adapter, raw) if isinstance(raw, dict) else None
            if expected is not None:
                checked = compare_fields(expected, prediction)
                report["native_field_agreement"]["documents_checked"] += 1
                for field, result in checked.items():
                    report["native_field_agreement"]["positive_or_predicted_fields_checked"] += 1
                    report["native_field_agreement"]["fields_matching" if result["matches"] else "fields_disagreeing"] += 1
                    if not result["matches"]:
                        report["native_disagreements"].append({**identity, "field": field, **result})
            for row in evidence[doc.id]:
                if row.field != "cvss" or row.state != "known":
                    continue
                for vector in row.value.get("vectors", []):
                    identity_vector = {**identity, "evidence_id": str(row.id), "vector": vector.get("vector")}
                    try:
                        score = v3_base(vector.get("vector"))
                    except (ValueError, KeyError, TypeError):
                        report["cvss_base_check"]["invalid_v3_vectors"] += 1
                        report["findings"].append({**identity_vector, "code": "invalid_cvss_v3_base_vector"})
                        continue
                    if score is None:
                        report["cvss_base_check"]["unsupported_or_missing_vectors"] += 1
                    elif vector.get("score") is None:
                        report["cvss_base_check"]["valid_v3_vector_source_score_unknown"] += 1
                    else:
                        report["cvss_base_check"]["v3_scores_compared"] += 1
                        if abs(score - vector["score"]) > .00001:
                            report["cvss_base_check"]["v3_score_disagreements"] += 1
                            report["findings"].append({**identity_vector, "code": "cvss_v3_base_score_disagreement",
                                                       "saved_score": vector["score"], "calculated_base_score": score})
                        else:
                            report["cvss_base_check"]["v3_scores_matching"] += 1
            by_source[source.adapter].append({**identity, "category": source.category,
                "title": doc.title, "ai_relevant": doc.ai_relevant, "document_type": doc.document_type,
                "source_updated_at": doc.source_updated_at.isoformat() if doc.source_updated_at else None,
                "published_at": doc.published_at.isoformat() if doc.published_at else None,
                "content_completeness": doc.content_completeness, "content_excerpt": doc.content[:700],
                "metadata": doc.metadata_json, "native": raw, "prediction": prediction})
        for adapter, cases in by_source.items():
            report["source_inventory"][adapter] = {"documents": len(cases), "ai_relevant": sum(c["ai_relevant"] for c in cases),
                                                   "category": cases[0]["category"]}
    selected = []
    for adapter, cases in sorted(by_source.items()):
        relevant = [case for case in cases if case["ai_relevant"]]
        pool = relevant or cases
        count = 12 if adapter == "osv" else 6 if adapter == "nvd" else 2
        ordered = sorted(pool, key=lambda item: hashlib.sha256((seed + item["document_id"]).encode()).hexdigest())
        if adapter == "osv":
            buckets = defaultdict(list)
            for case in ordered:
                names = sorted(x.get("package", {}).get("name", "") for x in case["native"].get("affected", []))
                buckets["|".join(names)].append(case)
            ordered = [items[index] for index in range(max(map(len, buckets.values()), default=0))
                       for _, items in sorted(buckets.items()) if index < len(items)]
        selected.extend(ordered[:count])
    report["sample"] = {"seed": seed, "documents": len(selected), "per_adapter": dict(Counter(c["adapter"] for c in selected)),
        "selection": "Deterministic per-adapter sample, AI-relevant preferred; OSV package strata; not population random gold",
        "fingerprint": hashlib.sha256(canonical([[c["document_id"], c["content_hash"]] for c in selected]).encode()).hexdigest()}
    return report, selected


class Upstream:
    def __init__(self):
        settings = get_settings()
        proxy = settings.outbound_proxy.get_secret_value() if settings.outbound_proxy else None
        self.transport = Transport(proxy=proxy)
        self.cache = {}

    def get(self, url, params=None):
        parsed = urlsplit(url)
        if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS or parsed.username or parsed.password:
            raise ValueError("audit_url_not_allowlisted")
        cache_key = url + canonical(params or {})
        if cache_key not in self.cache:
            self.transport.allowed_urls.add(url)
            status, headers, payload = self.transport.request("GET", url, params=params)
            if 300 <= status < 400:
                raise ValueError("redirect_requires_separate_review")
            self.cache[cache_key] = payload
        return self.cache[cache_key]

    def check(self, case):
        adapter, identifier = case["adapter"], case["external_id"]
        result = {key: case[key] for key in ("document_id", "adapter", "external_id", "source_url", "content_hash", "title", "ai_relevant", "content_completeness")}
        result["checked_at"] = datetime.now(timezone.utc).isoformat()
        raw, live, identity = case["native"], None, False
        if adapter == "osv":
            url = "https://api.osv.dev/v1/vulns/" + quote(identifier, safe="")
            payload = self.get(url)
            live = json.loads(payload)
            identity = live.get("id") == identifier
            modified = live.get("modified")
        elif adapter == "nvd":
            url = "https://services.nvd.nist.gov/rest/json/cves/2.0"
            payload = self.get(url, {"cveId": identifier})
            records = json.loads(payload).get("vulnerabilities", [])
            live = next((row["cve"] for row in records if row.get("cve", {}).get("id") == identifier), None)
            identity = live is not None
            modified = live.get("lastModified") if live else None
            if isinstance(modified, str) and not modified.endswith(("Z", "+00:00")):
                modified += "Z"
        elif adapter == "crossref":
            url = "https://api.crossref.org/works/" + quote(identifier, safe="")
            payload = self.get(url)
            live = json.loads(payload).get("message", {})
            identity = live.get("DOI", "").casefold() == identifier.casefold()
            expected_title = plain(" ".join(live.get("title", [])))[:500]
            result["title_matches"] = expected_title == case["title"]
        elif adapter in {"govuk", "govuk_search"}:
            url = "https://www.gov.uk/api/content" + urlsplit(case["source_url"]).path
            payload = self.get(url)
            live = json.loads(payload)
            identity = live.get("base_path") == urlsplit(case["source_url"]).path
            result["title_matches"] = live.get("title") == case["title"]
        elif adapter == "nvidia":
            url = "https://www.nvidia.com/content/dam/en-zz/Solutions/product-security/product-security.json"
            payload = self.get(url)
            rows = json.loads(payload).get("data", [])
            live = next((row for row in rows if str(row.get("bulletin id")) == identifier), None)
            identity = live is not None
            if live:
                result["catalogue_fields_match"] = all(raw.get(key) == live.get(key) for key in
                    ("title", "cve identifier(s)", "severity", "publish date", "last updated"))
                result["bulletin_full_text_verified"] = False
        elif adapter == "huggingface":
            topic_id = (raw.get("id") if isinstance(raw, dict) else None) or case["metadata"].get("topic_id")
            if topic_id is None:
                match = re.search(r"/(\d+)/?$", urlsplit(case["source_url"]).path)
                topic_id = match.group(1) if match else None
            if not str(topic_id).isdigit():
                raise ValueError("topic_id_missing")
            url = "https://discuss.huggingface.co/t/" + str(topic_id) + ".json"
            payload = self.get(url)
            live = json.loads(payload)
            identity = str(live.get("id")) == str(topic_id)
        else:
            url = case["source_url"]
            payload = self.get(url)
            if payload.startswith(b"%PDF"):
                result.update(status="downloaded_pdf_not_semantically_checked", endpoint=url,
                              response_sha256=hashlib.sha256(payload).hexdigest())
                return result
            body = plain(payload.decode("utf-8", errors="replace"))
            title = " ".join(case["title"].split())
            identity = title.casefold() in body.casefold()
            excerpt = " ".join(case["content_excerpt"].split())[:160]
            result["saved_excerpt_found"] = bool(excerpt) and excerpt.casefold() in body.casefold()
        result.update(endpoint=url, response_sha256=hashlib.sha256(payload).hexdigest(), identity_confirmed=identity)
        if not identity:
            result["status"] = "identity_not_confirmed_requires_review"
        elif adapter in {"osv", "nvd"}:
            compared = compare_fields(native_fields(adapter, live), case["prediction"])
            result["fields"] = compared
            latest, stored = timestamp(modified), timestamp(case["source_updated_at"])
            result["upstream_modified_at"] = latest.isoformat() if latest else None
            result["stored_modified_at"] = case["source_updated_at"]
            mismatches = [field for field, values in compared.items() if not values["matches"]]
            result["disagreeing_fields"] = mismatches
            result["status"] = "native_fields_agree" if not mismatches else (
                "source_updated_since_collection" if latest and stored and latest > stored else "field_disagreement_requires_review")
        else:
            result["status"] = "identity_confirmed_partial_check"
        return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--seed", default="accuracy-audit-20261012-v1")
    parser.add_argument("--self-check", action="store_true")
    args = parser.parse_args()
    self_check()
    if args.self_check:
        print("CVSS v3 base self-checks: 6 passed; no database/network/model access")
        return 0
    report, cases = load_snapshot(args.seed)
    report.update(measured_at=datetime.now(timezone.utc).isoformat(), references=REFERENCES,
        database_writes=0, human_labels_created=0, model_calls=0,
        limitations=["Native source agreement is not independent factual truth or human-labelled accuracy",
            "A bounded, stratified upstream sample cannot estimate full-corpus accuracy or recall",
            "V3 base scores only; CVSS v2/v4, environmental scoring and version applicability are not recomputed",
            "HTTP/title identity checks do not verify every statement or every linked fix/POC", 
            "Network failures and upstream modifications are separate from confirmed data errors"])
    report["upstream_checks"] = []
    upstream = Upstream() if not args.offline else None
    try:
        for index, case in enumerate(cases, 1):
            if upstream is None:
                break
            try:
                result = upstream.check(case)
            except httpx.HTTPStatusError as error:
                result = {key: case[key] for key in ("document_id", "adapter", "external_id", "source_url")}
                result.update(status="source_http_failure", http_status=error.response.status_code)
            except (httpx.TimeoutException, httpx.NetworkError, httpx.ProxyError) as error:
                result = {key: case[key] for key in ("document_id", "adapter", "external_id", "source_url")}
                result.update(status="network_failure", error_type=type(error).__name__)
            except Exception as error:
                result = {key: case[key] for key in ("document_id", "adapter", "external_id", "source_url")}
                result.update(status="not_verified", error_type=type(error).__name__)
            report["upstream_checks"].append(result)
            print(json.dumps({"progress": f"{index}/{len(cases)}", "adapter": case["adapter"], "status": result["status"]}), flush=True)
    finally:
        if upstream:
            upstream.transport.close()
    report["upstream_summary"] = dict(Counter(row["status"] for row in report["upstream_checks"]))
    report["selected_documents"] = [{key: case[key] for key in ("document_id", "adapter", "external_id", "source_url",
        "content_hash", "title", "ai_relevant", "document_type", "content_completeness", "content_excerpt")} for case in cases]
    folder = ROOT / "output/enrichment-quality"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("accuracy-audit-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ") + ".json")
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(json.dumps({"report": str(path), "integrity": report["integrity"], "native_field_agreement": report["native_field_agreement"],
        "cvss_base_check": report["cvss_base_check"], "sample": report["sample"], "upstream_summary": report["upstream_summary"],
        "human_review_records": report["human_review_records"], "database_writes": 0, "model_calls": 0}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        raise SystemExit("Accuracy audit could not finish: " + type(error).__name__ + "; no secrets printed") from None
