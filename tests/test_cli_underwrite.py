"""Synthetic tests for the unified underwriting CLI (Task 6.2).

Self-contained public tests; no real deal bytes, no models, no network, no
engine import or execution, no threads. The CLI is a thin client over the
Task 6.1 workflow state machine: stdout stays a single machine-readable
JSON record, stderr carries typed errors only, and no source content may
ever surface in records, errors, exception chains or captured output.
All fixtures are synthetic; the canary string must never appear anywhere.
"""
from __future__ import annotations

import json
import os
import pty
import stat
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / 'harness' / 'src'
CANARY = 'PRIVATE_CANARY_RESIDENT_TEXT'

if sys.version_info >= (3, 11):
    import tomllib
else:
    import tomli as tomllib


# ------------------------------------------------------------------ fixtures


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return path


def _sources(tmp_path: Path, *, spaced: bool = False):
    root = tmp_path / 'sources'
    if spaced:
        om = _write(root / 'Synthetic Deal OM.pdf', b'synthetic om bytes ' + CANARY.encode())
        rr = _write(root / 'rent roll with spaces.pdf', b'synthetic rr bytes')
        t12 = _write(root / 't 12 statement.xlsx', b'synthetic t12 bytes')
        debt = _write(root / 'debt schedule.csv', b'synthetic debt bytes')
    else:
        om = _write(root / 'om.pdf', b'synthetic om bytes ' + CANARY.encode())
        rr = _write(root / 'rr.pdf', b'synthetic rr bytes')
        t12 = _write(root / 't12.xlsx', b'synthetic t12 bytes')
        debt = _write(root / 'debt.csv', b'synthetic debt bytes')
    return om, rr, t12, debt


def _full_args(tmp_path, om, rr, t12=None, debt=None, out=None, extra=()):
    args = ['--om', str(om), '--rr', str(rr), '--subject', 'synthetic_property']
    if t12 is not None:
        args += ['--t12', str(t12)]
    if debt is not None:
        args += ['--debt', str(debt)]
    args += ['--out', str(out if out is not None else tmp_path / 'run')]
    return args + list(extra)


def run_main(args, capsys):
    from plat_harness.cli_underwrite import main
    code = main(list(args))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _decision(status='approved', resolutions=None, decision_id='dec_001'):
    return {'decision_id': decision_id, 'status': status,
            'reviewer': 'host_reviewer',
            'resolutions': resolutions if resolutions is not None else {
                'millage': {'value': '25.31', 'evidence': 'synthetic-policy'},
                'horizon': {'value': '5', 'evidence': 'synthetic-policy'}}}


def _cli_env():
    env = {key: value for key, value in os.environ.items()
           if key != 'PYTHONPATH' and not key.startswith('PLAT_HARNESS_')}
    env['PYTHONPATH'] = str(SRC)
    return env


def _run_cli(args, *, cwd, stdin=None):
    return subprocess.run(
        [sys.executable, '-B', '-m', 'plat_harness.cli_underwrite', *args],
        cwd=str(cwd), env=_cli_env(), capture_output=True, text=True,
        stdin=stdin if stdin is not None else subprocess.DEVNULL, timeout=180)


# ------------------------------------------------------------ module + docs


def test_pyproject_registers_plat_underwrite_entry():
    project = tomllib.loads((REPO_ROOT / 'pyproject.toml').read_text(encoding='utf-8'))['project']
    scripts = project['scripts']
    assert scripts['plat-underwrite'] == 'plat_harness.cli_underwrite:main'
    assert scripts['plat-harness'] == 'plat_harness.cli:main'  # backward compatible


def test_documented_exit_codes():
    doc = (REPO_ROOT / 'docs' / 'CLI_UNDERWRITE.md').read_text(encoding='utf-8')
    assert 'plat-underwrite' in doc
    for code in ('0', '2', '3', '4'):
        assert code in doc


def test_module_source_has_no_network_process_or_engine_imports():
    from plat_harness import cli_underwrite
    source = Path(cli_underwrite.__file__).read_text(encoding='utf-8')
    for banned in ('import socket', 'urllib', 'subprocess', 'threading',
                   'requests', 'adapters', 'ingest', 'slice_b', 'openpyxl', 'xlrd'):
        assert banned not in source, banned


# --------------------------------------------------------- intake behaviour


def test_two_inputs_start_intake_needs_review(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(_full_args(tmp_path, om, rr), capsys)
    assert code == 2
    record = json.loads(out)  # stdout is machine-readable JSON
    assert record['stage'] == 'review_required'
    assert record['outcome'] == 'needs_review_or_data'
    blocked = {item['field'] for item in record['blockers']}
    assert {'t12', 'debt', 'millage', 'horizon'} <= blocked
    assert record['fields']['om']['status'] == 'present'
    assert record['certified'] is False
    run_dir = tmp_path / 'run'
    events = sorted((run_dir / 'events').iterdir())
    assert events and events[0].name == '0001.json'
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE(events[0].stat().st_mode) == 0o600


def test_full_inputs_complete_requested_stage(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    record = json.loads(out)
    assert record['stage'] == 'canonical_ready'
    assert record['outcome'] == 'complete'
    assert record['blockers'] == []
    assert record['certified'] is False


def test_explicit_early_stage_with_full_inputs_exits_complete(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5', '--stage', 'normalized'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    assert json.loads(out)['stage'] == 'normalized'


def test_missing_rr_flag_is_typed_usage_error(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(['--om', str(om)], capsys)
    assert code == 3
    payload = json.loads(err)
    assert payload['error'] == 'MISSING_INPUT'
    assert not (tmp_path / 'run').exists()


def test_missing_source_file_refuses_without_side_effect(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(
        _full_args(tmp_path, tmp_path / 'nonexistent.pdf', rr), capsys)
    assert code == 3
    payload = json.loads(err)
    assert payload['error'] == 'NOT_FOUND'
    assert not (tmp_path / 'run').exists()


def test_directory_as_source_refuses(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(_full_args(tmp_path, tmp_path, rr), capsys)
    assert code == 3
    assert json.loads(err)['error'] == 'NOT_FOUND'


def test_unsupported_suffix_refuses_without_side_effect(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    legacy = _write(tmp_path / 'legacy.xls', b'legacy biff bytes')
    code, out, err = run_main(_full_args(tmp_path, om, rr, t12=legacy), capsys)
    assert code == 3
    payload = json.loads(err)
    assert payload['error'] == 'UNSUPPORTED_INPUT_FORMAT'
    assert not (tmp_path / 'run').exists()


def test_paths_with_spaces_and_derived_subject(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path, spaced=True)
    args = ['--om', str(om), '--rr', str(rr), '--t12', str(t12),
            '--debt', str(debt), '--out', str(tmp_path / 'run dir with spaces'),
            '--millage', '25.31', '--horizon', '5']
    code, out, err = run_main(args, capsys)
    assert code == 0
    record = json.loads(out)
    assert record['subject'] == 'SyntheticDealOM'
    assert record['stage'] == 'canonical_ready'


def test_default_out_dir_under_cwd(tmp_path, capsys, monkeypatch):
    om, rr, t12, debt = _sources(tmp_path)
    monkeypatch.chdir(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt, out=None,
                      extra=['--millage', '25.31', '--horizon', '5'])
    args = [a for i, a in enumerate(args) if not (a == '--out' or
              (i > 0 and args[i - 1] == '--out'))]
    code, out, err = run_main(args, capsys)
    assert code == 0
    record = json.loads(out)
    runs_root = tmp_path / 'plat-underwrite-runs'
    assert runs_root.is_dir()
    assert record['content_identity'][:32] in os.listdir(runs_root)
    assert stat.S_IMODE((runs_root / record['content_identity'][:32]).stat().st_mode) == 0o700


# ------------------------------------------------------------------- dry run


def test_dry_run_writes_nothing(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5', '--dry-run'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    report = json.loads(out)
    assert report['dry_run'] is True
    assert report['outcome'] == 'complete'
    assert not (tmp_path / 'run').exists()
    # The identity preview must match what a real create would pin.
    real_args = _full_args(tmp_path, om, rr, t12, debt,
                           extra=['--millage', '25.31', '--horizon', '5'])
    code2, out2, _ = run_main(real_args, capsys)
    assert code2 == 0
    assert json.loads(out2)['content_identity'] == report['content_identity']


def test_dry_run_two_inputs_lists_blockers(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, extra=['--dry-run'])
    code, out, err = run_main(args, capsys)
    assert code == 2
    report = json.loads(out)
    assert report['dry_run'] is True
    blocked = {item['field'] for item in report['blockers']}
    assert {'t12', 'debt', 'millage', 'horizon'} <= blocked
    assert not (tmp_path / 'run').exists()


# -------------------------------------------------------------------- resume


def test_resume_is_idempotent(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    first = json.loads(out)
    code2, out2, err2 = run_main(['--resume', str(tmp_path / 'run')], capsys)
    assert code2 == 0
    second = json.loads(out2)
    for key in ('run_id', 'content_identity', 'stage', 'event_count'):
        assert second[key] == first[key]


def test_resume_missing_run_dir(tmp_path, capsys):
    code, out, err = run_main(['--resume', str(tmp_path / 'absent')], capsys)
    assert code == 3
    assert json.loads(err)['error'] == 'NOT_FOUND'


def test_resume_blocked_run_is_nonzero(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(_full_args(tmp_path, om, rr), capsys)
    assert code == 2
    code2, out2, err2 = run_main(['--resume', str(tmp_path / 'run')], capsys)
    assert code2 == 2
    assert json.loads(out2)['outcome'] == 'needs_review_or_data'


def test_resume_rejects_new_run_flags(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(['--resume', str(tmp_path / 'absent'), '--om', str(om)], capsys)
    assert code == 3
    assert json.loads(err)['error'] == 'INVALID_INPUT'
    code, out, err = run_main(['--resume', str(tmp_path / 'absent'), '--dry-run'], capsys)
    assert code == 3


# --------------------------------------------------------------------- review


def test_review_decision_unblocks_to_canonical_but_debt_blocks(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12)  # no debt
    code, out, err = run_main(args, capsys)
    assert code == 2
    decision = tmp_path / 'decision.json'
    decision.write_bytes(json.dumps(_decision()).encode())
    code2, out2, err2 = run_main(
        ['--resume', str(tmp_path / 'run'), '--review', str(decision)], capsys)
    assert code2 == 2  # debt still missing: blocked, never success
    record = json.loads(out2)
    assert record['stage'] == 'canonical_ready'
    assert record['outcome'] == 'needs_review_or_data'
    unresolved = {item['field'] for item in record['blockers']
                  if item['resolved_by'] is None}
    assert unresolved == {'debt'}
    # review-resolved fields stay visible with their decision id
    resolved = {item['field']: item['resolved_by'] for item in record['blockers']
                if item['resolved_by'] is not None}
    assert resolved == {'millage': 'dec_001', 'horizon': 'dec_001'}


def test_review_then_full_cycle_completes(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt)
    code, out, err = run_main(args, capsys)
    assert code == 2
    assert json.loads(out)['stage'] == 'review_required'
    decision = tmp_path / 'decision.json'
    decision.write_bytes(json.dumps(_decision()).encode())
    code2, out2, err2 = run_main(
        ['--resume', str(tmp_path / 'run'), '--review', str(decision)], capsys)
    assert code2 == 0
    record = json.loads(out2)
    assert record['stage'] == 'canonical_ready'
    assert record['outcome'] == 'complete'


def test_cancelled_review_stays_blocked(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(_full_args(tmp_path, om, rr, t12, debt), capsys)
    assert code == 2
    decision = tmp_path / 'cancelled.json'
    decision.write_bytes(json.dumps(_decision(status='cancelled', resolutions={})).encode())
    code2, out2, err2 = run_main(
        ['--resume', str(tmp_path / 'run'), '--review', str(decision)], capsys)
    assert code2 == 2
    record = json.loads(out2)
    assert record['stage'] == 'review_required'
    blocked = {item['field'] for item in record['blockers'] if item['resolved_by'] is None}
    assert {'millage', 'horizon'} <= blocked


def test_invalid_decision_file_json(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    run_main(_full_args(tmp_path, om, rr), capsys)
    bad = tmp_path / 'bad.json'
    bad.write_bytes(b'{"not": "json ' + CANARY.encode() + b'"')
    code, out, err = run_main(
        ['--resume', str(tmp_path / 'run'), '--review', str(bad)], capsys)
    assert code == 3
    payload = json.loads(err)
    assert payload['error'] == 'INVALID_DECISION_FILE'
    assert CANARY not in err and CANARY not in out


def test_review_without_resume_refuses(tmp_path, capsys):
    decision = tmp_path / 'decision.json'
    decision.write_bytes(json.dumps(_decision()).encode())
    code, out, err = run_main(['--om', 'x.pdf', '--rr', 'y.pdf',
                               '--review', str(decision)], capsys)
    assert code == 3
    assert json.loads(err)['error'] == 'INVALID_INPUT'


def test_decision_resolving_unsupported_field_refuses(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    run_main(_full_args(tmp_path, om, rr), capsys)
    decision = tmp_path / 'wrong_field.json'
    decision.write_bytes(json.dumps(_decision(
        resolutions={'t12': {'value': 'b', 'evidence': 'synthetic-policy'}})).encode())
    code, out, err = run_main(
        ['--resume', str(tmp_path / 'run'), '--review', str(decision)], capsys)
    assert code == 3
    assert json.loads(err)['error'] == 'INVALID_RESOLUTION'


# ------------------------------------------------------- collisions, dupes


def test_duplicate_run_request_refuses(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    first = json.loads(out)
    code2, out2, err2 = run_main(args, capsys)
    assert code2 == 3
    assert json.loads(err2)['error'] == 'DUPLICATE_RUN'
    current = json.loads((tmp_path / 'run' / 'run.json').read_bytes())
    assert current['event_count'] == first['event_count']


def test_output_collision_refuses(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    dirty = tmp_path / 'dirty'
    dirty.mkdir()
    (dirty / 'junk.txt').write_bytes(b'junk')
    code, out, err = run_main(_full_args(tmp_path, om, rr, out=dirty), capsys)
    assert code == 3
    assert json.loads(err)['error'] == 'OUTPUT_COLLISION'
    assert list(p.name for p in dirty.iterdir()) == ['junk.txt']


# ------------------------------------------------------- provider and stages


def test_unapproved_provider_refuses(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    for provider in ('some/unknown-model', 'anthropic/claude-synthetic'):
        code, out, err = run_main(_full_args(
            tmp_path, om, rr, extra=['--provider', provider]), capsys)
        assert code == 3
        payload = json.loads(err)
        assert payload['error'] == 'INVALID_PROVIDER'
        assert 'http' not in err
        assert not (tmp_path / 'run').exists()


def test_engine_stage_requests_refuse(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    for stage in ('execution_authorized', 'engine_executed', 'report_ready'):
        code, out, err = run_main(_full_args(
            tmp_path, om, rr, t12, debt, extra=['--stage', stage]), capsys)
        assert code == 3
        assert json.loads(err)['error'] == 'UNSUPPORTED_STAGE'
        assert not (tmp_path / 'run').exists()


# ------------------------------------------------------------- honest values


def test_invalid_millage_is_needs_review_not_zero(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    for bad in ('abc', '0'):
        code, out, err = run_main(_full_args(
            tmp_path, om, rr, t12, debt, extra=['--millage', bad]), capsys)
        assert code == 2
        payload = json.loads(err)
        assert payload['error'] == 'MISSING_MILLAGE'
        assert 'mills per $1,000' in payload['message']
        assert not (tmp_path / 'run').exists()


# ----------------------------------------------------------------- thinness


def test_missing_optional_dependencies_do_not_break_intake(tmp_path, capsys, monkeypatch):
    om, rr, t12, debt = _sources(tmp_path)
    monkeypatch.setitem(sys.modules, 'openpyxl', None)
    monkeypatch.setitem(sys.modules, 'xlrd', None)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    assert json.loads(out)['stage'] == 'canonical_ready'


def test_cli_never_touches_adapters_or_engine(tmp_path, capsys, monkeypatch):
    om, rr, t12, debt = _sources(tmp_path)
    monkeypatch.setitem(sys.modules, 'plat_harness.adapters', None)
    monkeypatch.setitem(sys.modules, 'plat_harness.ingest', None)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5'])
    code, out, err = run_main(args, capsys)
    assert code == 0


def test_no_stdin_read_in_intake(tmp_path, capsys, monkeypatch):
    om, rr, t12, debt = _sources(tmp_path)

    class _TtySpy:
        def isatty(self):
            return True

        def read(self, *args):
            raise AssertionError('CLI must not read stdin')

    monkeypatch.setattr(sys, 'stdin', _TtySpy())
    args = _full_args(tmp_path, om, rr, extra=['--non-interactive', '--json'])
    code, out, err = run_main(args, capsys)
    assert code == 2  # blocked honestly, but no prompt was attempted


# ---------------------------------------------------------------- canary/PII


def test_canary_never_surfaces_in_output(tmp_path, capsys):
    om, rr, t12, debt = _sources(tmp_path)
    code, out, err = run_main(_full_args(tmp_path, om, rr), capsys)
    assert code == 2
    assert CANARY not in out
    assert CANARY not in err


# --------------------------------------------------------------- exit code 4


def test_resume_execution_error_exit_code(tmp_path, capsys):
    from plat_harness import workflow
    om, rr, t12, debt = _sources(tmp_path)
    args = _full_args(tmp_path, om, rr, t12, debt,
                      extra=['--millage', '25.31', '--horizon', '5'])
    code, out, err = run_main(args, capsys)
    assert code == 0
    record = json.loads(out)
    run_dir = tmp_path / 'run'
    approval = {'approval_id': 'appr_001',
                'content_sha256': record['content_identity'],
                'inputs_sha256': {name: ref['sha256']
                                  for name, ref in record['inputs'].items()}}
    workflow.request_execution(run_dir, approval)
    workflow.advance(run_dir, 'engine_executed',
                     execution={'status': 'error', 'error_code': 'ENGINE_TIMEOUT'})
    code2, out2, err2 = run_main(['--resume', str(run_dir)], capsys)
    assert code2 == 4
    record2 = json.loads(out2)
    assert record2['outcome'] == 'execution_error'


# -------------------------------------------------------------- subprocesses


def test_subprocess_from_non_repo_cwd(tmp_path):
    om, rr, t12, debt = _sources(tmp_path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    args = _full_args(tmp_path, om, rr, t12, debt,
                      out=tmp_path / 'remote_run',
                      extra=['--millage', '25.31', '--horizon', '5'])
    result = _run_cli(args, cwd=elsewhere)
    assert result.returncode == 0
    record = json.loads(result.stdout)
    assert record['stage'] == 'canonical_ready'
    assert (tmp_path / 'remote_run' / 'run.json').is_file()


def test_subprocess_tty_matches_non_tty(tmp_path):
    om, rr, t12, debt = _sources(tmp_path)
    elsewhere = tmp_path / 'elsewhere'
    elsewhere.mkdir()
    args = _full_args(tmp_path, om, rr, t12, debt,
                      out=tmp_path / 'tty_run',
                      extra=['--millage', '25.31', '--horizon', '5'])
    baseline = _run_cli(args, cwd=elsewhere)
    assert baseline.returncode == 0
    master, slave = pty.openpty()
    tty_args = ['--om', args[args.index('--om') + 1], '--rr', args[args.index('--rr') + 1],
                '--t12', args[args.index('--t12') + 1], '--debt', args[args.index('--debt') + 1],
                '--out', str(tmp_path / 'tty run dir'), '--subject', 'synthetic_property',
                '--millage', '25.31', '--horizon', '5']
    try:
        proc = subprocess.Popen(
            [sys.executable, '-B', '-m', 'plat_harness.cli_underwrite', *tty_args],
            cwd=str(elsewhere), env=_cli_env(), stdin=slave,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        os.close(slave)
        out, err = proc.communicate(timeout=180)
    finally:
        os.close(master)
    assert proc.returncode == baseline.returncode
    tty_record = json.loads(out.decode())
    baseline_record = json.loads(baseline.stdout)
    assert tty_record['stage'] == baseline_record['stage']
    assert tty_record['content_identity'] == baseline_record['content_identity']
    assert tty_record['run_id'] == baseline_record['run_id']