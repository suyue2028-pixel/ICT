"""Regenerate public schemas from actual models, never from configuration or data."""
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from backend.main import app
from backend.schemas.agent import AgentAnswer, AgentQuery, AgentQueryResponse
from backend.schemas.contracts import KnowledgeAck, EnrichmentWrite
from backend.schemas.tools import ToolDocumentPreview, ToolDocumentVersion
from backend.schemas.traceability import AgentInvocationEvidence, DocumentAssociations, DocumentVersionProof


def main():
    models = [AgentAnswer, AgentQuery, AgentQueryResponse, KnowledgeAck, EnrichmentWrite, ToolDocumentPreview, ToolDocumentVersion,
              AgentInvocationEvidence, DocumentAssociations, DocumentVersionProof]
    folder = ROOT / "docs/contracts"
    folder.mkdir(exist_ok=True)
    for model in models:
        (folder / (model.__name__ + ".schema.json")).write_text(json.dumps(model.model_json_schema(), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    (ROOT / "docs/openapi.json").write_text(json.dumps(app.openapi(), ensure_ascii=False, indent=2), encoding="utf-8", newline="\n")
    print(f"Exported {len(models)} model schemas and current OpenAPI; no application data exported")


if __name__ == "__main__":
    main()
