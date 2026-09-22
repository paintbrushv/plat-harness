"""Public synthetic tests for canonical engine-intake translation.

No engine execution, no models, no network, no real deal bytes. Fixtures are
synthetic; canaries must never surface in results, warnings or exceptions.
"""
import copy
import hashlib
import importlib.util
import inspect
import json
import re
import traceback

import pytest

SUBJECT = 'subject_demo'
AS_OF = '2026-01-31'
POLICY = 'policy-2026-01'
OCC_SOURCE = 'src_' + '1' * 32
ACC_SOURCE = 'src_' + '2' * 32
OCC_SHA = hashlib.sha256(b'public synthetic occupancy source').hexdigest()
ACC_SHA = hashlib.sha256(b'public synthetic accounting source').hexdigest()
UNSET = object()  # sentinel: parameter explicitly absent, distinct from None
BASIS = {
    'unit_rent': 'unit', 'charge_rent': 'charge', 'report_total': 'report',
    'deposit': 'deposit', 'concession': 'concession', 'arrears': 'arrears',
    'scheduled_charge': 'scheduled_charge', 'collected_cash': 'collected_cash',
}


def _enc(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def api():
    from plat_harness.adapters import canonical_intake as module
    assert callable(getattr(module, 'translate_intake', None))
    return module


# ---------------------------------------------------------------- fixtures

def unit_cite(row, column=2):
    return {'source_id': OCC_SOURCE, 'source_sha256': OCC_SHA,
            'sheet': 1, 'row': row, 'row_end': row, 'column': column}


def make_unit(row, status='occupied', unit_type='residential'):
    unit_id = unit_cite(row, 2)
    fields = [{'unit_id': unit_id, 'status': unit_cite(row, 3)}]
    if unit_type is not None:
        fields[0]['unit_type'] = unit_cite(row, 4)
    return {
        'observation_id': 'unit_' + hashlib.sha256(_enc(unit_id)).hexdigest(),
        'source_id': OCC_SOURCE, 'unit_type': unit_type, 'status': status,
        'evidence': fields,
    }


def occupancy_envelope(units=None, unknown=None, issues=None,
                       completeness=None, status=None,
                       subject_id=SUBJECT, as_of=AS_OF):
    units = units if units is not None else [make_unit(2), make_unit(3)]
    unknown = unknown or []
    issues = issues or []
    if completeness is None:
        res = [u for u in units if u['unit_type'] == 'residential']
        counts = {'occupied': 0, 'vacant': 0, 'down': 0}
        for unit in res:
            counts[unit['status']] = counts.get(unit['status'], 0) + 1
        completeness = {
            'residential': {'enumeration': 'complete', 'coverage': 'established',
                            'coverage_citations': [unit_cite(2, 2)],
                            'counts': {**counts, 'total': len(res)}},
            'commercial': {'enumeration': 'unknown', 'coverage': 'unknown',
                           'coverage_citations': [],
                           'counts': {f: None for f in ('occupied', 'vacant', 'down', 'total')}},
        }
    everyone = units + unknown
    forced = (not everyone or bool(unknown)
              or any(u['status'] is None for u in everyone)
              or any(i['severity'] == 'blocker' for i in issues))
    status = status or ('blocked' if forced else 'observed_unvalidated')
    return {
        'contract_version': 'ingest-observation/2.0.0',
        'subject_id': subject_id, 'as_of': as_of,
        'adapter': {'id': 'synthetic-occupancy', 'version': '1.0.0'},
        'sources': [{'source_id': OCC_SOURCE, 'sha256': OCC_SHA, 'role': 'original',
                     'original_source_ids': [], 'subject_id': subject_id, 'as_of': as_of}],
        'units': units, 'unknown_use_units': unknown, 'summaries': [],
        'issues': issues, 'completeness': completeness, 'status': status,
    }


def null_count_completeness():
    empty = {f: None for f in ('occupied', 'vacant', 'down', 'total')}
    return {
        'residential': {'enumeration': 'unknown', 'coverage': 'unknown',
                        'coverage_citations': [], 'counts': dict(empty)},
        'commercial': {'enumeration': 'unknown', 'coverage': 'unknown',
                       'coverage_citations': [], 'counts': dict(empty)},
    }


def unresolved_use_envelope():
    unknown_unit = make_unit(4, unit_type=None)
    issue = {
        'issue_id': 'iss_' + hashlib.sha256(b'synthetic unknown use').hexdigest()[:32],
        'code': 'UNRESOLVED_UNIT_USE', 'severity': 'blocker',
        'citation': unit_cite(4, 2),
        'observation_ids': [unknown_unit['observation_id']], 'summary_ids': [],
    }
    counts = {'occupied': 2, 'vacant': 0, 'down': 0, 'total': 2}
    completeness = {
        'residential': {'enumeration': 'partial', 'coverage': 'unknown',
                        'coverage_citations': [], 'counts': counts},
        'commercial': {'enumeration': 'unknown', 'coverage': 'unknown',
                       'coverage_citations': [],
                       'counts': {f: None for f in ('occupied', 'vacant', 'down', 'total')}},
    }
    return occupancy_envelope(units=[make_unit(2), make_unit(3)],
                              unknown=[unknown_unit], issues=[issue],
                              completeness=completeness)


def money_obs(kind='unit_rent', source_text='1450.00', decimal='1450.00',
              period='month', row=2, column=4):
    cite = {'source_id': ACC_SOURCE, 'source_sha256': ACC_SHA,
            'sheet': 1, 'row': row, 'row_end': row, 'column': column}
    identity = 'mny_' + hashlib.sha256(_enc(cite)).hexdigest()
    amount = None if source_text is None else {
        'decimal': decimal, 'source_text': source_text, 'currency': 'USD',
        'unit': 'currency', 'period': period,
    }
    return {'observation_id': identity, 'source_id': ACC_SOURCE, 'kind': kind,
            'measurement_basis': BASIS[kind], 'amount': amount,
            'cell_origin': 'typed', 'citation': cite}


def accounting_envelope(observations, subject_id=SUBJECT, as_of=AS_OF):
    status = ('blocked' if any(item['amount'] is None for item in observations)
              else 'observed_unvalidated')
    return {
        'contract_version': 'ingest-accounting/1.0.0',
        'subject_id': subject_id, 'as_of': as_of,
        'adapter': {'id': 'synthetic-accounting', 'version': '1.0.0'},
        'sources': [{'source_id': ACC_SOURCE, 'sha256': ACC_SHA, 'role': 'original',
                     'original_source_ids': [], 'subject_id': subject_id,
                     'as_of': as_of}],
        'observations': observations, 'issues': [], 'status': status,
    }


def base_observations(with_target=False, extra=None):
    items = [
        money_obs('unit_rent', '1450.00', '1450.00', row=2, column=4),
        money_obs('unit_rent', '1500.00', '1500.00', row=3, column=4),
    ]
    if with_target:
        items.append(money_obs('unit_rent', '1600.00', '1600.00', row=4, column=4))
    if extra:
        items.extend(extra)
    return items


def evidence(provider=('provider_a', 'model_x'), observations=None,
             with_target=False, commercial=None, other=None, curves=None,
             occupancy=None, cohorts=UNSET, accounting=None, **overrides):
    obs = observations if observations is not None else base_observations(with_target)
    default_curves = {
        'loss_to_lease': [{'cohort_id': 'res_1x', 'ltl_percent': '0.02'}],
        'physical_vacancy': [{'cohort_id': 'res_1x', 'vacancy_rate': '0.04'}],
        'collection_loss': [{'applies_to': 'Rent', 'loss_rate': '0.01'}],
    }
    default_occupancy = occupancy_envelope()
    cohorts_default = [{
        'cohort_id': 'res_1x', 'unit_type': 'residential', 'unit_count': 2,
        'inplace_rent_observation_id': obs[0]['observation_id'],
        'market_rent_observation_id': obs[1]['observation_id'],
        'target_rent': (
            {'observation_id': obs[2]['observation_id'],
             'authorization': {'authorized': True,
                               'authority_ref': 'synthetic-review-001'}}
            if with_target else None),
    }]
    value = {
        'contract_version': 'ingest-canonical-intake/1.0.0',
        'subject_id': SUBJECT, 'as_of': AS_OF, 'policy_version': POLICY,
        'provider': {'provider_id': provider[0], 'model_id': provider[1]},
        'analysis_window': {'analysis_start_date': '2026-02',
                            'analysis_end_date': '2027-01'},
        'lease_basis': 'monthly_rent',
        'run_id': 'run_001', 'analyst': 'synthetic_analyst', 'purpose': 'screening',
        'occupancy': (default_occupancy if occupancy is None
                      else (None if occupancy is UNSET else occupancy)),
        'accounting': (accounting_envelope(obs) if accounting is None
                       else (None if accounting is UNSET else accounting)),
        'cohorts': (cohorts if cohorts is not UNSET else cohorts_default),
        'curves': (default_curves if curves is None
                   else (None if curves is UNSET else curves)),
        'millage': {'millage_rate_mills': '20.5', 'assessment_ratio': '0.85',
                    'source': 'synthetic_county_schedule',
                    'source_locator': 'synthetic/2026/millage/schedule-line-12',
                    'analyst_override': False},
        'commercial_income': commercial if commercial is not None else {
            'status': 'omitted', 'observation_id': None,
            'omission_explanation': 'no commercial units in the synthetic inventory',
            'canonical_program': None},
        'other_income': other if other is not None else {
            'status': 'omitted', 'observation_id': None,
            'omission_explanation': 'no recurring other income lines in the synthetic evidence',
            'canonical_program': None},
    }
    value.update(overrides)
    return value


def translate(value, **kwargs):
    module = api()
    return module.translate_intake(
        value, subject_id=kwargs.get('subject_id', SUBJECT),
        as_of=kwargs.get('as_of', AS_OF),
        policy_version=kwargs.get('policy_version', POLICY))


def refuse(value, code=None, **kwargs):
    module = api()
    with pytest.raises(module.CanonicalIntakeError) as caught:
        translate(value, **kwargs)
    assert caught.value.code in module.ERROR_CODES
    if code is not None:
        assert caught.value.code == code
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))
    return caught.value.code


def blocked(value, **kwargs):
    module = api()
    result = translate(value, **kwargs)
    assert result['status'] == 'blocked'
    assert result['canonical'] is None and result['canonical_json'] is None
    assert result['canonical_sha256'] is None and result['draft'] is not None
    assert result['blockers']
    for entry in result['blockers']:
        assert set(entry) == {'code', 'field', 'detail'}
        assert entry['code'] in module.BLOCKER_CODES
        assert type(entry['field']) is str and type(entry['detail']) is str
    return result


def codes(result):
    return [entry['code'] for entry in result['blockers']]


def fields(result):
    return [entry['field'] for entry in result['blockers']]


def walk(value):
    stack = [value]
    while stack:
        item = stack.pop()
        yield item
        if type(item) is dict:
            stack.extend(item.values())
        elif type(item) is list:
            stack.extend(item)


EXPECTED_CANONICAL = {
    'schema_version': '0.1',
    'metadata': {
        'deal_id': 'subject_demo', 'run_id': 'run_001', 'as_of_date': '2026-01-31',
        'analyst': 'synthetic_analyst', 'purpose': 'screening',
        'property_summary': {'property_tax_policy': {
            'millage_rate_mills': '20.5', 'assessment_ratio': '0.85',
            'source': 'synthetic_county_schedule',
            'source_locator': 'synthetic/2026/millage/schedule-line-12',
            'analyst_override': False}},
    },
    'time_grid': {'analysis_start_date': '2026-02', 'analysis_end_date': '2027-01'},
    'unit_cohorts': [{'cohort_id': 'res_1x', 'unit_type': 'residential',
                      'unit_count': 2, 'initial_inplace_rent': '1450.00'}],
    'market_rent_curve': [{'cohort_id': 'res_1x', 'start_period': '2026-02',
                           'end_period': '2027-01', 'market_rent': '1500.00'}],
    'loss_to_lease': [{'cohort_id': 'res_1x', 'start_period': '2026-02',
                       'end_period': '2027-01', 'ltl_percent': '0.02'}],
    'physical_vacancy_curve': [{'cohort_id': 'res_1x', 'start_period': '2026-02',
                                'end_period': '2027-01', 'vacancy_rate': '0.04'}],
    'collection_loss_curve': [{'applies_to': 'Rent', 'start_period': '2026-02',
                               'end_period': '2027-01', 'loss_rate': '0.01'}],
    'revenue_programs': [],
    'program_adoption_curve': [],
}

ENGINE_KEYS = {
    'top': {'schema_version', 'metadata', 'time_grid', 'unit_cohorts',
            'market_rent_curve', 'loss_to_lease', 'physical_vacancy_curve',
            'collection_loss_curve', 'revenue_programs', 'program_adoption_curve',
            'program_capacity', 'program_costs', 'utility_recovery_rules',
            'opex_table', 'turnover_assumptions', 'capex_schedule', 'debt_terms',
            'additional_debt_terms', 'capital_stack', 'debt_draw_schedule',
            'exit_assumptions', 'purchase_assumptions', 'pricing_provenance',
            'renovation_programs', 'unit_renovations', 'renovation_detail',
            'growth_assumptions', 'ltl_decay_assumptions', 'fund_assumptions',
            'replacement_reserves', 'capex_upfront_funded', 'refi_event',
            'concession_schedule', 'trade_out_assumptions', 'options'},
    'metadata': {'deal_id', 'run_id', 'as_of_date', 'analyst', 'purpose', 'notes',
                 'property_summary', 'address', 'market', 'year_built',
                 'om_extraction_confidence', 'intake_sanity_flags',
                 'purchase_assumptions_source', 'analyst_review_required'},
    'property_summary': {'property_tax_policy'},
    'property_tax_policy': {'millage_rate_mills', 'assessment_ratio', 'source',
                            'source_locator', 'analyst_override'},
    'time_grid': {'analysis_start_date', 'analysis_end_date'},
    'cohort': {'cohort_id', 'unit_type', 'unit_count', 'sqft',
               'initial_inplace_rent', 'bedrooms', 'bathrooms',
               'target_monthly_rent', 'target_monthly_rent_source'},
    'market_row': {'cohort_id', 'start_period', 'end_period', 'market_rent',
                   'target_monthly_rent_source'},
    'ltl_row': {'cohort_id', 'start_period', 'end_period', 'ltl_percent'},
    'vacancy_row': {'cohort_id', 'start_period', 'end_period', 'vacancy_rate'},
    'collection_row': {'applies_to', 'start_period', 'end_period', 'loss_rate'},
    'program_row': {'program_id', 'program_name', 'program_type', 'pricing_type',
                    'price_value', 'eligible_units', 'start_period', 'end_period'},
    'adoption_row': {'program_id', 'start_period', 'end_period', 'adoption_rate'},
}


# ---------------------------------------------------------------- seam + contract

def test_canonical_intake_seam_exists():
    assert importlib.util.find_spec('plat_harness.adapters.canonical_intake')


def test_versioned_contract_name_and_closed_code_sets():
    module = api()
    assert module.CONTRACT_VERSION == 'ingest-canonical-intake/1.0.0'
    assert isinstance(module.ERROR_CODES, frozenset)
    assert {'INVALID_INPUT', 'SCOPE_MISMATCH', 'UNSUPPORTED_LEASE_BASIS',
            'UNAUTHORIZED_TARGET_RENT', 'AMBIGUOUS_NUMERIC_FORMAT'} <= module.ERROR_CODES
    assert isinstance(module.BLOCKER_CODES, frozenset)
    assert {'ANALYSIS_WINDOW_INCOMPLETE', 'OCCUPANCY_COUNTS_INCOMPLETE',
            'UNRESOLVED_UNIT_USE', 'MILLAGE_MISSING', 'INPLACE_RENT_MISSING',
            'MARKET_RENT_MISSING', 'COMMERCIAL_INCOME_OMISSION_UNEXPLAINED',
            'OTHER_INCOME_OMISSION_UNEXPLAINED', 'MISSING_INPUT'} <= module.BLOCKER_CODES
    assert module.SUPPORTED_LEASE_BASIS == frozenset(('monthly_rent',))
    assert 'engine.modules.util.dec' in module.ENGINE_NUMERIC_PATH
    assert module.ENGINE_SCHEMA_VERSION == '0.1'


# ---------------------------------------------------------------- eligible translation

def test_eligible_translation_is_exact_engine_shaped_dict():
    result = translate(evidence())
    assert result['status'] == 'eligible'
    assert result['canonical'] == EXPECTED_CANONICAL
    encoded = json.dumps(EXPECTED_CANONICAL, sort_keys=True, separators=(',', ':'),
                         ensure_ascii=False)
    assert result['canonical_json'] == encoded
    assert result['canonical_sha256'] == hashlib.sha256(encoded.encode('utf-8')).hexdigest()
    assert result['draft'] is None and result['blockers'] == []


def test_canonical_keys_stay_inside_the_strict_engine_schema():
    result = translate(evidence())
    canonical = result['canonical']
    assert set(canonical) <= ENGINE_KEYS['top']
    assert set(canonical['metadata']) <= ENGINE_KEYS['metadata']
    assert set(canonical['metadata']['property_summary']) <= ENGINE_KEYS['property_summary']
    assert set(canonical['metadata']['property_summary']['property_tax_policy']) \
        <= ENGINE_KEYS['property_tax_policy']
    assert set(canonical['time_grid']) <= ENGINE_KEYS['time_grid']
    for row in canonical['unit_cohorts']:
        assert set(row) <= ENGINE_KEYS['cohort']
    for row in canonical['market_rent_curve']:
        assert set(row) <= ENGINE_KEYS['market_row']
    for row in canonical['loss_to_lease']:
        assert set(row) <= ENGINE_KEYS['ltl_row']
    for row in canonical['physical_vacancy_curve']:
        assert set(row) <= ENGINE_KEYS['vacancy_row']
    for row in canonical['collection_loss_curve']:
        assert set(row) <= ENGINE_KEYS['collection_row']
    for row in canonical['revenue_programs']:
        assert set(row) <= ENGINE_KEYS['program_row']
    for row in canonical['program_adoption_curve']:
        assert set(row) <= ENGINE_KEYS['adoption_row']


def test_money_is_exact_decimal_string_and_counts_are_ints():
    result = translate(evidence())
    cohort = result['canonical']['unit_cohorts'][0]
    assert type(cohort['initial_inplace_rent']) is str
    assert cohort['initial_inplace_rent'] == '1450.00'
    assert type(cohort['unit_count']) is int and cohort['unit_count'] == 2
    assert type(result['canonical']['market_rent_curve'][0]['market_rent']) is str
    assert not any(type(item) is float for item in walk(result['canonical']))


# ---------------------------------------------------------------- RED 1: scope mismatches refuse

def test_subject_scope_mismatch_refuses():
    assert refuse(evidence(), code='SCOPE_MISMATCH', subject_id='other_subject') \
        == 'SCOPE_MISMATCH'


def test_as_of_scope_mismatch_refuses():
    assert refuse(evidence(), code='SCOPE_MISMATCH', as_of='2026-02-28') \
        == 'SCOPE_MISMATCH'


def test_policy_version_mismatch_refuses():
    assert refuse(evidence(), code='SCOPE_MISMATCH',
                 policy_version='policy-2026-02') == 'SCOPE_MISMATCH'


def test_nested_observation_envelope_scope_mismatch_refuses():
    value = evidence()
    value['occupancy']['subject_id'] = value['occupancy']['sources'][0]['subject_id'] \
        = 'other_subject'
    assert refuse(value) == 'SCOPE_MISMATCH'
    value = evidence()
    value['accounting']['as_of'] = value['accounting']['sources'][0]['as_of'] \
        = '2026-02-28'
    assert refuse(value) == 'SCOPE_MISMATCH'


# ---------------------------------------------------------------- RED 2: window blocks

def test_incomplete_analysis_window_blocks_with_exact_fields():
    value = evidence(analysis_window={'analysis_start_date': '2026-02',
                                      'analysis_end_date': None})
    result = blocked(value)
    assert 'ANALYSIS_WINDOW_INCOMPLETE' in codes(result)
    assert '/time_grid/analysis_end_date' in fields(result)
    assert result['draft']['time_grid']['analysis_end_date'] is None
    assert result['draft']['time_grid']['analysis_start_date'] == '2026-02'


def test_reversed_analysis_window_blocks():
    result = blocked(evidence(analysis_window={'analysis_start_date': '2027-01',
                                               'analysis_end_date': '2026-02'}))
    assert 'ANALYSIS_WINDOW_INCOMPLETE' in codes(result)


def test_absent_window_names_both_fields():
    result = blocked(evidence(analysis_window=None))
    assert 'ANALYSIS_WINDOW_INCOMPLETE' in codes(result)
    assert '/time_grid/analysis_start_date' in fields(result)
    assert '/time_grid/analysis_end_date' in fields(result)


# ---------------------------------------------------------------- RED 3: occupancy + lease basis

def test_null_occupancy_counts_block():
    result = blocked(evidence(occupancy=occupancy_envelope(
        completeness=null_count_completeness())))
    assert 'OCCUPANCY_COUNTS_INCOMPLETE' in codes(result)
    assert 'occupancy.completeness.residential' in fields(result)


def test_unresolved_unit_use_blocks():
    result = blocked(evidence(occupancy=unresolved_use_envelope()))
    assert 'UNRESOLVED_UNIT_USE' in codes(result)
    assert 'occupancy.unknown_use_units' in fields(result)


def test_missing_occupancy_section_blocks():
    result = blocked(evidence(occupancy=UNSET))
    assert 'MISSING_INPUT' in codes(result) and 'occupancy' in fields(result)


@pytest.mark.parametrize('basis', ['annual_rent', 'weekly_rent', 'daily_rent', ''])
def test_unsupported_lease_basis_refuses(basis):
    assert refuse(evidence(lease_basis=basis)) == 'UNSUPPORTED_LEASE_BASIS'


def test_yearly_rent_observation_cited_as_monthly_refuses():
    yearly = money_obs('unit_rent', '17400.00', '17400.00', period='year',
                       row=2, column=4)
    value = evidence(observations=[yearly, base_observations()[1]])
    assert refuse(value) == 'UNSUPPORTED_LEASE_BASIS'


# ---------------------------------------------------------------- RED 4: millage + target rent

def test_missing_millage_blocks_valuation_path():
    result = blocked(evidence(millage=None))
    assert 'MILLAGE_MISSING' in codes(result)
    assert '/metadata/property_summary/property_tax_policy' in fields(result)
    assert result['draft']['metadata']['property_summary']['property_tax_policy'] is None
    assert 'millage' not in json.dumps(result['lineage']).lower()


def test_unauthorized_target_rent_refuses():
    value = evidence(with_target=True)
    value['cohorts'][0]['target_rent']['authorization'] = {
        'authorized': False, 'authority_ref': 'synthetic-review-001'}
    assert refuse(value) == 'UNAUTHORIZED_TARGET_RENT'
    value = evidence(with_target=True)
    value['cohorts'][0]['target_rent']['authorization'] = {'authorized': True}
    assert refuse(value) == 'UNAUTHORIZED_TARGET_RENT'
    value = evidence(with_target=True)
    value['cohorts'][0]['target_rent']['authorization'] = None
    assert refuse(value) == 'UNAUTHORIZED_TARGET_RENT'


def test_authorized_target_rent_translates_with_explicit_override_source():
    result = translate(evidence(with_target=True))
    assert result['status'] == 'eligible'
    cohort = result['canonical']['unit_cohorts'][0]
    assert cohort['target_monthly_rent'] == '1600.00'
    assert cohort['target_monthly_rent_source'] == 'analyst_override'


# ---------------------------------------------------------------- RED 5: income omissions

def test_unexplained_commercial_income_omission_is_a_blocker():
    value = evidence(commercial={'status': 'omitted', 'observation_id': None,
                                 'omission_explanation': None,
                                 'canonical_program': None})
    result = blocked(value)
    assert 'COMMERCIAL_INCOME_OMISSION_UNEXPLAINED' in codes(result)
    assert 'commercial_income' in fields(result)


def test_unexplained_other_income_omission_is_a_blocker():
    value = evidence(other={'status': 'omitted', 'observation_id': None,
                            'omission_explanation': None, 'canonical_program': None})
    result = blocked(value)
    assert 'OTHER_INCOME_OMISSION_UNEXPLAINED' in codes(result)
    assert 'other_income' in fields(result)


def test_explained_omissions_produce_no_silent_zeros():
    result = translate(evidence())
    assert result['status'] == 'eligible'
    assert result['canonical']['revenue_programs'] == []
    value = evidence()
    assert result['lineage']['omission_explanations']['commercial_income'] \
        == 'no commercial units in the synthetic inventory'
    assert result['lineage']['omission_explanations']['other_income'] \
        == 'no recurring other income lines in the synthetic evidence'


def present_commercial_evidence(price='450.00'):
    income = money_obs('report_total', price, price, row=5, column=4)
    return evidence(
        observations=base_observations() + [income],
        commercial={'status': 'present', 'observation_id': income['observation_id'],
                    'omission_explanation': None,
                    'canonical_program': {
                        'program_id': 'commercial_income_res',
                        'program_name': 'cited commercial income (synthetic)',
                        'program_type': 'asset-based', 'pricing_type': '$/asset',
                        'price_value': price, 'eligible_units': 'ALL',
                        'start_period': '2026-02', 'end_period': '2027-01',
                        'adoption_rate': '1.00'}},
        curves={'loss_to_lease': [{'cohort_id': 'res_1x', 'ltl_percent': '0.02'}],
                'physical_vacancy': [{'cohort_id': 'res_1x', 'vacancy_rate': '0.04'}],
                'collection_loss': [{'applies_to': 'Rent', 'loss_rate': '0.01'},
                                    {'applies_to': 'Programs', 'loss_rate': '0.01'}]})


def test_present_commercial_income_becomes_a_cited_revenue_program():
    result = translate(present_commercial_evidence())
    assert result['status'] == 'eligible'
    programs = result['canonical']['revenue_programs']
    assert len(programs) == 1
    assert programs[0] == {'program_id': 'commercial_income_res',
                           'program_name': 'cited commercial income (synthetic)',
                           'program_type': 'asset-based', 'pricing_type': '$/asset',
                           'price_value': '450.00', 'eligible_units': 'ALL',
                           'start_period': '2026-02', 'end_period': '2027-01'}
    assert result['canonical']['program_adoption_curve'] == [{
        'program_id': 'commercial_income_res', 'start_period': '2026-02',
        'end_period': '2027-01', 'adoption_rate': '1.00'}]


def test_present_income_price_mismatch_with_citation_refuses():
    value = present_commercial_evidence()
    value['commercial_income']['canonical_program']['price_value'] = '999.00'
    assert refuse(value) == 'INVALID_INPUT'


def test_present_income_with_null_amount_blocks_never_zero():
    income = money_obs('report_total', None, None, row=5, column=4)
    value = evidence(observations=base_observations() + [income],
                     commercial={'status': 'present',
                                 'observation_id': income['observation_id'],
                                 'omission_explanation': None,
                                 'canonical_program': {
                                     'program_id': 'commercial_income_res',
                                     'program_name': 'cited commercial income (synthetic)',
                                     'program_type': 'asset-based',
                                     'pricing_type': '$/asset', 'price_value': '450.00',
                                     'eligible_units': 'ALL', 'start_period': '2026-02',
                                     'end_period': '2027-01', 'adoption_rate': '1.00'}},
                     curves={'loss_to_lease': [{'cohort_id': 'res_1x', 'ltl_percent': '0.02'}],
                             'physical_vacancy': [{'cohort_id': 'res_1x', 'vacancy_rate': '0.04'}],
                             'collection_loss': [{'applies_to': 'Rent', 'loss_rate': '0.01'},
                                                 {'applies_to': 'Programs', 'loss_rate': '0.01'}]})
    result = blocked(value)
    assert 'MISSING_INPUT' in codes(result)
    assert '/revenue_programs[0]/price_value' in fields(result)
    assert result['draft']['revenue_programs'][0]['price_value'] is None


def test_programs_without_programs_collection_loss_coverage_block():
    value = present_commercial_evidence()
    value['curves']['collection_loss'] = [{'applies_to': 'Rent', 'loss_rate': '0.01'}]
    result = blocked(value)
    assert 'MISSING_INPUT' in codes(result)
    assert '/collection_loss_curve' in fields(result)


# ---------------------------------------------------------------- RED 6: provider metadata

def test_provider_metadata_cannot_alter_canonical_bytes():
    first = translate(evidence(provider=('provider_a', 'model_x')))
    second = translate(evidence(provider=('provider_b', 'model_y')))
    assert first['status'] == second['status'] == 'eligible'
    assert first['canonical_json'] == second['canonical_json']
    assert first['canonical_sha256'] == second['canonical_sha256']
    assert first['canonical'] == second['canonical']
    assert first['lineage']['provider'] != second['lineage']['provider']
    assert first['lineage']['provider'] == {'provider_id': 'provider_a', 'model_id': 'model_x'}


def test_provider_cannot_smuggle_evidence_values():
    value = evidence()
    value['provider']['market_rent'] = '9999.00'
    assert refuse(value) == 'INVALID_INPUT'


def test_no_provider_or_provenance_keys_inside_canonical():
    result = translate(evidence())
    body = result['canonical_json']
    for token in ('provider_id', 'model_id', 'lineage', 'policy_version',
                  'source_lineage', 'field_lineage'):
        assert token not in body
    for item in walk(result['canonical']):
        if type(item) is dict:
            assert not any(key.endswith('_provenance') for key in item)


# ---------------------------------------------------------------- RED 7: lineage sidecar

def test_lineage_sidecar_is_separate_and_keyed_to_canonical_hash():
    result = translate(evidence())
    lineage = result['lineage']
    assert set(lineage) == {'contract_version', 'status', 'canonical_sha256',
                            'subject_id', 'as_of', 'policy_version', 'provider',
                            'evidence_sha256', 'field_lineage',
                            'omission_explanations'}
    assert lineage['contract_version'] == 'ingest-canonical-intake/1.0.0'
    assert lineage['canonical_sha256'] == result['canonical_sha256']
    assert lineage['status'] == 'eligible'
    assert lineage['subject_id'] == SUBJECT and lineage['as_of'] == AS_OF
    assert lineage['policy_version'] == POLICY
    paths = {entry['canonical_path'] for entry in lineage['field_lineage']}
    assert '/unit_cohorts[0]/initial_inplace_rent' in paths
    assert '/market_rent_curve[0]/market_rent' in paths
    for entry in lineage['field_lineage']:
        assert set(entry) == {'canonical_path', 'observation_id', 'source_id',
                              'source_sha256', 'decimal'}
        assert entry['observation_id'].startswith('mny_')
        assert entry['source_id'] == ACC_SOURCE
        assert entry['source_sha256'] == ACC_SHA
    assert set(lineage['evidence_sha256']) == {'occupancy', 'accounting'}
    assert all(isinstance(v, str) and len(v) == 64
               for v in lineage['evidence_sha256'].values())


def test_blocked_lineage_binds_evidence_without_canonical_hash():
    result = blocked(evidence(millage=None))
    assert result['lineage']['canonical_sha256'] is None
    assert result['lineage']['status'] == 'blocked'
    assert result['lineage']['evidence_sha256']['occupancy']
    assert result['lineage']['evidence_sha256']['accounting']


# ---------------------------------------------------------------- RED 8: zero math

def test_no_financial_math_anywhere_in_the_adapter():
    module = api()
    source = inspect.getsource(module)
    assert 'Decimal(' not in source
    assert 'float(' not in source
    assert re.search(r'\bsum\(', source) is None
    assert 'annualiz' not in source
    assert '* 12' not in source and '*12' not in source
    for name in ('sum', 'total', 'aggregate', 'noi', 'cap_rate', 'dscr', 'rate'):
        assert not callable(getattr(module, name, None))
    assert 'engine.modules.util.dec' in module.ENGINE_NUMERIC_PATH


def test_rent_passthrough_is_exact_never_converted():
    result = translate(evidence())
    assert result['canonical']['unit_cohorts'][0]['initial_inplace_rent'] == '1450.00'
    assert result['canonical']['market_rent_curve'][0]['market_rent'] == '1500.00'
    assert result['canonical']['unit_cohorts'][0]['unit_count'] == 2


# ---------------------------------------------------------------- RED 9: numeric formats

def test_ambiguous_source_text_refuses_through_accounting_import():
    ambiguous = money_obs('unit_rent', '1.234', '1.234', row=2, column=4)
    value = evidence(observations=[ambiguous, base_observations()[1]])
    assert refuse(value) == 'AMBIGUOUS_NUMERIC_FORMAT'


@pytest.mark.parametrize('bad', ['1,234', '1.234.000', '12,5', '20.5%', '0x10', ' 20.5'])
def test_ambiguous_rate_and_millage_strings_refuse(bad):
    value = evidence()
    value['curves']['loss_to_lease'][0]['ltl_percent'] = bad
    assert refuse(value) == 'AMBIGUOUS_NUMERIC_FORMAT'
    value = evidence()
    value['millage']['millage_rate_mills'] = bad
    assert refuse(value) == 'AMBIGUOUS_NUMERIC_FORMAT'


def test_rate_values_are_carried_as_decimal_strings():
    result = translate(evidence())
    assert result['canonical']['loss_to_lease'][0]['ltl_percent'] == '0.02'
    assert type(result['canonical']['loss_to_lease'][0]['ltl_percent']) is str
    assert result['canonical']['metadata']['property_summary']['property_tax_policy'][
        'millage_rate_mills'] == '20.5'


# ---------------------------------------------------------------- RED 10: actionable missing inputs

def test_missing_metadata_inputs_name_exactly_what_is_missing():
    value = evidence(run_id=None, analyst=None, purpose=None)
    result = blocked(value)
    assert 'MISSING_INPUT' in codes(result)
    for field in ('metadata.run_id', 'metadata.analyst', 'metadata.purpose'):
        assert field in fields(result)
    assert result['draft']['metadata']['run_id'] is None


def test_missing_accounting_section_names_itself():
    result = blocked(evidence(accounting=UNSET))
    assert 'accounting' in fields(result)
    assert result['lineage']['evidence_sha256']['accounting'] is None


def test_missing_cohorts_section_blocks():
    result = blocked(evidence(cohorts=None))
    assert 'cohorts' in fields(result)


def test_missing_inplace_rent_never_zero_fills():
    value = evidence()
    value['cohorts'][0]['inplace_rent_observation_id'] = None
    result = blocked(value)
    assert 'INPLACE_RENT_MISSING' in codes(result)
    assert '/unit_cohorts[0]/initial_inplace_rent' in fields(result)
    assert result['draft']['unit_cohorts'][0]['initial_inplace_rent'] is None
    assert '0.00' not in json.dumps(result['draft']['unit_cohorts'][0])


def test_null_amount_inplace_rent_never_zero_fills():
    unknown = money_obs('unit_rent', None, None, row=2, column=4)
    value = evidence(observations=[unknown, base_observations()[1]])
    result = blocked(value)
    assert 'INPLACE_RENT_MISSING' in codes(result)
    assert result['draft']['unit_cohorts'][0]['initial_inplace_rent'] is None


def test_unknown_rent_observation_reference_blocks():
    value = evidence()
    value['cohorts'][0]['inplace_rent_observation_id'] = 'mny_' + '9' * 64
    result = blocked(value)
    assert 'INPLACE_RENT_MISSING' in codes(result)


def test_wrong_measurement_basis_rent_reference_refuses():
    deposit = money_obs('deposit', '1450.00', '1450.00', period='one_time',
                        row=2, column=4)
    value = evidence(observations=[deposit, base_observations()[1]])
    assert refuse(value) == 'INVALID_INPUT'


def test_missing_market_rent_blocks():
    value = evidence()
    value['cohorts'][0]['market_rent_observation_id'] = None
    result = blocked(value)
    assert 'MARKET_RENT_MISSING' in codes(result)
    assert '/market_rent_curve[0]/market_rent' in fields(result)
    assert result['draft']['market_rent_curve'][0]['market_rent'] is None


@pytest.mark.parametrize('section,row,field', [
    ('loss_to_lease', {'cohort_id': 'res_1x', 'ltl_percent': None},
     '/loss_to_lease[0]/ltl_percent'),
    ('physical_vacancy', {'cohort_id': 'res_1x', 'vacancy_rate': None},
     '/physical_vacancy_curve[0]/vacancy_rate'),
])
def test_curve_gaps_block_naming_the_cohort_row(section, row, field):
    value = evidence()
    value['curves'][section] = []
    result = blocked(value)
    assert 'MISSING_INPUT' in codes(result)
    assert field in fields(result)


def test_absent_collection_loss_blocks():
    result = blocked(evidence(curves=UNSET))
    assert 'MISSING_INPUT' in codes(result)
    assert '/collection_loss_curve' in fields(result)


def test_curve_row_for_unknown_cohort_refuses():
    value = evidence()
    value['curves']['loss_to_lease'] = [{'cohort_id': 'ghost', 'ltl_percent': '0.02'}]
    assert refuse(value) == 'INVALID_INPUT'


# ---------------------------------------------------------------- sanitization + hygiene

@pytest.mark.parametrize('mutation', [
    {'lease_basis': 'PRIVATE_CANARY'},
    {'extra_key': 'PRIVATE_CANARY'},
])
def test_refusals_are_sanitized_and_never_leak(mutation):
    value = evidence(**mutation)
    code = refuse(value)
    assert code in ('INVALID_INPUT', 'UNSUPPORTED_LEASE_BASIS')
    module = api()
    with pytest.raises(module.CanonicalIntakeError) as caught:
        translate(value)
    assert 'PRIVATE_CANARY' not in str(caught.value)
    assert 'PRIVATE_CANARY' not in json.dumps(caught.value.args)
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))


def test_provider_smuggling_refusal_is_sanitized():
    value = evidence()
    value['provider'] = {'provider_id': 'PRIVATE_CANARY',
                         'market_rent': '9999.00'}
    refuse(value)
    module = api()
    with pytest.raises(module.CanonicalIntakeError) as caught:
        translate(value)
    assert 'PRIVATE_CANARY' not in str(caught.value)
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))


def test_error_messages_are_static_codes_only():
    module = api()
    value = evidence()
    value['cohorts'][0]['unit_count'] = 2.5
    with pytest.raises(module.CanonicalIntakeError) as caught:
        translate(value)
    assert caught.value.code == 'INVALID_INPUT'
    assert str(caught.value) == 'Canonical intake refused (INVALID_INPUT).'
    assert caught.value.args == ('Canonical intake refused (INVALID_INPUT).',)


def test_float_and_bool_unit_counts_refuse():
    value = evidence()
    value['cohorts'][0]['unit_count'] = 2.5
    assert refuse(value) == 'INVALID_INPUT'
    value = evidence()
    value['cohorts'][0]['unit_count'] = True
    assert refuse(value) == 'INVALID_INPUT'


def test_blocked_result_is_a_draft_never_an_engine_call():
    result = blocked(evidence(millage=None))
    assert result['canonical'] is None
    source = inspect.getsource(api())
    assert re.search(r'^\s*(import|from)\s+(engine|subprocess|socket|os)\b',
                     source, re.M) is None
    assert 'plat_multifamily' not in source
    assert 'run_underwriting' not in source


def test_exact_result_key_sets():
    result = translate(evidence())
    assert set(result) == {'contract_version', 'status', 'subject_id', 'as_of',
                           'policy_version', 'canonical', 'canonical_json',
                           'canonical_sha256', 'draft', 'blockers', 'lineage'}
    assert result['contract_version'] == 'ingest-canonical-intake/1.0.0'
    assert result['subject_id'] == SUBJECT and result['as_of'] == AS_OF
    assert result['policy_version'] == POLICY


def test_translation_is_deterministic_and_does_not_mutate_input():
    first_value = evidence()
    snapshot = copy.deepcopy(first_value)
    first = translate(first_value)
    second = translate(first_value)
    assert first == second
    assert first_value == snapshot


def test_unknown_top_level_keys_refuse():
    value = evidence()
    value['unexpected_section'] = {}
    assert refuse(value) == 'INVALID_INPUT'


def test_non_string_dict_keys_refuse_sanitized():
    # A Python caller (not JSON) can smuggle non-string keys past the first
    # type gate; the refusal must still be static and never chain the TypeError.
    module = api()
    value = evidence()
    hostile = {**value, 0: 'PRIVATE_CANARY'}
    with pytest.raises(module.CanonicalIntakeError) as caught:
        translate(hostile)
    assert caught.value.code == 'INVALID_INPUT'
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))