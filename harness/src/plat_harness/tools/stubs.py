"""Slice 0 stubs for the remaining tools. Rank + millage gates fire; no calc."""

from __future__ import annotations

from plat_harness.errors import HarnessError, NOT_IMPLEMENTED
from plat_harness.ranks import PermissionRank
from plat_harness.tools.catalog import require_rank, stub_not_implemented
from plat_harness.tools.certified_metric import require_millage_for_underwrite

_ENGINE_TOOLS = frozenset(
    {
        "run_underwriting_model",
        "run_sensitivity",
        "run_backsolve_coc",
        "check_buy_box",
    }
)


def call_stub(tool_name: str, session_rank: PermissionRank, **kwargs: object) -> None:
    if tool_name == "get_certified_metric":
        raise TypeError("Use get_certified_metric() directly")
    require_rank(tool_name, session_rank)
    if tool_name in _ENGINE_TOOLS:
        require_millage_for_underwrite(kwargs.get("millage_rate_mills"))
        raise HarnessError(
            NOT_IMPLEMENTED,
            f"Tool '{tool_name}' requires the Decimal engine (Slice B). Millage is present; calc is not wired.",
            details={"tool": tool_name},
        )
    stub_not_implemented(tool_name, session_rank)
