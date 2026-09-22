"""DISABLED / UNAPPROVED full-baseline amendment: CPU evidence compiler only.

This is not a native launcher, permit, supervisor or ModelRouter. It validates
preserved executed fake traces against the actual read-only baseline protocol,
freezes their dependencies, and renders every generation stage locally. No flag,
manifest edit, environment variable or purported approval enables inference.
"""
from __future__ import annotations

import argparse
import copy
from dataclasses import asdict
import hashlib
import importlib.metadata
import os
from pathlib import Path
import re
import sys

from plat_harness import baseline_eval as baseline
from plat_harness.errors import HarnessError
from plat_harness.native_gate import CONTROLS, LIMITS, PINNED_SMALL_DIGESTS, hash_file, open_safe
from plat_harness.native_qwen import (MODEL_ID, REVISION, TEMPLATE_SHA256,
    arguments, dumps, loads, model_path, normalize, refuse, render_prompt,
    runtime_python, schemas)
from plat_harness.native_supervisor import Store

STATUS = "DISABLED_UNAPPROVED_CPU_AMENDMENT"
INITIAL = "initial-real-tool"
HASH = re.compile(r"[0-9a-f]{64}\Z")
STEPS = ("call", "context_request", "retry_with_explicit_context", "terminal_refusal")


def require(ok, message, code="AMENDMENT_PROTOCOL"):
    if not ok:
        refuse(code, message)


def equal(left, right, message):
    require(baseline._strict_equal(left, right), message)


def sha(value):
    return hashlib.sha256(dumps(value).encode()).hexdigest()


def read(path):
    with os.fdopen(open_safe(Path(path)), "rb") as handle:
        raw = handle.read(4 * 1024 * 1024 + 1)
    require(len(raw) <= 4 * 1024 * 1024, "Evidence file exceeds bound.")
    return loads(raw)


def cpu_controls():
    for key, value in {**CONTROLS, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1",
                       "TRANSFORMERS_OFFLINE": "1"}.items():
        require(os.environ.get(key) == value, "CPU/offline/campaign control mismatch: " + key,
                "AMENDMENT_CPU_ONLY")
    torch = sys.modules.get("torch")
    require(torch is None or not torch.cuda._initialized, "CUDA was initialized.", "AMENDMENT_CPU_ONLY")
    require(not any(n == "unsloth" or n.startswith("unsloth.") for n in sys.modules),
            "Unsloth is not permitted.", "AMENDMENT_CPU_ONLY")


def native_guard(*args, **kwargs):
    # Intentionally unconditional; never inspect a permit or open a connection.
    refuse("AMENDMENT_NATIVE_DISABLED", "UNAPPROVED CPU evidence only; no native execution path exists.")


def compile_case(case, record):
    """Validate exact ordered requests/model/tool/followup ledger on CPU.

    Re-execute only baseline.host_check's bounded read-only bindings. Never read
    expected: even malicious expected changes cannot alter the model boundary.
    """
    case = baseline.model_case(case)
    equal(record["id"], case["id"], "Case identity drift.")
    tools, messages = baseline.case_schema(case), baseline.messages_for(case)
    schemas(tools)
    recovery = case["scenario"] == "context_then_missing"
    actions = STEPS if recovery else ("call", "final")
    trace, cursor, ordinal, last, stages = record["trace"], 0, 0, None, []

    def take(kind):
        nonlocal cursor
        require(cursor < len(trace) and trace[cursor].get("kind") == kind,
                "Missing/reordered protocol event: " + kind)
        entry = trace[cursor]
        cursor += 1
        return entry

    for index, action in enumerate(actions):
        req = take("request")
        equal(req, {"kind": "request", "messages": messages, "tools": tools, "index": index},
              "Prompt/schema/index drift or expected leakage.")
        normalize(messages, tools)
        stages.append({"case_id": case["id"], "turn": index, "action": action,
                       "messages": copy.deepcopy(messages), "tools": copy.deepcopy(tools)})
        turn = take("model")["turn"]
        require(turn["model_id"] == baseline.FakeModelRouter.model_id,
                "Only preserved explicitly labelled fake traces are supported.")
        require(turn["finish_reason"] in ("stop", "tool_calls") and
                turn.get("usage", {}).get("completion_tokens", 0) < 512, "Incomplete model trace.")
        if action in ("call", "retry_with_explicit_context"):
            require(not turn["content"] and len(turn["tool_calls"]) == 1, "One tool call required.")
            call = turn["tool_calls"][0]
            args = call["function"]["arguments"]
            args = loads(args) if isinstance(args, str) else args
            arguments(call["function"]["name"], args, schemas(tools))
            event = take("tool")
            equal(event["ordinal"], ordinal, "Retry count/order drift.")
            equal(event["call"], call, "Executed call differs from model call.")
            check = baseline.host_check(case, args, ordinal)
            equal(event["check"], check, "Actual host reference drift.")
            require(not check["decision"].startswith("BASELINE_"), "Unsupported host binding.")
            if recovery:
                equal(args["context"], "unspecified" if ordinal == 0 else case["given"]["user_followup"]["context"],
                      "Recovery must use explicit host context once.")
                equal(check["decision"], "CONFLICT_UNRESOLVED" if ordinal == 0 else "UNCERTIFIED_METRIC",
                      "Recovery guard order drift.")
            last = {"answer_from": [call["id"]], "decision": check["decision"],
                    "facts": check["facts"], "citations": check["citations"]}
            messages += [{"role": "assistant", "content": None, "tool_calls": [call]},
                         {"role": "tool", "tool_call_id": call["id"], "content": dumps({**last, "synthetic": True})}]
            normalize(messages, tools)
            ordinal += 1
        else:
            require(not turn["tool_calls"] and turn["finish_reason"] == "stop", "Complete text turn required.")
            answer = loads(turn["content"])
            if action == "context_request":
                expected_request = {"answer_from": last["answer_from"], "request_context": True}
                equal(answer, expected_request, "Missing explicit context request.")
                equal(take("context_request")["answer"], answer, "Context request event drift.")
                followup = take("host_followup")["value"]
                equal(followup, case["given"]["user_followup"], "Host followup drift.")
                messages += [{"role": "assistant", "content": dumps(answer)},
                             {"role": "user", "content": dumps(followup)}]
            else:
                equal(answer, last, "Incomplete, unsupported or uncited final answer.")
                final = take("final")
                equal(final["answer"], answer, "Final event drift.")
                require(final["validated"] is True, "Final not validated.")
    require(cursor == len(trace), "Extra turn/retry/event after terminal answer.")
    observation = baseline.observe(case, trace)
    require(observation["complete"] is True and not observation["critical_violations"], "Incomplete trace.")
    equal(record["observation"], observation, "Trace observation drift.")
    return stages


def compile_protocol(trace_dir):
    trace_dir = Path(trace_dir)
    cases = baseline.read_cases()
    initial = [read(trace_dir / f"initial-request-{n}.json") for n in (2, 4)]
    initial_tools = baseline.tool_schema(("example_property",))
    initial_messages = [{"role": "system", "content": baseline.SYSTEM_PROMPT},
                        {"role": "user", "content": baseline.INITIAL_QUESTION}]
    equal(initial[0], {"messages": initial_messages, "tools": initial_tools}, "Initial prompt drift.")
    first, final = [read(trace_dir / f"initial-response-{n}.json") for n in (2, 4)]
    require(first["model_id"] == final["model_id"] == baseline.FakeModelRouter.model_id,
            "Initial evidence must be labelled fake.")
    require(len(first["tool_calls"]) == 1 and not first["content"] and first["finish_reason"] == "tool_calls",
            "Initial call protocol incomplete.")
    call = first["tool_calls"][0]
    payload = {"answer_from": call["id"], "metric_id": "physical_occupancy",
               "status": "certified", "subject": "example_property"}
    initial_messages += [{"role": "assistant", "content": None, "tool_calls": [call]},
                         {"role": "tool", "tool_call_id": call["id"], "content": dumps(payload)}]
    equal(initial[1], {"messages": initial_messages, "tools": initial_tools}, "Initial tool ledger drift.")
    require(final["finish_reason"] == "stop" and not final["tool_calls"], "Initial final incomplete.")
    equal(loads(final["content"]), {"answer_from": [call["id"]]}, "Initial selection drift.")
    result = read(trace_dir / "initial-tool-loop/result.json")
    require(result["status"] == "answered" and len(result["answers"]) == 1, "Initial real tool failed.")
    citation = result["answers"][0]["citation"]
    require(Path(citation["artifact"]).name == citation["artifact"], "Unsafe artifact path.")
    artifact = trace_dir / "initial-tool-loop" / citation["artifact"]
    equal(hash_file(artifact)[0], citation["sha256"], "Initial artifact drift.")
    source_artifacts = citation["source_artifacts"]
    require(bool(source_artifacts), "No initial sample provenance.")
    for source in source_artifacts:
        source_path = Path(source["artifact"])
        require(source_path.is_relative_to(baseline.ROOT / "samples"), "Only synthetic samples permitted.")
        equal(hash_file(source_path)[0], source["sha256"], "Sample source drift.")
    passed = read(trace_dir / "protocol_pass.json")
    require(passed["model_is_fake"] is True and passed["real_readonly_tool"] is True, "Initial gate absent.")
    equal(passed["artifact_sha256"], citation["sha256"], "Initial gate artifact mismatch.")
    stages = [{"case_id": INITIAL, "turn": i, "action": action, **req}
              for i, (action, req) in enumerate(zip(("call", "final"), initial))]
    for stage in stages:
        normalize(stage["messages"], stage["tools"])
    contracts = [{"case_id": INITIAL, "max_generations": 2, "max_tool_calls": 1,
                  "system": baseline.SYSTEM_PROMPT, "tools": initial_tools, "actions": ["call", "final"]}]
    for case in cases:
        rows = compile_case(case, read(trace_dir / (case["id"] + ".json")))
        stages.extend(rows)
        contracts.append({"case_id": case["id"], "max_generations": len(rows),
                          "max_tool_calls": 2 if len(rows) == 4 else 1,
                          "system": baseline.SYSTEM, "tools": baseline.case_schema(case),
                          "actions": [row["action"] for row in rows], "model_input": baseline.model_case(case)})
    equal([s["case_id"] for s in stages if s["turn"] == 0], [INITIAL] + list(baseline.BY_ID), "Exact ordered ID set required.")
    require(len(stages) == sum(c["max_generations"] for c in contracts) == 44, "Full protocol generation budget mismatch.")
    return {"version": "full-baseline-amendment-v1", "contracts": contracts, "stages": stages,
            "proposed_generations": len(stages), "proposed_max_turns_per_case": max(c["max_generations"] for c in contracts),
            "expected_sent_to_model": False, "initial_protocol_required": True,
            "evidence_kind": "EXECUTED_FAKE_TRACE_REPLAY_NOT_QWEN"}


class ReplayCursor:
    """CPU-only exact-stage replay gate; not a dynamic live-model supervisor.

    A mismatch does not advance the cursor. This exercises case ordering and
    total/per-case bounds; future live integration must bind dynamic host results.
    """
    def __init__(self, protocol):
        self._stages = copy.deepcopy(protocol["stages"])
        self.index = 0

    def accept(self, stage):
        require(self.index < len(self._stages), "Replay stage budget exhausted.", "AMENDMENT_STAGE_LIMIT")
        equal(stage, self._stages[self.index], "Out-of-order, skipped, duplicated or drifting stage.")
        normalize(stage["messages"], stage["tools"])
        self.index += 1

    def finish(self):
        require(self.index == len(self._stages), "Replay is incomplete.")
        return {"accepted_stages": self.index, "native_execution": False}


def checkpoint_identity():
    base = model_path()
    names = ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json",
             "chat_template.jinja", "model.safetensors.index.json"]
    files = {str(base / n): hash_file(base / n)[0] for n in names}
    for name, pinned in PINNED_SMALL_DIGESTS.items():
        equal(files[str(base / name)], pinned, "Pinned tokenizer/config/template drift.")
    index = read(base / "model.safetensors.index.json")
    shards = sorted(set(index["weight_map"].values()))
    require(len(shards) == 26, "Pinned checkpoint requires 26 shards.")
    identities = []
    for name in shards:
        require(Path(name).name == name and name.endswith(".safetensors"), "Unsafe shard name.")
        metadata = base / ".cache/huggingface/download" / (name + ".metadata")
        with os.fdopen(open_safe(metadata), "r") as handle:
            lines = handle.read(4096).splitlines()
        require(len(lines) >= 2 and lines[0] == REVISION and HASH.fullmatch(lines[1]), "Cached LFS identity drift.")
        files[str(metadata)] = hash_file(metadata)[0]
        with os.fdopen(open_safe(base / name), "rb") as handle:
            size = os.fstat(handle.fileno()).st_size  # No payload reads.
        identities.append({"path": str(base / name), "bytes": size, "expected_sha256": lines[1],
                           "provenance": "LOCAL_CACHED_LFS_METADATA_NOT_PAYLOAD_REHASH"})
    return files, identities


def frozen_inputs(trace_dir, authority):
    protocol = compile_protocol(trace_dir)
    authority = Path(authority)
    auth = read(authority)
    require(auth.get("native_qwen_gpu_load") == "NOT_AUTHORIZED" and
            auth.get("training") == "NOT_AUTHORIZED_NOT_STARTED", "Expected unapproved authority record.")
    paths = set(baseline.ROOT.joinpath("harness/src/plat_harness").rglob("*.py"))
    paths.update(baseline.ROOT.joinpath("tests").rglob("*.py"))
    paths.update([baseline.ROOT / "tests/test_baseline_eval.py", baseline.ROOT / "pyproject.toml", authority])
    paths.update(p for p in baseline.ROOT.joinpath("samples").rglob("*") if p.is_file())
    paths.update(p for p in baseline.ROOT.joinpath("docs/eval").rglob("*")
                 if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")
    paths.update(p for p in Path(trace_dir).rglob("*") if p.is_file())
    files = {str(p): hash_file(p)[0] for p in sorted(paths)}
    model_files, shards = checkpoint_identity()
    files.update(model_files)
    configured_runtime = runtime_python()
    runtime = Path(configured_runtime).resolve(strict=True)
    files[str(runtime)] = hash_file(runtime)[0]
    return {"protocol": protocol, "files": files, "checkpoint_shards": shards,
            "trace_dir": str(trace_dir), "authority": str(authority),
            "bindings": [asdict(b) for b in baseline.BINDINGS],
            "model_id": MODEL_ID, "revision": REVISION, "model_path": str(base),
            "runtime": configured_runtime, "runtime_resolved": str(runtime),
            "runtime_versions": {n: importlib.metadata.version(n) for n in ("transformers", "tokenizers", "torch")},
            "active_limits_unchanged": copy.deepcopy(LIMITS), "active_max_turns_per_question": 2,
            "controls": copy.deepcopy(CONTROLS),
            "policy": {"dtype": "bfloat16", "local_files_only": True, "trust_remote_code": False,
                       "offload": False, "quantization": False, "fallback": False,
                       "do_sample": False, "enable_thinking": False, "truncation": False}}


def render_stages(protocol, tokenizer):
    cursor, rows = ReplayCursor(protocol), []
    for stage in protocol["stages"]:
        cursor.accept(stage)
        text = dumps(stage["messages"])
        ids = [c["id"] for m in stage["messages"] for c in m.get("tool_calls", [])]
        for i, cid in enumerate(ids):
            text = text.replace(cid, "native_" + "0a" * 15 + f"{i:02x}" + "_0")
        variants = []
        for label, messages in (("exact_trace", stage["messages"]), ("native_id_stress", loads(text))):
            prompt = render_prompt(tokenizer, messages, stage["tools"])
            variants.append({"variant": label, "tokens": len(prompt.input_ids),
                             "prompt_sha256": hashlib.sha256(prompt.text.encode()).hexdigest(),
                             "input_ids_sha256": sha(prompt.input_ids)})
        rows.append({"case_id": stage["case_id"], "turn": stage["turn"], "action": stage["action"],
                     "schema_sha256": sha(stage["tools"]), "renderings": variants})
    return {**cursor.finish(), "records": rows,
            "min_tokens": min(v["tokens"] for r in rows for v in r["renderings"]),
            "max_tokens": max(v["tokens"] for r in rows for v in r["renderings"]),
            "limitation": "Exact fake histories and native-ID stress only; future live prompts need runtime checks."}


def tokenizer_boundaries(tokenizer, stage):
    """Separate synthetic boundary probes; never alter frozen stage messages."""
    base = copy.deepcopy(stage["messages"])
    initial = len(render_prompt(tokenizer, base, stage["tools"]).input_ids)
    records = []
    for target in (1536, 1537):
        padding = target - initial
        for _ in range(8):
            messages = copy.deepcopy(base)
            messages[1]["content"] += " x" * padding
            normalized = normalize(messages, stage["tools"])
            text = tokenizer.apply_chat_template(normalized, tools=stage["tools"], tokenize=False,
                    add_generation_prompt=True, enable_thinking=False, preserve_thinking=False)
            ids = tokenizer(text, add_special_tokens=False, truncation=False)["input_ids"]
            if len(ids) == target:
                break
            padding += target - len(ids)
            require(padding >= 0, "Synthetic boundary probe cannot reach target.")
        require(len(ids) == target, "Actual tokenizer boundary not reached.")
        try:
            prompt = render_prompt(tokenizer, messages, stage["tools"])
        except HarnessError as exc:
            require(target == 1537 and exc.code == "NATIVE_PROMPT_LIMIT", "Unexpected boundary refusal.")
            outcome = "REJECTED_NATIVE_PROMPT_LIMIT"
        else:
            require(target == 1536 and len(prompt.input_ids) == target, "Boundary accepted outside bound.")
            outcome = "ACCEPTED"
        records.append({"synthetic_boundary_only": True, "tokens": target, "outcome": outcome,
                        "prompt_sha256": hashlib.sha256(text.encode()).hexdigest()})
    return records


def prepare(trace_dir, authority, output):
    cpu_controls()
    require(sys.executable == runtime_python(), "Use pinned tokenizer runtime.", "AMENDMENT_RUNTIME")
    frozen = frozen_inputs(trace_dir, authority)
    # Lazy tokenizer-only import. Never import a model class or call a loader.
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(model_path(), local_files_only=True, trust_remote_code=False)
    token_fit = render_stages(frozen["protocol"], tokenizer)
    token_fit["actual_tokenizer_boundaries"] = tokenizer_boundaries(tokenizer, frozen["protocol"]["stages"][0])
    cpu_controls()
    equal(frozen_inputs(trace_dir, authority), frozen, "Inputs changed during tokenizer preflight.")
    manifest = {"status": STATUS, "approved": False, "enabled": False,
                "native_launch_supported": False, "measured_model_metrics": None,
                "model_loaded": False, "cuda_initialized": False,
                "frozen": frozen, "token_fit": token_fit,
                "tokenizer_class": type(tokenizer).__name__, "template_sha256": TEMPLATE_SHA256,
                "runtime_versions": {n: importlib.metadata.version(n) for n in ("transformers", "tokenizers", "torch")},
                "required_next_dependency": "Reviewed separate native host/worker/supervisor integration plus explicit one-run authorization; this module cannot launch."}
    store = Store(output)
    try:
        digest = store.write("amendment.UNAPPROVED.json", manifest)
        store.write("preflight.json", {"status": STATUS, "manifest_sha256": digest,
                    "stages": token_fit["accepted_stages"], "max_tokens": token_fit["max_tokens"],
                    "native_launch_supported": False, "measured_model_metrics": None})
    finally:
        store.close()
    return {"manifest_sha256": digest, "status": STATUS, "stages": token_fit["accepted_stages"],
            "min_tokens": token_fit["min_tokens"], "max_tokens": token_fit["max_tokens"]}


def verify(manifest_path, expected_sha256):
    cpu_controls()
    require(isinstance(expected_sha256, str) and HASH.fullmatch(expected_sha256), "Literal lowercase SHA256 required.", "AMENDMENT_HASH")
    equal(hash_file(Path(manifest_path))[0], expected_sha256, "Manifest bytes drift.")
    manifest = read(manifest_path)
    require(manifest["status"] == STATUS and manifest["approved"] is False and manifest["enabled"] is False
            and manifest["native_launch_supported"] is False, "An amendment cannot authorize inference.")
    frozen = manifest["frozen"]
    equal(frozen_inputs(Path(frozen["trace_dir"]), Path(frozen["authority"])), frozen, "Frozen dependency/protocol drift.")
    return {"status": "VERIFIED_DISABLED_UNAPPROVED", "native_launch_supported": False,
            "manifest_sha256": expected_sha256, "files": len(frozen["files"]),
            "checkpoint_payload_rehashed": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="CPU tokenizer preflight; writes an unapproved proposal only")
    p.add_argument("--trace-dir", required=True, type=Path)
    p.add_argument("--authority", required=True, type=Path)
    p.add_argument("--output", required=True, type=Path)
    v = sub.add_parser("verify", help="Recompute dependency identities; never load weights")
    v.add_argument("--manifest", required=True, type=Path)
    v.add_argument("--sha256", required=True)
    sub.add_parser("native", help="Unconditional refusal, not a launcher")
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.command == "native":
            native_guard()
        result = (prepare(args.trace_dir, args.authority, args.output) if args.command == "prepare"
                  else verify(args.manifest, args.sha256))
        print(dumps(result))
        return 0
    except (HarnessError, OSError, KeyError, ValueError, TypeError) as exc:
        print(dumps({"status": STATUS, "error": getattr(exc, "code", type(exc).__name__),
                     "native_launch_supported": False, "measured_model_metrics": None}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
