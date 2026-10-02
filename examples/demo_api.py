"""Loopback-only development adapter. Public API/session/auth belong to backend."""
from functools import lru_cache
from fastapi import FastAPI, HTTPException
from rag_agent.config import Settings
from rag_agent.schemas import QueryRequest, QueryResponse, EvidenceDetail
from rag_agent.service import RAGService

app = FastAPI(title="ICT RAG demo adapter", version="0.1.0")


@lru_cache
def service():
    return RAGService(Settings.from_env())


@app.get("/health")
async def health():
    s = service()
    snapshot = s.store.snapshot()
    return {"status": "ready", "documents": len(snapshot.documents), "generation": snapshot.generation,
            "llm_mode": s.settings.llm_mode, "embedding_provider": s.settings.embedding_provider}


@app.post("/demo/query", response_model=QueryResponse)
async def query(request: QueryRequest):
    return await service().query(request)


@app.get("/demo/evidence/{evidence_id}", response_model=EvidenceDetail)
async def evidence(evidence_id: str):
    try:
        return await service().get_evidence(evidence_id)
    except KeyError:
        raise HTTPException(404, "evidence not found") from None
