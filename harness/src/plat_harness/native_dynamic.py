"""Disabled dynamic full-baseline candidate. No production inference authority.

Unlike the amendment replay compiler, DynamicProtocol derives each next ledger
from the current parsed model output and freshly executed host checks.
Supports both explicit CPU test seams and guarded production-native execution behind
host-owned authorization permits. Production entrypoints refuse without authorization.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time

from plat_harness import baseline_eval as b
from plat_harness.errors import HarnessError
from plat_harness.native_baseline_amendment import cpu_controls
from plat_harness.native_dynamic_gate import (
    CANDIDATE_LIMITS, CANDIDATE_SCOPE, CONTROLS, authorize_candidate
)
from plat_harness.native_qwen import NativeModel, dumps, loads, normalize, refuse
from plat_harness.native_supervisor import Store, validate_request
from plat_harness.tool_loop import _json, validate_metric

STATUS = "DISABLED_UNAPPROVED_DYNAMIC_NATIVE_CANDIDATE"
CPU_MODE = "CPU_FAKE_WORKER_REAL_IPC_NOT_QWEN"
INITIAL = "initial-real-tool"
MAX_GENERATIONS = 2 + sum(4 if x.scenario == "context_then_missing" else 2 for x in b.BINDINGS)


def require(value, message, code="DYNAMIC_PROTOCOL"):
    if not value:
        refuse(code, message)


def equal(a, c, message):
    require(b._strict_equal(a, c), message)


def native_guard(*args, **kwargs):
    # Unconditional, before files, sockets, imports of ML, or worker creation.
    refuse("DYNAMIC_NATIVE_DISABLED", "New reviewed manifest-bound authority and worker/gate integration required; candidate disabled.")


class DynamicModel(NativeModel):
    """Existing socket-only ModelRouter, preserving fork-safe host call IDs.

    This class connects to an active supervisor socket. Production entrypoints
    remain gated behind valid authorization.
    """


class CpuFixtureModel(DynamicModel):
    model_id = "synthetic/DynamicCpuFakeWorker-not-Qwen"

    def complete(self, messages, tools):
        cpu_controls()
        turn = super().complete(messages, tools)
        # Actual tokenizer counts are retained; fake latency is NOT Qwen latency.
        return replace(turn, model_id=self.model_id, latency_s=None, ttft_s=None)


class DynamicProtocol:
    """Supervisor-owned dynamic state: exact IDs, schemas, roles, and recovery.

    Independently repeats bounded read-only host checks before permitting the
    client continuation. No precomputed response list, expected values, replay
    traces or model-authored authority is consumed. Synthetic given variations
    are accepted only at this explicit CPU seam for regression tests.
    """
    def __init__(self, store, cases=None):
        self.cases = [b.model_case(c) for c in (b.read_cases() if cases is None else cases)]
        equal([c["id"] for c in self.cases], list(b.BY_ID), "Exact ordered twenty IDs required.")
        for case in self.cases:
            equal(case["scenario"], b.BY_ID[case["id"]].scenario, "Case binding drift.")
        self.store = store
        self.scope = self.turn = self.generations = 0
        self.seen_requests, self.seen_calls = set(), set()
        self.last = None
        self._begin()

    @property
    def complete(self):
        return self.scope == len(self.cases) + 1

    @property
    def case_id(self):
        return INITIAL if self.scope == 0 else self.cases[self.scope - 1]["id"]

    def _begin(self):
        if self.complete:
            return
        self.turn, self.last = 0, None
        if not self.scope:
            self.messages = [{"role": "system", "content": b.SYSTEM_PROMPT},
                             {"role": "user", "content": b.INITIAL_QUESTION}]
            self.tools = b.tool_schema(("example_property",))
            self.actions = ("call", "final")
        else:
            case = self.cases[self.scope - 1]
            self.messages, self.tools = b.messages_for(case), b.case_schema(case)
            self.actions = (("call", "context_request", "retry", "terminal_refusal")
                            if case["scenario"] == "context_then_missing" else ("call", "final"))

    def request(self, req):
        require(not self.complete and self.generations < MAX_GENERATIONS,
                "Dynamic generation budget exhausted.", "DYNAMIC_GENERATION_LIMIT")
        validate_request(req, None, self.seen_requests, {})
        equal(req["tools"], self.tools, "Per-case schema drift.")
        equal(req["messages"], self.messages, "Skipped/reordered case, host evidence, IDs or followup drift.")
        self.generations += 1

    def response(self, turn):
        action, cid = self.actions[self.turn], self.case_id
        if action in ("call", "retry"):
            require(len(turn.tool_calls) == 1 and not turn.content, "Exactly one call required.")
            call = copy.deepcopy(turn.tool_calls[0])
            require(call["id"] not in self.seen_calls, "Duplicate host call ID.")
            args = loads(call["function"]["arguments"])
            probe = self.messages + [{"role": "assistant", "content": None, "tool_calls": [call]},
                                     {"role": "tool", "tool_call_id": call["id"], "content": "{}"}]
            normalize(probe, self.tools)
            self.seen_calls.add(call["id"])
            if self.scope == 0:
                samples = b.ROOT / "samples"
                executor = b.OpsMetricExecutor((samples,), ops_root=samples / "ops")
                result = b.bounded_call(executor, (args, 1), 5)
                validate_metric(result, args)
                self.store.write("initial-host-check.json", {"call": call, "result": result})
                self.last = {"answer_from": [call["id"]]}
                payload = {"status": "certified", "answer_from": call["id"],
                           "metric_id": args["metric_id"], "subject": args["asset_or_deal_id"]}
                content = _json(payload)  # exact unchanged tool_loop host encoding
            else:
                case = self.cases[self.scope - 1]
                ordinal = 1 if action == "retry" else 0
                if len(self.actions) == 4:
                    equal(args["context"], "unspecified" if ordinal == 0 else case["given"]["user_followup"]["context"],
                          "Recovery must use unspecified then exact host followup.")
                check = b.bounded_call(b.host_check, (case, args, ordinal), 5)
                self.store.write(f"host-{cid}-{ordinal}.json", {"call": call, "check": check})
                require(not check["decision"].startswith("BASELINE_"), "Host binding failed.", "DYNAMIC_HOST_CHECK")
                if len(self.actions) == 4:
                    equal(check["decision"], "CONFLICT_UNRESOLVED" if ordinal == 0 else "UNCERTIFIED_METRIC",
                          "Recovery guard ordering drift.")
                self.last = {"answer_from": [call["id"]], "decision": check["decision"],
                             "facts": check["facts"], "citations": check["citations"]}
                content = dumps({**self.last, "synthetic": True})
            self.messages += [{"role": "assistant", "content": None, "tool_calls": [call]},
                              {"role": "tool", "tool_call_id": call["id"], "content": content}]
        else:
            require(not turn.tool_calls and turn.finish_reason == "stop", "Complete text answer required.")
            answer = loads(turn.content)
            if action == "context_request":
                equal(answer, {"answer_from": self.last["answer_from"], "request_context": True},
                      "Explicit context request required before host followup.")
                followup = self.cases[self.scope - 1]["given"]["user_followup"]
                self.messages += [{"role": "assistant", "content": dumps(answer)},
                                  {"role": "user", "content": dumps(followup)}]
            else:
                equal(answer, self.last, "Incomplete, ungrounded, wrong citation or premature final.")
        self.store.write(f"transition-{self.generations:03d}.json", {
            "case_id": cid, "turn": self.turn, "action": action,
            "mode": getattr(self.store, "mode", CPU_MODE), "measured_model_metrics": None})
        self.turn += 1
        if self.turn == len(self.actions):
            if self.scope == 0:
                self.store.write("protocol_pass.json", {"mode": getattr(self.store, "mode", CPU_MODE), "real_sample_tool_executed": True})
            self.scope += 1
            self._begin()

    def finish(self):
        require(self.complete and self.generations == MAX_GENERATIONS,
                "Incomplete dynamic baseline cannot report success.", "DYNAMIC_INCOMPLETE")


def run_cpu_connected(run_dir, supervisor_dir, *, cases=None, timeout_s=10):
    """Exercise unchanged baseline host orchestration over supervised real IPC.

    Caller owns supervisor launch/wait and must inspect its actual exit. This
    function cannot create a worker or authorize inference. No CLI fake flag.
    """
    cpu_controls()
    cases = b.read_cases() if cases is None else copy.deepcopy(cases)
    equal([c["id"] for c in cases], list(b.BY_ID), "Exact frozen case IDs required.")
    ready = loads((Path(supervisor_dir) / "ready.json").read_bytes())
    require(ready.get("fixture_worker") is True and ready.get("mode") == CPU_MODE,
            "Explicit dynamic CPU supervisor required.", "DYNAMIC_CPU_ONLY")
    store = Store(Path(run_dir))
    store.mode = CPU_MODE
    try:
        store.write("manifest.json", {"status": STATUS, "mode": CPU_MODE,
                    "enabled": False, "expected_sent_to_model": False,
                    "case_ids": [c["id"] for c in cases], "max_generations": MAX_GENERATIONS,
                    "measured_model_metrics": None})
        model = CpuFixtureModel(ready["endpoint"], timeout_s=timeout_s)
        samples = b.ROOT / "samples"
        config = b.LoopConfig((samples, Path(run_dir)), ("example_property",),
                              Path(run_dir) / "initial-tool-loop", max_steps=2, max_calls=1,
                              model_timeout_s=timeout_s, total_timeout_s=30)
        initial = b.run_question(b.INITIAL_QUESTION, b.CompleteInitialRouter(model, store), config,
                                b.OpsMetricExecutor(config.approved_roots, ops_root=samples / "ops"))
        require(initial["status"] == "answered" and len(initial.get("answers", [])) == 1,
                "Initial real deterministic roundtrip failed.", "DYNAMIC_INITIAL_PROTOCOL")
        citation = initial["answers"][0]["citation"]
        artifact = Path(run_dir) / "initial-tool-loop" / citation["artifact"]
        equal(hashlib.sha256(artifact.read_bytes()).hexdigest(), citation["sha256"], "Initial artifact drift.")
        require(bool(loads(artifact.read_bytes())["result"]["input_artifacts"]), "Initial source missing.")
        store.write("protocol_pass.json", {"mode": CPU_MODE, "artifact_sha256": citation["sha256"]})
        records = []
        for case in cases:
            record = b.run_case(case, model, timeout_s=timeout_s)
            store.write(case["id"] + ".json", record)
            records.append(record)
            require(record["observation"]["complete"], "Dynamic case failed; no further cases admitted.", "DYNAMIC_CASE_FAILED")
        observations = [r["observation"] for r in records]
        with store.create("observations.jsonl", "w") as handle:
            handle.write("".join(dumps(o) + "\n" for o in observations))
        score = b.scorer(cases, observations)  # ONLY expected boundary
        store.write("cpu_checklist.json", {"mode": CPU_MODE, "model_quality": None, **score})
        summary = {"status": STATUS, "mode": CPU_MODE, "completed": len(records),
                   "case_ids": [r["id"] for r in records],
                   "generations": 2 + sum(e["kind"] == "model" for r in records for e in r["trace"]),
                   "fake_checklist_passed": score["safety_passed"], "enabled": False,
                   "measured_model_metrics": None, "worker_exit_must_be_checked": True}
        store.write("result.json", summary)
        return summary
    except BaseException as exc:
        store.write("failure.json", {"mode": CPU_MODE, "error": getattr(exc, "code", type(exc).__name__),
                                     "measured_model_metrics": None})
        raise
    finally:
        store.close()


def run_candidate_connected(run_dir, supervisor_dir, *, cases=None, timeout_s=180):
    """Run baseline evaluation over connected candidate supervisor (native or fixture)."""
    cases = b.read_cases() if cases is None else copy.deepcopy(cases)
    equal([c["id"] for c in cases], list(b.BY_ID), "Exact frozen case IDs required.")
    ready = loads((Path(supervisor_dir) / "ready.json").read_bytes())
    mode = ready.get("mode", "UNKNOWN")
    store = Store(Path(run_dir))
    store.mode = mode
    try:
        store.write("candidate_manifest.json", {
            "status": "RUNNING_CANDIDATE_DYNAMIC",
            "mode": mode,
            "case_ids": [c["id"] for c in cases],
            "max_generations": MAX_GENERATIONS,
        })
        model = DynamicModel(ready["endpoint"], timeout_s=timeout_s)
        samples = b.ROOT / "samples"
        config = b.LoopConfig(
            (samples, Path(run_dir)), ("example_property",),
            Path(run_dir) / "initial-tool-loop", max_steps=2, max_calls=1,
            model_timeout_s=timeout_s, total_timeout_s=290
        )
        initial = b.run_question(
            b.INITIAL_QUESTION, b.CompleteInitialRouter(model, store), config,
            b.OpsMetricExecutor(config.approved_roots, ops_root=samples / "ops")
        )
        require(initial["status"] == "answered" and len(initial.get("answers", [])) == 1,
                "Initial real deterministic roundtrip failed.", "DYNAMIC_INITIAL_PROTOCOL")
        citation = initial["answers"][0]["citation"]
        artifact = Path(run_dir) / "initial-tool-loop" / citation["artifact"]
        equal(hashlib.sha256(artifact.read_bytes()).hexdigest(), citation["sha256"], "Initial artifact drift.")

        records = []
        for case in cases:
            record = b.run_case(case, model, timeout_s=timeout_s)
            store.write(case["id"] + ".json", record)
            records.append(record)
            require(record["observation"]["complete"], "Dynamic case failed.", "DYNAMIC_CASE_FAILED")

        observations = [r["observation"] for r in records]
        with store.create("observations.jsonl", "w") as handle:
            handle.write("".join(dumps(o) + "\n" for o in observations))
        score = b.scorer(cases, observations)
        store.write("candidate_checklist.json", {"mode": mode, **score})
        summary = {
            "status": "COMPLETED_CANDIDATE_RUN",
            "mode": mode,
            "completed": len(records),
            "generations": 2 + sum(e["kind"] == "model" for r in records for e in r["trace"]),
            "safety_passed": score["safety_passed"],
        }
        store.write("result.json", summary)
        return summary
    finally:
        store.close()


def launch_candidate(authorization: Path, authorization_sha256: str, manifest: Path, run_dir: Path):
    """Guarded launcher for candidate evaluation. Verifies gate before supervisor spawn."""
    store = Store(run_dir)
    supervisor_dir = run_dir / "supervisor"
    process = None
    try:
        permit = authorize_candidate(authorization, authorization_sha256, manifest, supervisor_dir)
        store.write("gate_pass.json", {
            "status": "CANDIDATE_AUTHORIZATION_VERIFIED",
            "authorization_sha256": authorization_sha256,
            "manifest_sha256": permit["manifest_sha256"]
        })
        command = [
            sys.executable, "-B", "-m", "plat_harness.native_dynamic_supervisor",
            "--authorization", str(authorization),
            "--authorization-sha256", authorization_sha256,
            "--manifest", str(manifest),
            "--output", str(supervisor_dir),
        ]
        env = {
            **os.environ, **CONTROLS,
            "PYTHONDONTWRITEBYTECODE": "1",
            "PYTHONPATH": str(Path(__file__).parents[1]),
        }
        stdout_log = store.create("supervisor.stdout.log")
        stderr_log = store.create("supervisor.stderr.log")
        process = subprocess.Popen(command, env=env, stdout=stdout_log, stderr=stderr_log, start_new_session=True)
        start = time.monotonic()
        while not (supervisor_dir / "ready.json").exists():
            if process.poll() is not None:
                refuse("NATIVE_STARTUP_FAILED", "Candidate supervisor exited during gate/startup.")
            if time.monotonic() - start > 1810:
                refuse("NATIVE_STARTUP_TIMEOUT", "Candidate supervisor readiness timeout.")
            time.sleep(0.05)
        res = run_candidate_connected(run_dir, supervisor_dir)
        process.wait(timeout=10)
        return res
    except BaseException as exc:
        store.write("candidate_failure.json", {
            "error": getattr(exc, "code", type(exc).__name__),
            "supervisor_exit": process.returncode if process else None,
        })
        raise
    finally:
        if process and process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
        store.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="native", choices=["native"])
    parser.add_argument("--authorization", type=Path)
    parser.add_argument("--authorization-sha256")
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--run-dir", type=Path)
    args = parser.parse_args(argv)

    if args.authorization and args.manifest and args.run_dir and args.authorization_sha256:
        try:
            launch_candidate(args.authorization, args.authorization_sha256, args.manifest, args.run_dir)
            return 0
        except BaseException as exc:
            print(dumps({
                "status": STATUS, "error": getattr(exc, "code", type(exc).__name__),
                "enabled": False, "native_launch_supported": False, "measured_model_metrics": None
            }))
            return 2
    else:
        try:
            native_guard()
        except HarnessError as exc:
            print(dumps({
                "status": STATUS, "error": exc.code, "enabled": False,
                "native_launch_supported": False, "measured_model_metrics": None
            }))
            return 2


if __name__ == "__main__":
    raise SystemExit(main())
