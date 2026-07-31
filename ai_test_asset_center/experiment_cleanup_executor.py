"""Public cleanup executor facade.

The core module retains the existing compensation implementation. The lifecycle
adapter adds precondition-write visibility without changing cleanup algorithms
or public call sites. Every prior non-dunder symbol is re-exported so internal
compatibility imports keep the same surface after the module split.
"""
from . import experiment_cleanup_executor_core as _core

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)

from .experiment_cleanup_lifecycle_adapter import (
    execute_experiment_cleanup_compensation,
)

__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__") and name not in {"_core", "_name"}
)
