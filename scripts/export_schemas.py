"""Regenerate backend contracts directly from the public models."""
import json
from pathlib import Path
from rag_agent.schemas import DocumentInput, QueryRequest, QueryResponse, IngestResult, EnrichResult, EvidenceDetail

destination = Path("docs/schemas")
destination.mkdir(parents=True, exist_ok=True)
for model in [DocumentInput, QueryRequest, QueryResponse, IngestResult, EnrichResult, EvidenceDetail]:
    (destination / (model.__name__ + ".json")).write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2))
