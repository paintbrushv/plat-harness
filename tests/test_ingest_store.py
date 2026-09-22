"""Synthetic private-filesystem intake tests; no source-room or engine IO."""
import copy
import importlib.util
import json
import os
import stat
import traceback

import pytest

from test_ingest_reconciliation import unknown, decision, approve
from test_ingest_source_resolver import (
    envelope, resolver, digest, sha, RAW, SID, SUBJECT, DAY, CANARY,
)

RUN = 'run_' + '1' * 32


@pytest.fixture(autouse=True)
def private_root(tmp_path, monkeypatch):
    """Point PLAT_HARNESS_PRIVATE_ROOT at this test's private tmp area.

    Pinned-reference reads go through the adapter's fail-closed path gate;
    every test runs against a fresh 0700 root, never a builtin host directory.
    """
    tmp_path.chmod(0o700)
    root = tmp_path / 'private'
    if not root.exists():
        root.mkdir(mode=0o700)
    monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(root))
    return root



def api():
    assert importlib.util.find_spec('plat_harness.ingest.store') is not None
    from plat_harness.ingest import store
    assert callable(getattr(store, 'IntakeStore', None))
    return store


def host(tmp_path, monkeypatch, env=None, registry=None, raw=RAW, **overrides):
    env = envelope(raw) if env is None else env
    registry = {} if registry is None else registry
    root = tmp_path / 'private'
    if not root.exists():
        root.mkdir(mode=0o700)
    from plat_harness.adapters import review_bridge
    monkeypatch.setattr(review_bridge, 'host_registry', lambda: copy.deepcopy(registry))
    source = lambda: resolver(env, originals={SID: raw})
    args = dict(root=str(root), subject_id=env['subject_id'], as_of=env['as_of'],
                expected_envelope_sha256=digest(env),
                expected_context_sha256=digest(source().context()), source_loader=source)
    args.update(overrides)
    return api().IntakeStore(**args), root, env, registry


def refusal(call):
    try:
        call()
    except api().StoreError as exc:
        assert exc.__cause__ is None and exc.__context__ is None
        assert CANARY not in ''.join(traceback.format_exception(exc))
        return exc.code
    return 'ACCEPTED'


def test_blocked_normalized_create_read_and_private_artifact(tmp_path, monkeypatch, capsys, caplog):
    s, root, env, reg = host(tmp_path, monkeypatch, env=unknown())
    before = copy.deepcopy(env)
    result = s.create(RUN, env, [])
    assert result['overlay']['state'] == 'review_required'
    assert result['overlay']['observations'] == before == env
    assert s.read(RUN, expected=result['pin']) == result
    assert result['pin']['revision'] == 1
    assert result['pin']['envelope_sha256'] == digest(env)
    files = list(root.rglob('*'))
    assert files
    for p in files:
        assert stat.S_IMODE(p.stat().st_mode) == (0o700 if p.is_dir() else 0o600)
        if p.is_file():
            assert RAW not in p.read_bytes()
            assert p.stat().st_nlink == 1
    artifact = root / RUN / 'rev_000001.json'
    assert sha(artifact.read_bytes()) == result['pin']['sha256']
    assert CANARY not in json.dumps(result) + capsys.readouterr().out + caplog.text


def test_real_parser_migration_source_supported_create(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    assert out['overlay']['state'] == 'reconciled'
    assert s.read(RUN, expected=out['pin']) == out


@pytest.mark.parametrize('bad', [
    {'version': '1.0', 'tenant_name': CANARY},
    {'approved': True}, b'{}', None,
])
def test_normalization_precedes_any_payload_or_filesystem_write(tmp_path, monkeypatch, bad):
    s, root, _, _ = host(tmp_path, monkeypatch)
    assert refusal(lambda: s.create(RUN, bad, [])) != 'ACCEPTED'
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('key,value', [('subject_id', 'other'), ('as_of', '2026-02-01'),
    ('expected_envelope_sha256', '0' * 64), ('expected_context_sha256', '0' * 64)])
def test_independent_host_pins_refuse_before_write(tmp_path, monkeypatch, key, value):
    def attempt():
        s, _, env, _ = host(tmp_path, monkeypatch, **{key: value})
        return s.create(RUN, env, [])
    assert refusal(attempt) != 'ACCEPTED'
    assert list((tmp_path / 'private').iterdir()) == []


def test_create_once_even_identical_or_changed_run(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    first = s.create(RUN, env, [])
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    altered = copy.deepcopy(env)
    altered['status'] = 'blocked'
    assert refusal(lambda: s.create(RUN, altered, [])) != 'ACCEPTED'
    assert s.read(RUN, expected=first['pin']) == first


def test_full_signed_decision_private_but_not_in_output(tmp_path, monkeypatch):
    env, registry = unknown(), {}
    d = approve(decision(env), registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    result = s.create(RUN, env, [d])
    assert result['overlay']['state'] == 'reconciled'
    stored = json.loads((root / RUN / 'rev_000001.json').read_bytes())
    assert stored['decisions'] == [d]
    assert CANARY not in json.dumps(result)
    registry.clear()
    assert refusal(lambda: s.read(RUN, expected=result['pin'])) != 'ACCEPTED'


def history_api(s):
    assert callable(getattr(s, 'append', None))
    assert callable(getattr(s, 'preview', None))


def chain(env, registry):
    first = approve(decision(env), registry)
    second = approve(decision(env, decision_id='dec_' + '4' * 32,
                             predecessor_sha256=digest(first), supersedes=[digest(first)]), registry)
    return [first, second]


def test_append_exact_prefix_preview_and_restart(tmp_path, monkeypatch):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    history_api(s)
    proposed = s.preview(RUN, env, [], expected_prior=None)
    assert list(root.iterdir()) == []
    first = s.create(RUN, env, [])
    assert proposed == first['pin']
    old = (root / RUN / 'rev_000001.json').read_bytes()
    proposed = s.preview(RUN, env, ds, expected_prior=first['pin'])
    second = s.append(RUN, env, ds, expected=first['pin'])
    assert second['pin'] == proposed
    assert second['pin']['revision'] == 2
    assert second['pin']['decision_head_sha256'] == digest(ds[-1])
    assert second['overlay']['observations'] == env
    assert (root / RUN / 'rev_000001.json').read_bytes() == old
    assert [v['state'] for v in second['overlay']['history']] == ['superseded', 'active']
    restarted, _, _, _ = host(tmp_path, monkeypatch, env, registry)
    assert restarted.read(RUN, expected=second['pin']) == second
    assert refusal(lambda: restarted.read(RUN, expected=first['pin'])) != 'ACCEPTED'
    del registry[ds[0]['approval']['approval_id']]
    assert refusal(lambda: restarted.read(RUN, expected=second['pin'])) != 'ACCEPTED'


@pytest.mark.parametrize('attack', ['shorter', 'same', 'reorder', 'edited_old', 'branch', 'repeated'])
def test_append_refuses_nonextension_even_valid_signed_prefix(tmp_path, monkeypatch, attack):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    history_api(s)
    first = s.create(RUN, env, ds)
    proposal = copy.deepcopy(ds)
    if attack == 'shorter':
        proposal.pop()
    elif attack == 'reorder':
        proposal.reverse()
    elif attack == 'edited_old':
        proposal[0]['approval']['reason'] = 'new reason'
        proposal[0] = approve(proposal[0], registry)
    elif attack == 'branch':
        proposal = [approve(decision(env, decision_id='dec_' + '7' * 32), registry)]
    elif attack == 'repeated':
        proposal.append(ds[-1])
    assert refusal(lambda: s.append(RUN, env, proposal, expected=first['pin'])) != 'ACCEPTED'
    assert not (root / RUN / 'rev_000002.json').exists()


def test_concurrent_cas_conflict_has_exactly_one_winner(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    history_api(s)
    initial = s.create(RUN, env, [])
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: refusal(lambda: s.append(RUN, env, ds,
                                                                  expected=initial['pin'])), range(2)))
    assert results.count('ACCEPTED') == 1
    assert len(list((root / RUN).glob('rev_*.json'))) == 2
    expected = s.preview(RUN, env, ds, expected_prior=initial['pin'])
    assert s.read(RUN, expected=expected)['pin'] == expected


@pytest.mark.parametrize('attack', ['head_only', 'full_disk'])
def test_rollback_refuses_newer_independent_latest_pin(tmp_path, monkeypatch, attack):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    history_api(s)
    first = s.create(RUN, env, [])
    head = root / RUN / 'head.json'
    old_head = head.read_bytes()
    latest = s.append(RUN, env, ds, expected=first['pin'])
    head.write_bytes(old_head)
    if attack == 'full_disk':
        (root / RUN / 'rev_000002.json').unlink()
    assert refusal(lambda: s.read(RUN, expected=latest['pin'])) != 'ACCEPTED'


def test_historical_artifact_damage_refuses_current_read(tmp_path, monkeypatch):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    history_api(s)
    first = s.create(RUN, env, [])
    latest = s.append(RUN, env, ds, expected=first['pin'])
    (root / RUN / 'rev_000001.json').write_bytes(b'{}')
    assert refusal(lambda: s.read(RUN, expected=latest['pin'])) != 'ACCEPTED'


@pytest.mark.parametrize('target', ['root', 'ancestor', 'run', 'revision', 'head', 'lock'])
def test_symlink_refusal_all_levels(tmp_path, monkeypatch, target):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    chosen = {'root': root, 'ancestor': tmp_path, 'run': root / RUN,
              'revision': root / RUN / 'rev_000001.json', 'head': root / RUN / 'head.json',
              'lock': root / '.lock'}[target]
    moved = chosen.with_name(chosen.name + '_moved')
    chosen.rename(moved)
    chosen.symlink_to(moved, target_is_directory=moved.is_dir())
    try:
        assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'
    finally:
        chosen.unlink()
        moved.rename(chosen)


@pytest.mark.parametrize('path_kind', ['relative', 'dot', 'dotdot', 'double', 'trailing'])
def test_root_path_cannot_be_normalized_to_authority(tmp_path, monkeypatch, path_kind):
    root = str(tmp_path / 'private')
    paths = {'relative': 'private', 'dot': str(tmp_path) + '/./private',
             'dotdot': str(tmp_path) + '/x/../private',
             'double': str(tmp_path) + '//private', 'trailing': root + '/'}
    s, actual, env, _ = host(tmp_path, monkeypatch, root=paths[path_kind])
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert list(actual.iterdir()) == []


@pytest.mark.parametrize('run_id', ['', '../x', '/x', 'run_' + 'A' * 32,
    'run_' + '1' * 31, 'run_' + '1' * 33, 'run_' + '1' * 32 + '/x', True, None])
def test_run_opaque_grammar(tmp_path, monkeypatch, run_id):
    s, root, env, _ = host(tmp_path, monkeypatch)
    assert refusal(lambda: s.create(run_id, env, [])) != 'ACCEPTED'
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('target,mode', [('ancestor', 0o770), ('root', 0o750),
    ('run', 0o755), ('revision', 0o640), ('head', 0o660), ('lock', 0o644)])
def test_permission_refusal_no_repairs(tmp_path, monkeypatch, target, mode):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    path = {'ancestor': tmp_path, 'root': root, 'run': root / RUN,
            'revision': root / RUN / 'rev_000001.json', 'head': root / RUN / 'head.json',
            'lock': root / '.lock'}[target]
    before = stat.S_IMODE(path.stat().st_mode)
    path.chmod(mode)  # only this test's synthetic directory/file, never global paths
    try:
        assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'
        assert stat.S_IMODE(path.stat().st_mode) == mode
    finally:
        path.chmod(before)


@pytest.mark.parametrize('target', ['revision', 'head', 'lock'])
@pytest.mark.parametrize('kind', ['hardlink', 'fifo', 'directory', 'socket'])
def test_nonregular_or_alias_refused_without_blocking(tmp_path, monkeypatch, target, kind):
    import socket
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    path = {'revision': root / RUN / 'rev_000001.json', 'head': root / RUN / 'head.json',
            'lock': root / '.lock'}[target]
    sock = None
    if kind == 'hardlink':
        os.link(path, tmp_path / 'alias')
    else:
        path.unlink()
        if kind == 'fifo':
            os.mkfifo(path, 0o600)
        elif kind == 'directory':
            path.mkdir(mode=0o700)
        else:
            sock = socket.socket(socket.AF_UNIX)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                sock.bind('/proc/self/fd/' + str(directory) + '/' + path.name)
            finally:
                os.close(directory)
    try:
        assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'
    finally:
        if sock is not None:
            sock.close()


@pytest.mark.parametrize('target', ['root', 'run', 'revision', 'head', 'lock'])
def test_wrong_owner_stat_fault_injection(tmp_path, monkeypatch, target):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    path = {'root': root, 'run': root / RUN, 'revision': root / RUN / 'rev_000001.json',
            'head': root / RUN / 'head.json', 'lock': root / '.lock'}[target]
    inode = path.stat().st_ino
    original = api().os.fstat
    def wrong(fd):
        value = original(fd)
        if value.st_ino == inode:
            fields = list(value)
            fields[4] = os.geteuid() + 1
            return os.stat_result(fields)
        return value
    monkeypatch.setattr(api().os, 'fstat', wrong)
    assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'


def test_replaced_lock_inode_while_acquiring_refuses(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    old = api().fcntl.flock
    def swap(fd, flag):
        old(fd, flag)
        (root / '.lock').rename(root / '.old_lock')
        replacement = os.open(root / '.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
        os.close(replacement)
    monkeypatch.setattr(api().fcntl, 'flock', swap)
    assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'


def test_duplicate_create_leaves_no_operation_temp(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    before = set(root.iterdir())
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert set(root.iterdir()) == before
    assert s.read(RUN, expected=out['pin']) == out


def test_stale_head_even_with_old_pin_refuses_existing_successor(tmp_path, monkeypatch):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    first = s.create(RUN, env, [])
    old_head = (root / RUN / 'head.json').read_bytes()
    s.append(RUN, env, ds, expected=first['pin'])
    (root / RUN / 'head.json').write_bytes(old_head)
    assert refusal(lambda: s.read(RUN, expected=first['pin'])) != 'ACCEPTED'


@pytest.mark.parametrize('part', ['head', 'revision'])
@pytest.mark.parametrize('mutation', ['duplicate', 'float', 'deep', 'oversize', 'garbage', 'changed'])
def test_hostile_disk_bytes_refused(tmp_path, monkeypatch, part, mutation):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    path = root / RUN / ('head.json' if part == 'head' else 'rev_000001.json')
    good = path.read_bytes()
    hostile = {'duplicate': b'{"x":1,"x":2}', 'float': b'{"x":1e0}',
               'deep': b'[' * 40 + b'0' + b']' * 40,
               'oversize': b' ' * (api().MAX_BYTES + 1),
               'garbage': CANARY.encode(), 'changed': good + b' '}[mutation]
    path.write_bytes(hostile)
    assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'


def test_head_boolean_revision_is_not_integer(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    head = copy.deepcopy(out['pin'])
    head['revision'] = True
    (root / RUN / 'head.json').write_bytes(api().c._encode(head))
    assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'


@pytest.mark.parametrize('key,value', [('revision', True), ('revision', 0), ('revision', 65),
    ('sha256', '0' * 64), ('envelope_sha256', '0' * 64), ('context_sha256', '0' * 64),
    ('decision_head_sha256', '0' * 64), ('run_id', 'run_' + '2' * 32), ('approved', True)])
def test_independent_read_pins_all_fields_strict(tmp_path, monkeypatch, key, value):
    s, root, env, _ = host(tmp_path, monkeypatch)
    out = s.create(RUN, env, [])
    pin = copy.deepcopy(out['pin'])
    pin[key] = value
    assert refusal(lambda: s.read(RUN, expected=pin)) != 'ACCEPTED'


@pytest.mark.parametrize('attack', ['v1', 'resident', 'overlay', 'registry', 'rank', 'path', 'float', 'cycle', 'depth', 'items'])
def test_native_payload_boundary_before_filesystem(tmp_path, monkeypatch, attack):
    s, root, env, _ = host(tmp_path, monkeypatch)
    if attack == 'v1':
        from plat_harness.ingest import normalize_rent_roll
        import io
        env = normalize_rent_roll(io.BytesIO(RAW), 'yardi')
    elif attack in ('resident', 'overlay', 'registry', 'rank', 'path'):
        env[attack] = CANARY
    elif attack == 'float':
        env['completeness']['residential']['counts']['occupied'] = 1.0
    elif attack == 'cycle':
        env['issues'].append(env)
    elif attack == 'depth':
        value = []
        for _ in range(30):
            value = [value]
        env['issues'] = value
    else:
        env['issues'] = [None] * 50_001
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert list(root.iterdir()) == []


def test_current_source_loader_used_again_and_context_drift_refuses(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    current = {'raw': RAW}
    calls = []
    def load():
        calls.append(1)
        return resolver(env, originals={SID: current['raw']})
    s._source_loader = load  # independently host-controlled test capability
    out = s.create(RUN, env, [])
    n = len(calls)
    assert s.read(RUN, expected=out['pin']) == out
    assert len(calls) > n
    current['raw'] = RAW + b'changed'
    assert refusal(lambda: s.read(RUN, expected=out['pin'])) != 'ACCEPTED'


def test_host_error_canaries_are_not_chained_or_logged(tmp_path, monkeypatch, capsys, caplog):
    s, root, env, _ = host(tmp_path, monkeypatch)
    def broken():
        raise RuntimeError(CANARY)
    s._source_loader = broken
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert list(root.iterdir()) == []
    capture = capsys.readouterr()
    assert CANARY not in capture.out + capture.err + caplog.text


@pytest.mark.parametrize('operation', ['create', 'append'])
def test_stage_write_failure_cleans_only_own_temps(tmp_path, monkeypatch, operation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    first = s.create(RUN, env, []) if operation == 'append' else None
    unrelated = root / '.stage_someone_else'
    unrelated.mkdir(mode=0o700)
    marker = unrelated / 'keep'
    marker.write_bytes(b'synthetic unrelated operation')
    marker.chmod(0o600)
    before = set(root.rglob('*'))
    real = api()._write_file
    def fail(parent, name, raw):
        real(parent, name, raw[:17])
        raise OSError(CANARY)
    monkeypatch.setattr(api(), '_write_file', fail)
    action = (lambda: s.create(RUN, env, [])) if operation == 'create' else (
        lambda: s.append(RUN, env, ds, expected=first['pin']))
    assert refusal(action) != 'ACCEPTED'
    assert marker.read_bytes() == b'synthetic unrelated operation'
    assert set(root.rglob('*')) - {root / '.lock'} == before - {root / '.lock'}


def test_exact_resume_read_fsyncs_artifact_head_and_directories(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    intended = s.preview(RUN, env, [], expected_prior=None)
    real = api().os.fsync
    root_inode = root.stat().st_ino
    def fail_after_visibility(fd):
        if os.fstat(fd).st_ino == root_inode and (root / RUN).exists():
            raise OSError(CANARY)
        return real(fd)
    monkeypatch.setattr(api().os, 'fsync', fail_after_visibility)
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert (root / RUN / 'head.json').exists()
    seen = set()
    def track(fd):
        seen.add(os.fstat(fd).st_ino)
        return real(fd)
    monkeypatch.setattr(api().os, 'fsync', track)
    resumed = s.read(RUN, expected=intended)
    assert resumed['pin'] == intended
    required = {p.stat().st_ino for p in (root, root / RUN, root / RUN / 'head.json',
                                         root / RUN / 'rev_000001.json')}
    assert required <= seen


@pytest.mark.parametrize('failure', ['revision_publish', 'head_publish', 'post_visibility_read'])
def test_append_interruption_never_false_success(tmp_path, monkeypatch, failure):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    first = s.create(RUN, env, [])
    intended = s.preview(RUN, env, ds, expected_prior=first['pin'])
    with monkeypatch.context() as patch:
        if failure == 'revision_publish':
            real = api()._publish
            def fail(parent, old, new, **kwargs):
                real(parent, old, new, **kwargs)
                raise OSError(CANARY)
            patch.setattr(api(), '_publish', fail)
        elif failure == 'head_publish':
            real = api().os.replace
            def fail(*args, **kwargs):
                real(*args, **kwargs)
                raise OSError(CANARY)
            patch.setattr(api().os, 'replace', fail)
        else:
            real = s._read
            def fail(parent, run_id, expected):
                result = real(parent, run_id, expected)
                if expected['revision'] == 2:
                    raise OSError(CANARY)
                return result
            patch.setattr(s, '_read', fail)
        assert refusal(lambda: s.append(RUN, env, ds, expected=first['pin'])) != 'ACCEPTED'
    if failure == 'revision_publish':
        assert refusal(lambda: s.read(RUN, expected=intended)) != 'ACCEPTED'
        assert refusal(lambda: s.read(RUN, expected=first['pin'])) != 'ACCEPTED'
    else:
        assert s.read(RUN, expected=intended)['pin'] == intended


def test_successful_commit_fsyncs_each_file_and_modified_directory(tmp_path, monkeypatch):
    s, root, env, _ = host(tmp_path, monkeypatch)
    real, seen = api().os.fsync, set()
    def track(fd):
        seen.add(os.fstat(fd).st_ino)
        return real(fd)
    monkeypatch.setattr(api().os, 'fsync', track)
    s.create(RUN, env, [])
    assert {p.stat().st_ino for p in [root, *root.rglob('*')]} <= seen


@pytest.mark.parametrize('operation', ['read', 'append'])
def test_missing_lock_is_not_recreated_for_existing_run(tmp_path, monkeypatch, operation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    out = s.create(RUN, env, [])
    (root / '.lock').unlink()
    call = (lambda: s.read(RUN, expected=out['pin'])) if operation == 'read' else (
        lambda: s.append(RUN, env, ds, expected=out['pin']))
    assert refusal(call) != 'ACCEPTED'
    assert not (root / '.lock').exists()


def test_lock_replacement_before_publish_does_not_publish_revision(tmp_path, monkeypatch):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    out = s.create(RUN, env, [])
    real = api()._write_file
    swapped = []
    def replace_lock(parent, name, raw):
        real(parent, name, raw)
        if not swapped:
            swapped.append(1)
            (root / '.lock').rename(root / '.old_lock')
            fd = os.open(root / '.lock', os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(fd)
    monkeypatch.setattr(api(), '_write_file', replace_lock)
    assert refusal(lambda: s.append(RUN, env, ds, expected=out['pin'])) != 'ACCEPTED'
    assert not (root / RUN / 'rev_000002.json').exists()


@pytest.mark.parametrize('supported', [True, False])
def test_parser_migration_store_append_source_read_pipeline(tmp_path, monkeypatch, supported):
    raw = RAW if supported else RAW.replace(b'all_physical_units/1', b'unknown')
    env, registry = envelope(raw), {}
    d = decision(env)
    d['changes'][0]['citation'] = copy.deepcopy(env['units'][0]['evidence'][0]['unit_type'])
    d = approve(d, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry, raw=raw)
    initial = s.create(RUN, env, [])
    final = s.append(RUN, env, [d], expected=initial['pin'])
    assert s.read(RUN, expected=final['pin']) == final
    assert final['overlay']['observations'] == env
    assert final['overlay']['state'] == ('reconciled' if supported else 'review_required')
    if not supported:
        assert all(v is None for scope in final['overlay']['counts'].values() for v in scope.values())


@pytest.mark.parametrize('mutation', ['overlay', 'scope', 'context', 'decisions', 'previous'])
def test_rehashed_artifact_does_not_bypass_recomputation(tmp_path, monkeypatch, mutation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    out = s.create(RUN, env, ds)
    path = root / RUN / 'rev_000001.json'
    record = json.loads(path.read_bytes())
    if mutation == 'overlay':
        record['overlay']['state'] = 'review_required'
    elif mutation == 'scope':
        record['overlay']['observations']['subject_id'] = 'other'
    elif mutation == 'context':
        record['overlay']['context_sha256'] = '0' * 64
    elif mutation == 'decisions':
        record['decisions'][0]['approval']['reason'] = 'tampered'
    else:
        record['previous'] = out['pin']
    encoded = api().c._encode(record)
    path.write_bytes(encoded)
    updated = out['pin'] | {'sha256': sha(encoded)}
    (root / RUN / 'head.json').write_bytes(api().c._encode(updated))
    assert refusal(lambda: s.read(RUN, expected=updated)) != 'ACCEPTED'


def test_limits_refuse_without_persisting(tmp_path, monkeypatch):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    assert refusal(lambda: s.create(RUN, env, [ds[0]] * 257)) != 'ACCEPTED'
    pin = s.preview(RUN, env, [], expected_prior=None)
    assert refusal(lambda: s.preview(RUN, env, ds,
                                     expected_prior=pin | {'revision': 64})) != 'ACCEPTED'
    # Deliberately lower only the store's output budget, not frozen validators.
    monkeypatch.setattr(api(), 'MAX_BYTES', 64)
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('operation', ['create', 'append', 'read'])
def test_every_fsync_failure_is_refusal_not_success(tmp_path, monkeypatch, operation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    real = api().os.fsync
    # Determine actual fsync membership from a real successful operation.
    reference = tmp_path / 'reference'
    reference.mkdir(mode=0o700)
    s, _, _, _ = host(reference, monkeypatch, env, registry)
    initial = s.create(RUN, env, []) if operation != 'create' else None
    calls = []
    def tracking(fd):
        calls.append(1)
        return real(fd)
    def action(store, first):
        if operation == 'create':
            return store.create(RUN, env, [])
        if operation == 'append':
            return store.append(RUN, env, ds, expected=first['pin'])
        return store.read(RUN, expected=first['pin'])
    with monkeypatch.context() as patch:
        patch.setattr(api().os, 'fsync', tracking)
        action(s, initial)
    assert calls
    for failure_index in range(len(calls)):
        attempt = tmp_path / ('failure_' + str(failure_index))
        attempt.mkdir(mode=0o700)
        candidate, _, _, _ = host(attempt, monkeypatch, env, registry)
        first = candidate.create(RUN, env, []) if operation != 'create' else None
        seen = []
        def fail_one(fd):
            seen.append(1)
            if len(seen) == failure_index + 1:
                raise OSError(CANARY)
            return real(fd)
        with monkeypatch.context() as patch:
            patch.setattr(api().os, 'fsync', fail_one)
            assert refusal(lambda: action(candidate, first)) != 'ACCEPTED'


@pytest.mark.parametrize('target', ['root', 'run'])
def test_directory_replacement_mid_write_is_detected(tmp_path, monkeypatch, target):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    out = s.create(RUN, env, [])
    path = root if target == 'root' else root / RUN
    moved = path.with_name(path.name + '_moved')
    real, swapped = api()._write_file, []
    def change(parent, name, raw):
        real(parent, name, raw)
        if not swapped:
            swapped.append(1)
            path.rename(moved)
            path.mkdir(mode=0o700)
    monkeypatch.setattr(api(), '_write_file', change)
    assert refusal(lambda: s.append(RUN, env, ds, expected=out['pin'])) != 'ACCEPTED'
    actual = moved / RUN if target == 'root' else moved
    assert not (actual / 'rev_000002.json').exists()


def test_real_resident_canary_is_removed_before_private_storage(tmp_path, monkeypatch, capsys, caplog):
    raw = RAW.replace(b'Status Definition\n', b'Status Definition,Tenant Name\n')
    raw = raw.rstrip(b'\n') + b',' + CANARY.encode() + b'\n'
    env = envelope(raw)
    s, root, _, _ = host(tmp_path, monkeypatch, env=env, raw=raw)
    out = s.create(RUN, env, [])
    assert out['overlay']['state'] == 'review_required'  # unsupported source dialect
    assert s.read(RUN, expected=out['pin']) == out
    assert CANARY not in json.dumps(out)
    for path in root.rglob('*'):
        if path.is_file():
            assert CANARY.encode() not in path.read_bytes()
    capture = capsys.readouterr()
    assert CANARY not in capture.out + capture.err + caplog.text


@pytest.mark.parametrize('key', ['path', 'host_registry', 'source_resolver', 'rank', 'approved', 'overlay'])
def test_operation_cannot_accept_request_authority_kwargs(tmp_path, monkeypatch, key):
    s, root, env, _ = host(tmp_path, monkeypatch)
    assert refusal(lambda: s.create(RUN, env, [], **{key: CANARY})) != 'ACCEPTED'
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('kind', ['receipt', 'subclass'])
def test_store_source_capability_cannot_be_receipt_or_subclass(tmp_path, monkeypatch, kind):
    s, root, env, _ = host(tmp_path, monkeypatch)
    source = {'supported': True}
    if kind == 'subclass':
        source = resolver(env)
        class Derived(type(source)):
            pass
        source.__class__ = Derived
    s._source_loader = lambda: source
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert list(root.iterdir()) == []


@pytest.mark.parametrize('empty', [True, False])
def test_create_publish_collision_cannot_replace_even_empty_directory(tmp_path, monkeypatch, empty):
    s, root, env, _ = host(tmp_path, monkeypatch)
    real = api()._publish
    def collision(parent, old, new, **kwargs):
        os.mkdir(new, mode=0o700, dir_fd=parent)
        sentinel = os.open(new, os.O_RDONLY | os.O_DIRECTORY, dir_fd=parent)
        try:
            if not empty:
                marker = os.open('sentinel', os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600,
                                 dir_fd=sentinel)
                os.close(marker)
        finally:
            os.close(sentinel)
        return real(parent, old, new, **kwargs)
    monkeypatch.setattr(api(), '_publish', collision)
    assert refusal(lambda: s.create(RUN, env, [])) != 'ACCEPTED'
    assert {p.name for p in (root / RUN).iterdir()} == (set() if empty else {'sentinel'})


def test_source_read_helper_is_not_exposed():
    assert not any(hasattr(api().IntakeStore, name) for name in (
        'read_source', 'source_path', 'read_history', 'latest', 'discover_head'))


# Keep the unchanged production loader, captured before host() installs its
# synthetic membership seam. Disk-drift cases do not replace its validation.
from plat_harness.adapters.review_bridge import host_registry as _real_registry


def timing_authority(tmp_path, monkeypatch, s, env, registry, mutation):
    """Synthetic current authority; return change/restore/check capabilities."""
    from plat_harness.adapters import review_bridge
    approved = copy.deepcopy(registry)
    if mutation == 'revoked':
        def change():
            registry.clear()
        def restore():
            registry.update(copy.deepcopy(approved))
        def check():
            assert review_bridge.host_registry() == {}
    else:
        original = (json.dumps(registry, sort_keys=True).encode()
                    if mutation == 'registry_bytes' else RAW)
        # The pinned registry read goes through the fail-closed private path
        # gate: it must live below the configured private root, but NOT inside
        # the store root (whose exact contents these tests assert). The gate
        # accepts any subtree below the root, so a sibling directory works.
        monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(tmp_path))
        gate_root = tmp_path / 'gate'
        gate_root.mkdir(mode=0o700, exist_ok=True)
        path = gate_root / ('registry.json' if mutation == 'registry_bytes' else 'original.csv')
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(original)
        if mutation == 'registry_bytes':
            monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH', str(path))
            monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', sha(original))
            monkeypatch.setattr(review_bridge, 'host_registry', _real_registry)
            assert _real_registry() == registry
        else:
            assert mutation == 'source_bytes'
            s._source_loader = lambda: resolver(env, originals={SID: path.read_bytes()})
        def change():
            path.write_bytes(b'{}' if mutation == 'registry_bytes' else RAW + b'changed')
        def restore():
            path.write_bytes(original)
        def check():
            assert path.read_bytes() != original
            if mutation == 'registry_bytes':
                with pytest.raises(Exception):
                    _real_registry()
    return change, restore, check


def timing_operation(s, env, ds, operation):
    first = s.create(RUN, env, []) if operation != 'create' else None
    expected = s.preview(RUN, env, ds, expected_prior=None if first is None else first['pin'])
    if operation == 'read':
        saved = s.append(RUN, env, ds, expected=first['pin'])
        assert saved['pin'] == expected
        action = lambda: s.read(RUN, expected=expected)
    elif operation == 'append':
        action = lambda: s.append(RUN, env, ds, expected=first['pin'])
    else:
        action = lambda: s.create(RUN, env, ds)
    return action, expected, first


@pytest.mark.parametrize('operation', ['read', 'append'])
def test_timing_latest_full_chain_after_older_empty_history(tmp_path, monkeypatch, operation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    action, expected, first = timing_operation(s, env, ds, operation)
    real_read, injected = api()._read_file, []
    def read_then_revoke(parent, name):
        raw = real_read(parent, name)
        if name == 'rev_000001.json' and (root / RUN / 'rev_000002.json').exists():
            injected.append(1)
            registry.clear()
        return raw
    monkeypatch.setattr(api(), '_read_file', read_then_revoke)
    assert refusal(action) != 'ACCEPTED'
    assert injected
    assert json.loads((root / RUN / 'head.json').read_bytes()) == expected
    assert sha((root / RUN / 'rev_000002.json').read_bytes()) == expected['sha256']
    assert refusal(lambda: s.read(RUN, expected=first['pin'])) != 'ACCEPTED'


@pytest.mark.parametrize('operation', ['read', 'create', 'append'])
@pytest.mark.parametrize('mutation', ['revoked', 'registry_bytes', 'source_bytes'])
@pytest.mark.parametrize('barrier', ['fsync', '_attached'])
def test_timing_final_authority_after_durability_and_end_checks(
        tmp_path, monkeypatch, operation, mutation, barrier):
    # Measure a real approved operation's final durability/attachment ordinal.
    # Negative hooks call the real filesystem function, THEN change authority.
    real = getattr(api().os if barrier == 'fsync' else api(), barrier)
    target = api().os if barrier == 'fsync' else api()
    count = []
    for attempt in ('reference', 'drift'):
        folder = tmp_path / attempt
        folder.mkdir(mode=0o700)
        env, registry = unknown(), {}
        ds = chain(env, registry)
        s, root, _, _ = host(folder, monkeypatch, env, registry)
        change, restore, check = timing_authority(folder, monkeypatch, s, env, registry, mutation)
        action, expected, first = timing_operation(s, env, ds, operation)
        seen, injected = [], []
        def after_barrier(*args, **kwargs):
            result = real(*args, **kwargs)
            seen.append(1)
            if attempt == 'drift' and len(seen) == len(count):
                change()
                injected.append(1)
            return result
        with monkeypatch.context() as patch:
            patch.setattr(target, barrier, after_barrier)
            if attempt == 'reference':
                result = action()
                assert result['pin'] == expected
                assert result['overlay']['state'] == 'reconciled'
                count = seen[:]
                assert count
            else:
                outcome = refusal(action)
                assert injected == [1]
                check()
                assert outcome != 'ACCEPTED', 'Current authority must be checked after the last barrier'
        # Late failure is uncertain, not rollback: retain the exact commit and
        # permit only exact pending-pin resume once current authority is restored.
        restore()
        assert json.loads((root / RUN / 'head.json').read_bytes()) == expected
        revision = root / RUN / ('rev_%06d.json' % expected['revision'])
        assert sha(revision.read_bytes()) == expected['sha256']
        resumed = s.read(RUN, expected=expected)
        assert resumed['pin'] == expected and resumed['overlay']['state'] == 'reconciled'
        if first is not None:
            assert refusal(lambda: s.read(RUN, expected=first['pin'])) != 'ACCEPTED'


@pytest.mark.parametrize('operation', ['read', 'create', 'append'])
def test_timing_return_is_last_fresh_full_overlay(tmp_path, monkeypatch, operation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    action, expected, _ = timing_operation(s, env, ds, operation)
    real, seen = s._overlay, []
    def track(observations, decisions):
        result = real(observations, decisions)
        seen.append((copy.deepcopy(decisions), result[0]))
        return result
    monkeypatch.setattr(s, '_overlay', track)
    result = action()
    assert seen[-1][0] == ds
    assert result['overlay'] is seen[-1][1]
    assert result['pin'] == expected
    record = json.loads((root / RUN / ('rev_%06d.json' % expected['revision'])).read_bytes())
    assert result['overlay'] == record['overlay']


@pytest.mark.parametrize('mutation', ['revoked', 'registry_bytes', 'source_bytes'])
@pytest.mark.parametrize('operation,checkpoint', [
    ('create', 'staged_write'), ('append', 'staged_write'),
    ('create', 'prepublish_guard'), ('append', 'prepublish_guard'),
    ('append', 'revision_publish'), ('append', 'intermediate_fsync'),
    ('append', 'head_check'), ('append', 'head_guard'),
])
def test_timing_proposed_authority_at_publication_checkpoints(
        tmp_path, monkeypatch, mutation, operation, checkpoint):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    change, restore, check = timing_authority(tmp_path, monkeypatch, s, env, registry, mutation)
    action, intended, first = timing_operation(s, env, ds, operation)
    old = None if first is None else (root / RUN / 'rev_000001.json').read_bytes()
    target = root / RUN / 'rev_000002.json'
    staged_head, injected = [], []
    def inject():
        if not injected:
            injected.append(1)
            change()
    real_write, real_publish = api()._write_file, api()._publish
    real_sync, real_read, real_attached = api().os.fsync, api()._read_file, api()._attached
    def write(parent, name, raw):
        real_write(parent, name, raw)
        if name == 'head.json':
            staged_head.append(1)
            if checkpoint == 'staged_write':
                inject()
    def publish(parent, old, new, **kwargs):
        real_publish(parent, old, new, **kwargs)
        if checkpoint == 'revision_publish':
            inject()
    def sync(fd):
        real_sync(fd)
        if (checkpoint == 'intermediate_fsync' and target.exists()
                and os.fstat(fd).st_ino == (root / RUN).stat().st_ino):
            inject()
    def read(parent, name):
        raw = real_read(parent, name)
        if checkpoint == 'head_check' and name == 'head.json' and target.exists():
            inject()
        return raw
    def attached(parent, name, fd, **kwargs):
        real_attached(parent, name, fd, **kwargs)
        if name == '.lock' and staged_head:
            if checkpoint == 'prepublish_guard' and not target.exists():
                inject()
            elif checkpoint == 'head_guard' and target.exists():
                inject()
    with monkeypatch.context() as patch:
        patch.setattr(api(), '_write_file', write)
        patch.setattr(api(), '_publish', publish)
        patch.setattr(api().os, 'fsync', sync)
        patch.setattr(api(), '_read_file', read)
        patch.setattr(api(), '_attached', attached)
        outcome = refusal(action)
    assert injected == [1]
    check()
    assert outcome != 'ACCEPTED'
    prepublication = checkpoint in ('staged_write', 'prepublish_guard')
    if operation == 'create':
        assert not (root / RUN).exists(), 'Revoked creation must not publish a run'
        assert {p.name for p in root.iterdir()} == {'.lock'}
    else:
        assert (root / RUN / 'rev_000001.json').read_bytes() == old
        assert json.loads((root / RUN / 'head.json').read_bytes()) == first['pin'], (
            'Old head must remain when proposed authority fails before logical commit')
        assert target.exists() is not prepublication
        assert not any(p.name.startswith('.stage_') for p in (root / RUN).iterdir())
        if not prepublication:
            # Immutable publication is irreversible: keep the prepared orphan,
            # including its exact proposed bytes. Never "repair" by deletion.
            assert sha(target.read_bytes()) == intended['sha256']
    restore()
    if operation == 'create':
        assert s.create(RUN, env, ds)['pin'] == intended
    elif prepublication:
        assert s.read(RUN, expected=first['pin']) == first
        assert s.append(RUN, env, ds, expected=first['pin'])['pin'] == intended
    else:
        assert refusal(lambda: s.read(RUN, expected=first['pin'])) != 'ACCEPTED'
        assert refusal(lambda: s.read(RUN, expected=intended)) != 'ACCEPTED'
        assert refusal(lambda: s.append(RUN, env, ds, expected=first['pin'])) != 'ACCEPTED'
        assert sha(target.read_bytes()) == intended['sha256']


@pytest.mark.parametrize('mutation', ['revoked', 'registry_bytes', 'source_bytes'])
def test_timing_append_cleanup_fsync_requires_current_authority(tmp_path, monkeypatch, mutation):
    env, registry = unknown(), {}
    ds = chain(env, registry)
    s, root, _, _ = host(tmp_path, monkeypatch, env, registry)
    change, restore, check = timing_authority(tmp_path, monkeypatch, s, env, registry, mutation)
    action, intended, _ = timing_operation(s, env, ds, 'append')
    removed, injected = [], []
    real_rmdir, real_sync = api().os.rmdir, api().os.fsync
    def remove(*args, **kwargs):
        real_rmdir(*args, **kwargs)
        removed.append(1)
    def sync(fd):
        real_sync(fd)
        if removed and not injected:
            injected.append(1)
            change()
    with monkeypatch.context() as patch:
        patch.setattr(api().os, 'rmdir', remove)
        patch.setattr(api().os, 'fsync', sync)
        outcome = refusal(action)
    assert injected == [1]
    check()
    assert outcome != 'ACCEPTED'
    assert json.loads((root / RUN / 'head.json').read_bytes()) == intended
    assert sha((root / RUN / 'rev_000002.json').read_bytes()) == intended['sha256']
    restore()
    assert s.read(RUN, expected=intended)['pin'] == intended
