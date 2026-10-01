"""Bounded OpenRouter transport. Only fixed provider URLs; no payload logging."""

import asyncio
import json
import math
import uuid
from dataclasses import dataclass, field
from typing import Any
import httpx
from app.config import get_settings
from app.services.errors import ProviderError, ServiceError

OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
EMBEDDINGS_ENDPOINT = "https://openrouter.ai/api/v1/embeddings"
MAX_RESPONSE_BYTES = 8_388_608


def allowed_models(settings: Any = None) -> list[str]:
    settings = settings or get_settings()
    configured = settings.openrouter_models
    values = configured.split(",") if isinstance(configured, str) else configured
    return list(dict.fromkeys(str(v).strip() for v in values if str(v).strip()))


def select_model(model: str | None = None, settings: Any = None) -> str:
    settings = settings or get_settings()
    selected = model or settings.openrouter_default_model
    if selected not in allowed_models(settings):
        raise ServiceError("The selected model is not in the configured model allowlist.", 400)
    return selected


@dataclass(frozen=True)
class Completion:
    content: str
    model: str
    usage: dict[str, int]
    request_id: str


@dataclass(frozen=True)
class ModelTurn(Completion):
    tool_calls: list[dict] = field(default_factory=list)

    def message(self) -> dict:
        result = {"role": "assistant", "content": self.content or None}
        if self.tool_calls:
            result["tool_calls"] = self.tool_calls
        return result


class OpenRouterGateway:
    def __init__(self, settings: Any = None, transport: httpx.AsyncBaseTransport | None = None):
        self.settings = settings or get_settings()
        self.transport = transport

    async def _request(self, endpoint: str, payload: dict, request_id: str) -> dict:
        if not self.settings.allow_external_ai:
            raise ServiceError("External AI is disabled for this installation.", 403)
        key = self.settings.openrouter_api_key
        if hasattr(key, "get_secret_value"):
            key = key.get_secret_value()
        if not key:
            raise ServiceError("The OpenRouter API key is not configured.", 503)
        timeout = min(max(float(self.settings.openrouter_timeout_seconds), 1.0), 300.0)
        try:
            async with asyncio.timeout(timeout):
                async with httpx.AsyncClient(
                    transport=self.transport,
                    timeout=httpx.Timeout(timeout, connect=min(10.0, timeout)),
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "POST",
                        endpoint,
                        headers={"Authorization": f"Bearer {key}", "X-Request-ID": request_id},
                        json=payload,
                    ) as response:
                        if response.status_code != 200:
                            raise ProviderError(
                                f"OpenRouter returned HTTP {response.status_code}. Request {request_id}.",
                                retryable=response.status_code == 429
                                or response.status_code >= 500,
                                request_id=request_id,
                            )
                        body = bytearray()
                        async for part in response.aiter_bytes():
                            body.extend(part)
                            if len(body) > MAX_RESPONSE_BYTES:
                                raise ProviderError(
                                    "OpenRouter response exceeded the size limit.",
                                    request_id=request_id,
                                )
            data = json.loads(body)
            if not isinstance(data, dict):
                raise ValueError("Response must be an object")
            return data
        except ProviderError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise ProviderError(
                f"OpenRouter timed out. Request {request_id}.",
                retryable=True,
                request_id=request_id,
            ) from exc
        except httpx.HTTPError as exc:
            raise ProviderError(
                f"OpenRouter connection failed. Request {request_id}.",
                retryable=True,
                request_id=request_id,
            ) from exc
        except (ValueError, TypeError) as exc:
            raise ProviderError(
                f"OpenRouter returned an invalid response. Request {request_id}.",
                request_id=request_id,
            ) from exc

    async def complete(
        self,
        messages: list[dict],
        model: str | None = None,
        request_id: str | None = None,
        response_format: dict | None = None,
    ) -> Completion:
        turn = await self.complete_turn(
            messages, model, request_id, response_format=response_format
        )
        if turn.tool_calls:
            raise ProviderError(
                "Unexpected tool request for a text-only completion.", request_id=turn.request_id
            )
        return Completion(turn.content, turn.model, turn.usage, turn.request_id)

    async def complete_turn(
        self,
        messages: list[dict],
        model: str | None = None,
        request_id: str | None = None,
        *,
        tools: list[dict] | None = None,
        response_format: dict | None = None,
        tool_choice: str = "auto",
    ) -> ModelTurn:
        request_id = request_id or str(uuid.uuid4())
        selected = select_model(model, self.settings)
        if not messages or len(messages) > 100:
            raise ServiceError("A conversation must contain between 1 and 100 messages.")
        pending = set()
        for m in messages:
            role, content = m.get("role"), m.get("content")
            if role not in {"system", "user", "assistant", "tool"}:
                raise ServiceError("Invalid conversation message.")
            calls = m.get("tool_calls", [])
            if not isinstance(content, str) and not (
                role == "assistant" and content is None and calls
            ):
                raise ServiceError("Invalid conversation message.")
            if role == "assistant" and calls:
                if pending:
                    raise ServiceError("Unanswered tool calls in conversation.")
                pending = {c.get("id") for c in calls}
            elif role == "tool":
                if m.get("tool_call_id") not in pending:
                    raise ServiceError("Tool response does not match a pending call.")
                pending.remove(m["tool_call_id"])
            elif pending:
                raise ServiceError("A tool response is required before continuing.")
        if pending:
            raise ServiceError("A tool response is required before continuing.")
        if (
            sum(len(json.dumps(m, ensure_ascii=False)) for m in messages)
            > self.settings.max_context_chars + 1000
        ):
            raise ServiceError("The conversation exceeds the configured context budget.", 413)
        output_tokens = min(max(int(self.settings.max_output_tokens), 1), 8192)
        payload = {
            "model": selected,
            "messages": messages,
            "max_tokens": output_tokens,
            "stream": False,
        }
        if tools:
            if len(tools) > 20 or tool_choice not in {"auto", "none", "required"}:
                raise ServiceError("Invalid tool configuration.")
            payload.update(
                tools=tools,
                tool_choice=tool_choice,
                parallel_tool_calls=False,
                provider={"require_parameters": True},
            )
        if response_format:
            payload.update(response_format=response_format, provider={"require_parameters": True})
        data = await self._request(OPENROUTER_ENDPOINT, payload, request_id)
        try:
            choice = data["choices"][0]
            message = choice["message"]
            content = message.get("content") or ""
            calls = message.get("tool_calls") or []
            if not isinstance(content, str) or not isinstance(calls, list) or len(calls) > 8:
                raise ValueError("Invalid model output")
            finish = choice.get("finish_reason")
            if finish == "length":
                raise ProviderError(
                    "OpenRouter reached the output limit before completing the response.",
                    request_id=request_id,
                )
            if calls:
                offered = {t["function"]["name"] for t in tools or []}
                if finish != "tool_calls" or tool_choice == "none":
                    raise ValueError("Unexpected tool calls")
                seen = set()
                for call in calls:
                    if (
                        call.get("type") != "function"
                        or not isinstance(call.get("id"), str)
                        or len(call["id"]) > 200
                        or call["id"] in seen
                        or call["function"]["name"] not in offered
                        or not isinstance(call["function"]["arguments"], str)
                        or len(call["function"]["arguments"]) > 8000
                    ):
                        raise ValueError("Invalid tool call")
                    seen.add(call["id"])
            elif finish not in {None, "stop"} or not content.strip():
                raise ValueError("Incomplete completion")
            if len(content) > min(output_tokens * 20, 160000):
                raise ValueError("Output exceeds limit")
            usage = data.get("usage") or {}
            if not isinstance(usage, dict):
                raise ValueError("Invalid usage")
            safe_usage = {
                k: usage[k]
                for k in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(k), int)
                and not isinstance(usage[k], bool)
                and usage[k] >= 0
            }
            return ModelTurn(content.strip(), selected, safe_usage, request_id, calls)
        except ProviderError:
            raise
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError(
                f"OpenRouter returned an invalid response. Request {request_id}.",
                request_id=request_id,
            ) from exc

    async def embed(
        self, texts: list[str], model: str | None = None, dimensions: int | None = None
    ) -> list[list[float]]:
        if not getattr(self.settings, "embeddings_enabled", False):
            raise ServiceError("External embeddings are disabled.", 403)
        configured = getattr(self.settings, "embedding_model", "")
        if not configured or model and model != configured:
            raise ServiceError("The embedding model is not configured or permitted.", 400)
        if (
            not texts
            or len(texts) > 64
            or any(not isinstance(t, str) or len(t) > 32000 for t in texts)
        ):
            raise ServiceError("Invalid embedding batch.")
        expected = dimensions or getattr(self.settings, "embedding_dimensions", 1536)
        request_id = str(uuid.uuid4())
        data = await self._request(
            EMBEDDINGS_ENDPOINT,
            {
                "model": configured,
                "input": texts,
                "dimensions": expected,
                "encoding_format": "float",
            },
            request_id,
        )
        try:
            entries = sorted(data["data"], key=lambda e: e["index"])
            if len(entries) != len(texts) or [e["index"] for e in entries] != list(
                range(len(texts))
            ):
                raise ValueError("Embedding indices mismatch")
            vectors = [e["embedding"] for e in entries]
            if any(
                len(v) != expected
                or any(
                    isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x)
                    for x in v
                )
                for v in vectors
            ):
                raise ValueError("Invalid embedding dimensions or values")
            return vectors
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError(
                "OpenRouter returned invalid embeddings.", request_id=request_id
            ) from exc
