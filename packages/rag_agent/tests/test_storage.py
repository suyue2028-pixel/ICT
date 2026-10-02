import asyncio
from datetime import timedelta
import json
import numpy as np
import pytest
from rag_agent.ingestion.normalize import stable_id
from rag_agent.providers.mock import MockEmbedding, CharacterTokenizer
from rag_agent.ingestion.chunk import split_document
from rag_agent.schemas import QueryRequest


async def test_collection_is_idempotent(service, documents):
    before = service.store.snapshot().generation
    updated_collection = documents[0].model_copy(update={"collected_at": documents[0].collected_at + timedelta(days=1)})
    result = await service.ingest([updated_collection])
    assert result.unchanged == 1
    assert service.store.snapshot().generation == before


async def test_version_preserves_old_evidence(service, documents):
    doc_id = stable_id(documents[0])
    old = await service.get_evidence(doc_id + ":v1:c0")
    changed = documents[0].model_copy(update={"text": "Updated source text: new material. Fixed in 2.0.0."})
    result = await service.ingest([changed])
    assert result.updated == 1 and result.items[0].version == 2
    assert (await service.get_evidence(old.evidence_id)).excerpt == old.excerpt
    response = await service.query(QueryRequest(question="CVE-2099-1234"))
    assert all(e.version == 2 for e in response.sources if e.document_id == doc_id)
    assert service.retriever._generation is None  # exact lookup does not build vector cache


async def test_failed_embedding_does_not_replace_current(service, documents):
    class Broken(MockEmbedding):
        async def passages(self, texts):
            raise RuntimeError("sensitive provider body")
    service.embedding = Broken()
    changed = documents[0].model_copy(update={"text": "changed source"})
    result = await service.ingest([changed])
    assert result.failed == 1
    assert "sensitive" not in result.items[0].error
    assert service.store.current(stable_id(changed))["version"] == 1


async def test_model_mismatch_rejected_even_for_unchanged(service, documents):
    service.embedding = MockEmbedding("different-model")
    result = await service.ingest(documents[:1])
    assert result.failed == 1 and "mismatch" in result.items[0].error
    response = await service.query(QueryRequest(question="CVE-2099-1234"))
    assert response.status == "error"


def test_float32_not_pickle(service):
    with service.store.connection() as c:
        row = c.execute("SELECT vector,dimension,model_id,revision,prefix_policy FROM embeddings LIMIT 1").fetchone()
    assert len(row["vector"]) == row["dimension"] * 4
    assert np.isfinite(np.frombuffer(row["vector"], dtype="<f4")).all()
    assert row["prefix_policy"] == "mock-only-v1"


async def test_nvd_v4_and_boolean_boundaries_preserved(service, documents):
    raw_conditions = [{"operator": "AND", "negate": False, "nodes": [{"operator": "OR", "cpeMatch": [{"vulnerable": True, "criteria": "synthetic-cpe", "versionStartIncluding": "1.0", "versionEndExcluding": "2.0"}]}]}]
    doc = documents[2].model_copy(update={"raw_payload": {"cve": {"metrics": {"cvssMetricV40": [{"source": "synthetic", "type": "Primary", "cvssData": {"version": "4.0", "baseScore": 9.8, "vectorString": "SYNTHETIC"}}]}, "configurations": raw_conditions}}})
    await service.ingest([doc])
    snap = service.store.snapshot()
    doc_id = stable_id(doc)
    assertions = [a for a in snap.assertions if a.document_id == doc_id]
    cvss = next(a for a in assertions if a.field == "cvss")
    affected = next(a for a in assertions if a.field == "affected_version")
    assert cvss.value["version"] == "4.0" and cvss.value["score"] == 9.8
    assert affected.value["parsed"] is False and affected.value["raw_conditions"] == raw_conditions
    field_ev = snap.evidence[affected.evidence_ids[0]]
    assert json.loads(field_ev.excerpt) == raw_conditions


def test_chunk_offsets_cover_without_truncation():
    text = "中文内容A " * 250
    chunks = split_document(text, "短标题", CharacterTokenizer(), 100, 20)
    assert chunks[-1].end == len(text)
    assert all(c.text == text[c.start:c.end] for c in chunks)
    assert all(chunks[i].start <= chunks[i - 1].end for i in range(1, len(chunks)))
    assert all(len("passage: 短标题\n" + c.text) + 2 <= 512 for c in chunks)


def test_title_budget_not_silently_truncated():
    with pytest.raises(ValueError, match="title"):
        split_document("正文", "长" * 510, CharacterTokenizer())


async def test_snapshot_is_immutable_across_update(service, documents):
    old = service.store.snapshot()
    doc_id = stable_id(documents[0])
    await service.ingest([documents[0].model_copy(update={"text": "Changed!"})])
    new = service.store.snapshot()
    assert old.versions[doc_id] == 1 and new.versions[doc_id] == 2
    assert old.generation < new.generation
    assert old.documents[doc_id].text != new.documents[doc_id].text


async def test_concurrent_import_serializes_versions(service, documents):
    updated = documents[0].model_copy(update={"text": "a concurrent update"})
    results = await asyncio.gather(service.ingest([updated]), service.ingest([updated]))
    assert sum(r.updated for r in results) == 1
    assert sum(r.unchanged for r in results) == 1


async def test_enrich_idempotent(service):
    ids = list(service.store.snapshot().documents)
    result = await service.enrich(ids)
    assert result.relations_added == 0 and result.relations_retained == 6
    assert result.candidates == 0
