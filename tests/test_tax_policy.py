"""Task 4.3 RED-first tests for the harness-side tax-policy binding adapter.

Public synthetic tests only: no real parcels, bills, deal bytes, tenant rows,
private asset paths, models or network. Fixtures are synthetic tax-policy
envelopes for a fictional ``synthetic_tx_policy`` subject; canaries assert
that resident names never reach errors or envelopes.

Design constraints under test (from the Task 4.3 RED contract):
- Wrong parcel/asset/year refuses (SCOPE_MISMATCH / INVALID_INPUT).
- Reviewed-policy drift refuses (POLICY_DRIFT: the pinned sha256 of the
  approved regime package must match the package bytes at bind time AND at
  schedule time).
- Rate-unit confusion refuses (RATE_UNIT_INCONSISTENT: the policy declares
  its rate unit; mills/per-$100/per-$1,000 cannot disagree with the regime's
  statutory levy unit, and a unit word may never appear as a taxing-unit
  NAME).
- Unapproved analyst override refuses (APPROVAL_REQUIRED) — there is no
  second approval database; the frozen ``contracts._approval`` binding
  validates any override against a host registry.
- Missing mills refuses (MILLAGE_MISSING: a missing millage is a blocker,
  never zero).
- Inferred purchase-price basis refuses (INFERRED_BASIS_FORBIDDEN: the
  engine policy basis must be explicit; absent means blocked, never inferred
  from the price).
- A comparison T12 tax row cannot substitute for required policy evidence
  (T12_NOT_POLICY_EVIDENCE).
- First-year output and the multiyear schedule cannot diverge unnoticed
  (SCHEDULE_DIVERGENCE: the first year of the multiyear schedule must be
  byte-identical to the standalone first-year binding).

The engine is pinned by name (``engine.tax_regimes.build_tax_regime_schedule``,
contract ``engine-tax-regimes/1.0.0``) and is exercised only through a
host-bound callable — never imported, executed or modified in these tests
(the engine repository is read-only for harness work). No tax arithmetic is
performed here; the harness only selects and validates an approved package.
"""
from __future__ import annotations

import copy
import hashlib
import json

import pytest

from plat_harness.errors import HarnessError
from plat_harness.adapters import tax_policy as tp

CANARY = 'SyntheticResident CanaryName'


def stub_schedule(inputs):
    """Host-bound deterministic engine stub (never the real engine).

    Mirrors the pinned engine contract closely enough to prove routing:
    returns the inputs verbatim plus an engine-owned computed marker. The
    harness must pass the package through without altering any value.
    """
    return {'engine_contract': 'engine-tax-regimes/1.0.0',
            'echoed': copy.deepcopy(dict(inputs)),
            'computed_by_engine': True}


def approved_policy(**extra):
    """A minimal reviewed synthetic tax-policy package (all fields present)."""
    return {
        'contract_version': tp.POLICY_CONTRACT_VERSION,
        'policy_id': 'synthetic_policy_tx_001',
        'subject_id': 'synthetic_tx_policy',
        'parcel_id': 'synthetic-parcel-001',
        'state': 'TX',
        'tax_year': 2026,
        'effective_date': '2026-01-01',
        'basis': 'market_value_appraisal',
        'purchase_price_basis': 'explicit_appraised_value',
        'purchase_price': '10000000.00',
        'levy_unit': 'dollars_per_100_of_taxable_value',
        'taxing_units': [{'unit': 'Synthetic ISD', 'rate': '1.0700'},
                         {'unit': 'Synthetic County', 'rate': '0.3500'}],
        'millage_rate_mills': '14.2000',
        'assessment_ratio': '1.0000',
        'source': 'Synthetic Appraisal District',
        'source_locator': 'synthetic-notice-of-appraised-value.pdf#p1',
        'requires_competent_human_review': True,
        'review': {'reviewed': True, 'actor_id': 'actor-1', 'reviewed_at': '2026-09-21T00:00:00+00:00'},
        **extra,
    }


def approval_for(policy, actor_id='actor-1', approval_id='appr_synthetic_001'):
    payload = {k: v for k, v in policy.items() if k != 'approval'}
    return {'approval': {
        'approval_id': approval_id,
        'actor_id': actor_id,
        'actor_type': 'human',
        'approved_at': '2026-09-21T00:00:00+00:00',
        'reason': 'synthetic test approval',
        'record_locator': 'synthetic://registry/appr_synthetic_001',
        'payload_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True,
                                     separators=(',', ':')).encode()).hexdigest(),
    }}


def host_registry_for(policy):
    approval = approval_for(policy)['approval']
    registry = {approval['approval_id']: {
        'actor_id': approval['actor_id'],
        'payload_sha256': approval['payload_sha256'],
        'approval_sha256': hashlib.sha256(json.dumps(approval, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest(),
    }}
    return registry


# ---------------------------------------------------------------- seam + contract

def test_seam_exists():
    import importlib.util
    assert importlib.util.find_spec('plat_harness.adapters.tax_policy')


def test_contract_metadata_is_pinned():
    assert tp.CONTRACT_VERSION == 'tax-policy-binding/1.0.0'
    assert tp.POLICY_CONTRACT_VERSION == 'tax-regime-research/1.0.0'
    assert tp.ENGINE_SCHEDULE_PATH == 'engine.tax_regimes.build_tax_regime_schedule'
    assert tp.ENGINE_CONTRACT_VERSION == 'engine-tax-regimes/1.0.0'
    assert isinstance(tp.ERROR_CODES, frozenset)
    assert {'INVALID_INPUT', 'SCOPE_MISMATCH', 'UNSUPPORTED_TAX_REGIME',
            'POLICY_DRIFT', 'RATE_UNIT_INCONSISTENT', 'APPROVAL_REQUIRED',
            'MILLAGE_MISSING', 'INFERRED_BASIS_FORBIDDEN',
            'T12_NOT_POLICY_EVIDENCE', 'SCHEDULE_DIVERGENCE',
            'INVALID_CONTRACT'} <= tp.ERROR_CODES


# ---------------------------------------------------------------- happy path

def test_binding_returns_engine_output_with_bound_envelope():
    policy = approved_policy()
    bound = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert bound['status'] == 'bound'
    assert bound['engine_schedule']['computed_by_engine'] is True
    assert bound['engine_schedule']['echoed']['state'] == 'TX'
    assert bound['engine_schedule']['echoed']['taxing_unit_rates_per_100'] \
        == policy['taxing_units']
    # Engine output passes through verbatim; the harness alters nothing.
    assert json.dumps(bound['engine_schedule'], sort_keys=True) == \
        json.dumps(stub_schedule(tp._engine_inputs(policy)), sort_keys=True)
    envelope = bound['bound_envelope']
    assert envelope['policy_id'] == policy['policy_id']
    assert envelope['state'] == 'TX'
    assert envelope['subject_id'] == 'synthetic_tx_policy'
    assert envelope['parcel_id'] == 'synthetic-parcel-001'
    assert envelope['tax_year'] == 2026
    assert envelope['levy_unit'] == 'dollars_per_100_of_taxable_value'
    assert envelope['requires_competent_human_review'] is True
    # applicability/approval live in the SEPARATE bound envelope
    assert 'approval' not in json.dumps(bound['engine_schedule'])
    assert envelope['policy_sha256'] == tp.policy_sha256(policy)
    assert envelope['engine_contract_version'] == tp.ENGINE_CONTRACT_VERSION


def test_engine_inputs_route_regime_without_harness_arithmetic():
    policy = approved_policy()
    inputs = tp._engine_inputs(policy)
    assert inputs['state'] == 'TX'
    assert inputs['tax_year'] == 2026
    assert inputs['purchase_price'] == '10000000.00'
    assert inputs['taxing_unit_rates_per_100'] == [
        {'unit': 'Synthetic ISD', 'rate': '1.0700'},
        {'unit': 'Synthetic County', 'rate': '0.3500'},
    ]
    # The harness never computes a tax or a rate.
    assert 'tax' not in inputs or not isinstance(inputs.get('tax'), (int, float))


# ---------------------------------------------------------------- wrong parcel/asset/year

def test_wrong_subject_refuses():
    policy = approved_policy(subject_id='synthetic_tx_policy')
    result = tp.bind_tax_policy(policy, subject_id='other_subject',
                               engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'SCOPE_MISMATCH'


def test_wrong_parcel_refuses():
    policy = approved_policy()
    result = tp.bind_tax_policy(policy, parcel_id='different-parcel',
                               engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'SCOPE_MISMATCH'


def test_wrong_year_refuses():
    policy = approved_policy(tax_year=2026)
    result = tp.bind_tax_policy(policy, tax_year=2027,
                               engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'SCOPE_MISMATCH'


def test_default_subject_must_match_policy():
    policy = approved_policy()
    result = tp.bind_tax_policy(policy, subject_id='synthetic_tx_policy',
                               engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert result['status'] == 'bound'


def test_unsupported_state_refuses():
    policy = approved_policy(state='GA')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'UNSUPPORTED_TAX_REGIME'


# ---------------------------------------------------------------- policy drift

def test_policy_drift_refuses():
    policy = approved_policy()
    original_sha = tp.policy_sha256(policy)
    # Bind, then tamper with the reviewed package before the engine call
    # consumes it — the recorded sha must catch the drift.
    tampered = copy.deepcopy(policy)
    tampered['taxing_units'][0]['rate'] = '9.9900'
    assert tp.policy_sha256(tampered) != original_sha
    result = tp.bind_tax_policy(tampered, expected_policy_sha256=original_sha,
                                engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'POLICY_DRIFT'


def test_engine_input_drift_against_recorded_sha_refuses():
    # Even without an expected sha, the envelope's recorded sha must match
    # the bytes actually routed to the engine.
    policy = approved_policy()
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'bound'
    assert result['bound_envelope']['policy_sha256'] == tp.policy_sha256(policy)
    # Mutating the returned envelope's engine inputs cannot silently alter
    # the recorded provenance (deep-copy isolation).
    envelope = result['bound_envelope']
    before = envelope['policy_sha256']
    result['engine_schedule']['echoed']['taxing_unit_rates_per_100'][0]['rate'] = '0.0001'
    assert envelope['policy_sha256'] == before


def test_expected_sha_accepts_untampered_policy():
    policy = approved_policy()
    result = tp.bind_tax_policy(policy,
                               expected_policy_sha256=tp.policy_sha256(policy),
                               engine_schedule=stub_schedule,
                               approval_registry=host_registry_for(policy))
    assert result['status'] == 'bound'


# ---------------------------------------------------------------- rate-unit confusion

def test_mills_against_per_100_levy_unit_refuses():
    policy = approved_policy(levy_unit='dollars_per_100_of_taxable_value',
                            taxing_units=[{'unit': 'Synthetic ISD', 'rate': '0.0100'}])
    # The taxing-unit rate is in MILLS-scaled form (0.01 per 100 would be an
    # implausibly small per-$100 rate) while the declared unit is per-$100:
    # a numeric word alone is not proof, but a DECLARED unit field that
    # contradicts the millage_rate_mills evidence refuses.
    policy['millage_rate_mills'] = '14.2000'
    policy['declared_unit_for_rate'] = 'mills'
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'RATE_UNIT_INCONSISTENT'


def test_unit_word_as_taxing_unit_name_refuses():
    policy = approved_policy(taxing_units=[
        {'unit': 'mills', 'rate': '1.0700'},
        {'unit': 'Synthetic County', 'rate': '0.3500'}])
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'RATE_UNIT_INCONSISTENT'


def test_al_millage_declared_unit_must_be_mills():
    policy = approved_policy(state='AL', levy_unit='dollars_per_100_of_taxable_value',
                            jurisdiction='Synthetic County, AL',
                            total_millage_mills='27.5000',
                            fair_market_value='10000000.00',
                            declared_unit_for_rate='mills')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'RATE_UNIT_INCONSISTENT'


def test_tx_levy_unit_label_must_match_regime_statutory_unit():
    # TX levies are per $100 of taxable value (§§ 26.04, 26.09); a TX policy
    # declaring a per-$1,000 millage label is a unit confusion the harness
    # must refuse — it never converts between units.
    policy = approved_policy(levy_unit='mills_per_1000_of_taxable_value')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'RATE_UNIT_INCONSISTENT'


# ---------------------------------------------------------------- unapproved override

def test_unapproved_override_refuses():
    policy = approved_policy(analyst_override=True)
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'APPROVAL_REQUIRED'


def test_override_with_registry_entry_but_wrong_actor_refuses():
    policy = approved_policy(analyst_override=True)
    registry = host_registry_for(policy)
    registry['appr_synthetic_001']['actor_id'] = 'someone_else'
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=registry)
    assert result['status'] == 'refused'
    assert result['code'] == 'APPROVAL_REQUIRED'


def test_missing_registry_with_override_refuses():
    policy = approved_policy(analyst_override=True)
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry={})
    assert result['status'] == 'refused'
    assert result['code'] == 'APPROVAL_REQUIRED'


def test_approved_override_binds_with_host_registry_record():
    # Positive counterpart: an override carrying a human approval record
    # present in the host registry (frozen contracts._approval binding)
    # binds. Without this, a gate that refuses every override would pass.
    policy = approved_policy(analyst_override=True)
    payload = {k: v for k, v in policy.items() if k != 'override_approval'}
    approval = {
        'approval_id': 'appr_synthetic_001',
        'actor_id': 'actor-1',
        'actor_type': 'human',
        'approved_at': '2026-09-21T00:00:00+00:00',
        'reason': 'synthetic override approval',
        'record_locator': 'synthetic://registry/appr_synthetic_001',
        'payload_sha256': hashlib.sha256(json.dumps(payload, sort_keys=True,
                                         separators=(',', ':')).encode()).hexdigest(),
    }
    registry = {'appr_synthetic_001': {
        'actor_id': 'actor-1',
        'payload_sha256': approval['payload_sha256'],
        'approval_sha256': hashlib.sha256(json.dumps(approval, sort_keys=True,
                                          separators=(',', ':')).encode()).hexdigest(),
    }}
    policy['override_approval'] = approval
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=registry)
    assert result['status'] == 'bound'


def test_non_override_policy_needs_no_override_approval():
    policy = approved_policy()  # analyst_override absent → False
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'bound'


# ---------------------------------------------------------------- missing mills

def test_missing_millage_refuses():
    policy = approved_policy(millage_rate_mills=None)
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'MILLAGE_MISSING'
    # A missing millage is never zero: no engine call may be made.
    assert result['engine_schedule'] is None


def test_empty_millage_refuses():
    policy = approved_policy(millage_rate_mills='')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'MILLAGE_MISSING'


# ---------------------------------------------------------------- inferred purchase-price basis

def test_inferred_purchase_price_basis_refuses():
    policy = approved_policy(purchase_price_basis='inferred_from_price')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'INFERRED_BASIS_FORBIDDEN'


def test_absent_basis_is_blocked_not_inferred():
    policy = approved_policy()
    del policy['purchase_price_basis']
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'INFERRED_BASIS_FORBIDDEN'


# ---------------------------------------------------------------- T12 is not policy evidence

def test_t12_tax_row_cannot_substitute_for_policy_evidence():
    policy = approved_policy(source_locator='t12://synthetic/statements#property_taxes')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'T12_NOT_POLICY_EVIDENCE'


def test_explicit_t12_source_flag_refuses():
    policy = approved_policy(source='T12 property tax line')
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'T12_NOT_POLICY_EVIDENCE'


# ---------------------------------------------------------------- multiyear schedule

def test_multiyear_schedule_first_year_matches_standalone_binding():
    policy = approved_policy()
    years = [2026, 2027, 2028]
    multi = tp.bind_tax_schedule(
        policy, years=years, engine_schedule=stub_schedule,
        approval_registry=host_registry_for(policy))
    assert multi['status'] == 'bound'
    assert [row['tax_year'] for row in multi['years']] == years
    # First-year output and the multiyear schedule cannot diverge unnoticed.
    standalone = tp.bind_tax_policy(
        policy, tax_year=2026, engine_schedule=stub_schedule,
        approval_registry=host_registry_for(policy))
    assert json.dumps(multi['years'][0]['engine_schedule'], sort_keys=True) == \
        json.dumps(standalone['engine_schedule'], sort_keys=True)
    assert multi['years'][0]['engine_schedule_sha256'] == \
        standalone['bound_envelope']['engine_schedule_sha256']


def test_schedule_divergence_refuses():
    calls = []
    def flaky_schedule(inputs):
        # Same policy/year, different engine output on the second call:
        # a divergence between the first-year binding embedded in the
        # schedule and a fresh standalone binding must refuse.
        calls.append(copy.deepcopy(dict(inputs)))
        out = stub_schedule(inputs)
        out['drift_token'] = len(calls)
        return out
    policy = approved_policy()
    result = tp.bind_tax_schedule(policy, years=[2026, 2027],
                                  engine_schedule=flaky_schedule,
                                  approval_registry=host_registry_for(policy))
    # The flaky stub produced divergent outputs for identical inputs; the
    # adapter must detect rather than ship both.
    assert result['status'] == 'refused'
    assert result['code'] == 'SCHEDULE_DIVERGENCE'


def test_multiyear_year_mismatch_with_policy_refuses():
    policy = approved_policy(tax_year=2026)
    result = tp.bind_tax_schedule(policy, years=[2026, 2030],
                                  engine_schedule=stub_schedule,
                                  approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'INVALID_INPUT'


# ---------------------------------------------------------------- isolation + hygiene

def test_bind_result_is_deep_copied_from_module_state():
    policy = approved_policy()
    r1 = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                            approval_registry=host_registry_for(policy))
    # Tamper with a nested structure of one result; a fresh bind must not see it.
    r1['engine_schedule']['echoed']['taxing_unit_rates_per_100'][0]['unit'] = 'Tampered'
    r2 = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                            approval_registry=host_registry_for(policy))
    assert r2['engine_schedule']['echoed']['taxing_unit_rates_per_100'][0]['unit'] \
        == 'Synthetic ISD'


def test_canary_never_reaches_errors_or_envelope():
    policy = approved_policy(source=CANARY)
    result = tp.bind_tax_policy(policy, engine_schedule=stub_schedule,
                                approval_registry=host_registry_for(policy))
    blob = json.dumps(result)
    assert CANARY not in blob
    # A hostile policy payload with resident PII in a free-text field must
    # not be reflected into refusal details.
    hostile = approved_policy(source_locator='note:' + CANARY)
    refused = tp.bind_tax_policy(hostile, tax_year=2030,
                                 engine_schedule=stub_schedule,
                                 approval_registry=host_registry_for(hostile))
    assert refused['status'] == 'refused'
    assert CANARY not in json.dumps(refused)


def test_engine_failure_is_sanitized_no_chain():
    def broken_schedule(inputs):
        raise RuntimeError('internal path ' + CANARY + ' /tmp/private.xlsx')
    policy = approved_policy()
    result = tp.bind_tax_policy(policy, engine_schedule=broken_schedule,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'INVALID_CONTRACT'
    assert CANARY not in json.dumps(result)
    assert '/tmp/' not in json.dumps(result)


def test_unbound_engine_callable_refuses_not_implemented():
    # No deterministic engine schedule callable bound: the harness never
    # performs tax mathematics itself, so nothing is fabricated.
    policy = approved_policy()
    result = tp.bind_tax_policy(policy, engine_schedule=None,
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'NOT_IMPLEMENTED'
    assert result['engine_schedule'] is None


def test_non_callable_engine_schedule_refuses_not_implemented():
    policy = approved_policy()
    result = tp.bind_tax_policy(policy, engine_schedule='engine.py',
                                approval_registry=host_registry_for(policy))
    assert result['status'] == 'refused'
    assert result['code'] == 'NOT_IMPLEMENTED'


def test_no_engine_import_in_module_source():
    import plat_harness.adapters.tax_policy as module
    import re
    source = open(module.__file__).read()
    assert not re.search(r'^\s*(import|from)\s+(engine|subprocess|socket)\b',
                         source, re.M)


def test_engine_not_on_sys_path_through_adapter():
    # The adapter must never import the engine repository.
    import sys
    import plat_harness.adapters.tax_policy as module
    before = set(sys.modules)
    module.ENGINE_SCHEDULE_PATH  # merely reading the pin must not import
    assert 'engine.tax_regimes' not in sys.modules
    assert 'engine' not in sys.modules
