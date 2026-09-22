"""Offline protocol tests. No inference services or models are started."""
import io
import json
import urllib.error

import pytest

from plat_harness.errors import HarnessError
from plat_harness.local_model import FragmentAssembler, LocalModel, _NoRedirect, strict_json


class Response(io.BytesIO):
    def __init__(self, raw, content_type="application/json"):
        super().__init__(raw)
        self.headers = {"Content-Type": content_type}


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, req, timeout):
        self.requests.append(req)
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def sse(events, done=True):
    return Response(b"".join(b"data: " + json.dumps(e).encode() + b"\n\n" for e in events)
                    + (b"data: [DONE]\n\n" if done else b""), "text/event-stream")


def event(delta, finish=None):
    return {"model": "exact-id", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}


def configured(response, listing=None):
    model = LocalModel("http://127.0.0.1:12345", "exact-id")
    model.opener = FakeOpener([Response(b"ok"), Response(json.dumps(listing or {"data": [{"id": "exact-id"}]}).encode()), response])
    return model


@pytest.mark.parametrize("endpoint", ["https://api.openai.com", "http://10.0.0.1:8000", "http://localhost:8000",
    "http://127.0.0.1:8000/v1", "http://user:secret@127.0.0.1:8000", "http://127.0.0.1:8000?x=y",
    "http://127.0.0.1:8000#fragment", "file:///tmp/model", "http://127.0.0.1:bad"])
def test_nonlocal_or_ambiguous_origins_denied(endpoint):
    with pytest.raises(HarnessError, match="loopback"):
        LocalModel(endpoint, "exact-id")


@pytest.mark.parametrize("text", ['{"a":1,"a":2}', '{"a":NaN}', '{"a":Infinity}', '{broken', '{"a":1}trailing'])
def test_strict_json_rejects_malformed_duplicates_nonfinite(text):
    with pytest.raises(HarnessError):
        strict_json(text)


def test_fragmented_tool_calls_and_usage_ttft():
    events = [event({"tool_calls": [{"index": 0, "id": "call_", "type": "function", "function": {"name": "get_", "arguments": '{"met'}}]}),
              event({"tool_calls": [{"index": 0, "id": "one", "function": {"name": "metric", "arguments": 'ric":"x"}'}}]}, "tool_calls"),
              {"choices": [], "usage": {"prompt_tokens": 12, "completion_tokens": 8}}]
    model = configured(sse(events))
    turn = model.complete([{"role": "user", "content": "synthetic"}], [])
    assert turn.tool_calls[0] == {"id": "call_one", "type": "function", "function": {"name": "get_metric", "arguments": '{"metric":"x"}'}}
    assert turn.ttft_s is not None and turn.latency_s >= turn.ttft_s
    assert turn.usage["completion_tokens"] == 8
    payload = json.loads(model.opener.requests[-1].data)
    assert payload["model"] == "exact-id" and payload["temperature"] == 0


def test_reasoning_not_persisted():
    model = configured(sse([event({"reasoning_content": "private reasoning"}), event({"content": '{"refusal":"UNSUPPORTED_REQUEST"}'}, "stop")]))
    turn = model.complete([], [])
    assert "private reasoning" not in str(turn)
    assert turn.ttft_s is not None


@pytest.mark.parametrize("delta", [
    [{"index": 0, "id": "id", "function": {"name": "tool", "arguments": '{"x":'}}],
    [{"index": 1, "id": "id", "function": {"name": "tool", "arguments": '{}'}}],
    [{"index": 3}], [{"index": True}], [{"index": 0, "function": {"arguments": {}}}],
])
def test_bad_fragments_never_use_defaults(delta):
    assembler = FragmentAssembler()
    with pytest.raises(HarnessError):
        assembler.add(delta)
        assembler.finish()


def test_exact_id_must_be_listed_before_prompt():
    model = configured(sse([]), {"data": [{"id": "different-id"}]})
    with pytest.raises(HarnessError) as exc:
        model.complete([{"role": "user", "content": "secret"}], [])
    assert exc.value.code == "MODEL_NOT_SERVED"
    assert all(req.data is None for req in model.opener.requests)


@pytest.mark.parametrize("finish,done", [("length", True), ("stop", False), (None, True), ("tool_calls", True)])
def test_incomplete_stream_refuses(finish, done):
    model = configured(sse([event({"content": "{}"}, finish)], done))
    with pytest.raises(HarnessError):
        model.complete([], [])


def test_output_limit():
    model = configured(sse([event({"content": "x" * 5000}, "stop")]))
    model.max_response_bytes = 1024
    with pytest.raises(HarnessError) as exc:
        model.complete([], [])
    assert exc.value.code == "MODEL_OUTPUT_LIMIT"


def test_redirect_is_denied():
    with pytest.raises(HarnessError) as exc:
        _NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://external.example")
    assert exc.value.code == "ENDPOINT_REDIRECT_DENIED"


def test_endpoint_failure_no_fallback():
    model = LocalModel("http://127.0.0.1:12345", "exact-id")
    model.opener = FakeOpener([urllib.error.URLError("no server")])
    with pytest.raises(HarnessError) as exc:
        model.complete([], [])
    assert exc.value.code == "LOCAL_ENDPOINT_ERROR" and len(model.opener.requests) == 1


def test_server_completion_identity_mismatch():
    chunk = event({"content": "{}"}, "stop")
    chunk["model"] = "other-id"
    with pytest.raises(HarnessError) as exc:
        configured(sse([chunk])).complete([], [])
    assert exc.value.code == "MODEL_ID_MISMATCH"


def test_duplicate_call_ids():
    assembler = FragmentAssembler()
    assembler.add([{"index": i, "id": "same", "function": {"name": "tool", "arguments": "{}"}} for i in range(2)])
    with pytest.raises(HarnessError):
        assembler.finish()
