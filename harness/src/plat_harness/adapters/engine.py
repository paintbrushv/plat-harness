"""Path-dep adapter over a Decimal underwriting engine you bring.

This public tree does not vendor an engine. Without PLAT_HARNESS_ENGINE_ROOT
(or an installed engine extra) the adapter refuses — it does not invent CoC,
IRR, DSCR, EM, or cap. It does not rewrite run_underwriting.
"""

from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path
from typing import Any

from plat_harness.adapters import paths
from plat_harness.errors import HarnessError, NOT_FOUND, UNCERTIFIED_METRIC


def engine_importable() -> bool:
    root = paths.engine_root()
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from engine.modules.util import dec  # noqa: F401
    except ImportError:
        return False
    return True


def load_cash_on_cash(deal_id: str | None) -> dict[str, Any]:
    """Year-1 CoC as an engine Decimal string, cited to a golden/engine artifact."""
    if not deal_id or not str(deal_id).strip():
        raise HarnessError(
            NOT_FOUND,
            "Year-1 cash-on-cash requires --deal.",
            details={"metric_id": "cash_on_cash"},
        )
    if not engine_importable():
        raise HarnessError(
            UNCERTIFIED_METRIC,
            "Bring your engine. The public extract ships a path-dep stub and will not invent CoC.",
            details={"metric_id": "cash_on_cash", "deal_id": deal_id},
        )
    if not paths.uw_backend_configured():
        raise HarnessError(
            UNCERTIFIED_METRIC,
            "No underwriting golden/engine backend configured. Will not invent CoC.",
            details={"metric_id": "cash_on_cash", "deal_id": deal_id},
        )
    inputs_path, outputs_path = _resolve_golden(deal_id)
    if inputs_path is None or outputs_path is None:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            f"No engine golden for deal '{deal_id}'. Will not invent CoC/IRR/DSCR/EM/cap.",
            details={"metric_id": "cash_on_cash", "deal_id": deal_id},
        )
    return _coc_from_golden(inputs_path, outputs_path)


def _resolve_golden(deal_id: str) -> tuple[Path | None, Path | None]:
    key = str(deal_id).strip().lower()
    explicit_out = _env_outputs()
    if explicit_out is not None:
        inputs = explicit_out.with_name(
            explicit_out.name.replace("_model_outputs.json", "_model_inputs.json")
        )
        if inputs.is_file():
            return inputs, explicit_out
    golden_dir = paths.uw_golden_dir()
    if golden_dir is not None:
        token = key.replace(" ", "_").replace("&", "")
        for outputs in golden_dir.glob("*_model_outputs.json"):
            stem = outputs.name.lower()
            if token in stem or key in stem:
                inputs = outputs.with_name(
                    outputs.name.replace("_model_outputs.json", "_model_inputs.json")
                )
                if inputs.is_file():
                    return inputs, outputs
    return None, None


def _env_outputs() -> Path | None:
    import os

    raw = os.environ.get("PLAT_HARNESS_GOLDEN_UW_OUTPUT", "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def _coc_from_golden(inputs_path: Path, outputs_path: Path) -> dict[str, Any]:
    root = paths.engine_root()
    if root is not None and str(root) not in sys.path:
        sys.path.insert(0, str(root))
    try:
        from engine.modules.analysis_years import aggregate_analysis_years
        from engine.modules.util import dec
    except ImportError as exc:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            "Decimal engine is not importable. Will not invent CoC.",
            details={"error": str(exc)},
        ) from exc

    with inputs_path.open(encoding="utf-8") as handle:
        inputs = json.load(handle, parse_float=Decimal)
    with outputs_path.open(encoding="utf-8") as handle:
        outputs = json.load(handle, parse_float=Decimal)

    cashflow = outputs.get("cashflow") or {}
    fund = outputs.get("fund_waterfall") or {}
    equity = (inputs.get("purchase_assumptions") or {}).get("total_equity_basis")
    start = (inputs.get("time_grid") or {}).get("analysis_start_date")
    by_year = cashflow.get("by_year")
    if not by_year or equity is None or not start:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            "Golden engine artifacts are missing cashflow or equity. Will not invent CoC.",
            details={"inputs": str(inputs_path), "outputs": str(outputs_path)},
        )

    year_1 = _year_one(by_year, cashflow.get("by_month"), str(start), aggregate_analysis_years)
    leveraged = year_1.get("leveraged_cash_flow")
    if leveraged is None:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            "Golden cashflow is missing leveraged_cash_flow. Will not invent CoC.",
            details={"outputs": str(outputs_path)},
        )
    fund_by_year = fund.get("by_year") or []
    fy1 = fund_by_year[0] if fund_by_year else {}
    free_cf = dec(leveraged) - dec(fy1.get("asset_management_fee", 0)) - dec(
        fy1.get("partnership_expenses", 0)
    )
    eq = dec(equity)
    if eq <= 0:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            "Golden equity basis is not positive. Will not invent CoC.",
            details={"equity": str(eq)},
        )
    coc = free_cf / eq
    return {
        "value": format(coc, "f"),
        "unit": "ratio",
        "free_cf_year_1": format(free_cf, "f"),
        "equity_basis": format(eq, "f"),
        "source": [
            {
                "artifact": str(outputs_path),
                "row": "cashflow.by_month + fund_waterfall.by_year → Decimal CoC",
                "period": str(start),
            },
            {
                "artifact": str(inputs_path),
                "row": "purchase_assumptions.total_equity_basis",
                "period": str(start),
            },
        ],
        "freshness": str(start),
        "period": str(start),
        "as_of": str(start),
        "backend": "engine_golden",
        "note": (
            "run_underwriting on millage-less inputs refuses MISSING_MILLAGE; "
            "CoC is engine Decimal arithmetic on a frozen cashflow artifact, not a model ratio."
        ),
    }


def _year_one(by_year: list, by_month: object, start: str, aggregate_analysis_years) -> dict:
    if by_month and len(start) >= 7 and int(start[5:7]) != 1:
        analysis_years = aggregate_analysis_years(by_month, start)
        if analysis_years:
            return analysis_years[0]
    return by_year[0]
