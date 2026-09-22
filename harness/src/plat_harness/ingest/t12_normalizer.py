"""Deterministic T12 source-accounting preview; no math, IO or engine calls.

See docs/INGEST_T12.md. A trailing operating statement is previewed as cited
line-item money observations with explicit period completeness and approved
account-mapping references. Every money observation reuses the frozen
``ingest-accounting/1.0.0`` contract (finite decimal strings with currency,
unit and period plus a source locator; Python floats refused; nothing
summed, annualized or rate-derived). Total/subtotal rows, cumulative/YTD
columns and partial statements are never promoted into monthly line items,
and the existing reconciliation annualizer accepting partial month coverage
is NOT permission to treat a partial statement as a T12. This module
performs no financial arithmetic, file IO, model or engine calls.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re

from .accounting import (AccountingObservationError, CONTRACT_VERSION as
                         _ACCOUNTING_VERSION, observe_money,
                         validate_accounting_observations)

__all__ = [
    'ADAPTER', 'CANONICAL_ACCOUNTS', 'CONTRACT_VERSION', 'ERROR_CODES',
    'ISSUE_CODES', 'T12PreviewError', 'accounting_envelope', 'canonical_bytes',
    'preview_t12', 'validate_t12_preview',
]

CONTRACT_VERSION = 'ingest-t12-preview/1.0.0'
ADAPTER = {'id': 't12-normalizer', 'version': '1.0.0'}
CURRENCY = 'USD'
MAX_ROWS = 20_000
MAX_COLUMNS = 512
MAX_CELLS = 200_000
MAX_TEXT = 256
MAX_NODES = 10_000_000

ERROR_CODES = frozenset((
    'INVALID_INPUT', 'INVALID_MAPPING', 'INVALID_WINDOW', 'SCOPE_MISMATCH',
    'DUPLICATE_MONTH', 'MONTH_OUTSIDE_WINDOW', 'EXCLUDED_MONTH_COLUMN',
    'NO_MONTH_COLUMNS', 'EMPTY_STATEMENT', 'AMBIGUOUS_NUMERIC_FORMAT',
    'FORMULA_CACHE_NOT_ORIGINAL',
))
ISSUE_CODES = frozenset((
    'PARTIAL_MONTH_COVERAGE', 'UNMAPPED_ACCOUNT', 'UNLABELED_ROW',
    'UNRECOGNIZED_COLUMN', 'MISSING_MONTH_CELL',
))
ROW_KINDS = frozenset(('section', 'blank', 'line_item', 'total', 'unlabeled'))
COLUMN_ROLES = frozenset(('label', 'month', 'cumulative', 'descriptor',
                          'unrecognized', 'empty', 'excluded'))
MAPPING_STATES = frozenset(('mapped', 'unmapped', 'not_applicable'))

CANONICAL_ACCOUNTS = frozenset((
    'residential_rental_income', 'commercial_rental_income', 'other_income',
    'ancillary_income_recurring', 'ancillary_income_transactional',
    'property_taxes', 'property_insurance', 'employee_benefits', 'payroll',
    'utilities', 'repairs_and_maintenance', 'marketing', 'administrative',
    'management_fees', 'legal_professional', 'turnover',
))

_KEYS = frozenset((
    'contract_version', 'subject_id', 'as_of', 'adapter', 'source', 'sheet',
    'header_row', 'label_column', 'first_row', 'first_column', 'window',
    'months', 'coverage', 'columns', 'rows', 'issues', 'census', 'status',
))
_ROW_KEYS = frozenset(('row', 'row_kind', 'source_label', 'canonical_account',
                      'mapping_state', 'mapping_ref', 'monthly'))
_ENTRY_KEYS = frozenset(('month', 'column', 'observation'))
_MONTH_KEYS = frozenset(('month', 'column', 'header'))
_COLUMN_KEYS = frozenset(('column', 'role', 'header'))
_ISSUE_KEYS = frozenset(('issue_id', 'code', 'severity', 'citation',
                         'observation_ids'))

_MONTH_ABBR = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
               'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}
_MONTH_FULL = {'january': 1, 'february': 2, 'march': 3, 'april': 4,
               'may': 5, 'june': 6, 'july': 7, 'august': 8, 'september': 9,
               'october': 10, 'november': 11, 'december': 12}
_NAME = re.compile(r'^([A-Za-z]{3,9})[\s-]+(20[0-9]{2})$')
_ISO_MONTH = re.compile(r'^(20[0-9]{2})-([0-9]{2})$')
_ISO_DATE = re.compile(r'^(20[0-9]{2})-([0-9]{2})-([0-9]{2})$')
_US_DATE = re.compile(r'^([0-9]{1,2})/([0-9]{1,2})/(20[0-9]{2})$')
_CUMULATIVE = re.compile(
    r'\b(?:ytd|year[\s-]*to[\s-]*date|totals?|cumulative|annual|yearly)\b',
    re.IGNORECASE)
_DESCRIPTORS = frozenset((
    'notes', 'note', 'comments', 'comment', 'memo', 'description', 'descr',
    'gl account', 'gl', 'account code', 'acct code', 'account', 'code',
    'property', 'property id', 'property name', 'portfolio', 'region',
    'floor', 'suite', 'wing', 'category', 'type',
))


class T12PreviewError(ValueError):
    """Static diagnostic only; input-bearing exceptions are never chained."""

    def __init__(self, code='INVALID_INPUT'):
        self.code = code if code in ERROR_CODES else 'INVALID_INPUT'
        super().__init__('T12 preview refused (' + self.code + ').')


class _Failure(Exception):
    pass


def _require(condition, code='INVALID_INPUT'):
    if not condition:
        raise _Failure(code)


def _object(value, keys, code='INVALID_INPUT'):
    _require(type(value) is dict and frozenset(value) == frozenset(keys), code)


def _pattern(value, pattern, code='INVALID_INPUT'):
    _require(type(value) is str and re.fullmatch(pattern, value, re.ASCII)
             is not None, code)


def _identifier(value, prefix, length, code='INVALID_INPUT'):
    _pattern(value, prefix + r'_[0-9a-f]{' + str(length) + '}', code)


def _integer(value, low, high, code='INVALID_INPUT'):
    _require(type(value) is int and low <= value <= high, code)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _walk(value):
    """Reject floats and oversized trees; values stay JSON-native."""
    stack = [value]
    nodes = 0
    while stack:
        item = stack.pop()
        nodes += 1
        _require(nodes <= MAX_NODES)
        kind = type(item)
        if kind is dict:
            stack.extend(item.values())
        elif kind is list:
            stack.extend(item)
        elif kind is str:
            _require(len(item) <= MAX_TEXT)
        elif kind is float or kind is not int:
            _require(kind is not float)
            _require(item is None)


def _scope(subject_id, as_of):
    if subject_id is not None:
        _pattern(subject_id, r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', 'SCOPE_MISMATCH')
    if as_of is not None:
        _pattern(as_of, r'[0-9]{4}-[0-9]{2}-[0-9]{2}', 'SCOPE_MISMATCH')
        try:
            date.fromisoformat(as_of)
        except Exception:
            raise _Failure('SCOPE_MISMATCH') from None


def _source(value, subject_id, as_of):
    _object(value, ('source_id', 'sha256', 'role', 'original_source_ids',
                    'subject_id', 'as_of'))
    _identifier(value['source_id'], 'src', 32)
    _pattern(value['sha256'], r'[0-9a-f]{64}')
    _require(value['role'] == 'original')
    _require(value['original_source_ids'] == [])
    _require((value['subject_id'], value['as_of']) == (subject_id, as_of),
             'SCOPE_MISMATCH')
    return deepcopy(value)


def _parse_month(key, code='INVALID_INPUT'):
    _pattern(key, r'20[0-9]{2}-(0[1-9]|1[0-2])', code)
    return int(key[:4]), int(key[5:7])


def _month_index(key, code='INVALID_INPUT'):
    year, month = _parse_month(key, code)
    return year * 12 + month


def _month_range(first, last):
    start = _month_index(first, 'INVALID_WINDOW')
    end = _month_index(last, 'INVALID_WINDOW')
    _require(start <= end, 'INVALID_WINDOW')
    _require(end - start <= 11, 'INVALID_WINDOW')
    keys = []
    year, month = _parse_month(first, 'INVALID_WINDOW')
    current = start
    while current <= end:
        keys.append('%04d-%02d' % (year, month))
        month += 1
        if month == 13:
            year, month = year + 1, 1
        current += 1
    return keys


def _window(value):
    _require(type(value) is dict
             and frozenset(value) == frozenset(('first_month', 'last_month')),
             'INVALID_WINDOW')
    first, last = value['first_month'], value['last_month']
    _require(type(first) is str and type(last) is str, 'INVALID_WINDOW')
    return _month_range(first, last)


def _month_key(text):
    """Return YYYY-MM for a recognized month header, else None."""
    if type(text) is not str:
        return None
    body = text.strip()
    found = _NAME.match(body)
    if found:
        name = found.group(1).lower()
        if len(name) == 3:
            number = _MONTH_ABBR.get(name)
        elif name == 'sept':
            number = 9
        else:
            number = _MONTH_FULL.get(name)
        if number is None:
            return None
        return found.group(2) + '-%02d' % number
    found = _ISO_MONTH.match(body)
    if found and 1 <= int(found.group(2)) <= 12:
        return body
    found = _ISO_DATE.match(body)
    if found and 1 <= int(found.group(2)) <= 12 and 1 <= int(found.group(3)) <= 31:
        return found.group(1) + '-' + found.group(2)
    found = _US_DATE.match(body)
    if found and 1 <= int(found.group(1)) <= 12 and 1 <= int(found.group(2)) <= 31:
        return found.group(3) + '-%02d' % int(found.group(1))
    return None


def _is_total_label(upper):
    return (upper.startswith('TOTAL') or upper.startswith('SUBTOTAL')
            or upper.startswith('NET OPERATING INCOME')
            or upper.startswith('NET INCOME')
            or upper.startswith('EFFECTIVE GROSS INCOME'))


def _mapping(value):
    _require(type(value) in (tuple, list) and bool(value), 'INVALID_MAPPING')
    table = {}
    for entry in value:
        _object(entry, ('source_label', 'canonical_account', 'mapping_ref'),
                'INVALID_MAPPING')
        label = entry['source_label']
        account = entry['canonical_account']
        reference = entry['mapping_ref']
        _require(type(label) is str and bool(label.strip()), 'INVALID_MAPPING')
        _require(label not in table, 'INVALID_MAPPING')
        _require(account in CANONICAL_ACCOUNTS, 'INVALID_MAPPING')
        _identifier(reference, 'map', 64, 'INVALID_MAPPING')
        table[label] = (account, reference)
    return table


def _column_roles(value, width, label_column):
    _require(type(value) is dict)
    roles = {}
    for key, role in value.items():
        _require(type(key) is int and not isinstance(key, bool)
                 and 0 <= key < width)
        _require(key != label_column)
        _require(role in ('descriptor', 'excluded'))
        roles[key] = role
    return roles


def _cell_origin(value):
    _require(type(value) is str)
    if value in ('formula', 'formula_cache', 'cached_value', 'data_only_cache'):
        raise _Failure('FORMULA_CACHE_NOT_ORIGINAL')
    _require(value == 'typed')


def _grid(value):
    _require(type(value) is list and bool(value))
    _require(len(value) <= MAX_ROWS)
    cells = 0
    width = 0
    for row in value:
        _require(type(row) is list)
        cells += len(row)
        _require(cells <= MAX_CELLS)
        if len(row) > width:
            width = len(row)
        _require(width <= MAX_COLUMNS)
        for cell in row:
            _require(cell is None or type(cell) is str)
            if type(cell) is str:
                _require(len(cell) <= MAX_TEXT)
    return width


def _citation(source, sheet, row, column):
    return {'source_id': source['source_id'], 'source_sha256': source['sha256'],
            'sheet': sheet, 'row': row, 'row_end': row, 'column': column}


def _observe(source, sheet, row, column, cell_text, cell_origin):
    try:
        return observe_money(kind='report_total', measurement_basis='report',
                             source_text=cell_text, currency=CURRENCY,
                             period='month',
                             citation=_citation(source, sheet, row, column),
                             cell_origin=cell_origin)
    except AccountingObservationError as exc:
        if exc.code == 'FORMULA_CACHE_NOT_ORIGINAL':
            raise _Failure('FORMULA_CACHE_NOT_ORIGINAL') from None
        if exc.code in ('AMBIGUOUS_NUMERIC_FORMAT', 'INVALID_OBSERVATIONS'):
            raise _Failure('AMBIGUOUS_NUMERIC_FORMAT') from None
        raise _Failure('INVALID_INPUT') from None


def _is_zero(decimal):
    return decimal.lstrip('-').replace('.', '').lstrip('0') == ''


def _build_issue(source, sheet, code, severity, row, column, observation_ids):
    citation = _citation(source, sheet, row, column)
    ordered = sorted(observation_ids)
    payload = [code, citation, ordered]
    identity = 'iss_' + hashlib.sha256(_encode(payload)).hexdigest()[:32]
    return {'issue_id': identity, 'code': code, 'severity': severity,
            'citation': citation, 'observation_ids': ordered}


def preview_t12(*, grid, first_row, first_column, sheet, source, subject_id,
                as_of, window, header_row, label_column, mapping,
                column_roles=None, cell_origin='typed'):
    """Return a deterministic source-accounting preview; never math."""
    code = 'INVALID_INPUT'
    try:
        return _preview(grid, first_row, first_column, sheet, source,
                         subject_id, as_of, window, header_row, label_column,
                         mapping, column_roles, cell_origin)
    except _Failure as exc:
        code = exc.args[0]
    except T12PreviewError:
        raise
    except Exception:
        pass
    raise T12PreviewError(code) from None


def _preview(grid, first_row, first_column, sheet, source, subject_id, as_of,
             window, header_row, label_column, mapping, column_roles,
             cell_origin):
    _integer(first_row, 1, 1_000_000)
    _integer(first_column, 1, 16384)
    _integer(sheet, 1, 1024)
    width = _grid(grid)
    _require(first_column + width - 1 <= 16384)
    _require(type(header_row) is int and not isinstance(header_row, bool)
             and 0 <= header_row < len(grid))
    _require(type(label_column) is int and not isinstance(label_column, bool)
             and 0 <= label_column < len(grid[header_row]))
    roles = _column_roles(column_roles or {}, width, label_column)
    _cell_origin(cell_origin)
    _scope(subject_id, as_of)
    source = _source(source, subject_id, as_of)
    expected = _window(window)
    table = _mapping(mapping)
    header_row_physical = first_row + header_row
    label_column_physical = first_column + label_column

    data_rows = list(range(header_row + 1, len(grid)))
    has_data = [False] * width
    for row_index in data_rows:
        row = grid[row_index]
        for index, cell_value in enumerate(row):
            if cell_value is not None:
                has_data[index] = True

    def cell(row_index, index):
        row = grid[row_index]
        return row[index] if index < len(row) else None

    columns = []
    month_columns = {}
    unrecognized = []
    for index in range(width):
        header_text = cell(header_row, index)
        header = header_text.strip() if type(header_text) is str else None
        physical = first_column + index
        if index == label_column:
            columns.append({'column': physical, 'role': 'label',
                            'header': header})
            continue
        month = _month_key(header_text)
        if month is not None:
            inside = month in expected
            if inside and index in roles:
                raise _Failure('EXCLUDED_MONTH_COLUMN')
            if not inside and roles.get(index) != 'excluded':
                raise _Failure('MONTH_OUTSIDE_WINDOW')
            if inside:
                if month in month_columns:
                    raise _Failure('DUPLICATE_MONTH')
                month_columns[month] = index
                columns.append({'column': physical, 'role': 'month',
                                'header': header})
            else:
                columns.append({'column': physical, 'role': 'excluded',
                                'header': header})
        elif index in roles:
            columns.append({'column': physical, 'role': roles[index],
                            'header': header})
        else:
            if header is not None and _CUMULATIVE.search(header):
                role = 'cumulative'
            elif header is not None and header.lower() in _DESCRIPTORS:
                role = 'descriptor'
            elif has_data[index] or header is not None:
                role = 'unrecognized'
                unrecognized.append(physical)
            else:
                role = 'empty'
            columns.append({'column': physical, 'role': role, 'header': header})
    if not month_columns:
        raise _Failure('NO_MONTH_COLUMNS')
    ordered = sorted(month_columns.items(), key=lambda item: _month_index(item[0]))
    months = [{'month': key, 'column': first_column + month_columns[key],
               'header': cell(header_row, month_columns[key]).strip()}
              for key, _ in ordered]
    present = [entry['month'] for entry in months]
    missing = [key for key in expected if key not in month_columns]
    complete = not missing and len(present) == 12
    coverage = {'status': 'complete' if complete else 'partial',
                'expected_months': expected, 'present_months': present,
                'missing_months': missing}
    if not any(has_data[index] for index in month_columns.values()):
        raise _Failure('EMPTY_STATEMENT')
    first_month_column = months[0]['column']

    issues = []
    if not complete:
        issues.append(_build_issue(source, sheet, 'PARTIAL_MONTH_COVERAGE',
                                    'blocker', header_row_physical,
                                    first_month_column, []))
    for physical in unrecognized:
        severity = 'blocker' if has_data[physical - first_column] else 'warning'
        issues.append(_build_issue(source, sheet, 'UNRECOGNIZED_COLUMN',
                                    severity, header_row_physical, physical,
                                    []))

    rows = []
    census = {'preceding': header_row, 'header': 1, 'blank': 0, 'section': 0,
              'line_item': 0, 'total': 0, 'unlabeled': 0}
    for row_index in data_rows:
        physical_row = first_row + row_index
        raw_label = cell(row_index, label_column)
        label = raw_label if (type(raw_label) is str and raw_label.strip()) else None
        any_month_cell = any(cell(row_index, index) is not None
                             for index in month_columns.values())
        if label is None and not any_month_cell:
            kind = 'blank'
        elif label is None:
            kind = 'unlabeled'
        elif _is_total_label(label.upper()):
            kind = 'total'
        elif not any_month_cell and label not in table:
            kind = 'section'
        else:
            kind = 'line_item'
        census[kind] += 1
        if kind in ('section', 'blank'):
            rows.append({'row': physical_row, 'row_kind': kind,
                         'source_label': label, 'canonical_account': None,
                         'mapping_state': 'not_applicable', 'mapping_ref': None,
                         'monthly': []})
            continue
        monthly = []
        unknown_ids = []
        all_ids = []
        material = False
        for key, month_index in ordered:
            text = cell(row_index, month_index)
            observed = _observe(source, sheet, physical_row,
                                first_column + month_index, text, cell_origin)
            monthly.append({'month': key, 'column': first_column + month_index,
                            'observation': observed})
            identity = observed['observation_id']
            all_ids.append(identity)
            amount = observed['amount']
            if amount is None:
                unknown_ids.append(identity)
            elif not _is_zero(amount['decimal']):
                material = True
        if kind == 'line_item' and label in table:
            canonical, reference = table[label]
            state = 'mapped'
        else:
            canonical, reference, state = None, None, 'unmapped'
            if kind == 'total':
                state = 'not_applicable'
        rows.append({'row': physical_row, 'row_kind': kind,
                     'source_label': label, 'canonical_account': canonical,
                     'mapping_state': state, 'mapping_ref': reference,
                     'monthly': monthly})
        if kind == 'line_item' and state == 'unmapped':
            issues.append(_build_issue(
                source, sheet, 'UNMAPPED_ACCOUNT',
                'blocker' if material else 'warning', physical_row,
                label_column_physical, all_ids))
        if kind == 'unlabeled':
            issues.append(_build_issue(source, sheet, 'UNLABELED_ROW',
                                        'blocker', physical_row,
                                        first_month_column, all_ids))
        if unknown_ids:
            issues.append(_build_issue(source, sheet, 'MISSING_MONTH_CELL',
                                        'blocker', physical_row,
                                        first_month_column, unknown_ids))
    status = 'blocked' if any(issue['severity'] == 'blocker'
                              for issue in issues) else 'observed_unvalidated'
    return {
        'contract_version': CONTRACT_VERSION, 'subject_id': subject_id,
        'as_of': as_of, 'adapter': deepcopy(ADAPTER), 'source': source,
        'sheet': sheet, 'header_row': header_row_physical,
        'label_column': label_column_physical, 'first_row': first_row,
        'first_column': first_column,
        'window': {'first_month': window['first_month'],
                   'last_month': window['last_month']},
        'months': months, 'coverage': coverage, 'columns': columns,
        'rows': rows, 'issues': issues, 'census': census, 'status': status,
    }


def validate_t12_preview(value, *, subject_id, as_of):
    """Validate exact host scope and return a defensive JSON-native copy."""
    code = 'INVALID_INPUT'
    try:
        return _validate(value, subject_id, as_of)
    except _Failure as exc:
        code = exc.args[0]
    except T12PreviewError:
        raise
    except Exception:
        pass
    raise T12PreviewError(code) from None


def _validate(value, subject_id, as_of):
    _require(type(value) is dict)
    _walk(value)
    _object(value, _KEYS)
    _require(value['contract_version'] == CONTRACT_VERSION)
    _require(value['adapter'] == ADAPTER)
    _scope(subject_id, as_of)
    _scope(value['subject_id'], value['as_of'])
    _require((value['subject_id'], value['as_of']) == (subject_id, as_of),
             'SCOPE_MISMATCH')
    source = _source(value['source'], value['subject_id'], value['as_of'])
    sheet = value['sheet']
    _integer(sheet, 1, 1024)
    first_row = value['first_row']
    _integer(first_row, 1, 1_000_000)
    first_column = value['first_column']
    _integer(first_column, 1, 16384)
    header_row = value['header_row']
    _integer(header_row, first_row, 1_000_000)
    label_column = value['label_column']
    _integer(label_column, first_column, 16384)
    expected = _window(value['window'])

    months = value['months']
    _require(type(months) is list and bool(months), 'NO_MONTH_COLUMNS')
    _require(len(months) <= 12)
    seen_months = set()
    seen_columns = set()
    order = []
    for entry in months:
        _object(entry, _MONTH_KEYS)
        _parse_month(entry['month'])
        _integer(entry['column'], first_column, 16384)
        _require(entry['column'] != label_column)
        _require(type(entry['header']) is str and bool(entry['header']))
        _require(_month_key(entry['header']) == entry['month'])
        _require(entry['month'] not in seen_months, 'DUPLICATE_MONTH')
        _require(entry['column'] not in seen_columns, 'DUPLICATE_MONTH')
        _require(entry['month'] in expected, 'MONTH_OUTSIDE_WINDOW')
        seen_months.add(entry['month'])
        seen_columns.add(entry['column'])
        order.append(_month_index(entry['month']))
    _require(order == sorted(order))
    present = [entry['month'] for entry in months]
    missing = [key for key in expected if key not in seen_months]
    complete = not missing and len(present) == 12
    status_word = 'complete' if complete else 'partial'
    _require(value['coverage'] == {'status': status_word,
                                   'expected_months': expected,
                                   'present_months': present,
                                   'missing_months': missing})
    first_month_column = months[0]['column']

    columns = value['columns']
    _require(type(columns) is list and bool(columns))
    previous = None
    month_columns = {}
    unrecognized = []
    for entry in columns:
        _object(entry, _COLUMN_KEYS)
        column = entry['column']
        _integer(column, first_column, 16384)
        _require(previous is None or column == previous + 1)
        _require(columns[0]['column'] == first_column)
        previous = column
        _require(entry['role'] in COLUMN_ROLES)
        header = entry['header']
        _require(header is None or (type(header) is str and bool(header)))
        if column == label_column:
            _require(entry['role'] == 'label')
        elif entry['role'] == 'label':
            raise _Failure('INVALID_INPUT')
        if entry['role'] == 'month':
            key = _month_key(header)
            _require(key is not None and key in expected, 'INVALID_INPUT')
            _require(column not in month_columns, 'DUPLICATE_MONTH')
            month_columns[column] = key
        elif entry['role'] == 'excluded' and header is not None:
            key = _month_key(header)
            _require(key is None or key not in expected,
                     'EXCLUDED_MONTH_COLUMN')
        elif entry['role'] == 'cumulative':
            _require(header is not None and _CUMULATIVE.search(header))
        elif entry['role'] == 'empty':
            _require(header is None)
        elif entry['role'] == 'unrecognized':
            unrecognized.append(column)
    _require(frozenset(month_columns) == seen_columns)
    for entry in months:
        _require(month_columns[entry['column']] == entry['month'])
    _require(label_column <= columns[-1]['column'])

    rows = value['rows']
    _require(type(rows) is list and bool(rows), 'EMPTY_STATEMENT')
    previous_row = header_row
    census = {'preceding': header_row - first_row, 'header': 1, 'blank': 0,
              'section': 0, 'line_item': 0, 'total': 0, 'unlabeled': 0}
    facts = []
    identities = set()
    has_monthly = False
    for row in rows:
        _object(row, _ROW_KEYS)
        physical = row['row']
        _integer(physical, header_row + 1, 1_000_000)
        _require(physical == previous_row + 1)
        previous_row = physical
        kind = row['row_kind']
        _require(kind in ROW_KINDS)
        census[kind] += 1
        label = row['source_label']
        _require(label is None or (type(label) is str and bool(label.strip())))
        if kind in ('line_item', 'total', 'section'):
            _require(label is not None)
        else:
            _require(label is None)
        account = row['canonical_account']
        reference = row['mapping_ref']
        state = row['mapping_state']
        _require(state in MAPPING_STATES)
        if kind == 'line_item' and state == 'mapped':
            _require(account in CANONICAL_ACCOUNTS)
            _identifier(reference, 'map', 64)
            _require(not _is_total_label(label.upper()))
        else:
            _require(account is None and reference is None)
            wanted = 'not_applicable' if kind != 'unlabeled' else 'unmapped'
            _require(state == wanted)
        if kind == 'line_item':
            _require(not _is_total_label(label.upper()))
        if kind == 'total':
            _require(_is_total_label(label.upper()))
        if kind in ('section', 'blank'):
            _require(row['monthly'] == [])
            continue
        monthly = row['monthly']
        _require(type(monthly) is list and len(monthly) == len(months))
        has_monthly = True
        all_ids = []
        unknown_ids = []
        material = False
        for index, entry in enumerate(monthly):
            _object(entry, _ENTRY_KEYS)
            _require(entry['month'] == months[index]['month'])
            _require(entry['column'] == months[index]['column'])
            observed = entry['observation']
            _object(observed, ('observation_id', 'source_id', 'kind',
                               'measurement_basis', 'amount', 'cell_origin',
                               'citation'))
            citation = _citation(source, sheet, physical, entry['column'])
            _require(observed['citation'] == citation)
            amount = observed['amount']
            text = None if amount is None else amount['source_text']
            rebuilt = _observe(source, sheet, physical, entry['column'], text,
                              'typed')
            _require(observed == rebuilt)
            identity = observed['observation_id']
            _require(identity not in identities)
            identities.add(identity)
            all_ids.append(identity)
            if amount is None:
                unknown_ids.append(identity)
            elif not _is_zero(amount['decimal']):
                material = True
        facts.append((physical, kind, state, material, all_ids, unknown_ids))
    _require(has_monthly, 'EMPTY_STATEMENT')
    _require(value['census'] == census)

    rebuilt = []
    if not complete:
        rebuilt.append(('PARTIAL_MONTH_COVERAGE', 'blocker', header_row,
                        first_month_column, []))
    for column in unrecognized:
        rebuilt.append(('UNRECOGNIZED_COLUMN', None, header_row, column, []))
    for physical, kind, state, material, all_ids, unknown_ids in facts:
        if kind == 'line_item' and state == 'unmapped':
            rebuilt.append(('UNMAPPED_ACCOUNT',
                             'blocker' if material else 'warning', physical,
                             label_column, all_ids))
        if kind == 'unlabeled':
            rebuilt.append(('UNLABELED_ROW', 'blocker', physical,
                            first_month_column, all_ids))
        if unknown_ids:
            rebuilt.append(('MISSING_MONTH_CELL', 'blocker', physical,
                            first_month_column, unknown_ids))
    issues = value['issues']
    _require(type(issues) is list and len(issues) == len(rebuilt))
    for issue, spec in zip(issues, rebuilt):
        _object(issue, _ISSUE_KEYS)
        code, severity, row, column, ids = spec
        _require(issue['code'] == code)
        citation = _citation(source, sheet, row, column)
        _require(issue['citation'] == citation)
        _require(issue['observation_ids'] == sorted(ids))
        if severity is None:
            _require(issue['severity'] in ('blocker', 'warning'))
        else:
            _require(issue['severity'] == severity)
        expected_issue = _build_issue(source, sheet, code, issue['severity'],
                                      row, column, ids)
        _require(issue == expected_issue)
    blocked = any(issue['severity'] == 'blocker' for issue in issues)
    _require(value['status'] == ('blocked' if blocked
                                 else 'observed_unvalidated'))
    return deepcopy(value)


def accounting_envelope(preview, *, subject_id, as_of):
    """Validate the preview, then emit the frozen accounting envelope."""
    value = validate_t12_preview(preview, subject_id=subject_id, as_of=as_of)
    observations = [deepcopy(entry['observation']) for row in value['rows']
                    for entry in row['monthly']]
    envelope = {
        'contract_version': _ACCOUNTING_VERSION,
        'subject_id': value['subject_id'], 'as_of': value['as_of'],
        'adapter': deepcopy(ADAPTER), 'sources': [deepcopy(value['source'])],
        'observations': observations, 'issues': deepcopy(value['issues']),
        'status': 'blocked' if any(item['amount'] is None
                                   for item in observations)
        else 'observed_unvalidated',
    }
    return validate_accounting_observations(envelope, subject_id=subject_id,
                                            as_of=as_of)


def canonical_bytes(value, *, subject_id, as_of):
    """Validate before emitting compact sorted-key UTF-8 JSON (no newline)."""
    return _encode(validate_t12_preview(value, subject_id=subject_id,
                                         as_of=as_of))