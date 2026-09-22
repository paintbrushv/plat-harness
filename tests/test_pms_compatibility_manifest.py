"""Public PMS compatibility matrix and honest two-archetype gate.

Synthetic fixtures only. No original deal-room reads, no PII, no engine.
This suite does not claim Item 2 four-count acceptance.
"""
from __future__ import annotations

import copy
import importlib.util
import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
DOCS = REPO / 'docs' / 'PMS_COMPATIBILITY.md'

PRIVATE_LITERALS = (
    'raw_inputs',
    '/home/',
    '/Users/',
    'resident_name',
    '@gmail',
    '@yahoo',
    '.xlsm',
    '00099 -',
    '00101 -',
)
REQUIRED_READERS = (
    'pms-flat-yardi',
    'pms-flat-realpage',
    'pms-flat-entrata',
    'onesite-detailed-realpage',
    'lease-charge-xlsx',
    'onesite-compact',
    'pdf-rent-roll',
)
REQUIRED_LIMITS = (
    'unknown_use',
    'independent_down_unresolved',
    'csv_only_semantic_resolver',
    'engine_eligibility_not_evaluated',
)
MATRIX_KEYS = (
    'contract_version',
    'readers',
    'coverage',
    'two_archetype_gate',
    'totals',
    'engine_eligibility',
    'item2_milestone',
)
READER_KEYS = (
    'reader_id',
    'reader_version',
    'container',
    'structural_signature',
    'provenance_confidence',
    'supported_fields',
    'source_test_status',
    'limitations',
    'refusal_codes',
)
COVERAGE_KEYS = ('declared_originals', 'rows')
COVERAGE_ROW_KEYS = (
    'original_id',
    'accounted',
    'source_role',
    'container',
    'reader_id',
    'status',
)
GATE_KEYS = (
    'intended_pair',
    'garden',
    'high_rise',
    'independently_reviewed_use',
    'independently_reviewed_down',
    'four_count_status',
    'status',
    'blockers',
)
ARCHETYPE_KEYS = (
    'label',
    'form',
    'reader_id',
    'use_evidence',
    'down_evidence',
    'four_count_status',
)
TOTAL_KEYS = (
    'readers',
    'coverage_declared',
    'coverage_accounted',
    'synthetic_only',
    'acquisition_gaps',
    'real_export_verified',
    'ran',
    'four_count_reconciled',
)


def api():
    assert importlib.util.find_spec('plat_harness.ingest.compatibility') is not None
    from plat_harness.ingest import compatibility
    assert callable(getattr(compatibility, 'public_matrix', None))
    assert callable(getattr(compatibility, 'validate_matrix', None))
    return compatibility


def refuse(fn):
    try:
        return fn()
    except Exception as exc:
        return exc


def matrix():
    return copy.deepcopy(api().public_matrix())


def test_public_matrix_exact_keys_and_closed_contract():
    mod = api()
    value = mod.validate_matrix(mod.public_matrix())
    assert value['contract_version'] == 'pms-compatibility-matrix/1.0.0'
    assert set(value) == set(MATRIX_KEYS)
    assert value['engine_eligibility'] == 'not_evaluated'
    assert value['item2_milestone'] == 'not_evaluated'
    ids = [row['reader_id'] for row in value['readers']]
    assert ids == list(REQUIRED_READERS)
    for row in value['readers']:
        assert set(row) == set(READER_KEYS)
        assert row['reader_version'] == '1.0.0'
        assert type(row['supported_fields']) is list
        assert type(row['refusal_codes']) is list
        assert type(row['limitations']) is list
        for token in REQUIRED_LIMITS:
            assert token in row['limitations']
        assert row['source_test_status'] in ('synthetic_only', 'acquisition_gap')
        assert row['source_test_status'] != 'real_export_verified'
        assert row['provenance_confidence'] != 'real_export_verified'
    assert set(value['coverage']) == set(COVERAGE_KEYS)
    for row in value['coverage']['rows']:
        assert set(row) == set(COVERAGE_ROW_KEYS)
    assert set(value['two_archetype_gate']) == set(GATE_KEYS)
    for key in ('garden', 'high_rise'):
        assert set(value['two_archetype_gate'][key]) == set(ARCHETYPE_KEYS)
    assert set(value['totals']) == set(TOTAL_KEYS)


@pytest.mark.parametrize('target', ['matrix', 'reader', 'coverage', 'coverage_row', 'gate', 'archetype', 'totals'])
def test_extra_keys_refuse(target):
    value = matrix()
    if target == 'matrix':
        value['approved'] = True
    elif target == 'reader':
        value['readers'][0]['vendor_certified'] = True
    elif target == 'coverage':
        value['coverage']['notes'] = 'ok'
    elif target == 'coverage_row':
        value['coverage']['rows'][0]['path'] = '/forbidden'
    elif target == 'gate':
        value['two_archetype_gate']['passed'] = True
    elif target == 'archetype':
        value['two_archetype_gate']['garden']['filename'] = 'secret.xlsx'
    else:
        value['totals']['success'] = 1
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'EXTRA_KEY'


@pytest.mark.parametrize('key', MATRIX_KEYS)
def test_missing_matrix_keys_refuse(key):
    value = matrix()
    del value[key]
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'MISSING_KEY'


@pytest.mark.parametrize('key', READER_KEYS)
def test_missing_reader_keys_refuse(key):
    value = matrix()
    del value['readers'][0][key]
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'MISSING_KEY'


def test_synthetic_only_cannot_claim_real_export_verified():
    value = matrix()
    synthetic = [row for row in value['readers'] if row['source_test_status'] == 'synthetic_only']
    assert synthetic
    synthetic[0]['provenance_confidence'] = 'real_export_verified'
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'SYNTHETIC_REAL_CLAIM'
    value = matrix()
    synthetic = [row for row in value['readers'] if row['source_test_status'] == 'synthetic_only']
    synthetic[0]['source_test_status'] = 'real_export_verified'
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'SYNTHETIC_REAL_CLAIM'


def test_missing_entrata_originals_are_acquisition_gap_not_fabricated_coverage():
    value = matrix()
    entrata = next(row for row in value['readers'] if row['reader_id'] == 'pms-flat-entrata')
    assert entrata['source_test_status'] == 'acquisition_gap'
    assert 'missing_entrata_originals_acquisition_gap' in entrata['limitations']
    assert entrata['provenance_confidence'] != 'real_export_verified'
    rows = [row for row in value['coverage']['rows'] if row['reader_id'] == 'pms-flat-entrata']
    assert rows
    assert all(row['status'] == 'acquisition_gap' for row in rows)
    assert all(row['accounted'] is True for row in rows)
    assert value['totals']['real_export_verified'] == 0
    assert value['totals']['acquisition_gaps'] >= 1


def test_omitted_original_fails_coverage_accounting():
    value = matrix()
    declared = list(value['coverage']['declared_originals'])
    assert declared
    omitted = declared[-1]
    value['coverage']['rows'] = [row for row in value['coverage']['rows'] if row['original_id'] != omitted]
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'OMITTED_ORIGINAL'


def test_unknown_subtype_cannot_be_relabelled_student_housing():
    mod = api()
    row = {'label': 'University Cove', 'subtype': 'unknown'}
    assert mod.validate_subtype(row) == row
    error = refuse(lambda: mod.validate_subtype({'label': 'University Cove', 'subtype': 'student_housing'}))
    assert type(error) is mod.CompatibilityError
    assert error.code == 'STUDENT_HOUSING_RELABEL'
    error = refuse(lambda: mod.validate_subtype({'label': 'synthetic-unknown', 'subtype': 'student housing'}))
    assert type(error) is mod.CompatibilityError
    assert error.code == 'STUDENT_HOUSING_RELABEL'
    text = json.dumps(mod.public_matrix()) + '\n' + DOCS.read_text(encoding='utf-8')
    assert 'student housing' not in text.lower() or 'unverified' in text.lower()
    assert not re.search(r'University Cove.*student', text, re.I)


def test_totals_recomputed_from_rows_not_declared():
    mod = api()
    value = matrix()
    expected = mod.recompute_totals(value)
    assert value['totals'] == expected
    value['totals'] = dict(expected)
    value['totals']['real_export_verified'] = 4
    error = refuse(lambda: mod.validate_matrix(value))
    assert type(error) is mod.CompatibilityError
    assert error.code == 'TOTALS_MISMATCH'
    value = matrix()
    value['totals'] = dict(expected)
    value['totals']['ran'] = 99
    error = refuse(lambda: mod.validate_matrix(value))
    assert type(error) is mod.CompatibilityError
    assert error.code == 'TOTALS_MISMATCH'


def test_zero_ran_is_not_success():
    value = matrix()
    assert value['totals']['ran'] == 0
    assert value['two_archetype_gate']['status'] != 'pass'
    assert value['two_archetype_gate']['status'] == 'blocked'
    assert value['item2_milestone'] != 'accepted'
    value['two_archetype_gate']['status'] = 'pass'
    error = refuse(lambda: api().validate_matrix(value))
    assert type(error) is api().CompatibilityError
    assert error.code == 'ZERO_RAN_SUCCESS'


def test_abilene_and_360_market_square_rows_exist_with_blocked_four_count():
    gate = matrix()['two_archetype_gate']
    assert gate['intended_pair'] == ['Abilene garden', '360 Market Square high-rise']
    assert gate['garden']['label'] == 'Abilene garden'
    assert gate['garden']['form'] == 'garden'
    assert gate['high_rise']['label'] == '360 Market Square high-rise'
    assert gate['high_rise']['form'] == 'high-rise'
    assert gate['independently_reviewed_use'] is False
    assert gate['independently_reviewed_down'] is False
    assert gate['four_count_status'] == 'blocked'
    assert gate['garden']['four_count_status'] == 'blocked'
    assert gate['high_rise']['four_count_status'] == 'blocked'
    assert gate['garden']['use_evidence'] != 'present'
    assert gate['high_rise']['down_evidence'] != 'present'
    assert gate['status'] == 'blocked'
    assert 'MISSING_REVIEWED_USE' in gate['blockers']
    assert 'MISSING_INDEPENDENT_DOWN' in gate['blockers']
    fabricated = matrix()
    fabricated['two_archetype_gate']['four_count_status'] = 'reconciled'
    fabricated['two_archetype_gate']['status'] = 'pass'
    fabricated['two_archetype_gate']['independently_reviewed_use'] = True
    fabricated['two_archetype_gate']['independently_reviewed_down'] = True
    error = refuse(lambda: api().validate_matrix(fabricated))
    assert type(error) is api().CompatibilityError
    assert error.code in ('FOUR_COUNT_FABRICATED', 'ZERO_RAN_SUCCESS')


def test_public_sanitize_has_no_private_filenames_or_pii():
    mod = api()
    blob = json.dumps(mod.public_matrix(), sort_keys=True)
    docs = DOCS.read_text(encoding='utf-8')
    assert docs
    for text in (blob, docs):
        for snippet in PRIVATE_LITERALS:
            assert snippet not in text
        assert 'Item 2 four-count acceptance' not in text
        assert not re.search(r'[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}', text)
    assert callable(getattr(mod, 'sanitize_public', None))
    assert mod.sanitize_public(blob) == blob
    error = refuse(lambda: mod.sanitize_public('see raw_inputs/secret.xlsx'))
    assert type(error) is mod.CompatibilityError
    assert error.code == 'PRIVATE_LITERAL'


def test_honest_adapter_limits_and_csv_only_resolver():
    value = matrix()
    by_id = {row['reader_id']: row for row in value['readers']}
    assert by_id['pms-flat-yardi']['container'] == 'csv|xlsx'
    assert by_id['pms-flat-realpage']['container'] == 'csv|xlsx'
    assert by_id['pms-flat-entrata']['container'] == 'csv|xlsx'
    assert by_id['onesite-detailed-realpage']['container'] == 'xls_biff'
    assert by_id['lease-charge-xlsx']['container'] == 'xlsx'
    assert by_id['onesite-compact']['container'] == 'xls_biff'
    assert by_id['pdf-rent-roll']['container'] == 'pdf'
    assert all(row['engine_eligibility'] == 'not_evaluated' for row in (
        {'engine_eligibility': value['engine_eligibility']},
    ))
    for row in value['readers']:
        assert 'csv_only_semantic_resolver' in row['limitations']
        assert type(row['structural_signature']) is str and row['structural_signature']
        assert type(row['refusal_codes']) is list and row['refusal_codes']


def test_gate_stays_blocked_without_reviewed_use_and_down_evidence():
    mod = api()
    gate = mod.evaluate_two_archetype_gate(
        garden={'label': 'Abilene garden', 'form': 'garden', 'use_evidence': 'absent',
                'down_evidence': 'absent', 'reader_id': 'lease-charge-xlsx'},
        high_rise={'label': '360 Market Square high-rise', 'form': 'high-rise',
                   'use_evidence': 'absent', 'down_evidence': 'absent',
                   'reader_id': 'onesite-compact'},
        independently_reviewed_use=False,
        independently_reviewed_down=False,
    )
    assert gate['status'] == 'blocked'
    assert gate['four_count_status'] == 'blocked'
    still = mod.evaluate_two_archetype_gate(
        garden={'label': 'Abilene garden', 'form': 'garden', 'use_evidence': 'present',
                'down_evidence': 'present', 'reader_id': 'lease-charge-xlsx'},
        high_rise={'label': '360 Market Square high-rise', 'form': 'high-rise',
                   'use_evidence': 'present', 'down_evidence': 'present',
                   'reader_id': 'onesite-compact'},
        independently_reviewed_use=True,
        independently_reviewed_down=True,
        originals_ran=0,
    )
    assert still['status'] == 'blocked'
