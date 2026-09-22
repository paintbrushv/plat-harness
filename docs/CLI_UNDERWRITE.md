# Unified underwriting CLI: `cli-underwrite/1.0.0`

**Task 6.2.** `plat-underwrite` — the resumable, local, draft-only entry
point for underwriting runs. A thin client over the Task 6.1
[`workflow/1.0.0`](WORKFLOW.md) state machine: it pins sources, derives
run identity, drives host-controlled stage transitions, and reports honest
typed blockers. It is never a second orchestrator, never an engine, never a
parser: no financial math, no model, no provider, no network, no threads,
no helper processes, and no stdin reads. Missing inputs are blockers, never
invented values: a missing amount stays `null`, never zero. No stage,
outcome or exit code is financial certification; certification and
live-deal authorization remain unimplemented pending explicit human
authority.

## Usage

```text
plat-underwrite --om <file> --rr <file>                          # start intake
plat-underwrite --om <file> --rr <file> --t12 <file> --debt <file> --out <dir>
plat-underwrite --resume <run-dir>                               # idempotent resume
plat-underwrite --resume <run-dir> --review <decision-file>      # record a host decision
plat-underwrite --om <file> --rr <file> --dry-run                # project, write nothing
plat-underwrite --om <file> --rr <file> --non-interactive --json # no prompts, JSON stdout
```

Two pinned sources (`--om`, `--rr`) are enough to start intake; they are
never enough to invent the rest. Optional `--t12` and `--debt` sources,
`--millage` (mills per $1,000; validated, never defaulted) and `--horizon`
(whole years) resolve blockers as they are supplied. `--subject` defaults to
a sanitized stem of the OM filename; `--out` defaults to
`<cwd>/plat-underwrite-runs/<content-identity-prefix>` (0700). Paths with
spaces are ordinary input. `--stage` requests an explicit completion stage
(`normalized`, `review_required`, `reconciled`, `canonical_ready`);
requesting `execution_authorized`, `engine_executed` or `report_ready`
refuses `UNSUPPORTED_STAGE` — engine authority lives in the separately
reviewed execution envelope, not in this CLI. `--provider` refuses
`INVALID_PROVIDER` until host-approved provider routing is configured;
there is no implicit local-to-cloud fallback and no model runs by default.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0    | Complete: the requested stage finished with no unresolved blockers. |
| 2    | Needs review or data: run created/resumed and persisted; typed blockers remain (or millage/horizon invalid). Also `TRANSITION_BLOCKED`/`EXECUTION_BLOCKED`. |
| 3    | Unsupported input: typed usage or validation refusal (missing flags, missing file, unsupported suffix, bad provider/stage, duplicate run, output collision, corrupt journal, stale pins, malformed decision file). Nothing is persisted on a refusal. |
| 4    | Execution error: journaled engine failure surfaced on resume, or an unexpected internal error. |

Stdout is a single machine-readable JSON workflow record (or the dry-run
projection). Typed refusals go to stderr as JSON `{"error", "message"[,
"details"]}`. A completed intake (`--stage normalized`, exit 0) and a
completed forecast (`canonical_ready`, exit 0) are different requested
outcomes. `--dry-run` computes the same content identity and blockers with
zero writes and zero side effects. Resume is idempotent and journal-driven;
a review decision (`decision/…` JSON with `decision_id`, `status`
`approved|cancelled`, `reviewer`, `resolutions` mapping blocked fields to
`{value, evidence}`) is recorded once and can resolve only blocked default
value fields — never a missing source document, and never execution.

## Boundaries

Import-only thinness: the module imports only the workflow state machine,
typed errors and the millage parser — never `plat_harness.adapters`,
`plat_harness.ingest`, engine modules, optional spreadsheet readers or
network clients. Records and errors carry hashes, host paths, formats and
field names; source bytes, source text and credentials never surface.
Run directories inherit the state machine's private permissions (0700
dirs, 0600 journal events).