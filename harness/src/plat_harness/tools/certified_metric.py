"""get_certified_metric — glossary + refusals; Slice A wires occupancy / CoC / T12 R&M."""

from __future__ import annotations

from typing import Any

from plat_harness.errors import (
    CONFLICT_UNRESOLVED,
    HarnessError,
    OCCUPANCY_COUNTS_REQUIRED,
    UNCERTIFIED_CONTEXTS,
    UNCERTIFIED_METRIC,
)
from plat_harness.glossary import Glossary, Metric, load_glossary
from plat_harness.millage import parse_millage_rate
from plat_harness.occupancy import OccupancyCounts, require_occupancy_counts
from plat_harness.ranks import PermissionRank
from plat_harness.tools.catalog import require_rank

_OCCUPANCY_METRICS = frozenset({"physical_occupancy"})
_MILLAGE_METRICS = frozenset({"millage_rate"})
_COC_METRICS = frozenset({"cash_on_cash"})
_T12_RM_METRICS = frozenset({"t12_repairs_and_maintenance"})


def get_certified_metric(
    metric_id: str,
    *,
    context: str | None = None,
    period: str | None = None,
    asset_or_deal_id: str | None = None,
    millage_rate_mills: object = None,
    occupied: object = None,
    vacant: object = None,
    down: object = None,
    denominator: object = None,
    glossary: Glossary | None = None,
    session_rank: PermissionRank = PermissionRank.EXPLAIN,
) -> dict[str, Any]:
    require_rank("get_certified_metric", session_rank)
    glossary = glossary or load_glossary()
    metric = glossary.require(metric_id)

    if metric.status == "UNKNOWN":
        raise HarnessError(
            UNCERTIFIED_METRIC,
            f"Metric '{metric_id}' is UNKNOWN in the glossary. Refuse until a certified feed exists.",
            details={"metric_id": metric_id, "handshake": metric.handshake},
        )

    if context in UNCERTIFIED_CONTEXTS:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            f"Context '{context}' is uncertified (FORGE and similar must never be cited).",
            details={"metric_id": metric_id, "context": context},
        )

    needs_context = metric.status == "CONFLICT" or len(metric.definitions) > 1
    if needs_context and not context:
        raise HarnessError(
            CONFLICT_UNRESOLVED,
            f"Metric '{metric_id}' is {metric.status} and cannot be answered without a context tag. "
            + (metric.handshake or "Do not average competing formulas."),
            details={
                "metric_id": metric_id,
                "status": metric.status,
                "contexts": list(metric.contexts()),
            },
        )

    resolved = _resolve_definition(metric, context)

    if metric.id in _MILLAGE_METRICS:
        mills = parse_millage_rate(millage_rate_mills)
        return {
            "metric_id": metric.id,
            "term": metric.term,
            "value": str(mills),
            "unit": "mills_per_1000",
            "context": resolved.context if resolved else (context or "uw_proforma"),
            "formula_owner": (resolved.owner if resolved else None),
            "certification": metric.status,
            "period": period,
            "asset_or_deal_id": asset_or_deal_id,
            "source": [{"artifact": "canonical.property_tax_policy", "row": "millage_rate_mills", "period": period}],
            "handshake": metric.handshake,
        }

    if metric.id in _OCCUPANCY_METRICS:
        return _occupancy_result(
            metric,
            resolved,
            context,
            period,
            asset_or_deal_id,
            occupied,
            vacant,
            down,
            denominator,
        )

    if metric.id in _COC_METRICS:
        if context and context != "uw_proforma":
            raise HarnessError(
                UNCERTIFIED_METRIC,
                "Year-1 cash-on-cash on the certified board is uw_proforma (engine Decimal). "
                "Do not cite FORGE or governance CoC.",
                details={"metric_id": metric.id, "context": context},
            )
        from plat_harness.adapters.engine import load_cash_on_cash

        loaded = load_cash_on_cash(asset_or_deal_id)
        return _metric_payload(metric, resolved, context or "uw_proforma", period, asset_or_deal_id, loaded)

    if metric.id in _T12_RM_METRICS:
        from plat_harness.adapters.boxscore import load_t12_repairs_and_maintenance

        loaded = load_t12_repairs_and_maintenance(asset_or_deal_id, period)
        return _metric_payload(
            metric,
            resolved,
            context or "ops_actuals",
            period or loaded.get("period"),
            asset_or_deal_id,
            loaded,
        )

    raise HarnessError(
        UNCERTIFIED_METRIC,
        f"Metric '{metric_id}' has a glossary row but no live certified backend. "
        "Will not invent a number.",
        details={
            "metric_id": metric.id,
            "context": resolved.context if resolved else context,
            "status": metric.status,
            "handshake": metric.handshake,
        },
    )


def require_millage_for_underwrite(millage_rate_mills: object) -> None:
    """run_underwriting_model / underwrite CLI: millage before any engine call."""
    parse_millage_rate(millage_rate_mills)


def _occupancy_result(
    metric: Metric,
    resolved,
    context: str | None,
    period: str | None,
    asset_or_deal_id: str | None,
    occupied: object,
    vacant: object,
    down: object,
    denominator: object,
) -> dict[str, Any]:
    cli_provided = any(value is not None and value != "" for value in (occupied, vacant, down, denominator))
    if cli_provided:
        counts = require_occupancy_counts(occupied, vacant, down, denominator)
        return _occupancy_payload(metric, resolved, context, period, asset_or_deal_id, counts)

    if context in (None, "ops_actuals") and asset_or_deal_id:
        from plat_harness.adapters.boxscore import load_occupancy

        loaded = load_occupancy(asset_or_deal_id)
        if loaded is not None:
            counts = OccupancyCounts(
                occupied=int(loaded["occupied"]),
                vacant=int(loaded["vacant"]),
                down=int(loaded["down"]),
                denominator=int(loaded["denominator"]),
            )
            payload = _occupancy_payload(
                metric,
                resolved,
                context or "ops_actuals",
                period or loaded.get("as_of"),
                asset_or_deal_id,
                counts,
            )
            payload["as_of"] = loaded.get("as_of")
            payload["source"] = loaded.get("source") or payload["source"]
            payload["backend"] = loaded.get("backend")
            payload["freshness"] = loaded.get("freshness")
            return payload

    raise HarnessError(
        OCCUPANCY_COUNTS_REQUIRED,
        "Physical occupancy cannot return a number without occupied, vacant, "
        "down, and the denominator used.",
        details={"missing": ["occupied", "vacant", "down", "denominator"]},
    )


def _metric_payload(
    metric: Metric,
    resolved,
    context: str | None,
    period: str | None,
    asset_or_deal_id: str | None,
    loaded: dict[str, Any],
) -> dict[str, Any]:
    payload = dict(loaded)
    payload.update(
        {
            "metric_id": metric.id,
            "term": metric.term,
            "context": resolved.context if resolved else context,
            "formula_owner": resolved.owner if resolved else None,
            "certification": metric.status,
            "period": period or loaded.get("period") or loaded.get("as_of"),
            "asset_or_deal_id": asset_or_deal_id,
            "handshake": metric.handshake,
        }
    )
    return payload


def _resolve_definition(metric: Metric, context: str | None):
    if context:
        found = metric.definition_for(context)
        if found is None and metric.definitions:
            raise HarnessError(
                CONFLICT_UNRESOLVED,
                f"Metric '{metric.id}' has no definition for context '{context}'.",
                details={"metric_id": metric.id, "context": context, "contexts": list(metric.contexts())},
            )
        return found
    if len(metric.definitions) == 1:
        return metric.definitions[0]
    return None


def _occupancy_payload(
    metric: Metric,
    resolved,
    context: str | None,
    period: str | None,
    asset_or_deal_id: str | None,
    counts: OccupancyCounts,
) -> dict[str, Any]:
    payload = counts.as_dict()
    payload.update(
        {
            "metric_id": metric.id,
            "term": metric.term,
            "value": payload["rate"],
            "unit": "ratio",
            "context": resolved.context if resolved else context,
            "formula_owner": resolved.owner if resolved else None,
            "certification": metric.status,
            "period": period,
            "asset_or_deal_id": asset_or_deal_id,
            "source": [
                {
                    "artifact": "occupancy_counts",
                    "row": "occupied/vacant/down",
                    "period": period,
                }
            ],
            "handshake": metric.handshake,
        }
    )
    return payload
