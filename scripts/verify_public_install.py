"""Verify the exact merged public V3 packages without source checkout imports.

Run from a new environment with the four pinned wheels installed:

    env -u PYTHONPATH -u PLAT_COSTMODEL_PATH -u PLAT_COSTMODEL_DEFERRED_PATH \
      -u PLAT_MULTIFAMILY_UNDERWRITING_PATH -u UNDERWRITING_ENGINE_PATH \
      -u PLAT_DEALS_ROOT -u UNDERWRITING_MCP_CMD -u PLAT_COSTMODEL_CMD \
      PLAT_HARNESS_EXPECTED_SHA=<exact installed checkout commit> \
      python -I scripts/verify_public_install.py

This is a synthetic integration check, not a live migration or host test.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timezone
from hashlib import sha256
import importlib.metadata
from importlib.resources import files
import json
import os
import re
import sys
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory

_PATH_VARIABLES = (
    "PYTHONPATH",
    "PLAT_COSTMODEL_PATH",
    "PLAT_COSTMODEL_DEFERRED_PATH",
    "PLAT_MULTIFAMILY_UNDERWRITING_PATH",
    "UNDERWRITING_ENGINE_PATH",
    "PLAT_DEALS_ROOT",
    "UNDERWRITING_MCP_CMD",
    "PLAT_COSTMODEL_CMD",
)
if not sys.flags.isolated or any(os.environ.get(name) for name in _PATH_VARIABLES):
    raise RuntimeError("run with python -I and all sibling path variables unset")

import anyio
import engine
import plat_agent
import plat_costmodel
import plat_harness
from plat_harness import opening_migration, temporal
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from plat_agent.costmodel_client import _default_server_command
from plat_agent.lifecycle.judgment_rules import _ensure_agency_sizer
from plat_agent.lifecycle.synthetic_interior_scope import (
    interior_plus_synthetic_roof_yield,
    record_test001_thesis,
)
from plat_agent.lifecycle.versioned_adapters import COSTMODEL_V1, UNDERWRITING_V2
from plat_agent.orchestrator.tools import load_deal_inputs_tool
from plat_agent.sweep.perturb import get_preset_family
from plat_agent.underwriting_client import UnderwritingClient


_harness_commit = os.environ.get("PLAT_HARNESS_EXPECTED_SHA", "")
if not re.fullmatch(r"[0-9a-f]{40}", _harness_commit):
    raise RuntimeError("PLAT_HARNESS_EXPECTED_SHA must name the installed checkout commit")

EXPECTED_SHAS = {
    "plat-agent": "cc484170e413d9d49c407775fd9e3f9480b24d19",
    "plat-harness": _harness_commit,
    "plat-costmodel": COSTMODEL_V1.source_sha,
    "plat-multifamily-underwriting": UNDERWRITING_V2.source_sha,
}
def _check(condition: bool, message: str) -> None:
    if not condition:
        raise RuntimeError(message)


async def _costmodel_tool_count() -> int:
    command = _default_server_command()
    with anyio.fail_after(20):
        async with stdio_client(StdioServerParameters(command=command[0], args=command[1:])) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                return len((await session.list_tools()).tools)


def _verify_underwriting_mcp() -> dict[str, object]:
    client = UnderwritingClient()
    _check(client._server_params.cwd is None, "underwriting MCP used a source cwd")
    try:
        invalid = client.validate({})
        _check(invalid.get("status") == "FAIL", "underwriting MCP did not refuse empty inputs")
        _check(invalid.get("adapter_contract") == "plat.underwriting.mcp/1", "underwriting MCP contract changed")
        fixture = json.loads(files("plat_agent.lifecycle").joinpath(
            "fixtures/test001_underwriting_inputs.json"
        ).read_text(encoding="utf-8"))
        summary = client.run_summary(fixture)
        _check(summary.get("status") == "success" and "cashflow" not in summary,
               "installed underwriting MCP summary changed")
        feasibility = client.check_feasibility(fixture)
        ltv = feasibility.get("gates", {}).get("ltv", {}).get("actual")
        _check(ltv == 0.65, "installed underwriting MCP LTV changed")
        return {"contract": invalid["adapter_contract"], "ltv": ltv}
    finally:
        client.close()


def _verify_opening_install() -> None:
    for module in (opening_migration, temporal):
        _check("site-packages" in Path(module.__file__).resolve().parts,
               f"{module.__name__} came from a source tree")
    source = {
        "schema_version": opening_migration.SNAPSHOT_VERSION,
        "workspace_id": "public-synthetic", "source_system": "sample-pm",
        "source_account_id": "sample-account", "as_of": "2026-09-01",
        "rows": [{
            "sequence": 1, "source_property_id": "legacy-1",
            "source_record_id": "revenue-1", "revision": "1",
            "period": "2026-08", "account": "revenue",
            "unit_category": "residential", "amount": "100000.00",
            "deleted": False,
        }],
    }
    raw = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
    plan = opening_migration.plan_opening_snapshot(
        raw, expected_sha256=sha256(raw).hexdigest(),
        identity_map={"legacy-1": "property-1"},
    )
    known_at = datetime(2026, 9, 20, 13, tzinfo=timezone.utc)
    with TemporaryDirectory() as root:
        base = Path(root)
        _check(not list(base.iterdir()), "opening dry-run wrote a target")
        ledger = temporal.TemporalLedger(
            str(base / "events.sqlite"),
            authorize=lambda actor, workspace, aggregate, capability: (
                actor == "operator" and workspace == "public-synthetic" and
                aggregate == "property-1" and capability in ("read", "write")
            ),
            clock=lambda: datetime(2026, 9, 20, 12, tzinfo=timezone.utc),
        )
        try:
            progress = opening_migration.execute_opening_batch(
                plan, ledger, actor_id="operator", checkpoint_path=base / "checkpoint.json",
                batch_size=1, known_at=known_at,
            )
            _check(progress.validated and progress.next_index == 1,
                   "installed opening import did not validate")
            totals = opening_migration.read_opening_baseline(
                plan, ledger, actor_id="operator", effective_at=date(2026, 9, 1),
                known_at=known_at,
            )
            _check(totals == {
                '["property-1","2026-08","revenue","residential"]': "100000.00"
            }, "installed opening parity changed")
            _check(not ledger.as_known(
                actor_id="operator", workspace_id="public-synthetic",
                aggregate_id="property-1", effective_at=date(2026, 9, 2),
                known_at=known_at,
            ), "opening state leaked into a later date")
            try:
                opening_migration.read_opening_baseline(
                    plan, ledger, actor_id="former", effective_at=date(2026, 9, 1),
                    known_at=known_at,
                )
            except temporal.TemporalRefusal:
                pass
            else:
                raise RuntimeError("revoked historical access was accepted")
        finally:
            ledger.close()


def main() -> None:
    for package, module in (
        ("plat-agent", plat_agent),
        ("plat-harness", plat_harness),
        ("plat-costmodel", plat_costmodel),
        ("plat-multifamily-underwriting", engine),
    ):
        expected_version = "0.1.1" if package == "plat-multifamily-underwriting" else "0.1.0"
        _check(importlib.metadata.version(package) == expected_version, f"unexpected {package} version")
        _check("site-packages" in Path(module.__file__).resolve().parts, f"{package} came from a source tree")

    COSTMODEL_V1.verify()
    UNDERWRITING_V2.verify()
    for contract in (COSTMODEL_V1, UNDERWRITING_V2):
        try:
            replace(contract, content_sha256="0" * 64).verify()
        except RuntimeError as exc:
            _check("contents differ" in str(exc), f"wrong refusal for {contract.distribution}")
        else:
            raise AssertionError(f"{contract.distribution} accepted a stale content pin")

    _check(UnderwritingClient()._import_engine_api().__module__ == "engine.api", "wrong direct engine")
    _check(bool(get_preset_family("stabilized")), "scenario presets missing")
    _check(_ensure_agency_sizer(), "reviewed agency sizer missing")

    result = interior_plus_synthetic_roof_yield()
    _check(str(result.year_2_unlevered_noi) == "1039354.8", "year-two NOI drift")
    _check(str(result.interior_capex) == "1358150", "interior capex drift")
    _check(str(result.roof_capex) == "400000.0", "synthetic roof capex drift")
    _check(str(result.capex) == "1758150.0", "total capex drift")
    _check(str(result.year_2_unlevered_yield_on_cost) == "0.06811800906400841517484098662", "yield drift")
    _check(result.bid is None and result.withheld, "bid was not withheld")

    issued, thesis = record_test001_thesis()
    _check(issued["present_as_bid"] is False and issued["bid"] is None, "reasonability gate changed")
    _check(thesis.thesis.present_as_bid is False, "thesis bid changed")
    _check(thesis.thesis.capex == result.capex, "thesis capex drift")

    loaded = asyncio.run(load_deal_inputs_tool.handler({"deal_id": "TEST-001"}))
    _check(not loaded.get("is_error"), "packaged fixture did not load")
    _check(json.loads(loaded["content"][0]["text"])["inputs"]["metadata"]["deal_id"] == "TEST-001", "wrong packaged deal")
    tool_count = anyio.run(_costmodel_tool_count)
    _check(tool_count == 11, "installed costmodel server tool set changed")
    underwriting_mcp = _verify_underwriting_mcp()
    _verify_opening_install()

    print(json.dumps({
        "status": "INTEGRATION_VERIFIED",
        "scope": "public_synthetic",
        "expected_source_shas": EXPECTED_SHAS,
        "result": result.as_dict(),
        "bid_withheld": True,
        "costmodel_mcp_tools": tool_count,
        "underwriting_mcp": underwriting_mcp,
        "synthetic_opening_state": "verified",
    }, sort_keys=True))


if __name__ == "__main__":
    main()
