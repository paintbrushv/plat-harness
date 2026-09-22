"""Resolve live data roots from env only. Never copy those files into git."""

from __future__ import annotations

import os
from pathlib import Path


def ops_root() -> Path | None:
    return _env_dir("PLAT_HARNESS_OPS_ROOT")


def deal_root() -> Path | None:
    return _env_dir("PLAT_HARNESS_DEAL_ROOT")


def boxscore_db() -> Path | None:
    return _env_file("PLAT_HARNESS_BOXSCORE_DB")


def engine_root() -> Path | None:
    """Path to a Decimal engine checkout. Public tree ships a stub, not the engine."""
    return _env_dir("PLAT_HARNESS_ENGINE_ROOT") or _env_dir("PLAT_HARNESS_UW_ROOT")


def uw_root() -> Path | None:
    return engine_root()


def uw_golden_dir() -> Path | None:
    explicit = _env_dir("PLAT_HARNESS_UW_GOLDEN_DIR")
    if explicit:
        return explicit
    output_file = _env_file("PLAT_HARNESS_GOLDEN_UW_OUTPUT")
    if output_file:
        return output_file.parent
    root = engine_root()
    if root:
        output = root / "output"
        if output.is_dir():
            return output
    return None


def ops_backend_configured() -> bool:
    db = boxscore_db()
    if db is not None:
        return True
    root = ops_root()
    return root is not None and root.is_dir()


def uw_backend_configured() -> bool:
    if engine_root() is not None:
        return True
    golden = uw_golden_dir()
    if golden and any(golden.glob("*_model_outputs.json")):
        return True
    output_file = _env_file("PLAT_HARNESS_GOLDEN_UW_OUTPUT")
    if output_file is not None:
        return True
    root = deal_root()
    return root is not None and root.is_dir()


def _env_dir(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_dir() else None


def _env_file(name: str) -> Path | None:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None
