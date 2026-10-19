from fastapi import APIRouter

from backend.api.v1 import agent, auth, tools, indexing, operations, ai, demo, documents, intelligence, overview, sources, tasks, quality, dynamic_intelligence, llm

router = APIRouter(prefix="/api/v1")
for child in (sources.router, tasks.router, documents.router, intelligence.router,
              overview.router, ai.router, demo.router, agent.router, auth.router, tools.router, indexing.router, operations.router, quality.router):
    router.include_router(child)
router.include_router(dynamic_intelligence.router)
router.include_router(llm.router)
