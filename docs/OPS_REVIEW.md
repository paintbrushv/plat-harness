# Read-only operations review: `ops-review/2.0.0`

**Task 3.5.** A read-only, single-(property, period) operations review over the
ops owner's SQLite backend. It returns actual-versus-budget variance (only
through a bound deterministic oracle), occupancy change, and typed material
exception records with traceable evidence under a user-configured
materiality policy. It performs **no writes, no journal entries, no lease or
resident actions, and no automated recommendations** — a review, not an
operational workflow.

```python
from plat_harness.adapters.ops_review import (
    CONTRACT_VERSION, ORACLE_OWNER, ORACLE_FUNCTIONS, ERROR_CODES,
    EXCEPTION_CODES, RESULT_KEYS, STALE_AFTER_DAYS, SqliteOpsBackend,
    review_period,
)

result = review_period(
    asset_id='synthetic_ops',          # exactly one property
    period='2026-04',                  # exactly one YYYY-MM period
    materiality={'variance_abs': '500.00', 'currency': 'USD',
                 'stale_after_days': 45},   # stale_after_days optional
    as_of_date='2026-04-30',           # optional; gates feed freshness
    db_path='/path/to/ops.db',         # or backend=SqliteOpsBackend(...)
    variance_oracle=owner_adapter,     # or None -> typed blocker
)
```

`status` is `'reviewed'` or `'blocked'`; blockers are content, not refusals:
the review always returns its evidence and exceptions rather than raising on
missing data. Refusals (`HarnessError`, closed `ERROR_CODES`, static
sanitized messages, clean exception chains) are reserved for invalid input
and backend protocol breaches.

## Scope: one property, one period

Cross-property and cross-period aggregation is structurally impossible:
wildcard/aggregate asset tokens (`*`, `all`, `portfolio`, …) and multi-period
requests refuse `INVALID_INPUT`, an asset resolving to more than one property
refuses `AMBIGUOUS_PROPERTY`, and any backend row outside the requested
property or period refuses `SCOPE_MISMATCH`. `scope` in the result echoes
`properties_reviewed: 1, periods_reviewed: 1` so downstream consumers can
verify no merge occurred.

## Money and the variance oracle

The harness implements **no variance arithmetic** — that belongs to the
deterministic ops owner. `ORACLE_OWNER` pins
`boxscore::exact::variance` (`compute`,
`ORACLE_FUNCTIONS`); a host binds an adapter over those pure functions and
passes it as `variance_oracle(actuals, budgets)`. The oracle receives
exactly `ORACLE_ROW_KEYS` rows (`account_code`, `account_name`, `category`,
`amount` — decimal strings, sorted deterministically) covering only the
accounts that have **both** actual and budget rows; its output is validated
against the closed `BY_ACCOUNT_KEYS`/`NOI_BRIDGE_KEYS` shapes (floats and
malformed contracts refuse `INVALID_CONTRACT`; owner internals never
surface). `variance.input_sha256` pins the exact oracle inputs so every
number is reproducible.

Without an oracle the review still returns occupancy and exceptions, with a
`VARIANCE_NOT_IMPLEMENTED` blocker — never a fabricated variance.

`ops-review/2.0.0` reads both the legacy snapshot schema and the new exact
schema (`application_id=0x504c4154`, version 1). Exact GL rows come from the
latest revision of the requested property and period. INTEGER cents are
formatted directly as decimal strings; they never pass through float. Imported
categories do not imply approved mappings, so exact imports retain
`UNREVIEWED_ACCOUNT_MAPPING` exceptions until the human review gate is built.

Legacy REAL snapshots remain a compatibility read path. Their values cross once
through shortest round-trip decimal text, and the exact producer refuses
subcent input. Use the reviewed copy migration in
[the operating producer](https://github.com/paintbrushv/plat-operations/blob/main/docs/EXACT_CENTS.md)
for the released exact workflow. The old database and issued bodies are retained.

Bind `platworks.ops_oracle.compute` through `--variance-module platworks.ops_oracle`
for the CLI, or `variance_oracle=platworks.ops_oracle.variance_oracle` in Python.
The host must install and configure `boxscore-exact`. The adapter propagates typed
producer failures; it cannot approve signs, repair precision, or fabricate a
fallback. The umbrella records the binary and protocol input hashes in its
provenance. This changes the owner from legacy `boxscore::variance` to
`boxscore::exact::variance`; historical version-1 evidence is unchanged.

Every money result remains a record `{amount, currency, unit: 'usd', period,
source, source_truncated}`. Only USD is supported (`OPS_CURRENCIES`).

## Missing is not zero

- **Missing budget ≠ zero budget.** Accounts with actuals but no budget rows
  are excluded from variance and reported as a `MISSING_BUDGET` blocker; an
  explicitly stated `0.00` budget row is a stated zero and produces variance.
- **Budget without actual** is flagged (`BUDGET_WITHOUT_ACTUAL`), never
  treated as zero actuals. Zero actual rows overall blocks the review
  (`MISSING_ACTUALS`).
- An absent/zero/implausible unit count is `mismatch_status: 'unknown'`
  (`OCCUPANCY_UNIT_COUNT_UNKNOWN`), never a silent match; a missing prior
  snapshot leaves `occupancy.change` `None` (`OCCUPANCY_CHANGE_UNKNOWN`), not
  a zero change.
- Feed freshness cannot be assessed without an explicit `as_of_date`
  (`FEED_FRESHNESS_UNKNOWN`), never assumed current.

## Typed exceptions (closed `EXCEPTION_CODES`)

| Code | Severity | Meaning |
|---|---|---|
| `MISSING_ACTUALS` | blocker | no GL actual rows for the period |
| `MISSING_BUDGET` | blocker | accounts with actuals but no budget rows |
| `VARIANCE_NOT_IMPLEMENTED` | blocker | no deterministic oracle bound |
| `OCCUPANCY_COUNT_MISMATCH` | blocker | snapshot denominator ≠ stated unit count |
| `DUPLICATE_GL_IMPORT` | material | same source row imported more than once |
| `CHANGED_ACCOUNT_MAPPING` | material | GL category ≠ reviewed mapping category |
| `UNREVIEWED_ACCOUNT_MAPPING` | material | absent / unapproved / conflicting mapping |
| `MATERIAL_VARIANCE` | material | \|variance\| ≥ configured threshold |
| `BUDGET_WITHOUT_ACTUAL` | flag | budget rows with no actuals |
| `OCCUPANCY_SNAPSHOT_MISSING` | flag | no snapshot at/before the review bound |
| `OCCUPANCY_UNIT_COUNT_UNKNOWN` | flag | unit count absent or not credible |
| `OCCUPANCY_CHANGE_UNKNOWN` | flag | no prior snapshot to compare |
| `FEED_STALE` | flag | snapshot older than `stale_after_days` |
| `FEED_FRESHNESS_UNKNOWN` | flag | no explicit review as-of date |

Duplicate GL imports are deduplicated **before** the oracle using the
natural key (kind, source_file, source_row, account_code) with
first-import-wins by `(created_at, id)`, and every duplicate group is
disclosed with copy count and amount-drift flag — deduplication never
silently double-counts or drops money.

Account mappings reuse the frozen boxscore scoping (property scope, `*`,
empty or NULL); a property-scoped row takes precedence over a wildcard row
for the same account, and absent, unapproved or conflicting mapping states
are material exceptions, never assumptions.

## The LLM cannot explain away a mismatch

When any blocker exists — in particular a snapshot count mismatch —
`llm_explanation_ineligible` is `True` and the mismatch is a blocker.
`explanation` separates measured facts (deterministic statements, each with
evidence citations) from `hypotheses`, which this module never generates:
model prose is not evidence and cannot clear blockers.

## Read-only backend protocol

`SqliteOpsBackend` opens `file:...?mode=ro` URI connections per query and
never writes; the source database hash is unchanged by any review. It refuses
symlinked database paths (`UNSAFE_PATH`), missing files (`NOT_FOUND`) and WAL
sidecars (`SNAPSHOT_REQUIRED` — a quiesced snapshot is required, mirroring
the frozen tool-loop adapter), and any unsupported/missing schema table
refuses `INVALID_CONTRACT`.

An alternative backend must implement `artifact` (stable citation identity),
`resolve_property`, `unit_count`, `gl_rows`, `occupancy_snapshots`,
`account_mappings`. Row shapes are closed contracts (`GL_ROW_KEYS`,
`SNAPSHOT_KEYS`, `MAPPING_KEYS`): a backend that attaches resident details
(payee, remarks, names) refuses `INVALID_CONTRACT` instead of leaking them.
The review never queries `gl_transactions`; `search_transactions` remains an
unwired catalog contract.

## What this slice does NOT do

- No writes anywhere; no certification (`ops-review` is not an approval or
  certification contract).
- No CSV-backed review yet (the frozen `boxscore.load_occupancy` CSV path
  cannot provide prior-period history or unit counts; using it would silently
  downgrade change and mismatch to unknown — a future seam needs its own
  review).
- No percentage materiality thresholds (would introduce rate math and
  zero-denominator risk); `variance_abs` only.
- No live/vendor compatibility claims: coverage is the synthetic SQLite
  fixture contract above; real-feed compatibility remains blocked pending
  independent real-source evidence.
Exact evidence uses `exact_gl` and `exact_snapshots` table names. A GL artifact
locator carries revision ID, actual/budget kind, and input hash; its displayed
row number is `exact_gl.ordinal + 1`. Every revision supplying current or prior
occupancy is independently hash-verified, including fallback snapshots from an
earlier period. Reading those revisions does not change the requested period's
unit count.
