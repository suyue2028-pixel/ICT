"""Read-only derivation from recorded fields; no inference of safe/fixed versions.

CPE 2.3 formatted strings: https://csrc.nist.gov/publications/detail/ir/7695/final
NVD applicability trees are retained verbatim, not evaluated as an asset matcher.
"""
import re


def cpe_components(value):
    if not isinstance(value, str) or len(value) > 2000:
        return None
    parts, current, escaped = [], "", False
    for char in value:
        if escaped:
            current += "\\" + char
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == ":":
            parts.append(current)
            current = ""
        else:
            current += char
    parts.append(current)
    if escaped or len(parts) != 13 or parts[:2] != ["cpe", "2.3"] or parts[2] not in {"a", "o", "h"}:
        return None
    fields = ("part", "vendor", "product", "version", "update", "edition", "language",
              "sw_edition", "target_sw", "target_hw", "other")
    return {key: {"raw": item, "kind": "any" if item == "*" else "not_applicable" if item == "-" else "value",
                  "display": None if item in {"*", "-"} else re.sub(r"\\(.)", r"\1", item)}
            for key, item in zip(fields, parts[2:])}


def normalize_affected(value, raw_payload):
    items = []
    for index, match in enumerate(value.get("cpe_matches", [])):
        if not isinstance(match, dict):
            continue
        parts = cpe_components(match.get("criteria"))
        bounds = {key: match[key] for key in ("versionStartIncluding", "versionStartExcluding",
                  "versionEndIncluding", "versionEndExcluding") if key in match}
        items.append({"kind": "cpe", "state": "known" if parts else "unknown", "cpe": parts,
            "criteria": match.get("criteria"), "version_bounds": bounds,
            "locator": f"source_fields.affected.cpe_matches.{index}",
            "applicability": "not_evaluated", "fixed_version": None})
    # OSV ranges retain ordered introduced/fixed/last_affected/limit events.
    # An upper affected bound is never silently relabelled as a fixed release.
    for index, affected in enumerate(value.get("items", [])):
        if not isinstance(affected, dict):
            continue
        fixed = [e["fixed"] for r in affected.get("ranges", []) if isinstance(r, dict)
                 for e in r.get("events", []) if isinstance(e, dict) and isinstance(e.get("fixed"), str)]
        items.append({"kind": "osv", "state": "known", "package": affected.get("package"),
            "ranges": affected.get("ranges", []), "versions": affected.get("versions", []),
            "fixed_versions": list(dict.fromkeys(fixed)), "locator": f"source_fields.affected.items.{index}",
            "applicability": "not_evaluated"})
    raw = raw_payload.get("raw", {})
    configurations = raw.get("configurations") if isinstance(raw, dict) else None
    return {"state": "known" if items else "unknown", "items": items,
        "applicability_conditions": configurations,
        "limitation": "Parsed source naming/ranges only. Boolean/negated NVD conditions preserved; no asset matching, version ordering or claim that other versions are safe."}


def structured_enrichment(document, evidence_rows):
    source_rows = [e for e in evidence_rows if e.method == "source_reported"]
    affected = next((e for e in source_rows if e.field == "affected"), None)
    result = normalize_affected(affected.value if affected else {}, document.raw_payload)
    result.update(document_id=document.id, content_hash=document.content_hash, source_url=document.source_url,
        evidence_id=affected.id if affected else None, extraction_method="deterministic_source_parser_v1",
        review_state="source_recorded_not_independently_reviewed")
    # Every public field carries its original quote/locator rather than only a value.
    fields = {}
    for name in ("cwe", "cvss", "remediation"):
        e = next((e for e in source_rows if e.field == name), None)
        fields[name] = {"state": e.state if e else "unknown", "value": e.value if e else None,
            "evidence_id": e.id if e else None, "locator": e.locator if e else None,
            "excerpt": e.excerpt if e else None, "source_url": e.source_url if e else document.source_url,
            "content_hash": document.content_hash}
    return {"affected": result, **fields}
