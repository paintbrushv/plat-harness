"""Focused tests for candidate dynamic native approval and runtime identity gate."""
import copy
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from plat_harness import baseline_eval as b
from plat_harness.errors import HarnessError
from plat_harness import native_dynamic_gate as gate
from plat_harness.native_dynamic_gate import CANDIDATE_LIMITS, CANDIDATE_SCOPE, CONTROLS
from plat_harness.native_qwen import MODEL_ID, REVISION
from plat_harness.native_runtime_candidate import EXACT_FILES, EXACT_IDENTITY


@pytest.fixture
def candidate_approval_fixture(tmp_path, monkeypatch):
    base = tmp_path / "FAKE_MODEL"
    base.mkdir()
    code = tmp_path / "FAKE_CODE"
    code.mkdir()
    (code / "native_dynamic_gate.py").write_text("# explicit offline fixture\n")

    metadata = base / ".cache" / "huggingface" / "download"
    metadata.mkdir(parents=True)
    shards = [f"fake-{i:02d}.safetensors" for i in range(26)]
    files = {}
    for name in shards:
        raw = ("NOT_WEIGHTS:" + name).encode()
        (base / name).write_bytes(raw)
        digest = hashlib.sha256(raw).hexdigest()
        files[str(base / name)] = digest
        (metadata / (name + ".metadata")).write_text(REVISION + "\n" + digest + "\n0\n")

    for name in ["config.json", "generation_config.json", "tokenizer.json", "tokenizer_config.json", "chat_template.jinja"]:
        (base / name).write_text("{}")
    (base / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {f"fake_tensor_{i}": s for i, s in enumerate(shards)}}))

    for path in [*base.glob("*.json"), base / "chat_template.jinja", code / "native_dynamic_gate.py"]:
        files[str(path)] = hashlib.sha256(path.read_bytes()).hexdigest()

    monkeypatch.setattr(gate, "MODEL_PATH", str(base))
    monkeypatch.setattr(gate, "RUNTIME", "/EXPLICIT_FAKE_RUNTIME")
    monkeypatch.setattr(gate, "__file__", str(code / "native_dynamic_gate.py"))
    monkeypatch.setattr(gate, "PINNED_SMALL_DIGESTS", {name: files[str(base / name)] for name in gate.PINNED_SMALL_DIGESTS})
    monkeypatch.setattr(gate.platform, "node", lambda: "spark-17d5")
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(gate.platform, "machine", lambda: "aarch64")
    monkeypatch.setattr(gate.os, "getuid", lambda: 1000)
    monkeypatch.setattr(gate, "gpu_processes", lambda: [])
    monkeypatch.setattr(gate, "check_runtime_probe", lambda *a, **k: copy.deepcopy(EXACT_IDENTITY))
    monkeypatch.setattr(gate, "verify_runtime_files", lambda *a, **k: None)

    for key, value in CONTROLS.items():
        monkeypatch.setenv(key, value)

    manifest = {
        "model_id": MODEL_ID,
        "revision": REVISION,
        "model_path": str(base),
        "runtime": "/EXPLICIT_FAKE_RUNTIME",
        "limits": copy.deepcopy(CANDIDATE_LIMITS),
        "controls": copy.deepcopy(CONTROLS),
        "files": files,
        "synthetic_only": True,
        "initial_question": b.INITIAL_QUESTION,
        "case_ids": [c["id"] for c in b.read_cases()],
        "max_generations": 44,
    }

    output = tmp_path / "FAKE_CANDIDATE_OUTPUT"
    auth = {
        "approved": True,
        "approval_kind": "explicit_human_inference",
        "scope": CANDIDATE_SCOPE,
        "manifest_sha256": "not yet bound",
        "output_dir": str(output),
        "expires_at": (dt.datetime.now(dt.timezone.utc) + dt.timedelta(hours=1)).isoformat(),
        "approval_evidence": "EXPLICIT_OFFLINE_TEST_FIXTURE_NOT_REAL_APPROVAL",
    }

    def write(mutate_auth=None, mutate_manifest=None):
        m, a = copy.deepcopy(manifest), copy.deepcopy(auth)
        if mutate_manifest:
            mutate_manifest(m)
        mpath = tmp_path / "FAKE_MANIFEST.json"
        mpath.write_text(json.dumps(m))
        mpath.chmod(0o600)
        a["manifest_sha256"] = hashlib.sha256(mpath.read_bytes()).hexdigest()
        if mutate_auth:
            mutate_auth(a)
        apath = tmp_path / "FAKE_AUTH.json"
        apath.write_text(json.dumps(a))
        apath.chmod(0o600)
        return apath, hashlib.sha256(apath.read_bytes()).hexdigest(), mpath, output

    return write, base


def test_candidate_gate_valid_envelope_issues_permit(candidate_approval_fixture):
    make, base = candidate_approval_fixture
    permit = gate.authorize_candidate(*make())
    assert permit["scope"] == CANDIDATE_SCOPE
    assert permit["parent_pid"] == os.getpid()
    assert permit["limits"]["max_generations"] == 44
    assert len([p for p in permit["verified_stamps"] if p.endswith(".safetensors")]) == 26


@pytest.mark.parametrize("mutate", [
    lambda a: a.update(approved=False),
    lambda a: a.update(approved=1),
    lambda a: a.update(approval_kind="PROPOSAL_ONLY"),
    lambda a: a.update(scope="wrong_scope"),
    lambda a: a.update(scope="one_native_bf16_synthetic_inference_run"),  # old narrow scope rejected
    lambda a: a.update(manifest_sha256=None),
    lambda a: a.update(manifest_sha256="0" * 64),
    lambda a: a.update(output_dir="/unauthorized/path"),
    lambda a: a.update(expires_at="2000-01-01T00:00:00+00:00"),
    lambda a: a.update(expires_at=(dt.datetime.now(dt.timezone.utc) + dt.timedelta(days=2)).isoformat()),
    lambda a: a.update(approval_evidence=""),
    lambda a: a.update(extra_key="not_allowed"),
])
def test_candidate_gate_rejects_invalid_authorization(candidate_approval_fixture, mutate):
    make, _ = candidate_approval_fixture
    with pytest.raises(HarnessError):
        gate.authorize_candidate(*make(mutate_auth=mutate))


@pytest.mark.parametrize("mutate", [
    lambda m: m.update(model_id="Qwen/other"),
    lambda m: m.update(revision="0" * 40),
    lambda m: m.update(max_generations=42),  # must be 44
    lambda m: m.update(synthetic_only=False),
    lambda m: m.update(initial_question="different question"),
    lambda m: m.update(case_ids=m["case_ids"][:-1]),  # missing case
    lambda m: m["limits"].update(prompt_tokens=2048),
    lambda m: m["limits"].update(max_generations=42),
    lambda m: m["controls"].update(ALLOW_LIFECYCLE="1"),
    lambda m: m["files"].pop(next(iter(m["files"]))),  # missing file binding
])
def test_candidate_gate_rejects_manifest_drift(candidate_approval_fixture, mutate):
    make, _ = candidate_approval_fixture
    with pytest.raises(HarnessError):
        gate.authorize_candidate(*make(mutate_manifest=mutate))


def test_candidate_gate_gpu_coresident_rejected(candidate_approval_fixture, monkeypatch):
    make, _ = candidate_approval_fixture
    monkeypatch.setattr(gate, "gpu_processes", lambda: [999999])
    with pytest.raises(HarnessError) as exc:
        gate.authorize_candidate(*make())
    assert exc.value.code == "NATIVE_RESIDENT"


def test_candidate_gate_runtime_drift_rejected(candidate_approval_fixture, monkeypatch):
    make, _ = candidate_approval_fixture
    drifted = copy.deepcopy(EXACT_IDENTITY)
    drifted["torch_version"] = "2.11.0"  # missing +cu130 build suffix
    monkeypatch.setattr(gate, "check_runtime_probe", lambda *a, **k: gate.refuse("NATIVE_RUNTIME", "Torch build version mismatch"))
    with pytest.raises(HarnessError) as exc:
        gate.authorize_candidate(*make())
    assert exc.value.code == "NATIVE_RUNTIME"


def test_candidate_gate_prohibits_production_launch_without_human_approval(tmp_path):
    # Production entrypoint must refuse when called with unapproved file
    auth_file = tmp_path / "unapproved_auth.json"
    auth_file.write_text(json.dumps({"approved": False, "approval_kind": "unapproved"}))
    auth_file.chmod(0o600)
    digest = hashlib.sha256(auth_file.read_bytes()).hexdigest()
    with pytest.raises(HarnessError) as exc:
        gate.authorize_candidate(
            auth_file, digest,
            tmp_path / "manifest.json", tmp_path / "output"
        )
    assert exc.value.code == "NATIVE_AUTH"
