"""Fabricated decision registers only; no private economics in repository."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from plat_harness.errors import HarnessError
from plat_harness.gate_evidence import compare, render_markdown


def dump(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    raw=json.dumps(value).encode()
    with os.fdopen(os.open(path, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600), 'wb') as f:
        f.write(raw)
    return {'path':str(path),'sha256':hashlib.sha256(raw).hexdigest()}


def fixture(root, change=None):
    source=dump(root/'source.json', {'tax':{'candidate':'10'}})
    evidence={'artifact':source['path'],'sha256':source['sha256'],
              'locator':{'json_pointer':'/tax'},'epistemic_kind':'source_candidate'}
    row={'id':'D1','slug':'synthetic_property','asset_id':'synthetic_apartments',
         'field':'combined_millage_mills','units':'mills_per_1000',
         'proposed_value':{'source_tax_year':'2024','parcel_ids':['parcel_a'],'value':None},
         'approved':False,'approval':None,'status':'blocked_by_evidence',
         'evidence':[evidence],'remaining_gate':['Supply authority-issued all-parcel tax schedule.']}
    window={**copy.deepcopy(row),'id':'D2','field':'analysis_window',
            'proposed_value':{'start':'2025-01-01','end':'2029-12-31'},
            'status':'recommended_pending_authorization','units':'dates'}
    reg={'schema_version':'director-decision-register/1.0.0',
         'assets':[{'id':'synthetic_apartments','slug':'synthetic_property','scope':'Apartments only; retail excluded'}],
         'selected_slugs':['synthetic_property'],'decisions':[row,window],'human_approval':None}
    before=dump(root/'before.json',reg)
    after=copy.deepcopy(reg)
    if change: change(after)
    after=dump(root/'after.json',after)
    captures=dump(root/'captures.json',{'pdf_pages':[],'static_workbooks':[]})
    req={'schema':'gate-evidence-request/1.0.0','label':'SYNTHETIC',
         'subject':'synthetic_property','asset_id':'synthetic_apartments',
         'before':{'register':before,'captures':captures,'policy':None},
         'after':{'register':after,'captures':captures,'policy':None}}
    return req,source


def call(root,req):
    ref=dump(root/'request.json',req)
    return compare(ref['path'],ref['sha256'])


def test_unchanged_reproducible_read_only(tmp_path):
    req,_=fixture(tmp_path)
    ref=dump(tmp_path/'request.json',req)
    before={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    a=compare(ref['path'],ref['sha256']); assert a==compare(ref['path'],ref['sha256'])
    assert a['comparison']=='UNCHANGED'
    assert a['status']=='REFUSED' and not a['certified'] and not a['engine_executed']
    assert a['financial_metrics'] is None
    assert a['changes']==[] and len(a['handoff'])==2
    assert a['records'][0]['after']['binding']['tax_year']=='2024'
    assert a['records'][0]['after']['binding']['parcel_ids']==['parcel_a']
    assert a['records'][0]['after']['evidence'][0]['state']=='VERIFIED'
    assert before=={str(p):p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    assert 'Draft handoff' in render_markdown(a)


def test_changed_value_has_exact_leaf_delta(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0]['proposed_value'].update(value='12'))
    a=call(tmp_path,req)
    assert a['comparison']=='CHANGED'
    delta=next(x for x in a['changes'] if x['pointer']=='/proposed_value/value')
    assert delta['before'] is None and delta['after']=='12'
    assert delta['before_evidence']['json_pointer']=='/decisions/0/proposed_value/value'
    assert a['records'][0]['after']['state']=='MISSING'


@pytest.mark.parametrize('kind,state',[('remove','MISSING'),('stale','STALE'),('conflict','CONFLICTING'),('spoof','CONFLICTING')])
def test_missing_stale_conflict_spoof(tmp_path,kind,state):
    def change(reg):
        row=reg['decisions'][0]
        if kind=='remove': row['evidence']=[]
        if kind=='stale': row['evidence'][0]['sha256']='0'*64
        if kind=='conflict': row['conflicts']=['incompatible source columns']
        if kind=='spoof': row['approved']=True
    req,_=fixture(tmp_path,change)
    a=call(tmp_path,req)
    assert a['records'][0]['after']['state']==state
    assert not a['execution_authorized']


@pytest.mark.parametrize('change',[
    lambda x:x['decisions'][0].update(asset_id='retail'),
    lambda x:x['decisions'][0].update(slug='different'),
    lambda x:x['decisions'][0]['proposed_value'].update(source_tax_year='2025'),
    lambda x:x['decisions'][0]['proposed_value'].update(parcel_ids=['parcel_b']),
    lambda x:x['decisions'][1]['proposed_value'].update(start='2026-01-01'),
    lambda x:x['decisions'][0].update(units='percent'),
    lambda x:x['assets'][0].update(scope='Apartments and retail'),
])
def test_cross_scope_refused(tmp_path,change):
    req,_=fixture(tmp_path,change)
    with pytest.raises(HarnessError) as e: call(tmp_path,req)
    assert e.value.code in ('SUBJECT_MISMATCH','SCOPE_MISMATCH','PERIOD_MISMATCH','UNIT_MISMATCH')


def test_removed_record_is_not_resolved(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'].pop(0))
    a=call(tmp_path,req)
    assert a['records'][0]['after']['state']=='MISSING'
    assert a['records'][0]['change']=='REMOVED'
    assert a['handoff'][0]['retry_authorized'] is False


@pytest.mark.parametrize('pointer',['/absent','/tax/nope','/tax/~2bad'])
def test_invalid_locator_not_verified(tmp_path,pointer):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0]['evidence'][0]['locator'].update(json_pointer=pointer))
    a=call(tmp_path,req)
    assert a['records'][0]['after']['evidence'][0]['state'] in ('MISSING','CONFLICTING')


def test_root_pointer_and_null_are_distinct(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0]['evidence'][0].update(locator={'json_pointer':''}))
    assert call(tmp_path,req)['records'][0]['after']['evidence'][0]['state']=='VERIFIED'


@pytest.mark.parametrize('bad',['A'*64,'0'*63,None])
def test_bad_pin_before_lookup(bad):
    with pytest.raises(HarnessError) as e: compare('/not/here',bad)
    assert e.value.code=='INVALID_HASH'


def test_request_authority_injection_rejected(tmp_path):
    req,_=fixture(tmp_path); req['approval_registry']={}
    with pytest.raises(HarnessError): call(tmp_path,req)


def test_duplicate_record_rejected(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'].append(copy.deepcopy(x['decisions'][0])))
    with pytest.raises(HarnessError): call(tmp_path,req)


def test_source_missing_and_symlink(tmp_path):
    req,source=fixture(tmp_path); p=Path(source['path']); p.unlink()
    a=call(tmp_path,req); assert a['records'][0]['after']['evidence'][0]['state']=='MISSING'
    p.symlink_to(tmp_path/'before.json')
    ref=dump(tmp_path/'request2.json',req)
    a=compare(ref['path'],ref['sha256'])
    assert a['records'][0]['after']['evidence'][0]['state']=='CONFLICTING'


def test_host_policy_contract_not_snapshot_authority(tmp_path,monkeypatch):
    from plat_harness.contracts import canonical_sha256
    req,_=fixture(tmp_path)
    policy={'contract_version':'policy-pack/1.0.0','policy_id':'synthetic','version':'1.0.0',
            'subject_id':'synthetic_apartments','status':'approved','assumptions':[],'approval':None}
    a={'approval_id':'fixture','actor_id':'fixture-human','actor_type':'human','approved_at':'2026-01-01T00:00:00Z',
       'reason':'Synthetic only','record_locator':'fixture','payload_sha256':canonical_sha256({k:v for k,v in policy.items() if k!='approval'})}
    policy['approval']=a
    req['after']['policy']=dump(tmp_path/'policy.json',policy)
    r=dump(tmp_path/'request.json',req)
    monkeypatch.delenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH',raising=False)
    assert compare(r['path'],r['sha256'])['policy_contract']['after']['state']=='UNREVIEWED'
    reg=dump(tmp_path/'host.json',{'fixture':{'actor_id':a['actor_id'],'payload_sha256':a['payload_sha256'],'approval_sha256':canonical_sha256(a)}})
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_PATH',reg['path'])
    monkeypatch.setenv('PLAT_HARNESS_CONTRACT_REGISTRY_SHA256',reg['sha256'])
    out=compare(r['path'],r['sha256'])
    assert out['policy_contract']['after']['state']=='APPROVED'
    assert out['execution_authorized'] is False and out['certified'] is False


@pytest.mark.parametrize('fmt',['json','markdown'])
def test_real_cli_subprocess_cross_cwd(tmp_path,fmt):
    req,_=fixture(tmp_path); r=dump(tmp_path/'request.json',req)
    src=Path(__file__).resolve().parents[1]/'harness/src'
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
    cmd=[sys.executable,'-B','-c',f'import sys;sys.path.insert(0,{str(src)!r});from plat_harness.cli import main;raise SystemExit(main())',
         'gate-evidence','--request',r['path'],'--request-sha256',r['sha256'],'--format',fmt]
    p=subprocess.run(cmd,cwd=tmp_path,env=env,capture_output=True,text=True)
    assert p.returncode==2 and not p.stderr,p.stderr
    if fmt=='json': assert json.loads(p.stdout)['comparison']=='UNCHANGED'
    else: assert 'Draft handoff' in p.stdout


@pytest.mark.parametrize('kind,valid',[('page',True),('page',False),('cell',True),('cell',False)])
def test_captured_page_sheet_locators_without_parser(tmp_path,kind,valid):
    req,source=fixture(tmp_path)
    reg=json.loads(Path(req['after']['register']['path']).read_text())
    e=reg['decisions'][0]['evidence'][0]
    captures={'pdf_pages':[],'static_workbooks':[]}
    if kind=='page':
        e['locator']={'pdf_page':2}
        captures['pdf_pages']=[{**source,'pdf_page':2 if valid else 1,'exit_code':0,'text':'Synthetic captured page'}]
    else:
        e['locator']={'sheet':'Input','label_cell':'B2','label':'Tax label'}
        captures['static_workbooks']=[{**source,'cells':{'Input!B2':{'value':'Tax label' if valid else 'Different label'}}}]
    req['after']['register']=dump(tmp_path/'locator-register.json',reg)
    req['after']['captures']=dump(tmp_path/'locator-captures.json',captures)
    out=call(tmp_path,req)['records'][0]['after']['evidence'][0]
    assert (out['state']=='VERIFIED') is valid
    if valid:
        from plat_harness.gate_evidence import pointer
        assert pointer(captures,out['capture']['json_pointer']) is not None


def test_stale_register_pin_is_hard_refusal(tmp_path):
    req,_=fixture(tmp_path);req['after']['register']['sha256']='0'*64
    with pytest.raises(HarnessError) as e: call(tmp_path,req)
    assert e.value.code=='HASH_MISMATCH'


def test_explicit_cross_asset_evidence_conflicting(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0]['evidence'][0].update(subject_id='retail'))
    out=call(tmp_path,req)
    assert out['records'][0]['after']['state']=='CONFLICTING'


def test_forged_string_approval_and_provenance(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0].update(approved='true',approval={'actor_type':'human'}))
    out=call(tmp_path,req)
    assert out['records'][0]['after']['code']=='SNAPSHOT_CLAIM_NOT_EXECUTABLE_AUTHORITY'


def test_null_source_is_missing(tmp_path):
    req,_=fixture(tmp_path)
    reg=json.loads(Path(req['after']['register']['path']).read_text())
    src=dump(tmp_path/'null.json',{'tax':None})
    reg['decisions'][0]['evidence'][0].update(artifact=src['path'],sha256=src['sha256'])
    req['after']['register']=dump(tmp_path/'null-register.json',reg)
    assert call(tmp_path,req)['records'][0]['after']['evidence'][0]['state']=='MISSING'


def test_markdown_escapes_source_text(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0]['remaining_gate'].append('<script>bad</script>|'))
    text=render_markdown(call(tmp_path,req))
    assert '<script>' not in text and '&lt;script&gt;' in text


def test_cross_period_source_binding_conflicting(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0]['evidence'][0].update(tax_year='2023'))
    out=call(tmp_path,req)
    assert out['records'][0]['after']['state']=='CONFLICTING'


def test_positive_status_is_not_authority(tmp_path):
    req,_=fixture(tmp_path,lambda x:x['decisions'][0].update(status='approved'))
    assert call(tmp_path,req)['records'][0]['after']['state']=='CONFLICTING'


@pytest.mark.parametrize('change',[
    lambda x:x.update(selected_slugs=None),
    lambda x:x['decisions'][0]['proposed_value'].update(parcel_ids='unstructured'),
    lambda x:x['decisions'][1]['proposed_value'].update(start=True),
])
def test_malformed_binding_typed_refusal(tmp_path,change):
    req,_=fixture(tmp_path,change)
    with pytest.raises(HarnessError): call(tmp_path,req)
