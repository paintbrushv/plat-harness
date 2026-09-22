"""plat_harness — agent-agnostic control plane. Slice 0: stubs and refusals."""

from plat_harness.errors import HarnessError
from plat_harness.glossary import Glossary, load_glossary
from plat_harness.models import ModelRouter, ModelTurn, NullModel
from plat_harness.ranks import PermissionRank

__all__ = [
    "Glossary",
    "HarnessError",
    "ModelRouter",
    "ModelTurn",
    "NullModel",
    "PermissionRank",
    "load_glossary",
]

__version__ = "0.1.0"
