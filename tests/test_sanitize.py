"""Public-tree sanitize gates. Fail if private literals leak into the extract."""

from __future__ import annotations

from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_SELF = Path(__file__).resolve()

# Private overlay / live-data literals. Vendor SDK names may appear in
# "do not depend" docs; pyproject is gated by test_agent_agnostic.py.
_FORBIDDEN_SNIPPETS = (
    "your-org-name",
    "00_PRIVATE_DEPARTMENT",
    "private_tracker",
    "your-org-funds",
    "sample_deal_alpha",
    "Property One",
    "SANITIZE.md",
    "PRIVATE_SESSION_PROMPT",
    "ACCT1",
    "acct-001",
    "Property_Two",
    # Host identity and private data roots: the public tree must carry no
    # compiled-in host paths, hostnames, or the private overlay root.
    "/home/mdai",
    "spark-17d5",
    "data/uplift",
    "qwen36-unsloth",
)

_SAMPLE_GLOBS = (
    "samples/**/*.csv",
    "samples/**/*.md",
    "samples/**/*.json",
)


def _iter_text_files() -> list[Path]:
    skip_dirs = {".git", ".venv", ".deps", "__pycache__", ".pytest_cache", ".pytest-tmp"}
    files: list[Path] = []
    for path in REPO.rglob("*"):
        if not path.is_file():
            continue
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.suffix.lower() in {".png", ".jpg", ".pdf", ".db"}:
            continue
        files.append(path)
    return files


def test_no_private_literals_in_tree() -> None:
    hits: list[str] = []
    for path in _iter_text_files():
        if path.resolve() == _SELF:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for snippet in _FORBIDDEN_SNIPPETS:
            if snippet in text:
                hits.append(f"{path.relative_to(REPO)}: {snippet}")
    assert hits == []


def test_samples_have_no_resident_name_column() -> None:
    for path in REPO.glob("samples/**/*.csv"):
        header = path.read_text(encoding="utf-8").splitlines()[0].lower()
        assert "resident_name" not in header, path
        assert "resident" not in {h.strip() for h in header.split(",")}, path


def test_no_boxscore_db_in_tree() -> None:
    # Test fixtures legitimately create synthetic ops.db files under the
    # repo-local basetemp; the gate targets the SHIPPED tree.
    skip_dirs = {".git", ".venv", ".deps", "__pycache__", ".pytest_cache", ".pytest-tmp"}
    dbs = [
        path for path in REPO.glob("**/*.db")
        if not any(part in skip_dirs for part in path.parts)
    ]
    assert dbs == []
