"""Harness-side tax-policy binding adapter (Task 4.3).

Binds a *reviewed* tax-policy package to the underwriting engine's pinned
statutory schedule builder. This module performs **zero tax arithmetic**: the
engine (a read-only sibling repository) owns every unit, rounding and
arithmetic decision through its pinned entry point
``engine.tax_regimes.build_tax_regime_schedule`` (contract
``engine-tax-regimes/1.0.0``). The harness only selects and validates an
approved regime package and routes it to the bound engine callable — the
engine repository is never imported, executed or modified here.

Fail-closed gates, in order, all before any engine call:

- **Scope**: the requested subject, parcel and tax year must match the
  reviewed policy exactly (``SCOPE_MISMATCH``); unknown states refuse
  (``UNSUPPORTED_TAX_REGIME``) — there is no "Standard" fallback.
- **Policy drift**: the pinned policy SHA256 must match the actual policy
  bytes at bind time (``POLICY_DRIFT``).
- **T12 is not policy evidence**: a comparison T12 tax row can never
  substitute for statutory policy evidence (``T12_NOT_POLICY_EVIDENCE``).
- **Rate-unit consistency**: the declared levy unit must match the
  regime's statutory levy unit; a declared unit that contradicts the policy
  levy unit, a taxing-unit *named* after a unit of measure, or a duplicate
  taxing unit refuse (``RATE_UNIT_INCONSISTENT``). The harness never
  converts between mills, per-$100 and per-$1,000 — it only compares
  declared labels.
- **Missing mills**: a missing or blank millage refuses
  (``MILLAGE_MISSING``); a missing millage is never zero and no engine call
  is made.
- **Explicit basis**: an absent or purchase-price-inferred assessment basis
  refuses (``INFERRED_BASIS_FORBIDDEN``).
- **Unapproved override**: an analyst override requires a human approval
  record validated by the frozen ``contracts._approval`` binding against
  the host-owned registry (``APPROVAL_REQUIRED`` / ``APPROVAL_MISMATCH``) —
  there is no second approval database and no ``approved: true`` shortcut.
- **Schedule divergence**: the first year of a multiyear schedule must be
  byte-identical to a fresh standalone first-year binding
  (``SCHEDULE_DIVERGENCE``); first-year output and the multiyear schedule
  cannot diverge unnoticed.

Applicability and approval stay in a **separate bound envelope**: the
engine schedule object is passed through verbatim (byte-identical canonical
JSON) and never receives provenance keys. Free-text provenance fields
(``source``, ``source_locator``) are deliberately NOT copied into the bound
envelope or the engine inputs, so hostile free text cannot surface in
results. Insurance escalation is a separately approved assumption, never a
tax-regime default (see ``docs/TAX_REGIME_SPEC.md``).

Refusals are typed marker returns (never raised to callers, never chained)
with static, sanitized messages: policy values, engine internals and
exception details never surface. Every binding reports
``requires_competent_human_review`` — research is research, not law.
"""
from __future__ import annotations

import re
from copy import deepcopy
from datetime import date
from typing import Any, Callable

from plat_harness import contracts
from plat_harness.errors import HarnessError

CONTRACT_VERSION = 'tax-policy-binding/1.0.0'
# A bindable policy package carries the Task 4.1 research contract version.
POLICY_CONTRACT_VERSION = 'tax-regime-research/1.0.0'
# The pinned engine entry point; production hosts bind this exact callable.
# The engine repository is read-only and never imported by this module.
ENGINE_SCHEDULE_PATH = 'engine.tax_regimes.build_tax_regime_schedule'
ENGINE_CONTRACT_VERSION = 'engine-tax-regimes/1.0.0'

ERROR_CODES = frozenset({
    'INVALID_INPUT', 'SCOPE_MISMATCH', 'UNSUPPORTED_TAX_REGIME',
    'POLICY_DRIFT', 'RATE_UNIT_INCONSISTENT', 'APPROVAL_REQUIRED',
    'APPROVAL_MISMATCH', 'MILLAGE_MISSING', 'INFERRED_BASIS_FORBIDDEN',
    'T12_NOT_POLICY_EVIDENCE', 'SCHEDULE_DIVERGENCE', 'INVALID_CONTRACT',
    'NOT_IMPLEMENTED',
})

_MESSAGES = {
    'INVALID_INPUT':
        'The tax-policy package is malformed; nothing was bound.',
    'SCOPE_MISMATCH':
        'The requested subject, parcel or tax year does not match the '
        'reviewed policy; nothing was bound.',
    'UNSUPPORTED_TAX_REGIME':
        'Refused: no researched applicability for this jurisdiction, and '
        'no unexplained universal fallback exists.',
    'POLICY_DRIFT':
        'The reviewed policy bytes differ from the pinned policy SHA256; '
        'nothing was bound.',
    'RATE_UNIT_INCONSISTENT':
        'The declared levy unit is inconsistent with the regime statutory '
        'unit, or a taxing-unit name is a unit-of-measure word; the harness '
        'never converts between levy units.',
    'APPROVAL_REQUIRED':
        'An analyst override requires human approval recorded in the '
        'host-owned registry; there is no easier approval path.',
    'APPROVAL_MISMATCH':
        'The recorded approval does not bind the exact override payload.',
    'MILLAGE_MISSING':
        'Property tax millage is missing; a missing millage is never zero '
        'and no engine call was made.',
    'INFERRED_BASIS_FORBIDDEN':
        'The assessment basis is absent or inferred from the purchase '
        'price; an explicit reviewed basis is required.',
    'T12_NOT_POLICY_EVIDENCE':
        'A comparison T12 tax row is not statutory policy evidence.',
    'SCHEDULE_DIVERGENCE':
        'The first-year binding and the multiyear schedule diverge; '
        'nothing was bound.',
    'INVALID_CONTRACT':
        'The bound engine schedule callable failed or returned an '
        'unsupported contract; details are not surfaced.',
    'NOT_IMPLEMENTED':
        'No deterministic engine schedule callable is bound; tax '
        'mathematics is never performed in the harness.',
}

# The researched mandatory regimes (Task 4.1). Binding never re-researches;
# it validates a reviewed package against the recorded applicability.
SUPPORTED_STATES = frozenset({'TX', 'CA', 'FL', 'AL'})

# Statutory levy-unit labels per researched regime. A policy must declare
# the matching unit exactly; the harness compares labels only and never
# converts a rate between units.
_REGIME_LEVY_UNITS = {
    'TX': 'dollars_per_100_of_taxable_value',       # Tex. Tax Code §§ 26.04, 26.09
    'CA': 'decimal_fraction_of_assessed_value',    # Cal. Const. art. XIII A § 1(a)
    'FL': 'mills_per_1000_of_taxable_value',        # Fla. Stat. § 200.065
    'AL': 'mills_per_1000_of_assessed_value',       # Ala. Code § 40-8-1; ADOR millage
}

# A taxing unit NAMED after a unit of measure betrays unit confusion.
_LEVY_UNIT_WORDS = frozenset({
    'mills', 'mill', 'per_100', 'per_1000', 'decimal_rate',
    'percentage_points', '%', '$',
})

# Explicit (never purchase-price-inferred) assessment bases.
_EXPLICIT_BASES = frozenset({
    'explicit_appraised_value', 'base_year_value', 'just_value',
    'assessed_class_value',
})

# Free-text provenance that names a comparison statement rather than an
# authoritative policy source.
_T12_PATTERN = re.compile(r't12|trailing[ _-]?twelve', re.IGNORECASE)
_SUBJECT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
_PARCEL = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.-]{0,127}')
_RATE = re.compile(r'-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,8})?')
_SHA = re.compile(r'[0-9a-f]{64}')
_MAX_TEXT = 256

# Policy key -> engine input key, per regime. Only listed keys are routed;
# free-text provenance, millage labels and approval records never reach the
# engine, and the harness never invents a conversion between them.
_ENGINE_INPUT_MAP = {
    'TX': {
        'purchase_price': 'purchase_price',
        'taxing_units': 'taxing_unit_rates_per_100',
        'circuit_breaker': 'circuit_breaker',
        'prior_appraised_value': 'prior_appraised_value',
        'new_improvement_value': 'new_improvement_value',
        'special_assessments': 'special_assessments',
    },
    'CA': {
        'base_year': 'base_year',
        'base_year_value': 'base_year_value',
        'annual_cpi_factors': 'annual_cpi_factors',
        'ad_valorem_rate': 'ad_valorem_rate',
        'voter_approved_additions': 'voter_approved_additions',
        'ownership_change': 'ownership_change',
        'new_construction': 'new_construction',
    },
    'FL': {
        'unit_count': 'unit_count',
        'just_value': 'just_value',
        'prior_assessed_value': 'prior_assessed_value',
        'ownership_change_or_qualifying_improvement':
            'ownership_change_or_qualifying_improvement',
        'non_school_millage': 'non_school_millage',
        'school_millage': 'school_millage',
    },
    'AL': {
        'jurisdiction': 'jurisdiction',
        'fair_market_value': 'fair_market_value',
        'total_millage_mills': 'total_millage_mills',
    },
}

__all__ = [
    'bind_tax_policy', 'bind_tax_schedule', 'policy_sha256',
    'CONTRACT_VERSION', 'POLICY_CONTRACT_VERSION', 'ENGINE_SCHEDULE_PATH',
    'ENGINE_CONTRACT_VERSION', 'ERROR_CODES', 'SUPPORTED_STATES',
]


class _Refused(Exception):
    """Internal marker; converted to a typed refusal dict, never chained."""

    def __init__(self, code: str, details: dict | None = None):
        super().__init__(code)
        self.code = code
        self.details = details or {}


def _fail(code: str, details: dict | None = None) -> None:
    raise _Refused(code, details)


def _refusal(code: str, details: dict | None = None) -> dict:
    return {
        'status': 'refused',
        'code': code,
        'message': _MESSAGES.get(code, _MESSAGES['INVALID_CONTRACT']),
        'details': details or {},
        'engine_schedule': None,
        'engine_schedule_sha256': None,
        'bound_envelope': None,
    }


def policy_sha256(policy: Any) -> str:
    """Canonical-JSON SHA256 of the policy package (frozen contracts form)."""
    return contracts.canonical_sha256(policy)


def _text(value: Any, field: str) -> str:
    if (not isinstance(value, str) or not value.strip()
            or len(value) > _MAX_TEXT):
        _fail('INVALID_INPUT', {'field': field})
    return value


def _decimal_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or _RATE.fullmatch(value) is None:
        _fail('INVALID_INPUT', {'field': field})
    return value


def _validate_policy(policy: Any, *, expected_policy_sha256: str | None,
                     ) -> dict:
    """Gates 1-8; returns validated base facts. Never touches the engine."""
    if not isinstance(policy, dict):
        _fail('INVALID_INPUT', {'field': 'policy'})
    try:
        actual_sha = policy_sha256(policy)
    except HarnessError:
        _fail('INVALID_INPUT', {'field': 'policy'})
    if policy.get('contract_version') != POLICY_CONTRACT_VERSION:
        _fail('INVALID_INPUT', {'field': 'contract_version'})
    _text(policy.get('policy_id'), 'policy_id')
    subject = _text(policy.get('subject_id'), 'subject_id')
    if _SUBJECT.fullmatch(subject) is None:
        _fail('INVALID_INPUT', {'field': 'subject_id'})
    parcel = _text(policy.get('parcel_id'), 'parcel_id')
    if _PARCEL.fullmatch(parcel) is None:
        _fail('INVALID_INPUT', {'field': 'parcel_id'})
    state = _text(policy.get('state'), 'state').upper()
    tax_year = policy.get('tax_year')
    if isinstance(tax_year, bool) or not isinstance(tax_year, int):
        _fail('INVALID_INPUT', {'field': 'tax_year'})
    effective = _text(policy.get('effective_date'), 'effective_date')
    try:
        date.fromisoformat(effective)
    except ValueError:
        _fail('INVALID_INPUT', {'field': 'effective_date'})
    _text(policy.get('basis'), 'basis')
    if policy.get('requires_competent_human_review') is not True:
        _fail('INVALID_INPUT', {'field': 'requires_competent_human_review'})
    millage = policy.get('millage_rate_mills')
    if isinstance(millage, str) and millage.strip():
        _decimal_text(millage, 'millage_rate_mills')
    _decimal_text(policy.get('assessment_ratio'), 'assessment_ratio')
    purchase_price = policy.get('purchase_price')
    if purchase_price is not None:
        _decimal_text(purchase_price, 'purchase_price')
    return {'subject_id': subject, 'parcel_id': parcel, 'state': state,
            'tax_year': tax_year, 'policy_sha256': actual_sha}


def _validate_scope(facts: dict, *, subject_id: str | None,
                    parcel_id: str | None, tax_year: int | None) -> None:
    for field, requested, actual in (
            ('subject_id', subject_id, facts['subject_id']),
            ('parcel_id', parcel_id, facts['parcel_id']),
            ('tax_year', tax_year, facts['tax_year'])):
        if requested is not None and requested != actual:
            _fail('SCOPE_MISMATCH', {'field': field})


def _validate_drift(facts: dict, expected_policy_sha256: str | None) -> None:
    if expected_policy_sha256 is None:
        return
    if (not isinstance(expected_policy_sha256, str)
            or _SHA.fullmatch(expected_policy_sha256) is None):
        _fail('INVALID_INPUT', {'field': 'expected_policy_sha256'})
    if expected_policy_sha256 != facts['policy_sha256']:
        _fail('POLICY_DRIFT', {'field': 'policy_sha256'})


def _validate_t12_evidence(policy: dict) -> None:
    for field in ('source', 'source_locator'):
        value = policy.get(field)
        if isinstance(value, str) and _T12_PATTERN.search(value):
            _fail('T12_NOT_POLICY_EVIDENCE', {'field': field})


def _validate_rate_units(policy: dict, state: str) -> None:
    levy_unit = _text(policy.get('levy_unit'), 'levy_unit')
    if levy_unit != _REGIME_LEVY_UNITS[state]:
        _fail('RATE_UNIT_INCONSISTENT', {'field': 'levy_unit'})
    declared = policy.get('declared_unit_for_rate')
    if declared is not None and _text(declared, 'declared_unit_for_rate') \
            != levy_unit:
        _fail('RATE_UNIT_INCONSISTENT', {'field': 'declared_unit_for_rate'})
    units = policy.get('taxing_units')
    if units is None:
        return
    if not isinstance(units, list):
        _fail('INVALID_INPUT', {'field': 'taxing_units'})
    seen: set[str] = set()
    for entry in units:
        if not isinstance(entry, dict):
            _fail('INVALID_INPUT', {'field': 'taxing_units'})
        unit = _text(entry.get('unit'), 'taxing_units[].unit')
        if unit.strip().casefold() in _LEVY_UNIT_WORDS:
            _fail('RATE_UNIT_INCONSISTENT',
                  {'field': 'taxing_units[].unit'})
        if unit.strip() in seen:
            # A duplicate taxing unit would double-count the levy.
            _fail('RATE_UNIT_INCONSISTENT',
                  {'field': 'taxing_units[].unit'})
        seen.add(unit.strip())
        _decimal_text(entry.get('rate'), 'taxing_units[].rate')


def _validate_millage(policy: dict) -> None:
    millage = policy.get('millage_rate_mills')
    if millage is None or (isinstance(millage, str) and not millage.strip()):
        _fail('MILLAGE_MISSING', {'field': 'millage_rate_mills'})


def _validate_basis(policy: dict) -> None:
    basis = policy.get('purchase_price_basis')
    if basis is None or basis not in _EXPLICIT_BASES:
        _fail('INFERRED_BASIS_FORBIDDEN', {'field': 'purchase_price_basis'})


def _validate_override(policy: dict, approval_registry: dict | None) -> None:
    if not policy.get('analyst_override'):
        return
    record = policy.get('override_approval')
    if not isinstance(record, dict):
        _fail('APPROVAL_REQUIRED', {'field': 'analyst_override'})
    registry = approval_registry
    if registry is None:
        from plat_harness.adapters import review_bridge as rb
        registry = rb.host_registry()
    if not isinstance(registry, dict):
        _fail('INVALID_CONTRACT', {'field': 'approval_registry'})
    payload = {key: value for key, value in policy.items()
               if key != 'override_approval'}
    try:
        contracts._approval(record, payload, registry)
    except HarnessError as exc:
        code = exc.code if exc.code in ERROR_CODES else 'INVALID_CONTRACT'
        _fail(code, {'field': 'analyst_override'})


def _engine_inputs(policy: dict, tax_year: int | None = None) -> dict:
    """Route only the regime's mapped keys; the harness converts nothing.

    Free-text provenance (``source``/``source_locator``), millage labels,
    assessment-ratio labels, review records and approvals never reach the
    engine: the pinned engine contract validates its own inputs and owns
    every unit decision.
    """
    state = policy['state'].upper()
    inputs: dict[str, Any] = {
        'state': state,
        'tax_year': tax_year if tax_year is not None else policy['tax_year'],
    }
    for policy_key, engine_key in _ENGINE_INPUT_MAP[state].items():
        if policy_key in policy:
            inputs[engine_key] = deepcopy(policy[policy_key])
    return inputs


def _call_engine(engine_schedule: Callable, inputs: dict) -> dict:
    if not callable(engine_schedule):
        _fail('NOT_IMPLEMENTED', {'field': 'engine_schedule'})
    try:
        output = engine_schedule(deepcopy(inputs))
    except Exception:
        _fail('INVALID_CONTRACT')
    if not isinstance(output, dict):
        _fail('INVALID_CONTRACT')
    try:
        sha = contracts.canonical_sha256(output)
    except HarnessError:
        _fail('INVALID_CONTRACT')
    return output, sha


def _envelope(policy: dict, facts: dict, schedule_sha: str | None) -> dict:
    """Applicability/approval envelope; kept separate from engine objects."""
    return {
        'contract_version': CONTRACT_VERSION,
        'policy_id': policy['policy_id'],
        'state': facts['state'],
        'subject_id': facts['subject_id'],
        'parcel_id': facts['parcel_id'],
        'tax_year': facts['tax_year'],
        'effective_date': policy['effective_date'],
        'basis': policy['basis'],
        'purchase_price_basis': policy['purchase_price_basis'],
        'levy_unit': policy['levy_unit'],
        'requires_competent_human_review': True,
        'policy_sha256': facts['policy_sha256'],
        'engine_schedule_path': ENGINE_SCHEDULE_PATH,
        'engine_contract_version': ENGINE_CONTRACT_VERSION,
        'engine_schedule_sha256': schedule_sha,
    }


def _run_gates(policy: Any, *, subject_id: str | None,
               parcel_id: str | None, tax_year: int | None,
               expected_policy_sha256: str | None,
               approval_registry: dict | None) -> tuple[dict, dict]:
    """All fail-closed gates, in order, before any engine call."""
    facts = _validate_policy(policy,
                             expected_policy_sha256=expected_policy_sha256)
    _validate_scope(facts, subject_id=subject_id, parcel_id=parcel_id,
                    tax_year=tax_year)
    if facts['state'] not in SUPPORTED_STATES:
        _fail('UNSUPPORTED_TAX_REGIME', {'field': 'state'})
    _validate_drift(facts, expected_policy_sha256)
    _validate_t12_evidence(policy)
    _validate_rate_units(policy, facts['state'])
    _validate_millage(policy)
    _validate_basis(policy)
    _validate_override(policy, approval_registry)
    return facts, policy


def bind_tax_policy(policy: Any, *, subject_id: str | None = None,
                    parcel_id: str | None = None,
                    tax_year: int | None = None,
                    expected_policy_sha256: str | None = None,
                    engine_schedule: Callable | None = None,
                    approval_registry: dict | None = None) -> dict:
    """Bind one reviewed tax-policy package for one tax year.

    Returns ``{'status': 'bound', ...}`` with the engine schedule passed
    through verbatim plus a separate bound envelope, or a typed
    ``{'status': 'refused', 'code': ...}`` refusal. Refusals never raise,
    never chain and never echo policy values.
    """
    try:
        facts, validated = _run_gates(
            policy, subject_id=subject_id, parcel_id=parcel_id,
            tax_year=tax_year,
            expected_policy_sha256=expected_policy_sha256,
            approval_registry=approval_registry)
        inputs = _engine_inputs(validated, tax_year)
        schedule, schedule_sha = _call_engine(engine_schedule, inputs)
        return {
            'status': 'bound',
            'engine_schedule': deepcopy(schedule),
            'engine_schedule_sha256': schedule_sha,
            'bound_envelope': _envelope(validated, facts, schedule_sha),
        }
    except _Refused as refused:
        return _refusal(refused.code, refused.details)
    except HarnessError as exc:
        code = exc.code if exc.code in ERROR_CODES else 'INVALID_CONTRACT'
        return _refusal(code)
    except Exception:
        return _refusal('INVALID_CONTRACT')


def bind_tax_schedule(policy: Any, *, years: list[int],
                      expected_policy_sha256: str | None = None,
                      engine_schedule: Callable | None = None,
                      approval_registry: dict | None = None) -> dict:
    """Bind a consecutive multiyear schedule starting at the policy year.

    The first schedule year must be byte-identical to a fresh standalone
    first-year binding: any divergence between the embedded first-year
    output and a re-bound first year refuses with ``SCHEDULE_DIVERGENCE``
    and nothing is shipped.
    """
    try:
        facts, validated = _run_gates(
            policy, subject_id=None, parcel_id=None, tax_year=None,
            expected_policy_sha256=expected_policy_sha256,
            approval_registry=approval_registry)
        if (not isinstance(years, list) or not years):
            _fail('INVALID_INPUT', {'field': 'years'})
        for year in years:
            if isinstance(year, bool) or not isinstance(year, int):
                _fail('INVALID_INPUT', {'field': 'years'})
        if years[0] != facts['tax_year'] or any(
                years[i + 1] != years[i] + 1 for i in range(len(years) - 1)):
            _fail('INVALID_INPUT', {'field': 'years'})
        rows = []
        for year in years:
            inputs = _engine_inputs(validated, year)
            schedule, schedule_sha = _call_engine(engine_schedule, inputs)
            rows.append({
                'tax_year': year,
                'engine_schedule': deepcopy(schedule),
                'engine_schedule_sha256': schedule_sha,
            })
        # First-year output and the multiyear schedule cannot diverge
        # unnoticed: re-bind the first year and compare canonical bytes.
        first_inputs = _engine_inputs(validated, years[0])
        _, first_sha = _call_engine(engine_schedule, first_inputs)
        if first_sha != rows[0]['engine_schedule_sha256']:
            _fail('SCHEDULE_DIVERGENCE', {'field': 'years[0]'})
        return {
            'status': 'bound',
            'years': rows,
            'bound_envelope': _envelope(validated, facts, None),
        }
    except _Refused as refused:
        return _refusal(refused.code, refused.details)
    except HarnessError as exc:
        code = exc.code if exc.code in ERROR_CODES else 'INVALID_CONTRACT'
        return _refusal(code)
    except Exception:
        return _refusal('INVALID_CONTRACT')
