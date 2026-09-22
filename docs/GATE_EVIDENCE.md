# Gate-evidence comparison and draft handoff

`plat-harness gate-evidence` compares two **explicitly pinned existing decision-register versions**, rather than reprinting readiness. It matches stable decision IDs, emits exact content deltas with before/after JSON pointers, distinguishes removed records from null fields, resolves existing captured page/cell evidence, and drafts evidence requests from each register's remaining gates. No model is required.

```sh
plat-harness gate-evidence --request /absolute/private/comparison.json \
  --request-sha256 LITERAL_LOWERCASE_SHA256 --format markdown
```

Source checkouts may explicitly bootstrap `harness/src` with the existing interpreter; no install is required. Exit **2** is intentional for both JSON and Markdown: comparison is draft-only and cannot authorize underwriting. Successful comparison is stdout; malformed, unsafe, stale top-level pins or incompatible scopes produce typed stderr refusals. The command does not create any files.

## Request format

The request has exactly these keys; each `path` is an absolute private file under the existing harness private root. SHA256s bind literal bytes, not canonicalized JSON. The request itself must also be pinned.

```json
{
  "schema": "gate-evidence-request/1.0.0",
  "label": "SYNTHETIC",
  "subject": "synthetic_property",
  "asset_id": "synthetic_apartments",
  "before": {
    "register": {"path": "/absolute/private/before.json", "sha256": "LITERAL_SHA256"},
    "captures": {"path": "/absolute/private/captures.json", "sha256": "LITERAL_SHA256"},
    "policy": null
  },
  "after": {
    "register": {"path": "/absolute/private/after.json", "sha256": "LITERAL_SHA256"},
    "captures": {"path": "/absolute/private/captures.json", "sha256": "LITERAL_SHA256"},
    "policy": null
  }
}
```

Use `REAL` only for real unchanged or genuinely distinct captured versions. Labels are descriptive, not authority. If no real successor exists, pin the same register and captures on both sides. Do not fabricate a live evidence improvement.

Registers use existing `director-decision-register/1.0.0`: `assets`, `selected_slugs`, and `decisions`. Selected records retain their original `id`, `slug`, `asset_id`, `field`, `proposed_value`, `units`, `status`, `evidence`, `remaining_gate`, and approval fields. Other subjects are never combined. Scope objects must match exactly; a changed interest needs a separately scoped comparison. A stable ID cannot change asset, subject or field. Known parcel sets, tax year, analysis-window endpoints, period and units must agree. Unknown applicability remains null; newly supplied applicability is visible as a change, not approved. Proposed dates and source tax years remain proposals/candidates.

Capture files use existing `pdf_pages` and `static_workbooks` lists. PDF evidence requires one hash/path/page-matching successful nonempty **already captured** page. Spreadsheet evidence requires a hash/path/sheet/cell-matching captured nonnull cell and the exact label when supplied. The original raw source bytes are hash-checked, but no PDF/workbook parser, OCR, macro, recalculation or re-extraction runs. Captured text is not reinterpreted into tax economics. JSON evidence uses existing contract and bridge validation plus strict RFC6901 resolution; empty string is the root pointer, not `/`. Source-code/policy line citations and unsupported locators are retained as `UNREVIEWED`, never called verified.

## State and authority boundaries

- `MISSING`: absent record, absent/null locator evidence, or a register explicitly blocked by evidence. Removal never resolves a prerequisite.
- `STALE`: cited source bytes differ from their pin. Stale top-level register/capture/request bytes refuse the entire comparison.
- `CONFLICTING`: incompatible source claims, invalid locator, cross-asset evidence, unsafe source path, or positive snapshot approval/certification claims. Cross-version applicability mismatches refuse the comparison.
- `UNREVIEWED`: intact candidate/recommendation evidence is not reviewed economic truth. Even `resolved_within_authority` in the analytical register is not human approval.
- `APPROVED` is possible **only in the separate policy-contract result**, through existing `contracts.validate_policy_pack` and independently host-pinned `PLAT_HARNESS_CONTRACT_REGISTRY_PATH` / `PLAT_HARNESS_CONTRACT_REGISTRY_SHA256`. A `policy` reference has the same `{path, sha256}` shape. No request-supplied registry, rank or authority is accepted. Policy-contract approval does not validate its source facts, fill missing assumptions, resolve the evidence handoff or grant live execution.

All outputs retain `engine_executed=false`, `execution_authorized=false`, `certified=false`, `financial_metrics=null`, and `factual_completion=NOT_ESTABLISHED`. Handoffs preserve each remaining evidence/decision request and its source citation, with `retry_authorized=false` and `outreach_performed=false`. No tax selection, new policy, canonical creation, intake repair, source acquisition, outreach or economic arithmetic occurs.

## Limits

This is a bounded comparison of selected registers and captured locators, not a general state machine, standalone CLI audit or independent economic review. Pins prove bytes, not originator authority, legal parcel coverage, source subject attribution when absent, current-market freshness or economic correctness. Registers remain untrusted recommendations. The original readiness command remains available for broader source/canonical/analysis/tax/policy/engine/reconciliation/certification gates; this command neither changes nor supersedes those gates. Only synthetic fixtures belong in repository tests/docs; real packets and outputs stay private.
