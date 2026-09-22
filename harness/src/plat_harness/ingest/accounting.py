"""Pure source monetary observations; no IO, summing, rates or authority.

See docs/INGEST_ACCOUNTING.md. Amounts are finite decimal strings with
currency, unit, period and a source locator. Python floats are refused.
This module does not import host approval contracts or occupancy helpers.
"""
from copy import deepcopy
from datetime import date
import hashlib
import json
import re

CONTRACT_VERSION = 'ingest-accounting/1.0.0'
MAX_BYTES = 8 * 1024 * 1024
MAX_ITEMS = 50_000
MAX_DEPTH = 16
MAX_NODES = 500_000
MONEY_KINDS = (
    'unit_rent', 'charge_rent', 'report_total', 'deposit', 'concession',
    'arrears', 'scheduled_charge', 'collected_cash',
)
KIND_BASIS = {
    'unit_rent': 'unit', 'charge_rent': 'charge', 'report_total': 'report',
    'deposit': 'deposit', 'concession': 'concession', 'arrears': 'arrears',
    'scheduled_charge': 'scheduled_charge', 'collected_cash': 'collected_cash',
}
PERIODS = frozenset(('month', 'year', 'day', 'one_time', 'as_of'))
FORMULA_ORIGINS = frozenset(('formula', 'formula_cache', 'cached_value', 'data_only_cache'))
ERROR_CODES = frozenset((
    'INVALID_OBSERVATIONS', 'INPUT_LIMIT_EXCEEDED', 'SCOPE_MISMATCH',
    'AMBIGUOUS_NUMERIC_FORMAT', 'FORMULA_CACHE_NOT_ORIGINAL',
    'MIXED_MEASUREMENT_BASIS', 'UNKNOWN_AMOUNT_NOT_ZERO',
))
_UNKNOWN_TEXT = frozenset(('', 'n/a', 'na', 'unknown', 'none', 'null', '-', '--', '—'))
_SYMBOLS = {'$': 'USD', '€': 'EUR', '£': 'GBP'}
_CURRENCY_CODES = frozenset(('USD', 'EUR', 'GBP'))
_DECIMAL = re.compile(r'^-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,8})?$')
_CURRENCY = re.compile(r'^[A-Z]{3}$')


class AccountingObservationError(ValueError):
    """Static diagnostic only; input-bearing exceptions are never chained."""

    def __init__(self, code='INVALID_OBSERVATIONS'):
        self.code = code if code in ERROR_CODES else 'INVALID_OBSERVATIONS'
        super().__init__('Accounting observation rejected (' + self.code + ').')


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


def _scope(subject_id, as_of):
    if subject_id is not None:
        _pattern(subject_id, r'[A-Za-z0-9][A-Za-z0-9_-]{0,63}')
    if as_of is not None:
        _pattern(as_of, r'[0-9]{4}-[0-9]{2}-[0-9]{2}')
        date.fromisoformat(as_of)


def _tree(value):
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


def _unknown_text(value):
    if value is None:
        return True
    if type(value) is not str:
        return False
    return value.strip().lower() in _UNKNOWN_TEXT


def _thousands(int_part, separator):
    _require(separator not in (int_part[:1] + int_part[-1:]) and separator * 2 not in int_part,
             'AMBIGUOUS_NUMERIC_FORMAT')
    groups = int_part.split(separator)
    _require(len(groups) >= 2, 'AMBIGUOUS_NUMERIC_FORMAT')
    _require(groups[0].isdigit() and groups[0][0] != '0' and 1 <= len(groups[0]) <= 3,
             'AMBIGUOUS_NUMERIC_FORMAT')
    for group in groups[1:]:
        _require(re.fullmatch(r'[0-9]{3}', group) is not None, 'AMBIGUOUS_NUMERIC_FORMAT')


def _numeric_body(body):
    _require(type(body) is str and body, 'AMBIGUOUS_NUMERIC_FORMAT')
    if ',' in body and '.' in body:
        if body.rfind('.') > body.rfind(','):
            thousands, decimal_sep = ',', '.'
        else:
            thousands, decimal_sep = '.', ','
        _require(body.count(decimal_sep) == 1, 'AMBIGUOUS_NUMERIC_FORMAT')
        integer, fraction = body.rsplit(decimal_sep, 1)
        _require(thousands not in fraction and fraction.isdigit() and bool(fraction),
                 'AMBIGUOUS_NUMERIC_FORMAT')
        _thousands(integer, thousands)
        return integer.replace(thousands, ''), fraction
    separator = ',' if ',' in body else '.' if '.' in body else None
    if separator is None:
        _require(body.isdigit(), 'AMBIGUOUS_NUMERIC_FORMAT')
        return body, None
    parts = body.split(separator)
    _require(all(parts) and len(parts) == 2, 'AMBIGUOUS_NUMERIC_FORMAT')
    integer, fraction = parts
    _require(integer.isdigit() and fraction.isdigit(), 'AMBIGUOUS_NUMERIC_FORMAT')
    _require(len(fraction) != 3, 'AMBIGUOUS_NUMERIC_FORMAT')
    if separator == ',':
        _require(len(fraction) <= 2, 'AMBIGUOUS_NUMERIC_FORMAT')
    return integer, fraction


def _parse_amount(source_text, currency):
    _require(type(source_text) is str, 'INVALID_OBSERVATIONS')
    _require(type(currency) is str and _CURRENCY.fullmatch(currency), 'INVALID_OBSERVATIONS')
    text = source_text.strip()
    _require(not _unknown_text(text), 'UNKNOWN_AMOUNT_NOT_ZERO')
    negative = False
    if text.startswith('(') and text.endswith(')') and len(text) >= 3:
        negative = True
        text = text[1:-1].strip()
    elif text.startswith('-') and text != '-':
        negative = True
        text = text[1:].strip()
    found = None
    for symbol, code in _SYMBOLS.items():
        if text.startswith(symbol):
            found, text = code, text[len(symbol):].strip()
            break
        if text.endswith(symbol):
            found, text = code, text[:-len(symbol)].strip()
            break
    if found is None:
        for code in _CURRENCY_CODES:
            if text.startswith(code + ' ') or text.startswith(code):
                rest = text[len(code):]
                if rest[:1] in ('', ' '):
                    found, text = code, rest.strip()
                    break
            if text.endswith(' ' + code) or text.endswith(code):
                rest = text[:-len(code)]
                if rest[-1:] in ('', ' '):
                    found, text = code, rest.strip()
                    break
    if found is not None:
        _require(found == currency, 'INVALID_OBSERVATIONS')
    _require(text and not any(ch.isspace() for ch in text), 'AMBIGUOUS_NUMERIC_FORMAT')
    _require(re.search(r'[eExX]|inf|nan', text, re.IGNORECASE) is None, 'INVALID_OBSERVATIONS')
    _require(re.fullmatch(r'[0-9.,]+', text) is not None, 'AMBIGUOUS_NUMERIC_FORMAT')
    integer, fraction = _numeric_body(text)
    _require(integer.isdigit() and not (integer[0] == '0' and integer != '0'),
             'AMBIGUOUS_NUMERIC_FORMAT')
    _require(len(integer) <= 16, 'INVALID_OBSERVATIONS')
    if fraction is None:
        decimal = integer
    else:
        _require(1 <= len(fraction) <= 8, 'INVALID_OBSERVATIONS')
        decimal = integer + '.' + fraction
    if negative:
        decimal = '-' + decimal
    _require(_DECIMAL.fullmatch(decimal) is not None, 'INVALID_OBSERVATIONS')
    return decimal


def _amount(value):
    if value is None:
        return
    _object(value, ('decimal', 'source_text', 'currency', 'unit', 'period'))
    _require(type(value['decimal']) is str and type(value['source_text']) is str)
    _require(type(value['currency']) is str and _CURRENCY.fullmatch(value['currency']))
    _require(value['unit'] == 'currency')
    _require(value['period'] in PERIODS)
    if _unknown_text(value['source_text']):
        raise _Failure('UNKNOWN_AMOUNT_NOT_ZERO')
    parsed = _parse_amount(value['source_text'], value['currency'])
    _require(parsed == value['decimal'])
    _require(_DECIMAL.fullmatch(value['decimal']) is not None)


def _cell_origin(value):
    _require(type(value) is str)
    if value in FORMULA_ORIGINS:
        raise _Failure('FORMULA_CACHE_NOT_ORIGINAL')
    _require(value == 'typed')


def observation_id(anchor):
    """Position-derived opaque ID from the money citation."""
    code = 'INVALID_OBSERVATIONS'
    try:
        _tree(anchor)
        _citation(anchor)
        return 'mny_' + hashlib.sha256(_encode(anchor)).hexdigest()
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise AccountingObservationError(code) from None


def observe_money(*, kind, measurement_basis, source_text, currency, period,
                  citation, cell_origin='typed', unit='currency'):
    """Return one JSON-native money observation. Never totals or rates."""
    code = 'INVALID_OBSERVATIONS'
    try:
        _require(kind in MONEY_KINDS)
        if measurement_basis != KIND_BASIS[kind]:
            raise _Failure('MIXED_MEASUREMENT_BASIS')
        _cell_origin(cell_origin)
        _require(unit == 'currency')
        _require(period in PERIODS)
        _tree({'citation': citation, 'source_text': source_text, 'currency': currency})
        _citation(citation)
        if _unknown_text(source_text):
            amount = None
        else:
            _require(type(source_text) is str, 'INVALID_OBSERVATIONS')
            decimal = _parse_amount(source_text, currency)
            amount = {'decimal': decimal, 'source_text': source_text,
                      'currency': currency, 'unit': unit, 'period': period}
        return {
            'observation_id': 'mny_' + hashlib.sha256(_encode(citation)).hexdigest(),
            'source_id': citation['source_id'], 'kind': kind,
            'measurement_basis': measurement_basis, 'amount': amount,
            'cell_origin': cell_origin, 'citation': deepcopy(citation),
        }
    except _Failure as exc:
        code = exc.args[0]
    except AccountingObservationError:
        raise
    except Exception:
        pass
    raise AccountingObservationError(code) from None


def _sources(value, subject_id, as_of):
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
    return sources


def _issues(issues, sources, observations):
    _require(type(issues) is list)
    identifiers = set()
    for issue in issues:
        _object(issue, ('issue_id', 'code', 'severity', 'citation', 'observation_ids'))
        _id(issue['issue_id'], 'iss')
        _require(issue['issue_id'] not in identifiers)
        identifiers.add(issue['issue_id'])
        _require(type(issue['code']) is str and issue['severity'] in ('warning', 'blocker'))
        if issue['citation'] is not None:
            _citation(issue['citation'], sources)
        refs = issue['observation_ids']
        _require(type(refs) is list and all(type(ref) is str for ref in refs))
        _require(len(set(refs)) == len(refs) and all(ref in observations for ref in refs))


def _observations(value, sources):
    _require(type(value['observations']) is list)
    observed = {}
    for item in value['observations']:
        _object(item, ('observation_id', 'source_id', 'kind', 'measurement_basis',
                       'amount', 'cell_origin', 'citation'))
        identity = item['observation_id']
        _pattern(identity, r'mny_[0-9a-f]{64}')
        _require(identity not in observed and item['source_id'] in sources)
        _require(item['kind'] in MONEY_KINDS)
        if item['measurement_basis'] != KIND_BASIS[item['kind']]:
            raise _Failure('MIXED_MEASUREMENT_BASIS')
        _cell_origin(item['cell_origin'])
        _citation(item['citation'], sources, item['source_id'])
        _require(identity == 'mny_' + hashlib.sha256(_encode(item['citation'])).hexdigest())
        _amount(item['amount'])
        observed[identity] = item
    return observed


def _validate(value, subject_id, as_of):
    _tree(value)
    _scope(subject_id, as_of)
    _object(value, {'contract_version', 'subject_id', 'as_of', 'adapter',
                    'sources', 'observations', 'issues', 'status'})
    _require(value['contract_version'] == CONTRACT_VERSION)
    _scope(value['subject_id'], value['as_of'])
    _require((value['subject_id'], value['as_of']) == (subject_id, as_of), 'SCOPE_MISMATCH')
    _object(value['adapter'], ('id', 'version'))
    _pattern(value['adapter']['id'], r'[a-z][a-z0-9_-]{0,63}')
    _pattern(value['adapter']['version'],
             r'(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})\.(0|[1-9][0-9]{0,5})')
    sources = _sources(value, subject_id, as_of)
    observed = _observations(value, sources)
    _issues(value['issues'], sources, observed)
    unknown = any(item['amount'] is None for item in value['observations'])
    _require(value['status'] in ('blocked', 'observed_unvalidated'))
    _require(value['status'] == ('blocked' if unknown else 'observed_unvalidated'))
    _require(len(_encode(value)) <= MAX_BYTES, 'INPUT_LIMIT_EXCEEDED')
    return deepcopy(value)


def validate_accounting_observations(value, *, subject_id, as_of):
    """Validate exact host scope and return a defensive JSON-native copy."""
    code = 'INVALID_OBSERVATIONS'
    try:
        return _validate(value, subject_id, as_of)
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise AccountingObservationError(code) from None


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


def loads_accounting_observations(bytes_or_str, *, subject_id, as_of):
    """Decode bounded UTF-8 JSON, reject duplicate keys, then validate."""
    code = 'INVALID_OBSERVATIONS'
    try:
        return _validate(_decode(bytes_or_str), subject_id, as_of)
    except _Failure as exc:
        code = exc.args[0]
    except Exception:
        pass
    raise AccountingObservationError(code) from None


def canonical_bytes(value, *, subject_id, as_of):
    """Validate before emitting compact sorted-key UTF-8 JSON (no newline)."""
    return _encode(validate_accounting_observations(
        value, subject_id=subject_id, as_of=as_of))
