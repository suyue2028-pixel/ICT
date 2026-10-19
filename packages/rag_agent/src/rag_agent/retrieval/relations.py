from .exact import matches


def related_evidence(snapshot, evidence, filters, target_ids=None, limit=8):
    seed_docs = {e.document_id for e in evidence}
    confirmed = [r for r in snapshot.relations if r.verification == "source_recorded" and r.origin != "llm_candidate"]
    entities = {r.object for r in confirmed if r.subject in seed_docs}
    candidates = {r.subject for r in confirmed if r.object in entities} - seed_docs
    candidates = {d for d in candidates if d in snapshot.documents and matches(d, snapshot.documents[d], filters, snapshot)}
    if target_ids:
        candidates = {d for d in candidates if set(target_ids).intersection(i.value.upper() for i in snapshot.documents[d].identifiers)}
    return [snapshot.evidence[c["chunk_id"]] for c in snapshot.chunks if c["document_id"] in candidates][:limit]
