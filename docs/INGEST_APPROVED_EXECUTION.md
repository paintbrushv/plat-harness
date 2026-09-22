# Reviewed-draft execution envelope: `approved-execution/1.0.0`

**Task 3.4.** A separately versioned execution envelope for *reviewed drafts*
that reuses the existing authority primitives and grants **nothing new**.
Authority is exactly the frozen binding from `plat_harness.contracts`
(`_approval`: human actor, timezone-aware timestamp, exact `payload_sha256`,
record present byte-for-byte in the host-owned registry) loaded through the
frozen `review_bridge.host_registry()` env-pinned loader. There is **no second
approval database and no easier `approved: true` path**: the envelope's own
`approval` record is validated by that same frozen `contracts._approval`
binding via `review_bridge.approved`, and classification/policy review reuse
`contracts.require_approved_policy` unchanged.

The engine repository is read-only and is never imported, executed or
modified by this module. The existing engine entry point is referenced only
by its pinned module/function name:

```python
from plat_harness.adapters.approved_execution import (
    ENVELOPE_VERSION, ENGINE_PATH, RECON_PATH, ERROR_CODES, RUN_METADATA,
    ARTIFACT_NAMES, SOURCE_ARTIFACTS, DEFAULT_ENGINE_TIMEOUT_S,
    code_pins, execute_reviewed_draft, read_reviewed_draft,
)

result = execute_reviewed_draft(
    envelope=envelope,          # exact contract below
    canonical=canonical_bytes,  # exact canonical engine-input bytes
    evidence=evidence,           # {'classification': bytes, 'policy': bytes}
    engine_callable=run_underwriting,  # host binds ENGINE_PATH
    run_dir=private_run_dir,     # empty dir below the private data root
    registry_loader=None,        # default: review_bridge.host_registry()
    engine_timeout_s=60.0,       # bounded wait, required, 0 < s <= 180
)
```

`ENGINE_PATH` is `engine.engine.run_underwriting`;
`RECON_PATH` is `runs.build_underwriting_reconciliation.main` (pinned for
lineage; **not invoked** by this envelope — see "No certification").

## Result contract

`execute_reviewed_draft` returns the run manifest plus `artifact_dir`. Every
run is a **reviewed draft**: `status='draft_pending_review'`,
`certified=False`, `certification_eligible=False`,
`blocker='RECON_ECONOMICS_REQUIRED'`, `recon_status='NOT_REVIEWED'`.
`economics` and `economics_sha256` carry exactly what the bound engine
callable returned — byte-identical to direct existing-engine execution on
the same canonical bytes; this envelope computes, alters or rounds nothing.

Artifacts (all `0600`, published atomically by stage+rename, never
overwriting a populated run directory): `inputs.json`,
`classification.json`, `policy.json`, `envelope.json`, `economics.json`,
`manifest.json`. The manifest key set is exactly `RUN_METADATA`
(closed tuple, echoed in `run_metadata_keys`); the only documented
run-metadata difference between two executions of identical canonical bytes
is `executed_at`.

`read_reviewed_draft(run_dir=..., registry_loader=None)` re-verifies every
pin against **current** state before returning the same manifest: artifact
digests (inputs/evidence/economics/envelope/manifest), current `code_pins()`,
synthetic scope, supported locators, **and a freshly loaded host registry
with the full frozen approval/policy/classification validation re-run** on
the saved bytes. Any drift refuses; a tampered `certified: true` claim
refuses with `UNCERTIFIED_METRIC`.

## Gates (fail-closed, in order, all before any engine call or write)

| Gate | Effect |
|---|---|
| Envelope shape/version/identifier format | refuse `INVALID_CONTRACT` / `INVALID_ID` |
| `synthetic` not exactly `True` | refuse `LIVE_RUN_NOT_AUTHORIZED` |
| `intake_validated` not exactly `True` | refuse `REVIEW_REQUIRED` |
| Non-empty `overrides` | refuse `POLICY_CONFLICT` (approve new canonical bytes instead) |
| `code_sha256` != current `code_pins()` | refuse `STALE` |
| Canonical bytes absent or digest != `input_sha256` pin | refuse `INVALID_INPUT` / `HASH_MISMATCH` |
| **Synthetic scope** (RED 1): canonical `deal_id` lacks the `synthetic_` prefix, `purpose` outside the synthetic allowlist, or envelope/canonical identity mismatch | refuse `LIVE_RUN_NOT_AUTHORIZED` / `APPROVAL_MISMATCH` **before any engine call** |
| Missing/mismatched classification or policy evidence pins | refuse `EVIDENCE_INCOMPLETE` / `HASH_MISMATCH` |
| Run directory not an empty private dir | refuse `UNSAFE_PATH` / `RUN_CONFLICT` / `NOT_FOUND` |
| **Unsupported source locators** (RED 3): any locator other than `json_pointer` into the exact canonical `inputs.json` bytes, or any other artifact name | refuse `EVIDENCE_LOCATOR_UNSUPPORTED` — never translated into weaker evidence |
| Registry: env-pinned host registry (or validated loader), reloaded **per invocation** | refuse `APPROVAL_REQUIRED` / `INVALID_CONTRACT`; revocation blocks execution *and* readback |
| Frozen authority: `contracts._approval` on the envelope + `require_approved_policy` | refuse `APPROVAL_REQUIRED` / `APPROVAL_MISMATCH` / `CLASSIFICATION_REVIEW_REQUIRED` / `POLICY_*` / `SUBJECT_MISMATCH` |
| Reviewed policy millage must equal the exact canonical millage | refuse `POLICY_CONFLICT` |
| Evidence locators must resolve to nonnull values in the exact canonical bytes | refuse `EVIDENCE_INCOMPLETE` |
| Engine callable missing, wait unbound/non-finite/`> 180s`, engine raises, output not an object | refuse `NOT_IMPLEMENTED` / `INVALID_CONTRACT` / `ENGINE_TIMEOUT` (sanitized) |

A mapping/policy review alone is **insufficient** (RED 2): an approved
classification plus an approved policy pack executes only as a draft that
still carries `RECON_ECONOMICS_REQUIRED`; without a separately reviewed
economic reconciliation nothing certifies (RED 4), and `RECON_PATH` is never
invoked by this envelope.

## Bounded execution

The engine callable runs inside a **forked child process** that can always be
SIGKILLed at the bound — the same killable-subprocess pattern the frozen
slice_b adapter uses to bound the real engine, and the only way to bound a
callable that cannot be cancelled cooperatively. The parent stays
single-threaded (the frozen worker-context contract requires
``threading.active_count() == 1``); no thread, poll loop or unbounded wait
exists in this module. The wait is always bounded (`engine_timeout_s`,
default 60s, hard cap 180s; non-finite/zero/negative values refuse
`INVALID_CONTRACT`). Engine failures and timeouts refuse with typed,
sanitized errors — engine internals never surface.

## Sanitized errors

All refusals are `HarnessError` with a code from the closed `ERROR_CODES`
frozenset and a static operator-facing message. Frozen-bridge refusals are
re-raised through a sanitizing boundary so `.__cause__`/`.__context__` stay
`None`: input values, decoder details and engine internals never appear in
exception chains, details or messages.

## What this envelope does NOT do

- No live-deal execution: synthetic scope fails closed, and human
  authorization for actual-deal execution remains **required and
  unimplemented** — this plan authorizes implementation and synthetic
  testing only.
- No certification: `certified` is always `False`; economic reconciliation
  review is a separate, not-yet-implemented contract.
- No financial arithmetic, no assumption selection, no model-supplied
  authority, no filesystem IO from evidence locators (only in-memory
  canonical bytes are resolvable).