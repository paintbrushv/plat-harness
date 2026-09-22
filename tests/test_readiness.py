"""Synthetic fixtures only. Readiness is explanation, never execution authority."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
from plat_harness.errors import HarnessError
from plat_harness.readiness import explain, policy_state, render_markdown


def dump(p, obj):
    p.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    p.write_text(json.dumps(obj)); p.chmod(0o600)
    return hashlib.sha256(p.read_bytes()).hexdigest()


@pytest.fixture(autouse=True)
def private_root(tmp_path, monkeypatch):
    """Point PLAT_HARNESS_PRIVATE_ROOT at this test's private tmp area.

    Readiness bundle reads go through the adapter's fail-closed path gate,
    which is host configuration: each test uses a fresh 0700 root. The
    root itself is refused by the gate, so the fixture root is a dedicated
    subdirectory and bundles live below it.
    """
    tmp_path.chmod(0o700)
    root = tmp_path / 'private'
    root.mkdir(mode=0o700)
    monkeypatch.setenv('PLAT_HARNESS_PRIVATE_ROOT', str(root))
    return root


@pytest.fixture
def bundle(tmp_path, private_root):
    tmp_path.chmod(0o700)
    # The gate refuses the configured root itself, so the readiness bundle
    # lives one level below it.
    base = private_root / 'bundle'
    base.mkdir(mode=0o700)
    source=base/'raw.json'; sh=dump(source, {'synthetic': True})
    intake=base/'intake.json'; ih=dump(intake, {'status':'UNCERTIFIED'})
    row={'slug':'synthetic_property','asset_ids':['synthetic_apartments'],
         'source_validity':{'integrity':'HASH_VERIFIED','economic_validity':'INCOMPLETE_UNCERTIFIED_HISTORICAL','detail':'Synthetic source only'},
         'canonical_readiness':{'ready':False,'status':'BLOCKED_NOT_CANONICAL','blockers':['T12_REPAIRS_NOT_IMPLEMENTED','MISSING_MILLAGE'],'evidence':{'path':str(intake),'sha256':ih}},
         'analysis_approval':{'approved':False,'windows':[]},
         'tax_approval':{'approved':False,'combined_millage_mills':None,'detail':'Parcel/year applicability missing'},
         'policy_approval':{'approved':False},
         'engine_eligibility':{'eligible':False,'live_authorized':False},
         'reconciliation':{'reviewed_pass':False,'status':'NO_LIVE_REVIEWED_PASS'},
         'certification':{'certified':False,'status':'BLOCKED'}}
    matrix={'schema':'five-deal-readiness/1.0.0','as_of_utc':'2026-01-01T00:00:00Z','rows':[row]}
    package={'selected_slugs':['synthetic_property'],'approved':False,'human_approval':None,
             'assets':[{'id':'synthetic_apartments','slug':'synthetic_property','scope':'Synthetic apartments; retail excluded'}],
             'source_dependencies':[{'path':str(source),'sha256':sh,'slug':'synthetic_property','id':'S001'}],
             'exact_recommendation_files':[], 'additional_citation_dependencies':[]}
    ph=dump(base/'director/approval_package.json',package)
    matrix['approval_package_sha256']=ph
    dump(base/'director/readiness_matrix.json',matrix)
    dump(base/'authorization.json',{'intake_repairs':'BLOCKED_NO_RETRY_WITHOUT_RENEWED_PERMISSION','native_qwen_gpu_load':'NOT_AUTHORIZED'})
    def freeze():
        files={str(p.relative_to(base)):hashlib.sha256(p.read_bytes()).hexdigest() for p in [base/'director/approval_package.json',base/'director/readiness_matrix.json',base/'authorization.json']}
        return dump(base/'artifact_index.json',{'files':files})
    return base, freeze, matrix, package, source


def run(bundle):
    root,freeze,*_=bundle
    return explain(root,freeze(),'synthetic_property')


def test_workflow_and_reproducibility(bundle):
    a=run(bundle); b=run(bundle)
    assert a==b
    assert a['status']=='REFUSED' and a['certified'] is False
    assert a['first_unmet_gate']['gate']=='source_validity'
    assert a['first_unmet_gate']['state']=='UNREVIEWED'
    assert a['engine_executed'] is False
    assert a['policy_contract']['state']=='MISSING'
    assert a['source_integrity']['state']=='VERIFIED'
    assert a['scopes'][0]['scope'].endswith('retail excluded')
    action=next(x for x in a['actions'] if x['code']=='T12_REPAIRS_NOT_IMPLEMENTED')
    assert action['retry_authorized'] is False
    assert action['evidence']['sha256']
    assert a['financial_metrics'] is None
    assert 'First unmet gate' in render_markdown(a)


@pytest.mark.parametrize('mode,state', [('missing','MISSING'),('drift','STALE')])
def test_evidence_changes_fail_closed(bundle,mode,state):
    root,freeze,_,_,source=bundle
    pin=freeze()
    if mode=='missing': source.unlink()
    else: source.write_text('{}')
    result=explain(root,pin,'synthetic_property')
    assert result['source_integrity']['state']==state
    assert result['first_unmet_gate']['state']==state


def test_bad_hash_literal_before_lookup(bundle):
    with pytest.raises(HarnessError,match='SHA256'):
        explain('/does/not/exist','A'*64,'synthetic_property')


def test_index_drift(bundle):
    root,freeze,*_=bundle; pin=freeze()
    (root/'artifact_index.json').write_text('{}')
    with pytest.raises(HarnessError) as e: explain(root,pin,'synthetic_property')
    assert e.value.code=='HASH_MISMATCH'


@pytest.mark.parametrize('field', ['analysis_approval','tax_approval','policy_approval'])
def test_report_booleans_cannot_approve(bundle,field):
    root,freeze,m,_,_=bundle
    m['rows'][0][field]['approved']=True
    dump(root/'director/readiness_matrix.json',m)
    result=explain(root,freeze(),'synthetic_property')
    assert next(g for g in result['gates'] if g['gate']==field)['state']=='CONFLICTING'
    assert result['certified'] is False


@pytest.mark.parametrize('change', ['duplicate','unknown_subject','bad_boolean','missing_gate','cross_scope','package_pin'])
def test_malformed_matrix(bundle,change):
    root,freeze,m,p,_=bundle
    if change=='duplicate': m['rows'].append(copy.deepcopy(m['rows'][0]))
    if change=='unknown_subject': m['rows'][0]['slug']='different'
    if change=='bad_boolean': m['rows'][0]['canonical_readiness']['ready']='false'
    if change=='missing_gate': del m['rows'][0]['tax_approval']
    if change=='cross_scope': m['rows'][0]['asset_ids']=['wrong_asset']
    if change=='package_pin': m['approval_package_sha256']='0'*64
    dump(root/'director/readiness_matrix.json',m)
    with pytest.raises(HarnessError): explain(root,freeze(),'synthetic_property')


def test_unlisted_subject(bundle):
    root,freeze,*_=bundle
    with pytest.raises(HarnessError) as e: explain(root,freeze(),'sixth_subject')
    assert e.value.code=='SUBJECT_MISMATCH'


def test_symlink_source_refused(bundle):
    root,freeze,_,_,source=bundle; pin=freeze()
    target=root/'target'; target.write_bytes(source.read_bytes()); target.chmod(0o600)
    source.unlink(); source.symlink_to(target)
    result=explain(root,pin,'synthetic_property')
    assert result['source_integrity']['state']=='CONFLICTING'


def test_unsafe_index_entry(bundle):
    root,freeze,*_=bundle; freeze()
    idx=json.loads((root/'artifact_index.json').read_text())
    idx['files']['../escape']='0'*64
    pin=dump(root/'artifact_index.json',idx)
    with pytest.raises(HarnessError): explain(root,pin,'synthetic_property')


def test_duplicate_json_rejected(bundle):
    root,freeze,*_=bundle
    dump(root/'artifact_index.json',{})
    (root/'artifact_index.json').write_text('{"files":{},"files":{}}')
    pin=hashlib.sha256((root/'artifact_index.json').read_bytes()).hexdigest()
    with pytest.raises(HarnessError): explain(root,pin,'synthetic_property')


def test_policy_uses_existing_authority_contract():
    from plat_harness.contracts import canonical_sha256
    p={'contract_version':'policy-pack/1.0.0','policy_id':'synthetic','version':'1.0.0','subject_id':'synthetic_apartments','status':'draft','assumptions':[],'approval':None}
    assert policy_state(p,'synthetic_apartments',{})['state']=='UNREVIEWED'
    p['status']='approved'
    a={'approval_id':'fixture','actor_id':'fixture-human','actor_type':'human','approved_at':'2026-01-01T00:00:00Z','reason':'Synthetic only','record_locator':'fixture','payload_sha256':canonical_sha256({k:v for k,v in p.items() if k!='approval'})}
    p['approval']=a
    assert policy_state(p,'synthetic_apartments',{})['state']=='UNREVIEWED'
    registry={'fixture':{'actor_id':a['actor_id'],'payload_sha256':a['payload_sha256'],'approval_sha256':canonical_sha256(a)}}
    assert policy_state(p,'synthetic_apartments',registry)['state']=='APPROVED'
    assert policy_state(p,'different',registry)['state']=='CONFLICTING'
    p['version']='1.0.1'
    assert policy_state(p,'synthetic_apartments',registry)['state']=='STALE'


@pytest.mark.parametrize('fmt',['json','markdown'])
def test_cli_other_cwd_no_inherited_pythonpath(bundle,fmt):
    root,freeze,*_=bundle
    src=Path(__file__).resolve().parents[1]/'harness/src'
    env={k:v for k,v in os.environ.items() if k!='PYTHONPATH'}
    env['PYTHONDONTWRITEBYTECODE']='1'
    # Explicit source bootstrap belongs to this test, not inherited runner state.
    cmd=[sys.executable,'-B','-c',f'import sys;sys.path.insert(0,{str(src)!r});from plat_harness.cli import main;raise SystemExit(main())','readiness','--bundle',str(root),'--index-sha256',freeze(),'--deal','synthetic_property','--format',fmt]
    before={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
    p=subprocess.run(cmd,cwd=root,env=env,text=True,capture_output=True)
    assert p.returncode==2, p.stderr
    assert not p.stderr
    if fmt=='json': assert json.loads(p.stdout)['status']=='REFUSED'
    else: assert 'First unmet gate' in p.stdout
    assert before=={str(p):p.read_bytes() for p in root.rglob('*') if p.is_file()}
