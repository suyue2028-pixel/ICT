import re
import jieba
import numpy as np
from rank_bm25 import BM25Okapi
from .exact import matches

jieba.setLogLevel(30)
TERM = re.compile(r"(?:cve-\d{4}-\d{4,}|ghsa-[\w-]+|[a-z0-9][a-z0-9_.:+/-]*)|[\u4e00-\u9fff]+", re.I)
STOPWORDS = set("的 了 是 有 有哪些 什么 怎么 如何 请 根据 来源 说明 相关 情报 风险 安全 一个 这个 哪些 有什么 以及 和 与 对 在 我 你 吗 呢 能 会 问题 the a an of to in and or is are what how security risk risks information please".split())
# Domain vocabulary, not question-specific answers. Preserve original query for generation.
CONCEPTS = {
    "访问控制": ("access control", "authorization", "authentication", "permissions", "permission", "权限", "鉴权", "认证", "访问控制"),
    "工具审计": ("tool audit", "audit log", "audit trail", "auditability", "auditing", "audit", "审计日志", "调用日志", "审计"),
    "工具调用": ("tool calling", "tool invocation", "tool use", "tool execution", "tool", "tools", "toolkit", "工具调用", "工具执行", "工具使用", "工具"),
    "智能体": ("agent", "agents", "智能体", "智能代理"),
    "提示注入": ("prompt injection", "提示词注入"),
    "数据投毒": ("data poisoning", "dataset poisoning", "model backdoor", "backdoors", "训练数据投毒"),
    "供应链": ("supply chain",),
    "越权": ("privilege escalation", "unauthorized", "authorization bypass"),
    "数据泄露": ("data leakage", "information disclosure", "信息泄露"),
}

# These retrieve possible supporting cases, not interchangeable audit claims.
# The answer stage must assess them against the unchanged original question.
AUDIT_NEIGHBOURS = (
    "access control", "permissions", "file access", "file system", "file management",
    "filesystem", "path traversal", "arbitrary file", "访问权限", "文件访问", "路径穿越",
)


def concept_names(query):
    text = query.casefold()
    return [concept for concept, terms in CONCEPTS.items()
            if any(contains_term(text, term) for term in terms + (concept,))]


def adjacent_terms(query):
    concepts = concept_names(query)
    return AUDIT_NEIGHBOURS if "工具审计" in concepts and any(
        name in concepts for name in ("智能体", "工具调用")) else ()

def expand_query(query):
    # Lexical translation uses bounded domain aliases; it changes no filters.
    terms = [term for concept in concept_names(query) for term in CONCEPTS[concept]]
    return query + " " + " ".join(dict.fromkeys([*terms, *adjacent_terms(query)]))


def query_concepts(query):
    """Recognized concepts are ranking features, never hard topic filters."""
    return [CONCEPTS[concept] + (concept,) for concept in concept_names(query)]


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
    def __init__(self, embedding, *, rank_pool=32, vector_threshold=0.78):
        if type(rank_pool) is not int or not 8 <= rank_pool <= 100:
            raise ValueError("retrieval rank pool must be between 8 and 100")
        if isinstance(vector_threshold, bool) or not 0 <= vector_threshold <= 1:
            raise ValueError("retrieval vector threshold must be between zero and one")
        self.embedding = embedding
        self.rank_pool = rank_pool
        self.vector_threshold = vector_threshold
        self._generation = None
        self._cache_key = None
        self._matrix = None

    async def search(self, snapshot, query, filters, limit=8, diagnostics=None):
        counts = diagnostics if diagnostics is not None else {}
        counts.update(keyword_candidate_count=0, vector_candidate_count=0,
                      merged_candidate_count=0, candidate_count=0, selected_count=0,
                      scoped_chunks=0, retrieval_strategy=f"bm25+{self.embedding.mode}_vector_rrf_soft_topics")
        if type(limit) is not int or limit < 1:
            raise ValueError("retrieval limit must be a positive integer")
        limit = min(limit, 8)
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
        counts["scoped_chunks"] = len(indices)
        if not indices:
            counts["retrieval_strategy"] += ":empty_scope"
            return []
        original_query = query
        query = expand_query(query)
        query_tokens = tokenize(query)
        if not query_tokens:
            counts["retrieval_strategy"] += ":empty_terms"
            return []
        texts = [(snapshot.documents[snapshot.chunks[i]["document_id"]].title + " " + snapshot.chunks[i]["text"]).casefold()
                 for i in indices]
        corpus = [tokenize(text) or ["__empty__"] for text in texts]
        scores = BM25Okapi(corpus).get_scores(query_tokens)
        names = concept_names(original_query)
        concepts = query_concepts(original_query)
        neighbours = adjacent_terms(original_query)
        original_tokens = set(tokenize(original_query))
        features, overlaps = [], []
        for text, tokens in zip(texts, corpus):
            hits = [any(contains_term(text, term) for term in group) for group in concepts]
            # Agent/tool mentions are weaker than the requested mechanism. A
            # direct audit/access-control hit should outrank a generic mention.
            specificity = sum(hit for name, hit in zip(names, hits) if name not in {"智能体", "工具调用"})
            broad = sum(hit for name, hit in zip(names, hits) if name in {"智能体", "工具调用"})
            neighbour_hit = bool(neighbours and any(contains_term(text, term) for term in neighbours))
            features.append((specificity, broad, neighbour_hit))
            # Whole aliases avoid authorizing keyword hits on a fragment such as
            # "control" from the expanded phrase "access control" alone.
            overlaps.append(bool(original_tokens.intersection(tokens)) or any(hits) or neighbour_hit)
        lexical = sorted((i for i in range(len(indices)) if overlaps[i]),
                         key=lambda i: (features[i][0] + 0.6 * features[i][2], float(scores[i]), features[i][1], -i),
                         reverse=True)[:self.rank_pool]
        counts["keyword_candidate_count"] = len(lexical)
        # Keep the multilingual query intact for E5; a small canonical suffix
        # assists terminology without filling the embedding with every alias.
        semantic_query = original_query + " " + " ".join(CONCEPTS[name][0] for name in names)
        if neighbours:
            semantic_query += " tool permissions file access"
            counts["retrieval_strategy"] += ":bounded_audit_neighbours"
        vector = await self.embedding.query(semantic_query)
        candidate_matrix = matrix[indices]
        norm = np.linalg.norm(candidate_matrix, axis=1) * np.linalg.norm(vector)
        cosine = np.divide(candidate_matrix @ vector, norm, out=np.zeros(len(indices), dtype=np.float32), where=norm != 0)
        # Mock embeddings cannot authorize a semantic-only hit.
        threshold = self.vector_threshold if self.embedding.mode == "live" else 1.01
        dense = sorted((i for i in range(len(indices)) if overlaps[i] or cosine[i] >= threshold),
                       key=lambda i: (float(cosine[i]), -i), reverse=True)[:self.rank_pool]
        counts["vector_candidate_count"] = len(dense)
        rrf = {}
        for ranking in (lexical, dense):
            for rank, i in enumerate(ranking, 1):
                rrf[i] = rrf.get(i, 0) + 1 / (60 + rank)
        winners, per_document = [], {}
        # Soft boosts affect rank only. Candidate relevance and claim support
        # remain decisions of the Agent's assessment and verification stages.
        def rank_key(i):
            specific, broad, neighbour_hit = features[i]
            boosted = rrf[i] * (1 + 0.5 * specific + 0.1 * broad + 0.2 * neighbour_hit)
            return boosted, float(cosine[i]), -i

        for i in sorted(rrf, key=rank_key, reverse=True):
            doc_id = snapshot.chunks[indices[i]]["document_id"]
            if per_document.get(doc_id, 0) >= 2:
                continue
            winners.append(i)
            per_document[doc_id] = per_document.get(doc_id, 0) + 1
            if len(winners) == limit:
                break
        counts.update(merged_candidate_count=len(rrf), candidate_count=len(rrf), selected_count=len(winners))
        return [snapshot.evidence[snapshot.chunks[indices[i]]["chunk_id"]] for i in winners]
