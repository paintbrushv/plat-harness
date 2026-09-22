"""Tests share a glossary and strip vendor API keys plus private backend env."""

from __future__ import annotations

import os

import pytest

from plat_harness.glossary import load_glossary

_VENDOR_KEYS = (
    "ANTHROPIC_API_KEY",
    "OPENAI_API_KEY",
    "CLAUDE_API_KEY",
    "CURSOR_API_KEY",
)
_BACKEND_ENV = (
    "PLAT_HARNESS_OPS_ROOT",
    "PLAT_HARNESS_DEAL_ROOT",
    "PLAT_HARNESS_BOXSCORE_DB",
    "PLAT_HARNESS_UW_ROOT",
    "PLAT_HARNESS_ENGINE_ROOT",
    "PLAT_HARNESS_UW_GOLDEN_DIR",
    "PLAT_HARNESS_GOLDEN_UW_OUTPUT",
)


@pytest.fixture(autouse=True)
def _strip_vendor_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in _VENDOR_KEYS + _BACKEND_ENV:
        monkeypatch.delenv(key, raising=False)


@pytest.fixture(scope="session")
def glossary():
    return load_glossary()
