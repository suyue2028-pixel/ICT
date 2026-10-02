"""Versioned embeddings and exact semantic retrieval in the existing PostgreSQL.

No test/fallback vectors are generated in production. A configured cloud embedding
provider must return real, finite vectors before an event can become indexed.
"""

import math
import time
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from backend.models import Document, EmbeddingChunk

CHUNK_SIZE = 900
CHUNK_OVERLAP = 100
BATCH_SIZE = 10


class EmbeddingError(Exception):
    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class TextChunk:
    index: int
    start: int
    end: int
    excerpt: str


@dataclass(frozen=True)
class SemanticHit:
    document_id: UUID
    content_hash: str
    source_url: str
    excerpt: str
    chunk_index: int
    score: float


def split_text(source: str) -> list[TextChunk]:
    chunks = []
    position = 0
    while position < len(source):
        end = min(position + CHUNK_SIZE, len(source))
        excerpt = source[position:end]
        if excerpt.strip():
            chunks.append(TextChunk(len(chunks), position, end, excerpt))
        if end == len(source):
            break
        position = end - CHUNK_OVERLAP
    return chunks


def embedding_endpoint(settings) -> str:
    if not settings.embedding_configured:
        raise EmbeddingError("embedding_not_configured")
    base_url = settings.embedding_base_url.rstrip("/")
    parsed = urlsplit(base_url)
    host = (parsed.hostname or "").lower()
    allowed = host in {"dashscope.aliyuncs.com", "dashscope-intl.aliyuncs.com"} or host.endswith((
        ".cn-beijing.maas.aliyuncs.com", ".ap-southeast-1.maas.aliyuncs.com",
    ))
    if (parsed.scheme != "https" or not allowed or parsed.username or parsed.password
            or parsed.port or parsed.query or parsed.fragment
            or parsed.path != "/compatible-mode/v1" or "your_workspace_id" in host):
        raise EmbeddingError("embedding_base_url_invalid")
    if settings.embedding_model != "text-embedding-v4" or settings.embedding_dimensions not in {
            64, 128, 256, 512, 768, 1024, 1536, 2048}:
        raise EmbeddingError("embedding_model_config_invalid")
    return base_url + "/embeddings"


def embed_texts(texts: list[str], settings) -> list[list[float]]:
    """Call the official Model Studio OpenAI-compatible embeddings endpoint."""
    if not texts:
        return []
    endpoint = embedding_endpoint(settings)
    result = []
    for offset in range(0, len(texts), BATCH_SIZE):
        batch = texts[offset:offset + BATCH_SIZE]
        for attempt in range(3):
            try:
                response = httpx.post(endpoint, headers={
                    "Authorization": f"Bearer {settings.embedding_api_key.get_secret_value()}",
                    "Content-Type": "application/json",
                }, json={"model": settings.embedding_model, "input": batch,
                         "dimensions": settings.embedding_dimensions,
                         "encoding_format": "float"}, timeout=30)
            except httpx.RequestError:
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise EmbeddingError("embedding_network_error") from None
            if response.status_code in (401, 403):
                raise EmbeddingError("embedding_auth_failed")
            if response.status_code == 429 or response.status_code >= 500:
                if attempt < 2:
                    time.sleep(0.5 * (2 ** attempt))
                    continue
                raise EmbeddingError("embedding_provider_unavailable")
            if response.status_code >= 400:
                raise EmbeddingError("embedding_request_failed")
            break
        try:
            data = response.json()["data"]
            if not isinstance(data, list) or len(data) != len(batch):
                raise ValueError("wrong count")
            by_index = {item["index"]: item["embedding"] for item in data}
            if set(by_index) != set(range(len(batch))):
                raise ValueError("wrong index")
            vectors = []
            for index in range(len(batch)):
                raw = by_index[index]
                if (not isinstance(raw, list) or len(raw) != settings.embedding_dimensions
                        or any(type(value) not in (int, float) or not math.isfinite(value)
                               for value in raw)):
                    raise ValueError("bad vector")
                vector = [float(value) for value in raw]
                if not any(vector):
                    raise ValueError("zero vector")
                vectors.append(vector)
            result.extend(vectors)
        except (ValueError, KeyError, TypeError):
            raise EmbeddingError("embedding_invalid_response") from None
    return result


def store_chunks(db: Session, document: Document, chunks: list[TextChunk],
                 vectors: list[list[float]], settings) -> int:
    if not chunks or len(chunks) != len(vectors):
        raise EmbeddingError("embedding_count_mismatch")
    # Replace only this version/model; old versions remain available for audit.
    db.execute(delete(EmbeddingChunk).where(
        EmbeddingChunk.document_id == document.id,
        EmbeddingChunk.content_hash == document.content_hash,
        EmbeddingChunk.model == settings.embedding_model,
    ))
    for chunk, vector in zip(chunks, vectors, strict=True):
        db.add(EmbeddingChunk(document_id=document.id, content_hash=document.content_hash,
                              chunk_index=chunk.index, start_offset=chunk.start,
                              end_offset=chunk.end, excerpt=chunk.excerpt,
                              model=settings.embedding_model,
                              dimensions=settings.embedding_dimensions,
                              embedding=vector, is_demo=document.is_demo))
    db.flush()
    return len(chunks)


def search(db: Session, question: str, settings, is_demo: bool = False,
           document_ids: list[UUID] | None = None, limit: int = 5) -> list[SemanticHit]:
    """Exact cosine search over current indexed versions; suitable for this small corpus."""
    query = select(EmbeddingChunk, Document).join(Document, Document.id == EmbeddingChunk.document_id).where(
        EmbeddingChunk.content_hash == Document.content_hash,
        EmbeddingChunk.model == settings.embedding_model,
        EmbeddingChunk.dimensions == settings.embedding_dimensions,
        EmbeddingChunk.is_demo == is_demo,
        Document.is_demo == is_demo,
        Document.deleted_at.is_(None),
        Document.ai_relevant.is_(True),
    )
    if document_ids:
        query = query.where(Document.id.in_(document_ids))
    # Avoid a paid query embedding call when no real index exists.
    if db.execute(query.limit(1)).first() is None:
        return []
    query_vector = embed_texts([question], settings)[0]
    query_norm = math.sqrt(sum(value * value for value in query_vector))
    hits = []
    for chunk, document in db.execute(query).yield_per(100):
        vector = chunk.embedding
        norm = math.sqrt(sum(value * value for value in vector))
        if not norm:
            continue
        score = sum(left * right for left, right in zip(query_vector, vector, strict=True)) / (query_norm * norm)
        score = max(-1.0, min(1.0, score))
        hits.append(SemanticHit(document.id, document.content_hash, document.source_url,
                                chunk.excerpt, chunk.chunk_index, round(score, 6)))
    return sorted(hits, key=lambda item: item.score, reverse=True)[:limit]
