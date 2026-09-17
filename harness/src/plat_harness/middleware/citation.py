"""Citation: every number in the final message must point at artifact + row/period."""

from __future__ import annotations

import re
from dataclasses import dataclass

from plat_harness.errors import CITATION_REQUIRED, HarnessError

# Financial-ish numbers, percents, and millage-style decimals. Years like 2026 are
# excluded so narrative dates do not require a citation.
_NUMBER = re.compile(
    r"\$\s*\d{1,3}(?:,\d{3})*(?:\.\d+)?"
    r"|\b\d{1,3}(?:,\d{3})+(?:\.\d+)?\b"
    r"|\b\d+\.\d+\b"
    r"|\b\d+%"
)
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Citation:
    artifact: str
    row: str | None = None
    period: str | None = None
    value: str | None = None

    def is_complete(self) -> bool:
        return bool(self.artifact) and bool(self.row or self.period)


def check_citations(text: str, citations: list[Citation] | None) -> None:
    """Refuse any sentence that contains a number without a complete citation."""
    citations = citations or []
    complete = [item for item in citations if item.is_complete()]
    for sentence in _sentences(text):
        if not _has_number(sentence):
            continue
        if not complete:
            raise HarnessError(
                CITATION_REQUIRED,
                "A numeric claim is missing artifact + row/period citation.",
                details={"sentence": sentence.strip()},
            )


def _sentences(text: str) -> list[str]:
    parts = _SENTENCE.split(text.strip()) if text.strip() else []
    return [part for part in parts if part.strip()] or ([text] if text.strip() else [])


def _has_number(sentence: str) -> bool:
    stripped = _YEAR.sub(" ", sentence)
    return _NUMBER.search(stripped) is not None
