"""Bounded backend client and leased external-index synchronization.

The backend owns documents and event leases. This worker never follows an
event-supplied URL and never acknowledges an index before verifying its proof.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator

from ..config import Settings
from .contracts import IndexProof, IndexVerification


class BackendError(RuntimeError):
    """Public-safe error: never retain response bodies, URLs or credentials."""

    def __init__(self, code: str, *, status_code: int | None = None):
        self.code = code
        self.status_code = status_code
        super().__init__(code)


class BackendSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")
    base_url: str = Field(min_length=1)
    tool_token: SecretStr
    write_token: SecretStr
    db_path: Path = Path("data/backend-rag.db")
    timeout: float = Field(default=15, gt=0, le=60)
    rag_settings: Settings = Field(default_factory=Settings.from_env, exclude=True, repr=False)

    @field_validator("base_url")
    @classmethod
    def endpoint(cls, value):
        try:
            parsed = urlsplit(value)
            parsed.port  # Reject malformed ports during configuration.
        except ValueError:
            raise ValueError("invalid backend URL") from None
        if (not parsed.hostname or parsed.username or parsed.password or parsed.query
                or parsed.fragment or parsed.path not in {"", "/"}
                or parsed.scheme not in {"https", "http"}
                or (parsed.scheme == "http" and parsed.hostname not in {
                    "localhost", "127.0.0.1", "::1", "host.docker.internal"})):
            raise ValueError("backend URL must be an HTTPS origin or local HTTP origin without credentials")
        return value.rstrip("/")

    @field_validator("tool_token", "write_token")
    @classmethod
    def nonempty_token(cls, value):
        key = value.get_secret_value()
        if not key or len(key) > 1024 or any(ord(char) < 33 or ord(char) > 126 for char in key):
            raise ValueError("backend tokens must be nonempty printable ASCII without whitespace")
        return value

    @classmethod
    def from_env(cls):
        # Wrap secrets before Pydantic validation so a configuration exception
        # cannot include an unmasked token as its input value.
        return cls(
            base_url=os.environ.get("RAG_BACKEND_URL", ""),
            tool_token=SecretStr(os.environ.get("RAG_BACKEND_TOOL_TOKEN", "")),
            write_token=SecretStr(os.environ.get("RAG_BACKEND_WRITE_TOKEN", "")),
            db_path=os.environ.get("RAG_BACKEND_DB_PATH", "data/backend-rag.db"),
            timeout=os.environ.get("RAG_BACKEND_TIMEOUT", "15"),
            rag_settings=Settings.from_env(),
        )

    def service_settings(self) -> Settings:
        return self.rag_settings.model_copy(update={"db_path": self.db_path})


class KnowledgeEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")
    event_id: UUID
    document_id: UUID
    content_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    operation: Literal["add", "update", "delete"]
    is_demo: StrictBool
    lease_token: UUID
    lease_until: datetime
    # Transport compatibility only: this value is intentionally never followed.
    document_url: str | None = Field(default=None, max_length=2048)

    @field_validator("lease_until")
    @classmethod
    def aware_lease(cls, value):
        if value.tzinfo is None:
            raise ValueError("lease must have a timezone")
        return value.astimezone(timezone.utc)


class ClaimResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal["1"]
    provider: Literal["external"]
    items: list[KnowledgeEvent] = Field(max_length=20)


class BackendClient:
    MAX_RESPONSE_BYTES = 2 * 1024 * 1024

    def __init__(self, settings: BackendSettings):
        self.settings = settings

    async def _request(self, method, path, *, write=False, body=None, params=None, retry=False):
        """Retry only reads and idempotent ACKs; never silently re-claim leases."""
        attempts = 2 if retry else 1
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(self.settings.timeout):
                    async with httpx.AsyncClient(
                        timeout=self.settings.timeout, follow_redirects=False, trust_env=False,
                    ) as client:
                        token = self.settings.write_token if write else self.settings.tool_token
                        # The backend uses the SAME header name for read and write
                        # dependencies, with distinct expected token values.
                        async with client.stream(
                            method, self.settings.base_url + path,
                            headers={"X-Agent-Token": token.get_secret_value(), "Accept": "application/json"},
                            json=body, params=params,
                        ) as response:
                            if not 200 <= response.status_code < 300:
                                if path == '/api/v1/internal/intelligence/lookup':
                                    safe_codes={'intelligence_timeout','intelligence_rate_limited',
                                        'intelligence_source_unavailable','intelligence_invalid_response',
                                        'intelligence_entity_mismatch','intelligence_response_too_large'}
                                    safe_codes |= {'search_not_configured','search_timeout','search_rate_limited',
                                        'search_unavailable','search_invalid_response','search_unsafe_url',
                                        'search_auth_failed','search_budget_exceeded','search_response_too_large'}
                                    error_body=bytearray()
                                    async for part in response.aiter_bytes(chunk_size=1024):
                                        if len(error_body)+len(part)>4096:
                                            break
                                        error_body.extend(part)
                                    try:
                                        payload=json.loads(error_body)
                                        code=payload.get('detail')
                                        if code is None:
                                            code=payload.get('error',{}).get('code')
                                    except (ValueError,TypeError,AttributeError):
                                        code=None
                                    if isinstance(code,str) and code in safe_codes:
                                        raise BackendError(code,status_code=response.status_code)
                                raise BackendError(f"backend_http_{response.status_code}", status_code=response.status_code)
                            raw = bytearray()
                            async for part in response.aiter_bytes(chunk_size=8192):
                                if len(raw) + len(part) > self.MAX_RESPONSE_BYTES:
                                    raise BackendError("backend_response_too_large")
                                raw.extend(part)
                            try:
                                result = json.loads(raw)
                            except (ValueError, TypeError):
                                raise BackendError("backend_invalid_json") from None
                            if not isinstance(result, dict):
                                raise BackendError("backend_invalid_envelope")
                            return result
            except (TimeoutError, httpx.TimeoutException):
                error = BackendError("backend_timeout")
            except httpx.HTTPError:
                error = BackendError("backend_transport_unavailable")
            except BackendError as exc:
                error = exc
            transient = error.status_code in {429, 502, 503, 504} or error.code in {
                "backend_timeout", "backend_transport_unavailable",
            }
            if attempt + 1 >= attempts or not transient:
                raise error from None
            await asyncio.sleep(0.2)
        raise BackendError("backend_retry_exhausted")

    async def claim(self, *, is_demo=False, limit=5) -> list[KnowledgeEvent]:
        if type(is_demo) is not bool or type(limit) is not int or not 1 <= limit <= 20:
            raise ValueError("claim requires a boolean scope and limit 1..20")
        response = await self._request(
            "POST", "/api/v1/internal/knowledge-events/claim", write=True,
            body={"is_demo": is_demo, "limit": limit},
        )
        try:
            claim = ClaimResponse.model_validate(response)
        except ValueError:
            raise BackendError("backend_invalid_claim") from None
        if len(claim.items) > limit or any(item.is_demo is not is_demo for item in claim.items):
            raise BackendError("backend_claim_scope_mismatch")
        if len({item.event_id for item in claim.items}) != len(claim.items):
            raise BackendError("backend_duplicate_claim")
        return claim.items

    async def read_document(self, event: KnowledgeEvent) -> dict:
        # UUID-derived path and exact hash/scope parameters: document_url from
        # the event cannot redirect a request or leak either backend token.
        response = await self._request(
            "GET", f"/api/v1/tools/documents/{event.document_id}",
            params={"is_demo": str(event.is_demo).lower(), "content_hash": event.content_hash}, retry=True,
        )
        data = response.get("data")
        if response.get("schema_version") != "1" or response.get("status") != "completed" or not isinstance(data, dict):
            raise BackendError("backend_invalid_document_envelope")
        try:
            document_id = UUID(data["document_id"])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise BackendError("backend_document_identity_invalid") from None
        if (document_id != event.document_id or data.get("content_hash") != event.content_hash
                or data.get("is_demo") is not event.is_demo or data.get("is_current") is not True):
            raise BackendError("backend_document_scope_or_version_mismatch")
        if not isinstance(data.get("revision"), dict):
            raise BackendError("backend_document_revision_missing")
        return {**data, "operation": event.operation}

    async def acknowledge(self, event: KnowledgeEvent, status: str, detail: dict):
        if status not in {"indexed", "invalidated", "failed"}:
            raise ValueError("invalid acknowledgement status")
        response = await self._request(
            "POST", f"/api/v1/internal/knowledge-events/{event.event_id}/ack", write=True,
            body={"status": status, "detail": detail, "lease_token": str(event.lease_token)}, retry=True,
        )
        try:
            acknowledged_id = UUID(response["id"])
        except (ValueError, TypeError, KeyError, AttributeError):
            raise BackendError("backend_invalid_acknowledgement") from None
        if acknowledged_id != event.event_id or response.get("status") != status:
            raise BackendError("backend_acknowledgement_mismatch")
        return response


class BackendSyncWorker:
    def __init__(self, client: BackendClient, index):
        self.client = client
        self.index = index

    @staticmethod
    def _live_lease(event):
        if event.lease_until <= datetime.now(timezone.utc):
            raise BackendError("backend_lease_expired")

    async def _process(self, event: KnowledgeEvent) -> dict:
        result = {"event_id": str(event.event_id), "document_id": str(event.document_id),
                  "operation": event.operation, "is_demo": event.is_demo}
        try:
            self._live_lease(event)
            if event.operation == "delete":
                # Deleted documents are unavailable through the backend tools;
                # invalidation must use the event identity without reading them.
                index_id = await self.index.delete(str(event.document_id), event.content_hash, event.is_demo)
                status = "invalidated"
            else:
                document = await self.client.read_document(event)
                self._live_lease(event)
                index_id = await self.index.upsert(document)
                status = "indexed"
            request = IndexVerification(
                schema_version="1", document_id=event.document_id, content_hash=event.content_hash,
                index_id=index_id, is_demo=event.is_demo, operation=event.operation,
            )
            proof = IndexProof.model_validate(self.index.verify(request.model_dump(mode="json")))
            if (proof.document_id != event.document_id or proof.content_hash != event.content_hash
                    or proof.index_id != index_id or proof.is_demo is not event.is_demo
                    or proof.queryable is not (event.operation != "delete")
                    or proof.removed is not (event.operation == "delete")):
                raise BackendError("local_index_proof_mismatch")
            self._live_lease(event)
            # Backend performs its own HTTP proof check before committing this
            # ACK. A local proof alone can never mark the event as successful.
            await self.client.acknowledge(event, status, {"index_id": index_id})
            return {**result, "status": status, "index_id": index_id}
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            code = exc.code if isinstance(exc, BackendError) else "local_index_failed"
            result.update(status="failed", error_code=code, failure_acknowledged=False)
            # A 409 means the lease/event/version may have changed; do not
            # attempt another state transition with the stale lease.
            if isinstance(exc, BackendError) and (exc.status_code == 409 or code == "backend_lease_expired"):
                return result
            try:
                self._live_lease(event)
                await self.client.acknowledge(event, "failed", {"error_code": code})
                result["failure_acknowledged"] = True
            except asyncio.CancelledError:
                raise
            except Exception as ack_exc:
                result["ack_error_code"] = ack_exc.code if isinstance(ack_exc, BackendError) else "failure_ack_failed"
            return result

    async def run_once(self, *, is_demo=False, limit=5) -> dict:
        events = await self.client.claim(is_demo=is_demo, limit=limit)
        results = []
        for event in events:
            results.append(await self._process(event))
        return {
            "schema_version": "1", "is_demo": is_demo, "claimed": len(events),
            "indexed": sum(item["status"] == "indexed" for item in results),
            "invalidated": sum(item["status"] == "invalidated" for item in results),
            "failed": sum(item["status"] == "failed" for item in results), "results": results,
        }
