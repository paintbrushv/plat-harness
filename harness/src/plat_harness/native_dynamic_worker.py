"""Guarded native Transformers worker and CPU-checked transport for dynamic baseline.

Supports both:
1. Production native execution behind supervisor-issued permit (run_native).
2. CPU fixture injection for tests (serve_cpu_fixture).
Never import Unsloth. No weights or CUDA initializations occur on import.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import ctypes
import hashlib
import os
from pathlib import Path
import signal
import sys
import time

from plat_harness import baseline_eval as b
from plat_harness.errors import HarnessError
from plat_harness.native_baseline_amendment import cpu_controls
from plat_harness.native_dynamic_gate import CANDIDATE_SCOPE
from plat_harness.native_qwen import (
    MAX_LINE, MODEL_ID, MODEL_PATH, REVISION, RUNTIME,
    dumps, loads, refuse, render_prompt
)
from plat_harness.native_qwen_worker import emit, read_request
from plat_harness.native_runtime_candidate import EXACT_IDENTITY

CPU_MODE = "CPU_FAKE_WORKER_REAL_IPC_NOT_QWEN"
MAX_GENERATIONS = 44


def audit_model(model, loading_info, torch):
    """Audit loaded candidate native model for correct architecture, device, and BF16 precision."""
    from collections import Counter
    if type(model).__name__ != "Qwen3_5MoeForConditionalGeneration" or model.config.model_type != "qwen3_5_moe":
        refuse("NATIVE_ARCHITECTURE", "Unexpected native model architecture.")
    if getattr(model.config, "quantization_config", None) is not None:
        refuse("NATIVE_PRECISION", "Quantization is forbidden.")
    if any(loading_info.get(k) for k in ("missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs")):
        refuse("NATIVE_LOADING", "Incomplete or mismatched checkpoint loading.")
    counts = Counter()
    for name, parameter in model.named_parameters():
        if str(parameter.device) != "cuda:0" or parameter.dtype != torch.bfloat16:
            refuse("NATIVE_PLACEMENT", "Base parameter is not CUDA:0 BF16; no fallback.")
        counts[f"{parameter.device}/{parameter.dtype}"] += parameter.numel()
        parameter.requires_grad_(False)
    if not counts:
        refuse("NATIVE_LOADING", "No parameters loaded.")
    for name, buffer in model.named_buffers():
        if str(buffer.device) != "cuda:0":
            refuse("NATIVE_PLACEMENT", "Buffer has unplanned placement.")
        if buffer.is_floating_point() and buffer.dtype not in (torch.bfloat16, torch.float32):
            refuse("NATIVE_PRECISION", "Unexpected buffer dtype.")
    return {
        "parameter_counts": dict(counts),
        "loading_info": loading_info,
        "architecture": type(model).__name__,
        "quantization": None,
        "hf_device_map": getattr(model, "hf_device_map", None),
        "buffer_policy": "CUDA only; BF16 or native FP32 non-parameter buffers",
    }


def load_cpu_tokenizer():
    """Hash pinned small payloads before a local-only tokenizer import for tests."""
    cpu_controls()
    if sys.executable != RUNTIME:
        refuse("DYNAMIC_TOKENIZER_RUNTIME", "Use the exact reviewed tokenizer interpreter.")
    from plat_harness.native_gate import PINNED_SMALL_DIGESTS, hash_file
    for name, expected in PINNED_SMALL_DIGESTS.items():
        if hash_file(Path(MODEL_PATH) / name)[0] != expected:
            refuse("NATIVE_HASH", "Pinned tokenizer/config/template bytes changed.")
    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=False)
    cpu_controls()
    return tokenizer


def serve_cpu_fixture(tokenizer, generator):
    """Real JSONL IPC with actual tokenizer; generator is explicitly not Qwen."""
    cpu_controls()
    parent = os.getppid()
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0 or os.getppid() != parent:
        refuse("NATIVE_CLEANUP", "Cannot bind worker lifetime to supervisor.")
    emit({
        "type": "ready", "model_id": MODEL_ID, "revision": REVISION,
        "audit": {
            "CPU_FAKE_WORKER": True, "mode": CPU_MODE,
            "tokenizer_class": type(tokenizer).__name__, "model_loaded": False
        }
    })
    seen = set()
    while True:
        req = read_request(sys.stdin.buffer)
        if req is None:
            return 0
        rid = req["id"]
        try:
            if rid in seen or len(seen) >= MAX_GENERATIONS:
                refuse("DYNAMIC_PROTOCOL", "Duplicate request ID or generation limit reached.")
            seen.add(rid)
            prompt = render_prompt(tokenizer, req["messages"], req["tools"])
            print(dumps({"event": "actual_dynamic_prompt", "id": rid, "mode": CPU_MODE,
                         "prompt_tokens": len(prompt.input_ids),
                         "prompt_sha256": hashlib.sha256(prompt.text.encode()).hexdigest(),
                         "schema_sha256": prompt.schema_sha256}), file=sys.stderr, flush=True)
            text, finish_reason = generator(req, prompt)
            out_tokens = len(tokenizer.encode(text, add_special_tokens=False)) + 1
            emit({
                "type": "result", "id": rid, "model_id": MODEL_ID, "revision": REVISION,
                "text": text, "finish_reason": finish_reason,
                "prompt_tokens": len(prompt.input_ids), "generated_tokens": out_tokens,
                "generation_s": 0.001
            })
        except BaseException as exc:
            emit({"type": "error", "id": rid, "code": getattr(exc, "code", type(exc).__name__)})
            return 1


def run_native(permit_fd: int):
    """Guarded native production worker entrypoint behind supervisor-issued permit."""
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        refuse("NATIVE_CLEANUP", "Cannot install parent-death guard.")

    with os.fdopen(permit_fd, "rb") as f:
        raw = f.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        refuse("NATIVE_AUTH", "Permit exceeds bound.")
    permit = loads(raw)

    if (permit.get("parent_pid") != os.getppid()
            or permit.get("scope") != CANDIDATE_SCOPE
            or not permit.get("authorization_sha256")
            or not permit.get("manifest_sha256")):
        refuse("NATIVE_AUTH", "Missing or invalid supervisor-issued candidate permit.")

    if sys.executable != RUNTIME or os.environ.get("CUDA_VISIBLE_DEVICES") != "0":
        refuse("NATIVE_RUNTIME", "Worker must use reviewed native runtime and explicit CUDA:0.")

    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if os.environ.get(key) != "1":
            refuse("NATIVE_OFFLINE", "Offline controls are mandatory.")

    for name, expected in permit["verified_stamps"].items():
        st = Path(name).stat(follow_symlinks=False)
        if [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns] != expected:
            refuse("NATIVE_HASH", "File changed between hash gate and worker startup.")

    with redirect_stdout(sys.stderr):
        import torch
        from transformers import AutoTokenizer, Qwen3_5MoeForConditionalGeneration, GenerationConfig

        if any(n == "unsloth" or n.startswith("unsloth.") for n in sys.modules):
            refuse("NATIVE_RUNTIME", "Unsloth patches are forbidden.")

        tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, local_files_only=True, trust_remote_code=False)

        manifest = permit["manifest"]
        # Pre-verify token budgets for initial question and all frozen cases
        initial_prompt = render_prompt(
            tokenizer,
            [{"role": "system", "content": b.SYSTEM_PROMPT},
             {"role": "user", "content": b.INITIAL_QUESTION}],
            b.tool_schema(("example_property",))
        )
        if len(initial_prompt.input_ids) > 1536:
            refuse("NATIVE_INPUT_LIMIT", "Initial prompt exceeds 1536 token bound.")

        for case_dict in b.read_cases():
            case = b.model_case(case_dict)
            p = render_prompt(tokenizer, b.messages_for(case), b.case_schema(case))
            if len(p.input_ids) > 1536:
                refuse("NATIVE_INPUT_LIMIT", f"Case {case['id']} prompt exceeds 1536 token bound.")

        start = time.monotonic()
        model, info = Qwen3_5MoeForConditionalGeneration.from_pretrained(
            MODEL_PATH,
            dtype=torch.bfloat16,
            device_map={"": "cuda:0"},
            low_cpu_mem_usage=True,
            local_files_only=True,
            trust_remote_code=False,
            attn_implementation="sdpa",
            output_loading_info=True
        )
        audit = audit_model(model, info, torch)
        model.eval()
        torch.cuda.synchronize()
        load_s = time.monotonic() - start

    emit({
        "type": "ready",
        "model_id": MODEL_ID,
        "revision": REVISION,
        "load_s": load_s,
        "audit": audit,
        "mode": "PRODUCTION_NATIVE_BF16",
        "initial_prompt_tokens": len(initial_prompt.input_ids),
    })

    requests = set()
    while True:
        req = read_request(sys.stdin.buffer)
        if req is None:
            return 0
        rid = req["id"]
        if not isinstance(rid, str) or rid in requests or len(requests) >= MAX_GENERATIONS:
            refuse("NATIVE_PROTOCOL", "Duplicate request ID or generation count exceeded.")
        requests.add(rid)

        with redirect_stdout(sys.stderr):
            prompt = render_prompt(tokenizer, req["messages"], req["tools"])
            if len(prompt.input_ids) > 1536:
                refuse("NATIVE_INPUT_LIMIT", "Prompt length exceeds 1536 tokens.")
            inputs = torch.tensor([prompt.input_ids], dtype=torch.long, device="cuda:0")
            gen_start = time.monotonic()
            config = GenerationConfig(
                max_new_tokens=512,
                do_sample=False,
                num_beams=1,
                num_return_sequences=1,
                use_cache=True,
                eos_token_id=tokenizer.eos_token_id,
                pad_token_id=tokenizer.pad_token_id,
                bos_token_id=tokenizer.bos_token_id,
                disable_compile=True,
            )
            with torch.inference_mode():
                output = model.generate(
                    input_ids=inputs,
                    attention_mask=torch.ones_like(inputs),
                    generation_config=config,
                )
            torch.cuda.synchronize()
            generation_s = time.monotonic() - gen_start
            ids = output[0, inputs.shape[1]:].tolist()
            complete = bool(ids) and ids[-1] == tokenizer.eos_token_id and len(ids) < 512
            text = tokenizer.decode(ids[:-1] if complete else ids, skip_special_tokens=False)
            del output, inputs

        emit({
            "type": "result",
            "id": rid,
            "model_id": MODEL_ID,
            "revision": REVISION,
            "text": text,
            "finish_reason": "stop" if complete else "length",
            "prompt_tokens": len(prompt.input_ids),
            "generated_tokens": len(ids),
            "generation_s": generation_s,
        })


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", nargs="?", default="native", choices=["native"])
    parser.add_argument("--permit-fd", type=int,
                        help="Supervisor-issued candidate permit FD.")
    args = parser.parse_args(argv)
    if args.permit_fd is not None:
        try:
            return run_native(args.permit_fd)
        except BaseException as exc:
            import traceback
            traceback.print_exc(file=sys.stderr)
            emit({"type": "error", "code": getattr(exc, "code", type(exc).__name__)})
            return 1
    print(dumps({"status": "DISABLED_UNAPPROVED_DYNAMIC_NATIVE_CANDIDATE",
                 "error": "DYNAMIC_NATIVE_DISABLED", "enabled": False,
                 "native_launch_supported": False, "measured_model_metrics": None}))
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
