from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from backend.api.health import router as health_router
from backend.api.v1.router import router as api_router
from backend.core.config import get_settings

app = FastAPI(title=get_settings().app_name, version="0.1.0",
              description="后端采集、处理、语义检索与问答；云端向量服务需单独配置，演示样本均为虚构。")
app.add_middleware(
    CORSMiddleware, allow_origins=get_settings().cors_origins,
    allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "X-Request-ID", "Idempotency-Key"],
    expose_headers=["X-Request-ID", "X-RAG-Answer-Mode", "X-RAG-Embedding-Mode", "X-RAG-LLM-Calls"],
)
app.include_router(health_router)
app.include_router(api_router)


@app.middleware("http")
async def request_identifier(request: Request, call_next):
    request.state.request_id = str(uuid4())
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


def error_response(request, status, code):
    return JSONResponse(status_code=status, content={
        "error": {"code": code, "message": code},
        "request_id": getattr(request.state, "request_id", "unknown"),
    })


@app.exception_handler(HTTPException)
async def http_error(request: Request, error: HTTPException):
    response = error_response(request, error.status_code, str(error.detail))
    if error.headers:
        response.headers.update(error.headers)
    return response


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    return error_response(request, 422, "invalid_request")


@app.exception_handler(IntegrityError)
async def conflict_error(request: Request, error: IntegrityError):
    return error_response(request, 409, "record_conflict")


@app.exception_handler(SQLAlchemyError)
async def database_error(request: Request, error: SQLAlchemyError):
    return error_response(request, 503, "database_unavailable")
