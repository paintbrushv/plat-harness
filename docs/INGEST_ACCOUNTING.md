# Source monetary observations: `ingest-accounting/1.0.0`

**Task 3.1.** A separately versioned money envelope, not a replacement for
`ingest-observation/2.0.0` occupancy contracts and not a T12 normalizer.
No file reads, model calls, engine execution, approval registry, summing of
rents, NOI, rates or fabricated totals are introduced here.

## API

```python
from plat_harness.ingest.accounting import (
    CONTRACT_VERSION, AccountingObservationError, MONEY_KINDS, KIND_BASIS,
    observe_money, validate_accounting_observations,
    loads_accounting_observations, canonical_bytes, observation_id,
)

observed = observe_money(
    kind='scheduled_charge', measurement_basis='scheduled_charge',
    source_text='$1,450.00', currency='USD', period='month',
    citation=cell_locator, cell_origin='typed',
)
validated = validate_accounting_observations(
    envelope, subject_id=host_subject, as_of=host_date)
encoded = canonical_bytes(envelope, subject_id=host_subject, as_of=host_date)
```

`observe_money` and the validators return JSON-native dictionaries only:
objects, lists, strings, integers, booleans (none are used by a final field)
and null. Python `float`, `Decimal`, nonfinite values, tuples and arbitrary
objects are refused. Validation never fills nulls with zero, derives missing
amounts, sums observations, or upgrades status. Canonical serialization
validates first, sorts object keys, preserves list order, uses compact
separators and UTF-8 with `ensure_ascii=False`, and adds no newline.

Errors are `AccountingObservationError` with static `.code` and message.
Codes: `INVALID_OBSERVATIONS`, `INPUT_LIMIT_EXCEEDED`, `SCOPE_MISMATCH`,
`AMBIGUOUS_NUMERIC_FORMAT`, `FORMULA_CACHE_NOT_ORIGINAL`,
`MIXED_MEASUREMENT_BASIS`, `UNKNOWN_AMOUNT_NOT_ZERO`. No input values, paths
or chained decoder exceptions are retained in `.args`, `.__cause__`, or
`.__context__`.

There is no `sum`, `total`, `aggregate`, `noi`, `cap_rate` or `dscr` helper.

## Trust boundary

This contract establishes **syntax, internal consistency and declared
source/scope binding**, not source truth, locator existence, account mapping
approval or underwriting authority. Callers supply host-owned opaque IDs and
deterministically extracted locators. Passing validation does not authenticate
a workbook cell or certify economics.

Money, if present, is a **finite decimal string** with explicit currency, unit
and period plus a source locator. Floats are forbidden. Occupancy v2 envelopes
remain money-free; this module is the extension contemplated by
`docs/INGEST_CONTRACT.md`.

## Exact envelope

Every field is required unless its value is explicitly nullable. Every object
rejects extra keys.

| Field | Shape / meaning |
|---|---|
| `contract_version` | Exactly `ingest-accounting/1.0.0` |
| `subject_id` | Host-owned token `[A-Za-z0-9][A-Za-z0-9_-]{0,63}`, or null |
| `as_of` | Real calendar date `YYYY-MM-DD`, or null |
| `adapter` | Exactly `{id, version}` with the same patterns as occupancy v2 |
| `sources` | Nonempty list of at most 128 source records (same shape as occupancy v2) |
| `observations` | List of money observations |
| `issues` | List of static, optionally position-cited issues |
| `status` | `blocked` if any amount is null, otherwise `observed_unvalidated` |

Host keyword arguments are mandatory and must exactly equal the envelope and
every source record. Null is accepted only when the corresponding host argument
is also null. Extra envelope keys such as `totals`, `noi` or `approved` refuse.

### Money observations

Each observation is exactly:

```text
{observation_id, source_id, kind, measurement_basis, amount, cell_origin, citation}
```

`kind` is one of:

```text
unit_rent  charge_rent  report_total  deposit  concession
arrears  scheduled_charge  collected_cash
```

These kinds are disjoint. Unit rent, charge rent and a report total may coexist
as separate cited observations; they are never added together. Deposits,
concessions, arrears, scheduled charges and collected cash are likewise
distinct and are not aliases for one another.

`measurement_basis` must equal the kind's fixed basis (`unit`, `charge`,
`report`, `deposit`, `concession`, `arrears`, `scheduled_charge`,
`collected_cash`). A mixed or swapped basis refuses with
`MIXED_MEASUREMENT_BASIS`.

`observation_id = "mny_" + SHA256(canonical JSON of citation)`. IDs are unique
in the envelope.

`cell_origin` must be `typed`. Formula results, formula caches, `data_only`
cached values and similar origins refuse with `FORMULA_CACHE_NOT_ORIGINAL`.
A cached formula result cannot be certified as an original typed cell.

`citation` is the occupancy v2 locator:

```text
{source_id, source_sha256, sheet, row, row_end, column}
{source_id, source_sha256, page, bounds}
```

Sheet/row/column values are physical one-based integers, **not booleans**.
Citations bind to a declared original source digest.

### Amounts

`amount` is null when the source amount is missing or unknown, or exactly:

```text
{decimal, source_text, currency, unit, period}
```

- `source_text` is the exact original numeric text, including currency symbols
  or grouping that survived unambiguous parse. It is not rewritten.
- `decimal` is a finite decimal **string** matching
  `^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,8})?$`. It is not a Python float.
- `currency` is a three-letter ISO-like code (`USD`, `EUR`, `GBP`). A leading
  `$` / `€` / `£` or code in `source_text` must match.
- `unit` is exactly `currency`. Percents, basis points and ratios refuse; this
  module does not source rates.
- `period` is `month`, `year`, `day`, `one_time` or `as_of`.

Blank, `n/a`, `unknown`, a lone hyphen/dash and similar tokens are unknown.
Unknown is **not** coerced to zero (`UNKNOWN_AMOUNT_NOT_ZERO`). An explicit
typed `0` / `0.00` remains a cited zero only when that text was actually typed.

### Unambiguous numeric text

Grouping is accepted only when decimal versus thousands separators are
unambiguous:

- No separator: integer (`1450`).
- One period with 1–2 or 4+ fractional digits: decimal (`1450.50`, `1450.5`).
- One comma with 1–2 fractional digits: decimal (`1450,50`).
- Both separators: the last is decimal, the other is thousands in groups of
  three (`1,450.00`, `1.450,00`).
- Parentheses or a leading minus denote a negative (`(50.00)` → `-50.00`).

A single separator with exactly three digits on the right (`1.234`, `1,234`),
space grouping, mixed or incomplete thousands groups, scientific notation,
hex, `NaN` / `Infinity` and non-string values refuse. Ambiguous cases use
`AMBIGUOUS_NUMERIC_FORMAT`.

## What this module does not do

- No summing of unit rent, charge rent and report totals.
- No NOI, cap rate, DSCR, occupancy rate or other financial math.
- No account-mapping fork of ledger/lease-charge adapters; charge-code
  classification remains in those adapters.
- No promotion of Excel formula caches to typed source evidence.
- No engine inputs, approval flags or occupancy four-counts.

Synthetic tests in `tests/test_accounting_observations.py` construct in-memory
dictionaries only. No live or private deal files are read.
