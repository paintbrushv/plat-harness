"""Migration integration uses public synthetic parser bytes, never deal fixtures."""
import copy
import hashlib
import importlib.util
import io
import json
import traceback

import pytest

from plat_harness.ingest import normalize_rent_roll
from plat_harness.ingest.contracts import canonical_bytes, observation_id, validate_observations
from test_pms_normalizer import HEADERS, CANARIES, csv_stream, xlsx_stream, rows, unit
from test_pms_onesite import fixture_raw, CANARIES as BIFF_CANARIES

SOURCE = 'src_' + '1' * 32
SCOPES = ('residential', 'commercial')
HOST = {'source_id': SOURCE, 'subject_id': None, 'as_of': None}


def api():
    assert importlib.util.find_spec('plat_harness.ingest.compat') is not None
    from plat_harness.ingest import compat
    assert callable(getattr(compat, 'migrate_v1', None))
    return compat


def migrate(value, raw, adapter='pms-flat-yardi', **kwargs):
    return api().migrate_v1(value, original_bytes=raw, adapter_id=adapter, **(HOST | kwargs))


def cite(value):
    return None if value is None else {'source_id': SOURCE, **value}


def groups(values):
    return [{field: cite(locator) for field, locator in group.items()} for group in values]


def check_preserved(old, new):
    """Independent field-by-field oracle, including ordering and multiplicity."""
    assert validate_observations(new, subject_id=new['subject_id'], as_of=new['as_of']) == new
    assert new['sources'] == [{'source_id': SOURCE, 'sha256': old['source_sha256'],
        'role': 'original', 'original_source_ids': [],
        'subject_id': new['subject_id'], 'as_of': new['as_of']}]
    scoped = [u for scope in SCOPES for u in old[scope + '_units']]
    unknown = [i['observation'] for i in old['issues'] if i['code'] == 'UNRESOLVED_UNIT_USE']
    for originals, projected in ((scoped, new['units']), (unknown, new['unknown_use_units'])):
        assert len(originals) == len(projected)
        for source, target in zip(originals, projected):
            assert target == {'observation_id': observation_id(cite(source['evidence'][0]['unit_id'])),
                'source_id': SOURCE, 'unit_type': source['unit_type'], 'status': source['status'],
                'evidence': groups(source['evidence'])}
    original_summaries = [old['summary'][scope] for scope in SCOPES]
    original_summaries += [i['observation'] for i in old['issues'] if i['code'] == 'ONESITE_REPORT_SUMMARY']
    assert len(new['summaries']) == len(original_summaries)
    for index, (source, target) in enumerate(zip(original_summaries, new['summaries'])):
        assert target['scope'] == (SCOPES[index] if index < 2 else 'unknown')
        assert target['status'] == source['status']
        assert target['reported_counts'] == source['reported_counts']
        assert target['row_derived_counts'] == source.get('row_derived_counts')
        assert target['vendor_status_counts'] == source.get('vendor_status_counts', {})
        assert target['matched_fields'] == source.get('matched_fields', [])
        citations = source['citations']
        if type(citations) is dict:
            citations = [citations] if citations else []
        assert target['citations'] == groups(citations)
    assert len(new['issues']) == len(old['issues'])
    unknown_index = summary_index = 0
    for source, target in zip(old['issues'], new['issues']):
        assert (target['code'], target['severity'], target['citation']) == (
            source['code'], source['severity'], cite(source['citation']))
        units, summaries = [], []
        if source['code'] == 'UNRESOLVED_UNIT_USE':
            units = [new['unknown_use_units'][unknown_index]['observation_id']]
            unknown_index += 1
        if source['code'] == 'ONESITE_REPORT_SUMMARY':
            summaries = [new['summaries'][2 + summary_index]['summary_id']]
            summary_index += 1
        assert target['observation_ids'] == units
        assert target['summary_ids'] == summaries
    for scope in SCOPES:
        target = new['completeness'][scope]
        assert target['counts'] == old['counts'][scope]
        assert target['coverage'] == 'unknown' and target['coverage_citations'] == []
        expected = 'unknown' if all(v is None for v in old['counts'][scope].values()) else 'complete'
        assert target['enumeration'] == expected
    assert new['status'] == {'blocked':'blocked', 'normalized_unvalidated':'observed_unvalidated'}[old['status']]
    encoded = canonical_bytes(new, subject_id=new['subject_id'], as_of=new['as_of']).decode()
    assert '[REDACTED]' not in encoded
    assert all(canary not in encoded for canary in (*CANARIES, *BIFF_CANARIES))
    assert len({i['issue_id'] for i in new['issues']}) == len(new['issues'])
    assert len({s['summary_id'] for s in new['summaries']}) == len(new['summaries'])


@pytest.mark.parametrize('vendor', ['yardi', 'realpage', 'entrata'])
@pytest.mark.parametrize('container', ['csv', 'xlsx'])
def test_actual_flat_parser_projection(vendor, container):
    raw = (xlsx_stream(rows(vendor)) if container == 'xlsx' else csv_stream(rows(vendor), True)).getvalue()
    original = normalize_rent_roll(io.BytesIO(raw), vendor)
    frozen = copy.deepcopy(original)
    result = migrate(original, raw, 'pms-flat-' + vendor)
    check_preserved(original, result)
    assert result['adapter'] == {'id': 'pms-flat-' + vendor, 'version': '1.0.0'}
    assert original == frozen
    assert result == migrate(original, raw, 'pms-flat-' + vendor)
    assert all(s['row_derived_counts'] is None and s['matched_fields'] == [] for s in result['summaries'])
    result['units'][0]['evidence'][0]['status']['row'] = 999
    assert original == frozen


def test_actual_biff_moves_unknown_units_and_summary_exactly_once():
    raw = fixture_raw()
    original = normalize_rent_roll(io.BytesIO(raw), 'realpage')
    try:
        result = migrate(original, raw, 'onesite-detailed-realpage')
    except api().MigrationError as error:
        result = error.code
    assert type(result) is dict, 'Exact OneSite parser output must be representable'
    check_preserved(original, result)
    assert len(result['unknown_use_units']) == 3 and result['units'] == []
    assert len(result['summaries']) == 3
    assert result['status'] == 'blocked'
    report = result['summaries'][2]
    assert report['matched_fields'] == ['occupied', 'vacant', 'total']
    assert report['reported_counts']['down'] is None
    assert report['row_derived_counts']['down'] is None
    assert report['vendor_status_counts']['admin_down'] == 0
    assert result == migrate(original, raw, 'onesite-detailed-realpage')


@pytest.mark.parametrize('biff', [False, True])
def test_independent_preservation_ledger_and_tamper_detection(biff):
    raw = fixture_raw() if biff else csv_stream(rows(), True).getvalue()
    adapter = 'onesite-detailed-realpage' if biff else 'pms-flat-yardi'
    original = normalize_rent_roll(io.BytesIO(raw), 'realpage' if biff else 'yardi')
    result = migrate(original, raw, adapter)
    verifier = getattr(api(), 'verify_preservation', None)
    assert callable(verifier), 'Public byte-backed independent preservation verifier required'
    ledger = verifier(original, result, original_bytes=raw, adapter_id=adapter, **HOST)
    assert ledger['policy_version'] == 'v1-preservation/1.0.0'
    assert ledger['retained_v1_sha256'] == ledger['reconstructed_v1_sha256']
    assert ledger['v2_sha256'] == hashlib.sha256(canonical_bytes(result, subject_id=None, as_of=None)).hexdigest()
    assert ledger['observations'] == len(result['units']) + len(result['unknown_use_units'])
    assert ledger['issues'] == len(original['issues'])
    assert ledger['summaries'] == len(result['summaries'])
    assert 'unit_id' not in ledger
    assert all(c not in json.dumps(ledger) for c in (*CANARIES, *BIFF_CANARIES))
    changed = copy.deepcopy(result)
    target = (changed['unknown_use_units'] if biff else changed['units'])[0]
    target['evidence'][0]['status']['column'] += 1  # Still valid v2; wrong preservation.
    validate_observations(changed, subject_id=None, as_of=None)
    with pytest.raises(api().MigrationError) as caught:
        verifier(original, changed, original_bytes=raw, adapter_id=adapter, **HOST)
    assert caught.value.code == 'PRESERVATION_MISMATCH'


def test_migration_runs_independent_ledger_before_returning(monkeypatch):
    raw = csv_stream(rows(), True).getvalue()
    original = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    original_project = api()._project
    def lossy(*args):
        result = original_project(*args)
        result['units'][0]['evidence'][0]['status']['column'] += 1
        return result
    monkeypatch.setattr(api(), '_project', lossy)
    with pytest.raises(api().MigrationError) as caught:
        migrate(original, raw)
    assert caught.value.code == 'PRESERVATION_MISMATCH'


def flat_case(name):
    values = rows()
    expected = set()
    if name == 'mismatch':
        values[11][4] = 999
        expected = {'SUMMARY_MISMATCH'}
    elif name == 'invalid-summary':
        values[11][1] = 'not-a-count'
        expected = {'INVALID_SUMMARY'}
    elif name == 'conflicting-summary':
        values.append(['Residential', 1, 2, 1, 4])
        expected = {'SUMMARY_CONFLICT'}
    elif name == 'duplicate-summary':
        values.append(['Residential', 2, 1, 1, 4])
        expected = {'DUPLICATE_SUMMARY_EVIDENCE'}
    elif name == 'unresolved-summary':
        values[3][1] = ''
        expected = {'UNSUPPORTED_STATUS'}
    elif name == 'continuations':
        values = [HEADERS['yardi'], unit(), unit(), unit(status='', kind='', role='charge')]
        expected = {'DUPLICATE_UNIT_EVIDENCE', 'CONTINUATION_EVIDENCE'}
    elif name == 'conflicting-units':
        values = [HEADERS['yardi'], unit(), unit(status='Vacant')]
        expected = {'DUPLICATE_UNIT_CONFLICT', 'NO_CURRENT_UNITS'}
    elif name == 'orphan':
        values = [HEADERS['yardi'], unit(role='charge')]
        expected = {'ORPHAN_CONTINUATION'}
    elif name == 'future':
        values = [HEADERS['yardi'], unit(), unit('102', 'Applicant'), ['Future Residents'], unit('103')]
        expected = {'FUTURE_OR_APPLICANT_EXCLUDED'}
    elif name == 'nulls':
        values = [HEADERS['yardi'], unit(kind='Unrecognized')]
        expected = {'UNSUPPORTED_UNIT_TYPE'}
    elif name == 'empty':
        values = [HEADERS['yardi']]
        expected = {'NO_CURRENT_UNITS'}
    elif name == 'multiline':
        values = [['Synthetic\nReport'], HEADERS['yardi'], unit()]
        values[2][3] = 'Synthetic\nTenant'
    elif name == 'commercial-only':
        values = [HEADERS['yardi'], unit('R1', 'Down', 'Retail')]
    else:
        raise AssertionError('Unknown synthetic case')
    return values, expected


@pytest.mark.parametrize('case', ['mismatch', 'invalid-summary', 'conflicting-summary',
    'duplicate-summary', 'unresolved-summary', 'continuations', 'conflicting-units',
    'orphan', 'future', 'nulls', 'empty', 'multiline', 'commercial-only'])
@pytest.mark.parametrize('container', ['csv', 'xlsx'])
def test_actual_flat_edges_preserve_evidence_nulls_and_issue_order(case, container):
    values, expected = flat_case(case)
    raw = (xlsx_stream(values, extra_sheet=True) if container == 'xlsx' else csv_stream(values, True)).getvalue()
    original = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    assert expected <= {i['code'] for i in original['issues']}
    if container == 'xlsx':
        assert any(i['citation'] is None for i in original['issues'])
    result = migrate(original, raw)
    check_preserved(original, result)
    api().verify_preservation(original, result, original_bytes=raw, adapter_id='pms-flat-yardi', **HOST)


def synthetic_biff_variant(case):
    """Edit embedded BIFF bytes, not reader results or a fake workbook.

    Only this fixture's contiguous Workbook stream, ASCII SST entries,
    RK integer cells and LABELSST coordinates are supported by this test helper.
    """
    import struct
    from xlrd.compdoc import CompDoc
    raw = fixture_raw()
    def string(before, after):
        nonlocal raw
        after = after.ljust(len(before))
        assert len(after) == len(before)
        token = struct.pack('<HB', len(before), 0) + before.encode('ascii')
        replacement = struct.pack('<HB', len(after), 0) + after.encode('ascii')
        assert raw.count(token) == 1
        raw = raw.replace(token, replacement)
    if case == 'unknown-status':
        string('Occupied', 'Model')
    elif case == 'conflicting-unit':
        string('103', '101')
    elif case == 'duplicate-unit':
        string('102', '101')
        string('Occupied-NTVL', 'Occupied')
    elif case == 'future':
        string('Occupied-NTVL', 'Future')
    elif case == 'absent-summary':
        string('Unit Status', 'No summary')
    elif case == 'missing-boundary':
        string('Totals:', 'NoEnd')
    elif case in ('mismatch', 'invalid-summary', 'orphan'):
        mem, base, length = CompDoc(raw, logfile=io.StringIO()).locate_named_stream('Workbook')
        assert mem is raw  # Never invent a file offset for a fragmented stream.
        result = bytearray(raw)
        position, edits = base, 0
        target_row, target_col = (13, 35) if case == 'orphan' else (30 if case == 'mismatch' else 24, 20)
        while position < base + length:
            opcode, size = struct.unpack_from('<HH', raw, position)
            if opcode in (0x027E, 0x00FD) and struct.unpack_from('<HH', raw, position + 4) == (target_row, target_col):
                if case == 'orphan':
                    assert opcode == 0x00FD
                    struct.pack_into('<H', result, position + 4, 10)
                else:
                    assert opcode == 0x027E
                    value = 4 if case == 'mismatch' else -1
                    struct.pack_into('<i', result, position + 10, (value << 2) | 2)
                edits += 1
            position += 4 + size
        assert edits == 1
        raw = bytes(result)
    else:
        raise AssertionError('Unknown synthetic BIFF case')
    assert raw != fixture_raw()
    return raw


@pytest.mark.parametrize('case,code', [('unknown-status', 'UNSUPPORTED_UNIT_STATUS'),
    ('conflicting-unit', 'DUPLICATE_UNIT_CONFLICT'), ('duplicate-unit', 'DUPLICATE_UNIT_EVIDENCE'),
    ('future', 'NON_CURRENT_LEASE_EXCLUDED'), ('orphan', 'ORPHAN_CONTINUATION'),
    ('mismatch', 'SUMMARY_MISMATCH'), ('invalid-summary', 'INVALID_VENDOR_SUMMARY'),
    ('absent-summary', 'VENDOR_SUMMARY_ABSENT'), ('missing-boundary', 'INVENTORY_BOUNDARY_MISSING')])
def test_actual_biff_edge_bytes_preserved_without_reader_mocks(case, code):
    raw = synthetic_biff_variant(case)
    original = normalize_rent_roll(io.BytesIO(raw), 'realpage')
    assert code in {issue['code'] for issue in original['issues']}
    result = migrate(original, raw, 'onesite-detailed-realpage')
    check_preserved(original, result)
    api().verify_preservation(original, result, original_bytes=raw, adapter_id='onesite-detailed-realpage', **HOST)


def refused(value, raw, *, code=None, adapter='pms-flat-yardi', **kwargs):
    with pytest.raises(api().MigrationError) as caught:
        migrate(value, raw, adapter, **kwargs)
    error = caught.value
    if code is not None:
        assert error.code == code
    assert error.__cause__ is error.__context__ is None
    assert 'PRIVATE_CANARY' not in repr(error) + ''.join(traceback.format_exception(error))
    return error


@pytest.mark.parametrize('change', ['hash', 'version', 'vendor', 'counts', 'bool-count',
    'citation', 'bool-citation', 'unit-id', 'placeholder', 'issue', 'summary', 'unknown-key',
    'order', 'payload', 'derivative-claim'])
def test_native_v1_claims_must_be_exact_actual_parser_output(change):
    raw = csv_stream(rows(), True).getvalue()
    value = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    if change == 'hash': value['source_sha256'] = '0' * 64
    elif change == 'version': value['version'] = '1.1'
    elif change == 'vendor': value['pms_type'] = 'entrata'
    elif change == 'counts': value['counts']['residential']['occupied'] = 9
    elif change == 'bool-count': value['counts']['commercial']['occupied'] = True
    elif change == 'citation': value['residential_units'][0]['evidence'][0]['status']['row'] = 999
    elif change == 'bool-citation': value['residential_units'][0]['evidence'][0]['unit_id']['column'] = True
    elif change == 'unit-id': value['residential_units'][0]['unit_id'] = '999'
    elif change == 'placeholder': value['residential_units'][0]['tenant_name'] = 'PRIVATE_CANARY'
    elif change == 'issue': value['issues'].append({'code': 'NO_CURRENT_UNITS', 'severity': 'blocker', 'citation': None})
    elif change == 'summary': value['summary']['residential']['status'] = 'unresolved'
    elif change == 'unknown-key': value['PRIVATE_CANARY'] = None
    elif change == 'order': value['residential_units'].reverse()
    elif change == 'payload': value['issues'].append({'code': 'UNSUPPORTED_STATUS', 'severity': 'blocker', 'citation': None, 'observation': {'PRIVATE_CANARY': None}})
    elif change == 'derivative-claim': value['role'] = 'derivative'
    refused(value, raw)


@pytest.mark.parametrize('kind', ['bool', 'float', 'nan', 'inf', 'negative', 'huge-int',
    'tuple', 'object', 'subclass', 'key-subclass', 'str-subclass', 'int-subclass',
    'bytes', 'depth', 'cycle', 'width', 'long-string', 'surrogate'])
def test_bounded_native_tree_refuses_before_hooks_or_parser(monkeypatch, kind):
    raw = csv_stream(rows(), True).getvalue()
    value = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    touched = []
    class Hook:
        def __eq__(self, other):
            touched.append('eq')
            raise RuntimeError('PRIVATE_CANARY')
        def __repr__(self):
            touched.append('repr')
            raise RuntimeError('PRIVATE_CANARY')
    class D(dict):
        def items(self):
            touched.append('items')
            raise RuntimeError('PRIVATE_CANARY')
    class S(str):
        pass
    class I(int):
        pass
    options = {'bool': True, 'float': 1.0, 'nan': float('nan'), 'inf': float('inf'),
        'negative': -1, 'huge-int': 10**100, 'tuple': (), 'object': Hook(), 'subclass': D(),
        'key-subclass': {S('key'): None}, 'str-subclass': S('value'), 'int-subclass': I(1),
        'bytes': b'PRIVATE_CANARY', 'width': [None] * 50001, 'long-string': 'x' * 129,
        'surrogate': '\ud800'}
    bad = options.get(kind)
    if kind == 'depth':
        bad = None
        for _ in range(18):
            bad = [bad]
    elif kind == 'cycle':
        bad = []
        bad.append(bad)
    value['issues'] = bad
    def parser(*args):
        touched.append('parser')
        raise RuntimeError('PRIVATE_CANARY')
    monkeypatch.setattr(api(), 'normalize_rent_roll', parser)
    refused(value, raw)
    assert touched == []


@pytest.mark.parametrize('adapter', ['pms-flat-realpage', 'pms-flat-yardi', 'pms-flat-entrata'])
def test_biff_cannot_masquerade_as_flat_adapter(adapter):
    raw = fixture_raw()
    value = normalize_rent_roll(io.BytesIO(raw), 'realpage')
    refused(value, raw, adapter=adapter, code='ADAPTER_SIGNATURE_MISMATCH')


@pytest.mark.parametrize('adapter', ['onesite-detailed-realpage', 'yardi', 'pms-flat-Yardi',
    'pms-flat-yardi/1.0.0', 'PRIVATE_CANARY', None])
def test_adapter_identity_is_exact_and_signature_gated(adapter):
    raw = csv_stream(rows(), True).getvalue()
    value = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    refused(value, raw, adapter=adapter, code='ADAPTER_SIGNATURE_MISMATCH' if adapter == 'onesite-detailed-realpage' else 'UNSUPPORTED_ADAPTER')


@pytest.mark.parametrize('original', [None, 'PRIVATE_CANARY', bytearray(b'csv'), io.BytesIO(b'csv')])
def test_missing_original_bytes_and_derivative_only_paths_unavailable(original):
    raw = csv_stream(rows(), True).getvalue()
    value = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    refused(value, original, code='ORIGINAL_LINEAGE_REQUIRED')


def test_original_bytes_subclasses_and_size_are_refused():
    class B(bytes):
        pass
    raw = csv_stream(rows(), True).getvalue()
    value = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    refused(value, B(raw), code='ORIGINAL_LINEAGE_REQUIRED')
    refused(value, b'x' * (8 * 1024 * 1024 + 1), code='INPUT_LIMIT_EXCEEDED')
    refused(value, raw + b'\n', code='SOURCE_HASH_MISMATCH')


@pytest.mark.parametrize('raw,adapter', [(b'PRIVATE_CANARY', 'pms-flat-yardi'),
    (b'%PDF-1.4 PRIVATE_CANARY', 'pms-flat-yardi'),
    (b'PK\x03\x04PRIVATE_CANARY', 'pms-flat-entrata'),
    (bytes.fromhex('d0cf11e0a1b11ae1') + b'PRIVATE_CANARY', 'onesite-detailed-realpage')])
def test_parser_refusal_sanitized_without_raw_library_context(raw, adapter, capsys):
    refused({'source_sha256': hashlib.sha256(raw).hexdigest()}, raw, adapter=adapter, code='PARSER_REFUSED')
    assert 'PRIVATE_CANARY' not in ''.join(capsys.readouterr())


def test_original_hash_cannot_substitute_for_original_position_lineage():
    raw = csv_stream(rows(), True).getvalue()
    old = normalize_rent_roll(io.BytesIO(raw), 'yardi')
    derivative = b'\n' + raw
    # Replacing only the claimed digest cannot repair generated-file positions.
    old['source_sha256'] = hashlib.sha256(derivative).hexdigest()
    refused(old, derivative, code='PARSER_OUTPUT_MISMATCH')


@pytest.mark.parametrize('subject,as_of', [(None, None), ('subject_demo', None),
    (None, '2026-01-31'), ('subject_demo', '2026-01-31')])
def test_only_explicit_host_scope_is_carried_and_reverified(subject, as_of):
    raw = fixture_raw()
    old = normalize_rent_roll(io.BytesIO(raw), 'realpage')
    result = migrate(old, raw, 'onesite-detailed-realpage', subject_id=subject, as_of=as_of)
    assert (result['subject_id'], result['as_of']) == (subject, as_of)
    api().verify_preservation(old, result, original_bytes=raw, adapter_id='onesite-detailed-realpage',
                              **(HOST | {'subject_id': subject, 'as_of': as_of}))
    for different in ({'subject_id': 'other'}, {'as_of': '2026-02-01'}):
        with pytest.raises(api().MigrationError) as caught:
            api().verify_preservation(old, result, original_bytes=raw, adapter_id='onesite-detailed-realpage',
                                      **(HOST | {'subject_id': subject, 'as_of': as_of} | different))
        assert caught.value.code == 'PRESERVATION_MISMATCH'


@pytest.mark.parametrize('metadata', [{'source_id': '../PRIVATE_CANARY'}, {'source_id': 'src_' + 'A' * 32},
    {'subject_id': True}, {'subject_id': '../PRIVATE_CANARY'}, {'as_of': '2026-02-30'},
    {'as_of': '2026-1-1'}, {'as_of': False}])
def test_invalid_host_metadata_not_normalized(metadata):
    raw = csv_stream(rows(), True).getvalue()
    refused(normalize_rent_roll(io.BytesIO(raw), 'yardi'), raw, code='INVALID_V1', **metadata)


def test_unknown_future_parser_payload_is_concrete_blocker_not_silent_drop(monkeypatch):
    raw = fixture_raw()
    value = normalize_rent_roll(io.BytesIO(raw), 'realpage')
    value['issues'][0]['observation'] = {'new_field': 'PRIVATE_CANARY'}
    # Fault injection simulates future reader drift; not claimed as real parsing.
    monkeypatch.setattr(api(), 'normalize_rent_roll', lambda *args: copy.deepcopy(value))
    refused(value, raw, adapter='onesite-detailed-realpage', code='UNREPRESENTABLE_V1')


def test_ledger_detects_omission_reordering_and_downgrade_even_valid_v2():
    raw = fixture_raw()
    old = normalize_rent_roll(io.BytesIO(raw), 'realpage')
    result = migrate(old, raw, 'onesite-detailed-realpage')
    changes = []
    missing_issue = copy.deepcopy(result)
    missing_issue['issues'].pop(0)
    changes.append(missing_issue)
    reorder = copy.deepcopy(result)
    reorder['unknown_use_units'].reverse()
    changes.append(reorder)
    dropped_group = copy.deepcopy(result)
    dropped_group['unknown_use_units'][0]['evidence'].pop()
    changes.append(dropped_group)
    matched = copy.deepcopy(result)
    matched['summaries'][2]['matched_fields'].reverse()
    changes.append(matched)
    null_issue = copy.deepcopy(result)
    null_issue['issues'][0]['citation'] = None
    changes.append(null_issue)
    for changed in changes:
        validate_observations(changed, subject_id=None, as_of=None)
        with pytest.raises(api().MigrationError) as caught:
            api().verify_preservation(old, changed, original_bytes=raw,
                                      adapter_id='onesite-detailed-realpage', **HOST)
        assert caught.value.code == 'PRESERVATION_MISMATCH'


def test_documented_synthetic_example_and_original_role_warning():
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'docs' / 'INGEST_MIGRATION.md'
    assert path.is_file(), 'Migration API and original-role limitation must be documented'
    text = path.read_text()
    assert 'cannot identify a derivative pretending to be an original' in text
    source = text.split('```python\n', 1)[1].split('```', 1)[0]
    context = {}
    exec(compile(source, '<synthetic-migration-example>', 'exec'), context)
    assert context['ledger']['retained_v1_sha256'] == context['ledger']['reconstructed_v1_sha256']
    assert context['v2']['completeness']['commercial']['coverage'] == 'unknown'
