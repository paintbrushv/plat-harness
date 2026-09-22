"""Read-only comparison of pinned decision registers, not an approval registry.

Captured page/cell entries are resolved without re-opening document parsers.
Integrity and approval-contract validity never establish economic completeness.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from plat_harness import contracts
from plat_harness.adapters import review_bridge as bridge
from plat_harness.adapters import slice_b as b
from plat_harness.errors import HarnessError
from plat_harness.readiness import _bound, _sha, policy_state

VERSION = 'gate-evidence-request/1.0.0'
REGISTER = 'director-decision-register/1.0.0'
BINDING_KEYS = ('parcel_ids', 'tax_year', 'period', 'units')


def exact(value, keys, where):
    contracts._object(value, set(keys), where)


def pointer(value, path):
    """Strict RFC6901, including empty root; a null leaf remains null."""
    if not isinstance(path, str) or (path and not path.startswith('/')) or re.search(r'~(?![01])', path):
        b.refuse('INVALID_LOCATOR', 'Literal RFC6901 pointer required.')
    if path == '':
        return value
    try:
        for token in path[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            if isinstance(value, list):
                if not re.fullmatch(r'0|[1-9][0-9]*', token):
                    raise ValueError()
                value = value[int(token)]
            else:
                value = value[token]
        return value
    except (KeyError, IndexError, TypeError, ValueError):
        b.refuse('EVIDENCE_INCOMPLETE', 'JSON pointer does not resolve.')


def escape(token):
    return str(token).replace('~', '~0').replace('/', '~1')


def read_ref(ref):
    exact(ref, ('path', 'sha256'), 'pinned reference')
    if not isinstance(ref['path'], str):
        b.refuse('INVALID_INPUT', 'Literal absolute reference path required.')
    return _bound(ref['path'], ref['sha256'])


def citation(ref, ptr=''):
    return {**ref, 'json_pointer':ptr}


def _state(exc):
    return {'NOT_FOUND':'MISSING', 'EVIDENCE_INCOMPLETE':'MISSING',
            'HASH_MISMATCH':'STALE', 'APPROVAL_MISMATCH':'STALE'}.get(exc.code, 'CONFLICTING')


def _evidence(item, captures, captures_ref, asset, cache, binding):
    if not isinstance(item, dict):
        b.refuse('INVALID_CONTRACT', 'Evidence object required.')
    result = {'source':item, 'state':'UNREVIEWED', 'code':'LOCATOR_NOT_SUPPORTED', 'capture':None}
    try:
        path, pin, loc = item.get('artifact'), item.get('sha256'), item.get('locator')
        _sha(pin)
        if not isinstance(path, str) or not isinstance(loc, dict):
            b.refuse('INVALID_LOCATOR', 'Exact artifact and structured locator required.')
        if item.get('subject_id', asset) != asset or item.get('asset_id', asset) != asset:
            b.refuse('SUBJECT_MISMATCH', 'Evidence is bound to another asset.')
        if item.get('slug',binding['subject']) != binding['subject']:
            b.refuse('SUBJECT_MISMATCH', 'Evidence is bound to another subject.')
        for field in BINDING_KEYS:
            value=item.get(field)
            expected=binding[field]
            if value is not None and expected is not None and (type(value)!=type(expected) or value!=expected):
                b.refuse('PERIOD_MISMATCH' if field in ('tax_year','period') else 'SCOPE_MISMATCH',
                         'Source evidence applicability conflicts with the register.',field=field)
        # Policy source-code line citations are retained, not re-audited under
        # this private evidence interface. Never claim they were verified.
        if 'lines' in loc:
            return result
        key=(path,pin)
        if key not in cache:
            cache[key]=_bound(path,pin)
        raw=cache[key]
        if set(loc)=={'json_pointer'}:
            if loc['json_pointer'] != '':
                contracts.validate_evidence({'artifact':path,'sha256':pin,'subject_id':asset,
                    'source_kind':'analyst_inference','locator':loc},subject_id=asset)
                bridge.evidence_exists([{'artifact':path,'sha256':pin,'locator':loc}],{path:raw})
            value=pointer(bridge.decode(raw),loc['json_pointer'])
            if value is None:
                b.refuse('EVIDENCE_INCOMPLETE','Null is missing evidence, not zero.')
            result['capture']=citation({'path':path,'sha256':pin},loc['json_pointer'])
        elif 'pdf_page' in loc or set(loc)=={'page'}:
            page=loc.get('pdf_page',loc.get('page'))
            contracts.validate_evidence({'artifact':path,'sha256':pin,'subject_id':asset,
                'source_kind':'analyst_inference','locator':{'page':page}},subject_id=asset)
            matches=[i for i,x in enumerate(captures['pdf_pages']) if
                x.get('path')==path and x.get('sha256')==pin and x.get('pdf_page')==page
                and x.get('exit_code')==0 and isinstance(x.get('text'),str) and x['text'].strip()]
            if len(matches)!=1:
                b.refuse('EVIDENCE_INCOMPLETE','Exactly one existing captured page required; no re-extraction.')
            result['capture']=citation(captures_ref,f'/pdf_pages/{matches[0]}')
        elif 'sheet' in loc and ('label_cell' in loc or 'cell' in loc):
            cell=loc.get('label_cell',loc.get('cell'))
            if not isinstance(cell,str) or not re.fullmatch(r'[A-Z]{1,3}[1-9][0-9]*',cell):
                b.refuse('INVALID_LOCATOR','Exact sheet/cell locator required.')
            row=int(re.search(r'[0-9]+$',cell).group())
            contracts.validate_evidence({'artifact':path,'sha256':pin,'subject_id':asset,
                'source_kind':'analyst_inference','locator':{'sheet':loc['sheet'],'row':row}},subject_id=asset)
            cell_key=loc['sheet']+'!'+cell
            matches=[i for i,x in enumerate(captures['static_workbooks']) if x.get('path')==path
                and x.get('sha256')==pin and cell_key in x.get('cells',{})]
            if len(matches)!=1:
                b.refuse('EVIDENCE_INCOMPLETE','Existing captured sheet/cell absent; no workbook parsing.')
            saved=captures['static_workbooks'][matches[0]]['cells'][cell_key]
            if saved.get('value') is None:
                b.refuse('EVIDENCE_INCOMPLETE','Captured cell is null.')
            if 'label' in loc and saved['value']!=loc['label']:
                b.refuse('LOCATOR_CONFLICT','Captured cell label differs from register.')
            result['capture']=citation(captures_ref,f'/static_workbooks/{matches[0]}/cells/{escape(cell_key)}')
        else:
            return result
        result.update(state='VERIFIED',code='PINNED_LOCATOR_RESOLVED')
    except HarnessError as exc:
        result.update(state=_state(exc),code=exc.code)
    return result


def _binding(row):
    p=row.get('proposed_value')
    p=p if isinstance(p,dict) else {}
    years=[v for v in (row.get('tax_year'),p.get('tax_year'),p.get('source_tax_year')) if v is not None]
    if years and any(type(v)!=type(years[0]) or v!=years[0] for v in years):
        b.refuse('PERIOD_MISMATCH','Conflicting year bindings within a record.')
    period=row.get('period',p.get('period'))
    if row['field']=='analysis_window':
        period={k:p.get(k) for k in ('start','end')}
        if any(v is not None and (not isinstance(v,str) or not re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}',v)) for v in period.values()):
            b.refuse('INVALID_CONTRACT','Analysis endpoints must be literal dates or null.')
    parcels=row.get('parcel_ids',p.get('parcel_ids'))
    if parcels is not None and (not isinstance(parcels,list) or not all(isinstance(v,str) and v for v in parcels)
                                or len(set(parcels))!=len(parcels)):
        b.refuse('INVALID_CONTRACT','Parcel IDs must be unique literal strings or null.')
    return {'subject':row['slug'],'asset_id':row['asset_id'],
            'parcel_ids':parcels,
            'tax_year':years[0] if years else None,'period':period,
            'units':row.get('units')}


def _spoof(value):
    if isinstance(value,dict):
        for k,v in value.items():
            if k in ('approved','scope_approved','certified','eligible','live_authorized','reviewed_pass') and v is not False and v is not None:
                return True
            if k in ('status','state') and isinstance(v,str) and v in ('approved','APPROVED','certified','CERTIFIED','PASS'):
                return True
            if k in ('approval','human_approval') and v is not None:
                return True
            if _spoof(v):
                return True
    elif isinstance(value,list):
        return any(_spoof(v) for v in value)
    return False


def _version(spec, subject, asset):
    exact(spec,('register','captures','policy'),'version')
    register=bridge.decode(read_ref(spec['register']))
    captures=bridge.decode(read_ref(spec['captures']))
    if register.get('schema_version')!=REGISTER:
        b.refuse('INVALID_CONTRACT','Unsupported decision register version.')
    for key in ('pdf_pages','static_workbooks'):
        if not isinstance(captures.get(key),list) or not all(isinstance(x,dict) for x in captures[key]):
            b.refuse('INVALID_CONTRACT','Existing capture lists required.')
    if any(not isinstance(x.get('cells'),dict) or not all(isinstance(v,dict) for v in x['cells'].values()) for x in captures['static_workbooks']):
        b.refuse('INVALID_CONTRACT','Captured workbook cells must be structured objects.')
    assets=register.get('assets')
    if not isinstance(assets,list) or not all(isinstance(x,dict) for x in assets):
        b.refuse('INVALID_CONTRACT','Explicit asset scope required.')
    matched=[(i,x) for i,x in enumerate(assets) if x.get('id')==asset and x.get('slug')==subject]
    subjects=register.get('selected_slugs')
    if not isinstance(subjects,list) or not all(isinstance(x,str) for x in subjects):
        b.refuse('INVALID_CONTRACT','Explicit selected subject list required.')
    if len(matched)!=1 or subject not in subjects:
        b.refuse('SUBJECT_MISMATCH','Exact existing subject/asset scope required.')
    ai,scope=matched[0]
    rows=register.get('decisions')
    if not isinstance(rows,list) or not 1<=len(rows)<=512:
        b.refuse('INVALID_CONTRACT','Bounded decision list required.')
    ids=set();selected={};all_rows={};cache={}
    for i,row in enumerate(rows):
        if not isinstance(row,dict) or not isinstance(row.get('id'),str) or not b.ID.fullmatch(row['id']) or row['id'] in ids:
            b.refuse('INVALID_CONTRACT','Unique literal decision IDs required.')
        ids.add(row['id']);all_rows[row['id']]=row
        if row.get('slug')!=subject or row.get('asset_id')!=asset:
            continue
        if not isinstance(row.get('field'),str) or not isinstance(row.get('evidence'),list) or len(row['evidence'])>64:
            b.refuse('INVALID_CONTRACT','Field and bounded evidence list required.')
        if not isinstance(row.get('remaining_gate'),list) or not all(isinstance(x,str) for x in row['remaining_gate']):
            b.refuse('INVALID_CONTRACT','Explicit remaining-gate text required.')
        binding=_binding(row)
        evidence=[_evidence(e,captures,spec['captures'],asset,cache,binding) for e in row['evidence']]
        state='MISSING' if row.get('status')=='blocked_by_evidence' or not evidence else 'UNREVIEWED'
        code='FACTS_INCOMPLETE' if state=='MISSING' else 'ANALYTICAL_REVIEW_REQUIRED'
        problems=[e['state'] for e in evidence if e['state'] not in ('VERIFIED','UNREVIEWED')]
        if problems:
            state=next(s for s in ('CONFLICTING','STALE','MISSING') if s in problems)
            code='SOURCE_INTEGRITY_FAILED'
        if row.get('conflicts') or row.get('conflicting_signals'):
            state,code='CONFLICTING','SOURCE_CONFLICT_UNRESOLVED'
        if _spoof(row) or _spoof(scope) or register.get('human_approval') is not None:
            state,code='CONFLICTING','SNAPSHOT_CLAIM_NOT_EXECUTABLE_AUTHORITY'
        selected[row['id']]={'state':state,'code':code,'binding':binding,
            'record':row,'evidence':evidence,'citation':citation(spec['register'],f'/decisions/{i}'),
            'factual_completion':'NOT_ESTABLISHED','execution_authorized':False}
    policy=policy_state(None,asset,{})
    if spec['policy'] is not None:
        value=bridge.decode(read_ref(spec['policy']))
        try: registry=bridge.host_registry()
        except HarnessError as exc:
            if exc.code!='APPROVAL_REQUIRED': raise
            registry={}
        policy=policy_state(value,asset,registry)
        policy['evidence']=citation(spec['policy'])
    return {'records':selected,'all_rows':all_rows,'scope':scope,
            'scope_evidence':citation(spec['register'],f'/assets/{ai}'),'policy':policy}


def _diff(before, after, path=''):
    # Missing is distinct from explicit null. Lists are indivisible to avoid
    # pretending index shifts are independently matched records.
    if type(before)==type(after) and before==after:
        return []
    if isinstance(before,dict) and isinstance(after,dict):
        out=[]
        for k in sorted(set(before)|set(after)):
            p=path+'/'+escape(k)
            if k not in before or k not in after:
                out.append({'pointer':p,'before_present':k in before,'after_present':k in after,
                            'before':before.get(k),'after':after.get(k)})
            else: out.extend(_diff(before[k],after[k],p))
        return out
    return [{'pointer':path,'before_present':True,'after_present':True,'before':before,'after':after}]


def compare(request_path, request_sha256):
    """No writes, engine calls, parsing of raw documents, or approval creation."""
    _sha(request_sha256)
    request=bridge.decode(_bound(request_path,request_sha256))
    exact(request,('schema','label','subject','asset_id','before','after'),'comparison request')
    if request['schema']!=VERSION or request['label'] not in ('REAL','SYNTHETIC'):
        b.refuse('INVALID_CONTRACT','Explicit real/synthetic comparison label required.')
    for field in ('subject','asset_id'):
        if not isinstance(request[field],str) or not b.ID.fullmatch(request[field]):
            b.refuse('INVALID_ID','Literal subject and asset IDs required.')
    before=_version(request['before'],request['subject'],request['asset_id'])
    after=_version(request['after'],request['subject'],request['asset_id'])
    if before['scope']!=after['scope']:
        b.refuse('SCOPE_MISMATCH','Different conveyed/analytical interests cannot be compared as the same scope.')
    records=[]; changes=[]; handoff=[]
    if not before['records'] and not after['records']:
        b.refuse('EVIDENCE_INCOMPLETE','No selected decision records exist in either version.')
    for rid in sorted(set(before['records'])|set(after['records'])):
        left=before['records'].get(rid); right=after['records'].get(rid)
        for version in (before,after):
            other=version['all_rows'].get(rid)
            if other and (other.get('slug')!=request['subject'] or other.get('asset_id')!=request['asset_id']):
                b.refuse('SUBJECT_MISMATCH','Stable decision ID moved across subjects/assets.')
        if left and right:
            if left['record']['field']!=right['record']['field']:
                b.refuse('SCOPE_MISMATCH','Stable decision ID changed field meaning.')
            for key in BINDING_KEYS:
                l,r=left['binding'][key],right['binding'][key]
                if l is not None and r is not None and (type(l)!=type(r) or l!=r):
                    b.refuse('UNIT_MISMATCH' if key=='units' else 'PERIOD_MISMATCH' if key in ('tax_year','period') else 'SCOPE_MISMATCH',
                             'Evidence applicability differs; compare only the same bound scope.',field=key)
            delta=_diff(left['record'],right['record'])
            change='CHANGED' if delta or left['evidence']!=right['evidence'] else 'UNCHANGED'
        else:
            change='ADDED' if right else 'REMOVED'
            delta=[{'pointer':'','before_present':left is not None,'after_present':right is not None,
                    'before':left['record'] if left else None,'after':right['record'] if right else None}]
        for d in delta:
            changes.append({'id':rid,**d,
                'before_evidence':{**left['citation'],'json_pointer':left['citation']['json_pointer']+d['pointer']} if left and d['before_present'] else None,
                'after_evidence':{**right['citation'],'json_pointer':right['citation']['json_pointer']+d['pointer']} if right and d['after_present'] else None})
        missing={'state':'MISSING','code':'RECORD_ABSENT','record':None,'evidence':[],
                 'factual_completion':'NOT_ESTABLISHED','execution_authorized':False}
        records.append({'id':rid,'change':change,'before':left or missing,'after':right or missing})
        current=right or left
        handoff.append({'id':rid,'field':current['record']['field'],'change':change,
            'state':right['state'] if right else 'MISSING','binding':current['binding'],
            'requests':current['record']['remaining_gate'],
            'action':'Restore or explicitly account for removed evidence; absence is not resolution.' if right is None else
                     'Resolve integrity/conflict before review.' if right['state'] in ('STALE','CONFLICTING') else
                     'Supply the missing source schedule; then seek separate analytical approval.' if right['state']=='MISSING' else
                     'Review exact changed evidence and analytical choices; no approval is inferred.',
            'evidence':current['citation'],'retry_authorized':False,'outreach_performed':False})
    changed=any(x['change']!='UNCHANGED' for x in records) or before['policy']!=after['policy']
    return {'schema':'gate-evidence-comparison/1.0.0','status':'REFUSED','label':request['label'],
        'subject':request['subject'],'asset_id':request['asset_id'],'scope':before['scope'],
        'request':{'path':str(request_path),'sha256':request_sha256},'versions':{'before':request['before'],'after':request['after']},
        'comparison':'CHANGED' if changed else 'UNCHANGED','changes':changes,'records':records,'handoff':handoff,
        'policy_contract':{'before':before['policy'],'after':after['policy']},
        'engine_executed':False,'execution_authorized':False,'certified':False,'financial_metrics':None,
        'limitations':['Selected decision records and captured locators only; not a full raw-source economic audit.',
            'Historical bytes and captured page/cell existence do not prove current-market validity, legal applicability or factual completion.',
            'Policy APPROVED means only the existing host-pinned contract validated; never source truth or live authority.',
            'Unsupported locators remain UNREVIEWED. No source acquisition, re-extraction, intake repair or outreach.']}


def render_markdown(result):
    def text(value):
        return str(value).replace('&','&amp;').replace('<','&lt;').replace('>','&gt;').replace('|','\\|').replace('`','\\`').replace('\n',' ')
    def cite(e):
        return f"{text(e['path'])} · SHA256 {text(e['sha256'])} · JSON pointer {text(json.dumps(e['json_pointer']))}"
    lines=[f"# Gate evidence: {text(result['subject'])}",'',
        f"**{result['label']} / {result['comparison']} / REFUSED — draft only**",'',
        'No engine execution, live authority or certification. Financial metrics: null.',
        f"Scope: {text(result['scope'].get('scope'))}",'','## Exact changes']
    for d in result['changes']:
        lines.append(f"- {text(d['id'])} {text(d['pointer'])}: {text(json.dumps(d['before']))} → {text(json.dumps(d['after']))}")
        for side in ('before_evidence','after_evidence'):
            if d[side]: lines.append('  '+side+': '+cite(d[side]))
    if not result['changes']: lines.append('- No record content changes. See evidence states and policy-contract comparison separately.')
    lines += ['','## Draft handoff (not sent; no retry permission)']
    for h in result['handoff']:
        lines += [f"- **{text(h['id'])} / {h['state']} / {h['change']}**: {text(h['action'])}",
                  '  Applicability: '+text(json.dumps(h['binding'],sort_keys=True)),
                  '  '+cite(h['evidence'])]
        lines += ['  - '+text(x) for x in h['requests']]
    lines += ['','## Captured evidence locators']
    for r in result['records']:
        for e in r['after']['evidence']:
            s=e['source'];lines.append(f"- {text(r['id'])}: {e['state']} / {e['code']} · {text(s.get('artifact'))} · SHA256 {text(s.get('sha256'))} · {text(json.dumps(s.get('locator')))}")
            if e['capture']: lines.append('  '+cite(e['capture']))
    lines += ['','## Authority',text(json.dumps(result['policy_contract'],sort_keys=True)),'','## Limits']
    lines += ['- '+x for x in result['limitations']]
    return '\n'.join(lines)+'\n'
