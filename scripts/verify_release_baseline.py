"""Validate development source pins and fixture integrity (no release approval).

Run from any directory: python scripts/verify_release_baseline.py
Optionally verify clean, pinned checkouts under --workspace-root.
"""

from __future__ import annotations

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parents[1]
CORE = frozenset({
    "plat-harness", "plat-agent", "plat-costmodel",
    "plat-multifamily-underwriting", "plat-operations", "platworks",
})


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_baseline(root: Path = ROOT) -> dict:
    baseline = json.loads((root / "release/v0.1-baseline.json").read_text())
    require(baseline["contract_version"] == "plat.release-baseline/1", "unsupported baseline contract")
    require(baseline["status"] == "development_baseline", "baseline must remain distinct from release approval")
    require(baseline["release_approved"] is False, "baseline cannot approve a release")
    components = baseline["components"]
    require(set(components) == CORE, "missing or unexpected core component")
    for name, component in components.items():
        require(component["required"] is True, f"core component not required: {name}")
        require(component["repository"] == f"https://github.com/paintbrushv/{name}", f"repository mismatch: {name}")
        require(re.fullmatch(r"[0-9a-f]{40}", component["source_commit"]) is not None, f"invalid source pin: {name}")
    for name, descriptor in baseline["fixtures"].items():
        path = (root / descriptor["path"]).resolve()
        require(path.is_relative_to(root.resolve()), f"fixture outside checkout: {name}")
        raw = path.read_bytes()
        require(sha256(raw).hexdigest() == descriptor["sha256"], f"fixture hash mismatch: {name}")
        fixture = json.loads(raw)
        require(fixture["data_class"] == "synthetic", f"fixture must remain synthetic: {name}")
        require(fixture["fixture_revision"] == descriptor["revision"], f"fixture revision mismatch: {name}")
        producer = descriptor["producer"]
        require(fixture["source"]["repository"] == producer, f"fixture producer mismatch: {name}")
        require(fixture["source"]["commit"] == components[producer]["source_commit"], f"fixture source pin mismatch: {name}")
    return baseline


def verify_local_sources(baseline: dict, workspace: Path) -> None:
    for name, component in baseline["components"].items():
        actual = subprocess.check_output(
            ["git", "-C", str(workspace / name), "rev-parse", "HEAD"], text=True,
        ).strip()
        require(actual == component["source_commit"], f"baseline checkout mismatch: {name}")
        dirty = subprocess.check_output(
            ["git", "-C", str(workspace / name), "status", "--porcelain", "--untracked-files=all"],
            text=True,
        ).strip()
        require(not dirty, f"baseline checkout is dirty: {name} (including untracked files)")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace-root", type=Path)
    parser.add_argument("--github-output", type=Path)
    args = parser.parse_args()
    baseline = load_baseline()
    if args.workspace_root:
        verify_local_sources(baseline, args.workspace_root)
    operations_commit = baseline["components"]["plat-operations"]["source_commit"]
    if args.github_output:
        with args.github_output.open("a") as output:
            output.write(f"operations_commit={operations_commit}\n")
    print(json.dumps({
        "status": "baseline_verified", "release_approved": False,
        "operations_commit": operations_commit,
        "core_components": sorted(baseline["components"]),
        "local_source_pins_verified": args.workspace_root is not None,
    }, indent=2))


if __name__ == "__main__":
    main()
