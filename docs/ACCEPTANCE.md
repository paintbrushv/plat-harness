# Acceptance scorecard (honest, public aggregates only)

Contract `acceptance-scorecard/1.0.0`. This public scorecard is **not vendor
certification**, **not engine permission**, and **not Item 2 milestone
acceptance**. It scores ten dimensions separately. A green engineering suite
does not close a blocked gate: gates that lack evidence remain blocked for
missing evidence even with a green engineering suite. Reproducible private
acceptance manifests stay in the private evidence root; only anonymized
public aggregates are published here.

## Honest accounting rules

- A rejected, no-op or partial parse never counts as completed normalization.
  Only a fully observed, stored observation is a completed stage.
- Synthetic examples are not real vendor validation. All synthetic evaluation
  stays labelled synthetic; no synthetic dialect may be relabelled as a
  verified vendor export.
- Arithmetic equality is not economic approval. Engine eligibility stays
  `not_evaluated`; a parse is not engine permission, and human authorization
  for actual-deal execution remains required and unimplemented.
- A green engineering suite and a high test count do not replace source
  coverage. No model extraction metric is published unless it was actually
  measured on labelled data; this scorecard publishes none because none was
  measured.
- No real original documents are lawfully available to hold out, so the
  held-out source set and version remains empty. When real original documents
  become lawfully available, a held-out set and version must be registered
  before any real-export claim.

## Scorecard

Each dimension carries a status from a closed vocabulary (`blocked`,
`green_engineering_only`, `not_evaluated`, `not_measured`, `synthetic_only`)
and a basis naming its evidence seam.

| Dimension | Status | Basis |
|---|---|---|
| `format_identification_source_accounting` | synthetic_only | Seven structural readers identify containers and account for every declared original; the Entrata originals remain an explicit acquisition gap, not fabricated coverage, and zero readers ran against originals. |
| `physical_units_status_use_observation` | synthetic_only | Physical unit, status and count observations are stored for synthetic fixtures of every reader; unknown use stays a recorded limitation on every row, so use is observed only where a cited source states it. |
| `four_count_reconciliation` | blocked | Blocked: independently reviewed use evidence is absent, independent Down evidence is absent, and zero readers ran against originals, so no denominator is supported on any archetype. |
| `source_citations_resolve_and_match` | green_engineering_only | Every model-claimed fact is deterministically re-anchored to its cited source span; invented locators and values absent from cited content refuse on synthetic corpora. |
| `pii_removed_from_payloads_logs_exports` | green_engineering_only | Typed redaction with grid-cited redaction records plus fail-closed redaction self-checks on synthetic canaries; canary literals are asserted absent from payloads, logs and exports. |
| `canonical_inputs_approved_and_complete` | blocked | Blocked: canonical intake exists for lease basis translation, but canonical inputs for a live deal are not approved and complete — human approval is required and remains unimplemented, so no input set is accepted. |
| `deterministic_engine_equality_and_accounting_reconciliation` | green_engineering_only | Canonical engine-intake bytes are identical across provider labels (SHA256-pinned), with lineage recorded in a separate sidecar; accounting observations stay decimal strings and the engine owns all arithmetic. |
| `underwriting_ops_report_completeness` | synthetic_only | Reports render cited deterministic tool records verbatim with a visible watermark inside blocked artifacts, on synthetic runs only; no real-deal report has been produced. |
| `analyst_review_actions_time_and_blocker_reasons` | not_measured | The append-only journal records every review action and blocker reason with no hidden defaults, but no operator observation session has been run, so no review-time or correction-burden number is published. |
| `model_extraction_metrics_only_when_measured` | not_measured | No model-specific extraction metric has been measured on labelled data, so none is published; provider independence is proven structurally (byte-identical canonical output), not by a scored benchmark. |

## Two-archetype gate

Intended pair: Abilene garden and 360 Market Square high-rise. The gate is
**blocked**: `MISSING_REVIEWED_USE`, `MISSING_INDEPENDENT_DOWN`, `ZERO_RAN`.
Four-count reconciliation is blocked on both archetypes. Engineering tests on
synthetic bytes do not close this gate. University Cove is not a verified
student-housing fixture; an unknown subtype cannot be relabelled Student
Housing.

## Public aggregates

Recomputed from the published PMS compatibility matrix
(`pms-compatibility-matrix/1.0.0`); declared totals are never trusted:

```json
{
  "contract_version": "acceptance-scorecard/1.0.0",
  "readers": 7,
  "ran_against_originals": 0,
  "real_export_verified": 0,
  "four_count_reconciled": 0,
  "acquisition_gaps": 1,
  "engine_eligibility": "not_evaluated",
  "item2_milestone": "not_evaluated",
  "two_archetype_gate": "blocked"
}
```

## Sanitize

This public view contains no private paths, overlay directory names, mailbox
identifiers, tenant rows or deal bytes. Scope labels above name the gate, not
filenames, and are not a claim that original bytes were read for this
scorecard.