"""Tool dispatch: reviewed synthetic Slice B plus typed unwired stubs."""

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


def call_stub(tool_name: str, session_rank: PermissionRank, **kwargs: object) -> object:
    if tool_name == "get_certified_metric":
        raise TypeError("Use get_certified_metric() directly")
    require_rank(tool_name, session_rank)
    if tool_name == "run_underwriting_model" and "inputs_path" in kwargs:
        from plat_harness.adapters.slice_b import run_underwriting_model

        required = {"subject_id", "run_id", "input_sha256", "policy_version", "inputs_path",
                    "approval_path", "policy_path", "t12_path", "broker_path", "classification_path"}
        if set(kwargs) != required or any(not isinstance(v, str) for v in kwargs.values()):
            raise HarnessError("INVALID_ARGUMENTS", "Slice B requires exactly the host-reviewed run arguments.")
        return run_underwriting_model(session_rank=session_rank, **kwargs)
    if tool_name in _ENGINE_TOOLS:
        require_millage_for_underwrite(kwargs.get("millage_rate_mills"))
        raise HarnessError(
            NOT_IMPLEMENTED,
            f"Tool '{tool_name}' requires the Decimal engine (Slice B). Millage is present; calc is not wired.",
            details={"tool": tool_name},
        )
    stub_not_implemented(tool_name, session_rank)
