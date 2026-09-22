"""Synthetic acceptance-runner tests; no original deal sources or live authority."""
import importlib.util
import importlib.metadata
import copy
import hashlib
import io
import json
import os
import platform
from pathlib import Path
import pytest
from plat_harness.ingest import acceptance as a


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


BASE_MODULES = ('plat_harness', 'plat_harness.errors', 'plat_harness.glossary',
    'plat_harness.models', 'plat_harness.ranks', 'plat_harness.ingest',
    'plat_harness.ingest.acceptance', 'plat_harness.ingest.contracts',
    'plat_harness.ingest.compat', 'plat_harness.ingest.pms_normalizer')
AUTH_MODULES = ('plat_harness.ingest.reconciliation', 'plat_harness.ingest.source_resolver',
    'plat_harness.contracts', 'plat_harness.adapters', 'plat_harness.adapters.review_bridge',
    'plat_harness.adapters.slice_b', 'plat_harness.adapters.paths', 'plat_harness.millage',
    'plat_harness.tools', 'plat_harness.tools.catalog', 'plat_harness.tools.certified_metric',
    'plat_harness.occupancy')
SID = 'src_' + '1' * 32
EID = 'entry_' + '1' * 32
RUN = 'acc_' + '1' * 32
HEADER = b'Unit,Status,Unit Type,Property,As Of,Coverage,Status Definition\n'
KNOWN = b'101,occupied,residential,synthetic_asset,2026-01-01,all_physical_units/1,occupied_vacant_down/1\n'
UNKNOWN = b'102,vacant,unknown,synthetic_asset,2026-01-01,all_physical_units/1,occupied_vacant_down/1\n'


def entry(raw=HEADER + KNOWN, **changes):
    result = dict(entry_id=EID, source_id=SID, source_sha256=sha(raw), source_size_bytes=len(raw),
        source_role='original', intake_id='intake_' + '2' * 32, subject_id='synthetic_asset',
        as_of='2026-01-01', entity_kind='property', snapshot_id='snap_' + '1' * 32,
        corpus_use='inventory', selection='run', exclusion_code=None, duplicate_of=None,
        adapter={'id': 'pms-flat-yardi', 'version': '1.0.0'}, expected_container='csv',
        requested_stage='observe', decisions_sha256=None)
    result.update(changes)
    return result


def setup(tmp_path, entries=(), raws=None, chains=None, limits=None):
    entries = copy.deepcopy(list(entries))
    modules = set(BASE_MODULES)
    if any(e['adapter']['id'] == 'onesite-detailed-realpage' for e in entries):
        modules.add('plat_harness.ingest.onesite')
    if any(e['requested_stage'] == 'reconcile' for e in entries):
        modules.update(AUTH_MODULES)
    paths = {name: importlib.import_module(name).__file__ for name in sorted(modules)}
    pins = {name: sha(Path(path).read_bytes()) for name, path in paths.items()}
    manifest = dict(contract_version='ingest-acceptance-manifest/1.0.0',
        corpus_id='corpus_' + '1' * 32, corpus_revision=1, corpus_basis_sha256='2' * 64,
        predecessor=None, change_kind='initial', code_manifest_sha256=sha(encode(pins)),
        runtime=dict(python_version=platform.python_version(),
                     openpyxl_version=importlib.metadata.version('openpyxl'),
                     xlrd_version=importlib.metadata.version('xlrd')), entries=entries)
    root = tmp_path / 'sources'
    root.mkdir(mode=0o700)
    out = tmp_path / 'output'
    out.mkdir(mode=0o700)
    sources = {}
    for e in entries:
        if e['selection'] != 'run':
            continue
        name = e['entry_id']
        sources[name] = a.SourcePath(str(root), (name,))
        raw = (raws or {}).get(name, HEADER + KNOWN)
        if raw is not None:
            fd = os.open(root / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, 'wb') as stream:
                stream.write(raw)
    kwargs = dict(manifest_bytes=encode(manifest), expected_manifest_sha256=sha(encode(manifest)),
        source_paths=sources, code_paths=paths, expected_code_sha256s=pins,
        decision_chains=chains or {}, output_root=str(out), limits=limits or a.AcceptanceLimits())
    return manifest, kwargs


def manifest_update(kwargs, manifest):
    kwargs.update(manifest_bytes=encode(manifest), expected_manifest_sha256=sha(encode(manifest)))


def attempt(fn):
    try:
        return fn()
    except Exception as exc:
        return exc


@pytest.mark.parametrize('attack', ['extra', 'float', 'bool', 'duplicate', 'hash', 'depth',
    'code', 'runtime', 'path_authority', 'chain', 'limits', 'bad_date', 'id', 'missing_key'])
def test_constructor_refuses_untrusted_schema_before_source_io(tmp_path, attack):
    m, kw = setup(tmp_path, [entry()])
    expected = 'INVALID_MANIFEST'
    if attack == 'extra': m['approved'] = True
    elif attack == 'float': m['corpus_revision'] = 1.0
    elif attack == 'bool': m['entries'][0]['source_size_bytes'] = True
    elif attack == 'depth': m['entries'][0]['subject_id'] = [[[[[[[[[[[[[[[[[0]]]]]]]]]]]]]]]]]
    elif attack == 'runtime':
        m['runtime']['python_version'] = '0.0'
        expected = 'RUNTIME_MISMATCH'
    elif attack == 'path_authority': m['entries'][0]['path'] = '/forbidden'
    elif attack == 'bad_date': m['entries'][0]['as_of'] = '2026-02-30'
    elif attack == 'id': m['entries'][0]['entry_id'] = 'entry_' + 'A' * 32
    elif attack == 'missing_key': del m['entries'][0]['intake_id']
    manifest_update(kw, m)
    if attack == 'hash':
        kw['expected_manifest_sha256'] = '0' * 64
        expected = 'MANIFEST_HASH_MISMATCH'
    elif attack == 'duplicate':
        kw['manifest_bytes'] = kw['manifest_bytes'].replace(b'"corpus_revision":1', b'"corpus_revision":1,"corpus_revision":1')
        kw['expected_manifest_sha256'] = sha(kw['manifest_bytes'])
    elif attack == 'code':
        kw['expected_code_sha256s']['plat_harness.ingest.compat'] = '0' * 64
        expected = 'CODE_PIN_MISMATCH'
    elif attack == 'chain':
        kw['decision_chains'] = {EID: []}
        expected = 'INVALID_HOST_INPUT'
    elif attack == 'limits':
        kw['limits'] = a.AcceptanceLimits(max_entries=129)
        expected = 'INVALID_HOST_INPUT'
    result = attempt(lambda: a.AcceptanceRunner(**kw))
    assert type(result) is a.AcceptanceError, type(result).__name__
    assert result.code == expected
    assert result.__context__ is None and result.__cause__ is None
    assert list(Path(kw['output_root']).iterdir()) == []


def test_valid_constructor_and_defensive_host_snapshots(tmp_path):
    m, kw = setup(tmp_path)
    result = attempt(lambda: a.AcceptanceRunner(**kw))
    assert type(result) is a.AcceptanceRunner, type(result).__name__


@pytest.mark.parametrize('excluded', [False, True])
def test_zero_ran_immutable_accounted_snapshot_and_exact_read(tmp_path, excluded):
    entries = [entry(selection='excluded', exclusion_code='OUTSIDE_FROZEN_SELECTION')] if excluded else []
    m, kw = setup(tmp_path, entries)
    runner = a.AcceptanceRunner(**kw)
    assert callable(getattr(runner, 'run', None)), 'run must publish a verified matrix'
    result = runner.run(RUN)
    assert set(result) == {'pin', 'matrix'}
    matrix = result['matrix']
    assert matrix['assessment'] == 'no_sources_ran'
    assert matrix['totals']['declared'] == len(entries)
    assert matrix['totals']['excluded'] == len(entries)
    assert matrix['totals']['parser_started'] == matrix['totals']['engine_eligible'] == 0
    assert matrix['engine_eligibility'] == matrix['item2_milestone'] == 'not_evaluated'
    assert matrix['artifacts'] == []
    assert runner.read(RUN, expected=result['pin']) == result
    folder = Path(kw['output_root']) / RUN
    assert folder.stat().st_mode & 0o777 == 0o700
    assert (folder / 'matrix.json').stat().st_mode & 0o777 == 0o600
    assert sha((folder / 'matrix.json').read_bytes()) == result['pin']['matrix_sha256']
    assert attempt(lambda: runner.run(RUN)).code == 'RUN_COLLISION'
    bad = dict(result['pin'], matrix_sha256='0' * 64)
    assert attempt(lambda: runner.read(RUN, expected=bad)).code == 'ARTIFACT_REFUSED'
    (folder / 'extra.json').write_bytes(b'{}')
    assert attempt(lambda: runner.read(RUN, expected=result['pin'])).code == 'ARTIFACT_REFUSED'


def artifact(kw, kind, eid=EID):
    return json.loads((Path(kw['output_root']) / RUN / (eid + '.' + kind + '.json')).read_bytes())


def xlsx_bytes():
    # Actual minimal OOXML, not a mocked worksheet or disguised CSV.
    import zipfile
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr('[Content_Types].xml', '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>')
        z.writestr('_rels/.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>')
        z.writestr('xl/workbook.xml', '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Synthetic" sheetId="1" r:id="rId1"/></sheets></workbook>')
        z.writestr('xl/_rels/workbook.xml.rels', '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>')
        rows = ''
        for rn, values in enumerate([['Unit','Status','Unit Type'], ['101','occupied','residential']], 1):
            rows += '<row r="%d">'%rn + ''.join('<c r="%s%d" t="inlineStr"><is><t>%s</t></is></c>'%(col,rn,value) for col,value in zip('ABC',values)) + '</row>'
        z.writestr('xl/worksheets/sheet1.xml', '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>' + rows + '</sheetData></worksheet>')
    return raw.getvalue()


@pytest.mark.parametrize('vendor', ['yardi', 'realpage', 'entrata'])
@pytest.mark.parametrize('container', ['csv', 'xlsx'])
def test_actual_flat_original_pipeline_locations_not_values(tmp_path, vendor, container):
    raw = HEADER + KNOWN if container == 'csv' else xlsx_bytes()
    e = entry(raw, adapter={'id':'pms-flat-' + vendor, 'version':'1.0.0'}, expected_container=container)
    m, kw = setup(tmp_path, [e], {EID:raw})
    source = Path(kw['source_paths'][EID].root) / EID
    before = source.stat()
    result = a.AcceptanceRunner(**kw).run(RUN)
    row = result['matrix']['entries'][0]
    assert row['state'] == 'observed_unvalidated', row['blockers']
    assert row['parser_started'] is row['v2_valid'] is True
    assert row['observed_source_sha256'] == sha(raw)
    assert row['counts'] == {s: dict.fromkeys(('occupied','vacant','down','total')) for s in ('residential','commercial')}
    env, ledger = artifact(kw, 'observations'), artifact(kw, 'citations')
    preservation = artifact(kw, 'preservation')
    assert preservation['retained_v1_sha256'] == preservation['reconstructed_v1_sha256']
    assert ledger['totals']['occurrences'] == preservation['citation_occurrences']
    assert len(env['units']) == 1
    for occurrence in ledger['occurrences']:
        target = env
        for key in occurrence['pointer'].split('/')[1:]:
            target = target[int(key)] if type(target) is list else target[key]
        assert target == occurrence['citation']
        assert occurrence['location'] == 'located'
        assert occurrence['value_check'] == 'not_assessed'
    after = source.stat()
    assert (before.st_mode, before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_atime_ns) == (after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_atime_ns)


@pytest.mark.parametrize('problem,code', [('missing','SOURCE_MISSING'), ('link','SOURCE_UNSAFE'),
    ('hardlink','SOURCE_UNSAFE'), ('fifo','SOURCE_UNSAFE'), ('hash','SOURCE_HASH_MISMATCH'),
    ('size','SOURCE_SIZE_MISMATCH'), ('derivative','ORIGINAL_REQUIRED'), ('unknown','ORIGINAL_REQUIRED'),
    ('intake','ORIGINAL_REQUIRED'), ('adapter','ADAPTER_UNSUPPORTED'), ('container','CONTAINER_MISMATCH'),
    ('limit','SOURCE_LIMIT')])
def test_refused_sources_are_accounted_without_success(tmp_path, problem, code):
    e = entry()
    raw = HEADER + KNOWN
    if problem in ('derivative','unknown'): e['source_role'] = problem
    if problem == 'intake': e['intake_id'] = None
    if problem == 'hash': e['source_sha256'] = '0' * 64
    if problem == 'size': e['source_size_bytes'] += 1
    if problem == 'adapter': e['adapter']['id'] = 'unregistered-native-v2'
    if problem == 'container': e['expected_container'] = 'xlsx'
    m, kw = setup(tmp_path, [e], {EID:None if problem in ('missing','fifo','link') else raw},
                  limits=a.AcceptanceLimits(max_source_bytes=1) if problem == 'limit' else None)
    source = Path(kw['source_paths'][EID].root) / EID
    if problem == 'fifo': os.mkfifo(source, 0o600)
    if problem == 'link': source.symlink_to(tmp_path / 'never-open')
    if problem == 'hardlink': os.link(source, source.parent / 'alias')
    result = a.AcceptanceRunner(**kw).run(RUN)
    row = result['matrix']['entries'][0]
    assert row['state'] == 'refused'
    assert code in {b['code'] for b in row['blockers']}
    assert not row['parser_started']
    assert result['matrix']['assessment'] == 'no_sources_ran'
    assert result['matrix']['totals']['refused'] == 1


def test_detailed_biff_unknown_observations_preserved(tmp_path):
    from test_pms_onesite import fixture_raw
    raw = fixture_raw()
    m, kw = setup(tmp_path, [entry(raw, adapter={'id':'onesite-detailed-realpage','version':'1.0.0'}, expected_container='xls_biff')], {EID:raw})
    result = a.AcceptanceRunner(**kw).run(RUN)
    row = result['matrix']['entries'][0]
    assert row['state'] == 'parsed_with_blockers', row['blockers']
    env = artifact(kw, 'observations')
    assert env['unknown_use_units'] and not env['units']
    assert env['status'] == 'blocked'
    assert all(n is None for counts in row['counts'].values() for n in counts.values())
    ledger = artifact(kw, 'citations')
    assert len(ledger['occurrences']) >= artifact(kw, 'preservation')['citation_occurrences']


def test_citation_multiplicity_survives_canonical_ipc_key_order():
    raw, e = HEADER + KNOWN, entry()
    packet = a._pipeline(raw, e, '0' * 64)
    decoded = json.loads(encode(packet))
    expected = list(a._occurrences(decoded['observations']))
    assert [(o['pointer'],o['citation']) for o in decoded['citations']['occurrences']] == expected
    assert a._validate_ledgers(decoded, e, '0' * 64) is None


def registry_file(tmp_path, monkeypatch, registry, name='registry'):
    target = tmp_path / name
    data = encode(registry)
    fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd,'wb') as stream: stream.write(data)
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH', str(target))
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256', sha(data))
    return target


def confirmation(raw=HEADER+KNOWN, chain_length=1):
    from test_ingest_reconciliation import approve, decision
    env = a._pipeline(raw, entry(raw), '0'*64)['observations']
    registry, chain = {}, []
    for n in range(chain_length):
        payload = decision(env, decision_id='dec_' + format(n+1,'032x'),
            predecessor_sha256=sha(encode(chain[-1])) if chain else None,
            supersedes=[sha(encode(chain[-1]))] if chain else [])
        payload['changes'][0]['citation'] = env['units'][0]['evidence'][0]['unit_type']
        chain.append(approve(payload, registry))
    return registry, chain


@pytest.mark.parametrize('length', [0,1,2])
def test_known_use_production_registry_reconciliation_and_confirmation(tmp_path, monkeypatch, length):
    registry, chain = confirmation(chain_length=length)
    registry_file(tmp_path,monkeypatch,registry)
    e = entry(requested_stage='reconcile', decisions_sha256=sha(encode(chain)))
    m, kw = setup(tmp_path,[e],chains={EID:chain})
    runner = a.AcceptanceRunner(**kw)
    result = runner.run(RUN)
    row = result['matrix']['entries'][0]
    assert row['state'] == row['reconciliation_state'] == 'reconciled', row['blockers']
    assert result['matrix']['assessment'] == 'reconciled_selected'
    assert row['counts_basis'] == 'reconciled_overlay'
    assert row['counts']['residential'] == dict(occupied=1,vacant=0,down=0,total=1)
    overlay = artifact(kw,'resolution')
    assert overlay['observations'] == artifact(kw,'observations')
    assert not overlay['observations']['unknown_use_units']  # confirmation, NOT unknown-use resolution
    assert len(overlay['history']) == length
    if length == 2: assert [h['state'] for h in overlay['history']] == ['superseded','active']
    registry_file(tmp_path,monkeypatch,{},'revoked')
    # Exact historical read cannot consult current registry or original files.
    (Path(kw['source_paths'][EID].root)/EID).unlink()
    assert runner.read(RUN,expected=result['pin']) == result


@pytest.mark.parametrize('adapter', ['pms-flat-yardi','pms-flat-realpage','pms-flat-entrata','onesite-detailed-realpage'])
def test_amendment_unknown_use_stays_blocked_null_counts(tmp_path,monkeypatch,adapter):
    from test_pms_onesite import fixture_raw
    raw = fixture_raw() if adapter.startswith('onesite') else HEADER+KNOWN+UNKNOWN
    registry_file(tmp_path,monkeypatch,{})
    e = entry(raw, adapter={'id':adapter,'version':'1.0.0'},
        expected_container='xls_biff' if adapter.startswith('onesite') else 'csv',
        requested_stage='reconcile', decisions_sha256=sha(encode([])))
    m,kw = setup(tmp_path,[e],{EID:raw},{EID:[]})
    result=a.AcceptanceRunner(**kw).run(RUN)
    row=result['matrix']['entries'][0]
    assert row['state'] in ('parsed_with_blockers','refused')
    assert row['reconciliation_state'] in ('unavailable','review_required','refused')
    assert all(v is None for counts in row['counts'].values() for v in counts.values())
    env=artifact(kw,'observations')
    if adapter.startswith('onesite'): assert env['unknown_use_units'] and not env['units']
    else:
        assert not env['unknown_use_units'] and len(env['units']) == 1
        assert any(i['code']=='UNSUPPORTED_UNIT_TYPE' for i in env['issues'])
        assert artifact(kw,'resolution')['state'] == 'review_required'


@pytest.mark.parametrize('attack', ['revoked_prefix','edited_approval','edited_change','broken_chain','missing_registry'])
def test_bad_authority_never_observe_fallback(tmp_path,monkeypatch,attack):
    registry,chain=confirmation(chain_length=2)
    if attack=='revoked_prefix': del registry[chain[0]['approval']['approval_id']]
    elif attack=='edited_approval': chain[-1]['approval']['reason']='edited'
    elif attack=='edited_change': chain[-1]['changes'][0]['after']='commercial'
    elif attack=='broken_chain': chain=chain[1:]
    if attack!='missing_registry': registry_file(tmp_path,monkeypatch,registry)
    e=entry(requested_stage='reconcile',decisions_sha256=sha(encode(chain)))
    m,kw=setup(tmp_path,[e],chains={EID:chain})
    row=a.AcceptanceRunner(**kw).run(RUN)['matrix']['entries'][0]
    assert row['state']=='refused'
    assert row['reconciliation_state']=='refused'
    assert 'RECONCILIATION_REFUSED' in {d['code'] for d in row['blockers']}
    assert row['v2_valid'] and all(v is None for counts in row['counts'].values() for v in counts.values())


@pytest.mark.parametrize('late', [False,True])
def test_full_chain_revocation_after_staging_or_end_barrier(tmp_path,monkeypatch,late):
    registry,chain=confirmation(chain_length=2)
    registry_file(tmp_path,monkeypatch,registry)
    e=entry(requested_stage='reconcile',decisions_sha256=sha(encode(chain)))
    m,kw=setup(tmp_path,[e],chains={EID:chain})
    runner=a.AcceptanceRunner(**kw)
    real=runner._current
    calls=[]
    def revoke(rows,payloads,deadline):
        calls.append(1)
        if len(calls)==(2 if late else 1):
            changed=copy.deepcopy(registry)
            del changed[chain[0]['approval']['approval_id']]
            registry_file(tmp_path,monkeypatch,changed,'revoked')
        return real(rows,payloads,deadline)
    monkeypatch.setattr(runner,'_current',revoke)  # Timing injection; actual production authority remains.
    result=attempt(lambda:runner.run(RUN))
    assert type(result) is a.AcceptanceError, 'revocation must not return cached matrix'
    assert result.code == ('WRITE_UNCERTAIN' if late else 'ARTIFACT_REFUSED')
    folder=Path(kw['output_root'])/RUN
    assert folder.exists() is late
    if late: assert (folder/'matrix.json').is_file()


@pytest.mark.parametrize('kind', ['hash','path','declared','snapshot','different_dates'])
@pytest.mark.parametrize('reverse',[False,True])
def test_duplicates_snapshots_account_all_members(tmp_path,kind,reverse):
    raw2=HEADER+KNOWN.replace(b'101,',b'102,')
    first=entry()
    second=entry(raw2,entry_id='entry_'+'2'*32,source_id='src_'+'2'*32,snapshot_id='snap_'+'2'*32)
    if kind in ('hash','declared'):
        second.update(source_sha256=first['source_sha256'],source_size_bytes=first['source_size_bytes'],snapshot_id=first['snapshot_id'])
        raw2=HEADER+KNOWN
    if kind=='declared': second.update(selection='declared_duplicate',exclusion_code='DECLARED_DUPLICATE',duplicate_of=EID)
    if kind=='different_dates': second['as_of']='2026-02-01'
    entries=[first,second]
    if reverse: entries.reverse()
    m,kw=setup(tmp_path,entries,{EID:HEADER+KNOWN,second['entry_id']:raw2})
    if kind=='path': kw['source_paths'][second['entry_id']]=kw['source_paths'][EID]
    result=a.AcceptanceRunner(**kw).run(RUN)['matrix']
    assert [r['entry_id'] for r in result['entries']]==[e['entry_id'] for e in entries]
    if kind in ('hash','path'):
        assert result['totals']['refused']==2 and result['totals']['parser_started']==0
        assert all('DUPLICATE_SOURCE' in {d['code'] for d in r['blockers']} for r in result['entries'])
        assert result['totals']['distinct_selected_properties']==0
    elif kind=='declared':
        assert result['totals']['declared_duplicate']==1 and result['totals']['parser_started']==1
    elif kind=='snapshot':
        assert result['totals']['parsed_with_blockers']==2
        assert all('AMBIGUOUS_SNAPSHOT' in {d['code'] for d in r['blockers']} for r in result['entries'])
    else:
        assert result['totals']['observed_unvalidated']==2
        assert result['totals']['distinct_selected_properties']==1
        assert result['totals']['selected_snapshots']==2


@pytest.mark.parametrize('change', ['repeat_assessment','source_correction','selection_change'])
def test_successor_exact_prior_and_old_evidence_immutable(tmp_path,change):
    m,kw=setup(tmp_path,[entry()])
    initial=a.AcceptanceRunner(**kw).run(RUN)
    old=(Path(kw['output_root'])/RUN/'matrix.json').read_bytes()
    m['predecessor']={k:initial['pin'][k] for k in ('run_id','matrix_sha256','manifest_sha256')}
    m['change_kind']=change
    if change!='repeat_assessment': m['corpus_revision']=2
    if change=='source_correction':
        raw=HEADER+KNOWN.replace(b'101,',b'102,')
        source=Path(kw['source_paths'][EID].root)/'corrected'
        source.write_bytes(raw)
        kw['source_paths'][EID]=a.SourcePath(str(source.parent),(source.name,))
        m['entries'][0].update(source_sha256=sha(raw),source_size_bytes=len(raw),snapshot_id='snap_'+'2'*32)
    if change=='selection_change':
        m['entries'][0].update(selection='excluded',exclusion_code='OUTSIDE_FROZEN_SELECTION')
        kw['source_paths']={}
    manifest_update(kw,m)
    out=a.AcceptanceRunner(**kw).run('acc_'+'2'*32)
    assert out['matrix']['predecessor']==m['predecessor']
    assert (Path(kw['output_root'])/RUN/'matrix.json').read_bytes()==old
    # Reusing the historical ID is never retry authority.
    assert attempt(lambda:a.AcceptanceRunner(**kw).run(RUN)).code=='RUN_COLLISION'


@pytest.mark.parametrize('attack',['missing','wrong_pin','same_revision','unchanged_snapshot','false_reason','false_repeat'])
def test_successor_cannot_invent_lineage_or_rebrand_changed_source(tmp_path,attack):
    m,kw=setup(tmp_path,[entry()])
    initial=a.AcceptanceRunner(**kw).run(RUN)
    m['predecessor']={k:initial['pin'][k] for k in ('run_id','matrix_sha256','manifest_sha256')}
    m.update(change_kind='source_correction',corpus_revision=2)
    raw=HEADER+KNOWN.replace(b'101,',b'102,')
    # Synthetic correction gets a new source file; old original stays unchanged.
    source=Path(kw['source_paths'][EID].root)/'corrected'
    source.write_bytes(raw)
    kw['source_paths'][EID]=a.SourcePath(str(source.parent),(source.name,))
    m['entries'][0].update(source_sha256=sha(raw),source_size_bytes=len(raw),snapshot_id='snap_'+'2'*32)
    if attack=='missing': m['predecessor']['run_id']='acc_'+'3'*32
    elif attack=='wrong_pin': m['predecessor']['matrix_sha256']='0'*64
    elif attack=='same_revision': m['corpus_revision']=1
    elif attack=='unchanged_snapshot': m['entries'][0]['snapshot_id']='snap_'+'1'*32
    elif attack=='false_reason': m['change_kind']='review_change'
    elif attack=='false_repeat': m.update(change_kind='repeat_assessment',corpus_revision=1)
    manifest_update(kw,m)
    result=attempt(lambda:a.AcceptanceRunner(**kw).run('acc_'+'2'*32))
    assert type(result) is a.AcceptanceError, attack
    assert not (Path(kw['output_root'])/('acc_'+'2'*32)).exists()


@pytest.mark.parametrize('attack',['extra','bool_total','orphan','nonnull_counts','state','citation','ledger_count'])
def test_rehashed_malformed_historical_artifacts_are_not_a_schema_bypass(tmp_path,attack):
    m,kw=setup(tmp_path,[entry()])
    runner=a.AcceptanceRunner(**kw)
    result=runner.run(RUN)
    folder=Path(kw['output_root'])/RUN
    matrix=copy.deepcopy(result['matrix'])
    if attack=='extra': matrix['approved']=True
    elif attack=='bool_total': matrix['totals']['selected']=True
    elif attack=='orphan': matrix['entries'][0]['observation_sha256']=None
    elif attack=='nonnull_counts': matrix['entries'][0]['counts']['residential']['occupied']=1
    elif attack=='state': matrix['entries'][0]['reconciliation_state']='reconciled'
    else:
        ledger=artifact(kw,'citations')
        if attack=='citation': ledger['occurrences'][0]['value_check']='verified'
        else: ledger['totals']['located']=999
        data=encode(ledger)
        (folder/(EID+'.citations.json')).write_bytes(data)
        matrix['entries'][0]['citation_ledger_sha256']=sha(data)
        for slot in matrix['artifacts']:
            if slot['kind']=='citations': slot.update(sha256=sha(data),size_bytes=len(data))
    raw=encode(matrix)
    (folder/'matrix.json').write_bytes(raw)
    forged=dict(result['pin'],matrix_sha256=sha(raw))
    refused=attempt(lambda:runner.read(RUN,expected=forged))
    assert type(refused) is a.AcceptanceError, attack


def test_opened_inode_aliases_all_refused_before_parser(tmp_path,monkeypatch):
    raw2=HEADER+KNOWN.replace(b'101,',b'102,')
    second=entry(raw2,entry_id='entry_'+'2'*32,source_id='src_'+'2'*32,as_of='2026-02-01',snapshot_id='snap_'+'2'*32)
    m,kw=setup(tmp_path,[entry(),second],{EID:HEADER+KNOWN,second['entry_id']:raw2})
    real=a._source
    def aliased(*args):
        code,data=real(*args)
        return (code,data) if code else (None,(data[0],(1,2,*data[1][2:])))
    monkeypatch.setattr(a,'_source',aliased)  # Failure injection for mount alias, not parser success.
    result=a.AcceptanceRunner(**kw).run(RUN)['matrix']
    assert result['totals']['refused']==2 and result['totals']['parser_started']==0
    assert all('DUPLICATE_SOURCE' in {d['code'] for d in r['blockers']} for r in result['entries'])


def test_visible_publish_failure_is_uncertain_permanent_retained(tmp_path,monkeypatch):
    m,kw=setup(tmp_path)
    real=a._publish
    def publish_then_fail(*args):
        real(*args)
        raise OSError('synthetic-privacy-canary')
    monkeypatch.setattr(a,'_publish',publish_then_fail)
    out=attempt(lambda:a.AcceptanceRunner(**kw).run(RUN))
    assert type(out) is a.AcceptanceError and out.code=='WRITE_UNCERTAIN'
    assert out.__cause__ is out.__context__ is None
    assert (Path(kw['output_root'])/RUN/'matrix.json').exists()


def test_final_code_guard_cannot_leave_stale_chain_authority(tmp_path,monkeypatch):
    registry,chain=confirmation(chain_length=2)
    registry_file(tmp_path,monkeypatch,registry)
    m,kw=setup(tmp_path,[entry(requested_stage='reconcile',decisions_sha256=sha(encode(chain)))],chains={EID:chain})
    runner=a.AcceptanceRunner(**kw)
    real=runner._check_code
    calls=[]
    def revoke_at_final_guard():
        real()
        calls.append(1)
        if len(calls)==5:
            changed=copy.deepcopy(registry)
            del changed[chain[0]['approval']['approval_id']]
            registry_file(tmp_path,monkeypatch,changed,'final-revocation')
    monkeypatch.setattr(runner,'_check_code',revoke_at_final_guard)
    out=attempt(lambda:runner.run(RUN))
    assert type(out) is a.AcceptanceError and out.code=='WRITE_UNCERTAIN'


def test_review_required_preserves_exact_resolution_diagnostics(tmp_path,monkeypatch):
    raw=HEADER+KNOWN+UNKNOWN
    registry_file(tmp_path,monkeypatch,{})
    m,kw=setup(tmp_path,[entry(raw,requested_stage='reconcile',decisions_sha256=sha(encode([])))],{EID:raw},{EID:[]})
    row=a.AcceptanceRunner(**kw).run(RUN)['matrix']['entries'][0]
    assert {'stage':'reconciliation','namespace':'resolution','code':'INVENTORY_MEMBERSHIP_MISMATCH'} in row['blockers']


@pytest.mark.parametrize('attack',['count','bool'])
def test_preservation_and_citation_ledgers_rebuild_counts(tmp_path,attack):
    m,kw=setup(tmp_path,[entry()])
    runner=a.AcceptanceRunner(**kw)
    result=runner.run(RUN)
    matrix=result['matrix']
    kind='preservation' if attack=='count' else 'citations'
    value=artifact(kw,kind)
    if attack=='count': value['scoped_observations']=99
    else: value['totals']['null']=False
    folder=Path(kw['output_root'])/RUN
    raw=encode(value)
    (folder/(EID+'.'+kind+'.json')).write_bytes(raw)
    matrix['entries'][0]['preservation_sha256' if kind=='preservation' else 'citation_ledger_sha256']=sha(raw)
    for slot in matrix['artifacts']:
        if slot['kind']==kind: slot.update(sha256=sha(raw),size_bytes=len(raw))
    raw=encode(matrix)
    (folder/'matrix.json').write_bytes(raw)
    pin=dict(result['pin'],matrix_sha256=sha(raw))
    out=attempt(lambda:runner.read(RUN,expected=pin))
    assert type(out) is a.AcceptanceError


@pytest.mark.parametrize('failure',['setup','timeout','overflow','protocol','canary','inherited'])
def test_worker_supervision_and_private_failure_sinks(tmp_path,monkeypatch,capfd,caplog,failure):
    import time
    import warnings
    import logging
    canary='synthetic-contact-canary@example.invalid'
    m,kw=setup(tmp_path,[entry()],limits=a.AcceptanceLimits(worker_wall_seconds=1))
    fd=os.open(tmp_path/'unrelated',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    monkeypatch.setenv('PLAT_HARNESS_SYNTHETIC_SECRET',canary)
    expected='WORKER_EXIT'
    if failure=='setup':
        def fail_setup(*args): raise OSError(canary)
        monkeypatch.setattr(a,'_worker_setup',fail_setup)
        expected='WORKER_SETUP_REFUSED'
    else:
        def fail_pipeline(*args):
            if failure=='timeout':
                while True: time.sleep(0.1)
            if failure=='overflow': return {'bad':'x'*(25*1024*1024)}
            if failure=='protocol': return {'certified':True,'count':1}
            if failure=='inherited':
                assert 'PLAT_HARNESS_SYNTHETIC_SECRET' not in os.environ
                assert os.environ['HF_HUB_OFFLINE']=='1' and os.environ['CUDA_VISIBLE_DEVICES']==''
                try: os.fstat(fd)
                except OSError: pass
                else: raise AssertionError('inherited descriptor')
                raise a.compat.MigrationError('PARSER_REFUSED')
            os.write(1,canary.encode()); os.write(2,canary.encode())
            logging.error(canary)
            warnings.warn(canary)
            raise RuntimeError(canary)
        monkeypatch.setattr(a,'_pipeline',fail_pipeline)  # Failure injection only.
        if failure=='timeout': expected='WORKER_TIMEOUT'
        elif failure=='protocol': expected='WORKER_PROTOCOL'
        elif failure=='inherited': expected='PARSER_REFUSED'
    before=set(Path('/proc/self/task').iterdir())
    try: result=a.AcceptanceRunner(**kw).run(RUN)
    finally: os.close(fd)
    row=result['matrix']['entries'][0]
    assert row['state']=='refused' and expected in {d['code'] for d in row['blockers']}
    assert set(Path('/proc/self/task').iterdir())==before
    assert Path('/proc/self/task/'+str(os.getpid())+'/children').read_text().strip()==''
    captured=capfd.readouterr()
    assert canary not in encode(result).decode()+captured.out+captured.err+caplog.text
    assert not result['matrix']['artifacts']


@pytest.mark.parametrize('mode',[0o400,0o600,0o444,0o644])
def test_noatime_source_policy_accepts_exact_modes_without_repairs(tmp_path,mode):
    m,kw=setup(tmp_path,[entry()],{EID:None})
    source=Path(kw['source_paths'][EID].root)/EID
    mask=os.umask(0)
    try: fd=os.open(source,os.O_WRONLY|os.O_CREAT|os.O_EXCL,mode)
    finally: os.umask(mask)
    with os.fdopen(fd,'wb') as f: f.write(HEADER+KNOWN)
    before=source.stat()
    result=a.AcceptanceRunner(**kw).run(RUN)
    assert result['matrix']['entries'][0]['state']=='observed_unvalidated'
    after=source.stat()
    assert (before.st_mode,before.st_mtime_ns,before.st_ctime_ns,before.st_atime_ns)==(after.st_mode,after.st_mtime_ns,after.st_ctime_ns,after.st_atime_ns)


def test_multithreaded_embedding_refuses_before_source_io(tmp_path):
    import threading
    m,kw=setup(tmp_path,[entry()])
    event=threading.Event()
    thread=threading.Thread(target=event.wait)
    thread.start()
    try: result=attempt(lambda:a.AcceptanceRunner(**kw).run(RUN))
    finally: event.set(); thread.join()
    assert type(result) is a.AcceptanceError and result.code=='INVALID_HOST_INPUT'
    assert list(Path(kw['output_root']).iterdir())==[]


@pytest.mark.parametrize('kind',['dense','exact_cap','cap_plus_one','resolver_cap'])
def test_actual_csv_capacity_and_controlled_refusals(tmp_path,monkeypatch,kind):
    if kind=='dense':
        raw=HEADER+b''.join(KNOWN.replace(b'101,',str(n).encode()+b',',1) for n in range(1,257))
    else:
        target=8*1024*1024+(kind=='cap_plus_one') if kind!='resolver_cap' else 1024*1024+1
        remaining=target-len(HEADER+KNOWN)
        band=b'x'*59999+b'\n'
        raw=band*(remaining//len(band))
        rest=remaining%len(band)
        raw+=(b'x'*(rest-1)+b'\n') if rest else b''
        raw+=HEADER+KNOWN
        assert len(raw)==target
    e=entry(raw)
    chains={}
    if kind=='resolver_cap':
        e.update(requested_stage='reconcile',decisions_sha256=sha(encode([])))
        chains={EID:[]}
        registry_file(tmp_path,monkeypatch,{})
    m,kw=setup(tmp_path,[e],{EID:raw},chains)
    row=a.AcceptanceRunner(**kw).run(RUN)['matrix']['entries'][0]
    if kind=='cap_plus_one': assert row['state']=='refused' and not row['parser_started']
    elif kind=='resolver_cap':
        assert row['state']=='parsed_with_blockers' and row['v2_valid']
        assert 'RECONCILIATION_SOURCE_LIMIT' in {d['code'] for d in row['blockers']}
    else:
        assert row['state']=='observed_unvalidated', row['blockers']
        assert len(artifact(kw,'observations')['units'])==(256 if kind=='dense' else 1)


@pytest.mark.parametrize('attack',['extra_code','copied_code','pin_drift','chain_pin','source_binding','dot','slash','overlap'])
def test_host_authority_is_not_well_formed_document_text(tmp_path,attack):
    m,kw=setup(tmp_path,[entry()])
    if attack=='extra_code':
        kw['code_paths']['plugin']='not-an-import'
        kw['expected_code_sha256s']['plugin']='0'*64
    elif attack=='copied_code':
        name='plat_harness.ingest.compat'
        copied=tmp_path/'copied.py'
        copied.write_bytes(Path(kw['code_paths'][name]).read_bytes())
        kw['code_paths'][name]=str(copied)
    elif attack=='pin_drift':
        runner=a.AcceptanceRunner(**kw)
        runner._pins['plat_harness.ingest.compat']='0'*64
        out=attempt(lambda:runner.run(RUN))
        assert type(out) is a.AcceptanceError and out.code=='CODE_PIN_MISMATCH'
        return
    elif attack=='chain_pin':
        m['entries'][0].update(requested_stage='reconcile',decisions_sha256='0'*64)
        kw['decision_chains']={EID:[]}
        manifest_update(kw,m)
    elif attack=='source_binding': kw['source_paths']={}
    elif attack=='dot': kw['source_paths'][EID]=a.SourcePath(str(tmp_path/'sources'),('..',EID))
    elif attack=='slash': kw['source_paths'][EID]=a.SourcePath(str(tmp_path/'sources')+'/',(EID,))
    elif attack=='overlap': kw['output_root']=str(tmp_path/'sources')
    out=attempt(lambda:a.AcceptanceRunner(**kw))
    assert type(out) is a.AcceptanceError
    assert not (tmp_path/'output'/RUN).exists()


@pytest.mark.parametrize('attack',['root_symlink','ancestor_symlink','writable_file','directory','noatime','replace','grow'])
def test_source_descriptor_races_and_unsafe_objects(tmp_path,monkeypatch,attack):
    m,kw=setup(tmp_path,[entry()])
    source=Path(kw['source_paths'][EID].root)/EID
    if attack=='root_symlink':
        link=tmp_path/'linked-root'; link.symlink_to(source.parent,target_is_directory=True)
        kw['source_paths'][EID]=a.SourcePath(str(link),(EID,))
    elif attack=='ancestor_symlink':
        actual=source.parent/'actual'; actual.mkdir(mode=0o700)
        source.rename(actual/EID)
        link=source.parent/'link'; link.symlink_to(actual,target_is_directory=True)
        kw['source_paths'][EID]=a.SourcePath(str(source.parent),('link',EID))
    elif attack=='writable_file':
        source.unlink()
        mask=os.umask(0)
        try: fd=os.open(source,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o660)
        finally: os.umask(mask)
        with os.fdopen(fd,'wb') as f: f.write(HEADER+KNOWN)
    elif attack=='directory': source.unlink(); source.mkdir(mode=0o700)
    elif attack=='noatime':
        real=os.open
        def no_atime(path,flags,*args,**kwargs):
            if flags & os.O_NOATIME: raise PermissionError('synthetic-private-canary')
            return real(path,flags,*args,**kwargs)
        monkeypatch.setattr(a.os,'open',no_atime)
    else:
        real=os.read
        inode=source.stat().st_ino
        fired=[]
        def mutate(fd,size):
            chunk=real(fd,size)
            if not fired and os.fstat(fd).st_ino==inode:
                fired.append(True)
                if attack=='replace':
                    source.rename(source.with_name('old-original'))
                    source.write_bytes(HEADER+KNOWN)
                else:
                    with source.open('ab') as f: f.write(b'\n')
            return chunk
        monkeypatch.setattr(a.os,'read',mutate)
    row=a.AcceptanceRunner(**kw).run(RUN)['matrix']['entries'][0]
    assert row['state']=='refused' and not row['parser_started']
    assert {d['code'] for d in row['blockers']} <= {'SOURCE_UNSAFE','SOURCE_CHANGED','SOURCE_SIZE_MISMATCH'}


def test_unsafe_output_root_has_specific_safe_error(tmp_path):
    m,kw=setup(tmp_path)
    link=tmp_path/'output-link'; link.symlink_to(kw['output_root'],target_is_directory=True)
    kw['output_root']=str(link)
    out=attempt(lambda:a.AcceptanceRunner(**kw).run(RUN))
    assert type(out) is a.AcceptanceError and out.code=='UNSAFE_OUTPUT_ROOT'


@pytest.mark.parametrize('stage',['write','publish','readback','endguard'])
def test_durability_faults_do_not_report_success_or_remove_permanent_files(tmp_path,monkeypatch,stage):
    m,kw=setup(tmp_path,[entry()])
    runner=a.AcceptanceRunner(**kw)
    if stage=='write':
        def fail(*args): raise OSError('synthetic-private-canary')
        monkeypatch.setattr(a,'_write_file',fail)
    elif stage=='publish':
        def fail(*args): raise OSError('synthetic-private-canary')
        monkeypatch.setattr(a,'_publish',fail)
    else:
        name='_read' if stage=='readback' else '_current'
        original=getattr(runner,name)
        calls=[]
        def fail(*args):
            calls.append(1)
            if len(calls)==2: raise OSError('synthetic-private-canary')
            return original(*args)
        monkeypatch.setattr(runner,name,fail)
    out=attempt(lambda:runner.run(RUN))
    assert type(out) is a.AcceptanceError
    assert out.code==('ARTIFACT_REFUSED' if stage=='write' else 'WRITE_UNCERTAIN')
    assert out.__context__ is out.__cause__ is None
    assert (Path(kw['output_root'])/RUN).exists()==(stage in ('readback','endguard'))
    assert not any(p.name.startswith('.stage_') for p in Path(kw['output_root']).iterdir())


def test_csv_multiline_continuation_summary_every_occurrence_and_privacy(tmp_path,capfd):
    canary='synthetic-resident-canary@example.invalid'
    raw=('Unit,Status,Unit Type,Resident\n101,occupied,residential,"'+canary+'\nprivate"\n101,occupied,residential,Other\nScope,Occupied,Vacant,Down,Total\nresidential,1,0,0,1\n').replace('\\n','\n').encode()
    m,kw=setup(tmp_path,[entry(raw)],{EID:raw})
    row=a.AcceptanceRunner(**kw).run(RUN)['matrix']['entries'][0]
    assert row['v2_valid'] and row['state']=='observed_unvalidated',row['blockers']
    ledger=artifact(kw,'citations')
    env=artifact(kw,'observations')
    assert len(env['units'][0]['evidence'])==2
    assert any(o['citation'] and o['citation']['row']==2 and o['citation']['row_end']==3 for o in ledger['occurrences'])
    assert any(o['pointer'].startswith('/summaries/') for o in ledger['occurrences'])
    assert any(o['pointer'].startswith('/issues/') for o in ledger['occurrences'])
    assert ledger['totals']['occurrences']==artifact(kw,'preservation')['citation_occurrences']
    all_bytes=b''.join(p.read_bytes() for p in (Path(kw['output_root'])/RUN).iterdir())
    assert canary.encode() not in all_bytes
    captured=capfd.readouterr()
    assert canary not in captured.out+captured.err


def test_location_does_not_prove_values_and_blank_never_promotes():
    raw=b'Unit,Status,Unit Type,Resident\n101,occupied,residential,\n'.replace(b'\\n',b'\n')
    e=entry(raw)
    env=a._pipeline(raw,e,'0'*64)['observations']
    # Isolated negative checker challenge, NEVER a runner acceptance fixture.
    env['units'][0]['status']='vacant'
    cites=a._citations(env,raw,e,'0'*64)
    assert all(o['value_check']=='not_assessed' for o in cites['occurrences'])
    assert cites['totals']['located']==3  # The wrong semantic value is not checked here.
    anchor=env['units'][0]['evidence'][0]['status']
    anchor['column']=4
    assert a._citations(env,raw,e,'0'*64)['totals']['empty_or_implicit']==1
    anchor['column']=5
    assert a._citations(env,raw,e,'0'*64)['totals']['missing']==1


def test_no_model_engine_network_or_subprocess_calls(tmp_path,monkeypatch):
    import socket
    import subprocess
    import builtins
    m,kw=setup(tmp_path,[entry()])
    runner=a.AcceptanceRunner(**kw)
    original=builtins.__import__
    def guarded(name,*args,**kwargs):
        assert name.split('.')[0] not in ('torch','transformers','vllm','engine')
        return original(name,*args,**kwargs)
    def denied(*args,**kwargs): raise AssertionError('prohibited execution surface')
    monkeypatch.setattr(builtins,'__import__',guarded)
    monkeypatch.setattr(socket,'socket',denied)
    monkeypatch.setattr(subprocess,'Popen',denied)
    assert runner.run(RUN)['matrix']['entries'][0]['state']=='observed_unvalidated'


@pytest.mark.parametrize('variant',['hidden','understated','sparse','formula','chart'])
def test_actual_xlsx_coordinates_formula_and_chart_boundaries(tmp_path,variant):
    import zipfile
    from xml.etree import ElementTree as ET
    raw=xlsx_bytes()
    if variant=='chart':
        # Keep optional numerical-library imports out of the single-threaded
        # embedding host; construct the actual OOXML parts directly.
        stream=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as out:
            for info in src.infolist():
                data=src.read(info.filename)
                if info.filename=='xl/workbook.xml':
                    data=data.replace(b'</sheets>',b'<sheet name="Synthetic-chart" sheetId="2" r:id="rId2"/></sheets>')
                elif info.filename=='xl/_rels/workbook.xml.rels':
                    data=data.replace(b'</Relationships>',b'<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/chartsheet" Target="chartsheets/sheet1.xml"/></Relationships>')
                elif info.filename=='[Content_Types].xml':
                    data=data.replace(b'</Types>',b'<Override PartName="/xl/chartsheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.chartsheet+xml"/></Types>')
                out.writestr(info.filename,data)
            out.writestr('xl/chartsheets/sheet1.xml','<chartsheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetViews><sheetView workbookViewId="0"/></sheetViews></chartsheet>')
            out.writestr('xl/chartsheets/_rels/sheet1.xml.rels','<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"/>')
        raw=stream.getvalue()
    else:
        stream=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as out:
            for info in src.infolist():
                data=src.read(info.filename)
                if variant=='hidden' and info.filename=='xl/workbook.xml': data=data.replace(b'sheetId="1"',b'sheetId="1" state="hidden"')
                if info.filename=='xl/worksheets/sheet1.xml':
                    if variant=='understated': data=data.replace(b'<sheetData>',b'<dimension ref="A1:A1"/><sheetData>')
                    elif variant=='sparse': data=data.replace(b'<row r="2">',b'<row r="50001">').replace(b'2"',b'50001"')
                    elif variant=='formula': data=data.replace(b'<c r="B2" t="inlineStr"><is><t>occupied</t></is></c>',b'<c r="B2"><f>1+1</f><v>2</v></c>')
                out.writestr(info.filename,data)
        raw=stream.getvalue()
    m,kw=setup(tmp_path,[entry(raw,expected_container='xlsx')],{EID:raw})
    row=a.AcceptanceRunner(**kw).run(RUN)['matrix']['entries'][0]
    if variant in ('hidden','understated'): assert row['state']=='observed_unvalidated',row['blockers']
    elif variant=='sparse': assert row['state']=='refused'
    else:
        assert row['state']=='parsed_with_blockers',row['blockers']
        assert all(v is None for counts in row['counts'].values() for v in counts.values())
        if variant=='chart': assert artifact(kw,'citations')['totals']['unsupported']>0
        else: assert not artifact(kw,'observations')['units']


@pytest.mark.parametrize('kind',['cpu','memory','cancel','artifact_cap','matrix_cap','optional_dependency'])
def test_additional_resource_refusals_never_fallback(tmp_path,monkeypatch,kind):
    limits=a.AcceptanceLimits()
    raw=HEADER+KNOWN
    if kind=='cpu': limits=a.AcceptanceLimits(worker_cpu_seconds=1,worker_wall_seconds=3)
    elif kind=='memory': limits=a.AcceptanceLimits(worker_address_space_bytes=1024*1024)
    elif kind=='artifact_cap': limits=a.AcceptanceLimits(max_artifact_bytes=512)
    elif kind=='matrix_cap': limits=a.AcceptanceLimits(max_matrix_bytes=16)
    elif kind=='optional_dependency': raw=xlsx_bytes()
    m,kw=setup(tmp_path,[entry(raw,expected_container='xlsx' if kind=='optional_dependency' else 'csv')],{EID:raw},limits=limits)
    if kind=='cpu':
        def busy(*args):
            while True: pass
        monkeypatch.setattr(a,'_pipeline',busy)
    elif kind=='cancel':
        def cancel(*args): raise KeyboardInterrupt()
        monkeypatch.setattr(a,'_worker',cancel)
    elif kind=='optional_dependency':
        import builtins
        real=builtins.__import__
        def missing(name,*args,**kwargs):
            if name=='openpyxl': raise ImportError('synthetic-private-canary')
            return real(name,*args,**kwargs)
        monkeypatch.setattr(builtins,'__import__',missing)
    out=attempt(lambda:a.AcceptanceRunner(**kw).run(RUN))
    if kind in ('cancel','artifact_cap','matrix_cap'):
        assert type(out) is a.AcceptanceError
        if kind=='cancel': assert out.code=='CANCELLED'
        assert not (Path(kw['output_root'])/RUN).exists()
    else:
        assert type(out) is dict
        row=out['matrix']['entries'][0]
        assert row['state']=='refused' and not row['v2_valid']
        if kind=='optional_dependency': assert 'XLSX_DEPENDENCY_MISSING' in {d['code'] for d in row['blockers']}
    assert Path('/proc/self/task/'+str(os.getpid())+'/children').read_text().strip()==''


def test_same_inode_with_conflicting_declared_pin_refuses_both_actual_paths(tmp_path):
    second=entry(entry_id='entry_'+'2'*32,source_id='src_'+'2'*32,source_sha256='0'*64,
                 as_of='2026-02-01',snapshot_id='snap_'+'2'*32)
    m,kw=setup(tmp_path,[entry(),second],{EID:None,second['entry_id']:None})
    root=tmp_path/'sources'; inner=root/'inner'; inner.mkdir(mode=0o700)
    (inner/'original').write_bytes(HEADER+KNOWN)
    # Different safe root/relative handles open the SAME nlink-one original.
    kw['source_paths'][EID]=a.SourcePath(str(root),('inner','original'))
    kw['source_paths'][second['entry_id']]=a.SourcePath(str(inner),('original',))
    matrix=a.AcceptanceRunner(**kw).run(RUN)['matrix']
    assert matrix['totals']['refused']==2 and matrix['totals']['parser_started']==0
    assert all('DUPLICATE_SOURCE' in {d['code'] for d in row['blockers']} for row in matrix['entries'])


def test_actual_serialized_aggregate_cap_stops_before_later_workers(tmp_path,monkeypatch):
    probe=tmp_path/'probe'; probe.mkdir(mode=0o700)
    raw=HEADER+b''.join(KNOWN.replace(b'101,',str(n).encode()+b',',1) for n in range(1,65))
    m,kw=setup(probe,[entry(raw)],{EID:raw})
    result=a.AcceptanceRunner(**kw).run(RUN)
    budget=sum(slot['size_bytes'] for slot in result['matrix']['artifacts'])+10000
    actual=tmp_path/'actual'; actual.mkdir(mode=0o700)
    entries=[]; raws={}
    for n in range(1,4):
        value=raw.replace(b'1,occupied,',str(100+n).encode()+b',occupied,',1)
        eid='entry_'+format(n,'032x')
        entries.append(entry(value,entry_id=eid,source_id='src_'+format(n,'032x'),
            as_of='2026-01-0'+str(n),snapshot_id='snap_'+format(n,'032x')))
        raws[eid]=value
    m,kw=setup(actual,entries,raws,limits=a.AcceptanceLimits(max_artifact_bytes=budget))
    real=a._worker; started=[]
    def counted(*args):
        started.append(args[1]['entry_id'])
        return real(*args)  # Observe actual worker dispatch, do not fabricate a parser result.
    monkeypatch.setattr(a,'_worker',counted)
    out=attempt(lambda:a.AcceptanceRunner(**kw).run(RUN))
    assert type(out) is a.AcceptanceError
    assert len(started)==2, 'aggregate serialization must stop before a third worker'
    assert not (Path(kw['output_root'])/RUN).exists()


def test_host_only_api_seam():
    assert importlib.util.find_spec('plat_harness.ingest.acceptance') is not None
    from plat_harness.ingest import acceptance as a
    assert all(hasattr(a, name) for name in (
        'SourcePath', 'AcceptanceLimits', 'AcceptanceError', 'AcceptanceRunner'))
