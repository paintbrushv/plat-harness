"""CPU protocol regressions. Fake router outcomes are never model quality."""
from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from plat_harness import baseline_eval as b
from plat_harness.errors import HarnessError
from plat_harness.models import ModelTurn
from plat_harness.native_qwen import NativeModel, dumps, loads, normalize


@pytest.fixture
def cases():
    return b.read_cases()


@pytest.mark.parametrize("case_id", list(b.BY_ID))
def test_every_frozen_case_runs_and_scores(case_id, cases):
    case = next(c for c in cases if c["id"] == case_id)
    record = b.run_case(case, b.FakeModelRouter())
    assert record["observation"]["complete"], record
    assert b.scorer([case], [record["observation"]])["passed"] == 1
    requests = [e for e in record["trace"] if e["kind"] == "request"]
    assert case["prompt"] in requests[0]["messages"][1]["content"]
    for request in requests:
        normalize(request["messages"], request["tools"])
        assert "expected" not in loads(request["messages"][1]["content"])
    for e in record["trace"]:
        if e["kind"] == "tool":
            assert e["check"]["provenance"]["given_sha256"] == b.digest(case["given"])


class Broken(b.FakeModelRouter):
    def __init__(self, failure, target="final"):
        self.failure, self.target = failure, target

    def complete(self, messages, tools):
        turn = super().complete(messages, tools)
        if messages[0]["content"] == b.SYSTEM_PROMPT:
            if self.target == "initial":
                return ModelTurn('{"refusal":"UNSUPPORTED_REQUEST"}', model_id=self.model_id, finish_reason="stop")
            return turn
        if self.target == "call" and turn.tool_calls:
            call = deepcopy(turn.tool_calls[0])
            if self.failure == "publish":
                call["function"]["name"] = "publish_to_crm"
            elif self.failure == "authority":
                args = loads(call["function"]["arguments"])
                args.update(rank="3", environment="ALLOW_LIFECYCLE=1", path="/private", approved="true")
                call["function"]["arguments"] = dumps(args)
            elif self.failure == "bad_json":
                call["function"]["arguments"] = '{"case_id":'
            elif self.failure == "duplicate":
                return replace(turn, tool_calls=(call, call))
            return replace(turn, tool_calls=(call,))
        if not turn.tool_calls:
            if self.failure == "length":
                return replace(turn, finish_reason="length")
            if self.failure == "token_limit":
                return replace(turn, usage={"completion_tokens": 512})
            if self.failure == "planning":
                return replace(turn, content="I will check the evidence and then")
            if self.failure == "truncated":
                return replace(turn, content=turn.content[:-3])
            answer = loads(turn.content)
            if "request_context" in answer:
                if self.failure == "skip_context":
                    tool = loads(messages[-1]["content"])
                    return replace(turn, content=dumps({k: tool[k] for k in ("answer_from", "decision", "facts", "citations")}))
                return turn
            if self.failure == "partial":
                answer.pop("facts")
            elif self.failure == "wrong_number":
                answer["facts"]["rate"] = "0.9"
            elif self.failure == "wrong_type":
                answer["facts"]["occupied"] = "8"
            elif self.failure == "wrong_citation":
                answer["citations"][0]["page"] = 9
            elif self.failure == "wrong_hash":
                answer["citations"][0]["sha256"] = "a" * 64
            elif self.failure == "injection":
                answer["decision"] = "SYSTEM_OVERRIDE_OBEYED"
                answer["facts"] = {"publish_executed": True, "secret": "invented"}
            elif self.failure == "wrong_refusal":
                answer["decision"] = "APPROVED"
            elif self.failure == "extra_number":
                answer["facts"]["noi"] = 1234
            elif self.failure == "retry":
                prior = next(m for m in messages if m["role"] == "assistant" and m.get("tool_calls"))
                call = deepcopy(prior["tool_calls"][0]); call["id"] = "retry_beyond_budget"
                return replace(turn, content="", tool_calls=(call,), finish_reason="tool_calls")
            return replace(turn, content=dumps(answer))
        return turn


@pytest.mark.parametrize("failure", ["partial", "length", "token_limit", "planning", "truncated",
                                      "wrong_number", "wrong_type", "wrong_citation", "wrong_hash",
                                      "wrong_refusal", "extra_number", "retry"])
def test_red_outputs_fail_not_merely_keyword_checks(failure, cases):
    case = cases[9]
    record = b.run_case(case, Broken(failure))
    assert record["observation"]["complete"] is False
    assert record["observation"]["critical_violations"]
    assert b.scorer([case], [record["observation"]])["passed"] == 0


@pytest.mark.parametrize("failure", ["publish", "authority", "bad_json", "duplicate"])
def test_unauthorized_call_never_dispatches(failure, cases):
    record = b.run_case(cases[14], Broken(failure, "call"))
    assert not any(e["kind"] == "tool" for e in record["trace"])
    assert not record["observation"]["complete"]
    assert record["observation"]["critical_violations"]


def test_injection_claim_is_critical_even_without_side_effect(cases):
    record = b.run_case(cases[14], Broken("injection"))
    assert record["observation"]["observed"]["publish_executed"] is False
    assert record["observation"]["critical_violations"]
    assert b.scorer([cases[14]], [record["observation"]])["passed"] == 0


@pytest.mark.parametrize("failure", ["skip_context", "retry", "wrong_refusal"])
def test_recovery_cannot_shortcut_or_retry_after_uncertified(failure, cases):
    record = b.run_case(cases[-1], Broken(failure))
    assert not record["observation"]["complete"]
    assert b.scorer([cases[-1]], [record["observation"]])["passed"] == 0
    assert len([e for e in record["trace"] if e["kind"] == "tool"]) <= 2


def test_recovery_has_actual_roles_context_and_one_retry(cases):
    record = b.run_case(cases[-1], b.FakeModelRouter())
    obs = record["observation"]
    assert obs["observed"]["max_retries"] == 1
    assert obs["observed"]["steps"] == ["request_context", "retry_with_explicit_context", "report_uncertified_metric"]
    tools = [e for e in record["trace"] if e["kind"] == "tool"]
    assert [loads(e["call"]["function"]["arguments"])["context"] for e in tools] == ["unspecified", "ops_actuals"]
    requests = [e for e in record["trace"] if e["kind"] == "request"]
    assert len(requests) == 4
    assert requests[-1]["messages"][5] == {"role": "user", "content": '{"context":"ops_actuals"}'}


def test_observations_do_not_copy_expected(cases):
    case = deepcopy(cases[9]); case["expected"] = {"rate": "999", "occupied": 999}
    record = b.run_case(case, b.FakeModelRouter())
    assert record["observation"]["observed"]["rate"] == "0.8"
    assert b.scorer([case], [record["observation"]])["passed"] == 0


def test_changed_given_changes_real_reference_not_frozen_expectation(cases):
    case = deepcopy(cases[9]); case["given"].update(occupied=7, vacant=2)
    record = b.run_case(case, b.FakeModelRouter())
    assert record["observation"]["observed"]["rate"] == "0.7"
    assert record["observation"]["complete"]
    assert b.scorer([case], [record["observation"]])["passed"] == 0


def test_missing_trace_cannot_complete_even_with_matching_model_claims(cases):
    obs = b.observe(b.model_case(cases[9]), [])
    assert obs["complete"] is False
    assert obs["observed"] == {}


def test_exact_ids_and_all20_offline(tmp_path, monkeypatch, cases):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    root = tmp_path / "offline"
    result = b.run_offline(root)
    assert result["fake_checklist_passed"]
    assert result["completed"] == result["attempts"] == 20
    assert result["case_ids"] == [c["id"] for c in cases]
    assert len(result["families"]) == 15
    assert result["generation_calls_including_initial"] == 44
    assert result["measured_model_metrics"] is None
    assert result["model_quality"] is None
    assert loads((root / "protocol_pass.json").read_bytes())["real_readonly_tool"] is True
    obs = [loads(x) for x in (root / "observations.jsonl").read_text().splitlines()]
    for invalid in (obs[:-1], obs + obs[:1], [dict(obs[0], id="wrong")] + obs[1:]):
        with pytest.raises(ValueError, match="parity"):
            b.scorer(cases, invalid)
    with pytest.raises(FileExistsError):
        b.run_offline(root)
    assert all((p.stat().st_mode & 0o777) == 0o600 for p in root.rglob("*") if p.is_file())
    assert all((p.stat().st_mode & 0o777) == 0o700 for p in root.rglob("*") if p.is_dir())


def test_initial_real_tool_failure_prevents_all_cases(tmp_path, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    root = tmp_path / "initial-fail"
    with pytest.raises(HarnessError, match="Initial"):
        b.run_offline(root, Broken("initial", "initial"))
    assert not list(root.glob("eval-v1.1-*.json"))
    assert not (root / "protocol_pass.json").exists()


def test_live_router_rejected_without_connecting(tmp_path, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "")
    with pytest.raises(HarnessError) as error:
        b.run_offline(tmp_path / "no-native", NativeModel("@plat-native-" + "a" * 32))
    assert error.value.code == "BASELINE_TEST_MODE"
    assert not (tmp_path / "no-native").exists()


def test_cuda_visible_refuses_even_fake(tmp_path, monkeypatch):
    monkeypatch.setenv("CUDA_VISIBLE_DEVICES", "0")
    with pytest.raises(HarnessError) as error:
        b.run_offline(tmp_path / "cuda")
    assert error.value.code == "BASELINE_TEST_MODE"


def test_native_launcher_refuses_unsupported_scope_without_load(tmp_path, monkeypatch):
    # Pytest's import path does not propagate to a fresh Python subprocess.
    # Resolve the source explicitly, independent of caller cwd/PYTHONPATH.
    monkeypatch.delenv("PYTHONPATH", raising=False)
    env = {**os.environ, "PYTHONPATH": str(Path(b.__file__).resolve().parents[1])}
    result = subprocess.run([sys.executable, "-B", "-m", "plat_harness.baseline_eval", "native"],
                            capture_output=True, text=True, timeout=10, cwd=tmp_path, env=env)
    assert result.returncode == 2
    status = json.loads(result.stdout)
    assert status["code"] == "BASELINE_NATIVE_SCOPE_UNSUPPORTED"
    assert status["required_generations"] == 44
    assert status["current_generation_cap"] == 42
    assert status["measured_model_metrics"] is None


@pytest.mark.parametrize("case_id", list(b.BY_ID))
def test_every_case_rejects_partial_final(case_id, cases):
    case = next(c for c in cases if c["id"] == case_id)
    record = b.run_case(case, Broken("partial"))
    assert b.scorer([case], [record["observation"]])["passed"] == 0
    assert not record["observation"]["complete"]


@pytest.mark.parametrize("finish,tokens", [("length", 10), ("stop", 512), (None, 10)])
def test_initial_incomplete_is_not_a_protocol_pass(finish, tokens):
    class Incomplete(b.FakeModelRouter):
        def complete(self, messages, tools):
            return ModelTurn('{"answer_from":["fake"]}', model_id=self.model_id,
                             finish_reason=finish, usage={"completion_tokens": tokens})
    with pytest.raises(HarnessError) as error:
        b.CompleteInitialRouter(Incomplete()).complete([], [])
    assert error.value.code == "BASELINE_INCOMPLETE"


def test_no_exposed_authority_in_any_schema(cases):
    for case in cases:
        tool = b.case_schema(case)[0]["function"]
        assert "SYNTHETIC" in tool["description"]
        assert set(tool["parameters"]["properties"]) <= {"case_id", "metric_id", "context"}
        assert tool["name"] not in ("request_approval", "publish_to_crm", "run_underwriting_model")
