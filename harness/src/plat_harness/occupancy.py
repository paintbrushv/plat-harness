"""Occupancy must always emit occupied, vacant, down, and the denominator used."""

from __future__ import annotations

from dataclasses import dataclass

from plat_harness.errors import HarnessError, IMPLICIT_ZERO_FORBIDDEN, OCCUPANCY_COUNTS_REQUIRED


@dataclass(frozen=True)
class OccupancyCounts:
    occupied: int
    vacant: int
    down: int
    denominator: int

    def as_dict(self) -> dict:
        return {
            "occupied": self.occupied,
            "vacant": self.vacant,
            "down": self.down,
            "denominator": self.denominator,
            "rate": self.occupied / self.denominator,
        }


def require_occupancy_counts(
    occupied: object = None,
    vacant: object = None,
    down: object = None,
    denominator: object = None,
) -> OccupancyCounts:
    """Refuse a rate unless all four counts are present and the denominator is positive."""
    missing = [
        name
        for name, value in (
            ("occupied", occupied),
            ("vacant", vacant),
            ("down", down),
            ("denominator", denominator),
        )
        if value is None or value == ""
    ]
    if missing:
        raise HarnessError(
            OCCUPANCY_COUNTS_REQUIRED,
            "Physical occupancy cannot return a number without occupied, vacant, "
            "down, and the denominator used.",
            details={"missing": missing},
        )
    try:
        counts = OccupancyCounts(
            occupied=int(occupied),  # type: ignore[arg-type]
            vacant=int(vacant),  # type: ignore[arg-type]
            down=int(down),  # type: ignore[arg-type]
            denominator=int(denominator),  # type: ignore[arg-type]
        )
    except (TypeError, ValueError) as exc:
        raise HarnessError(
            OCCUPANCY_COUNTS_REQUIRED,
            "Occupancy counts must be integers (occupied, vacant, down, denominator).",
        ) from exc
    if any(value < 0 for value in (counts.occupied, counts.vacant, counts.down, counts.denominator)):
        raise HarnessError(
            OCCUPANCY_COUNTS_REQUIRED,
            "Occupancy counts cannot be negative.",
        )
    if counts.denominator == 0:
        raise HarnessError(
            IMPLICIT_ZERO_FORBIDDEN,
            "Occupancy denominator is zero; refusing an implicit 0% or 100% rate.",
        )
    return counts
