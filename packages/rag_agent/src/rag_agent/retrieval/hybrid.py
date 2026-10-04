import re
import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from .exact import matches

jieba.setLogLevel(30)
TERM = re.compile(r"(?:cve-\d{4}-\d{4,}|ghsa-[\w-]+|[a-z0-9][a-z0-9_.:+/-]*)|[\u4e00-\u9fff]+", re.I)


def tokenize(text):
    terms = []
    for match in TERM.finditer(text.casefold()):
        token = match.group()
        if re.match(r"[\u4e00-\u9fff]", token):
            terms.extend(t for t in jieba.lcut(token) if t.strip())
        else:
            terms.append(token)
    return terms


class HybridRetriever:
    def __init__(self, embedding):
        self.embedding = embedding
        self._generation = None
        self._cache_key = None
        self._matrix = None

    async def search(self, snapshot, query, filters, limit=8):
        if snapshot.signature and snapshot.signature != self.embedding.signature():
            raise ValueError("query embedding does not match index; rebuild required")
        # Demo/real queries can have different matrices at the same generation.
        cache_key = (snapshot.generation, tuple(c["chunk_id"] for c in snapshot.chunks))
        if self._cache_key != cache_key:
            self._matrix = np.stack([c["vector"] for c in snapshot.chunks]) if snapshot.chunks else np.empty((0, self.embedding.dimension), dtype=np.float32)
            self._generation = snapshot.generation
            self._cache_key = cache_key
        # Retain a local reference: another request may replace the cache later.
        matrix = self._matrix
        indices = [i for i, c in enumerate(snapshot.chunks) if matches(c["document_id"], snapshot.documents[c["document_id"]], filters, snapshot)]
        if not indices:
            return []
        query_tokens = tokenize(query)
        corpus = [tokenize(snapshot.documents[snapshot.chunks[i]["document_id"]].title + " " + snapshot.chunks[i]["text"]) or ["__empty__"] for i in indices]
        scores = BM25Okapi(corpus).get_scores(query_tokens)
        overlaps = [bool(set(query_tokens).intersection(t)) for t in corpus]
        lexical = sorted((i for i in range(len(indices)) if overlaps[i]), key=lambda i: (scores[i], -i), reverse=True)[:20]
        vector = await self.embedding.query(query)
        candidate_matrix = matrix[indices]
        norm = np.linalg.norm(candidate_matrix, axis=1) * np.linalg.norm(vector)
        cosine = np.divide(candidate_matrix @ vector, norm, out=np.zeros(len(indices), dtype=np.float32), where=norm != 0)
        # Mock embeddings cannot authorize a semantic-only hit.
        threshold = 0.78 if self.embedding.mode == "live" else 1.01
        dense = sorted((i for i in range(len(indices)) if overlaps[i] or cosine[i] >= threshold), key=lambda i: float(cosine[i]), reverse=True)[:20]
        rrf = {}
        for ranking in (lexical, dense):
            for rank, i in enumerate(ranking, 1):
                rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
        winners = sorted(rrf, key=lambda i: (rrf[i], float(cosine[i])), reverse=True)[:limit]
        return [snapshot.evidence[snapshot.chunks[indices[i]]["chunk_id"]] for i in winners]
