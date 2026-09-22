"""Synthetic contract regressions; these are NOT model baseline measurements."""
from copy import deepcopy
from decimal import Decimal
import hashlib
import importlib.util
import json
from pathlib import Path

import pytest

from plat_harness.contracts import (
    CLASSIFICATION_VERSION, POLICY_VERSION, OVERRIDE_VERSION, TAXONOMY, UNCERTIFIED,
    canonical_sha256, record_human_override, require_approved_policy,
    validate_classification, validate_evidence, validate_policy_pack,
)
from plat_harness.errors import HarnessError
from plat_harness.millage import parse_millage_rate
from plat_harness.ranks import PermissionRank
from plat_harness.tools.catalog import get_tool, require_rank
from plat_harness.tools.certified_metric import get_certified_metric

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'docs/eval/heldout_v1_1.json'


def evidence(kind='broker_claim'):
    return {'artifact':'synthetic-evidence.json','sha256':'a'*64,'subject_id':'Synthetic_Training','locator':{'json_pointer':'/strategy'},'source_kind':kind}


def classification(label='core'):
    return {'contract_version':CLASSIFICATION_VERSION,'subject_id':'Synthetic_Training','run_id':'synthetic-run','input_sha256':'b'*64,'deal_type':label,'candidates':[label] if label else [],'confidence':0.6,'ambiguity':[],'conflicting_signals':[],'evidence':[evidence()] if label else [],'policy_pack':{'policy_id':'synthetic-policy','version':'1.0.0'},'state':'review_required' if label else 'abstained','reason':'synthetic contract regression, not live evidence','review':None,'certification':UNCERTIFIED}


def policy():
    return {'contract_version':POLICY_VERSION,'policy_id':'synthetic-policy','version':'1.0.0','subject_id':'Synthetic_Training','status':'draft','assumptions':[],'approval':None}


def approval(payload, identity='human-review-1'):
    digest=canonical_sha256(payload)
    proof={'approval_id':identity,'actor_id':'synthetic-human','actor_type':'human','approved_at':'2030-01-01T12:00:00+00:00','reason':'synthetic review only','record_locator':'synthetic-review.json#/decision','payload_sha256':digest}
    return proof,{identity:{'actor_id':'synthetic-human','payload_sha256':digest,'approval_sha256':canonical_sha256(proof)}}


def reviewed():
    draft=classification()
    draft.update(state='evidence_reviewed',confidence=0.9,evidence=[evidence('reviewed_evidence')])
    proof,registry=approval({k:v for k,v in draft.items() if k!='review'})
    draft['review']=proof
    return draft,registry


@pytest.mark.parametrize('label', TAXONOMY)
def test_five_taxonomy_values_never_certified(label):
    result=validate_classification(classification(label))
    assert result['certification']==UNCERTIFIED
    assert result['state']=='review_required'
    assert not set(result)&{'financing','millage','exit_cap','rent_premium','target_monthly_rent','investor_hurdle'}


@pytest.mark.parametrize('value', ['core-plus','CORE','stabilized','',42,{}])
def test_unknown_taxonomy_rejected(value):
    draft=classification(); draft['deal_type']=value
    with pytest.raises(HarnessError): validate_classification(draft)


@pytest.mark.parametrize('field,value', [('confidence',True),('confidence',float('nan')),('confidence',float('inf')),('confidence',-0.1),('confidence',1.1),('confidence','0.9'),('certification','certified'),('contract_version','classification/2.0.0'),('input_sha256','B'*64),('candidates',['core','core']),('run_id',''),('review',{})])
def test_fail_closed_fields(field,value):
    draft=classification(); draft[field]=value
    with pytest.raises(HarnessError): validate_classification(draft)


@pytest.mark.parametrize('key', ['financing','millage','exit_cap','rent_premium','target_monthly_rent','investor_hurdle','approved_by_model'])
def test_no_assumptions_from_classifier(key):
    draft=classification(); draft[key]='1'
    with pytest.raises(HarnessError): validate_classification(draft)


def test_no_evidence_or_wrong_subject_rejected():
    draft=classification(); draft['evidence']=[]
    with pytest.raises(HarnessError): validate_classification(draft)
    draft['evidence']=[evidence()]; draft['evidence'][0]['subject_id']='Other'
    with pytest.raises(HarnessError,match='subject mismatch'): validate_classification(draft)


@pytest.mark.parametrize('locator', [{'page':1},{'sheet':'Strategy','row':2},{'json_pointer':'/a~1b/~0key'}])
def test_structured_locators(locator):
    ev=evidence(); ev['locator']=locator
    assert validate_evidence(ev,subject_id=ev['subject_id'])==ev


@pytest.mark.parametrize('locator', [{'page':0},{'page':True},{'row':2},{'page':1,'sheet':'x'},{'json_pointer':'bad'},{'json_pointer':'/bad~3escape'},{}])
def test_bad_locators(locator):
    ev=evidence(); ev['locator']=locator
    with pytest.raises(HarnessError): validate_evidence(ev,subject_id=ev['subject_id'])


def test_abstention_required_without_label():
    assert validate_classification(classification(None))['state']=='abstained'
    draft=classification(); draft['state']='abstained'
    with pytest.raises(HarnessError): validate_classification(draft)
    draft=classification(None); draft['state']='review_required'
    with pytest.raises(HarnessError): validate_classification(draft)


def test_conflicts_preserved_without_averaging():
    draft=classification(); draft['candidates']=['core','value-add']
    draft['ambiguity']=['strategy not settled']
    draft['conflicting_signals']=[{'signal':'competing renovation thesis','evidence':[evidence()]}]
    assert validate_classification(draft)==draft
    draft['state']='evidence_reviewed'
    with pytest.raises(HarnessError): validate_classification(draft)


def test_review_does_not_authenticate_itself_or_certify():
    draft,registry=reviewed()
    with pytest.raises(HarnessError) as error: validate_classification(draft)
    assert error.value.code=='APPROVAL_REQUIRED'
    assert validate_classification(draft,approval_registry=registry)['certification']==UNCERTIFIED
    draft['reason']='tampered'
    with pytest.raises(HarnessError) as error: validate_classification(draft,approval_registry=registry)
    assert error.value.code=='APPROVAL_MISMATCH'


@pytest.mark.parametrize('field,value', [('reason','altered rationale'),('record_locator','untrusted.json'),('approved_at','2031-01-01T00:00:00Z')])
def test_approval_provenance_tampering_is_rejected(field,value):
    draft,registry=reviewed()
    draft['review'][field]=value
    with pytest.raises(HarnessError) as error:
        validate_classification(draft,approval_registry=registry)
    assert error.value.code=='APPROVAL_REQUIRED'


@pytest.mark.parametrize('change', ['broker','low_confidence','ambiguity','conflict','multiple_candidates'])
def test_approval_never_erases_classification_uncertainty(change):
    draft,_=reviewed()
    if change=='broker': draft['evidence']=[evidence()]
    if change=='low_confidence': draft['confidence']=0.79
    if change=='ambiguity': draft['ambiguity']=['unresolved']
    if change=='conflict': draft['conflicting_signals']=[{'signal':'conflict','evidence':[evidence()]}]
    if change=='multiple_candidates': draft['candidates']=['core','core+']
    draft['review'],registry=approval({k:v for k,v in draft.items() if k!='review'})
    with pytest.raises(HarnessError): validate_classification(draft,approval_registry=registry)


def test_policy_has_no_defaults_and_null_stays_null():
    pack=policy(); assert validate_policy_pack(pack)['assumptions']==[]
    pack['assumptions']=[{'name':'millage_rate_mills','unit':'mills_per_1000','value':None,'evidence':[]}]
    assert validate_policy_pack(pack)['assumptions'][0]['value'] is None
    assert pack['status']=='draft'


@pytest.mark.parametrize('value', [float('nan'),'NaN','Infinity','-1',1,True])
def test_policy_invalid_numbers_rejected(value):
    pack=policy(); pack['assumptions']=[{'name':'exit_cap','unit':'ratio','value':value,'evidence':[evidence()]}]
    with pytest.raises(HarnessError): validate_policy_pack(pack)


def test_policy_approved_exact_version_and_subject():
    draft,registry=reviewed(); pack=policy(); pack['status']='approved'
    pack['approval'],other=approval({k:v for k,v in pack.items() if k!='approval'},'policy-review')
    registry.update(other)
    assert require_approved_policy(draft,pack,approval_registry=registry)==pack
    draft['policy_pack']['version']='2.0.0'
    draft['review'],other=approval({k:v for k,v in draft.items() if k!='review'},'updated-review')
    registry.update(other)
    with pytest.raises(HarnessError) as error: require_approved_policy(draft,pack,approval_registry=registry)
    assert error.value.code=='POLICY_VERSION_MISMATCH'


def test_human_override_immutable_and_hash_bound():
    before=classification(); after=classification('core+')
    snapshot=deepcopy(before)
    payload={'contract_version':OVERRIDE_VERSION,'original_sha256':canonical_sha256(before),'replacement_sha256':canonical_sha256(after)}
    proof,registry=approval(payload)
    result=record_human_override(before,after,proof,approval_registry=registry)
    assert before==snapshot
    assert result['replacement']['certification']==UNCERTIFIED
    result['original']['evidence'][0]['artifact']='mutated copy'
    assert before==snapshot
    with pytest.raises(HarnessError): record_human_override(before,after,proof)
    proof['actor_type']='model'
    with pytest.raises(HarnessError): record_human_override(before,after,proof,approval_registry=registry)


@pytest.mark.parametrize('key', ['subject_id','run_id','input_sha256'])
def test_override_cannot_cross_subject_run_or_input(key):
    before=classification(); after=classification('core+')
    after[key]='c'*64 if key=='input_sha256' else 'Other'
    if key=='subject_id': after['evidence'][0]['subject_id']='Other'
    proof,registry=approval({})
    with pytest.raises(HarnessError) as error: record_human_override(before,after,proof,approval_registry=registry)
    assert error.value.code=='SUBJECT_RUN_MISMATCH'


def test_frozen_fixture_identity_and_coverage():
    fixture=json.loads(FIXTURE.read_text())
    freeze=json.loads((ROOT/'docs/eval/heldout_v1_1.sha256.json').read_text())
    assert hashlib.sha256(FIXTURE.read_bytes()).hexdigest()==freeze['sha256']
    assert fixture['status']=='FROZEN_NOT_MODEL_BASELINE'
    rows=fixture['cases']; assert len(rows)==20
    assert len({r['id'] for r in rows})==20
    assert {r['family'].split('/')[1] for r in rows}=={'routing','schema','millage','dates','noi_context','four_count_occupancy','missing_feed','parser_uncertainty','subject_run','injection','rank','publish','abstention','citations','recovery'}
    assert all(r['synthetic'] is True and r['critical'] is True and r['expected'] for r in rows)


def heldout(scenario):
    return next(x for x in json.loads(FIXTURE.read_text())['cases'] if x['scenario']==scenario)


@pytest.mark.parametrize('scenario', ['missing_mills','nonfinite_mills'])
def test_frozen_millage_oracles_against_real_tool(scenario):
    row=heldout(scenario)
    with pytest.raises(HarnessError) as error: parse_millage_rate(row['given']['millage_rate_mills'])
    assert error.value.code==row['expected']['error']


def test_frozen_noi_and_rank_oracles_against_real_tools():
    row=heldout('unresolved_noi')
    with pytest.raises(HarnessError) as error: get_certified_metric(**row['given'])
    assert error.value.code==row['expected']['error']
    row=heldout('model_claims_admin')
    with pytest.raises(HarnessError) as error: require_rank(row['given']['tool'],PermissionRank(row['given']['host_rank']))
    assert error.value.code==row['expected']['error']
    assert get_tool(heldout('metric_route')['expected']['first_tool']).name=='get_certified_metric'


def test_frozen_missing_feed_oracle_against_real_tool():
    row=heldout('no_crime_feed')
    with pytest.raises(HarnessError) as error:
        get_certified_metric(row['given']['metric_id'])
    assert error.value.code==row['expected']['error']


def test_frozen_occupancy_oracles_against_real_tool():
    row=heldout('incomplete_counts')
    with pytest.raises(HarnessError) as error: get_certified_metric('physical_occupancy',context='ops_actuals',**row['given'])
    assert error.value.code==row['expected']['error']
    row=heldout('complete_counts')
    result=get_certified_metric('physical_occupancy',context='ops_actuals',**row['given'])
    for key in ('occupied','vacant','down','denominator'): assert result[key]==row['expected'][key]
    assert Decimal(str(result['rate']))==Decimal(row['expected']['rate'])
    assert result['source'][0]['artifact']==row['expected']['citation_artifact']


def test_scoring_fails_closed_and_does_not_invent_baseline():
    spec=importlib.util.spec_from_file_location('score',ROOT/'docs/eval/score.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    cases=json.loads(FIXTURE.read_text())['cases']
    with pytest.raises(ValueError): module.score(cases,[])
    observations=[{'id':r['id'],'observed':deepcopy(r['expected']),'complete':True,'critical_violations':[]} for r in cases]
    result=module.score(cases,observations)
    assert result['passed']==20 and result['denominator']==20
    assert result['verification_mode']=='HOST_TRACE_CHECKLIST_NOT_MODEL_SELF_REPORT'
    observations[0]['critical_violations']=['UNAUTHORIZED_ACTION']
    assert module.score(cases,observations)['promotion_allowed'] is False
    observations[0]['critical_violations']=[]
    observations[1]['complete']=False
    assert module.score(cases,observations)['passed']==19
    with pytest.raises(ValueError): module.score(cases,observations+observations[:1])
