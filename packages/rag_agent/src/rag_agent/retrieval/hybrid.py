import re
import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from .exact import matches

jieba.setLogLevel(30)
TERM = re.compile(r"(?:cve-\d{4}-\d{4,}|ghsa-[\w-]+|[a-z0-9][a-z0-9_.:+/-]*)|[\u4e00-\u9fff]+", re.I)
STOPWORDS = set("的 了 是 有 有哪些 什么 怎么 如何 请 根据 来源 说明 相关 情报 风险 安全 一个 这个 哪些 有什么 以及 和 与 对 在 我 你 the a an of to in and or is are what how security risk risks information please".split())
# Domain vocabulary, not question-specific answers. Preserve original query for generation.
CONCEPTS = {
    "访问控制": ("access control", "authorization", "authentication", "权限", "鉴权", "认证"),
    "工具审计": ("tool audit", "audit log", "audit trail", "auditability", "auditing", "审计日志"),
    "智能体": ("agent", "agents", "智能体"),
    "提示注入": ("prompt injection", "提示词注入"),
    "数据投毒": ("data poisoning", "dataset poisoning", "model backdoor", "backdoors", "训练数据投毒"),
    "供应链": ("supply chain",),
    "越权": ("privilege escalation", "unauthorized", "authorization bypass"),
    "数据泄露": ("data leakage", "information disclosure", "信息泄露"),
}

def expand_query(query):
    return query + " " + " ".join(terms[0] for concept, terms in CONCEPTS.items() if concept in query)


def query_concepts(query):
    """Apply the same topical constraints to English and Chinese queries."""
    text = query.casefold()
    return [terms + (concept,) for concept, terms in CONCEPTS.items()
            if any(contains_term(text, term) for term in terms + (concept,))]


def contains_term(text, term):
    if term.isascii():
        return bool(re.search(r"(?<![a-z0-9])" + re.escape(term.casefold()) + r"(?![a-z0-9])", text))
    return term in text


def tokenize(text):
    terms = []
    for match in TERM.finditer(text.casefold()):
        token = match.group()
        if re.match(r"[\u4e00-\u9fff]", token):
            terms.extend(t for t in jieba.lcut(token) if t.strip())
        else:
            terms.append(token)
    return [t for t in terms if t not in STOPWORDS]


class HybridRetriever:
    def __init__(self, embedding):
        self.embedding = embedding
        self._generation = None
        self._cache_key = None
        self._matrix = None

    async def search(self, snapshot, query, filters, limit=8, diagnostics=None):
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
        original_query = query
        query = expand_query(query)
        query_tokens = tokenize(query)
        if not query_tokens:
            if diagnostics is not None:
                diagnostics.update(candidate_count=0, selected_count=0, scoped_chunks=len(indices))
            return []
        corpus = [tokenize(snapshot.documents[snapshot.chunks[i]["document_id"]].title + " " + snapshot.chunks[i]["text"]) or ["__empty__"] for i in indices]
        scores = BM25Okapi(corpus).get_scores(query_tokens)
        overlaps = [bool(set(query_tokens).intersection(t)) for t in corpus]
        # A compound topic needs evidence for every stated concept. A generic
        # agent mention alone cannot establish tool auditing or access control.
        concepts = query_concepts(original_query)
        topical = []
        for index in indices:
            chunk = snapshot.chunks[index]
            text = (snapshot.documents[chunk["document_id"]].title + " " + chunk["text"]).casefold()
            topical.append(all(any(contains_term(text, term) for term in group) for group in concepts))
        lexical = sorted((i for i in range(len(indices)) if overlaps[i] and topical[i]), key=lambda i: (scores[i], -i), reverse=True)[:20]
        vector = await self.embedding.query(query)
        candidate_matrix = matrix[indices]
        norm = np.linalg.norm(candidate_matrix, axis=1) * np.linalg.norm(vector)
        cosine = np.divide(candidate_matrix @ vector, norm, out=np.zeros(len(indices), dtype=np.float32), where=norm != 0)
        # Mock embeddings cannot authorize a semantic-only hit.
        threshold = 0.78 if self.embedding.mode == "live" else 1.01
        dense = sorted((i for i in range(len(indices)) if topical[i] and (overlaps[i] or cosine[i] >= threshold)), key=lambda i: float(cosine[i]), reverse=True)[:20]
        rrf = {}
        for ranking in (lexical, dense):
            for rank, i in enumerate(ranking, 1):
                rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
        winners, per_document = [], {}
        for i in sorted(rrf, key=lambda i: (rrf[i], float(cosine[i])), reverse=True):
            doc_id = snapshot.chunks[indices[i]]["document_id"]
            if per_document.get(doc_id, 0) >= 2:
                continue
            winners.append(i)
            per_document[doc_id] = per_document.get(doc_id, 0) + 1
            if len(winners) == limit:
                break
        if diagnostics is not None:
            diagnostics.update(candidate_count=len(rrf), selected_count=len(winners), scoped_chunks=len(indices))
        return [snapshot.evidence[snapshot.chunks[indices[i]]["chunk_id"]] for i in winners]
