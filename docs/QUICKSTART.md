# plat-harness QUICKSTART — the no-model walkthrough

Task 7.4 usability walkthrough. This guide takes a brand-new analyst from a
clean install to a first meaningful result in minutes, using **only** the
synthetic walkthrough fixtures in `samples/walkthrough/`. Everything here
runs offline, model-free, read-only on inputs, and entirely on your machine.

**Honesty first:** nothing in this walkthrough is financial certification
— it is **not financial certification**, not vendor validation and not
engine permission. `certified` is always `false` in every record. A
completed stage is progress, not approval. Synthetic fixtures are synthetic;
a parse is not execution authority.

## What you need

- Python 3.10+ on Linux (tested on 3.10/3.11, aarch64; no other claims).
- A built wheel or this repository checkout (see [INSTALL.md](INSTALL.md)).

```bash
python -m venv .venv && . .venv/bin/activate
pip install plat_harness-0.1.0-py3-none-any.whl   # or: pip install .
plat-underwrite --help
plat-ops --help
```

A core install imports and runs with no GPU frameworks, no model weights,
no provider SDKs and no network access. Missing optional capabilities raise
typed errors that name the extra to install (`ingest`, `pdf`).

## The 3-minute walkthrough

All commands run from the repository root, against the synthetic fixtures.
Run them from a scratch directory if you prefer — nothing here writes to
the repo.

### 1. Projection first: see blockers before any write

```bash
plat-underwrite \
  --om samples/walkthrough/om.pdf \
  --rr samples/walkthrough/rent_roll.pdf \
  --subject synthetic_walkthrough \
  --dry-run
```

`--dry-run` computes the same content identity and typed blockers as a real
run with **zero writes and zero side effects**. With only the two required
sources pinned you will see exit code `2` (needs review or data) and blockers
for `t12`, `debt`, `millage` and `horizon`. Missing inputs are blockers,
never invented values; a missing amount stays `null`, never zero.

### 2. One supported successful synthetic underwriting path

```bash
plat-underwrite \
  --om samples/walkthrough/om.pdf \
  --rr samples/walkthrough/rent_roll.pdf \
  --t12 samples/walkthrough/t12.xlsx \
  --debt samples/walkthrough/debt_schedule.csv \
  --subject synthetic_walkthrough \
  --out ./walkthrough-run \
  --millage 25.31 \
  --horizon 5
```

Exit code `0`, stage `canonical_ready`, outcome `complete`, `blockers: []` —
and still `certified: false`. The run directory (`./walkthrough-run`,
0700) holds an append-only journal that records every stage transition and
review action.

### 3. One blocked case (the honest path)

```bash
plat-underwrite \
  --om samples/walkthrough/om.pdf \
  --rr samples/walkthrough/rent_roll.pdf \
  --subject synthetic_walkthrough \
  --out ./blocked-run
```

Exit code `2`: the run is created and persisted, and the record names every
unresolved blocker with a kind (`pending_human_action`) and a
`resolved_by` slot that stays `null` until a human decision resolves it.
Nothing fails silently and nothing is zero-filled.

### 4. One cancelled/resumed review

```bash
plat-underwrite --resume ./blocked-run --review samples/walkthrough/review_decision.json
```

The decision file is the shape a host reviewer writes:
`decision_id`, `status` (`approved`|`cancelled`), `reviewer`, and
`resolutions` mapping blocked *value* fields to `{value, evidence}`. A
decision resolves only blocked default value fields (`millage`, `horizon`) —
never a missing source document, and never execution. In this walkthrough the
`debt` source is still absent, so the resumed run honestly reports
`needs_review_or_data` (exit `2`) with `debt` still unresolved. A `cancelled`
status leaves the run blocked and recoverable — resume is idempotent and
journal-driven, so an interrupted review loses nothing.

### 5. One read-only ops period review

```bash
plat-ops review \
  --asset synthetic_ops \
  --as-of 2026-04 \
  --db samples/walkthrough/ops_snapshot.sqlite \
  --materiality 500.00 \
  --out ./ops-out \
  --no-variance
```

One property, one period — cross-property aggregation is structurally
impossible. `--no-variance` honestly blocks variance arithmetic instead of
inventing a parallel formula; to compute variances, bind a host-approved
`boxscore::variance` oracle module with `--variance-module`. Deliverables
(`report.json`, `summary.md`, `metrics.csv`) render cited deterministic
records verbatim; `publication_authorized` is always `false` — generating a
report grants no publish permission.

### 6. Provider substitution stays refused (by design)

```bash
plat-underwrite --om samples/walkthrough/om.pdf \
  --rr samples/walkthrough/rent_roll.pdf --provider vendor/model-1
# exit 3, INVALID_PROVIDER: no provider is approved in this offline default
```

There is no implicit local-to-cloud fallback and no model runs by default.
When host-approved provider routing is configured, canonical provider/model
labels can never alter canonical economics (byte-identical canonical JSON
across labels) — see [PROVIDER_BRIDGE.md](PROVIDER_BRIDGE.md).

## Exit codes (stable contract)

| Code | Meaning |
| ---- | ------- |
| 0    | Complete: the requested stage finished with no unresolved blockers. |
| 2    | Needs review or data: run persisted; typed blockers remain. |
| 3    | Unsupported input: typed usage or validation refusal; nothing persisted. |
| 4    | Execution error: journaled failure surfaced on resume. |

Stdout is a single machine-readable JSON record; typed refusals go to stderr.

## The no-model path is the product path

`NullModel` is the offline default: every walkthrough above ran with no
model, no provider and no network. The model is rented, never assumed — the
harness is agent-agnostic and the same workflow runs unchanged when a
provider is later approved.

## Where to go next

- [OPERATIONS.md](OPERATIONS.md) — running the harness day to day.
- [CLI_UNDERWRITE.md](CLI_UNDERWRITE.md) — the full `plat-underwrite` contract.
- [OPS_REVIEW.md](OPS_REVIEW.md) — the read-only ops review contract.
- [WORKFLOW.md](WORKFLOW.md) — the resumable state machine underneath.
- [ACCEPTANCE.md](ACCEPTANCE.md) — the honest public scorecard (what is
  blocked and why).
- [INSTALL.md](INSTALL.md) — clean installs and optional extras.
