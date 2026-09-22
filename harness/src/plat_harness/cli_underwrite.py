"""Unified underwriting CLI: ``plat-underwrite`` (Task 6.2).

A thin, resumable client over the Task 6.1 workflow state machine — never a
second orchestrator, never an engine, never a parser. Intake needs exactly two
pinned sources (``--om`` and ``--rr``); anything still missing is a typed
blocker, never an invented value: a missing amount stays null, never zero,
and no stage here is financial certification. The module performs no
financial math, reads no stdin, launches no threads or helper processes,
opens no sockets, and invokes no model, provider or engine. stdout is a
single machine-readable JSON record (or dry-run projection); typed refusals
go to stderr. Source text, paths beyond the pinned host path, credentials
and exception chains never appear in records, messages or captured output.

Exit codes are the documented workflow outcomes: 0 complete, 2 needs
review or data, 3 unsupported input, 4 execution error. A completed intake
stage and a completed forecast are different asked-for outcomes; asking for
``--stage normalized`` completes intake even when execution inputs remain
blocked. See docs/CLI_UNDERWRITE.md.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path
from typing import NoReturn

from plat_harness import workflow
from plat_harness.errors import HarnessError
from plat_harness.millage import parse_millage_rate

VERSION = 'cli-underwrite/1.0.0'
RUNS_DIR_NAME = 'plat-underwrite-runs'
FORMATS = frozenset(workflow.SUPPORTED_FORMATS)
SUFFIX_FORMATS = {'.pdf': 'pdf', '.xlsx': 'xlsx', '.csv': 'csv'}
STAGES = ('normalized', 'review_required', 'reconciled', 'canonical_ready')
ENGINE_STAGES = ('execution_authorized', 'engine_executed', 'report_ready')
MAX_DECISION_BYTES = 1024 * 1024
MAX_HORIZON_YEARS = 100

_MISSING_INPUT = 'Both --om and --rr are required to start a run.'
_INVALID = 'Invalid workflow request.'

# Typed refusal -> documented exit code. Refusals never print a record.
_REFUSAL_EXIT = {
    'MISSING_INPUT': 3, 'NOT_FOUND': 3, 'UNSUPPORTED_INPUT_FORMAT': 3,
    'INVALID_INPUT': 3, 'INVALID_PROVIDER': 3, 'UNSUPPORTED_STAGE': 3,
    'DUPLICATE_RUN': 3, 'OUTPUT_COLLISION': 3, 'INVALID_DECISION_FILE': 3,
    'INVALID_RESOLUTION': 3, 'INVALID_TRANSITION': 3, 'DUPLICATE_DECISION': 3,
    'CORRUPT_INPUT': 3, 'STALE': 3, 'STALE_APPROVAL': 3,
    'MISSING_MILLAGE': 2, 'MISSING_HORIZON': 2,
    'TRANSITION_BLOCKED': 2, 'EXECUTION_BLOCKED': 2,
}
_DEFAULT_EXIT = 4

_NEW_RUN_FLAGS = ('om', 'rr', 't12', 'debt', 'subject', 'out', 'stage',
                  'millage', 'horizon', 'provider', 'dry_run')


def _refuse(code: str, message: str, **details) -> NoReturn:
    raise HarnessError(code, message, details=details or None) from None


def _emit(record: dict) -> int:
    print(json.dumps(record, indent=2, sort_keys=True))
    return workflow.EXIT_CODES[record['outcome']]


def _fail(exc: HarnessError) -> int:
    print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
    return _REFUSAL_EXIT.get(exc.code, _DEFAULT_EXIT)


# ------------------------------------------------------------------ sources


def _absolute(raw: str) -> Path:
    path = Path(raw)
    return path if path.is_absolute() else Path.cwd() / path


def _source(name: str, raw: str) -> dict:
    path = _absolute(raw)
    if not path.is_file():
        _refuse('NOT_FOUND', 'Pinned source file not found.', input=name)
    fmt = SUFFIX_FORMATS.get(path.suffix.lower())
    if fmt is None or fmt not in FORMATS:
        _refuse('UNSUPPORTED_INPUT_FORMAT',
                'Source suffix is not a supported pinned format.', input=name)
    try:
        raw_bytes = path.read_bytes()
    except OSError:
        _refuse('NOT_FOUND', 'Pinned source file not found.', input=name)
    else:
        return {'sha256': hashlib.sha256(raw_bytes).hexdigest(),
                'path': str(path), 'format': fmt}
    raise AssertionError('unreachable')  # pragma: no cover


def _collect_inputs(args: argparse.Namespace) -> dict:
    if not args.om or not args.rr:
        _refuse('MISSING_INPUT', _MISSING_INPUT)
    inputs = {'om': _source('om', args.om), 'rr': _source('rr', args.rr)}
    if args.t12:
        inputs['t12'] = _source('t12', args.t12)
    if args.debt:
        inputs['debt'] = _source('debt', args.debt)
    return inputs


def _subject(args: argparse.Namespace, inputs: dict) -> str:
    if args.subject:
        return args.subject
    stem = Path(inputs['om']['path']).stem
    kept = ''.join(ch for ch in stem if ch.isalnum() or ch in '_.-')
    stripped = kept.lstrip('._-')
    return stripped if stripped else 'input'


def _validated_values(args: argparse.Namespace) -> dict:
    values = {}
    if args.millage:
        values['millage'] = str(parse_millage_rate(args.millage))
    if args.horizon:
        text = str(args.horizon).strip()
        if not text.isdigit() or not 1 <= int(text) <= MAX_HORIZON_YEARS:
            _refuse('MISSING_HORIZON',
                    'The underwriting horizon must be a positive whole '
                    'number of years.')
        values['horizon'] = text
    return values


def _predict_blockers(inputs: dict, values: dict) -> list:
    blockers = []
    for name in workflow.REQUIRED_INPUTS:
        if name not in inputs:
            blockers.append({'field': name, 'kind': 'pending_human_action',
                             'resolved_by': None})
    for name in workflow.DEFAULT_VALUES:
        if name not in values or values[name] is None:
            blockers.append({'field': name, 'kind': 'pending_human_action',
                             'resolved_by': None})
    return blockers


# -------------------------------------------------------------------- stages


def _advance_to(run_dir: Path, target: str) -> dict:
    workflow.advance(run_dir, 'normalized')
    if target == 'normalized':
        return workflow.resume(run_dir)
    if target == 'review_required':
        return workflow.advance(run_dir, 'review_required')
    return _advance_reconciled(run_dir, canonical=target == 'canonical_ready')


def _advance_reconciled(run_dir: Path, *, canonical: bool = False) -> dict:
    try:
        record = workflow.advance(run_dir, 'reconciled')
    except HarnessError as exc:
        if exc.code != 'TRANSITION_BLOCKED':
            raise
        return workflow.advance(run_dir, 'review_required')
    if canonical:
        record = workflow.advance(run_dir, 'canonical_ready')
    return record


def _apply_review(run_dir: Path, decision: dict) -> dict:
    workflow.record_review(run_dir, decision)
    try:
        record = workflow.advance(run_dir, 'reconciled')
    except HarnessError as exc:
        if exc.code != 'TRANSITION_BLOCKED':
            raise
        return workflow.resume(run_dir)
    return workflow.advance(run_dir, 'canonical_ready')


def _load_decision(raw: str) -> dict:
    path = _absolute(raw)
    if not path.is_file():
        _refuse('INVALID_DECISION_FILE',
                'Review decision file not found.')
    try:
        size = os.path.getsize(path)
        data = path.read_bytes()
    except OSError:
        _refuse('INVALID_DECISION_FILE',
                'Review decision file is not a readable decision object.')
    if size > MAX_DECISION_BYTES:
        _refuse('INVALID_DECISION_FILE',
                'Review decision file is not a readable decision object.')
    try:
        parsed = json.loads(data)
    except Exception:
        _refuse('INVALID_DECISION_FILE',
                'Review decision file is not a readable decision object.')
    if type(parsed) is not dict:
        _refuse('INVALID_DECISION_FILE',
                'Review decision file is not a readable decision object.')
    return parsed


# ----------------------------------------------------------------- run flows


def _new_run(args: argparse.Namespace) -> int:
    if args.review:
        _refuse('INVALID_INPUT',
                'Recording a review decision requires --resume <run-dir>.')
    inputs = _collect_inputs(args)
    subject = _subject(args, inputs)
    values = _validated_values(args)
    if args.stage in ENGINE_STAGES:
        _refuse('UNSUPPORTED_STAGE',
                'Engine stages are not CLI-requestable; execution authority '
                'lives in the separately reviewed envelope.',
                supported=list(STAGES))
    if args.stage not in STAGES:
        _refuse('UNSUPPORTED_STAGE', 'Requested stage is not a CLI stage.',
                supported=list(STAGES))
    if args.provider:
        _refuse('INVALID_PROVIDER',
                'No provider is approved in this offline default; provider '
                'routing requires separate host configuration. Refusing.')
    identity = workflow.content_identity(inputs, values)
    if args.dry_run:
        blockers = _predict_blockers(inputs, values)
        outcome = 'complete' if not blockers else 'needs_review_or_data'
        report = {'schema': VERSION, 'dry_run': True, 'subject': subject,
                  'content_identity': identity,
                  'inputs': {name: ref['format'] for name, ref
                             in sorted(inputs.items())},
                  'values': dict(values), 'blockers': blockers,
                  'outcome': outcome, 'certified': False}
        print(json.dumps(report, indent=2, sort_keys=True))
        return workflow.EXIT_CODES[outcome]
    if args.out:
        run_dir = _absolute(args.out)
    else:
        runs_root = Path.cwd() / RUNS_DIR_NAME
        os.makedirs(runs_root, mode=0o700, exist_ok=True)
        run_dir = runs_root / identity[:32]
    record = workflow.create_run(run_dir, subject, inputs,
                                 values or None)
    record = _advance_to(run_dir, args.stage)
    return _emit(record)


def _resume_run(args: argparse.Namespace) -> int:
    _DEFAULT_STAGE = 'canonical_ready'
    for flag in _NEW_RUN_FLAGS:
        value = getattr(args, flag, None)
        if flag == 'stage' and value == _DEFAULT_STAGE:
            continue
        if flag == 'dry_run' and not value:
            continue
        if value:
            _refuse('INVALID_INPUT',
                    'New-run flags cannot be combined with --resume.')
    run_dir = _absolute(args.resume)
    record = workflow.resume(run_dir)
    if args.review:
        decision = _load_decision(args.review)
        record = _apply_review(run_dir, decision)
    return _emit(record)


# ---------------------------------------------------------------------- main


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='plat-underwrite',
        description='Unified underwriting CLI: pinned intake, resumable '
                    'workflow, honest blockers. Local, draft-only, offline.')
    parser.add_argument('--version', action='version',
                        version=f'plat-underwrite {VERSION}')
    parser.add_argument('--resume', help='Existing run directory to resume')
    parser.add_argument('--review',
                        help='Host-reviewed decision file to record on resume')
    parser.add_argument('--om', help='Offering memorandum source (pinned)')
    parser.add_argument('--rr', help='Rent roll source (pinned)')
    parser.add_argument('--t12', help='T12 statement source (optional, pinned)')
    parser.add_argument('--debt', help='Debt schedule source (optional, pinned)')
    parser.add_argument('--subject', help='Run subject; derived from --om when omitted')
    parser.add_argument('--out', help='New run directory; default '
                                      '<cwd>/plat-underwrite-runs/<identity>')
    parser.add_argument('--stage', default='canonical_ready',
                        help='Requested completion stage: normalized, '
                             'review_required, reconciled or canonical_ready')
    parser.add_argument('--millage',
                        help='Mills per $1,000 of assessed value; never a default')
    parser.add_argument('--horizon', help='Whole number of years')
    parser.add_argument('--provider',
                        help='Canonical provider/model id; refused until '
                             'host-approved routing exists')
    parser.add_argument('--dry-run', action='store_true',
                        help='Project identity and blockers; no writes, no side effects')
    parser.add_argument('--non-interactive', action='store_true',
                        help='Never prompt; stdout stays machine-readable')
    parser.add_argument('--json', action='store_true',
                        help='Machine-readable JSON is the default output')
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        return _resume_run(args) if args.resume else _new_run(args)
    except HarnessError as exc:
        return _fail(exc)
    except Exception:
        _refuse('EXECUTION_ERROR',
                'CLI failed unexpectedly; no source content is included '
                'in this message.')


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())