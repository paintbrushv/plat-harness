# Read-only readiness explanation

`plat-harness readiness` converts an existing, hash-pinned historical readiness bundle into an actionable JSON or Markdown refusal. It requires no LLM, GPU, financial calculation, source re-extraction or live engine execution. It does not repair intake or perform the separately gated standalone CLI audit.

```sh
plat-harness readiness --bundle /absolute/private/closed-bundle \
  --index-sha256 LITERAL_REVIEWED_INDEX_SHA256 --deal EXACT_EXISTING_SUBJECT \
  --format markdown
```

Use the existing installed CLI or explicitly set the repository's `harness/src` import path for a source checkout. No install is needed. The bundle must lie under the harness's existing private data root. It must contain registered `director/readiness_matrix.json`, `director/approval_package.json` and `authorization.json`. The matrix must use `five-deal-readiness/1.0.0`, bind the exact analytical package and contain at most five unique subjects. Subject and asset coverage must match the package.

The command emits:
- the first unmet gate and separate source/canonical/analysis/tax/policy/engine/reconciliation/certification states;
- exact scope and proposed historical windows, never approved by file presence;
- typed blockers with required evidence/action, exact hash/JSON-pointer citations and no automatic retry authorization;
- missing, stale, conflicting and unreviewed evidence states;
- explicit null financial metrics, `engine_executed=false`, `certified=false`.

Exit **2** is intentional: this feature explains a refusal; it cannot authorize or execute underwriting. Malformed/unregistered/unsafe bundle inputs emit typed errors on stderr. A successful explanation goes to stdout. Redirect outputs only into a new private run. Nothing is written by the command.

## Authority and limitations

A hash pin establishes exact bytes, not trusted business approval or current-market freshness. The command checks selected registered package dependencies, recommendation files and intake-result bytes—not every raw file or economic correctness. Historic no-retry restrictions are displayed, never reopened. Missing taxes are not zero; proposed dates are not approved dates. Claimed positive eligibility/approval/certification booleans in a snapshot are marked `CONFLICTING`, not accepted as authority. The first gate remains source economic review for this historical blocked-bundle interface; this is not a generalized live readiness state machine.

An optional `--policy /absolute/private/policy.json --policy-sha256 LITERAL_SHA256` checks an existing `policy-pack/1.0.0` against the existing independently pinned `PLAT_HARNESS_CONTRACT_REGISTRY_PATH` / `PLAT_HARNESS_CONTRACT_REGISTRY_SHA256` host registry. No registry is accepted from the request. It can report `APPROVED` only after `contracts.validate_policy_pack` verifies exact payload and approval provenance. This validates the approval contract, not source truth, completeness or execution eligibility. It never replaces the historical gates or grants execution/certification.

No arbitrary approvals, financial defaults, model paths, intake edits, parser fixes, policy changes, source acquisition or engine calls occur. Private subjects and scenario packets remain outside this repository. Tests use only synthetic data.
