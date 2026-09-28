# Synthetic observed opening-state migration

This is a bounded rehearsal of mandate Section 26. A versioned
`plat.opening-snapshot/1` JSON byte string and its exact SHA-256 are planned
without writing a target. The caller supplies an explicit stable property-ID
map scoped by workspace, source system, and account. Names and equal monetary
amounts are never used as identities. The importer accepts only one reviewed
revision per source record in this opening snapshot; unsupported amendments
fail instead of being silently selected.

The source's `as_of` is an **observation boundary**. Imported rows are
`observed_opening_state/1` assertions confined to that date, recorded when the
local ledger commits them. They do not claim that PLAT knew the source's
August financial values in August. `read_opening_baseline` refuses dates other
than the opening date rather than filling unsupported history or forecasts.
The source accounting period stays a separate field. Tombstones stay explicit
with `amount=null`, never converted to a zero balance.

## Rehearsal API

1. `plan_opening_snapshot(bytes, expected_sha256=..., identity_map=...)`
   validates the frozen byte hash, schema, complete sequence, mapping,
   source/revision identity, date-only boundary, period, cents precision,
   bounded rows, and null-versus-zero semantics. It writes nothing.
2. `execute_opening_batch(plan, ledger, actor_id=..., checkpoint_path=...,
   batch_size=..., known_at=...)` processes at most 100 rows per call under
   a local file lock. It checks current read/write scope for every stable
   property. Each committed assertion precedes an fsynced, atomic checkpoint
   advance. A crash between those steps replays the exact source revision as
   an idempotent no-op. The checkpoint pins snapshot and mapping digests.
3. `reconcile_opening` compares every imported source/revision row with the
   exact expected payload, including tombstones, before producing exact
   Decimal totals by stable property, period, account, and unit category.
   Validation precedes the `validated` checkpoint state. A repeated call
   rechecks row parity even when that state was already set.

The six synthetic tests in `tests/test_opening_migration.py` cover a pure
plan, two bounded batches, exact independent grouped money expectations,
repeat import, interruption after ledger commit and before checkpoint,
concurrent resume workers, gaps, changed mapping, hash mismatch, missing
identity, tombstone/null and cents refusals, row-level drift, and current
deal-scope authorization. The earlier eleven temporal tests still run.
The test data is public and synthetic; no live/ignored deal files enter the
repository or this import.

A fresh Python 3.14 venv installed the candidate harness wheel and its
declared PyYAML dependency. With `PYTHONPATH` unset and `python -I`, the
installed `opening_migration` and `temporal` modules loaded from
`site-packages`, completed a one-row opening import/reconciliation, and
`pip check` found no broken requirements. The observed wheel SHA-256 was
`734582338d958dfa758fdc361525855d673a48b07ddac50cb934c91285b82f4e`.
This validates installed packaging for the synthetic lane, not the full
four-package release combination or a real migration cohort.

The checked-in synthetic four-row baseline has snapshot SHA-256
`0ce3902b850c076db7d2c99175d5beb1c7ee63eae84b228d596c34b2a2ea9a1a`
and mapping SHA-256
`d42322594fcda703bde3502bd2837876d95a342c5841adaafe45420c05f1aa77`.
The independent grouped expectations are property `prop-101` August
residential revenue `200000.00`, expense `-100000.00`, and property
`prop-202` August commercial revenue `30000.00`; the fourth row is a
null-amount tombstone. These are synthetic dollars and exact-cent checks.

## Gates still open

This is one local Linux snapshot lane. It does not quarantine invalid rows
with a durable review queue; it rejects the whole plan. It does not migrate
legacy knowledge history, approval or outbox state, mutable source changes,
amendments, split/merged identities, source timestamps, occupancy-day claims,
actual PMS data, or external action outcomes. It has no catch-up watermark,
snapshot restore proof, retention/redaction overlay, atomic serving
projection, traffic/write fence, or forward-repair rehearsal. There is no
actual host identity/policy integration or authorization to cut over. The
full Sections 13 and 26–27 acceptance matrix remains blocked.

The embedding host must supply an isolated target and protect both the
temporal store and checkpoint directory. This rehearsal does not prove that
an arbitrary existing event stream is a safe migration target.
