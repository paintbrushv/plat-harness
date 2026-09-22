"""Synthetic immutable original bytes only; no files or source-room access."""
import copy
import hashlib
import importlib.util
import io
import json
import traceback

import pytest

from plat_harness.ingest import normalize_rent_roll
from plat_harness.ingest.compat import migrate_v1
from plat_harness.ingest.contracts import canonical_bytes

SID = 'src_' + '1' * 32
SUP = 'src_' + '2' * 32
SUBJECT = 'synthetic_asset'
DAY = '2026-01-01'
HEADER = 'Unit,Status,Unit Type,Property,As Of,Coverage,Status Definition\n'
ROW = '101,occupied,residential,synthetic_asset,2026-01-01,all_physical_units/1,occupied_vacant_down/1\n'
RAW = (HEADER + ROW).encode()
CANARY = 'PRIVATE_PERSON_987@example.invalid'


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def digest(value):
    return sha(json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode())


def api():
    assert importlib.util.find_spec('plat_harness.ingest.source_resolver') is not None
    from plat_harness.ingest import source_resolver
    assert callable(getattr(source_resolver, 'ByteSourceResolver', None))
    return source_resolver


def envelope(raw=RAW, subject=SUBJECT, day=DAY):
    old = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    return migrate_v1(old, original_bytes=raw, source_id=SID, subject_id=subject,
                      as_of=day, adapter_id='pms-flat-yardi')


def record(raw, sid=SID, subject=SUBJECT, day=DAY):
    return {'source_id': sid, 'sha256': sha(raw), 'role': 'original',
            'original_source_ids': [], 'subject_id': subject, 'as_of': day}


def citation(raw=RAW, sid=SID, row=2, column=2):
    return {'source_id': sid, 'source_sha256': sha(raw), 'sheet': 1,
            'row': row, 'row_end': row, 'column': column}


def resolver(env=None, originals=None, records=None, **overrides):
    env = envelope() if env is None else env
    originals = {SID: RAW} if originals is None else originals
    records = copy.deepcopy(env['sources']) if records is None else records
    args = dict(originals=originals, sources=records, subject_id=env['subject_id'],
                as_of=env['as_of'], adapter=env['adapter'], expected_envelope_sha256=digest(env),
                intake_provenance={s['source_id']: {'sha256': s['sha256'], 'role': 'original',
                    'intake_id': 'intake_' + '9' * 32} for s in records if s['role'] == 'original'})
    args.update(overrides)
    return api().ByteSourceResolver(**args)


def claim(raw=RAW, sid=SID, field='status', value='occupied', token='occupied'):
    return {'target': citation(column=1), 'field': field, 'value': value,
            'citation': citation(raw, sid, column=2 if field == 'status' else 3),
            'rule_id': 'status-token/1' if field == 'status' else 'use-token/1',
            'value_sha256': sha(token.encode())}


def test_real_source_cell_and_context_are_verified():
    r = resolver()
    result = r.verify(claim())
    assert result['state'] == 'verified'
    assert result['claim_sha256'] == digest(claim())
    assert result['citation'] == claim()['citation']
    assert result['verifier'] == api().SEMANTICS
    assert r.context()['expected_envelope_sha256'] == digest(envelope())
    assert CANARY not in json.dumps(result)


@pytest.mark.parametrize('change', [
    {'column': 3}, {'column': 1}, {'row': 1, 'row_end': 1},
    {'row': 999, 'row_end': 999}, {'row_end': 3}, {'sheet': 2},
])
def test_correct_hash_wrong_position_or_meaning_is_not_verified(change):
    c = claim()
    c['citation'].update(change)
    assert resolver().verify(c)['state'] == 'evidence_required'


@pytest.mark.parametrize('mutation', [
    {'value': 'vacant'}, {'value_sha256': '0' * 64}, {'rule_id': 'custom-prose'},
])
def test_wrong_value_fingerprint_and_rule_are_not_verified(mutation):
    c = claim() | mutation
    assert resolver().verify(c)['state'] == 'evidence_required'


def test_source_bytes_and_original_intake_are_host_pinned():
    for overrides in ({'originals': {SID: RAW + b'x'}}, {'intake_provenance': {}},
                      {'originals': {SID: bytearray(RAW)}},
                      {'expected_envelope_sha256': 'bad'}):
        with pytest.raises(api().SourceResolutionError) as caught:
            resolver(**overrides)
        assert caught.value.__context__ is None
        assert caught.value.__cause__ is None


def test_no_receipts_or_unrecognized_claim_keys():
    with pytest.raises(api().SourceResolutionError):
        resolver().verify(claim() | {'supported': True})


@pytest.mark.parametrize('raw,code', [
    (b'%PDF-1.7\n', 'UNSUPPORTED_FORMAT'), (b'PK\x03\x04', 'UNSUPPORTED_FORMAT'),
    (bytes.fromhex('d0cf11e0a1b11ae1'), 'UNSUPPORTED_FORMAT'),
    (RAW.replace(b'Status,Unit Type', b'Unit Type,Status'), 'UNSUPPORTED_LAYOUT'),
    (RAW.replace(b'101,', b'"101",'), 'UNSUPPORTED_LAYOUT'),
    (RAW.replace(b'101,', b'101\n,'), 'UNSUPPORTED_LAYOUT'),
    (RAW + ROW.encode(), 'AMBIGUOUS_UNIT_IDENTITY'),
    (RAW.replace(b'101,', CANARY.encode() + b','), 'UNIT_IDENTITY_REQUIRED'),
])
def test_unsupported_formats_and_ambiguous_layouts_are_evidence_requests(raw, code, capsys, caplog):
    env = envelope()
    supplement = record(raw, SUP)
    r = resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [supplement])
    result = r.verify(claim(raw, SUP))
    assert result == {'state': 'evidence_required', 'code': code}
    assert CANARY not in json.dumps(result) + capsys.readouterr().out + caplog.text


@pytest.mark.parametrize('token,value', [('current', 'occupied'), ('notice', 'occupied'),
    ('vacant', 'vacant'), ('down', 'down'), ('offline', 'down'), ('out of service', 'down')])
def test_supported_status_semantics_exact_tokens(token, value):
    raw = RAW.replace(b'occupied,residential', (token + ',residential').encode())
    env = envelope(raw)
    r = resolver(env, {SID: raw})
    c = claim(raw, field='status', value=value, token=token)
    c['target'] = citation(raw, column=1)
    assert r.verify(c)['state'] == 'verified'


@pytest.mark.parametrize('token', ['Admin/Down', 'admin', 'model', 'nonrevenue', 'future', '0', CANARY])
def test_admin_down_and_unsupported_tokens_are_not_observed_down(token, caplog):
    raw = RAW.replace(b'occupied,residential', (token + ',residential').encode())
    # The private bytes have a valid target identity but no verified status.
    env = envelope()
    supplement = record(raw, SUP)
    r = resolver(env, {SID: RAW, SUP: raw}, env['sources'] + [supplement])
    c = claim(raw, SUP, value='down', token=token)
    c['target'] = citation(raw, SUP, column=1)
    out = r.verify(c)
    assert out['state'] == 'evidence_required'
    assert CANARY not in json.dumps(out) + caplog.text


def test_bounded_byte_limits_and_immutable_snapshots():
    with pytest.raises(api().SourceResolutionError):
        resolver(originals={SID: b'x' * (api().MAX_SOURCE_BYTES + 1)})
    env = envelope()
    originals = {SID: RAW}
    records = copy.deepcopy(env['sources'])
    r = resolver(env, originals, records)
    originals[SID] = b'changed'
    records[0]['sha256'] = '0' * 64
    ctx = r.context()
    ctx['sources'][0]['sha256'] = '0' * 64
    assert r.verify(claim())['state'] == 'verified'
    assert r.context()['sources'] == env['sources']


def test_row_budget_and_safe_unknown_bytes(monkeypatch):
    monkeypatch.setattr(api(), 'MAX_SOURCE_ROWS', 1)
    raw = RAW + ROW.replace('101,', '102,').encode()
    env = envelope(raw)
    c = claim(raw)
    c['target'] = citation(raw, column=1)
    assert resolver(env, {SID: raw}).verify(c) == {'state': 'evidence_required', 'code': 'SOURCE_LIMIT'}
    bad = b'\xff' + CANARY.encode()
    r = resolver(envelope(), {SID: RAW, SUP: bad}, [record(RAW), record(bad, SUP)])
    assert r.verify(claim(bad, SUP)) == {'state': 'evidence_required', 'code': 'UNSUPPORTED_LAYOUT'}


def test_source_key_subclasses_never_execute_equality_hooks():
    touched = []
    class Key(str):
        __hash__ = str.__hash__
        def __eq__(self, other):
            touched.append('eq')
            return str.__eq__(self, other)
    supplied = {Key(SID): RAW}
    error = None
    try:
        resolver(originals=supplied)
    except api().SourceResolutionError as caught:
        error = caught.code
    assert error == 'INVALID_SOURCE_CONTEXT'
    assert touched == []


@pytest.mark.parametrize('field,other,expected', [
    ('unit_type', 'vacant', 'evidence_required'),
    ('unit_type', 'current', 'verified'),
    ('status', 'commercial', 'evidence_required'),
    ('status', 'apartment', 'verified'),
])
def test_verify_compares_both_recognized_fields_on_joined_row(field, other, expected):
    raw = RAW.replace(b'occupied,residential',
                      (other + ',apartment' if field == 'unit_type' else 'current,' + other).encode())
    r = resolver(originals={SID: RAW, SUP: raw}, records=[record(RAW), record(raw, SUP)])
    result = r.verify(claim(raw, SUP, field=field,
        value='residential' if field == 'unit_type' else 'occupied',
        token='apartment' if field == 'unit_type' else 'current'))
    assert result['state'] == expected
    if expected == 'evidence_required':
        assert result['code'] == 'SOURCE_CONTRADICTION'


@pytest.mark.parametrize('replacement,code', [
    (b'unknown,apartment', 'SOURCE_VALUE_UNSUPPORTED'),
    (b'Admin/Down,apartment', 'SOURCE_VALUE_UNSUPPORTED'),
    (CANARY.encode() + b',apartment', 'SOURCE_VALUE_UNSUPPORTED'),
])
def test_unknown_other_supplement_field_is_not_guessed(replacement, code, capsys, caplog):
    raw = RAW.replace(b'occupied,residential', replacement)
    r = resolver(originals={SID: RAW, SUP: raw}, records=[record(RAW), record(raw, SUP)])
    result = r.verify(claim(raw, SUP, field='unit_type', value='residential', token='apartment'))
    assert result == {'state': 'evidence_required', 'code': code}
    assert CANARY not in json.dumps(result) + capsys.readouterr().out + caplog.text


def test_use_mapping_also_requires_supplement_status_definition():
    raw = RAW.replace(b'residential', b'apartment').replace(b'occupied_vacant_down/1', b'admin_down/1')
    r = resolver(originals={SID: RAW, SUP: raw}, records=[record(RAW), record(raw, SUP)])
    assert r.verify(claim(raw, SUP, field='unit_type', value='residential', token='apartment')) == {
        'state': 'evidence_required', 'code': 'DOWN_DEFINITION_REQUIRED'}


@pytest.mark.parametrize('conflict', [False, True])
@pytest.mark.parametrize('reverse', [False, True])
def test_consistency_compares_supplements_pairwise_not_only_against_unknown_base(conflict, reverse):
    base = RAW.replace(b'occupied,residential', b'unknown,unknown')
    sid = 'src_' + '7' * 32
    extra = RAW.replace(b'occupied,residential', b'vacant,apartment' if conflict else b'current,apartment')
    r = resolver(envelope(base), {SID: base, SUP: RAW, sid: extra},
                 [record(base), record(RAW, SUP), record(extra, sid)])
    assert callable(getattr(r, 'consistency', None))
    target = citation(base, column=1)
    cites = [citation(RAW, SUP, column=3), citation(extra, sid, column=3)]
    if reverse:
        cites.reverse()
    result = r.consistency(target, cites)
    if conflict:
        assert result == {'state': 'evidence_required', 'code': 'SOURCE_CONTRADICTION'}
    else:
        assert result['state'] == 'verified'
        assert result['target_sha256'] == digest(target)
        assert result['citations_sha256'] == digest(cites)
        assert result['context_sha256'] == digest(r.context())
        assert result['verifier'] == api().SEMANTICS


@pytest.mark.parametrize('mutation,code', [
    ({'column': 1}, 'POSITION_MEANING_MISMATCH'),
    ({'sheet': 2}, 'POSITION_MEANING_MISMATCH'),
    ({'row_end': 3}, 'POSITION_MEANING_MISMATCH'),
])
def test_consistency_keeps_precise_field_locator_boundaries(mutation, code):
    r = resolver()
    assert callable(getattr(r, 'consistency', None))
    assert r.consistency(citation(column=1), [citation(column=3) | mutation]) == {
        'state': 'evidence_required', 'code': code}


def test_consistency_invalid_native_inputs_are_static_unchained(capsys, caplog):
    r = resolver()
    assert callable(getattr(r, 'consistency', None))
    for cites in ([citation(column=3) | {'source_sha256': '0' * 64}],
                  [citation(column=3) | {'row': True}], {'receipt': CANARY}):
        with pytest.raises(api().SourceResolutionError) as caught:
            r.consistency(citation(column=1), cites)
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        assert CANARY not in ''.join(traceback.format_exception(caught.value))
    assert CANARY not in capsys.readouterr().out + caplog.text


def test_source_native_subclasses_and_strict_citations_are_safe():
    class Bytes(bytes):
        def __len__(self):
            raise RuntimeError(CANARY)
    with pytest.raises(api().SourceResolutionError) as error:
        resolver(originals={SID: Bytes(RAW)})
    assert error.value.__context__ is None
    assert CANARY not in ''.join(traceback.format_exception(error.value))
    for value in (True, 2.0, '2', -1):
        c = claim()
        c['citation']['row'] = value
        with pytest.raises(api().SourceResolutionError):
            resolver().verify(c)
