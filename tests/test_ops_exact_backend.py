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
