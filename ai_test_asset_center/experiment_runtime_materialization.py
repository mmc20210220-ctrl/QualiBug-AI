"""Runtime materialization facade with obligation-scoped cleanup authority."""
from __future__ import annotations
from typing import Any

from . import experiment_runtime_materialization_mainline_base as _base
from .runtime_materialization_cleanup_authority import (
    install_runtime_materialization_cleanup_authority,
)

install_runtime_materialization_cleanup_authority(_base)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))
