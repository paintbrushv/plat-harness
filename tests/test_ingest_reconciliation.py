"""Host-authorized synthetic mapping; never manufacture production approval."""
import copy
import importlib.util
import json
import traceback

import pytest

from plat_harness import contracts as authority
from plat_harness.ingest.contracts import canonical_bytes
from test_ingest_source_resolver import (SID, SUP, RAW, ROW, HEADER, SUBJECT, DAY, CANARY,
    sha, digest, envelope, resolver, record, citation, api as source_api)


def api():
    assert importlib.util.find_spec('plat_harness.ingest.reconciliation') is not None
    from plat_harness.ingest import reconciliation
    assert callable(getattr(reconciliation, 'reconcile_observations', None))
    return reconciliation


def unknown():
    env = envelope()
    u = env['units'].pop()
    u['unit_type'] = None
    env['unknown_use_units'] = [u]
    env['status'] = 'blocked'
    for scope in env['completeness'].values():
        scope['enumeration'] = 'unknown'
        scope['counts'] = dict.fromkeys(('occupied', 'vacant', 'down', 'total'))
    env['issues'] = [{'issue_id': 'iss_' + '5' * 32, 'code': 'UNRESOLVED_UNIT_USE',
                      'severity': 'blocker', 'citation': citation(column=3),
                      'observation_ids': [u['observation_id']], 'summary_ids': []}]
    return env


def decision(env, **changes):
    u = (env['units'] + env['unknown_use_units'])[0]
    result = {'contract_version': 'ingest-mapping/1.0.0', 'decision_id': 'dec_' + '3' * 32,
        'envelope_sha256': digest(env), 'subject_id': env['subject_id'], 'as_of': env['as_of'],
        'adapter': copy.deepcopy(env['adapter']), 'sources': copy.deepcopy(env['sources']),
        'supplemental_sources': [], 'predecessor_sha256': None, 'supersedes': [],
        'semantics': copy.deepcopy(source_api().SEMANTICS), 'dimension': 'physical_unit',
        'changes': [{'observation_id': u['observation_id'], 'field': 'unit_type',
                     'before': u['unit_type'], 'after': 'residential', 'rule_id': 'use-token/1',
                     'citation': citation(column=3), 'value_sha256': sha(b'residential')}]}
    result.update(changes)
    return result


def approve(payload, registry):
    value = copy.deepcopy(payload)
    value.pop('approval', None)
    approval = {'approval_id': 'a-' + digest(value)[:32], 'actor_id': 'synthetic-reviewer',
        'actor_type': 'human', 'approved_at': '2026-01-02T00:00:00Z',
        'reason': CANARY, 'record_locator': 'host://synthetic/' + CANARY,
        'payload_sha256': authority.canonical_sha256(value)}
    registry[approval['approval_id']] = {'actor_id': approval['actor_id'],
        'payload_sha256': approval['payload_sha256'],
        'approval_sha256': authority.canonical_sha256(approval)}
    return value | {'approval': approval}


def run(env, decisions=(), registry=None, source=None):
    registry = {} if registry is None else registry
    return api().reconcile_observations(env, list(decisions), host_registry=lambda: registry,
                                       source_resolver=resolver(env) if source is None else source)


def test_exact_host_approval_separate_overlay_no_mutation_or_pii(capsys, caplog):
    env = unknown()
    frozen = canonical_bytes(env, subject_id=SUBJECT, as_of=DAY)
    registry = {}
    d = approve(decision(env), registry)
    out = run(env, [d], registry)
    assert out['contract_version'] == 'ingest-resolution/1.0.0'
    assert out['observations'] == env
    assert out['envelope_sha256'] == digest(env)
    assert out['resolutions'][0]['unit_type'] == 'residential'
    assert out['history'][0]['approval_sha256'] == digest(d['approval'])
    assert out['history'][0]['decision_sha256'] == digest(d)
    assert out == run(env, [d], registry)
    assert CANARY not in json.dumps(out) + capsys.readouterr().out + caplog.text
    assert canonical_bytes(env, subject_id=SUBJECT, as_of=DAY) == frozen
    assert not any(k in out for k in ('eligible_for_engine', 'certified', 'execution_permission'))


@pytest.mark.parametrize('attack', ['forged', 'removed', 'provenance', 'edited', 'machine'])
def test_existing_authority_refuses_forgery_and_revocation_without_chains(attack):
    env, registry = unknown(), {}
    d = approve(decision(env), registry)
    if attack == 'forged':
        d['approval']['approval_id'] = 'forged'
    elif attack == 'removed':
        registry.clear()
    elif attack == 'provenance':
        d['approval']['reason'] = 'changed'
    elif attack == 'edited':
        d['changes'][0]['after'] = 'commercial'
    else:
        d['approval']['actor_type'] = 'machine'
    frozen = copy.deepcopy(env)
    with pytest.raises(api().ReconciliationError) as error:
        run(env, [d], registry)
    assert error.value.__context__ is None and error.value.__cause__ is None
    assert CANARY not in ''.join(traceback.format_exception(error.value))
    assert env == frozen


def test_host_loader_is_called_again_and_mapping_is_not_a_loader():
    env, registry = unknown(), {}
    d = approve(decision(env), registry)
    calls = []
    def loader():
        calls.append(1)
        return registry
    r = resolver(env)
    api().reconcile_observations(env, [d], host_registry=loader, source_resolver=r)
    registry.clear()
    with pytest.raises(api().ReconciliationError):
        api().reconcile_observations(env, [d], host_registry=loader, source_resolver=r)
    assert len(calls) == 2
    with pytest.raises(api().ReconciliationError):
        api().reconcile_observations(env, [], host_registry={}, source_resolver=r)


@pytest.mark.parametrize('key,value', [
    ('envelope_sha256', '0' * 64), ('subject_id', 'other_asset'), ('as_of', '2026-02-01'),
    ('adapter', {'id': 'pms-flat-entrata', 'version': '1.0.0'}),
    ('adapter', {'id': 'pms-flat-yardi', 'version': '2.0.0'}), ('dimension', 'lease'),
    ('semantics', {'id': 'bounded-csv-physical', 'version': '2.0.0', 'sha256': '0' * 64}),
    ('sources', []),
])
def test_even_approved_cross_context_replay_refuses(key, value):
    env, registry = unknown(), {}
    d = approve(decision(env, **{key: value}), registry)
    with pytest.raises(api().ReconciliationError):
        run(env, [d], registry)


@pytest.mark.parametrize('key,value', [('observation_id', 'unit_' + '0' * 64),
                                     ('before', 'commercial'), ('rule_id', 'execute:prose')])
def test_approved_wrong_membership_before_and_rule_refuse(key, value):
    env, registry = unknown(), {}
    d = decision(env)
    d['changes'][0][key] = value
    d = approve(d, registry)
    with pytest.raises(api().ReconciliationError):
        run(env, [d], registry)


def test_approved_assertion_without_corroboration_stays_unknown():
    env, registry = unknown(), {}
    d = decision(env)
    d['changes'][0]['after'] = 'commercial'
    d = approve(d, registry)
    out = run(env, [d], registry)
    assert out['state'] == 'review_required'
    assert out['resolutions'][0]['unit_type'] is None
    assert out['evidence_requests']
    assert out['observations']['issues'] == env['issues']


def attempted_run(env, decisions, registry):
    try:
        return run(env, decisions, registry)
    except api().ReconciliationError as error:
        return error.code


def test_identical_duplicate_history_is_idempotent_and_changed_id_refuses():
    env, registry = unknown(), {}
    first = approve(decision(env), registry)
    assert attempted_run(env, [first, first], registry) == run(env, [first], registry)
    changed = copy.deepcopy(first)
    changed['predecessor_sha256'] = digest(first)
    changed = approve(changed, registry)
    with pytest.raises(api().ReconciliationError):
        run(env, [first, changed], registry)


def test_conflict_is_not_last_write_wins_and_supersession_is_explicit():
    env, registry = unknown(), {}
    first = approve(decision(env), registry)
    second = decision(env, decision_id='dec_' + '4' * 32, predecessor_sha256=digest(first))
    second['changes'][0]['after'] = 'commercial'
    second = approve(second, registry)
    out = attempted_run(env, [first, second], registry)
    assert type(out) is dict
    assert out['state'] == 'review_required'
    assert out['resolutions'][0]['unit_type'] is None
    assert 'DECISION_CONFLICT' in {r['code'] for r in out['evidence_requests']}
    third = approve(decision(env, decision_id='dec_' + '6' * 32,
                    predecessor_sha256=digest(second), supersedes=[digest(first), digest(second)]), registry)
    final = run(env, [first, second, third], registry)
    assert final['resolutions'][0]['unit_type'] == 'residential'
    assert [h['state'] for h in final['history']] == ['superseded', 'superseded', 'active']
    assert final['head_sha256'] == digest(third)
    assert final['observations'] == env
    assert final == run(env, [first, first, second, third, first], registry)


@pytest.mark.parametrize('kind', ['missing_previous', 'unknown_superseded', 'self', 'double_supersede',
                                  'different_membership', 'reordered', 'duplicate_supersedes'])
def test_broken_chains_and_nonexplicit_supersession_refuse(kind):
    env, registry = unknown(), {}
    first = approve(decision(env), registry)
    second = decision(env, decision_id='dec_' + '4' * 32, predecessor_sha256=digest(first),
                      supersedes=[digest(first)])
    if kind == 'missing_previous':
        second['predecessor_sha256'] = None
    elif kind in ('unknown_superseded', 'self'):
        second['supersedes'] = ['0' * 64]
    elif kind == 'different_membership':
        second['changes'][0].update(field='status', before='occupied', after='occupied',
                                   rule_id='status-token/1', citation=citation(column=2),
                                   value_sha256=sha(b'occupied'))
    elif kind == 'duplicate_supersedes':
        second['supersedes'] *= 2
    second = approve(second, registry)
    chain = [first, second]
    if kind == 'double_supersede':
        third = approve(decision(env, decision_id='dec_' + '6' * 32,
                         predecessor_sha256=digest(second), supersedes=[digest(first)]), registry)
        chain.append(third)
    elif kind == 'reordered':
        chain.reverse()
    with pytest.raises(api().ReconciliationError):
        run(env, chain, registry)


def test_revoked_superseded_approval_also_refuses():
    env, registry = unknown(), {}
    first = approve(decision(env), registry)
    second = approve(decision(env, decision_id='dec_' + '4' * 32,
                    predecessor_sha256=digest(first), supersedes=[digest(first)]), registry)
    assert type(attempted_run(env, [first, second], registry)) is dict
    del registry[first['approval']['approval_id']]
    with pytest.raises(api().ReconciliationError):
        run(env, [first, second], registry)


def test_actual_migrated_csv_and_source_supported_mapping_can_reconcile():
    base = envelope()
    out = run(base)
    assert out['state'] == 'reconciled'
    assert out['coverage']['state'] == 'verified'
    assert out['counts']['residential'] == dict(occupied=1, vacant=0, down=0, total=1)
    assert out['counts']['commercial'] == dict(occupied=0, vacant=0, down=0, total=0)
    assert out['observations'] == base
    env, registry = unknown(), {}
    d = approve(decision(env), registry)
    mapped = run(env, [d], registry)
    assert mapped['state'] == 'reconciled'
    assert mapped['dispositions'] == [{'issue_id': env['issues'][0]['issue_id'], 'state': 'resolved'}]
    assert mapped['observations']['issues'] == env['issues']
    assert mapped['evidence_requests'] == []


@pytest.mark.parametrize('raw,code', [
    (RAW.replace(b'all_physical_units/1', b'unknown'), 'COVERAGE_REQUIRED'),
    (RAW.replace(b'occupied_vacant_down/1', b'admin_down/1'), 'DOWN_DEFINITION_REQUIRED'),
])
def test_empty_commercial_and_admin_down_zero_never_establish_coverage(raw, code):
    env = envelope(raw)
    out = run(env, source=resolver(env, {SID: raw}))
    assert out['state'] == 'review_required'
    assert code in {item['code'] for item in out['evidence_requests']}
    assert all(v is None for counts in out['counts'].values() for v in counts.values())
    assert env['completeness']['commercial']['counts']['total'] == 0


def test_scope_is_not_inferred_even_when_csv_names_it():
    env = envelope(subject=None, day=None)
    out = run(env, source=resolver(env))
    assert out['state'] == 'review_required'
    assert 'SCOPE_REQUIRED' in {r['code'] for r in out['evidence_requests']}
    assert out['observations']['subject_id'] is None


def test_source_coverage_is_not_established_when_v2_omits_a_row():
    raw = RAW + ROW.replace('101,occupied', '102,vacant').encode()
    env = envelope(raw)
    env['units'].pop()
    env['completeness']['residential']['counts'] = dict(occupied=1, vacant=0, down=0, total=1)
    out = run(env, source=resolver(env, {SID: raw}))
    assert out['state'] == 'review_required'
    assert 'INVENTORY_MEMBERSHIP_MISMATCH' in {r['code'] for r in out['evidence_requests']}


@pytest.mark.parametrize('field,value', [('status', 'vacant'), ('unit_type', 'commercial')])
def test_base_v2_facts_are_source_checked_not_trusted(field, value):
    env = envelope()
    env['units'][0][field] = value
    for scope in env['completeness'].values():
        scope['enumeration'] = 'unknown'
        scope['counts'] = dict.fromkeys(('occupied', 'vacant', 'down', 'total'))
    out = run(env)
    assert out['state'] == 'review_required'
    assert out['resolutions'][0][field] is None
    assert 'BASE_FACT_UNVERIFIED' in {r['code'] for r in out['evidence_requests']}


def test_supplemental_original_joins_real_unit_without_mutating_ancestry():
    env, registry = unknown(), {}
    raw = RAW.replace(b'residential', b'apartment')
    supplement = record(raw, SUP)
    r = resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [supplement])
    payload = decision(env, supplemental_sources=[supplement])
    payload['changes'][0].update(citation=citation(raw, SUP, column=3), value_sha256=sha(b'apartment'))
    d = approve(payload, registry)
    out = run(env, [d], registry, r)
    assert out['state'] == 'reconciled'
    assert out['observations']['sources'] == env['sources']
    assert out['history'][0]['payload']['supplemental_sources'] == [supplement]
    assert '"101"' not in json.dumps(out)


@pytest.mark.parametrize('before,after', [('101', '102'), ('synthetic_asset', 'other_asset'),
                                         ('2026-01-01', '2026-02-01')])
def test_wrong_supplemental_unit_property_date_cannot_corroborate(before, after):
    env, registry = unknown(), {}
    raw = RAW.replace(before.encode(), after.encode())
    supplement = record(raw, SUP)
    r = resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [supplement])
    payload = decision(env, supplemental_sources=[supplement])
    payload['changes'][0]['citation'] = citation(raw, SUP, column=3)
    out = run(env, [approve(payload, registry)], registry, r)
    assert out['state'] == 'review_required'
    assert out['resolutions'][0]['unit_type'] is None
    assert all(v is None for counts in out['counts'].values() for v in counts.values())


def test_all_original_summaries_and_unresolved_blockers_stay_visible():
    env = envelope()
    env['issues'].append({'issue_id': 'iss_' + '8' * 32, 'code': 'DOWN_EVIDENCE_UNRESOLVED',
        'severity': 'blocker', 'citation': None, 'observation_ids': [], 'summary_ids': []})
    env['status'] = 'blocked'
    summary = env['summaries'][0]
    summary.update(status='unresolved', vendor_status_counts={'admin_down': 0},
                   citations=[{'admin_down': citation(column=2)}])
    out = run(env)
    assert out['state'] == 'review_required'
    assert out['observations'] == env
    assert {d['summary_id'] for d in out['summary_dispositions']} == {s['summary_id'] for s in env['summaries']}
    assert {'ORIGINAL_BLOCKER', 'SUMMARY_VERIFICATION_REQUIRED'} <= {r['code'] for r in out['evidence_requests']}
    assert out['dispositions'] == [{'issue_id': env['issues'][0]['issue_id'], 'state': 'retained'}]


def test_conflicting_supplemental_cannot_overwrite_verified_base_fact():
    env, registry = envelope(), {}
    raw = RAW.replace(b'residential', b'commercial')
    supplement = record(raw, SUP)
    payload = decision(env, supplemental_sources=[supplement])
    payload['changes'][0].update(after='commercial', citation=citation(raw, SUP, column=3),
                                 value_sha256=sha(b'commercial'))
    out = run(env, [approve(payload, registry)], registry,
              resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [supplement]))
    assert out['resolutions'][0]['unit_type'] is None
    assert 'SOURCE_CONTRADICTION' in {r['code'] for r in out['evidence_requests']}


def test_unimplemented_host_adapter_version_never_verifies_semantics():
    env = envelope()
    env['adapter']['version'] = '2.0.0'
    out = run(env)
    assert out['state'] == 'review_required'
    assert 'ADAPTER_UNSUPPORTED' in {r['code'] for r in out['evidence_requests']}


def test_production_request_uses_existing_host_registry_and_exact_json(monkeypatch, tmp_path):
    from plat_harness.adapters import review_bridge
    env, registry = unknown(), {}
    d = approve(decision(env), registry)
    raw = json.dumps(registry, sort_keys=True).encode()
    path = tmp_path / 'registry.json'
    path.write_bytes(raw)
    path.chmod(0o600)
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH', str(path))
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', sha(raw))
    assert review_bridge.host_registry() == registry
    assert callable(getattr(api(), 'reconcile_request', None))
    request = {'observations': env, 'decisions': [d]}
    result = api().reconcile_request(json.dumps(request).encode(), source_resolver=resolver(env))
    assert result == run(env, [d], registry)
    path.write_bytes(b'{}')
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', sha(b'{}'))
    with pytest.raises(api().ReconciliationError):
        api().reconcile_request(request, source_resolver=resolver(env))


@pytest.mark.parametrize('key', ['host_registry', 'registry', 'path', 'rank', 'approved',
                                 'source_resolver', 'resolver', 'supported', 'receipt'])
def test_request_cannot_supply_host_authority(key):
    assert callable(getattr(api(), 'reconcile_request', None))
    request = {'observations': envelope(), 'decisions': [], key: CANARY}
    with pytest.raises(api().ReconciliationError) as error:
        api().reconcile_request(request, source_resolver=resolver())
    assert CANARY not in ''.join(traceback.format_exception(error.value))
    assert error.value.__context__ is None


@pytest.mark.parametrize('raw', [
    '{"observations":{},"observations":{},"decisions":[]}',
    '{"observations":{},"decisions":[{"approval":{"reason":"a","reason":"b"}}]}',
    '{"observations":NaN,"decisions":[]}', '{"observations":1.0,"decisions":[]}',
    '[' * 17 + '0' + ']' * 17,
])
def test_duplicate_json_keys_nonfinite_float_and_nesting_refuse(raw):
    assert callable(getattr(api(), 'reconcile_request', None))
    with pytest.raises(api().ReconciliationError) as error:
        api().reconcile_request(raw, source_resolver=resolver())
    assert error.value.__context__ is None and error.value.__cause__ is None


def test_native_subclasses_never_execute_coercion_hooks():
    touched = []
    class Evil(str):
        def __eq__(self, other):
            touched.append('eq')
            raise ValueError(CANARY)
        def __str__(self):
            touched.append('str')
            raise ValueError(CANARY)
    env = envelope()
    for field in ('subject_id', 'as_of'):
        attacked = copy.deepcopy(env)
        attacked[field] = Evil(CANARY)
        with pytest.raises(api().ReconciliationError):
            run(attacked, source=resolver(env))
    assert touched == []
    class EvilDict(dict):
        def items(self):
            touched.append('items')
            raise ValueError(CANARY)
    with pytest.raises(api().ReconciliationError):
        run(env, source=EvilDict(supported=True))
    assert touched == []


@pytest.mark.parametrize('mutate', ['extra', 'bool', 'float', 'duplicate_member', 'long', 'many', 'cycle'])
def test_decision_schema_and_resource_limits_refuse(mutate):
    env, registry = unknown(), {}
    d = decision(env)
    if mutate == 'extra':
        d['approved'] = True
    elif mutate in ('bool', 'float'):
        d['changes'][0]['citation']['column'] = True if mutate == 'bool' else 3.0
    elif mutate == 'duplicate_member':
        d['changes'] *= 2
    elif mutate == 'long':
        d['dimension'] = 'x' * 129
    elif mutate == 'many':
        d['changes'] *= 257
    if mutate == 'cycle':
        d['changes'].append(d)
        signed = d
    else:
        signed = approve(d, registry)
    with pytest.raises(api().ReconciliationError) as error:
        run(env, [signed], registry)
    assert error.value.__context__ is None


def test_inherited_host_exceptions_are_static_unchained_and_not_logged(capsys, caplog):
    def broken():
        raise RuntimeError(CANARY)
    env = envelope()
    with pytest.raises(api().ReconciliationError) as error:
        api().reconcile_observations(env, [], host_registry=broken, source_resolver=resolver())
    assert error.value.__cause__ is None and error.value.__context__ is None
    assert CANARY not in str(error.value) + capsys.readouterr().out + caplog.text


def test_another_base_source_cannot_be_silently_excluded_from_coverage():
    env = envelope()
    raw = RAW.replace(b'101', b'102')
    extra = record(raw, SUP)
    env['sources'].append(extra)
    out = run(env, source=resolver(env, {SID: RAW, SUP: raw}, env['sources']))
    assert out['state'] == 'review_required'
    assert 'MULTISOURCE_COVERAGE_UNSUPPORTED' in {r['code'] for r in out['evidence_requests']}


def test_output_budget_refuses_instead_of_emitting_unbounded_overlay(monkeypatch):
    assert type(getattr(api(), 'MAX_OVERLAY_BYTES', None)) is int
    monkeypatch.setattr(api(), 'MAX_OVERLAY_BYTES', 1)
    with pytest.raises(api().ReconciliationError):
        run(envelope())


@pytest.mark.parametrize('field,other_token,expected', [
    ('unit_type', 'vacant', 'review_required'),
    ('unit_type', 'current', 'reconciled'),
    ('status', 'commercial', 'review_required'),
    ('status', 'apartment', 'reconciled'),
])
def test_relied_on_supplement_checks_other_counted_field(field, other_token, expected, capsys, caplog):
    env, registry = unknown() if field == 'unit_type' else envelope(), {}
    raw = RAW.replace(b'occupied,residential',
                      (other_token + ',apartment' if field == 'unit_type'
                       else 'current,' + other_token).encode())
    supplement = record(raw, SUP)
    payload = decision(env, supplemental_sources=[supplement])
    if field == 'status':
        payload['changes'][0].update(field='status', before='occupied', after='occupied',
                                    rule_id='status-token/1')
    payload['changes'][0].update(citation=citation(raw, SUP, column=3 if field == 'unit_type' else 2),
                                value_sha256=sha(b'apartment' if field == 'unit_type' else b'current'))
    frozen = canonical_bytes(env, subject_id=SUBJECT, as_of=DAY)
    out = run(env, [approve(payload, registry)], registry,
              resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [supplement]))
    assert out['state'] == expected
    if expected == 'review_required':
        assert 'SOURCE_CONTRADICTION' in {r['code'] for r in out['evidence_requests']}
        assert all(v is None for counts in out['counts'].values() for v in counts.values())
    else:
        assert out['evidence_requests'] == []
        assert out['counts']['residential'] == dict(occupied=1, vacant=0, down=0, total=1)
    assert out['observations'] == env
    assert canonical_bytes(env, subject_id=SUBJECT, as_of=DAY) == frozen
    assert CANARY not in json.dumps(out) + capsys.readouterr().out + caplog.text


@pytest.mark.parametrize('field', ['status', 'unit_type'])
@pytest.mark.parametrize('conflict', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_supplements_are_pairwise_consistent_even_when_base_field_unknown(field, conflict, reverse):
    # Both source and v2 are unknown in the compared field, not merely a null v2 enum.
    base = RAW.replace(b'occupied,residential', b'unknown,unknown')
    env, registry = envelope(base), {}
    # The legacy parser drops unsupported-status rows. Explicit synthetic v2
    # observations retain the unknown row without changing that frozen parser.
    u = copy.deepcopy(envelope()['units'][0])
    u['evidence'] = [{'unit_id': citation(base, column=1),
                      'status': citation(base, column=2), 'unit_type': citation(base, column=3)}]
    u['observation_id'] = 'unit_' + digest(u['evidence'][0]['unit_id'])
    u['unit_type'], u['status'] = None, None
    env['units'], env['unknown_use_units'] = [], [u]
    env['status'] = 'blocked'
    env['issues'] = [{'issue_id': 'iss_' + '5' * 32, 'code': 'UNRESOLVED_UNIT_USE',
        'severity': 'blocker', 'citation': citation(base, column=3),
        'observation_ids': [u['observation_id']], 'summary_ids': []}]
    for scope in env['completeness'].values():
        scope['enumeration'] = 'unknown'
        scope['counts'] = dict.fromkeys(('occupied', 'vacant', 'down', 'total'))
    second_sid = 'src_' + '7' * 32
    other = (b'vacant,apartment' if field == 'status' else b'current,commercial') if conflict else b'current,apartment'
    raws = {SID: base, SUP: RAW, second_sid: RAW.replace(b'occupied,residential', other)}
    supplements = [record(raws[sid], sid) for sid in (SUP, second_sid)]
    specs = [(SUP, 'unit_type', 'residential', 'residential'),
             (second_sid, 'unit_type', 'residential', 'apartment'),
             (SUP, 'status', 'occupied', 'occupied')]
    if field == 'unit_type':
        specs = [(SUP, 'status', 'occupied', 'occupied'),
                 (second_sid, 'status', 'occupied', 'current'),
                 (SUP, 'unit_type', 'residential', 'residential')]
    if reverse:
        specs.reverse()
    decisions = []
    for index, (sid, mapped_field, value, token) in enumerate(specs):
        payload = decision(env, decision_id='dec_' + str(index + 3) * 32,
                           supplemental_sources=supplements,
                           predecessor_sha256=digest(decisions[-1]) if decisions else None)
        payload['changes'][0].update(field=mapped_field, before=None, after=value,
            rule_id='status-token/1' if mapped_field == 'status' else 'use-token/1',
            citation=citation(raws[sid], sid, column=2 if mapped_field == 'status' else 3),
            value_sha256=sha(token.encode()))
        decisions.append(approve(payload, registry))
    frozen = canonical_bytes(env, subject_id=SUBJECT, as_of=DAY)
    out = run(env, decisions, registry, resolver(env, raws, env['sources'] + supplements))
    assert out['state'] == ('review_required' if conflict else 'reconciled')
    if conflict:
        assert 'SOURCE_CONTRADICTION' in {r['code'] for r in out['evidence_requests']}
        assert all(v is None for counts in out['counts'].values() for v in counts.values())
    else:
        assert out['evidence_requests'] == []
        assert out['counts']['residential'] == dict(occupied=1, vacant=0, down=0, total=1)
    assert out['observations'] == env
    assert canonical_bytes(env, subject_id=SUBJECT, as_of=DAY) == frozen


def test_authorized_but_unrelied_supplement_does_not_enter_consistency_scope():
    env = envelope()
    raw = RAW.replace(b'occupied,residential', b'vacant,commercial')
    out = run(env, source=resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [record(raw, SUP)]))
    assert out['state'] == 'reconciled'
    assert out['evidence_requests'] == []


@pytest.mark.parametrize('explanation', ['none', 'warning', 'unlinked_fields'])
def test_unexplained_root_blocker_cannot_be_cleared_by_field_evidence(explanation):
    env, registry = (unknown() if explanation == 'unlinked_fields' else envelope()), {}
    env['status'] = 'blocked'
    if explanation == 'warning':
        env['issues'] = [{'issue_id': 'iss_' + '8' * 32, 'code': 'DUPLICATE_UNIT_EVIDENCE',
            'severity': 'warning', 'citation': None, 'observation_ids': [], 'summary_ids': []}]
    elif explanation == 'unlinked_fields':
        env['issues'] = []
    frozen = canonical_bytes(env, subject_id=SUBJECT, as_of=DAY)
    decisions = [approve(decision(env), registry)] if explanation == 'unlinked_fields' else []
    out = run(env, decisions, registry)
    assert out['state'] == 'review_required'
    assert 'UNEXPLAINED_ROOT_BLOCKER' in {r['code'] for r in out['evidence_requests']}
    assert all(v is None for counts in out['counts'].values() for v in counts.values())
    assert out['observations'] == env
    assert canonical_bytes(env, subject_id=SUBJECT, as_of=DAY) == frozen


@pytest.mark.parametrize('retain_another', [False, True])
def test_explained_root_block_reconciles_only_after_all_blockers_resolved(retain_another):
    env, registry = unknown(), {}
    if retain_another:
        env['issues'].append({'issue_id': 'iss_' + '8' * 32, 'code': 'DOWN_EVIDENCE_UNRESOLVED',
            'severity': 'blocker', 'citation': None, 'observation_ids': [], 'summary_ids': []})
    frozen = canonical_bytes(env, subject_id=SUBJECT, as_of=DAY)
    before = run(env)
    assert before['state'] == 'review_required'
    assert all(v is None for counts in before['counts'].values() for v in counts.values())
    out = run(env, [approve(decision(env), registry)], registry)
    assert 'UNEXPLAINED_ROOT_BLOCKER' not in {r['code'] for r in out['evidence_requests']}
    assert out['dispositions'][0]['state'] == 'resolved'
    if retain_another:
        assert out['state'] == 'review_required'
        assert 'ORIGINAL_BLOCKER' in {r['code'] for r in out['evidence_requests']}
        assert all(v is None for counts in out['counts'].values() for v in counts.values())
    else:
        assert out['state'] == 'reconciled'
        assert out['evidence_requests'] == []
        assert out['counts']['residential'] == dict(occupied=1, vacant=0, down=0, total=1)
    assert out['observations'] == env and env['status'] == 'blocked'
    assert canonical_bytes(env, subject_id=SUBJECT, as_of=DAY) == frozen


def test_envelope_hash_binds_untargeted_issues_and_counts():
    env = envelope()
    r = resolver(env)
    env['issues'].append({'issue_id': 'iss_' + '8' * 32, 'code': 'DOWN_EVIDENCE_UNRESOLVED',
        'severity': 'blocker', 'citation': None, 'observation_ids': [], 'summary_ids': []})
    env['status'] = 'blocked'
    with pytest.raises(api().ReconciliationError):
        run(env, source=r)
