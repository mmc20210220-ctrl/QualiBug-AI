"""Single-authority discovery planning and experiment-candidate runtime.

Planning lives in ``discovery_runtime_planning``; execution lives in
``discovery_runtime_execution``. This module re-exports the public surface for
compatibility with ``v12_pipeline`` and existing tests. The public planning
entry installs exact accepted rule/interface identity binding before any plan
is compiled. The public execution entry projects receipt-backed evidence and an
honest loss funnel without creating findings or inventing quality metrics.

The formal UI surface is installed on the same experiment mainline as API and
persistence obligations. It registers a source-declared browser protocol,
typed observer and assertion kind. The read-only guard blocks click/fill/select
plans until browser-side cleanup equivalence exists. Importing this module
registers capability only; it opens no browser and performs no target I/O.
"""
from __future__ import annotations

from .formal_ui_surface import install_formal_ui_surface
from .formal_ui_surface_guard import install_formal_ui_read_only_guard

install_formal_ui_surface()
install_formal_ui_read_only_guard()

from .discovery_runtime_execution import (  # noqa: E402,F401
    RUNTIME_SCHEMA,
    _authority_findings,
    _legacy_execution_terminal,
    _manual_terminal_receipts,
    _project_gate_results_for_authority,
)
from .discovery_runtime_planning import (  # noqa: E402,F401
    _api_operations,
    _campaign_object,
    _campaign_store,
    _contract,
    _runtime_actors,
)
from .discovery_runtime_semantic_binding import (  # noqa: E402,F401
    build_discovery_plan,
)
from .formal_event_binding_receipt_bridge import (  # noqa: E402
    install_formal_event_binding_receipt_bridge,
)
from .formal_event_verdict_reason_bridge import (  # noqa: E402
    install_formal_event_verdict_reason_bridge,
)

# Semantic binding registers the event observer, assertion and pre-cleanup wrapper first.
# These additive bridges then wrap the current registered authorities in that exact order.
install_formal_event_binding_receipt_bridge()
install_formal_event_verdict_reason_bridge()

from .discovery_runtime_quality_projection import (  # noqa: E402,F401
    run_experiment_candidate,
)

__all__ = [
    "RUNTIME_SCHEMA",
    "build_discovery_plan",
    "run_experiment_candidate",
]
