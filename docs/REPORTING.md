# Deterministic reporting and ops CLI: `reporting/1.0.0` + `cli-ops/1.0.0`

**Task 6.4.** Two thin, deterministic rendering surfaces. `plat_harness.reporting`
turns a frozen tool record (engine or ops output) into three byte-deterministic
deliverables — canonical JSON, a cited Markdown summary, and a formula-
injection-safe CSV export. `plat-ops` is the read-only ops CLI: one property,
one period, no writes to the source, no financial math anywhere.

```python
from plat_harness import reporting
from plat_harness.cli_ops import main

json_text = reporting.render_json_report(tool_record)      # canonical JSON
md_text = reporting.render_markdown_summary(tool_record)    # cited summary
csv_text = reporting.render_csv_export(tool_record)         # safe cells
reporting.write_deliverables(tool_record, out_dir)          # all three files
```

```text
plat-ops review --asset <opaque-id> --as-of 2026-04 \
  --db /path/to/ops.db --out /path/to/fresh-dir
```

## What the renderer guarantees

- **Engine numbers pass through verbatim.** A metric amount is a decimal
  string inside a closed money record (`amount`, `currency`, `unit`,
  `period`, `source`, `source_truncated`); floats refuse `INVALID_INPUT`.
  A missing metric stays `null` — never zero — and remains visible in
  every deliverable.
- **Citations resolve or refuse.** Every rendered metric must carry a
  non-empty citation; an uncited metric refuses `CITATION_REQUIRED`, and a
  citation for an absent metric refuses too — no orphan numbers ship.
- **The record shape is closed.** Unknown top-level keys refuse
  `INVALID_CONTRACT`; hostile payload content cannot ride along.
- **Blocked runs stay visibly watermarked.** With any blocker on the
  record, each deliverable carries the `WATERMARK` line; it disappears only
  when the record carries no blockers, never by argument.
- **Spreadsheets are injection-safe.** A rendered cell that would begin
  with `=`, `+`, `@`, a tab or a CR refuses `INVALID_INPUT` — source
  record text is untrusted and never echoes into a cell context.
- **Generation is not permission to publish.** Every JSON payload records
  `publication_authorized: false` and `certified: false`.
- **Byte-deterministic.** Identical records produce byte-identical
  deliverables, pinned by `source_record_sha256` in the JSON payload.

## What the ops CLI guarantees

- **One property, one period.** Wildcard/aggregate asset tokens and
  multi-period requests refuse `INVALID_INPUT`; the seam itself also
  refuses any backend row outside the requested scope.
- **Missing backend is a blocker.** A nonexistent `--db` refuses
  `NOT_FOUND` — the CLI never fabricates a review from nothing.
- **No parallel variance formula.** Variance comes only from an oracle
  bound over the pinned ops owner (`boxscore::variance` —
  `compute_account_variances` + `compute_noi_bridge`), supplied explicitly
  via `--variance-module`. With no oracle (or `--no-variance`) the review
  completes blocked with `VARIANCE_NOT_IMPLEMENTED` and renders
  `noi_variance` as `null` — never zero, never computed locally.
- **Read-only.** The source database is opened through the frozen
  read-only backend; a review leaves it byte-identical.
- **Blocked reviews still deliver.** Every review writes `report.json`,
  `summary.md` and `metrics.csv` into a fresh (0700) directory; blocked
  runs carry the reporting watermark end to end.

## Exit codes

| Code | Meaning |
| ---- | ------- |
| 0    | Complete: reviewed with no unresolved blockers. |
| 2    | Needs review or data: blocked review (e.g. no variance oracle, missing budget) — deliverables are still written. |
| 3    | Unsupported input: typed usage refusal (missing flags, missing db, wildcard asset, bad period, output collision, unknown command). Nothing is written. |
| 4    | Execution error: unexpected internal failure. |

stdout is a single machine-readable JSON review record; typed refusals go
to stderr as JSON `{"error", "message"[, "details"]}`.

## What these modules do NOT do

- No financial math of any kind (the renderer copies, the CLI delegates).
- No writes to ops sources; no journal entries; no lease or resident
  actions; no automated recommendations.
- No engine import or execution; no model, provider, network, threads or
  spreadsheet writers.
- No certification and no publication: generation is not authorization to
  publish; live-deal authorization remains unimplemented pending explicit
  human authority.