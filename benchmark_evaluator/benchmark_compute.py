"""Benchmark compute facade with truthful commercial scoring semantics.

The matching / coverage implementation remains in ``_benchmark_compute_mechanics``.
This facade changes only the metric projection returned by ``compute_benchmark``:
GT-unmatched customer-deliverable runtime defects are not labelled false
positives, and seeded precision/FPR/F1 stay explicitly NOT_MEASURED.
"""
from __future__ import annotations

from typing import Any

from . import _benchmark_compute_mechanics as _core
from ._benchmark_compute_mechanics import *  # noqa: F401,F403
from .commercial_scoring_contract import apply_commercial_scoring_contract

_original_compute_benchmark = _core.compute_benchmark


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_core)))


def compute_benchmark(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return apply_commercial_scoring_contract(
        _original_compute_benchmark(*args, **kwargs)
    )


compute_benchmark._qualibug_commercial_scoring_contract = True  # type: ignore[attr-defined]
compute_benchmark._qualibug_original_compute_benchmark = (  # type: ignore[attr-defined]
    _original_compute_benchmark
)

# The mechanics end-to-end helper resolves compute_benchmark in its own module
# globals. Point that name at the governed facade implementation so callers of
# run_benchmark_end_to_end cannot bypass the same scoring contract.
_core.compute_benchmark = compute_benchmark

__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "compute_benchmark",
    }
)
