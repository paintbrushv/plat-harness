"""Sanitized host-portable runtime identity pins for the disabled candidate gate.

EXACT_IDENTITY pins version facts only; the ``python`` key is configured per
host via PLAT_HARNESS_RUNTIME (the reviewed interpreter), and pinned runtime
files are addressed relative to the configured site-packages root
(PLAT_HARNESS_SITE_PACKAGES). No host path is compiled into the source.
"""
from pathlib import Path

from plat_harness.native_gate import hash_file
from plat_harness.native_qwen import refuse, runtime_python

EXACT_IDENTITY = {
    "python_version": [3, 13, 14],
    "machine": "aarch64",
    "distribution_version": "2.11.0",
    "torch_version": "2.11.0+cu130",
    "compiled_cuda": "13.0",
    "torch_git_version": "70d99e998b4955e0049d13a98d77ae1b14db1f45",
    "transformers_version": "5.5.0",
    "tokenizers_version": "0.22.2",
}

# Pinned selected torch identity files, relative to the configured
# site-packages root. Keys are package-relative so they stay host-portable.
EXACT_FILES = {
    "torch/version.py": {"sha256": "323d35171ef1184f1d7db3bbd1f3d3e227e0e826be8fd52200778346e17c873f", "bytes": 317},
    "torch/__init__.py": {"sha256": "0387d8b811b289287479c8bfdf4e1dac3a71b246f938d82da1331cf2dc8bf001", "bytes": 107845},
    "torch/_C.cpython-313-aarch64-linux-gnu.so": {"sha256": "8fcd72c89a7f8ceb3fb106829dca55295928a904eded268d953bf0fa26088e31", "bytes": 263057},
    "torch-2.11.0.dist-info/METADATA": {"sha256": "d65e0ab5a65ced0dce799a2f6f32bff57b3d3e9d050069f607ef71eb24dd9976", "bytes": 29846},
    "torch-2.11.0.dist-info/WHEEL": {"sha256": "100308e6fc03e14b816e3d7fd56299655cac945f17c10560dcc5b87ccf7efddd", "bytes": 114},
    "torch-2.11.0.dist-info/RECORD": {"sha256": "60ba042a9b75fff14bb52bdf97712e225565b7a2b64f29b1c312dc277786aae1", "bytes": 1279065},
}


def site_packages() -> Path:
    """Configured site-packages root for the pinned runtime files.

    PLAT_HARNESS_SITE_PACKAGES must name the reviewed interpreter's
    site-packages directory; refusing when unset or empty is the fail-closed
    default — no host path is compiled into the source.
    """
    import os
    raw = os.environ.get("PLAT_HARNESS_SITE_PACKAGES", "")
    if not raw:
        refuse("NATIVE_RUNTIME_CANDIDATE", "PLAT_HARNESS_SITE_PACKAGES must configure the pinned runtime site-packages root.")
    return Path(raw)


def expected_identity() -> dict:
    """Full expected identity including the configured reviewed interpreter."""
    return {**EXACT_IDENTITY, "python": runtime_python()}


def expected_files() -> dict:
    """Pinned file map resolved against the configured site-packages root."""
    root = site_packages()
    return {str(root / name): spec for name, spec in EXACT_FILES.items()}


def validate_identity(observation):
    """Validate an observation only. Actual byte readback is verify_files()."""
    if not isinstance(observation, dict):
        refuse("NATIVE_RUNTIME_CANDIDATE", "Expected a runtime observation.")
    expected = expected_identity()
    for key, value in expected.items():
        actual = observation.get(key)
        if type(actual) is not type(value) or actual != value:
            refuse("NATIVE_RUNTIME_CANDIDATE", f"Exact runtime identity mismatch: {key}.")
    if (observation.get("cuda_initialized") is not False
            or observation.get("model_loaded") is not False
            or observation.get("forward_compatibility") != "NOT_TESTED"):
        refuse("NATIVE_RUNTIME_CANDIDATE", "CPU observation cannot assert model compatibility.")
    files = observation.get("files")
    if not isinstance(files, dict):
        refuse("NATIVE_RUNTIME_CANDIDATE", "Exact identity file set required.")
    if set(files) != set(expected_files()):
        refuse("NATIVE_RUNTIME_CANDIDATE", "Exact identity file set required.")
    for path, spec in expected_files().items():
        item = files.get(path)
        if (not isinstance(item, dict) or set(item) != {"sha256", "bytes"}
                or type(item["bytes"]) is not int or item != spec):
            refuse("NATIVE_RUNTIME_CANDIDATE", "Pinned runtime identity file mismatch.")
    return {"status": "EXACT_OBSERVED_IDENTITY", "enabled": False,
            "approved": False, "native_launch_supported": False,
            "forward_compatibility": "NOT_TESTED",
            "payload_coverage": "SELECTED_TORCH_IDENTITY_FILES_NOT_ALL_RUNTIME_LIBRARIES"}


def verify_files(observation):
    """Rehash exact selected files with existing no-follow/race-safe reader."""
    result = validate_identity(observation)
    for path, spec in expected_files().items():
        digest, _ = hash_file(Path(path))
        if digest != spec["sha256"]:
            refuse("NATIVE_RUNTIME_CANDIDATE", "Runtime identity changed after observation.")
    return {**result, "files_rehashed": len(EXACT_FILES)}


def authorize_native(*args, **kwargs):
    refuse("NATIVE_RUNTIME_CANDIDATE_DISABLED",
           "Diagnostic candidate is disabled; active gate and authority unchanged.")