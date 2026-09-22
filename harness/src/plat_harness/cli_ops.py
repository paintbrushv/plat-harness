"""Read-only ops CLI: ``plat-ops`` (Task 6.4). See docs/REPORTING.md.

A thin, local, read-only client over the Task 3.5 ``ops-review/1.0.0``
seam: exactly one property and one period per review — never a cross-
property or cross-period aggregate. The module performs **no financial
math**, no writes to the source database, and never implements a variance
formula of its own: variance is delegated entirely to a host-approved
oracle bound over the pinned ops owner ``boxscore::variance``
(``compute_account_variances`` + ``compute_noi_bridge``), supplied
explicitly via ``--variance-module``; without one the review is an honest
blocked review (``VARIANCE_NOT_IMPLEMENTED``), never a fabricated
variance.

Typed contract rules inherited from the seam:

- Missing budget is not zero budget; missing actuals block the review.
- Wildcard / aggregate asset tokens refuse ``INVALID_INPUT``.
- A missing ops backend is a typed refusal (``NOT_FOUND``), never a
  fabricated report.
- Blocked reviews still produce full watermarked deliverables through the
  Task 6.4 reporting module; a missing bridge metric renders ``null``,
  never zero.

stdout is a single machine-readable JSON record; typed refusals go to
stderr as JSON. Exit codes are the documented workflow outcomes: 0
complete, 2 needs review or data, 3 unsupported input, 4 execution error.
Report generation is never permission to publish. The module launches no
threads, opens no sockets, reads no stdin and imports no spreadsheet
writers.
"""
from __future__ import annotations

import argparse
import importlib
import json
import re
import sys
from pathlib import Path
from typing import Callable, NoReturn

from plat_harness import reporting
from plat_harness.adapters import ops_review
from plat_harness.errors import HarnessError

VERSION = 'cli-ops/1.0.0'
ORACLE_OWNER = 'boxscore::variance'
MAX_MODULE_TOKEN = 128

EXIT_CODES = {'complete': 0, 'needs_review_or_data': 2,
              'unsupported_input': 3, 'execution_error': 4}

_REFUSAL_EXIT = {
    'NOT_FOUND': 3, 'INVALID_INPUT': 3, 'INVALID_CONTRACT': 3,
    'AMBIGUOUS_PROPERTY': 3, 'SCOPE_MISMATCH': 3, 'UNSAFE_PATH': 3,
    'SNAPSHOT_REQUIRED': 3, 'INPUT_LIMIT_EXCEEDED': 3,
    'MISSING_INPUT': 3, 'UNSUPPORTED_COMMAND': 3, 'OUTPUT_COLLISION': 3,
    'INVALID_ORACLE_MODULE': 3,
}
_DEFAULT_EXIT = 4

_COMMANDS = frozenset({'review'})
_MODULE_TOKEN = re.compile(r'[A-Za-z0-9_.]{1,%d}\Z' % MAX_MODULE_TOKEN)
_MISSING_INPUT = ('A review requires --asset, --as-of, --db and --out; '
                  'exactly one property and one period.')
_UNSUPPORTED = 'plat-ops supports only the read-only review command.'


def _refuse(code: str, message: str, **details) -> NoReturn:
    raise HarnessError(code, message, details=details or None) from None


def _emit(record: dict) -> int:
    print(json.dumps(record, indent=2, sort_keys=True))
    return EXIT_CODES[record.get('outcome', 'execution_error')]


def _fail(exc: HarnessError) -> int:
    print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
    return _REFUSAL_EXIT.get(exc.code, _DEFAULT_EXIT)


# ---------------------------------------------------------------- oracle bind

def _load_oracle(module_token) -> 'Callable | None':
    """Bind a host-approved variance oracle module, or return None.

    The CLI never implements variance arithmetic; an explicit
    ``--variance-module`` names a module exposing the two pinned
    ``boxscore::variance`` functions. Loading is a plain import under a
    strict token; a module missing either function refuses.
    """
    if module_token in (None, ''):
        return None
    if not isinstance(module_token, str) or not _MODULE_TOKEN.fullmatch(
            module_token):
        _refuse('INVALID_ORACLE_MODULE',
                'The variance module token is not a plain module name.')
    module = importlib.import_module(module_token)
    missing = [name for name in ops_review.ORACLE_FUNCTIONS
               if not hasattr(module, name)]
    if missing:
        _refuse('INVALID_ORACLE_MODULE',
                'The variance module does not expose the pinned oracle '
                'functions.', missing=missing)

    def _oracle(actuals, budgets, _module=module):
        return {
            'by_account': list(_module.compute_account_variances(
                actuals, budgets)),
            'noi_bridge': dict(_module.compute_noi_bridge(
                actuals, budgets)),
        }

    return _oracle


# --------------------------------------------------------------------- review

def _review_record(result: dict) -> dict:
    """Adapt an ops-review result into a reporting tool record.

    The frozen seam already renders every bridge value as a closed money
    record; the adapter copies amounts verbatim and pins the citation to
    the oracle owner label. A metric with no oracle value stays ``None`` —
    missing is not zero.
    """
    metrics: dict = {}
    citations: dict = {}
    variance = result.get('variance') or {}
    bridge = variance.get('noi_bridge') or {}
    for name in sorted(ops_review.NOI_BRIDGE_KEYS):
        value = bridge.get(name)
        if isinstance(value, dict) and isinstance(value.get('amount'), str):
            metrics[name] = {
                'amount': value['amount'],
                'currency': value['currency'],
                'unit': value['unit'],
                'period': value['period'],
                'source': ORACLE_OWNER,
                'source_truncated': bool(value.get('source_truncated')),
            }
            citations[name] = ORACLE_OWNER
        else:
            metrics[name] = None
    blockers = [
        {'field': code, 'kind': 'pending_human_action',
         'message': ops_review.EXCEPTION_MESSAGES.get(code, '')}
        for code in result.get('blockers', [])
    ]
    facts = []
    for fact in result.get('explanation', {}).get('measured_facts', []):
        evidence = fact.get('evidence')
        if isinstance(evidence, str) and evidence:
            cited = [evidence]
        elif isinstance(evidence, list) and evidence:
            cited = [str(item) for item in evidence]
        else:
            cited = [ORACLE_OWNER]
        facts.append({'statement': fact['statement'], 'evidence': cited})
    return {
        'tool': 'plat-ops review',
        'tool_version': ops_review.CONTRACT_VERSION,
        'run_id': 'ops-review-%s-%s' % (result['asset_id'], result['period']),
        'subject': result['asset_id'],
        'period': result['period'],
        'metrics': metrics,
        'metrics_citations': citations,
        'blockers': blockers,
        'facts': facts,
    }


def _do_review(args: argparse.Namespace) -> int:
    for flag, value in (('--asset', args.asset), ('--as-of', args.as_of),
                        ('--db', args.db), ('--out', args.out)):
        if not value:
            _refuse('MISSING_INPUT', _MISSING_INPUT, flag=flag)
    db_path = Path(args.db)
    if not db_path.is_file():
        _refuse('NOT_FOUND',
                'The ops backend database was not found; a missing ops '
                'backend is a blocker, never a fabricated report.')
    oracle = None if args.no_variance else _load_oracle(args.variance_module)
    result = ops_review.review_period(
        asset_id=args.asset,
        period=args.as_of,
        materiality={'variance_abs': args.materiality, 'currency': 'USD'},
        as_of_date=None,
        db_path=str(db_path),
        variance_oracle=oracle,
    )
    outcome = ('complete' if result['status'] == 'reviewed'
               else 'needs_review_or_data')
    reporting.write_deliverables(_review_record(result), args.out)
    payload = dict(result)
    payload['outcome'] = outcome
    payload['cli_version'] = VERSION
    payload['publication_authorized'] = False
    return _emit(payload)


# ---------------------------------------------------------------------- main

def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='plat-ops', description='Read-only operations reviews.')
    sub = parser.add_subparsers(dest='command')
    review = sub.add_parser('review', help='One property, one period.')
    review.add_argument('--asset', default=None,
                        help='A single opaque asset identifier.')
    review.add_argument('--as-of', default=None,
                        help='Exactly one period in YYYY-MM form.')
    review.add_argument('--db', default=None,
                        help='Path to the quiesced ops SQLite snapshot.')
    review.add_argument('--materiality', default='500.00',
                        help='Positive decimal-string materiality bound.')
    review.add_argument('--out', default=None,
                        help='Fresh output directory for deliverables.')
    review.add_argument('--variance-module', default=None,
                        help='Host-approved module exposing the pinned '
                             'boxscore::variance oracle functions.')
    review.add_argument('--no-variance', action='store_true',
                        help='Explicitly skip the oracle; the review '
                             'honestly blocks variance instead.')
    return parser


def _dispatch(argv) -> int:
    argv_list = list(argv)
    if argv_list and not argv_list[0].startswith('-') \
            and argv_list[0] not in _COMMANDS:
        _refuse('UNSUPPORTED_COMMAND', _UNSUPPORTED,
                command=argv_list[0])
    args = _parser().parse_args(argv_list)
    if args.command not in _COMMANDS:
        _refuse('UNSUPPORTED_COMMAND', _UNSUPPORTED)
    return _do_review(args)


def main(argv=None) -> int:
    try:
        return _dispatch(sys.argv[1:] if argv is None else argv)
    except HarnessError as exc:
        return _fail(exc)


if __name__ == '__main__':  # pragma: no cover
    sys.exit(main())