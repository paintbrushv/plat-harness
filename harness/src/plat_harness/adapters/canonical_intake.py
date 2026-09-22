"""Thin canonical engine-intake translation; zero financial arithmetic.

This adapter shapes already-validated harness evidence into the engine's
canonical deal-dict contract (engine-owned ``deal_schema_v0_1.json``, strict
``additionalProperties: false`` objects). It performs no financial math of
any kind: no summing, no rate computation, no rounding, no float arithmetic.
Money is carried as the exact finite decimal string certified by the frozen
``ingest-accounting/1.0.0`` contract, which is reused **by import** for every
money value — never forked. Non-money rate strings must already be plain
finite decimal strings; separator, percent, hex and space formats refuse.
When the engine schema requires native numbers, the deterministic
engine-owned path ``engine.modules.util.dec`` (referenced by
``ENGINE_NUMERIC_PATH``) performs that conversion; the engine repository is
read-only and is never imported, executed or modified here.

Strict engine objects never receive arbitrary provenance keys: lineage is
emitted in a separate source-lineage sidecar keyed to the canonical SHA256.
Missing inputs produce an actionable draft/blocked envelope naming exactly
what is missing — a blocked result never contains canonical bytes, nothing is
zero-filled, and no engine call is ever made from this module.
"""
import hashlib
import json
import re

from plat_harness.ingest.accounting import (
    AccountingObservationError,
    validate_accounting_observations,
)
from plat_harness.ingest.contracts import (
    ObservationContractError,
    validate_observations,
)

__all__ = [
    'ADAPTER', 'BLOCKER_CODES', 'CONTRACT_VERSION', 'ENGINE_NUMERIC_PATH',
    'ENGINE_SCHEMA_VERSION', 'ERROR_CODES', 'SUPPORTED_LEASE_BASIS',
    'CanonicalIntakeError', 'canonical_bytes', 'translate_intake',
]

CONTRACT_VERSION = 'ingest-canonical-intake/1.0.0'
ADAPTER = {'id': 'canonical-intake', 'version': '1.0.0'}
ENGINE_SCHEMA_VERSION = '0.1'
# Deterministic engine-owned numeric path for any schema-required conversion.
# The engine repository is read-only; this adapter never imports or runs it.
ENGINE_NUMERIC_PATH = 'engine.modules.util.dec'
SUPPORTED_LEASE_BASIS = frozenset(('monthly_rent',))
MAX_COHORTS = 64
MAX_TEXT = 256

ERROR_CODES = frozenset((
    'INVALID_INPUT', 'SCOPE_MISMATCH', 'UNSUPPORTED_LEASE_BASIS',
    'UNAUTHORIZED_TARGET_RENT', 'AMBIGUOUS_NUMERIC_FORMAT',
))

BLOCKER_CODES = frozenset((
    'MISSING_INPUT', 'ANALYSIS_WINDOW_INCOMPLETE', 'OCCUPANCY_COUNTS_INCOMPLETE',
    'UNRESOLVED_UNIT_USE', 'MILLAGE_MISSING', 'INPLACE_RENT_MISSING',
    'MARKET_RENT_MISSING', 'COMMERCIAL_INCOME_OMISSION_UNEXPLAINED',
    'OTHER_INCOME_OMISSION_UNEXPLAINED',
))

_MONTH = re.compile(r'20[0-9]{2}-(0[1-9]|1[0-2])')
_SUBJECT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
_COHORT_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
_RATE = re.compile(r'-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,8})?')
_COHORT_KEYS = ('cohort_id', 'unit_type', 'unit_count',
                'inplace_rent_observation_id', 'market_rent_observation_id',
                'target_rent')
_TOP_KEYS = ('contract_version', 'subject_id', 'as_of', 'policy_version',
             'provider', 'analysis_window', 'lease_basis', 'run_id', 'analyst',
             'purpose', 'occupancy', 'accounting', 'cohorts', 'curves',
             'millage', 'commercial_income', 'other_income')
_MILLAGE_KEYS = ('millage_rate_mills', 'assessment_ratio', 'source',
                  'source_locator', 'analyst_override')
_OMISSION_KEYS = ('status', 'observation_id', 'omission_explanation',
                  'canonical_program')
_PROGRAM_KEYS = ('program_id', 'program_name', 'program_type', 'pricing_type',
                 'price_value', 'eligible_units', 'start_period', 'end_period',
                 'adoption_rate')


class CanonicalIntakeError(ValueError):
    """Static diagnostic only; input-bearing exceptions are never chained."""

    def __init__(self, code='INVALID_INPUT'):
        self.code = code if code in ERROR_CODES else 'INVALID_INPUT'
        super().__init__('Canonical intake refused (' + self.code + ').')


class _Failure(Exception):
    pass


def _require(condition, code='INVALID_INPUT'):
    if not condition:
        raise _Failure(code)


def _exact(value, keys, code='INVALID_INPUT'):
    _require(type(value) is dict and tuple(sorted(value)) == tuple(sorted(keys)),
             code)


def _text(value, code='INVALID_INPUT'):
    _require(type(value) is str and bool(value) and len(value) <= MAX_TEXT, code)
    return value


def _typed(value, kind, code='INVALID_INPUT'):
    _require(type(value) is kind, code)
    return value


def _integer(value, low, high, code='INVALID_INPUT'):
    _require(type(value) is int and low <= value <= high, code)
    return value


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(_encode(value)).hexdigest()


def _rate(value, code='AMBIGUOUS_NUMERIC_FORMAT'):
    """Plain finite decimal string only; separators/percent/hex/space refuse."""
    _require(type(value) is str and _RATE.fullmatch(value) is not None, code)
    return value


def _month(value, code='INVALID_INPUT'):
    _require(type(value) is str and _MONTH.fullmatch(value) is not None, code)
    return value


def _window(value):
    _exact(value, ('analysis_start_date', 'analysis_end_date'))
    window = {}
    for key in ('analysis_start_date', 'analysis_end_date'):
        window[key] = None if value[key] is None else _month(value[key])
    return window


def _window_complete(window):
    return (window['analysis_start_date'] is not None
            and window['analysis_end_date'] is not None
            and window['analysis_start_date'] <= window['analysis_end_date'])


def _tax_policy(value):
    if value is None:
        return None
    _exact(value, _MILLAGE_KEYS)
    return {
        'millage_rate_mills': _rate(value['millage_rate_mills']),
        'assessment_ratio': _rate(value['assessment_ratio']),
        'source': _text(value['source']),
        'source_locator': _text(value['source_locator']),
        'analyst_override': _typed(value['analyst_override'], bool),
    }


def _rent_of(observations, observation_id, index, field, blockers, code):
    """Resolve one cited unit_rent observation to its exact decimal string.

    Never converts, rescales or changes the period of a cited rent; a missing
    or null citation is a blocker, never a zero.
    """
    if observation_id is None:
        blockers.append({'code': code, 'field': field,
                         'detail': 'required rent observation is absent'})
        return None
    _typed(observation_id, str)
    for item in observations:
        if item['observation_id'] == observation_id:
            _require(item['kind'] == 'unit_rent'
                     and item['measurement_basis'] == 'unit', 'INVALID_INPUT')
            if item['amount'] is None:
                blockers.append({'code': code, 'field': field,
                                 'detail': 'cited rent observation carries a null amount'})
                return None
            return item['amount']['decimal']
    blockers.append({'code': code, 'field': field,
                     'detail': 'cited rent observation is not present'})
    return None


def _cohort(value, observations, index, blockers):
    _exact(value, _COHORT_KEYS)
    cohort_id = _typed(value['cohort_id'], str)
    _require(_COHORT_ID.fullmatch(cohort_id) is not None, 'INVALID_INPUT')
    unit_type = _text(value['unit_type'])
    unit_count = _integer(value['unit_count'], 0, 100_000)
    inplace = _rent_of(observations, value['inplace_rent_observation_id'],
                       index, '/unit_cohorts[%d]/initial_inplace_rent' % index,
                       blockers, 'INPLACE_RENT_MISSING')
    market = _rent_of(observations, value['market_rent_observation_id'],
                      index, '/market_rent_curve[%d]/market_rent' % index,
                      blockers, 'MARKET_RENT_MISSING')

    target = value['target_rent']
    target_rent = None
    target_source = None
    if target is not None:
        _exact(target, ('observation_id', 'authorization'))
        authorization = target['authorization']
        # Fail-closed: only an exact well-formed authorization authorizes a
        # target rent; anything else (False, malformed, null) refuses.
        if not (type(authorization) is dict
                and tuple(sorted(authorization)) == ('authority_ref', 'authorized')
                and authorization['authorized'] is True
                and type(authorization['authority_ref']) is str
                and bool(authorization['authority_ref'])):
            raise _Failure('UNAUTHORIZED_TARGET_RENT')
        target_rent = _rent_of(
            observations, target['observation_id'], index,
            '/unit_cohorts[%d]/target_monthly_rent' % index,
            blockers, 'MISSING_INPUT')
        target_source = 'analyst_override'

    cohort = {'cohort_id': cohort_id, 'unit_type': unit_type,
              'unit_count': unit_count, 'initial_inplace_rent': inplace}
    if target_rent is not None or value['target_rent'] is not None:
        cohort['target_monthly_rent'] = target_rent
        cohort['target_monthly_rent_source'] = target_source
    return cohort, market


def _curves(value, cohort_ids, blockers):
    if value is None:
        for field in ('/loss_to_lease', '/physical_vacancy_curve',
                       '/collection_loss_curve'):
            blockers.append({'code': 'MISSING_INPUT', 'field': field,
                             'detail': 'curves section is absent'})
        return {'loss_to_lease': [], 'physical_vacancy': [],
                'collection_loss': []}
    _exact(value, ('loss_to_lease', 'physical_vacancy', 'collection_loss'))
    tables = {}
    for name in ('loss_to_lease', 'physical_vacancy', 'collection_loss'):
        _typed(value[name], list, 'INVALID_INPUT')
    for row in value['loss_to_lease']:
        _exact(row, ('cohort_id', 'ltl_percent'))
        # Membership is enforced only against a declared cohort set; an absent
        # cohorts section is already a named blocker and never reaches an
        # engine call, so orphan rows stay in the draft without hiding it.
        if cohort_ids:
            _require(_typed(row['cohort_id'], str) in cohort_ids, 'INVALID_INPUT')
        _rate(row['ltl_percent'])
    for row in value['physical_vacancy']:
        _exact(row, ('cohort_id', 'vacancy_rate'))
        if cohort_ids:
            _require(_typed(row['cohort_id'], str) in cohort_ids, 'INVALID_INPUT')
        _rate(row['vacancy_rate'])
    for row in value['collection_loss']:
        _exact(row, ('applies_to', 'loss_rate'))
        _require(row['applies_to'] in ('Rent', 'Programs', 'ALL'),
                 'INVALID_INPUT')
        _rate(row['loss_rate'])
    if not value['loss_to_lease']:
        blockers.append({'code': 'MISSING_INPUT',
                         'field': '/loss_to_lease[0]/ltl_percent',
                         'detail': 'no loss-to-lease row for the first cohort'})
    if not value['physical_vacancy']:
        blockers.append({'code': 'MISSING_INPUT',
                         'field': '/physical_vacancy_curve[0]/vacancy_rate',
                         'detail': 'no physical vacancy row for the first cohort'})
    if not value['collection_loss']:
        blockers.append({'code': 'MISSING_INPUT',
                         'field': '/collection_loss_curve',
                         'detail': 'collection loss coverage is absent'})
    tables['loss_to_lease'] = [_typed(row, dict) for row in value['loss_to_lease']]
    tables['physical_vacancy'] = [_typed(row, dict)
                                   for row in value['physical_vacancy']]
    tables['collection_loss'] = [_typed(row, dict)
                                 for row in value['collection_loss']]
    return tables


def _income_section(value, observations, blockers, code, field):
    """Cited commercial/other income, or an explicitly explained omission.

    An unexplained omission is a blocker; an omitted line is never a silent
    zero anywhere in the canonical output.
    """
    if value is None:
        blockers.append({'code': 'MISSING_INPUT', 'field': field,
                         'detail': 'income section is absent'})
        return None
    _exact(value, _OMISSION_KEYS)
    status = _typed(value['status'], str)
    _require(status in ('omitted', 'present'), 'INVALID_INPUT')
    if status == 'omitted':
        _require(value['observation_id'] is None
                 and value['canonical_program'] is None, 'INVALID_INPUT')
        explanation = value['omission_explanation']
        if type(explanation) is str and explanation.strip():
            return {'status': 'omitted', 'explanation': explanation}
        blockers.append({'code': code, 'field': field,
                         'detail': 'omission carries no explanation'})
        return {'status': 'omitted', 'explanation': None}

    observation_id = _typed(value['observation_id'], str)
    _require(value['omission_explanation'] is None, 'INVALID_INPUT')
    program = value['canonical_program']
    _exact(program, _PROGRAM_KEYS)
    price = None
    for item in observations:
        if item['observation_id'] == observation_id:
            if item['amount'] is not None:
                price = item['amount']['decimal']
            break
    else:
        raise _Failure('INVALID_INPUT')
    if price is not None:
        _require(price == _typed(program['price_value'], str), 'INVALID_INPUT')
    if price is None:
        blockers.append({'code': 'MISSING_INPUT',
                         'field': '/revenue_programs[0]/price_value',
                         'detail': 'cited income observation carries a null amount'})
    row = {
        'program_id': _text(program['program_id']),
        'program_name': _text(program['program_name']),
        'program_type': _typed(program['program_type'], str),
        'pricing_type': _typed(program['pricing_type'], str),
        'price_value': price,
        'eligible_units': _typed(program['eligible_units'], str),
        'start_period': _month(program['start_period']),
        'end_period': _month(program['end_period']),
    }
    adoption = _rate(program['adoption_rate'])
    return {'status': 'present', 'program': row, 'adoption': adoption,
            'explanation': None}


def _translate(value, subject_id, as_of, policy_version):
    _typed(value, dict)
    _exact(value, _TOP_KEYS)
    _require(value['contract_version'] == CONTRACT_VERSION, 'INVALID_INPUT')
    _require(type(subject_id) is str and _SUBJECT.fullmatch(subject_id) is not
             None, 'SCOPE_MISMATCH')
    _require(type(as_of) is str, 'SCOPE_MISMATCH')
    _require(type(policy_version) is str, 'SCOPE_MISMATCH')
    _require((value['subject_id'], value['as_of']) == (subject_id, as_of),
             'SCOPE_MISMATCH')
    _require(value['policy_version'] == policy_version, 'SCOPE_MISMATCH')

    provider = value['provider']
    _exact(provider, ('provider_id', 'model_id'))
    _text(provider['provider_id'])
    _text(provider['model_id'])

    _require(value['lease_basis'] in SUPPORTED_LEASE_BASIS,
             'UNSUPPORTED_LEASE_BASIS')

    blockers = []
    window = _window(value['analysis_window'] if
                     value['analysis_window'] is not None else
                     {'analysis_start_date': None, 'analysis_end_date': None})
    complete = _window_complete(window)
    if not complete:
        if window['analysis_start_date'] is None:
            blockers.append({'code': 'ANALYSIS_WINDOW_INCOMPLETE',
                             'field': '/time_grid/analysis_start_date',
                             'detail': 'analysis window start is missing'})
        if window['analysis_end_date'] is None:
            blockers.append({'code': 'ANALYSIS_WINDOW_INCOMPLETE',
                             'field': '/time_grid/analysis_end_date',
                             'detail': 'analysis window end is missing'})
        elif window['analysis_start_date'] is not None:
            blockers.append({'code': 'ANALYSIS_WINDOW_INCOMPLETE',
                             'field': '/time_grid/analysis_end_date',
                             'detail': 'analysis window end precedes start'})

    for name in ('run_id', 'analyst', 'purpose'):
        if value[name] is None:
            blockers.append({'code': 'MISSING_INPUT',
                             'field': 'metadata.' + name,
                             'detail': 'required metadata field is missing'})

    occupancy = value['occupancy']
    if occupancy is not None:
        occupancy = validate_observations(occupancy, subject_id=subject_id,
                                          as_of=as_of)
        counts = occupancy['completeness']['residential']['counts']
        if any(counts[field] is None for field in
               ('occupied', 'vacant', 'down', 'total')):
            blockers.append({'code': 'OCCUPANCY_COUNTS_INCOMPLETE',
                             'field': 'occupancy.completeness.residential',
                             'detail': 'residential occupancy counts are missing'})
        if occupancy['unknown_use_units']:
            blockers.append({'code': 'UNRESOLVED_UNIT_USE',
                             'field': 'occupancy.unknown_use_units',
                             'detail': 'units with unresolved use remain'})
    else:
        blockers.append({'code': 'MISSING_INPUT', 'field': 'occupancy',
                         'detail': 'occupancy evidence is absent'})

    accounting = value['accounting']
    if accounting is not None:
        accounting = validate_accounting_observations(
            accounting, subject_id=subject_id, as_of=as_of)
        for item in accounting['observations']:
            if item['kind'] == 'unit_rent' and item['amount'] is not None:
                _require(item['amount']['period'] == 'month',
                         'UNSUPPORTED_LEASE_BASIS')
    else:
        blockers.append({'code': 'MISSING_INPUT', 'field': 'accounting',
                         'detail': 'accounting evidence is absent'})
    observations = [] if accounting is None else accounting['observations']

    tax_policy = _tax_policy(value['millage'])
    if tax_policy is None:
        blockers.append({'code': 'MILLAGE_MISSING',
                         'field': '/metadata/property_summary/property_tax_policy',
                         'detail': 'property tax policy is absent'})

    cohorts_input = value['cohorts']
    cohort_rows = []
    market_rents = []
    cohort_ids = []
    if cohorts_input is None:
        blockers.append({'code': 'MISSING_INPUT', 'field': 'cohorts',
                         'detail': 'unit cohorts are absent'})
    else:
        _typed(cohorts_input, list)
        _require(bool(cohorts_input) and len(cohorts_input) <= MAX_COHORTS,
                'INVALID_INPUT')
        for index, item in enumerate(cohorts_input):
            cohort, market = _cohort(item, observations, index, blockers)
            cohort_rows.append(cohort)
            market_rents.append(market)
            cohort_ids.append(cohort['cohort_id'])

    tables = _curves(value['curves'], cohort_ids, blockers)

    programs = []
    adoptions = []
    explanations = {}
    for section, code, field in (
            ('commercial_income', 'COMMERCIAL_INCOME_OMISSION_UNEXPLAINED',
             'commercial_income'),
            ('other_income', 'OTHER_INCOME_OMISSION_UNEXPLAINED',
             'other_income')):
        income = _income_section(value[section], observations, blockers,
                                 code, field)
        if income is None:
            explanations[section] = None
        elif income['status'] == 'omitted':
            explanations[section] = income['explanation']
        else:
            explanations[section] = None
            programs.append(income['program'])
            adoptions.append({'program_id': income['program']['program_id'],
                              'start_period': income['program']['start_period'],
                              'end_period': income['program']['end_period'],
                              'adoption_rate': income['adoption']})
    if programs:
        applies = {row['applies_to'] for row in tables['collection_loss']}
        if 'Programs' not in applies and 'ALL' not in applies:
            blockers.append({'code': 'MISSING_INPUT',
                             'field': '/collection_loss_curve',
                             'detail': 'collection loss coverage for Programs is missing'})

    start, end = window['analysis_start_date'], window['analysis_end_date']
    draft = {
        'schema_version': ENGINE_SCHEMA_VERSION,
        'metadata': {
            'deal_id': subject_id, 'run_id': value['run_id'],
            'as_of_date': as_of, 'analyst': value['analyst'],
            'purpose': value['purpose'],
            'property_summary': {'property_tax_policy': tax_policy},
        },
        'time_grid': dict(window),
        'unit_cohorts': cohort_rows,
        'market_rent_curve': [
            {'cohort_id': cohort_ids[index], 'start_period': start,
             'end_period': end, 'market_rent': market_rents[index]}
            for index in range(len(cohort_ids))],
        'loss_to_lease': [
            {'cohort_id': row['cohort_id'], 'start_period': start,
             'end_period': end, 'ltl_percent': row['ltl_percent']}
            for row in tables['loss_to_lease']],
        'physical_vacancy_curve': [
            {'cohort_id': row['cohort_id'], 'start_period': start,
             'end_period': end, 'vacancy_rate': row['vacancy_rate']}
            for row in tables['physical_vacancy']],
        'collection_loss_curve': [
            {'applies_to': row['applies_to'], 'start_period': start,
             'end_period': end, 'loss_rate': row['loss_rate']}
            for row in tables['collection_loss']],
        'revenue_programs': programs,
        'program_adoption_curve': adoptions,
    }

    status = 'blocked' if blockers else 'eligible'
    if blockers:
        canonical = canonical_json = canonical_sha256 = None
    else:
        canonical = draft
        canonical_json = json.dumps(canonical, sort_keys=True,
                                    separators=(',', ':'),
                                    ensure_ascii=False)
        canonical_sha256 = hashlib.sha256(
            canonical_json.encode('utf-8')).hexdigest()

    field_lineage = []
    if accounting is not None:
        by_id = {item['observation_id']: item
                 for item in accounting['observations']}
        for index, item in enumerate(cohorts_input or []):
            for key, path in (
                    ('inplace_rent_observation_id',
                     '/unit_cohorts[%d]/initial_inplace_rent' % index),
                    ('market_rent_observation_id',
                     '/market_rent_curve[%d]/market_rent' % index)):
                cited = item.get(key)
                if cited is None or cited not in by_id:
                    continue
                observation = by_id[cited]
                if observation['amount'] is None:
                    continue
                field_lineage.append({
                    'canonical_path': path,
                    'observation_id': cited,
                    'source_id': observation['source_id'],
                    'source_sha256': observation['citation']['source_sha256'],
                    'decimal': observation['amount']['decimal']})

    lineage = {
        'contract_version': CONTRACT_VERSION, 'status': status,
        'canonical_sha256': canonical_sha256, 'subject_id': subject_id,
        'as_of': as_of, 'policy_version': policy_version,
        'provider': dict(provider),
        'evidence_sha256': {
            'occupancy': None if occupancy is None else _digest(occupancy),
            'accounting': None if accounting is None else _digest(accounting),
        },
        'field_lineage': field_lineage,
        'omission_explanations': explanations,
    }

    return {
        'contract_version': CONTRACT_VERSION, 'status': status,
        'subject_id': subject_id, 'as_of': as_of,
        'policy_version': policy_version, 'canonical': canonical,
        'canonical_json': canonical_json,
        'canonical_sha256': canonical_sha256,
        'draft': draft if blockers else None,
        'blockers': blockers, 'lineage': lineage,
    }


def translate_intake(value, *, subject_id, as_of, policy_version):
    """Translate validated evidence to canonical engine shape or a draft.

    Returns ``eligible`` with canonical bytes plus a lineage sidecar, or
    ``blocked`` with an actionable draft naming exactly what is missing.
    Raises ``CanonicalIntakeError`` with a static code otherwise.
    """
    code = 'INVALID_INPUT'
    try:
        return _translate(value, subject_id, as_of, policy_version)
    except _Failure as exc:
        code = exc.args[0]
    except ObservationContractError:
        code = 'SCOPE_MISMATCH'
    except AccountingObservationError as exc:
        if exc.code == 'SCOPE_MISMATCH':
            code = 'SCOPE_MISMATCH'
        elif exc.code == 'AMBIGUOUS_NUMERIC_FORMAT':
            code = 'AMBIGUOUS_NUMERIC_FORMAT'
    except CanonicalIntakeError:
        raise
    except Exception:
        pass
    raise CanonicalIntakeError(code) from None


def canonical_bytes(value, *, subject_id, as_of, policy_version):
    """Validate first, then emit compact sorted-key UTF-8 JSON (no newline)."""
    result = translate_intake(value, subject_id=subject_id, as_of=as_of,
                              policy_version=policy_version)
    if result['status'] != 'eligible':
        raise CanonicalIntakeError('INVALID_INPUT') from None
    return result['canonical_json'].encode('utf-8')