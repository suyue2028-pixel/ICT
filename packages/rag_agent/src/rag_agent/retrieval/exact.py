def matches(doc_id, doc, filters, snapshot):
    ids = {i.value.upper() for i in doc.identifiers}
    if filters.identifier and not ids.intersection(filters.identifier):
        return False
    if filters.product and filters.product.casefold() not in {p.casefold() for p in doc.product_entities}:
        return False
    if filters.doc_types and doc.doc_type not in filters.doc_types:
        return False
    if filters.source_kinds and doc.source_kind not in filters.source_kinds:
        return False
    if filters.published_from and (doc.published_at is None or doc.published_at < filters.published_from):
        return False
    if filters.published_to and (doc.published_at is None or doc.published_at > filters.published_to):
        return False
    if filters.min_cvss_score is not None or filters.severity or filters.cvss_version:
        scores = [a.value for a in snapshot.assertions if a.document_id == doc_id and a.field == "cvss" and isinstance(a.value, dict)]
        if not any(
            (not filters.cvss_version or v.get("version") == filters.cvss_version)
            and (filters.min_cvss_score is None or isinstance(v.get("score"), (int, float)) and v["score"] >= filters.min_cvss_score)
            and (not filters.severity or str(v.get("severity") or "").upper() == filters.severity)
            for v in scores
        ):
            return False
    return True


def exact_lookup(snapshot, targets, filters, limit=8):
    groups, missing = [], []
    for target in targets:
        docs = {i for i, doc in snapshot.documents.items()
                if target.upper() in {x.value.upper() for x in doc.identifiers} and matches(i, doc, filters, snapshot)}
        group = [snapshot.evidence[c["chunk_id"]] for c in snapshot.chunks if c["document_id"] in docs]
        if not group:
            missing.append(target)
        groups.append(group)
    # Round robin prevents a long first document from consuming all target slots.
    result, seen = [], set()
    while any(groups) and len(result) < limit:
        for group in groups:
            if group:
                ev = group.pop(0)
                if ev.evidence_id not in seen:
                    result.append(ev)
                    seen.add(ev.evidence_id)
                    if len(result) == limit:
                        break
    return result, missing
