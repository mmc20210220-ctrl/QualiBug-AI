"""Single-authority discovery planning and experiment-candidate runtime.

Planning lives in ``discovery_runtime_planning``; execution lives in
``discovery_runtime_execution``. This module re-exports the public surface for
compatibility with ``v12_pipeline`` and existing tests. The public planning
entry installs exact accepted rule/interface identity binding before any plan
is compiled, and the public execution entry projects receipt-backed evidence
without creating additional findings.
"""
from __future__ import annotations

from .discovery_runtime_execution import (  # noqa: F401
    RUNTIME_SCHEMA,
    _authority_findings,
    _legacy_execution_terminal,
    _manual_terminal_receipts,
    _project_gate_results_for_authority,
)
from .discovery_runtime_planning import (  # noqa: F401
    _api_operations,
    _campaign_object,
    _campaign_store,
    _contract,
    _runtime_actors,
)
from .discovery_runtime_semantic_binding import (  # noqa: F401
    build_discovery_plan,
)
from .formal_evidence_projection import (  # noqa: F401
    run_experiment_candidate,
)

__all__ = [
    "RUNTIME_SCHEMA",
    "build_discovery_plan",
    "run_experiment_candidate",
]
