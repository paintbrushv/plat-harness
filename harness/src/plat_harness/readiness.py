"""Read-only explanation of hash-pinned historical readiness evidence.

Not an intake audit, parser, assumption selector, authority registry or engine.
A snapshot pin proves bytes, not business approval or current market validity.
Only existing contracts.py and the independently pinned host registry can verify
an optional policy approval; even that never authorizes execution here.
"""
from __future__ import annotations

from pathlib import Path
from typing import Mapping

from plat_harness.adapters import slice_b as b
from plat_harness.adapters.review_bridge import decode, host_registry
from plat_harness.contracts import validate_policy_pack
from plat_harness.errors import HarnessError

COLUMNS = ('source_validity', 'canonical_readiness', 'analysis_approval',
           'tax_approval', 'policy_approval', 'engine_eligibility',
           'reconciliation', 'certification')
BOOLS = {'canonical_readiness':('ready',), 'analysis_approval':('approved',),
         'tax_approval':('approved',), 'policy_approval':('approved',),
         'engine_eligibility':('eligible','live_authorized'),
         'reconciliation':('reviewed_pass',), 'certification':('certified',)}
ACTIONS = {
 'T12_REPAIRS_NOT_IMPLEMENTED': ('engineering_permission', 'Obtain explicit renewed intake-repair authority before regression-first T12 repair; no retry is authorized.'),
 'CANONICAL_TARGET_RENT_GATE_NOT_IMPLEMENTED': ('engineering_permission', 'Renew intake repair authority before implementing the canonical target-rent gate; never impute target rent.'),
 'UNVERIFIED_MONTH_COVERAGE': ('source_and_engineering', 'Reconcile exact monthly headers and completeness to the source after renewed intake authority; do not sum an annual-total column.'),
 'OPEX_BOUNDARY_AND_MAPPING_REVIEW_REQUIRED': ('source_and_engineering', 'Resolve operating versus financing boundaries, signs and subtotal ties; intake edits remain gated.'),
 'AMBIGUOUS_FLOORPLAN': ('source_evidence', 'Supply source-backed floorplan mapping and occupancy cohorts; do not infer bedroom count from an opaque code.'),
 'MISSING_INPLACE_RENT': ('source_evidence', 'Recover supported base rent and charge-code mapping; missing rent is not zero or target rent.'),
 'MISSING_MILLAGE': ('source_evidence_then_human', 'Complete asset/parcel/year applicability with explicit rate units, charges and engine representability, then obtain separate tax selection approval.'),
 'NEEDS_ANALYSIS_WINDOW': ('human_analytical_approval', 'Accept or amend the exact historical replay window in the existing analytical package; not current underwriting or live execution authority.'),
 'MIXED_USE_REVENUE_AND_TRANSACTION_SCOPE_REVIEW_REQUIRED': ('source_evidence_then_human', 'Bind the conveyed interest; reconcile residential, commercial and shared costs separately. Deferred interests are not zero value.'),
 'COMMERCIAL_SCOPE_REVIEW_REQUIRED': ('source_evidence_then_human', 'Bind commercial units, leases, commencement and CAM separately from apartments.'),
 'PORTFOLIO_ASSET_SCOPE_REVIEW_REQUIRED': ('source_evidence_then_human', 'Keep each named asset separate; require explicit supported basis and tax allocation before any portfolio scenario.')}


def _sha(value):
    if not isinstance(value, str) or not b.SHA.fullmatch(value):
        b.refuse('INVALID_HASH', 'Literal lowercase SHA256 required; no normalization.')


def _object(value, where):
    if not isinstance(value, dict):
        b.refuse('INVALID_READINESS', f'{where}: object required.')
    return value


def _strings(value, where):
    if not isinstance(value, list) or any(not isinstance(x,str) or not x for x in value) or len(set(value))!=len(value):
        b.refuse('INVALID_READINESS', f'{where}: unique nonempty strings required.')
    return value


def _bound(path, pin):
    _sha(pin)
    raw=b._read(path)
    if b.digest(raw)!=pin:
        b.refuse('HASH_MISMATCH', 'Registered evidence differs from exact pinned bytes.', path=str(path))
    return raw


def policy_state(value, subject: str, registry: Mapping[str,dict]):
    """Trusted-host API only; registry is never taken from evidence/model JSON."""
    if value is None:
        return {'state':'MISSING','code':'POLICY_CONTRACT_MISSING'}
    if not isinstance(value,dict) or value.get('subject_id')!=subject:
        return {'state':'CONFLICTING','code':'SUBJECT_MISMATCH'}
    try:
        pack=validate_policy_pack(value,approval_registry=registry)
    except HarnessError as exc:
        state='STALE' if exc.code=='APPROVAL_MISMATCH' else 'UNREVIEWED' if exc.code=='APPROVAL_REQUIRED' else 'CONFLICTING'
        return {'state':state,'code':exc.code}
    return {'state':'APPROVED' if pack['status']=='approved' else 'UNREVIEWED',
            'code':'POLICY_CONTRACT_VALIDATED','policy_id':pack['policy_id'],
            'version':pack['version'],'execution_authorized':False}


def _reference_status(ref):
    _object(ref,'source reference')
    _sha(ref.get('sha256'))
    if not isinstance(ref.get('path'),str):
        b.refuse('INVALID_READINESS','Reference path required.')
    try:
        _bound(ref['path'],ref['sha256'])
        state,code='VERIFIED',None
    except HarnessError as exc:
        state={'NOT_FOUND':'MISSING','HASH_MISMATCH':'STALE'}.get(exc.code,'CONFLICTING')
        code=exc.code
    return {'path':ref['path'],'sha256':ref['sha256'],'state':state,'code':code}


def explain(bundle, index_sha256, subject, *, policy_path=None, policy_sha256=None):
    """No writes or subprocesses. Scope is at most five pinned snapshot subjects."""
    _sha(index_sha256)  # Validate before filesystem lookup.
    if not isinstance(subject,str) or not b.ID.fullmatch(subject):
        b.refuse('INVALID_ID','Exact subject identifier required.')
    root=b.safe_path(bundle,directory=True)
    index=decode(_bound(root/'artifact_index.json',index_sha256))
    files=_object(index.get('files'),'artifact index files')
    for name,pin in files.items():
        if not isinstance(name,str) or not name or Path(name).is_absolute() or '..' in Path(name).parts:
            b.refuse('UNSAFE_PATH','Index members must be relative non-traversing paths.')
        _sha(pin)
    def registered(name):
        if name not in files:
            b.refuse('EVIDENCE_UNREGISTERED','Required snapshot artifact is not registered.',artifact=name)
        return decode(_bound(root/name,files[name]))
    matrix=registered('director/readiness_matrix.json')
    package=registered('director/approval_package.json')
    authorization=registered('authorization.json')
    if matrix.get('schema')!='five-deal-readiness/1.0.0':
        b.refuse('INVALID_READINESS','Unsupported readiness schema.')
    if matrix.get('approval_package_sha256')!=files['director/approval_package.json']:
        b.refuse('HASH_MISMATCH','Readiness and analytical package binding differs.')
    rows=matrix.get('rows')
    if not isinstance(rows,list) or not 1<=len(rows)<=5:
        b.refuse('INVALID_READINESS','One to five snapshot subjects required.')
    slugs=[]
    assets=package.get('assets')
    if not isinstance(assets,list) or not all(isinstance(x,dict) for x in assets):
        b.refuse('INVALID_READINESS','Explicit package assets required.')
    for row in rows:
        _object(row,'row')
        slug=row.get('slug')
        if not isinstance(slug,str) or not b.ID.fullmatch(slug):
            b.refuse('INVALID_READINESS','Exact row subject required.')
        slugs.append(slug)
        ids=_strings(row.get('asset_ids'),'asset IDs')
        if not ids or set(ids)!={a.get('id') for a in assets if a.get('slug')==slug}:
            b.refuse('SUBJECT_MISMATCH','Row assets differ from bound analytical scope.')
        for column in COLUMNS:
            value=_object(row.get(column),column)
            for field in BOOLS.get(column,()):
                if type(value.get(field)) is not bool:
                    b.refuse('INVALID_READINESS',f'{column}/{field}: literal boolean required.')
        _strings(row['canonical_readiness'].get('blockers'),'canonical blockers')
        _object(row['canonical_readiness'].get('evidence'),'canonical evidence')
        if not isinstance(row['analysis_approval'].get('windows'),list):
            b.refuse('INVALID_READINESS','Analysis windows list required.')
    if len(set(slugs))!=len(slugs) or set(slugs)!=set(_strings(package.get('selected_slugs'),'package subjects')):
        b.refuse('SUBJECT_MISMATCH','Snapshot subjects must exactly match the package without duplicates.')
    if subject not in slugs:
        b.refuse('SUBJECT_MISMATCH','Subject is outside this bounded snapshot.')
    row=rows[slugs.index(subject)]; row_pointer=f'/rows/{slugs.index(subject)}'
    matrix_ref={'path':str(root/'director/readiness_matrix.json'),'sha256':files['director/readiness_matrix.json'],'json_pointer':row_pointer}
    refs=[row['canonical_readiness']['evidence']]
    dependencies=package.get('source_dependencies')
    if not isinstance(dependencies,list) or not all(isinstance(x,dict) for x in dependencies):
        b.refuse('INVALID_READINESS','Source dependencies required.')
    refs += [x for x in dependencies if x.get('slug')==subject]
    # Recommendation files are private derived evidence, not approval.
    recommendations=package.get('exact_recommendation_files')
    if not isinstance(recommendations,list):
        b.refuse('INVALID_READINESS','Recommendation register required.')
    refs += recommendations
    if len(refs)>64:
        b.refuse('INVALID_READINESS','Readiness dependency budget exceeded.')
    evidence=[_reference_status(x) for x in refs]
    problems=[x for x in evidence if x['state']!='VERIFIED']
    integrity=next((s for s in ('CONFLICTING','STALE','MISSING') if any(x['state']==s for x in problems)),'VERIFIED')
    gates=[]
    details={
        'source_validity':row['source_validity'].get('detail','Economic evidence remains unreviewed.'),
        'canonical_readiness':'Canonical input not validated; no forecast is generated.',
        'analysis_approval':'Analytical recommendation is not a human-approved analysis window.',
        'tax_approval':row['tax_approval'].get('detail','Tax units and applicability require evidence and separate approval.'),
        'policy_approval':'Analytical package is not an approved executable policy payload.',
        'engine_eligibility':'Exact live-input and execution authority is absent; this command never runs the engine.',
        'reconciliation':'No source-complete live reviewed reconciliation is established.',
        'certification':'No live certification is issued by this read-only explanation.'}
    for column in COLUMNS:
        state='UNREVIEWED' if column in ('source_validity','analysis_approval','policy_approval') else 'MISSING' if column in ('tax_approval','reconciliation') else 'BLOCKED'
        code={'source_validity':'SOURCE_ECONOMIC_REVIEW_REQUIRED','canonical_readiness':'BLOCKED_NOT_CANONICAL',
              'analysis_approval':'APPROVAL_REQUIRED','tax_approval':'TAX_EVIDENCE_AND_APPROVAL_REQUIRED',
              'policy_approval':'APPROVAL_REQUIRED','engine_eligibility':'LIVE_EXECUTION_NOT_AUTHORIZED',
              'reconciliation':'RECON_EVIDENCE_INCOMPLETE','certification':'UNCERTIFIED'}[column]
        if any(row[column].get(f) is True for f in BOOLS.get(column,())):
            state,code='CONFLICTING','SNAPSHOT_CLAIM_NOT_EXECUTABLE_AUTHORITY'
        if column=='source_validity' and integrity!='VERIFIED':
            state,code=integrity,'SOURCE_INTEGRITY_FAILED'
        gates.append({'gate':column,'state':state,'code':code,'detail':details[column],
                      'evidence':{**matrix_ref,'json_pointer':row_pointer+'/'+column}})
    actions=[]
    for blocker_index, code in enumerate(row['canonical_readiness']['blockers']):
        kind,action=ACTIONS.get(code,('unclassified_blocker','Resolve the recorded blocker with source evidence; no automatic permission or retry.'))
        actions.append({'code':code,'kind':kind,'next_action':action,'retry_authorized':False,
                        'evidence':{**matrix_ref,'json_pointer':row_pointer+'/canonical_readiness/blockers/'+str(blocker_index)},
                        'intake_evidence':row['canonical_readiness']['evidence']})
    actions.extend([
        {'code':'APPROVAL_REQUIRED','kind':'human_analytical_approval','next_action':'Existing exact analytical package remains for consideration; do not repeat an unchanged unanswered request. Later executable payload approval is separate.','retry_authorized':False,'evidence':{'path':str(root/'director/approval_package.json'),'sha256':files['director/approval_package.json'],'json_pointer':'/human_approval'}},
        {'code':'LIVE_EXECUTION_NOT_AUTHORIZED','kind':'execution_authority','next_action':'Only after all source, canonical, analysis, tax and policy gates, obtain exact live-input/execution authority.','retry_authorized':False,'evidence':{'path':str(root/'authorization.json'),'sha256':files['authorization.json'],'json_pointer':''}}])
    contract=policy_state(None,subject,{})
    if (policy_path is None)!=(policy_sha256 is None):
        b.refuse('INVALID_INPUT','Policy path and literal hash must be supplied together.')
    if policy_path is not None:
        policy=decode(_bound(policy_path,policy_sha256))
        ps=policy.get('subject_id')
        if ps not in row['asset_ids'] and ps!=subject:
            contract={'state':'CONFLICTING','code':'SUBJECT_MISMATCH'}
        else:
            try: registry=host_registry()
            except HarnessError as exc:
                if exc.code!='APPROVAL_REQUIRED': raise
                registry={}
            contract=policy_state(policy,ps,registry)
        contract['evidence']={'path':str(policy_path),'sha256':policy_sha256}
    return {'schema':'readiness-explanation/1.0.0','status':'REFUSED','subject':subject,
            'snapshot_as_of':matrix.get('as_of_utc'),'current_market_validity':'NOT_ESTABLISHED',
            'bundle':str(root),'index_sha256':index_sha256,'matrix_evidence':matrix_ref,
            'source_integrity':{'state':integrity,'checked':len(evidence),
                                'scope':'Selected registered package dependencies and intake-result bytes; not a complete raw-source or economic audit.',
                                'evidence':evidence},
            'scopes':[x for x in assets if x.get('slug')==subject],
            'analysis_windows':row['analysis_approval'].get('windows',[]),
            'gates':gates,'first_unmet_gate':gates[0],'actions':actions,
            'policy_contract':contract,'snapshot_authorization':authorization,
            'engine_executed':False,'certified':False,'financial_metrics':None,
            'note':'Historical evidence explanation only. Hash integrity is not economic validity, approval or execution eligibility. No model or financial arithmetic used.'}


def render_markdown(result):
    def text(value):
        # Data is rendered as text, never active markup/HTML.
        return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('`','\\`').replace('\n',' ')
    first=result['first_unmet_gate']
    lines=[f"# Readiness: {text(result['subject'])}",'',
           '**REFUSED — draft evidence explanation; not completed underwriting.**','',
           f"First unmet gate: **{text(first['gate'])} / {text(first['state'])}** — {text(first['code'])}",
           f"Snapshot: {text(result['snapshot_as_of'])}; current-market validity not established.",
           f"Source integrity: {text(result['source_integrity']['state'])}.",'',
           '| Gate | State | Required evidence / decision |','|---|---|---|']
    lines += [f"| {text(g['gate'])} | {text(g['state'])} | {text(g['detail'])} |" for g in result['gates']]
    lines += ['', '## Exact scope']+[f"- {text(s['id'])}: {text(s.get('scope',''))}" for s in result['scopes']]
    lines += ['', '## Next actions (none grants retry authority)']
    for a in result['actions']:
        e=a['evidence']; lines += [f"- **{text(a['code'])}** ({text(a['kind'])}): {text(a['next_action'])}", f"  Evidence: {text(e['path'])} · SHA256 {text(e['sha256'])} · {text(e.get('json_pointer','/'))}"]
    lines += ['', '## Evidence integrity']
    lines += [f"- {text(e['state'])}: {text(e['path'])} · SHA256 {text(e['sha256'])}" for e in result['source_integrity']['evidence']]
    lines += ['',f"Optional policy contract: {text(result['policy_contract']['state'])}. Even APPROVED here is not execution authority.",'',result['note'],'']
    return '\n'.join(lines)
