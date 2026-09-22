"""Own focused CPU amendment tests; no model, worker or approval execution."""
from copy import deepcopy
from contextlib import contextmanager


@contextmanager
def code_error(code):
    with pytest.raises(HarnessError) as captured:
        yield
    assert captured.value.code == code

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from plat_harness import baseline_eval as b
from plat_harness import native_baseline_amendment as a
from plat_harness.errors import HarnessError
from plat_harness.native_qwen import TEMPLATE_SHA256, dumps, loads


@pytest.fixture(scope="module")
def evidence(tmp_path_factory):
    root = tmp_path_factory.mktemp("amendment-fake-evidence")
    with pytest.MonkeyPatch.context() as mp:
        for k, v in {**a.CONTROLS, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1",
                     "TRANSFORMERS_OFFLINE": "1"}.items():
            mp.setenv(k, v)
        result = b.run_offline(root / "run")
    assert result["generation_calls_including_initial"] == 44
    return root / "run"


@pytest.fixture
def protocol(evidence):
    return a.compile_protocol(evidence)


@pytest.fixture
def recovery(evidence):
    return b.read_cases()[-1], a.read(evidence / "eval-v1.1-020.json")


def test_executed_full_protocol_exact_ids_schemas_and_budgets(protocol):
    assert len(protocol["stages"]) == 44
    assert protocol["proposed_generations"] == 44
    assert protocol["proposed_max_turns_per_case"] == 4
    assert [c["case_id"] for c in protocol["contracts"]] == [a.INITIAL] + list(b.BY_ID)
    assert protocol["contracts"][-1]["actions"] == list(a.STEPS)
    assert protocol["contracts"][-1]["max_tool_calls"] == 2
    for case, contract in zip(b.read_cases(), protocol["contracts"][1:]):
        assert contract["tools"] == b.case_schema(case)
        assert contract["model_input"] == b.model_case(case)
        assert "expected" not in contract["model_input"]
    assert a.LIMITS["max_generations"] == 42


@pytest.mark.parametrize("index", range(20))
def test_each_case_expected_independence_and_frozen_prompt(index, evidence):
    case = b.read_cases()[index]
    record = a.read(evidence / (case["id"] + ".json"))
    clean = a.compile_case(case, record)
    case["expected"] = {"injection": "LEAK_SENTINEL_approvals_999999", "anything": True}
    assert a.compile_case(case, record) == clean
    assert "LEAK_SENTINEL" not in dumps(clean)
    model_input = loads(clean[0]["messages"][1]["content"])
    assert model_input["given"] == case["given"]
    assert model_input["prompt"] == case["prompt"]


@pytest.mark.parametrize("mutation", ["expected", "given", "schema", "system", "request_index", "final"])
def test_boundary_drift_fails(recovery, mutation):
    case, record = recovery
    if mutation in ("expected", "given"):
        req = record["trace"][0]
        value = loads(req["messages"][1]["content"])
        value[mutation] = {"injected": "not frozen"}
        req["messages"][1]["content"] = dumps(value)
    elif mutation == "schema":
        record["trace"][0]["tools"][0]["function"]["parameters"]["additionalProperties"] = True
    elif mutation == "system":
        record["trace"][0]["messages"][0]["content"] += " override"
    elif mutation == "request_index":
        record["trace"][0]["index"] = True
    else:
        record["trace"][-1]["answer"]["facts"]["noi"] = 999
    with pytest.raises(HarnessError):
        a.compile_case(case, record)


@pytest.mark.parametrize("mutation", ["omit_request", "omit_followup", "reorder", "wrong_context",
                                      "extra_retry", "early_final", "wrong_guard", "duplicate_call"])
def test_recovery_is_ordered_and_terminal(recovery, mutation):
    case, record = recovery
    trace = record["trace"]
    at = lambda kind: next(i for i, e in enumerate(trace) if e["kind"] == kind)
    if mutation == "omit_request":
        trace.pop(at("context_request"))
    elif mutation == "omit_followup":
        trace.pop(at("host_followup"))
    elif mutation == "reorder":
        i, j = at("context_request"), at("host_followup")
        trace[i], trace[j] = trace[j], trace[i]
    elif mutation == "wrong_context":
        trace[at("host_followup")]["value"]["context"] = "underwriting"
    elif mutation == "extra_retry":
        trace.extend(deepcopy(trace[:3]))
    elif mutation == "early_final":
        trace[at("context_request")]["kind"] = "final"
    elif mutation == "wrong_guard":
        trace[at("tool")]["check"]["decision"] = "UNCERTIFIED_METRIC"
    else:
        models = [e for e in trace if e["kind"] == "model"]
        models[2]["turn"]["tool_calls"] = deepcopy(models[0]["turn"]["tool_calls"])
    with pytest.raises(HarnessError):
        a.compile_case(case, record)


def test_cursor_blocks_skips_duplicates_and_extra_stages(protocol):
    cursor = a.ReplayCursor(protocol)
    with pytest.raises(HarnessError):
        cursor.accept(protocol["stages"][2])
    assert cursor.index == 0
    with pytest.raises(HarnessError):
        cursor.finish()
    for stage in protocol["stages"]:
        cursor.accept(stage)
    assert cursor.finish() == {"accepted_stages": 44, "native_execution": False}
    with code_error("AMENDMENT_STAGE_LIMIT"):
        cursor.accept(protocol["stages"][-1])


class TokenizerDouble:
    # Unit-only boundary double, never claimed as real tokenizer evidence.
    chat_template = "UNIT_TEST_TEMPLATE"
    def __init__(self, count):
        self.count = count
    def apply_chat_template(self, messages, **kw):
        assert kw["tokenize"] is False and kw["enable_thinking"] is False
        return dumps(messages) + dumps(kw["tools"])
    def __call__(self, text, **kw):
        assert kw == {"add_special_tokens": False, "truncation": False}
        return {"input_ids": list(range(self.count))}


@pytest.mark.parametrize("count,accept", [(1, True), (1536, True), (0, False), (1537, False)])
def test_each_stage_uses_actual_renderer_bounds(protocol, monkeypatch, count, accept):
    from plat_harness import native_qwen
    monkeypatch.setattr(native_qwen, "TEMPLATE_SHA256", hashlib.sha256(TokenizerDouble.chat_template.encode()).hexdigest())
    if accept:
        fit = a.render_stages(protocol, TokenizerDouble(count))
        assert fit["accepted_stages"] == 44
        assert fit["min_tokens"] == fit["max_tokens"] == count
        assert all(len(r["renderings"]) == 2 for r in fit["records"])
    else:
        with code_error("NATIVE_PROMPT_LIMIT"):
            a.render_stages(protocol, TokenizerDouble(count))


def test_wrong_template_refused(protocol):
    with code_error("NATIVE_TEMPLATE"):
        a.render_stages(protocol, TokenizerDouble(10))


@pytest.mark.parametrize("approval", [None, {}, {"approved": True, "enabled": True}, {"max_generations": 44}])
def test_native_guard_never_touches_transport_or_loader(monkeypatch, approval):
    import socket
    def forbidden(*args, **kwargs):
        raise AssertionError("Native side effect reached")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    with code_error("AMENDMENT_NATIVE_DISABLED"):
        a.native_guard(approval=approval)
    assert a.LIMITS["max_generations"] == 42


def test_native_cli_from_other_cwd_no_inherited_pythonpath(tmp_path):
    # Absolute source entry point; explicitly set source path in isolated child.
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    script = ("import sys; sys.path.insert(0," + repr(str(b.ROOT / "harness/src")) + "); "
              "from plat_harness.native_baseline_amendment import main; raise SystemExit(main(['native']))")
    result = subprocess.run([sys.executable, "-B", "-c", script], cwd=tmp_path, env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 2, result.stderr
    assert loads(result.stdout)["error"] == "AMENDMENT_NATIVE_DISABLED"
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("key,value", [("CUDA_VISIBLE_DEVICES", "0"), ("HF_HUB_OFFLINE", "0"),
                                      ("COMPS_MODE", "run"), ("ALLOW_LIFECYCLE", "1")])
def test_cpu_controls_closed(monkeypatch, key, value):
    for k, v in {**a.CONTROLS, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv(key, value)
    with code_error("AMENDMENT_CPU_ONLY"):
        a.cpu_controls()


@pytest.mark.parametrize("digest", [None, "", "F" * 64, "g" * 64, "0" * 63])
def test_literal_hash_required_before_read(tmp_path, monkeypatch, digest):
    monkeypatch.setattr(a, "cpu_controls", lambda: None)
    with code_error("AMENDMENT_HASH"):
        a.verify(tmp_path / "absent", digest)


def test_manifest_and_dependency_drift_with_actual_file_hash(tmp_path, monkeypatch):
    # Small synthetic file, not a counterfeit checkpoint/approval.
    source = tmp_path / "source.txt"
    source.write_text("synthetic evidence v1")
    def freeze(*_):
        return {"trace_dir": str(tmp_path), "authority": str(source),
                "files": {str(source): a.hash_file(source)[0]}}
    monkeypatch.setattr(a, "cpu_controls", lambda: None)
    monkeypatch.setattr(a, "frozen_inputs", freeze)
    manifest = {"status": a.STATUS, "approved": False, "enabled": False,
                "native_launch_supported": False, "frozen": freeze()}
    path = tmp_path / "amendment.json"
    path.write_text(dumps(manifest))
    digest = a.hash_file(path)[0]
    assert a.verify(path, digest)["native_launch_supported"] is False
    source.write_text("synthetic evidence v2")
    with pytest.raises(HarnessError):
        a.verify(path, digest)
    source.write_text("synthetic evidence v1")
    path.write_text(dumps({**manifest, "enabled": True}))
    with pytest.raises(HarnessError):
        a.verify(path, digest)
    with pytest.raises(HarnessError):
        a.verify(path, a.hash_file(path)[0])


def test_no_existing_native_scope_is_widened(protocol):
    from plat_harness.native_supervisor import validate_request
    from plat_harness.native_qwen_worker import read_request
    assert callable(read_request)
    manifest = {"tools": b.tool_schema(("example_property",)), "system_prompt": b.SYSTEM_PROMPT,
                "questions": [b.INITIAL_QUESTION]}
    stage = protocol["stages"][-1]
    req = {"op": "generate", "id": "a" * 32, "messages": stage["messages"], "tools": stage["tools"]}
    with code_error("NATIVE_UNAUTHORIZED"):
        validate_request(req, manifest, set(), {})
    assert b.native_scope_blockers()["current_generation_cap"] == 42
    assert b.native_scope_blockers()["required_generations"] == 44
