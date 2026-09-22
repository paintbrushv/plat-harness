"""Public PMS compatibility matrix. Not vendor certification or Item 2 acceptance.

Synthetic-only fixtures cannot be labelled real-export verified. Missing Entrata
originals are an acquisition gap. The two-archetype gate stays blocked without
independently reviewed use and independent Down evidence. Engine eligibility is
always not_evaluated. See docs/PMS_COMPATIBILITY.md.
"""
from copy import deepcopy
import json
import re

__all__ = [
    'CompatibilityError',
    'CONTRACT_VERSION',
    'evaluate_two_archetype_gate',
    'public_matrix',
    'recompute_totals',
    'sanitize_public',
    'validate_matrix',
    'validate_subtype',
]

CONTRACT_VERSION = 'pms-compatibility-matrix/1.0.0'
READER_VERSION = '1.0.0'
ERROR_CODES = frozenset((
    'INVALID_MATRIX', 'EXTRA_KEY', 'MISSING_KEY', 'SYNTHETIC_REAL_CLAIM',
    'OMITTED_ORIGINAL', 'STUDENT_HOUSING_RELABEL', 'TOTALS_MISMATCH',
    'ZERO_RAN_SUCCESS', 'FOUR_COUNT_FABRICATED', 'PRIVATE_LITERAL',
))
MATRIX_KEYS = (
    'contract_version', 'readers', 'coverage', 'two_archetype_gate', 'totals',
    'engine_eligibility', 'item2_milestone',
)
READER_KEYS = (
    'reader_id', 'reader_version', 'container', 'structural_signature',
    'provenance_confidence', 'supported_fields', 'source_test_status',
    'limitations', 'refusal_codes',
)
COVERAGE_KEYS = ('declared_originals', 'rows')
COVERAGE_ROW_KEYS = (
    'original_id', 'accounted', 'source_role', 'container', 'reader_id', 'status',
)
GATE_KEYS = (
    'intended_pair', 'garden', 'high_rise', 'independently_reviewed_use',
    'independently_reviewed_down', 'four_count_status', 'status', 'blockers',
)
ARCHETYPE_KEYS = (
    'label', 'form', 'reader_id', 'use_evidence', 'down_evidence', 'four_count_status',
)
TOTAL_KEYS = (
    'readers', 'coverage_declared', 'coverage_accounted', 'synthetic_only',
    'acquisition_gaps', 'real_export_verified', 'ran', 'four_count_reconciled',
)
SHARED_LIMITS = (
    'unknown_use', 'independent_down_unresolved', 'csv_only_semantic_resolver',
    'engine_eligibility_not_evaluated',
)
PRIVATE_LITERALS = (
    'raw_inputs', '/home/', '/Users/', 'resident_name', '@gmail', '@yahoo',
    '.xlsm', '00099 -', '00101 -',
)
_EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}')
_STUDENT = re.compile(r'student[\s_]+housing')
_FLAT_FIELDS = ['unit_id', 'status', 'unit_type', 'counts', 'summary', 'issues']
_FLAT_REFUSALS = [
    'MALFORMED_INPUT', 'INPUT_LIMIT_EXCEEDED', 'XLSX_DEPENDENCY_MISSING',
    'AMBIGUOUS_HEADER', 'HEADER_NOT_FOUND', 'UNSUPPORTED_PMS', 'EMPTY_INPUT',
    'UNSUPPORTED_INPUT_FORMAT', 'UNSUPPORTED_PMS_FORMAT',
]


class CompatibilityError(ValueError):
    """Static diagnostic only; input-bearing exceptions are never chained."""

    def __init__(self, code='INVALID_MATRIX'):
        self.code = code if type(code) is str and code in ERROR_CODES else 'INVALID_MATRIX'
        super().__init__('PMS compatibility matrix refused (' + self.code + ').')


class _Failure(Exception):
    pass


def _require(condition, code='INVALID_MATRIX'):
    if not condition:
        raise _Failure(code)


def _keys(value, keys):
    _require(type(value) is dict)
    actual = set(value)
    expected = set(keys)
    extra = actual - expected
    missing = expected - actual
    _require(not extra, 'EXTRA_KEY')
    _require(not missing, 'MISSING_KEY')


def _text_list(value):
    _require(type(value) is list and value)
    _require(all(type(item) is str and item and type(item) is not bool for item in value))


def _bool(value):
    _require(type(value) is bool)


def _string(value):
    _require(type(value) is str and value and type(value) is not bool)


def sanitize_public(value):
    try:
        _require(type(value) is str, 'INVALID_MATRIX')
        for snippet in PRIVATE_LITERALS:
            _require(snippet not in value, 'PRIVATE_LITERAL')
        _require(_EMAIL.search(value) is None, 'PRIVATE_LITERAL')
        return value
    except _Failure as exc:
        raise CompatibilityError(exc.args[0]) from None


def validate_subtype(row):
    try:
        _keys(row, ('label', 'subtype'))
        _string(row['label'])
        _string(row['subtype'])
        _require(_STUDENT.search(row['subtype'].lower()) is None, 'STUDENT_HOUSING_RELABEL')
        return row
    except _Failure as exc:
        raise CompatibilityError(exc.args[0]) from None


def recompute_totals(matrix):
    readers = matrix['readers']
    declared = matrix['coverage']['declared_originals']
    rows = matrix['coverage']['rows']
    return {
        'readers': len(readers),
        'coverage_declared': len(declared),
        'coverage_accounted': sum(1 for row in rows if row.get('accounted') is True),
        'synthetic_only': sum(1 for row in readers if row.get('source_test_status') == 'synthetic_only'),
        'acquisition_gaps': sum(1 for row in rows if row.get('status') == 'acquisition_gap'),
        'real_export_verified': sum(
            1 for row in readers
            if row.get('source_test_status') == 'real_export_verified'
            or row.get('provenance_confidence') == 'real_export_verified'),
        'ran': 0,
        'four_count_reconciled': 0,
    }


def evaluate_two_archetype_gate(garden, high_rise, independently_reviewed_use,
                                independently_reviewed_down, originals_ran=0):
    try:
        _require(type(garden) is dict and type(high_rise) is dict)
        _bool(independently_reviewed_use)
        _bool(independently_reviewed_down)
        _require(type(originals_ran) is int and originals_ran >= 0)
        blockers = []
        if independently_reviewed_use is not True:
            blockers.append('MISSING_REVIEWED_USE')
        if independently_reviewed_down is not True:
            blockers.append('MISSING_INDEPENDENT_DOWN')
        if originals_ran == 0:
            blockers.append('ZERO_RAN')
        for row in (garden, high_rise):
            if row.get('use_evidence') != 'present':
                if 'MISSING_REVIEWED_USE' not in blockers:
                    blockers.append('MISSING_REVIEWED_USE')
            if row.get('down_evidence') != 'present':
                if 'MISSING_INDEPENDENT_DOWN' not in blockers:
                    blockers.append('MISSING_INDEPENDENT_DOWN')
        if not blockers:
            blockers.append('FOUR_COUNT_UNPROVEN')

        def archetype(source, form):
            return {
                'label': source['label'],
                'form': form,
                'reader_id': source['reader_id'],
                'use_evidence': source['use_evidence'],
                'down_evidence': source['down_evidence'],
                'four_count_status': 'blocked',
            }

        return {
            'intended_pair': ['Abilene garden', '360 Market Square high-rise'],
            'garden': archetype(garden, 'garden'),
            'high_rise': archetype(high_rise, 'high-rise'),
            'independently_reviewed_use': independently_reviewed_use,
            'independently_reviewed_down': independently_reviewed_down,
            'four_count_status': 'blocked',
            'status': 'blocked',
            'blockers': blockers,
        }
    except _Failure as exc:
        raise CompatibilityError(exc.args[0]) from None


def _reader(reader_id, container, signature, status, refusals, extra_limits=()):
    limits = list(SHARED_LIMITS)
    limits.extend(extra_limits)
    confidence = 'acquisition_gap' if status == 'acquisition_gap' else 'synthetic_structural'
    return {
        'reader_id': reader_id,
        'reader_version': READER_VERSION,
        'container': container,
        'structural_signature': signature,
        'provenance_confidence': confidence,
        'supported_fields': list(_FLAT_FIELDS),
        'source_test_status': status,
        'limitations': limits,
        'refusal_codes': list(refusals),
    }


def _coverage_row(original_id, container, reader_id, status):
    return {
        'original_id': original_id,
        'accounted': True,
        'source_role': 'synthetic' if status == 'synthetic_only' else 'declared_missing_original',
        'container': container,
        'reader_id': reader_id,
        'status': status,
    }


def _published():
    readers = [
        _reader('pms-flat-yardi', 'csv|xlsx', 'flat_inventory_header', 'synthetic_only', _FLAT_REFUSALS),
        _reader('pms-flat-realpage', 'csv|xlsx', 'flat_inventory_header', 'synthetic_only', _FLAT_REFUSALS),
        _reader('pms-flat-entrata', 'csv|xlsx', 'flat_inventory_header', 'acquisition_gap',
                _FLAT_REFUSALS, ('missing_entrata_originals_acquisition_gap',)),
        _reader('onesite-detailed-realpage', 'xls_biff', 'onesite_detailed_row9', 'synthetic_only',
                ['UNSUPPORTED_ONESITE_LAYOUT', 'UNSUPPORTED_XLS_FORMAT', 'MALFORMED_XLS',
                 'INPUT_LIMIT_EXCEEDED', 'XLS_DEPENDENCY_MISSING']),
        _reader('lease-charge-xlsx', 'xlsx', 'split_header_lease_charge', 'synthetic_only',
                ['MALFORMED_INPUT', 'INPUT_LIMIT_EXCEEDED', 'EMPTY_INPUT',
                 'UNSUPPORTED_XLSX_FORMAT', 'UNSUPPORTED_XLSX_LAYOUT', 'XLSX_DEPENDENCY_MISSING']),
        _reader('onesite-compact', 'xls_biff', 'onesite_compact_g6', 'synthetic_only',
                ['MALFORMED_INPUT', 'INPUT_LIMIT_EXCEEDED', 'UNSUPPORTED_XLS_FORMAT',
                 'UNSUPPORTED_ONESITE_LAYOUT', 'XLS_DEPENDENCY_MISSING']),
        _reader('pdf-rent-roll', 'pdf', 'pdf_split_header_current_notice_vacant', 'synthetic_only',
                ['MALFORMED_INPUT', 'INPUT_LIMIT_EXCEEDED', 'UNSUPPORTED_PDF_LAYOUT',
                 'EMPTY_INPUT', 'PDF_DEPENDENCY_MISSING']),
    ]
    rows = [
        _coverage_row('synthetic-flat-yardi', 'csv|xlsx', 'pms-flat-yardi', 'synthetic_only'),
        _coverage_row('synthetic-flat-realpage', 'csv|xlsx', 'pms-flat-realpage', 'synthetic_only'),
        _coverage_row('entrata-originals', 'csv|xlsx', 'pms-flat-entrata', 'acquisition_gap'),
        _coverage_row('synthetic-onesite-detailed', 'xls_biff', 'onesite-detailed-realpage', 'synthetic_only'),
        _coverage_row('synthetic-lease-charge', 'xlsx', 'lease-charge-xlsx', 'synthetic_only'),
        _coverage_row('synthetic-onesite-compact', 'xls_biff', 'onesite-compact', 'synthetic_only'),
        _coverage_row('synthetic-pdf-rent-roll', 'pdf', 'pdf-rent-roll', 'synthetic_only'),
    ]
    gate = evaluate_two_archetype_gate(
        garden={'label': 'Abilene garden', 'form': 'garden', 'use_evidence': 'absent',
                'down_evidence': 'absent', 'reader_id': 'lease-charge-xlsx'},
        high_rise={'label': '360 Market Square high-rise', 'form': 'high-rise',
                   'use_evidence': 'absent', 'down_evidence': 'absent',
                   'reader_id': 'onesite-compact'},
        independently_reviewed_use=False,
        independently_reviewed_down=False,
        originals_ran=0,
    )
    matrix = {
        'contract_version': CONTRACT_VERSION,
        'readers': readers,
        'coverage': {
            'declared_originals': [row['original_id'] for row in rows],
            'rows': rows,
        },
        'two_archetype_gate': gate,
        'engine_eligibility': 'not_evaluated',
        'item2_milestone': 'not_evaluated',
    }
    matrix['totals'] = recompute_totals(matrix)
    return matrix


_PUBLIC = _published()


def validate_matrix(matrix):
    try:
        _keys(matrix, MATRIX_KEYS)
        _require(matrix['contract_version'] == CONTRACT_VERSION)
        _require(matrix['engine_eligibility'] == 'not_evaluated')
        _require(matrix['item2_milestone'] in ('not_evaluated', 'blocked'))
        _require(type(matrix['readers']) is list and matrix['readers'])
        for row in matrix['readers']:
            _keys(row, READER_KEYS)
            _string(row['reader_id'])
            _require(row['reader_version'] == READER_VERSION)
            _string(row['container'])
            _string(row['structural_signature'])
            _string(row['provenance_confidence'])
            _text_list(row['supported_fields'])
            _string(row['source_test_status'])
            _text_list(row['limitations'])
            _text_list(row['refusal_codes'])
            for token in SHARED_LIMITS:
                _require(token in row['limitations'])
            synthetic = row['source_test_status'] == 'synthetic_only'
            real = (row['source_test_status'] == 'real_export_verified'
                    or row['provenance_confidence'] == 'real_export_verified')
            _require(not (synthetic and real), 'SYNTHETIC_REAL_CLAIM')
            _require(row['source_test_status'] != 'real_export_verified', 'SYNTHETIC_REAL_CLAIM')
            _require(row['provenance_confidence'] != 'real_export_verified', 'SYNTHETIC_REAL_CLAIM')
        coverage = matrix['coverage']
        _keys(coverage, COVERAGE_KEYS)
        _require(type(coverage['declared_originals']) is list)
        _require(all(type(item) is str and item for item in coverage['declared_originals']))
        _require(type(coverage['rows']) is list)
        accounted_ids = []
        for row in coverage['rows']:
            _keys(row, COVERAGE_ROW_KEYS)
            _string(row['original_id'])
            _bool(row['accounted'])
            _string(row['source_role'])
            _string(row['container'])
            _string(row['reader_id'])
            _string(row['status'])
            accounted_ids.append(row['original_id'])
        declared = coverage['declared_originals']
        _require(len(set(declared)) == len(declared))
        missing = [item for item in declared if item not in accounted_ids]
        _require(not missing, 'OMITTED_ORIGINAL')
        gate = matrix['two_archetype_gate']
        _keys(gate, GATE_KEYS)
        _require(type(gate['intended_pair']) is list)
        _require(gate['intended_pair'] == ['Abilene garden', '360 Market Square high-rise'])
        for key, form in (('garden', 'garden'), ('high_rise', 'high-rise')):
            asset = gate[key]
            _keys(asset, ARCHETYPE_KEYS)
            _string(asset['label'])
            _require(asset['form'] == form)
            _string(asset['reader_id'])
            _string(asset['use_evidence'])
            _string(asset['down_evidence'])
            _string(asset['four_count_status'])
        _bool(gate['independently_reviewed_use'])
        _bool(gate['independently_reviewed_down'])
        _string(gate['four_count_status'])
        _string(gate['status'])
        _require(type(gate['blockers']) is list)
        _require(all(type(item) is str and item for item in gate['blockers']))
        _keys(matrix['totals'], TOTAL_KEYS)
        expected = recompute_totals(matrix)
        _require(matrix['totals'] == expected, 'TOTALS_MISMATCH')
        _require(expected['ran'] == 0)
        _require(expected['four_count_reconciled'] == 0)
        _require(expected['real_export_verified'] == 0, 'SYNTHETIC_REAL_CLAIM')
        if expected['ran'] == 0:
            _require(gate['status'] != 'pass', 'ZERO_RAN_SUCCESS')
            _require(matrix['item2_milestone'] != 'accepted', 'ZERO_RAN_SUCCESS')
        evidence = (
            gate['independently_reviewed_use'] is True
            and gate['independently_reviewed_down'] is True
            and gate['garden']['use_evidence'] == 'present'
            and gate['garden']['down_evidence'] == 'present'
            and gate['high_rise']['use_evidence'] == 'present'
            and gate['high_rise']['down_evidence'] == 'present'
            and expected['ran'] > 0
            and expected['four_count_reconciled'] > 0
        )
        if gate['four_count_status'] != 'blocked' or gate['status'] == 'pass':
            _require(evidence, 'FOUR_COUNT_FABRICATED')
        encoded = json.dumps(matrix, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
        sanitize_public(encoded)
        return matrix
    except CompatibilityError:
        raise
    except _Failure as exc:
        raise CompatibilityError(exc.args[0]) from None


def public_matrix():
    return deepcopy(validate_matrix(_PUBLIC))
