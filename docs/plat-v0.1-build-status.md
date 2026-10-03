# Platworks v0.1 build status

Started 2026-10-02. This is a development baseline, not a release approval.

## Source baseline

[release/v0.1-baseline.json](../release/v0.1-baseline.json) records the six
required public components, exact starting commits, source/package version
differences, target platform matrix, contract versions, fixture hashes, and
unverified host/release gates. Public main was freshly cloned for all six.
The baseline's harness commit is the starting parent; a release candidate
must identify the eventual implementation commit and built artifact hashes.

```bash
python scripts/verify_release_baseline.py
# Optional: check all six baseline checkouts before implementation commits.
python scripts/verify_release_baseline.py --workspace-root /path/to/checkouts
```

## Financial correction

The [synthetic operating fixture](synthetic-read-http.md) now uses positive
expense costs and the corrected Oak Ridge bridge. It carries a revision,
producer pin, six input hashes, and the producer lock hash. A new CI job
compares the installed HTTP resource with actual Rust CLI analysis and checks
that a repeated analysis preserves the issued report.

Local check results are recorded below as they are executed. The new hosted
CI workflow has not run until the changes are pushed.

## Local validation — 2026-10-03

- Baseline validator passed for all six source checkouts and the fixture hash.
- 25 focused Python tests passed, including the corrected HTTP resource,
  authorization/read-only controls, stale fixture/source refusal, income
  review, reasonability, and original thesis.
- Both pinned Rust producer regressions passed: the independent economic
  bridge and legacy negative-expense refusal.
- Harness wheel and source archive built; clean wheel smoke and `pip check`
  passed. The corrected fixture loads from installed package resources.
- [Installed HTTP versus Rust CLI evidence](../release/evidence/oak-ridge-v2.local.json)
  confirms all seven bridge values, three occupancy counts, and preservation
  of the first report when a second report is issued.

Local Python was 3.14.4 and Cargo was 1.96.0. The intended Python 3.11/3.12
platform matrix and the new hosted workflow remain unverified. The local
wheel retains the baseline 0.1.0 version for development and was not published;
release packaging must assign an unused version.

## Remaining work

- Compatible MCP major, complete installed umbrella dependencies, packaged
  Rust binaries, and tested release/version gates.
- Local acquisition and operations review journeys for supported real files;
  a separate reviewed-draft contract preserves existing synthetic guards.
- Reviewed library retrieval and real ChatGPT/Claude/Grok host checks. Muse
  remains pending external review and does not block launch.
- Existing website alignment, ownership community eligibility and deployment,
  original-source acceptance, temporal/replay scope, and three-user pilot.

The older [V3 ledger](plat-v3-acceptance-gates.md) and
[acceptance scorecard](ACCEPTANCE.md) retain their missing-evidence gates.
Synthetic arithmetic checks cannot close those gates.

## Financial core candidate — 2026-10-03

B2 adds the public `plat.backsolve/1` API in underwriting, used by its CLI,
agent, and both MCP surfaces. Versioned policy and benchmark rate/date/source
are explicit. The wrapper's duplicate solver is removed. Success, feasible
ceiling, infeasibility, and iteration exhaustion have distinct statuses.

B3 adds `boxscore-exact` with checked cents, INTEGER persistence, canonical
CSV/JSON imports, snapshots, immutable reports and correction bridges. Its
`plat.ops/1` protocol replaces the umbrella's Python financial port. Reviewed
migration creates a new database and preserves the source and historical bodies.
Legacy floating-point features remain excluded from the exact workflow.

This harness adds `ops-review/2.0.0` and an exact database reader. It selects the
current revision, formats cents without floats, verifies the canonical input
hash, and keeps account mapping approval unresolved. The CLI binds a single
`compute(actuals, budgets)` owner function; `platworks.ops_oracle` provides it.
See [the review contract](OPS_REVIEW.md).

Local validation: 122 focused harness tests passed. The real Rust/Python
acceptance test in the umbrella imports CSV, issues a report, applies a one-cent
correction, preserves the original body, and reviews the exact database.
The full local harness run was killed with exit 137 after progressing past 64%;
it is not recorded as a pass. Hosted full-suite validation is required.
The separate starting baseline and its historical fixture evidence are preserved.
