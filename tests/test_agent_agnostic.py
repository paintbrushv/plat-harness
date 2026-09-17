"""Agent-agnostic contracts: no Claude/Cursor/Anthropic runtime."""

from __future__ import annotations

import ast
import shutil
import tomllib
from pathlib import Path

import pytest

from plat_harness.errors import HarnessError
from plat_harness.models import ModelRouter, NullModel

REPO_ROOT = Path(__file__).resolve().parents[1]
HARNESS_SRC = REPO_ROOT / "harness" / "src"
FORBIDDEN_DEPS = frozenset(
    {
        "claude-agent-sdk",
        "anthropic",
        "cursor",
        "claude-code-sdk",
        "anthropic-client",
    }
)
FORBIDDEN_IMPORTS = frozenset(
    {
        "anthropic",
        "claude_agent_sdk",
        "plat_agent.dispatch",
        "plat_agent.dispatch.sibling",
    }
)


def test_pyproject_has_no_vendor_agent_deps() -> None:
    raw = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = list(raw["project"].get("dependencies") or [])
    for extra in (raw["project"].get("optional-dependencies") or {}).values():
        deps.extend(extra)
    names = {dep.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip().lower() for dep in deps}
    assert names.isdisjoint({item.lower() for item in FORBIDDEN_DEPS})
    assert "pyyaml" in names
    assert "click" not in names


def test_source_does_not_import_vendor_or_claude_dispatch() -> None:
    offenders = []
    for path in HARNESS_SRC.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in FORBIDDEN_IMPORTS or alias.name.split(".")[0] in {
                        "anthropic",
                        "claude_agent_sdk",
                    }:
                        offenders.append(f"{path}: import {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                if node.module in FORBIDDEN_IMPORTS or node.module.startswith("plat_agent.dispatch"):
                    offenders.append(f"{path}: from {node.module}")
                if node.module.split(".")[0] in {"anthropic", "claude_agent_sdk"}:
                    offenders.append(f"{path}: from {node.module}")
    assert offenders == []


def test_source_has_no_claude_dash_p() -> None:
    hits = []
    for path in HARNESS_SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        if "claude -p" in text:
            hits.append(str(path))
    assert hits == []


def test_claude_binary_may_be_missing() -> None:
    _ = shutil.which("claude")


def test_null_model_is_a_model_router() -> None:
    model: ModelRouter = NullModel()
    assert model.model_id == "null"
    with pytest.raises(HarnessError) as caught:
        model.complete([], [])
    assert caught.value.code == "NO_MODEL_CONFIGURED"
