"""Release install contract: clean-install boundaries and honest packaging.

Task 7.1. A stranger must be able to build a wheel from this repository,
install it into a disposable venv, import the package and run CLI help with
**no** GPU frameworks, model weights, provider SDKs or a checkout of this
repository, and receive actionable typed errors when optional extras are
missing. Isolated disposable build/test environments only; the shared ML
environment is never touched.

Sovereign modules (native_*, adapters/slice_b*) ship in the wheel by owner
decision and stay inert in a core install: their GPU imports are lazy and
their private pinned paths are constants that only execute inside explicitly
authorized workflows. Core modules must resolve paths from env, never from
developer home directories.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SRC = REPO_ROOT / "harness" / "src"
PYPROJECT = REPO_ROOT / "pyproject.toml"
VERSION = "0.1.0"

# GPU/ML training stacks, model runtimes and cloud/provider SDKs that a core
# install must never require.
_FORBIDDEN_CORE_DEPENDENCIES = frozenset(
    {
        "torch",
        "transformers",
        "peft",
        "trl",
        "datasets",
        "accelerate",
        "unsloth",
        "vllm",
        "sentencepiece",
        "bitsandbytes",
        "openai",
        "anthropic",
        "boto3",
        "google-generativeai",
        "google-genai",
    }
)

# Modules that must not be imported as a side effect of `import plat_harness`
# or CLI help in a clean core install.
_FORBIDDEN_RUNTIME_MODULES = (
    "torch",
    "transformers",
    "peft",
    "trl",
    "datasets",
    "accelerate",
    "unsloth",
    "vllm",
    "openai",
    "anthropic",
    "boto3",
)

# Private developer/home paths. Sovereign modules keep pinned local constants
# by owner decision; core modules resolve data roots from env instead.
_PRIVATE_PATH_PATTERN = re.compile(
    r"/home/[A-Za-z0-9_.-]+|/Users/[A-Za-z0-9_.-]+|C:\\\\Users\\\\"
)
SOVEREIGN_MODULES = (
    "adapters/slice_b.py",
    "adapters/slice_b_worker.py",
    "baseline_eval.py",
    "native_baseline_amendment.py",
    "native_dynamic.py",
    "native_dynamic_gate.py",
    "native_dynamic_supervisor.py",
    "native_dynamic_worker.py",
    "native_gate.py",
    "native_qwen.py",
    "native_qwen_worker.py",
    "native_runtime_candidate.py",
    "native_supervisor.py",
    "native_trial.py",
)

# Private/non-public payload that must never be inside the public wheel.
_FORBIDDEN_WHEEL_ENTRIES = (
    "samples/",
    "docs/eval/",
    "engine/src/",
    "policies/",
    "docs/glossary.yaml",
    ".env",
    "msal_token_cache",
    "heldout_v1",
)

_EXPECTED_EXTRAS = {"ingest", "engine", "dev", "sovereign", "pdf"}
# Reviewer-approved sovereign runtime pins (the reviewed native runtime).
_REVIEWED_SOVEREIGN_PINS = {
    "torch": "2.11.0",
    "transformers": "5.5.0",
    "peft": "0.20.0",
    "trl": "0.24.0",
    "datasets": "4.3.0",
    "accelerate": "1.14.0",
    "unsloth": "2026.8.18",
}
_PDF_EXTRAS = {"pdfplumber", "reportlab"}


def _load_project() -> dict:
    return tomllib.loads(PYPROJECT.read_text(encoding="utf-8"))["project"]


def _dep_name(dep: str) -> str:
    return re.split(r"[<>=!;\[]", dep.strip(), maxsplit=1)[0].strip().lower()


def _venv_bin(home: Path) -> Path:
    return home / ("Scripts" if os.name == "nt" else "bin")


def _venv_python(home: Path) -> Path:
    return _venv_bin(home) / ("python.exe" if os.name == "nt" else "python")


def _clean_env(extra: dict | None = None) -> dict:
    """Child env with no repo import path or private harness roots attached."""
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith(("PLAT_HARNESS", "PYTHON", "VIRTUAL_ENV"))
    }
    env["PYTHONNOUSERSITE"] = "1"
    if extra:
        env.update(extra)
    return env


def _run(code: str, venv_home: Path, cwd: Path | None = None,
         env_extra: dict | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(_venv_python(venv_home)), "-I", "-c", code],
        capture_output=True,
        text=True,
        cwd=str(cwd or venv_home),
        env=_clean_env(env_extra),
        timeout=180,
    )


def _build_wheel(outdir: Path) -> Path:
    """Build the wheel with the current (runner) interpreter.

    The runner venv has no `build` package installed and must not be mutated.
    `build` (+ its deps) is unpacked into a DISPOSABLE directory under
    pytest's tmp and loaded via PYTHONPATH for the child invocation only.
    Falls back to `python -m build` when the current interpreter already has
    the real package (the repository's empty `build/` layout directory does
    not count).
    """
    outdir.mkdir(parents=True, exist_ok=True)
    cmd: list[str]
    env: dict | None
    real_build = None
    try:
        import build as real_build  # noqa: F401
    except ImportError:
        pass
    if real_build is not None and real_build.__file__ is not None:
        cmd = [sys.executable, "-m", "build", "--wheel", "--outdir",
               str(outdir), str(REPO_ROOT)]
        env = None
    else:
        # The runner venv has no `build` package and must not be mutated: use
        # the pinned local wheel cache, unpacked into a DISPOSABLE directory
        # under pytest's tmp, loaded via PYTHONPATH for this child only.
        cache = Path("/tmp/runner-wheels")
        # /tmp is wiped on reboot; the durable pin lives at an operator-provided
        # location (PLAT_HARNESS_RUNNER_WHEELS) and is restored hash-checked
        # when the ephemeral copy is gone. Without either source the build
        # cannot run offline: skip honestly rather than hit the network.
        durable = Path(os.environ.get("PLAT_HARNESS_RUNNER_WHEELS", ""))
        if not cache.is_dir() and not durable.is_dir():
            pytest.skip(
                "offline build wheels not available: set PLAT_HARNESS_RUNNER_WHEELS "
                "to a directory containing the pinned build/packaging wheels"
            )
        if not cache.is_dir() and durable.is_dir():
            restored = subprocess.run(
                ["cp", "-a", f"{durable}/.", str(cache)],
                capture_output=True, text=True, timeout=120,
            )
            assert restored.returncode == 0, (
                f"restoring pinned build wheels from {durable} failed: {restored.stderr}"
            )
        wheels = sorted(cache.glob("*.whl")) if cache.is_dir() else []
        required = ("build-", "pyproject_metadata-", "pyproject_hooks-")
        if not all(any(w.name.startswith(prefix) for w in wheels) for prefix in required):
            raise AssertionError(
                "pinned build wheels unavailable in /tmp/runner-wheels "
                "(build, pyproject_metadata, pyproject_hooks)"
            )
        libdir = outdir.parent / "_build_env_lib"
        libdir.mkdir(parents=True, exist_ok=True)
        for wheel in wheels:
            with zipfile.ZipFile(wheel) as archive:
                archive.extractall(libdir)
        # setuptools ships unpacked next to the wheels (uv-managed stdlib copy).
        setuptools_dir = next(
            (info.parent / "setuptools" for info in cache.glob("setuptools-*.dist-info")),
            None,
        )
        if setuptools_dir and setuptools_dir.is_dir():
            if not (libdir / "setuptools").exists():
                shutil.copytree(setuptools_dir, libdir / "setuptools")
            for info in cache.glob("setuptools-*.dist-info"):
                shutil.copytree(info, libdir / info.name, dirs_exist_ok=True)
        cmd = [sys.executable, "-c",
               "from build.__main__ import main as bm\n"
               f"bm(['--wheel', '--outdir', {str(outdir)!r}, {str(REPO_ROOT)!r}])\n"]
        stdlib_site = (
            Path(sys.base_prefix) / "lib" / f"python{sys.version_info[0]}.{sys.version_info[1]}"
            / "site-packages"
        )
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            part for part in (str(libdir), str(stdlib_site)) if Path(part).is_dir()
        )
    proc = subprocess.run(
        cmd, capture_output=True, text=True, timeout=900, cwd=str(REPO_ROOT), env=env,
    )
    assert proc.returncode == 0, f"wheel build failed:\n{proc.stdout}\n{proc.stderr}"
    (wheel,) = outdir.glob("*.whl")
    return wheel


def _xlsx_bytes() -> bytes:
    """Minimal structurally valid XLSX so the zip gate passes and the
    openpyxl import (and nothing before it) is what a missing extra tests."""
    import io

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="xml" ContentType="application/xml"/></Types>',
        )
        archive.writestr("xl/workbook.xml", "<workbook/>")
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Packaging metadata
# ---------------------------------------------------------------------------

def test_requires_python_advertises_a_tested_floor():
    spec = _load_project()["requires-python"].strip()
    match = re.fullmatch(r">=(3\.\d{1,2})", spec)
    assert match, f"requires-python must be an explicit >= floor, got {spec!r}"
    floor = tuple(int(part) for part in match.group(1).split("."))
    assert floor <= sys.version_info[:2], (
        "Advertised floor must not exceed the interpreter this repo is tested on"
    )


def test_core_dependencies_exclude_gpu_and_provider_sdks():
    deps = {_dep_name(dep) for dep in _load_project()["dependencies"]}
    assert deps, "core dependencies must be declared explicitly, not guessed"
    leaked = deps & _FORBIDDEN_CORE_DEPENDENCIES
    assert not leaked, f"GPU/provider frameworks leaked into core dependencies: {sorted(leaked)}"


def test_extras_are_explicit_bounded_and_separated():
    extras = _load_project()["optional-dependencies"]
    assert set(extras) == _EXPECTED_EXTRAS, (
        f"expected exactly {sorted(_EXPECTED_EXTRAS)}, got {sorted(extras)}"
    )
    # Ingest stays bounded: readers for the two supported spreadsheet formats.
    ingest = {_dep_name(dep) for dep in extras["ingest"]}
    assert {"openpyxl", "xlrd"} <= ingest <= {"openpyxl", "xlrd"}, (
        "ingest extra must stay bounded to the supported spreadsheet readers"
    )
    # PDF extra covers the documented PDF ingest path and its test generator.
    pdf = {_dep_name(dep) for dep in extras["pdf"]}
    assert pdf == _PDF_EXTRAS, f"pdf extra must be {sorted(_PDF_EXTRAS)}, got {sorted(pdf)}"
    # Dev is separated from runtime extras: it carries the test runner.
    assert any(dep.lower().startswith("pytest") for dep in extras["dev"])
    # Engine is documented, not vendored.
    assert extras["engine"] == []
    # Only the sovereign extra may pin GPU/training runtimes.
    for name, deps in extras.items():
        if name == "sovereign":
            continue
        leaked = {_dep_name(dep) for dep in deps} & _FORBIDDEN_CORE_DEPENDENCIES
        assert not leaked, f"{name} extra must not declare GPU/provider runtimes: {sorted(leaked)}"


def test_sovereign_extra_pins_the_reviewed_native_runtime_exactly():
    sovereign = _load_project()["optional-dependencies"]["sovereign"]
    pins: dict[str, str] = {}
    for dep in sovereign:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([0-9][A-Za-z0-9_.+!]*)", dep.strip())
        assert match, f"sovereign extra must pin exact versions, got {dep!r}"
        pins[_dep_name(match.group(1))] = match.group(2)
    assert pins == _REVIEWED_SOVEREIGN_PINS, (
        "sovereign pins must match the reviewed native runtime (torch/transformers/"
        "peft/trl/datasets/accelerate/unsloth), not invented versions"
    )


def test_console_scripts_stay_backward_compatible():
    scripts = _load_project()["scripts"]
    assert scripts["plat-harness"] == "plat_harness.cli:main"
    assert scripts["plat-underwrite"] == "plat_harness.cli_underwrite:main"


# ---------------------------------------------------------------------------
# Source-level boundaries
# ---------------------------------------------------------------------------

def _gpu_import_nodes(tree: ast.AST) -> list[tuple[int, str]]:
    found = []
    for node in ast.walk(tree):
        mods: list[str] = []
        if isinstance(node, ast.Import):
            mods = [alias.name.split(".")[0] for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            mods = [node.module.split(".")[0]]
        for mod in mods:
            if mod in _FORBIDDEN_RUNTIME_MODULES and isinstance(node, ast.stmt):
                found.append((node.lineno, mod))
    return found


def test_gpu_and_sdk_imports_are_lazy_never_module_scope():
    offenders: list[str] = []
    for path in sorted(HARNESS_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        guarded: set[int] = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.If, ast.Try)):
                guarded.update(range(node.lineno, (node.end_lineno or node.lineno) + 1))
        for lineno, mod in _gpu_import_nodes(tree):
            if lineno not in guarded:
                offenders.append(
                    f"{path.relative_to(HARNESS_SRC)}: line {lineno} module-scope import {mod}"
                )
    assert offenders == [], (
        "GPU/provider imports must stay lazy so a core install never pays for them: "
        + "; ".join(offenders)
    )


def test_core_tests_do_not_import_optional_model_runtimes():
    offenders = []
    for path in sorted((REPO_ROOT / "tests").glob("test_*.py")):
        if path.name == "test_release_install.py":
            continue  # this file names the modules to ban, it never imports them
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for lineno, mod in _gpu_import_nodes(tree):
            offenders.append(f"{path.name}:{lineno} imports {mod}")
    assert offenders == [], (
        "core tests must not import optional model runtimes; they would fail on "
        "a clean core install: " + "; ".join(offenders)
    )


def test_private_paths_are_confined_to_sovereign_modules():
    offenders: list[str] = []
    for path in sorted(HARNESS_SRC.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        rel = path.relative_to(HARNESS_SRC).as_posix()
        # rel is "plat_harness/<module>.py"; SOVEREIGN_MODULES lists package-
        # relative module paths. Compare module paths, not the package prefix.
        module_rel = rel.split("plat_harness/", 1)[1] if rel.startswith("plat_harness/") else rel
        if _PRIVATE_PATH_PATTERN.search(path.read_text(encoding="utf-8")):
            if module_rel not in SOVEREIGN_MODULES:
                offenders.append(module_rel)
    assert offenders == [], (
        "private developer paths are only permitted in owner-reviewed sovereign "
        "modules; core modules must resolve paths from env: " + "; ".join(offenders)
    )


def test_install_doc_exists_and_covers_clean_install_and_extras():
    doc = REPO_ROOT / "docs" / "INSTALL.md"
    assert doc.is_file(), "docs/INSTALL.md must exist"
    text = doc.read_text(encoding="utf-8")
    for phrase in ("pip install", "[ingest]", "[dev]", "[pdf]", "[sovereign]"):
        assert phrase in text, f"INSTALL.md must mention {phrase}"
    assert "GPU" in text or "torch" in text, (
        "INSTALL.md must explain that GPU/sovereign components are optional"
    )
    assert "wheel" in text.lower(), "INSTALL.md must cover wheel/sdist release artifacts"


# ---------------------------------------------------------------------------
# Wheel content
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def wheel_path(tmp_path_factory) -> Path:
    return _build_wheel(tmp_path_factory.mktemp("release-dist"))


def test_wheel_contains_only_public_package_payload(wheel_path):
    with zipfile.ZipFile(wheel_path) as archive:
        names = archive.namelist()
    assert any(n.endswith("plat_harness/cli.py") for n in names), "CLI module missing"
    assert any(n.endswith("plat_harness/cli_underwrite.py") for n in names), "CLI module missing"
    with zipfile.ZipFile(wheel_path) as archive:
        entry_points = archive.read(f"plat_harness-{VERSION}.dist-info/entry_points.txt").decode()
    assert "plat-harness" in entry_points and "plat-underwrite" in entry_points
    for name in names:
        low = name.replace("\\", "/")
        for forbidden in _FORBIDDEN_WHEEL_ENTRIES:
            assert forbidden not in low, f"wheel leaks private/non-public file: {name}"
    # Private developer paths only inside sovereign modules.
    with zipfile.ZipFile(wheel_path) as archive:
        for name in names:
            if not name.endswith(".py") or "plat_harness/" not in name:
                continue
            rel = name.split("plat_harness/", 1)[1]
            text = archive.read(name).decode("utf-8", "replace")
            if _PRIVATE_PATH_PATTERN.search(text):
                assert rel in SOVEREIGN_MODULES, (
                    f"{name} bakes a private developer path into the wheel"
                )


def test_wheel_hash_is_recordable_release_evidence(wheel_path):
    digest = hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    assert len(digest) == 64
    with zipfile.ZipFile(wheel_path) as archive:
        meta = archive.read(f"plat_harness-{VERSION}.dist-info/METADATA").decode()
    assert "Requires-Python:" in meta, "wheel metadata must carry the Python floor"


# ---------------------------------------------------------------------------
# Clean venv install: import, CLI, entrypoints, missing extras
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def clean_venv(tmp_path_factory, wheel_path) -> Path:
    """Disposable venv with a core install only. Never a shared ML environment."""
    home = tmp_path_factory.mktemp("release-venv") / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", str(home)],
        capture_output=True, text=True, timeout=600, check=True,
    )
    install = subprocess.run(
        [str(_venv_python(home)), "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check", str(wheel_path)],
        capture_output=True,
        text=True,
        timeout=900,
        env=_clean_env(),
    )
    assert install.returncode == 0, f"clean install failed:\n{install.stdout}\n{install.stderr}"
    # The runner env sets PYTHONPATH to the repo's harness/src (which carries
    # plat_harness.egg-info). If that leaked into pip above, pip would report
    # "already satisfied" and install NOTHING while returning rc 0 — the
    # clean-venv -I import then fails with ModuleNotFoundError. _clean_env()
    # strips PYTHON*, and the guard below proves the install actually landed.
    landed = subprocess.run(
        [str(_venv_python(home)), "-I", "-c", "import plat_harness"],
        capture_output=True, text=True, timeout=180, env=_clean_env(),
    )
    assert landed.returncode == 0, (
        "wheel install reported success but plat_harness is not importable in "
        f"the clean venv; a leaked PYTHONPATH probably made pip a no-op:\n"
        f"{landed.stderr}"
    )
    return home


def test_clean_install_imports_without_gpu_frameworks(clean_venv):
    code = (
        "import sys, json\n"
        "import plat_harness\n"
        f"banned = {list(_FORBIDDEN_RUNTIME_MODULES)!r}\n"
        "leaked = [m for m in banned if m in sys.modules]\n"
        "print(json.dumps({'version': plat_harness.__version__, 'leaked': leaked}))\n"
    )
    proc = _run(code, clean_venv)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["version"] == VERSION
    assert payload["leaked"] == []


def test_clean_install_cli_help_without_model_runtimes(clean_venv):
    code = (
        "import sys, json\n"
        "from plat_harness.cli import main\n"
        f"banned = {list(_FORBIDDEN_RUNTIME_MODULES)!r}\n"
        "try:\n"
        "    main(['--help'])\n"
        "except SystemExit as exc:\n"
        "    print('HELP_RC', exc.code)\n"
        "    print(json.dumps([m for m in banned if m in sys.modules]))\n"
    )
    proc = _run(code, clean_venv)
    assert proc.returncode == 0, proc.stderr
    assert "plat-harness" in proc.stdout
    assert json.loads(proc.stdout.splitlines()[-1]) == []


def test_clean_install_glossary_failure_names_the_env_var(wheel_path):
    """Fresh installs have no glossary.yaml; the error must say what to set.

    The clean venv is placed OUTSIDE any plat-harness checkout: the installed
    package's default_glossary_path() walks ancestors looking for
    docs/glossary.yaml, and a venv created inside a checkout (e.g. pytest's
    basetemp) would legitimately find the repo's own glossary instead of
    failing. The no-glossary contract is only meaningful when no ancestor
    carries one.
    """
    import tempfile
    import venv as _venv
    external = Path(tempfile.mkdtemp(prefix="plat-harness-glossary-probe-"))
    try:
        _venv.create(str(external / "venv"), with_pip=True)
        subprocess.run(
            [str(external / "venv" / "bin" / "python"), "-m", "pip", "install",
             "--quiet", "--disable-pip-version-check", str(wheel_path)],
            capture_output=True, text=True, timeout=600, env=_clean_env(),
        )
        code = (
            "import json\n"
            "from plat_harness import load_glossary\n"
            "try:\n"
            "    load_glossary()\n"
            "    print(json.dumps({'ok': True}))\n"
            "except FileNotFoundError as exc:\n"
            "    print(json.dumps({'ok': False, 'msg': str(exc)}))\n"
        )
        proc = _run(code, external / "venv")
        payload = json.loads(proc.stdout)
        assert payload["ok"] is False, (
            "glossary must not resolve when no ancestor carries docs/glossary.yaml"
        )
        assert "PLAT_HARNESS_GLOSSARY" in payload["msg"], (
            "missing glossary error must point at PLAT_HARNESS_GLOSSARY"
        )
    finally:
        shutil.rmtree(external, ignore_errors=True)


def test_missing_spreadsheet_extra_returns_actionable_typed_error(clean_venv):
    code = (
        "import io, json, sys, zipfile\n"
        "assert 'openpyxl' not in sys.modules\n"
        f"payload = {_xlsx_bytes()!r}\n"
        "from plat_harness.ingest import RentRollNormalizationError, normalize_rent_roll\n"
        "try:\n"
        "    normalize_rent_roll(io.BytesIO(payload), 'yardi')\n"
        "    print(json.dumps({'code': None}))\n"
        "except RentRollNormalizationError as exc:\n"
        "    print(json.dumps({'code': exc.code}))\n"
    )
    proc = _run(code, clean_venv)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["code"] == "XLSX_DEPENDENCY_MISSING", (
        f"missing openpyxl must give the typed XLSX_DEPENDENCY_MISSING error, got {payload}"
    )


def test_missing_pdf_extra_is_refused_with_typed_error(clean_venv):
    """A PDF rent roll without pdfplumber must fail typed, not as raw crash."""
    code = (
        "import json\n"
        "from plat_harness.ingest.pdf_rent_roll import normalize_pdf_rent_roll\n"
        "from plat_harness.ingest.pms_normalizer import RentRollNormalizationError\n"
        "try:\n"
        "    normalize_pdf_rent_roll(b'%PDF-1.4 SYNTHETIC PRIVATE CANARY')\n"
        "    print(json.dumps({'code': None}))\n"
        "except RentRollNormalizationError as exc:\n"
        "    print(json.dumps({'code': exc.code}))\n"
    )
    proc = _run(code, clean_venv)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["code"] == "PDF_DEPENDENCY_MISSING"


def test_rent_roll_api_refuses_pdf_without_pdf_extra(clean_venv):
    """The generic rent-roll entry refuses PDFs typed; never parses them."""
    code = (
        "import io, json\n"
        "from plat_harness.ingest import RentRollNormalizationError, normalize_rent_roll\n"
        "try:\n"
        "    normalize_rent_roll(io.BytesIO(b'%PDF-1.4 SYNTHETIC PRIVATE CANARY'), 'yardi')\n"
        "    print(json.dumps({'code': None}))\n"
        "except RentRollNormalizationError as exc:\n"
        "    print(json.dumps({'code': exc.code}))\n"
    )
    proc = _run(code, clean_venv)
    assert proc.returncode == 0, proc.stderr
    assert json.loads(proc.stdout)["code"] == "UNSUPPORTED_INPUT_FORMAT"


def test_cross_cwd_entrypoints_work_without_repo_path_leakage(clean_venv, tmp_path):
    """Both installed console scripts must run from any cwd with no PYTHONPATH."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    outputs = {}
    for script_name, args, want_rc in (
        ("plat-harness", ["underwrite", "--deal", "example_garden_style"], 2),
        ("plat-underwrite", ["--om", "om-does-not-exist.pdf", "--rr", "rr-does-not-exist.csv",
                             "--out", str(tmp_path / "run"), "--json"], 3),
    ):
        script = _venv_bin(clean_venv) / script_name
        assert script.exists(), f"console entrypoint {script_name} not installed"
        proc = subprocess.run(
            [str(script), *args],
            capture_output=True,
            text=True,
            cwd=str(elsewhere),
            env=_clean_env(),
            timeout=180,
        )
        assert proc.returncode == want_rc, (
            f"{script_name} exit {proc.returncode}, wanted {want_rc}: {proc.stdout}{proc.stderr}"
        )
        outputs[script_name] = proc
    payload = json.loads(outputs["plat-harness"].stderr)
    assert payload["error"] == "MISSING_MILLAGE"
    payload = json.loads(outputs["plat-underwrite"].stderr)
    assert payload["error"] == "NOT_FOUND", "missing pinned sources must fail typed"
    for proc in outputs.values():
        assert str(REPO_ROOT) not in proc.stdout + proc.stderr, (
            "editable-install/repo path leaked into clean-install output"
        )


def test_core_tests_pass_in_clean_install_venv(clean_venv, tmp_path):
    """The core suite must pass on a clean core install (import-light).

    pytest goes into the DISPOSABLE venv only; the shared ML environment is
    never touched. Suites that need the sovereign runtime or private basetemp
    roots stay with the sanctioned runner; PDF-generation suites need the
    pdf extra's reportlab, so the pdf extra is also installed here — exactly
    what a dev install would provide.
    """
    extras = _load_project()["optional-dependencies"]
    install_more = subprocess.run(
        [str(_venv_python(clean_venv)), "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check", "pytest", *extras["pdf"], *extras["ingest"]],
        capture_output=True,
        text=True,
        timeout=1200,
        env=_clean_env(),
    )
    assert install_more.returncode == 0, (
        f"pytest/extras install failed:\n{install_more.stdout}\n{install_more.stderr}"
    )
    sandbox = tmp_path / "core-tests"
    shutil.copytree(
        REPO_ROOT / "tests",
        sandbox / "tests",
        ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"),
    )
    # Core tests are repo-contract tests as well as package tests: they read
    # pyproject.toml, docs/, policies/ and samples/ from the repo root, and the
    # subprocess/AST suites bootstrap from harness/src explicitly — exactly
    # what a dev checkout provides. Private payload (runs/deals, engine/
    # sources) stays out.
    for item in ("pyproject.toml", "docs", "policies", "samples", "harness"):
        src = REPO_ROOT / item
        if (src).is_dir():
            shutil.copytree(src, sandbox / item,
                            ignore=shutil.ignore_patterns("__pycache__", ".pytest_cache"))
        elif src.is_file():
            shutil.copy2(src, sandbox / item)
    # Drop suites that pin the sovereign runtime or private roots.
    for pattern in (
        "test_native*.py",
        "test_slice_b*.py",
        "test_release_install.py",
        # baseline_eval binds the frozen docs/eval fixture set and the
        # sovereign native_qwen/native_supervisor runtime (test_baseline_eval.py).
        "test_baseline_eval.py",
    ):
        for path in sorted((sandbox / "tests").glob(pattern)):
            path.unlink()
    # The clean install has no repo checkout; the glossary env var is the
    # documented resolution path and the sandbox carries docs/glossary.yaml.
    sandbox_env = _clean_env({
        "PLAT_HARNESS_GLOSSARY": str(sandbox / "docs" / "glossary.yaml"),
    })
    proc = subprocess.run(
        [str(_venv_python(clean_venv)), "-m", "pytest", "-q", "tests",
         # Private basetemp, like the sanctioned runner: the intake store's
         # root walk refuses any ancestor with group/other write bits, and a
         # default /tmp/pytest-of-* basetemp sits under world-writable /tmp.
         "--basetemp", str(sandbox.parent / "inner-basetemp")],
        capture_output=True,
        text=True,
        cwd=str(sandbox),
        env=sandbox_env,
        timeout=1800,
    )
    assert proc.returncode == 0, (
        f"core tests failed in the clean-install venv:\n"
        f"{proc.stdout[-4000:]}\n{proc.stderr[-2000:]}"
    )


@pytest.mark.skipif(shutil.which("uv") is None, reason="uv not available for floor test")
def test_advertised_minimum_python_installs_and_imports(tmp_path):
    """Test the advertised floor honestly, or revise support in pyproject.

    Fetches the floor CPython via uv (isolated download; never the shared ML
    environment) and proves the wheel installs and both CLIs import there.
    """
    match = re.fullmatch(r">=(3\.\d{1,2})", _load_project()["requires-python"].strip())
    assert match, "requires-python must be an explicit >= floor"
    minor = int(match.group(1).split(".")[1])
    fetch = subprocess.run(
        ["uv", "python", "install", f"3.{minor}"],
        capture_output=True, text=True, timeout=900,
    )
    assert fetch.returncode == 0, fetch.stderr
    find = subprocess.run(
        ["uv", "python", "find", f"3.{minor}"],
        capture_output=True, text=True, timeout=120,
    )
    assert find.returncode == 0, find.stderr
    interpreter = find.stdout.strip().splitlines()[0]
    assert interpreter, f"uv did not report a 3.{minor} interpreter"
    home = tmp_path / f"venv3{minor}"
    subprocess.run(
        [interpreter, "-m", "venv", str(home)],
        capture_output=True, text=True, timeout=600, check=True,
    )
    wheel = _build_wheel(tmp_path / f"dist3{minor}")
    install = subprocess.run(
        [str(_venv_python(home)), "-m", "pip", "install", "--quiet",
         "--disable-pip-version-check", str(wheel)],
        capture_output=True, text=True, timeout=900,
        env=_clean_env(),
    )
    assert install.returncode == 0, f"3.{minor} install failed:\n{install.stdout}\n{install.stderr}"
    code = (
        "import sys, json\n"
        "import plat_harness\n"
        "from plat_harness.cli import main\n"
        "from plat_harness.cli_underwrite import main as uw_main\n"
        "print(json.dumps({'py': list(sys.version_info[:2]), 'v': plat_harness.__version__}))\n"
    )
    proc = _run(code, home)
    assert proc.returncode == 0, proc.stderr
    payload = json.loads(proc.stdout)
    assert payload["py"] == [3, minor]
    assert payload["v"] == VERSION