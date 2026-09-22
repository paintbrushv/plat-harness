"""Small local OpenAI-protocol backend. No SDK, proxy, redirect or fallback.

Only literal loopback IPs are accepted deliberately: private IP alone is not
proof of ownership. Exact model identity must pass a health/listing probe before
any prompt is sent. The loop supervisor supplies a hard wall-clock deadline.
"""
from __future__ import annotations

import ipaddress
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from plat_harness.errors import HarnessError
from plat_harness.models import ModelTurn


def fail(code: str, message: str) -> None:
    raise HarnessError(code, message)


def strict_json(text: str) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                fail("INVALID_MODEL_JSON", "Duplicate JSON key.")
            result[key] = value
        return result

    def constant(_):
        fail("INVALID_MODEL_JSON", "Non-finite JSON number.")

    try:
        return json.loads(text, object_pairs_hook=pairs, parse_constant=constant)
    except (ValueError, TypeError, RecursionError) as exc:
        raise HarnessError("INVALID_MODEL_JSON", "Malformed JSON; no default arguments used.") from exc


def validate_call(call: dict) -> dict:
    if not isinstance(call, dict) or set(call) != {"id", "type", "function"}:
        fail("INVALID_TOOL_CALL", "Unexpected tool call fields.")
    if call["type"] != "function" or not isinstance(call["id"], str) or not 1 <= len(call["id"]) <= 128:
        fail("INVALID_TOOL_CALL", "Invalid tool identity.")
    fn = call["function"]
    if not isinstance(fn, dict) or set(fn) != {"name", "arguments"}:
        fail("INVALID_TOOL_CALL", "Unexpected function fields.")
    if not isinstance(fn["name"], str) or not 1 <= len(fn["name"]) <= 128:
        fail("INVALID_TOOL_CALL", "Invalid function name.")
    if not isinstance(fn["arguments"], str) or len(fn["arguments"]) > 8192:
        fail("INVALID_TOOL_CALL", "Arguments must be bounded JSON text.")
    if not isinstance(strict_json(fn["arguments"]), dict):
        fail("INVALID_TOOL_CALL", "Arguments must be an object.")
    return call


class FragmentAssembler:
    """Assemble by index, reject malformed/incomplete arguments, never repair."""
    def __init__(self, max_calls: int = 2):
        self.calls: dict[int, dict] = {}
        self.max_calls = max_calls

    def add(self, deltas: list) -> None:
        if not isinstance(deltas, list):
            fail("INVALID_TOOL_CALL", "Tool deltas must be a list.")
        for delta in deltas:
            if not isinstance(delta, dict) or set(delta) - {"index", "id", "type", "function"}:
                fail("INVALID_TOOL_CALL", "Invalid streamed tool delta.")
            index = delta.get("index")
            if type(index) is not int or not 0 <= index < self.max_calls:
                fail("TOOL_CALL_LIMIT", "Tool index exceeds the call bound.")
            call = self.calls.setdefault(index, {"id": "", "type": "function", "function": {"name": "", "arguments": ""}})
            if delta.get("type", "function") != "function":
                fail("INVALID_TOOL_CALL", "Unsupported tool type.")
            fn = delta.get("function") or {}
            if not isinstance(fn, dict) or set(fn) - {"name", "arguments"}:
                fail("INVALID_TOOL_CALL", "Invalid function delta.")
            for target, key, value, bound in [(call, "id", delta.get("id"), 128),
                                              (call["function"], "name", fn.get("name"), 128),
                                              (call["function"], "arguments", fn.get("arguments"), 8192)]:
                if value is not None:
                    if not isinstance(value, str):
                        fail("INVALID_TOOL_CALL", "Tool fragment is not text.")
                    target[key] += value
                    if len(target[key]) > bound:
                        fail("MODEL_OUTPUT_LIMIT", "Tool fragments exceed bound.")

    def finish(self) -> tuple[dict, ...]:
        if sorted(self.calls) != list(range(len(self.calls))):
            fail("INVALID_TOOL_CALL", "Missing tool call index.")
        calls = tuple(validate_call(self.calls[i]) for i in sorted(self.calls))
        if len({c["id"] for c in calls}) != len(calls):
            fail("INVALID_TOOL_CALL", "Duplicate tool call id.")
        return calls


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        fail("ENDPOINT_REDIRECT_DENIED", "Local endpoint redirects are forbidden.")


class LocalModel:
    """Replaceable ModelRouter, exact server model ID (no normalization)."""
    def __init__(self, endpoint: str, model_id: str, *, timeout_s: float = 30,
                 max_tokens: int = 512, max_response_bytes: int = 131072):
        try:
            url = urllib.parse.urlsplit(endpoint)
            address = ipaddress.ip_address(url.hostname or "")
            valid = (url.scheme == "http" and address.is_loopback and url.port is not None
                     and url.path in ("", "/") and not url.query and not url.fragment
                     and url.username is None and url.password is None)
        except ValueError:
            valid = False
        if not valid:
            fail("ENDPOINT_NOT_LOCAL", "Use an explicit http loopback-IP:port origin, without path or credentials.")
        if not isinstance(model_id, str) or not model_id or len(model_id) > 512:
            fail("MODEL_ID_REQUIRED", "Use the exact server-listed model ID.")
        if not 0 < timeout_s <= 120 or type(max_tokens) is not int or not 1 <= max_tokens <= 2048:
            fail("INVALID_LIMIT", "Invalid model timeout/token limit.")
        if type(max_response_bytes) is not int or not 1024 <= max_response_bytes <= 1048576:
            fail("INVALID_LIMIT", "Invalid response limit.")
        self.endpoint, self.model_id = endpoint.rstrip("/"), model_id
        self.timeout_s, self.max_tokens, self.max_response_bytes = timeout_s, max_tokens, max_response_bytes
        self.validated = False
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())

    def _open(self, route: str, payload: dict | None = None):
        data = json.dumps(payload, allow_nan=False).encode() if payload is not None else None
        if data is not None and len(data) > 131072:
            fail("MODEL_INPUT_LIMIT", "Request exceeds input byte limit.")
        req = urllib.request.Request(self.endpoint + route, data=data,
                                     headers={"Content-Type": "application/json", "Accept": "application/json"})
        try:
            return self.opener.open(req, timeout=self.timeout_s)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            raise HarnessError("LOCAL_ENDPOINT_ERROR", "Local endpoint request failed; no fallback attempted.",
                               details={"exception": type(exc).__name__}) from exc

    def validate(self) -> dict:
        self.validated = False
        with self._open("/health") as response:
            if len(response.read(self.max_response_bytes + 1)) > self.max_response_bytes:
                fail("MODEL_OUTPUT_LIMIT", "Health response exceeds bound.")
        with self._open("/v1/models") as response:
            raw = response.read(self.max_response_bytes + 1)
        if len(raw) > self.max_response_bytes:
            fail("MODEL_OUTPUT_LIMIT", "Models response exceeds bound.")
        try:
            listing = strict_json(raw.decode("utf-8"))
            ids = [item["id"] for item in listing["data"]]
        except (UnicodeError, KeyError, TypeError) as exc:
            raise HarnessError("INVALID_MODEL_LIST", "Not an OpenAI-compatible models listing.") from exc
        if self.model_id not in ids:
            fail("MODEL_NOT_SERVED", "Requested exact model ID is not listed.")
        self.validated = True
        return {"endpoint": self.endpoint, "model_id": self.model_id, "validated": True}

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelTurn:
        # Revalidate each request: validation cannot be inherited indefinitely.
        self.validate()
        payload = {"model": self.model_id, "messages": messages, "tools": tools,
                   "tool_choice": "auto", "temperature": 0, "max_tokens": self.max_tokens,
                   "stream": True, "stream_options": {"include_usage": True}}
        t0 = time.monotonic()
        assembler, content, ttft, usage, finish, done, size = FragmentAssembler(), "", None, {}, None, False, 0
        with self._open("/v1/chat/completions", payload) as response:
            if "text/event-stream" not in response.headers.get("Content-Type", ""):
                fail("INVALID_MODEL_PROTOCOL", "Expected bounded SSE response.")
            while True:
                line = response.readline(self.max_response_bytes + 1)
                size += len(line)
                if size > self.max_response_bytes:
                    fail("MODEL_OUTPUT_LIMIT", "Stream exceeds response bound.")
                if time.monotonic() - t0 > self.timeout_s:
                    fail("MODEL_TIMEOUT", "Model stream exceeded wall deadline.")
                if not line:
                    break
                try:
                    text = line.decode("utf-8").strip()
                except UnicodeError:
                    fail("INVALID_MODEL_PROTOCOL", "Invalid UTF-8 stream.")
                if not text or text.startswith(":"):
                    continue
                if not text.startswith("data:"):
                    fail("INVALID_MODEL_PROTOCOL", "Unsupported SSE field.")
                text = text[5:].strip()
                if text == "[DONE]":
                    done = True
                    break
                chunk = strict_json(text)
                if not isinstance(chunk, dict) or chunk.get("error"):
                    fail("INVALID_MODEL_PROTOCOL", "Invalid stream event.")
                if chunk.get("model", self.model_id) != self.model_id:
                    fail("MODEL_ID_MISMATCH", "Completion identity differs from selected model.")
                if chunk.get("usage") is not None:
                    usage = chunk["usage"]
                choices = chunk.get("choices", [])
                if not isinstance(choices, list) or len(choices) > 1:
                    fail("INVALID_MODEL_PROTOCOL", "Only a single choice is supported.")
                if not choices:
                    continue
                choice = choices[0]
                if not isinstance(choice, dict) or choice.get("index", 0) != 0:
                    fail("INVALID_MODEL_PROTOCOL", "Invalid choice index.")
                delta = choice.get("delta", {})
                if not isinstance(delta, dict):
                    fail("INVALID_MODEL_PROTOCOL", "Invalid delta.")
                if ttft is None and any(delta.get(k) for k in ("content", "reasoning", "reasoning_content", "tool_calls")):
                    ttft = time.monotonic() - t0
                part = delta.get("content") or ""
                if not isinstance(part, str):
                    fail("INVALID_MODEL_PROTOCOL", "Content must be text.")
                content += part
                if delta.get("tool_calls") is not None:
                    assembler.add(delta["tool_calls"])
                # Reasoning is not persisted or returned to callers.
                if choice.get("finish_reason") is not None:
                    finish = choice["finish_reason"]
        if not done or finish not in ("stop", "tool_calls"):
            fail("MODEL_INCOMPLETE", "Truncated or unfinished model response.")
        calls = assembler.finish()
        if bool(calls) != (finish == "tool_calls"):
            fail("INVALID_MODEL_PROTOCOL", "Finish reason/tool-call mismatch.")
        return ModelTurn(content, calls, self.model_id, finish_reason=finish,
                         latency_s=time.monotonic() - t0, ttft_s=ttft, usage=usage)
