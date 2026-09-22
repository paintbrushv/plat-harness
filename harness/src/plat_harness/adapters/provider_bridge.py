"""Provider capability and routing contract (Task 5.3).

One internal request/response contract (``ProviderRequest`` -> canonical
``ModelTurn``) with per-provider wire translators for Anthropic-compatible
cloud, OpenAI-compatible cloud and an explicitly configured local
loopback vLLM/Ollama endpoint. The bridge routes a canonical
``vendor/model`` identifier to a registered provider; it never hardcodes
historical vendor model names as available (use :func:`discover_models`
against a live endpoint), never starts a local server, never falls back
from a failing local provider to a cloud provider, and never performs any
network I/O itself — all bytes move through an injected transport, so
tests run entirely on recorded synthetic responses.

``NullModel`` remains the offline default; constructing a bridge with no
routing table keeps the offline refusal contract intact.

No raw prompt source, response body or credential ever enters bridge
events or exception messages: events carry bounded metadata
(provider id, canonical model id, path, byte count, payload SHA-256,
status, finish reason, usage) only.
"""

from __future__ import annotations

import copy
import hashlib
import ipaddress
import json
import time
import urllib.parse
from dataclasses import dataclass, field
from typing import Any, Callable

from plat_harness.errors import HarnessError, NOT_IMPLEMENTED, NO_MODEL_CONFIGURED
from plat_harness.local_model import fail, strict_json, validate_call
from plat_harness.models import ModelTurn

# --------------------------------------------------------------- contract ids

CAP_TOOL_CALLS = "tool_calls"
CAP_STRUCTURED_OUTPUT = "structured_output"
CAPABILITIES = frozenset({CAP_TOOL_CALLS, CAP_STRUCTURED_OUTPUT})

KIND_ANTHROPIC = "anthropic"
KIND_OPENAI = "openai"
KIND_LOCAL_OPENAI = "local-openai"
KINDS = frozenset({KIND_ANTHROPIC, KIND_OPENAI, KIND_LOCAL_OPENAI})

ANTHROPIC_ORIGIN = "https://api.anthropic.com"
OPENAI_ORIGIN = "https://api.openai.com"
ANTHROPIC_VERSION = "2023-06-01"

ENDPOINT_NOT_ALLOWED = "ENDPOINT_NOT_ALLOWED"
ENDPOINT_NOT_LOCAL = "ENDPOINT_NOT_LOCAL"
INVALID_LIMIT = "INVALID_LIMIT"
INVALID_MODEL_ID = "INVALID_MODEL_ID"
INVALID_MODEL_JSON = "INVALID_MODEL_JSON"
INVALID_MODEL_LIST = "INVALID_MODEL_LIST"
INVALID_MODEL_PROTOCOL = "INVALID_MODEL_PROTOCOL"
INVALID_PROVIDER = "INVALID_PROVIDER"
INVALID_TOOL_CALL = "INVALID_TOOL_CALL"
CONTEXT_OVERFLOW = "CONTEXT_OVERFLOW"
UNKNOWN_MODEL = "UNKNOWN_MODEL"
MODEL_NOT_SERVED = "MODEL_NOT_SERVED"
MODEL_OUTPUT_LIMIT = "MODEL_OUTPUT_LIMIT"
MODEL_INCOMPLETE = "MODEL_INCOMPLETE"
PROVIDER_UNSUPPORTED_CAPABILITY = "PROVIDER_UNSUPPORTED_CAPABILITY"
PROVIDER_STREAM_UNSUPPORTED = "PROVIDER_STREAM_UNSUPPORTED"
PROVIDER_CREDENTIAL_MISSING = "PROVIDER_CREDENTIAL_MISSING"
PROVIDER_AUTH = "PROVIDER_AUTH"
PROVIDER_RATE_LIMIT = "PROVIDER_RATE_LIMIT"
PROVIDER_BAD_REQUEST = "PROVIDER_BAD_REQUEST"
PROVIDER_TIMEOUT = "PROVIDER_TIMEOUT"
PROVIDER_CANCELLED = "PROVIDER_CANCELLED"
PROVIDER_TRANSPORT_ERROR = "PROVIDER_TRANSPORT_ERROR"
LOCAL_ENDPOINT_ERROR = "LOCAL_ENDPOINT_ERROR"

RETRYABLE_STATUSES = frozenset({408, 429, 500, 502, 503, 504})

MAX_REQUEST_BYTES_DEFAULT = 262144
MAX_RESPONSE_BYTES_DEFAULT = 131072
MAX_TIMEOUT_S = 120.0
MAX_RETRIES_BOUND = 5


def _text(value: Any, code: str, message: str) -> str:
    if not isinstance(value, str) or not 1 <= len(value) <= 512:
        fail(code, message)
    return value


def _endpoint_error(kind: str, message: str) -> None:
    if kind == KIND_LOCAL_OPENAI:
        fail(ENDPOINT_NOT_LOCAL, message)
    fail(ENDPOINT_NOT_ALLOWED, message)


@dataclass(frozen=True)
class ProviderSpec:
    """One registered provider: transport origin, capabilities and bounds.

    Cloud origins are the pinned official API origins (no arbitrary URLs, no
    SSRF surface); the local origin must be a literal loopback IP over plain
    http with an explicit port, no path, query, fragment or credentials.
    """

    provider_id: str
    kind: str
    capabilities: frozenset[str]
    api_key_env: str | None = None
    base_url: str | None = None
    timeout_s: float = 30.0
    max_request_bytes: int = MAX_REQUEST_BYTES_DEFAULT
    max_retries: int = 2

    def __post_init__(self) -> None:
        _text(self.provider_id, INVALID_PROVIDER, "Provider id must be bounded text.")
        if self.kind not in KINDS:
            fail(INVALID_PROVIDER, "Unknown provider kind.")
        if not isinstance(self.capabilities, frozenset) or not self.capabilities <= CAPABILITIES:
            fail(INVALID_PROVIDER, "Unknown provider capability.")
        if self.kind in (KIND_ANTHROPIC, KIND_OPENAI):
            if not isinstance(self.api_key_env, str) or not self.api_key_env:
                fail(INVALID_PROVIDER, "Cloud providers require an explicit credential environment name.")
            official = ANTHROPIC_ORIGIN if self.kind == KIND_ANTHROPIC else OPENAI_ORIGIN
            if self.base_url is not None and self.base_url != official:
                _endpoint_error(self.kind, "Cloud providers use the pinned official origin only; arbitrary URLs are refused.")
        else:  # local-openai
            if self.base_url is None:
                fail(ENDPOINT_NOT_LOCAL, "Local providers must declare an explicit loopback origin.")
            url = urllib.parse.urlsplit(self.base_url)
            try:
                address = ipaddress.ip_address(url.hostname or "")
            except ValueError:
                address = None
            if (address is None or not address.is_loopback or url.scheme != "http"
                    or url.port is None or url.path not in ("", "/")
                    or url.query or url.fragment or url.username is not None or url.password is not None):
                fail(ENDPOINT_NOT_LOCAL,
                     "Use an explicit http loopback-IP:port origin, without path, query or credentials.")
        if type(self.timeout_s) not in (int, float) or not 0 < self.timeout_s <= MAX_TIMEOUT_S:
            fail(INVALID_LIMIT, "Invalid provider timeout bound.")
        if type(self.max_request_bytes) is not int or not 1024 <= self.max_request_bytes <= 1048576:
            fail(INVALID_LIMIT, "Invalid request byte bound.")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= MAX_RETRIES_BOUND:
            fail(INVALID_LIMIT, "Invalid retry bound.")

    def is_local(self) -> bool:
        return self.kind == KIND_LOCAL_OPENAI


@dataclass(frozen=True)
class ModelRoute:
    """Canonical ``vendor/model`` identifier bound to a provider and its wire model id."""

    canonical: str
    provider_id: str
    wire_model: str

    def __post_init__(self) -> None:
        if not isinstance(self.canonical, str) or len(self.canonical) > 512:
            fail(INVALID_MODEL_ID, "Model id must be bounded text.")
        vendor, slash, model = self.canonical.partition("/")
        if not slash or not vendor or not model:
            fail(INVALID_MODEL_ID, "Model id must be 'vendor/model'.")
        if vendor != vendor.lower() or not vendor[0].isalnum() or not all(c.isalnum() or c == "-" for c in vendor):
            fail(INVALID_MODEL_ID, "Vendor segment must be lowercase alphanumeric with dashes.")
        if not model[0].isalnum() or not model.islower() or not all(c.isalnum() or c in "._-" for c in model):
            fail(INVALID_MODEL_ID, "Model segment must be lowercase alphanumeric with dots, dashes or underscores.")
        _text(self.provider_id, INVALID_PROVIDER, "Route provider id must be bounded text.")
        _text(self.wire_model, INVALID_MODEL_ID, "Wire model id must be bounded text.")


class ProviderRegistry:
    """Frozen-at-read provider registry; every accessor returns isolated deep copies."""

    def __init__(self) -> None:
        self._specs: dict[str, ProviderSpec] = {}

    def register(self, spec: ProviderSpec) -> None:
        if not isinstance(spec, ProviderSpec):
            fail(INVALID_PROVIDER, "Only ProviderSpec instances can be registered.")
        if spec.provider_id in self._specs:
            fail(INVALID_PROVIDER, "Provider id is already registered.")
        self._specs[spec.provider_id] = spec

    def get(self, provider_id: str) -> ProviderSpec:
        spec = self._specs.get(provider_id)
        if spec is None:
            fail(INVALID_PROVIDER, "Provider is not registered.")
        return copy.deepcopy(spec)

    def specs(self) -> dict[str, ProviderSpec]:
        return {provider_id: copy.deepcopy(spec) for provider_id, spec in self._specs.items()}


class RoutingTable:
    """Canonical model id -> (route, provider spec); lookups return isolated copies."""

    def __init__(self, registry: ProviderRegistry, routes: dict[str, ModelRoute]) -> None:
        if not isinstance(registry, ProviderRegistry) or not isinstance(routes, dict):
            fail(INVALID_PROVIDER, "Routing table needs a registry and a route mapping.")
        self._registry = registry
        self._routes: dict[str, ModelRoute] = {}
        for canonical, route in routes.items():
            if not isinstance(route, ModelRoute) or route.canonical != canonical:
                fail(INVALID_MODEL_ID, "Route key must match its canonical model id.")
            registry.get(route.provider_id)  # refuses unregistered providers
            self._routes[canonical] = route

    def resolve(self, canonical: str) -> tuple[ModelRoute, ProviderSpec]:
        route = self._routes.get(canonical)
        if route is None:
            fail(UNKNOWN_MODEL, "Canonical model id is not routed.")
        return copy.deepcopy(route), self._registry.get(route.provider_id)


@dataclass(frozen=True)
class ProviderRequest:
    """The single internal request contract shared by every translator."""

    messages: list[dict[str, Any]]
    tools: tuple[dict[str, Any], ...] = ()
    response_format: dict[str, Any] | None = None
    max_tokens: int = 512
    stream: bool = False


@dataclass(frozen=True)
class TransportResult:
    """One recorded HTTP exchange: numeric status plus raw body bytes."""

    status: int
    body: bytes


class CancelFlag:
    """Host-owned cooperative cancellation; checked before and after every send."""

    def __init__(self) -> None:
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    @property
    def cancelled(self) -> bool:
        return self._cancelled


@dataclass(frozen=True)
class BoundedRetryPolicy:
    """Bounded exponential backoff; a policy of zero retries means one attempt."""

    max_retries: int = 2
    base_delay_s: float = 0.25
    max_delay_s: float = 8.0

    def __post_init__(self) -> None:
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= MAX_RETRIES_BOUND:
            fail(INVALID_LIMIT, "Retry count must be a small non-negative integer.")
        if type(self.base_delay_s) not in (int, float) or not 0 <= self.base_delay_s <= 60:
            fail(INVALID_LIMIT, "Invalid base delay.")
        if type(self.max_delay_s) not in (int, float) or not 0 <= self.max_delay_s <= 60:
            fail(INVALID_LIMIT, "Invalid delay cap.")

    def delay(self, attempt: int) -> float:
        return min(self.base_delay_s * (2 ** max(0, attempt - 1)), self.max_delay_s)


def _local_transport_error(spec: ProviderSpec, exc: BaseException) -> HarnessError:
    if spec.is_local():
        return HarnessError(LOCAL_ENDPOINT_ERROR,
                            "Local endpoint request failed; no fallback attempted.",
                            details={"exception": type(exc).__name__})
    return HarnessError(PROVIDER_TRANSPORT_ERROR, "Provider transport failed after bounded retries.",
                        details={"exception": type(exc).__name__})


def _status_error(spec: ProviderSpec, status: int, body: bytes) -> HarnessError:
    """Map a non-2xx status to a typed refusal; never embeds body or credentials."""
    if status in (401, 403):
        return HarnessError(PROVIDER_AUTH, "Provider rejected the credentials.")
    if status == 429:
        return HarnessError(PROVIDER_RATE_LIMIT, "Provider rate limit exhausted the bounded retry policy.")
    if b"context_length_exceeded" in body or b"prompt is too long" in body:
        return HarnessError(CONTEXT_OVERFLOW, "Provider reports the request exceeds its context window.")
    if status in RETRYABLE_STATUSES:
        return HarnessError(PROVIDER_TRANSPORT_ERROR, "Provider unavailable after bounded retries.",
                            details={"status": status})
    return HarnessError(PROVIDER_BAD_REQUEST, "Provider refused the request.",
                        details={"status": status})


class RetryingTransport:
    """Bounded retry around an injected transport; cancellable, never infinite.

    Retry applies only to transient statuses (408/429/5xx) and transport
    errors, capped by the policy. A cancelled flag stops further attempts.
    """

    def __init__(self, transport: Any, policy: BoundedRetryPolicy,
                 sleep: Callable[[float], None] | None = None) -> None:
        self._transport = transport
        self._policy = policy
        self._sleep = sleep or time.sleep

    def send(self, spec: ProviderSpec, path: str, payload: str, headers: dict[str, str],
             timeout_s: float, cancel: CancelFlag | None) -> TransportResult:
        attempts = 1 + self._policy.max_retries
        for attempt in range(attempts):
            if cancel is not None and cancel.cancelled:
                fail(PROVIDER_CANCELLED, "Request cancelled by the host before retrying.")
            try:
                result = self._transport.send(spec, path, payload, headers, timeout_s, cancel)
            except TimeoutError as exc:
                if spec.is_local():
                    raise _local_transport_error(spec, exc) from None
                raise HarnessError(PROVIDER_TIMEOUT, "Provider request exceeded its wall deadline.",
                                   details={"exception": type(exc).__name__}) from None
            except OSError as exc:
                if attempt + 1 >= attempts:
                    raise _local_transport_error(spec, exc) from None
                self._sleep(self._policy.delay(attempt + 1))
                continue
            if not isinstance(result, TransportResult):
                fail(INVALID_MODEL_PROTOCOL, "Transport must return a TransportResult.")
            if 200 <= result.status < 300:
                return result
            if result.status not in RETRYABLE_STATUSES or attempt + 1 >= attempts:
                raise _status_error(spec, result.status, result.body) from None
            self._sleep(self._policy.delay(attempt + 1))
        fail(PROVIDER_TRANSPORT_ERROR, "Provider unavailable after bounded retries.")


class AnthropicTranslator:
    """Anthropic Messages API wire format. Tool schemas become ``input_schema``."""

    def __init__(self, spec: ProviderSpec) -> None:
        self._spec = spec

    def _capability_gate(self, request: ProviderRequest) -> None:
        if request.stream:
            fail(PROVIDER_STREAM_UNSUPPORTED, "The bridge contract is non-streaming only.")
        if request.tools and CAP_TOOL_CALLS not in self._spec.capabilities:
            fail(PROVIDER_UNSUPPORTED_CAPABILITY, "Provider does not advertise tool calling.")
        if request.response_format is not None:
            if CAP_STRUCTURED_OUTPUT not in self._spec.capabilities:
                fail(PROVIDER_UNSUPPORTED_CAPABILITY, "Provider does not advertise structured output.")
            fail(NOT_IMPLEMENTED, "Anthropic structured output is not wired.")

    def to_wire(self, request: ProviderRequest, wire_model: str) -> tuple[str, str]:
        self._capability_gate(request)
        system_parts: list[str] = []
        messages: list[dict[str, Any]] = []
        for message in request.messages:
            role = message.get("role")
            if role == "system":
                system_parts.append(message.get("content") or "")
            elif role == "user":
                messages.append({"role": "user", "content": message.get("content") or ""})
            elif role == "assistant":
                calls = message.get("tool_calls")
                if calls:
                    blocks = [{"type": "tool_use", "id": call["id"],
                               "name": call["function"]["name"],
                               "input": strict_json(call["function"]["arguments"])}
                              for call in calls]
                    messages.append({"role": "assistant", "content": blocks})
                else:
                    messages.append({"role": "assistant", "content": message.get("content") or ""})
            elif role == "tool":
                messages.append({"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": message["tool_call_id"],
                     "content": message.get("content") or ""}]})
            else:
                fail(INVALID_MODEL_PROTOCOL, "Unsupported message role.")
        payload: dict[str, Any] = {"model": wire_model, "max_tokens": request.max_tokens,
                                   "messages": messages, "temperature": 0}
        if system_parts:
            payload["system"] = "\n\n".join(system_parts)
        if request.tools:
            payload["tools"] = [{"name": tool["name"], "description": tool.get("description", ""),
                                 "input_schema": tool.get("parameters", {"type": "object"})}
                                for tool in request.tools]
            payload["tool_choice"] = "auto"
        return "/v1/messages", json.dumps(payload, allow_nan=False)

    def from_wire(self, body: bytes, canonical: str) -> ModelTurn:
        data = _decode_json(body)
        content, calls = "", []
        for block in data.get("content", []):
            if not isinstance(block, dict):
                fail(INVALID_MODEL_PROTOCOL, "Content blocks must be objects.")
            if block.get("type") == "text":
                content += block.get("text") or ""
            elif block.get("type") == "tool_use":
                calls.append({"id": block.get("id"), "type": "function",
                              "function": {"name": block.get("name"),
                                           "arguments": json.dumps(block.get("input") or {}, allow_nan=False)}})
            else:
                fail(INVALID_MODEL_PROTOCOL, "Unsupported content block type.")
        finish = {"end_turn": "stop", "tool_use": "tool_calls"}.get(data.get("stop_reason"))
        if finish is None:
            fail(MODEL_INCOMPLETE, "Truncated or unfinished provider response.")
        return ModelTurn(content, tuple(validate_call(call) for call in calls), canonical,
                         finish_reason=finish, usage=_usage(data.get("usage")))


class OpenAITranslator:
    """OpenAI-compatible chat-completions wire format (cloud and local loopback)."""

    def __init__(self, spec: ProviderSpec) -> None:
        self._spec = spec

    def _capability_gate(self, request: ProviderRequest) -> None:
        if request.stream:
            fail(PROVIDER_STREAM_UNSUPPORTED, "The bridge contract is non-streaming only.")
        if request.tools and CAP_TOOL_CALLS not in self._spec.capabilities:
            fail(PROVIDER_UNSUPPORTED_CAPABILITY, "Provider does not advertise tool calling.")
        if request.response_format is not None and CAP_STRUCTURED_OUTPUT not in self._spec.capabilities:
            fail(PROVIDER_UNSUPPORTED_CAPABILITY, "Provider does not advertise structured output.")

    def to_wire(self, request: ProviderRequest, wire_model: str) -> tuple[str, str]:
        self._capability_gate(request)
        payload: dict[str, Any] = {"model": wire_model,
                                   "messages": [dict(message) for message in request.messages],
                                   "temperature": 0, "max_tokens": request.max_tokens}
        if request.tools:
            payload["tools"] = [{"type": "function",
                                 "function": {"name": tool["name"],
                                              "description": tool.get("description", ""),
                                              "parameters": tool.get("parameters", {"type": "object"})}}
                                for tool in request.tools]
            payload["tool_choice"] = "auto"
        if request.response_format is not None:
            payload["response_format"] = request.response_format
        return "/v1/chat/completions", json.dumps(payload, allow_nan=False)

    def from_wire(self, body: bytes, canonical: str) -> ModelTurn:
        data = _decode_json(body)
        choices = data.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            fail(INVALID_MODEL_PROTOCOL, "Exactly one choice is supported.")
        choice = choices[0]
        message = choice.get("message")
        if not isinstance(message, dict):
            fail(INVALID_MODEL_PROTOCOL, "Choice must carry a message object.")
        content = message.get("content")
        if content is not None and not isinstance(content, str):
            fail(INVALID_MODEL_PROTOCOL, "Content must be text.")
        raw_calls = message.get("tool_calls") or []
        if not isinstance(raw_calls, list):
            fail(INVALID_MODEL_PROTOCOL, "Tool calls must be a list.")
        finish = choice.get("finish_reason")
        if finish not in ("stop", "tool_calls"):
            fail(MODEL_INCOMPLETE, "Truncated or unfinished provider response.")
        return ModelTurn(content or "", tuple(validate_call(call) for call in raw_calls), canonical,
                         finish_reason=finish, usage=_usage(data.get("usage")))


def _decode_json(body: bytes) -> dict[str, Any]:
    try:
        data = strict_json(body.decode("utf-8"))
    except UnicodeError as exc:
        raise HarnessError(INVALID_MODEL_JSON, "Provider response is not valid UTF-8.") from exc
    if not isinstance(data, dict):
        fail(INVALID_MODEL_PROTOCOL, "Provider response must be a JSON object.")
    return data


def _usage(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


class ProviderBridge:
    """ModelRouter over routed providers; no network of its own, no fallback.

    Completing against ``None`` routing keeps the offline contract
    (``NO_MODEL_CONFIGURED``); a local provider failure raises
    ``LOCAL_ENDPOINT_ERROR`` and never retries against a cloud provider.
    Events record bounded metadata only — never payloads, headers or
    credentials.
    """

    def __init__(self, routing: RoutingTable | None, canonical: str | None,
                 transport: Any, credentials: Callable[[ProviderSpec], str | None] | None = None,
                 cancel: CancelFlag | None = None,
                 max_response_bytes: int = MAX_RESPONSE_BYTES_DEFAULT,
                 max_tokens: int = 512) -> None:
        self._routing = routing
        self._canonical = canonical
        self._transport = transport
        self._credentials = credentials
        self._cancel = cancel
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1048576:
            fail(INVALID_LIMIT, "Invalid response byte bound.")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 4096:
            fail(INVALID_LIMIT, "Invalid token bound.")
        self._max_response_bytes = max_response_bytes
        self._max_tokens = max_tokens
        self._events: list[dict[str, Any]] = []

    @property
    def events(self) -> list[dict[str, Any]]:
        return list(self._events)

    def _translator(self, spec: ProviderSpec) -> AnthropicTranslator | OpenAITranslator:
        if spec.kind == KIND_ANTHROPIC:
            return AnthropicTranslator(spec)
        return OpenAITranslator(spec)

    def complete(self, messages: list[dict[str, Any]],
                 tools: list[dict[str, Any]] | tuple[dict[str, Any], ...] = (),
                 response_format: dict[str, Any] | None = None) -> ModelTurn:
        if self._routing is None or not self._canonical:
            fail(NO_MODEL_CONFIGURED,
                 "No provider bridge is configured; NullModel remains the offline default.")
        route, spec = self._routing.resolve(self._canonical)
        tool_tuple = tuple(tools or ())
        if tool_tuple and CAP_TOOL_CALLS not in spec.capabilities:
            fail(PROVIDER_UNSUPPORTED_CAPABILITY, "Provider does not advertise tool calling.")
        credential = None
        if not spec.is_local():
            credential = self._credentials(spec) if self._credentials is not None else None
            if not credential:
                fail(PROVIDER_CREDENTIAL_MISSING, "Provider credentials are missing; nothing was sent.")
        translator = self._translator(spec)
        request = ProviderRequest(messages=copy.deepcopy(list(messages)), tools=tool_tuple,
                                  response_format=response_format, max_tokens=self._max_tokens)
        path, payload = translator.to_wire(request, route.wire_model)
        body_bytes = payload.encode("utf-8")
        if len(body_bytes) > spec.max_request_bytes:
            fail(CONTEXT_OVERFLOW, "Request exceeds the provider context bound; refused before send.")
        headers = {"content-type": "application/json"}
        if spec.kind == KIND_ANTHROPIC:
            headers["x-api-key"] = credential or ""
            headers["anthropic-version"] = ANTHROPIC_VERSION
        elif spec.kind == KIND_OPENAI:
            headers["Authorization"] = f"Bearer {credential}"
        self._events.append({"event": "provider_request", "provider_id": spec.provider_id,
                             "model": self._canonical, "path": path,
                             "request_bytes": len(body_bytes),
                             "request_sha256": hashlib.sha256(body_bytes).hexdigest()})
        started = time.monotonic()
        result = self._transport.send(spec, path, payload, headers, spec.timeout_s, self._cancel)
        if not isinstance(result, TransportResult):
            fail(INVALID_MODEL_PROTOCOL, "Transport must return a TransportResult.")
        if not 200 <= result.status < 300:
            error = _status_error(spec, result.status, result.body)
            self._events.append({"event": "provider_response", "provider_id": spec.provider_id,
                                 "model": self._canonical, "status": result.status,
                                 "error": error.code})
            raise error
        if len(result.body) > self._max_response_bytes:
            fail(MODEL_OUTPUT_LIMIT, "Provider response exceeds the byte bound.")
        turn = translator.from_wire(result.body, self._canonical)
        turn = ModelTurn(turn.content, turn.tool_calls, turn.model_id,
                         finish_reason=turn.finish_reason,
                         latency_s=time.monotonic() - started, usage=turn.usage)
        self._events.append({"event": "provider_response", "provider_id": spec.provider_id,
                             "model": self._canonical, "status": result.status,
                             "finish_reason": turn.finish_reason, "usage": turn.usage})
        return turn


def discover_models(transport: Any, spec: ProviderSpec) -> list[str]:
    """List the model ids a provider currently serves; never a hardcoded catalog."""
    result = transport.send(spec, "/v1/models", None, {}, spec.timeout_s, None)
    if not isinstance(result, TransportResult):
        fail(INVALID_MODEL_PROTOCOL, "Transport must return a TransportResult.")
    if not 200 <= result.status < 300:
        raise _status_error(spec, result.status, result.body)
    data = _decode_json(result.body)
    listing = data.get("data")
    if not isinstance(listing, list):
        fail(INVALID_MODEL_LIST, "Not an OpenAI-compatible models listing.")
    ids = []
    for item in listing:
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            fail(INVALID_MODEL_LIST, "Not an OpenAI-compatible models listing.")
        ids.append(item["id"])
    return ids


def verify_model_available(transport: Any, spec: ProviderSpec, wire_model: str) -> dict[str, Any]:
    """Refuse a route whose wire model is not currently served by its provider."""
    if wire_model not in discover_models(transport, spec):
        fail(MODEL_NOT_SERVED, "Requested wire model id is not currently served.")
    return {"provider_id": spec.provider_id, "wire_model": wire_model, "verified": True}