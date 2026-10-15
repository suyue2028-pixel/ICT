"""Bounded retrieval regressions; no model or network calls.

The public-source excerpts below were read from the existing local index.
Their use here checks candidate selection, not factual answer accuracy.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import numpy as np
import pytest

from rag_agent.retrieval.hybrid import HybridRetriever, concept_names, expand_query
from rag_agent.schemas import EvidenceDetail, Filters


class ConstantEmbedding:
    """Deterministic ranking double, explicitly not a semantic model."""
    mode = 'mock'
    dimension = 2

    async def query(self, text):
        return np.array([1.0, 0.0], dtype=np.float32)


def snapshot(records):
    documents, chunks, evidence = {}, [], {}
    for index, (url, excerpt) in enumerate(records):
        doc_id = f'synthetic-document-{index}'
        chunk_id = f'{doc_id}:v1:c0'
        documents[doc_id] = SimpleNamespace(title='Synthetic file access record',
            source_url=url, identifiers=[], product_entities=[])
        chunks.append({'document_id':doc_id, 'chunk_id':chunk_id,
            'text':'Synthetic shared file access record.',
            'vector':np.array([1.0, 0.0], dtype=np.float32)})
        evidence[chunk_id] = EvidenceDetail(evidence_id=chunk_id,
            document_id=doc_id, version=1, title=documents[doc_id].title,
            url=url, excerpt=excerpt, locator={'kind':'synthetic_fixture'},
            collected_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
    return SimpleNamespace(generation=1, signature=None, documents=documents,
        chunks=chunks, evidence=evidence, assertions=[])


async def test_mirrored_candidates_do_not_consume_the_eight_slot_cutoff():
    records = [('https://example.org/synthetic-mirror/', 'same   synthetic source'),
               ('https://example.org/synthetic-mirror', 'same synthetic source')]
    records += [(f'https://example.org/synthetic-{i}', f'synthetic source {i}')
                for i in range(7)]
    data = snapshot(records)
    counts = {}
    result = await HybridRetriever(ConstantEmbedding()).search(data, 'file access',
        Filters(), diagnostics=counts)
    assert len(result) == 8 and counts['selected_count'] == 8
    assert len({(item.url.rstrip('/'), ' '.join(item.excerpt.split()))
                for item in result}) == 8
    assert any(item.excerpt == 'synthetic source 6' for item in result)


async def test_same_source_distinct_excerpts_and_different_sources_are_retained():
    data = snapshot([
        ('https://example.org/synthetic-a', 'synthetic condition A'),
        ('https://example.org/synthetic-a', 'synthetic condition B'),
        ('https://example.org/synthetic-b', 'synthetic condition A'),
    ])
    result = await HybridRetriever(ConstantEmbedding()).search(data, 'file access', Filters())
    assert len(result) == 3
    assert {item.excerpt for item in result} == {'synthetic condition A', 'synthetic condition B'}
    assert len({item.url for item in result}) == 2


async def test_existing_public_excerpt_mirror_leaves_room_for_other_document():
    langchain = 'Langchain 0.0.171 is vulnerable to Arbitrary code execution in `load_prompt`.'
    owasp = 'The root cause of Excessive Agency is typically one or more of: excessive functionality; excessive permissions; excessive autonomy.'
    data = snapshot([
        ('https://osv.dev/vulnerability/GHSA-6643-h7h5-x9wh', langchain),
        ('https://osv.dev/vulnerability/GHSA-6643-h7h5-x9wh', langchain),
        ('https://genai.owasp.org/llmrisk/llm062025-excessive-agency/', owasp),
    ])
    result = await HybridRetriever(ConstantEmbedding()).search(data, 'file access', Filters(), limit=2)
    assert len(result) == 2
    assert {item.excerpt for item in result} == {langchain, owasp}
    assert all(item is data.evidence[item.evidence_id] for item in result)


@pytest.mark.parametrize('query,concept,aliases',[
    ('过度代理有哪些风险？', '过度代理', ('excessive agency',)),
    ('Excessive Agency', '过度代理', ('过度代理',)),
    ('LangChain 文件访问漏洞', '文件访问', ('file access', 'path traversal')),
    ('LangChain 路径穿越', '文件访问', ('file access', 'directory traversal')),
])
def test_domain_aliases_expand_retrieval_expression_only(query,concept,aliases):
    assert concept in concept_names(query)
    expanded = expand_query(query)
    assert expanded.startswith(query + ' ')
    assert all(alias in expanded for alias in aliases)
    assert 'CVE-' not in expanded and 'https://' not in expanded


def test_new_domain_aliases_do_not_match_unrelated_everyday_questions():
    assert not set(concept_names('我喜欢你，今天吃什么？')).intersection({'过度代理','文件访问'})
