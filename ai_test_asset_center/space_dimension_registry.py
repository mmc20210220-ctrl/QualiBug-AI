"""System-Space Dimension Registry — unified registration of exploration dimensions.

SPEC §7: 6 space layers, 16+ dimensions.
Only references existing facts from Behavior IR and Binding Graph.
Does NOT create entities, fields, operations, or any business facts.

Domains:
  SYSTEM   - structural elements (entity, field, relation, state)
  BUSINESS - semantic elements (actor, role, tenant, invariant)
  SURFACE  - interaction entry points (api, ui, event, batch, file)
  DYNAMIC  - runtime conditions (order, replay, concurrency, failure)
  OBSERVATION - where facts are observed (api_response, db, event_stream, trace)
  SCALE    - volume and pressure (data_volume, batch_size, concurrency_level)
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


# ─── Space Domains ─────────────────────────────────────────────────────────────

SPACE_DOMAINS = frozenset({
    "SYSTEM", "BUSINESS", "SURFACE", "DYNAMIC", "OBSERVATION", "SCALE",
})

# ─── Value Sources ─────────────────────────────────────────────────────────────

VALUE_SOURCES = frozenset({
    "behavior_ir", "binding_graph", "runtime_environment", "frozen_config",
})


# ─── Dimension Definition ──────────────────────────────────────────────────────

def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "dim_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def create_dimension(
    *,
    dimension_type: str,
    domain: str,
    value_source: str = "behavior_ir",
    allowed_values: list[str] | None = None,
    constraints: list[str] | None = None,
    required_entities: list[str] | None = None,
    required_operations: list[str] | None = None,
    required_bindings: list[str] | None = None,
    required_observers: list[str] | None = None,
    risk_level: str = "LOW",
    description: str = "",
) -> dict[str, Any]:
    """Create a space dimension definition."""
    if domain not in SPACE_DOMAINS:
        raise ValueError(f"invalid_domain: {domain} not in {SPACE_DOMAINS}")
    if value_source not in VALUE_SOURCES:
        raise ValueError(f"invalid_value_source: {value_source}")

    dim_id = _stable_id(domain, dimension_type)
    return {
        "dimension_id": dim_id,
        "dimension_type": dimension_type,
        "domain": domain,
        "value_source": value_source,
        "allowed_values": list(allowed_values or []),
        "constraints": list(constraints or []),
        "applicability": {
            "required_entities": list(required_entities or []),
            "required_operations": list(required_operations or []),
            "required_bindings": list(required_bindings or []),
            "required_observers": list(required_observers or []),
        },
        "risk_level": risk_level,
        "description": description,
        "version": 1,
        "created_at": time.time(),
    }


# ─── Default Dimension Set (16+ dimensions) ────────────────────────────────────

def build_default_dimensions() -> list[dict[str, Any]]:
    """Build the default set of 20 system-space dimensions."""
    dims = []

    # SYSTEM domain (4)
    dims.append(create_dimension(
        dimension_type="ENTITY", domain="SYSTEM",
        description="Entity structural dimension - which entities are involved",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="FIELD", domain="SYSTEM",
        description="Field-level dimension - which fields are under test",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="RELATION", domain="SYSTEM",
        description="Inter-entity relation dimension",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="STATE", domain="SYSTEM",
        description="State/lifecycle dimension - state field and transitions",
        risk_level="MEDIUM",
    ))

    # BUSINESS domain (4)
    dims.append(create_dimension(
        dimension_type="ACTOR", domain="BUSINESS",
        value_source="binding_graph",
        description="Actor identity dimension - who performs the operation",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="ROLE", domain="BUSINESS",
        value_source="binding_graph",
        description="Role/permission dimension",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="TENANT", domain="BUSINESS",
        value_source="binding_graph",
        description="Tenant/organization isolation dimension",
        risk_level="MEDIUM",
    ))
    dims.append(create_dimension(
        dimension_type="INVARIANT", domain="BUSINESS",
        description="Business invariant under test",
        risk_level="LOW",
    ))

    # SURFACE domain (3)
    dims.append(create_dimension(
        dimension_type="EXECUTION_SURFACE", domain="SURFACE",
        value_source="runtime_environment",
        allowed_values=["API", "UI", "FILE", "EVENT", "BATCH", "EXTERNAL"],
        description="Where the action enters the system",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="OBSERVATION_SURFACE", domain="SURFACE",
        value_source="runtime_environment",
        allowed_values=["API_RESPONSE", "UI_STATE", "DATABASE", "EVENT_STREAM",
                        "CACHE", "REPORT", "AUDIT_LOG", "TRACE"],
        description="Where the result is observed",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="CROSS_SURFACE", domain="SURFACE",
        value_source="runtime_environment",
        description="Cross-surface consistency dimension",
        risk_level="MEDIUM",
    ))

    # DYNAMIC domain (4)
    dims.append(create_dimension(
        dimension_type="ORDERING", domain="DYNAMIC",
        value_source="runtime_environment",
        allowed_values=["SEQUENTIAL", "REORDERED", "OUT_OF_ORDER"],
        description="Operation/event ordering dimension",
        risk_level="MEDIUM",
    ))
    dims.append(create_dimension(
        dimension_type="REPLAY", domain="DYNAMIC",
        value_source="runtime_environment",
        allowed_values=["NONE", "EXACT", "SAME_KEY_DIFF_PAYLOAD", "DUPLICATE_EVENT"],
        description="Replay/idempotency dimension",
        risk_level="MEDIUM",
    ))
    dims.append(create_dimension(
        dimension_type="CONCURRENCY", domain="DYNAMIC",
        value_source="runtime_environment",
        allowed_values=["NONE", "PARALLEL_SAME_ACTOR", "PARALLEL_DIFF_ACTOR",
                        "PARALLEL_SAME_RESOURCE", "READ_WRITE_INTERLEAVE"],
        description="Concurrency dimension",
        risk_level="HIGH",
    ))
    dims.append(create_dimension(
        dimension_type="FAILURE", domain="DYNAMIC",
        value_source="runtime_environment",
        allowed_values=["NONE", "BEFORE_SIDE_EFFECT", "AFTER_PARTIAL",
                        "BEFORE_COMMIT", "AFTER_COMMIT", "DEPENDENCY_FAILURE"],
        description="Failure injection dimension",
        risk_level="HIGH",
    ))

    # OBSERVATION domain (2)
    dims.append(create_dimension(
        dimension_type="OBSERVATION_LAYER", domain="OBSERVATION",
        value_source="runtime_environment",
        allowed_values=["API", "UI", "DB", "EVENT", "TRACE", "METRIC", "FILE", "REPORT"],
        description="Multi-layer observation dimension",
        risk_level="LOW",
    ))
    dims.append(create_dimension(
        dimension_type="CORRELATION", domain="OBSERVATION",
        value_source="runtime_environment",
        description="Cross-layer correlation key dimension",
        risk_level="LOW",
    ))

    # SCALE domain (3)
    dims.append(create_dimension(
        dimension_type="DATA_VOLUME", domain="SCALE",
        value_source="frozen_config",
        allowed_values=["SINGLE", "SMALL_BATCH", "LARGE_BATCH", "MASSIVE"],
        description="Data volume dimension",
        risk_level="MEDIUM",
    ))
    dims.append(create_dimension(
        dimension_type="CONCURRENCY_LEVEL", domain="SCALE",
        value_source="frozen_config",
        allowed_values=["1", "2", "5", "10", "50", "100"],
        description="Concurrent request level",
        risk_level="HIGH",
    ))
    dims.append(create_dimension(
        dimension_type="LATENCY_PROFILE", domain="SCALE",
        value_source="frozen_config",
        allowed_values=["NORMAL", "ELEVATED", "TIMEOUT_APPROACHING", "TIMEOUT"],
        description="Latency/timeout dimension",
        risk_level="MEDIUM",
    ))

    return dims


# ─── Dimension Registry ────────────────────────────────────────────────────────

class SpaceDimensionRegistry:
    """Unified registry for system-space dimensions."""

    def __init__(self, *, project_id: str = ""):
        self.project_id = project_id
        self._dimensions: dict[str, dict[str, Any]] = {}
        self._version = 1

    @property
    def size(self) -> int:
        return len(self._dimensions)

    @property
    def version(self) -> int:
        return self._version

    def register(self, dimension: dict[str, Any]) -> str:
        """Register a dimension. Returns dimension_id."""
        dim_id = dimension.get("dimension_id", "")
        if not dim_id:
            raise ValueError("dimension_missing_id")
        self._dimensions[dim_id] = dimension
        self._version += 1
        return dim_id

    def register_defaults(self) -> int:
        """Register all default dimensions. Returns count."""
        dims = build_default_dimensions()
        for d in dims:
            self._dimensions[d["dimension_id"]] = d
        self._version += 1
        return len(dims)

    def get(self, dimension_id: str) -> dict[str, Any] | None:
        return self._dimensions.get(dimension_id)

    def get_by_type(self, dimension_type: str) -> list[dict[str, Any]]:
        return [d for d in self._dimensions.values()
                if d.get("dimension_type") == dimension_type]

    def get_by_domain(self, domain: str) -> list[dict[str, Any]]:
        return [d for d in self._dimensions.values()
                if d.get("domain") == domain]

    def all_types(self) -> list[str]:
        return sorted({d["dimension_type"] for d in self._dimensions.values()})

    def all_domains(self) -> list[str]:
        return sorted({d["domain"] for d in self._dimensions.values()})

    def coverage_summary(self) -> dict[str, Any]:
        """Summary of registered dimensions by domain."""
        by_domain: dict[str, int] = {}
        for d in self._dimensions.values():
            dom = d.get("domain", "UNKNOWN")
            by_domain[dom] = by_domain.get(dom, 0) + 1
        return {
            "total_dimensions": self.size,
            "by_domain": by_domain,
            "all_types": self.all_types(),
            "registry_version": self._version,
        }

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.space-dimension-registry.v1",
            "project_id": self.project_id,
            "registry_version": self._version,
            "dimensions": list(self._dimensions.values()),
            "summary": self.coverage_summary(),
        }

    def load(self, data: dict[str, Any]) -> None:
        self.project_id = data.get("project_id", "")
        self._version = data.get("registry_version", 1)
        self._dimensions = {}
        for d in data.get("dimensions", []):
            dim_id = d.get("dimension_id", "")
            if dim_id:
                self._dimensions[dim_id] = d
