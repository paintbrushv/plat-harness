"""Synthetic tests for the read-only ops CLI: ``plat-ops`` (Task 6.4).

Self-contained public tests; synthetic SQLite ops fixtures only — no real
deal bytes, no resident rows, no models, no network, no writes to the source
database. The CLI is a thin client over the Task 3.5 ``ops-review/1.0.0``
seam: one property, one period, no cross-property aggregation, missing
budget is a blocker (never zero), and a missing ops backend is a typed
blocker, never a fabricated report. stdout is a single machine-readable
JSON record; typed refusals go to stderr; exit codes follow the documented
workflow outcomes. All fixtures are synthetic; canaries must never surface
in records, errors, exception chains or captured output.
"""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / 'harness' / 'src'
CANARY_PAYEE = 'PRIVATE_CANARY_PAYEE'
CANARY_REMARK = 'PRIVATE_CANARY_NOTE'
CANARY = 'PRIVATE_CANARY_RESIDENT_TEXT'

ASSET = 'synthetic_ops'
PERIOD = '2026-04'
AS_OF = '2026-04-30'
MATERIALITY = {'variance_abs': '500.00', 'currency': 'USD'}

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
"""


def api():
    """Import the Task 6.4 seam; the RED run may raise ModuleNotFoundError."""
    from plat_harness import cli_ops
    return cli_ops


def reporting_watermark() -> str:
    from plat_harness import reporting
    return reporting.WATERMARK


# --------------------------------------------------------- synthetic fixtures

def build_db(tmp_path: Path) -> Path:
    path = tmp_path / 'ops.db'
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    con.execute("INSERT INTO properties VALUES (?,?,?,?,?,?,?)",
                (ASSET, 'Synthetic_Ops', 'synthetic_market', 10,
                 'synthetic_owner', 'synthetic_pm', '2026-01-01T00:00:00Z'))
    for label in ('2026-03', PERIOD):
        con.execute("INSERT INTO periods VALUES (?,?,?,?)",
                    (f'p_{label}', int(label[:4]), int(label[5:7]), label))
    con.execute("INSERT INTO gl_actuals VALUES (?,?,?,?,?,?,?,?,?,?)",
                ('ga_4000', ASSET, f'p_{PERIOD}', '4000', 'Rental Income',
                 'rental income', 5000.00, 'synthetic_gl_actuals.csv', 2,
                 '2026-05-02T00:00:00Z'))
    con.execute("INSERT INTO gl_actuals VALUES (?,?,?,?,?,?,?,?,?,?)",
                ('ga_6100', ASSET, f'p_{PERIOD}', '6100',
                 'Repairs & Maintenance', 'repairs & maintenance', 1200.50,
                 'synthetic_gl_actuals.csv', 3, '2026-05-02T00:00:00Z'))
    con.execute("INSERT INTO gl_budgets VALUES (?,?,?,?,?,?,?,?,?,?)",
                ('gb_4000', ASSET, f'p_{PERIOD}', '4000', 'Rental Income',
                 'rental income', 4400.00, 'synthetic_gl_budgets.csv', 2,
                 '2026-05-02T00:00:00Z'))
    con.execute("INSERT INTO gl_budgets VALUES (?,?,?,?,?,?,?,?,?,?)",
                ('gb_6100', ASSET, f'p_{PERIOD}', '6100',
                 'Repairs & Maintenance', 'repairs & maintenance', 1100.00,
                 'synthetic_gl_budgets.csv', 3, '2026-05-02T00:00:00Z'))
    con.execute("INSERT INTO rent_roll_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ('rr_2026-03-31', ASSET, '2026-03-31', 7, 2, 7, 0, 1,
                 15000.0, 14000.0, 'synthetic_rent_roll.csv', 1,
                 '2026-04-01T00:00:00Z'))
    con.execute("INSERT INTO rent_roll_snapshots VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (f'rr_{AS_OF}', ASSET, AS_OF, 8, 1, 8, 0, 1,
                 15000.0, 14000.0, 'synthetic_rent_roll.csv', 1,
                 '2026-05-01T00:00:00Z'))
    con.execute("INSERT INTO account_mappings VALUES (?,?,?,?,?,?,?,?,?,?)",
                ('m_etl_4000', 'synthetic_etl', ASSET, '4000', 'Rental Income',
                 'rental income', 0.9, 'approved',
                 '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
    con.execute("INSERT INTO account_mappings VALUES (?,?,?,?,?,?,?,?,?,?)",
                ('m_etl_6100', 'synthetic_etl', ASSET, '6100',
                 'Repairs & Maintenance', 'repairs & maintenance', 0.9,
                 'approved', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'))
    con.commit()
    con.close()
    return path


ORACLE_SOURCE = '''
"""Labeled synthetic stand-in for the ops owner (boxscore::variance).

Mirrors compute_account_variances + compute_noi_bridge semantics: per-
account sums, variance = actual - budget, revenue/expense classification.
Synthetic only; no real deal bytes.
"""
from decimal import Decimal

REVENUE_CATEGORIES = frozenset(
    {'rental income', 'concessions', 'bad debt', 'other income'})
UNMAPPED_CATEGORIES = frozenset({'unmapped'})


def _fmt(value):
    return f'{value:.2f}'


def compute_account_variances(actuals, budgets):
    totals = {}
    for row in actuals:
        key = (row['account_code'], row['account_name'], row['category'])
        totals.setdefault(key, [Decimal('0'), Decimal('0')])[0] += Decimal(
            row['amount'])
    for row in budgets:
        key = (row['account_code'], row['account_name'], row['category'])
        totals.setdefault(key, [Decimal('0'), Decimal('0')])[1] += Decimal(
            row['amount'])
    rows = []
    for (code, name, category), (actual, budget) in sorted(totals.items()):
        rows.append({
            'account_code': code, 'account_name': name, 'category': category,
            'actual': _fmt(actual), 'budget': _fmt(budget),
            'variance': _fmt(actual - budget)})
    return rows


def compute_noi_bridge(actuals, budgets):
    rev_a = rev_b = exp_a = exp_b = unm_a = unm_b = Decimal('0')
    for row in compute_account_variances(actuals, budgets):
        actual, budget = Decimal(row['actual']), Decimal(row['budget'])
        if row['category'] in REVENUE_CATEGORIES:
            rev_a += actual
            rev_b += budget
        elif row['category'] in UNMAPPED_CATEGORIES:
            unm_a += actual
            unm_b += budget
        else:
            exp_a += actual
            exp_b += budget
    return {
        'actual_revenue': _fmt(rev_a), 'budget_revenue': _fmt(rev_b),
        'revenue_variance': _fmt(rev_a - rev_b),
        'actual_expenses': _fmt(exp_a), 'budget_expenses': _fmt(exp_b),
        'expense_variance': _fmt(exp_a - exp_b),
        'actual_noi': _fmt(rev_a - exp_a), 'budget_noi': _fmt(rev_b - exp_b),
        'noi_variance': _fmt((rev_a - exp_a) - (rev_b - exp_b)),
        'unmapped_actual': _fmt(unm_a), 'unmapped_budget': _fmt(unm_b),
    }
'''


def write_oracle_module(tmp_path: Path, name='synthetic_ops_oracle'):
    """Write the labeled synthetic oracle module onto a temp sys.path dir."""
    import sys
    root = tmp_path / 'oracle_path'
    root.mkdir(exist_ok=True)
    (root / (name + '.py')).write_text(ORACLE_SOURCE, encoding='utf-8')
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    return name, root


def _cli_env():
    env = {key: value for key, value in os.environ.items()
           if key != 'PYTHONPATH' and not key.startswith('PLAT_HARNESS_')}
    env['PYTHONPATH'] = str(SRC)
    return env


def _run_cli(args, *, cwd):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'plat_harness.cli_ops', *args],
        cwd=str(cwd), env=_cli_env(), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=180)


def _review_args(db: Path, out: Path, extra=()):
    return (['review', '--asset', ASSET, '--as-of', PERIOD,
              '--db', str(db), '--materiality', '500.00',
              '--out', str(out)] + list(extra))


def _full_review_args(db: Path, out: Path, tmp_path: Path, extra=()):
    """Happy-path args with the labeled synthetic oracle bound."""
    name, _ = write_oracle_module(tmp_path)
    return _review_args(db, out, extra=('--variance-module', name))


# ------------------------------------------------------------ module contract

def test_version_contract():
    assert api().VERSION == 'cli-ops/1.0.0'


def test_exit_codes_are_documented_workflow_outcomes():
    cli = api()
    assert cli.EXIT_CODES == {'complete': 0, 'needs_review_or_data': 2,
                              'unsupported_input': 3, 'execution_error': 4}


# ------------------------------------------------------------------ happy path

def test_review_writes_ops_deliverable(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    out = tmp_path / 'ops-report'
    code = cli.main(list(_full_review_args(db, out, tmp_path)))
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out)
    assert payload['outcome'] == 'complete'
    assert payload['status'] == 'reviewed'
    assert payload['scope']['properties_reviewed'] == 1
    assert payload['scope']['periods_reviewed'] == 1
    assert payload['asset_id'] == ASSET
    assert payload['period'] == PERIOD
    for name in ('report.json', 'summary.md', 'metrics.csv'):
        assert (out / name).is_file(), name
    # deliverable JSON mirrors the review record plus reporting contract
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert report['subject'] == ASSET
    assert report['metrics']['noi_variance']['amount'] == \
        payload['variance']['noi_bridge']['noi_variance']['amount']
    summary = (out / 'summary.md').read_text(encoding='utf-8')
    assert ASSET in summary and PERIOD in summary
    for canary in (CANARY, CANARY_PAYEE, CANARY_REMARK):
        assert canary not in captured.out
        assert canary not in captured.err
        assert canary not in summary
        assert canary not in (out / 'metrics.csv').read_text(encoding='utf-8')


def test_review_is_deterministic(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    one, two = tmp_path / 'r1', tmp_path / 'r2'
    assert cli.main(list(_full_review_args(db, one, tmp_path))) == 0
    capsys.readouterr()
    assert cli.main(list(_full_review_args(db, two, tmp_path))) == 0
    capsys.readouterr()
    for name in ('report.json', 'summary.md', 'metrics.csv'):
        a = (one / name).read_bytes()
        b = (two / name).read_bytes()
        assert a == b, name


def test_review_is_read_only_on_source_db(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    before = db.read_bytes()
    code = cli.main(list(_full_review_args(db, tmp_path / 'out', tmp_path)))
    capsys.readouterr()
    assert code == 0
    assert db.read_bytes() == before


# ------------------------------------------------------------- blocked paths

def test_missing_db_is_typed_blocker_not_fabricated(tmp_path, capsys):
    cli = api()
    out = tmp_path / 'out'
    code = cli.main(list(_review_args(tmp_path / 'missing.db', out)))
    captured = capsys.readouterr()
    assert code == 3
    payload = json.loads(captured.err)
    assert payload['error'] == 'NOT_FOUND'
    assert not out.exists()


def test_wildcard_asset_refuses(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    args = _review_args(db, tmp_path / 'out')
    args[args.index('--asset') + 1] = 'all'
    code = cli.main(list(args))
    captured = capsys.readouterr()
    assert code == 3
    assert json.loads(captured.err)['error'] == 'INVALID_INPUT'


def test_no_oracle_leaves_blocked_review_not_failure(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    out = tmp_path / 'out'
    code = cli.main(list(_review_args(db, out, extra=['--no-variance'])))
    captured = capsys.readouterr()
    assert code == 2
    payload = json.loads(captured.out)
    assert payload['outcome'] == 'needs_review_or_data'
    assert payload['status'] == 'blocked'
    codes = {exc['code'] for exc in payload['exceptions']}
    assert 'VARIANCE_NOT_IMPLEMENTED' in codes
    summary = (out / 'summary.md').read_text(encoding='utf-8')
    assert reporting_watermark() in summary  # blocked stays watermarked
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    # missing oracle metric renders null, never zero, never fabricated
    assert report['metrics']['noi_variance'] is None
    assert report['metrics']['noi_variance'] is not False


def test_bad_period_refuses(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    args = _review_args(db, tmp_path / 'out')
    args[args.index('--as-of') + 1] = '2026-4'
    code = cli.main(list(args))
    captured = capsys.readouterr()
    assert code == 3
    assert json.loads(captured.err)['error'] == 'INVALID_INPUT'


def test_missing_required_flag_refuses(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    args = _review_args(db, tmp_path / 'out')
    # drop every --asset occurrence and its value
    pruned = []
    skip = False
    for item in args:
        if item == '--asset':
            skip = True
            continue
        if skip:
            skip = False
            continue
        pruned.append(item)
    code = cli.main(pruned)
    captured = capsys.readouterr()
    assert code == 3
    assert json.loads(captured.err)['error'] == 'MISSING_INPUT'


def test_output_collision_refuses(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    out = tmp_path / 'out'
    out.mkdir()
    (out / 'report.json').write_text('existing', encoding='utf-8')
    code = cli.main(list(_full_review_args(db, out, tmp_path)))
    captured = capsys.readouterr()
    assert code == 3
    assert json.loads(captured.err)['error'] == 'OUTPUT_COLLISION'
    assert (out / 'report.json').read_text(encoding='utf-8') == 'existing'


# ------------------------------------------------------------ deliverable gate

def test_report_generation_is_not_permission_to_publish(tmp_path, capsys):
    cli = api()
    db = build_db(tmp_path)
    out = tmp_path / 'out'
    assert cli.main(list(_full_review_args(db, out, tmp_path))) == 0
    payload = json.loads(capsys.readouterr().out)
    report = json.loads((out / 'report.json').read_text(encoding='utf-8'))
    assert payload['publication_authorized'] is False
    assert report['publication_authorized'] is False


def test_unknown_command_refuses(tmp_path, capsys):
    cli = api()
    code = cli.main(['execute', '--asset', ASSET])
    captured = capsys.readouterr()
    assert code == 3
    assert json.loads(captured.err)['error'] == 'UNSUPPORTED_COMMAND'


def test_module_import_boundary():
    source = Path(api().__file__).read_text(encoding='utf-8')
    for banned in ('import socket', 'urllib', 'threading', 'requests',
                   'openpyxl', 'xlrd'):
        assert banned not in source, banned


# ---------------------------------------------------------------- subprocess

def test_subprocess_from_non_repo_cwd(tmp_path):
    db = build_db(tmp_path)
    out = tmp_path / 'out'
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    name, oracle_root = write_oracle_module(tmp_path)
    env = _cli_env()
    env['PYTHONPATH'] = env['PYTHONPATH'] + os.pathsep + str(oracle_root)
    done = subprocess.run(
        [sys.executable, '-B', '-m', 'plat_harness.cli_ops']
        + list(_review_args(db, out, extra=('--variance-module', name))),
        cwd=str(elsewhere), env=env, capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=180)
    assert done.returncode == 0, done.stderr
    payload = json.loads(done.stdout)
    assert payload['status'] == 'reviewed'
    assert (out / 'report.json').is_file()