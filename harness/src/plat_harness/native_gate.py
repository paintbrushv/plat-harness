"""Host-owned approval/hash gates. No import of torch, model, or network code."""
from __future__ import annotations

import datetime as dt
import hashlib
import os
from pathlib import Path
import platform
import stat
import subprocess

from plat_harness.native_qwen import (MODEL_ID, MODEL_PATH, REVISION, RUNTIME,
                                      TEMPLATE_SHA256, dumps, loads, refuse, schemas)

PINNED_SMALL_DIGESTS = {"config.json": "93a4693fa9d8392fbfccd4b3c9873f4bfdcb14fdede978b123d07d19675efe99",
                        "tokenizer.json": "5f9e4d4901a92b997e463c1f46055088b6cca5ca61a6522d1b9f64c4bb81cb42",
                        "chat_template.jinja": TEMPLATE_SHA256}

CONTROLS = {"COMPS_MODE": "skip", "MAX_DEALS": "5", "ALLOW_LIFECYCLE": "0"}
LIMITS = {"load_s": 900, "generation_s": 180, "whole_s": 9000,
          "max_generations": 42, "prompt_tokens": 1536, "output_tokens": 512,
          "total_tokens": 2048, "min_available_gib": 24,
          "max_workload_gib": 90, "max_swap_growth_gib": 1}


def open_safe(path: Path, *, directory=False):
    if not path.is_absolute() or ".." in path.parts:
        refuse("NATIVE_PATH", "Require absolute nontraversing path.")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for i, part in enumerate(path.parts[1:]):
            flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
            if i < len(path.parts) - 2 or directory:
                flags |= os.O_DIRECTORY
            nxt = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = nxt
        mode = os.fstat(fd).st_mode
        if not (stat.S_ISDIR(mode) if directory else stat.S_ISREG(mode)):
            refuse("NATIVE_PATH", "Expected regular file or directory.")
        return fd
    except BaseException:
        os.close(fd)
        raise


def read_private(path: Path, expected_sha: str | None = None):
    fd = open_safe(path)
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if st.st_uid != os.getuid() or stat.S_IMODE(st.st_mode) != 0o600 or st.st_size > 1024 * 1024:
            refuse("NATIVE_AUTH", "Approval/manifest must be owner-owned 0600 bounded regular file.")
        raw = f.read()
    if expected_sha is not None and hashlib.sha256(raw).hexdigest() != expected_sha:
        refuse("NATIVE_HASH", "Approval/manifest bytes changed.")
    return loads(raw), hashlib.sha256(raw).hexdigest()


def hash_file(path: Path):
    fd = open_safe(path)
    with os.fdopen(fd, "rb") as f:
        before = os.fstat(f.fileno())
        h = hashlib.sha256()
        for chunk in iter(lambda: f.read(8 * 1024 * 1024), b""):
            h.update(chunk)
        after = os.fstat(f.fileno())
        stamp = lambda s: [s.st_dev, s.st_ino, s.st_size, s.st_mtime_ns, s.st_ctime_ns]
        if stamp(before) != stamp(after):
            refuse("NATIVE_HASH", "File changed during streaming hash.")
        return h.hexdigest(), stamp(after)


def gpu_processes():
    p = subprocess.run(["nvidia-smi", "--query-compute-apps=pid", "--format=csv,noheader,nounits"],
                       capture_output=True, text=True, timeout=10, check=True)
    return [int(line.strip()) for line in p.stdout.splitlines() if line.strip()]


def authorize(authorization: Path, authorization_sha256: str, manifest_path: Path,
              output_dir: Path):
    """Only a host-supplied hash-bound approval can cross the GPU gate.

    An approved JSON file is an operator handoff, not a cryptographic claim of
    human identity. The caller must obtain actual approval before creating it.
    Proposals and automatic agent decisions are explicitly rejected.
    """
    if (platform.node() != "spark-17d5" or platform.system() != "Linux"
            or platform.machine() != "aarch64" or os.getuid() != 1000):
        refuse("NATIVE_HOST", "Native inference is pinned to spark-17d5/mdai/aarch64.")
    if len(authorization_sha256) != 64 or any(c not in "0123456789abcdef" for c in authorization_sha256):
        refuse("NATIVE_AUTH", "Supply the literal reviewed approval SHA256.")
    auth, auth_sha = read_private(authorization, authorization_sha256)
    required = {"approved", "approval_kind", "scope", "manifest_sha256", "output_dir", "expires_at", "approval_evidence"}
    if not isinstance(auth, dict) or set(auth) != required or auth["approved"] is not True:
        refuse("NATIVE_AUTH", "Explicit inference authorization is absent.")
    if auth["approval_kind"] != "explicit_human_inference" or auth["scope"] != "one_native_bf16_synthetic_inference_run":
        refuse("NATIVE_AUTH", "Proposal/training/other approval does not authorize inference.")
    if auth["output_dir"] != str(output_dir) or not isinstance(auth["approval_evidence"], str) or not auth["approval_evidence"]:
        refuse("NATIVE_AUTH", "Approval must bind this output directory and owner approval evidence.")
    try:
        expiry = dt.datetime.fromisoformat(auth["expires_at"])
        now = dt.datetime.now(dt.timezone.utc)
        if expiry.tzinfo is None or expiry <= now or expiry - now > dt.timedelta(days=1):
            refuse("NATIVE_AUTH", "Approval expired or is not bounded to the next 24 hours.")
    except (ValueError, TypeError):
        refuse("NATIVE_AUTH", "Invalid approval expiry.")
    digest = auth["manifest_sha256"]
    if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
        refuse("NATIVE_AUTH", "Approval must bind a literal manifest SHA256.")
    manifest, manifest_sha = read_private(manifest_path, digest)
    required_manifest = {"model_id", "revision", "model_path", "runtime", "limits", "controls", "files", "tools", "system_prompt", "questions", "synthetic_only"}
    if not isinstance(manifest, dict) or set(manifest) != required_manifest:
        refuse("NATIVE_MANIFEST", "Unexpected manifest fields.")
    if (manifest["model_id"], manifest["revision"], manifest["model_path"], manifest["runtime"]) != (MODEL_ID, REVISION, MODEL_PATH, RUNTIME):
        refuse("NATIVE_MANIFEST", "Pinned identity/runtime mismatch.")
    if (manifest["limits"] != LIMITS or any(type(v) is not int for v in manifest["limits"].values())
            or manifest["controls"] != CONTROLS or manifest["synthetic_only"] is not True):
        refuse("NATIVE_MANIFEST", "Limits/controls/synthetic scope mismatch.")
    # This implementation is intentionally not an arbitrary tool host.
    from plat_harness.tool_loop import SYSTEM_PROMPT, tool_schema
    if manifest["tools"] != tool_schema(("example_property",)) or manifest["system_prompt"] != SYSTEM_PROMPT:
        refuse("NATIVE_MANIFEST", "Initial native trial permits only the existing synthetic read-only tool.")
    schemas(manifest["tools"])
    questions = manifest["questions"]
    if (not isinstance(questions, list) or not 1 <= len(questions) <= 21
            or any(not isinstance(q, str) or not 1 <= len(q) <= 4000 for q in questions)
            or len(set(questions)) != len(questions)):
        refuse("NATIVE_MANIFEST", "Freeze unique synthetic questions before approval.")
    from plat_harness.native_qwen import normalize
    for q in questions:
        normalize([{"role": "system", "content": manifest["system_prompt"]}, {"role": "user", "content": q}], manifest["tools"])
    for key, value in CONTROLS.items():
        if os.environ.get(key) != value:
            refuse("NATIVE_CONTROLS", "Required host controls not set exactly.")
    files = manifest["files"]
    if not isinstance(files, dict):
        refuse("NATIVE_MANIFEST", "File digests must be a mapping.")
    base = Path(MODEL_PATH)
    with os.fdopen(open_safe(base / "model.safetensors.index.json"), "rb") as f:
        index_raw = f.read(8 * 1024 * 1024 + 1)
    if len(index_raw) > 8 * 1024 * 1024:
        refuse("NATIVE_HASH", "Checkpoint index exceeds bound.")
    index = loads(index_raw)
    shards = sorted(set(index["weight_map"].values()))
    if len(shards) != 26 or any(Path(s).name != s or not s.endswith(".safetensors") for s in shards):
        refuse("NATIVE_HASH", "Expected the exact 26 local checkpoint shards.")
    required_paths = {str(base / name) for name in shards + ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja", "model.safetensors.index.json"]}
    root = Path(__file__).parent
    required_paths |= {str(path) for path in root.rglob("*.py")}
    if set(files) != required_paths:
        refuse("NATIVE_HASH", "Manifest must bind every required code/tokenizer/config/shard file exactly.")
    for name, expected in PINNED_SMALL_DIGESTS.items():
        if files[str(base / name)] != expected:
            refuse("NATIVE_HASH", "Config/tokenizer/template identity differs from pinned revision.")
    stamps = {}
    for name, expected in files.items():
        if not isinstance(expected, str) or len(expected) != 64:
            refuse("NATIVE_HASH", "Invalid literal digest.")
        actual, stamp = hash_file(Path(name))
        if actual != expected:
            refuse("NATIVE_HASH", "Code/checkpoint hash gate failed.")
        stamps[name] = stamp
        if name.endswith(".safetensors"):
            metadata = base / ".cache" / "huggingface" / "download" / (Path(name).name + ".metadata")
            with os.fdopen(open_safe(metadata), "r") as f:
                lines = f.read(4096).splitlines()
            if len(lines) < 2 or lines[0] != REVISION or lines[1] != expected:
                refuse("NATIVE_HASH", "Shard digest not backed by pinned cached LFS metadata.")
    # Installed runtime is inspected without importing/initializing CUDA.
    probe = subprocess.run([RUNTIME, "-B", "-c", "import importlib.metadata as m,sys; print(sys.version_info[:2]); print(m.version('transformers')); print(m.version('torch'))"],
                           env={**os.environ, "CUDA_VISIBLE_DEVICES": "", "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                           capture_output=True, text=True, timeout=30, check=True)
    if probe.stdout.splitlines() != ["(3, 13)", "5.5.0", "2.11.0+cu130"]:
        refuse("NATIVE_RUNTIME", "Installed runtime differs from the reviewed native runtime.")
    if gpu_processes():
        refuse("NATIVE_RESIDENT", "Another GPU compute process is resident; never stop it automatically.")
    return {"authorization_sha256": auth_sha, "manifest_sha256": manifest_sha,
            "manifest": manifest, "verified_stamps": stamps, "runtime_probe": probe.stdout,
            "parent_pid": os.getpid(), "scope": auth["scope"]}
