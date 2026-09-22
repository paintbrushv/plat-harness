"""Read-only OneSite detailed BIFF observations; never occupancy approval.

This adapter deliberately does not infer residential/commercial use from a
floorplan or designation, or Down from the vendor's combined Admin/Down label.
Unscoped observations live in issues[].observation, preserving the shared root
schema without falsely placing them in residential_units/commercial_units.
"""
from __future__ import annotations

import hashlib
import math
import re
import warnings

from .pms_normalizer import RentRollNormalizationError

__all__ = ['normalize_onesite_xls']
_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
_COUNTS = ('occupied', 'vacant', 'down', 'total')
_SCOPES = ('residential', 'commercial')
_HEADERS = {0: 'unit', 2: 'floorplan', 7: 'unit designation',
            17: 'unit lease status', 19: 'name', 35: 'trans code', 50: 'total billing'}
_STATUSES = {'occupied': 'occupied', 'occupied ntv': 'occupied',
             'occupied ntvl': 'occupied', 'vacant': 'vacant', 'vacant leased': 'vacant'}
_NON_CURRENT = {'applicant', 'pending renewal', 'pending', 'former', 'future',
                'former resident', 'future resident'}
_EXCLUDED_SECTIONS = {'former residents', 'future residents', 'applicants',
                      'pending renewals'}
_SUMMARY_LABELS = ('occupied no ntv', 'occupied ntv', 'occupied ntv leased',
                   'vacant leased', 'admin down', 'vacant not leased', 'totals')


class _Failure(Exception):
    pass


class _DiscardLog:
    """xlrd can write input-bearing diagnostics even when verbosity is zero."""
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def _key(value):
    if not isinstance(value, str):
        return '' if value is None else '\x00'
    return re.sub(r'[\s/,:\-]+', ' ', value.strip().lower()).strip()


def _parse(book, raw):
    if book.nsheets != 1:
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')
    sheet = book.sheet_by_index(0)
    if sheet.nrows > 50_000 or sheet.ncols > 128 or sheet.nrows * sheet.ncols > 250_000:
        raise _Failure('INPUT_LIMIT_EXCEEDED')
    if sheet.nrows < 13 or sheet.ncols < 51:
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')
    cell = sheet.cell_value
    key = lambda r, c: _key(cell(r, c))
    header = lambda r: all(key(r, c) == text for c, text in _HEADERS.items())
    if (not re.search(r'\bonesite\b', key(1, 0))
            or key(2, 12) != 'rent roll detail' or not header(8)):
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')
    digest = hashlib.sha256(raw).hexdigest()
    result = {'version': '1.0', 'pms_type': 'realpage', 'source_sha256': digest,
              'residential_units': [], 'commercial_units': [],
              'counts': {s: dict.fromkeys(_COUNTS) for s in _SCOPES},
              'summary': {s: {'status': 'absent', 'reported_counts': None, 'citations': []}
                          for s in _SCOPES},
              'issues': [], 'status': 'blocked'}

    def cite(r, c):
        return {'source_sha256': digest, 'sheet': 1, 'row': r + 1,
                'row_end': r + 1, 'column': c + 1}

    def issue(code, r=None, c=0, *, warning=False, observation=None):
        entry = {'code': code, 'severity': 'warning' if warning else 'blocker',
                 'citation': cite(r, c) if r is not None else None}
        if observation is not None:
            entry['observation'] = observation
        result['issues'].append(entry)

    units = {}
    conflicts = set()
    current = None
    non_current_lease = False
    inventory_valid = True
    excluded = False
    end = None
    for r in range(9, sheet.nrows):
        if header(r):
            continue
        first, status_key = key(r, 0), key(r, 17)
        if first == 'totals':
            end = r
            break
        if first in _EXCLUDED_SECTIONS:
            excluded = True
            current = None
            issue('NON_CURRENT_SECTION_EXCLUDED', r, warning=True)
            continue
        if first in {'details', 'current residents', 'current units'}:
            excluded = False
            continue
        if excluded:
            continue
        if status_key in _NON_CURRENT:
            non_current_lease = True
            issue('NON_CURRENT_LEASE_EXCLUDED', r, 17, warning=True)
            continue
        if cell(r, 0):
            non_current_lease = False
            # Raw identifiers are used only for within-report deduplication.
            # Nothing from this key is returned, hashed separately, or logged.
            identifier = (type(cell(r, 0)).__name__, cell(r, 0))
            status = _STATUSES.get(status_key)
            evidence = {'unit_id': cite(r, 0), 'status': cite(r, 17),
                        'designation': cite(r, 7), 'floorplan': cite(r, 2)}
            if not cell(r, 2):
                inventory_valid = False
                issue('INCOMPLETE_UNIT_ROW', r, 2)
            if status is None:
                inventory_valid = False
                issue('UNSUPPORTED_UNIT_STATUS', r, 17)
            if identifier in conflicts:
                issue('DUPLICATE_UNIT_CONFLICT', r)
                current = None
                continue
            existing = units.get(identifier)
            if existing:
                if existing['status'] != status:
                    inventory_valid = False
                    conflicts.add(identifier)
                    existing['status'] = None
                    issue('DUPLICATE_UNIT_CONFLICT', r)
                else:
                    issue('DUPLICATE_UNIT_EVIDENCE', r, warning=True)
                existing['evidence'].append(evidence)
                current = existing
            else:
                current = {'unit_id': '[REDACTED]', 'status': status, 'unit_type': None,
                           'tenant_name': '[REDACTED]', 'evidence': [evidence]}
                units[identifier] = current
            continue
        if status_key:
            # A second occupied/status row without a physical-unit identifier
            # is not silently attached or promoted into another physical unit.
            inventory_valid = False
            issue('UNSUPPORTED_SECONDARY_STATUS', r, 17)
            continue
        if cell(r, 19) or cell(r, 35):
            if non_current_lease:
                issue('NON_CURRENT_CONTINUATION_EXCLUDED', r, 35, warning=True)
                continue
            if current is None:
                inventory_valid = False
                issue('ORPHAN_CONTINUATION', r, 35)
            else:
                evidence = {}
                if cell(r, 19):
                    evidence['tenant_name'] = cite(r, 19)
                if cell(r, 35):
                    evidence['charge'] = cite(r, 35)
                current['evidence'].append(evidence)
                issue('CONTINUATION_EVIDENCE', r, 35, warning=True)
    if end is None:
        inventory_valid = False
        issue('INVENTORY_BOUNDARY_MISSING', 8)
    if not units:
        inventory_valid = False
        issue('NO_CURRENT_UNITS', 8)
    for unit in units.values():
        issue('UNRESOLVED_UNIT_USE', unit['evidence'][0]['unit_id']['row'] - 1,
              7, observation=unit)
    # No current evidence establishes an independently defined Down category.
    issue('DOWN_EVIDENCE_UNRESOLVED', 8, 17)
    derived = dict.fromkeys(_COUNTS)
    if inventory_valid:
        derived.update(occupied=sum(u['status'] == 'occupied' for u in units.values()),
                       vacant=sum(u['status'] == 'vacant' for u in units.values()),
                       total=len(units))

    summary_rows = [r for r in range((end + 1) if end is not None else 9, sheet.nrows)
                    if key(r, 1) == 'unit status' and key(r, 20) in {'units', '# units'}]
    summary = {'status': 'absent', 'reported_counts': None, 'row_derived_counts': derived,
               'vendor_status_counts': {}, 'citations': {}, 'matched_fields': []}
    if summary_rows:
        summary['status'] = 'unresolved'
        invalid = len(summary_rows) != 1
        if invalid:
            issue('AMBIGUOUS_VENDOR_SUMMARY', summary_rows[0], 1)
        else:
            start = summary_rows[0]
            for r in range(start + 1, sheet.nrows):
                label = key(r, 1)
                if not label:
                    continue
                if label not in _SUMMARY_LABELS:
                    invalid = True
                    issue('INVALID_VENDOR_SUMMARY', r, 1)
                    break
                tag = label.replace(' ', '_')
                value = cell(r, 20)
                summary['citations'][tag] = cite(r, 20)
                if (sheet.cell_type(r, 20) != 2 or isinstance(value, bool)
                        or not isinstance(value, (int, float)) or not math.isfinite(value)
                        or value < 0 or value > 50_000 or int(value) != value
                        or tag in summary['vendor_status_counts']):
                    invalid = True
                    issue('INVALID_VENDOR_SUMMARY', r, 20)
                else:
                    summary['vendor_status_counts'][tag] = int(value)
                if label == 'totals':
                    break
            values = summary['vendor_status_counts']
            if len(values) != len(_SUMMARY_LABELS):
                invalid = True
                issue('INCOMPLETE_VENDOR_SUMMARY', start, 20)
            if not invalid:
                reported = {'occupied': sum(values[k] for k in
                                            ('occupied_no_ntv', 'occupied_ntv', 'occupied_ntv_leased')),
                            'vacant': values['vacant_leased'] + values['vacant_not_leased'],
                            'down': None, 'total': values['totals']}
                summary['reported_counts'] = reported
                # These are disjoint explicit vendor count rows, not finances.
                # Admin/Down is preserved verbatim as a vendor category, NEVER
                # mapped to Down, including when its reported count is zero.
                mismatch = False
                for field in ('occupied', 'vacant', 'total'):
                    if derived[field] is not None:
                        if reported[field] == derived[field]:
                            summary['matched_fields'].append(field)
                        else:
                            mismatch = True
                if sum(values[k] for k in values if k != 'totals') != values['totals']:
                    mismatch = True
                if mismatch:
                    summary['status'] = 'mismatch'
                    issue('SUMMARY_MISMATCH', start, 20)
    else:
        issue('VENDOR_SUMMARY_ABSENT', end if end is not None else 8)
    # Scoped summaries remain absent: this report has no supported use scope.
    issue('ONESITE_REPORT_SUMMARY', summary_rows[0] if summary_rows else 8,
          20, observation=summary)
    return result


def normalize_onesite_xls(raw: bytes) -> dict:
    """Return unvalidated observations from one supported detailed BIFF XLS.

    Optional xlrd is imported lazily. No macros, formulas, links or network calls
    are executed. Input/library failures become static errors outside handlers,
    so neither traceback chaining nor warning/log sinks expose source text.
    """
    code = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            if not isinstance(raw, bytes) or not raw.startswith(_MAGIC):
                raise _Failure('UNSUPPORTED_XLS_FORMAT')
            if len(raw) > 8 * 1024 * 1024:
                raise _Failure('INPUT_LIMIT_EXCEEDED')
            try:
                import xlrd
            except ImportError:
                raise _Failure('XLS_DEPENDENCY_MISSING') from None
            book = xlrd.open_workbook(file_contents=raw, logfile=_DiscardLog(),
                                      verbosity=0, on_demand=True)
            try:
                return _parse(book, raw)
            finally:
                book.release_resources()
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        code = 'MALFORMED_XLS'
    raise RentRollNormalizationError(code) from None
