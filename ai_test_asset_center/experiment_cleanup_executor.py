"""Public cleanup executor facade.

The core module retains the existing compensation implementation. The lifecycle
adapter adds precondition and process-graph visibility without changing public
call sites. Every prior non-dunder symbol is re-exported for compatibility.
"""
from __future__ import annotations

from typing import Any

from . import experiment_cleanup_executor_core as _core
from . import experiment_cleanup_lifecycle_adapter as _adapter

for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def _sync_core_hooks() -> None:
    """Keep established monkeypatch/runtime injection points authoritative."""
    for name in (
        "execute_governed_control_write",
        "sandbox_write_allowed",
        "_http_request",
    ):
        if name in globals() and hasattr(_core, name):
            setattr(_core, name, globals()[name])


def execute_experiment_cleanup_compensation(**kwargs: Any) -> dict[str, Any]:
    _sync_core_hooks()
    return _adapter.execute_experiment_cleanup_compensation(**kwargs)


__all__ = sorted(
    name
    for name in globals()
    if not name.startswith("__")
    and name not in {"_core", "_adapter", "_name"}
)
