"""Synthetic tests for the reviewed-draft execution envelope (Task 3.4).

Self-contained public tests; no real deal bytes, no models, no network, no
import or execution of the engine repository. The equality oracle exercises
the envelope against a **stubbed** engine callable representing the existing
``engine.engine.run_underwriting`` contract; the real engine is never run by
this file. Approvals are synthetic host-registry fixtures that exercise the
frozen ``contracts._approval`` authority binding; they are NOT human
authorization for live-deal execution.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import time
from pathlib import Path

import pytest

from plat_harness import contracts
from plat_harness.adapters import slice_b as b
from plat_harness.errors import HarnessError

SUBJECT = 'synthetic_property'
RUN_ID = 'run_001'
POLICY_VERSION = '1.0.0'
ENVELOPE_VERSION = 'approved-execution/1.0.0'
ENGINE_PATH = 'engine.engine.run_underwriting'
RECON_PATH = 'runs.build_underwriting_reconciliation.main'
CANARY = 'PRIVATE_CANARY'


def api():
    """Import the Task 3.4 seam; the RED run may raise ModuleNotFoundError."""
    from plat_harness.adapters import approved_execution as module
    return module


def _enc(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


def _digest(value):
    return hashlib.sha256(value if isinstance(value, bytes) else _enc(value)).hexdigest()


def approval(payload, identity='execution-fixture'):
    """Synthetic host approval proof + registry record (mirrors contracts._approval)."""
    digest = contracts.canonical_sha256(payload)
    proof = {'approval_id': identity, 'actor_id': 'synthetic-human',
             'actor_type': 'human', 'approved_at': '2030-01-01T12:00:00+00:00',
             'reason': 'synthetic reviewed-draft fixture only',
             'record_locator': 'synthetic-host-registry#/approvals',
             'payload_sha256': digest}
    record = {identity: {'actor_id': 'synthetic-human', 'payload_sha256': digest,
                         'approval_sha256': contracts.canonical_sha256(proof)}}
    return proof, record


def classification_payload(identity, source, *, subject_id=SUBJECT, run_id=RUN_ID,
                          input_sha256=None, state='evidence_reviewed',
                          source_kind='reviewed_evidence', policy_version=POLICY_VERSION):
    """Classification contract payload bound to exact canonical bytes."""
    payload = {'contract_version': contracts.CLASSIFICATION_VERSION,
               'subject_id': subject_id, 'run_id': run_id,
               'input_sha256': input_sha256, 'deal_type': 'core',
               'candidates': ['core'], 'confidence': 0.9, 'ambiguity': [],
               'conflicting_signals': [], 'evidence': [source],
               'policy_pack': {'policy_id': 'synthetic-policy', 'version': policy_version},
               'state': state, 'reason': 'synthetic fixture, not live review',
               'review': None, 'certification': contracts.UNCERTIFIED}
    record = {}
    if state == 'evidence_reviewed':
        proof, record = approval({k: v for k, v in payload.items() if k != 'review'},
                                 identity)
        payload['review'] = proof
    return payload, record


def policy_payload(identity, source, *, subject_id=SUBJECT, status='approved',
                   millage='20.5'):
    """Approved policy-pack contract payload with a reviewed millage assumption."""
    payload = {'contract_version': contracts.POLICY_VERSION,
               'policy_id': 'synthetic-policy', 'version': POLICY_VERSION,
               'subject_id': subject_id, 'status': status,
               'assumptions': [{'name': 'millage_rate_mills', 'value': millage,
                                'unit': 'mills_per_1000', 'evidence': [source]}],
               'approval': None}
    record = {}
    if status == 'approved':
        proof, record = approval({k: v for k, v in payload.items() if k != 'approval'},
                                 identity)
        payload['approval'] = proof
    return payload, record


def synthetic_canonical():
    """Synthetic canonical engine-input bytes; not real deal bytes."""
    return {
        'schema_version': '0.1',
        'metadata': {
            'deal_id': SUBJECT, 'run_id': RUN_ID, 'as_of_date': '2026-01-31',
            'analyst': 'synthetic_analyst',
            'purpose': 'synthetic_approved_execution',
            'property_summary': {
                'property_tax_policy': {
                    'millage_rate_mills': '20.5', 'assessment_ratio': '0.85',
                    'source': 'synthetic_county_schedule',
                    'source_locator': 'synthetic/2026/millage/schedule-line-12',
                    'analyst_override': False}},
        },
        'time_grid': {'analysis_start_date': '2026-02',
                      'analysis_end_date': '2027-01'},
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


def stub_engine_output(inputs):
    """Synthetic stand-in for the EXISTING engine's deterministic output.

    Represents engine-owned economics only; the module under test never
    computes or alters any of these values.
    """
    return {
        'schema_version': inputs['schema_version'],
        'metrics': {
            'cash_on_cash': {'cash_on_cash_year_1_exact': '0.0461'},
            'noi': {'noi_year_1_exact': '702000.00'},
        },
        '_validator_report': {'status': 'PASS', 'rule_count': 2},
    }


def reviewed_source(input_sha256, *, pointer='/metadata/property_summary/property_tax_policy',
                    subject_id=SUBJECT):
    return {'artifact': 'inputs.json', 'sha256': input_sha256,
            'subject_id': subject_id, 'locator': {'json_pointer': pointer},
            'source_kind': 'reviewed_evidence'}


@pytest.fixture
def case():
    """A fully signed synthetic reviewed-draft request; no host environment set."""
    canonical = synthetic_canonical()
    canonical_bytes = _enc(canonical)
    input_sha256 = _digest(canonical_bytes)
    source = reviewed_source(input_sha256)
    classification, classification_record = classification_payload('cls-001', source,
                                                                   input_sha256=input_sha256)
    policy, policy_record = policy_payload('pol-001', source)
    evidence = {'classification': _enc(classification), 'policy': _enc(policy)}
    envelope = {
        'contract_version': ENVELOPE_VERSION,
        'subject_id': SUBJECT, 'run_id': RUN_ID, 'input_sha256': input_sha256,
        'policy_version': POLICY_VERSION,
        'synthetic': True, 'intake_validated': True,
        'analysis_window': canonical['time_grid'],
        'policy_sha256': _digest(evidence['policy']),
        'evidence_sha256': {k: _digest(v) for k, v in evidence.items()},
        'code_sha256': None, 'overrides': [], 'approval': None,
    }
    registry = dict(classification_record)
    registry.update(policy_record)
    return {
        'canonical': canonical, 'canonical_bytes': canonical_bytes,
        'input_sha256': input_sha256, 'source': source,
        'classification': classification, 'policy': policy,
        'evidence': evidence, 'envelope': envelope, 'registry': registry,
    }


def install(case):
    """Pin current code hashes and sign the envelope approval (synthetic host)."""
    module = api()
    envelope = dict(case['envelope'])
    envelope['code_sha256'] = module.code_pins()
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['envelope'] = envelope
    case['registry']['execution-fixture'] = record['execution-fixture']
    return envelope


def resign_envelope(case, canonical_bytes=None, evidence=None):
    """Rebind + re-sign the envelope after canonical/evidence mutation."""
    module = api()
    canonical_bytes = case['canonical_bytes'] if canonical_bytes is None else canonical_bytes
    evidence = case['evidence'] if evidence is None else evidence
    envelope = dict(case['envelope'])
    envelope['input_sha256'] = _digest(canonical_bytes)
    envelope['analysis_window'] = json.loads(canonical_bytes)['time_grid']
    envelope['evidence_sha256'] = {name: _digest(raw) for name, raw in evidence.items()}
    envelope['policy_sha256'] = envelope['evidence_sha256']['policy']
    envelope['code_sha256'] = module.code_pins()
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['envelope'] = envelope
    case['registry']['execution-fixture'] = record['execution-fixture']
    case['canonical_bytes'] = canonical_bytes
    case['evidence'] = evidence
    return envelope


def rebuild_classification(case, source=None, *, state='evidence_reviewed',
                          source_kind='reviewed_evidence', policy_version=POLICY_VERSION,
                          subject_id=SUBJECT, run_id=RUN_ID):
    source = dict(case['source'] if source is None else source)
    source['source_kind'] = source_kind
    classification, record = classification_payload(
        'cls-001', source, input_sha256=case['input_sha256'], state=state,
        source_kind=source_kind, policy_version=policy_version,
        subject_id=subject_id, run_id=run_id)
    case['evidence']['classification'] = _enc(classification)
    if record:
        case['registry']['cls-001'] = record['cls-001']
    case['classification'] = classification
    return classification


def rebuild_policy(case, *, status='approved', subject_id=SUBJECT, millage='20.5'):
    policy, record = policy_payload('pol-001', case['source'], status=status,
                                   subject_id=subject_id, millage=millage)
    case['evidence']['policy'] = _enc(policy)
    if record:
        case['registry']['pol-001'] = record['pol-001']
    case['policy'] = policy
    return policy


def host_env(monkeypatch, case, tmp_path):
    """Point the host registry env pins at a private 0600 registry file."""
    raw = b.encode(case['registry'])
    path = tmp_path / 'host-registry.json'
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH', str(path))
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', b.digest(raw))


def fresh_run_dir(tmp_path, name='run_001'):
    run_dir = tmp_path / 'runs' / name
    run_dir.mkdir(mode=0o700, parents=True)
    return run_dir


def ready(case, monkeypatch, tmp_path):
    """Install envelope + host registry env; return the module under test."""
    module = api()
    install(case)
    host_env(monkeypatch, case, tmp_path)
    return module


def execute(case, module, run_dir, *, engine=None, registry_loader=None, **overrides):
    kwargs = dict(envelope=case['envelope'], canonical=case['canonical_bytes'],
                  evidence=case['evidence'],
                  engine_callable=engine if engine is not None else
                  (lambda inputs, *, federation_mode=False: stub_engine_output(inputs)),
                  run_dir=str(run_dir), registry_loader=registry_loader)
    kwargs.update(overrides)
    return module.execute_reviewed_draft(**kwargs)


def refused(fn, code):
    with pytest.raises(HarnessError) as caught:
        fn()
    assert caught.value.code == code
    return caught.value


def assert_sanitized(exc):
    """Typed, sanitized refusals: no chained exceptions, no canary leakage."""
    assert exc.__cause__ is None and exc.__context__ is None
    assert CANARY not in json.dumps(exc.as_dict())


class RecordingEngine:
    """Stub representing the EXISTING engine's run_underwriting contract.

    The envelope runs the callable in a forked child, so calls are recorded to
    a shared file (the child's in-memory list is invisible to the parent).
    """

    def __init__(self, output, log_path):
        self.output = output
        self.log_path = Path(log_path)
        self.calls = []

    def __call__(self, inputs, *, federation_mode=False):
        if federation_mode is not True:
            raise AssertionError('the existing engine is called with federation_mode=True')
        with self.log_path.open('a') as log:
            log.write(json.dumps(inputs, sort_keys=True, default=str) + '\n')
        return json.loads(json.dumps(self.output))

    def recorded(self):
        if not self.log_path.exists():
            return []
        return [line for line in self.log_path.read_text().splitlines() if line]


# ----------------------------------------------------- seam + contract shape

def test_seam_exists():
    assert importlib.util.find_spec('plat_harness.adapters.approved_execution')


def test_contract_metadata_is_closed_and_versioned():
    module = api()
    assert module.ENVELOPE_VERSION == ENVELOPE_VERSION
    assert module.ENGINE_MODULE == 'engine.engine'
    assert module.ENGINE_FUNCTION == 'run_underwriting'
    assert module.RECON_MODULE == 'runs.build_underwriting_reconciliation'
    assert module.RECON_FUNCTION == 'main'
    assert module.ENGINE_PATH == ENGINE_PATH
    assert module.RECON_PATH == RECON_PATH
    assert module.SOURCE_ARTIFACTS == ('inputs.json',)
    assert module.BLOCKER == 'RECON_ECONOMICS_REQUIRED'
    assert module.STATUS == 'draft_pending_review'
    assert isinstance(module.ERROR_CODES, frozenset)
    assert module.ERROR_CODES == frozenset({
        'APPROVAL_MISMATCH', 'APPROVAL_REQUIRED', 'CLASSIFICATION_REVIEW_REQUIRED',
        'EVIDENCE_INCOMPLETE', 'EVIDENCE_LOCATOR_UNSUPPORTED', 'ENGINE_TIMEOUT',
        'HASH_MISMATCH', 'INVALID_CONTRACT', 'INVALID_ID', 'INVALID_INPUT',
        'LIVE_RUN_NOT_AUTHORIZED', 'NOT_FOUND', 'NOT_IMPLEMENTED', 'POLICY_CONFLICT',
        'POLICY_VERSION_MISMATCH', 'RECON_ECONOMICS_REQUIRED', 'REVIEW_REQUIRED',
        'RUN_CONFLICT', 'STALE', 'SUBJECT_MISMATCH', 'UNCERTIFIED_CLASSIFICATION',
        'UNCERTIFIED_METRIC', 'UNSAFE_PATH'})
    assert isinstance(module.RUN_METADATA, tuple) \
        and list(module.RUN_METADATA) == sorted(module.RUN_METADATA)
    assert isinstance(module.DEFAULT_ENGINE_TIMEOUT_S, float)
    assert 0 < module.DEFAULT_ENGINE_TIMEOUT_S <= 180


# ------------------------------------------- RED 1: synthetic scope fails closed

def test_live_purpose_refuses_before_engine_call(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    canonical = json.loads(case['canonical_bytes'])
    canonical['metadata']['purpose'] = 'live_underwriting'
    canonical_bytes = _enc(canonical)
    envelope = resign_envelope(case, canonical_bytes=canonical_bytes)
    engine = RecordingEngine(stub_engine_output(canonical), tmp_path / 'engine.log')

    def attempt():
        return module.execute_reviewed_draft(
            envelope=envelope, canonical=canonical_bytes, evidence=case['evidence'],
            engine_callable=engine, run_dir=str(fresh_run_dir(tmp_path)),
            registry_loader=None)

    exc = refused(attempt, 'LIVE_RUN_NOT_AUTHORIZED')
    assert engine.recorded() == []
    assert_sanitized(exc)


def test_non_synthetic_subject_prefix_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    canonical = json.loads(case['canonical_bytes'])
    canonical['metadata']['deal_id'] = 'subject_demo'
    canonical_bytes = _enc(canonical)
    envelope = resign_envelope(case, canonical_bytes=canonical_bytes)
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=canonical_bytes, evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'LIVE_RUN_NOT_AUTHORIZED')
    assert_sanitized(exc)


def test_subject_identity_mismatch_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    canonical = json.loads(case['canonical_bytes'])
    canonical['metadata']['deal_id'] = 'synthetic_other'
    canonical_bytes = _enc(canonical)
    envelope = resign_envelope(case, canonical_bytes=canonical_bytes)
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=canonical_bytes, evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'APPROVAL_MISMATCH')
    assert_sanitized(exc)


def test_run_identity_mismatch_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    canonical = json.loads(case['canonical_bytes'])
    canonical['metadata']['run_id'] = 'run_002'
    canonical_bytes = _enc(canonical)
    envelope = resign_envelope(case, canonical_bytes=canonical_bytes)
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=canonical_bytes, evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'APPROVAL_MISMATCH')
    assert_sanitized(exc)


def test_envelope_synthetic_flag_false_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['synthetic'] = False
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'LIVE_RUN_NOT_AUTHORIZED')
    assert_sanitized(exc)


def test_envelope_intake_not_validated_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['intake_validated'] = False
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'REVIEW_REQUIRED')
    assert_sanitized(exc)


# --------------------------- RED 2: mapping/policy review alone is insufficient

def test_policy_and_mapping_review_alone_stay_insufficient(case, monkeypatch, tmp_path):
    """Approved classification + policy still cannot certify; economics review gates."""
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    assert result['status'] == 'draft_pending_review'
    assert result['blocker'] == 'RECON_ECONOMICS_REQUIRED'
    assert result['certified'] is False
    assert result['certification_eligible'] is False
    assert result['economics'] == stub_engine_output(json.loads(case['canonical_bytes']))


def test_draft_policy_cannot_execute(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    rebuild_policy(case, status='draft')
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'APPROVAL_REQUIRED')
    assert_sanitized(exc)


def test_classification_review_required_cannot_execute(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    rebuild_classification(case, state='review_required')
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'CLASSIFICATION_REVIEW_REQUIRED')
    assert_sanitized(exc)


def test_broker_claim_classification_cannot_execute(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    rebuild_classification(case, source_kind='broker_claim')
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'CLASSIFICATION_REVIEW_REQUIRED')
    assert_sanitized(exc)


def test_policy_version_mismatch_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    rebuild_classification(case, policy_version='2.0.0')
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'POLICY_VERSION_MISMATCH')
    assert_sanitized(exc)


def test_policy_subject_mismatch_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    rebuild_policy(case, subject_id='synthetic_other')
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'SUBJECT_MISMATCH')
    assert_sanitized(exc)


# ------------------------------ RED 3: unsupported source-locator types refuse

@pytest.mark.parametrize('locator', [
    {'page': 2},
    {'sheet': 'T12', 'row': 5},
    {'row': 3},
    {'sha256': '0' * 64},
    'inputs.json:5',
])
def test_unsupported_locator_types_refuse(case, monkeypatch, tmp_path, locator):
    module = ready(case, monkeypatch, tmp_path)
    source = dict(case['source'])
    source['locator'] = locator
    rebuild_classification(case, source=source)
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'EVIDENCE_LOCATOR_UNSUPPORTED')
    assert_sanitized(exc)


def test_unknown_artifact_name_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    source = dict(case['source'])
    source['artifact'] = 'classification.json'
    rebuild_classification(case, source=source)
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'EVIDENCE_LOCATOR_UNSUPPORTED')
    assert_sanitized(exc)


def test_unresolvable_supported_pointer_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    source = reviewed_source(case['input_sha256'], pointer='/absent')
    rebuild_classification(case, source=source)
    resign_envelope(case)
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'EVIDENCE_INCOMPLETE')
    assert_sanitized(exc)


# --------------------- RED 4: missing economic reconciliation cannot certify

def test_missing_economic_reconciliation_never_certifies(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    manifest = json.loads(Path(result['artifact_dir'], 'manifest.json').read_bytes())
    assert manifest['certified'] is False
    assert manifest['certification_eligible'] is False
    assert manifest['recon_status'] == 'NOT_REVIEWED'
    assert manifest['status'] == 'draft_pending_review'
    assert manifest['blocker'] == 'RECON_ECONOMICS_REQUIRED'


def test_certified_manifest_claim_refuses_at_readback(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    artifact = Path(result['artifact_dir'])
    manifest_path = artifact / 'manifest.json'
    manifest = json.loads(manifest_path.read_bytes())
    manifest['certified'] = True
    manifest_path.write_bytes(_enc(manifest))
    manifest_path.chmod(0o600)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(artifact), registry_loader=None), 'UNCERTIFIED_METRIC')
    assert_sanitized(exc)


# ------------------- RED 5: exact pins at execution and re-verified at readback

def test_stale_code_pins_refuse(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['code_sha256'] = dict(envelope['code_sha256'])
    envelope['code_sha256']['contracts_sha256'] = '0' * 64
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'STALE')
    assert_sanitized(exc)


def test_input_digest_drift_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['input_sha256'] = '0' * 64
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'HASH_MISMATCH')
    assert_sanitized(exc)


def test_readback_rejects_input_drift(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    artifact = Path(result['artifact_dir'])
    inputs_path = artifact / 'inputs.json'
    tampered = json.loads(inputs_path.read_bytes())
    tampered['metadata']['analyst'] = 'another_analyst'
    inputs_path.write_bytes(_enc(tampered))
    inputs_path.chmod(0o600)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(artifact), registry_loader=None), 'HASH_MISMATCH')
    assert_sanitized(exc)


def test_readback_rejects_evidence_drift(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    artifact = Path(result['artifact_dir'])
    path = artifact / 'classification.json'
    tampered = json.loads(path.read_bytes())
    tampered['reason'] = 'mutated after execution'
    path.write_bytes(_enc(tampered))
    path.chmod(0o600)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(artifact), registry_loader=None), 'HASH_MISMATCH')
    assert_sanitized(exc)


def test_readback_rejects_economics_drift(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    artifact = Path(result['artifact_dir'])
    path = artifact / 'economics.json'
    tampered = json.loads(path.read_bytes())
    tampered['metrics']['noi']['noi_year_1_exact'] = '1.00'
    path.write_bytes(_enc(tampered))
    path.chmod(0o600)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(artifact), registry_loader=None), 'HASH_MISMATCH')
    assert_sanitized(exc)


def test_readback_rejects_manifest_pin_drift(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    artifact = Path(result['artifact_dir'])
    manifest_path = artifact / 'manifest.json'
    original = manifest_path.read_bytes()
    manifest = json.loads(original)
    manifest['input_sha256'] = '0' * 64
    manifest_path.write_bytes(_enc(manifest))
    manifest_path.chmod(0o600)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(artifact), registry_loader=None), 'HASH_MISMATCH')
    assert_sanitized(exc)
    manifest_path.write_bytes(original)
    manifest_path.chmod(0o600)


def test_readback_rejects_current_code_drift(case, monkeypatch, tmp_path):
    """Fault injection: current code pins differ from the executed envelope pins."""
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    drifted = dict(module.code_pins())
    drifted['contracts_sha256'] = '0' * 64
    monkeypatch.setattr(module, 'code_pins', lambda: drifted)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(result['artifact_dir']), registry_loader=None), 'STALE')
    assert_sanitized(exc)


def test_readback_reproduces_execute_result(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    run_dir = fresh_run_dir(tmp_path)
    result = execute(case, module, run_dir)
    assert module.read_reviewed_draft(run_dir=str(run_dir), registry_loader=None) == result
    artifact = Path(result['artifact_dir'])
    assert sorted(p.name for p in artifact.iterdir()) == sorted(module.ARTIFACT_NAMES)
    for path in artifact.iterdir():
        assert path.stat().st_mode & 0o077 == 0


# ------------- RED 6: registry membership reloaded per invocation; revocation

def test_missing_registry_env_refuses(case, monkeypatch, tmp_path):
    module = api()
    install(case)
    monkeypatch.delenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH', raising=False)
    monkeypatch.delenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', raising=False)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'APPROVAL_REQUIRED')
    assert_sanitized(exc)


def test_registry_pin_drift_refuses(case, monkeypatch, tmp_path):
    module = api()
    install(case)
    host_env(monkeypatch, case, tmp_path)
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', '0' * 64)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path)),
                  'APPROVAL_REQUIRED')
    assert_sanitized(exc)


def test_revoked_approval_blocks_execution(case, monkeypatch, tmp_path):
    module = api()
    install(case)
    case['registry'].pop('execution-fixture')
    host_env(monkeypatch, case, tmp_path)
    run_dir = fresh_run_dir(tmp_path)
    exc = refused(lambda: execute(case, module, run_dir),
                  'APPROVAL_REQUIRED')
    assert_sanitized(exc)
    assert not list(run_dir.iterdir())


def test_revocation_between_execution_and_readback_blocks(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    run_dir = fresh_run_dir(tmp_path)
    execute(case, module, run_dir)
    case['registry'].pop('execution-fixture')
    host_env(monkeypatch, case, tmp_path)
    exc = refused(lambda: module.read_reviewed_draft(
        run_dir=str(run_dir), registry_loader=None), 'APPROVAL_REQUIRED')
    assert_sanitized(exc)


def test_registry_loader_consulted_every_invocation(case, tmp_path):
    module = api()
    install(case)
    loads = []

    def counting_loader():
        loads.append(1)
        return dict(case['registry'])

    first = execute(case, module, fresh_run_dir(tmp_path, 'run_a'),
                    registry_loader=counting_loader)
    assert first['status'] == 'draft_pending_review'
    assert len(loads) == 1
    case['registry'].pop('execution-fixture')
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path, 'run_b'),
                                  registry_loader=counting_loader), 'APPROVAL_REQUIRED')
    assert len(loads) == 2
    assert_sanitized(exc)


def test_malformed_loader_registry_refuses(case, tmp_path):
    module = api()
    install(case)

    def bad_loader():
        return {'execution-fixture': {'actor_id': 'synthetic-human'}}

    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path),
                                  registry_loader=bad_loader), 'INVALID_CONTRACT')
    assert_sanitized(exc)


# --------------------------------------------- RED 7: the equality oracle

def test_equality_oracle_bridge_equals_direct_engine(case, monkeypatch, tmp_path):
    """Identical canonical bytes: bridge economics == direct existing-engine output."""
    module = ready(case, monkeypatch, tmp_path)
    engine = RecordingEngine(stub_engine_output(json.loads(case['canonical_bytes'])),
                             tmp_path / 'engine.log')
    direct_inputs = b.decode(case['canonical_bytes'])
    direct = engine(direct_inputs, federation_mode=True)
    result = execute(case, module, fresh_run_dir(tmp_path), engine=engine)
    assert result['economics'] == direct
    assert result['economics_sha256'] == b.digest(b.encode(direct))
    calls = engine.recorded()
    assert len(calls) == 2
    assert calls[0] == calls[1]
    manifest = json.loads(Path(result['artifact_dir'], 'manifest.json').read_bytes())
    assert manifest['economics_sha256'] == b.digest(b.encode(direct))
    persisted = Path(result['artifact_dir'], 'economics.json').read_bytes()
    assert persisted == b.encode(direct)  # byte-identical economics
    assert json.loads(persisted) == json.loads(b.encode(direct))


def test_equality_oracle_is_deterministic_across_runs(case, monkeypatch, tmp_path):
    """Two executions on identical canonical bytes differ only in executed_at."""
    module = ready(case, monkeypatch, tmp_path)
    first = execute(case, module, fresh_run_dir(tmp_path, 'run_a'))
    second = execute(case, module, fresh_run_dir(tmp_path, 'run_b'))
    assert first['economics'] == second['economics']
    assert first['economics_sha256'] == second['economics_sha256']
    manifest_a = json.loads(Path(first['artifact_dir'], 'manifest.json').read_bytes())
    manifest_b = json.loads(Path(second['artifact_dir'], 'manifest.json').read_bytes())
    assert manifest_a['executed_at'] != manifest_b['executed_at'] \
        or manifest_a == manifest_b
    strip = lambda m: {k: v for k, v in m.items() if k != 'executed_at'}
    assert strip(manifest_a) == strip(manifest_b)


# --------------------------------------------- RED 8: bounded execution only

def test_missing_engine_callable_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=case['envelope'], canonical=case['canonical_bytes'],
        evidence=case['evidence'], engine_callable=None,
        run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None), 'NOT_IMPLEMENTED')
    assert_sanitized(exc)


@pytest.mark.parametrize('bound', [None, 0, -1, 'x', float('nan')])
def test_engine_wait_bound_is_required(case, monkeypatch, tmp_path, bound):
    module = ready(case, monkeypatch, tmp_path)
    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path),
                                  engine_timeout_s=bound), 'INVALID_CONTRACT')
    assert_sanitized(exc)


def test_hanging_engine_times_out(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)

    def hanging_engine(inputs, *, federation_mode=False):
        time.sleep(0.6)
        return stub_engine_output(inputs)

    run_dir = fresh_run_dir(tmp_path)
    exc = refused(lambda: execute(case, module, run_dir,
                                  engine=hanging_engine, engine_timeout_s=0.1),
                  'ENGINE_TIMEOUT')
    assert_sanitized(exc)
    assert not list(run_dir.iterdir())


def test_engine_failure_is_sanitized(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)

    def failing_engine(inputs, *, federation_mode=False):
        raise RuntimeError('engine internals must not surface')

    exc = refused(lambda: execute(case, module, fresh_run_dir(tmp_path),
                                  engine=failing_engine), 'NOT_IMPLEMENTED')
    assert_sanitized(exc)
    assert 'engine internals' not in exc.message


# ------------------------------------- RED 9: the envelope grants nothing new

def test_overrides_are_not_implemented(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['overrides'] = [{'field': 'millage'}]
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'POLICY_CONFLICT')
    assert_sanitized(exc)


def test_unknown_envelope_key_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['economic_review'] = 'self-declared'
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'INVALID_CONTRACT')
    assert_sanitized(exc)


def test_wrong_envelope_version_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['contract_version'] = 'approved-execution/0.9.0'
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'INVALID_CONTRACT')
    assert_sanitized(exc)


def test_bad_identity_format_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    envelope = dict(case['envelope'])
    envelope['subject_id'] = 'bad subject!'
    envelope['approval'] = None
    proof, record = approval({k: v for k, v in envelope.items() if k != 'approval'})
    envelope['approval'] = proof
    case['registry']['execution-fixture'] = record['execution-fixture']
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=case['canonical_bytes'], evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'INVALID_ID')
    assert_sanitized(exc)


def test_evidence_set_mismatch_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    evidence = {'policy': case['evidence']['policy']}
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=case['envelope'], canonical=case['canonical_bytes'],
        evidence=evidence, engine_callable=None,
        run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'EVIDENCE_INCOMPLETE')
    assert_sanitized(exc)


def test_run_dir_conflict_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    run_dir = fresh_run_dir(tmp_path)
    (run_dir / 'manifest.json').write_bytes(b'{}')
    (run_dir / 'manifest.json').chmod(0o600)
    exc = refused(lambda: execute(case, module, run_dir), 'RUN_CONFLICT')
    assert_sanitized(exc)


def test_unsafe_run_dir_refuses(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    exc = refused(lambda: execute(case, module, '/tmp/outside_private_root'),
                  'UNSAFE_PATH')
    assert_sanitized(exc)


# ------------------------------------------- sanitized errors + canaries

def test_canary_never_surfaces_in_refusals(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    canonical = json.loads(case['canonical_bytes'])
    canonical['metadata']['analyst'] = CANARY
    canonical['metadata']['purpose'] = 'live_underwriting'
    canonical_bytes = _enc(canonical)
    envelope = resign_envelope(case, canonical_bytes=canonical_bytes)
    exc = refused(lambda: module.execute_reviewed_draft(
        envelope=envelope, canonical=canonical_bytes, evidence=case['evidence'],
        engine_callable=None, run_dir=str(fresh_run_dir(tmp_path)), registry_loader=None),
        'LIVE_RUN_NOT_AUTHORIZED')
    assert CANARY not in exc.message
    assert CANARY not in json.dumps(exc.details)
    assert_sanitized(exc)


def test_manifest_records_pinned_paths_and_metadata(case, monkeypatch, tmp_path):
    module = ready(case, monkeypatch, tmp_path)
    result = execute(case, module, fresh_run_dir(tmp_path))
    manifest = json.loads(Path(result['artifact_dir'], 'manifest.json').read_bytes())
    assert set(manifest) == set(module.RUN_METADATA)
    assert manifest['engine_path'] == ENGINE_PATH
    assert manifest['recon_path'] == RECON_PATH
    assert manifest['envelope_version'] == ENVELOPE_VERSION
    assert manifest['approval_id'] == 'execution-fixture'
    assert manifest['synthetic'] is True
    assert manifest['run_metadata_keys'] == list(module.RUN_METADATA)