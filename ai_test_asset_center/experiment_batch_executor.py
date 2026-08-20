"""Batch-execution facade retaining governed observations for runtime feedback.

The complete existing batch/fanout implementation is preserved in
``experiment_batch_executor_base``. This boundary adds only an evidence
projection after the original batch has finalized: receipt-backed observations
already present on raw outcomes are mirrored into ``execution_results`` so the
existing Runtime Fact Candidate feedback path does not lose them on BLOCKED or
HARNESS terminal projections.
"""
from __future__ import annotations

from typing import Any

from . import experiment_batch_executor_base as _base
from .runtime_feedback_observation_projection import (
    attach_runtime_feedback_observation_evidence,
)

for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_original_execute_selected_experiments = _base.execute_selected_experiments


def execute_selected_experiments(*args: Any, **kwargs: Any) -> dict[str, Any]:
    batch = _original_execute_selected_experiments(*args, **kwargs)
    return attach_runtime_feedback_observation_evidence(batch)


__all__ = sorted(
    {
        *[
            name for name in dir(_base)
            if not name.startswith("__")
        ],
        "execute_selected_experiments",
    }
)
