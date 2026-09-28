# Selected synthetic migration and authorization boundary — 2026-09-28

The owner selected the public synthetic four-row fixture for the next
migration rehearsal and synthetic grants for the current access tests. This
selection authorizes only a disposable synthetic run. It supplies neither a
sanitized legacy source cohort nor a current host-owned workspace/deal grant
registry. An actual host and test identity have not yet been selected.

## Exact boundary and version set

| Item | Selected evidence boundary |
| --- | --- |
| Harness source | Merged `53fc98d066b7a467ae611924eca541cd7474a819` |
| Other installed-package baseline | Agent `a19d726ae8006e3ccbfc6984fcfef0d02abf02e3`; underwriting `10a88ed393e6d6611c8c710b5e15ef64e128af6b`; costmodel `518142ecb8771e52fcc9985237fe1a6f97a76168` |
| Synthetic source | `tests/test_opening_migration.py::snapshot`, `plat.opening-snapshot/1`, workspace `alpha`, source `legacy-pm`, account `account-7`, observed as of `2026-09-01` |
| Source bytes | Canonical `json.dumps(snapshot(), sort_keys=True, separators=(",", ":")).encode()`; SHA-256 `0ce3902b850c076db7d2c99175d5beb1c7ee63eae84b228d596c34b2a2ea9a1a` |
| Stable-ID map | `legacy-A → prop-101`, `legacy-B → prop-202`; SHA-256 `d42322594fcda703bde3502bd2837876d95a342c5841adaafe45420c05f1aa77` |
| Synthetic resource grants | `tests/test_temporal_rehearsal.py::Host.grants` and `tests/test_opening_migration.py::ledger` authorization callbacks; these are test-owned, in-memory grants |
| Synthetic contracts approvals | Host-pinned fixture file made by `tests/test_approved_execution.py::host_env`, loaded by `review_bridge.host_registry` using `PLAT_HARNESS_CONTRACT_REGISTRY_PATH` and `PLAT_HARNESS_CONTRACT_REGISTRY_SHA256` |
| Actual host/grant source | Unspecified. The contracts approval registry and the resource-grant callbacks are different authorities. Neither is a current real-host grant source. |

The four expected rows are August residential revenue `200000.00` and
expense `-100000.00` for `prop-101`, August commercial revenue `30000.00` for
`prop-202`, and an explicit null-amount tombstone. The old source has no
established historical event log; the import treats these as opening
observations, without inventing past knowledge or a deletion amount.

## Executed evidence

On 2026-09-28, from a `git archive` of the exact merged harness SHA, the
following local source tests passed:

```text
python3 -m pytest tests/test_opening_migration.py tests/test_temporal_rehearsal.py
18 passed in 0.22s

python3 -m pytest tests/test_approved_execution.py --basetemp=<private 0700 root>
56 passed in 0.83s
```

The second command used a temporary private test root under the original
checkout. Its ancestor was temporarily made non-group-writable to exercise
the artifact integrity guard. The original mode `0775` was restored, and the
temporary directory was removed. No original checkout content or worktree
reference was changed. The test suite used its configured `harness/src`
source path; the separate exact-SHA installed-wheel check is recorded in
`plat-v3-acceptance-gates.md`.

These tests cover exact-cent row groups and source/revision identity,
missing mappings, gaps, tombstone/null precision, bounded restart after an
interrupted checkpoint, repeat import, competing resume workers, frozen
issued reports, temporal corrections, current synthetic grant revocation,
and freshly reloaded pinned synthetic approval records. They are component
and local workflow evidence, not a source-of-record migration or an actual
host authorization test.

## Required before the remaining gates can run

1. Identify an explicitly approved, suitably protected sanitized or
   restricted non-production source snapshot/cohort, its immutable hash,
   source writer/version, stable identity mapping, and allowed test location.
2. Identify the current host-owned workspace/deal resource-grant source, a
   read-only test identity, and the exact host to validate. Pin its version
   and test revocation across execution, historical readback, replay, and
   restore. Keep the contracts approval registry separate from resource
   grants.
3. Reconcile the chosen source at property, period, account, unit category,
   and source/revision level; quarantine rejects; inspect pending actions and
   uncertain outcomes; prove catch-up where applicable, zero-effect replay,
   redaction retention through restore, and rollback or forward recovery.

All eleven Section 13 **integrated end-to-end** cases and the Section 26–27
real-cohort/host gates remain blocked. `INTEGRATION_VERIFIED` applies only to
the separate public synthetic installed-package path. `RELEASE_CANDIDATE_READY`
is false, and `LIVE_CUTOVER_NOT_AUTHORIZED` remains in force.
