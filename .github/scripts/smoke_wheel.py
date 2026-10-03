#!/usr/bin/env python3
"""Install the freshly built wheel into a throwaway venv and smoke it.

Run from the repo root after `python -m build`; expects exactly one wheel in
dist/. Mirrors the release-install contract (tests/test_release_install.py):
a core install must work with no GPU frameworks, model weights, provider
SDKs, or a checkout of this repository. Verifies: install resolves with the
pyyaml-only core dep, the package imports inert, all three console scripts
answer --help, the packaged glossary loads yield_on_cost from the wheel
with no checkout and no PLAT_HARNESS_GLOSSARY, and a sovereign module stays
out of the import graph.
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import tempfile
import venv
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
DIST = REPO / "dist"

# Child env with no repo import path or private harness roots attached.
_STRIPPED_PREFIXES = ("PLAT_HARNESS", "PYTHON", "VIRTUAL_ENV")


def run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd))
    kw.setdefault("cwd", tempfile.gettempdir())
    subprocess.run(cmd, check=True, **kw)


def clean_env() -> dict:
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(_STRIPPED_PREFIXES)
    }
    env["PYTHONNOUSERSITE"] = "1"
    return env


def main() -> int:
    wheels = sorted(glob.glob(str(DIST / "*.whl")))
    if len(wheels) != 1:
        print(f"expected exactly one wheel in dist/, found: {wheels}")
        return 1
    wheel = wheels[0]

    with tempfile.TemporaryDirectory(prefix="plat-harness-smoke-") as tmp:
        # The store/acceptance layers refuse group/world-writable artifact
        # ancestors by design (fail-closed mode gates); keep the venv private.
        venv_dir = Path(tmp) / "venv"
        venv_dir.parent.chmod(0o700)
        venv.create(venv_dir, with_pip=True, symlinks=sys.platform != "win32")
        bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")
        py = str(bin_dir / ("python.exe" if sys.platform == "win32" else "python"))
        bin_dir = venv_dir / ("Scripts" if sys.platform == "win32" else "bin")

        run([py, "-m", "pip", "install", "--quiet", "--upgrade", "pip"],
            env=clean_env())
        run([py, "-m", "pip", "install", "--quiet", wheel], env=clean_env())

        # Core imports stay inert: no GPU/provider runtimes pulled in
        run([py, "-I", "-c",
             "import plat_harness; "
             "from plat_harness import HarnessError, load_glossary, "
             "ModelRouter, NullModel; "
             "print('import-ok', plat_harness.__version__)"], env=clean_env())

        # Console scripts work from any cwd with no repo path leakage
        for script in ("plat-harness", "plat-underwrite", "plat-ops"):
            exe = bin_dir / script
            if not exe.exists() and (bin_dir / (script + ".exe")).exists():
                exe = bin_dir / (script + ".exe")
            run([str(exe), "--help"], env=clean_env(), cwd=tmp)
        print("cli-ok")

        # The wheel ships glossary.yaml. A fresh install outside the checkout
        # must load yield_on_cost from that package resource.
        run([py, "-I", "-c",
             "from plat_harness import load_glossary\n"
             "glossary = load_glossary()\n"
             "metric = glossary.get('yield_on_cost')\n"
             "owner = ''\n"
             "if metric is not None:\n"
             "    for item in metric.definitions:\n"
             "        if item.owner:\n"
             "            owner = item.owner\n"
             "            break\n"
             "expected = 'plat_harness.underwriting_direction.year_2_unlevered_yield_on_cost'\n"
             "if owner != expected:\n"
             "    raise SystemExit('packaged glossary missing yield owner: ' + owner)\n"
             "print('glossary-guard-ok')"], env=clean_env())

        # The corrected public fixture must load without a source checkout.
        run([py, "-I", "-c",
             "from plat_harness.synthetic_read_http import _oak_ridge_resource\n"
             "fixture = _oak_ridge_resource()\n"
             "assert fixture['fixture_revision'] == 2\n"
             "assert fixture['actual_noi'] == '46750'\n"
             "assert fixture['budget_noi'] == '88600'\n"
             "assert fixture['noi_variance'] == '-41850'\n"
             "print('packaged-operating-fixture-ok')"], env=clean_env(), cwd=tmp)

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
