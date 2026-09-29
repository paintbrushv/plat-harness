"""Run distinct public synthetic deals against installed underwriting and harness packages.

This is a component/integration matrix.  The adapter functions are called
in-process; verify_public_install.py separately checks the MCP transport.
No source checkout imports, host credentials, or live deal files are used.
"""
from __future__ import annotations

from decimal import Decimal
import json
import os
from pathlib import Path
import sys

_PATH_VARIABLES = (
    "PYTHONPATH", "PLAT_COSTMODEL_PATH", "PLAT_COSTMODEL_DEFERRED_PATH",
    "PLAT_MULTIFAMILY_UNDERWRITING_PATH", "UNDERWRITING_ENGINE_PATH",
    "PLAT_DEALS_ROOT", "UNDERWRITING_MCP_CMD", "PLAT_COSTMODEL_CMD",
)
if not sys.flags.isolated or any(os.environ.get(name) for name in _PATH_VARIABLES):
    raise RuntimeError("run with python -I and all sibling/host path variables unset")

import engine
import plat_harness
from engine.engine import run_underwriting
from engine.mcp_server import validate_deal_inputs, run_deal_summary, check_deal_feasibility
from plat_agent.lifecycle.versioned_adapters import UNDERWRITING_V2
from plat_harness.errors import HarnessError
from plat_harness.original_thesis import record_original_thesis, record_operations_actual
from plat_harness.reasonability import present_underwriting
from plat_harness.underwriting_direction import (
    classify_physical_position, year_2_unlevered_yield_on_cost, yield_in_band,
)

ROOT = Path(__file__).resolve().parents[1] / "samples" / "deals" / "synthetic_matrix"
MANIFEST = json.loads((ROOT / "manifest.json").read_text(encoding="utf-8"))
COUNTS = MANIFEST["physical_counts"]
if MANIFEST["data_class"] != "public_synthetic" or set(COUNTS) != {
    "stabilized", "lease_up", "cost_stress", "high_price",
    "small_distressed", "missing_price",
}:
    raise RuntimeError("synthetic matrix manifest changed")


def check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


def verify() -> dict[str, dict[str, object]]:
    for package in (engine, plat_harness):
        check("site-packages" in Path(package.__file__).resolve().parts,
              f"{package.__name__} came from a source checkout")
    UNDERWRITING_V2.verify()
    results: dict[str, dict[str, object]] = {}
    ids: set[str] = set()
    check({path.stem for path in ROOT.glob("*.json") if path.name != "manifest.json"} == set(COUNTS),
          "fixture set differs from manifest")

    for name, counts in COUNTS.items():
        check(len(counts) == 4 and all(type(value) is int and value >= 0 for value in counts)
              and sum(counts[:3]) == counts[3],
              f"invalid physical counts: {name}")
        inputs = json.loads((ROOT / f"{name}.json").read_text(encoding="utf-8"))
        deal_id = inputs["metadata"]["deal_id"]
        check(deal_id not in ids and deal_id.startswith("SYN-"), f"invalid synthetic id: {name}")
        ids.add(deal_id)
        validation = validate_deal_inputs(inputs)
        check(validation.get("adapter_contract") == "plat.underwriting.mcp/1",
              f"adapter contract changed: {name}")
        if name == "missing_price":
            check(validation["status"] == "FAIL" and any(
                issue["severity"] == "ERROR"
                and issue["path"] == "/purchase_assumptions/purchase_price"
                for issue in validation["issues"]
            ), "missing purchase price was accepted")
            try:
                year_2_unlevered_yield_on_cost("1000000", None, "0")
            except HarnessError:
                pass
            else:
                raise RuntimeError("missing price was treated as zero")
            presentation = present_underwriting({"year_1_noi": "1000000", "units": 100})
            check(not presentation["present_as_bid"] and presentation["bid"] is None,
                  "missing-price bid was presented")
            results[name] = {"validation": "FAIL", "missing_price_refused": True}
            continue

        check(validation["status"] in ("PASS", "WARN"), f"invalid deal {name}: {validation}")
        full = run_underwriting(inputs)
        summary = run_deal_summary(inputs)
        feasibility = check_deal_feasibility(inputs)
        check(summary["status"] == "success" and summary["yields"] == full["metrics"]["yields"],
              f"summary diverged from engine: {name}")
        check(feasibility["gates"]["ltv"]["actual"] == 0.65,
              f"synthetic financing drifted: {name}")
        year1 = Decimal(str(full["metrics"]["noi"]["year_1_noi"]))
        year2 = Decimal(str(full["metrics"]["noi"]["year_2_unlevered_noi"]))
        capex = Decimal(str(full["cashflow"]["summary"]["total_capex"]))
        price = Decimal(str(inputs["purchase_assumptions"]["purchase_price"]))
        check(capex >= 0 and price > 0, f"invalid basis: {name}")
        ratio = year_2_unlevered_yield_on_cost(year2, price, capex)
        position = classify_physical_position(*counts, noi=year1)
        check(position is not None, f"occupancy unavailable: {name}")
        check(sum(row["unit_count"] for row in inputs["unit_cohorts"]) == counts[3],
              f"occupancy denominator differs from modeled units: {name}")
        presentation = present_underwriting({
            "purchase_price": price,
            "going_in_cap_rate": full["metrics"]["yields"]["going_in_cap_rate"],
            "exit_cap_rate": inputs["exit_assumptions"]["exit_cap_rate"],
            "units": counts[3],
            "minimum_dscr": full["metrics"]["dscr"]["minimum_dscr"],
            "year_1_noi": year1,
        })
        thesis = record_original_thesis(
            deal_id, purchase_price=price, year_2_unlevered_noi=year2,
            capex=capex, present_as_bid=presentation["present_as_bid"],
        )
        later = record_operations_actual(thesis, year1 - Decimal("50000"))
        check(later.thesis == thesis.thesis and thesis.operations_actual_noi is None
              and later.operations_actual_noi == year1 - Decimal("50000"),
              f"original thesis changed after operations actual: {name}")
        results[name] = {
            "validation": validation["status"], "position": position.label,
            "year_2_unlevered_noi": str(year2), "year_2_yield_on_cost": str(ratio),
            "yield_in_7_8_band": yield_in_band(ratio), "explicit_capex": str(capex),
            "numeric_bid_presented": presentation["present_as_bid"],
            "feasible_at_default_gates": feasibility["feasible"],
        }

    check(results["stabilized"]["position"] == "stabilized"
          and results["stabilized"]["yield_in_7_8_band"]
          and results["stabilized"]["feasible_at_default_gates"],
          "stabilized control did not pass")
    check(results["lease_up"]["position"] == "lease_up"
          and Decimal(results["lease_up"]["year_2_unlevered_noi"])
          < Decimal(results["stabilized"]["year_2_unlevered_noi"]),
          "lease-up economics did not worsen")
    check(Decimal(results["cost_stress"]["year_2_unlevered_noi"])
          < Decimal(results["stabilized"]["year_2_unlevered_noi"]),
          "cost stress did not lower NOI")
    check(Decimal(results["high_price"]["year_2_yield_on_cost"])
          < Decimal(results["stabilized"]["year_2_yield_on_cost"])
          and Decimal(results["high_price"]["year_2_unlevered_noi"])
          < Decimal(results["stabilized"]["year_2_unlevered_noi"]),
          "high-price yield or reassessed NOI did not fall")
    check(results["small_distressed"]["position"] == "distressed"
          and Decimal(results["small_distressed"]["explicit_capex"]) == Decimal("250000"),
          "small distressed deal lost its explicit roof cost")
    check(results["missing_price"]["missing_price_refused"],
          "missing-price refusal disappeared")
    return results


if __name__ == "__main__":
    print(json.dumps({"status": "SYNTHETIC_VERIFIED", "cases": verify()}, indent=2))
