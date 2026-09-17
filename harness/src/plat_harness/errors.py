"""Typed harness errors. The model never invents a number to paper over these."""

from __future__ import annotations


class HarnessError(Exception):
    """Typed refusal. ``code`` is the contract id; ``message`` is operator-facing."""

    def __init__(self, code: str, message: str, *, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def as_dict(self) -> dict:
        payload = {"error": self.code, "message": self.message}
        if self.details:
            payload["details"] = self.details
        return payload


NOT_FOUND = "NOT_FOUND"
STALE = "STALE"
UNCERTIFIED_METRIC = "UNCERTIFIED_METRIC"
APPROVAL_REQUIRED = "APPROVAL_REQUIRED"
RECONCILE_FAILED = "RECONCILE_FAILED"
BUY_BOX_FAIL = "BUY_BOX_FAIL"
MISSING_MILLAGE = "MISSING_MILLAGE"
IMPLICIT_ZERO_FORBIDDEN = "IMPLICIT_ZERO_FORBIDDEN"
CONFLICT_UNRESOLVED = "CONFLICT_UNRESOLVED"
RANK_FORBIDDEN = "RANK_FORBIDDEN"
NOT_IMPLEMENTED = "NOT_IMPLEMENTED"
NO_MODEL_CONFIGURED = "NO_MODEL_CONFIGURED"
OCCUPANCY_COUNTS_REQUIRED = "OCCUPANCY_COUNTS_REQUIRED"
CITATION_REQUIRED = "CITATION_REQUIRED"

# Contexts that must never reach a certified answer, even when tagged.
UNCERTIFIED_CONTEXTS = frozenset({"forge"})
