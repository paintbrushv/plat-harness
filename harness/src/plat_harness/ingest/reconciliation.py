"""Separate host-reviewed resolution overlay. Never mutates or upgrades v2.

Host dependencies are Python-only capabilities. See INGEST_RECONCILIATION.md.
No storage, engine eligibility, certification or execution permission is granted.
"""
from copy import deepcopy

from plat_harness import contracts as authority
from . import contracts as c
from .source_resolver import ByteSourceResolver, RULES, SEMANTICS, TOKENS, digest

CONTRACT_VERSION = 'ingest-resolution/1.0.0'
DECISION_VERSION = 'ingest-mapping/1.0.0'
MAX_DECISIONS = 256
MAX_CHANGES = 256
MAX_OVERLAY_BYTES = 8 * 1024 * 1024


class ReconciliationError(ValueError):
    def __init__(self):
        self.code = 'RECONCILIATION_REFUSED'
        super().__init__('Reconciliation refused (RECONCILIATION_REFUSED).')


def _decision(value, env, context, units, registry):
    c._object(value, ('contract_version', 'decision_id', 'envelope_sha256', 'subject_id', 'as_of',
        'adapter', 'sources', 'supplemental_sources', 'predecessor_sha256', 'supersedes',
        'semantics', 'dimension', 'changes', 'approval'))
    c._require(value['contract_version'] == DECISION_VERSION)
    c._id(value['decision_id'], 'dec')
    c._require(value['envelope_sha256'] == context['expected_envelope_sha256'])
    for key in ('subject_id', 'as_of', 'adapter', 'sources'):
        c._require(value[key] == env[key])
    c._require(value['semantics'] == SEMANTICS and value['dimension'] == 'physical_unit')
    available = {s['source_id']: s for s in context['sources']}
    base = {s['source_id']: s for s in env['sources']}
    supplements = value['supplemental_sources']
    c._require(type(supplements) is list and len(supplements) <= 32)
    allowed = dict(base)
    for source in supplements:
        c._require(type(source) is dict and source == available.get(source.get('source_id')))
        c._require(source['role'] == 'original' and source['source_id'] not in allowed)
        allowed[source['source_id']] = source
    changes = value['changes']
    c._require(type(changes) is list and 0 < len(changes) <= MAX_CHANGES)
    membership = set()
    for change in changes:
        c._object(change, ('observation_id', 'field', 'before', 'after', 'rule_id', 'citation', 'value_sha256'))
        oid, field = change['observation_id'], change['field']
        c._require(type(oid) is str and oid in units and type(field) is str and field in TOKENS)
        c._require((oid, field) not in membership)
        membership.add((oid, field))
        c._require(change['before'] == units[oid][field])
        c._require(type(change['after']) is str and change['after'] in set(TOKENS[field].values()))
        c._require(change['rule_id'] == RULES[field])
        c._pattern(change['value_sha256'], r'[0-9a-f]{64}')
        c._citation(change['citation'], allowed)
    # The one existing authority binds ALL payload fields and full provenance.
    authority._approval(value['approval'], {k: v for k, v in value.items() if k != 'approval'}, registry)
    return membership


def _reconcile(observations, decisions, host_registry, source_resolver):
    c._require(callable(host_registry) and type(source_resolver) is ByteSourceResolver)
    context = source_resolver.context()
    c._tree(observations)
    c._tree(decisions)
    c._require(type(decisions) is list and len(decisions) <= MAX_DECISIONS)
    env = c.validate_observations(observations, subject_id=context['subject_id'], as_of=context['as_of'])
    c._require(digest(env) == context['expected_envelope_sha256'] and env['adapter'] == context['adapter'])
    available = {s['source_id']: s for s in context['sources']}
    c._require(all(s == available.get(s['source_id']) for s in env['sources']))
    registry = host_registry()  # Reload for EVERY invocation, including readback.
    c._tree(registry)
    c._require(type(registry) is dict)
    units = {u['observation_id']: u for u in env['units'] + env['unknown_use_units']}
    resolutions = {oid: {'observation_id': oid, 'unit_type': None, 'status': None,
                          'decision_sha256s': [], 'proofs': []} for oid in units}
    requests = []
    relied_on = {oid: [] for oid in units}

    def request(code, oid=None, field=None, ref=None):
        requests.append({'code': code, 'observation_id': oid, 'field': field, 'reference_sha256': ref})

    for oid, unit in units.items():
        target = unit['evidence'][0]['unit_id']
        for field in TOKENS:
            if unit[field] is None:
                continue
            cites = [group[field] for group in unit['evidence'] if field in group]
            relied_on[oid].extend(cites)
            results = [source_resolver.verify({'target': target, 'field': field, 'value': unit[field],
                'citation': cite, 'rule_id': RULES[field], 'value_sha256': None}) for cite in cites]
            if results and all(result['state'] == 'verified' for result in results):
                resolutions[oid][field] = unit[field]
                resolutions[oid]['proofs'].extend(results)
            else:
                request('BASE_FACT_UNVERIFIED', oid, field)
    history, by_id, by_hash, memberships = [], {}, {}, {}
    head = None
    for value in decisions:
        membership = _decision(value, env, context, units, registry)
        ref, did = digest(value), value['decision_id']
        if did in by_id:
            c._require(by_id[did] == ref)
            continue  # Identical replay does not move the supplied chain head.
        c._require(value['predecessor_sha256'] == head)
        supersedes = value['supersedes']
        c._require(type(supersedes) is list and len(supersedes) <= MAX_DECISIONS)
        c._require(len(set(supersedes)) == len(supersedes))
        for prior in supersedes:
            c._pattern(prior, r'[0-9a-f]{64}')
            c._require(prior in by_hash and by_hash[prior]['state'] == 'active')
            c._require(memberships[prior] == membership)
            by_hash[prior]['state'] = 'superseded'
        entry = {'decision_id': did, 'decision_sha256': ref,
            'approval_sha256': digest(value['approval']),
            'payload': {k: deepcopy(v) for k, v in value.items() if k != 'approval'}, 'state': 'active'}
        history.append(entry)
        by_id[did], by_hash[ref], memberships[ref], head = ref, entry, membership, ref
    active = {}
    for entry in history:
        if entry['state'] == 'active':
            for change in entry['payload']['changes']:
                active.setdefault((change['observation_id'], change['field']), []).append(
                    (entry['decision_sha256'], change))
    for (oid, field), proposals in active.items():
        relied_on[oid].extend(change['citation'] for _, change in proposals)
        if len({change['after'] for _, change in proposals}) != 1:
            resolutions[oid][field] = None
            request('DECISION_CONFLICT', oid, field)
            continue
        verified = []
        for ref, change in proposals:
            result = source_resolver.verify({'target': units[oid]['evidence'][0]['unit_id'],
                'field': field, 'value': change['after'], 'citation': change['citation'],
                'rule_id': change['rule_id'], 'value_sha256': change['value_sha256']})
            if result['state'] == 'verified':
                verified.append(result)
                resolutions[oid]['decision_sha256s'].append(ref)
            else:
                request(result['code'], oid, field, ref)
        if len(verified) == len(proposals):
            resolutions[oid][field] = proposals[0][1]['after']
            resolutions[oid]['proofs'].extend(verified)
        else:
            resolutions[oid][field] = None
    for oid, resolution in resolutions.items():
        # Individual field verification cannot detect conflicts between two
        # supplements when the original source has no recognized value.
        result = source_resolver.consistency(units[oid]['evidence'][0]['unit_id'], relied_on[oid])
        if result['state'] == 'verified':
            resolution['proofs'].append(result)
        else:
            request(result['code'], oid)
        for field in TOKENS:
            if resolution[field] is None:
                request('FIELD_EVIDENCE_REQUIRED', oid, field)
    if sum(s['role'] == 'original' for s in env['sources']) != 1:
        coverage = {'state': 'evidence_required', 'code': 'MULTISOURCE_COVERAGE_UNSUPPORTED'}
    else:
        coverage = source_resolver.coverage([u['evidence'][0]['unit_id'] for u in units.values()])
    if coverage['state'] != 'verified':
        request(coverage['code'])
    # A root block without an explicit blocker issue has no disposition path.
    # Field proofs (or warnings) cannot invent an explanation for that block.
    if env['status'] == 'blocked' and not any(i['severity'] == 'blocker' for i in env['issues']):
        request('UNEXPLAINED_ROOT_BLOCKER')
    dispositions = []
    for issue in env['issues']:
        resolved = (issue['code'] == 'UNRESOLVED_UNIT_USE' and bool(issue['observation_ids'])
                    and all(resolutions[oid]['unit_type'] is not None for oid in issue['observation_ids']))
        dispositions.append({'issue_id': issue['issue_id'], 'state': 'resolved' if resolved else 'retained'})
        if issue['severity'] == 'blocker' and not resolved:
            request('ORIGINAL_BLOCKER', ref=digest(issue))
    for summary in env['summaries']:
        if summary['status'] != 'absent':
            request('SUMMARY_VERIFICATION_REQUIRED', ref=digest(summary))
    counts = {scope: dict.fromkeys(c.COUNT_FIELDS) for scope in c.SCOPES}
    if not requests:
        for scope in c.SCOPES:
            selected = [r for r in resolutions.values() if r['unit_type'] == scope]
            counts[scope] = {field: sum(r['status'] == field for r in selected) for field in c.COUNT_FIELDS[:3]}
            counts[scope]['total'] = len(selected)
    return {'contract_version': CONTRACT_VERSION, 'envelope_sha256': digest(env), 'observations': env,
        'context_sha256': digest(context), 'semantics': deepcopy(SEMANTICS),
        'state': 'review_required' if requests else 'reconciled', 'coverage': coverage,
        'history': history, 'head_sha256': history[-1]['decision_sha256'] if history else None,
        'resolutions': list(resolutions.values()), 'evidence_requests': requests, 'dispositions': dispositions,
        'summary_dispositions': [{'summary_id': summary['summary_id'], 'state': 'retained'}
                                 for summary in env['summaries']], 'counts': counts}


def reconcile_observations(observations, decisions, *, host_registry, source_resolver):
    """Validate current host approvals and exact original bytes; return overlay.

    Production host_registry must be review_bridge.host_registry, or an equivalent
    independently pinned CURRENT loader. A dictionary/cached proof is not accepted.
    The resolver must be a host-constructed ByteSourceResolver, never request JSON.
    """
    try:
        result = _reconcile(observations, decisions, host_registry, source_resolver)
        c._tree(result)
        c._require(len(c._encode(result)) <= MAX_OVERLAY_BYTES)
        return result
    except Exception:
        pass
    # Outside handlers: no inherited source/authority exceptions in __context__.
    raise ReconciliationError() from None


def reconcile_request(request, *, source_resolver):
    """Production boundary: exact {observations, decisions}, native or UTF-8 JSON.

    The embedding host constructs source_resolver independently. No resolver or
    registry configuration is deserialized from request. Artifact lookup belongs
    to Task 1.4; this entrypoint deliberately accepts no paths/references yet.
    """
    try:
        value = c._decode(request) if type(request) in (bytes, str) else request
        c._tree(value)
        c._object(value, ('observations', 'decisions'))
        from plat_harness.adapters.review_bridge import host_registry
        return reconcile_observations(value['observations'], value['decisions'],
            host_registry=host_registry, source_resolver=source_resolver)
    except Exception:
        pass
    raise ReconciliationError() from None
