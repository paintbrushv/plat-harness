"""Pure, bounded ingestion validation; no IO, financial rates or authority.

See docs/INGEST_CONTRACT.md. This module deliberately does not import the
host's classification/approval contracts or the legacy occupancy coercions.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re

CONTRACT_VERSION = 'ingest-observation/2.0.0'
MAX_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 50_000
MAX_DEPTH = 16
MAX_NODES = 500_000
COUNT_FIELDS = ('occupied', 'vacant', 'down', 'total')
SCOPES = ('residential', 'commercial')


class ObservationContractError(ValueError):
    """Static diagnostic only; input-bearing exceptions are never chained."""
    def __init__(self, code='INVALID_OBSERVATIONS'):
        self.code = code if code in ('INVALID_OBSERVATIONS', 'INPUT_LIMIT_EXCEEDED',
                                     'SCOPE_MISMATCH') else 'INVALID_OBSERVATIONS'
        super().__init__('Observation contract rejected (' + self.code + ').')


class _Failure(Exception):
    pass


def _require(condition, code='INVALID_OBSERVATIONS'):
    if not condition:
        raise _Failure(code)


def _object(value, keys):
    _require(type(value) is dict and set(value) == set(keys))


def _pattern(value, pattern):
    _require(type(value) is str and re.fullmatch(pattern, value, re.ASCII) is not None)


def _id(value, prefix):
    _pattern(value, prefix + r'_[0-9a-f]{32}')


def _integer(value, low=0, high=1_000_000):
    _require(type(value) is int and low <= value <= high)


def _counts(value):
    _object(value, COUNT_FIELDS)
    for count in value.values():
        if count is not None:
            _integer(count)


def _scope(subject_id, as_of):
    if subject_id is not None:
        _pattern(subject_id, r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
    if as_of is not None:
        _pattern(as_of, r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
        date.fromisoformat(as_of)


def _tree(value):
    """Reject non-JSON native types/cycles/budgets before copying or encoding."""
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
                stack.append((key, depth + 1))
                stack.append((child, depth + 1))
        elif kind is list:
            _require(len(item) <= MAX_ITEMS, 'INPUT_LIMIT_EXCEEDED')
            stack.extend((child, depth + 1) for child in item)
        elif kind is str:
            _require(len(item) <= 128, 'INPUT_LIMIT_EXCEEDED')
            text_bytes += len(item.encode('utf-8'))
            _require(text_bytes <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
        elif kind is int:
            _require(abs(item) <= 1_000_000)
        else:
            _require(item is None or kind is bool)


def _encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False).encode('utf-8')


EVIDENCE_FIELDS = frozenset(('unit_id', 'status', 'unit_type', 'tenant_name', 'phone',
    'email', 'record_type', 'designation', 'floorplan', 'charge'))
ISSUE_CODES = frozenset('''FUTURE_OR_APPLICANT_EXCLUDED INCOMPLETE_INVENTORY_HEADER
UNSUPPORTED_SUMMARY_SCOPE INVALID_SUMMARY SUMMARY_CONFLICT DUPLICATE_SUMMARY_EVIDENCE
UNSCOPED_REPORT_TOTAL MALFORMED_ROW UNSUPPORTED_RECORD_TYPE INVALID_UNIT_CONTEXT
AMBIGUOUS_UNIT_CONTEXT UNSUPPORTED_UNIT_ID_FORMAT INVALID_UNIT_ID DUPLICATE_UNIT_CONFLICT
ORPHAN_CONTINUATION UNSUPPORTED_STATUS UNSUPPORTED_UNIT_TYPE CONTINUATION_EVIDENCE
DUPLICATE_UNIT_EVIDENCE NON_INVENTORY_SHEET_IGNORED NO_CURRENT_UNITS SUMMARY_MISMATCH
NON_CURRENT_SECTION_EXCLUDED NON_CURRENT_LEASE_EXCLUDED INCOMPLETE_UNIT_ROW
UNSUPPORTED_UNIT_STATUS UNSUPPORTED_SECONDARY_STATUS NON_CURRENT_CONTINUATION_EXCLUDED
INVENTORY_BOUNDARY_MISSING UNRESOLVED_UNIT_USE DOWN_EVIDENCE_UNRESOLVED
AMBIGUOUS_VENDOR_SUMMARY INVALID_VENDOR_SUMMARY INCOMPLETE_VENDOR_SUMMARY
VENDOR_SUMMARY_ABSENT ONESITE_REPORT_SUMMARY'''.split())


def _citation(value, sources=None, source_id=None):
    _require(type(value) is dict)
    base = {'source_id', 'source_sha256'}
    if set(value) == base | {'sheet', 'row', 'row_end', 'column'}:
        _integer(value['sheet'], 1, 1024)
        _integer(value['row'], 1, 1_000_000)
        _integer(value['row_end'], value['row'], 1_000_000)
        _integer(value['column'], 1, 16384)
    elif set(value) == base | {'page', 'bounds'}:
        _integer(value['page'], 1, 100_000)
        bounds = value['bounds']
        _require(type(bounds) is list and len(bounds) == 4)
        for number in bounds:
            _integer(number)
        _require(bounds[0] < bounds[2] and bounds[1] < bounds[3])
    else:
        raise _Failure('INVALID_OBSERVATIONS')
    _id(value['source_id'], 'src')
    _pattern(value['source_sha256'], r'[0-9a-f]{64}')
    if sources is not None:
        original = sources.get(value['source_id'])
        _require(original is not None and original['role'] == 'original'
                 and original['sha256'] == value['source_sha256'])
        if source_id is not None:
            source = sources.get(source_id)
            _require(source is not None)
            allowed = [source_id] if source['role'] == 'original' else source['original_source_ids']
            _require(value['source_id'] in allowed)


def observation_id(anchor):
    """Position-derived opaque ID; anchor is the first unit_id citation.

    This helper validates locator syntax only. Envelope validation additionally
    binds the locator to a declared original source and its digest.
    """
    code = 'INVALID_OBSERVATIONS'
    try:
        _tree(anchor)
        _citation(anchor)
        return 'unit_' + hashlib.sha256(_encode(anchor)).hexdigest()
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise ObservationContractError(code) from None


def _units(value, sources):
    units = {}
    positions = {}
    for collection in ('units', 'unknown_use_units'):
        _require(type(value[collection]) is list)
        for unit in value[collection]:
            _object(unit, ('observation_id', 'source_id', 'unit_type', 'status', 'evidence'))
            identity = unit['observation_id']
            _pattern(identity, r'unit_[0-9a-f]{64}')
            _require(identity not in units and unit['source_id'] in sources)
            _require(unit['unit_type'] in SCOPES if collection == 'units' else unit['unit_type'] is None)
            _require(unit['status'] in ('occupied', 'vacant', 'down', None))
            evidence = unit['evidence']
            _require(type(evidence) is list and bool(evidence))
            for fields in evidence:
                _require(type(fields) is dict and bool(fields) and set(fields) <= EVIDENCE_FIELDS)
                for cite in fields.values():
                    _citation(cite, sources, unit['source_id'])
                if 'unit_id' in fields:
                    position = _encode(fields['unit_id'])
                    _require(position not in positions or positions[position] == identity)
                    positions[position] = identity
            _require({'unit_id', 'status'} <= set(evidence[0]))
            if unit['unit_type'] is not None:
                _require(any('unit_type' in fields for fields in evidence))
            expected = 'unit_' + hashlib.sha256(_encode(evidence[0]['unit_id'])).hexdigest()
            _require(identity == expected)
            units[identity] = unit
    return units


WARNING_CODES = frozenset('''FUTURE_OR_APPLICANT_EXCLUDED DUPLICATE_SUMMARY_EVIDENCE
CONTINUATION_EVIDENCE DUPLICATE_UNIT_EVIDENCE NON_INVENTORY_SHEET_IGNORED
NON_CURRENT_SECTION_EXCLUDED NON_CURRENT_LEASE_EXCLUDED
NON_CURRENT_CONTINUATION_EXCLUDED'''.split())


def _issues(issues, sources, units, summaries):
    _require(type(issues) is list)
    identifiers = set()
    for issue in issues:
        _object(issue, ('issue_id', 'code', 'severity', 'citation', 'observation_ids', 'summary_ids'))
        _id(issue['issue_id'], 'iss')
        _require(issue['issue_id'] not in identifiers)
        identifiers.add(issue['issue_id'])
        _require(issue['code'] in ISSUE_CODES and issue['severity'] in ('warning', 'blocker'))
        _require(issue['severity'] == 'blocker' or issue['code'] in WARNING_CODES)
        if issue['citation'] is not None:
            _citation(issue['citation'], sources)
        for field, targets in (('observation_ids', units), ('summary_ids', summaries)):
            refs = issue[field]
            _require(type(refs) is list and all(type(ref) is str for ref in refs))
            _require(len(set(refs)) == len(refs) and all(ref in targets for ref in refs))
            if issue['citation'] is not None:
                for ref in refs:
                    _citation(issue['citation'], sources, targets[ref]['source_id'])
        if issue['code'] == 'UNRESOLVED_UNIT_USE':
            _require(bool(issue['observation_ids']) and not issue['summary_ids'])
            _require(all(units[ref]['unit_type'] is None for ref in issue['observation_ids']))
        if issue['code'] == 'ONESITE_REPORT_SUMMARY':
            _require(bool(issue['summary_ids']) and not issue['observation_ids'])


VENDOR_FIELDS = ('occupied_no_ntv', 'occupied_ntv', 'occupied_ntv_leased',
                 'vacant_leased', 'admin_down', 'vacant_not_leased', 'totals')
_VENDOR_GROUPS = {'occupied': VENDOR_FIELDS[:3],
                  'vacant': ('vacant_leased', 'vacant_not_leased'), 'total': ('totals',)}


def _derived(units):
    counts = {field: sum(unit['status'] == field for unit in units) for field in COUNT_FIELDS[:3]}
    if any(unit['status'] is None for unit in units):
        counts = dict.fromkeys(COUNT_FIELDS[:3])
    counts['total'] = len(units)
    return counts


def _matches_derived(counts, derived):
    for field, count in counts.items():
        if count is not None:
            _require(count == derived[field])


def _summaries(values, sources, units, completeness):
    _require(type(values) is list)
    summaries = {}
    for summary in values:
        _object(summary, ('summary_id', 'source_id', 'scope', 'status', 'reported_counts',
                          'row_derived_counts', 'vendor_status_counts', 'citations', 'matched_fields'))
        _id(summary['summary_id'], 'sum')
        _require(summary['summary_id'] not in summaries and summary['source_id'] in sources)
        _require(summary['scope'] in (*SCOPES, 'unknown'))
        _require(summary['status'] in ('absent', 'unresolved', 'invalid', 'mismatch', 'reconciled'))
        reported = summary['reported_counts']
        derived = summary['row_derived_counts']
        for counts in (reported, derived):
            if counts is not None:
                _counts(counts)
        selected = [unit for unit in units.values() if unit['source_id'] == summary['source_id']
                    and (summary['scope'] == 'unknown' or unit['unit_type'] == summary['scope'])]
        actual = _derived(selected)
        if derived is not None:
            _matches_derived(derived, actual)
        vendor = summary['vendor_status_counts']
        _require(type(vendor) is dict and set(vendor) <= set(VENDOR_FIELDS))
        for count in vendor.values():
            if count is not None:
                _integer(count)
        citations = summary['citations']
        _require(type(citations) is list)
        cited = set()
        for fields in citations:
            _require(type(fields) is dict and bool(fields)
                     and set(fields) <= set(COUNT_FIELDS) | set(VENDOR_FIELDS))
            for cite in fields.values():
                _citation(cite, sources, summary['source_id'])
            cited.update(fields)
        _require(set(vendor) <= cited)
        if reported is not None:
            for field, count in reported.items():
                if count is None:
                    continue
                if field not in cited:
                    group = _VENDOR_GROUPS.get(field)
                    _require(group is not None and all(vendor.get(key) is not None and key in cited for key in group))
                    _require(count == sum(vendor[key] for key in group))
        matched = summary['matched_fields']
        _require(type(matched) is list and all(field in COUNT_FIELDS for field in matched))
        _require(len(set(matched)) == len(matched))
        for field in matched:
            _require(reported is not None and derived is not None and reported[field] is not None
                     and reported[field] == derived[field])
        if summary['status'] == 'absent':
            _require(reported is None and not vendor and not citations and not matched)
        if summary['status'] == 'reconciled':
            _require(summary['scope'] in SCOPES and reported is not None
                     and all(count is not None for count in reported.values()))
            _require(not any(unit['unit_type'] is None for unit in units.values()))
            _require(reported == actual)
            _require(sum(reported[field] for field in COUNT_FIELDS[:3]) == reported['total'])
            _require(completeness[summary['scope']]['enumeration'] == 'complete')
        summaries[summary['summary_id']] = summary
    return summaries


def _completeness(value, sources, units):
    _object(value['completeness'], SCOPES)
    for scope, item in value['completeness'].items():
        _object(item, ('enumeration', 'coverage', 'coverage_citations', 'counts'))
        _counts(item['counts'])
        _require(item['enumeration'] in ('unknown', 'partial', 'complete'))
        _require(item['coverage'] in ('unknown', 'established'))
        cites = item['coverage_citations']
        _require(type(cites) is list)
        for cite in cites:
            _citation(cite, sources)
        _require(bool(cites) == (item['coverage'] == 'established'))
        if item['coverage'] == 'established':
            _require(value['subject_id'] is not None and value['as_of'] is not None
                     and item['enumeration'] == 'complete' and not value['unknown_use_units'])
        counts = item['counts']
        selected = [unit for unit in units.values() if unit['unit_type'] == scope]
        _matches_derived(counts, _derived(selected))
        if item['enumeration'] == 'unknown':
            _require(all(count is None for count in counts.values()))
        if item['enumeration'] == 'complete':
            _require(all(count is not None for count in counts.values()))
            _require(not value['unknown_use_units'])
        if all(count is not None for count in counts.values()):
            _require(sum(counts[field] for field in COUNT_FIELDS[:3]) == counts['total'])


def _validate(value, subject_id, as_of):
    _tree(value)
    _scope(subject_id, as_of)
    _object(value, {'contract_version', 'subject_id', 'as_of', 'adapter', 'sources',
                    'units', 'unknown_use_units', 'summaries', 'issues', 'completeness', 'status'})
    _require(value['contract_version'] == CONTRACT_VERSION)
    _scope(value['subject_id'], value['as_of'])
    _require((value['subject_id'], value['as_of']) == (subject_id, as_of), 'SCOPE_MISMATCH')
    _object(value['adapter'], ('id', 'version'))
    _pattern(value['adapter']['id'], r'[a-z][a-z0-9_-]{0,63}')
    _pattern(value['adapter']['version'], r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})')
    _require(type(value['sources']) is list and 0 < len(value['sources']) <= 128)
    sources = {}
    for source in value['sources']:
        _object(source, ('source_id', 'sha256', 'role', 'original_source_ids', 'subject_id', 'as_of'))
        _id(source['source_id'], 'src')
        _require(source['source_id'] not in sources)
        _pattern(source['sha256'], r'[0-9a-f]{64}')
        _require((source['subject_id'], source['as_of']) == (subject_id, as_of), 'SCOPE_MISMATCH')
        _require(source['role'] in ('original', 'derivative'))
        parents = source['original_source_ids']
        _require(type(parents) is list)
        for parent in parents:
            _id(parent, 'src')
        _require(len(set(parents)) == len(parents))
        _require(bool(parents) == (source['role'] == 'derivative'))
        sources[source['source_id']] = source
    for source in sources.values():
        for parent in source['original_source_ids']:
            _require(parent in sources and sources[parent]['role'] == 'original')
    _require(len({source['sha256'] for source in sources.values()}) == len(sources))
    units = _units(value, sources)
    _completeness(value, sources, units)
    summaries = _summaries(value['summaries'], sources, units, value['completeness'])
    _issues(value['issues'], sources, units, summaries)
    _require(value['status'] in ('blocked', 'observed_unvalidated'))
    if (not units or value['unknown_use_units'] or any(unit['status'] is None for unit in units.values())
            or any(issue['severity'] == 'blocker' for issue in value['issues'])
            or any(summary['status'] in ('invalid', 'mismatch', 'unresolved') for summary in summaries.values())):
        _require(value['status'] == 'blocked')
    _require(len(_encode(value)) <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
    return deepcopy(value)


def validate_observations(value, *, subject_id, as_of):
    """Validate exact host scope and return a defensive JSON-native copy."""
    code = 'INVALID_OBSERVATIONS'
    try:
        return _validate(value, subject_id, as_of)
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise ObservationContractError(code) from None


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        _require(key not in result)
        result[key] = value
    return result


def _not_number(_):
    raise _Failure('INVALID_OBSERVATIONS')


def _decode(raw):
    _require(type(raw) in (bytes, str))
    _require(len(raw) <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
    text = raw.decode('utf-8') if type(raw) is bytes else raw
    _require(len(text.encode('utf-8')) <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
    # Bound nesting before the standard JSON decoder allocates nested containers.
    depth = 0
    quoted = escaped = False
    for char in text:
        if quoted:
            if escaped:
                escaped = False
            elif char == '\\':
                escaped = True
            elif char == '"':
                quoted = False
        elif char == '"':
            quoted = True
        elif char in '[{':
            depth += 1
            _require(depth <= MAX_DEPTH, 'INPUT_LIMIT_EXCEEDED')
        elif char in ']}':
            depth -= 1
    return json.loads(text, object_pairs_hook=_pairs, parse_constant=_not_number,
                      parse_float=_not_number)


def loads_observations(bytes_or_str, *, subject_id, as_of):
    """Decode bounded UTF-8 JSON, reject duplicate keys, then validate."""
    code = 'INVALID_OBSERVATIONS'
    try:
        return _validate(_decode(bytes_or_str), subject_id, as_of)
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise ObservationContractError(code) from None


def canonical_bytes(value, *, subject_id, as_of):
    """Validate before emitting compact sorted-key UTF-8 JSON (no newline)."""
    return _encode(validate_observations(value, subject_id=subject_id, as_of=as_of))
