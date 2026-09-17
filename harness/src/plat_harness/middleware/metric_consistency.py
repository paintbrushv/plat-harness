"""MetricConsistency: do not answer with uncertified or untagged CONFLICT metrics."""

from __future__ import annotations

from collections.abc import Iterable

from plat_harness.errors import (
    CONFLICT_UNRESOLVED,
    HarnessError,
    UNCERTIFIED_CONTEXTS,
    UNCERTIFIED_METRIC,
)
from plat_harness.glossary import Glossary, load_glossary


def check_metric_consistency(
    metric_ids: Iterable[str],
    *,
    context: str | None = None,
    glossary: Glossary | None = None,
) -> None:
    """Raise if any cited metric would be illegal to answer under this context."""
    glossary = glossary or load_glossary()
    if context in UNCERTIFIED_CONTEXTS:
        raise HarnessError(
            UNCERTIFIED_METRIC,
            f"Context '{context}' is uncertified and cannot back a numeric answer.",
            details={"context": context},
        )
    for metric_id in metric_ids:
        metric = glossary.require(metric_id)
        if metric.status == "UNKNOWN":
            raise HarnessError(
                UNCERTIFIED_METRIC,
                f"Metric '{metric_id}' is UNKNOWN.",
                details={"metric_id": metric_id},
            )
        needs_context = metric.status == "CONFLICT" or len(metric.definitions) > 1
        if needs_context and not context:
            raise HarnessError(
                CONFLICT_UNRESOLVED,
                f"Cannot cite '{metric_id}' without a context tag.",
                details={"metric_id": metric_id, "status": metric.status},
            )
        if context and metric.definitions and metric.definition_for(context) is None:
            raise HarnessError(
                CONFLICT_UNRESOLVED,
                f"Metric '{metric_id}' has no definition for context '{context}'.",
                details={"metric_id": metric_id, "context": context},
            )
