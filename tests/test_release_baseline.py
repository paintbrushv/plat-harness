"""Reject the stale producer/fixture combinations that caused demo drift."""

from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
import subprocess

import pytest


ROOT = Path(__file__).resolve().parents[1]
load_baseline = runpy.run_path(str(ROOT / "scripts/verify_release_baseline.py"))["load_baseline"]
producer_verifier = runpy.run_path(str(ROOT / "scripts/verify_oak_ridge_producer.py"))
check_result = producer_verifier["check_result"]
verify_source = producer_verifier["verify_source"]


@pytest.fixture
def baseline_copy(tmp_path):
    baseline = json.loads((ROOT / "release/v0.1-baseline.json").read_text())
    for fixture in baseline["fixtures"].values():
        destination = tmp_path / fixture["path"]
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((ROOT / fixture["path"]).read_bytes())
    (tmp_path / "release").mkdir()
    path = tmp_path / "release/v0.1-baseline.json"
    path.write_text(json.dumps(baseline))
    return tmp_path, path, baseline


def test_current_baseline_and_packaged_fixture_agree():
    baseline = load_baseline(ROOT)
    assert baseline["release_approved"] is False


def test_refuse_fixture_byte_drift(baseline_copy):
    root, _, baseline = baseline_copy
    path = root / baseline["fixtures"]["oak_ridge_2026_05"]["path"]
    path.write_bytes(path.read_bytes().replace(b'"46750"', b'"339150"'))
    with pytest.raises(ValueError, match="fixture hash mismatch"):
        load_baseline(root)


def test_refuse_stale_producer_pin_even_with_new_fixture_hash(baseline_copy):
    root, manifest_path, baseline = baseline_copy
    descriptor = baseline["fixtures"]["oak_ridge_2026_05"]
    path = root / descriptor["path"]
    fixture = json.loads(path.read_text())
    fixture["source"]["commit"] = "171eb9622f53fe9d6b9703191d5a6832064100ed"
    path.write_text(json.dumps(fixture))
    descriptor["sha256"] = sha256(path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="fixture source pin mismatch"):
        load_baseline(root)


def test_baseline_cannot_claim_release_approval(baseline_copy):
    root, path, baseline = baseline_copy
    baseline["release_approved"] = True
    path.write_text(json.dumps(baseline))
    with pytest.raises(ValueError, match="cannot approve a release"):
        load_baseline(root)


def test_producer_refuses_untracked_build_script_but_allows_build_output(tmp_path):
    """Cargo auto-discovers build.rs even when all tracked files are clean."""
    repo = tmp_path / "producer"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output([
            "git", "-C", str(repo), "-c", "user.name=Synthetic Test",
            "-c", "user.email=synthetic@example.invalid",
            "-c", "commit.gpgsign=false", "-c", f"core.hooksPath={tmp_path / 'no-hooks'}",
            *args,
        ], text=True).strip()

    git("init", "--quiet")
    lock = repo / "boxscore/Cargo.lock"
    lock.parent.mkdir()
    lock.write_text("# synthetic test lock\n")
    sample = repo / "boxscore/sample.csv"
    sample.write_text("synthetic input\n")
    (repo / ".gitignore").write_text("target/\n")
    git("add", ".gitignore", "boxscore/Cargo.lock", "boxscore/sample.csv")
    git("commit", "--quiet", "-m", "Synthetic producer fixture")
    fixture = {"source": {
        "commit": git("rev-parse", "HEAD"),
        "input_sha256": {"boxscore/sample.csv": sha256(sample.read_bytes()).hexdigest()},
        "cargo_lock_sha256": sha256(lock.read_bytes()).hexdigest(),
    }}
    target = repo / "boxscore/target"
    target.mkdir()
    (target / "build-output").write_text("synthetic compiler output\n")
    verify_source(repo, fixture)

    (repo / "boxscore/build.rs").write_text('fn main() { println!("cargo:warning=untracked"); }\n')
    with pytest.raises(RuntimeError, match="including untracked files"):
        verify_source(repo, fixture)


def test_producer_parity_rejects_legacy_inflated_noi():
    # Reproduce the original sign defect: arithmetic is internally consistent
    # but economically wrong, so checking variance alone would miss it.
    fixture_path = ROOT / "harness/src/plat_harness/fixtures/oak_ridge_2026_05.v2.json"
    fixture = json.loads(fixture_path.read_text())
    old_result = {
        "property": "Oak Ridge", "period": "2026-05",
        "noi_bridge": {
            "actual_revenue": 192950, "actual_expenses": -146200, "actual_noi": 339150,
            "budget_revenue": 220900, "budget_expenses": -132300, "budget_noi": 353200,
            "noi_variance": -14050,
        },
        "operating_metrics": {"occupied_units": 153, "vacant_units": 15, "down_units": 4},
        "gaps": [],
    }
    with pytest.raises(RuntimeError, match="financial mismatch: actual_expenses"):
        check_result(old_result, fixture)
    # Matching only the top-line NOI also cannot hide a wrong expense bridge.
    partial_fix = deepcopy(old_result)
    partial_fix["noi_bridge"].update(actual_noi=46750, budget_noi=88600, noi_variance=-41850)
    with pytest.raises(RuntimeError, match="financial mismatch: actual_expenses"):
        check_result(partial_fix, fixture)
