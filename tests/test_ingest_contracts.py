"""Public synthetic tests for the v2 observation boundary; no source IO."""
import copy
import hashlib
import importlib.util
import json
import traceback

import pytest

SUBJECT = 'subject_demo'
AS_OF = '2026-01-31'
SOURCE = 'src_' + '1' * 32
SHA = hashlib.sha256(b'public synthetic source').hexdigest()
FIELDS = ('occupied', 'vacant', 'down', 'total')


def api():
    from plat_harness.ingest import contracts as c
    assert callable(getattr(c, 'validate_observations', None))
    assert callable(getattr(c, 'loads_observations', None))
    assert callable(getattr(c, 'canonical_bytes', None))
    return c


def envelope():
    return {
        'contract_version': 'ingest-observation/2.0.0',
        'subject_id': SUBJECT, 'as_of': AS_OF,
        'adapter': {'id': 'synthetic-flat', 'version': '1.0.0'},
        'sources': [{'source_id': SOURCE, 'sha256': SHA, 'role': 'original',
                     'original_source_ids': [], 'subject_id': SUBJECT, 'as_of': AS_OF}],
        'units': [], 'unknown_use_units': [], 'summaries': [], 'issues': [],
        'completeness': {scope: {'enumeration': 'unknown', 'coverage': 'unknown',
                                'coverage_citations': [], 'counts': dict.fromkeys(FIELDS)}
                         for scope in ('residential', 'commercial')},
        'status': 'blocked',
    }


def validate(value, **kwargs):
    return api().validate_observations(value, subject_id=kwargs.get('subject_id', SUBJECT),
                                       as_of=kwargs.get('as_of', AS_OF))


def refuse(value, **kwargs):
    c = api()
    with pytest.raises(c.ObservationContractError) as caught:
        validate(value, **kwargs)
    assert caught.value.code in {'INVALID_OBSERVATIONS', 'INPUT_LIMIT_EXCEEDED', 'SCOPE_MISMATCH'}
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))


def test_versioned_contract_seam_exists():
    assert importlib.util.find_spec('plat_harness.ingest.contracts') is not None


def test_empty_unknown_envelope_is_defensively_copied():
    original = envelope()
    result = validate(original)
    assert result == original and result is not original
    result['sources'][0]['sha256'] = '0' * 64
    assert original['sources'][0]['sha256'] == SHA
    assert all(v is None for v in result['completeness']['commercial']['counts'].values())


def test_exact_scope_nullable_only_when_host_also_unknown():
    value = envelope()
    value['subject_id'] = value['sources'][0]['subject_id'] = None
    value['as_of'] = value['sources'][0]['as_of'] = None
    assert validate(value, subject_id=None, as_of=None) == value
    refuse(value)
    refuse(envelope(), as_of='2026-02-01')
    refuse(envelope(), subject_id='other_subject')


@pytest.mark.parametrize('bad', [True, False, -1, 1.0, 0.5, float('inf'), float('nan'), '1', 1000001])
def test_counts_never_coerce(bad):
    value = envelope()
    value['completeness']['residential']['counts']['total'] = bad
    refuse(value)


@pytest.mark.parametrize('path', [(), ('adapter',), ('sources', 0), ('completeness',),
                                  ('completeness', 'residential'),
                                  ('completeness', 'residential', 'counts')])
def test_every_object_rejects_extra_keys(path):
    value = envelope()
    target = value
    for key in path:
        target = target[key]
    target['PRIVATE_CANARY'] = 'PRIVATE_CANARY'
    refuse(value)


@pytest.mark.parametrize('key,bad', [('contract_version', '1.0'), ('status', 'approved'),
                                   ('subject_id', '../PRIVATE_CANARY'), ('as_of', '2026-02-30')])
def test_version_authority_and_scope_formats(key, bad):
    value = envelope()
    value[key] = bad
    refuse(value)


@pytest.mark.parametrize('bad', [None, [], (), {'arbitrary': 'PRIVATE_CANARY'}])
def test_non_envelope_rejected(bad):
    refuse(bad)


def test_sources_required_unique_bound_and_not_paths():
    for sources in ([], [envelope()['sources'][0]] * 2):
        value = envelope()
        value['sources'] = sources
        refuse(value)
    for key, bad in [('sha256', 'x' * 64), ('source_id', '/PRIVATE_CANARY'),
                     ('role', 'approved'), ('subject_id', 'other'), ('as_of', None),
                     ('original_source_ids', [SOURCE])]:
        value = envelope()
        value['sources'][0][key] = bad
        refuse(value)


def test_canonical_json_and_round_trip():
    c = api()
    value = envelope()
    expected = json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()
    assert c.canonical_bytes(value, subject_id=SUBJECT, as_of=AS_OF) == expected
    assert c.loads_observations(expected, subject_id=SUBJECT, as_of=AS_OF) == value
    assert c.loads_observations(expected.decode(), subject_id=SUBJECT, as_of=AS_OF) == value
    value['approved'] = True
    with pytest.raises(c.ObservationContractError):
        c.canonical_bytes(value, subject_id=SUBJECT, as_of=AS_OF)


@pytest.mark.parametrize('raw', [b'\xffPRIVATE_CANARY', '{PRIVATE_CANARY',
    '{"subject_id":null,"subject_id":null}',
    '{"adapter":{"id":"a","id":"b"}}', '[NaN]', '[Infinity]', '[1e999]',
    '[' * 40 + '0' + ']' * 40, b'{}' * 5000000, 123, '\ud800'],
    ids=['utf8', 'syntax', 'duplicate-root', 'duplicate-nested', 'nan', 'inf',
         'overflow', 'depth', 'size', 'type', 'surrogate'])
def test_loader_rejects_malformed_duplicate_nonfinite_and_limits_without_chain(raw):
    c = api()
    with pytest.raises(c.ObservationContractError) as caught:
        c.loads_observations(raw, subject_id=SUBJECT, as_of=AS_OF)
    assert caught.value.__cause__ is caught.value.__context__ is None
    assert 'PRIVATE_CANARY' not in ''.join(traceback.format_exception(caught.value))


def test_object_input_bounded_and_not_arbitrary_python_objects():
    value = envelope()
    value['issues'] = [None] * 50001
    refuse(value)
    value = envelope()
    value['issues'].append(value)
    refuse(value)
    value = envelope()
    value['issues'] = object()
    refuse(value)


def citation(row=2, column=1):
    return {'source_id': SOURCE, 'source_sha256': SHA,
            'sheet': 1, 'row': row, 'row_end': row, 'column': column}


def unit(row=2, use='residential', status='occupied'):
    anchor = citation(row)
    identity = 'unit_' + hashlib.sha256(json.dumps(anchor, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False).encode()).hexdigest()
    return {'observation_id': identity, 'source_id': SOURCE, 'unit_type': use,
            'status': status, 'evidence': [{'unit_id': anchor, 'status': citation(row, 2),
                                         'unit_type': citation(row, 3)}]}


def observed():
    value = envelope()
    value['units'] = [unit()]
    return value


def test_positional_units_and_every_v1_field_citation_survive():
    value = observed()
    value['units'][0]['evidence'].append({k: citation(3, n) for n, k in enumerate(
        ('tenant_name', 'phone', 'email', 'record_type', 'designation', 'floorplan', 'charge'), 1)})
    assert validate(value) == value
    c = api()
    assert callable(getattr(c, 'observation_id', None))
    assert c.observation_id(citation()) == value['units'][0]['observation_id']
    assert c.observation_id(citation(3)) != c.observation_id(citation())
    assert 'tenant_name' in validate(value)['units'][0]['evidence'][1]


def test_unknown_use_and_status_preserved_not_scoped_or_counted():
    value = envelope()
    value['unknown_use_units'] = [unit(use=None, status=None)]
    assert validate(value) == value
    value['units'] = value['unknown_use_units']
    refuse(value)
    value = observed()
    value['unknown_use_units'] = value.pop('units')
    value['units'] = []
    refuse(value)


@pytest.mark.parametrize('change', ['unit-id', 'duplicate', 'no-evidence', 'missing-status',
    'source', 'free-text', 'evidence-extra', 'raw-identity', 'status', 'use'])
def test_unit_shape_and_identity_refusals(change):
    value = observed()
    item = value['units'][0]
    if change == 'unit-id': item['observation_id'] = 'unit_' + '0' * 64
    elif change == 'duplicate': value['units'].append(copy.deepcopy(item))
    elif change == 'no-evidence': item['evidence'] = []
    elif change == 'missing-status': del item['evidence'][0]['status']
    elif change == 'source': item['source_id'] = 'src_' + '0' * 32
    elif change == 'free-text': item['tenant_name'] = 'PRIVATE_CANARY'
    elif change == 'evidence-extra': item['evidence'][0]['notes'] = citation()
    elif change == 'raw-identity': item['observation_id'] = '101'
    elif change == 'status': item['status'] = 'admin_down'
    elif change == 'use': item['unit_type'] = 'apartment'
    refuse(value)


@pytest.mark.parametrize('key,bad', [('sheet', True), ('row', 0), ('row', 1.5),
    ('row_end', 1), ('column', -1), ('column', 16385), ('sheet', 'PRIVATE_CANARY'),
    ('source_sha256', '0' * 64), ('source_id', 'src_' + '0' * 32), ('approved', True)])
def test_citation_types_ranges_binding_and_exact_keys(key, bad):
    value = observed()
    value['units'][0]['evidence'][0]['status'][key] = bad
    refuse(value)


def test_original_page_bounds_and_derivative_lineage():
    value = observed()
    derivative = copy.deepcopy(value['sources'][0])
    derivative.update(source_id='src_' + '2' * 32, sha256='2' * 64,
                      role='derivative', original_source_ids=[SOURCE])
    value['sources'].append(derivative)
    value['units'][0]['source_id'] = derivative['source_id']
    value['units'][0]['evidence'][0]['status'] = {
        'source_id': SOURCE, 'source_sha256': SHA, 'page': 1,
        'bounds': [0, 0, 1000000, 1000000]}
    assert validate(value) == value
    original = copy.deepcopy(value)
    for bounds in ([0, 0, 0, 1], [-1, 0, 2, 3], [0, 0, 2], [False, 0, 2, 3],
                   [0, 0, 1.0, 2], [0, 0, 1000001, 2]):
        value = copy.deepcopy(original)
        value['units'][0]['evidence'][0]['status']['bounds'] = bounds
        refuse(value)
    value = copy.deepcopy(original)
    value['units'][0]['evidence'][0]['status'].update(
        source_id=derivative['source_id'], source_sha256=derivative['sha256'])
    refuse(value)
    value = copy.deepcopy(original)
    value['sources'][1]['original_source_ids'] = []
    refuse(value)


def issue(code='UNRESOLVED_UNIT_USE', **kwargs):
    return {'issue_id': 'iss_' + '1' * 32, 'code': code, 'severity': 'blocker',
            'citation': citation(), 'observation_ids': [], 'summary_ids': [], **kwargs}


def test_static_linked_issues_preserve_blockers_and_null_citations():
    value = observed()
    value['issues'] = [issue('UNSUPPORTED_STATUS', observation_ids=[value['units'][0]['observation_id']])]
    assert validate(value) == value
    value['issues'][0]['citation'] = None
    assert validate(value) == value
    value['issues'][0]['code'] = 'PRIVATE_CANARY'
    refuse(value)
    value['issues'][0]['code'] = 'UNSUPPORTED_STATUS'
    value['issues'][0]['observation_ids'] = ['unit_' + '0' * 64]
    refuse(value)


def test_issue_ids_unique_and_no_generic_payload_or_authority():
    value = envelope()
    value['issues'] = [issue(), issue()]
    refuse(value)
    value['issues'] = [issue(observation={'notes': 'PRIVATE_CANARY'})]
    refuse(value)
    value['issues'] = [issue()]
    value['status'] = 'observed_unvalidated'
    refuse(value)


def complete():
    value = observed()
    value['units'] += [unit(3, status='vacant'), unit(4, status='down')]
    value['completeness']['residential'].update(
        enumeration='complete', coverage='established', coverage_citations=[citation(8)],
        counts={'occupied': 1, 'vacant': 1, 'down': 1, 'total': 3})
    value['completeness']['commercial'].update(
        enumeration='complete', counts=dict.fromkeys(FIELDS, 0))
    value['status'] = 'observed_unvalidated'
    return value


def summary(scope='residential', **kwargs):
    return {'summary_id': 'sum_' + '1' * 32, 'source_id': SOURCE, 'scope': scope,
            'status': 'absent', 'reported_counts': None, 'row_derived_counts': None,
            'vendor_status_counts': {}, 'citations': [], 'matched_fields': [], **kwargs}


def test_complete_disjoint_counts_are_observations_not_approval_or_rates():
    value = complete()
    assert validate(value) == value
    for prohibited in ('approved', 'certification', 'rate', 'occupancy_rate'):
        bad = copy.deepcopy(value)
        bad['completeness']['residential'][prohibited] = True
        refuse(bad)
    assert value['completeness']['commercial']['coverage'] == 'unknown'
    assert value['completeness']['commercial']['counts']['total'] == 0


@pytest.mark.parametrize('change', ['total', 'occupied', 'coverage-citation', 'unknown-use',
    'unknown-status', 'partial-coverage', 'unknown-period', 'unknown-subject'])
def test_complete_counts_and_independent_coverage_cannot_be_forged(change):
    value = complete()
    scope = value['completeness']['residential']
    if change == 'total': scope['counts']['total'] = 4
    elif change == 'occupied': scope['counts'].update(occupied=2, vacant=0)
    elif change == 'coverage-citation': scope['coverage_citations'] = []
    elif change == 'unknown-use': value['unknown_use_units'] = [unit(6, use=None)]
    elif change == 'unknown-status': value['units'][0]['status'] = None
    elif change == 'partial-coverage': scope['enumeration'] = 'partial'
    elif change == 'unknown-period':
        value['as_of'] = value['sources'][0]['as_of'] = None
        refuse(value, as_of=None)
        return
    elif change == 'unknown-subject':
        value['subject_id'] = value['sources'][0]['subject_id'] = None
        refuse(value, subject_id=None)
        return
    refuse(value)


def test_enumeration_never_infers_coverage_and_partial_can_keep_unknowns():
    value = complete()
    for item in value['completeness'].values():
        item.update(coverage='unknown', coverage_citations=[])
    value['status'] = 'blocked'
    assert validate(value) == value
    value['completeness']['residential'].update(enumeration='partial',
        counts={'occupied': 1, 'vacant': 1, 'down': None, 'total': 3})
    assert validate(value) == value
    value['completeness']['residential']['enumeration'] = 'unknown'
    refuse(value)


def test_reconciled_scoped_summary_checks_counts_and_keeps_all_citation_groups():
    value = complete()
    counts = value['completeness']['residential']['counts']
    value['summaries'] = [summary(status='reconciled', reported_counts=copy.deepcopy(counts),
        row_derived_counts=copy.deepcopy(counts), matched_fields=list(FIELDS),
        citations=[{key: citation(9, n) for n, key in enumerate(FIELDS, 1)},
                   {key: citation(10, n) for n, key in enumerate(FIELDS, 1)}])]
    assert validate(value) == value
    value['summaries'][0]['reported_counts']['total'] = 4
    refuse(value)


def test_mismatch_summary_and_invalid_citations_retained_but_blocked():
    value = observed()
    value['summaries'] = [summary(status='mismatch', reported_counts={
        'occupied': 2, 'vacant': 0, 'down': 0, 'total': 9},
        citations=[{key: citation(9, n) for n, key in enumerate(FIELDS, 1)}])]
    assert validate(value) == value  # Contradictory source claims are not repaired.
    value['status'] = 'observed_unvalidated'
    refuse(value)
    value['status'] = 'blocked'
    value['summaries'][0].update(status='invalid', reported_counts=None)
    assert validate(value) == value


def onesite_observations():
    value = envelope()
    value['unknown_use_units'] = [unit(2, use=None), unit(3, use=None, status='vacant')]
    counts = {'occupied': 1, 'vacant': 1, 'down': None, 'total': 2}
    vendor = dict(occupied_no_ntv=1, occupied_ntv=0, occupied_ntv_leased=0,
                  vacant_leased=0, admin_down=0, vacant_not_leased=1, totals=2)
    value['summaries'] = [summary('unknown', status='unresolved', reported_counts=counts,
        row_derived_counts=copy.deepcopy(counts), vendor_status_counts=vendor,
        citations=[{key: citation(10 + n, 21) for n, key in enumerate(vendor)}],
        matched_fields=['occupied', 'vacant', 'total'])]
    value['issues'] = [issue('ONESITE_REPORT_SUMMARY', summary_ids=[value['summaries'][0]['summary_id']])]
    return value


def test_onesite_vendor_summary_preserves_every_field_without_down_promotion():
    value = onesite_observations()
    assert validate(value) == value
    assert validate(value)['summaries'][0]['reported_counts']['down'] is None
    assert validate(value)['summaries'][0]['vendor_status_counts']['admin_down'] == 0
    value['summaries'][0]['reported_counts']['down'] = 0
    refuse(value)


@pytest.mark.parametrize('change', ['extra', 'vendor-extra', 'citation-extra', 'boolean',
    'matched-unknown', 'matched-duplicate', 'matched-lie', 'uncited-count', 'uncited-vendor',
    'row-derived-lie', 'duplicate-id', 'scope', 'missing-field', 'wrong-source', 'absent-data'])
def test_deep_summary_validation(change):
    value = onesite_observations()
    item = value['summaries'][0]
    if change == 'extra': item['notes'] = 'PRIVATE_CANARY'
    elif change == 'vendor-extra': item['vendor_status_counts']['model'] = 0
    elif change == 'citation-extra': item['citations'][0]['resident'] = citation()
    elif change == 'boolean': item['vendor_status_counts']['admin_down'] = False
    elif change == 'matched-unknown': item['matched_fields'] = ['rate']
    elif change == 'matched-duplicate': item['matched_fields'] *= 2
    elif change == 'matched-lie': item['reported_counts']['occupied'] = 5
    elif change == 'uncited-count': item['reported_counts']['down'] = 0
    elif change == 'uncited-vendor': del item['citations'][0]['admin_down']
    elif change == 'row-derived-lie': item['row_derived_counts']['occupied'] = 4
    elif change == 'duplicate-id': value['summaries'].append(copy.deepcopy(item))
    elif change == 'scope': item['scope'] = 'model'
    elif change == 'missing-field': del item['row_derived_counts']
    elif change == 'wrong-source': item['source_id'] = 'src_' + '0' * 32
    elif change == 'absent-data': item['status'] = 'absent'
    refuse(value)


def test_absent_and_partial_invalid_vendor_summaries_remain_representable():
    value = onesite_observations()
    item = value['summaries'][0]
    item.update(status='absent', reported_counts=None, vendor_status_counts={},
                citations=[], matched_fields=[])
    assert validate(value) == value  # Row-derived counts survive an absent vendor summary.
    item.update(status='unresolved', vendor_status_counts={'admin_down': None},
                citations=[{'admin_down': citation(10, 21)}])
    assert validate(value) == value


def test_summary_and_issue_links_cannot_cross_source_lineage():
    value = onesite_observations()
    value['issues'][0]['summary_ids'] = ['sum_' + '0' * 32]
    refuse(value)


def test_duplicate_original_digest_does_not_create_more_inventory():
    value = observed()
    alias = copy.deepcopy(value['sources'][0])
    alias['source_id'] = 'src_' + '2' * 32
    value['sources'].append(alias)
    refuse(value)


@pytest.mark.parametrize('code', ['UNSUPPORTED_STATUS', 'UNRESOLVED_UNIT_USE',
                                 'SUMMARY_MISMATCH', 'DOWN_EVIDENCE_UNRESOLVED'])
def test_existing_parser_blockers_cannot_be_downgraded_to_warnings(code):
    value = observed()
    value['issues'] = [issue(code, severity='warning')]
    value['status'] = 'observed_unvalidated'
    refuse(value)


def test_issues_with_citations_must_bind_the_linked_observation_source():
    value = observed()
    source = copy.deepcopy(value['sources'][0])
    source.update(source_id='src_' + '2' * 32, sha256='2' * 64)
    value['sources'].append(source)
    other = citation()
    other.update(source_id=source['source_id'], source_sha256=source['sha256'])
    value['issues'] = [issue('UNSUPPORTED_STATUS', citation=other,
                            observation_ids=[value['units'][0]['observation_id']])]
    refuse(value)


def test_onesite_issue_payloads_require_typed_links_not_silent_loss():
    value = envelope()
    value['issues'] = [issue('ONESITE_REPORT_SUMMARY')]
    refuse(value)
    value['issues'] = [issue('UNRESOLVED_UNIT_USE')]
    refuse(value)


def test_empty_parse_cannot_claim_observed_status():
    value = envelope()
    value['status'] = 'observed_unvalidated'
    refuse(value)


def test_all_current_parser_issue_codes_and_evidence_fields_are_allowlisted():
    import ast
    import inspect
    from plat_harness.ingest import onesite, pms_normalizer
    codes = set()
    for module in (onesite, pms_normalizer):
        tree = ast.parse(inspect.getsource(module))
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == 'issue':
                codes.update(child.value for child in ast.walk(node.args[0])
                             if isinstance(child, ast.Constant) and isinstance(child.value, str))
    assert codes <= api().ISSUE_CODES
    assert {'unit_id', 'status', 'unit_type', 'tenant_name', 'phone', 'email',
            'record_type', 'designation', 'floorplan', 'charge'} == api().EVIDENCE_FIELDS


@pytest.mark.parametrize('make', [envelope, observed, complete, onesite_observations])
def test_schema_nested_type_mutations_are_sanitized(make):
    original = make()
    paths = []
    def visit(item, path=()):
        if isinstance(item, dict):
            for key, child in item.items():
                paths.append(path + (key,))
                visit(child, path + (key,))
        elif isinstance(item, list):
            for index, child in enumerate(item):
                paths.append(path + (index,))
                visit(child, path + (index,))
    visit(original)
    for path in paths:
        value = copy.deepcopy(original)
        parent = value
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = {'PRIVATE_CANARY': object()}
        refuse(value)


def test_valid_canonical_roundtrip_with_observations_and_nested_duplicate_key():
    value = onesite_observations()
    c = api()
    raw = c.canonical_bytes(value, subject_id=SUBJECT, as_of=AS_OF)
    assert c.loads_observations(raw, subject_id=SUBJECT, as_of=AS_OF) == value
    duplicate = raw.replace(b'"row":2,', b'"row":2,"row":2,', 1)
    assert duplicate != raw
    with pytest.raises(c.ObservationContractError):
        c.loads_observations(duplicate, subject_id=SUBJECT, as_of=AS_OF)


def test_documented_minimal_example_executes():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / 'docs' / 'INGEST_CONTRACT.md').read_text()
    section = text.split('## Minimal runnable envelope', 1)[1]
    example = section.split('```python\n', 1)[1].split('```', 1)[0]
    scope = {}
    exec(compile(example, '<synthetic-contract-example>', 'exec'), scope)
    assert scope['example']['status'] == 'blocked'


@pytest.mark.parametrize('make', [complete, onesite_observations])
def test_no_final_field_accepts_booleans(make):
    original = make()
    paths = []
    def visit(value, path=()):
        if isinstance(value, dict):
            for key, child in value.items():
                paths.append(path + (key,))
                visit(child, path + (key,))
        elif isinstance(value, list):
            for key, child in enumerate(value):
                paths.append(path + (key,))
                visit(child, path + (key,))
    visit(original)
    for path in paths:
        value = copy.deepcopy(original)
        parent = value
        for key in path[:-1]:
            parent = parent[key]
        parent[path[-1]] = True
        refuse(value)
