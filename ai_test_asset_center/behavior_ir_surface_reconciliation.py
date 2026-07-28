"""Reconcile declared adapter surfaces with the canonical Behavior IR.

The base Behavior IR builder historically enumerates only HTTP, UI and DB surfaces. Adapter
capability is now extensible and already returns product-owned process timelines and explicit
event streams, but those declarations were silently dropped before obligation compilation.
This additive reconciliation keeps the builder's existing nodes and appends/updates every
surface named by ``available_surfaces``.

No capability is inferred. A surface exists here only because the planner passed the explicit
adapter-derived declaration map. Unknown future surfaces are represented with a generic label
and no invented capability node unless adapter_capability declares the corresponding mapping.
"""
from __future__ import annotations

import copy
from typing import Any

from . import behavior_ir as _bir
from .adapter_capability import (
    ADAPTER_TO_CAPABILITY,
    ADAPTER_TO_OBSERVATION_SURFACE,
)

RECONCILIATION_SCHEMA = "qualibug.behavior-ir-surface-reconciliation.v1"
_SURFACE_LABELS = {
    "http_api": "HTTP/API",
    "ui_browser": "Browser/UI",
    "db_snapshot": "DB read snapshot",
    "process_timeline": "Governed process timeline",
    "event_stream": "Source-declared event stream",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _surface_capability_map() -> dict[str, tuple[str, str]]:
    """Return surface -> (adapter, capability) from the one capability registry."""
    resolved: dict[str, tuple[str, str]] = {}
    for adapter, surface in ADAPTER_TO_OBSERVATION_SURFACE.items():
        capability = _text(ADAPTER_TO_CAPABILITY.get(adapter))
        if _text(surface) and capability:
            resolved[_text(surface)] = (_text(adapter), capability)
    return resolved


def reconcile_declared_observation_surfaces(
    behavior_ir: dict[str, Any],
    available_surfaces: dict[str, bool] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Make IR surfaces/capabilities exactly reflect the declared surface map."""
    model = copy.deepcopy(_dict(behavior_ir))
    if not isinstance(available_surfaces, dict):
        receipt = {
            "schema_version": RECONCILIATION_SCHEMA,
            "status": "NOT_REQUESTED",
            "declared_surface_count": 0,
            "surface_added_count": 0,
            "surface_updated_count": 0,
            "capability_added_count": 0,
        }
        model["observation_surface_reconciliation_receipt"] = receipt
        return model, receipt

    declared = {
        _text(surface): bool(value)
        for surface, value in available_surfaces.items()
        if _text(surface)
    }
    surfaces = [
        dict(row)
        for row in _list(model.get("observation_surfaces"))
        if isinstance(row, dict)
    ]
    by_surface = {
        _text(row.get("surface")): row
        for row in surfaces
        if _text(row.get("surface"))
    }
    added = 0
    updated = 0
    for surface_id, is_available in sorted(declared.items()):
        existing = by_surface.get(surface_id)
        if existing is not None:
            expected_status = "accepted" if is_available else "unknown"
            expected_confidence = 1.0 if is_available else 0.3
            if (
                existing.get("available") is not is_available
                or _text(existing.get("status")) != expected_status
                or _text(existing.get("availability_basis"))
                != "declared_adapter_capability"
            ):
                existing.update({
                    "available": is_available,
                    "availability_basis": "declared_adapter_capability",
                    "confidence": expected_confidence,
                    "derivation": "explicit",
                    "status": expected_status,
                })
                updated += 1
            continue
        node = _bir._fact_node(
            node_id=_bir._stable_id("surface", surface_id),
            typed_fields={
                "surface": surface_id,
                "label": _SURFACE_LABELS.get(
                    surface_id,
                    "Declared observation surface: " + surface_id,
                ),
                "available": is_available,
                "availability_basis": "declared_adapter_capability",
            },
            confidence=1.0 if is_available else 0.3,
            derivation="explicit",
            status="accepted" if is_available else "unknown",
        )
        surfaces.append(node)
        by_surface[surface_id] = node
        added += 1
    model["observation_surfaces"] = surfaces

    capabilities = [
        dict(row)
        for row in _list(model.get("capabilities"))
        if isinstance(row, dict)
    ]
    existing_capabilities = {
        _text(row.get("capability"))
        for row in capabilities
        if _text(row.get("capability"))
    }
    capability_added = 0
    surface_capabilities = _surface_capability_map()
    for surface_id, is_available in sorted(declared.items()):
        if not is_available or surface_id not in surface_capabilities:
            continue
        adapter, capability = surface_capabilities[surface_id]
        if capability in existing_capabilities:
            continue
        capabilities.append(_bir._fact_node(
            node_id=_bir._stable_id("cap", capability),
            typed_fields={
                "capability": capability,
                "adapter": adapter,
                "surface": surface_id,
                "availability_basis": "declared_adapter_capability",
            },
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        ))
        existing_capabilities.add(capability)
        capability_added += 1
    model["capabilities"] = capabilities

    receipt = {
        "schema_version": RECONCILIATION_SCHEMA,
        "status": "RECONCILED",
        "declared_surface_count": len(declared),
        "available_surface_count": sum(1 for value in declared.values() if value),
        "surface_added_count": added,
        "surface_updated_count": updated,
        "capability_added_count": capability_added,
        "available_surfaces": sorted(
            surface for surface, value in declared.items() if value
        ),
        "declaration_basis": "planner_adapter_capability_map",
    }
    model["observation_surface_reconciliation_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError(
            "observation_surface_reconciliation_invalid:"
            + ",".join(errors[:12])
        )
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


__all__ = ["reconcile_declared_observation_surfaces"]
