"""Strict pinned Qwen text/tool protocol and fork-safe ModelRouter client.

The vendor template is XML-like, NOT XML (``function=name``). No XML entity
expansion, JSON repair, inferred arguments, or model-supplied call IDs occur.
Only the closed, string-enum tool schemas used by the read-only harness are
supported. Expanding this surface requires an explicit reviewed schema change.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import os
import re
import socket
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from plat_harness.errors import HarnessError
from plat_harness.models import ModelTurn

MODEL_ID = "Qwen/Qwen3.6-35B-A3B"
REVISION = "995ad96eacd98c81ed38be0c5b274b04031597b0"
TEMPLATE_SHA256 = "e84f32a23fdda27689f868aa4a1a5621f41133e51a48d7f3efcbea2839574259"
MAX_LINE = 131072
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,127}\Z")
CALL_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
RESERVED = re.compile(r"<\|\||\|>|</?(?:tool_call|tool_response|function|parameter|think|tools)(?:[=>\s]|$)", re.I)


def model_path() -> Path:
    """Local model checkpoint path from host configuration at call time.

    PLAT_HARNESS_MODEL_PATH must name the approved local checkpoint directory;
    refusing when unset or empty is the fail-closed default — no host path is
    compiled into the source.
    """
    raw = os.environ.get("PLAT_HARNESS_MODEL_PATH", "")
    if not raw:
        refuse("NATIVE_CONFIG", "PLAT_HARNESS_MODEL_PATH must configure the local model checkpoint directory.")
    return Path(raw)


def runtime_python() -> str:
    """Reviewed tokenizer interpreter path from host configuration at call time.

    PLAT_HARNESS_RUNTIME must name the exact reviewed interpreter; refusing
    when unset or empty is the fail-closed default.
    """
    raw = os.environ.get("PLAT_HARNESS_RUNTIME", "")
    if not raw:
        refuse("NATIVE_CONFIG", "PLAT_HARNESS_RUNTIME must configure the reviewed tokenizer interpreter.")
    return raw


def refuse(code: str, message: str):
    raise HarnessError(code, message)


def dumps(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    except (ValueError, TypeError, RecursionError) as exc:
        raise HarnessError("NATIVE_JSON", "Invalid JSON value.") from exc


def loads(raw: str | bytes) -> Any:
    def pairs(items):
        out = {}
        for k, v in items:
            if k in out:
                refuse("NATIVE_JSON", "Duplicate JSON key.")
            out[k] = v
        return out

    def number(s):
        value = float(s)
        if not math.isfinite(value):
            refuse("NATIVE_JSON", "Nonfinite or overflow number.")
        return value

    def constant(_):
        refuse("NATIVE_JSON", "Nonfinite constant.")

    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_float=number, parse_constant=constant)
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise HarnessError("NATIVE_JSON", "Malformed JSON; no defaults used.") from exc


def safe_text(value, *, empty=True):
    if not isinstance(value, str) or (not empty and not value) or RESERVED.search(value) or "\x00" in value:
        refuse("NATIVE_SENTINEL", "Text contains structural markup or is not plain text.")
    return value


def schemas(tools: list[dict]) -> dict[str, dict]:
    """Deliberately reject unsupported JSON-schema semantics, not ignore them."""
    if not isinstance(tools, list) or not 1 <= len(tools) <= 4:
        refuse("NATIVE_SCHEMA", "One to four closed read-only tool schemas required.")
    result = {}
    for tool in tools:
        if not isinstance(tool, dict) or set(tool) != {"type", "function"} or tool["type"] != "function":
            refuse("NATIVE_SCHEMA", "Invalid function schema wrapper.")
        fn = tool["function"]
        if not isinstance(fn, dict) or set(fn) != {"name", "description", "parameters"}:
            refuse("NATIVE_SCHEMA", "Invalid function fields.")
        name = fn["name"]
        if not isinstance(name, str) or not IDENT.fullmatch(name) or name in result:
            refuse("NATIVE_SCHEMA", "Invalid or duplicate function name.")
        safe_text(fn["description"])
        params = fn["parameters"]
        if (not isinstance(params, dict) or set(params) != {"type", "properties", "required", "additionalProperties"}
                or params["type"] != "object" or params["additionalProperties"] is not False):
            refuse("NATIVE_SCHEMA", "Only exact closed object schemas are supported.")
        props, required = params["properties"], params["required"]
        if (not isinstance(props, dict) or not props or not isinstance(required, list)
                or any(not isinstance(k, str) for k in required)
                or len(set(required)) != len(required) or set(required) != set(props)):
            refuse("NATIVE_SCHEMA", "All explicit properties must be required exactly once.")
        for key, spec in props.items():
            if not IDENT.fullmatch(key) or not isinstance(spec, dict) or set(spec) != {"type", "enum"} or spec["type"] != "string":
                refuse("NATIVE_SCHEMA", "Only named string-enum arguments supported.")
            values = spec["enum"]
            if (not isinstance(values, list) or not values or any(not isinstance(v, str) for v in values)
                    or len(set(values)) != len(values)):
                refuse("NATIVE_SCHEMA", "Invalid argument enum.")
            for value in values:
                safe_text(value, empty=False)
                if value != value.strip():
                    refuse("NATIVE_SCHEMA", "Whitespace-bearing enums are ambiguous in vendor markup.")
        result[name] = params
    return result


def arguments(name: str, args: Any, allowed: dict):
    if name not in allowed:
        refuse("NATIVE_UNAUTHORIZED", "Tool name is outside host allowlist.")
    spec = allowed[name]
    if not isinstance(args, dict) or set(args) != set(spec["required"]):
        refuse("NATIVE_ARGUMENTS", "Missing or unknown arguments; no defaults applied.")
    for key, value in args.items():
        if not isinstance(value, str) or value not in spec["properties"][key]["enum"]:
            refuse("NATIVE_ARGUMENTS", "Argument does not match its exact host enum.")
        safe_text(value)
    return args


def normalize(messages: list[dict], tools: list[dict]) -> list[dict]:
    """Deep-copy only; preserve original IDs/roles and validate ordered responses.

    The vendor renderer omits call IDs. The host ledger retains them; tool-result
    JSON must carry the host answer_from ID for the subsequent selection turn.
    """
    allowed = schemas(tools)
    if not isinstance(messages, list) or not 1 <= len(messages) <= 32:
        refuse("NATIVE_MESSAGES", "Invalid message count.")
    result = copy.deepcopy(messages)
    seen, pending = set(), []
    user_seen = False
    for index, msg in enumerate(result):
        if not isinstance(msg, dict):
            refuse("NATIVE_MESSAGES", "Message must be an object.")
        role = msg.get("role")
        fields = {"role", "content"}
        if role == "assistant":
            fields.add("tool_calls")
        elif role == "tool":
            fields.add("tool_call_id")
        elif role not in ("system", "user"):
            refuse("NATIVE_MESSAGES", "Unsupported role.")
        if set(msg) - fields or not {"role", "content"} <= set(msg):
            refuse("NATIVE_MESSAGES", "Unknown or missing message fields.")
        if role == "system" and index != 0:
            refuse("NATIVE_MESSAGES", "System message must be first.")
        if pending and role != "tool":
            refuse("NATIVE_MESSAGES", "Outstanding tool responses must be consecutive.")
        if role == "user":
            user_seen = True
        content = msg["content"]
        if content is None and role == "assistant" and msg.get("tool_calls"):
            content = ""
        safe_text(content)
        # Preserve None in the normalized message; the real template handles it.
        if role == "assistant":
            calls = msg.get("tool_calls", [])
            if not isinstance(calls, list) or len(calls) > 2:
                refuse("NATIVE_CALLS", "Invalid call batch.")
            signatures = set()
            for call in calls:
                if not isinstance(call, dict) or set(call) != {"id", "type", "function"} or call["type"] != "function":
                    refuse("NATIVE_CALLS", "Invalid tool call wrapper.")
                cid = call["id"]
                if not isinstance(cid, str) or not CALL_ID.fullmatch(cid) or cid in seen:
                    refuse("NATIVE_CALLS", "Invalid or duplicate call ID.")
                fn = call["function"]
                if not isinstance(fn, dict) or set(fn) != {"name", "arguments"} or not isinstance(fn["name"], str):
                    refuse("NATIVE_CALLS", "Invalid function fields.")
                args = loads(fn["arguments"]) if isinstance(fn["arguments"], str) else loads(dumps(fn["arguments"]))
                arguments(fn["name"], args, allowed)
                signature = (fn["name"], dumps(sorted(args.items())))
                if signature in signatures:
                    refuse("NATIVE_CALLS", "Duplicate function/argument call.")
                signatures.add(signature)
                fn["arguments"] = args
                seen.add(cid)
                pending.append(cid)
        if role == "tool":
            cid = msg.get("tool_call_id")
            if not pending or cid != pending[0]:
                refuse("NATIVE_MESSAGES", "Unmatched, duplicate or reordered tool response.")
            pending.pop(0)
    if not user_seen or pending or result[-1]["role"] not in ("user", "tool"):
        refuse("NATIVE_MESSAGES", "Generation requires a user query or completed tool responses.")
    if len(dumps(result).encode()) > MAX_LINE // 2:
        refuse("NATIVE_INPUT_LIMIT", "Messages exceed byte bound.")
    return result


@dataclass(frozen=True)
class Prompt:
    text: str
    input_ids: list[int]
    messages: list[dict]
    schema_sha256: str


def render_prompt(tokenizer, messages, tools) -> Prompt:
    normalized = normalize(messages, tools)
    if hashlib.sha256(tokenizer.chat_template.encode()).hexdigest() != TEMPLATE_SHA256:
        refuse("NATIVE_TEMPLATE", "Chat template differs from the pinned checkpoint.")
    text = tokenizer.apply_chat_template(normalized, tools=copy.deepcopy(tools), tokenize=False,
                                         add_generation_prompt=True, enable_thinking=False,
                                         preserve_thinking=False)
    ids = tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]
    if not 1 <= len(ids) <= 1536 or len(ids) + 512 > 2048:
        refuse("NATIVE_PROMPT_LIMIT", "Full prompt including schemas exceeds 1536 tokens; never truncated.")
    return Prompt(text, ids, normalized, hashlib.sha256(dumps(tools).encode()).hexdigest())


def parse_output(text: str, tools: list[dict], *, request_id: str,
                 finish_reason: str, generated_tokens: int) -> ModelTurn:
    allowed = schemas(tools)
    if finish_reason != "stop" or type(generated_tokens) is not int or not 0 < generated_tokens < 512:
        refuse("NATIVE_INCOMPLETE", "Missing EOS or output token ceiling reached.")
    if not isinstance(request_id, str) or not CALL_ID.fullmatch(request_id) or len(request_id) > 80:
        refuse("NATIVE_PROTOCOL", "Invalid host request ID.")
    if not isinstance(text, str) or not text.strip() or len(text.encode()) > 32768:
        refuse("NATIVE_OUTPUT_LIMIT", "Empty or oversized response.")
    # Thinking was already closed by the generation prefix. Any extra structural
    # markers outside the exact function grammar are an error, never stripped.
    stripped = text.strip()
    if not stripped.startswith("<tool_call>"):
        safe_text(text, empty=False)
        return ModelTurn(text, model_id=MODEL_ID, finish_reason="stop")
    pattern = re.compile(r"<tool_call>\s*<function=([A-Za-z_][A-Za-z0-9_]*)>\s*(.*?)\s*</function>\s*</tool_call>", re.S)
    parameter = re.compile(r"<parameter=([A-Za-z_][A-Za-z0-9_]*)>\n(.*?)\n</parameter>", re.S)
    cursor, calls, signatures = 0, [], set()
    while cursor < len(stripped):
        match = pattern.match(stripped, cursor)
        if not match:
            refuse("NATIVE_CALLS", "Malformed or truncated function block; suffix/prose forbidden.")
        name, body = match.groups()
        if name not in allowed:
            refuse("NATIVE_UNAUTHORIZED", "Tool name is outside host allowlist.")
        args, pos = {}, 0
        while pos < len(body):
            p = parameter.match(body, pos)
            if not p:
                refuse("NATIVE_CALLS", "Malformed parameter block.")
            key, value = p.groups()
            if key in args:
                refuse("NATIVE_CALLS", "Duplicate parameter.")
            safe_text(value)
            args[key] = value
            pos = p.end()
            while pos < len(body) and body[pos].isspace():
                pos += 1
        arguments(name, args, allowed)
        signature = (name, dumps(sorted(args.items())))
        if signature in signatures:
            refuse("NATIVE_CALLS", "Duplicate function/argument call.")
        signatures.add(signature)
        if len(calls) >= 2:
            refuse("NATIVE_CALLS", "Maximum two calls per turn.")
        calls.append({"id": f"native_{request_id}_{len(calls)}", "type": "function",
                      "function": {"name": name, "arguments": dumps(args)}})
        cursor = match.end()
        while cursor < len(stripped) and stripped[cursor].isspace():
            cursor += 1
    return ModelTurn("", tuple(calls), MODEL_ID, finish_reason="tool_calls")


def validate_response(response, tools, request_id):
    if not isinstance(response, dict) or response.get("id") != request_id:
        refuse("NATIVE_PROTOCOL", "Response identity mismatch.")
    if response.get("type") == "error":
        refuse("NATIVE_WORKER_ERROR", str(response.get("code", "worker failed"))[:128])
    if set(response) != {"type", "id", "model_id", "revision", "text", "finish_reason", "prompt_tokens", "generated_tokens", "generation_s"}:
        refuse("NATIVE_PROTOCOL", "Unexpected response fields.")
    if response["type"] != "result" or response["model_id"] != MODEL_ID or response["revision"] != REVISION:
        refuse("NATIVE_PROTOCOL", "Worker identity mismatch.")
    if type(response["prompt_tokens"]) is not int or not 1 <= response["prompt_tokens"] <= 1536:
        refuse("NATIVE_PROTOCOL", "Invalid prompt token count.")
    latency = response["generation_s"]
    if type(latency) not in (float, int) or not math.isfinite(latency) or not 0 <= latency <= 180:
        refuse("NATIVE_PROTOCOL", "Invalid generation measurement.")
    turn = parse_output(response["text"], tools, request_id=request_id,
                        finish_reason=response["finish_reason"], generated_tokens=response["generated_tokens"])
    return ModelTurn(turn.content, turn.tool_calls, MODEL_ID, finish_reason=turn.finish_reason,
                     latency_s=latency, ttft_s=None,
                     usage={"prompt_tokens": response["prompt_tokens"], "completion_tokens": response["generated_tokens"]})


class NativeModel:
    """Socket-only ModelRouter; safe across the existing loop's fork boundary.

    A separately launched, finite-lifetime native_supervisor owns every worker
    PID. Killing this client closes its socket, which cancels and kills the
    entire worker group; generation cannot outlive a timed-out loop wrapper.
    """
    model_id = MODEL_ID
    max_tokens = 512
    endpoint = None

    def __init__(self, socket_path: str, *, timeout_s: float = 180):
        if (not isinstance(socket_path, str) or len(socket_path.encode()) > 107
                or not (socket_path.startswith("/") or re.fullmatch(r"@plat-native-[a-f0-9]{32}", socket_path))):
            refuse("NATIVE_SOCKET", "Use a bounded local Unix socket or host-generated abstract endpoint.")
        if type(timeout_s) not in (float, int) or not math.isfinite(timeout_s) or not 0 < timeout_s <= 180:
            refuse("NATIVE_LIMIT", "Invalid generation deadline.")
        self.socket_path, self.timeout_s = socket_path, timeout_s

    def complete(self, messages, tools) -> ModelTurn:
        normalize(messages, tools)
        rid = uuid.uuid4().hex
        payload = dumps({"op": "generate", "id": rid, "messages": messages, "tools": tools}).encode() + b"\n"
        if len(payload) > MAX_LINE:
            refuse("NATIVE_INPUT_LIMIT", "Request exceeds line bound.")
        start = time.monotonic()
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as conn:
                conn.settimeout(self.timeout_s)
                conn.connect("\0" + self.socket_path[1:] if self.socket_path.startswith("@") else self.socket_path)
                conn.sendall(payload)
                raw = bytearray()
                while not raw.endswith(b"\n"):
                    remaining = self.timeout_s - (time.monotonic() - start)
                    if remaining <= 0:
                        refuse("NATIVE_TIMEOUT", "Client generation deadline exceeded.")
                    conn.settimeout(remaining)
                    part = conn.recv(min(4096, MAX_LINE + 1 - len(raw)))
                    if not part:
                        refuse("NATIVE_PROTOCOL", "Supervisor disconnected without response.")
                    raw.extend(part)
                    if len(raw) > MAX_LINE or b"\n" in raw[:-1]:
                        refuse("NATIVE_OUTPUT_LIMIT", "Invalid JSONL frame.")
        except (OSError, TimeoutError) as exc:
            raise HarnessError("NATIVE_TRANSPORT", "Supervisor unavailable or timed out; no fallback.") from exc
        return validate_response(loads(raw), tools, rid)
