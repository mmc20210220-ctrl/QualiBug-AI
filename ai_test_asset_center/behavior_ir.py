"""Public Behavior IR facade with fail-closed compensation derivation."""
from __future__ import annotations
from typing import Any

from . import behavior_ir_mainline_base as _base
from .compensation_derivation_authority import install_compensation_derivation_authority

install_compensation_derivation_authority(_base._core)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
