"""Explicit setup download; normal ingest/query only use local model files."""
import asyncio
import json
from rag_agent.config import Settings
from rag_agent.providers.embedding import E5Embedding


async def main():
    settings = Settings.from_env()
    provider = E5Embedding(settings.embedding_model, settings.embedding_revision, settings.embedding_device, allow_download=True)
    await provider.tokenizer()
    vector = await provider.query("模型安装验证")
    print(json.dumps({"signature": provider.signature(), "dimension": len(vector), "embedding_mode": "live"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())
