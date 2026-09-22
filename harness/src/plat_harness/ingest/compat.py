"""Original-byte-backed v1 migration. No disk lookup, scope inference or authority.

The host must establish original-source role before calling; bytes alone cannot
prove authenticity. See docs/INGEST_MIGRATION.md for the preservation policy.
"""
from copy import deepcopy
from datetime import date
import hashlib
import io
import json
import re

from .contracts import (CONTRACT_VERSION, MAX_BYTES, MAX_DEPTH, MAX_ITEMS, MAX_NODES,
                        ObservationContractError, observation_id, validate_observations)
from .pms_normalizer import normalize_rent_roll

__all__ = ['MigrationError', 'migrate_v1', 'verify_preservation']
SCOPES = ('residential', 'commercial')
ADAPTERS = {'pms-flat-yardi': 'yardi', 'pms-flat-realpage': 'realpage',
            'pms-flat-entrata': 'entrata', 'onesite-detailed-realpage': 'realpage'}
MAGIC = bytes.fromhex('d0cf11e0a1b11ae1')
ERROR_CODES = frozenset(('INVALID_V1', 'INPUT_LIMIT_EXCEEDED', 'ORIGINAL_LINEAGE_REQUIRED',
    'SOURCE_HASH_MISMATCH', 'UNSUPPORTED_ADAPTER', 'ADAPTER_SIGNATURE_MISMATCH',
    'PARSER_REFUSED', 'PARSER_OUTPUT_MISMATCH', 'UNREPRESENTABLE_V1', 'PRESERVATION_MISMATCH'))


class MigrationError(ValueError):
    """Static code only; no raw values, paths or input-bearing exception chain."""
    def __init__(self, code='INVALID_V1'):
        self.code = code if type(code) is str and code in ERROR_CODES else 'INVALID_V1'
        super().__init__('V1 migration refused (' + self.code + ').')


class _Failure(Exception):
    pass


def _require(condition, code='INVALID_V1'):
    if not condition:
        raise _Failure(code)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


def _native(value):
    """Bound native trees before equality/copy/serialization, without user hooks."""
    stack = [(value, 0)]
    nodes = text_bytes = 0
    while stack:
        item, depth = stack.pop()
        nodes += 1
        _require(nodes <= MAX_NODES and depth <= MAX_DEPTH, 'INPUT_LIMIT_EXCEEDED')
        kind = type(item)
        if kind is dict:
            _require(len(item) <= MAX_ITEMS, 'INPUT_LIMIT_EXCEEDED')
            for key, child in item.items():
                _require(type(key) is str)
                stack.extend(((key, depth + 1), (child, depth + 1)))
        elif kind is list:
            _require(len(item) <= MAX_ITEMS, 'INPUT_LIMIT_EXCEEDED')
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            _require(len(item) <= 128, 'INPUT_LIMIT_EXCEEDED')
            text_bytes += len(item.encode('utf-8'))
            _require(text_bytes <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
        elif kind is int:
            _require(0 <= item <= 1_000_000)
        else:
            _require(item is None)  # No v1 field accepts bool, float or subclasses.
    _require(len(_encode(value)) <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')


def _shape(value, keys):
    _require(type(value) is dict and set(value) == set(keys), 'UNREPRESENTABLE_V1')


def _reproduce(value, source_id, subject_id, as_of, adapter_id, original_bytes):
    _native(value)
    _require(type(source_id) is str and re.fullmatch(r'src_[0-9a-f]{32}', source_id) is not None)
    _require(subject_id is None or (type(subject_id) is str and
             re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}', subject_id) is not None))
    if as_of is not None:
        _require(type(as_of) is str and re.fullmatch(r'[0-9]{4}-[0-9]{2}-[0-9]{2}', as_of) is not None)
        date.fromisoformat(as_of)
    _require(type(adapter_id) is str and adapter_id in ADAPTERS, 'UNSUPPORTED_ADAPTER')
    _require(type(original_bytes) is bytes, 'ORIGINAL_LINEAGE_REQUIRED')
    _require(len(original_bytes) <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
    _require(type(value) is dict)
    digest = hashlib.sha256(original_bytes).hexdigest()
    _require(value.get('source_sha256') == digest, 'SOURCE_HASH_MISMATCH')
    detailed = adapter_id == 'onesite-detailed-realpage'
    _require(original_bytes.startswith(MAGIC) == detailed, 'ADAPTER_SIGNATURE_MISMATCH')
    # Explicit adapter/container gate, followed by the unchanged reader's layout
    # gate. Neither the filename nor a claimed issue payload chooses the parser.
    try:
        actual = normalize_rent_roll(io.BytesIO(original_bytes), ADAPTERS[adapter_id])
    except Exception:
        raise _Failure('PARSER_REFUSED') from None
    _native(actual)
    _require(_encode(value) == _encode(actual), 'PARSER_OUTPUT_MISMATCH')
    return actual


def _record_id(prefix, source_id, digest, pointer):
    data = ['v1-migration/1.0.0', prefix, source_id, digest, pointer]
    return prefix + '_' + hashlib.sha256(_encode(data)).hexdigest()[:32]


def _project(value, source_id, subject_id, as_of, adapter_id):
    _shape(value, ('version', 'pms_type', 'source_sha256', 'residential_units',
                   'commercial_units', 'counts', 'summary', 'issues', 'status'))
    _require(value['version'] == '1.0' and value['pms_type'] == ADAPTERS[adapter_id], 'UNREPRESENTABLE_V1')
    _require(value['status'] in ('blocked', 'normalized_unvalidated'), 'UNREPRESENTABLE_V1')
    digest = value['source_sha256']

    def cite(locator):
        if locator is None:
            return None
        _shape(locator, ('source_sha256', 'sheet', 'row', 'row_end', 'column'))
        _require(locator['source_sha256'] == digest, 'UNREPRESENTABLE_V1')
        return {'source_id': source_id, **locator}

    def groups(evidence):
        return [{field: cite(locator) for field, locator in group.items()} for group in evidence]

    def unit(item):
        required = {'unit_id', 'status', 'unit_type', 'evidence'}
        omitted = {'tenant_name', 'phone', 'email'}
        _require(required <= set(item) <= required | omitted, 'UNREPRESENTABLE_V1')
        _require(all(item[field] == '[REDACTED]' for field in omitted & set(item)), 'UNREPRESENTABLE_V1')
        evidence = groups(item['evidence'])
        return {'observation_id': observation_id(evidence[0]['unit_id']), 'source_id': source_id,
                'unit_type': item['unit_type'], 'status': item['status'], 'evidence': evidence}

    _shape(value['summary'], SCOPES)
    _shape(value['counts'], SCOPES)
    result = {'contract_version': CONTRACT_VERSION, 'subject_id': subject_id, 'as_of': as_of,
        'adapter': {'id': adapter_id, 'version': '1.0.0'},
        'sources': [{'source_id': source_id, 'sha256': digest, 'role': 'original',
                     'original_source_ids': [], 'subject_id': subject_id, 'as_of': as_of}],
        'units': [unit(item) for scope in SCOPES for item in value[scope + '_units']],
        'unknown_use_units': [], 'summaries': [], 'issues': [], 'completeness': {},
        'status': 'blocked' if value['status'] == 'blocked' else 'observed_unvalidated'}
    for scope in SCOPES:
        summary = value['summary'][scope]
        _shape(summary, ('status', 'reported_counts', 'citations'))
        result['summaries'].append({
            'summary_id': _record_id('sum', source_id, digest, '/summary/' + scope),
            'source_id': source_id, 'scope': scope, 'status': summary['status'],
            'reported_counts': deepcopy(summary['reported_counts']), 'row_derived_counts': None,
            'vendor_status_counts': {}, 'matched_fields': [], 'citations': groups(summary['citations'])})
        counts = deepcopy(value['counts'][scope])
        enumeration = ('unknown' if all(count is None for count in counts.values()) else
                       'complete' if all(count is not None for count in counts.values()) else 'partial')
        result['completeness'][scope] = {'enumeration': enumeration, 'coverage': 'unknown',
                                        'coverage_citations': [], 'counts': counts}
    for index, issue in enumerate(value['issues']):
        pointer = '/issues/' + str(index)
        observation_ids, summary_ids = [], []
        if 'observation' in issue:
            _shape(issue, ('code', 'severity', 'citation', 'observation'))
            _require(adapter_id == 'onesite-detailed-realpage', 'UNREPRESENTABLE_V1')
            payload = issue['observation']
            if issue['code'] == 'UNRESOLVED_UNIT_USE':
                projected = unit(payload)
                _require(projected['unit_type'] is None, 'UNREPRESENTABLE_V1')
                result['unknown_use_units'].append(projected)
                observation_ids.append(projected['observation_id'])
            elif issue['code'] == 'ONESITE_REPORT_SUMMARY':
                _shape(payload, ('status', 'reported_counts', 'row_derived_counts',
                                 'vendor_status_counts', 'citations', 'matched_fields'))
                summary_id = _record_id('sum', source_id, digest, pointer + '/observation')
                result['summaries'].append({
                    'summary_id': summary_id, 'source_id': source_id, 'scope': 'unknown',
                    'status': payload['status'], 'reported_counts': deepcopy(payload['reported_counts']),
                    'row_derived_counts': deepcopy(payload['row_derived_counts']),
                    'vendor_status_counts': deepcopy(payload['vendor_status_counts']),
                    'matched_fields': deepcopy(payload['matched_fields']),
                    'citations': groups([payload['citations']]) if payload['citations'] else []})
                summary_ids.append(summary_id)
            else:
                raise _Failure('UNREPRESENTABLE_V1')
        else:
            _shape(issue, ('code', 'severity', 'citation'))
        result['issues'].append({
            'issue_id': _record_id('iss', source_id, digest, pointer),
            'code': issue['code'], 'severity': issue['severity'], 'citation': cite(issue['citation']),
            'observation_ids': observation_ids, 'summary_ids': summary_ids})
    return validate_observations(result, subject_id=subject_id, as_of=as_of)


def _preservation(actual, migrated, source_id, subject_id, as_of, adapter_id):
    """Independent inverse projection, not a second call to _project.

    Reconstruct all v1 semantics except explicitly omitted identity/placeholder
    fields, then compare exact canonical bytes. Only aggregate hashes/counts
    leave this helper; the reconstructed in-memory tree is never persisted.
    """
    def same(left, right):
        _require(_encode(left) == _encode(right), 'PRESERVATION_MISMATCH')

    digest = actual['source_sha256']
    same(migrated['adapter'], {'id': adapter_id, 'version': '1.0.0'})
    same(migrated['sources'], [{'source_id': source_id, 'sha256': digest, 'role': 'original',
        'original_source_ids': [], 'subject_id': subject_id, 'as_of': as_of}])
    same([migrated['subject_id'], migrated['as_of']], [subject_id, as_of])

    def unbind(value):
        if type(value) is dict:
            return {key: unbind(child) for key, child in value.items() if key != 'source_id'}
        if type(value) is list:
            return [unbind(child) for child in value]
        return value

    def restore_unit(item):
        return {'status': item['status'], 'unit_type': item['unit_type'],
                'evidence': unbind(item['evidence'])}

    expected = deepcopy(actual)
    old_units = [item for scope in SCOPES for item in expected[scope + '_units']]
    old_units += [issue['observation'] for issue in expected['issues']
                  if issue['code'] == 'UNRESOLVED_UNIT_USE']
    omitted_placeholders = 0
    for item in old_units:
        del item['unit_id']
        for key in ('tenant_name', 'phone', 'email'):
            if key in item:
                same(item.pop(key), '[REDACTED]')
                omitted_placeholders += 1

    restored = {'version': '1.0', 'pms_type': ADAPTERS[adapter_id], 'source_sha256': digest,
        'residential_units': [], 'commercial_units': [], 'counts': {}, 'summary': {},
        'issues': [], 'status': 'blocked' if migrated['status'] == 'blocked' else 'normalized_unvalidated'}
    # The flattened scoped collection retains residential then commercial order,
    # including each parser-emitted collection's original observation order.
    same([unit['unit_type'] for unit in migrated['units']],
         [scope for scope in SCOPES for _ in actual[scope + '_units']])
    for item in migrated['units']:
        restored[item['unit_type'] + '_units'].append(restore_unit(item))
    for index, scope in enumerate(SCOPES):
        item = migrated['completeness'][scope]
        counts = actual['counts'][scope]
        enumeration = 'partial'
        if all(v is None for v in counts.values()):
            enumeration = 'unknown'
        elif all(v is not None for v in counts.values()):
            enumeration = 'complete'
        same(item, {'enumeration': enumeration, 'coverage': 'unknown',
                    'coverage_citations': [], 'counts': counts})
        restored['counts'][scope] = deepcopy(item['counts'])
        summary = migrated['summaries'][index]
        same([summary['summary_id'], summary['scope']],
             [_record_id('sum', source_id, digest, '/summary/' + scope), scope])
        same([summary['row_derived_counts'], summary['vendor_status_counts'], summary['matched_fields']],
             [None, {}, []])
        restored['summary'][scope] = {'status': summary['status'],
            'reported_counts': deepcopy(summary['reported_counts']), 'citations': unbind(summary['citations'])}

    unknown_index, summary_index = 0, 2
    for index, issue in enumerate(migrated['issues']):
        same(issue['issue_id'], _record_id('iss', source_id, digest, '/issues/' + str(index)))
        item = {'code': issue['code'], 'severity': issue['severity'], 'citation': unbind(issue['citation'])}
        if issue['code'] == 'UNRESOLVED_UNIT_USE':
            target = migrated['unknown_use_units'][unknown_index]
            unknown_index += 1
            same(issue['observation_ids'], [target['observation_id']])
            same(issue['summary_ids'], [])
            item['observation'] = restore_unit(target)
        elif issue['code'] == 'ONESITE_REPORT_SUMMARY':
            target = migrated['summaries'][summary_index]
            summary_index += 1
            same(issue['summary_ids'], [target['summary_id']])
            same(issue['observation_ids'], [])
            same([target['summary_id'], target['scope']], [
                _record_id('sum', source_id, digest, '/issues/' + str(index) + '/observation'), 'unknown'])
            _require(len(target['citations']) <= 1, 'PRESERVATION_MISMATCH')
            item['observation'] = {key: deepcopy(target[key]) for key in
                ('status', 'reported_counts', 'row_derived_counts', 'vendor_status_counts', 'matched_fields')}
            item['observation']['citations'] = unbind(target['citations'][0]) if target['citations'] else {}
        else:
            same([issue['observation_ids'], issue['summary_ids']], [[], []])
        restored['issues'].append(item)
    same(unknown_index, len(migrated['unknown_use_units']))
    same(summary_index, len(migrated['summaries']))
    same(expected, restored)

    def citations(value):
        if type(value) is dict:
            return (1 if 'source_sha256' in value and 'column' in value else
                    sum(citations(child) for child in value.values()))
        if type(value) is list:
            return sum(citations(child) for child in value)
        return 0

    return {'policy_version': 'v1-preservation/1.0.0', 'source_sha256': digest,
        'retained_v1_sha256': hashlib.sha256(_encode(expected)).hexdigest(),
        'reconstructed_v1_sha256': hashlib.sha256(_encode(restored)).hexdigest(),
        'v2_sha256': hashlib.sha256(_encode(migrated)).hexdigest(),
        'observations': len(old_units), 'scoped_observations': len(migrated['units']),
        'unknown_use_observations': unknown_index, 'summaries': summary_index,
        'issues': len(actual['issues']), 'citation_occurrences': citations(expected),
        'evidence_groups': sum(len(item['evidence']) for item in old_units),
        'omitted_unit_identity_fields': len(old_units),
        'omitted_redacted_placeholder_fields': omitted_placeholders}


def verify_preservation(value, migrated, *, source_id, subject_id, as_of, adapter_id, original_bytes):
    """Reparse original bytes and independently verify a v2 artifact; return ledger.

    This is a preservation check only, not original-role authentication, source
    scope review, a PII scrubber, reconciliation or permission to execute finance.
    """
    code = 'PRESERVATION_MISMATCH'
    try:
        actual = _reproduce(value, source_id, subject_id, as_of, adapter_id, original_bytes)
        checked = validate_observations(migrated, subject_id=subject_id, as_of=as_of)
        return _preservation(actual, checked, source_id, subject_id, as_of, adapter_id)
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise MigrationError(code) from None


def migrate_v1(value, *, source_id, subject_id, as_of, adapter_id, original_bytes):
    """Reproduce an exact v1 parser result, then project without upgrading trust.

    Original bytes must be immutable, host-authorized bytes, not a path or a
    derivative. Caller-owned value is neither mutated nor accepted as authority.
    """
    code = 'INVALID_V1'
    try:
        actual = _reproduce(value, source_id, subject_id, as_of, adapter_id, original_bytes)
        projected = _project(actual, source_id, subject_id, as_of, adapter_id)
        _preservation(actual, projected, source_id, subject_id, as_of, adapter_id)
        return projected
    except _Failure as exc:
        code = exc.args[0]
    except ObservationContractError:
        code = 'UNREPRESENTABLE_V1'
    except Exception:
        pass
    # Outside all handlers: even __context__ must not retain source exceptions.
    raise MigrationError(code) from None
