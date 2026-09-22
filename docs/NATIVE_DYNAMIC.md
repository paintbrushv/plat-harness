# Disabled dynamic native-Qwen baseline candidate

**Status: DISABLED / UNAPPROVED. CPU fake-worker integration, not Qwen quality.**

`native_dynamic.py`, `native_dynamic_supervisor.py`, and
`native_dynamic_worker.py` add a live request/response path distinct from the
older `native_baseline_amendment.py` replay compiler. They do not change
`native_gate`, `native_supervisor`, `native_qwen_worker`, `baseline_eval`,
`tool_loop`, the CLI, or frozen evaluation data.

## Executable behavior

- `DynamicModel` uses the existing fork-safe `NativeModel` socket transport and
  strict Qwen function parser, host-generated request/call IDs, response identity
  validation, normalization and ModelRouter interface.
- `DynamicProtocol` owns an incremental supervisor-side state machine. It takes
  current parsed model output, independently executes existing bounded read-only
  host checks, then validates the next exact request ledger. It never loads
  preserved response traces or uses `ReplayCursor`.
- The first two generations must complete a real deterministic sample occupancy
  tool roundtrip. The unchanged `run_question` also executes the actual tool,
  validates its counts/provenance, and persists an artifact whose hash is read
  back before the twenty cases begin. Supervisor-side checks intentionally
  repeat read-only host execution; this is not another financial engine.
- All exact twenty IDs, original prompts/given data and per-case allowlisted
  schemas come from `baseline_eval`. Only scoring sees `expected`. CPU tests can
  explicitly supply changed synthetic `given`/`expected` to demonstrate dynamic
  results and leakage independence; this seam is not native authority.
- Recovery must be call → conflict → explicit context request → exact host user
  followup → one context-bearing retry → terminal refusal. Missing/changed roles,
  IDs, tool observations, schema or followup, wrong numbers/citations, and extra
  calls cannot advance the protocol. A failed case stops further admission.
- The CPU worker uses `render_prompt` on **each actual received history** before
  generating even a deliberately malformed output. It enforces the pinned
  template and actual 1536 prompt / 512 output / 2048 total policy without
  truncation. The CPU tokenizer loader checks the exact interpreter and pinned
  config/tokenizer/template SHA256s before its local-only import. Requests, raw
  responses, prompt counts/hashes, stdout, stderr,
  transitions, memory/phase telemetry and actual worker/supervisor exits persist.
- The isolated candidate loop derives from the existing supervisor event loop
  and reuses its exclusive `Store`, memory checks, process-group TERM/KILL and
  Linux subreaper cleanup. No active constants or functions are monkeypatched.
  The CPU API admits at most 44 generations, 900 seconds load, 180 seconds per
  generation and 9000 seconds whole lifetime. Whole lifetime includes idle time;
  incomplete shutdown is not success. These ceilings are not measured ETA.

## Entry points and explicit test seam

All three module entrypoints accept only `native` and always exit 2 with
`DYNAMIC_NATIVE_DISABLED`, before loading, spawning or connecting. There is no
CLI fake-worker option, approval boolean, environment switch or old permit that
can enable the candidate.

The **test-only API** `supervise_cpu(store, fixture_worker=..., cases=...)` requires
exact campaign/offline controls and hidden CUDA. The worker must announce itself
as `CPU_FAKE_WORKER`. `serve_cpu_fixture(tokenizer, generator)` is the corresponding
CPU generator injection seam. The fake generator lives only in
`tests/test_native_dynamic_worker.py`; it reacts to current tool results and IDs,
not pre-recorded responses, and loads only the local pinned tokenizer.

`run_cpu_connected(output, supervisor_dir)` drives the unchanged host baseline
through that already running, explicitly labelled supervisor. It does not launch
or assume successful cleanup. Its result requires the owning caller to send
shutdown, wait for the actual supervisor exit and validate `exit.json`; tests do
this and inspect the empty process group. Fake generation latency is never
reported as Qwen latency, and model-quality/TTFT/inference metrics remain null.

Focused tests (use an exclusively new private basetemp and umask 077):

```sh
COMPS_MODE=skip MAX_DEALS=5 ALLOW_LIFECYCLE=0 \
CUDA_VISIBLE_DEVICES='' HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 \
PYTHONDONTWRITEBYTECODE=1 \
$PYTHON -B -m pytest \
-p no:cacheprovider --basetemp /ABSOLUTE/NEW/PRIVATE/tmp \
--junitxml /ABSOLUTE/NEW/PRIVATE/junit.xml \
tests/test_native_dynamic.py
```

The subprocess fake uses `$SOVEREIGN_RUNTIME_PYTHON` solely for
CPU/offline tokenizer imports. No Unsloth module, model class, weight loading,
GPU execution, adapter, optimizer or package/environment modification is used.

## What is still blocked

This is a substantive dynamic CPU integration, **not a production native loader**.
The old worker still caps execution at 42 generations; its manifest admits only
fixed occupancy/two-turn histories and cannot be reused as full-baseline
permission. A future separately reviewed native worker/gate must connect this
protocol to authorized generation without weakening BF16/local-only/no remote
code/no quantization/no offload/one-resident-model and memory rules.

Parent-owned runtime identity diagnostics distinguish package metadata from the
compiled version, not GB10/Qwen forward compatibility. Before any inference:
settle source; freeze all dynamic sources/tests, scorer, frozen cases,
system/schema/protocol, sample inputs, tokenizer/template, checkpoint and exact
runtime identity; review the explicit **44 vs 42** generation and **four vs two**
turn amendment; then obtain new one-run manifest-bound authority. This document,
a passing CPU score or a diagnostic identity record grants none of that authority.
