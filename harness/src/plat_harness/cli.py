"""Operator API. Replaces vendor slash commands. No vendor agent runtime."""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from plat_harness import __version__
from plat_harness.errors import (
    HarnessError,
    NO_MODEL_CONFIGURED,
    NOT_FOUND,
    OCCUPANCY_COUNTS_REQUIRED,
    UNCERTIFIED_METRIC,
)
from plat_harness.millage import PROPERTY_TAX_MILLAGE_QUESTION, parse_millage_rate
from plat_harness.models import NullModel
from plat_harness.ranks import PermissionRank
from plat_harness.tools.certified_metric import get_certified_metric
from plat_harness.tools.stubs import call_stub

_SCOREBOARD_FIELDS = (
    "NOI",
    "physical_occupancy (occupied/vacant/down + denominator)",
    "loss_to_lease",
    "DSCR",
    "cash_on_cash",
    "budget variance",
    "UW vs actual",
    "freshness",
    "mapping %",
)


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "ask":
            return _cmd_ask(args)
        if args.command == "scoreboard":
            return _cmd_scoreboard(args)
        if args.command == "underwrite":
            return _cmd_underwrite(args)
        parser.print_help()
        return 2
    except HarnessError as exc:
        print(json.dumps(exc.as_dict(), indent=2), file=sys.stderr)
        return 2


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="plat-harness",
        description="Harness CLI. Agent-agnostic. The model is rented.",
    )
    parser.add_argument("--version", action="version", version=f"plat-harness {__version__}")
    sub = parser.add_subparsers(dest="command")

    ask = sub.add_parser("ask", help="Rank-0 Q&A (NullModel until a rented model is configured)")
    ask.add_argument("question", nargs="?", default="", help="Free-text question (requires a rented model)")
    ask.add_argument("--metric", help="Certified metric id (glossary)")
    ask.add_argument("--context", help="Glossary context tag (required for CONFLICT metrics)")
    ask.add_argument("--period", help="As-of period")
    ask.add_argument("--asset", dest="asset_or_deal_id")
    ask.add_argument("--deal", dest="asset_or_deal_id")
    ask.add_argument("--millage-rate", dest="millage_rate")
    ask.add_argument("--occupied", type=int)
    ask.add_argument("--vacant", type=int)
    ask.add_argument("--down", type=int)
    ask.add_argument("--denominator", type=int)
    ask.add_argument("--rank", type=int, default=0, choices=(0, 1, 2, 3))

    board = sub.add_parser("scoreboard", help="Deterministic certified board")
    board.add_argument("--asset")
    board.add_argument("--deal")

    uw = sub.add_parser(
        "underwrite",
        help="Deal path. Millage required. Engine extra required to compute CoC.",
    )
    uw.add_argument("--millage-rate", dest="millage_rate")
    uw.add_argument("--deal")
    uw.add_argument("--rank", type=int, default=2, choices=(0, 1, 2, 3))
    return parser


def _cmd_ask(args: argparse.Namespace) -> int:
    rank = PermissionRank(args.rank)
    if args.metric:
        result = get_certified_metric(
            args.metric,
            context=args.context,
            period=args.period,
            asset_or_deal_id=args.asset_or_deal_id,
            millage_rate_mills=args.millage_rate,
            occupied=args.occupied,
            vacant=args.vacant,
            down=args.down,
            denominator=args.denominator,
            session_rank=rank,
        )
        print(json.dumps(result, indent=2, default=str))
        return 0
    model = NullModel()
    try:
        model.complete(
            [{"role": "user", "content": args.question or "(empty)"}],
            tools=[],
        )
    except HarnessError as exc:
        if exc.code != NO_MODEL_CONFIGURED:
            raise
        payload: dict[str, Any] = {
            "error": exc.code,
            "message": exc.message,
            "rank": int(rank),
            "model": "null",
        }
        if args.question:
            payload["question"] = args.question
        print(json.dumps(payload, indent=2))
        return 2
    return 0


def _cmd_scoreboard(args: argparse.Namespace) -> int:
    subject = args.asset or args.deal
    if not subject:
        print(
            json.dumps(
                {
                    "error": "NOT_FOUND",
                    "message": "Pass --asset <id> or --deal <slug>. Scoreboard is one subject, not the portfolio.",
                },
                indent=2,
            ),
            file=sys.stderr,
        )
        return 2
    metrics: dict[str, Any] = {}
    gaps: list[dict[str, Any]] = []
    if args.asset:
        _scoreboard_try(
            metrics,
            gaps,
            "physical_occupancy",
            context="ops_actuals",
            asset_or_deal_id=args.asset,
        )
        _scoreboard_try(
            metrics,
            gaps,
            "t12_repairs_and_maintenance",
            context="ops_actuals",
            asset_or_deal_id=args.asset,
        )
    if args.deal:
        _scoreboard_try(
            metrics,
            gaps,
            "cash_on_cash",
            context="uw_proforma",
            asset_or_deal_id=args.deal,
        )
    status = "certified_partial" if metrics else "uncertified_empty"
    print(
        json.dumps(
            {
                "subject": subject,
                "kind": "asset" if args.asset else "deal",
                "status": status,
                "slice": 1 if metrics else 0,
                "required_fields": list(_SCOREBOARD_FIELDS),
                "metrics": metrics,
                "gaps": gaps,
                "note": (
                    "Certified numbers come from configured ops/engine adapters. "
                    "Unwired fields stay empty. IRR/DSCR/EM/cap are not invented. "
                    "Board figures come from synthetic samples or your env-mounted data only."
                ),
            },
            indent=2,
            default=str,
        )
    )
    return 0


def _scoreboard_try(
    metrics: dict[str, Any],
    gaps: list[dict[str, Any]],
    metric_id: str,
    *,
    context: str,
    asset_or_deal_id: str,
) -> None:
    try:
        metrics[metric_id] = get_certified_metric(
            metric_id,
            context=context,
            asset_or_deal_id=asset_or_deal_id,
        )
    except HarnessError as exc:
        if exc.code in {OCCUPANCY_COUNTS_REQUIRED, UNCERTIFIED_METRIC, NOT_FOUND}:
            gaps.append(exc.as_dict())
            return
        raise


def _cmd_underwrite(args: argparse.Namespace) -> int:
    parse_millage_rate(args.millage_rate)
    rank = PermissionRank(args.rank)
    try:
        call_stub(
            "run_underwriting_model",
            rank,
            millage_rate_mills=args.millage_rate,
        )
    except HarnessError as exc:
        payload = exc.as_dict()
        payload["millage_rate_mills"] = str(parse_millage_rate(args.millage_rate))
        payload["deal"] = args.deal
        payload["source_locator"] = "plat-harness:property_tax_millage"
        payload["millage_question"] = PROPERTY_TAX_MILLAGE_QUESTION
        print(json.dumps(payload, indent=2))
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
