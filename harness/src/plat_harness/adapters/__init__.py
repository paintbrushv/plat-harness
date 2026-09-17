"""Calc-backend adapters. Do not import plat_agent.dispatch.sibling."""

from plat_harness.errors import HarnessError, NOT_IMPLEMENTED

__all__ = ["not_wired"]


def not_wired(name: str) -> None:
    raise HarnessError(
        NOT_IMPLEMENTED,
        f"Adapter '{name}' is not wired.",
        details={"adapter": name},
    )
