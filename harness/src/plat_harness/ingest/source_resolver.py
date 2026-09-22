"""Host-only original-byte verifier for a deliberately narrow CSV dialect.

No filesystem, network, receipt intake, finance or approval authority. Intake
provenance is supplied by the trusted host, NEVER authenticated by CSV syntax.
"""
from copy import deepcopy
import hashlib
import re

from . import contracts as c

MAX_SOURCE_BYTES = 1024 * 1024
MAX_SOURCE_ROWS = 10_000
MAX_SOURCES = 32
HEADER = ('Unit', 'Status', 'Unit Type', 'Property', 'As Of', 'Coverage', 'Status Definition')
TOKENS = {'unit_type': {'residential': 'residential', 'apartment': 'residential',
                        'commercial': 'commercial', 'retail': 'commercial', 'office': 'commercial'},
          'status': {'occupied': 'occupied', 'current': 'occupied', 'notice': 'occupied',
                     'vacant': 'vacant', 'down': 'down', 'offline': 'down',
                     'out of service': 'down'}}
RULES = {'unit_type': 'use-token/1', 'status': 'status-token/1'}
_SPEC = {'header': list(HEADER), 'tokens': TOKENS, 'rules': RULES,
         'coverage': 'all_physical_units/1', 'status_definition': 'occupied_vacant_down/1',
         'dimension': 'physical_unit', 'dialect': 'ascii-unquoted-csv/1',
         'consistency': 'both counted fields across base and all active relied-on rows; aliases compare by enum',
         'supplement_meaning': 'both fields supported with independent status definition; unknown base may be resolved',
         'definition': 'occupied includes current/notice; vacant excludes down; down is out of service',
         'coverage_definition': 'all physical units of both uses; no omitted units or ancillary spaces'}


def digest(value):
    return hashlib.sha256(c._encode(value)).hexdigest()


SEMANTICS = {'id': 'bounded-csv-physical', 'version': '1.0.1', 'sha256': digest(_SPEC)}


class SourceResolutionError(ValueError):
    def __init__(self):
        self.code = 'INVALID_SOURCE_CONTEXT'
        super().__init__('Source verification refused (INVALID_SOURCE_CONTEXT).')


class _Need(Exception):
    pass


def _need(condition, code):
    if not condition:
        raise _Need(code)


def _records(sources, subject_id, as_of):
    c._require(type(sources) is list and 0 < len(sources) <= MAX_SOURCES)
    result = {}
    for source in sources:
        c._object(source, ('source_id', 'sha256', 'role', 'original_source_ids', 'subject_id', 'as_of'))
        c._id(source['source_id'], 'src')
        c._pattern(source['sha256'], r'[0-9a-f]{64}')
        c._require(source['source_id'] not in result)
        c._require((source['subject_id'], source['as_of']) == (subject_id, as_of))
        c._require(source['role'] in ('original', 'derivative'))
        parents = source['original_source_ids']
        c._require(type(parents) is list and len(parents) == len(set(parents)))
        c._require(bool(parents) == (source['role'] == 'derivative'))
        result[source['source_id']] = source
    c._require(len({s['sha256'] for s in sources}) == len(sources))
    for source in sources:
        for parent in source['original_source_ids']:
            c._require(parent in result and result[parent]['role'] == 'original')
    return result


def _table(raw):
    """Physical one-line rows; no quoting, BOM, blank bands or free-text columns."""
    try:
        if raw.startswith((b'PK', b'%PDF', bytes.fromhex('d0cf11e0a1b11ae1'))):
            raise _Need('UNSUPPORTED_FORMAT')
        text = raw.decode('ascii').replace('\r\n', '\n')
        _need(all(ch == '\n' or 32 <= ord(ch) <= 126 for ch in text) and '"' not in text,
              'UNSUPPORTED_LAYOUT')
        lines = text.split('\n')
        if lines[-1] == '':
            lines.pop()
        _need(1 <= len(lines) <= MAX_SOURCE_ROWS + 1, 'SOURCE_LIMIT')
        rows = tuple(tuple(line.split(',')) for line in lines)
        _need(rows[0] == HEADER, 'UNSUPPORTED_LAYOUT')
        _need(all(len(row) == len(HEADER) and all(len(cell) <= 128 for cell in row)
                  for row in rows[1:]), 'UNSUPPORTED_LAYOUT')
        _need(all(re.fullmatch(r'(?:[A-Z]{1,3}-?)?[0-9]{1,4}[A-Z]?', row[0], re.ASCII)
                  for row in rows[1:]), 'UNIT_IDENTITY_REQUIRED')
        _need(len({row[0] for row in rows[1:]}) == len(rows) - 1, 'AMBIGUOUS_UNIT_IDENTITY')
        return None, rows
    except _Need as exc:
        code = exc.args[0]
    except Exception:
        code = 'UNSUPPORTED_LAYOUT'
    return code, ()


class ByteSourceResolver:
    """Construct ONLY from independently authorized host intake state.

    originals: source_id -> immutable bytes. sources: exact v2 source records.
    intake_provenance: every original ID -> {sha256, role:'original', intake_id}.
    The host must vet provenance/role; creating this mapping is not authentication.
    Byte and record snapshots are private, defensive copies. No user paths exist.
    """
    def __init__(self, *, originals, sources, expected_envelope_sha256,
                 subject_id, as_of, adapter, intake_provenance):
        try:
            c._tree([sources, expected_envelope_sha256, subject_id, as_of, adapter, intake_provenance])
            c._scope(subject_id, as_of)
            c._pattern(expected_envelope_sha256, r'[0-9a-f]{64}')
            c._object(adapter, ('id', 'version'))
            c._pattern(adapter['id'], r'[a-z][a-z0-9_-]{0,63}')
            c._pattern(adapter['version'], r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})')
            records = _records(sources, subject_id, as_of)
            original_ids = {sid for sid, s in records.items() if s['role'] == 'original'}
            c._require(type(originals) is dict and all(type(key) is str for key in originals))
            c._require(set(originals) == original_ids)
            originals = dict(originals)  # Snapshot before digest validation and parsing.
            c._require(type(intake_provenance) is dict and set(intake_provenance) == original_ids)
            total = 0
            for sid, raw in originals.items():
                c._require(type(raw) is bytes and len(raw) <= MAX_SOURCE_BYTES)
                total += len(raw)
                c._require(total <= c.MAX_BYTES)
                c._require(hashlib.sha256(raw).hexdigest() == records[sid]['sha256'])
                proof = intake_provenance[sid]
                c._object(proof, ('sha256', 'role', 'intake_id'))
                c._require(proof['sha256'] == records[sid]['sha256'] and proof['role'] == 'original')
                c._id(proof['intake_id'], 'intake')
            self._context = deepcopy({'expected_envelope_sha256': expected_envelope_sha256,
                'subject_id': subject_id, 'as_of': as_of, 'adapter': adapter, 'sources': sources,
                'intake_provenance_sha256': digest(intake_provenance)})
            self._records = deepcopy(records)
            self._originals = dict(originals)
            self._tables = {sid: _table(raw) for sid, raw in originals.items()}
            return
        except Exception:
            pass
        raise SourceResolutionError() from None

    def context(self):
        return deepcopy(self._context)

    def _scope_adapter(self):
        ctx = self._context
        _need(ctx['subject_id'] is not None and ctx['as_of'] is not None, 'SCOPE_REQUIRED')
        _need(ctx['adapter']['id'] in ('pms-flat-yardi', 'pms-flat-realpage', 'pms-flat-entrata')
              and ctx['adapter']['version'] == '1.0.0', 'ADAPTER_UNSUPPORTED')

    def _coverage(self, anchors):
        c._tree(anchors)
        c._require(type(anchors) is list)
        self._scope_adapter()
        _need(bool(anchors), 'COVERAGE_REQUIRED')
        for cite in anchors:
            c._citation(cite, self._records)
        source_ids = {cite['source_id'] for cite in anchors}
        _need(len(source_ids) == 1, 'MULTISOURCE_COVERAGE_UNSUPPORTED')
        sid = anchors[0]['source_id']
        code, rows = self._tables[sid]
        _need(code is None, code)
        observed = set()
        citations = []
        for cite in anchors:
            row = self._row(cite, 1)
            _need(cite['row'] not in observed, 'INVENTORY_MEMBERSHIP_MISMATCH')
            observed.add(cite['row'])
            _need(row[5] == 'all_physical_units/1', 'COVERAGE_REQUIRED')
            _need(row[6] == 'occupied_vacant_down/1', 'DOWN_DEFINITION_REQUIRED')
            citations.extend(({**cite, 'column': 6}, {**cite, 'column': 7}))
        _need(observed == set(range(2, len(rows) + 1)), 'INVENTORY_MEMBERSHIP_MISMATCH')
        return {'state': 'verified', 'anchors_sha256': digest(anchors), 'citations': citations,
                'context_sha256': digest(self._context), 'verifier': deepcopy(SEMANTICS)}

    def coverage(self, anchors):
        """Independent explicit whole-inventory and disjoint-status definitions."""
        try:
            return self._coverage(anchors)
        except _Need as exc:
            return {'state': 'evidence_required', 'code': exc.args[0]}
        except Exception:
            pass
        raise SourceResolutionError() from None

    def _row(self, cite, column):
        c._citation(cite, self._records)
        _need(set(cite) == {'source_id', 'source_sha256', 'sheet', 'row', 'row_end', 'column'},
              'UNSUPPORTED_FORMAT')
        _need(cite['sheet'] == 1 and cite['column'] == column and cite['row'] == cite['row_end'],
              'POSITION_MEANING_MISMATCH')
        code, rows = self._tables[cite['source_id']]
        _need(code is None, code)
        _need(2 <= cite['row'] <= len(rows), 'POSITION_MEANING_MISMATCH')
        row = rows[cite['row'] - 1]
        ctx = self._context
        _need(ctx['subject_id'] is not None and ctx['as_of'] is not None, 'SCOPE_REQUIRED')
        _need((row[3], row[4]) == (ctx['subject_id'], ctx['as_of']), 'SOURCE_SCOPE_MISMATCH')
        return row

    def _consistency(self, target, citations):
        c._tree([target, citations])
        c._require(type(citations) is list)
        self._scope_adapter()
        base = self._row(target, 1)
        rows = [(base, False)]
        for cite in citations:
            c._citation(cite, self._records)
            column = cite.get('column')
            _need(column in (2, 3), 'POSITION_MEANING_MISMATCH')
            row = self._row(cite, column)
            _need(row[0] == base[0], 'TARGET_UNIT_MISMATCH')
            # Unknown base fields may be resolved by evidence. A different
            # relied-on row needs supported meaning for both counted fields.
            supplemental = (cite['source_id'], cite['row']) != (target['source_id'], target['row'])
            rows.append((row, supplemental))
        facts = {field: set() for field in TOKENS}
        unsupported = False
        for row, supplemental in rows:
            _need(row[6] == 'occupied_vacant_down/1', 'DOWN_DEFINITION_REQUIRED')
            for field, index in (('status', 1), ('unit_type', 2)):
                value = TOKENS[field].get(row[index])
                if value is not None:
                    facts[field].add(value)
                elif supplemental:
                    unsupported = True
        # Sets compare every recognized pair, including when base is unknown.
        _need(all(len(values) <= 1 for values in facts.values()), 'SOURCE_CONTRADICTION')
        _need(not unsupported, 'SOURCE_VALUE_UNSUPPORTED')
        return {'state': 'verified', 'target_sha256': digest(target),
                'citations_sha256': digest(citations), 'context_sha256': digest(self._context),
                'verifier': deepcopy(SEMANTICS)}

    def consistency(self, target, citations):
        """Check both counted fields across exactly these relied-on field rows.

        This is not a field-value or coverage proof. Unknown base values remain
        unknown; only verify(claim) can support a proposed effective value.
        """
        try:
            return self._consistency(target, citations)
        except _Need as exc:
            return {'state': 'evidence_required', 'code': exc.args[0]}
        except Exception:
            pass
        raise SourceResolutionError() from None

    def _verify(self, claim):
        c._tree(claim)
        c._object(claim, ('target', 'field', 'value', 'citation', 'rule_id', 'value_sha256'))
        c._require(claim['field'] in TOKENS)
        c._require(claim['value'] in set(TOKENS[claim['field']].values()))
        if claim['value_sha256'] is not None:
            c._pattern(claim['value_sha256'], r'[0-9a-f]{64}')
        c._citation(claim['target'], self._records)
        c._citation(claim['citation'], self._records)
        field = claim['field']
        self._scope_adapter()
        _need(claim['rule_id'] == RULES[field], 'SEMANTIC_RULE_REQUIRED')
        target = self._row(claim['target'], 1)
        row = self._row(claim['citation'], 2 if field == 'status' else 3)
        _need(row[0] == target[0], 'TARGET_UNIT_MISMATCH')
        if field == 'status':
            _need(row[6] == 'occupied_vacant_down/1', 'DOWN_DEFINITION_REQUIRED')
        token = row[1 if field == 'status' else 2]
        original = TOKENS[field].get(target[1 if field == 'status' else 2])
        _need(original is None or original == claim['value'], 'SOURCE_CONTRADICTION')
        _need(TOKENS[field].get(token) == claim['value'], 'SOURCE_VALUE_UNSUPPORTED')
        fingerprint = hashlib.sha256(token.encode('ascii')).hexdigest()
        _need(claim['value_sha256'] is None or fingerprint == claim['value_sha256'],
              'SOURCE_VALUE_MISMATCH')
        self._consistency(claim['target'], [claim['citation']])
        return {'state': 'verified', 'claim_sha256': digest(claim),
                'citation': deepcopy(claim['citation']), 'value_sha256': fingerprint,
                'context_sha256': digest(self._context), 'verifier': deepcopy(SEMANTICS)}

    def verify(self, claim):
        """Return a freshly computed proof or a static typed evidence request.

        A null fingerprint is reserved for host-generated verification of base
        observations; mapping decision validation requires a nonnull fingerprint.
        """
        try:
            return self._verify(claim)
        except _Need as exc:
            return {'state': 'evidence_required', 'code': exc.args[0]}
        except Exception:
            pass
        raise SourceResolutionError() from None
