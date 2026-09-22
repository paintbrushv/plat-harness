"""Synthetic tests for the reusable workflow state machine (Task 6.1).

Self-contained public tests; no real deal bytes, no models, no network, no
threads, and no import or execution of the engine repository. Stage names are
workflow states, never financial certification: ``execution_authorized``
does not mean certified. All fixtures are synthetic; the canary string must
never surface in records, errors, exception chains or captured output.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import threading
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError

CANARY = 'PRIVATE_CANARY_RESIDENT_TEXT'
SUBJECT = 'synthetic_property'
FULL_VALUES = {'millage': '25.31', 'horizon': '5'}
ARTIFACT_A = 'a' * 64
ARTIFACT_B = 'b' * 64


def api():
    """Import the Task 6.1 seam; the RED run may raise ModuleNotFoundError."""
    from plat_harness import workflow
    return workflow


def _write(path: Path, data: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    os.chmod(path, 0o600)
    return path


def _pin(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _ref(path: Path, fmt: str = 'pdf') -> dict:
    return {'sha256': _pin(path.read_bytes()), 'path': str(path), 'format': fmt}


def _sources(tmp_path: Path):
    root = tmp_path / 'sources'
    om = _write(root / 'om.pdf', b'synthetic offering memorandum ' + CANARY.encode())
    rr = _write(root / 'rr.pdf', b'synthetic rent roll rows')
    t12 = _write(root / 't12.xlsx', b'synthetic t12 workbook bytes')
    debt = _write(root / 'debt.csv', b'synthetic debt schedule bytes')
    return om, rr, t12, debt


def _full_inputs(om, rr, t12, debt) -> dict:
    return {'om': _ref(om), 'rr': _ref(rr),
            't12': _ref(t12, 'xlsx'), 'debt': _ref(debt, 'csv')}


def _create(tmp_path, inputs, values=None, *, name='run', engine_backend='engine_stub',
            policy_sha256=None, code_sha256=None):
    wf = api()
    return wf.create_run(tmp_path / name, SUBJECT, inputs, values,
                         policy_sha256=policy_sha256, code_sha256=code_sha256,
                         engine_backend=engine_backend)  # type: ignore[arg-type]


def _decision(decision_id, status, resolutions=None, reviewer='host_reviewer'):
    return {'decision_id': decision_id, 'status': status,
            'reviewer': reviewer, 'resolutions': resolutions or {}}


def _approval(record, *, content=None, inputs_pins=None):
    return {'approval_id': 'appr_001',
            'content_sha256': content if content is not None else record['content_identity'],
            'inputs_sha256': inputs_pins if inputs_pins is not None
            else {n: i['sha256'] for n, i in record['inputs'].items()}}


def _refuse(code, fn, *args, **kwargs) -> HarnessError:
    with pytest.raises(HarnessError) as caught:
        fn(*args, **kwargs)
    assert caught.value.code == code
    return caught.value


def _to_canonical(tmp_path, *, inputs=None, values=FULL_VALUES, engine_backend='engine_stub'):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    if inputs is None:
        inputs = _full_inputs(om, rr, t12, debt)
    _create(tmp_path, inputs, values, engine_backend=engine_backend)
    run_dir = tmp_path / 'run'
    for stage in ('normalized', 'reconciled', 'canonical_ready'):
        wf.advance(run_dir, stage)
    return wf.resume(run_dir)


# --- contract -------------------------------------------------------------

def test_contract_constants():
    wf = api()
    assert wf.VERSION == 'workflow/1.0.0'
    assert tuple(wf.STAGES) == ('inspected', 'normalized', 'review_required', 'reconciled',
                                'canonical_ready', 'execution_authorized', 'engine_executed',
                                'report_ready')
    assert 'certified' not in wf.STAGES
    assert tuple(wf.BLOCKER_KINDS) == ('pending_human_action', 'unsupported_engineering',
                                       'corrupt_input')
    assert tuple(wf.OUTCOMES) == ('complete', 'needs_review_or_data', 'unsupported_input',
                                  'execution_error')


def test_full_lifecycle_reaches_report_ready_without_certification(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    record = wf.request_execution(run_dir, _approval(record))
    assert record['stage'] == 'execution_authorized'
    record = wf.advance(run_dir, 'engine_executed', artifact_sha256=ARTIFACT_A)
    record = wf.advance(run_dir, 'report_ready', artifact_sha256=ARTIFACT_B)
    assert record['stage'] == 'report_ready'
    assert record['certified'] is False
    assert record['outcome'] == 'complete'
    assert record['run_id'] == wf.resume(run_dir)['run_id']


# --- creation and pins ------------------------------------------------------

def test_create_run_pins_sources_and_stores_no_source_bytes(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    record = _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    assert record['stage'] == 'inspected'
    assert re.fullmatch(r'run_[0-9a-f]{32}', record['run_id'])
    for name in ('om', 'rr', 't12', 'debt'):
        assert record['fields'][name]['status'] == 'present'
    assert record['values'] == FULL_VALUES
    assert CANARY not in json.dumps(record)
    for path in (tmp_path / 'run').rglob('*'):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes()


def test_create_run_refuses_incorrect_pin(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    inputs['rr']['sha256'] = '0' * 64
    exc = _refuse('HASH_MISMATCH', wf.create_run, tmp_path / 'run', SUBJECT, inputs, FULL_VALUES)
    assert exc.details.get('input') == 'rr'
    assert not (tmp_path / 'run').exists()


def test_create_run_refuses_missing_source(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    os.remove(rr)
    exc = _refuse('NOT_FOUND', wf.create_run, tmp_path / 'run', SUBJECT, inputs, FULL_VALUES)
    assert exc.details.get('input') == 'rr'
    assert not (tmp_path / 'run').exists()


def test_duplicate_run_request_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    first = _create(tmp_path, inputs, FULL_VALUES)
    _refuse('DUPLICATE_RUN', wf.create_run, tmp_path / 'run', SUBJECT, inputs, FULL_VALUES)
    assert wf.resume(tmp_path / 'run') == first


def test_output_collision_refused_for_dirty_run_dir(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    stray = _write(tmp_path / 'run' / 'stray.txt', b'pre-existing bytes')
    inputs = _full_inputs(om, rr, t12, debt)
    _refuse('OUTPUT_COLLISION', wf.create_run, tmp_path / 'run', SUBJECT, inputs, FULL_VALUES)
    assert stray.read_bytes() == b'pre-existing bytes'


def test_event_slot_collision_refused_without_overwrite(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    slot = _write(run_dir / 'events' / '0002.json', b'occupied')
    _refuse('OUTPUT_COLLISION', wf.advance, run_dir, 'normalized')
    assert slot.read_bytes() == b'occupied'
    assert json.loads((run_dir / 'run.json').read_bytes())['stage'] == 'inspected'


def test_content_identity_and_run_id_stability(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    one = _create(tmp_path, inputs, FULL_VALUES, name='one')
    two = _create(tmp_path, inputs, FULL_VALUES, name='two')
    assert one['run_id'] == two['run_id']
    assert one['content_identity'] == two['content_identity']
    assert (tmp_path / 'one' / 'events' / '0001.json').read_bytes() == \
           (tmp_path / 'two' / 'events' / '0001.json').read_bytes()
    rr2 = _write(tmp_path / 'sources' / 'rr2.pdf', b'different synthetic rent roll')
    changed = dict(inputs)
    changed['rr'] = _ref(rr2)
    three = _create(tmp_path, changed, FULL_VALUES, name='three')
    assert three['run_id'] != one['run_id']
    assert three['content_identity'] != one['content_identity']
    pin = 'c' * 64
    assert _create(tmp_path, inputs, FULL_VALUES, name='p1',
                   policy_sha256=pin)['run_id'] != one['run_id']
    assert _create(tmp_path, inputs, FULL_VALUES, name='c1',
                   code_sha256=pin)['run_id'] != one['run_id']
    assert _create(tmp_path, inputs, {'millage': '25.31', 'horizon': '10'},
                   name='v1')['run_id'] != one['run_id']


def test_content_identity_is_order_insensitive(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    a = _full_inputs(om, rr, t12, debt)
    b = {name: a[name] for name in reversed(list(a))}
    assert wf.content_identity(a, FULL_VALUES) == wf.content_identity(b, FULL_VALUES)
    assert wf.content_identity(a, None) == wf.content_identity(a, {})


def test_invalid_requests_refused_without_state(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', 'Bad Subject!', inputs, FULL_VALUES)
    bad_name = dict(inputs)
    bad_name['OM!'] = inputs['om']
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, bad_name, FULL_VALUES)
    bad_sha = {k: dict(v) for k, v in inputs.items()}
    bad_sha['om']['sha256'] = 'zz'
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, bad_sha, FULL_VALUES)
    bad_keys = {k: dict(v) for k, v in inputs.items()}
    bad_keys['om']['extra'] = 1
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, bad_keys, FULL_VALUES)
    bad_path = {k: dict(v) for k, v in inputs.items()}
    bad_path['om']['path'] = 'relative/om.pdf'
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, bad_path, FULL_VALUES)
    bad_path2 = {k: dict(v) for k, v in inputs.items()}
    bad_path2['om']['path'] = str(tmp_path / '..' / 'om.pdf')
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, bad_path2, FULL_VALUES)
    many = {f'i{n:02d}': _ref(om) for n in range(17)}
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, many, FULL_VALUES)
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, inputs,
            {'millage': 25.31})
    _refuse('INVALID_INPUT', wf.create_run, tmp_path / 'run', SUBJECT, inputs, FULL_VALUES,
            policy_sha256='short')
    assert not (tmp_path / 'run').exists()


# --- missing data and no all-or-nothing -------------------------------------

def test_missing_execution_fields_block_but_observations_survive(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    record = _create(tmp_path, {'om': _ref(om), 'rr': _ref(rr)}, None)
    blocked = {b['field'] for b in record['blockers']}
    assert blocked == {'t12', 'debt', 'millage', 'horizon'}
    assert all(b['kind'] == 'pending_human_action' for b in record['blockers'])
    assert all(b['resolved_by'] is None for b in record['blockers'])
    assert record['fields']['om']['status'] == 'present'
    assert record['fields']['rr']['status'] == 'present'
    assert record['outcome'] == 'needs_review_or_data'


def test_missing_value_is_null_never_zero(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    record = _create(tmp_path, _full_inputs(om, rr, t12, debt), {'horizon': '5'})
    assert record['values']['millage'] is None
    assert record['values']['horizon'] == '5'
    assert not any(v == '0' for v in record['values'].values())


def test_unsupported_format_blocks_engineering_not_intake(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    inputs['rr']['format'] = 'biffx'
    record = _create(tmp_path, inputs, FULL_VALUES)
    rr_blockers = [b for b in record['blockers'] if b['field'] == 'rr']
    assert rr_blockers and rr_blockers[0]['kind'] == 'unsupported_engineering'
    assert record['fields']['rr']['status'] == 'present'
    assert record['outcome'] == 'unsupported_input'
    record = wf.advance(tmp_path / 'run', 'normalized')
    assert record['stage'] == 'normalized'


def test_vanished_source_is_visible_corrupt_input_blocker(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    os.remove(rr)
    record = wf.resume(tmp_path / 'run')
    rr_blockers = [b for b in record['blockers'] if b['field'] == 'rr']
    assert rr_blockers and rr_blockers[0]['kind'] == 'corrupt_input'
    assert record['outcome'] == 'needs_review_or_data'


# --- transitions ------------------------------------------------------------

def test_normalized_requires_intake_sources(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, {'om': _ref(om)}, FULL_VALUES)
    run_dir = tmp_path / 'run'
    exc = _refuse('TRANSITION_BLOCKED', wf.advance, run_dir, 'normalized')
    assert exc.details.get('missing') == ['rr']
    _create(tmp_path, {'om': _ref(om), 'rr': _ref(rr)}, FULL_VALUES, name='run2')
    assert wf.advance(tmp_path / 'run2', 'normalized')['stage'] == 'normalized'


def test_stage_skips_and_backwards_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    _refuse('INVALID_TRANSITION', wf.advance, run_dir, 'reconciled')  # skip over normalized
    _refuse('INVALID_INPUT', wf.advance, run_dir, 'not_a_stage')
    wf.advance(run_dir, 'normalized')
    _refuse('INVALID_TRANSITION', wf.advance, run_dir, 'inspected')  # backwards


def test_zero_pending_blockers_may_skip_review(tmp_path):
    wf = api()
    _to_canonical(tmp_path)
    record = wf.resume(tmp_path / 'run')
    # _to_canonical advanced directly normalized -> reconciled -> canonical_ready
    assert record['stage'] == 'canonical_ready'
    assert record['review'] is None


def test_pending_blockers_force_review_path(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    inputs.pop('t12')
    _create(tmp_path, inputs, FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    exc = _refuse('TRANSITION_BLOCKED', wf.advance, run_dir, 'reconciled')
    assert 't12' in exc.details.get('unresolved', [])
    record = wf.advance(run_dir, 'review_required')
    assert record['stage'] == 'review_required'
    assert record['outcome'] == 'needs_review_or_data'


# --- human review -----------------------------------------------------------

def test_cancelled_review_keeps_run_blocked(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), {'horizon': '5'})
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    wf.advance(run_dir, 'review_required')
    record = wf.record_review(run_dir, _decision('dec_001', 'cancelled'))
    assert record['review']['status'] == 'cancelled'
    _refuse('TRANSITION_BLOCKED', wf.advance, run_dir, 'reconciled')
    assert wf.resume(run_dir)['outcome'] == 'needs_review_or_data'


def test_approved_decision_resolves_value_blocker(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), {'horizon': '5'})
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    wf.advance(run_dir, 'review_required')
    record = wf.record_review(run_dir, _decision(
        'dec_001', 'approved',
        resolutions={'millage': {'value': '25.31', 'evidence': 'synthetic-policy'}}))
    millage = [b for b in record['blockers'] if b['field'] == 'millage']
    assert millage and millage[0]['resolved_by'] == 'dec_001'
    assert record['fields']['millage']['status'] == 'review_resolved'
    assert record['resolutions']['millage']['value'] == '25.31'
    wf.advance(run_dir, 'reconciled')
    record = wf.advance(run_dir, 'canonical_ready')
    record = wf.request_execution(run_dir, _approval(record))
    assert record['stage'] == 'execution_authorized'


def test_review_cannot_resolve_missing_source_document(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    inputs.pop('t12')
    _create(tmp_path, inputs, FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    wf.advance(run_dir, 'review_required')
    exc = _refuse('INVALID_RESOLUTION', wf.record_review, run_dir,
                  _decision('dec_001', 'approved',
                            resolutions={'t12': {'value': '1', 'evidence': 'not-a-source'}}))
    assert exc.details.get('field') == 't12'
    assert wf.resume(run_dir)['resolutions'] == {}


def test_duplicate_decision_id_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), {'horizon': '5'})
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    wf.advance(run_dir, 'review_required')
    wf.record_review(run_dir, _decision('dec_001', 'cancelled'))
    _refuse('DUPLICATE_DECISION', wf.record_review, run_dir, _decision('dec_001', 'approved'))


# --- execution --------------------------------------------------------------

def test_direct_advance_to_execution_authorized_refused(tmp_path):
    wf = api()
    _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    _refuse('INVALID_TRANSITION', wf.advance, run_dir, 'execution_authorized')


def test_execution_blocked_on_missing_fields(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    inputs.pop('debt')
    _create(tmp_path, inputs, {'millage': '25.31'})
    run_dir = tmp_path / 'run'
    for stage in ('normalized', 'reconciled', 'canonical_ready'):
        wf.advance(run_dir, stage)
    exc = _refuse('EXECUTION_BLOCKED', wf.request_execution, run_dir,
                  _approval(wf.resume(run_dir)))
    assert 'debt' in exc.details.get('missing', [])
    assert 'horizon' in exc.details.get('missing', [])


def test_execution_blocked_on_unresolved_unsupported(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    inputs = _full_inputs(om, rr, t12, debt)
    inputs['rr']['format'] = 'biffx'
    _create(tmp_path, inputs, FULL_VALUES)
    run_dir = tmp_path / 'run'
    for stage in ('normalized', 'reconciled', 'canonical_ready'):
        wf.advance(run_dir, stage)
    exc = _refuse('EXECUTION_BLOCKED', wf.request_execution, run_dir,
                  _approval(wf.resume(run_dir)))
    assert 'rr' in exc.details.get('unresolved', [])


def test_execution_requires_engine_backend(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES,
            engine_backend=None)
    run_dir = tmp_path / 'run'
    for stage in ('normalized', 'reconciled', 'canonical_ready'):
        wf.advance(run_dir, stage)
    _refuse('UNSUPPORTED_ENGINEERING', wf.request_execution, run_dir,
            _approval(wf.resume(run_dir)))


def test_valid_approval_authorizes_execution(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    record = wf.request_execution(run_dir, _approval(record))
    assert record['stage'] == 'execution_authorized'
    assert record['approval'] == {'approval_id': 'appr_001',
                                  'content_sha256': record['content_identity']}
    assert record['certified'] is False
    _refuse('INVALID_TRANSITION', wf.request_execution, run_dir, _approval(record))


def test_stale_approval_refused(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    _refuse('STALE_APPROVAL', wf.request_execution, run_dir, _approval(record, content='f' * 64))
    wrong_pins = {n: i['sha256'] for n, i in record['inputs'].items()}
    wrong_pins['rr'] = 'e' * 64
    _refuse('STALE_APPROVAL', wf.request_execution, run_dir,
            _approval(record, inputs_pins=wrong_pins))
    assert wf.resume(run_dir)['stage'] == 'canonical_ready'


def test_post_approval_source_change_refused(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    wf.request_execution(run_dir, _approval(record))
    om, rr, t12, debt = _sources(tmp_path)
    original = rr.read_bytes()
    _write(rr, b'mutated synthetic rent roll ' + CANARY.encode())
    exc = _refuse('STALE', wf.resume, run_dir)
    assert exc.details.get('input') == 'rr'
    _refuse('STALE', wf.advance, run_dir, 'engine_executed', artifact_sha256=ARTIFACT_A)
    _write(rr, original)  # restoring the pinned bytes unblocks the run
    assert wf.resume(run_dir)['stage'] == 'execution_authorized'


def test_resume_cannot_swap_rr_snapshot(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    other = _write(tmp_path / 'sources' / 'rr_other.pdf', b'a different synthetic rent roll')
    exc = _refuse('STALE', wf.resume, run_dir,
                  inputs={'rr': {'sha256': _pin(other.read_bytes()), 'path': str(other),
                                 'format': 'pdf'}})
    assert exc.details.get('input') == 'rr'
    moved = _write(tmp_path / 'moved' / 'rr.pdf', rr.read_bytes())
    record = wf.resume(run_dir, inputs={'rr': {'sha256': _pin(rr.read_bytes()),
                                               'path': str(moved), 'format': 'pdf'}})
    assert record['inputs']['rr']['path'] == str(moved)
    assert record['inputs']['rr']['sha256'] == _pin(rr.read_bytes())
    assert record['event_count'] == 2  # the re-pin is recorded as an event


def test_policy_and_code_pin_mismatch_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    pin = 'c' * 64
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES,
            policy_sha256=pin, code_sha256=pin)
    run_dir = tmp_path / 'run'
    exc = _refuse('STALE', wf.resume, run_dir, policy_sha256='d' * 64)
    assert exc.details.get('pin') == 'policy_sha256'
    _refuse('STALE', wf.resume, run_dir, code_sha256='d' * 64)
    assert wf.resume(run_dir, policy_sha256=pin, code_sha256=pin)['stage'] == 'inspected'


def test_engine_and_report_stages_pin_artifacts(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    wf.request_execution(run_dir, _approval(record))
    record = wf.advance(run_dir, 'engine_executed', artifact_sha256=ARTIFACT_A)
    assert record['execution'] == {'status': 'executed', 'artifact_sha256': ARTIFACT_A}
    _refuse('INVALID_TRANSITION', wf.advance, run_dir, 'engine_executed')
    record = wf.advance(run_dir, 'report_ready', artifact_sha256=ARTIFACT_B)
    assert record['stage'] == 'report_ready'
    assert record['artifacts']['report_ready'] == ARTIFACT_B
    assert record['outcome'] == 'complete'


def test_engine_executed_requires_artifact_or_error_record(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    wf.request_execution(run_dir, _approval(record))
    _refuse('INVALID_TRANSITION', wf.advance, run_dir, 'engine_executed')


def test_execution_error_is_recorded_not_hidden(tmp_path):
    wf = api()
    record = _to_canonical(tmp_path)
    run_dir = tmp_path / 'run'
    wf.request_execution(run_dir, _approval(record))
    record = wf.advance(run_dir, 'engine_executed',
                        execution={'status': 'error', 'error_code': 'ENGINE_TIMEOUT'})
    assert record['stage'] == 'execution_authorized'
    assert record['execution']['status'] == 'error'
    assert record['execution']['error_code'] == 'ENGINE_TIMEOUT'
    assert record['outcome'] == 'execution_error'
    _refuse('INVALID_TRANSITION', wf.advance, run_dir, 'report_ready')


# --- resume and crash semantics ----------------------------------------------

def test_resume_is_idempotent_and_stateless(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    first = wf.resume(run_dir)
    second = wf.resume(run_dir)
    assert first == second
    assert first['stage'] == 'normalized'
    assert first['event_count'] == 2


def test_cache_rebuilt_after_crash(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    before = wf.resume(run_dir)
    os.remove(run_dir / 'run.json')
    after = wf.resume(run_dir)
    assert after == before
    assert (run_dir / 'run.json').exists()


def test_tampered_event_refused_sanitized(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    event = run_dir / 'events' / '0002.json'
    event.write_bytes(event.read_bytes().replace(b'"normalized"', b'"review_required"'))
    exc = _refuse('CORRUPT_INPUT', wf.resume, run_dir)
    assert exc.__cause__ is None
    assert exc.__context__ is None or exc.__suppress_context__
    assert CANARY not in str(exc)
    assert CANARY not in json.dumps(exc.details)


def test_tampered_cache_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    cache = json.loads((run_dir / 'run.json').read_bytes())
    cache['stage'] = 'report_ready'
    (run_dir / 'run.json').write_bytes(json.dumps(cache, sort_keys=True,
                                                  separators=(',', ':')).encode())
    _refuse('CORRUPT_INPUT', wf.resume, run_dir)


def test_missing_event_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    os.remove(run_dir / 'events' / '0001.json')
    _refuse('CORRUPT_INPUT', wf.resume, run_dir)


def test_malformed_event_refused(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    wf.advance(run_dir, 'normalized')
    (run_dir / 'events' / '0003.json').write_bytes(b'not-json')
    _refuse('CORRUPT_INPUT', wf.resume, run_dir)


# --- sanitize and isolation ----------------------------------------------------

def test_canary_never_surfaces_anywhere(tmp_path, capsys):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    record = _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    assert CANARY not in json.dumps(record)
    # Before approval a mutated source is a visible corrupt_input blocker.
    _write(rr, b'mutated ' + CANARY.encode())
    record = wf.resume(run_dir)
    assert {'field': 'rr', 'kind': 'corrupt_input',
            'resolved_by': None} in record['blockers']
    assert record['fields']['rr']['status'] == 'missing'
    assert CANARY not in json.dumps(record)
    # After approval the same mutation refuses STALE, sanitized.
    _write(rr, b'synthetic rent roll rows')  # restore the pinned bytes
    for stage in ('normalized', 'reconciled', 'canonical_ready'):
        wf.advance(run_dir, stage)
    wf.request_execution(run_dir, _approval(wf.resume(run_dir)))
    _write(rr, b'mutated again ' + CANARY.encode())
    exc = _refuse('STALE', wf.resume, run_dir)
    assert CANARY not in str(exc)
    assert CANARY not in json.dumps(exc.details)
    output = capsys.readouterr()
    assert CANARY not in output.out + output.err
    for path in run_dir.rglob('*'):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes()


def test_returned_records_are_isolated_copies(tmp_path):
    wf = api()
    om, rr, t12, debt = _sources(tmp_path)
    record = _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    one = wf.resume(run_dir)
    two = wf.resume(run_dir)
    one['fields']['rr']['sha256'] = '0' * 64
    one['blockers'].append({'field': 'om', 'kind': 'pending_human_action'})
    assert two['fields']['rr']['sha256'] == record['fields']['rr']['sha256']
    assert two['blockers'] == record['blockers']
    three = wf.resume(run_dir)
    assert three['fields']['rr']['sha256'] == record['fields']['rr']['sha256']


def test_no_threads_spawned(tmp_path):
    before = threading.active_count()
    record = _to_canonical(tmp_path)
    wf = api()
    run_dir = tmp_path / 'run'
    wf.request_execution(run_dir, _approval(record))
    wf.advance(run_dir, 'engine_executed', artifact_sha256=ARTIFACT_A)
    assert threading.active_count() == before


def test_private_permissions_and_layout(tmp_path):
    om, rr, t12, debt = _sources(tmp_path)
    _create(tmp_path, _full_inputs(om, rr, t12, debt), FULL_VALUES)
    run_dir = tmp_path / 'run'
    assert stat.S_IMODE(run_dir.stat().st_mode) == 0o700
    assert stat.S_IMODE((run_dir / 'events').stat().st_mode) == 0o700
    for name in ('run.json', 'events/0001.json'):
        assert stat.S_IMODE((run_dir / name).stat().st_mode) == 0o600


def test_outcome_rejects_garbage():
    wf = api()
    _refuse('INVALID_INPUT', wf.outcome, {'stage': 'inspected'})
    _refuse('INVALID_INPUT', wf.outcome, 'not-a-record')