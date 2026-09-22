"""Read-only Compact OneSite XLS/BIFF adapter; never occupancy approval.

Normalized extraction for compact RealPage OneSite BIFF exports with 20-30 columns,
in-row ancillary charge columns, and summary termination.
No raw rows, workbook metadata, free text, money, or tenant identifiers leave
this module. Output is an unvalidated observation, never underwriting approval.
"""
from __future__ import annotations

import hashlib
import re
import warnings
from typing import Any

from .pms_normalizer import RentRollNormalizationError

__all__ = [
    'BASE_RENT_CODES',
    'ANCILLARY_CATEGORIES',
    'ANCILLARY_CODES',
    'classify_charge',
    'is_base_rent',
    'is_ancillary_charge',
    'normalize_onesite_compact_xls',
]

_MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
_MAX_BYTES = 32 * 1024 * 1024
_MAX_ROWS = 50_000
_MAX_COLS = 128
_MAX_CELLS = 250_000

_COUNTS = ('occupied', 'vacant', 'down', 'total')
_SCOPES = ('residential', 'commercial')

_STATUSES = {
    'occupied': 'occupied',
    'occupied ntv': 'occupied',
    'occupied ntvl': 'occupied',
    'occupied no ntv': 'occupied',
    'occupied leased': 'occupied',
    'current': 'occupied',
    'notice': 'occupied',
    'vacant': 'vacant',
    'vacant leased': 'vacant',
    'vacant unleased': 'vacant',
    'vacant not leased': 'vacant',
    'vacant rented': 'vacant',
    'vacant unrented': 'vacant',
    'down': 'down',
    'offline': 'down',
    'out of service': 'down',
}

_NON_CURRENT = {
    'applicant',
    'pending renewal',
    'pending',
    'former',
    'future',
    'former resident',
    'future resident',
    'former applicant',
    'pending resident',
}

_EXCLUDED_SECTIONS = {
    'former residents',
    'future residents',
    'applicants',
    'pending renewals',
    'former applicants',
    'future applicants',
    'future / applicants',
    'future/applicants',
    'future residents & applicants',
    'future residents and applicants',
}

_TOTALS = {
    'total',
    'totals',
    'totals:',
    'grand total',
    'report total',
    'summary',
}

BASE_RENT_CODES = frozenset({
    'rent',
    'base rent',
    'baserent',
    'mrent',
    'market rent',
    'apt rent',
    'aptr',
    'lease rent',
    'resrent',
    'contract rent',
    'market + addl.',
    'market addl',
})

_NON_CHARGE_HEADERS = frozenset({
    'lease id', 'lease start', 'lease end', 'lease term',
    'resh id', 'resident id', 'tenant id', 'move in', 'move out',
    'balance', 'required deposit', 'dep on hand', 'deposit',
})

ANCILLARY_CATEGORIES: dict[str, frozenset[str]] = {
    'parking': frozenset({
        'park', 'parking', 'garage', 'carport', 'space', 'lot',
        'covered parking', 'reserved parking',
    }),
    'pet': frozenset({
        'pet', 'pet rent', 'petrent', 'pet fee', 'animal', 'dog', 'cat',
    }),
    'storage': frozenset({
        'stor', 'storage', 'locker', 'storage locker', 'bike storage',
    }),
    'utilities': frozenset({
        'util', 'utility', 'water', 'sewer', 'trash', 'electric', 'gas',
        'rubs', 'cable', 'tech', 'valet trash', 'pest',
    }),
    'washer_dryer': frozenset({
        'washer', 'dryer', 'w/d', 'wd', 'w d', 'laundry', 'appliance',
    }),
    'concessions': frozenset({
        'conc', 'concession', 'discount', 'free rent', 'credit', 'model concession',
    }),
    'insurance': frozenset({
        'damage waiver', 'damage waiver program', 'insurance',
        'renters insurance', 'liability',
    }),
}

ANCILLARY_CODES = frozenset.union(*ANCILLARY_CATEGORIES.values())


class _Failure(Exception):
    pass


class _DiscardLog:
    """xlrd can write input-bearing diagnostics even when verbosity is zero."""
    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        pass


def _key(value: Any) -> str:
    if value is None:
        return ''
    if isinstance(value, str):
        return re.sub(r'[\s/,:\-_]+', ' ', value.strip().lower()).strip()
    return re.sub(r'[\s/,:\-_]+', ' ', str(value).strip().lower()).strip()


def classify_charge(code: str | None) -> str:
    """Classify charge code into 'base_rent', specific ancillary category, or 'unknown'."""
    if not code:
        return 'unknown'
    normalized = _key(code)
    if not normalized:
        return 'unknown'
    if normalized in _TOTALS or normalized in _NON_CHARGE_HEADERS:
        return 'unknown'
    # Prioritize ancillary categories to avoid shadowing (e.g. 'pet rent', 'free rent')
    for category, codes in ANCILLARY_CATEGORIES.items():
        if normalized in codes or any(
            c == normalized or normalized.startswith(c + ' ') or normalized.endswith(' ' + c)
            for c in codes
        ):
            return category
    if normalized in BASE_RENT_CODES or any(
        normalized == b or normalized.startswith(b + ' ') or normalized.endswith(' ' + b)
        for b in BASE_RENT_CODES
    ):
        return 'base_rent'
    return 'unknown'


def is_base_rent(code: str | None) -> bool:
    return classify_charge(code) == 'base_rent'


def is_ancillary_charge(code: str | None) -> bool:
    cat = classify_charge(code)
    return cat not in ('base_rent', 'unknown')


def _parse(book: Any, raw_bytes: bytes) -> dict[str, Any]:
    if book.nsheets != 1:
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')
    sheet = book.sheet_by_index(0)
    if sheet.nrows > _MAX_ROWS or sheet.ncols > _MAX_COLS or sheet.nrows * sheet.ncols > _MAX_CELLS:
        raise _Failure('INPUT_LIMIT_EXCEEDED')
    # Compact OneSite typically has ~20-30 columns (reject detailed OneSite with 51+ cols)
    if sheet.ncols >= 51 or sheet.ncols < 7 or sheet.nrows < 6:
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')

    # Verify preamble contains OneSite / Rent Roll Detail
    preamble_match = False
    for r in range(min(5, sheet.nrows)):
        for c in range(sheet.ncols):
            v = _key(sheet.cell_value(r, c))
            if 'rent roll' in v or 'onesite' in v:
                preamble_match = True
                break
        if preamble_match:
            break
    if not preamble_match:
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')

    # Detect header row in rows 4-11
    header_r = None
    for r in range(4, min(12, sheet.nrows)):
        row_keys = [_key(sheet.cell_value(r, c)) for c in range(sheet.ncols)]
        has_unit = any(k in {'unit', 'unit #', 'unit number'} for k in row_keys)
        has_floorplan = any(k in {'floorplan', 'floor plan'} for k in row_keys)
        has_status = any(k in {'unit/lease status', 'unit lease status', 'lease status', 'status'} for k in row_keys)
        has_name = any(k in {'name', 'resident', 'resident name', 'tenant', 'tenant name'} for k in row_keys)
        has_sqft = any(k in {'sqft', 'sq ft', 'sq. ft.', 'square feet'} for k in row_keys)
        if has_unit and has_floorplan and has_status and has_name and has_sqft:
            header_r = r
            break

    if header_r is None:
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')

    col_map: dict[str, int] = {}
    ancillary_cols: list[int] = []
    base_rent_cols: list[int] = []

    for c in range(sheet.ncols):
        k = _key(sheet.cell_value(header_r, c))
        if not k:
            continue
        if 'unit' not in col_map and k in {'unit', 'unit #', 'unit number'}:
            col_map['unit'] = c
        elif 'floorplan' not in col_map and k in {'floorplan', 'floor plan'}:
            col_map['floorplan'] = c
        elif 'designation' not in col_map and k in {'unit designation', 'designation'}:
            col_map['designation'] = c
        elif 'sqft' not in col_map and k in {'sqft', 'sq ft', 'sq. ft.', 'square feet'}:
            col_map['sqft'] = c
        elif 'status' not in col_map and k in {'unit/lease status', 'unit lease status', 'lease status', 'status'}:
            col_map['status'] = c
        elif 'name' not in col_map and k in {'name', 'resident', 'resident name', 'tenant', 'tenant name'}:
            col_map['name'] = c
        elif k == 'rent':
            col_map['rent'] = c
            base_rent_cols.insert(0, c)
        elif 'lease_rent' not in col_map and k == 'lease rent':
            col_map['lease_rent'] = c
            base_rent_cols.append(c)
        elif is_base_rent(k):
            base_rent_cols.append(c)
        elif is_ancillary_charge(k):
            ancillary_cols.append(c)

    required = ('unit', 'floorplan', 'sqft', 'status', 'name')
    if not all(col in col_map for col in required):
        raise _Failure('UNSUPPORTED_ONESITE_LAYOUT')

    digest = hashlib.sha256(raw_bytes).hexdigest()
    result: dict[str, Any] = {
        'version': '1.0',
        'pms_type': 'realpage',
        'source_sha256': digest,
        'residential_units': [],
        'commercial_units': [],
        'counts': {s: dict.fromkeys(_COUNTS) for s in _SCOPES},
        'summary': {
            s: {'status': 'absent', 'reported_counts': None, 'citations': []}
            for s in _SCOPES
        },
        'issues': [],
        'status': 'blocked',
    }

    def cite(r: int, c: int) -> dict[str, Any]:
        return {
            'source_sha256': digest,
            'sheet': 1,
            'row': r + 1,
            'row_end': r + 1,
            'column': c + 1,
        }

    def issue(
        code: str,
        r: int | None = None,
        c: int = 0,
        *,
        warning: bool = False,
        observation: dict[str, Any] | None = None,
    ) -> None:
        entry: dict[str, Any] = {
            'code': code,
            'severity': 'warning' if warning else 'blocker',
            'citation': cite(r, c) if r is not None else None,
        }
        if observation is not None:
            entry['observation'] = observation
        result['issues'].append(entry)

    units: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    current_unit: dict[str, Any] | None = None
    non_current_lease = False
    inventory_valid = True
    excluded = False
    end: int | None = None

    col_unit = col_map['unit']
    col_floorplan = col_map['floorplan']
    col_designation = col_map.get('designation')
    col_sqft = col_map['sqft']
    col_status = col_map['status']
    col_name = col_map['name']

    for r in range(header_r + 1, sheet.nrows):
        row_vals = [sheet.cell_value(r, c) for c in range(sheet.ncols)]
        if not any(v not in ('', None) for v in row_vals):
            continue

        val_first = _key(sheet.cell_value(r, 0))
        val_unit_key = _key(sheet.cell_value(r, col_unit))
        status_key = _key(sheet.cell_value(r, col_status))

        # Check totals / summary
        if val_first in _TOTALS or val_unit_key in _TOTALS:
            end = r
            break

        # Check excluded sections
        if val_first in _EXCLUDED_SECTIONS or val_unit_key in _EXCLUDED_SECTIONS:
            excluded = True
            current_unit = None
            issue('NON_CURRENT_SECTION_EXCLUDED', r, 0, warning=True)
            continue

        if (
            val_first in {'details', 'current residents', 'current units'}
            or val_unit_key in {'details', 'current residents', 'current units'}
        ):
            excluded = False
            continue

        if excluded:
            continue

        # Check non-current lease row
        if status_key in _NON_CURRENT:
            non_current_lease = True
            issue('NON_CURRENT_LEASE_EXCLUDED', r, col_status, warning=True)
            continue

        raw_unit = sheet.cell_value(r, col_unit)
        unit_str = None
        if raw_unit not in ('', None):
            if isinstance(raw_unit, float) and raw_unit.is_integer():
                unit_str = str(int(raw_unit))
            else:
                unit_str = str(raw_unit).strip()

        # Primary unit row
        if unit_str:
            non_current_lease = False
            status = _STATUSES.get(status_key)

            evidence_primary: dict[str, Any] = {
                'unit_id': cite(r, col_unit),
                'status': cite(r, col_status),
            }
            if col_floorplan is not None and sheet.cell_value(r, col_floorplan):
                evidence_primary['floorplan'] = cite(r, col_floorplan)
            if col_designation is not None and sheet.cell_value(r, col_designation):
                evidence_primary['designation'] = cite(r, col_designation)
            if col_name is not None and sheet.cell_value(r, col_name) and status == 'occupied':
                evidence_primary['tenant_name'] = cite(r, col_name)

            if not sheet.cell_value(r, col_floorplan):
                inventory_valid = False
                issue('INCOMPLETE_UNIT_ROW', r, col_floorplan)

            sqft_val = sheet.cell_value(r, col_sqft)
            if not sqft_val:
                inventory_valid = False
                issue('INCOMPLETE_UNIT_ROW', r, col_sqft)

            if status is None:
                inventory_valid = False
                issue('UNSUPPORTED_UNIT_STATUS', r, col_status)

            # Base rent citation in primary evidence
            for bc in base_rent_cols:
                b_val = sheet.cell_value(r, bc)
                if b_val not in ('', None, 0, 0.0):
                    evidence_primary['charge'] = cite(r, bc)
                    break

            if unit_str in conflicts:
                issue('DUPLICATE_UNIT_CONFLICT', r, col_unit)
                current_unit = None
                continue

            existing = units.get(unit_str)
            if existing:
                if existing['status'] != status:
                    inventory_valid = False
                    conflicts.add(unit_str)
                    existing['status'] = None
                    issue('DUPLICATE_UNIT_CONFLICT', r, col_unit)
                else:
                    issue('DUPLICATE_UNIT_EVIDENCE', r, col_unit, warning=True)
                existing['evidence'].append(evidence_primary)
                current_unit = existing
            else:
                current_unit = {
                    'unit_id': unit_str,
                    'status': status,
                    'unit_type': None,
                    'tenant_name': '[REDACTED]',
                    'evidence': [evidence_primary],
                }
                units[unit_str] = current_unit

            # Ancillary charges in primary row
            for ac in ancillary_cols:
                a_val = sheet.cell_value(r, ac)
                if a_val not in ('', None, 0, 0.0):
                    current_unit['evidence'].append({'charge': cite(r, ac)})
                    issue('CONTINUATION_EVIDENCE', r, ac, warning=True)

            continue

        # Continuation row (blank unit ID)
        has_continuation_data = False
        name_val = sheet.cell_value(r, col_name) if col_name is not None else None
        if name_val not in ('', None):
            has_continuation_data = True
        for c in base_rent_cols + ancillary_cols:
            if sheet.cell_value(r, c) not in ('', None, 0, 0.0):
                has_continuation_data = True
                break

        if has_continuation_data:
            if non_current_lease:
                issue('NON_CURRENT_CONTINUATION_EXCLUDED', r, 0, warning=True)
                continue
            if current_unit is None:
                inventory_valid = False
                issue('ORPHAN_CONTINUATION', r, 0)
            else:
                if name_val not in ('', None) and current_unit['status'] == 'occupied':
                    current_unit['evidence'].append({'tenant_name': cite(r, col_name)})
                    issue('CONTINUATION_EVIDENCE', r, col_name, warning=True)
                for c in base_rent_cols + ancillary_cols:
                    if sheet.cell_value(r, c) not in ('', None, 0, 0.0):
                        current_unit['evidence'].append({'charge': cite(r, c)})
                        issue('CONTINUATION_EVIDENCE', r, c, warning=True)
            continue

        if status_key:
            inventory_valid = False
            issue('UNSUPPORTED_SECONDARY_STATUS', r, col_status)

    if end is None:
        inventory_valid = False
        issue('INVENTORY_BOUNDARY_MISSING', header_r)

    if not units:
        inventory_valid = False
        issue('NO_CURRENT_UNITS', header_r)

    for unit in units.values():
        issue(
            'UNRESOLVED_UNIT_USE',
            unit['evidence'][0]['unit_id']['row'] - 1,
            col_designation if col_designation is not None else col_floorplan,
            observation=unit,
        )

    issue('DOWN_EVIDENCE_UNRESOLVED', header_r, col_status)

    return result


def normalize_onesite_compact_xls(raw_bytes: bytes) -> dict[str, Any]:
    """Parse and normalize compact OneSite BIFF XLS into v1-compatible observation dict.

    Never executes macros, formulas, or external links.
    Tenant identities are strictly redacted.
    """
    code = None
    try:
        with warnings.catch_warnings():
            warnings.simplefilter('error')
            if not isinstance(raw_bytes, bytes):
                raise _Failure('MALFORMED_INPUT')
            if len(raw_bytes) > _MAX_BYTES:
                raise _Failure('INPUT_LIMIT_EXCEEDED')
            if not raw_bytes.startswith(_MAGIC):
                raise _Failure('UNSUPPORTED_XLS_FORMAT')
            try:
                import xlrd
            except ImportError:
                raise _Failure('XLS_DEPENDENCY_MISSING') from None
            book = xlrd.open_workbook(
                file_contents=raw_bytes,
                logfile=_DiscardLog(),
                verbosity=0,
                on_demand=True,
            )
            try:
                return _parse(book, raw_bytes)
            finally:
                book.release_resources()
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        code = 'MALFORMED_INPUT'

    raise RentRollNormalizationError(code) from None
