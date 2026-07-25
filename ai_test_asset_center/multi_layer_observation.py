"""Multi-Layer Observation — observe across multiple layers.

SPEC §24: Observer types: API/UI/DB/Event/Trace/Metric/File/Report/External.
Correlation: Tenant/Entity ID/Correlation Key/Business Request ID/Trace ID/Event Key.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "obs_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Observer Types ────────────────────────────────────────────────────────────

OBSERVER_TYPES = frozenset({
    "API", "UI", "DB", "EVENT", "TRACE",
    "METRIC", "FILE", "REPORT", "EXTERNAL",
})

# ─── Correlation Keys ─────────────────────────────────────────────────────────

CORRELATION_KEYS = frozenset({
    "TENANT_ID", "ENTITY_ID", "CORRELATION_KEY",
    "BUSINESS_REQUEST_ID", "TRACE_ID", "EVENT_KEY",
})


# ─── Observer Definition ───────────────────────────────────────────────────────

def create_observer(
    *,
    observer_type: str,
    description: str = "",
    target_layers: list[str] | None = None,
    correlation_keys: list[str] | None = None,
    capture_fields: list[str] | None = None,
    timeout_seconds: float = 10.0,
    async_capable: bool = False,
) -> dict[str, Any]:
    """Create an observer definition."""
    if observer_type not in OBSERVER_TYPES:
        raise ValueError(f"invalid_observer_type: {observer_type}")

    observer_id = _stable_id("observer", observer_type)
    return {
        "observer_id": observer_id,
        "observer_type": observer_type,
        "version": 1,
        "description": description,
        "target_layers": list(target_layers or [observer_type]),
        "correlation_keys": list(correlation_keys or ["ENTITY_ID"]),
        "capture_fields": list(capture_fields or []),
        "timeout_seconds": timeout_seconds,
        "async_capable": async_capable,
        "created_at": time.time(),
    }


def build_default_observers() -> list[dict[str, Any]]:
    """Build all default observers."""
    return [
        create_observer(
            observer_type="API",
            description="Observe API response status, headers, body",
            correlation_keys=["BUSINESS_REQUEST_ID", "TRACE_ID"],
            capture_fields=["status_code", "response_body", "headers", "latency_ms"],
        ),
        create_observer(
            observer_type="UI",
            description="Observe UI state, DOM elements, visual consistency",
            correlation_keys=["ENTITY_ID", "CORRELATION_KEY"],
            capture_fields=["dom_state", "visible_elements", "error_messages", "screenshot"],
            async_capable=True,
        ),
        create_observer(
            observer_type="DB",
            description="Observe database state after operation",
            correlation_keys=["ENTITY_ID", "TENANT_ID"],
            capture_fields=["row_data", "timestamps", "version", "soft_delete_flag"],
        ),
        create_observer(
            observer_type="EVENT",
            description="Observe event bus messages",
            correlation_keys=["EVENT_KEY", "CORRELATION_KEY"],
            capture_fields=["event_type", "payload", "partition", "offset", "timestamp"],
            async_capable=True,
        ),
        create_observer(
            observer_type="TRACE",
            description="Observe distributed trace spans",
            correlation_keys=["TRACE_ID", "BUSINESS_REQUEST_ID"],
            capture_fields=["spans", "service_name", "duration_ms", "status"],
        ),
        create_observer(
            observer_type="METRIC",
            description="Observe application metrics",
            correlation_keys=["ENTITY_ID"],
            capture_fields=["counter", "gauge", "histogram", "labels"],
            async_capable=True,
        ),
        create_observer(
            observer_type="FILE",
            description="Observe file system state",
            correlation_keys=["ENTITY_ID", "CORRELATION_KEY"],
            capture_fields=["file_path", "file_size", "content_hash", "modified_at"],
        ),
        create_observer(
            observer_type="REPORT",
            description="Observe generated reports",
            correlation_keys=["ENTITY_ID", "TENANT_ID"],
            capture_fields=["report_data", "aggregations", "row_count"],
            async_capable=True,
        ),
        create_observer(
            observer_type="EXTERNAL",
            description="Observe external system calls",
            correlation_keys=["CORRELATION_KEY", "TRACE_ID"],
            capture_fields=["request", "response", "status", "latency_ms"],
        ),
    ]


# ─── Observation Registry ──────────────────────────────────────────────────────

class MultiLayerObservationRegistry:
    """Registry for multi-layer observers."""

    def __init__(self):
        self._observers: dict[str, dict[str, Any]] = {}
        self._observations: list[dict[str, Any]] = []

    def register(self, observer: dict[str, Any]) -> str:
        """Register an observer."""
        obs_type = observer.get("observer_type", "")
        if obs_type not in OBSERVER_TYPES:
            raise ValueError(f"invalid_observer_type: {obs_type}")
        self._observers[obs_type] = observer
        return observer.get("observer_id", "")

    def register_defaults(self) -> int:
        """Register all default observers."""
        for obs in build_default_observers():
            self.register(obs)
        return len(self._observers)

    def get(self, observer_type: str) -> dict[str, Any] | None:
        return self._observers.get(observer_type)

    def get_all(self) -> list[dict[str, Any]]:
        return list(self._observers.values())

    def get_for_experiment(
        self,
        experiment: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Get observers needed for an experiment."""
        needed_types = set()

        # From experiment's observer requirements
        for obs_type in experiment.get("observers", []):
            needed_types.add(obs_type)

        # From surface adapter observation layers
        surface = experiment.get("surface_adapter", "API")
        surface_map = {
            "API": ["API", "DB"],
            "UI": ["UI", "API", "DB"],
            "EVENT": ["EVENT", "DB"],
            "BATCH": ["DB", "METRIC"],
            "FILE": ["FILE", "DB"],
            "EXTERNAL": ["EXTERNAL", "DB"],
        }
        for layer in surface_map.get(surface, ["API", "DB"]):
            if layer in OBSERVER_TYPES:
                needed_types.add(layer)

        return [
            self._observers[t] for t in needed_types
            if t in self._observers
        ]

    def record_observation(
        self,
        *,
        experiment_id: str,
        observer_type: str,
        data: dict[str, Any],
        correlation: dict[str, str] | None = None,
    ) -> str:
        """Record an observation from an observer."""
        obs_id = _stable_id("observation", experiment_id, observer_type, str(time.time()))
        observation = {
            "observation_id": obs_id,
            "experiment_id": experiment_id,
            "observer_type": observer_type,
            "data": data,
            "correlation": correlation or {},
            "timestamp": time.time(),
        }
        self._observations.append(observation)
        return obs_id

    def get_observations_for_experiment(
        self,
        experiment_id: str,
    ) -> list[dict[str, Any]]:
        """Get all observations for an experiment."""
        return [
            o for o in self._observations
            if o.get("experiment_id") == experiment_id
        ]

    def coverage_summary(self) -> dict[str, Any]:
        return {
            "total_observers": len(self._observers),
            "observer_types": sorted(self._observers.keys()),
            "missing_types": sorted(OBSERVER_TYPES - set(self._observers.keys())),
            "total_observations": len(self._observations),
        }

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.multi-layer-observation.v1",
            "observers": list(self._observers.values()),
            "observations_count": len(self._observations),
            "coverage": self.coverage_summary(),
        }


# ─── Correlation Engine ────────────────────────────────────────────────────────

def correlate_observations(
    observations: list[dict[str, Any]],
    *,
    correlation_key: str = "ENTITY_ID",
    correlation_value: str = "",
) -> dict[str, Any]:
    """Correlate observations across layers by key."""
    correlated = []
    for obs in observations:
        corr = obs.get("correlation", {})
        if corr.get(correlation_key) == correlation_value:
            correlated.append(obs)

    # Group by observer type
    by_type: dict[str, list] = {}
    for obs in correlated:
        t = obs.get("observer_type", "")
        by_type.setdefault(t, []).append(obs)

    return {
        "correlation_key": correlation_key,
        "correlation_value": correlation_value,
        "total_correlated": len(correlated),
        "layers_observed": sorted(by_type.keys()),
        "observations_by_layer": {k: len(v) for k, v in by_type.items()},
    }


# ─── Observation Completeness Check ───────────────────────────────────────────

def check_observation_completeness(
    experiment: dict[str, Any],
    observations: list[dict[str, Any]],
    *,
    required_layers: list[str] | None = None,
) -> dict[str, Any]:
    """Check if observations cover all required layers."""
    required = set(required_layers or ["API", "DB"])
    observed_layers = {o.get("observer_type", "") for o in observations}

    missing = required - observed_layers
    complete = len(missing) == 0

    return {
        "complete": complete,
        "required_layers": sorted(required),
        "observed_layers": sorted(observed_layers),
        "missing_layers": sorted(missing),
        "observation_count": len(observations),
    }
