"""Deterministic underwriting report rendering (Task 6.4). See docs/REPORTING.md.

Renders a frozen tool record (engine or ops output) into three deterministic
deliverables: canonical JSON, a cited Markdown summary, and a formula-
injection-safe CSV export. The module is a pure renderer: it performs **no
financial math** — every number is copied verbatim from the source record as
a decimal string inside its money record (currency, unit, period, source),
a missing amount stays ``null`` — never zero — and no value is ever
recomputed, summed or reformatted.

Fail-closed contract rules:

- Uncited metrics refuse ``CITATION_REQUIRED`` — no orphan numbers ship.
- Unknown top-level payload keys refuse ``INVALID_CONTRACT``; the record
  shape is closed, so hostile payload content cannot ride along.
- Floats and non-decimal-text amounts refuse ``INVALID_INPUT``; money is
  never a float anywhere in a rendered report.
- A blocked run (any blocker on the record) renders with a visible
  ``WATERMARK`` on every deliverable; the watermark disappears only when
  the record carries no blockers — never by argument.
- Spreadsheet formula injection (leading ``=``, ``+``, ``-``, ``@``, tab,
  CR) in any rendered cell refuses ``INVALID_INPUT``; the source record is
  untrusted text and is never echoed into a cell context un-neutralized.
- Report generation is never permission to publish: every payload records
  ``publication_authorized: false`` and ``certified: false``.

Rendering is byte-deterministic: identical inputs produce byte-identical
outputs, and the JSON payload pins ``source_record_sha256`` so every
deliverable is reproducible against the exact source record. Canary
strings never surface: facts are copied verbatim from the record and the
record itself is contract-closed. The module imports nothing beyond the
stdlib and typed errors — no adapters, no ingest, no engine, no network,
no threads, no spreadsheet writers.
"""
from __future__ import annotations

import csv
import io
import json
import re
from typing import NoReturn

from plat_harness.errors import HarnessError

VERSION = 'reporting/1.0.0'
WATERMARK = '<<BLOCKED: results are not complete; see blockers>>'

ERROR_CODES = frozenset({
    'INVALID_INPUT', 'INVALID_CONTRACT', 'CITATION_REQUIRED', 'NOT_FOUND',
})

SOURCE_KEYS = frozenset({
    'tool', 'tool_version', 'run_id', 'subject', 'period', 'metrics',
    'metrics_citations', 'blockers', 'facts',
})
MONEY_KEYS = frozenset({
    'amount', 'currency', 'unit', 'period', 'source', 'source_truncated',
})
BLOCKER_KEYS = frozenset({'field', 'kind', 'message'})
FACT_KEYS = frozenset({'statement', 'evidence'})

_DECIMAL = re.compile(r'-?(0|[1-9][0-9]{0,15})(\.[0-9]{1,8})?\Z')
_SUBJECT = re.compile(r'[A-Za-z0-9][A-Za-z0-9_.\- ]{0,119}\Z')
# Spreadsheet formula-injection prefixes (CSV/Excel dangerous first bytes).
_INJECTION = ('=', '+', '@', '\t', '\r')


def _refuse(code: str, message: str, **details) -> NoReturn:
    raise HarnessError(code, message, details=details or None) from None


# --------------------------------------------------------------- input gates

def _clean(value: str) -> str:
    """A rendered cell must not start with a formula-injection byte."""
    if value.startswith(_INJECTION):
        _refuse('INVALID_INPUT',
                'A rendered cell would begin with a spreadsheet formula '
                'operator; source text is untrusted and refuses to render.')
    return value


def _money(name: str, value) -> 'dict | None':
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != MONEY_KEYS:
        _refuse('INVALID_INPUT',
                'Metric values must be money records with a closed shape.',
                field=name)
    amount = value['amount']
    if not isinstance(amount, str) or not _DECIMAL.fullmatch(amount):
        _refuse('INVALID_INPUT',
                'Metric amounts must be decimal strings; floats refuse.',
                field=name)
    for key in ('currency', 'unit', 'period'):
        if not isinstance(value[key], str) or not value[key]:
            _refuse('INVALID_INPUT',
                    'Money records must carry currency, unit and period.',
                    field=name)
    _clean(value['source'])
    return dict(value)


def _validate(record) -> dict:
    if not isinstance(record, dict):
        _refuse('INVALID_INPUT', 'A tool record object is required.')
    unknown = set(record) - SOURCE_KEYS
    if unknown:
        _refuse('INVALID_CONTRACT',
                'The tool record shape is closed; unknown keys refuse.',
                keys=sorted(unknown))
    for key in ('tool', 'tool_version', 'run_id', 'subject', 'period'):
        if not isinstance(record.get(key), str) or not record[key]:
            _refuse('INVALID_INPUT',
                    'The tool record must identify its tool, run, subject '
                    'and period.', field=key)
    if not _SUBJECT.fullmatch(record['subject']):
        _refuse('INVALID_INPUT',
                'The subject identifier is not a renderable label.')
    metrics = record.get('metrics')
    citations = record.get('metrics_citations')
    blockers = record.get('blockers')
    facts = record.get('facts')
    if not isinstance(metrics, dict) or not isinstance(citations, dict):
        _refuse('INVALID_INPUT',
                'The tool record must carry a metrics map and a citations '
                'map.')
    if not isinstance(blockers, list) or not isinstance(facts, list):
        _refuse('INVALID_INPUT',
                'The tool record must carry blocker and fact lists.')
    for name, value in metrics.items():
        if citations.get(name) in (None, ''):
            if value is None:
                continue  # a blocked metric needs no citation
            _refuse('CITATION_REQUIRED',
                    'Every rendered metric must carry a resolvable citation.',
                    field=name)
    for name in citations:
        if name not in metrics or metrics[name] is None:
            _refuse('CITATION_REQUIRED',
                    'A citation exists for a metric that is absent or '
                    'blocked.', field=name)
    return record


def _blocked(record: dict) -> bool:
    return bool(record['blockers']) or any(
        value is None for value in record['metrics'].values())


def _pin(record: dict) -> str:
    import hashlib
    return hashlib.sha256(json.dumps(
        record, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


# ------------------------------------------------------------------ renderers

def _ordered_metrics(record: dict) -> list:
    return sorted(record['metrics'].items())


def render_json_report(record) -> str:
    """Canonical deterministic JSON report pinned to the source record."""
    validated = _validate(record)
    payload = {
        'contract_version': VERSION,
        'tool': validated['tool'],
        'tool_version': validated['tool_version'],
        'run_id': validated['run_id'],
        'subject': validated['subject'],
        'period': validated['period'],
        'source_record_sha256': _pin(validated),
        'watermark': WATERMARK if _blocked(validated) else None,
        'publication_authorized': False,
        'certified': False,
        'metrics': {
            name: (None if _money(name, value) is None
                   else _money(name, value))
            for name, value in _ordered_metrics(validated)
        },
        'metrics_citations': {
            name: validated['metrics_citations'][name]
            for name, value in _ordered_metrics(validated)
            if value is not None
        },
        'blockers': [
            {key: item.get(key) for key in ('field', 'kind', 'message')}
            for item in validated['blockers']
        ],
        'facts': [
            {'statement': fact['statement'], 'evidence': list(
                fact['evidence'])}
            for fact in validated['facts']
            if _fact_ok(fact)
        ],
    }
    return json.dumps(payload, indent=2, sort_keys=True)


def _fact_ok(fact) -> bool:
    if not isinstance(fact, dict) or set(fact) != FACT_KEYS:
        _refuse('INVALID_INPUT',
                'Facts must be statement/evidence records.')
    if not isinstance(fact['statement'], str) or not fact['statement']:
        _refuse('INVALID_INPUT', 'A fact statement is required.')
    if (not isinstance(fact['evidence'], list)
            or not fact['evidence']
            or any(not isinstance(item, str) or not item
                   for item in fact['evidence'])):
        _refuse('CITATION_REQUIRED',
                'Every fact must cite its evidence.')
    return True


def _watermark_line(record: dict) -> str:
    return WATERMARK if _blocked(record) else ''


def render_markdown_summary(record) -> str:
    """Cited Markdown summary; blocked records render watermarked."""
    validated = _validate(record)
    _money_all(validated)  # validate money shapes before rendering text
    lines = [
        f'# Underwriting report: {_clean(validated["subject"])}',
        f'Period: {_clean(validated["period"])}',
        f'Source tool: {_clean(validated["tool"])} '
        f'({_clean(validated["tool_version"])})',
        f'Run: {_clean(validated["run_id"])}',
        '',
    ]
    mark = _watermark_line(validated)
    if mark:
        lines += [mark, '']
    lines.append('## Metrics')
    for name, value in _ordered_metrics(validated):
        if value is None:
            lines.append(f'- {name}: blocked (no value; missing is not zero)')
            continue
        citation = _clean(validated['metrics_citations'][name])
        lines.append(f'- {name}: {_clean(value["amount"])} '
                     f'{_clean(value["currency"])} '
                     f'(source: {citation})')
    if validated['blockers']:
        lines += ['', '## Blockers']
        for item in validated['blockers']:
            field = item.get('field') or ''
            message = item.get('message') or ''
            lines.append(f'- {_clean(str(field))}: {_clean(str(message))}')
    if validated['facts']:
        lines += ['', '## Measured facts']
        for fact in validated['facts']:
            _fact_ok(fact)
            evidence = ', '.join(_clean(item) for item in fact['evidence'])
            lines.append(f'- {_clean(fact["statement"])} (evidence: '
                         f'{evidence})')
    lines += ['', 'Report generation is not permission to publish.']
    return '\n'.join(lines) + '\n'


def _money_all(record: dict) -> None:
    for name, value in record['metrics'].items():
        if value is not None:
            _money(name, value)


_CSV_HEADER = ('subject', 'period', 'metric', 'amount', 'currency',
               'citation', 'watermark')


def render_csv_export(record) -> str:
    """Formula-injection-safe CSV row export of every cited metric."""
    validated = _validate(record)
    mark = _watermark_line(validated)
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator='\n')
    writer.writerow(_CSV_HEADER)
    for name, value in _ordered_metrics(validated):
        if value is None:
            writer.writerow([
                _clean(validated['subject']), _clean(validated['period']),
                _clean(name), '', '', '', mark])
            continue
        money = _money(name, value)
        assert money is not None  # value is not None here
        writer.writerow([
            _clean(validated['subject']), _clean(validated['period']),
            _clean(name), money['amount'], money['currency'],
            _clean(validated['metrics_citations'][name]), mark])
    return buffer.getvalue()


def write_deliverables(record, out_dir) -> dict:
    """Render all three deterministic deliverables into a fresh directory."""
    import os
    from pathlib import Path

    out = Path(out_dir)
    if out.exists() and any(out.iterdir()):
        _refuse('OUTPUT_COLLISION',
                'The output directory already holds deliverables.',
                path=str(out))
    out.mkdir(parents=True, exist_ok=True, mode=0o700)
    payloads = {
        'report.json': render_json_report(record),
        'summary.md': render_markdown_summary(record),
        'metrics.csv': render_csv_export(record),
    }
    for name, text in payloads.items():
        target = out / name
        if target.exists():
            _refuse('OUTPUT_COLLISION',
                    'The output directory already holds deliverables.',
                    path=str(target))
        with open(target, 'w', encoding='utf-8', newline='') as handle:
            handle.write(text)
        os.chmod(target, 0o600)
    return {'files': sorted(payloads),
            'source_record_sha256': _pin(_validate(record))}