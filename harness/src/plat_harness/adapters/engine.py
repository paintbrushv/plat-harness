"""Exact-run engine artifact reader. Historical substring goldens are unsafe.

Slice B owns engine invocation and evidence-bound private persistence. Engine
outputs are drafts until a complete reconciliation/review contract is wired.
"""
from __future__ import annotations

import os
from typing import Any

from plat_harness.errors import HarnessError, UNCERTIFIED_METRIC


def load_cash_on_cash(deal_id: str | None, *, run_id: str | None = None,
                      input_sha256: str | None = None,
                      policy_version: str | None = None) -> dict[str, Any]:
    """Read only the exact host-selected run; never find a golden by substring.

    Environment selectors support the current scoreboard without CLI coupling.
    They are host configuration, not tool/model-provided rank or approval.
    """
    run_id = run_id if run_id is not None else os.environ.get("PLAT_HARNESS_RUN_ID")
    input_sha256 = input_sha256 if input_sha256 is not None else os.environ.get("PLAT_HARNESS_INPUT_SHA256")
    policy_version = policy_version if policy_version is not None else os.environ.get("PLAT_HARNESS_POLICY_VERSION")
    if not deal_id or not run_id or not input_sha256 or not policy_version:
        raise HarnessError(UNCERTIFIED_METRIC,
                           "Engine metric requires exact subject, run ID, input SHA256 and policy version; will not invent a metric or search historical goldens.",
                           details={"metric_id": "cash_on_cash", "required": ["subject_id", "run_id", "input_sha256", "policy_version"]})
    from plat_harness.adapters.slice_b import load_cash_on_cash as load_exact
    return load_exact(subject_id=deal_id, run_id=run_id, input_sha256=input_sha256,
                      policy_version=policy_version)
