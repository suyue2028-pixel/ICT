import argparse
import asyncio
import json
from pathlib import Path
from .config import Settings
from .schemas import DocumentInput, QueryRequest
from .service import RAGService


async def run(args):
    settings = Settings.from_env()
    if args.db:
        settings.db_path = Path(args.db)
    if args.mock_embedding:
        settings.embedding_provider = "mock"
    service = RAGService(settings)
    if args.command == "import":
        # Schema failures include line number; valid documents still import independently.
        documents, failures = [], []
        for line_no, line in enumerate(Path(args.path).read_text().splitlines(), 1):
            if line.strip():
                try:
                    documents.append(DocumentInput.model_validate_json(line))
                except ValueError as exc:
                    failures.append({"line": line_no, "error": str(exc)[:240]})
        result = await service.ingest(documents)
        print(json.dumps({"ingest": result.model_dump(mode="json"), "invalid_lines": failures}, ensure_ascii=False, indent=2))
        print((await service.enrich(result.document_ids)).model_dump_json(indent=2))
        return 1 if result.failed or failures else 0
    if args.command == "query":
        request = QueryRequest.model_validate_json(Path(args.request_file).read_text()) if args.request_file else QueryRequest(question=args.question, filters=json.loads(args.filters))
        response = await service.query(request)
        print(response.model_dump_json(indent=2))
        return 1 if response.status == "error" else 0
    if args.command == "evidence":
        print((await service.get_evidence(args.id)).model_dump_json(indent=2))
        return 0
    if args.command == "health":
        s = service.store.snapshot()
        print(json.dumps({"documents": len(s.documents), "chunks": len(s.chunks), "generation": s.generation,
                          "embedding_signature": s.signature, "llm_mode": settings.llm_mode}, indent=2))
        return 0


def main():
    parser = argparse.ArgumentParser(description="ICT local RAG CLI")
    parser.add_argument("--db")
    parser.add_argument("--mock-embedding", action="store_true")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("import").add_argument("path")
    q = commands.add_parser("query")
    q.add_argument("question", nargs="?", default="")
    q.add_argument("--filters", default="{}")
    q.add_argument("--request-file")
    commands.add_parser("evidence").add_argument("id")
    commands.add_parser("health")
    raise SystemExit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
