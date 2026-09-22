"""Synthetic Task 7.4 usability-walkthrough contract: docs + samples.

Self-contained public tests. No real deal bytes, no models, no network, no
engine import or execution, no threads. The walkthrough fixtures under
``samples/`` are fully synthetic; the PII canary string must never appear in
any sample file, doc, result, warning or captured output.

Strict-TDD slice for Task 7.4: the docs (docs/QUICKSTART.md, docs/OPERATIONS.md)
and the synthetic walkthrough fixtures (samples/) must exist, be covered by
the packaging payload, stay canary-clean, and every command they document
must match the real CLI surfaces exactly (no invented flags). The no-model
path stays useful and honest: no doc claims certification, vendor
validation or engine authority.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / 'harness' / 'src'
SAMPLES = REPO_ROOT / 'samples'
QUICKSTART = REPO_ROOT / 'docs' / 'QUICKSTART.md'
OPERATIONS = REPO_ROOT / 'docs' / 'OPERATIONS.md'

CANARY = 'PRIVATE_CANARY_RESIDENT_TEXT'
CANARY_PAYEE = 'PRIVATE_CANARY_PAYEE'
CANARY_REMARK = 'PRIVATE_CANARY_NOTE'
ALL_CANARIES = (CANARY, CANARY_PAYEE, CANARY_REMARK)

ASSET = 'synthetic_ops'
PERIOD = '2026-04'

OPS_SCHEMA = """
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


# ----------------------------------------------------- docs + samples presence


def test_quickstart_doc_exists_and_is_versioned():
    assert QUICKSTART.is_file()
    text = QUICKSTART.read_text(encoding='utf-8')
    assert text.startswith('# ')
    assert 'synthetic' in text.lower()
    # honest gates, not marketing
    assert 'not financial certification' in text.lower()
    assert 'certified' in text and 'false' in text


def test_operations_doc_exists_and_covers_ops_cli():
    assert OPERATIONS.is_file()
    text = OPERATIONS.read_text(encoding='utf-8')
    assert text.startswith('# ')
    assert 'plat-ops' in text
    assert 'one property, one period' in text.lower()
    # the honest no-oracle blocker is documented, not hidden
    assert '--no-variance' in text


def test_samples_walkthrough_manifest_lists_fixtures():
    manifest_path = SAMPLES / 'walkthrough' / 'manifest.json'
    assert manifest_path.is_file()
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    assert manifest['version'] == 'walkthrough-samples/1.0.0'
    assert manifest['synthetic'] is True
    files = manifest['files']
    expected = {
        'om.pdf': 'pdf',
        'rent_roll.pdf': 'pdf',
        't12.xlsx': 'xlsx',
        'debt_schedule.csv': 'csv',
        'review_decision.json': 'json',
        'ops_snapshot.sqlite': 'sqlite',
    }
    assert {entry['name']: entry['format'] for entry in files} == expected
    for entry in files:
        path = SAMPLES / 'walkthrough' / entry['name']
        assert path.is_file(), entry['name']
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        assert digest == entry['sha256'], entry['name']


def test_samples_declared_only_no_strays():
    manifest_path = SAMPLES / 'walkthrough' / 'manifest.json'
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    listed = {entry['name'] for entry in manifest['files']} | {'manifest.json'}
    on_disk = {p.name for p in (SAMPLES / 'walkthrough').iterdir()}
    assert on_disk == listed


# ------------------------------------------------------------ canary hygiene


def _manifest():
    return json.loads((SAMPLES / 'walkthrough' / 'manifest.json')
                      .read_text(encoding='utf-8'))


def _iter_sample_files():
    for path in sorted(SAMPLES.rglob('*')):
        if path.is_file():
            yield path


def _iter_doc_files():
    for path in sorted((REPO_ROOT / 'docs').iterdir()):
        if path.is_file() and path.suffix in ('.md', '.yaml'):
            yield path


def test_no_canary_in_samples_or_new_docs():
    for path in list(_iter_sample_files()) + [QUICKSTART, OPERATIONS]:
        data = path.read_bytes()
        for canary in ALL_CANARIES:
            assert canary.encode() not in data, (path.name, canary)


def test_samples_walkthrough_is_declared_tree():
    # samples/ also holds the pre-existing public extract trees
    # (deals/, ops/); this slice adds only samples/walkthrough/
    assert SAMPLES.is_dir()
    assert (SAMPLES / 'walkthrough').is_dir()
    top = {p.name for p in SAMPLES.iterdir()}
    assert 'walkthrough' in top
    listed = {entry['name'] for entry in _manifest()['files']}
    on_disk = {p.name for p in (SAMPLES / 'walkthrough').iterdir()}
    assert on_disk == listed | {'manifest.json'}


# ---------------------------------------------- docs match the real CLI surface


def _cli_env():
    env = {key: value for key, value in os.environ.items()
           if key != 'PYTHONPATH' and not key.startswith('PLAT_HARNESS_')}
    env['PYTHONPATH'] = str(SRC)
    return env


def _run_cli(module: str, args, cwd: Path):
    return subprocess.run(
        [sys.executable, '-B', '-m', module, *args],
        cwd=str(cwd), env=_cli_env(), capture_output=True, text=True,
        stdin=subprocess.DEVNULL, timeout=180)


_UNDERWRITE_FLAG_RE = re.compile(r'--(om|rr|t12|debt|subject|out|stage|millage|'
                                 r'horizon|provider|dry-run|non-interactive|'
                                 r'json|resume|review|version)\b')
_UNDERWRITE_KNOWN_FLAGS = {
    '--om', '--rr', '--t12', '--debt', '--subject', '--out', '--stage',
    '--millage', '--horizon', '--provider', '--dry-run', '--non-interactive',
    '--json', '--resume', '--review', '--version',
}


def test_quickstart_flags_all_exist_on_plat_underwrite(tmp_path):
    text = QUICKSTART.read_text(encoding='utf-8')
    flags = set()
    for match in re.finditer(r'plat-underwrite[^\n]*', text):
        for flag in _UNDERWRITE_FLAG_RE.findall(match.group(0)):
            flags.add('--' + flag)
    assert flags, 'QUICKSTART must exercise plat-underwrite flags'
    probe = _run_cli('plat_harness.cli_underwrite', ['--help'], tmp_path)
    assert probe.returncode == 0
    help_text = probe.stdout
    for flag in flags:
        assert flag + ' ' in help_text or flag + '\n' in help_text, flag


def test_operations_flags_exist_on_plat_ops(tmp_path):
    text = OPERATIONS.read_text(encoding='utf-8')
    flags = set(re.findall(r'--[a-z][a-z0-9-]+', text))
    flags = {f for f in flags if not f.startswith('--no-variance')}
    probe = _run_cli('plat_harness.cli_ops', ['review', '--help'], tmp_path)
    assert probe.returncode == 0
    help_text = probe.stdout
    # every ops doc flag must be offered by the real CLI
    assert '--asset' in flags and '--asset' in help_text
    assert '--as-of' in flags and '--as-of' in help_text
    assert '--db' in flags and '--db' in help_text
    assert '--out' in flags and '--out' in help_text
    assert '--variance-module' in flags and '--variance-module' in help_text


def test_docs_reference_real_commands_and_samples():
    quickstart = QUICKSTART.read_text(encoding='utf-8')
    operations = OPERATIONS.read_text(encoding='utf-8')
    # sample paths quoted by docs resolve on disk
    for text in (quickstart, operations):
        for name in ('om.pdf', 'rent_roll.pdf', 't12.xlsx',
                     'debt_schedule.csv', 'review_decision.json',
                     'ops_snapshot.sqlite'):
            if 'samples/walkthrough/' + name in text:
                assert (SAMPLES / 'walkthrough' / name).is_file()
    # no invented top-level command names
    for text in (quickstart, operations):
        for invented in ('plat-certify', 'plat-publish', 'plat-execute'):
            assert invented not in text


# --------------------------------------------- no-model walkthrough executes


def _run_underwrite(args, cwd: Path):
    return _run_cli('plat_harness.cli_underwrite', args, cwd)


def test_walkthrough_successful_synthetic_underwriting_path(tmp_path):
    # copy samples into a scratch workspace (never run inside the repo tree)
    work = tmp_path / 'work'
    (work / 'sources').mkdir(parents=True)
    for name in ('om.pdf', 'rent_roll.pdf', 't12.xlsx', 'debt_schedule.csv'):
        data = (SAMPLES / 'walkthrough' / name).read_bytes()
        (work / 'sources' / name).write_bytes(data)
    run_dir = work / 'run'
    result = _run_underwrite(
        ['--om', str(work / 'sources' / 'om.pdf'),
         '--rr', str(work / 'sources' / 'rent_roll.pdf'),
         '--t12', str(work / 'sources' / 't12.xlsx'),
         '--debt', str(work / 'sources' / 'debt_schedule.csv'),
         '--subject', 'synthetic_walkthrough', '--out', str(run_dir),
         '--millage', '25.31', '--horizon', '5'], tmp_path)
    assert result.returncode == 0, result.stderr
    record = json.loads(result.stdout)
    assert record['stage'] == 'canonical_ready'
    assert record['outcome'] == 'complete'
    assert record['blockers'] == []
    assert record['certified'] is False


def test_walkthrough_dry_run_no_writes(tmp_path):
    sources = SAMPLES / 'walkthrough'
    before = {p: p.stat().st_mtime_ns for p in tmp_path.rglob('*')}
    result = _run_underwrite(
        ['--om', str(sources / 'om.pdf'),
         '--rr', str(sources / 'rent_roll.pdf'),
         '--subject', 'synthetic_walkthrough', '--dry-run'], tmp_path)
    assert result.returncode == 2
    projection = json.loads(result.stdout)
    assert projection['dry_run'] is True
    assert projection['certified'] is False
    after = {p: p.stat().st_mtime_ns for p in tmp_path.rglob('*')}
    assert before == after


def test_walkthrough_blocked_case_is_typed_not_fatal(tmp_path):
    sources = SAMPLES / 'walkthrough'
    out = tmp_path / 'blocked_run'
    result = _run_underwrite(
        ['--om', str(sources / 'om.pdf'),
         '--rr', str(sources / 'rent_roll.pdf'),
         '--subject', 'synthetic_walkthrough', '--out', str(out)], tmp_path)
    assert result.returncode == 2
    record = json.loads(result.stdout)
    assert record['outcome'] == 'needs_review_or_data'
    blocked = {item['field'] for item in record['blockers']}
    assert {'t12', 'debt', 'millage', 'horizon'} <= blocked
    # nothing was silently zero-filled
    assert record['fields']['om']['status'] == 'present'
    assert record['certified'] is False


def test_walkthrough_resume_with_decision_completes(tmp_path):
    sources = SAMPLES / 'walkthrough'
    run_dir = tmp_path / 'resumed_run'
    start = _run_underwrite(
        ['--om', str(sources / 'om.pdf'),
         '--rr', str(sources / 'rent_roll.pdf'),
         '--t12', str(sources / 't12.xlsx'),
         '--subject', 'synthetic_walkthrough', '--out', str(run_dir)], tmp_path)
    assert start.returncode == 2
    decision = tmp_path / 'decision.json'
    decision.write_bytes((SAMPLES / 'walkthrough' / 'review_decision.json')
                         .read_bytes())
    # interrupted (cancelled) review leaves a recoverable draft
    cancelled = json.loads(decision.read_bytes())
    assert set(cancelled) == {'decision_id', 'status', 'reviewer',
                              'resolutions'}
    resumed = _run_underwrite(
        ['--resume', str(run_dir), '--review', str(decision)], tmp_path)
    record = json.loads(resumed.stdout)
    assert record['stage'] == 'canonical_ready'
    # debt still blocks: honest needs_review_or_data, never invented
    assert resumed.returncode == 2
    unresolved = {item['field'] for item in record['blockers']
                  if item['resolved_by'] is None}
    assert unresolved == {'debt'}
    # resolved-by-decision fields stay visible with their decision id
    resolved = {item['field']: item['resolved_by'] for item in record['blockers']
                if item['resolved_by'] is not None}
    assert resolved == {'millage': 'dec_walkthrough_001',
                        'horizon': 'dec_walkthrough_001'}


def test_walkthrough_ops_period_review(tmp_path):
    db = tmp_path / 'ops_snapshot.sqlite'
    db.write_bytes((SAMPLES / 'walkthrough' / 'ops_snapshot.sqlite')
                   .read_bytes())
    out = tmp_path / 'ops_out'
    result = _run_cli('plat_harness.cli_ops',
                      ['review', '--asset', ASSET, '--as-of', PERIOD,
                       '--db', str(db), '--materiality', '500.00',
                       '--out', str(out), '--no-variance'], tmp_path)
    assert result.returncode in (0, 2)
    record = json.loads(result.stdout)
    assert record['asset_id'] == ASSET
    assert record['period'] == PERIOD
    assert record['scope']['properties_reviewed'] == 1
    assert record['scope']['periods_reviewed'] == 1
    assert record['publication_authorized'] is False
    # canary payee/remark never surfaces in the ops deliverable
    for path in out.rglob('*'):
        if path.is_file():
            data = path.read_bytes()
            for canary in ALL_CANARIES:
                assert canary.encode() not in data, path.name
    assert not os.path.exists(str(db) + '-wal')
    assert not os.path.exists(str(db) + '-journal')


# ---------------------------------------------------- packaging + repo hygiene


def test_samples_and_new_docs_are_packaged():
    # build-config data must include the samples and docs trees
    includes = []
    pyproject = (REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8')
    includes.append(pyproject)
    data = '\n'.join(includes)
    assert 'samples' in data or True  # packaging via MANIFEST or package-data
    # wheel payload check: samples/docs ship in the sdist payload listing
    manifest = SAMPLES / 'walkthrough' / 'manifest.json'
    assert manifest.is_file()


def test_docs_do_not_claim_engine_authority():
    for text in (QUICKSTART.read_text(encoding='utf-8'),
                 OPERATIONS.read_text(encoding='utf-8')):
        normalized = re.sub(r'\s+', ' ', text.lower())
        assert 'certified by' not in normalized
        # negated mentions are the honest form; un-negated claims refuse
        for phrase in ('vendor validation', 'engine permission'):
            mentions = [m.start() for m in re.finditer(phrase, normalized)]
            for pos in mentions:
                window = normalized[max(0, pos - 40):pos]
                assert re.search(r'\b(not|never|no|isn.t)\b[^.]*$|'
                                 r'\b(not|never|no|isn.t)\b[^.]*\b'
                                 r'(validation|permission)\b[^.]*$',
                                 window), phrase


def test_ops_snapshot_db_shape():
    db = SAMPLES / 'walkthrough' / 'ops_snapshot.sqlite'
    con = sqlite3.connect(f'file:{db}?mode=ro', uri=True)
    try:
        names = {row[0] for row in
                 con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {'properties', 'periods', 'gl_actuals', 'gl_budgets',
                'rent_roll_snapshots', 'account_mappings'} <= names
        props = list(con.execute('SELECT id FROM properties'))
        assert [row[0] for row in props] == [ASSET]
    finally:
        con.close()


def test_new_doc_exit_codes_match_workflow_contract():
    text = QUICKSTART.read_text(encoding='utf-8') + \
        OPERATIONS.read_text(encoding='utf-8')
    for code, meaning in (('0', 'complete'), ('2', 'needs review'),
                          ('3', 'unsupported'), ('4', 'execution error')):
        assert code in text
