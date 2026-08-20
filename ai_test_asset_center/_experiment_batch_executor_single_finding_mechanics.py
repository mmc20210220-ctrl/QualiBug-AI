"""Batch execution facade with one authoritative service-routing boundary.

The preserved mechanics live in
``_experiment_batch_executor_single_finding_mechanics_base``. That legacy
implementation still contains a fixed-port, single-service pre-filter and a
string-only service URL loader that predate project-declared multi-service
routing. Keeping either behavior active for a declared topology can destroy
Recall before the governed per-experiment router gets a chance to act.

This facade keeps the old mechanics byte-for-byte behind a compatibility layer:
for topology-backed runs the public ``service_topology_execution_authority`` is
the sole routing authority, and resolver URLs come from the same normalized
approved topology. Projects with no topology retain the historical single-target
behavior unchanged.
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
_original_load_service_base_urls = _base._load_service_base_urls
_CONTEXT_ATTR = "_qualibug_batch_service_filter_context"
_BRIDGE_MARKER = "_qualibug_topology_routing_bridge"


def __getattr__(name: str) -> Any:
    return getattr(_base, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_base)))


def _guarded_topology(project: str, root: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    from .service_topology_config_guard import load_guarded_project_service_topology

    return load_guarded_project_service_topology(str(project), Path(root))


def _topology_routing_is_authoritative(project: str, root: Path) -> bool:
    """True when the public topology router must see every selected experiment.

    A malformed declared topology is authoritative too: the public router must
    emit its typed BLOCKED receipt. Falling back to the legacy port filter in
    that case would convert an observable configuration error into silent Recall
    loss.
    """

    try:
        topology, receipt = _guarded_topology(project, root)
    except Exception:
        # The same guarded loader is invoked again at the public per-experiment
        # boundary. Let that authority fail closed with a receipt instead of
        # allowing this compatibility filter to erase the candidate first.
        return True
    status = str((receipt or {}).get("status") or "").strip().upper()
    return bool(topology) or status == "BLOCKED"


def _source_truthful_service_base_urls(project: str, root: Path) -> dict[str, str]:
    """Resolve cross-service binding URLs from the normalized topology authority.

    The preserved mechanics used ``str(value)`` for every ``services`` entry.
    Modern configuration also permits object rows such as
    ``{"base_url": ..., "actor_token_keys": ...}``; stringifying such a row
    produces a dict representation, not a URL, and makes resolver/fixture reads
    fail systematically. Consume the same normalized ``approved_base_url`` used
    by execution routing. If no topology is declared, preserve legacy behavior.
    """

    try:
        topology, receipt = _guarded_topology(project, root)
    except Exception:
        return {}
    status = str((receipt or {}).get("status") or "").strip().upper()
    if topology:
        return {
            str(name): str((row or {}).get("approved_base_url") or "").strip()
            for name, row in topology.items()
            if str(name).strip()
            and isinstance(row, dict)
            and str(row.get("approved_base_url") or "").strip()
        }
    if status == "BLOCKED":
        # Do not manufacture resolver routes from malformed configuration; the
        # public router will surface the typed topology BLOCKED receipt.
        return {}
    return _original_load_service_base_urls(project, root)


# The preserved function resolves this helper from its own module globals.
# Installing the normalized loader here upgrades resolver/fixture routing
# without copying or rewriting the 1200-line mechanics implementation.
_base._load_service_base_urls = _source_truthful_service_base_urls


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
        "_source_truthful_service_base_urls",
    }
)
