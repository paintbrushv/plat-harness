"""Host-owned approval and runtime identity gate for dynamic native candidate.

Enforces literal SHA256 bindings, one-run scope, candidate limits (44 generations,
4-turn recovery), exact model revision, exact runtime identity (comparing distribution
metadata 2.11.0 and build version 2.11.0+cu130 independently), and GPU process exclusion.
No model weights, CUDA initialization, or training occur here.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import platform
import stat
import subprocess
from typing import Any

from plat_harness import baseline_eval as b
from plat_harness.errors import HarnessError
from plat_harness.native_gate import hash_file, open_safe, read_private, gpu_processes
from plat_harness.native_qwen import MODEL_ID, MODEL_PATH, REVISION, RUNTIME, TEMPLATE_SHA256, loads, refuse
from plat_harness.native_runtime_candidate import EXACT_FILES, EXACT_IDENTITY

CANDIDATE_SCOPE = "one_native_bf16_dynamic_inference_run"

CANDIDATE_LIMITS = {
    "load_s": 900,
    "generation_s": 180,
    "whole_s": 9000,
    "max_generations": 44,
    "prompt_tokens": 1536,
    "output_tokens": 512,
    "total_tokens": 2048,
    "min_available_gib": 24,
    "max_workload_gib": 90,
    "max_swap_growth_gib": 1,
}

CONTROLS = {
    "COMPS_MODE": "skip",
    "MAX_DEALS": "5",
    "ALLOW_LIFECYCLE": "0",
}

PINNED_SMALL_DIGESTS = {
    "config.json": "93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99",
    "tokenizer.json": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
    "chat_template.jinja": TEMPLATE_SHA256,
}


def check_runtime_probe(runtime_python=None):
    """Inspect runtime environment without CUDA initialization or model loading."""
    exe = runtime_python or RUNTIME
    cmd = [
        exe, "-B", "-c",
        "import importlib.metadata as m, sys, platform, torch, json\n"
        "info = {\n"
        "    'python': sys.executable,\n"
        "    'python_version': list(sys.version_info[:3]),\n"
        "    'machine': platform.machine(),\n"
        "    'distribution_version': m.version('torch'),\n"
        "    'torch_version': torch.__version__,\n"
        "    'compiled_cuda': torch.version.cuda,\n"
        "    'torch_git_version': torch.version.git_version,\n"
        "    'transformers_version': m.version('transformers'),\n"
        "    'tokenizers_version': m.version('tokenizers'),\n"
        "    'cuda_initialized': torch.cuda.is_initialized()\n"
        "}\n"
        "print(json.dumps(info))\n"
    ]
    env = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
    }
    try:
        proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=30, check=True)
        data = loads(proc.stdout)
    except Exception as exc:
        refuse("NATIVE_RUNTIME", f"Runtime probe execution failed: {exc}")

    for key, expected in EXACT_IDENTITY.items():
        actual = data.get(key)
        if actual != expected:
            refuse("NATIVE_RUNTIME", f"Runtime identity mismatch for {key}: expected {expected}, got {actual}")

    if data.get("cuda_initialized") is not False:
        refuse("NATIVE_RUNTIME", "CUDA must not be initialized during runtime inspection.")

    return data


def verify_runtime_files():
    """Verify hash and size of pinned runtime library files."""
    for path_str, expected in EXACT_FILES.items():
        p = Path(path_str)
        if not p.exists():
            refuse("NATIVE_RUNTIME", f"Required runtime file missing: {path_str}")
        digest, st = hash_file(p)
        if digest != expected["sha256"]:
            refuse("NATIVE_RUNTIME", f"Runtime file hash mismatch for {path_str}: expected {expected['sha256']}, got {digest}")


def authorize_candidate(authorization: Path, authorization_sha256: str, manifest_path: Path, output_dir: Path):
    """Authorizes the dynamic native candidate behind an exact host approval envelope."""
    if (platform.node() != "spark-17d5" or platform.system() != "Linux"
            or platform.machine() != "aarch64" or os.getuid() != 1000):
        refuse("NATIVE_HOST", "Dynamic candidate inference is pinned to spark-17d5/mdai/aarch64.")

    if len(authorization_sha256) != 64 or any(c not in "0123456789abcdef" for c in authorization_sha256):
        refuse("NATIVE_AUTH", "Supply literal reviewed approval SHA256.")

    auth, auth_sha = read_private(authorization, authorization_sha256)
    required_auth_keys = {"approved", "approval_kind", "scope", "manifest_sha256", "output_dir", "expires_at", "approval_evidence"}
    if not isinstance(auth, dict) or set(auth) != required_auth_keys or auth["approved"] is not True:
        refuse("NATIVE_AUTH", "Explicit candidate inference authorization is absent.")

    if auth["approval_kind"] != "explicit_human_inference" or auth["scope"] != CANDIDATE_SCOPE:
        refuse("NATIVE_AUTH", f"Approval scope must be {CANDIDATE_SCOPE}.")

    if auth["output_dir"] != str(output_dir) or not isinstance(auth["approval_evidence"], str) or not auth["approval_evidence"]:
        refuse("NATIVE_AUTH", "Approval must bind exact output directory and non-empty approval evidence.")

    try:
        expiry = dt.datetime.fromisoformat(auth["expires_at"])
        now = dt.datetime.now(dt.timezone.utc)
        if expiry.tzinfo is None or expiry <= now or expiry - now > dt.timedelta(days=1):
            refuse("NATIVE_AUTH", "Approval expired or not bounded within 24 hours.")
    except (ValueError, TypeError):
        refuse("NATIVE_AUTH", "Invalid approval expiry format.")

    manifest_digest = auth["manifest_sha256"]
    if not isinstance(manifest_digest, str) or len(manifest_digest) != 64 or any(c not in "0123456789abcdef" for c in manifest_digest):
        refuse("NATIVE_AUTH", "Approval must bind literal manifest SHA256.")

    manifest, manifest_sha = read_private(manifest_path, manifest_digest)
    required_manifest_keys = {
        "model_id", "revision", "model_path", "runtime", "limits", "controls",
        "files", "synthetic_only", "initial_question", "case_ids", "max_generations"
    }
    if not isinstance(manifest, dict) or not required_manifest_keys.issubset(set(manifest)):
        refuse("NATIVE_MANIFEST", "Missing required candidate manifest fields.")

    if (manifest["model_id"], manifest["revision"], manifest["model_path"], manifest["runtime"]) != (MODEL_ID, REVISION, MODEL_PATH, RUNTIME):
        refuse("NATIVE_MANIFEST", "Pinned candidate identity or runtime mismatch.")

    if manifest["limits"] != CANDIDATE_LIMITS or manifest["controls"] != CONTROLS or manifest["synthetic_only"] is not True:
        refuse("NATIVE_MANIFEST", "Limits/controls/synthetic_only mismatch for candidate.")

    if manifest.get("max_generations") != 44:
        refuse("NATIVE_MANIFEST", "Candidate requires exactly 44 generations.")

    if manifest.get("initial_question") != b.INITIAL_QUESTION:
        refuse("NATIVE_MANIFEST", "Candidate requires standard initial deterministic question.")

    expected_case_ids = [c["id"] for c in b.read_cases()]
    if manifest.get("case_ids") != expected_case_ids:
        refuse("NATIVE_MANIFEST", "Candidate requires exact ordered 20 frozen case IDs.")

    for key, value in CONTROLS.items():
        if os.environ.get(key) != value:
            refuse("NATIVE_CONTROLS", f"Required host control {key}={value} not set.")

    # Validate files in manifest
    files = manifest.get("files")
    if not isinstance(files, dict):
        refuse("NATIVE_MANIFEST", "Manifest files must be a dictionary.")

    base = Path(MODEL_PATH)
    with os.fdopen(open_safe(base / "model.safetensors.index.json"), "rb") as f:
        index_raw = f.read(8 * 1024 * 1024 + 1)
    if len(index_raw) > 8 * 1024 * 1024:
        refuse("NATIVE_HASH", "Checkpoint index exceeds 8MB.")
    index = loads(index_raw)
    shards = sorted(set(index["weight_map"].values()))
    if len(shards) != 26 or any(Path(s).name != s or not s.endswith(".safetensors") for s in shards):
        refuse("NATIVE_HASH", "Expected exact 26 local checkpoint shards.")

    required_paths = {
        str(base / name) for name in shards + [
            "config.json", "generation_config.json", "tokenizer.json",
            "tokenizer_config.json", "chat_template.jinja", "model.safetensors.index.json"
        ]
    }
    root = Path(__file__).parent
    required_paths |= {str(path) for path in root.rglob("*.py")}

    if not required_paths.issubset(set(files)):
        refuse("NATIVE_HASH", "Manifest must bind every required code/tokenizer/config/shard file.")

    for name, expected in PINNED_SMALL_DIGESTS.items():
        if files.get(str(base / name)) != expected:
            refuse("NATIVE_HASH", f"Small digest mismatch for {name}.")

    stamps = {}
    for name, expected in files.items():
        if not isinstance(expected, str) or len(expected) != 64:
            refuse("NATIVE_HASH", f"Invalid digest format for {name}.")
        actual, stamp = hash_file(Path(name))
        if actual != expected:
            refuse("NATIVE_HASH", f"File hash mismatch for {name}.")
        stamps[name] = stamp
        if name.endswith(".safetensors"):
            meta = base / ".cache" / "huggingface" / "download" / (Path(name).name + ".metadata")
            with os.fdopen(open_safe(meta), "r") as f:
                lines = f.read(4096).splitlines()
            if len(lines) < 2 or lines[0] != REVISION or lines[1] != expected:
                refuse("NATIVE_HASH", f"Shard {name} metadata mismatch.")

    # Probe runtime environment
    probe_info = check_runtime_probe()
    verify_runtime_files()

    # GPU processes check
    if gpu_processes():
        refuse("NATIVE_RESIDENT", "Another GPU compute process is active.")

    return {
        "parent_pid": os.getpid(),
        "scope": CANDIDATE_SCOPE,
        "authorization_sha256": auth_sha,
        "manifest_sha256": manifest_sha,
        "verified_stamps": stamps,
        "manifest": manifest,
        "limits": CANDIDATE_LIMITS,
        "runtime_probe": probe_info,
    }
