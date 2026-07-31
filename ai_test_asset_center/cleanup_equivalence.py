"""Public cleanup equivalence authority.

Ordinary experiments delegate byte-for-byte to ``cleanup_equivalence_core``.
A process-graph proof set is evaluated by applying that same core engine to each
source step and aggregating only the resulting formal receipts.
"""
from __future__ import annotations

from typing import Any

from . import cleanup_equivalence_core as _core
from .process_graph_cleanup_equivalence import (
    evaluate_process_graph_cleanup_equivalence,
)
from .process_graph_reversibility import (
    is_process_graph_reversibility_proof,
)


for _name in dir(_core):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_core, _name)


def evaluate_cleanup_equivalence(
    *,
    proof: dict[str, Any],
    before_observation: dict[str, Any],
    after_write_observation: dict[str, Any],
    after_cleanup_observation: dict[str, Any],
    runtime_bindings: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Dispatch ordinary or per-source-step graph equivalence."""
    if is_process_graph_reversibility_proof(proof):
        return evaluate_process_graph_cleanup_equivalence(
            proof=proof,
            cleanup_execution_receipt=cleanup_execution_receipt,
        )
    return _core.evaluate_cleanup_equivalence(
        proof=proof,
        before_observation=before_observation,
        after_write_observation=after_write_observation,
        after_cleanup_observation=after_cleanup_observation,
        runtime_bindings=runtime_bindings,
        cleanup_execution_receipt=cleanup_execution_receipt,
    )


__all__ = sorted(
    {
        *[
            name
            for name in dir(_core)
            if not name.startswith("__")
        ],
        "evaluate_cleanup_equivalence",
    }
)
