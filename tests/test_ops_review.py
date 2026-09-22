"""Synthetic tests for the read-only operations review slice (Task 3.5).

Self-contained public tests: synthetic SQLite ops fixtures only — no real
deal bytes, no resident rows, no models, no network, no writes to the source.
The variance oracle in this file is a labeled synthetic stand-in representing
the deterministic ops owner (``boxscore::variance`` pure functions); the
module under test performs no variance arithmetic of its own. All data is
synthetic; canary strings stand in for resident details and must never
surface in results, refusals or exception chains.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import sqlite3
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError

ASSET = 'synthetic_ops'
ASSET_NAME = 'Synthetic_Ops'
PERIOD = '2026-04'
AS_OF = '2026-04-30'
MATERIALITY = {'variance_abs': '500.00', 'currency': 'USD'}
CANARY_PAYEE = 'PRIVATE_CANARY_PAYEE'
CANARY_REMARK = 'PRIVATE_CANARY_NOTE'

SCHEMA = """
CREATE TABLE properties (
  id TEXT PRIMARY KEY, name TEXT NOT NULL, market TEXT NOT NULL,
  unit_count INTEGER NOT NULL, owner_entity TEXT NOT NULL,
  property_manager TEXT NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE periods (
  id TEXT PRIMARY KEY, year INTEGER NOT NULL, month INTEGER NOT NULL,
  label TEXT NOT NULL UNIQUE);
CREATE TABLE gl_actuals (
  id TEXT PRIMARY KEY, property_id TEXT NOT NULL, period_id TEXT NOT NULL,
  account_code TEXT NOT NULL, account_name TEXT NOT NULL, category TEXT NOT NULL,
  amount REAL NOT NULL, source_file TEXT NOT NULL, source_row INTEGER NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE gl_budgets (
  id TEXT PRIMARY KEY, property_id TEXT NOT NULL, period_id TEXT NOT NULL,
  account_code TEXT NOT NULL, account_name TEXT NOT NULL, category TEXT NOT NULL,
  amount REAL NOT NULL, source_file TEXT NOT NULL, source_row INTEGER NOT NULL,
  created_at TEXT NOT NULL);
CREATE TABLE rent_roll_snapshots (
  id TEXT PRIMARY KEY, property_id TEXT NOT NULL, as_of_date TEXT NOT NULL,
  occupied_units INTEGER NOT NULL, vacant_units INTEGER NOT NULL,
  leased_units INTEGER NOT NULL, notice_units INTEGER NOT NULL,
  down_units INTEGER NOT NULL, market_rent_total REAL NOT NULL,
  in_place_rent_total REAL NOT NULL, source_file TEXT NOT NULL,
  source_row INTEGER NOT NULL, created_at TEXT NOT NULL);
CREATE TABLE account_mappings (
  id TEXT PRIMARY KEY, source_system TEXT NOT NULL, property_scope TEXT NOT NULL,
  account_code TEXT NOT NULL, account_name TEXT NOT NULL, noi_category TEXT NOT NULL,
  confidence_score REAL NOT NULL, status TEXT NOT NULL,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
CREATE TABLE gl_transactions (
  id TEXT PRIMARY KEY, property_id TEXT NOT NULL, entity_code TEXT NOT NULL,
  account_code TEXT NOT NULL, txn_date TEXT, period TEXT NOT NULL,
  payee TEXT NOT NULL DEFAULT '', is_resident INTEGER NOT NULL DEFAULT 0,
  control TEXT, reference TEXT, amount REAL NOT NULL, remarks TEXT,
  source_file TEXT NOT NULL, source_row INTEGER NOT NULL, created_at TEXT NOT NULL);
"""

REVENUE_CATEGORIES = frozenset({'rental income', 'concessions', 'bad debt', 'other income'})
UNMAPPED_CATEGORIES = frozenset({'unmapped'})


def api():
    """Import the Task 3.5 seam; the RED run may raise ModuleNotFoundError."""
    from plat_harness.adapters import ops_review as module
    return module


# --------------------------------------------------------- synthetic fixtures

def add_property(con, pid=ASSET, name=ASSET_NAME, units=10):
    con.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?)",
                (pid, name, 'synthetic_market', units, 'synthetic_owner',
                 'synthetic_pm', '2026-01-01T00:00:00Z'))


def add_period(con, label):
    con.execute("INSERT INTO periods VALUES (?,?,?,?)",
                (f'p_{label}', int(label[:4]), int(label[5:7]), label))


def add_gl(con, kind, code, amount, *, name='Rental Income', category='rental income',
           pid=ASSET, period=PERIOD, source_file=None, source_row=2, row_id=None,
           created='2026-05-02T00:00:00Z'):
    table = 'gl_actuals' if kind == 'actual' else 'gl_budgets'
    source_file = source_file or f'synthetic_{table}.csv'
    row_id = row_id or f"{'ga' if kind == 'actual' else 'gb'}_{code}_{source_row}"
    con.execute(f"INSERT INTO {table} VALUES (?,?,?,?,?,?,?,?,?,?)",
                (row_id, pid, f'p_{period}', code, name, category, amount,
                 source_file, source_row, created))


def add_snapshot(con, as_of, occupied, vacant, down, *, pid=ASSET, row_id=None,
                 created='2026-05-01T00:00:00Z', source_file='synthetic_rent_roll.csv',
                 source_row=1):
    row_id = row_id or f'rr_{as_of}'
    con.execute("INSERT INTO rent_roll_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (row_id, pid, as_of, occupied, vacant, occupied, 0, down,
                 15000.0, 14000.0, source_file, source_row, created))


def add_mapping(con, code, category, *, status='approved', scope=ASSET,
                name='Rental Income', system='synthetic_etl', row_id=None):
    row_id = row_id or f'm_{system}_{code}'
    con.execute("INSERT INTO account_mappings VALUES (?,?,?,?,?,?,?,?,?,?)",
                (row_id, system, scope, code, name, category, 0.9, status,
                 '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))


def add_resident_transaction(con, *, payee=CANARY_PAYEE, remarks=CANARY_REMARK):
    con.execute("INSERT INTO gl_transactions VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('gt_canary', ASSET, 'ENT1', '4000', '2026-04-05', PERIOD,
                 payee, 1, 'CTRL1', 'REF1', 5000.0, remarks,
                 'synthetic_gl_transactions.csv', 7, '2026-05-02T00:00:00Z'))


def seed_clean(con):
    add_property(con)
    add_period(con, '2026-03')
    add_period(con, PERIOD)
    add_gl(con, 'actual', '4000', 5000.00, source_row=2)
    add_gl(con, 'actual', '6100', 1200.50, name='Repairs & Maintenance',
           category='repairs & maintenance', source_row=3)
    add_gl(con, 'budget', '4000', 4400.00, source_row=2)
    add_gl(con, 'budget', '6100', 1100.00, name='Repairs & Maintenance',
           category='repairs & maintenance', source_row=3)
    add_snapshot(con, '2026-03-31', 7, 2, 1, created='2026-04-01T00:00:00Z')
    add_snapshot(con, AS_OF, 8, 1, 1, created='2026-05-01T00:00:00Z')
    add_mapping(con, '4000', 'rental income')
    add_mapping(con, '6100', 'repairs & maintenance', name='Repairs & Maintenance')
    add_resident_transaction(con)


def build_db(tmp_path, name='ops.db', seed=seed_clean):
    path = tmp_path / name
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    if seed is not None:
        seed(con)
    con.commit()
    con.close()
    return path


# ---------------------------------- labeled synthetic variance-oracle stand-in

def stub_oracle(actuals, budgets):
    """Stand-in for the ops owner's pure variance functions (boxscore::variance).

    Mirrors ``compute_account_variances`` and ``compute_noi_bridge`` semantics:
    per-(account,name,category) sums, variance = actual - budget, revenue /
    expense / unmapped classification, expenses at their natural sign.
    """
    totals = {}
    for row in actuals:
        key = (row['account_code'], row['account_name'], row['category'])
        totals.setdefault(key, [Decimal('0'), Decimal('0')])[0] += Decimal(row['amount'])
    for row in budgets:
        key = (row['account_code'], row['account_name'], row['category'])
        totals.setdefault(key, [Decimal('0'), Decimal('0')])[1] += Decimal(row['amount'])
    by_account = []
    rev_a = rev_b = exp_a = exp_b = unm_a = unm_b = Decimal('0')
    for (code, acct_name, category), (actual, budget) in sorted(totals.items()):
        by_account.append({
            'account_code': code, 'account_name': acct_name, 'category': category,
            'actual': f'{actual:.2f}', 'budget': f'{budget:.2f}',
            'variance': f'{actual - budget:.2f}'})
        if category in REVENUE_CATEGORIES:
            rev_a += actual
            rev_b += budget
        elif category in UNMAPPED_CATEGORIES:
            unm_a += actual
            unm_b += budget
        else:
            exp_a += actual
            exp_b += budget
    noi_bridge = {
        'actual_revenue': f'{rev_a:.2f}', 'budget_revenue': f'{rev_b:.2f}',
        'revenue_variance': f'{rev_a - rev_b:.2f}',
        'actual_expenses': f'{exp_a:.2f}', 'budget_expenses': f'{exp_b:.2f}',
        'expense_variance': f'{exp_a - exp_b:.2f}',
        'actual_noi': f'{rev_a - exp_a:.2f}', 'budget_noi': f'{rev_b - exp_b:.2f}',
        'noi_variance': f'{(rev_a - exp_a) - (rev_b - exp_b):.2f}',
        'unmapped_actual': f'{unm_a:.2f}', 'unmapped_budget': f'{unm_b:.2f}',
    }
    return {'by_account': by_account, 'noi_bridge': noi_bridge}


class RecordingOracle:
    """Records the exact inputs the module hands to the bound oracle."""

    def __init__(self):
        self.calls = []

    def __call__(self, actuals, budgets):
        self.calls.append((json.loads(json.dumps(actuals)), json.loads(json.dumps(budgets))))
        return stub_oracle(actuals, budgets)


class BadOracle:
    def __init__(self, output):
        self.output = output

    def __call__(self, actuals, budgets):
        return self.output


class CrashingOracle:
    def __call__(self, actuals, budgets):
        raise RuntimeError('owner internals must not surface ' + CANARY_REMARK)


# ------------------------------------------------------------ review helpers

def review(db, **kwargs):
    module = api()
    options = dict(asset_id=ASSET, period=PERIOD, materiality=dict(MATERIALITY),
                   as_of_date=AS_OF, db_path=str(db),
                   variance_oracle=RecordingOracle())
    options.update(kwargs)
    return module.review_period(**options)


def refused(fn, code):
    with pytest.raises(HarnessError) as caught:
        fn()
    assert caught.value.code == code
    return caught.value


def assert_sanitized(exc):
    """Typed, sanitized refusals: no chained exceptions, no canary leakage."""
    assert exc.__cause__ is None and exc.__context__ is None
    payload = json.dumps(exc.as_dict())
    assert CANARY_PAYEE not in payload
    assert CANARY_REMARK not in payload


def codes_of(result):
    return [record['code'] for record in result['exceptions']]


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':')).encode('utf-8')


# ------------------------------------------------- seam + closed contract shape

def test_seam_exists():
    assert importlib.util.find_spec('plat_harness.adapters.ops_review')


def test_contract_metadata_is_closed_and_versioned():
    module = api()
    assert module.CONTRACT_VERSION == 'ops-review/1.0.0'
    assert module.ORACLE_OWNER == 'boxscore::variance'
    assert tuple(module.ORACLE_FUNCTIONS) == ('compute_account_variances', 'compute_noi_bridge')
    assert module.STALE_AFTER_DAYS == 45
    assert module.OPS_CURRENCIES == frozenset({'USD'})
    assert module.SEVERITIES == frozenset({'blocker', 'material', 'flag'})
    for name in ('ERROR_CODES', 'EXCEPTION_CODES'):
        assert isinstance(getattr(module, name), frozenset)
    assert module.RESULT_KEYS == tuple(sorted(module.RESULT_KEYS))
    assert module.NOI_BRIDGE_KEYS == frozenset({
        'actual_revenue', 'budget_revenue', 'revenue_variance',
        'actual_expenses', 'budget_expenses', 'expense_variance',
        'actual_noi', 'budget_noi', 'noi_variance',
        'unmapped_actual', 'unmapped_budget'})
    assert module.MONEY_KEYS == frozenset(
        {'amount', 'currency', 'unit', 'period', 'source', 'source_truncated'})
    assert module.CITATION_KEYS == frozenset({'artifact', 'row', 'table', 'period'})


# --------------------------------------------------------- the clean green path

def test_clean_review_reports_variance_and_occupancy(tmp_path):
    db = build_db(tmp_path)
    oracle = RecordingOracle()
    result = review(db, variance_oracle=oracle)
    assert result['status'] == 'reviewed'
    assert result['blockers'] == []
    assert result['llm_explanation_ineligible'] is False
    assert result['property_key'] == ASSET
    assert result['scope'] == {'asset_id': ASSET, 'property_key': ASSET,
                               'period': PERIOD, 'currency': 'USD',
                               'properties_reviewed': 1, 'periods_reviewed': 1}
    assert result['counts'] == {'actual_rows': 2, 'budget_rows': 2,
                                'distinct_accounts': 2, 'duplicate_groups': 0,
                                'duplicate_rows': 0}
    assert result['feeds'] == {'snapshot_as_of': AS_OF, 'freshness': 'current',
                               'age_days': 0, 'stale_after_days': 45}
    occupancy = result['occupancy']
    assert occupancy['current']['occupied'] == 8
    assert occupancy['current']['denominator'] == 10
    assert occupancy['current']['rate'] == pytest.approx(0.8)
    assert occupancy['prior']['occupied'] == 7
    assert occupancy['change'] == {'occupied_change': 1, 'vacant_change': -1,
                                   'down_change': 0, 'denominator_change': 0}
    assert occupancy['unit_count'] == 10
    assert occupancy['mismatch_status'] == 'match'
    variance = result['variance']
    assert variance['status'] == 'provided'
    assert variance['covered_accounts'] == ['4000', '6100']
    assert variance['excluded_accounts'] == {'missing_budget': [], 'missing_actual': []}
    assert variance['materiality'] == {'variance_abs': '500.00', 'currency': 'USD'}
    assert variance['oracle_owner'] == 'boxscore::variance'
    assert [row['account_code'] for row in variance['by_account']] == ['4000', '6100']
    first = variance['by_account'][0]
    assert set(first) == set(module_keys_by_account())
    assert first['actual']['amount'] == '5000.00'
    assert first['budget']['amount'] == '4400.00'
    assert first['variance']['amount'] == '600.00'
    assert first['variance']['currency'] == 'USD'
    assert first['variance']['unit'] == 'usd'
    assert first['variance']['period'] == PERIOD
    assert first['variance']['source'][0]['artifact'] == 'synthetic_gl_actuals.csv'
    assert first['variance']['source'][0]['row'] == 2
    assert first['variance']['source_truncated'] is False
    bridge = variance['noi_bridge']
    assert bridge['noi_variance']['amount'] == '499.50'
    assert bridge['actual_noi']['amount'] == '3799.50'
    expected_actuals = [
        {'account_code': '4000', 'account_name': 'Rental Income',
         'category': 'rental income', 'amount': '5000.0'},
        {'account_code': '6100', 'account_name': 'Repairs & Maintenance',
         'category': 'repairs & maintenance', 'amount': '1200.5'},
    ]
    expected_budgets = [
        {'account_code': '4000', 'account_name': 'Rental Income',
         'category': 'rental income', 'amount': '4400.0'},
        {'account_code': '6100', 'account_name': 'Repairs & Maintenance',
         'category': 'repairs & maintenance', 'amount': '1100.0'},
    ]
    assert oracle.calls == [(expected_actuals, expected_budgets)]
    assert variance['input_sha256']['actuals'] == hashlib.sha256(
        canonical(expected_actuals)).hexdigest()
    assert variance['input_sha256']['budgets'] == hashlib.sha256(
        canonical(expected_budgets)).hexdigest()
    assert codes_of(result) == ['MATERIAL_VARIANCE']
    assert result['exceptions'] == result['material_exceptions']
    material = result['material_exceptions'][0]
    assert material['severity'] == 'material'
    assert material['details']['account_code'] == '4000'
    assert material['details']['threshold'] == {'amount': '500.00', 'currency': 'USD'}
    assert material['details']['variance']['amount'] == '600.00'
    facts = result['explanation']['measured_facts']
    assert facts and all(set(fact) == {'statement', 'evidence'} and fact['evidence']
                         for fact in facts)
    assert result['explanation']['hypotheses'] == []


def module_keys_by_account():
    return api().BY_ACCOUNT_KEYS


def test_result_shape_is_closed(tmp_path):
    module = api()
    db = build_db(tmp_path)
    result = review(db)
    assert set(result) == set(module.RESULT_KEYS)
    assert set(result['counts']) == set(module.COUNTS_KEYS)
    assert set(result['scope']) == set(module.SCOPE_KEYS)
    assert set(result['feeds']) == set(module.FEEDS_KEYS)
    assert set(result['occupancy']) == set(module.OCCUPANCY_KEYS)
    assert set(result['variance']) == set(module.VARIANCE_KEYS)
    assert set(result['explanation']) == set(module.EXPLANATION_KEYS)
    for record in result['exceptions'] + result['material_exceptions']:
        assert set(record) == set(module.EXCEPTION_KEYS)
    for record in result['variance']['by_account']:
        assert set(record) == set(module.BY_ACCOUNT_KEYS)
        for key in ('actual', 'budget', 'variance'):
            assert set(record[key]) == set(module.MONEY_KEYS)
    for key, money in result['variance']['noi_bridge'].items():
        assert set(money) == set(module.MONEY_KEYS)
    snapshot = result['occupancy']['current']
    assert set(snapshot) == set(module.SNAPSHOT_PAYLOAD_KEYS)
    for source in snapshot['source']:
        assert set(source) == set(module.CITATION_KEYS)


def test_name_alias_resolves_to_the_same_property(tmp_path):
    db = build_db(tmp_path)
    result = review(db, asset_id=ASSET_NAME)
    assert result['asset_id'] == ASSET_NAME
    assert result['property_key'] == ASSET
    assert result['status'] == 'reviewed'


def test_prior_period_rows_are_never_aggregated(tmp_path):
    def seed(con):
        seed_clean(con)
        add_gl(con, 'actual', '4000', 999.00, period='2026-03', source_row=9)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['counts']['actual_rows'] == 2
    assert '999' not in json.dumps(result)


def test_deterministic_result_bytes(tmp_path):
    db = build_db(tmp_path)
    first = review(db)
    second = review(db)
    assert first == second
    assert canonical(first) == canonical(second)


# ------------------------------ RED 1: missing budget is not zero budget

def test_missing_budget_is_not_zero_budget(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    oracle = RecordingOracle()
    result = review(db, variance_oracle=oracle)
    assert result['status'] == 'blocked'
    assert 'MISSING_BUDGET' in result['blockers']
    missing = [record for record in result['exceptions']
               if record['code'] == 'MISSING_BUDGET']
    assert missing[0]['severity'] == 'blocker'
    assert missing[0]['details']['accounts'] == ['4000']
    assert missing[0]['evidence'][0]['artifact'] == 'synthetic_gl_actuals.csv'
    assert result['variance']['status'] == 'not_available'
    assert result['variance']['by_account'] == []
    assert result['variance']['excluded_accounts']['missing_budget'] == ['4000']
    assert result['variance']['noi_bridge'] is None
    assert oracle.calls == []
    assert '5000.00' not in json.dumps(result['variance'])


def test_explicit_zero_budget_is_a_stated_zero_not_missing(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 0.0)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert 'MISSING_BUDGET' not in result['blockers']
    assert result['variance']['status'] == 'provided'
    assert result['variance']['by_account'][0]['budget']['amount'] == '0.00'
    assert result['variance']['by_account'][0]['variance']['amount'] == '5000.00'


def test_missing_actuals_block_the_review(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['status'] == 'blocked'
    assert result['counts']['actual_rows'] == 0
    assert 'MISSING_ACTUALS' in result['blockers']
    assert result['variance']['status'] == 'not_available'
    assert result['llm_explanation_ineligible'] is True


def test_budget_without_actual_is_flagged_not_zero_actual(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_gl(con, 'budget', '4100', 300.00, name='Other Income',
               category='other income', source_row=4)
        add_mapping(con, '4000', 'rental income')
        add_mapping(con, '4100', 'other income', name='Other Income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['status'] == 'reviewed'
    assert set(codes_of(result)) == {'BUDGET_WITHOUT_ACTUAL',
                                     'OCCUPANCY_CHANGE_UNKNOWN',
                                     'MATERIAL_VARIANCE'}
    flagged = [record for record in result['exceptions']
               if record['code'] == 'BUDGET_WITHOUT_ACTUAL']
    assert flagged[0]['details']['accounts'] == ['4100']
    assert result['variance']['excluded_accounts']['missing_actual'] == ['4100']
    assert result['variance']['covered_accounts'] == ['4000']


# --------------------- RED 2: no cross-property or cross-period aggregation

@pytest.mark.parametrize('asset_id', [
    '*', 'all', '', 'synthetic_ops,other', '../escape', '/etc/passwd', 'a b', None, 7,
])
def test_wildcard_or_multi_assets_refuse(tmp_path, asset_id):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, asset_id=asset_id), 'INVALID_INPUT')
    assert_sanitized(exc)


@pytest.mark.parametrize('period', [
    '2026-4', '2026-13', '2026/04', '202604', '', '2026-04-01',
    '2026-*', '2026-01,2026-04', '*', None,
])
def test_multi_period_or_malformed_periods_refuse(tmp_path, period):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, period=period), 'INVALID_INPUT')
    assert_sanitized(exc)


@pytest.mark.parametrize('as_of_date', [
    '2026-4-30', '2026-04-31', '20260430', '', '2026-13-01', '2026-03-31',
])
def test_malformed_or_before_period_as_of_refuses(tmp_path, as_of_date):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, as_of_date=as_of_date), 'INVALID_INPUT')
    assert_sanitized(exc)


def test_ambiguous_property_name_refuses(tmp_path):
    def seed(con):
        add_property(con, pid='prop_one', name='Twin_A')
        add_property(con, pid='prop_two', name='twin_a')
        add_period(con, PERIOD)
    db = build_db(tmp_path, seed=seed)
    exc = refused(lambda: review(db, asset_id='twin_a'), 'AMBIGUOUS_PROPERTY')
    assert_sanitized(exc)


def test_unknown_property_refuses(tmp_path):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, asset_id='absent_property'), 'NOT_FOUND')
    assert_sanitized(exc)


class HostileBackend:
    """Labeled fault injection: a backend that breaches its row contract."""

    def __init__(self, base, *, poison_gl=None, poison_snapshot=None, poison_mapping=None):
        self.base = base
        self._poison_gl = poison_gl
        self._poison_snapshot = poison_snapshot
        self._poison_mapping = poison_mapping

    @property
    def artifact(self):
        return self.base.artifact

    def resolve_property(self, asset_id):
        return self.base.resolve_property(asset_id)

    def unit_count(self, property_key):
        return self.base.unit_count(property_key)

    def gl_rows(self, property_key, period):
        rows = self.base.gl_rows(property_key, period)
        if self._poison_gl:
            rows = rows + [dict(rows[0], **self._poison_gl)]
        return rows

    def occupancy_snapshots(self, property_key, bound_date):
        rows = self.base.occupancy_snapshots(property_key, bound_date)
        if self._poison_snapshot:
            rows = rows + [dict(rows[0], **self._poison_snapshot)]
        return rows

    def account_mappings(self, property_key):
        rows = self.base.account_mappings(property_key)
        if self._poison_mapping:
            rows = rows + [dict(rows[0], **self._poison_mapping)]
        return rows


def hostile(tmp_path, **poisons):
    module = api()
    base = module.SqliteOpsBackend(build_db(tmp_path))
    return module.review_period(
        asset_id=ASSET, period=PERIOD, materiality=dict(MATERIALITY),
        backend=HostileBackend(base, **poisons), variance_oracle=RecordingOracle())


def test_backend_rows_from_another_property_refuse(tmp_path):
    exc = refused(lambda: hostile(tmp_path, poison_gl={'property': 'other_property'}),
                  'SCOPE_MISMATCH')
    assert_sanitized(exc)


def test_backend_rows_from_another_period_refuse(tmp_path):
    exc = refused(lambda: hostile(tmp_path, poison_gl={'period': '2026-03'}),
                  'SCOPE_MISMATCH')
    assert_sanitized(exc)


def test_backend_rows_with_resident_fields_refuse_sanitized(tmp_path):
    exc = refused(lambda: hostile(
        tmp_path, poison_gl={'payee': CANARY_PAYEE, 'remarks': CANARY_REMARK}),
        'INVALID_CONTRACT')
    assert CANARY_PAYEE not in exc.message
    assert CANARY_REMARK not in json.dumps(exc.details)
    assert_sanitized(exc)


def test_backend_mapping_rows_with_resident_fields_refuse(tmp_path):
    exc = refused(lambda: hostile(tmp_path, poison_mapping={'payee': CANARY_PAYEE}),
                  'INVALID_CONTRACT')
    assert_sanitized(exc)


def test_backend_future_dated_snapshot_refuses(tmp_path):
    exc = refused(lambda: hostile(tmp_path, poison_snapshot={'as_of_date': '2026-12-31'}),
                  'INVALID_INPUT')
    assert_sanitized(exc)


# ------------------------------------------------- RED 3: stale feed detection

def test_stale_snapshot_feed_is_flagged(tmp_path):
    def seed(con):
        seed_clean(con)
    db = build_db(tmp_path, seed=seed)
    result = review(db, as_of_date='2026-06-15')
    age = (date(2026, 6, 15) - date(2026, 4, 30)).days
    assert result['feeds']['freshness'] == 'stale'
    assert result['feeds']['age_days'] == age
    stale = [record for record in result['exceptions'] if record['code'] == 'FEED_STALE']
    assert stale[0]['details']['age_days'] == age
    assert stale[0]['details']['stale_after_days'] == 45
    assert 'FEED_STALE' not in result['blockers']


def test_freshness_unknown_without_as_of_date(tmp_path):
    db = build_db(tmp_path)
    result = review(db, as_of_date=None)
    assert result['feeds']['freshness'] == 'unknown'
    assert result['feeds']['age_days'] is None
    assert 'FEED_FRESHNESS_UNKNOWN' in codes_of(result)


def test_custom_stale_bound_is_honored(tmp_path):
    db = build_db(tmp_path)
    policy = dict(MATERIALITY, stale_after_days=90)
    result = review(db, as_of_date='2026-06-15', materiality=policy)
    assert result['feeds']['stale_after_days'] == 90
    assert result['feeds']['freshness'] == 'current'


# --------------------------------------- RED 4: duplicate GL import detection

def test_duplicate_gl_import_is_deduped_and_disclosed(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 100.10, source_row=5, row_id='ga_a',
               created='2026-05-01T00:00:00Z')
        add_gl(con, 'actual', '4000', 100.10, source_row=5, row_id='ga_b',
               created='2026-05-02T00:00:00Z')
        add_gl(con, 'budget', '4000', 50.00, source_row=5)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['counts']['actual_rows'] == 1
    assert result['counts']['duplicate_groups'] == 1
    assert result['counts']['duplicate_rows'] == 1
    duplicates = [record for record in result['exceptions']
                  if record['code'] == 'DUPLICATE_GL_IMPORT']
    assert duplicates[0]['severity'] == 'material'
    assert duplicates[0]['details']['copies'] == 2
    assert duplicates[0]['details']['amounts_differ'] is False
    assert duplicates[0]['details']['kept_amount']['amount'] == '100.1'
    assert result['variance']['by_account'][0]['actual']['amount'] == '100.10'
    assert '100.1' in json.dumps(result['variance']['by_account'])


def test_duplicate_import_amount_drift_keeps_first_import(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 100.10, source_row=5, row_id='ga_a',
               created='2026-05-01T00:00:00Z')
        add_gl(con, 'actual', '4000', 90.00, source_row=5, row_id='ga_b',
               created='2026-05-02T00:00:00Z')
        add_gl(con, 'budget', '4000', 50.00, source_row=5)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    duplicates = [record for record in result['exceptions']
                  if record['code'] == 'DUPLICATE_GL_IMPORT']
    assert duplicates[0]['details']['amounts_differ'] is True
    assert duplicates[0]['details']['kept_amount']['amount'] == '100.1'
    assert result['variance']['by_account'][0]['actual']['amount'] == '100.10'


# ------------------------------------- RED 5: changed / unreviewed mappings

def test_changed_account_mapping_is_flagged(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '6100', 1200.50, name='Repairs & Maintenance',
               category='repairs & maintenance')
        add_gl(con, 'budget', '6100', 1100.00, name='Repairs & Maintenance',
               category='repairs & maintenance')
        add_mapping(con, '6100', 'administrative', name='Repairs & Maintenance')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    changed = [record for record in result['exceptions']
               if record['code'] == 'CHANGED_ACCOUNT_MAPPING']
    assert changed[0]['severity'] == 'material'
    assert changed[0]['details']['account_code'] == '6100'
    assert changed[0]['details']['gl_categories'] == ['repairs & maintenance']
    assert changed[0]['details']['mapped_category'] == 'administrative'
    assert changed[0]['details']['status'] == 'approved'


def test_unapproved_mapping_status_is_flagged(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income', status='pending')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    unreviewed = [record for record in result['exceptions']
                  if record['code'] == 'UNREVIEWED_ACCOUNT_MAPPING']
    assert unreviewed[0]['details']['reason'] == 'status'
    assert unreviewed[0]['details']['status'] == 'pending'


def test_absent_mapping_is_flagged_not_assumed(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    unreviewed = [record for record in result['exceptions']
                  if record['code'] == 'UNREVIEWED_ACCOUNT_MAPPING']
    assert unreviewed[0]['details']['reason'] == 'absent'
    assert 'rental income' not in json.dumps(unreviewed[0]['details'])


def test_conflicting_mappings_are_flagged(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income', system='yardi')
        add_mapping(con, '4000', 'administrative', system='entrata')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    unreviewed = [record for record in result['exceptions']
                  if record['code'] == 'UNREVIEWED_ACCOUNT_MAPPING']
    assert unreviewed[0]['details']['reason'] == 'conflicting'


def test_property_scope_mapping_beats_wildcard_scope(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'administrative', scope='*', system='global')
        add_mapping(con, '4000', 'rental income', system='property')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert 'CHANGED_ACCOUNT_MAPPING' not in codes_of(result)
    assert 'UNREVIEWED_ACCOUNT_MAPPING' not in codes_of(result)
    assert result['status'] == 'reviewed'


# ------------------------------------------------ RED 6: resident redaction

def test_resident_transaction_details_never_surface(tmp_path):
    db = build_db(tmp_path)  # seeded with canary payee/remarks transactions
    result = review(db)
    payload = json.dumps(result)
    assert CANARY_PAYEE not in payload
    assert CANARY_REMARK not in payload
    assert 'gl_transactions' not in payload


# --------------- RED 7: snapshot count mismatch cannot be explained away

def test_snapshot_count_mismatch_blocks_llm_explanation(tmp_path):
    def seed(con):
        add_property(con, units=12)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['occupancy']['mismatch_status'] == 'mismatch'
    assert 'OCCUPANCY_COUNT_MISMATCH' in result['blockers']
    assert result['llm_explanation_ineligible'] is True
    mismatch = [record for record in result['exceptions']
                if record['code'] == 'OCCUPANCY_COUNT_MISMATCH']
    assert mismatch[0]['severity'] == 'blocker'
    assert mismatch[0]['details'] == {'snapshot_denominator': 10, 'unit_count': 12}
    assert result['explanation']['hypotheses'] == []
    assert result['status'] == 'blocked'


def test_unit_count_zero_is_unknown_not_a_match(tmp_path):
    def seed(con):
        add_property(con, units=0)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['occupancy']['mismatch_status'] == 'unknown'
    flagged = [record for record in result['exceptions']
               if record['code'] == 'OCCUPANCY_UNIT_COUNT_UNKNOWN']
    assert flagged[0]['severity'] == 'flag'
    assert 'OCCUPANCY_UNIT_COUNT_UNKNOWN' not in result['blockers']


def test_missing_prior_snapshot_leaves_change_unknown(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['occupancy']['prior'] is None
    assert result['occupancy']['change'] is None
    assert 'OCCUPANCY_CHANGE_UNKNOWN' in codes_of(result)


def test_missing_snapshot_is_a_flag_not_a_zero(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    assert result['occupancy']['current'] is None
    assert result['feeds']['snapshot_as_of'] is None
    assert 'OCCUPANCY_SNAPSHOT_MISSING' in codes_of(result)
    assert 'occupancy' in json.dumps(result)


# ------------------------------------------ the variance oracle boundary

def test_absent_oracle_blocks_variance_without_fabricating(tmp_path):
    db = build_db(tmp_path)
    result = review(db, variance_oracle=None)
    assert result['status'] == 'blocked'
    assert 'VARIANCE_NOT_IMPLEMENTED' in result['blockers']
    assert result['variance']['status'] == 'not_implemented'
    assert result['variance']['by_account'] == []
    assert result['variance']['noi_bridge'] is None
    assert result['variance']['input_sha256'] == {'actuals': None, 'budgets': None}
    blocked = [record for record in result['exceptions']
               if record['code'] == 'VARIANCE_NOT_IMPLEMENTED']
    assert blocked[0]['details']['oracle_owner'] == 'boxscore::variance'
    assert blocked[0]['details']['oracle_functions'] == [
        'compute_account_variances', 'compute_noi_bridge']
    assert result['occupancy']['current']['occupied'] == 8  # still useful read-only


def _oracle_output(**overrides):
    output = stub_oracle(
        [{'account_code': '4000', 'account_name': 'Rental Income',
          'category': 'rental income', 'amount': '5000.0'}],
        [{'account_code': '4000', 'account_name': 'Rental Income',
          'category': 'rental income', 'amount': '4400.0'}])
    for key, value in overrides.items():
        if key in ('by_account', 'noi_bridge'):
            if isinstance(value, dict):
                output[key].update(value)
            else:
                output[key] = value
        else:
            output[key] = value
    return output


@pytest.mark.parametrize('output', [
    _oracle_output(by_account=[{'account_code': '4000', 'account_name': 'x',
                                'category': 'rental income', 'actual': '1.00',
                                'budget': '1.00', 'variance': 123.4}]),
    _oracle_output(by_account=[{'account_code': '4000', 'account_name': 'x',
                                'category': 'rental income', 'actual': '1.00',
                                'budget': '1.00'}]),
    _oracle_output(by_account=[dict(_oracle_output()['by_account'][0], extra='x')]),
    _oracle_output(by_account=[dict(_oracle_output()['by_account'][0],
                                    account_code='9999')]),
    _oracle_output(by_account=[_oracle_output()['by_account'][0],
                               _oracle_output()['by_account'][0]]),
    _oracle_output(by_account='not-a-list'),
    _oracle_output(noi_bridge={'actual_revenue': '1.00'}),
    _oracle_output(noi_bridge=dict(stub_oracle([], [])['noi_bridge'],
                                   noi_variance='not-a-number')),
    _oracle_output(extra_section={}),
    {'by_account': []},
    {},
])
def test_malformed_oracle_output_refuses(tmp_path, output):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, variance_oracle=BadOracle(output)),
                  'INVALID_CONTRACT')
    assert_sanitized(exc)


def test_crashing_oracle_is_sanitized(tmp_path):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, variance_oracle=CrashingOracle()),
                  'INVALID_CONTRACT')
    assert CANARY_REMARK not in exc.message
    assert_sanitized(exc)


def test_oracle_receives_exactly_the_owner_row_contract(tmp_path):
    db = build_db(tmp_path)
    module = api()
    oracle = RecordingOracle()
    review(db, variance_oracle=oracle)
    actuals, budgets = oracle.calls[0]
    for row in actuals + budgets:
        assert set(row) == set(module.ORACLE_ROW_KEYS)


# --------------------------------------------- user-configured materiality

@pytest.mark.parametrize('policy', [
    None, {'variance_abs': '0.00', 'currency': 'USD'},
    {'variance_abs': '-5.00', 'currency': 'USD'},
    {'variance_abs': 'abc', 'currency': 'USD'},
    {'variance_abs': '1,500.00', 'currency': 'USD'},
    {'variance_abs': 500, 'currency': 'USD'},
    {'variance_abs': '500.00'},
    {'variance_abs': '500.00', 'currency': 'EUR'},
    {'variance_abs': '500.00', 'currency': 'usd'},
    {'variance_abs': '500.00', 'currency': 'USD', 'unknown_key': 1},
    {'variance_abs': '500.00', 'currency': 'USD', 'stale_after_days': 0},
    {'variance_abs': '500.00', 'currency': 'USD', 'stale_after_days': 400},
    {'variance_abs': '500.00', 'currency': 'USD', 'stale_after_days': 'x'},
])
def test_invalid_materiality_policy_refuses(tmp_path, policy):
    db = build_db(tmp_path)
    exc = refused(lambda: review(db, materiality=policy), 'INVALID_INPUT')
    assert_sanitized(exc)


def test_materiality_threshold_ranks_material_exceptions(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)  # variance +600.00 -> material
        add_gl(con, 'actual', '6100', 1200.50, name='Repairs & Maintenance',
               category='repairs & maintenance', source_row=3)
        add_gl(con, 'budget', '6100', 1100.00, name='Repairs & Maintenance',
               category='repairs & maintenance', source_row=3)  # +100.50 -> not
        add_mapping(con, '4000', 'rental income')
        add_mapping(con, '6100', 'repairs & maintenance', name='Repairs & Maintenance')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db, materiality={'variance_abs': '500.00', 'currency': 'USD'})
    assert [record['details']['account_code']
            for record in result['material_exceptions']] == ['4000']
    lowered = review(db, materiality={'variance_abs': '50.00', 'currency': 'USD'})
    assert [record['details']['account_code']
            for record in lowered['material_exceptions']] == ['4000', '6100']


def test_sub_material_variances_stay_visible_not_material(tmp_path):
    db = build_db(tmp_path)
    result = review(db, materiality={'variance_abs': '1000.00', 'currency': 'USD'})
    assert result['material_exceptions'] == []
    assert result['variance']['by_account'][0]['variance']['amount'] == '600.00'


# ---------------------------------------------------- explanations + facts

def test_explanations_separate_measured_facts_from_hypotheses(tmp_path):
    def seed(con):
        add_property(con, units=12)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    result = review(db)
    explanation = result['explanation']
    assert explanation['hypotheses'] == []
    assert 'not evidence' in explanation['note']
    assert explanation['measured_facts']
    for fact in explanation['measured_facts']:
        assert set(fact) == {'statement', 'evidence'}
        assert fact['evidence']
    statements = [fact['statement'] for fact in explanation['measured_facts']]
    assert any('occupied 8' in statement for statement in statements)
    assert any('OCCUPANCY_COUNT_MISMATCH' in statement for statement in statements)


# ------------------------------------------------- read-only + backend safety

def test_review_never_writes_the_database(tmp_path):
    db = build_db(tmp_path)
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    review(db)
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before


def test_review_opens_read_only_without_write_permission(tmp_path):
    db = build_db(tmp_path)
    db.chmod(0o444)
    try:
        result = review(db)
        assert result['status'] == 'reviewed'
    finally:
        db.chmod(0o644)


def test_wal_sidecar_snapshot_required(tmp_path):
    db = build_db(tmp_path)
    Path(str(db) + '-wal').write_bytes(b'journal')
    exc = refused(lambda: review(db), 'SNAPSHOT_REQUIRED')
    assert_sanitized(exc)


def test_symlinked_database_refuses(tmp_path):
    db = build_db(tmp_path)
    link = tmp_path / 'link.db'
    link.symlink_to(db)
    exc = refused(lambda: review(link), 'UNSAFE_PATH')
    assert_sanitized(exc)


def test_absent_database_refuses(tmp_path):
    exc = refused(lambda: review(tmp_path / 'absent.db'), 'NOT_FOUND')
    assert_sanitized(exc)


def test_backend_binding_requires_exactly_one_source(tmp_path):
    module = api()
    db = build_db(tmp_path)
    kwargs = dict(asset_id=ASSET, period=PERIOD, materiality=dict(MATERIALITY),
                  variance_oracle=RecordingOracle())
    exc = refused(lambda: module.review_period(**kwargs), 'INVALID_INPUT')
    assert_sanitized(exc)
    exc = refused(lambda: module.review_period(
        db_path=str(db), backend=module.SqliteOpsBackend(db), **kwargs), 'INVALID_INPUT')
    assert_sanitized(exc)
    result = module.review_period(backend=module.SqliteOpsBackend(db), **kwargs)
    assert result['status'] == 'reviewed'
    assert result['property_key'] == ASSET


def test_backend_missing_protocol_methods_refuse(tmp_path):
    module = api()
    exc = refused(lambda: module.review_period(
        asset_id=ASSET, period=PERIOD, materiality=dict(MATERIALITY),
        backend=object(), variance_oracle=RecordingOracle()), 'INVALID_CONTRACT')
    assert_sanitized(exc)


@pytest.mark.parametrize('table', [
    'account_mappings', 'rent_roll_snapshots', 'gl_actuals', 'gl_budgets',
    'periods', 'properties',
])
def test_unsupported_backend_schema_refuses(tmp_path, table):
    db = build_db(tmp_path)
    con = sqlite3.connect(db)
    con.execute(f'DROP TABLE {table}')
    con.commit()
    con.close()
    exc = refused(lambda: review(db), 'INVALID_CONTRACT')
    assert_sanitized(exc)


def test_row_count_bound_refuses(tmp_path):
    module = api()
    db = build_db(tmp_path)
    original = module.SqliteOpsBackend.gl_rows

    def unbounded(self, property_key, period):
        return [{'property': property_key, 'period': period, 'kind': 'actual',
                 'account_code': '4000', 'account_name': 'x', 'category': 'c',
                 'amount': '1.00', 'source_file': 'f.csv', 'source_row': i,
                 'created_at': '2026-05-02T00:00:00Z', 'id': str(i)}
                for i in range(module.MAX_GL_ROWS + 1)]

    module.SqliteOpsBackend.gl_rows = unbounded
    try:
        exc = refused(lambda: review(db), 'INPUT_LIMIT_EXCEEDED')
        assert_sanitized(exc)
    finally:
        module.SqliteOpsBackend.gl_rows = original


def test_nonfinite_amount_refuses(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', float('inf'))
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    exc = refused(lambda: review(db), 'INVALID_INPUT')
    assert_sanitized(exc)


def test_ambiguous_text_amount_refuses(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        con.execute("INSERT INTO gl_actuals VALUES ('ga_x', ?, 'p_2026-04', '4000',"
                    " 'Rental Income', 'rental income', '1,500.00',"
                    " 'synthetic_gl_actuals.csv', 2, '2026-05-02T00:00:00Z')", (ASSET,))
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 8, 1, 1)
    db = build_db(tmp_path, seed=seed)
    exc = refused(lambda: review(db), 'INVALID_INPUT')
    assert_sanitized(exc)


def test_dead_snapshot_denominator_refuses(tmp_path):
    def seed(con):
        add_property(con)
        add_period(con, PERIOD)
        add_gl(con, 'actual', '4000', 5000.00)
        add_gl(con, 'budget', '4000', 4400.00)
        add_mapping(con, '4000', 'rental income')
        add_snapshot(con, AS_OF, 0, 0, 0)
    db = build_db(tmp_path, seed=seed)
    exc = refused(lambda: review(db), 'IMPLICIT_ZERO_FORBIDDEN')
    assert_sanitized(exc)