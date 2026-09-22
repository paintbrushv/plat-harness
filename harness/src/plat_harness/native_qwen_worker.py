"""Bounded JSONL native Transformers worker. Never import Unsloth.

Only native_supervisor may supply the inherited permit FD. This file is not a
notebook or an approval generator. No weights are touched by importing it.
"""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import os
from pathlib import Path
import sys
import time

from plat_harness.native_qwen import (MAX_LINE, MODEL_ID, REVISION,
                                      dumps, loads, model_path, refuse, render_prompt, runtime_python)


def emit(payload):
    raw = dumps(payload)
    if len(raw.encode()) + 1 > MAX_LINE:
        refuse("NATIVE_OUTPUT_LIMIT", "Worker frame exceeds bound.")
    print(raw, flush=True)


def read_request(stream):
    raw = stream.readline(MAX_LINE + 1)
    if not raw:
        return None
    if len(raw) > MAX_LINE or not raw.endswith(b"\n"):
        refuse("NATIVE_INPUT_LIMIT", "Overlong or incomplete worker JSONL request.")
    req = loads(raw)
    if not isinstance(req, dict) or set(req) != {"op", "id", "messages", "tools"} or req["op"] != "generate":
        refuse("NATIVE_PROTOCOL", "Unexpected worker request.")
    return req


def audit_model(model, loading_info, torch):
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
    return {"parameter_counts": dict(counts), "loading_info": loading_info,
            "architecture": type(model).__name__, "quantization": None,
            "hf_device_map": getattr(model, "hf_device_map", None),
            "buffer_policy": "CUDA only; BF16 or native FP32 non-parameter buffers"}


def run(permit_fd):
    # Checks happen before torch import or GPU initialization.
    import ctypes
    import signal
    # Even an uncatchable supervisor SIGKILL must not leave a resident model.
    # The permit's parent-PID check below closes the pre-prctl orphan race.
    if ctypes.CDLL(None).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        refuse("NATIVE_CLEANUP", "Cannot install parent-death guard.")
    with os.fdopen(permit_fd, "rb") as f:
        raw = f.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        refuse("NATIVE_AUTH", "Permit exceeds bound.")
    permit = loads(raw)
    if (permit.get("parent_pid") != os.getppid()
            or permit.get("scope") != "one_native_bf16_synthetic_inference_run"
            or not permit.get("authorization_sha256") or not permit.get("manifest_sha256")):
        refuse("NATIVE_AUTH", "Missing supervisor-issued inference permit.")
    if sys.executable != runtime_python() or os.environ.get("CUDA_VISIBLE_DEVICES") != "0":
        refuse("NATIVE_RUNTIME", "Worker must use reviewed native runtime and explicit CUDA:0.")
    for key in ("HF_HUB_OFFLINE", "TRANSFORMERS_OFFLINE"):
        if os.environ.get(key) != "1":
            refuse("NATIVE_OFFLINE", "Offline controls are mandatory.")
    for name, expected in permit["verified_stamps"].items():
        st = Path(name).stat(follow_symlinks=False)
        if [st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns] != expected:
            refuse("NATIVE_HASH", "File changed between hash gate and worker startup.")
    # All library diagnostics go to stderr, never the JSONL transport.
    with redirect_stdout(sys.stderr):
        import torch
        from transformers import AutoTokenizer, Qwen3_5MoeForConditionalGeneration
        if any(n == "unsloth" or n.startswith("unsloth.") for n in sys.modules):
            refuse("NATIVE_RUNTIME", "Unsloth patches are forbidden.")
        tokenizer = AutoTokenizer.from_pretrained(model_path(), local_files_only=True, trust_remote_code=False)
        # Full schemas and frozen initial prompts must fit BEFORE weight loading.
        manifest = permit["manifest"]
        prompt_counts = []
        for question in manifest["questions"]:
            p = render_prompt(tokenizer, [{"role": "system", "content": manifest["system_prompt"]},
                                          {"role": "user", "content": question}], manifest["tools"])
            prompt_counts.append(len(p.input_ids))
        start = time.monotonic()
        model, info = Qwen3_5MoeForConditionalGeneration.from_pretrained(
            model_path(), dtype=torch.bfloat16, device_map={"": "cuda:0"},
            low_cpu_mem_usage=True, local_files_only=True, trust_remote_code=False,
            attn_implementation="sdpa", output_loading_info=True)
        audit = audit_model(model, info, torch)
        model.eval()
        torch.cuda.synchronize()
        load_s = time.monotonic() - start
    emit({"type": "ready", "model_id": MODEL_ID, "revision": REVISION,
          "load_s": load_s, "audit": audit, "initial_prompt_tokens": prompt_counts})
    requests = set()
    while True:
        req = read_request(sys.stdin.buffer)
        if req is None:
            return 0
        rid = req["id"]
        if not isinstance(rid, str) or rid in requests or len(requests) >= 42:
            refuse("NATIVE_PROTOCOL", "Duplicate request ID or generation count exceeded.")
        requests.add(rid)
        with redirect_stdout(sys.stderr):
            prompt = render_prompt(tokenizer, req["messages"], req["tools"])
            inputs = torch.tensor([prompt.input_ids], dtype=torch.long, device="cuda:0")
            start = time.monotonic()
            # Use a fresh explicit config; do not inherit vendor sampling/beam,
            # speculative or compilation settings from generation_config.json.
            from transformers import GenerationConfig
            config = GenerationConfig(max_new_tokens=512, do_sample=False, num_beams=1,
                                      num_return_sequences=1, use_cache=True,
                                      eos_token_id=tokenizer.eos_token_id,
                                      pad_token_id=tokenizer.pad_token_id,
                                      bos_token_id=tokenizer.bos_token_id,
                                      disable_compile=True)
            with torch.inference_mode():
                output = model.generate(input_ids=inputs, attention_mask=torch.ones_like(inputs),
                                        generation_config=config)
            torch.cuda.synchronize()
            generation_s = time.monotonic() - start
            ids = output[0, inputs.shape[1]:].tolist()
            complete = bool(ids) and ids[-1] == tokenizer.eos_token_id and len(ids) < 512
            # Remove ONLY the verified terminal EOS; never hide special tokens
            # that could signal injection, thinking or malformed output.
            text = tokenizer.decode(ids[:-1] if complete else ids, skip_special_tokens=False)
            del output, inputs
        emit({"type": "result", "id": rid, "model_id": MODEL_ID, "revision": REVISION,
              "text": text, "finish_reason": "stop" if complete else "length",
              "prompt_tokens": len(prompt.input_ids), "generated_tokens": len(ids),
              "generation_s": generation_s})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--permit-fd", type=int, required=True)
    args = parser.parse_args()
    try:
        return run(args.permit_fd)
    except BaseException as exc:
        # Preserve traceback in the private stderr, not raw hidden reasoning.
        import traceback
        traceback.print_exc(file=sys.stderr)
        emit({"type": "error", "code": getattr(exc, "code", type(exc).__name__)})
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
