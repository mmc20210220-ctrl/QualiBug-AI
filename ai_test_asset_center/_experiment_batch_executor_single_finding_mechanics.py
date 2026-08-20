"""Batch execution facade with one authoritative service-routing boundary.

The preserved mechanics live in
``_experiment_batch_executor_single_finding_mechanics_base``.  That legacy
implementation still contains a fixed-port, single-service pre-filter that was
correct before project-declared multi-service routing existed.  Keeping that
filter active when a topology is declared silently drops selected experiments
before ``experiment_executor.execute_one_experiment`` can route them to their
own approved service target or emit an explicit routing BLOCKED receipt.

This facade disables only that obsolete pre-filter for the current execution
thread when multi-service routing is authoritative.  The public per-experiment
``service_topology_execution_authority`` remains the sole routing decision.  A
project with no topology keeps the historical single-target behavior unchanged.
"""
from __future__ import annotations

from pathlib import Path
import threading
from typing import Any

from . import _experiment_batch_executor_single_finding_mechanics_base as _base


for _name in dir(_base):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_base, _name)


_original_execute_selected_experiments = _base.execute_selected_experiments
_CONTEXT_ATTR = "_qualibug_batch_service_filter_context"
_BRIDGE_MARKER = "_qualibug_topology_routing_bridge"


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))


def _topology_routing_is_authoritative(project: str, root: Path) -> bool:
    """True when the public topology router must see every selected experiment.

    A malformed declared topology is authoritative too: the public router must
    emit its typed BLOCKED receipt.  Falling back to the legacy port filter in
    that case would convert an observable configuration error into silent Recall
    loss.
    """

    try:
        from .service_topology_config_guard import (
            load_guarded_project_service_topology,
        )

        topology, receipt = load_guarded_project_service_topology(
            str(project), Path(root)
        )
    except Exception:
        # The same guarded loader is invoked again at the public per-experiment
        # boundary.  Let that authority fail closed with a receipt instead of
        # allowing this compatibility filter to erase the candidate first.
        return True
    status = str((receipt or {}).get("status") or "").strip().upper()
    return bool(topology) or status == "BLOCKED"


def _service_filter_context() -> threading.local:
    """Install a thread-local bridge over the historical fixed-port helper."""

    from . import discovery_runtime_planning as planning

    context = getattr(planning, _CONTEXT_ATTR, None)
    if not isinstance(context, threading.local):
        context = threading.local()
        setattr(planning, _CONTEXT_ATTR, context)

    current = planning._target_service_name_from_base_url
    if not getattr(current, _BRIDGE_MARKER, False):
        original = current

        def _bridge(base_url: str) -> str:
            active = getattr(planning, _CONTEXT_ATTR, None)
            if active is not None and bool(
                getattr(active, "topology_authoritative", False)
            ):
                return ""
            return original(base_url)

        setattr(_bridge, _BRIDGE_MARKER, True)
        setattr(_bridge, "_qualibug_original", original)
        planning._target_service_name_from_base_url = _bridge
    return context


def execute_selected_experiments(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Execute the preserved batch mechanics without pre-routing candidate loss."""

    context = _service_filter_context()
    previous = bool(getattr(context, "topology_authoritative", False))
    context.topology_authoritative = _topology_routing_is_authoritative(
        str(kwargs.get("project") or ""),
        Path(kwargs.get("root") or "."),
    )
    try:
        return _original_execute_selected_experiments(*args, **kwargs)
    finally:
        context.topology_authoritative = previous


__all__ = sorted(
    {
        *[
            name
            for name in dir(_base)
            if not name.startswith("__")
        ],
        "execute_selected_experiments",
    }
)
