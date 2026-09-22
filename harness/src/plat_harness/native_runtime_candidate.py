"""Disabled exact runtime-identity diagnostic, NOT a GPU compatibility gate.

The installed wheel uses distribution version 2.11.0 and build version
2.11.0+cu130. Compare both fields independently, never strip a local suffix.
The active native_gate remains unchanged. This policy neither issues permits
nor attests all shared libraries, GB10 kernels, Qwen forwards or provenance of
an upstream download. The selected installed files are pinned for review.
"""
from pathlib import Path

from plat_harness.native_gate import hash_file
from plat_harness.native_qwen import refuse

EXACT_IDENTITY = {
    "python": "/home/mdai/venvs/qwen36-unsloth/bin/python",
    "python_version": [3, 13, 14],
    "machine": "aarch64",
    "distribution_version": "2.11.0",
    "torch_version": "2.11.0+cu130",
    "compiled_cuda": "13.0",
    "torch_git_version": "70d99e998b4955e0049d13a98d77ae1b14db1f45",
    "transformers_version": "5.5.0",
    "tokenizers_version": "0.22.2",
}
_ROOT = "/home/mdai/venvs/qwen36-unsloth/lib/python3.13/site-packages/"
EXACT_FILES = {
    _ROOT + "torch/version.py": {"sha256": "323d35171ef1184f1d7db3bbd1f3d3e227e0e826be8fd52200778346e17c873f", "bytes": 317},
    _ROOT + "torch/__init__.py": {"sha256": "0387d8b811b289287479c8bfdf4e1dac3a71b246f938d82da1331cf2dc8bf001", "bytes": 107845},
    _ROOT + "torch/_C.cpython-313-aarch64-linux-gnu.so": {"sha256": "8fcd72c89a7f8ceb3fb106829dca55295928a904eded268d953bf0fa26088e31", "bytes": 263057},
    _ROOT + "torch-2.11.0.dist-info/METADATA": {"sha256": "d65e0ab5a65ced0dce799a2f6f32bff57b3d3e9d050069f607ef71eb24dd9976", "bytes": 29846},
    _ROOT + "torch-2.11.0.dist-info/WHEEL": {"sha256": "100308e6fc03e14b816e3d7fd56299655cac945f17c10560dcc5b87ccf7efddd", "bytes": 114},
    _ROOT + "torch-2.11.0.dist-info/RECORD": {"sha256": "60ba042a9b75fff14bb52bdf97712e225565b7a2b64f29b1c312dc277786aae1", "bytes": 1279065},
}


def validate_identity(observation):
    """Validate an observation only. Actual byte readback is verify_files()."""
    if not isinstance(observation, dict):
        refuse("NATIVE_RUNTIME_CANDIDATE", "Expected a runtime observation.")
    for key, expected in EXACT_IDENTITY.items():
        actual = observation.get(key)
        if type(actual) is not type(expected) or actual != expected:
            refuse("NATIVE_RUNTIME_CANDIDATE", f"Exact runtime identity mismatch: {key}.")
    if (observation.get("cuda_initialized") is not False
            or observation.get("model_loaded") is not False
            or observation.get("forward_compatibility") != "NOT_TESTED"):
        refuse("NATIVE_RUNTIME_CANDIDATE", "CPU observation cannot assert model compatibility.")
    files = observation.get("files")
    if not isinstance(files, dict) or set(files) != set(EXACT_FILES):
        refuse("NATIVE_RUNTIME_CANDIDATE", "Exact identity file set required.")
    for path, expected in EXACT_FILES.items():
        item = files[path]
        if (not isinstance(item, dict) or set(item) != {"sha256", "bytes"}
                or type(item["bytes"]) is not int or item != expected):
            refuse("NATIVE_RUNTIME_CANDIDATE", "Pinned runtime identity file mismatch.")
    return {"status": "EXACT_OBSERVED_IDENTITY", "enabled": False,
            "approved": False, "native_launch_supported": False,
            "forward_compatibility": "NOT_TESTED",
            "payload_coverage": "SELECTED_TORCH_IDENTITY_FILES_NOT_ALL_RUNTIME_LIBRARIES"}


def verify_files(observation):
    """Rehash exact selected files with existing no-follow/race-safe reader."""
    result = validate_identity(observation)
    for name, expected in EXACT_FILES.items():
        digest, _ = hash_file(Path(name))
        if digest != expected["sha256"]:
            refuse("NATIVE_RUNTIME_CANDIDATE", "Runtime identity changed after observation.")
    return {**result, "files_rehashed": len(EXACT_FILES)}


def authorize_native(*args, **kwargs):
    refuse("NATIVE_RUNTIME_CANDIDATE_DISABLED",
           "Diagnostic candidate is disabled; active gate and authority unchanged.")
