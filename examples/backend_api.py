"""V1 service for backend integration; isolated from the standalone demo DB.

Run with ``python -m uvicorn examples.backend_api:app --host 127.0.0.1 --port 9001``.
Set RAG_SERVICE_TOKEN and use the same value as backend AGENT_SERVICE_TOKEN.
"""
import os
from pathlib import Path

from rag_agent.config import Settings
from rag_agent.integration.api import create_app
from rag_agent.integration.index import BackendIndex
from rag_agent.service import RAGService


def build_app():
    settings = Settings.from_env().model_copy(update={
        "db_path": Path(os.environ.get("RAG_BACKEND_DB_PATH", "data/backend-rag.db")),
    })
    service = RAGService(settings)
    return create_app(service, BackendIndex(service), os.environ.get("RAG_SERVICE_TOKEN"))


app = build_app()
