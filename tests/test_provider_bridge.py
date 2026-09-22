"""Synthetic-only contract tests for the provider capability/routing bridge (Task 5.3).

No live providers, no real credentials, no network: every transport is a fake
holding recorded synthetic responses. Provider/model names are synthetic, not
historical vendor names. Canaries below must stay absent from bridge events,
exception messages, exception details and captured wire records. The bridge
must never fall back from a failing local provider to a cloud provider.
"""
from __future__ import annotations

import json

import pytest

from plat_harness.adapters.provider_bridge import (
    BoundedRetryPolicy,
    CAP_STRUCTURED_OUTPUT,
    CAP_TOOL_CALLS,
    CONTEXT_OVERFLOW,
    ENDPOINT_NOT_ALLOWED,
    ENDPOINT_NOT_LOCAL,
    INVALID_LIMIT,
    INVALID_MODEL_ID,
    INVALID_MODEL_JSON,
    INVALID_MODEL_LIST,
    INVALID_PROVIDER,
    AnthropicTranslator,
    CancelFlag,
    OpenAITranslator,
    ProviderBridge,
    ProviderRegistry,
    ProviderRequest,
    ProviderSpec,
    RetryingTransport,
    RoutingTable,
    TransportResult,
    discover_models,
    verify_model_available,
)
from plat_harness.errors import HarnessError, NO_MODEL_CONFIGURED
from plat_harness.models import ModelTurn, NullModel

ANTHROPIC = ProviderSpec(
    provider_id="anthropic-cloud",
    kind="anthropic",
    capabilities=frozenset({CAP_TOOL_CALLS}),
    api_key_env="ANTHROPIC_API_KEY",
)
OPENAI = ProviderSpec(
    provider_id="openai-cloud",
    kind="openai",
    capabilities=frozenset({CAP_TOOL_CALLS, CAP_STRUCTURED_OUTPUT}),
    api_key_env="OPENAI_API_KEY",
)
NO_TOOLS = ProviderSpec(
    provider_id="openai-notools",
    kind="openai",
    capabilities=frozenset(),
    api_key_env="OPENAI_API_KEY",
)
LOCAL = ProviderSpec(
    provider_id="local-vllm",
    kind="local-openai",
    capabilities=frozenset({CAP_TOOL_CALLS}),
    base_url="http://127.0.0.1:8000",
)

ANTHROPIC_TEXT = {
    "id": "msg_synth",
    "model": "synth-model-a",
    "content": [{"type": "text", "text": "host-rendered"}],
    "stop_reason": "end_turn",
    "usage": {"input_tokens": 11, "output_tokens": 7},
}
ANTHROPIC_TOOL = {
    "id": "msg_synth",
    "model": "synth-model-a",
    "content": [
        {
            "type": "tool_use",
            "id": "call_synth_1",
            "name": "lookup_metric",
            "input": {"metric_id": "synthetic.metric", "asset_or_deal_id": "SYNTH-1"},
        }
    ],
    "stop_reason": "tool_use",
    "usage": {"input_tokens": 20, "output_tokens": 9},
}
OPENAI_TEXT = {
    "model": "synth-model-o",
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "host-rendered"},
         "finish_reason": "stop"}
    ],
    "usage": {"prompt_tokens": 11, "completion_tokens": 7},
}
OPENAI_TOOL = {
    "model": "synth-model-o",
    "choices": [
        {"index": 0,
         "message": {"role": "assistant", "content": None, "tool_calls": [
             {"id": "call_synth_1", "type": "function",
              "function": {"name": "lookup_metric",
                           "arguments": json.dumps({"metric_id": "synthetic.metric",
                                                    "asset_or_deal_id": "SYNTH-1"})}}]},
         "finish_reason": "tool_calls"}
    ],
    "usage": {"prompt_tokens": 20, "completion_tokens": 9},
}

MESSAGES = [
    {"role": "system", "content": "system prompt"},
    {"role": "user", "content": "question"},
]
TOOLS = [
    {
        "name": "lookup_metric",
        "description": "synthetic tool",
        "parameters": {"type": "object", "properties": {"metric_id": {"type": "string"}}},
    }
]


class FakeTransport:
    """Records every send; replays a scripted sequence of results/exceptions."""

    def __init__(self, script=None):
        self.calls = []
        self.script = list(script or [])

    def send(self, spec, path, payload, headers, timeout_s, cancel):
        self.calls.append({"provider_id": spec.provider_id, "path": path,
                           "payload": payload, "headers": dict(headers)})
        if not self.script:
            return TransportResult(200, json.dumps(OPENAI_TEXT).encode())
        item = self.script.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, TransportResult):
            return item
        return TransportResult(200, json.dumps(item).encode())


class DispatchTransport:
    """One transport per provider id; used to prove no cross-provider fallback."""

    def __init__(self, mapping):
        self.mapping = mapping

    def send(self, spec, path, payload, headers, timeout_s, cancel):
        return self.mapping[spec.provider_id].send(
            spec, path, payload, headers, timeout_s, cancel)


def make_routing():
    registry = ProviderRegistry()
    for spec in (ANTHROPIC, OPENAI, NO_TOOLS, LOCAL):
        registry.register(spec)
    from plat_harness.adapters.provider_bridge import ModelRoute

    routes = {
        "anthropic/synth-model": ModelRoute("anthropic/synth-model", "anthropic-cloud", "synth-model-a"),
        "openai/synth-model": ModelRoute("openai/synth-model", "openai-cloud", "synth-model-o"),
        "openai/notools": ModelRoute("openai/notools", "openai-notools", "synth-notools"),
        "local/synth-model": ModelRoute("local/synth-model", "local-vllm", "synth-model-l"),
    }
    return RoutingTable(registry, routes)


def make_bridge(canonical, script=None, transport=None, credentials=None, **kw):
    routing = None if canonical is None else make_routing()
    wire = transport or FakeTransport(script)
    if credentials is None:
        credentials = lambda spec: "sk-synth-test"  # noqa: E731
    bridge = ProviderBridge(routing, canonical, wire, credentials=credentials, **kw)
    return bridge, wire


# ---------------------------------------------------------------- endpoints


@pytest.mark.parametrize("base_url", [
    "https://evil.example.com", "http://api.anthropic.com", "https://api.anthropic.com/",
])
def test_anthropic_official_origin_only(base_url):
    with pytest.raises(HarnessError) as exc:
        ProviderSpec(provider_id="anthropic-cloud", kind="anthropic",
                     capabilities=frozenset({CAP_TOOL_CALLS}), base_url=base_url,
                     api_key_env="ANTHROPIC_API_KEY")
    assert exc.value.code == ENDPOINT_NOT_ALLOWED


@pytest.mark.parametrize("base_url", [
    "https://api.openai.com/v2", "http://api.openai.com", "https://evil.example.com",
])
def test_openai_official_origin_only(base_url):
    with pytest.raises(HarnessError) as exc:
        ProviderSpec(provider_id="openai-cloud", kind="openai",
                     capabilities=frozenset({CAP_TOOL_CALLS}), base_url=base_url,
                     api_key_env="OPENAI_API_KEY")
    assert exc.value.code == ENDPOINT_NOT_ALLOWED


@pytest.mark.parametrize("base_url", [
    "http://localhost:11434",           # hostname, not a literal loopback IP
    "http://10.0.0.5:8000",             # private, not loopback
    "http://169.254.169.254:80",        # cloud metadata endpoint (SSRF)
    "https://127.0.0.1:8000",           # must be plain http
    "http://127.0.0.1:8000/v1",         # path is not allowed
    "http://127.0.0.1",                 # port required
    "http://user:pw@127.0.0.1:8000",    # credentials in URL refused
    "ftp://127.0.0.1:8000",
])
def test_local_endpoint_policy(base_url):
    with pytest.raises(HarnessError) as exc:
        ProviderSpec(provider_id="local-vllm", kind="local-openai",
                     capabilities=frozenset({CAP_TOOL_CALLS}), base_url=base_url)
    assert exc.value.code in (ENDPOINT_NOT_LOCAL, INVALID_PROVIDER)


def test_local_loopback_accepted():
    spec = ProviderSpec(provider_id="local-vllm", kind="local-openai",
                        capabilities=frozenset({CAP_TOOL_CALLS}),
                        base_url="http://127.0.0.1:8000")
    assert spec.base_url == "http://127.0.0.1:8000"


@pytest.mark.parametrize("kwargs", [
    {"timeout_s": 0}, {"timeout_s": 121}, {"max_request_bytes": 100},
    {"max_request_bytes": 2000000}, {"max_retries": 9}, {"max_retries": -1},
])
def test_spec_bounds(kwargs):
    with pytest.raises(HarnessError) as exc:
        ProviderSpec(provider_id="local-vllm", kind="local-openai",
                     capabilities=frozenset(), base_url="http://127.0.0.1:8000",
                     **kwargs)
    assert exc.value.code == INVALID_LIMIT


@pytest.mark.parametrize("capabilities", [frozenset({"flying"}), frozenset({"tool_calls", "bad_cap"})])
def test_unknown_capability_refused(capabilities):
    with pytest.raises(HarnessError) as exc:
        ProviderSpec(provider_id="x", kind="openai", capabilities=capabilities,
                     api_key_env="OPENAI_API_KEY")
    assert exc.value.code == INVALID_PROVIDER


@pytest.mark.parametrize("kind", ["anthropic", "openai"])
def test_cloud_requires_explicit_credential_name(kind):
    with pytest.raises(HarnessError) as exc:
        ProviderSpec(provider_id="x", kind=kind, capabilities=frozenset({CAP_TOOL_CALLS}))
    assert exc.value.code == INVALID_PROVIDER


# ------------------------------------------------------- model id / routing


@pytest.mark.parametrize("canonical", [
    "gpt-synth", "OpenAI/gpt-synth", "openai/", "/gpt-synth", "openai/-gpt",
    "openai/gpt/synth",
])
def test_canonical_model_id_format(canonical):
    from plat_harness.adapters.provider_bridge import ModelRoute

    with pytest.raises(HarnessError) as exc:
        ModelRoute(canonical, "openai-cloud", "synth-model-o")
    assert exc.value.code == INVALID_MODEL_ID


def test_unknown_model_refused():
    bridge, _ = make_bridge("anthropic/missing")
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "UNKNOWN_MODEL"


def test_registry_returns_isolated_copies():
    registry = ProviderRegistry()
    registry.register(ANTHROPIC)
    with pytest.raises(HarnessError):
        registry.register(ANTHROPIC)  # duplicate provider id
    specs = registry.specs()
    specs["anthropic-cloud"] = "tampered"
    specs["injected"] = LOCAL
    fresh = registry.specs()
    assert fresh == {"anthropic-cloud": ANTHROPIC}
    got = registry.get("anthropic-cloud")
    assert got == ANTHROPIC and got is not fresh["anthropic-cloud"]
    with pytest.raises(HarnessError) as exc:
        registry.get("nope")
    assert exc.value.code == INVALID_PROVIDER


def test_routing_resolve_returns_isolated_copies():
    table = make_routing()
    first_route, first_spec = table.resolve("local/synth-model")
    second_route, second_spec = table.resolve("local/synth-model")
    assert first_route == second_route and first_route is not second_route
    assert first_spec == second_spec and first_spec is not second_spec


def test_routing_rejects_unknown_provider():
    from plat_harness.adapters.provider_bridge import ModelRoute

    registry = ProviderRegistry()
    registry.register(LOCAL)
    with pytest.raises(HarnessError) as exc:
        RoutingTable(registry, {"local/synth-model": ModelRoute("local/synth-model", "ghost", "x")})
    assert exc.value.code == INVALID_PROVIDER


# --------------------------------------------------------------- capability


def test_tools_refused_without_capability():
    bridge, wire = make_bridge("openai/notools", script=[OPENAI_TEXT])
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_UNSUPPORTED_CAPABILITY"
    assert wire.calls == []  # refused before any send


def test_structured_output_refused_without_capability():
    request = ProviderRequest(
        messages=[{"role": "user", "content": "q"}], tools=(),
        response_format={"type": "json_object"}, max_tokens=64,
    )
    with pytest.raises(HarnessError) as exc:
        AnthropicTranslator(ANTHROPIC).to_wire(request, "synth-model-a")
    assert exc.value.code == "PROVIDER_UNSUPPORTED_CAPABILITY"


def test_structured_output_allowed_for_openai():
    request = ProviderRequest(
        messages=[{"role": "user", "content": "q"}], tools=(),
        response_format={"type": "json_object"}, max_tokens=64,
    )
    path, payload = OpenAITranslator(OPENAI).to_wire(request, "synth-model-o")
    assert path == "/v1/chat/completions"
    assert json.loads(payload)["response_format"] == {"type": "json_object"}


def test_streaming_refused():
    request = ProviderRequest(messages=[{"role": "user", "content": "q"}], tools=(),
                              max_tokens=64, stream=True)
    with pytest.raises(HarnessError) as exc:
        OpenAITranslator(OPENAI).to_wire(request, "synth-model-o")
    assert exc.value.code == "PROVIDER_STREAM_UNSUPPORTED"


# ------------------------------------------------------------ wire formats


def test_anthropic_request_wire_shape():
    bridge, wire = make_bridge("anthropic/synth-model", script=[ANTHROPIC_TEXT])
    bridge.complete(MESSAGES, TOOLS)
    call = wire.calls[0]
    assert call["path"] == "/v1/messages"
    payload = json.loads(call["payload"])
    assert payload["model"] == "synth-model-a"
    assert payload["system"] == "system prompt"
    assert [m["role"] for m in payload["messages"]] == ["user"]  # system hoisted out
    tool = payload["tools"][0]
    assert tool["input_schema"] == TOOLS[0]["parameters"]
    assert "parameters" not in tool
    assert call["headers"]["x-api-key"] == "sk-synth-test"
    assert call["headers"]["anthropic-version"]


def test_anthropic_assistant_tool_use_translation():
    request = ProviderRequest(
        messages=[{"role": "assistant", "content": None, "tool_calls": [
            {"id": "call_synth_1", "type": "function",
             "function": {"name": "lookup_metric", "arguments": "{\"a\": 1}"}}]}],
        tools=(), max_tokens=64,
    )
    _, payload = AnthropicTranslator(ANTHROPIC).to_wire(request, "synth-model-a")
    block = json.loads(payload)["messages"][0]["content"][0]
    assert block == {"type": "tool_use", "id": "call_synth_1",
                     "name": "lookup_metric", "input": {"a": 1}}


def test_anthropic_tool_result_translation():
    request = ProviderRequest(
        messages=[{"role": "tool", "tool_call_id": "call_synth_1",
                   "content": "{\"status\": \"certified\"}"}],
        tools=(), max_tokens=64,
    )
    _, payload = AnthropicTranslator(ANTHROPIC).to_wire(request, "synth-model-a")
    message = json.loads(payload)["messages"][0]
    assert message["role"] == "user"  # anthropic has no tool role
    block = message["content"][0]
    assert block["type"] == "tool_result"
    assert block["tool_use_id"] == "call_synth_1"
    assert block["content"] == "{\"status\": \"certified\"}"


def test_anthropic_text_response_to_model_turn():
    bridge, _ = make_bridge("anthropic/synth-model", script=[ANTHROPIC_TEXT])
    turn = bridge.complete(MESSAGES, TOOLS)
    assert isinstance(turn, ModelTurn)
    assert turn.content == "host-rendered"
    assert turn.tool_calls == ()
    assert turn.model_id == "anthropic/synth-model"
    assert turn.finish_reason == "stop"
    assert turn.usage == {"input_tokens": 11, "output_tokens": 7}


def test_anthropic_tool_use_response_to_model_turn():
    bridge, _ = make_bridge("anthropic/synth-model", script=[ANTHROPIC_TOOL])
    turn = bridge.complete(MESSAGES, TOOLS)
    assert turn.finish_reason == "tool_calls"
    assert len(turn.tool_calls) == 1
    call = turn.tool_calls[0]
    assert call["id"] == "call_synth_1"
    assert call["function"]["name"] == "lookup_metric"
    args = json.loads(call["function"]["arguments"])  # strict JSON text
    assert args == {"metric_id": "synthetic.metric", "asset_or_deal_id": "SYNTH-1"}


def test_openai_request_wire_shape():
    bridge, wire = make_bridge("openai/synth-model", script=[OPENAI_TEXT])
    bridge.complete(MESSAGES, TOOLS)
    call = wire.calls[0]
    assert call["path"] == "/v1/chat/completions"
    payload = json.loads(call["payload"])
    assert payload["model"] == "synth-model-o"
    assert payload["messages"][0]["role"] == "system"
    tool = payload["tools"][0]
    assert tool["type"] == "function"
    assert tool["function"]["parameters"] == TOOLS[0]["parameters"]
    assert "input_schema" not in tool
    assert call["headers"]["Authorization"] == "Bearer sk-synth-test"


def test_openai_text_response_to_model_turn():
    bridge, _ = make_bridge("openai/synth-model", script=[OPENAI_TEXT])
    turn = bridge.complete(MESSAGES, TOOLS)
    assert turn.content == "host-rendered"
    assert turn.model_id == "openai/synth-model"
    assert turn.finish_reason == "stop"


def test_openai_tool_call_response_to_model_turn():
    bridge, _ = make_bridge("openai/synth-model", script=[OPENAI_TOOL])
    turn = bridge.complete(MESSAGES, TOOLS)
    assert turn.finish_reason == "tool_calls"
    assert turn.tool_calls[0]["function"]["name"] == "lookup_metric"
    assert json.loads(turn.tool_calls[0]["function"]["arguments"])["asset_or_deal_id"] == "SYNTH-1"


# --------------------------------------------------------- response hygiene


@pytest.mark.parametrize("body", [
    b'{"a": 1, "a": 2}',
    b'{"x": NaN}',
    b'{"x": Infinity}',
    b'{"choices": [',
])
def test_malformed_wire_json_refused(body):
    bridge, _ = make_bridge("openai/synth-model",
                            script=[TransportResult(200, body)])
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == INVALID_MODEL_JSON


def test_invalid_tool_call_in_response_refused():
    bad = {"choices": [{"message": {"tool_calls": [
        {"type": "function", "function": {"name": "x", "arguments": "{}"}}]},
        "finish_reason": "tool_calls"}]}
    bridge, _ = make_bridge("openai/synth-model", script=[bad])
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "INVALID_TOOL_CALL"


def test_truncated_finish_reason_refused():
    bad = {"choices": [{"message": {"content": "x"}, "finish_reason": "length"}]}
    bridge, _ = make_bridge("openai/synth-model", script=[bad])
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "MODEL_INCOMPLETE"


# -------------------------------------------------------- lifecycle errors


def test_missing_credentials_refused_before_send():
    bridge, wire = make_bridge("openai/synth-model", script=[OPENAI_TEXT],
                               credentials=lambda spec: None)
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_CREDENTIAL_MISSING"
    assert wire.calls == []


def test_credentials_in_headers_not_in_events():
    bridge, _ = make_bridge("anthropic/synth-model", script=[ANTHROPIC_TEXT],
                           credentials=lambda spec: "sk-SECRET-CANARY-KEY")
    bridge.complete(MESSAGES, TOOLS)
    dump = json.dumps(bridge.events)
    assert "sk-SECRET-CANARY-KEY" not in dump
    assert "Bearer" not in dump


def test_auth_error_sanitized():
    bridge, _ = make_bridge("openai/synth-model",
                           script=[TransportResult(401, b'{"error": "bad key"}')],
                           credentials=lambda spec: "sk-SECRET-CANARY-KEY")
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_AUTH"
    assert "sk-SECRET-CANARY-KEY" not in str(exc.value)
    assert "sk-SECRET-CANARY-KEY" not in json.dumps(exc.value.details)


def test_rate_limit_after_bounded_retries():
    policy = BoundedRetryPolicy(max_retries=3, base_delay_s=0.0, max_delay_s=0.0)
    inner = FakeTransport([TransportResult(429, b"{}")] * 10)
    bridge = ProviderBridge(make_routing(), "openai/synth-model",
                            RetryingTransport(inner, policy, sleep=lambda s: None),
                            credentials=lambda spec: "sk-synth-test")
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_RATE_LIMIT"
    assert len(inner.calls) == 4  # 1 + max_retries, never more


def test_transient_error_retried_then_success():
    policy = BoundedRetryPolicy(max_retries=2, base_delay_s=0.0, max_delay_s=0.0)
    sleeps = []
    inner = FakeTransport([TransportResult(503, b"{}"), OPENAI_TEXT])
    bridge = ProviderBridge(make_routing(), "openai/synth-model",
                            RetryingTransport(inner, policy, sleep=sleeps.append),
                            credentials=lambda spec: "sk-synth-test")
    turn = bridge.complete(MESSAGES, TOOLS)
    assert turn.content == "host-rendered"
    assert len(inner.calls) == 2
    assert len(sleeps) == 1


def test_non_retryable_status_single_attempt():
    policy = BoundedRetryPolicy(max_retries=3, base_delay_s=0.0, max_delay_s=0.0)
    inner = FakeTransport([TransportResult(400, b"bad request")] * 5)
    bridge = ProviderBridge(make_routing(), "openai/synth-model",
                            RetryingTransport(inner, policy, sleep=lambda s: None),
                            credentials=lambda spec: "sk-synth-test")
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_BAD_REQUEST"
    assert len(inner.calls) == 1


def test_timeout_bounded_no_retry_beyond_policy():
    inner = FakeTransport([TimeoutError("deadline")] * 5)
    bridge = ProviderBridge(make_routing(), "openai/synth-model",
                            RetryingTransport(inner, BoundedRetryPolicy(max_retries=0),
                                              sleep=lambda s: None),
                            credentials=lambda spec: "sk-synth-test")
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_TIMEOUT"
    assert len(inner.calls) == 1


def test_cancellation_stops_retries():
    cancel = CancelFlag()

    class CancelAfterFirst503(FakeTransport):
        def send(self, spec, path, payload, headers, timeout_s, cancel):
            result = super().send(spec, path, payload, headers, timeout_s, cancel)
            cancel.cancel()
            return result

    inner = CancelAfterFirst503([TransportResult(503, b"{}")] * 5)
    bridge = ProviderBridge(make_routing(), "openai/synth-model",
                           RetryingTransport(inner, BoundedRetryPolicy(max_retries=5),
                                             sleep=lambda s: None),
                           credentials=lambda spec: "sk-synth-test", cancel=cancel)
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_CANCELLED"
    assert len(inner.calls) == 1  # cancel observed, no further attempts


def test_context_overflow_refused_before_send():
    bridge, wire = make_bridge("local/synth-model", script=[OPENAI_TEXT])
    big = [{"role": "user", "content": "x" * 400000}]
    with pytest.raises(HarnessError) as exc:
        bridge.complete(big, TOOLS)
    assert exc.value.code == CONTEXT_OVERFLOW
    assert wire.calls == []


def test_context_overflow_from_provider():
    body = b'{"error": {"message": "prompt is too long: context_length_exceeded"}}'
    bridge, _ = make_bridge("openai/synth-model",
                            script=[TransportResult(400, body)])
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == CONTEXT_OVERFLOW


def test_response_bound_enforced():
    huge = TransportResult(200, b"x" * 200000)
    bridge, _ = make_bridge("openai/synth-model", script=[huge])
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "MODEL_OUTPUT_LIMIT"


def test_provider_unavailable_after_retries():
    inner = FakeTransport([OSError("connection reset")] * 5)
    bridge = ProviderBridge(make_routing(), "openai/synth-model",
                           RetryingTransport(inner, BoundedRetryPolicy(max_retries=0),
                                             sleep=lambda s: None),
                           credentials=lambda spec: "sk-synth-test")
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "PROVIDER_TRANSPORT_ERROR"


# -------------------------------------------------- fallback / offline gate


def test_no_implicit_local_to_cloud_fallback():
    local_wire = FakeTransport([OSError("connection refused")] * 3)
    cloud_wire = FakeTransport()
    transport = DispatchTransport({
        "local-vllm": local_wire,
        "openai-cloud": cloud_wire,
        "anthropic-cloud": cloud_wire,
        "openai-notools": cloud_wire,
    })
    bridge = ProviderBridge(make_routing(), "local/synth-model",
                           RetryingTransport(transport, BoundedRetryPolicy(max_retries=0),
                                             sleep=lambda s: None))
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == "LOCAL_ENDPOINT_ERROR"
    assert "no fallback" in str(exc.value)
    assert cloud_wire.calls == []  # cloud was never contacted


def test_no_providers_configured_stays_offline():
    bridge, _ = make_bridge(None)
    with pytest.raises(HarnessError) as exc:
        bridge.complete(MESSAGES, TOOLS)
    assert exc.value.code == NO_MODEL_CONFIGURED


def test_null_model_preserved_as_offline_default():
    with pytest.raises(HarnessError) as exc:
        NullModel().complete(MESSAGES, TOOLS)
    assert exc.value.code == NO_MODEL_CONFIGURED


# ------------------------------------------------ discovery / verification


def test_discover_models_and_verify_available():
    listing = TransportResult(200, b'{"data": [{"id": "synth-model-l"}]}')
    transport = FakeTransport([listing])
    ids = discover_models(transport, LOCAL)
    assert ids == ["synth-model-l"]
    assert transport.calls[0]["path"] == "/v1/models"
    verify = verify_model_available(FakeTransport([listing]), LOCAL, "synth-model-l")
    assert verify == {"provider_id": "local-vllm", "wire_model": "synth-model-l",
                      "verified": True}


def test_verify_absent_model_refused():
    listing = TransportResult(200, b'{"data": [{"id": "synth-model-l"}]}')
    transport = FakeTransport([listing])
    with pytest.raises(HarnessError) as exc:
        verify_model_available(transport, LOCAL, "synth-model-x")
    assert exc.value.code == "MODEL_NOT_SERVED"


def test_discover_malformed_listing_refused():
    transport = FakeTransport([TransportResult(200, b'{"data": "nope"}')])
    with pytest.raises(HarnessError) as exc:
        discover_models(transport, LOCAL)
    assert exc.value.code == INVALID_MODEL_LIST


# ------------------------------------------------------- logging hygiene


def test_events_never_carry_raw_source_or_secret():
    canary_content = "CANARY RESIDENT Jane Canary +1-555-019-9999"
    messages = [{"role": "system", "content": "system"},
                {"role": "user", "content": canary_content}]
    for script, credential in (
        ([ANTHROPIC_TEXT], "sk-SECRET-CANARY-KEY"),
        ([TransportResult(500, b"boom")], "sk-SECRET-CANARY-KEY"),
    ):
        bridge, _ = make_bridge("anthropic/synth-model", script=script,
                                credentials=lambda spec: credential)
        try:
            bridge.complete(messages, TOOLS)
        except HarnessError as exc:
            assert canary_content not in str(exc)
            assert "sk-SECRET-CANARY-KEY" not in str(exc)
            assert canary_content not in json.dumps(exc.details)
        dump = json.dumps(bridge.events)
        assert canary_content not in dump
        assert "sk-SECRET-CANARY-KEY" not in dump


def test_events_record_bounded_metadata_only():
    bridge, _ = make_bridge("openai/synth-model", script=[OPENAI_TEXT])
    turn = bridge.complete(MESSAGES, TOOLS)
    request_events = [e for e in bridge.events if e["event"] == "provider_request"]
    response_events = [e for e in bridge.events if e["event"] == "provider_response"]
    assert len(request_events) == 1 and len(response_events) == 1
    request = request_events[0]
    assert set(request) == {"event", "provider_id", "model", "path",
                            "request_bytes", "request_sha256"}
    assert request["provider_id"] == "openai-cloud"
    assert request["model"] == "openai/synth-model"
    assert request["request_bytes"] > 0
    assert len(request["request_sha256"]) == 64
    response = response_events[0]
    assert response["status"] == 200
    assert response["finish_reason"] == turn.finish_reason
    assert "payload" not in json.dumps(bridge.events)


def test_local_route_needs_no_credentials():
    bridge, wire = make_bridge("local/synth-model", script=[OPENAI_TEXT],
                              credentials=lambda spec: None)
    turn = bridge.complete(MESSAGES, TOOLS)
    assert turn.model_id == "local/synth-model"
    assert "Authorization" not in wire.calls[0]["headers"]
    assert "x-api-key" not in wire.calls[0]["headers"]