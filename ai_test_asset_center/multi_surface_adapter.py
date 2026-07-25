"""Multi-Surface Adapter — unified execution across surfaces.

SPEC §20-23: Unified interface for API/UI/FILE/EVENT/BATCH/EXTERNAL surfaces.
Each adapter implements: prepare/execute/capture_receipt/wait_for_completion/cleanup.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "sa_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Surface Types ─────────────────────────────────────────────────────────────

SURFACE_TYPES = frozenset({
    "API", "UI", "FILE", "EVENT", "BATCH", "EXTERNAL",
})

# ─── Adapter Contract ──────────────────────────────────────────────────────────

ADAPTER_OPERATIONS = frozenset({
    "prepare", "execute", "capture_receipt",
    "wait_for_completion", "cleanup",
})


def create_surface_adapter(
    *,
    surface_type: str,
    description: str = "",
    capabilities: list[str] | None = None,
    supported_operations: list[str] | None = None,
    timeout_seconds: float = 30.0,
    retry_policy: dict[str, Any] | None = None,
    observation_layers: list[str] | None = None,
) -> dict[str, Any]:
    """Create a surface adapter definition."""
    if surface_type not in SURFACE_TYPES:
        raise ValueError(f"invalid_surface_type: {surface_type}")

    adapter_id = _stable_id("adapter", surface_type)
    return {
        "adapter_id": adapter_id,
        "surface_type": surface_type,
        "version": 1,
        "description": description,
        "capabilities": list(capabilities or []),
        "supported_operations": list(supported_operations or list(ADAPTER_OPERATIONS)),
        "timeout_seconds": timeout_seconds,
        "retry_policy": retry_policy or {"max_retries": 2, "backoff": "exponential"},
        "observation_layers": list(observation_layers or []),
        "created_at": time.time(),
    }


# ─── Default Adapters ──────────────────────────────────────────────────────────

def build_default_adapters() -> list[dict[str, Any]]:
    """Build all default surface adapters."""
    return [
        create_surface_adapter(
            surface_type="API",
            description="REST/GraphQL API surface adapter",
            capabilities=["sync_request", "async_request", "pagination", "filtering"],
            observation_layers=["API_RESPONSE", "DB_STATE", "EVENT_LOG"],
            timeout_seconds=30.0,
        ),
        create_surface_adapter(
            surface_type="UI",
            description="Web/Mobile UI surface adapter",
            capabilities=["click", "type", "navigate", "scroll", "screenshot", "dom_query"],
            observation_layers=["UI_STATE", "API_RESPONSE", "DB_STATE"],
            timeout_seconds=60.0,
        ),
        create_surface_adapter(
            surface_type="FILE",
            description="File upload/download/import/export adapter",
            capabilities=["upload", "download", "parse_csv", "parse_excel", "validate_schema"],
            observation_layers=["FILE_STATE", "DB_STATE", "EVENT_LOG"],
            timeout_seconds=120.0,
        ),
        create_surface_adapter(
            surface_type="EVENT",
            description="Event bus / message queue adapter",
            capabilities=["publish", "subscribe", "ack", "nack", "dead_letter", "replay"],
            observation_layers=["EVENT_LOG", "DB_STATE", "CONSUMER_STATE"],
            timeout_seconds=45.0,
        ),
        create_surface_adapter(
            surface_type="BATCH",
            description="Batch processing / scheduled job adapter",
            capabilities=["submit_batch", "check_progress", "cancel", "get_results"],
            observation_layers=["BATCH_STATE", "DB_STATE", "METRIC"],
            timeout_seconds=300.0,
        ),
        create_surface_adapter(
            surface_type="EXTERNAL",
            description="External system / third-party integration adapter",
            capabilities=["call_webhook", "mock_response", "simulate_timeout", "simulate_failure"],
            observation_layers=["EXTERNAL_CALL_LOG", "DB_STATE"],
            timeout_seconds=60.0,
        ),
    ]


# ─── Adapter Registry ──────────────────────────────────────────────────────────

class MultiSurfaceAdapterRegistry:
    """Registry for multi-surface adapters."""

    def __init__(self):
        self._adapters: dict[str, dict[str, Any]] = {}

    def register(self, adapter: dict[str, Any]) -> str:
        """Register a surface adapter."""
        surface_type = adapter.get("surface_type", "")
        if surface_type not in SURFACE_TYPES:
            raise ValueError(f"invalid_surface_type: {surface_type}")
        self._adapters[surface_type] = adapter
        return adapter.get("adapter_id", "")

    def register_defaults(self) -> int:
        """Register all default adapters."""
        for adapter in build_default_adapters():
            self.register(adapter)
        return len(self._adapters)

    def get(self, surface_type: str) -> dict[str, Any] | None:
        """Get adapter by surface type."""
        return self._adapters.get(surface_type)

    def get_all(self) -> list[dict[str, Any]]:
        """Get all registered adapters."""
        return list(self._adapters.values())

    def get_supported_surfaces(self) -> list[str]:
        """Get list of supported surface types."""
        return sorted(self._adapters.keys())

    def coverage_summary(self) -> dict[str, Any]:
        """Coverage summary of registered adapters."""
        return {
            "total_adapters": len(self._adapters),
            "surface_types": sorted(self._adapters.keys()),
            "missing_surfaces": sorted(SURFACE_TYPES - set(self._adapters.keys())),
            "all_surfaces_covered": len(self._adapters) == len(SURFACE_TYPES),
        }

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.multi-surface-adapter.v1",
            "adapters": list(self._adapters.values()),
            "coverage": self.coverage_summary(),
        }


# ─── Execution Receipt ─────────────────────────────────────────────────────────

def create_execution_receipt(
    *,
    experiment_id: str,
    surface_type: str,
    operation: str,
    status: str = "COMPLETED",
    response_data: dict[str, Any] | None = None,
    observations: list[dict[str, Any]] | None = None,
    duration_ms: float = 0.0,
    error: str = "",
) -> dict[str, Any]:
    """Create an execution receipt from surface adapter."""
    receipt_id = _stable_id("receipt", experiment_id, surface_type, operation)
    return {
        "receipt_id": receipt_id,
        "experiment_id": experiment_id,
        "surface_type": surface_type,
        "operation": operation,
        "status": status,
        "response_data": response_data or {},
        "observations": list(observations or []),
        "duration_ms": duration_ms,
        "error": error,
        "timestamp": time.time(),
    }


# ─── Cross-Surface Execution Plan ─────────────────────────────────────────────

def plan_cross_surface_execution(
    *,
    experiment: dict[str, Any],
    adapter_registry: MultiSurfaceAdapterRegistry,
    primary_surface: str = "API",
    observation_surfaces: list[str] | None = None,
) -> dict[str, Any]:
    """Plan execution across multiple surfaces.

    Returns execution plan with ordered steps for each surface.
    """
    obs_surfaces = observation_surfaces or ["API", "DB"]
    steps = []

    # Step 1: Prepare on primary surface
    primary_adapter = adapter_registry.get(primary_surface)
    if primary_adapter:
        steps.append({
            "step": 1,
            "surface": primary_surface,
            "operation": "prepare",
            "adapter_id": primary_adapter.get("adapter_id", ""),
        })

    # Step 2: Execute on primary surface
    steps.append({
        "step": 2,
        "surface": primary_surface,
        "operation": "execute",
        "adapter_id": primary_adapter.get("adapter_id", "") if primary_adapter else "",
    })

    # Step 3: Capture receipt
    steps.append({
        "step": 3,
        "surface": primary_surface,
        "operation": "capture_receipt",
        "adapter_id": primary_adapter.get("adapter_id", "") if primary_adapter else "",
    })

    # Step 4: Wait for completion (async surfaces)
    if primary_surface in ("EVENT", "BATCH", "EXTERNAL"):
        steps.append({
            "step": 4,
            "surface": primary_surface,
            "operation": "wait_for_completion",
            "adapter_id": primary_adapter.get("adapter_id", "") if primary_adapter else "",
        })

    # Step 5: Observe on observation surfaces
    for i, obs_surface in enumerate(obs_surfaces):
        obs_adapter = adapter_registry.get(obs_surface)
        steps.append({
            "step": len(steps) + 1,
            "surface": obs_surface,
            "operation": "observe",
            "adapter_id": obs_adapter.get("adapter_id", "") if obs_adapter else "",
        })

    # Final: Cleanup
    steps.append({
        "step": len(steps) + 1,
        "surface": primary_surface,
        "operation": "cleanup",
        "adapter_id": primary_adapter.get("adapter_id", "") if primary_adapter else "",
    })

    return {
        "experiment_id": experiment.get("experiment_id", ""),
        "primary_surface": primary_surface,
        "observation_surfaces": obs_surfaces,
        "total_steps": len(steps),
        "steps": steps,
        "estimated_duration_ms": sum(
            (adapter_registry.get(s.get("surface", "")) or {}).get("timeout_seconds", 30) * 1000
            for s in steps
        ),
    }


# ─── Event/Async Exploration Support (SPEC §21) ───────────────────────────────

EVENT_EXPLORATION_SCENARIOS = frozenset({
    "EVENT_SEND",
    "EVENT_CONSUME",
    "EVENT_DUPLICATE",
    "EVENT_OUT_OF_ORDER",
    "EVENT_DELAYED",
    "EVENT_CONSUME_FAILURE",
    "EVENT_ACK_FAILURE",
    "EVENT_RETRY",
    "EVENT_DEAD_LETTER",
    "EVENT_EVENTUAL_CONSISTENCY",
})


def create_event_exploration_plan(
    *,
    experiment_id: str,
    scenarios: list[str] | None = None,
) -> dict[str, Any]:
    """Create event/async exploration plan."""
    selected = scenarios or list(EVENT_EXPLORATION_SCENARIOS)
    invalid = set(selected) - EVENT_EXPLORATION_SCENARIOS
    if invalid:
        raise ValueError(f"invalid_event_scenarios: {invalid}")

    return {
        "experiment_id": experiment_id,
        "surface_type": "EVENT",
        "scenarios": selected,
        "total_scenarios": len(selected),
        "requires_eventual_consistency_wait": True,
        "max_consistency_wait_seconds": 30,
        "created_at": time.time(),
    }


# ─── Batch/Scale Exploration Support (SPEC §22) ───────────────────────────────

def create_batch_scale_plan(
    *,
    experiment_id: str,
    data_volumes: list[int] | None = None,
    batch_sizes: list[int] | None = None,
    invariant_ids: list[str] | None = None,
) -> dict[str, Any]:
    """Create batch/scale exploration plan.

    Performance experiments MUST bind to business invariants.
    Only slow response ≠ deep business bug.
    """
    volumes = data_volumes or [10, 100, 1000, 10000]
    sizes = batch_sizes or [1, 10, 50, 100]
    invariants = invariant_ids or []

    return {
        "experiment_id": experiment_id,
        "surface_type": "BATCH",
        "data_volumes": volumes,
        "batch_sizes": sizes,
        "bound_invariant_ids": invariants,
        "requires_business_invariant": True,
        "invariant_binding_complete": len(invariants) > 0,
        "note": "performance_must_bind_business_invariant",
        "created_at": time.time(),
    }


# ─── UI Cross-Surface Integration (SPEC §23) ──────────────────────────────────

UI_EXPLORATION_CHECKS = frozenset({
    "STATE_FORBIDDEN_VIA_UI",
    "API_ALLOWED_UI_BLOCKED",
    "FIELD_INCONSISTENCY_API_UI",
    "ROLE_MENU_MISMATCH",
    "DUPLICATE_CLICK",
    "PAGINATION_OMISSION",
})


def create_ui_exploration_plan(
    *,
    experiment_id: str,
    checks: list[str] | None = None,
) -> dict[str, Any]:
    """Create UI cross-surface exploration plan."""
    selected = checks or list(UI_EXPLORATION_CHECKS)
    invalid = set(selected) - UI_EXPLORATION_CHECKS
    if invalid:
        raise ValueError(f"invalid_ui_checks: {invalid}")

    return {
        "experiment_id": experiment_id,
        "surface_type": "UI",
        "checks": selected,
        "total_checks": len(selected),
        "requires_api_comparison": True,
        "requires_db_comparison": True,
        "created_at": time.time(),
    }
