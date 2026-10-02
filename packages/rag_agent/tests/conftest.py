import json
from pathlib import Path
import pytest
import pytest_asyncio
from rag_agent.config import Settings
from rag_agent.schemas import DocumentInput
from rag_agent.service import RAGService


@pytest.fixture
def documents():
    raw = json.loads((Path(__file__).parent / "fixtures/documents.json").read_text())
    assert raw["synthetic"] is True
    return [DocumentInput.model_validate(d) for d in raw["documents"]]


@pytest_asyncio.fixture
async def service(tmp_path, documents):
    s = RAGService(Settings(db_path=tmp_path / "test.db", embedding_provider="mock"))
    result = await s.ingest(documents)
    assert result.inserted == 3 and not result.failed
    await s.enrich(result.document_ids)
    return s
