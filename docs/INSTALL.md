# Installing plat-harness

plat-harness is an agent-agnostic control plane for multifamily underwriting
and ops. The core package is deliberately small: a clean install imports and
runs with **no GPU frameworks, no model weights, no provider SDKs and no
checkout of this repository**. Everything heavy is an explicit opt-in extra.

## Requirements

- Python **3.10+** (advertised floor `>=3.10`; tested on 3.10 and the 3.11
  runner; no macOS/Windows/x86 claims are made from these Linux tests).
- `pip` (any recent version; wheels are plain `py3-none-any`).

## Clean core install

From a built wheel (release artifact) or the repository:

```bash
python -m venv .venv
. .venv/bin/activate
pip install plat_harness-0.1.0-py3-none-any.whl   # or: pip install .
plat-harness --help
plat-underwrite --help
```

The core depends on `pyyaml` only. CSV ingestion is stdlib-only. A core
install must `import plat_harness`, show CLI help and run both console
entrypoints from any working directory without a repository checkout,
without pulling in torch/transformers/unsloth or any cloud SDK.

Missing optional capabilities never crash raw: they raise typed errors that
name what to install or which environment variable to set, e.g.
`XLSX_DEPENDENCY_MISSING` (install the `ingest` extra) and
`PDF_DEPENDENCY_MISSING` (install the `pdf` extra).

## Optional extras

```bash
pip install "plat-harness[ingest]"     # spreadsheet readers: openpyxl, xlrd
pip install "plat-harness[pdf]"        # PDF rent-roll ingest: pdfplumber, reportlab
pip install "plat-harness[dev]"        # test runner + spreadsheet readers + tomli<3.11
pip install "plat-harness[sovereign]"   # owner-reviewed native runtime pins (GPU)
```

- **`ingest`** — XLSX/XLS rent-roll readers (`openpyxl>=3.1,<4`,
  `xlrd>=2.0.2,<3`). Bounded to the two supported spreadsheet formats.
- **`pdf`** — PDF rent-roll extraction (`pdfplumber>=0.11,<1`) and the
  synthetic PDF fixture generator used by tests (`reportlab>=4,<6`).
- **`dev`** — pytest plus the `ingest` readers plus `tomli` on Python <3.11,
  for working on the repository itself.
- **`engine`** — deliberately empty. The underwriting engine is documented,
  not vendored; point `PLAT_HARNESS_ENGINE_ROOT` at your own checkout of the
  engine repository.
- **`sovereign`** — the owner-reviewed private native runtime, pinned exactly
  (torch/transformers/peft/trl/datasets/accelerate/unsloth). These are
  **optional GPU components**: a core install never needs them. The sovereign
  modules ship in the wheel by owner decision and stay inert in a core
  install — their GPU imports are lazy and execute only inside explicitly
  authorized workflows.

## Environment variables

| Variable | Purpose |
|---|---|
| `PLAT_HARNESS_GLOSSARY` | Path to `glossary.yaml`. A fresh install without the repo must set this; the error message says so. |
| `PLAT_HARNESS_ENGINE_ROOT` | Root of the underwriting engine checkout (the `engine` extra is documentation, not a vendored dependency). |
| `PLAT_HARNESS_OPS_ROOT` / `PLAT_HARNESS_DEAL_ROOT` / `PLAT_HARNESS_BOXSCORE_DB` | Roots of your own private ops/deal data and database (never shipped). |
| `PLAT_HARNESS_PRIVATE_ROOT` | Private data root for Slice B artifacts; required by the adapter's fail-closed path gate. |
| `PLAT_HARNESS_SOVEREIGN_HOST` / `PLAT_HARNESS_SOVEREIGN_UID` | Approved host/uid envelope for native inference authorization; unset means refuse. |
| `PLAT_HARNESS_NATIVE_LOCK_DIR` | Approved 0700 lock directory for the resident-model global lock; unset means refuse. |
| `PLAT_HARNESS_MODEL_PATH` / `PLAT_HARNESS_RUNTIME` | Local model checkpoint directory and reviewed tokenizer interpreter; unset means refuse. |
| `PLAT_HARNESS_SITE_PACKAGES` | Site-packages root of the pinned runtime identity files; unset means refuse. |

The fail-closed rule for all of these is identical: **no host path,
hostname or uid is compiled into the source; an unset or empty value is a
typed refusal, never a guessed default.**

## Building release artifacts

Wheel and sdist are built in **isolated, disposable environments only** —
never in a shared ML environment:

```bash
python -m build --wheel --outdir dist/ .
python -m build --sdist --outdir dist/ .
sha256sum dist/*.whl   # record the wheel hash as release evidence
```

If `build` is not installed in the current interpreter and the environment
must not be mutated, a pinned local wheel cache (build, pyproject_metadata,
pyproject_hooks, setuptools) can be unpacked into a throwaway directory and
used for the child invocation only.

## What a release artifact contains

The wheel contains only the `plat_harness` package and its metadata. Samples,
private evaluation data, engine sources, policies, glossary and environment
secrets are **not** part of the artifact. The recorded wheel SHA-256 and the
clean-install transcript (import + CLI help) are the release evidence.