"""Small provider adapter for the server-orchestrated evidence workflow.

JSON output is validated downstream by the Agent's existing purpose schemas.
Native model tool calls are explicitly unsupported by this transport; actual
intelligence tools remain independently executed and audited by the Agent.
"""
import asyncio
import json
import logging
from dataclasses import dataclass
from time import monotonic

import httpx

from pydantic import ValidationError
from backend.schemas.llm import (CompletionRequest, CompletionResponse, TokenUsage,
                                 ClassificationResult, GeneralExplanationResult)
from backend.services.llm_prompts import (SYSTEM, CONVERSATION_SYSTEM, LEGACY_SYSTEM,
                                        CLASSIFICATION_SYSTEM, GENERAL_EXPLANATION_SYSTEM, SYNTHESIS_LENGTH_SYSTEM)

logger = logging.getLogger(__name__)
ERROR_STATUS = {
    "platform_model_not_configured": 503,
    "platform_model_auth_failed": 502,
    "platform_model_rate_limited": 503,
    "platform_model_timeout": 504,
    "platform_model_unavailable": 502,
    "platform_model_format_invalid": 502,
    "platform_model_capability_unsupported": 502,
}
# Deliberately bounded profiles: a newly configured model needs a reviewed
# capability entry rather than silently assuming tool/JSON parity.
PROFILES = {
    "deepseek": {"deepseek-flash", "deepseek-v4-pro", "deepseek-chat"},
    "qwen": {"qwen-plus", "qwen-flash", "qwen-turbo", "qwen-max"},
}


class PlatformModelError(Exception):
    def __init__(self, code: str, attempts: int = 0):
        self.code = code
        self.attempts = attempts
        self.status_code = ERROR_STATUS[code]
        super().__init__(code)


@dataclass(frozen=True)
class ModelCapabilities:
    json_object: bool = True
    server_tool_workflow: bool = True
    native_tool_calls: bool = False


class PlatformLLM:
    """Provider selection is exclusively Settings-owned, never request-owned."""
    def __init__(self, settings, *, transport=None):
        self.settings = settings
        self.provider = settings.llm_provider
        self.model = settings.platform_llm_model
        self.base_url = settings.platform_llm_base_url
        self.transport = transport  # deterministic unit-test seam, no mock runtime fallback
        self.capabilities = ModelCapabilities()

    def _key(self) -> str:
        key = self.settings.llm_api_key or (self.settings.deepseek_api_key if self.provider == "deepseek"
                                            else self.settings.dashscope_api_key)
        if key and key.get_secret_value().strip():
            result = key.get_secret_value().strip()
        elif self.settings.llm_api_key_file:
            path = self.settings.llm_api_key_file
            try:
                stat = path.stat()
                if (not path.is_file() or path.is_symlink() or stat.st_mode & 0o077
                        or not 0 < stat.st_size <= 4096):
                    raise PlatformModelError("platform_model_not_configured")
                result = path.read_text(encoding="utf-8").strip()
            except (OSError, UnicodeError):
                raise PlatformModelError("platform_model_not_configured") from None
        else:
            raise PlatformModelError("platform_model_not_configured")
        if not result or len(result) > 4096 or any(char.isspace() for char in result):
            raise PlatformModelError("platform_model_not_configured")
        return result

    def _body(self, request: CompletionRequest) -> dict:
        if self.model not in PROFILES.get(self.provider, set()):
            raise PlatformModelError("platform_model_capability_unsupported")
        if any(not getattr(self.capabilities, name) for name in request.required_capabilities):
            raise PlatformModelError("platform_model_capability_unsupported")
        payload = dict(request.payload)
        if request.purpose in {"converse", "classify"}:
            # Model identity is platform-owned and remains correct after a switch.
            payload["configured_model"] = self.model
        system = (CLASSIFICATION_SYSTEM if request.purpose == "classify" else
                  GENERAL_EXPLANATION_SYSTEM if request.purpose == "general_explain" else
                  CONVERSATION_SYSTEM if request.purpose == "converse" else
                  LEGACY_SYSTEM if request.purpose == "legacy_answer" else SYSTEM)
        messages = [{"role": "system", "content": system}]
        if request.purpose == "synthesize":
            messages.append({"role": "system", "content": SYNTHESIS_LENGTH_SYSTEM})
        if request.purpose in {"classify", "converse", "general_explain", "legacy_answer"}:
            history = payload.pop("history", []) if request.purpose == "legacy_answer" else payload.get("history", [])
            if not isinstance(history, list) or len(history) > 8:
                raise PlatformModelError("platform_model_format_invalid")
            for message in history:
                if (not isinstance(message, dict) or message.get("role") not in {"user", "assistant"}
                        or not isinstance(message.get("content"), str) or len(message["content"]) > 2000):
                    raise PlatformModelError("platform_model_format_invalid")
                messages.append({"role": message["role"], "content": message["content"]})
        messages.append({"role": "user", "content": json.dumps({**payload, "purpose": request.purpose}, ensure_ascii=False)})
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": min(8192, request.max_answer_tokens) if request.purpose in {"synthesize", "general_explain"}
                else min(request.max_answer_tokens, 1024 if request.purpose in {"classify", "assess", "legacy_answer"} else 512),
            "response_format": {"type": "json_object"},
            "messages": messages,
        }
        # Provider-specific options are never sent to the other vendor.
        if self.provider == "deepseek":
            body["thinking"] = {"type": "disabled"}
        elif self.provider == "qwen":
            body["enable_thinking"] = False
        return body

    async def complete(self, request: CompletionRequest) -> CompletionResponse:
        body = self._body(request)
        key = self._key()
        attempts = 0
        deadline = monotonic() + min(request.timeout_seconds, self.settings.llm_timeout_seconds)
        limit = min(request.max_attempts, self.settings.llm_max_attempts)
        async with httpx.AsyncClient(transport=self.transport, follow_redirects=False, trust_env=False) as client:
            while attempts < limit:
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise PlatformModelError("platform_model_timeout", attempts)
                attempts += 1
                try:
                    async with asyncio.timeout(remaining):
                        async with client.stream("POST", self.base_url.rstrip("/") + "/chat/completions",
                                timeout=remaining, headers={"Authorization": "Bearer " + key}, json=body) as response:
                            status = response.status_code
                            # No upstream error body is read, logged or forwarded.
                            if status in {401, 403}:
                                raise PlatformModelError("platform_model_auth_failed", attempts)
                            if status == 429 or status >= 500:
                                code = "platform_model_rate_limited" if status == 429 else "platform_model_unavailable"
                                if status in {429, 503} and attempts < limit and deadline - monotonic() > 0.2:
                                    retry = True
                                else:
                                    raise PlatformModelError(code, attempts)
                            elif status == 400:
                                raise PlatformModelError("platform_model_capability_unsupported", attempts)
                            elif not 200 <= status < 300:
                                raise PlatformModelError("platform_model_unavailable", attempts)
                            else:
                                retry = False
                                parts, size = [], 0
                                async for part in response.aiter_bytes():
                                    size += len(part)
                                    if size > 384000:
                                        raise PlatformModelError("platform_model_format_invalid", attempts)
                                    parts.append(part)
                                result, usage = self._parse(b"".join(parts), key, attempts)
                                try:
                                    if request.purpose == "classify":
                                        result = ClassificationResult.model_validate(result).model_dump(mode="json")
                                    elif request.purpose == "general_explain":
                                        result = GeneralExplanationResult.model_validate(result).model_dump(mode="json")
                                except ValidationError:
                                    raise PlatformModelError("platform_model_format_invalid", attempts) from None
                                logger.info("platform_llm purpose=%s provider=%s model=%s attempts=%s usage=%s",
                                    request.purpose, self.provider, self.model, attempts, usage.model_dump(exclude_none=True))
                                return CompletionResponse(result=result, attempts=attempts, usage=usage,
                                    provider=self.provider, model=self.model)
                except (TimeoutError, httpx.TimeoutException):
                    raise PlatformModelError("platform_model_timeout", attempts) from None
                except httpx.HTTPError:
                    raise PlatformModelError("platform_model_unavailable", attempts) from None
                if retry:
                    await asyncio.sleep(min(0.2, max(0, deadline - monotonic())))
        raise PlatformModelError("platform_model_unavailable", attempts)

    @staticmethod
    def _parse(raw: bytes, key: str, attempts: int) -> tuple[dict, TokenUsage]:
        try:
            envelope = json.loads(raw)
            choice = envelope["choices"][0]
            if choice.get("finish_reason") == "tool_calls" or choice["message"].get("tool_calls"):
                raise PlatformModelError("platform_model_capability_unsupported", attempts)
            if choice.get("finish_reason") != "stop":
                raise ValueError("incomplete response")
            content = choice["message"]["content"]
            if not isinstance(content, str) or not content.strip() or key in content:
                raise ValueError("empty or unsafe response")
            result = json.loads(content)
            if not isinstance(result, dict):
                raise ValueError("JSON object required")
            usage_raw = envelope.get("usage", {})
            usage = TokenUsage.model_validate({name: usage_raw[name] for name in (
                "prompt_tokens", "completion_tokens", "total_tokens") if isinstance(usage_raw, dict) and name in usage_raw})
            return result, usage
        except (ValueError, TypeError, KeyError, IndexError, AttributeError):
            raise PlatformModelError("platform_model_format_invalid", attempts) from None
