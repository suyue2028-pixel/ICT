from ..ingestion.extract import object_id
from ..schemas import Relation, EnrichResult


def enrich(store, document_ids):
    snapshot = store.snapshot()
    relations, entities, warnings = [], [], []
    retained_assertions = 0
    for doc_id in dict.fromkeys(document_ids):
        if doc_id not in snapshot.documents:
            warnings.append(f"document_not_found:{doc_id}")
            continue
        doc = snapshot.documents[doc_id]
        version = snapshot.versions[doc_id]
        entities.append((doc_id, "document", doc.title))
        retained_assertions += sum(a.document_id == doc_id for a in snapshot.assertions)
        for values, kind, pointer in [(doc.identifiers, "identifier", "/identifiers"), (doc.product_entities, "product", "/product_entities")]:
            ev_ids = [e.evidence_id for e in snapshot.evidence.values() if e.document_id == doc_id and e.locator.get("pointer") == pointer]
            for value in values:
                if kind == "identifier":
                    entity_id, label = f"{value.namespace}:{value.value}", value.value
                else:
                    entity_id, label = "product:" + value.casefold(), value
                entities.append((entity_id, kind, label))
                if not ev_ids:
                    continue
                relation = Relation(document_id=doc_id, version=version, subject=doc_id,
                                    predicate="mentions_" + kind, object=entity_id, evidence_ids=ev_ids)
                relation.relation_id = object_id("rel_", relation.model_dump(mode="json", exclude={"relation_id"}))
                relations.append(relation)
    added, retained = store.add_relations(relations, entities)
    return EnrichResult(assertions_retained=retained_assertions, relations_added=added,
                        relations_retained=retained, warnings=warnings)
