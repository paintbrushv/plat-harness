"""Load the packaged glossary.yaml. CONFLICT rows must not be averaged."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any, Iterator

import yaml

from plat_harness.errors import HarnessError, NOT_FOUND

_STATUS_VALUES = frozenset(
    {"CERTIFIED", "CONFLICT", "DIRTY", "UNSTRUCTURED", "UNKNOWN"}
)


@dataclass(frozen=True)
class MetricDefinition:
    context: str
    owner: str | None = None
    formula: str | None = None
    money: str | None = None
    notes: str | None = None
    hurdle: str | None = None


@dataclass(frozen=True)
class Metric:
    id: str
    term: str
    status: str
    definitions: tuple[MetricDefinition, ...]
    handshake: str = ""
    notes: str | None = None

    def contexts(self) -> tuple[str, ...]:
        return tuple(d.context for d in self.definitions)

    def definition_for(self, context: str) -> MetricDefinition | None:
        for item in self.definitions:
            if item.context == context:
                return item
        return None


@dataclass(frozen=True)
class Glossary:
    version: str
    as_of: str | None
    metrics: dict[str, Metric]
    raw: dict[str, Any]

    def get(self, metric_id: str) -> Metric | None:
        return self.metrics.get(metric_id)

    def require(self, metric_id: str) -> Metric:
        metric = self.get(metric_id)
        if metric is None:
            raise HarnessError(
                NOT_FOUND,
                f"Metric '{metric_id}' is not in the certified glossary.",
                details={"metric_id": metric_id},
            )
        return metric

    def __iter__(self) -> Iterator[Metric]:
        return iter(self.metrics.values())


def packaged_glossary_text() -> str | None:
    """Glossary bytes shipped inside the plat_harness package."""
    resource = files("plat_harness").joinpath("glossary.yaml")
    if not resource.is_file():
        return None
    return resource.read_text(encoding="utf-8")


def default_glossary_path() -> Path:
    env = os.environ.get("PLAT_HARNESS_GLOSSARY")
    if env:
        return Path(env)
    sibling = Path(__file__).resolve().parent / "glossary.yaml"
    if sibling.is_file():
        return sibling
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    # harness/src/plat_harness/glossary.py → repo root / docs/glossary.yaml
    try:
        candidates.append(here.parents[3] / "docs" / "glossary.yaml")
    except IndexError:
        pass
    try:
        candidates.append(here.parents[2] / "docs" / "glossary.yaml")
    except IndexError:
        pass
    # Source checkout without the packaged sibling: walk up to docs/glossary.yaml.
    for parent in here.parents:
        candidates.append(parent / "docs" / "glossary.yaml")
    seen: set[Path] = set()
    for candidate in candidates:
        resolved = candidate.resolve() if candidate.exists() else candidate
        if resolved in seen:
            continue
        seen.add(resolved)
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "glossary.yaml not found; set PLAT_HARNESS_GLOSSARY or install from the public repo"
    )


def load_glossary(path: Path | None = None) -> Glossary:
    origin, raw = _read_glossary(path)
    if not isinstance(raw, dict) or "metrics" not in raw:
        raise ValueError(f"Invalid glossary at {origin}")
    metrics: dict[str, Metric] = {}
    for metric_id, body in (raw.get("metrics") or {}).items():
        if not isinstance(body, dict):
            continue
        status = str(body.get("status") or "UNKNOWN")
        if status not in _STATUS_VALUES:
            status = "UNKNOWN"
        definitions = []
        for item in body.get("definitions") or []:
            if not isinstance(item, dict) or not item.get("context"):
                continue
            definitions.append(
                MetricDefinition(
                    context=str(item["context"]),
                    owner=_opt_str(item.get("owner")),
                    formula=_opt_str(item.get("formula")),
                    money=_opt_str(item.get("money")),
                    notes=_opt_str(item.get("notes")),
                    hurdle=_opt_str(item.get("hurdle")),
                )
            )
        metrics[str(metric_id)] = Metric(
            id=str(metric_id),
            term=str(body.get("term") or metric_id),
            status=status,
            definitions=tuple(definitions),
            handshake=str(body.get("handshake") or ""),
            notes=_opt_str(body.get("notes")),
        )
    return Glossary(
        version=str(raw.get("version") or ""),
        as_of=_opt_str(raw.get("as_of")),
        metrics=metrics,
        raw=raw,
    )


def _read_glossary(path: Path | None) -> tuple[str, Any]:
    if path is not None:
        return str(path), yaml.safe_load(path.read_text(encoding="utf-8"))
    env = os.environ.get("PLAT_HARNESS_GLOSSARY")
    if env:
        return env, yaml.safe_load(Path(env).read_text(encoding="utf-8"))
    packaged = packaged_glossary_text()
    if packaged is not None:
        return "plat_harness/glossary.yaml", yaml.safe_load(packaged)
    glossary_path = default_glossary_path()
    return str(glossary_path), yaml.safe_load(glossary_path.read_text(encoding="utf-8"))


def _opt_str(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None
