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
        if args.command == "local-ask":
            return _cmd_local_ask(args)
        if args.command == "readiness":
            from plat_harness.readiness import explain, render_markdown
            result = explain(args.bundle, args.index_sha256, args.deal,
                             policy_path=args.policy, policy_sha256=args.policy_sha256)
            print(render_markdown(result) if args.format == "markdown" else json.dumps(result, indent=2))
            return 2  # Explanation never grants underwriting/certification authority.
        if args.command == "gate-evidence":
            from plat_harness.gate_evidence import compare, render_markdown
            result = compare(args.request, args.request_sha256)
            print(render_markdown(result) if args.format == "markdown" else json.dumps(result, indent=2))
            return 2  # Evidence changes are not approval or live eligibility.
        if args.command == "scoreboard":
            return _cmd_scoreboard(args)
        if args.command == "underwrite":
            return _cmd_underwrite(args)
        if args.command == "synthetic-underwrite":
            return _cmd_synthetic_underwrite(args)
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

    local = sub.add_parser("local-ask", help="Bounded read-only local model loop; answers/evidence saved privately")
    local.add_argument("question")
    local.add_argument("--endpoint", required=True, help="Explicit http://127.0.0.1:PORT origin")
    local.add_argument("--model-id", required=True, help="Exact model ID from /v1/models")
    local.add_argument("--asset", action="append", required=True, help="Host-approved subject (maximum five)")
    local.add_argument("--approved-root", action="append", required=True)
    local.add_argument("--run-dir", required=True, help="New private directory inside an approved root")
    local.add_argument("--ops-root", help="Explicit read-only Standardized feed root")
    local.add_argument("--boxscore-db", help="Explicit standalone read-only SQLite snapshot")
    local.add_argument("--rank", type=int, default=1, choices=(0, 1, 2))
    local.add_argument("--max-steps", type=int, default=4)
    local.add_argument("--max-calls", type=int, default=2)
    local.add_argument("--model-timeout", type=float, default=45)
    local.add_argument("--tool-timeout", type=float, default=5)
    local.add_argument("--total-timeout", type=float, default=100)
    local.add_argument("--max-tokens", type=int, default=512)

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

    ready = sub.add_parser("readiness", help="Read-only, hash-bound historical blocker explanation; never executes underwriting", allow_abbrev=False)
    ready.add_argument("--bundle", required=True, help="Absolute private closed evidence bundle")
    ready.add_argument("--index-sha256", required=True, help="Literal SHA256 of the reviewed artifact index (not approval)")
    ready.add_argument("--deal", required=True, help="Exact subject from the bounded snapshot")
    ready.add_argument("--format", choices=("json", "markdown"), default="json")
    ready.add_argument("--policy", help="Optional exact private policy-pack/1.0.0; never grants execution")
    ready.add_argument("--policy-sha256", help="Mandatory pin if --policy supplied; authority uses existing host registry")

    evidence = sub.add_parser("gate-evidence", help="Compare exact pinned decision evidence and draft a handoff; read-only", allow_abbrev=False)
    evidence.add_argument("--request", required=True, help="Absolute private gate-evidence-request/1.0.0 JSON")
    evidence.add_argument("--request-sha256", required=True, help="Literal SHA256; byte pin is not approval")
    evidence.add_argument("--format", choices=("json", "markdown"), default="json")

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

    synthetic = sub.add_parser(
        "synthetic-underwrite",
        help="Exact host-reviewed synthetic Slice B draft; never financial certification",
        description=(
            "Read a strict private JSON object containing exactly subject_id, run_id, "
            "input_sha256, policy_version, inputs_path, approval_path, policy_path, "
            "t12_path, broker_path and classification_path (all strings). "
            "Host configuration must independently set PLAT_HARNESS_ENGINE_ROOT, "
            "PLAT_HARNESS_SLICE_B_ROOT and PLAT_HARNESS_SLICE_B_APPROVAL_SHA256. "
            "No environment/rank/approval registry is accepted from the request. "
            "Versioned contracts require an explicit slice-b-execution/1.0.0 approval "
            "and a separately pinned host contracts registry. "
            "Persisted draft manifests go to stdout; preflight refusals to stderr. "
            "Exit 2 means uncertified/blocked, even when the engine child exits 0."
        ),
        allow_abbrev=False,
    )
    synthetic.add_argument("--request", required=True, help="Absolute private reviewed-request JSON path")
    synthetic.add_argument("--rank", type=int, default=2, help="Host session rank; only draft rank 2 is allowed")
    return parser


def _cmd_local_ask(args: argparse.Namespace) -> int:
    from pathlib import Path
    from plat_harness.local_model import LocalModel
    from plat_harness.tool_loop import LoopConfig, OpsMetricExecutor, run_question

    roots = tuple(Path(root) for root in args.approved_root)
    config = LoopConfig(roots, tuple(args.asset), Path(args.run_dir), rank=args.rank,
                        max_steps=args.max_steps, max_calls=args.max_calls,
                        model_timeout_s=args.model_timeout, tool_timeout_s=args.tool_timeout,
                        total_timeout_s=args.total_timeout)
    model = LocalModel(args.endpoint, args.model_id, timeout_s=args.model_timeout,
                       max_tokens=args.max_tokens)
    executor = OpsMetricExecutor(roots, ops_root=Path(args.ops_root) if args.ops_root else None,
                                boxscore_db=Path(args.boxscore_db) if args.boxscore_db else None)
    result = run_question(args.question, model, config, executor)
    # Keep live figures out of terminal/UI summaries; full cited result is private.
    print(json.dumps({"status": result["status"], "error": result.get("error"),
                      "result_file": str(config.run_dir / "result.json")}, indent=2))
    return 0 if result["status"] == "answered" else 2


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


def _read_slice_b_cli_json(raw: str) -> dict:
    """Bounded private read; no-follow descriptors for every path component."""
    from plat_harness.adapters import slice_b

    try:
        return slice_b.decode(slice_b._read(raw, limit=1024 * 1024))
    except ValueError as exc:
        raise HarnessError("UNSAFE_PATH", "Invalid private input path.") from exc
    except HarnessError as exc:
        if exc.code == "NOT_FOUND":
            # Preserve the existing CLI's typed missing-file outcome.
            raise FileNotFoundError("Private request file is absent") from exc
        raise


def _reject_unconnected_slice_b_contract(value: dict) -> None:
    # contracts.py's review registry is NOT the wrapper's pinned approval.
    # Recognize, refuse, and never translate a classifier/policy review into it.
    if "contract_version" in value:
        raise HarnessError(
            "NOT_IMPLEMENTED",
            "Versioned classifier/policy contracts are not connected to Slice B host approval.",
            details={"blocker": "CLASSIFIER_POLICY_INTEGRATION_NOT_IMPLEMENTED"},
        )


def _cmd_synthetic_underwrite(args: argparse.Namespace) -> int:
    """Thin operator seam only: no approval minting, policy selection or math."""
    from plat_harness.adapters import slice_b
    from plat_harness.tools.catalog import require_rank

    try:
        # A minimum-rank check alone would permit Rank 3. This command cannot
        # accept publish authority, and rejects it before touching any file.
        if args.rank not in (0, 1, 2):
            raise HarnessError("RANK_FORBIDDEN", "Synthetic underwriting permits only host draft rank 2.")
        rank = PermissionRank(args.rank)
        require_rank("run_underwriting_model", rank)
        request = _read_slice_b_cli_json(args.request)
        _reject_unconnected_slice_b_contract(request)
        required = {"subject_id", "run_id", "input_sha256", "policy_version", "inputs_path",
                    "approval_path", "policy_path", "t12_path", "broker_path", "classification_path"}
        if set(request) != required or any(not isinstance(v, str) for v in request.values()):
            raise HarnessError("INVALID_ARGUMENTS", "Exactly the ten string-valued Slice B arguments are required.")
        from plat_harness.adapters import review_bridge
        approval = _read_slice_b_cli_json(request["approval_path"])
        # A classifier/policy review alone is still not execution permission.
        if approval.get("contract_version") != review_bridge.EXECUTION_VERSION:
            for field in ("approval_path", "policy_path", "classification_path"):
                _reject_unconnected_slice_b_contract(_read_slice_b_cli_json(request[field]))
        result = call_stub("run_underwriting_model", rank, **request)
        import os
        if os.environ.get("PLAT_HARNESS_SYNTHETIC_REVIEW_PATH"):
            result = {**result, "synthetic_review": review_bridge.review_run(result)}
    except (HarnessError, OSError, RecursionError) as exc:
        if not isinstance(exc, HarnessError):
            exc = HarnessError(
                "INVALID_INPUT" if isinstance(exc, RecursionError) else "INPUT_IO_ERROR",
                "Unable to process the private reviewed request.",
                details={"exception_type": type(exc).__name__},
            )
        print(json.dumps({**exc.as_dict(), "certified": False}, indent=2), file=sys.stderr)
        return 2
    # Preserve the wrapper's complete, exact-readback manifest and blocker.
    # Child execution success is not valuation success. This slice never certifies.
    print(slice_b.encode(result).decode("utf-8"))
    return 2


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
