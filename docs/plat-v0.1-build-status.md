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

- Authoritative public backsolve API and explicit benchmark/policy inputs.
- Checked integer-cent operating workflow, Rust JSON protocol, and reviewed
  copy migration of legacy databases. Current Boxscore still uses `f64` money.
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
