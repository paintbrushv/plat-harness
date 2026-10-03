"""Exact storage contract: cents, content identity, and schema refusal."""
import hashlib
import json
import sqlite3

import pytest

from plat_harness.adapters.ops_review import ExactSqliteOpsBackend, review_period
from plat_harness.errors import HarnessError


def database(tmp_path):
    path = tmp_path / 'exact.sqlite'
    line = {'account_code': '4000', 'account_name': 'Rent',
            'category': 'rental income', 'amount': '92233720368547758.07'}
    data = {'property': 'synthetic_ops', 'period': '2026-05', 'currency': 'USD',
            'expense_convention': 'positive_costs', 'unit_count': 10,
            'actuals': [line], 'budgets': [], 'snapshot': None}
    digest = hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()
    with sqlite3.connect(path) as con:
        con.executescript('''
          PRAGMA application_id=1347174740;
          PRAGMA user_version=1;
          CREATE TABLE exact_revisions(id,property,period,revision,unit_count,created_at,input_sha256);
          CREATE TABLE exact_gl(revision_id,kind,ordinal,account_code,account_name,category,amount_cents);
          CREATE TABLE exact_snapshots(revision_id,as_of_date,occupied_units,vacant_units,down_units);
          CREATE TABLE exact_snapshot_money(revision_id,field,amount_cents);
        ''')
        con.execute('INSERT INTO exact_revisions VALUES (?,?,?,?,?,?,?)',
                    ('revision', 'synthetic_ops', '2026-05', 1, 10, '2026-05-31', digest))
        con.execute('INSERT INTO exact_gl VALUES (?,?,?,?,?,?,?)',
                    ('revision', 'actual', 0, '4000', 'Rent', 'rental income', 9223372036854775807))
    return path


def test_integer_limit_never_round_trips_through_float(tmp_path):
    path = database(tmp_path)
    backend = ExactSqliteOpsBackend(path)
    rows = backend.gl_rows('synthetic_ops', '2026-05')
    assert rows[0]['amount'] == '92233720368547758.07'
    result = review_period('synthetic_ops', '2026-05', db_path=path,
                           materiality={'variance_abs': '500.00', 'currency': 'USD'})
    assert result['contract_version'] == 'ops-review/2.0.0'
    assert any(e['code'] == 'MISSING_BUDGET' for e in result['exceptions'])
    assert backend.account_mappings('synthetic_ops') == []


def test_modified_contents_and_incomplete_schema_refuse(tmp_path):
    path = database(tmp_path)
    with sqlite3.connect(path) as con:
        con.execute('UPDATE exact_gl SET amount_cents=1')
    with pytest.raises(HarnessError, match='canonical input hash'):
        ExactSqliteOpsBackend(path).gl_rows('synthetic_ops', '2026-05')
    with sqlite3.connect(path) as con:
        con.execute('PRAGMA user_version=0')
    with pytest.raises(HarnessError, match='incomplete or unsupported'):
        ExactSqliteOpsBackend(path)


def test_prior_snapshot_is_verified_and_citations_identify_exact_rows(tmp_path):
    path = database(tmp_path)
    snapshot = {'as_of_date': '2026-04-30', 'occupied_units': 19, 'vacant_units': 1,
                'down_units': 0, 'market_rent_total': '123.45', 'in_place_rent_total': None,
                'delinquent_amount': None, 'prepaid_amount': None, 'concessions_amount': None}
    data = {'property': 'synthetic_ops', 'period': '2026-04', 'currency': 'USD',
            'expense_convention': 'positive_costs', 'unit_count': 20,
            'actuals': [], 'budgets': [], 'snapshot': snapshot}
    digest = hashlib.sha256(json.dumps(data, separators=(',', ':')).encode()).hexdigest()
    with sqlite3.connect(path) as con:
        con.execute('INSERT INTO exact_revisions VALUES (?,?,?,?,?,?,?)',
                    ('prior', 'synthetic_ops', '2026-04', 1, 20, '2026-04-30', digest))
        con.execute('INSERT INTO exact_snapshots VALUES (?,?,?,?,?)',
                    ('prior', '2026-04-30', 19, 1, 0))
        con.execute('INSERT INTO exact_snapshot_money VALUES (?,?,?)',
                    ('prior', 'market_rent_total', 12345))
    backend = ExactSqliteOpsBackend(path)
    backend.gl_rows('synthetic_ops', '2026-05')
    assert backend.occupancy_snapshots('synthetic_ops', '2026-05-31')[0]['occupied'] == 19
    assert backend.unit_count('synthetic_ops') == 10
    result = review_period('synthetic_ops', '2026-05', db_path=path,
                           materiality={'variance_abs': '500.00', 'currency': 'USD'})
    assert result['occupancy']['current']['source'][0]['table'] == 'exact_snapshots'
    missing = next(e for e in result['exceptions'] if e['code'] == 'MISSING_BUDGET')
    assert missing['evidence'][0]['table'] == 'exact_gl'
    assert ':revision:actual:' in missing['evidence'][0]['artifact']
    assert missing['evidence'][0]['row'] == 1  # exact_gl.ordinal 0, display row 1
    # A concurrent new revision must not replace the snapshot for the GL
    # revision already selected by this review.
    newer = {**data, 'period': '2026-05', 'unit_count': 10,
             'snapshot': {**snapshot, 'as_of_date': '2026-05-31', 'occupied_units': 9}}
    newer_hash = hashlib.sha256(json.dumps(newer, separators=(',', ':')).encode()).hexdigest()
    with sqlite3.connect(path) as con:
        con.execute('INSERT INTO exact_revisions VALUES (?,?,?,?,?,?,?)',
                    ('newer', 'synthetic_ops', '2026-05', 2, 10, '2026-05-31', newer_hash))
        con.execute('INSERT INTO exact_snapshots VALUES (?,?,?,?,?)',
                    ('newer', '2026-05-31', 9, 1, 0))
        con.execute('INSERT INTO exact_snapshot_money VALUES (?,?,?)',
                    ('newer', 'market_rent_total', 12345))
    assert [r['id'] for r in backend.occupancy_snapshots('synthetic_ops', '2026-05-31')] == ['prior']
    with sqlite3.connect(path) as con:
        con.execute("UPDATE exact_snapshots SET occupied_units=18,vacant_units=2 WHERE revision_id='prior'")
    with pytest.raises(HarnessError, match='canonical input hash'):
        review_period('synthetic_ops', '2026-05', db_path=path,
                      materiality={'variance_abs': '500.00', 'currency': 'USD'})
