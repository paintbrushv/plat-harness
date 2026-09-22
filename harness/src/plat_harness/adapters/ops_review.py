"""Read-only operations period review (Task 3.5). See docs/OPS_REVIEW.md.

A read-only, single-(property, period) operations review over an ops SQLite
backend: actual-vs-budget variance (only through a bound deterministic oracle
owned by the ops repo), occupancy change, and typed material exceptions with
traceable evidence. The module performs zero writes and zero variance
arithmetic: every financial number is either read from the backend and
converted once to a decimal string at the f64 boundary, or produced by the
bound oracle (pinned to ``boxscore::variance`` pure functions) and passed
through verbatim inside a money record.

Fail-closed contract rules:

- Missing budget is not zero budget: accounts with actuals but no budget rows
  are excluded from variance and reported as a blocker; an explicit ``0.00``
  budget row is a stated zero and produces variance.
- One property, one period: cross-property or cross-period aggregation is
  refused (``SCOPE_MISMATCH``); wildcard/multi-asset/multi-period requests
  refuse ``INVALID_INPUT``.
- Duplicate GL imports are deduplicated before the oracle (first import wins
  by (created_at, id)) and disclosed loudly, with amount drift flagged.
- Stale feeds, unreviewed/changed account mappings, snapshot/unit-count
  mismatches and unknowns become typed exceptions — never silent zeros.
- A snapshot count mismatch is a blocker that makes the review ineligible
  for LLM explanation; measured facts and hypotheses stay separated and no
  hypotheses are generated here.
- Resident transaction details (payee, remarks, names) are never read; row
  shapes are closed so hostile extra fields refuse instead of leaking.

Refusals are typed ``HarnessError`` with static messages and are raised
outside ``except`` blocks (via marker returns) so exception chains stay clean.
"""
from __future__ import annotations

import calendar
import hashlib
import json
import math
import re
import sqlite3
from datetime import date
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Callable, NoReturn

from plat_harness.errors import HarnessError
from plat_harness.occupancy import require_occupancy_counts

CONTRACT_VERSION = 'ops-review/1.0.0'
ORACLE_OWNER = 'boxscore::variance'
ORACLE_FUNCTIONS = ('compute_account_variances', 'compute_noi_bridge')
STALE_AFTER_DAYS = 45
MAX_GL_ROWS = 100_000
MAX_SNAPSHOTS = 1_000
MAX_EVIDENCE_PER_EXCEPTION = 50
MAX_FACTS = 100
OPS_CURRENCIES = frozenset({'USD'})
SEVERITIES = frozenset({'blocker', 'material', 'flag'})

ERROR_CODES = frozenset({
    'NOT_FOUND', 'AMBIGUOUS_PROPERTY', 'INVALID_INPUT', 'INVALID_CONTRACT',
    'SCOPE_MISMATCH', 'UNSAFE_PATH', 'SNAPSHOT_REQUIRED', 'INPUT_LIMIT_EXCEEDED',
    'NOT_IMPLEMENTED',
})

EXCEPTION_CODES = frozenset({
    'MISSING_ACTUALS', 'MISSING_BUDGET', 'VARIANCE_NOT_IMPLEMENTED',
    'OCCUPANCY_COUNT_MISMATCH', 'OCCUPANCY_UNIT_COUNT_UNKNOWN',
    'OCCUPANCY_CHANGE_UNKNOWN', 'OCCUPANCY_SNAPSHOT_MISSING',
    'FEED_STALE', 'FEED_FRESHNESS_UNKNOWN', 'DUPLICATE_GL_IMPORT',
    'CHANGED_ACCOUNT_MAPPING', 'UNREVIEWED_ACCOUNT_MAPPING',
    'MATERIAL_VARIANCE', 'BUDGET_WITHOUT_ACTUAL',
})

EXCEPTION_MESSAGES = {
    'MISSING_ACTUALS': 'No GL actual rows for the requested period; missing actuals are not zero.',
    'MISSING_BUDGET': 'Accounts have actuals but no budget rows; missing budget is not zero budget.',
    'VARIANCE_NOT_IMPLEMENTED': 'No deterministic variance oracle is bound; variance is not fabricated.',
    'OCCUPANCY_COUNT_MISMATCH': 'Snapshot count does not match the stated property unit count.',
    'OCCUPANCY_UNIT_COUNT_UNKNOWN': 'Property unit count is absent or not credible; mismatch cannot be assessed.',
    'OCCUPANCY_CHANGE_UNKNOWN': 'No prior occupancy snapshot exists; change is unknown, not zero.',
    'OCCUPANCY_SNAPSHOT_MISSING': 'No occupancy snapshot at or before the review bound.',
    'FEED_STALE': 'The occupancy snapshot feed is older than the configured staleness bound.',
    'FEED_FRESHNESS_UNKNOWN': 'Feed freshness cannot be assessed without an explicit review as-of date.',
    'DUPLICATE_GL_IMPORT': 'The same GL source row was imported more than once; duplicates were deduplicated before variance and are disclosed here.',
    'CHANGED_ACCOUNT_MAPPING': 'A GL account category differs from its reviewed mapping.',
    'UNREVIEWED_ACCOUNT_MAPPING': 'A GL account has no single approved account mapping.',
    'MATERIAL_VARIANCE': 'Actual-versus-budget variance meets the configured materiality threshold.',
    'BUDGET_WITHOUT_ACTUAL': 'Accounts have budget rows but no actual rows; missing actuals are not zero.',
}

RESULT_KEYS = tuple(sorted({
    'contract_version', 'asset_id', 'property_key', 'period', 'as_of_date',
    'status', 'scope', 'counts', 'feeds', 'occupancy', 'variance',
    'material_exceptions', 'exceptions', 'blockers', 'llm_explanation_ineligible',
    'explanation',
}))
COUNTS_KEYS = frozenset({
    'actual_rows', 'budget_rows', 'distinct_accounts', 'duplicate_groups',
    'duplicate_rows',
})
SCOPE_KEYS = frozenset({
    'asset_id', 'property_key', 'period', 'currency', 'properties_reviewed',
    'periods_reviewed',
})
FEEDS_KEYS = frozenset({'snapshot_as_of', 'freshness', 'age_days', 'stale_after_days'})
OCCUPANCY_KEYS = frozenset({'current', 'prior', 'change', 'unit_count', 'mismatch_status'})
SNAPSHOT_PAYLOAD_KEYS = frozenset({
    'occupied', 'vacant', 'down', 'denominator', 'rate', 'as_of', 'source',
})
VARIANCE_KEYS = frozenset({
    'status', 'oracle_owner', 'oracle_functions', 'covered_accounts',
    'excluded_accounts', 'by_account', 'noi_bridge', 'materiality', 'input_sha256',
})
BY_ACCOUNT_KEYS = frozenset({
    'account_code', 'account_name', 'category', 'actual', 'budget', 'variance',
})
NOI_BRIDGE_KEYS = frozenset({
    'actual_revenue', 'budget_revenue', 'revenue_variance',
    'actual_expenses', 'budget_expenses', 'expense_variance',
    'actual_noi', 'budget_noi', 'noi_variance', 'unmapped_actual', 'unmapped_budget',
})
MONEY_KEYS = frozenset({'amount', 'currency', 'unit', 'period', 'source',
                        'source_truncated'})
CITATION_KEYS = frozenset({'artifact', 'row', 'table', 'period'})
EXCEPTION_KEYS = frozenset({'code', 'severity', 'message', 'details', 'evidence'})
EXPLANATION_KEYS = frozenset({'measured_facts', 'hypotheses', 'note'})
GL_ROW_KEYS = frozenset({
    'property', 'period', 'kind', 'account_code', 'account_name', 'category',
    'amount', 'source_file', 'source_row', 'created_at', 'id',
})
ORACLE_ROW_KEYS = frozenset({'account_code', 'account_name', 'category', 'amount'})
SNAPSHOT_KEYS = frozenset({
    'as_of_date', 'occupied', 'vacant', 'down', 'source_file', 'source_row',
    'created_at', 'id',
})
MAPPING_KEYS = frozenset({
    'account_code', 'account_name', 'noi_category', 'status', 'property_scope',
})
SNAPSHOT_TABLE = 'rent_roll_snapshots'
MAPPING_TABLE = 'account_mappings'
GL_TABLES = {'actual': 'gl_actuals', 'budget': 'gl_budgets'}
HYPOTHESES_NOTE = ('No hypotheses are generated by this deterministic review; '
                   'model prose is not evidence and cannot clear blockers.')

_ASSET = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,119}\Z')
_PERIOD = re.compile(r'[0-9]{4}-[0-9]{2}\Z')
_DAY = re.compile(r'[0-9]{4}-[0-9]{2}-[0-9]{2}\Z')
_DECIMAL = re.compile(r'-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,8})?\Z')
_ACCOUNT_CODE = re.compile(r'[!-~]{1,64}\Z')
_BACKEND_METHODS = ('resolve_property', 'unit_count', 'gl_rows',
                   'occupancy_snapshots', 'account_mappings')


def _refuse(code: str, message: str, **details: Any) -> NoReturn:
    raise HarnessError(code, message, details=details)


def _attempt(fn: Callable[[], Any]) -> tuple[Any, Any]:
    """Marker pattern: run fn, return (value, None) or (None, error).

    Refusals are always raised outside an active ``except`` block so
    ``__cause__`` and ``__context__`` stay None.
    """
    try:
        return fn(), None
    except BaseException as exc:  # noqa: BLE001 - converted to typed refusals
        return None, exc


def _fail(code: str, message: str, **details: Any) -> NoReturn:
    _refuse(code, message, **details)


# ------------------------------------------------------------ input validation

_AGGREGATE_TOKENS = frozenset({'all', '*', 'portfolio', 'total', 'all_properties'})


def _validate_asset(asset_id: Any) -> str:
    if not isinstance(asset_id, str) or not _ASSET.fullmatch(asset_id):
        _fail('INVALID_INPUT', 'A single explicit asset identifier is required.')
    if asset_id.strip().lower() in _AGGREGATE_TOKENS:
        _fail('INVALID_INPUT',
              'Aggregate asset tokens are refused; review exactly one property.')
    return asset_id


def _validate_period(period: Any) -> tuple[str, int, int]:
    if not isinstance(period, str) or not _PERIOD.fullmatch(period):
        _fail('INVALID_INPUT', 'Exactly one period in YYYY-MM form is required.')
    year, month = int(period[:4]), int(period[5:7])
    if not 1 <= month <= 12:
        _fail('INVALID_INPUT', 'Exactly one period in YYYY-MM form is required.')
    return period, year, month


def _validate_day(value: Any, *, code: str = 'INVALID_INPUT',
                  message: str = 'A calendar date in YYYY-MM-DD form is required.') -> date:
    if not isinstance(value, str) or not _DAY.fullmatch(value):
        _fail(code, message)
    parsed, err = _attempt(lambda: date.fromisoformat(value))
    if err is not None or parsed.isoformat() != value:
        _fail(code, message)
    return parsed


def _validate_materiality(materiality: Any) -> dict:
    if not isinstance(materiality, dict):
        _fail('INVALID_INPUT', 'A materiality policy object is required.')
    allowed = {'variance_abs', 'currency', 'stale_after_days'}
    if not set(materiality) <= allowed or 'variance_abs' not in materiality \
            or 'currency' not in materiality:
        _fail('INVALID_INPUT', 'Materiality requires exactly variance_abs and currency '
                                'plus an optional stale_after_days bound.')
    amount = materiality['variance_abs']
    if not isinstance(amount, str) or not _DECIMAL.fullmatch(amount):
        _fail('INVALID_INPUT', 'variance_abs must be a positive decimal string.')
    threshold, err = _attempt(lambda: Decimal(amount))
    if err is not None or threshold <= 0:
        _fail('INVALID_INPUT', 'variance_abs must be a positive decimal string.')
    currency = materiality['currency']
    if not isinstance(currency, str) or currency not in OPS_CURRENCIES:
        _fail('INVALID_INPUT', 'currency must be one of the supported ops currencies.')
    stale_after_days = materiality.get('stale_after_days', STALE_AFTER_DAYS)
    if not isinstance(stale_after_days, int) or isinstance(stale_after_days, bool) \
            or not 1 <= stale_after_days <= 365:
        _fail('INVALID_INPUT', 'stale_after_days must be an integer between 1 and 365.')
    return {'variance_abs': amount, 'currency': currency,
            'stale_after_days': stale_after_days}


def _amount(value: Any) -> str:
    """Convert one backend amount to a decimal string; refuse ambiguous input.

    The ops backend stores money as SQLite REAL (f64 — flagged, not mixed with
    Decimal underwriting money). Conversion happens once here at the read
    boundary via shortest round-trip repr; no float ever enters the result
    contract or the oracle input.
    """
    if isinstance(value, bool):
        _fail('INVALID_INPUT', 'GL amounts must be decimal-string compatible.')
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, float):
        if not math.isfinite(value):
            _fail('INVALID_INPUT', 'GL amounts must be finite.')
        parsed, err = _attempt(lambda: Decimal(repr(value)))
        if err is not None:
            _fail('INVALID_INPUT', 'GL amounts must be decimal-string compatible.')
        if parsed and (parsed.adjusted() > 15 or parsed.adjusted() < -15 and parsed != 0):
            _fail('INPUT_LIMIT_EXCEEDED', 'GL amount magnitude exceeds the supported bound.')
        text = format(parsed, 'f')
    elif isinstance(value, str):
        text = value
        if not _DECIMAL.fullmatch(text):
            _fail('INVALID_INPUT', 'GL amounts must be unambiguous decimal strings.')
    else:
        _fail('INVALID_INPUT', 'GL amounts must be decimal-string compatible.')
    return text


# -------------------------------------------------------------- backend seam

class SqliteOpsBackend:
    """Read-only adapter over the ops owner's SQLite schema.

    Opens ``mode=ro`` URI connections, refuses symlinked databases and WAL
    sidecars (a snapshot is required, mirroring the frozen tool-loop adapter),
    and never writes. Queries mirror the frozen boxscore adapter's scoping.
    """

    def __init__(self, db_path: str | Path):
        path = Path(db_path)
        if path.is_symlink():
            _fail('UNSAFE_PATH', 'The ops database must be a real file, not a symlink.')
        if not path.is_file():
            _fail('NOT_FOUND', 'The ops database file does not exist.')
        if Path(str(path) + '-wal').exists():
            _fail('SNAPSHOT_REQUIRED', 'A WAL sidecar exists; a quiesced snapshot is required.')
        self.path = path
        self.artifact = str(path)

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(f'file:{self.path}?mode=ro', uri=True)

    def _query(self, sql: str, params: tuple) -> list[tuple]:
        def run():
            con = self._connect()
            try:
                return con.execute(sql, params).fetchall()
            finally:
                con.close()
        rows, err = _attempt(run)
        if err is not None:
            _fail('INVALID_CONTRACT',
                  'The ops backend query failed or its schema is unsupported.')
        return rows

    def resolve_property(self, asset_id: str) -> str:
        rows = self._query('SELECT id FROM properties WHERE id = ?', (asset_id,))
        if rows:
            return str(rows[0][0])
        key = asset_id.strip().lower().replace(' ', '_')
        name_rows = self._query(
            'SELECT id FROM properties WHERE lower(name) = lower(?) '
            'OR lower(replace(name, \' \', \'_\')) = ?', (asset_id, key))
        ids: list[str] = []
        for row in name_rows:
            value = str(row[0])
            if value not in ids:
                ids.append(value)
        if len(ids) > 1:
            _fail('AMBIGUOUS_PROPERTY',
                  'The asset identifier matches more than one property; '
                  'cross-property review is refused.')
        if not ids:
            _fail('NOT_FOUND', 'No property matches the asset identifier.')
        return ids[0]

    def unit_count(self, property_key: str) -> int | None:
        rows = self._query('SELECT unit_count FROM properties WHERE id = ?',
                           (property_key,))
        return int(rows[0][0]) if rows else None

    def gl_rows(self, property_key: str, period: str) -> list[dict]:
        rows: list[dict] = []
        for kind, table in sorted(GL_TABLES.items()):
            for row in self._query(
                f'SELECT a.account_code, a.account_name, a.category, a.amount, '
                f'a.source_file, a.source_row, a.created_at, a.id FROM {table} a '
                f'JOIN periods p ON p.id = a.period_id '
                f'WHERE a.property_id = ? AND p.label = ? '
                f'ORDER BY a.account_code, a.source_file, a.source_row, a.id',
                    (property_key, period)):
                rows.append({
                    'property': property_key, 'period': period, 'kind': kind,
                    'account_code': row[0], 'account_name': row[1],
                    'category': row[2], 'amount': row[3], 'source_file': row[4],
                    'source_row': row[5], 'created_at': row[6], 'id': row[7],
                })
        return rows

    def occupancy_snapshots(self, property_key: str, bound_date: str) -> list[dict]:
        return [
            {'as_of_date': row[0], 'occupied': row[1], 'vacant': row[2],
             'down': row[3], 'source_file': row[4], 'source_row': row[5],
             'created_at': row[6], 'id': row[7]}
            for row in self._query(
                'SELECT as_of_date, occupied_units, vacant_units, down_units, '
                'source_file, source_row, created_at, id FROM rent_roll_snapshots '
                'WHERE property_id = ? AND as_of_date <= ? '
                'ORDER BY as_of_date DESC, created_at DESC, id DESC LIMIT ?',
                (property_key, bound_date, MAX_SNAPSHOTS))
        ]

    def account_mappings(self, property_key: str) -> list[dict]:
        return [
            {'account_code': row[0], 'account_name': row[1], 'noi_category': row[2],
             'status': row[3], 'property_scope': row[4]}
            for row in self._query(
                'SELECT account_code, account_name, noi_category, status, property_scope '
                'FROM account_mappings WHERE lower(property_scope) = ? '
                "OR property_scope = '*' OR property_scope = '' "
                'OR property_scope IS NULL ORDER BY account_code, source_system',
                (property_key,))
        ]


def _resolve_backend(db_path: Any, backend: Any) -> Any:
    if db_path is not None and backend is not None:
        _fail('INVALID_INPUT', 'Provide exactly one ops backend binding.')
    if db_path is None and backend is None:
        _fail('INVALID_INPUT', 'An ops backend binding (db_path or backend) is required.')
    if db_path is not None:
        if not isinstance(db_path, (str, Path)):
            _fail('INVALID_INPUT', 'db_path must be a filesystem path.')
        return SqliteOpsBackend(db_path)
    for name in _BACKEND_METHODS + ('artifact',):
        if not hasattr(backend, name):
            _fail('INVALID_CONTRACT', 'The backend does not implement the ops review protocol.')
    return backend


# -------------------------------------------------------- row validation

def _require_dict(value: Any, keys: frozenset, code: str, message: str) -> dict:
    if not isinstance(value, dict) or set(value) != keys:
        _fail(code, message)
    return value


def _text(value: Any, limit: int, code: str, message: str) -> str:
    if not isinstance(value, str) or not value or len(value) > limit:
        _fail(code, message)
    return value


def _integer(value: Any, code: str, message: str, *, low: int = 0) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < low:
        _fail(code, message)
    return value


def _validate_gl_rows(rows: Any, property_key: str, period: str) -> list[dict]:
    if not isinstance(rows, list):
        _fail('INVALID_CONTRACT', 'The backend must return a list of GL rows.')
    if len(rows) > MAX_GL_ROWS:
        _fail('INPUT_LIMIT_EXCEEDED', 'GL row count exceeds the supported bound.')
    validated = []
    for row in rows:
        _require_dict(row, GL_ROW_KEYS, 'INVALID_CONTRACT',
                      'The backend must return rows with exactly the GL row contract.')
        if row['property'] != property_key:
            _fail('SCOPE_MISMATCH', 'A GL row belongs to a different property; '
                                     'cross-property aggregation is refused.')
        if row['period'] != period:
            _fail('SCOPE_MISMATCH', 'A GL row belongs to a different period; '
                                     'cross-period aggregation is refused.')
        if row['kind'] not in GL_TABLES:
            _fail('INVALID_CONTRACT', 'GL row kind must be actual or budget.')
        validated.append({
            'kind': row['kind'],
            'account_code': _text(row['account_code'], 64, 'INVALID_CONTRACT',
                                  'Account codes must be non-empty strings.'),
            'account_name': _text(row['account_name'], 128, 'INVALID_CONTRACT',
                                  'Account names must be non-empty strings.'),
            'category': _text(row['category'], 128, 'INVALID_CONTRACT',
                              'Categories must be non-empty strings.'),
            'amount': _amount(row['amount']),
            'source_file': _text(row['source_file'], 256, 'INVALID_CONTRACT',
                                 'Source files must be non-empty strings.'),
            'source_row': _integer(row['source_row'], 'INVALID_CONTRACT',
                                   'Source rows must be non-negative integers.'),
            'created_at': _text(row['created_at'], 64, 'INVALID_CONTRACT',
                               'Row timestamps must be non-empty strings.'),
            'id': _text(row['id'], 128, 'INVALID_CONTRACT',
                        'Row ids must be non-empty strings.'),
        })
    return validated


def _dedupe(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """Deduplicate re-imported GL rows by (kind, source_file, source_row, account).

    First import wins by (created_at, id); every duplicate group is disclosed.
    """
    groups: dict[tuple, list[dict]] = {}
    for row in rows:
        key = (row['kind'], row['source_file'], row['source_row'], row['account_code'])
        groups.setdefault(key, []).append(row)
    deduped: list[dict] = []
    duplicates: list[dict] = []
    for key in sorted(groups):
        members = sorted(groups[key], key=lambda r: (r['created_at'], r['id']))
        kept = members[0]
        deduped.append(kept)
        if len(members) > 1:
            duplicates.append({'group': key, 'kept': kept, 'copies': len(members),
                               'amounts_differ':
                                   len({m['amount'] for m in members}) > 1})
    return deduped, duplicates


def _validate_snapshots(rows: Any, bound_date: date) -> list[dict]:
    if not isinstance(rows, list):
        _fail('INVALID_CONTRACT', 'The backend must return a list of snapshots.')
    if len(rows) > MAX_SNAPSHOTS:
        _fail('INPUT_LIMIT_EXCEEDED', 'Snapshot count exceeds the supported bound.')
    validated = []
    for row in rows:
        _require_dict(row, SNAPSHOT_KEYS, 'INVALID_CONTRACT',
                      'The backend must return rows with exactly the snapshot contract.')
        as_of = _validate_day(row['as_of_date'], message='Snapshot dates must be calendar dates.')
        if as_of > bound_date:
            _fail('INVALID_INPUT', 'A snapshot is dated after the review bound.')
        validated.append({
            'as_of_date': as_of,
            'occupied': _integer(row['occupied'], 'INVALID_CONTRACT',
                                 'Snapshot counts must be non-negative integers.'),
            'vacant': _integer(row['vacant'], 'INVALID_CONTRACT',
                               'Snapshot counts must be non-negative integers.'),
            'down': _integer(row['down'], 'INVALID_CONTRACT',
                             'Snapshot counts must be non-negative integers.'),
            'source_file': _text(row['source_file'], 256, 'INVALID_CONTRACT',
                                 'Source files must be non-empty strings.'),
            'source_row': _integer(row['source_row'], 'INVALID_CONTRACT',
                                   'Source rows must be non-negative integers.'),
            'created_at': _text(row['created_at'], 64, 'INVALID_CONTRACT',
                               'Row timestamps must be non-empty strings.'),
            'id': _text(row['id'], 128, 'INVALID_CONTRACT',
                        'Row ids must be non-empty strings.'),
        })
    validated.sort(key=lambda r: (r['as_of_date'], r['created_at'], r['id']),
                   reverse=True)
    return validated


def _validate_mappings(rows: Any, property_key: str) -> list[dict]:
    if not isinstance(rows, list):
        _fail('INVALID_CONTRACT', 'The backend must return a list of mappings.')
    validated = []
    for row in rows:
        _require_dict(row, MAPPING_KEYS, 'INVALID_CONTRACT',
                      'The backend must return rows with exactly the mapping contract.')
        validated.append({
            'account_code': _text(row['account_code'], 64, 'INVALID_CONTRACT',
                                  'Account codes must be non-empty strings.'),
            'account_name': _text(row['account_name'], 128, 'INVALID_CONTRACT',
                                  'Account names must be non-empty strings.'),
            'noi_category': _text(row['noi_category'], 128, 'INVALID_CONTRACT',
                                  'Mapped categories must be non-empty strings.'),
            'status': _text(row['status'], 64, 'INVALID_CONTRACT',
                            'Mapping statuses must be non-empty strings.'),
            'property_scope': row['property_scope'],
        })
    # Property-scoped rows take precedence over wildcard scopes for the same account.
    property_scoped = {r['account_code'] for r in validated
                       if isinstance(r['property_scope'], str)
                       and r['property_scope'].strip().lower() == property_key}
    return [r for r in validated
            if (isinstance(r['property_scope'], str)
                and r['property_scope'].strip().lower() == property_key)
            or r['account_code'] not in property_scoped]


# ----------------------------------------------------------- evidence helpers

def _citation(source_file: str, source_row: int, table: str, period: str) -> dict:
    return {'artifact': source_file, 'row': source_row, 'table': table, 'period': period}


def _citations_for(rows: list[dict], period: str) -> list[dict]:
    return [_citation(r['source_file'], r['source_row'], GL_TABLES[r['kind']],
                      period) for r in rows]


def _capped(rows: list[dict], period: str) -> tuple[list[dict], bool]:
    return (_citations_for(rows[:MAX_EVIDENCE_PER_EXCEPTION], period),
            len(rows) > MAX_EVIDENCE_PER_EXCEPTION)


def _money(amount: str, currency: str, period: str, rows: list[dict]) -> dict:
    source, truncated = _capped(sorted(
        rows, key=lambda r: (r['kind'], r['source_file'], r['source_row'], r['id'])),
        period)
    return {'amount': amount, 'currency': currency, 'unit': 'usd',
            'period': period, 'source': source, 'source_truncated': truncated}


def _exception(code: str, severity: str, *, details: dict,
               evidence: list[dict]) -> dict:
    return {'code': code, 'severity': severity, 'message': EXCEPTION_MESSAGES[code],
            'details': details, 'evidence': evidence[:MAX_EVIDENCE_PER_EXCEPTION]}


def _occupancy_payload(snapshot: dict, counts: Any) -> dict:
    return {
        'occupied': counts.occupied, 'vacant': counts.vacant, 'down': counts.down,
        'denominator': counts.denominator, 'rate': counts.as_dict()['rate'],
        'as_of': snapshot['as_of_date'].isoformat(),
        'source': [_citation(snapshot['source_file'], snapshot['source_row'],
                             SNAPSHOT_TABLE, snapshot['as_of_date'].isoformat())],
    }


def _counts_of(snapshot: dict) -> Any:
    def run():
        return require_occupancy_counts(
            occupied=snapshot['occupied'], vacant=snapshot['vacant'],
            down=snapshot['down'],
            denominator=snapshot['occupied'] + snapshot['vacant'] + snapshot['down'])
    counts, err = _attempt(run)
    if err is not None:
        if isinstance(err, HarnessError):
            _fail(err.code, err.message, **err.details)
        _fail('INVALID_INPUT', 'Occupancy snapshot counts are not valid.')
    return counts


# ----------------------------------------------------------- oracle validation

def _oracle_rows(rows: list[dict], kind: str, accounts: set[str]) -> list[dict]:
    selected = [r for r in rows if r['kind'] == kind and r['account_code'] in accounts]
    return [{'account_code': r['account_code'], 'account_name': r['account_name'],
             'category': r['category'], 'amount': r['amount']}
            for r in sorted(selected, key=lambda r: (
                r['account_code'], r['account_name'], r['category'], r['amount'],
                r['source_file'], r['source_row'], r['id']))]


def _oracle_money(value: Any) -> str:
    if not isinstance(value, str) or not _DECIMAL.fullmatch(value):
        _fail('INVALID_CONTRACT',
              'The bound variance oracle returned an unsupported contract.')
    return value


def _run_oracle(oracle: Any, actuals: list[dict], budgets: list[dict],
                covered: set[str]) -> tuple[list[dict], dict]:
    def run():
        return oracle([dict(r) for r in actuals], [dict(r) for r in budgets])
    output, err = _attempt(run)
    if err is not None or not isinstance(output, dict) \
            or set(output) != {'by_account', 'noi_bridge'}:
        _fail('INVALID_CONTRACT',
              'The bound variance oracle failed or returned an unsupported contract.')
    by_rows = output['by_account']
    if not isinstance(by_rows, list):
        _fail('INVALID_CONTRACT',
              'The bound variance oracle returned an unsupported contract.')
    by_account = []
    for row in by_rows:
        _require_dict(row, BY_ACCOUNT_KEYS, 'INVALID_CONTRACT',
                      'The bound variance oracle returned an unsupported contract.')
        by_account.append({
            'account_code': _text(row['account_code'], 64, 'INVALID_CONTRACT',
                                  'The oracle returned an unsupported contract.'),
            'account_name': _text(row['account_name'], 128, 'INVALID_CONTRACT',
                                  'The oracle returned an unsupported contract.'),
            'category': _text(row['category'], 128, 'INVALID_CONTRACT',
                             'The oracle returned an unsupported contract.'),
            'actual': _oracle_money(row['actual']),
            'budget': _oracle_money(row['budget']),
            'variance': _oracle_money(row['variance']),
        })
    codes = [row['account_code'] for row in by_account]
    if len(set(codes)) != len(codes) or set(codes) != covered:
        _fail('INVALID_CONTRACT',
              'The bound variance oracle returned an unsupported contract.')
    bridge = output['noi_bridge']
    _require_dict(bridge, NOI_BRIDGE_KEYS, 'INVALID_CONTRACT',
                  'The bound variance oracle returned an unsupported contract.')
    noi_bridge = {key: _oracle_money(value) for key, value in bridge.items()}
    by_account.sort(key=lambda row: row['account_code'])
    return by_account, noi_bridge


# ------------------------------------------------------------------- the review

def review_period(asset_id, period, *, materiality, as_of_date=None,
                   db_path=None, backend=None, variance_oracle=None) -> dict:
    """Read-only review of exactly one (property, period); never writes."""
    asset_id = _validate_asset(asset_id)
    period, year, month = _validate_period(period)
    policy = _validate_materiality(materiality)
    review_day: date | None = None
    if as_of_date is not None:
        review_day = _validate_day(as_of_date)
        if review_day < date(year, month, 1):
            _fail('INVALID_INPUT',
                  'The review as-of date precedes the requested period.')
    backend_obj = _resolve_backend(db_path, backend)
    property_key = backend_obj.resolve_property(asset_id)

    period_end = date(year, month, calendar.monthrange(year, month)[1])
    bound = review_day or period_end
    bound_text = bound.isoformat()

    rows = _validate_gl_rows(backend_obj.gl_rows(property_key, period),
                             property_key, period)
    deduped, duplicates = _dedupe(rows)
    mappings = _validate_mappings(backend_obj.account_mappings(property_key),
                                  property_key)
    snapshots = _validate_snapshots(
        backend_obj.occupancy_snapshots(property_key, bound_text), bound)
    unit_count = backend_obj.unit_count(property_key)
    if unit_count is not None and (not isinstance(unit_count, int)
                                   or isinstance(unit_count, bool)):
        _fail('INVALID_CONTRACT', 'The stated unit count must be an integer or absent.')

    exceptions: list[dict] = []
    material: list[dict] = []
    facts: list[dict] = []

    # ---- GL coverage: missing budget is not zero budget.
    accounts_actual = {r['account_code'] for r in deduped if r['kind'] == 'actual'}
    accounts_budget = {r['account_code'] for r in deduped if r['kind'] == 'budget'}
    covered = accounts_actual & accounts_budget
    missing_budget = sorted(accounts_actual - accounts_budget)
    missing_actual = sorted(accounts_budget - accounts_actual)
    if not accounts_actual:
        exceptions.append(_exception(
            'MISSING_ACTUALS', 'blocker',
            details={'actual_rows': 0, 'budget_rows': len(accounts_budget)},
            evidence=[]))
    if missing_budget:
        rows_for = [r for r in deduped if r['account_code'] in set(missing_budget)]
        evidence, _ = _capped(sorted(
            rows_for, key=lambda r: (r['kind'], r['source_file'], r['source_row'], r['id'])),
            period)
        exceptions.append(_exception(
            'MISSING_BUDGET', 'blocker', details={'accounts': missing_budget},
            evidence=evidence))
    if missing_actual:
        rows_for = [r for r in deduped if r['account_code'] in set(missing_actual)]
        evidence, _ = _capped(sorted(
            rows_for, key=lambda r: (r['kind'], r['source_file'], r['source_row'], r['id'])),
            period)
        exceptions.append(_exception(
            'BUDGET_WITHOUT_ACTUAL', 'flag', details={'accounts': missing_actual},
            evidence=evidence))

    # ---- occupancy snapshot, freshness, change and count mismatch.
    current = snapshots[0] if snapshots else None
    prior = None
    if current is not None:
        prior = next((s for s in snapshots
                      if s['as_of_date'] < current['as_of_date']), None)
    feeds = {'snapshot_as_of': None, 'freshness': 'unknown',
             'age_days': None, 'stale_after_days': policy['stale_after_days']}
    occupancy = {'current': None, 'prior': None, 'change': None,
                 'unit_count': None, 'mismatch_status': 'unknown'}
    if current is None:
        exceptions.append(_exception(
            'OCCUPANCY_SNAPSHOT_MISSING', 'flag', details={'bound': bound_text},
            evidence=[]))
    else:
        counts = _counts_of(current)
        citation = _citation(current['source_file'], current['source_row'],
                             SNAPSHOT_TABLE, current['as_of_date'].isoformat())
        occupancy['current'] = _occupancy_payload(current, counts)
        feeds['snapshot_as_of'] = current['as_of_date'].isoformat()
        if review_day is None:
            exceptions.append(_exception(
                'FEED_FRESHNESS_UNKNOWN', 'flag', details={},
                evidence=[citation]))
        else:
            age = (review_day - current['as_of_date']).days
            feeds['age_days'] = age
            if age > policy['stale_after_days']:
                feeds['freshness'] = 'stale'
                exceptions.append(_exception(
                    'FEED_STALE', 'flag',
                    details={'age_days': age,
                             'stale_after_days': policy['stale_after_days']},
                    evidence=[citation]))
                facts.append({'statement':
                              f'Occupancy snapshot feed is {age} days old '
                              f'(bound {policy["stale_after_days"]} days).',
                              'evidence': [citation]})
            else:
                feeds['freshness'] = 'current'
        facts.append({'statement':
                      f'occupied {counts.occupied} of {counts.denominator} units '
                      f'as of {current["as_of_date"].isoformat()}.',
                      'evidence': [citation]})
        if prior is None:
            exceptions.append(_exception(
                'OCCUPANCY_CHANGE_UNKNOWN', 'flag', details={}, evidence=[citation]))
        else:
            prior_counts = _counts_of(prior)
            occupancy['prior'] = _occupancy_payload(prior, prior_counts)
            occupancy['change'] = {
                'occupied_change': counts.occupied - prior_counts.occupied,
                'vacant_change': counts.vacant - prior_counts.vacant,
                'down_change': counts.down - prior_counts.down,
                'denominator_change': counts.denominator - prior_counts.denominator,
            }
            facts.append({'statement':
                          f'Occupied units changed by '
                          f'{counts.occupied - prior_counts.occupied} since '
                          f'{prior["as_of_date"].isoformat()}.',
                          'evidence': [citation]})
        occupancy['unit_count'] = unit_count
        if unit_count is None or unit_count <= 0:
            exceptions.append(_exception(
                'OCCUPANCY_UNIT_COUNT_UNKNOWN', 'flag',
                details={'unit_count': unit_count}, evidence=[citation]))
        elif unit_count != counts.denominator:
            occupancy['mismatch_status'] = 'mismatch'
            exceptions.append(_exception(
                'OCCUPANCY_COUNT_MISMATCH', 'blocker',
                details={'snapshot_denominator': counts.denominator,
                         'unit_count': unit_count},
                evidence=[citation]))
            facts.append({'statement':
                          f'OCCUPANCY_COUNT_MISMATCH: snapshot denominator '
                          f'{counts.denominator} differs from stated unit count '
                          f'{unit_count}.',
                          'evidence': [citation]})
        else:
            occupancy['mismatch_status'] = 'match'

    # ---- duplicate GL imports (deduplicated above; disclosed here).
    for duplicate in duplicates:
        kept = duplicate['kept']
        exceptions.append({
            'code': 'DUPLICATE_GL_IMPORT', 'severity': 'material',
            'message': EXCEPTION_MESSAGES['DUPLICATE_GL_IMPORT'],
            'details': {'kind': kept['kind'], 'source_file': kept['source_file'],
                        'source_row': kept['source_row'],
                        'copies': duplicate['copies'],
                        'amounts_differ': duplicate['amounts_differ'],
                        'kept_amount': _money(kept['amount'], policy['currency'],
                                              period, [kept])},
            'evidence': [_citation(kept['source_file'], kept['source_row'],
                                  GL_TABLES[kept['kind']], period)],
        })
    if duplicates:
        facts.append({'statement':
                      f'{len(duplicates)} duplicate GL import group(s) deduplicated '
                      f'before variance.',
                      'evidence': exceptions[-1]['evidence'] if exceptions else []})

    # ---- account mapping review state.
    mapping_by_code: dict[str, list[dict]] = {}
    for mapping in mappings:
        mapping_by_code.setdefault(mapping['account_code'], []).append(mapping)
    for code in sorted({r['account_code'] for r in deduped}):
        gl_categories = sorted({r['category'] for r in deduped
                                if r['account_code'] == code})
        rows_for = [r for r in deduped if r['account_code'] == code]
        evidence, _ = _capped(sorted(
            rows_for, key=lambda r: (r['kind'], r['source_file'], r['source_row'], r['id'])),
            period)
        citation = {'artifact': backend_obj.artifact, 'row': None,
                    'table': MAPPING_TABLE, 'period': period}
        records = mapping_by_code.get(code, [])
        states = sorted({(record['noi_category'], record['status']) for record in records})
        if not records:
            exceptions.append(_exception(
                'UNREVIEWED_ACCOUNT_MAPPING', 'material',
                details={'account_code': code, 'reason': 'absent', 'status': None},
                evidence=evidence + [citation]))
        elif len(states) > 1:
            exceptions.append(_exception(
                'UNREVIEWED_ACCOUNT_MAPPING', 'material',
                details={'account_code': code, 'reason': 'conflicting',
                         'status': sorted({state[1] for state in states})},
                evidence=evidence + [citation]))
        else:
            noi_category, status = states[0]
            if status != 'approved':
                exceptions.append(_exception(
                    'UNREVIEWED_ACCOUNT_MAPPING', 'material',
                    details={'account_code': code, 'reason': 'status',
                             'status': status},
                    evidence=evidence + [citation]))
            elif gl_categories != [noi_category]:
                exceptions.append(_exception(
                    'CHANGED_ACCOUNT_MAPPING', 'material',
                    details={'account_code': code, 'gl_categories': gl_categories,
                             'mapped_category': noi_category, 'status': status},
                    evidence=evidence + [citation]))

    # ---- variance through the bound oracle only.
    variance = {
        'status': 'not_implemented', 'oracle_owner': ORACLE_OWNER,
        'oracle_functions': list(ORACLE_FUNCTIONS),
        'covered_accounts': sorted(covered),
        'excluded_accounts': {'missing_budget': missing_budget,
                               'missing_actual': missing_actual},
        'by_account': [], 'noi_bridge': None,
        'materiality': {'variance_abs': policy['variance_abs'],
                        'currency': policy['currency']},
        'input_sha256': {'actuals': None, 'budgets': None},
    }
    threshold = Decimal(policy['variance_abs'])
    if variance_oracle is None:
        exceptions.append(_exception(
            'VARIANCE_NOT_IMPLEMENTED', 'blocker',
            details={'oracle_owner': ORACLE_OWNER,
                     'oracle_functions': list(ORACLE_FUNCTIONS)},
            evidence=[]))
    elif covered:
        actuals = _oracle_rows(deduped, 'actual', covered)
        budgets = _oracle_rows(deduped, 'budget', covered)
        variance['input_sha256'] = {
            'actuals': hashlib.sha256(_canonical(actuals)).hexdigest(),
            'budgets': hashlib.sha256(_canonical(budgets)).hexdigest(),
        }
        by_account, noi_bridge = _run_oracle(variance_oracle, actuals, budgets, covered)
        variance['status'] = 'provided'
        variance['by_account'] = by_account
        variance['noi_bridge'] = noi_bridge
        covered_rows = [r for r in deduped if r['account_code'] in covered]
        bridge_source, bridge_truncated = _capped(sorted(
            covered_rows, key=lambda r: (r['kind'], r['source_file'],
                                         r['source_row'], r['id'])), period)
        rendered = {
            key: {'amount': value, 'currency': policy['currency'], 'unit': 'usd',
                  'period': period, 'source': bridge_source,
                  'source_truncated': bridge_truncated}
            for key, value in noi_bridge.items()
        }
        for row in by_account:
            rows_for = sorted([r for r in covered_rows
                               if r['account_code'] == row['account_code']],
                              key=lambda r: (r['kind'], r['source_file'],
                                             r['source_row'], r['id']))
            row['actual'] = _money(row['actual'], policy['currency'], period,
                                  [r for r in rows_for if r['kind'] == 'actual']
                                  or rows_for)
            row['budget'] = _money(row['budget'], policy['currency'], period,
                                   [r for r in rows_for if r['kind'] == 'budget']
                                   or rows_for)
            row['variance'] = _money(row['variance'], policy['currency'], period,
                                     rows_for)
        variance['noi_bridge'] = rendered
        ranked = sorted(
            by_account,
            key=lambda row: (-abs(Decimal(row['variance']['amount'])),
                             row['account_code']))
        for row in ranked:
            delta = abs(Decimal(row['variance']['amount']))
            if delta < threshold:
                continue
            exceptions.append(_exception(
                'MATERIAL_VARIANCE', 'material',
                details={'account_code': row['account_code'],
                         'category': row['category'],
                         'actual': row['actual'], 'budget': row['budget'],
                         'variance': row['variance'],
                         'threshold': {'amount': policy['variance_abs'],
                                       'currency': policy['currency']}},
                evidence=row['variance']['source']))
            facts.append({'statement':
                          f'Account {row["account_code"]} variance '
                          f'{row["variance"]["amount"]} {policy["currency"]} meets '
                          f'the materiality threshold {policy["variance_abs"]}.',
                          'evidence': row['variance']['source']})
    else:
        variance['status'] = 'not_available'

    blockers = [record['code'] for record in exceptions
                if record['severity'] == 'blocker']
    material_records = [record for record in exceptions
                       if record['severity'] == 'material']

    return {
        'contract_version': CONTRACT_VERSION,
        'asset_id': asset_id,
        'property_key': property_key,
        'period': period,
        'as_of_date': review_day.isoformat() if review_day is not None else None,
        'status': 'blocked' if blockers else 'reviewed',
        'scope': {'asset_id': asset_id, 'property_key': property_key,
                  'period': period, 'currency': policy['currency'],
                  'properties_reviewed': 1, 'periods_reviewed': 1},
        'counts': {
            'actual_rows': sum(1 for r in deduped if r['kind'] == 'actual'),
            'budget_rows': sum(1 for r in deduped if r['kind'] == 'budget'),
            'distinct_accounts': len({r['account_code'] for r in deduped}),
            'duplicate_groups': len(duplicates),
            'duplicate_rows': sum(d['copies'] - 1 for d in duplicates),
        },
        'feeds': feeds,
        'occupancy': occupancy,
        'variance': variance,
        'material_exceptions': material_records,
        'exceptions': exceptions,
        'blockers': blockers,
        'llm_explanation_ineligible': bool(blockers),
        'explanation': {'measured_facts': facts[:MAX_FACTS],
                        'hypotheses': [], 'note': HYPOTHESES_NOTE},
    }


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')