"""Deterministic extraction-fact validation (Task 5.4).

Model output is untrusted extraction *claims*, never financial results.
Every claimed fact must be re-anchored to cited packet content by
deterministic checks: the citation must name this exact source (SHA-256),
the locator must exist in the egress packet, and the claimed value/label
must appear verbatim in the cited line or row cell. The validator performs
zero numeric parsing or arithmetic — extracted values survive only as
exact source strings; ambiguous figures stay verbatim and are never
converted. Model-generated assumptions, approvals and prompt-injected
tool actions are refused (recorded, never executed); confidence scores
cannot bypass any check. Injection-bearing cited lines are quarantined.
Narrative fields are dropped whole so prose cannot introduce new amounts.

Failures are recorded per fact (``unverified``/``review``), never silently
dropped; malformed input raises a sanitized, non-chained typed error.
Provider metadata is recorded for measurement only and can never alter
facts, statuses or citations.
"""
import copy
import re

from .extraction_packet import _INJECTION_RES
from .pms_normalizer import RentRollNormalizationError

__all__ = [
    'CONTRACT_VERSION', 'ERROR_CODES', 'ISSUE_CODES', 'MAX_CLAIMS',
    'MAX_VALUE_CHARS', 'ExtractionValidationError', 'validate_extraction',
]

CONTRACT_VERSION = 'ingest-extraction-validator/1.0.0'
MAX_CLAIMS = 1_000
MAX_VALUE_CHARS = 2_000

ERROR_CODES = frozenset((
    'MALFORMED_INPUT', 'MALFORMED_CLAIM', 'EGRESS_NOT_APPROVED',
))

ISSUE_CODES = frozenset((
    'CITATION_NOT_IN_SOURCE', 'VALUE_NOT_IN_CITED_CONTENT',
    'LABEL_NOT_IN_CITED_CONTENT', 'PERIOD_MISMATCH_REVIEW',
    'UNIT_MISMATCH_REVIEW', 'UNSUPPORTED_CLAIM_KIND',
    'PROMPT_INJECTED_TOOL_ACTION', 'PROMPT_INJECTION_SUSPECT_CITATION',
))

SUPPORTED_CLAIM_KINDS = frozenset(('fact',))

REQUIRED_CLAIM_KEYS = ('kind', 'label', 'value', 'section_code', 'citation')
OPTIONAL_CLAIM_KEYS = ('unit', 'period', 'confidence', 'tool_action',
                       'approval', 'narrative')
_CITATION_KEYS = ('source_sha256', 'sheet', 'row', 'row_end', 'column')
_PACKET_KEYS = ('version', 'source_sha256', 'total_pages', 'metadata',
                'sections', 'ocr_required_pages', 'issues')
_FACT_KEYS = ('label', 'value', 'citation', 'kind', 'status')
_PROVIDER_KEYS = ('provider_id', 'model_id')
_SHA256_RE = re.compile(r'[0-9a-f]{64}')


class ExtractionValidationError(ValueError):
    """Static diagnostic only; input-bearing exceptions are never chained."""

    def __init__(self, code='MALFORMED_INPUT'):
        self.code = code if code in ERROR_CODES else 'MALFORMED_INPUT'
        super().__init__('Extraction validation refused (' + self.code + ').')


class _Failure(Exception):
    pass


def _require(condition, code='MALFORMED_INPUT'):
    if not condition:
        raise _Failure(code)


def _typed(value, kind, code='MALFORMED_INPUT'):
    _require(type(value) is kind, code)
    return value


def _citation_shape(value):
    _require(type(value) is dict, 'MALFORMED_CLAIM')
    _require(tuple(sorted(value)) == tuple(sorted(_CITATION_KEYS)),
             'MALFORMED_CLAIM')
    _require(type(value['source_sha256']) is str
             and _SHA256_RE.fullmatch(value['source_sha256']) is not None,
             'MALFORMED_CLAIM')
    for key in ('sheet', 'row', 'row_end', 'column'):
        _require(type(value[key]) is int and value[key] >= 1, 'MALFORMED_CLAIM')
    return {key: value[key] for key in _CITATION_KEYS}


def _claim_shape(value):
    _require(type(value) is dict, 'MALFORMED_CLAIM')
    keys = set(value)
    allowed = set(REQUIRED_CLAIM_KEYS) | set(OPTIONAL_CLAIM_KEYS)
    _require(set(REQUIRED_CLAIM_KEYS) <= keys, 'MALFORMED_CLAIM')
    _require(keys <= allowed, 'MALFORMED_CLAIM')
    _require(type(value['kind']) is str, 'MALFORMED_CLAIM')
    _require(type(value['label']) is str and 0 < len(value['label'])
             <= MAX_VALUE_CHARS, 'MALFORMED_CLAIM')
    _require(type(value['value']) is str and bool(value['value'])
             and len(value['value']) <= MAX_VALUE_CHARS, 'MALFORMED_CLAIM')
    _require(type(value['section_code']) is str and bool(value['section_code']),
             'MALFORMED_CLAIM')
    for key in ('unit', 'period'):
        if value.get(key) is not None:
            _require(type(value[key]) is str and bool(value[key]),
                     'MALFORMED_CLAIM')
    citation = _citation_shape(value['citation'])
    return {
        'kind': value['kind'], 'label': value['label'],
        'value': value['value'], 'section_code': value['section_code'],
        'citation': citation, 'unit': value.get('unit'),
        'period': value.get('period'),
        'tool_action': value.get('tool_action'),
        'approval': value.get('approval'),
    }


def _provider_shape(value):
    if value is None:
        return None
    _require(type(value) is dict, 'MALFORMED_INPUT')
    _require(tuple(sorted(value)) == tuple(sorted(_PROVIDER_KEYS)),
             'MALFORMED_INPUT')
    for key in _PROVIDER_KEYS:
        _require(type(value[key]) is str and bool(value[key])
                 and len(value[key]) <= 256, 'MALFORMED_INPUT')
    return copy.deepcopy(value)


def _packet_shape(value):
    _require(type(value) is dict, 'MALFORMED_INPUT')
    _require(tuple(sorted(value)) == tuple(sorted(_PACKET_KEYS)),
             'MALFORMED_INPUT')
    digest = value['source_sha256']
    _require(type(digest) is str and _SHA256_RE.fullmatch(digest) is not None,
             'MALFORMED_INPUT')
    _require(type(value['total_pages']) is int and value['total_pages'] >= 1,
             'MALFORMED_INPUT')
    _require(type(value['sections']) is list, 'MALFORMED_INPUT')
    for section in value['sections']:
        _require(type(section) is dict, 'MALFORMED_INPUT')
        _require(type(section.get('code')) is str, 'MALFORMED_INPUT')
        _require(type(section.get('lines')) is list, 'MALFORMED_INPUT')
        _require(type(section.get('rows')) is list, 'MALFORMED_INPUT')
        for line in section['lines']:
            _require(type(line) is dict
                     and type(line.get('text')) is str
                     and type(line.get('citation')) is dict, 'MALFORMED_INPUT')
        for row in section['rows']:
            _require(type(row) is dict and type(row.get('cells')) is dict
                     and type(row.get('citation')) is dict, 'MALFORMED_INPUT')
    return digest, value['sections']


def _citation_equal(claim_cite, cite):
    return all(claim_cite[key] == cite.get(key) for key in _CITATION_KEYS)


def _injected(text):
    return any(pattern.search(text) for pattern in _INJECTION_RES)


def _find_anchor(sections, claim):
    """Locate the cited line or row; return its text content or None."""
    for section in sections:
        if section['code'] != claim['section_code']:
            continue
        for line in section['lines']:
            if _citation_equal(claim['citation'], line['citation']):
                return line['text']
        for row in section['rows']:
            if _citation_equal(claim['citation'], row['citation']):
                return '\n'.join(str(cell) for cell in row['cells'].values())
    return None


def _validate_claim(claim, sections, digest):
    """Deterministic re-anchoring; returns (fact, issue or None)."""
    issues = []

    def issue(code):
        issues.append({'code': code, 'citation': copy.deepcopy(
            claim['citation'])})

    status = 'verified'
    if claim['tool_action'] is not None or claim['approval'] is not None:
        # Model output is untrusted data: it can never carry tool or
        # approval authority, regardless of any confidence score.
        issue('PROMPT_INJECTED_TOOL_ACTION')
        status = 'unverified'
    elif claim['kind'] not in SUPPORTED_CLAIM_KINDS:
        issue('UNSUPPORTED_CLAIM_KIND')
        status = 'unverified'
    elif claim['citation']['source_sha256'] != digest:
        issue('CITATION_NOT_IN_SOURCE')
        status = 'unverified'
    else:
        content = _find_anchor(sections, claim)
        if content is None:
            issue('CITATION_NOT_IN_SOURCE')
            status = 'unverified'
        elif _injected(content):
            # A prompt-injection-bearing line is quarantined: it can never
            # anchor a verified fact, and its text never travels onward.
            issue('PROMPT_INJECTION_SUSPECT_CITATION')
            status = 'unverified'
        elif claim['value'] not in content:
            issue('VALUE_NOT_IN_CITED_CONTENT')
            status = 'unverified'
        else:
            if claim['label'] not in content:
                issue('LABEL_NOT_IN_CITED_CONTENT')
                status = 'review' if status == 'verified' else status
            for key, code in (('period', 'PERIOD_MISMATCH_REVIEW'),
                             ('unit', 'UNIT_MISMATCH_REVIEW')):
                token = claim[key]
                if token is not None and token not in content:
                    issue(code)
                    status = 'review' if status == 'verified' else status

    fact = {
        'label': claim['label'], 'value': claim['value'],
        'citation': copy.deepcopy(claim['citation']),
        'kind': claim['kind'], 'status': status,
    }
    return fact, issues


def _validate(claims, source, egress_approved, provider):
    digest, sections = _packet_shape(source)
    provider = _provider_shape(provider)
    if egress_approved is not True:
        raise _Failure('EGRESS_NOT_APPROVED')
    _require(type(claims) is list, 'MALFORMED_INPUT')
    _require(len(claims) <= MAX_CLAIMS, 'MALFORMED_INPUT')
    shaped = [_claim_shape(claim) for claim in claims]

    facts = []
    issues = []
    for claim in shaped:
        fact, claim_issues = _validate_claim(claim, sections, digest)
        facts.append(fact)
        issues.extend(claim_issues)

    if any(fact['status'] == 'unverified' for fact in facts):
        status = 'invalid'
    elif issues:
        status = 'needs_review'
    else:
        status = 'verified'

    return {
        'version': CONTRACT_VERSION,
        'source_sha256': digest,
        'status': status,
        'facts': facts,
        'issues': issues,
        'provider': provider,
    }


def validate_extraction(claims, source, *, egress_approved=False,
                       provider=None):
    """Validate model-claimed extraction facts against cited packet content.

    Returns per-fact statuses (``verified`` / ``unverified`` / ``review``)
    with typed issues; never executes tool actions, never converts numbers,
    and never lets provider labels or confidence scores alter a check.
    Raises ``ExtractionValidationError`` with a static code on malformed
    input or unapproved egress.
    """
    code = 'MALFORMED_INPUT'
    try:
        return _validate(claims, source, egress_approved, provider)
    except _Failure as exc:
        code = exc.args[0]
    except ExtractionValidationError:
        raise
    except RentRollNormalizationError:
        code = 'MALFORMED_INPUT'
    except Exception:
        pass
    raise ExtractionValidationError(code) from None
