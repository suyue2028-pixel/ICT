"""Internal-only platform model gateway, separate from user login credentials."""
from fastapi import APIRouter, Depends, Response
from fastapi.responses import JSONResponse
from backend.core.config import get_settings
from backend.core.security import require_tool
from backend.schemas.llm import CompletionRequest, CompletionResponse
from backend.services.platform_llm import PlatformLLM, PlatformModelError

router = APIRouter(prefix="/internal/llm", tags=["平台模型内部调用"], dependencies=[Depends(require_tool)])


@router.get("/capabilities")
def capabilities(response: Response):
    settings = get_settings()
    adapter = PlatformLLM(settings)
    from backend.services.platform_llm import PROFILES
    response.headers["Cache-Control"] = "no-store"
    return {"configured": settings.platform_llm_configured, "provider": adapter.provider,
            "model": adapter.model, "supported_model": adapter.model in PROFILES.get(adapter.provider, set()),
            "capabilities": vars(adapter.capabilities)}


@router.post("/complete", response_model=CompletionResponse)
async def complete(body: CompletionRequest, response: Response):
    response.headers["Cache-Control"] = "no-store"
    try:
        return await PlatformLLM(get_settings()).complete(body)
    except PlatformModelError as error:
        # JSONResponse preserves attempts, unlike the public HTTPException
        # handler which intentionally exposes only a simple safe error code.
        return JSONResponse(status_code=error.status_code, content={
            "detail": {"code": error.code, "attempts": error.attempts},
        }, headers={"Cache-Control": "no-store"})
