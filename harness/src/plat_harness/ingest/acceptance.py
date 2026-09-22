"""Private host-only acceptance snapshots, not engine permission."""
from dataclasses import dataclass, asdict
from contextlib import contextmanager
from copy import deepcopy
from datetime import date
from functools import wraps
import hashlib
import importlib.metadata
import json
import os
import platform
import re
import stat
import sys
import threading
import time

from . import contracts as c
from . import compat, pms_normalizer


_ERRORS = frozenset('INVALID_HOST_INPUT INVALID_MANIFEST MANIFEST_HASH_MISMATCH CODE_PIN_MISMATCH RUNTIME_MISMATCH UNSAFE_OUTPUT_ROOT RUN_COLLISION CANCELLED ARTIFACT_REFUSED WRITE_UNCERTAIN'.split())
_BASE = ('plat_harness', 'plat_harness.errors', 'plat_harness.glossary',
    'plat_harness.models', 'plat_harness.ranks', 'plat_harness.ingest',
    'plat_harness.ingest.acceptance', 'plat_harness.ingest.contracts',
    'plat_harness.ingest.compat', 'plat_harness.ingest.pms_normalizer')
_AUTH = ('plat_harness.ingest.reconciliation', 'plat_harness.ingest.source_resolver',
    'plat_harness.contracts', 'plat_harness.adapters', 'plat_harness.adapters.review_bridge',
    'plat_harness.adapters.slice_b', 'plat_harness.adapters.paths', 'plat_harness.millage',
    'plat_harness.tools', 'plat_harness.tools.catalog', 'plat_harness.tools.certified_metric',
    'plat_harness.occupancy')
_MANIFEST = frozenset('contract_version corpus_id corpus_revision corpus_basis_sha256 predecessor change_kind code_manifest_sha256 runtime entries'.split())
_ENTRY = frozenset('entry_id source_id source_sha256 source_size_bytes source_role intake_id subject_id as_of entity_kind snapshot_id corpus_use selection exclusion_code duplicate_of adapter expected_container requested_stage decisions_sha256'.split())
_EXCLUSIONS = frozenset('OUTSIDE_FROZEN_SELECTION DERIVATIVE NON_RR_SOURCE ALTERNATE_SNAPSHOT PORTFOLIO_COMPARISON DECLARED_DUPLICATE'.split())
_DIR = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
_FILE = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC


class _Failure(Exception):
    pass


def _require(value, code='INVALID_MANIFEST'):
    if not value:
        raise _Failure(code)


def _boundary(default):
    def decorate(fn):
        @wraps(fn)
        def safe(*args, **kwargs):
            code = default
            try:
                return fn(*args, **kwargs)
            except _Failure as exc:
                code = exc.args[0] if exc.args[0] in _ERRORS else default
            except Exception:
                pass
            raise AcceptanceError(code) from None
        return safe
    return decorate


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False,
                      allow_nan=False).encode('utf-8')


def _tree(value, cap, *, strings=128):
    stack = [(value, 0)]
    nodes = size = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(depth <= 16 and nodes <= 500000)
        if type(item) is dict:
            _require(len(item) <= 50000)
            for key, child in item.items():
                _require(type(key) is str)
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif type(item) is list:
            _require(len(item) <= 50000)
            stack.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            _require(len(item) <= strings)
            size += len(item.encode('utf-8'))
            _require(size <= cap)
        else:
            _require(item is None or type(item) is bool or
                     (type(item) is int and abs(item) <= 2 ** 40))
    _require(len(_encode(value)) <= cap)


def _decode(raw, cap):
    _require(type(raw) is bytes and len(raw) <= cap)
    text = raw.decode('utf-8')
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped: escaped = False
            elif char == '\\': escaped = True
            elif char == '"': quoted = False
        elif char == '"': quoted = True
        elif char in '[{':
            depth += 1
            _require(depth <= 16)
        elif char in ']}': depth -= 1
    def pairs(items):
        result = {}
        for key, value in items:
            _require(key not in result)
            result[key] = value
        return result
    def no_number(_):
        raise _Failure('INVALID_MANIFEST')
    value = json.loads(text, object_pairs_hook=pairs, parse_float=no_number, parse_constant=no_number)
    _tree(value, cap)
    return value


def _shape(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _pattern(value, pattern):
    _require(type(value) is str and re.fullmatch(pattern, value, re.ASCII) is not None)


def _id(value, prefix):
    _pattern(value, prefix + r'_[0-9a-f]{32}')


def _hash(value):
    _pattern(value, r'[0-9a-f]{64}')


def _int(value, high=2 ** 40, low=0):
    _require(type(value) is int and low <= value <= high)


def _scope(subject, day):
    if subject is not None:
        _pattern(subject, r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
    if day is not None:
        _pattern(day, r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
        date.fromisoformat(day)


def _manifest(value, limits):
    _shape(value, _MANIFEST)
    _require(value['contract_version'] == 'ingest-acceptance-manifest/1.0.0')
    _id(value['corpus_id'], 'corpus')
    _int(value['corpus_revision'], 1000000, 1)
    for key in ('corpus_basis_sha256', 'code_manifest_sha256'): _hash(value[key])
    _require(value['change_kind'] in ('initial', 'source_correction', 'selection_change',
                                    'adapter_change', 'review_change', 'repeat_assessment'))
    prior = value['predecessor']
    _require((prior is None) == (value['change_kind'] == 'initial'))
    if prior is not None:
        _shape(prior, ('run_id', 'matrix_sha256', 'manifest_sha256'))
        _id(prior['run_id'], 'acc')
        _hash(prior['matrix_sha256']); _hash(prior['manifest_sha256'])
    runtime = value['runtime']
    _shape(runtime, ('python_version', 'openpyxl_version', 'xlrd_version'))
    for key, version in runtime.items():
        if version is not None or key == 'python_version':
            _pattern(version, r'[A-Za-z0-9][A-Za-z0-9.+_-]{0,127}')
    entries = value['entries']
    _require(type(entries) is list and len(entries) <= limits.max_entries)
    ids, sids = {}, set()
    for e in entries:
        _shape(e, _ENTRY)
        _id(e['entry_id'], 'entry'); _id(e['source_id'], 'src'); _id(e['snapshot_id'], 'snap')
        _require(e['entry_id'] not in ids and e['source_id'] not in sids)
        ids[e['entry_id']] = e; sids.add(e['source_id'])
        _hash(e['source_sha256']); _int(e['source_size_bytes'])
        _require(e['source_role'] in ('original', 'derivative', 'unknown'))
        if e['intake_id'] is not None: _id(e['intake_id'], 'intake')
        _scope(e['subject_id'], e['as_of'])
        _require(e['entity_kind'] in ('property', 'portfolio', 'unknown'))
        _require(e['corpus_use'] in ('inventory', 'comparison_only'))
        _require(e['requested_stage'] in ('observe', 'reconcile'))
        if e['entity_kind'] == 'portfolio':
            _require(e['subject_id'] is None and e['corpus_use'] == 'comparison_only' and e['requested_stage'] == 'observe')
        if e['requested_stage'] == 'observe':
            _require(e['decisions_sha256'] is None)
        else:
            _hash(e['decisions_sha256'])
            _require(e['entity_kind'] == 'property' and e['corpus_use'] == 'inventory')
        _require(e['selection'] in ('run', 'excluded', 'declared_duplicate'))
        if e['selection'] == 'run':
            _require(e['exclusion_code'] is None and e['duplicate_of'] is None)
        elif e['selection'] == 'excluded':
            _require(e['exclusion_code'] in _EXCLUSIONS - {'DECLARED_DUPLICATE'} and e['duplicate_of'] is None)
        else:
            _require(e['exclusion_code'] == 'DECLARED_DUPLICATE')
            _id(e['duplicate_of'], 'entry')
        _shape(e['adapter'], ('id', 'version'))
        _pattern(e['adapter']['id'], r'[a-z][a-z0-9_-]{0,63}')
        _pattern(e['adapter']['version'], r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})')
        _require(e['expected_container'] in ('csv', 'xlsx', 'xls_biff', 'pdf', 'unknown'))
    for e in entries:
        if e['selection'] == 'declared_duplicate':
            target = ids.get(e['duplicate_of'])
            _require(target is not None and target['selection'] != 'declared_duplicate')
            _require(all(e[k] == target[k] for k in ('source_sha256', 'source_size_bytes',
                'snapshot_id', 'subject_id', 'as_of', 'entity_kind', 'corpus_use', 'expected_container', 'source_role')))
    return value


def _path(path):
    _require(type(path) is str and path.startswith('/') and len(path) <= 4096, 'INVALID_HOST_INPUT')
    parts = path.split('/')[1:]
    _require(bool(parts) and all(p and p not in ('.', '..') and '\x00' not in p for p in parts), 'INVALID_HOST_INPUT')
    return parts


def _runtime(value):
    actual = {'python_version': platform.python_version()}
    for dependency in ('openpyxl', 'xlrd'):
        try:
            actual[dependency + '_version'] = importlib.metadata.version(dependency)
        except importlib.metadata.PackageNotFoundError:
            actual[dependency + '_version'] = None
    _require(actual == value, 'RUNTIME_MISMATCH')


@dataclass(frozen=True)
class SourcePath:
    root: str
    relative_parts: tuple[str, ...]


@dataclass(frozen=True)
class AcceptanceLimits:
    max_manifest_bytes: int = 1024 * 1024
    max_entries: int = 128
    max_source_bytes: int = 8 * 1024 * 1024
    max_total_source_bytes: int = 256 * 1024 * 1024
    max_matrix_bytes: int = 8 * 1024 * 1024
    max_artifact_bytes: int = 128 * 1024 * 1024
    worker_wall_seconds: int = 60
    worker_cpu_seconds: int = 30
    worker_address_space_bytes: int = 1024 * 1024 * 1024
    run_wall_seconds: int = 600


class AcceptanceError(ValueError):
    def __init__(self, code='INVALID_HOST_INPUT'):
        self.code = code if type(code) is str and code in _ERRORS else 'INVALID_HOST_INPUT'
        super().__init__('Acceptance refused (' + self.code + ').')


# Acceptance-local descriptor routines: deliberately independent of IntakeStore.
def _stamp(st):
    return st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns, st.st_ctime_ns


def _directory(st, private=False):
    _require(stat.S_ISDIR(st.st_mode) and st.st_uid in (0, os.geteuid()) and
             not st.st_mode & 0o7022, 'ARTIFACT_REFUSED')
    if private:
        _require(st.st_uid == os.geteuid() and stat.S_IMODE(st.st_mode) == 0o700, 'ARTIFACT_REFUSED')


def _regular(st, source=False):
    modes = (0o400, 0o600, 0o444, 0o644) if source else (0o600,)
    _require(stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid() and
             st.st_nlink == 1 and stat.S_IMODE(st.st_mode) in modes, 'ARTIFACT_REFUSED')


def _attached(parent, name, fd, *, directory=False, private=False, source=False):
    live, opened = os.stat(name, dir_fd=parent, follow_symlinks=False), os.fstat(fd)
    _require((live.st_dev, live.st_ino) == (opened.st_dev, opened.st_ino), 'ARTIFACT_REFUSED')
    for st in (live, opened):
        if directory: _directory(st, private)
        else: _regular(st, source)


@contextmanager
def _root(path):
    parts = _path(path)
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
        for fd in reversed(fds): os.close(fd)


def _read_file(parent, name, cap):
    fd = os.open(name, _FILE, dir_fd=parent)
    try:
        _attached(parent, name, fd)
        before = os.fstat(fd)
        _require(0 < before.st_size <= cap, 'ARTIFACT_REFUSED')
        raw = bytearray()
        while len(raw) <= cap:
            chunk = os.read(fd, min(65536, cap + 1 - len(raw)))
            if not chunk: break
            raw.extend(chunk)
        os.fsync(fd)
        _attached(parent, name, fd)
        _require(_stamp(before) == _stamp(os.fstat(fd)) and len(raw) == before.st_size, 'ARTIFACT_REFUSED')
        return bytes(raw)
    finally:
        os.close(fd)


def _write_file(parent, name, raw):
    fd = os.open(name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC,
                 0o600, dir_fd=parent)
    try:
        _attached(parent, name, fd)
        view = memoryview(raw)
        while view:
            count = os.write(fd, view)
            _require(count > 0, 'ARTIFACT_REFUSED')
            view = view[count:]
        os.fsync(fd)
        _attached(parent, name, fd)
    finally:
        os.close(fd)


def _publish(parent, old, new):
    import ctypes
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = (ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint)
    rename.restype = ctypes.c_int
    if rename(parent, old.encode(), parent, new.encode(), 1):
        import errno
        _require(ctypes.get_errno() != errno.EEXIST, 'RUN_COLLISION')
        raise _Failure('ARTIFACT_REFUSED')


def _absent(parent, name):
    try:
        os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return
    raise _Failure('RUN_COLLISION')


@contextmanager
def _stage(parent, names):
    import uuid
    name = '.stage_' + uuid.uuid4().hex
    os.mkdir(name, 0o700, dir_fd=parent)
    fd = os.open(name, _DIR, dir_fd=parent)
    try:
        _attached(parent, name, fd, directory=True, private=True)
        yield name, fd
    finally:
        try:
            try: os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError: pass  # Never remove a published namespace.
            else:
                _attached(parent, name, fd, directory=True, private=True)
                for item in names:
                    try: st = os.stat(item, dir_fd=fd, follow_symlinks=False)
                    except FileNotFoundError: continue
                    _regular(st)
                    os.unlink(item, dir_fd=fd)
                os.fsync(fd)
                os.rmdir(name, dir_fd=parent)
                os.fsync(parent)
        finally:
            os.close(fd)


def _null_counts():
    return {scope: dict.fromkeys(c.COUNT_FIELDS) for scope in c.SCOPES}


def _row(e):
    keys = 'entry_id source_id subject_id as_of entity_kind snapshot_id corpus_use selection exclusion_code duplicate_of requested_stage adapter expected_container decisions_sha256'.split()
    return {**{k: deepcopy(e[k]) for k in keys}, 'detected_container': None,
        'expected_source_sha256': e['source_sha256'], 'observed_source_sha256': None,
        'source_size_bytes': None, 'parser_started': False, 'v2_valid': False,
        'state': e['selection'] if e['selection'] != 'run' else 'refused',
        'blockers': [], 'warnings': [], 'observation_sha256': None,
        'preservation_sha256': None, 'citation_ledger_sha256': None,
        'reconciliation_sha256': None,
        'reconciliation_state': 'not_requested' if e['requested_stage'] == 'observe' else 'unavailable',
        'counts_basis': 'none', 'counts': _null_counts(), 'engine_eligibility': 'not_evaluated'}


def _totals(rows):
    selected = [r for r in rows if r['selection'] == 'run']
    inventory = [r for r in selected if r['entity_kind'] == 'property' and
                 r['corpus_use'] == 'inventory' and r['subject_id'] is not None and
                 not any(d['code']=='DUPLICATE_SOURCE' for d in r['blockers'])]
    subjects = {r['subject_id'] for r in inventory}
    result = {k: sum(r['state'] == k for r in rows) for k in ('excluded', 'declared_duplicate',
        'refused', 'parsed_with_blockers', 'observed_unvalidated', 'reconciled')}
    result.update(declared=len(rows), selected=len(selected),
        parser_started=sum(r['parser_started'] for r in rows), validated_v2=sum(r['v2_valid'] for r in rows),
        distinct_selected_properties=len(subjects),
        distinct_reconciled_properties=sum(all(r['state'] == 'reconciled' for r in inventory
                                             if r['subject_id'] == s) for s in subjects),
        portfolio_entries=sum(r['entity_kind'] == 'portfolio' for r in rows),
        selected_snapshots=len({r['snapshot_id'] for r in selected}), engine_eligible=0)
    return result


def _assessment(t):
    if not t['parser_started']: return 'no_sources_ran'
    if t['refused'] or t['parsed_with_blockers']: return 'blocked'
    if t['reconciled'] == t['selected'] and t['distinct_reconciled_properties']: return 'reconciled_selected'
    return 'observed_unvalidated'


def _pin(matrix, raw):
    return {k: matrix[k] for k in ('run_id', 'manifest_sha256', 'code_manifest_sha256')} | {'matrix_sha256': _sha(raw)}


_ACCEPTANCE_CODES = frozenset('ORIGINAL_REQUIRED SOURCE_MISSING SOURCE_UNSAFE SOURCE_CHANGED SOURCE_HASH_MISMATCH SOURCE_SIZE_MISMATCH SOURCE_LIMIT DUPLICATE_SOURCE AMBIGUOUS_SNAPSHOT SCOPE_REQUIRED ADAPTER_UNSUPPORTED CONTAINER_MISMATCH WORKER_SETUP_REFUSED WORKER_TIMEOUT WORKER_EXIT WORKER_PROTOCOL RUN_LIMIT EMPTY_PARSE CITATION_MISSING CITATION_UNSUPPORTED CITATION_EMPTY_OR_IMPLICIT PRIVACY_REFUSED RECONCILIATION_UNSUPPORTED RECONCILIATION_SOURCE_LIMIT RECONCILIATION_REFUSED'.split())
_PARSER_CODES = frozenset('UNSUPPORTED_PMS MALFORMED_INPUT INPUT_LIMIT_EXCEEDED EMPTY_INPUT UNSUPPORTED_INPUT_FORMAT UNSUPPORTED_PMS_FORMAT XLSX_DEPENDENCY_MISSING AMBIGUOUS_HEADER HEADER_NOT_FOUND UNSUPPORTED_ONESITE_LAYOUT UNSUPPORTED_XLS_FORMAT XLS_DEPENDENCY_MISSING MALFORMED_XLS'.split())
_KINDS = {'observations': 'observation_sha256', 'preservation': 'preservation_sha256',
          'citations': 'citation_ledger_sha256', 'resolution': 'reconciliation_sha256'}


def _diag(row, code, stage='source', namespace='acceptance', warning=False):
    allowed = {'acceptance': _ACCEPTANCE_CODES, 'parser': _PARSER_CODES,
               'migration': compat.ERROR_CODES, 'observation': c.ISSUE_CODES, 'resolution': _RESOLUTION_CODES}
    _require(namespace in allowed and code in allowed[namespace], 'ARTIFACT_REFUSED')
    row['warnings' if warning else 'blockers'].append({'stage': stage, 'namespace': namespace, 'code': code})


def _source(handle, entry, cap, deadline):
    code = 'SOURCE_UNSAFE'
    stamp = None
    try:
        with _root(handle.root) as (root, stable):
            links, fds = [], []
            try:
                parent = root
                for part in handle.relative_parts[:-1]:
                    fd = os.open(part, _DIR, dir_fd=parent)
                    fds.append(fd)
                    _attached(parent, part, fd, directory=True)
                    links.append((parent, part, fd))
                    parent = fd
                name = handle.relative_parts[-1]
                fd = os.open(name, _FILE | os.O_NOATIME, dir_fd=parent)
                fds.append(fd)
                _attached(parent, name, fd, source=True)
                before = os.fstat(fd)
                stamp = _stamp(before)
                _require(before.st_size <= cap, 'SOURCE_LIMIT')
                raw = bytearray()
                while len(raw) <= cap:
                    _require(time.monotonic() < deadline, 'RUN_LIMIT')
                    chunk = os.read(fd, min(65536, cap + 1 - len(raw)))
                    if not chunk: break
                    raw.extend(chunk)
                _require(len(raw) <= cap, 'SOURCE_LIMIT')
                _require(stamp == _stamp(os.fstat(fd)) and len(raw) == before.st_size, 'SOURCE_CHANGED')
                _attached(parent, name, fd, source=True)
                for parent, name, fd in links: _attached(parent, name, fd, directory=True)
                stable()
                _require(len(raw) == entry['source_size_bytes'], 'SOURCE_SIZE_MISMATCH')
                _require(_sha(raw) == entry['source_sha256'], 'SOURCE_HASH_MISMATCH')
                result = bytes(raw), stamp
            finally:
                for fd in reversed(fds): os.close(fd)
        return None, result
    except FileNotFoundError:
        code = 'SOURCE_MISSING'
    except _Failure as exc:
        if exc.args[0] in _ACCEPTANCE_CODES: code = exc.args[0]
    except Exception:
        pass
    return code, (None, stamp) if stamp is not None else None


def _container(raw):
    if raw.startswith(compat.MAGIC): return 'xls_biff'
    if raw.startswith(b'PK'): return 'xlsx'
    if raw.startswith(b'%PDF'): return 'pdf'
    try:
        text = raw.decode('utf-8-sig')
        return 'unknown' if '\x00' in text else 'csv'
    except UnicodeError:
        return 'unknown'


def _occurrences(value, pointer=''):
    if type(value) is dict:
        if 'source_sha256' in value and 'source_id' in value:
            yield pointer, value
        else:
            for key in sorted(value):
                child = value[key]
                p = pointer + '/' + key.replace('~', '~0').replace('/', '~1')
                if key == 'citation' and child is None: yield p, None
                else: yield from _occurrences(child, p)
    elif type(value) is list:
        for index, child in enumerate(value):
            yield from _occurrences(child, pointer + '/' + str(index))


def _positions(raw, container):
    """Physical occupancy only. Never return raw cell values or semantic proof."""
    import csv
    import io
    if container == 'csv':
        reader = csv.reader(io.StringIO(raw.decode('utf-8-sig'), newline=''), strict=True)
        cells, start = {}, 1
        for row in reader:
            _require(reader.line_num <= 50000 and len(row) <= 128, 'WORKER_PROTOCOL')
            for column, value in enumerate(row, 1):
                cells[(1, start, reader.line_num, column)] = bool(value.strip())
            _require(len(cells) <= 250000, 'WORKER_PROTOCOL')
            start = reader.line_num + 1
        return cells, None
    if container == 'xlsx':
        import zipfile
        from xml.etree import ElementTree as ET
        ns = '{http://schemas.openxmlformats.org/spreadsheetml/2006/main}'
        rn = '{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id'
        with zipfile.ZipFile(io.BytesIO(raw)) as z:
            infos = z.infolist()
            _require(len(infos) <= 1024 and sum(i.file_size for i in infos) <= 32 * 1024 * 1024 and
                     len({i.filename for i in infos}) == len(infos), 'WORKER_PROTOCOL')
            def xml(name):
                data = z.read(name)
                _require(b'<!DOCTYPE' not in data and b'<!ENTITY' not in data, 'WORKER_PROTOCOL')
                return ET.fromstring(data)
            relations = {}
            for r in xml('xl/_rels/workbook.xml.rels'):
                _require(r.get('Id') not in relations, 'WORKER_PROTOCOL')
                relations[r.get('Id')] = r.attrib
            cells, extents = {}, {}
            for sn, sheet in enumerate(xml('xl/workbook.xml').find(ns + 'sheets'), 1):
                relation = relations[sheet.get(rn)]
                if not relation.get('Type', '').endswith('/worksheet'):
                    return {}, 'CITATION_UNSUPPORTED'
                _require(relation.get('TargetMode') != 'External', 'WORKER_PROTOCOL')
                target = relation['Target']
                target = target[1:] if target.startswith('/') else 'xl/' + target
                _require('..' not in target.split('/') and target.startswith('xl/worksheets/'), 'WORKER_PROTOCOL')
                document = xml(target)
                lastrow = 0
                for row in document.iter(ns + 'row'):
                    number = int(row.get('r', str(lastrow + 1)))
                    _require(lastrow < number <= 50000, 'WORKER_PROTOCOL')
                    lastrow, lastcol = number, 0
                    for cell in row:
                        _require(cell.tag == ns + 'c', 'WORKER_PROTOCOL')
                        coordinate = cell.get('r')
                        col = lastcol + 1
                        if coordinate:
                            match = re.fullmatch(r'([A-Z]{1,3})([1-9][0-9]{0,5})', coordinate)
                            _require(match is not None and int(match[2]) == number, 'WORKER_PROTOCOL')
                            col = 0
                            for letter in match[1]: col = col * 26 + ord(letter) - 64
                        _require(lastcol < col <= 128, 'WORKER_PROTOCOL')
                        lastcol = col
                        populated = any(node.text is not None and bool(node.text.strip())
                                        for node in cell.iter() if node.tag in (ns+'v', ns+'t', ns+'f'))
                        cells[(sn, number, number, col)] = populated
                        _require(len(cells) <= 250000, 'WORKER_PROTOCOL')
            return cells, None
    if container == 'xls_biff':
        import xlrd
        book = xlrd.open_workbook(file_contents=raw, logfile=io.StringIO(), verbosity=0, on_demand=True)
        try:
            _require(book.nsheets == 1, 'WORKER_PROTOCOL')
            sheet = book.sheet_by_index(0)
            _require(sheet.nrows <= 50000 and sheet.ncols <= 128 and sheet.nrows * sheet.ncols <= 250000, 'WORKER_PROTOCOL')
            return {(1, r+1, r+1, col+1): sheet.cell_type(r,col) not in (0,6) and sheet.cell_value(r,col) != ''
                    for r in range(sheet.nrows) for col in range(sheet.ncols)}, None
        finally: book.release_resources()
    return {}, 'CITATION_UNSUPPORTED'


def _citations(env, raw, entry, checker_pin):
    positions, unsupported = _positions(raw, _container(raw))
    occurrences = []
    for pointer, cite in _occurrences(env):
        if cite is None: location = 'null'
        elif unsupported or 'sheet' not in cite: location = 'unsupported'
        elif cite['source_id'] != entry['source_id'] or cite['source_sha256'] != _sha(raw): location = 'missing'
        else:
            key = tuple(cite[k] for k in ('sheet','row','row_end','column'))
            present = positions.get(key)
            location = 'missing' if present is None else 'located' if present else 'empty_or_implicit'
        occurrences.append({'pointer':pointer, 'citation':cite, 'location':location, 'value_check':'not_assessed'})
    totals = {k:sum(o['location'] == k for o in occurrences) for k in ('located','empty_or_implicit','missing','unsupported','null')}
    return {'contract_version':'ingest-acceptance-citations/1.0.0','entry_id':entry['entry_id'],
        'observation_sha256':_sha(_encode(env)),
        'checker':{'id':'original-position-checker','version':'1.0.0','code_sha256':checker_pin},
        'occurrences':occurrences,'totals':{'occurrences':len(occurrences),**totals}}


def _pipeline(raw, entry, checker_pin):
    import io
    meta = {'source_id':entry['source_id'], 'subject_id':entry['subject_id'], 'as_of':entry['as_of'],
            'adapter_id':entry['adapter']['id'], 'original_bytes':raw}
    old = pms_normalizer.normalize_rent_roll(io.BytesIO(raw), compat.ADAPTERS[entry['adapter']['id']])
    env = compat.migrate_v1(old, **meta)
    c.canonical_bytes(env, subject_id=entry['subject_id'], as_of=entry['as_of'])
    preservation = compat.verify_preservation(old, env, **meta)
    return {'observations':env,'preservation':preservation,'citations':_citations(env,raw,entry,checker_pin)}


def _worker_setup(write_fd, limits):
    import logging
    import resource
    import warnings
    # No inherited source/output/authority handles remain accessible to parser code.
    for name in os.listdir('/proc/self/fd'):
        fd = int(name)
        if fd not in (0,1,2,write_fd):
            try: os.close(fd)
            except OSError: pass
    null = os.open('/dev/null', os.O_RDWR)
    try:
        for fd in (0,1,2): os.dup2(null, fd)
    finally:
        if null > 2: os.close(null)
    os.environ.clear()
    os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_HUB_DISABLE_TELEMETRY='1',
                      CUDA_VISIBLE_DEVICES='', PYTHONDONTWRITEBYTECODE='1',
                      OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1')
    sys.dont_write_bytecode = True
    logging.disable(logging.CRITICAL)
    warnings.simplefilter('error')
    for which, cap in ((resource.RLIMIT_CORE,0), (resource.RLIMIT_FSIZE,0),
                       (resource.RLIMIT_CPU,limits.worker_cpu_seconds),
                       (resource.RLIMIT_AS,limits.worker_address_space_bytes)):
        resource.setrlimit(which, (cap, cap))
        _require(resource.getrlimit(which) == (cap,cap), 'WORKER_SETUP_REFUSED')
    # Lowering RLIMIT_AS below inherited mappings does not unmap them. Existing
    # allocator arenas can otherwise let a tiny parse appear to obey an already
    # exceeded host ceiling. Refuse that embedding rather than claim the limit.
    fd = os.open('/proc/self/statm', os.O_RDONLY | os.O_CLOEXEC)
    try:
        pages = os.read(fd, 256).split()[0]
        _require(pages.isdigit() and int(pages) * os.sysconf('SC_PAGE_SIZE') <=
                 limits.worker_address_space_bytes, 'WORKER_SETUP_REFUSED')
    finally: os.close(fd)



def _worker(raw, entry, checker_pin, limits, deadline):
    import selectors
    import signal
    cap = min(limits.max_artifact_bytes, 3 * c.MAX_BYTES)
    read_fd, write_fd = os.pipe2(os.O_CLOEXEC)
    pid = None
    started = False
    try:
        pid = os.fork()
        if pid == 0:
            status = 1
            try:
                _worker_setup(write_fd, limits)
                # Separate started byte is supervisor-owned, not adapter output.
                os.write(write_fd, b'S')
                try:
                    value = _pipeline(raw, entry, checker_pin)
                except pms_normalizer.RentRollNormalizationError as exc:
                    value = {'error':{'namespace':'parser','code':exc.code if exc.code in _PARSER_CODES else 'WORKER_PROTOCOL'}}
                except compat.MigrationError as exc:
                    value = {'error':{'namespace':'migration','code':exc.code if exc.code in compat.ERROR_CODES else 'WORKER_PROTOCOL'}}
                _tree(value, cap)
                data = _encode(value)
                _require(len(data) <= cap, 'WORKER_PROTOCOL')
                view = memoryview(data)
                while view:
                    n = os.write(write_fd, view)
                    _require(n > 0, 'WORKER_PROTOCOL')
                    view = view[n:]
                status = 0
            except BaseException:
                pass
            os._exit(status)
        os.close(write_fd); write_fd = None
        os.set_blocking(read_fd, False)
        data = bytearray()
        end = min(deadline, time.monotonic() + limits.worker_wall_seconds)
        with selectors.DefaultSelector() as selector:
            selector.register(read_fd, selectors.EVENT_READ)
            while True:
                left = end - time.monotonic()
                _require(left > 0, 'WORKER_TIMEOUT')
                if not selector.select(min(left, 0.1)): continue
                chunk = os.read(read_fd, min(65536, cap + 2 - len(data)))
                if not chunk: break
                data.extend(chunk)
                started = data[:1] == b'S'
                _require(len(data) <= cap + 1, 'WORKER_PROTOCOL')
        while True:
            done, status = os.waitpid(pid, os.WNOHANG)
            if done: pid = None; break
            _require(time.monotonic() < end, 'WORKER_TIMEOUT')
            time.sleep(0.001)
        _require(started, 'WORKER_SETUP_REFUSED')
        _require(os.waitstatus_to_exitcode(status) == 0, 'WORKER_EXIT')
        result = _decode(bytes(data[1:]), cap)
        if set(result) == {'error'}:
            err = result['error']
            _shape(err, ('namespace','code'))
            _require(err['namespace'] in ('parser','migration') and err['code'] in
                     (_PARSER_CODES if err['namespace']=='parser' else compat.ERROR_CODES), 'WORKER_PROTOCOL')
            return started, err, None
        _shape(result, ('observations','preservation','citations'))
        env = c.validate_observations(result['observations'], subject_id=entry['subject_id'], as_of=entry['as_of'])
        _require(env['adapter'] == entry['adapter'] and env['sources'] == [{'source_id':entry['source_id'],
            'sha256':entry['source_sha256'],'role':'original','original_source_ids':[],
            'subject_id':entry['subject_id'],'as_of':entry['as_of']}], 'WORKER_PROTOCOL')
        _validate_ledgers(result, entry, checker_pin)
        return started, None, result
    except _Failure as exc:
        code = exc.args[0] if exc.args[0] in _ACCEPTANCE_CODES else 'WORKER_PROTOCOL'
    except Exception:
        code = 'WORKER_PROTOCOL'
    finally:
        if write_fd is not None: os.close(write_fd)
        os.close(read_fd)
        if pid:
            try: os.kill(pid, signal.SIGKILL)
            except ProcessLookupError: pass
            os.waitpid(pid, 0)
    return started, {'namespace':'acceptance','code':code}, None


def _validate_ledgers(result, entry, checker_pin):
    env, p, ledger = (result[k] for k in ('observations','preservation','citations'))
    _shape(p, ('policy_version','source_sha256','retained_v1_sha256','reconstructed_v1_sha256','v2_sha256',
        'observations','scoped_observations','unknown_use_observations','summaries','issues','citation_occurrences',
        'evidence_groups','omitted_unit_identity_fields','omitted_redacted_placeholder_fields'))
    _require(p['policy_version'] == 'v1-preservation/1.0.0' and p['source_sha256'] == entry['source_sha256'] and
             p['v2_sha256'] == _sha(_encode(env)) and p['retained_v1_sha256'] == p['reconstructed_v1_sha256'], 'WORKER_PROTOCOL')
    for k,v in p.items():
        if k.endswith('sha256'): _hash(v)
        elif k != 'policy_version': _int(v,1000000)
    units = env['units'] + env['unknown_use_units']
    expected_counts = {'observations':len(units), 'scoped_observations':len(env['units']),
        'unknown_use_observations':len(env['unknown_use_units']), 'summaries':len(env['summaries']),
        'issues':len(env['issues']), 'evidence_groups':sum(len(u['evidence']) for u in units),
        'omitted_unit_identity_fields':len(units)}
    _require(all(p[k]==v for k,v in expected_counts.items()) and
             p['omitted_redacted_placeholder_fields'] <= 3 * len(units), 'WORKER_PROTOCOL')
    _shape(ledger, ('contract_version','entry_id','observation_sha256','checker','occurrences','totals'))
    _require(ledger['contract_version'] == 'ingest-acceptance-citations/1.0.0' and
        ledger['entry_id'] == entry['entry_id'] and ledger['observation_sha256'] == p['v2_sha256'] and
        ledger['checker'] == {'id':'original-position-checker','version':'1.0.0','code_sha256':checker_pin}, 'WORKER_PROTOCOL')
    expected = list(_occurrences(env))
    _require(type(ledger['occurrences']) is list and len(expected) == len(ledger['occurrences']), 'WORKER_PROTOCOL')
    for (pointer,cite), item in zip(expected, ledger['occurrences']):
        _shape(item, ('pointer','citation','location','value_check'))
        _require(item['pointer'] == pointer and item['citation'] == cite and item['value_check'] == 'not_assessed' and
            item['location'] in ('located','empty_or_implicit','missing','unsupported','null') and
            (cite is None) == (item['location'] == 'null'), 'WORKER_PROTOCOL')
    totals = {k:sum(o['location']==k for o in ledger['occurrences']) for k in ('located','empty_or_implicit','missing','unsupported','null')}
    _shape(ledger['totals'], ('occurrences',*totals))
    for value in ledger['totals'].values(): _int(value,1000000)
    _require(ledger['totals'] == {'occurrences':len(expected),**totals} and
             p['citation_occurrences'] == len(expected)-totals['null'], 'WORKER_PROTOCOL')


def _snapshot(matrix, blobs):
    """Rebuild strict historical structures without contacting current authority."""
    _shape(matrix, ('contract_version','run_id','manifest_sha256','corpus_id','corpus_revision',
        'corpus_basis_sha256','predecessor','change_kind','code_manifest_sha256','input_identity_sha256',
        'runtime','limits','assessment','item2_milestone','engine_eligibility','entries','totals','artifacts'))
    _id(matrix['run_id'],'acc')
    for k in ('manifest_sha256','input_identity_sha256'): _hash(matrix[k])
    _shape(matrix['limits'],asdict(AcceptanceLimits()))
    for k,v in matrix['limits'].items(): _int(v,getattr(AcceptanceLimits(),k),1)
    limits=AcceptanceLimits(**matrix['limits'])
    pseudo={k:deepcopy(matrix[k]) for k in _MANIFEST-{'entries','contract_version'}}
    pseudo.update(contract_version='ingest-acceptance-manifest/1.0.0',entries=[])
    slots={}
    for slot in matrix['artifacts']:
        key=(slot['entry_id'],slot['kind'])
        _require(key not in slots,'ARTIFACT_REFUSED')
        slots[key]=slot
    expected_slots=set()
    for row in matrix['entries']:
        # _row constructs the fixed row schema from a manifest-shaped host entry.
        e={k:deepcopy(row[k]) for k in _ENTRY-{'source_sha256','source_size_bytes','source_role','intake_id'}}
        e.update(source_sha256=row['expected_source_sha256'],source_size_bytes=0,source_role='original',intake_id=None)
        pseudo['entries'].append(e)
        _shape(row,_row(e))
        _require(type(row['parser_started']) is bool and type(row['v2_valid']) is bool,'ARTIFACT_REFUSED')
        _require(row['engine_eligibility']=='not_evaluated','ARTIFACT_REFUSED')
        _require(row['detected_container'] in (None,'csv','xlsx','xls_biff','pdf','unknown'),'ARTIFACT_REFUSED')
        if row['observed_source_sha256'] is not None:
            _hash(row['observed_source_sha256']); _int(row['source_size_bytes'])
            _require(row['observed_source_sha256']==row['expected_source_sha256'],'ARTIFACT_REFUSED')
        else: _require(row['source_size_bytes'] is None,'ARTIFACT_REFUSED')
        _require(row['reconciliation_state'] in ('not_requested','unavailable','review_required','reconciled','refused'),'ARTIFACT_REFUSED')
        _require((row['requested_stage']=='observe')==(row['reconciliation_state']=='not_requested'),'ARTIFACT_REFUSED')
        _require(row['state'] in ('excluded','declared_duplicate','refused','parsed_with_blockers','observed_unvalidated','reconciled'),'ARTIFACT_REFUSED')
        if row['selection']!='run':
            _require(row['state']==row['selection'] and not row['parser_started'] and not row['v2_valid'] and
                     row['observed_source_sha256'] is None and not row['blockers'],'ARTIFACT_REFUSED')
        else: _require(row['state'] not in ('excluded','declared_duplicate'),'ARTIFACT_REFUSED')
        for key in ('blockers','warnings'):
            _require(type(row[key]) is list,'ARTIFACT_REFUSED')
            for diagnostic in row[key]:
                _shape(diagnostic,('stage','namespace','code'))
                _require(diagnostic['stage'] in ('selection','source','parser','migration','citation','privacy','reconciliation','limits'),'ARTIFACT_REFUSED')
                sink={'blockers':[],'warnings':[]}
                _diag(sink,diagnostic['code'],diagnostic['stage'],diagnostic['namespace'],key=='warnings')
        _shape(row['counts'],c.SCOPES)
        for counts in row['counts'].values():
            _shape(counts,c.COUNT_FIELDS)
            for value in counts.values():
                if value is not None: _int(value,1000000)
        if row['state']=='reconciled':
            _require(row['reconciliation_state']=='reconciled' and row['counts_basis']=='reconciled_overlay' and
                row['entity_kind']=='property' and row['corpus_use']=='inventory' and row['subject_id'] is not None and
                row['as_of'] is not None and not row['blockers'] and
                all(v is not None for counts in row['counts'].values() for v in counts.values()),'ARTIFACT_REFUSED')
        else: _require(row['counts']==_null_counts() and row['counts_basis']=='none','ARTIFACT_REFUSED')
        if row['state'] in ('observed_unvalidated','reconciled','parsed_with_blockers'):
            _require(row['v2_valid'],'ARTIFACT_REFUSED')
        if row['state']=='observed_unvalidated':
            _require(row['requested_stage']=='observe' and not row['blockers'],'ARTIFACT_REFUSED')
        packet={}
        for kind,field in _KINDS.items():
            key=(row['entry_id'],kind)
            if row[field] is not None:
                _hash(row[field]); expected_slots.add(key)
                _require(key in slots and slots[key]['sha256']==row[field],'ARTIFACT_REFUSED')
                packet[kind]=blobs[key]
            else: _require(key not in slots,'ARTIFACT_REFUSED')
        _require(row['v2_valid']==all(k in packet for k in ('observations','preservation','citations')),'ARTIFACT_REFUSED')
        if row['v2_valid']:
            _require(row['parser_started'] and row['observed_source_sha256'] is not None,'ARTIFACT_REFUSED')
            env=c.validate_observations(packet['observations'],subject_id=row['subject_id'],as_of=row['as_of'])
            _require(env['adapter']==row['adapter'] and env['sources']==[{'source_id':row['source_id'],
                'sha256':row['expected_source_sha256'],'role':'original','original_source_ids':[],
                'subject_id':row['subject_id'],'as_of':row['as_of']}],'ARTIFACT_REFUSED')
            checker=packet['citations']['checker']['code_sha256']
            _hash(checker)
            _validate_ledgers(packet,e,checker)
        else: _require(not packet,'ARTIFACT_REFUSED')
        _require(('resolution' in packet)==(row['reconciliation_state'] in ('reconciled','review_required')),'ARTIFACT_REFUSED')
        if 'resolution' in packet:
            overlay=packet['resolution']
            _resolution_shape(overlay,packet['observations'])
            _require(row['reconciliation_state']==overlay['state'],'ARTIFACT_REFUSED')
            if row['state']=='reconciled': _require(row['counts']==overlay['counts'],'ARTIFACT_REFUSED')
    _manifest(pseudo,limits)
    _require(set(slots)==expected_slots,'ARTIFACT_REFUSED')
    _shape(matrix['totals'],_totals(matrix['entries']))
    for value in matrix['totals'].values(): _int(value,1000000)
    _require(matrix['totals']==_totals(matrix['entries']) and matrix['assessment']==_assessment(matrix['totals']) and
        matrix['item2_milestone']==matrix['engine_eligibility']=='not_evaluated','ARTIFACT_REFUSED')


_RESOLUTION_CODES=frozenset('BASE_FACT_UNVERIFIED DECISION_CONFLICT FIELD_EVIDENCE_REQUIRED ORIGINAL_BLOCKER SUMMARY_VERIFICATION_REQUIRED UNEXPLAINED_ROOT_BLOCKER MULTISOURCE_COVERAGE_UNSUPPORTED UNSUPPORTED_FORMAT UNSUPPORTED_LAYOUT SOURCE_LIMIT UNIT_IDENTITY_REQUIRED AMBIGUOUS_UNIT_IDENTITY SCOPE_REQUIRED ADAPTER_UNSUPPORTED COVERAGE_REQUIRED INVENTORY_MEMBERSHIP_MISMATCH DOWN_DEFINITION_REQUIRED POSITION_MEANING_MISMATCH SOURCE_SCOPE_MISMATCH TARGET_UNIT_MISMATCH SOURCE_CONTRADICTION SOURCE_VALUE_UNSUPPORTED SEMANTIC_RULE_REQUIRED SOURCE_VALUE_MISMATCH'.split())


def _resolution_shape(o, env):
    c._tree(o)
    _shape(o,('contract_version','envelope_sha256','observations','context_sha256','semantics','state','coverage',
        'history','head_sha256','resolutions','evidence_requests','dispositions','summary_dispositions','counts'))
    _require(o['contract_version']=='ingest-resolution/1.0.0' and o['observations']==env and
        o['envelope_sha256']==_sha(_encode(env)) and o['state'] in ('review_required','reconciled'),'ARTIFACT_REFUSED')
    _hash(o['context_sha256'])
    _shape(o['semantics'],('id','version','sha256'))
    _require(o['semantics']['id']=='bounded-csv-physical' and o['semantics']['version']=='1.0.1','ARTIFACT_REFUSED')
    _hash(o['semantics']['sha256'])
    units={u['observation_id']:u for u in env['units']+env['unknown_use_units']}
    def proof(p):
        _require(type(p) is dict,'ARTIFACT_REFUSED')
        if p.get('state')=='evidence_required':
            _shape(p,('state','code')); _require(p['code'] in _RESOLUTION_CODES,'ARTIFACT_REFUSED'); return
        _require(p.get('state')=='verified' and p.get('context_sha256')==o['context_sha256'] and
                 p.get('verifier')==o['semantics'],'ARTIFACT_REFUSED')
        if 'anchors_sha256' in p:
            _shape(p,('state','anchors_sha256','citations','context_sha256','verifier'))
            _hash(p['anchors_sha256'])
            for cite in p['citations']: c._citation(cite)
        elif 'claim_sha256' in p:
            _shape(p,('state','claim_sha256','citation','value_sha256','context_sha256','verifier'))
            _hash(p['claim_sha256']); _hash(p['value_sha256']); c._citation(p['citation'])
        else:
            _shape(p,('state','target_sha256','citations_sha256','context_sha256','verifier'))
            _hash(p['target_sha256']); _hash(p['citations_sha256'])
    proof(o['coverage'])
    for request in o['evidence_requests']:
        _shape(request,('code','observation_id','field','reference_sha256'))
        _require(request['code'] in _RESOLUTION_CODES and request['observation_id'] in (None,*units) and
                 request['field'] in (None,'unit_type','status'),'ARTIFACT_REFUSED')
        if request['reference_sha256'] is not None: _hash(request['reference_sha256'])
    _require((o['state']=='reconciled')==(not o['evidence_requests']),'ARTIFACT_REFUSED')
    ids=[]
    for r in o['resolutions']:
        _shape(r,('observation_id','unit_type','status','decision_sha256s','proofs'))
        _require(r['observation_id'] in units and r['unit_type'] in (None,*c.SCOPES) and
                 r['status'] in (None,'occupied','vacant','down'),'ARTIFACT_REFUSED')
        ids.append(r['observation_id'])
        for pin in r['decision_sha256s']: _hash(pin)
        for p in r['proofs']: proof(p)
    _require(len(ids)==len(set(ids)) and set(ids)==set(units),'ARTIFACT_REFUSED')
    for key,source,idkey in (('dispositions',env['issues'],'issue_id'),('summary_dispositions',env['summaries'],'summary_id')):
        _require(len(o[key])==len(source),'ARTIFACT_REFUSED')
        for d,item in zip(o[key],source):
            _shape(d,(idkey,'state'))
            _require(d[idkey]==item[idkey] and d['state'] in ('resolved','retained'),'ARTIFACT_REFUSED')
    for h in o['history']:
        _shape(h,('decision_id','decision_sha256','approval_sha256','payload','state'))
        _id(h['decision_id'],'dec'); _hash(h['decision_sha256']); _hash(h['approval_sha256'])
        _require(h['state'] in ('active','superseded'),'ARTIFACT_REFUSED')
        p=h['payload']
        _shape(p,('contract_version','decision_id','envelope_sha256','subject_id','as_of','adapter','sources',
            'supplemental_sources','predecessor_sha256','supersedes','semantics','dimension','changes'))
        _require(p['contract_version']=='ingest-mapping/1.0.0' and p['decision_id']==h['decision_id'] and
            p['envelope_sha256']==o['envelope_sha256'] and p['semantics']==o['semantics'] and
            p['dimension']=='physical_unit' and p['supplemental_sources']==[] and
            all(p[k]==env[k] for k in ('subject_id','as_of','adapter','sources')),'ARTIFACT_REFUSED')
        if p['predecessor_sha256'] is not None: _hash(p['predecessor_sha256'])
        for pin in p['supersedes']: _hash(pin)
        for change in p['changes']:
            _shape(change,('observation_id','field','before','after','rule_id','citation','value_sha256'))
            _require(change['observation_id'] in units and change['field'] in ('unit_type','status'),'ARTIFACT_REFUSED')
            field=change['field']; choices=c.SCOPES if field=='unit_type' else ('occupied','vacant','down')
            _require(change['before']==units[change['observation_id']][field] and change['after'] in choices and
                     change['rule_id']==('use-token/1' if field=='unit_type' else 'status-token/1'),'ARTIFACT_REFUSED')
            c._citation(change['citation']); _hash(change['value_sha256'])
    _require(o['head_sha256']==(o['history'][-1]['decision_sha256'] if o['history'] else None),'ARTIFACT_REFUSED')
    _shape(o['counts'],c.SCOPES)
    for counts in o['counts'].values():
        _shape(counts,c.COUNT_FIELDS)
        for value in counts.values():
            if o['state']=='reconciled': _int(value,1000000)
            else: _require(value is None,'ARTIFACT_REFUSED')


class AcceptanceRunner:
    """Host-authorized capabilities only. Inputs must not originate in a request."""

    @_boundary('INVALID_MANIFEST')
    def __init__(self, *, manifest_bytes, expected_manifest_sha256, source_paths,
                 code_paths, expected_code_sha256s, decision_chains, output_root, limits):
        _require(type(limits) is AcceptanceLimits, 'INVALID_HOST_INPUT')
        for key, ceiling in asdict(AcceptanceLimits()).items():
            value = getattr(limits, key)
            _require(type(value) is int and 0 < value <= ceiling, 'INVALID_HOST_INPUT')
        self._limits = limits
        _require(type(manifest_bytes) is bytes and len(manifest_bytes) <= limits.max_manifest_bytes)
        _hash(expected_manifest_sha256)
        _require(_sha(manifest_bytes) == expected_manifest_sha256, 'MANIFEST_HASH_MISMATCH')
        self._manifest = _manifest(_decode(manifest_bytes, limits.max_manifest_bytes), limits)
        self._manifest_hash = expected_manifest_sha256
        _runtime(self._manifest['runtime'])
        selected = {e['entry_id'] for e in self._manifest['entries'] if e['selection'] == 'run'}
        reconcile = {e['entry_id'] for e in self._manifest['entries'] if e['requested_stage'] == 'reconcile'}
        _require(type(source_paths) is dict and set(source_paths) == selected, 'INVALID_HOST_INPUT')
        _path(output_root)
        self._output = output_root
        self._sources = {}
        for key, value in source_paths.items():
            _require(type(value) is SourcePath, 'INVALID_HOST_INPUT')
            _path(value.root)
            _require(type(value.relative_parts) is tuple and 0 < len(value.relative_parts) <= 128, 'INVALID_HOST_INPUT')
            _require(all(type(p) is str and p not in ('', '.', '..') and '/' not in p and '\x00' not in p
                         and len(p) <= 255 for p in value.relative_parts), 'INVALID_HOST_INPUT')
            _require(not (output_root == value.root or output_root.startswith(value.root + '/')
                          or value.root.startswith(output_root + '/')), 'INVALID_HOST_INPUT')
            self._sources[key] = value
        _require(type(decision_chains) is dict and set(decision_chains) == reconcile, 'INVALID_HOST_INPUT')
        c._tree(decision_chains)
        self._chains = deepcopy(decision_chains)
        for e in self._manifest['entries']:
            if e['requested_stage'] == 'reconcile':
                chain = self._chains[e['entry_id']]
                _require(type(chain) is list and len(chain) <= 256, 'INVALID_HOST_INPUT')
                _require(_sha(_encode(chain)) == e['decisions_sha256'], 'INVALID_HOST_INPUT')
        modules = set(_BASE)
        if any(e['adapter']['id'] == 'onesite-detailed-realpage' for e in self._manifest['entries']):
            from . import onesite
            modules.add('plat_harness.ingest.onesite')
        if reconcile:
            from . import reconciliation, source_resolver
            from plat_harness.adapters import review_bridge
            modules.update(_AUTH)
        _require(type(code_paths) is dict and type(expected_code_sha256s) is dict and
                 set(code_paths) == modules == set(expected_code_sha256s), 'CODE_PIN_MISMATCH')
        self._code = dict(code_paths)
        self._pins = dict(expected_code_sha256s)
        self._check_code()
        _require(_sha(_encode(self._pins)) == self._manifest['code_manifest_sha256'], 'CODE_PIN_MISMATCH')

    def _check_code(self):
        for name, path in self._code.items():
            module = sys.modules.get(name)
            _require(module is not None and type(path) is str and
                     module.__file__ == path and module.__spec__.origin == path, 'CODE_PIN_MISMATCH')
            _path(path)
            pin = self._pins[name]
            _require(type(pin) is str and re.fullmatch('[0-9a-f]{64}', pin) is not None, 'CODE_PIN_MISMATCH')
            fd = os.open(path, _FILE)
            try:
                before = os.fstat(fd)
                _require(stat.S_ISREG(before.st_mode) and before.st_size <= 1024 * 1024, 'CODE_PIN_MISMATCH')
                raw = bytearray()
                while len(raw) <= 1024 * 1024:
                    chunk = os.read(fd, min(65536, 1024 * 1024 + 1 - len(raw)))
                    if not chunk: break
                    raw.extend(chunk)
                after = os.fstat(fd)
                live = os.stat(path, follow_symlinks=False)
                _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) ==
                         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_ctime_ns) and
                         (live.st_dev, live.st_ino) == (after.st_dev, after.st_ino) and
                         len(raw) == before.st_size and _sha(raw) == pin, 'CODE_PIN_MISMATCH')
            finally:
                os.close(fd)

    def _matrix(self, run_id, rows, artifacts):
        m = self._manifest
        identity = {k: v for k, v in m.items() if k not in ('predecessor', 'change_kind', 'corpus_revision')}
        totals = _totals(rows)
        return {'contract_version': 'ingest-acceptance-matrix/1.0.0', 'run_id': run_id,
            'manifest_sha256': self._manifest_hash,
            **{k: deepcopy(m[k]) for k in ('corpus_id', 'corpus_revision', 'corpus_basis_sha256',
                'predecessor', 'change_kind', 'code_manifest_sha256', 'runtime')},
            'input_identity_sha256': _sha(_encode({'manifest': identity, 'limits': asdict(self._limits)})),
            'limits': asdict(self._limits), 'assessment': _assessment(totals),
            'item2_milestone': 'not_evaluated', 'engine_eligibility': 'not_evaluated',
            'entries': rows, 'totals': totals, 'artifacts': artifacts}

    def _read(self, fd, run_id, expected):
        _shape(expected, ('run_id', 'matrix_sha256', 'manifest_sha256', 'code_manifest_sha256'))
        _id(run_id, 'acc')
        _require(expected['run_id'] == run_id, 'ARTIFACT_REFUSED')
        for k in ('matrix_sha256', 'manifest_sha256', 'code_manifest_sha256'): _hash(expected[k])
        raw = _read_file(fd, 'matrix.json', self._limits.max_matrix_bytes)
        _require(_sha(raw) == expected['matrix_sha256'], 'ARTIFACT_REFUSED')
        matrix = _decode(raw, self._limits.max_matrix_bytes)
        _require(_encode(matrix) == raw and _pin(matrix, raw) == expected, 'ARTIFACT_REFUSED')
        _require(matrix['contract_version'] == 'ingest-acceptance-matrix/1.0.0' and
                 matrix['totals'] == _totals(matrix['entries']) and
                 matrix['assessment'] == _assessment(matrix['totals']) and
                 matrix['engine_eligibility'] == matrix['item2_milestone'] == 'not_evaluated', 'ARTIFACT_REFUSED')
        names = {'matrix.json'}
        blobs = {}
        total = len(raw)
        for artifact in matrix['artifacts']:
            _shape(artifact, ('entry_id', 'kind', 'sha256', 'size_bytes'))
            _id(artifact['entry_id'], 'entry')
            _require(artifact['kind'] in ('observations', 'preservation', 'citations', 'resolution'), 'ARTIFACT_REFUSED')
            _hash(artifact['sha256']); _int(artifact['size_bytes'], self._limits.max_artifact_bytes, 1)
            name = artifact['entry_id'] + '.' + artifact['kind'] + '.json'
            _require(name not in names, 'ARTIFACT_REFUSED')
            names.add(name)
            data = _read_file(fd, name, min(artifact['size_bytes'], self._limits.max_artifact_bytes))
            total += len(data)
            _require(len(data) == artifact['size_bytes'] and _sha(data) == artifact['sha256'] and
                     total <= self._limits.max_artifact_bytes, 'ARTIFACT_REFUSED')
            value = _decode(data, self._limits.max_artifact_bytes)
            _require(_encode(value) == data, 'ARTIFACT_REFUSED')
            blobs[(artifact['entry_id'],artifact['kind'])] = value
        _snapshot(matrix, blobs)
        _require(set(os.listdir(fd)) == names, 'ARTIFACT_REFUSED')
        os.fsync(fd)
        return {'pin': deepcopy(expected), 'matrix': matrix}

    @_boundary('ARTIFACT_REFUSED')
    def read(self, run_id, *, expected):
        """Exact-pin historical byte verification, NEVER current authorization."""
        _id(run_id, 'acc')
        with _root(self._output) as (root, stable):
            fd = os.open(run_id, _DIR, dir_fd=root)
            try:
                _attached(root, run_id, fd, directory=True, private=True)
                result = self._read(fd, run_id, expected)
                _attached(root, run_id, fd, directory=True, private=True)
                os.fsync(root)
            finally:
                os.close(fd)
        return result


    def _reconcile(self, e, raw, env):
        from .reconciliation import reconcile_request
        from .source_resolver import ByteSourceResolver
        chain = self._chains[e['entry_id']]
        _require(_sha(_encode(chain)) == e['decisions_sha256'], 'ARTIFACT_REFUSED')
        if any(type(d) is not dict or d.get('supplemental_sources') != [] for d in chain):
            return 'RECONCILIATION_REFUSED', None
        if len(raw) > 1024 * 1024:
            return 'RECONCILIATION_SOURCE_LIMIT', None
        if _container(raw) != 'csv':
            return 'RECONCILIATION_UNSUPPORTED', None
        try:
            resolver = ByteSourceResolver(originals={e['source_id']:raw}, sources=env['sources'],
                expected_envelope_sha256=_sha(_encode(env)), subject_id=e['subject_id'], as_of=e['as_of'],
                adapter=e['adapter'], intake_provenance={e['source_id']:{'sha256':e['source_sha256'],
                    'role':'original','intake_id':e['intake_id']}})
            overlay = reconcile_request({'observations':env,'decisions':chain}, source_resolver=resolver)
            c._tree(overlay)
            _require(len(_encode(overlay)) <= c.MAX_BYTES and overlay['observations'] == env, 'ARTIFACT_REFUSED')
            return None, overlay
        except Exception:
            pass
        return 'RECONCILIATION_REFUSED', None

    def _current(self, rows, payloads, deadline):
        self._check_code()
        _runtime(self._manifest['runtime'])
        fresh = []
        for e in self._manifest['entries']:
            if e['entry_id'] not in self._relied: continue
            code, data = _source(self._sources[e['entry_id']], e, self._limits.max_source_bytes, deadline)
            _require(code is None and data is not None and data[1] == self._relied[e['entry_id']], 'ARTIFACT_REFUSED')
            started, error, result = _worker(data[0], e, self._pins[__name__], self._limits, deadline)
            _require(error is None and started, 'ARTIFACT_REFUSED')
            for kind, value in result.items():
                _require(_encode(value) == payloads[e['entry_id'] + '.' + kind + '.json'], 'ARTIFACT_REFUSED')
            fresh.append((e, data[0], result['observations']))
        self._check_code()
        # Full newest chains are the LAST authority checks, after source replay,
        # all code/end guards and (at the final checkpoint) staging cleanup.
        for e, raw, env in fresh:
            name = e['entry_id'] + '.resolution.json'
            if name in payloads:
                error, overlay = self._reconcile(e, raw, env)
                _require(error is None and _encode(overlay) == payloads[name], 'ARTIFACT_REFUSED')
        _require(time.monotonic() < deadline, 'ARTIFACT_REFUSED')


    def _predecessor(self, run_id):
        prior = self._manifest['predecessor']
        if prior is None: return
        _require(prior['run_id'] != run_id, 'RUN_COLLISION')
        with _root(self._output) as (root, stable):
            fd = os.open(prior['run_id'], _DIR, dir_fd=root)
            try:
                _attached(root, prior['run_id'], fd, directory=True, private=True)
                raw = _read_file(fd, 'matrix.json', self._limits.max_matrix_bytes)
                _require(_sha(raw) == prior['matrix_sha256'], 'ARTIFACT_REFUSED')
                old = _decode(raw, self._limits.max_matrix_bytes)
                expected = {**prior, 'code_manifest_sha256':old['code_manifest_sha256']}
                old = self._read(fd, prior['run_id'], expected)['matrix']
                _attached(root, prior['run_id'], fd, directory=True, private=True)
            finally: os.close(fd)
        m = self._manifest
        _require(old['corpus_id'] == m['corpus_id'], 'INVALID_MANIFEST')
        new_identity = self._matrix(run_id, [], [])['input_identity_sha256']
        if m['change_kind'] == 'repeat_assessment':
            _require(old['input_identity_sha256'] == new_identity and
                     m['corpus_revision'] >= old['corpus_revision'], 'INVALID_MANIFEST')
            return
        _require(m['corpus_revision'] > old['corpus_revision'], 'INVALID_MANIFEST')
        before = {r['entry_id']:r for r in old['entries']}
        after = {e['entry_id']:e for e in m['entries']}
        common = before.keys() & after.keys()
        corrected = False
        for eid in common:
            if before[eid]['expected_source_sha256'] != after[eid]['source_sha256']:
                corrected = True
                _require(before[eid]['snapshot_id'] != after[eid]['snapshot_id'], 'INVALID_MANIFEST')
        selection_keys = ('selection','exclusion_code','duplicate_of','subject_id','as_of','entity_kind','corpus_use')
        changed_selection = (before.keys() != after.keys() or m['corpus_basis_sha256'] != old['corpus_basis_sha256'] or
            any(before[eid][k] != after[eid][k] for eid in common for k in selection_keys))
        changed_adapter = (m['code_manifest_sha256'] != old['code_manifest_sha256'] or
            any(before[eid]['adapter'] != after[eid]['adapter'] for eid in common))
        changed_review = any(before[eid]['decisions_sha256'] != after[eid]['decisions_sha256'] or
                             before[eid]['requested_stage'] != after[eid]['requested_stage'] for eid in common)
        _require({'source_correction':corrected,'selection_change':changed_selection,
                  'adapter_change':changed_adapter,'review_change':changed_review}[m['change_kind']], 'INVALID_MANIFEST')

    def _selection(self, rows):
        selected = [e for e in self._manifest['entries'] if e['selection'] == 'run']
        duplicate, ambiguous = set(), set()
        for index, e in enumerate(selected):
            for other in selected[index+1:]:
                if e['source_sha256'] == other['source_sha256'] or self._sources[e['entry_id']] == self._sources[other['entry_id']]:
                    duplicate.update((e['entry_id'],other['entry_id']))
                elif (e['subject_id'] is not None and e['as_of'] is not None and
                     (e['subject_id'],e['as_of']) == (other['subject_id'],other['as_of'])):
                    ambiguous.update((e['entry_id'],other['entry_id']))
        for row in rows:
            if row['entry_id'] in duplicate: _diag(row,'DUPLICATE_SOURCE','selection')
            if row['entry_id'] in ambiguous: _diag(row,'AMBIGUOUS_SNAPSHOT','selection')
        return duplicate


    def _output_safe(self):
        try:
            with _root(self._output): pass
            return
        except Exception:
            pass
        raise _Failure('UNSAFE_OUTPUT_ROOT')

    def run(self, run_id):
        """Create once and return only after durable readback and final guards."""
        published = False
        failure_code = 'ARTIFACT_REFUSED'
        try:
            _id(run_id, 'acc')
            _require(sys.platform == 'linux' and threading.active_count() == 1 and
                     len(os.listdir('/proc/self/task')) == 1, 'INVALID_HOST_INPUT')
            deadline = time.monotonic() + self._limits.run_wall_seconds
            self._check_code()
            _runtime(self._manifest['runtime'])
            self._output_safe()
            with _root(self._output) as (root, stable): _absent(root, run_id)
            self._predecessor(run_id)
            rows = [_row(e) for e in self._manifest['entries']]
            duplicate = self._selection(rows)
            payloads = {}
            artifacts = []
            self._relied = {}
            cumulative = 0
            acquired = {}
            opened_inodes = {}
            for e, row in zip(self._manifest['entries'], rows):
                if e['selection'] != 'run' or e['entry_id'] in duplicate: continue
                if time.monotonic() >= deadline:
                    _diag(row, 'RUN_LIMIT', 'limits'); continue
                if e['source_role'] != 'original' or e['intake_id'] is None:
                    _diag(row, 'ORIGINAL_REQUIRED', 'selection'); continue
                adapter = e['adapter']
                if adapter['id'] not in compat.ADAPTERS or adapter['version'] != '1.0.0':
                    _diag(row, 'ADAPTER_UNSUPPORTED', 'selection'); continue
                if cumulative + e['source_size_bytes'] > self._limits.max_total_source_bytes:
                    _diag(row, 'SOURCE_LIMIT', 'limits'); continue
                code, data = _source(self._sources[e['entry_id']], e, self._limits.max_source_bytes, deadline)
                if data is not None and data[1] is not None:
                    opened_inodes[e['entry_id']] = data[1][:2]
                if code:
                    _diag(row, code); continue
                _require(data is not None and data[0] is not None, 'SOURCE_MISSING')
                raw, stamp = data
                cumulative += len(raw)
                row.update(observed_source_sha256=_sha(raw), source_size_bytes=len(raw), detected_container=_container(raw))
                container = row['detected_container']
                valid_containers = ('xls_biff',) if adapter['id'] == 'onesite-detailed-realpage' else ('csv','xlsx')
                if container != e['expected_container'] or container not in valid_containers:
                    _diag(row, 'CONTAINER_MISMATCH', 'selection'); continue
                acquired[e['entry_id']] = (raw, stamp)
            # Complete acquisition before dispatch, so opened inode aliases never first-win.
            inode_groups = {}
            for eid, ino in opened_inodes.items(): inode_groups.setdefault(ino, []).append(eid)
            aliased = {eid for group in inode_groups.values() if len(group)>1 for eid in group}
            for e, row in zip(self._manifest['entries'], rows):
                if e['entry_id'] in aliased:
                    _diag(row, 'DUPLICATE_SOURCE', 'selection'); continue
                if e['entry_id'] not in acquired: continue
                if time.monotonic() >= deadline:
                    _diag(row, 'RUN_LIMIT', 'limits'); continue
                raw, stamp = acquired[e['entry_id']]
                started, error, result = _worker(raw, e, self._pins[__name__], self._limits, deadline)
                row['parser_started'] = started
                if error:
                    _diag(row, error['code'], 'parser', error['namespace']); continue
                self._relied[e['entry_id']] = stamp
                env = result['observations']
                row.update(v2_valid=True, state='observed_unvalidated')
                for issue in env['issues']:
                    _diag(row, issue['code'], 'parser', 'observation', issue['severity']=='warning')
                if not env['units'] and not env['unknown_use_units']: _diag(row,'EMPTY_PARSE','parser')
                if e['entity_kind'] == 'property' and (e['subject_id'] is None or e['as_of'] is None):
                    _diag(row, 'SCOPE_REQUIRED','selection')
                totals = result['citations']['totals']
                for key, diagnostic in (('missing','CITATION_MISSING'),('unsupported','CITATION_UNSUPPORTED'),
                                        ('empty_or_implicit','CITATION_EMPTY_OR_IMPLICIT')):
                    if totals[key]: _diag(row,diagnostic,'citation')
                if row['blockers'] or env['status'] == 'blocked': row['state'] = 'parsed_with_blockers'
                if totals['missing']: row['state'] = 'refused'
                if e['requested_stage'] == 'reconcile':
                    error, overlay = self._reconcile(e, raw, env)
                    if error:
                        _diag(row, error, 'reconciliation')
                        if error == 'RECONCILIATION_REFUSED':
                            row.update(state='refused', reconciliation_state='refused')
                        else:
                            row.update(state='parsed_with_blockers', reconciliation_state='unavailable')
                    else:
                        result['resolution'] = overlay
                        row['reconciliation_state'] = overlay['state']
                        if overlay['state'] == 'reconciled' and not row['blockers']:
                            row.update(state='reconciled', counts=deepcopy(overlay['counts']), counts_basis='reconciled_overlay')
                        else:
                            if row['state'] != 'refused': row['state'] = 'parsed_with_blockers'
                            for request in overlay['evidence_requests']:
                                _diag(row, request['code'], 'reconciliation', 'resolution')
                for kind, value in result.items():
                    encoded = _encode(value)
                    row[_KINDS[kind]] = _sha(encoded)
                    payloads[e['entry_id'] + '.' + kind + '.json'] = encoded
                    artifacts.append({'entry_id':e['entry_id'],'kind':kind,'sha256':_sha(encoded),'size_bytes':len(encoded)})
                _require(sum(map(len, payloads.values())) <= self._limits.max_artifact_bytes, 'ARTIFACT_REFUSED')
            matrix = self._matrix(run_id, rows, artifacts)
            _tree(matrix, self._limits.max_matrix_bytes)
            raw = _encode(matrix)
            pin = _pin(matrix, raw)
            payloads['matrix.json'] = raw
            _require(sum(map(len,payloads.values())) <= self._limits.max_artifact_bytes, 'ARTIFACT_REFUSED')
            with _root(self._output) as (root, stable):
                _absent(root, run_id)
                with _stage(root, payloads) as (stage, fd):
                    for name, data in payloads.items(): _write_file(fd, name, data)
                    os.fsync(fd)
                    self._read(fd, run_id, pin)
                    stable()
                    self._current(rows, payloads, deadline)
                    published = True  # A failing publication syscall boundary may already be visible.
                    try:
                        _publish(root, stage, run_id)
                    except _Failure as exc:
                        if exc.args[0] == 'RUN_COLLISION': published = False
                        raise
                    os.fsync(root)
                fd = os.open(run_id, _DIR, dir_fd=root)
                try:
                    _attached(root, run_id, fd, directory=True, private=True)
                    result = self._read(fd, run_id, pin)
                    _attached(root, run_id, fd, directory=True, private=True)
                    os.fsync(root)
                finally: os.close(fd)
            self._current(rows, payloads, deadline)
            return result
        except _Failure as exc:
            if exc.args[0] in _ERRORS: failure_code = exc.args[0]
        except KeyboardInterrupt:
            failure_code = 'CANCELLED'
        except Exception:
            pass
        raise AcceptanceError('WRITE_UNCERTAIN' if published else failure_code) from None
