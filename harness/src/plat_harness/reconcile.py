"""Human reconciliation UX: ``reconcile/1.0.0`` (Task 6.3).

Concise, trustworthy prompts for the fields a human must reconcile on a run.
Every blocked field produces exactly one question per review, batched
upfront: field, source evidence, consequence, valid options and an explicit
``defer`` action. Prompt responses are *not* authority: an answer typed at a
prompt is an operator convenience, never an authenticated human approval, and
no prompt path can self-authorize execution — execution authority lives in
the separately reviewed envelope, unchanged.

The module is a thin layer over the Task 6.1 ``workflow/1.0.0`` journal: it
reads the public record, validates answers with the existing millage gate,
and records decisions through ``workflow.record_review``. Interactive and
noninteractive reviews produce the same content-bound decision document
(``reconcile-decision/1.0.0``); EOF or ``defer`` leaves a recoverable draft
with no journal event and no hidden economic default — a missing amount stays
``null``, never zero. It performs no financial math beyond delegation to the
frozen millage parser, reads no source bytes, launches no threads, and never
invokes the engine. See docs/RECONCILE.md.
"""
from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from typing import Iterator, NoReturn

from plat_harness import workflow
from plat_harness.errors import HarnessError
from plat_harness.millage import parse_millage_rate

VERSION = 'reconcile/1.0.0'
DECISION_SCHEMA = 'reconcile-decision/1.0.0'

# Canonical wording retained verbatim; the example is formatting guidance
# only, never a default for a live deal.
MILLAGE_PROMPT = (
    'What combined property-tax millage should be used? Enter mills '
    'per $1,000 of assessed value (for example, `25.31`).'
)
DEFER_TOKEN = 'defer'
REVIEWER = 'host_reviewer'
MAX_HORIZON_YEARS = 100
MAX_ANSWER_BYTES = 512

# Interactive answers may resolve only blocked scalar default values; a
# missing source document needs a pinned file, never a typed string.
_PROMPTABLE = frozenset(workflow.DEFAULT_VALUES)
_SOURCE_DOC_HINTS = {
    't12': 'trailing-twelve-months financial statement',
    'debt': 'debt schedule',
}

_INVALID = 'Invalid reconciliation request.'
_MILLAGE_TEXT = ('The submitted property-tax millage is not a positive '
                 'number of mills per $1,000.')
_HORIZON_TEXT = ('The underwriting horizon must be a positive whole '
                 'number of years.')


def _refuse(code: str, message: str, **details) -> NoReturn:
    raise HarnessError(code, message, details=details or None) from None


# ------------------------------------------------------------------ planning


def _blocked_fields(record: dict) -> list:
    blocked = []
    for item in record['blockers']:
        if item.get('kind') == 'pending_human_action' and item.get(
                'resolved_by') is None:
            blocked.append(item['field'])
    return blocked


def plan_questions(run_dir) -> list:
    """One question per unresolved blocked field, batched, in stable order."""
    record = workflow.resume(_run_dir(run_dir))
    if record['stage'] not in ('review_required', 'normalized', 'inspected'):
        _refuse('INVALID_TRANSITION',
                'Requested stage transition is not allowed.',
                stage=record['stage'])
    questions = []
    for field in _blocked_fields(record):
        if field in _PROMPTABLE:
            if field == 'millage':
                prompt = MILLAGE_PROMPT
                evidence = ('missing value; required by owner tax policy')
                consequence = ('Without millage the run cannot proceed to '
                               'canonical intake; property tax remains '
                               'unset, never zero.')
                options = ['mills per $1,000 as a positive decimal '
                           '(for example, `25.31`)', DEFER_TOKEN]
            else:
                prompt = ('What underwriting horizon should be used? Enter a '
                          'whole number of years.')
                evidence = 'missing value; required by owner policy'
                consequence = ('Without a horizon the run cannot proceed to '
                               'canonical intake; projections remain unset.')
                options = ['whole number of years (1-%d)'
                           % MAX_HORIZON_YEARS, DEFER_TOKEN]
        else:
            hint = _SOURCE_DOC_HINTS.get(field, 'source document')
            prompt = ('Provide a pinned %s source to unblock field %r.'
                      % (hint, field))
            evidence = ('missing source document; expected a pinned '
                       'trailing-twelve-months statement'
                       if field == 't12' else
                       'missing source document; expected a pinned %s'
                       % hint)
            consequence = ('Without the %s document the run cannot reach '
                           'canonical intake; the field stays blocked.' % field)
            options = [DEFER_TOKEN]
        questions.append({'field': field, 'prompt': prompt,
                          'evidence': evidence, 'consequence': consequence,
                          'options': options, 'defer': DEFER_TOKEN})
    return questions


# ------------------------------------------------------------- answer parsing


def _normalized(answer) -> str:
    if isinstance(answer, str):
        return answer.strip()
    _refuse('INVALID_INPUT', _INVALID)


def _validated(field: str, answer: str) -> str | None:
    if answer == DEFER_TOKEN:
        return None
    if field == 'millage':
        try:
            parsed = parse_millage_rate(answer)
        except HarnessError:
            _refuse('MISSING_MILLAGE', _MILLAGE_TEXT)
        return str(parsed)
    if field == 'horizon':
        if not answer.isdigit() or not 1 <= int(answer) <= MAX_HORIZON_YEARS:
            _refuse('MISSING_HORIZON', _HORIZON_TEXT)
        return str(int(answer))
    _refuse('INVALID_RESOLUTION', 'Prompt answers cannot resolve that field.',
            field=field)


def _answers(fields: list, answers: dict) -> dict:
    if type(answers) is not dict:
        _refuse('INVALID_INPUT', _INVALID)
    resolutions = {}
    for field, answer in answers.items():
        if type(field) is not str or not field:
            _refuse('INVALID_INPUT', _INVALID)
        raw = json.dumps(answer).encode('utf-8')
        if len(raw) > MAX_ANSWER_BYTES:
            _refuse('INVALID_INPUT', _INVALID)
        normalized = _normalized(answer)
        if field not in _PROMPTABLE:
            _refuse('INVALID_RESOLUTION',
                    'Prompt answers cannot resolve that field.', field=field)
        value = _validated(field, normalized)
        if value is not None:
            resolutions[field] = {'value': value, 'evidence': 'host-review'}
    return resolutions


# ------------------------------------------------------------ decision schema


def decision_document(run_dir, resolutions: dict, *, decision_id=None,
                      reviewer=REVIEWER) -> dict:
    """Content-bound decision document; interactive == noninteractive."""
    record = workflow.resume(_run_dir(run_dir))
    if type(resolutions) is not dict:
        _refuse('INVALID_INPUT', _INVALID)
    checked = {}
    for field, item in resolutions.items():
        if (type(field) is not str or type(item) is not dict
                or set(item) != {'value', 'evidence'}):
            _refuse('INVALID_INPUT', _INVALID)
        for key in ('value', 'evidence'):
            if type(item[key]) is not str or not item[key]:
                _refuse('INVALID_INPUT', _INVALID)
        checked[field] = {'value': item['value'], 'evidence': item['evidence']}
    payload = {'schema': DECISION_SCHEMA, 'run_id': record['run_id'],
               'content_identity': record['content_identity'],
               'resolutions': {field: checked[field]
                               for field in sorted(checked)}}
    content_sha256 = hashlib.sha256(_enc(payload)).hexdigest()
    if decision_id is None:
        decision_id = 'dec_' + content_sha256[:32]
    if type(decision_id) is not str or not decision_id:
        _refuse('INVALID_INPUT', _INVALID)
    if type(reviewer) is not str or not reviewer:
        _refuse('INVALID_INPUT', _INVALID)
    return {'schema': DECISION_SCHEMA,
            'decision_id': decision_id,
            'status': 'approved',
            'reviewer': reviewer,
            'content_sha256': content_sha256,
            'resolutions': {field: dict(checked[field])
                            for field in sorted(checked)},
            'certified': False,
            'authorizes_execution': False}


def _enc(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False).encode('utf-8')


# ------------------------------------------------------------------- reviews


def review_interactive(run_dir, stdin: Iterator[str], answers: dict) -> dict:
    """Scripted-stdin review; EOF or defer leaves a recoverable draft."""
    run_dir = _run_dir(run_dir)
    record = workflow.resume(run_dir)
    questions = plan_questions(run_dir)
    merged = dict(answers) if type(answers) is dict else {}
    for question in questions:
        field = question['field']
        if field in merged:
            continue
        try:
            line = next(stdin)
        except StopIteration:
            break  # EOF: keep the draft; no journal event, no defaults
        if isinstance(line, str):
            merged[field] = line
        else:
            _refuse('INVALID_INPUT', _INVALID)
    resolutions = _answers([q['field'] for q in questions], merged)
    if not resolutions:
        return {'decision': None, 'record': record}
    decision = decision_document(run_dir, resolutions)
    return {'decision': decision,
            'record': _record_decision(run_dir, decision)}


def review_noninteractive(run_dir, decision: dict) -> dict:
    """Record a host-reviewed decision file; identical content schema."""
    run_dir = _run_dir(run_dir)
    if type(decision) is not dict or set(decision) not in (
            {'decision_id', 'resolutions'}, {'decision_id', 'reviewer',
                                             'resolutions'}):
        _refuse('INVALID_INPUT', _INVALID)
    current = workflow.resume(run_dir)
    _check_conflicts(current, decision['resolutions'])
    document = decision_document(run_dir, decision['resolutions'],
                                 decision_id=decision['decision_id'],
                                 reviewer=decision.get('reviewer', REVIEWER))
    return {'decision': document,
            'record': _record_decision(run_dir, document)}


def _check_conflicts(record: dict, resolutions: dict) -> None:
    """A second decision disagreeing with a resolved value never resolves it."""
    for field, item in resolutions.items():
        if not isinstance(item, dict) or field not in record['resolutions']:
            continue
        existing = record['resolutions'][field]
        if existing.get('value') != item.get('value'):
            _refuse('CONFLICT_UNRESOLVED',
                    'Conflicting evidence for a resolved field; the field '
                    'stays blocked at its first recorded value.', field=field)


def _record_decision(run_dir: Path, document: dict) -> dict:
    record = workflow.record_review(run_dir, {
        'decision_id': document['decision_id'],
        'status': document['status'],
        'reviewer': document['reviewer'],
        'resolutions': {field: {'value': item['value'],
                                 'evidence': item['evidence']}
                        for field, item in document['resolutions'].items()}})
    try:
        record = workflow.advance(run_dir, 'reconciled')
        record = workflow.advance(run_dir, 'canonical_ready')
    except HarnessError as exc:
        if exc.code != 'TRANSITION_BLOCKED':
            raise
    return record


# ------------------------------------------------------------------- helpers


def _run_dir(run_dir) -> Path:
    if type(run_dir) is str:
        run_dir = Path(run_dir)
    if not isinstance(run_dir, Path):
        _refuse('INVALID_INPUT', _INVALID)
    return run_dir