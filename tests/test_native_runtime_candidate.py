"""Synthetic regression coverage for disabled runtime identity policy.

The expected interpreter and pinned runtime file paths are host
configuration (PLAT_HARNESS_RUNTIME / PLAT_HARNESS_SITE_PACKAGES); tests pin
them to synthetic values, so no host path appears here and the suite runs on
any machine.
"""
from copy import deepcopy
import pytest
from plat_harness.errors import HarnessError
from plat_harness import native_runtime_candidate as candidate


@pytest.fixture(autouse=True)
def synthetic_host(monkeypatch):
    """Pin the reviewed interpreter and site-packages root to synthetic values."""
    monkeypatch.setenv("PLAT_HARNESS_RUNTIME", "/EXPLICIT_FAKE_RUNTIME")
    monkeypatch.setenv("PLAT_HARNESS_SITE_PACKAGES", "/EXPLICIT_FAKE_SITE_PACKAGES")


def observation():
    return {**deepcopy(candidate.expected_identity()),
            "files": deepcopy(candidate.expected_files()),
            "cuda_initialized": False, "model_loaded": False,
            "forward_compatibility": "NOT_TESTED"}


def test_exact_split_identity_is_diagnostic_not_authority():
    result = candidate.validate_identity(observation())
    assert result == {"status": "EXACT_OBSERVED_IDENTITY", "enabled": False,
                      "approved": False, "native_launch_supported": False,
                      "forward_compatibility": "NOT_TESTED",
                      "payload_coverage": "SELECTED_TORCH_IDENTITY_FILES_NOT_ALL_RUNTIME_LIBRARIES"}


def test_identity_pins_configured_interpreter_not_a_host_path():
    # The expected 'python' is the configured runtime, and pinned files live
    # under the configured site-packages root — never a compiled host path.
    identity = candidate.expected_identity()
    files = candidate.expected_files()
    assert identity["python"] == "/EXPLICIT_FAKE_RUNTIME"
    assert all(path.startswith("/EXPLICIT_FAKE_SITE_PACKAGES/") for path in files)
    assert "/home/" not in identity["python"]
    assert all("/home/" not in path for path in files)


@pytest.mark.parametrize("key,value", [
    ("distribution_version", "2.11.0+cu130"),
    ("distribution_version", "2.11.1"),
    ("torch_version", "2.11.0"),
    ("torch_version", "2.11.0+cu129"),
    ("compiled_cuda", "12.9"),
    ("torch_git_version", "0" * 40),
    ("python_version", [3, 13, 13]),
    ("machine", "x86_64"),
    ("python", "/usr/bin/python3"),
    ("transformers_version", "5.5.1"),
    ("tokenizers_version", "0.22.1"),
    ("cuda_initialized", True),
    ("model_loaded", True),
    ("forward_compatibility", "PASS"),
])
def test_genuine_mismatch_and_unearned_forward_claim_refused(key, value):
    value_input = observation()
    value_input[key] = value
    with pytest.raises(HarnessError) as exc:
        candidate.validate_identity(value_input)
    assert exc.value.code == "NATIVE_RUNTIME_CANDIDATE"


@pytest.mark.parametrize("mutation", ["null", "uppercase", "missing", "extra", "size", "bool_size"])
def test_file_identity_cannot_be_bypassed(mutation):
    value = observation()
    path = next(iter(value["files"]))
    if mutation == "missing":
        del value["files"][path]
    elif mutation == "extra":
        value["files"]["/tmp/untrusted"] = value["files"][path]
    elif mutation == "size":
        value["files"][path]["bytes"] += 1
    elif mutation == "bool_size":
        value["files"][path]["bytes"] = True
    else:
        value["files"][path]["sha256"] = None if mutation == "null" else "A" * 64
    with pytest.raises(HarnessError):
        candidate.validate_identity(value)


@pytest.mark.parametrize("env_var", ["PLAT_HARNESS_RUNTIME", "PLAT_HARNESS_SITE_PACKAGES"])
def test_unset_host_config_refuses_fail_closed(monkeypatch, env_var):
    monkeypatch.delenv(env_var, raising=False)
    with pytest.raises(HarnessError):
        observation()


def test_actual_readback_required_separately(monkeypatch):
    called = []
    def hasher(path):
        called.append(str(path))
        return candidate.expected_files()[str(path)]["sha256"], [0] * 5
    monkeypatch.setattr(candidate, "hash_file", hasher)
    result = candidate.verify_files(observation())
    assert sorted(called) == sorted(candidate.expected_files())
    assert result["files_rehashed"] == len(candidate.EXACT_FILES)
    monkeypatch.setattr(candidate, "hash_file", lambda path: ("0" * 64, []))
    with pytest.raises(HarnessError):
        candidate.verify_files(observation())


def test_no_runtime_launch_even_with_purported_approval():
    with pytest.raises(HarnessError) as exc:
        candidate.authorize_native({"approved": True})
    assert exc.value.code == "NATIVE_RUNTIME_CANDIDATE_DISABLED"


def test_active_limits_and_gate_identity_unchanged():
    from plat_harness.native_gate import LIMITS
    assert LIMITS["max_generations"] == 42
    assert LIMITS["prompt_tokens"] == 1536
    assert LIMITS["output_tokens"] == 512