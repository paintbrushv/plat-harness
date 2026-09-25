"""Synthetic walkthrough fixtures shipped with the wheel.

All files are fabricated demo data (reportlab/openpyxl-generated); they carry
no real property, tenant, or portfolio information. The synthetic sample
notice lives in ``samples_data/manifest.json``.
"""
from pathlib import Path

WALKTHROUGH_DIR = Path(__file__).resolve().parent


def walkthrough_path(name: str) -> Path:
    """Absolute path of a shipped walkthrough fixture by filename."""
    p = WALKTHROUGH_DIR / name
    if not p.is_file():
        raise FileNotFoundError(
            f"walkthrough fixture not shipped in this wheel: {name}"
        )
    return p
