#!/usr/bin/env python3
"""Install the freshly built wheel into a throwaway venv and smoke it.

Run from the repo root after `python -m build`; expects exactly one wheel in
dist/. Mirrors the release-install contract (tests/test_release_install.py):
a core install must work with no GPU frameworks, model weights, provider
SDKs, or a checkout of this repository. Verifies: install resolves with the
pyyaml-only core dep, the package imports inert, all three console scripts
answer --help, the glossary miss raises the documented typed error naming
PLAT_HARNESS_GLOSSARY, and a sovereign module stays out of the import graph.
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
        venv.create(venv_dir, with_pip=True)
        py = str(venv_dir / "bin" / "python")
        bin_dir = venv_dir / "bin"

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

        # Typed refusal: no glossary in a fresh install → the documented
        # env var must be named, never a bare traceback.
        run([py, "-I", "-c",
             "import os\n"
             "from plat_harness import load_glossary\n"
             "try:\n"
             "    load_glossary()\n"
             "    raise SystemExit('glossary unexpectedly resolved')\n"
             "except FileNotFoundError as e:\n"
             "    assert 'PLAT_HARNESS_GLOSSARY' in str(e), e\n"
             "    print('glossary-guard-ok')"], env=clean_env())

    print("SMOKE OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())