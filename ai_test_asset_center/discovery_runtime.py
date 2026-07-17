"""Single-authority discovery planning and experiment-candidate runtime.

Planning lives in ``discovery_runtime_planning``; execution lives in
``discovery_runtime_execution``. This module re-exports the public surface for
compatibility with ``v12_pipeline`` and existing tests.
"""
from __future__ import annotations

from .discovery_runtime_execution import (  # noqa: F401
    RUNTIME_SCHEMA,
    _authority_findings,
    _legacy_execution_terminal,
    _manual_terminal_receipts,
    _project_gate_results_for_authority,
    run_experiment_candidate,
)
from .discovery_runtime_planning import (  # noqa: F401
    _api_operations,
    _campaign_object,
    _campaign_store,
    _contract,
    _runtime_actors,
    build_discovery_plan,
)

__all__ = [
    "RUNTIME_SCHEMA",
    "build_discovery_plan",
    "run_experiment_candidate",
]
