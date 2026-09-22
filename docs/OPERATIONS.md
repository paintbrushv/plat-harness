# plat-harness OPERATIONS

Task 7.4 operator documentation: running the harness day to day. The
harness is a local, offline, draft-only control plane. Every operational
rule below is enforced by typed contracts, not conventions: a rule the tool
does not enforce is documented as a boundary instead of being claimed.

## Operating principles

- **No stage is financial certification.** `certified` is always `false`;
  a completed stage is progress, not approval. Human authorization for
  actual-deal execution remains required and unimplemented.
- **Missing is a blocker, never a value.** A missing amount stays `null`,
  never zero; an explicit zero is a real comparison. Missing budgets, feeds
  and mappings become typed blockers, not fabricated reports.
- **Read-only on inputs.** Source files and the ops database are opened
  read-only; run directories are the only writable surface (0700 dirs,
  0600 journal events).
- **Offline by default.** No model runs by default, no provider is
  contacted, no implicit local-to-cloud fallback exists. The no-model path
  (`NullModel`) is the product path and stays useful.
- **Synthetic stays labelled.** Walkthrough fixtures in
  `samples/walkthrough/` are synthetic and labelled as such; they are not
  vendor validation and never count as real-export evidence.

## Daily underwriting operations (`plat-underwrite`)

```bash
plat-underwrite --om <om.pdf> --rr <rent-roll.pdf> --dry-run  # projection, no writes
plat-underwrite --om <om.pdf> --rr <rent-roll.pdf> --t12 <t12.xlsx> \
  --debt <debt.csv> --subject <id> --out <run-dir> \
  --millage <mills> --horizon <years>                         # full intake
plat-underwrite --resume <run-dir>                            # idempotent resume
plat-underwrite --resume <run-dir> --review <decision.json>   # record a decision
```

Two pinned sources (`--om`, `--rr`) start intake; `--t12`, `--debt`,
`--millage` (mills per $1,000; validated, never defaulted) and `--horizon`
resolve blockers as they are supplied. `--stage` requests an explicit
completion stage (`normalized`, `review_required`, `reconciled`,
`canonical_ready`); engine stages are not CLI-requestable — execution
authority lives in the separately reviewed envelope. `--provider` refuses
`INVALID_PROVIDER` until host-approved routing is configured.

### Run lifecycle and interruption

- Run identity is content-addressed: same inputs → same identity; changed
  inputs → a new run. Duplicate runs and output collisions refuse typed
  (`DUPLICATE_RUN` / `OUTPUT_COLLISION`); nothing is overwritten.
- The append-only journal is the truth. Resume rebuilds state from it
  idempotently, so a cancelled review or a killed process loses nothing —
  `--resume` is safe at any point.
- A review decision (`--review <file>`) records a host decision once; it
  can resolve only blocked default value fields (`millage`, `horizon`) —
  never a missing source document, and never execution. `status:
  cancelled` leaves the run blocked and recoverable.
- Exit codes are the documented workflow outcomes: `0` complete, `2` needs
  review or data, `3` unsupported input (nothing persisted), `4` execution
  error. A completed intake and a completed forecast are different
  requested outcomes.

### Review decision files

```json
{
  "decision_id": "dec_001",
  "status": "approved",
  "reviewer": "host_reviewer",
  "resolutions": {
    "millage": {"value": "25.31", "evidence": "synthetic-policy"},
    "horizon": {"value": "5", "evidence": "synthetic-policy"}
  }
}
```

Interactive review answers are decisions, never self-authorization: they
are recorded through the review path with no execution side effects.

## Daily ops reviews (`plat-ops review`)

```bash
plat-ops review --asset <id> --as-of <YYYY-MM> --db <ops.db> \
  --materiality <decimal-string> --out <fresh-dir> [--variance-module <module>]
```

- **One property, one period.** Wildcard/aggregate asset tokens (`*`,
  `all`, `portfolio`) and multi-period requests refuse `INVALID_INPUT`;
  `scope` in the record echoes `properties_reviewed: 1, periods_reviewed: 1`.
- **Variance only through a bound oracle.** The harness performs no variance
  arithmetic. `--variance-module` binds a host-approved module exposing the
  pinned `boxscore::variance` pure functions; with no oracle, pass
  `--no-variance` and the review honestly blocks variance (`--no-variance`
  is explicit, never a silent skip). Missing budget is a blocker while an
  explicit zero is a real comparison.
- **The source database is never written.** The review opens the quiesced
  snapshot read-only; deliverables go to a fresh `--out` directory
  (`report.json`, `summary.md`, `metrics.csv`, 0700). Generating a report
  grants no publish permission: `publication_authorized` is always `false`,
  and blocked runs carry a visible watermark inside the artifact.
- Stale feeds, duplicate GL imports and mapping drift are surfaced as typed
  material exceptions with traceable citations, not aggregated away.

## PII and redaction

Typed redaction with grid-cited redaction records runs on every egress
path; resident details never reach payloads, logs or exports. Canary
strings are asserted absent from results, refusals and exception chains in
the test suite. Over-budget content is excluded whole (never cut mid-line),
and a fail-closed redaction self-check runs before any payload is
exportable. See [SECURITY.md](SECURITY.md) for the honest boundary model.

## Incident-style triage

| Symptom | What it means | Operator action |
| --- | --- | --- |
| Exit `2` with blockers | Needs review or data — the honest normal | Read the record's `blockers` (field, kind, `resolved_by`); supply the missing source or record a decision. |
| Exit `3` typed refusal | Unsupported input; nothing was persisted | Fix the named flag/file; re-run. Nothing needs cleanup. |
| Exit `4` execution error | Journaled failure surfaced on resume | `--resume` again after fixing the cause; the journal preserves state. |
| `INVALID_PROVIDER` | No provider routing is approved | Intentional. Configure host-approved routing first; no fallback exists. |
| Variance blockers in ops review | No oracle bound (or `--no-variance`) | Bind the approved oracle with `--variance-module` or accept the blocked variance. |
| `XLSX_DEPENDENCY_MISSING` / `PDF_DEPENDENCY_MISSING` | Optional extra absent | `pip install "plat-harness[ingest]"` / `[pdf]`. |

## What operations never does

- No writes to source documents or the ops database.
- No cross-property or cross-period aggregation.
- No automated recommendations, no publish, no certification.
- No model launch, provider call, network egress or fine-tuning.
- No zero-filling of missing data and no relabelling of synthetic fixtures
  as vendor validation.

## Measuring the walkthrough

Time to first meaningful result, unresolved prompts, correction burden and
reproducibility are measured from actual operator observation sessions;
business targets are set with the owner after a baseline is measured — no
time-saved or economic-impact claims are published without a measured
baseline. The `analyst_review_actions_time_and_blocker_reasons` scorecard
dimension stays `not_measured` until such a session runs
(see [ACCEPTANCE.md](ACCEPTANCE.md)).
