"""Reusable, resumable workflow state machine: ``workflow/1.0.0``.

Workflow states are progress markers, never financial certification: no stage
implies underwriting correctness, and ``execution_authorized`` only records a
content-bound approval echo — actual engine execution and live-deal authority
stay with the separately reviewed execution envelope. The module performs no
financial math, launches no threads, never invokes the engine, and stores
only hashes and host paths, never source bytes. See docs/WORKFLOW.md.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import NoReturn

from plat_harness.errors import HarnessError

VERSION = 'workflow/1.0.0'
STAGES = ('inspected', 'normalized', 'review_required', 'reconciled',
          'canonical_ready', 'execution_authorized', 'engine_executed',
          'report_ready')
BLOCKER_KINDS = ('pending_human_action', 'unsupported_engineering', 'corrupt_input')
OUTCOMES = ('complete', 'needs_review_or_data', 'unsupported_input', 'execution_error')
DEFAULT_VALUES = ('millage', 'horizon')
REQUIRED_INTAKE = ('om', 'rr')
REQUIRED_INPUTS = ('om', 'rr', 't12', 'debt')
# Fields that must be human-cleared before reconciliation; debt/horizon only
# gate execution, so a run is never dead-locked waiting on them mid-flow.
RECON_REQUIRED = ('t12', 'millage')
SUPPORTED_FORMATS = frozenset(('pdf', 'xlsx', 'csv'))
MAX_INPUTS = 16
EXIT_CODES = {'complete': 0, 'needs_review_or_data': 2, 'unsupported_input': 3,
              'execution_error': 4}

_ALLOWED = {
    'normalized': ('inspected',),
    'review_required': ('normalized',),
    'reconciled': ('normalized', 'review_required'),
    'canonical_ready': ('reconciled',),
    'engine_executed': ('execution_authorized',),
    'report_ready': ('engine_executed',),
}
_STAGE_INDEX = {name: index for index, name in enumerate(STAGES)}

_NAME = re.compile(r'[a-z][a-z0-9_]*')
_SUBJECT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]*')
_SHA = re.compile(r'[0-9a-f]{64}')
_FORMAT = re.compile(r'[a-z0-9]+')
_SLOT = re.compile(r'([0-9]{4})[.]json')
_REF_KEYS = frozenset(('sha256', 'path', 'format'))
_CREATED_KEYS = frozenset(('n', 'kind', 'schema', 'run_id', 'subject',
                           'content_identity', 'inputs', 'values',
                           'policy_sha256', 'code_sha256', 'engine_backend'))
_REVIEW_KEYS = frozenset(('decision_id', 'status', 'reviewer', 'resolutions'))
_REVIEW_STATUSES = frozenset(('approved', 'cancelled'))
_RESOLUTION_KEYS = frozenset(('value', 'evidence'))
_APPROVAL_KEYS = frozenset(('approval_id', 'content_sha256', 'inputs_sha256'))

_CORRUPT = 'Run journal is unreadable or inconsistent.'
_INVALID = 'Invalid workflow request.'


def _refuse(code: str, message: str, **details) -> NoReturn:
    raise HarnessError(code, message, details=details or None) from None


def _enc(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _digest(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _sha(value, code: str) -> None:
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _refuse(code, _INVALID)


def _pin(value, code: str = 'INVALID_INPUT') -> None:
    if value is not None:
        _sha(value, code)


def _ref(value, code: str = 'INVALID_INPUT') -> dict:
    if type(value) is not dict or set(value) != _REF_KEYS:
        _refuse(code, _INVALID)
    _sha(value['sha256'], code)
    path = value['path']
    if (type(path) is not str or not path or len(path) > 4096
            or '\x00' in path):
        _refuse(code, _INVALID)
    parts = Path(path).parts
    if not Path(path).is_absolute() or '..' in parts:
        _refuse(code, _INVALID)
    fmt = value['format']
    if type(fmt) is not str or _FORMAT.fullmatch(fmt) is None:
        _refuse(code, _INVALID)
    return {'sha256': value['sha256'], 'path': path, 'format': fmt}


def _inputs(value, code: str = 'INVALID_INPUT') -> dict:
    if type(value) is not dict or not 1 <= len(value) <= MAX_INPUTS:
        _refuse(code, _INVALID)
    checked = {}
    for name in value:
        if type(name) is not str or _NAME.fullmatch(name) is None:
            _refuse(code, _INVALID)
        checked[name] = _ref(value[name], code)
    return checked


def _values(value, code: str = 'INVALID_INPUT') -> dict:
    if value is None:
        value = {}
    if type(value) is not dict:
        _refuse(code, _INVALID)
    for name, item in value.items():
        if type(name) is not str or _NAME.fullmatch(name) is None:
            _refuse(code, _INVALID)
        if item is not None and type(item) is not str:
            _refuse(code, _INVALID)
    merged = {name: None for name in DEFAULT_VALUES}
    merged.update(value)
    return merged


def _decision(value, code: str = 'INVALID_INPUT') -> dict:
    if type(value) is not dict or set(value) != _REVIEW_KEYS:
        _refuse(code, _INVALID)
    for field in ('decision_id', 'reviewer'):
        if type(value[field]) is not str or not value[field]:
            _refuse(code, _INVALID)
    if type(value['status']) is not str or value['status'] not in _REVIEW_STATUSES:
        _refuse(code, _INVALID)
    resolutions = value['resolutions']
    if type(resolutions) is not dict:
        _refuse(code, _INVALID)
    for field, item in resolutions.items():
        if type(field) is not str or _NAME.fullmatch(field) is None:
            _refuse(code, _INVALID)
        if type(item) is not dict or set(item) != _RESOLUTION_KEYS:
            _refuse(code, _INVALID)
        for key in _RESOLUTION_KEYS:
            if type(item[key]) is not str or not item[key]:
                _refuse(code, _INVALID)
    return {'decision_id': value['decision_id'], 'status': value['status'],
            'reviewer': value['reviewer'],
            'resolutions': {field: {'value': item['value'],
                                    'evidence': item['evidence']}
                            for field, item in resolutions.items()}}


def _approval(value, code: str = 'INVALID_INPUT') -> dict:
    if type(value) is not dict or set(value) != _APPROVAL_KEYS:
        _refuse(code, _INVALID)
    if type(value['approval_id']) is not str or not value['approval_id']:
        _refuse(code, _INVALID)
    _sha(value['content_sha256'], code)
    pins = value['inputs_sha256']
    if type(pins) is not dict:
        _refuse(code, _INVALID)
    for name, pin in pins.items():
        if type(name) is not str or _NAME.fullmatch(name) is None:
            _refuse(code, _INVALID)
        _sha(pin, code)
    return {'approval_id': value['approval_id'],
            'content_sha256': value['content_sha256'],
            'inputs_sha256': dict(pins)}


def _run_dir(path) -> Path:
    if type(path) is str:
        path = Path(path)
    if not isinstance(path, Path):
        _refuse('INVALID_INPUT', _INVALID)
    text = str(path)
    if not path.is_absolute() or '..' in path.parts or len(text) > 4096:
        _refuse('INVALID_INPUT', _INVALID)
    return path


def content_identity(inputs, values, *, policy_sha256=None, code_sha256=None) -> str:
    """Stable content identity: same bytes and values, same run id."""
    checked = _inputs(inputs)
    merged = _values(values)
    _pin(policy_sha256)
    _pin(code_sha256)
    payload = {'schema': VERSION,
               'inputs': {name: checked[name]['sha256']
                          for name in sorted(checked)},
               'values': merged,
               'policy_sha256': policy_sha256,
               'code_sha256': code_sha256}
    return _digest(_enc(payload))


def _run_id(subject: str, identity: str) -> str:
    digest = _digest(_enc({'schema': VERSION, 'kind': 'run', 'subject': subject,
                           'content_identity': identity}))
    return 'run_' + digest[:32]


def _parse_event(raw: bytes, n: int) -> dict:
    try:
        event = json.loads(raw)
    except Exception:
        _refuse('CORRUPT_INPUT', _CORRUPT)
    if (type(event) is not dict or event.get('n') != n
            or type(event.get('kind')) is not str):
        _refuse('CORRUPT_INPUT', _CORRUPT)
    kind = event['kind']
    if kind == 'created':
        if set(event) != _CREATED_KEYS or event['schema'] != VERSION:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        try:
            if (type(event['subject']) is not str
                    or _SUBJECT.fullmatch(event['subject']) is None):
                _refuse('CORRUPT_INPUT', _CORRUPT)
            if (type(event['run_id']) is not str
                    or re.fullmatch(r'run_[0-9a-f]{32}', event['run_id']) is None):
                _refuse('CORRUPT_INPUT', _CORRUPT)
            _sha(event['content_identity'], 'CORRUPT_INPUT')
            _inputs(event['inputs'], 'CORRUPT_INPUT')
            _values(event['values'], 'CORRUPT_INPUT')
            _pin(event['policy_sha256'], 'CORRUPT_INPUT')
            _pin(event['code_sha256'], 'CORRUPT_INPUT')
            backend = event['engine_backend']
            if backend is not None and (type(backend) is not str or not backend):
                _refuse('CORRUPT_INPUT', _CORRUPT)
            identity = content_identity(event['inputs'], event['values'],
                                         policy_sha256=event['policy_sha256'],
                                         code_sha256=event['code_sha256'])
        except HarnessError:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if identity != event['content_identity']:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if _run_id(event['subject'], identity) != event['run_id']:
            _refuse('CORRUPT_INPUT', _CORRUPT)
    elif kind == 'stage':
        keys = set(event)
        if keys not in ({'n', 'kind', 'stage'},
                        {'n', 'kind', 'stage', 'artifact_sha256'}):
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if (event['stage'] not in _ALLOWED
                or type(event['stage']) is not str):
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if 'artifact_sha256' in event:
            _sha(event['artifact_sha256'], 'CORRUPT_INPUT')
        elif event['stage'] in ('engine_executed', 'report_ready'):
            _refuse('CORRUPT_INPUT', _CORRUPT)
    elif kind == 'engine_error':
        if (set(event) != {'n', 'kind', 'error_code'}
                or type(event['error_code']) is not str or not event['error_code']):
            _refuse('CORRUPT_INPUT', _CORRUPT)
    elif kind == 'review':
        if set(event) != {'n', 'kind', 'decision'}:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        _decision(event['decision'], 'CORRUPT_INPUT')
    elif kind == 'execution_authorized':
        if set(event) != {'n', 'kind', 'approval'}:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        _approval(event['approval'], 'CORRUPT_INPUT')
    elif kind == 'inputs_repinned':
        if set(event) != {'n', 'kind', 'inputs'}:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        _inputs(event['inputs'], 'CORRUPT_INPUT')
    else:
        _refuse('CORRUPT_INPUT', _CORRUPT)
    return event


def _read_events(run_dir: Path) -> list:
    if not run_dir.is_dir():
        _refuse('NOT_FOUND', 'Run directory not found.')
    events_dir = run_dir / 'events'
    if not events_dir.is_dir():
        _refuse('CORRUPT_INPUT', _CORRUPT)
    slots = {}
    try:
        entries = os.listdir(events_dir)
    except OSError:
        _refuse('CORRUPT_INPUT', _CORRUPT)
    for entry in entries:
        match = _SLOT.fullmatch(entry)
        if match:
            slots[int(match.group(1))] = entry
    events = []
    n = 1
    while n in slots:
        try:
            raw = (events_dir / slots[n]).read_bytes()
        except OSError:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        events.append(_parse_event(raw, n))
        n += 1
    if not events:
        _refuse('CORRUPT_INPUT', _CORRUPT)
    return events


def _apply(events: list) -> dict:
    first = events[0]
    if first['kind'] != 'created':
        _refuse('CORRUPT_INPUT', _CORRUPT)
    state = {'run_id': first['run_id'], 'subject': first['subject'],
             'content_identity': first['content_identity'],
             'inputs': {name: dict(ref) for name, ref in first['inputs'].items()},
             'values': dict(first['values']),
             'policy_sha256': first['policy_sha256'],
             'code_sha256': first['code_sha256'],
             'engine_backend': first['engine_backend'],
             'stage': 'inspected', 'review': None, 'resolutions': {},
             'resolved_by': {}, 'approval': None, 'execution': None,
             'artifacts': {}, 'decisions': {}}
    for event in events[1:]:
        kind = event['kind']
        if kind == 'stage':
            target = event['stage']
            if state['stage'] not in _ALLOWED[target]:
                _refuse('CORRUPT_INPUT', _CORRUPT)
            state['stage'] = target
            if 'artifact_sha256' in event:
                state['artifacts'][target] = event['artifact_sha256']
                if target == 'engine_executed':
                    state['execution'] = {'status': 'executed',
                                          'artifact_sha256': event['artifact_sha256']}
        elif kind == 'engine_error':
            if state['stage'] != 'execution_authorized':
                _refuse('CORRUPT_INPUT', _CORRUPT)
            state['execution'] = {'status': 'error',
                                  'error_code': event['error_code']}
        elif kind == 'review':
            if state['stage'] != 'review_required':
                _refuse('CORRUPT_INPUT', _CORRUPT)
            decision = event['decision']
            did = decision['decision_id']
            if did in state['decisions']:
                _refuse('CORRUPT_INPUT', _CORRUPT)
            state['decisions'][did] = decision['status']
            state['review'] = {'decision_id': did, 'status': decision['status'],
                               'reviewer': decision['reviewer'],
                               'resolutions': {field: dict(item) for field, item
                                               in decision['resolutions'].items()}}
            if decision['status'] == 'approved':
                for field, item in decision['resolutions'].items():
                    if field not in DEFAULT_VALUES:
                        _refuse('CORRUPT_INPUT', _CORRUPT)
                    state['resolutions'][field] = {'value': item['value'],
                                                   'evidence': item['evidence']}
                    state['resolved_by'][field] = did
        elif kind == 'execution_authorized':
            if state['stage'] != 'canonical_ready':
                _refuse('CORRUPT_INPUT', _CORRUPT)
            approval = event['approval']
            if approval['content_sha256'] != state['content_identity']:
                _refuse('CORRUPT_INPUT', _CORRUPT)
            expected = {name: ref['sha256']
                        for name, ref in state['inputs'].items()}
            if approval['inputs_sha256'] != expected:
                _refuse('CORRUPT_INPUT', _CORRUPT)
            state['approval'] = {'approval_id': approval['approval_id'],
                                 'content_sha256': approval['content_sha256']}
            state['stage'] = 'execution_authorized'
        elif kind == 'inputs_repinned':
            for name, ref in event['inputs'].items():
                if name not in state['inputs']:
                    _refuse('CORRUPT_INPUT', _CORRUPT)
                if ref['sha256'] != state['inputs'][name]['sha256']:
                    _refuse('CORRUPT_INPUT', _CORRUPT)
            state['inputs'] = {name: dict(ref) for name, ref
                               in event['inputs'].items()}
        else:
            _refuse('CORRUPT_INPUT', _CORRUPT)
    state['event_count'] = len(events)
    return state


def _core(state: dict) -> dict:
    return {'schema': VERSION,
            'run_id': state['run_id'],
            'content_identity': state['content_identity'],
            'subject': state['subject'],
            'stage': state['stage'],
            'engine_backend': state['engine_backend'],
            'policy_sha256': state['policy_sha256'],
            'code_sha256': state['code_sha256'],
            'inputs': {name: dict(ref) for name, ref
                       in sorted(state['inputs'].items())},
            'values': dict(state['values']),
            'resolutions': {field: dict(item) for field, item
                            in sorted(state['resolutions'].items())},
            'resolved_by': dict(state['resolved_by']),
            'review': dict(state['review']) if state['review'] else None,
            'approval': dict(state['approval']) if state['approval'] else None,
            'execution': dict(state['execution']) if state['execution'] else None,
            'artifacts': dict(state['artifacts']),
            'event_count': state['event_count']}


def _live(state: dict):
    """Verify pinned sources now; corrupt bytes are blockers, STALE after approval."""
    fields = {}
    corrupt = []
    unsupported = []
    for name in sorted(state['inputs']):
        ref = state['inputs'][name]
        try:
            path = Path(ref['path'])
            ok = path.is_file() and _digest(path.read_bytes()) == ref['sha256']
        except OSError:
            ok = False
        fields[name] = {'status': 'present' if ok else 'missing',
                        'sha256': ref['sha256'], 'path': ref['path'],
                        'format': ref['format']}
        if not ok:
            corrupt.append(name)
        elif ref['format'] not in SUPPORTED_FORMATS:
            unsupported.append(name)
    for name in DEFAULT_VALUES:
        if name in state['resolved_by']:
            fields[name] = {'status': 'review_resolved'}
        elif state['values'].get(name) is not None:
            fields[name] = {'status': 'present'}
        else:
            fields[name] = {'status': 'missing'}
    if corrupt and state['approval'] is not None:
        _refuse('STALE', 'Pinned source changed after approval.', input=corrupt[0])
    blockers = []
    for name in corrupt:
        blockers.append({'field': name, 'kind': 'corrupt_input',
                         'resolved_by': None})
    for name in unsupported:
        blockers.append({'field': name, 'kind': 'unsupported_engineering',
                         'resolved_by': None})
    for name in REQUIRED_INPUTS:
        if name not in state['inputs']:
            blockers.append({'field': name, 'kind': 'pending_human_action',
                            'resolved_by': None})
    for name in DEFAULT_VALUES:
        if state['values'].get(name) is None:
            blockers.append({'field': name, 'kind': 'pending_human_action',
                             'resolved_by': state['resolved_by'].get(name)})
    return fields, blockers


def outcome(record) -> str:
    """Documented stable outcome for a record; never a financial judgment."""
    if type(record) is not dict:
        _refuse('INVALID_INPUT', _INVALID)
    for key in ('stage', 'blockers', 'execution'):
        if key not in record:
            _refuse('INVALID_INPUT', _INVALID)
    if type(record['stage']) is not str or record['stage'] not in _STAGE_INDEX:
        _refuse('INVALID_INPUT', _INVALID)
    execution = record['execution']
    if isinstance(execution, dict) and execution.get('status') == 'error':
        return 'execution_error'
    unresolved = [item for item in record['blockers']
                  if isinstance(item, dict) and item.get('resolved_by') is None]
    if any(item.get('kind') == 'unsupported_engineering' for item in unresolved):
        return 'unsupported_input'
    if unresolved:
        return 'needs_review_or_data'
    return 'complete'


def _public(state: dict) -> dict:
    fields, blockers = _live(state)
    record = _core(state)
    record['fields'] = fields
    record['blockers'] = blockers
    record['certified'] = False
    record['outcome'] = outcome(record)
    return record


def _write_cache(run_dir: Path, core: dict) -> None:
    tmp = run_dir / '.run.json.tmp'
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(_enc(core))
        handle.flush()
        os.fsync(handle.fileno())
    cache = run_dir / 'run.json'
    os.replace(tmp, cache)
    os.chmod(cache, 0o600)
    fd = os.open(run_dir, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _load(run_dir: Path) -> dict:
    """Journal is truth; the cache snapshot is verified, repaired or rebuilt."""
    events = _read_events(run_dir)
    state = _apply(events)
    core = _core(state)
    cache = run_dir / 'run.json'
    if cache.exists():
        try:
            raw = cache.read_bytes()
            parsed = json.loads(raw)
        except Exception:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if type(parsed) is not dict:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        count = parsed.get('event_count')
        if type(count) is not int or not 1 <= count <= len(events):
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if _enc(_core(_apply(events[:count]))) != raw:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if count < len(events):
            _write_cache(run_dir, core)  # torn write: repair from the journal
    else:
        _write_cache(run_dir, core)
    return state


def _append_event(run_dir: Path, n: int, event: dict) -> None:
    path = run_dir / 'events' / f'{n:04d}.json'
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        _refuse('OUTPUT_COLLISION', 'Refusing to overwrite existing files.', slot=n)
    with os.fdopen(fd, 'wb') as handle:
        handle.write(_enc(event))
        handle.flush()
        os.fsync(handle.fileno())
    os.chmod(path, 0o600)


def _next_slot(run_dir: Path) -> int:
    cache = run_dir / 'run.json'
    if cache.exists():
        try:
            parsed = json.loads(cache.read_bytes())
        except Exception:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        if type(parsed) is not dict or type(parsed.get('event_count')) is not int:
            _refuse('CORRUPT_INPUT', _CORRUPT)
        return parsed['event_count'] + 1
    n = 1
    while (run_dir / 'events' / f'{n:04d}.json').exists():
        n += 1
    return n


def _verify_source(name: str, ref: dict) -> None:
    path = Path(ref['path'])
    if not path.is_file():
        _refuse('NOT_FOUND', 'Pinned source file not found.', input=name)
    try:
        raw = path.read_bytes()
    except OSError:
        _refuse('HASH_MISMATCH',
                'Pinned source bytes differ from the recorded SHA256.', input=name)
    if _digest(raw) != ref['sha256']:
        _refuse('HASH_MISMATCH',
                'Pinned source bytes differ from the recorded SHA256.', input=name)


def create_run(run_dir, subject, inputs, values=None, *, policy_sha256=None,
               code_sha256=None, engine_backend='engine_stub') -> dict:
    """Create a run directory, or refuse; never overwrites existing files."""
    run_dir = _run_dir(run_dir)
    if type(subject) is not str or _SUBJECT.fullmatch(subject) is None:
        _refuse('INVALID_INPUT', _INVALID)
    checked = _inputs(inputs)
    merged = _values(values)
    _pin(policy_sha256)
    _pin(code_sha256)
    if engine_backend is not None and (type(engine_backend) is not str
                                       or not engine_backend):
        _refuse('INVALID_INPUT', _INVALID)
    identity = content_identity(checked, merged, policy_sha256=policy_sha256,
                                code_sha256=code_sha256)
    run_id = _run_id(subject, identity)
    if run_dir.exists():
        if not run_dir.is_dir():
            _refuse('OUTPUT_COLLISION', 'Refusing to overwrite existing files.')
        entries = set(os.listdir(run_dir))
        if 'run.json' in entries:
            try:
                existing = json.loads((run_dir / 'run.json').read_bytes())
            except Exception:
                _refuse('CORRUPT_INPUT', _CORRUPT)
            if type(existing) is not dict or type(existing.get('run_id')) is not str:
                _refuse('CORRUPT_INPUT', _CORRUPT)
            if existing['run_id'] == run_id:
                _refuse('DUPLICATE_RUN', 'An identical run already exists at this path.',
                        run_id=run_id)
            _refuse('OUTPUT_COLLISION', 'Refusing to overwrite existing files.')
        if entries:
            _refuse('OUTPUT_COLLISION', 'Refusing to overwrite existing files.')
    for name in sorted(checked):
        _verify_source(name, checked[name])
    events = run_dir / 'events'
    os.makedirs(events, exist_ok=True)
    os.chmod(run_dir, 0o700)
    os.chmod(events, 0o700)
    _append_event(run_dir, 1, {'n': 1, 'kind': 'created', 'schema': VERSION,
                                'run_id': run_id, 'subject': subject,
                                'content_identity': identity, 'inputs': checked,
                                'values': merged, 'policy_sha256': policy_sha256,
                                'code_sha256': code_sha256,
                                'engine_backend': engine_backend})
    return _public(_load(run_dir))


def resume(run_dir, *, inputs=None, policy_sha256=None, code_sha256=None) -> dict:
    """Rebuild state from the journal; refuses stale pins, never reuses them."""
    run_dir = _run_dir(run_dir)
    state = _load(run_dir)
    if policy_sha256 is not None:
        _pin(policy_sha256)
        if policy_sha256 != state['policy_sha256']:
            _refuse('STALE', 'Run state is stale relative to pinned inputs.',
                    pin='policy_sha256')
    if code_sha256 is not None:
        _pin(code_sha256)
        if code_sha256 != state['code_sha256']:
            _refuse('STALE', 'Run state is stale relative to pinned inputs.',
                    pin='code_sha256')
    if inputs is not None:
        override = _inputs(inputs)
        for name in sorted(override):
            if name not in state['inputs']:
                _refuse('INVALID_INPUT', _INVALID, input=name)
            if override[name]['sha256'] != state['inputs'][name]['sha256']:
                _refuse('STALE', 'Run state is stale relative to pinned inputs.',
                        input=name)
            _verify_source(name, override[name])
        merged = {name: dict(ref) for name, ref
                  in {**state['inputs'], **override}.items()}
        n = state['event_count'] + 1
        _append_event(run_dir, n, {'n': n, 'kind': 'inputs_repinned',
                                   'inputs': merged})
        state = _load(run_dir)
    return _public(state)


def advance(run_dir, stage, *, artifact_sha256=None, execution=None) -> dict:
    """Host-controlled transition; appends one immutable journal event."""
    if type(stage) is not str or stage not in STAGES:
        _refuse('INVALID_INPUT', _INVALID)
    run_dir = _run_dir(run_dir)
    slot = _next_slot(run_dir)
    if (run_dir / 'events' / f'{slot:04d}.json').exists():
        _refuse('OUTPUT_COLLISION', 'Refusing to overwrite existing files.', slot=slot)
    record = resume(run_dir)
    current = record['stage']
    if stage not in _ALLOWED or current not in _ALLOWED[stage]:
        _refuse('INVALID_TRANSITION', 'Requested stage transition is not allowed.',
                stage=stage, current=current)
    if stage == 'normalized':
        missing = [name for name in REQUIRED_INTAKE
                   if name not in record['inputs']
                   or record['fields'][name]['status'] != 'present']
        if missing:
            _refuse('TRANSITION_BLOCKED',
                    'Stage transition blocked by unresolved items.', missing=missing)
    if stage == 'reconciled':
        unresolved = [item['field'] for item in record['blockers']
                      if item['resolved_by'] is None
                      and (item['kind'] == 'corrupt_input'
                           or (item['kind'] == 'pending_human_action'
                               and item['field'] in RECON_REQUIRED))]
        if unresolved:
            _refuse('TRANSITION_BLOCKED',
                    'Stage transition blocked by unresolved items.',
                    unresolved=unresolved)
    event = {'n': slot, 'kind': 'stage', 'stage': stage}
    if stage in ('engine_executed', 'report_ready'):
        if stage == 'engine_executed' and execution is not None:
            if artifact_sha256 is not None:
                _refuse('INVALID_INPUT', _INVALID)
            if (type(execution) is not dict
                    or set(execution) != {'status', 'error_code'}
                    or execution['status'] != 'error'
                    or type(execution['error_code']) is not str
                    or not execution['error_code']):
                _refuse('INVALID_INPUT', _INVALID)
            event = {'n': slot, 'kind': 'engine_error',
                     'error_code': execution['error_code']}
        elif artifact_sha256 is None:
            _refuse('INVALID_TRANSITION',
                    'Requested stage transition is not allowed.', stage=stage)
        else:
            _sha(artifact_sha256, 'INVALID_INPUT')
            event['artifact_sha256'] = artifact_sha256
    elif artifact_sha256 is not None or execution is not None:
        _refuse('INVALID_INPUT', _INVALID)
    _append_event(run_dir, slot, event)
    return _public(_load(run_dir))


def record_review(run_dir, decision) -> dict:
    """Record one human review decision; decisions never self-authorize."""
    run_dir = _run_dir(run_dir)
    checked = _decision(decision)
    state = _load(run_dir)
    fields, blockers = _live(state)
    if state['stage'] != 'review_required':
        _refuse('INVALID_TRANSITION', 'Requested stage transition is not allowed.',
                stage=state['stage'])
    did = checked['decision_id']
    if did in state['decisions']:
        _refuse('DUPLICATE_DECISION', 'Decision id already recorded.', decision_id=did)
    if checked['status'] == 'approved':
        for field in checked['resolutions']:
            if field not in DEFAULT_VALUES:
                _refuse('INVALID_RESOLUTION',
                        'Decision cannot resolve that field.', field=field)
            if not any(item['field'] == field
                       and item['kind'] == 'pending_human_action'
                       and item['resolved_by'] is None for item in blockers):
                _refuse('INVALID_RESOLUTION',
                        'Decision cannot resolve that field.', field=field)
    n = state['event_count'] + 1
    _append_event(run_dir, n, {'n': n, 'kind': 'review', 'decision': checked})
    return _public(_load(run_dir))


def request_execution(run_dir, approval) -> dict:
    """Bind a content-bound approval; never live-deal authorization."""
    run_dir = _run_dir(run_dir)
    checked = _approval(approval)
    record = resume(run_dir)
    if record['stage'] != 'canonical_ready':
        _refuse('INVALID_TRANSITION', 'Requested stage transition is not allowed.',
                stage=record['stage'])
    unresolved = [item for item in record['blockers'] if item['resolved_by'] is None]
    missing = [item['field'] for item in unresolved
                if item['kind'] == 'pending_human_action']
    bad = [item['field'] for item in unresolved
           if item['kind'] in ('unsupported_engineering', 'corrupt_input')]
    if missing or bad:
        _refuse('EXECUTION_BLOCKED', 'Execution blocked by unresolved items.',
                missing=missing, unresolved=bad)
    if record['engine_backend'] is None:
        _refuse('UNSUPPORTED_ENGINEERING',
                'No engine backend is configured for this run.')
    if checked['content_sha256'] != record['content_identity']:
        _refuse('STALE_APPROVAL', 'Approval does not match the current run content.')
    expected = {name: ref['sha256'] for name, ref in record['inputs'].items()}
    if checked['inputs_sha256'] != expected:
        _refuse('STALE_APPROVAL', 'Approval does not match the current run content.')
    n = record['event_count'] + 1
    _append_event(run_dir, n, {'n': n, 'kind': 'execution_authorized',
                               'approval': checked})
    return _public(_load(run_dir))