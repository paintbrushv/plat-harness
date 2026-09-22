"""Host-internal immutable intake storage; no request paths or approval authority.

Linux local filesystems only. See docs/INGEST_STORE.md for external latest-pin
requirements, crash semantics, bounds and the trusted host intake boundary.
"""
from contextlib import contextmanager
from copy import deepcopy
import ctypes
import fcntl
from functools import wraps
import hashlib
import os
import re
import stat
import uuid

from . import contracts as c
from .reconciliation import reconcile_request
from .source_resolver import ByteSourceResolver, digest

VERSION = 'ingest-store/1.0.0'
MAX_REVISIONS = 64
MAX_BYTES = c.MAX_BYTES
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC
_PIN_KEYS = ('run_id', 'revision', 'sha256', 'envelope_sha256',
             'context_sha256', 'decision_head_sha256')


class StoreError(ValueError):
    """A failed write may be visible: use an independently retained exact pin."""
    def __init__(self):
        self.code = 'STORE_REFUSED_OR_UNCERTAIN'
        super().__init__('Intake store refused or uncertain (STORE_REFUSED_OR_UNCERTAIN).')


def _boundary(fn):
    @wraps(fn)
    def safe(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception:
            pass
        # Outside the handler: neither cause nor context retains source text.
        raise StoreError() from None
    return safe


def _require(value):
    if not value:
        raise ValueError('STORE_INVALID')


def _run_id(value):
    _require(type(value) is str and re.fullmatch(r'run_[0-9a-f]{32}', value) is not None)


def _sha(value):
    _require(type(value) is str and re.fullmatch(r'[0-9a-f]{64}', value) is not None)


def _pin(value, run_id):
    c._tree(value)
    c._object(value, _PIN_KEYS)
    _run_id(value['run_id'])
    _require(value['run_id'] == run_id)
    _require(type(value['revision']) is int and 1 <= value['revision'] <= MAX_REVISIONS)
    for field in ('sha256', 'envelope_sha256', 'context_sha256'):
        _sha(value[field])
    if value['decision_head_sha256'] is not None:
        _sha(value['decision_head_sha256'])
    return deepcopy(value)


def _same_inode(a, b):
    return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)


def _directory(st, private=False):
    _require(stat.S_ISDIR(st.st_mode) and st.st_uid in (0, os.geteuid()))
    _require(not st.st_mode & 0o022 and not st.st_mode & 0o7000)
    if private:
        _require(st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700)


def _regular(st):
    _require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()
             and stat.S_IMODE(st.st_mode) == 0o600 and st.st_nlink == 1)


def _attached(parent, name, fd, directory=False, private=True):
    live = os.stat(name, dir_fd=parent, follow_symlinks=False)
    opened = os.fstat(fd)
    _require(_same_inode(live, opened))
    if directory:
        _directory(live, private)
        _directory(opened, private)
    else:
        _regular(live)
        _regular(opened)


@contextmanager
def _root(path):
    # Do not normalize away a symlink, dot, repeated slash or traversal token.
    _require(type(path) is str and path.startswith('/') and len(path) <= 4096)
    parts = path.split('/')[1:]
    _require(bool(parts) and all(p and p not in ('.', '..') for p in parts))
    fds, links = [], []
    try:
        fd = os.open('/', _DIR)
        fds.append(fd)
        _directory(os.fstat(fd))
        for index, part in enumerate(parts):
            child = os.open(part, _DIR, dir_fd=fd)
            fds.append(child)
            private = index == len(parts) - 1
            _attached(fd, part, child, directory=True, private=private)
            links.append((fd, part, child, private))
            fd = child
        def stable():
            _directory(os.fstat(fds[0]))
            for parent, name, child, private in links:
                _attached(parent, name, child, directory=True, private=private)
        stable()
        yield fd, stable
        stable()
    finally:
        for fd in reversed(fds):
            os.close(fd)


@contextmanager
def _run(root, name):
    fd = os.open(name, _DIR, dir_fd=root)
    try:
        _attached(root, name, fd, directory=True)
        yield fd
        _attached(root, name, fd, directory=True)
    finally:
        os.close(fd)


@contextmanager
def _lock(root, stable, *, create=False):
    # Never truncate/recreate an existing lock. All operations share this inode.
    created = False
    if create:
        try:
            fd = os.open('.lock', os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                         | os.O_NONBLOCK | os.O_CLOEXEC, 0o600, dir_fd=root)
            created = True
        except FileExistsError:
            fd = os.open('.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                         dir_fd=root)
    else:
        fd = os.open('.lock', os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=root)
    try:
        _attached(root, '.lock', fd)
        _require(os.fstat(fd).st_size == 0)
        fcntl.flock(fd, fcntl.LOCK_EX)
        stable()
        _attached(root, '.lock', fd)
        if created:
            os.fsync(fd)
            os.fsync(root)
        def guard():
            stable()
            _attached(root, '.lock', fd)
            _require(os.fstat(fd).st_size == 0)
        guard()
        yield guard
        guard()
    finally:
        os.close(fd)  # releases the lock, without reopening an attacker inode


def _read_file(parent, name):
    fd = os.open(name, _FILE, dir_fd=parent)
    try:
        _attached(parent, name, fd)
        before = os.fstat(fd)
        _require(0 < before.st_size <= MAX_BYTES)
        data = bytearray()
        while len(data) <= MAX_BYTES:
            chunk = os.read(fd, min(65536, MAX_BYTES + 1 - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        # An exact resume read must establish durability after an uncertain write.
        os.fsync(fd)
        after = os.fstat(fd)
        _attached(parent, name, fd)
        _require((before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                 (after.st_size, after.st_mtime_ns, after.st_ctime_ns))
        _require(len(data) == before.st_size)
        return bytes(data)
    finally:
        os.close(fd)


def _write_file(parent, name, data):
    _require(type(data) is bytes and 0 < len(data) <= MAX_BYTES)
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
                 | os.O_CLOEXEC, 0o600, dir_fd=parent)
    try:
        _attached(parent, name, fd)
        view = memoryview(data)
        while view:
            count = os.write(fd, view)
            _require(count > 0)
            view = view[count:]
        os.fsync(fd)
        _attached(parent, name, fd)
    finally:
        os.close(fd)


def _publish(parent, old, new, *, destination=None):
    # Linux renameat2(RENAME_NOREPLACE) atomically publishes files/directories.
    # No link/unlink window with nlink=2 and no rename-overwrite fallback.
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    target = parent if destination is None else destination
    if rename(parent, old.encode(), target, new.encode(), 1) != 0:
        raise OSError(ctypes.get_errno(), 'STORE_PUBLISH_FAILED')


def _name(number):
    return 'rev_' + format(number, '06d') + '.json'


def _absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise ValueError('STORE_COLLISION')


@contextmanager
def _staging(parent):
    """Own one new private namespace; remove only its fixed, known temp names."""
    name = '.stage_' + uuid.uuid4().hex
    os.mkdir(name, mode=0o700, dir_fd=parent)
    fd = os.open(name, _DIR, dir_fd=parent)
    try:
        _attached(parent, name, fd, directory=True)
        yield name, fd
    finally:
        try:
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass  # published directory: never clean the permanent run
            else:
                _attached(parent, name, fd, directory=True)
                for item in ('rev_000001.json', 'revision.json', 'head.json'):
                    try:
                        st = os.stat(item, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    _regular(st)
                    os.unlink(item, dir_fd=fd)
                os.fsync(fd)
                _attached(parent, name, fd, directory=True)
                os.rmdir(name, dir_fd=parent)
                os.fsync(parent)
        finally:
            os.close(fd)


def _encoded(value):
    c._tree(value)
    raw = c._encode(value)
    _require(len(raw) <= MAX_BYTES)
    return raw


def _decode(raw):
    value = c._decode(raw)
    _require(_encoded(value) == raw)  # canonical bytes, no duplicate/extra representation
    return value


def _record_pin(record, raw):
    overlay = record['overlay']
    return dict(run_id=record['run_id'], revision=record['revision'],
                sha256=hashlib.sha256(raw).hexdigest(),
                envelope_sha256=overlay['envelope_sha256'],
                context_sha256=overlay['context_sha256'],
                decision_head_sha256=overlay['head_sha256'])


class IntakeStore:
    """Host-only capabilities. Never construct this object from request JSON.

    source_loader constructs a fresh exact ByteSourceResolver from current trusted
    originals. The registry is always the existing reconcile_request host loader.
    Caller must not mutate host configuration or inputs concurrently.
    """
    @_boundary
    def __init__(self, *, root, subject_id, as_of, expected_envelope_sha256,
                 expected_context_sha256, source_loader):
        _require(type(root) is str and callable(source_loader))
        c._scope(subject_id, as_of)
        _sha(expected_envelope_sha256)
        _sha(expected_context_sha256)
        self._root = root
        self._subject = subject_id
        self._as_of = as_of
        self._envelope = expected_envelope_sha256
        self._context = expected_context_sha256
        self._source_loader = source_loader

    def _overlay(self, observations, decisions):
        env = c.validate_observations(observations, subject_id=self._subject, as_of=self._as_of)
        _require(digest(env) == self._envelope)
        c._tree(decisions)
        _require(type(decisions) is list and len(decisions) <= 256)
        decisions = deepcopy(decisions)
        # Persistence rejects duplicate replays: each append adds unique IDs.
        _require(len({d['decision_id'] for d in decisions}) == len(decisions))
        source = self._source_loader()
        _require(type(source) is ByteSourceResolver and digest(source.context()) == self._context)
        overlay = reconcile_request(dict(observations=env, decisions=decisions), source_resolver=source)
        _require(overlay['context_sha256'] == self._context)
        return overlay, decisions

    def _build(self, run_id, observations, decisions, previous=None):
        _run_id(run_id)
        if previous is not None:
            previous = _pin(previous, run_id)
            _require(previous['envelope_sha256'] == self._envelope and
                     previous['context_sha256'] == self._context)
        number = 1 if previous is None else previous['revision'] + 1
        _require(number <= MAX_REVISIONS)
        overlay, signed = self._overlay(observations, decisions)
        record = dict(contract_version=VERSION, run_id=run_id, revision=number,
                      previous=previous, overlay=overlay, decisions=signed)
        raw = _encoded(record)
        return record, raw, _record_pin(record, raw)

    @staticmethod
    def _extends(old, new):
        _require(len(new) > len(old) and _encoded(new[:len(old)]) == _encoded(old))

    def _current(self, record, expected):
        """Fresh full-chain authority, bound to the exact verified/intended pin."""
        overlay, signed = self._overlay(record['overlay']['observations'], record['decisions'])
        _require(_encoded(overlay) == _encoded(record['overlay']))
        current = dict(record, overlay=overlay, decisions=signed)
        _require(_record_pin(current, _encoded(current)) == expected)
        return dict(pin=deepcopy(expected), overlay=overlay)

    def _read(self, parent, run_id, expected):
        expected = _pin(expected, run_id)
        _require(expected['envelope_sha256'] == self._envelope and
                 expected['context_sha256'] == self._context)
        _require(_pin(_decode(_read_file(parent, 'head.json')), run_id) == expected)
        # Prepared or stale successors are not a license to accept an old head.
        for number in range(expected['revision'] + 1, MAX_REVISIONS + 1):
            _absent(parent, _name(number))
        cursor, newer, result = expected, None, None
        for number in range(expected['revision'], 0, -1):
            cursor = _pin(cursor, run_id)
            _require(cursor['revision'] == number)
            raw = _read_file(parent, _name(number))
            _require(hashlib.sha256(raw).hexdigest() == cursor['sha256'])
            record = _decode(raw)
            c._object(record, ('contract_version', 'run_id', 'revision', 'previous', 'overlay', 'decisions'))
            _require(record['contract_version'] == VERSION and record['run_id'] == run_id)
            _require(type(record['revision']) is int and record['revision'] == number)
            overlay, signed = self._overlay(record['overlay']['observations'], record['decisions'])
            _require(_encoded(overlay) == _encoded(record['overlay']))
            _require(_record_pin(record, raw) == cursor)
            if result is None:
                # Retain signed content, not an early authorization receipt.
                result = record
            if newer is not None:
                self._extends(signed, newer)
            newer, cursor = signed, record['previous']
        _require(cursor is None)
        return result

    @_boundary
    def preview(self, run_id, observations, decisions, *, expected_prior):
        """Compute an intended pin without IO; NOT proof of a commit or valid CAS."""
        return self._build(run_id, observations, decisions, expected_prior)[2]

    @_boundary
    def append(self, run_id, observations, decisions, *, expected):
        """CAS append a strict full signed-decision extension, never a branch."""
        _pin(expected, run_id)
        record, raw, pin = self._build(run_id, observations, decisions, expected)
        with _root(self._root) as (root, stable), _lock(root, stable) as guard:
            with _run(root, run_id) as fd:
                self._read(fd, run_id, expected)
                prior_raw = _read_file(fd, _name(expected['revision']))
                _require(hashlib.sha256(prior_raw).hexdigest() == expected['sha256'])
                self._extends(_decode(prior_raw)['decisions'], record['decisions'])
                guard()
                with _staging(fd) as (stage, staged):
                    _write_file(staged, 'revision.json', raw)
                    _write_file(staged, 'head.json', _encoded(pin))
                    os.fsync(staged)
                    guard()
                    _attached(root, run_id, fd, directory=True)
                    # The old history cannot authorize the proposed extension.
                    # Recompute its full signed chain after staging/end guards.
                    self._current(record, pin)
                    _publish(staged, 'revision.json', _name(pin['revision']), destination=fd)
                    os.fsync(staged)
                    os.fsync(fd)
                    _require(_pin(_decode(_read_file(fd, 'head.json')), run_id) == expected)
                    guard()
                    _attached(root, run_id, fd, directory=True)
                    # Failure here leaves the permanent prepared revision and
                    # old head intact; cleanup owns only this operation's stage.
                    self._current(record, pin)
                    os.replace('head.json', 'head.json', src_dir_fd=staged, dst_dir_fd=fd)
                    os.fsync(staged)
                    os.fsync(fd)
                latest = self._read(fd, run_id, pin)
        # No history, cleanup fsync or context-manager end check after this
        # checkpoint. It is not an atomic transaction with host authority.
        return self._current(latest, pin)

    @_boundary
    def create(self, run_id, observations, decisions):
        """Create once; validate normalization and current authority before IO."""
        record, raw, pin = self._build(run_id, observations, decisions)
        with _root(self._root) as (root, stable), _lock(root, stable, create=True) as guard:
            _absent(root, run_id)
            with _staging(root) as (stage, fd):
                _write_file(fd, _name(1), raw)
                _write_file(fd, 'head.json', _encoded(pin))
                os.fsync(fd)
                self._read(fd, run_id, pin)
                guard()
                self._current(record, pin)
                _publish(root, stage, run_id)
                os.fsync(root)
            with _run(root, run_id) as fd:
                latest = self._read(fd, run_id, pin)
        return self._current(latest, pin)

    @_boundary
    def read(self, run_id, *, expected):
        """Only current, independently pinned reads; never discover latest head."""
        _run_id(run_id)
        _pin(expected, run_id)
        with _root(self._root) as (root, stable), _lock(root, stable):
            with _run(root, run_id) as fd:
                latest = self._read(fd, run_id, expected)
                os.fsync(fd)
                os.fsync(root)
        return self._current(latest, expected)
