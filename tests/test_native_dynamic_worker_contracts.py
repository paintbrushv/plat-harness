"""CPU-only audit and contract tests for candidate dynamic native worker."""
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import pytest

from plat_harness.errors import HarnessError
from plat_harness.native_dynamic_worker import audit_model, run_native


class Parameter:
    def __init__(self, device="cuda:0", dtype="BF16"):
        self.device = device
        self.dtype = dtype
        self.frozen = False

    def numel(self):
        return 1024

    def requires_grad_(self, value):
        self.frozen = not value


def fake_model(device="cuda:0", dtype="BF16", model_type="qwen3_5_moe"):
    p = Parameter(device, dtype)
    cls = type("Qwen3_5MoeForConditionalGeneration", (), {})
    model = cls()
    model.config = SimpleNamespace(model_type=model_type)
    model.named_parameters = lambda: [("model.layers.0.mlp.gate", p)]
    model.named_buffers = lambda: []
    return model, p


def test_audit_model_valid():
    model, p = fake_model()
    torch_ns = SimpleNamespace(bfloat16="BF16", float32="FP32")
    info = audit_model(model, {}, torch_ns)
    assert info["architecture"] == "Qwen3_5MoeForConditionalGeneration"
    assert info["parameter_counts"] == {"cuda:0/BF16": 1024}
    assert p.frozen


@pytest.mark.parametrize("device,dtype", [
    ("cpu", "BF16"),
    ("meta", "BF16"),
    ("disk", "BF16"),
    ("cuda:1", "BF16"),
    ("cuda:0", "FP32"),
    ("cuda:0", "INT4"),
])
def test_audit_model_rejects_unplanned_placement_or_precision(device, dtype):
    model, _ = fake_model(device, dtype)
    torch_ns = SimpleNamespace(bfloat16="BF16", float32="FP32")
    with pytest.raises(HarnessError) as exc:
        audit_model(model, {}, torch_ns)
    assert exc.value.code == "NATIVE_PLACEMENT"


def test_audit_model_rejects_quantization():
    model, _ = fake_model()
    model.config.quantization_config = {"load_in_4bit": True}
    torch_ns = SimpleNamespace(bfloat16="BF16", float32="FP32")
    with pytest.raises(HarnessError) as exc:
        audit_model(model, {}, torch_ns)
    assert exc.value.code == "NATIVE_PRECISION"


@pytest.mark.parametrize("field", ["missing_keys", "unexpected_keys", "mismatched_keys", "error_msgs"])
def test_audit_model_rejects_loading_errors(field):
    model, _ = fake_model()
    torch_ns = SimpleNamespace(bfloat16="BF16", float32="FP32")
    with pytest.raises(HarnessError) as exc:
        audit_model(model, {field: ["bad_key"]}, torch_ns)
    assert exc.value.code == "NATIVE_LOADING"


def test_worker_import_does_not_initialize_ml():
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(root / "harness" / "src"),
    }
    cmd = [
        sys.executable, "-B", "-c",
        "import sys\n"
        "import plat_harness.native_dynamic_worker\n"
        "assert 'torch' not in sys.modules, 'torch was imported!'\n"
        "print('CLEAN_IMPORT')\n"
    ]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=5)
    assert proc.returncode == 0
    assert "CLEAN_IMPORT" in proc.stdout


def test_worker_cli_refuses_without_permit_fd():
    root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "CUDA_VISIBLE_DEVICES": "",
        "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONPATH": str(root / "harness" / "src"),
    }
    cmd = [sys.executable, "-B", "-m", "plat_harness.native_dynamic_worker"]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=5)
    assert proc.returncode != 0


def test_worker_run_native_rejects_invalid_permit_pid(tmp_path):
    permit = {
        "parent_pid": os.getpid() + 99999,  # Mismatched parent
        "scope": "one_native_bf16_dynamic_inference_run",
        "authorization_sha256": "a" * 64,
        "manifest_sha256": "b" * 64,
        "verified_stamps": {},
    }
    permit_file = tmp_path / "permit.json"
    permit_file.write_text(json.dumps(permit))
    fd = os.open(str(permit_file), os.O_RDONLY)
    with pytest.raises(HarnessError) as exc:
        run_native(fd)
    assert exc.value.code == "NATIVE_AUTH"
