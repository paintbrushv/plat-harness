"""Public synthetic tests for source monetary observations; no source IO or math."""
import copy
import hashlib
import importlib.util
import json
import traceback

import pytest

SUBJECT = 'subject_demo'
AS_OF = '2026-01-31'
SOURCE = 'src_' + '1' * 32
SHA = hashlib.sha256(b'public synthetic accounting source').hexdigest()
KINDS = ('unit_rent', 'charge_rent', 'report_total', 'deposit', 'concession',
         'arrears', 'scheduled_charge', 'collected_cash')
BASIS = {
    'unit_rent': 'unit', 'charge_rent': 'charge', 'report_total': 'report',
    'deposit': 'deposit', 'concession': 'concession', 'arrears': 'arrears',
    'scheduled_charge': 'scheduled_charge', 'collected_cash': 'collected_cash',
}


def api():
    from plat_harness.ingest import accounting as module
    assert callable(getattr(module, 'validate_accounting_observations', None))
    assert callable(getattr(module, 'loads_accounting_observations', None))
    assert callable(getattr(module, 'canonical_bytes', None))
    assert callable(getattr(module, 'observe_money', None))
    return module


def citation(row=2, column=4):
    return {'source_id': SOURCE, 'source_sha256': SHA,
            'sheet': 1, 'row': row, 'row_end': row, 'column': column}


def money(kind='scheduled_charge', source_text='1450.00', decimal='1450.00',
          currency='USD', period='month', row=2, column=4, origin='typed'):
    cite = citation(row, column)
    identity = 'mny_' + hashlib.sha256(json.dumps(cite, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    amount = None if source_text is None else {
        'decimal': decimal, 'source_text': source_text, 'currency': currency,
        'unit': 'currency', 'period': period,
    }
    return {
        'observation_id': identity, 'source_id': SOURCE, 'kind': kind,
        'measurement_basis': BASIS[kind], 'amount': amount,
        'cell_origin': origin, 'citation': cite,
    }


def envelope(items=None):
    observations = items if items is not None else [money()]
    status = 'blocked' if any(item['amount'] is None for item in observations) else 'observed_unvalidated'
    return {
        'contract_version': 'ingest-accounting/1.0.0',
        'subject_id': SUBJECT, 'as_of': AS_OF,
        'adapter': {'id': 'synthetic-accounting', 'version': '1.0.0'},
        'sources': [{'source_id': SOURCE, 'sha256': SHA, 'role': 'original',
                     'original_source_ids': [], 'subject_id': SUBJECT, 'as_of': AS_OF}],
        'observations': observations, 'issues': [], 'status': status,
    }


def validate(value, **kwargs):
    return api().validate_accounting_observations(
        value, subject_id=kwargs.get('subject_id', SUBJECT),
        as_of=kwargs.get('as_of', AS_OF))


def refuse(value, **kwargs):
    module = api()
    with pytest.raises(module.AccountingObservationError) as caught:
        validate(value, **kwargs)
    assert caught.value.code in {
        'INVALID_OBSERVATIONS', 'INPUT_LIMIT_EXCEEDED', 'SCOPE_MISMATCH',
        'AMBIGUOUS_NUMERIC_FORMAT', 'FORMULA_CACHE_NOT_ORIGINAL',
        'MIXED_MEASUREMENT_BASIS', 'UNKNOWN_AMOUNT_NOT_ZERO',
    }
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))
    return caught.value.code


def test_versioned_accounting_seam_exists():
    assert importlib.util.find_spec('plat_harness.ingest.accounting') is not None


def test_typed_money_observation_is_json_safe_decimal_string():
    original = envelope()
    result = validate(original)
    assert result == original and result is not original
    amount = result['observations'][0]['amount']
    assert type(amount['decimal']) is str and amount['decimal'] == '1450.00'
    assert amount['source_text'] == '1450.00'
    assert amount['currency'] == 'USD' and amount['period'] == 'month'
    assert amount['unit'] == 'currency'
    encoded = json.dumps(result, allow_nan=False)
    assert '1450.00' in encoded
    assert not any(type(node) is float for node in (amount['decimal'], result['status']))


def test_observe_money_preserves_exact_numeric_text_and_period():
    module = api()
    observed = module.observe_money(
        kind='unit_rent', measurement_basis='unit', source_text='$1,450.00',
        currency='USD', period='month', citation=citation(), cell_origin='typed')
    assert observed['amount']['source_text'] == '$1,450.00'
    assert observed['amount']['decimal'] == '1450.00'
    assert observed['amount']['currency'] == 'USD'
    assert observed['amount']['period'] == 'month'
    assert observed['cell_origin'] == 'typed'
    assert type(observed['amount']['decimal']) is str
    value = envelope([observed])
    assert validate(value) == value


@pytest.mark.parametrize('text', [
    '1.234', '1,234', '1.234.000', '1,234,00', '1.234,567.89', '12,34.56',
    '1 450.00', '1.2.3', '1,,450', '1.450.00',
])
def test_ambiguous_decimal_or_thousands_formatting_refused(text):
    module = api()
    with pytest.raises(module.AccountingObservationError) as caught:
        module.observe_money(
            kind='scheduled_charge', measurement_basis='scheduled_charge',
            source_text=text, currency='USD', period='month',
            citation=citation(), cell_origin='typed')
    assert caught.value.code == 'AMBIGUOUS_NUMERIC_FORMAT'
    value = envelope()
    value['observations'][0]['amount'].update(source_text=text, decimal='1234')
    refuse(value)


@pytest.mark.parametrize('text,decimal', [
    ('1450', '1450'), ('1450.5', '1450.5'), ('1450.50', '1450.50'),
    ('1,450.00', '1450.00'), ('1.450,00', '1450.00'), ('0.00', '0.00'),
    ('(50.00)', '-50.00'), ('-50.00', '-50.00'), ('USD 1450.00', '1450.00'),
])
def test_unambiguous_source_text_keeps_currency_and_exact_text(text, decimal):
    module = api()
    observed = module.observe_money(
        kind='deposit', measurement_basis='deposit', source_text=text,
        currency='USD', period='one_time', citation=citation(), cell_origin='typed')
    assert observed['amount']['source_text'] == text
    assert observed['amount']['decimal'] == decimal
    assert observed['amount']['currency'] == 'USD'
    assert observed['amount']['period'] == 'one_time'


def test_unit_rent_charge_rent_and_report_totals_are_never_summed():
    items = [
        money('unit_rent', '1000.00', '1000.00', row=2, column=4),
        money('charge_rent', '75.00', '75.00', row=2, column=5),
        money('report_total', '50000.00', '50000.00', row=99, column=4),
    ]
    value = envelope(items)
    result = validate(value)
    kinds = [item['kind'] for item in result['observations']]
    assert kinds == ['unit_rent', 'charge_rent', 'report_total']
    assert 'totals' not in result and 'noi' not in result
    for name in ('sum', 'total', 'aggregate', 'noi', 'cap_rate', 'dscr'):
        assert not callable(getattr(api(), name, None))
    value['totals'] = {'decimal': '51075.00', 'currency': 'USD', 'period': 'month'}
    refuse(value)
    mixed = money('unit_rent')
    mixed['kind'] = 'combined_rent'
    mixed['measurement_basis'] = 'mixed'
    refuse(envelope([mixed]))


@pytest.mark.parametrize('kind', KINDS)
def test_deposit_concessions_arrears_charges_and_cash_stay_distinct(kind):
    item = money(kind, '25.00', '25.00', period='as_of' if kind in {
        'deposit', 'arrears', 'collected_cash'} else 'month')
    result = validate(envelope([item]))
    assert result['observations'][0]['kind'] == kind
    assert result['observations'][0]['measurement_basis'] == BASIS[kind]
    other = copy.deepcopy(item)
    other['measurement_basis'] = 'mixed' if kind != 'unit_rent' else 'charge'
    refuse(envelope([other]))


def test_missing_and_unknown_amounts_are_not_zero():
    module = api()
    for text in (None, '', 'n/a', 'N/A', 'unknown', '—', '-'):
        observed = module.observe_money(
            kind='arrears', measurement_basis='arrears', source_text=text,
            currency='USD', period='as_of', citation=citation(), cell_origin='typed')
        assert observed['amount'] is None
    value = envelope([money(source_text=None, decimal=None)])
    assert value['status'] == 'blocked'
    assert validate(value)['observations'][0]['amount'] is None
    value['observations'][0]['amount'] = {
        'decimal': '0', 'source_text': '', 'currency': 'USD',
        'unit': 'currency', 'period': 'as_of'}
    assert refuse(value) == 'UNKNOWN_AMOUNT_NOT_ZERO'
    value = envelope([money(source_text=None, decimal=None)])
    value['observations'][0]['amount'] = {
        'decimal': '0.00', 'source_text': 'n/a', 'currency': 'USD',
        'unit': 'currency', 'period': 'as_of'}
    refuse(value)


@pytest.mark.parametrize('origin', [
    'formula', 'formula_cache', 'cached_value', 'data_only_cache',
])
def test_formula_caches_cannot_be_certified_as_original_typed_cells(origin):
    module = api()
    with pytest.raises(module.AccountingObservationError) as caught:
        module.observe_money(
            kind='scheduled_charge', measurement_basis='scheduled_charge',
            source_text='1450.00', currency='USD', period='month',
            citation=citation(), cell_origin=origin)
    assert caught.value.code == 'FORMULA_CACHE_NOT_ORIGINAL'
    value = envelope()
    value['observations'][0]['cell_origin'] = origin
    assert refuse(value) == 'FORMULA_CACHE_NOT_ORIGINAL'
    value = envelope()
    value['observations'][0]['certified_original'] = True
    refuse(value)


@pytest.mark.parametrize('bad', [
    True, False, 1450, 1450.0, 0.5, float('inf'), float('nan'),
    '-inf', 'NaN', 'Infinity', '1e3', '1E+2', '0x10',
])
def test_bool_float_nonfinite_and_non_string_amounts_refuse(bad):
    value = envelope()
    value['observations'][0]['amount']['decimal'] = bad
    refuse(value)
    if type(bad) is not str:
        module = api()
        with pytest.raises(module.AccountingObservationError):
            module.observe_money(
                kind='deposit', measurement_basis='deposit', source_text=bad,
                currency='USD', period='one_time', citation=citation(),
                cell_origin='typed')


def test_rates_noi_and_extra_keys_are_refused():
    value = envelope()
    value['observations'][0]['amount']['unit'] = 'percent'
    refuse(value)
    value = envelope()
    value['observations'][0]['kind'] = 'noi'
    value['observations'][0]['measurement_basis'] = 'report'
    refuse(value)
    value = envelope()
    value['noi'] = '1000.00'
    refuse(value)
    value = envelope()
    value['observations'][0]['PRIVATE_CANARY'] = 'PRIVATE_CANARY'
    refuse(value)


def test_canonical_json_round_trip_and_defensive_copy():
    module = api()
    value = envelope()
    expected = json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False).encode()
    assert module.canonical_bytes(value, subject_id=SUBJECT, as_of=AS_OF) == expected
    assert module.loads_accounting_observations(expected, subject_id=SUBJECT, as_of=AS_OF) == value
    mutated = validate(value)
    mutated['observations'][0]['amount']['decimal'] = '0'
    assert value['observations'][0]['amount']['decimal'] == '1450.00'


@pytest.mark.parametrize('raw', [
    b'\xffPRIVATE_CANARY', '{PRIVATE_CANARY',
    '{"subject_id":null,"subject_id":null}', '[NaN]', '[Infinity]', '[1e999]',
    123, True, 1.0,
], ids=['utf8', 'syntax', 'duplicate', 'nan', 'inf', 'overflow', 'int', 'bool', 'float'])
def test_loader_rejects_malformed_nonfinite_without_chain(raw):
    module = api()
    with pytest.raises(module.AccountingObservationError) as caught:
        module.loads_accounting_observations(raw, subject_id=SUBJECT, as_of=AS_OF)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))


def test_scope_binding_and_citation_integers_not_bools():
    refuse(envelope(), as_of='2026-02-01')
    value = envelope()
    value['observations'][0]['citation']['row'] = True
    refuse(value)
    value = envelope()
    value['observations'][0]['citation']['column'] = 1.0
    refuse(value)
    value = envelope()
    value['subject_id'] = value['sources'][0]['subject_id'] = None
    value['as_of'] = value['sources'][0]['as_of'] = None
    assert validate(value, subject_id=None, as_of=None) == value
