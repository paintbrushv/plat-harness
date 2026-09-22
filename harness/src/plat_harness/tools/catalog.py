"""The 18 tool contracts. Specs + typed stub errors; calc is adapter-backed or stubbed."""

from __future__ import annotations

from dataclasses import dataclass

from plat_harness.errors import HarnessError, NOT_IMPLEMENTED, RANK_FORBIDDEN
from plat_harness.ranks import TOOL_RANK_LABEL, PermissionRank


@dataclass(frozen=True)
class ToolSpec:
    number: int
    name: str
    purpose: str
    rank_label: str
    side_effect: str
    timeout_s: int
    idempotent: str

    @property
    def min_rank(self) -> PermissionRank:
        return TOOL_RANK_LABEL[self.rank_label]


TOOL_SPECS: tuple[ToolSpec, ...] = (
    ToolSpec(1, "get_certified_metric", "One metric, one context, one period", "READ", "none", 5, "yes"),
    ToolSpec(2, "get_rent_roll", "Cohort/status slice as-of", "READ", "none", 15, "yes"),
    ToolSpec(3, "get_t12", "Trailing statement", "READ", "none", 15, "yes"),
    ToolSpec(4, "parse_rent_roll", "Wrap rent-roll standardizer; strip resident_name", "DRAFT", "draft artifact", 60, "key"),
    ToolSpec(5, "parse_t12", "Deterministic parse → preview", "DRAFT", "draft artifact", 60, "key"),
    ToolSpec(6, "parse_om", "Broker snapshot / local fallback", "DRAFT", "draft artifact", 120, "key"),
    ToolSpec(7, "reconcile_sources", "Actuals vs OM vs house", "READ", "draft recon", 60, "key"),
    ToolSpec(8, "run_underwriting_model", "Reviewed synthetic engine + recon draft; exact host approval required", "READ/DRAFT", "private draft outputs", 180, "reviewed input/evidence/policy/code hashes"),
    ToolSpec(9, "run_sensitivity", "Engine sensitivity", "READ", "draft", 180, "key"),
    ToolSpec(10, "run_backsolve_coc", "Highest feasible price at the configured CoC hurdle", "RECOMMEND", "draft", 300, "key"),
    ToolSpec(11, "check_buy_box", "Policy buy-box + millage present", "READ", "none", 5, "yes"),
    ToolSpec(12, "explain_variance", "Ops variance engine", "READ", "draft report", 60, "key"),
    ToolSpec(13, "list_exceptions", "Material gaps, unmapped, stale", "READ", "none", 15, "yes"),
    ToolSpec(14, "search_unstructured", "OM/notes/report search, redacted", "READ", "none", 30, "yes"),
    ToolSpec(15, "search_transactions", "GL lines, PII redacted", "READ", "none", 15, "yes"),
    ToolSpec(16, "save_artifact", "Write draft JSON/md", "DRAFT", "local artifact", 10, "key"),
    ToolSpec(17, "request_approval", "Promote diff", "WRITE", "approval record", 10, "key"),
    ToolSpec(18, "fetch_trace", "Prior run", "READ", "none", 5, "yes"),
)

_BY_NAME = {spec.name: spec for spec in TOOL_SPECS}


def get_tool(name: str) -> ToolSpec:
    try:
        return _BY_NAME[name]
    except KeyError as exc:
        raise HarnessError("NOT_FOUND", f"Unknown tool '{name}'.") from exc


def require_rank(tool_name: str, session_rank: PermissionRank) -> ToolSpec:
    spec = get_tool(tool_name)
    if session_rank < spec.min_rank:
        raise HarnessError(
            RANK_FORBIDDEN,
            f"Tool '{tool_name}' requires rank {spec.min_rank.name} "
            f"({int(spec.min_rank)}); session is {session_rank.name} ({int(session_rank)}).",
            details={"tool": tool_name, "required_rank": int(spec.min_rank)},
        )
    return spec


def stub_not_implemented(tool_name: str, session_rank: PermissionRank) -> None:
    """Rank check, then typed stub. Live backends land behind adapters."""
    require_rank(tool_name, session_rank)
    raise HarnessError(
        NOT_IMPLEMENTED,
        f"Tool '{tool_name}' is a stub. Calc backends are not wired yet.",
        details={"tool": tool_name},
    )
