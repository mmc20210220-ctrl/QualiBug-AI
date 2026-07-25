"""Invariant Graph — unified invariant structure for exploration.

SPEC §9: 16 invariant types unified from existing field-level rules,
state rules, conservation rules, and relation rules.

Reuses:
  - field_level_golden_rules.py (20 golden rules)
  - Behavior IR invariants
  - Binding Graph evidence

Does NOT create a parallel rule system.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


# ─── Invariant Types ───────────────────────────────────────────────────────────

INVARIANT_TYPES = frozenset({
    "FIELD_CAUSAL",
    "STATE_LIFECYCLE",
    "RELATION_CONSISTENCY",
    "AUTHORIZATION",
    "OWNERSHIP",
    "TENANT_ISOLATION",
    "CONSERVATION",
    "AGGREGATE",
    "COMPENSATION",
    "IDEMPOTENCY",
    "TEMPORAL",
    "TRANSACTIONAL_ATOMICITY",
    "EVENTUAL_CONSISTENCY",
    "VERSION_MONOTONICITY",
    "CROSS_SURFACE_CONSISTENCY",
    "SCALE_STABILITY",
})


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "inv_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


def _text(v: Any) -> str:
    return str(v or "").strip()


def _list(v: Any) -> list:
    return v if isinstance(v, list) else []


def _dict(v: Any) -> dict:
    return v if isinstance(v, dict) else {}


# ─── Invariant Creation ────────────────────────────────────────────────────────

def create_invariant(
    *,
    invariant_type: str,
    subject_entities: list[str] | None = None,
    required_fields: list[str] | None = None,
    required_relations: list[str] | None = None,
    required_operations: list[str] | None = None,
    preconditions: list[str] | None = None,
    expected_expression: str = "",
    forbidden_expression: str = "",
    applicable_dimensions: list[str] | None = None,
    compatible_operators: list[str] | None = None,
    required_observations: list[str] | None = None,
    oracle_contract: dict[str, Any] | None = None,
    confidence: float = 0.8,
    source_evidence: list[str] | None = None,
    source_rule_ids: list[str] | None = None,
    description: str = "",
) -> dict[str, Any]:
    """Create an invariant node in the graph."""
    if invariant_type not in INVARIANT_TYPES:
        raise ValueError(f"invalid_invariant_type: {invariant_type}")

    inv_id = _stable_id(
        invariant_type,
        ",".join(subject_entities or []),
        expected_expression or forbidden_expression,
    )

    return {
        "invariant_id": inv_id,
        "invariant_type": invariant_type,
        "source_rule_ids": list(source_rule_ids or []),
        "subject_entities": list(subject_entities or []),
        "required_fields": list(required_fields or []),
        "required_relations": list(required_relations or []),
        "required_operations": list(required_operations or []),
        "preconditions": list(preconditions or []),
        "expected_expression": expected_expression,
        "forbidden_expression": forbidden_expression,
        "applicable_dimensions": list(applicable_dimensions or []),
        "compatible_operators": list(compatible_operators or []),
        "required_observations": list(required_observations or []),
        "oracle_contract": oracle_contract or {},
        "confidence": confidence,
        "source_evidence": list(source_evidence or []),
        "description": description,
        "version": 1,
        "created_at": time.time(),
    }


# ─── Invariant Graph ───────────────────────────────────────────────────────────

class InvariantGraph:
    """Unified invariant graph connecting rules to exploration dimensions."""

    def __init__(self, *, project_id: str = ""):
        self.project_id = project_id
        self._invariants: dict[str, dict[str, Any]] = {}
        self._version = 1

    @property
    def size(self) -> int:
        return len(self._invariants)

    @property
    def version(self) -> int:
        return self._version

    def add(self, invariant: dict[str, Any]) -> str:
        """Add an invariant to the graph."""
        inv_id = invariant.get("invariant_id", "")
        if not inv_id:
            raise ValueError("invariant_missing_id")
        self._invariants[inv_id] = invariant
        self._version += 1
        return inv_id

    def get(self, invariant_id: str) -> dict[str, Any] | None:
        return self._invariants.get(invariant_id)

    def get_by_type(self, invariant_type: str) -> list[dict[str, Any]]:
        return [inv for inv in self._invariants.values()
                if inv.get("invariant_type") == invariant_type]

    def get_by_entity(self, entity_id: str) -> list[dict[str, Any]]:
        return [inv for inv in self._invariants.values()
                if entity_id in inv.get("subject_entities", [])]

    def get_applicable_for_dimensions(self, dimensions: list[str]) -> list[dict[str, Any]]:
        """Get invariants applicable to given dimension types."""
        result = []
        dim_set = set(dimensions)
        for inv in self._invariants.values():
            applicable = set(inv.get("applicable_dimensions", []))
            if applicable & dim_set:
                result.append(inv)
        return result

    def all_types_present(self) -> list[str]:
        return sorted({inv["invariant_type"] for inv in self._invariants.values()})

    def coverage_summary(self) -> dict[str, Any]:
        by_type: dict[str, int] = {}
        for inv in self._invariants.values():
            t = inv.get("invariant_type", "UNKNOWN")
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "total_invariants": self.size,
            "by_type": by_type,
            "types_present": self.all_types_present(),
            "types_missing": sorted(INVARIANT_TYPES - set(self.all_types_present())),
            "graph_version": self._version,
        }

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.invariant-graph.v1",
            "project_id": self.project_id,
            "graph_version": self._version,
            "invariants": list(self._invariants.values()),
            "summary": self.coverage_summary(),
        }

    def load(self, data: dict[str, Any]) -> None:
        self.project_id = data.get("project_id", "")
        self._version = data.get("graph_version", 1)
        self._invariants = {}
        for inv in data.get("invariants", []):
            inv_id = inv.get("invariant_id", "")
            if inv_id:
                self._invariants[inv_id] = inv


# ─── Build from Existing Sources ───────────────────────────────────────────────

def build_invariants_from_behavior_ir(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract invariants from Behavior IR invariants section."""
    ir = _dict(behavior_ir)
    raw_invariants = _list(ir.get("invariants"))
    result = []

    for raw in raw_invariants:
        if not isinstance(raw, dict):
            continue
        raw_type = _text(raw.get("invariant_type")).upper()

        # Map IR invariant types to our 16 types
        type_map = {
            "CONSERVATION": "CONSERVATION",
            "CAUSAL": "FIELD_CAUSAL",
            "FIELD_CAUSAL": "FIELD_CAUSAL",
            "STATE": "STATE_LIFECYCLE",
            "STATE_LIFECYCLE": "STATE_LIFECYCLE",
            "AUTHORIZATION": "AUTHORIZATION",
            "OWNERSHIP": "OWNERSHIP",
            "TENANT_ISOLATION": "TENANT_ISOLATION",
            "IDEMPOTENCY": "IDEMPOTENCY",
            "TEMPORAL": "TEMPORAL",
            "RELATION": "RELATION_CONSISTENCY",
            "RELATION_CONSISTENCY": "RELATION_CONSISTENCY",
        }
        inv_type = type_map.get(raw_type, "CONSERVATION")

        # Extract terms as fields
        terms = _list(raw.get("terms"))
        fields = [_text(t.get("field")) for t in terms if isinstance(t, dict) and _text(t.get("field"))]

        # Extract entity refs
        entities = []
        ent_ref = _text(raw.get("entity_ref"))
        if ent_ref:
            entities.append(ent_ref)

        inv = create_invariant(
            invariant_type=inv_type,
            subject_entities=entities,
            required_fields=fields,
            source_rule_ids=[_text(raw.get("id"))],
            source_evidence=[_text(raw.get("id"))],
            description=_text(raw.get("description") or f"{inv_type} from Behavior IR"),
            confidence=0.85,
        )
        result.append(inv)

    return result


def build_invariants_from_golden_rules() -> list[dict[str, Any]]:
    """Build invariants from the 20 golden rules in field_level_golden_rules.py."""
    try:
        from ai_test_asset_center.field_level_golden_rules import GOLDEN_RULES
    except ImportError:
        return []

    result = []
    type_map = {
        "causal": "FIELD_CAUSAL",
        "state": "STATE_LIFECYCLE",
        "conservation": "CONSERVATION",
    }

    for rule in GOLDEN_RULES:
        if not isinstance(rule, dict):
            continue
        category = _text(rule.get("category")).lower()
        inv_type = type_map.get(category, "FIELD_CAUSAL")

        terms = _list(rule.get("terms"))
        fields = [_text(t) if isinstance(t, str) else _text(t.get("field"))
                  for t in terms]

        inv = create_invariant(
            invariant_type=inv_type,
            required_fields=[f for f in fields if f],
            source_rule_ids=[_text(rule.get("rule_id"))],
            applicable_dimensions=["FIELD", "STATE", "INVARIANT"],
            description=_text(rule.get("description")),
            confidence=0.9,
        )
        result.append(inv)

    return result


def build_default_invariant_graph(
    behavior_ir: dict[str, Any] | None = None,
    *,
    project_id: str = "",
) -> InvariantGraph:
    """Build a complete invariant graph from all available sources."""
    graph = InvariantGraph(project_id=project_id)

    # From Behavior IR
    if behavior_ir:
        for inv in build_invariants_from_behavior_ir(behavior_ir):
            graph.add(inv)

    # From Golden Rules
    for inv in build_invariants_from_golden_rules():
        graph.add(inv)

    # Add structural invariants that are always applicable
    structural_invariants = [
        create_invariant(
            invariant_type="AUTHORIZATION",
            applicable_dimensions=["ACTOR", "ROLE", "TENANT"],
            compatible_operators=["SWITCH_ACTOR", "SWITCH_ROLE", "SWITCH_TENANT"],
            description="Operation must respect actor authorization boundaries",
            confidence=0.95,
        ),
        create_invariant(
            invariant_type="TENANT_ISOLATION",
            applicable_dimensions=["TENANT", "ACTOR"],
            compatible_operators=["SWITCH_TENANT", "USE_CROSS_SCOPE_RESOURCE"],
            description="Resources must be isolated between tenants",
            confidence=0.95,
        ),
        create_invariant(
            invariant_type="IDEMPOTENCY",
            applicable_dimensions=["REPLAY"],
            compatible_operators=["EXACT_REPLAY", "SAME_KEY_SAME_PAYLOAD", "DUPLICATE_EVENT_DELIVERY"],
            description="Duplicate operations must not produce duplicate side effects",
            confidence=0.9,
        ),
        create_invariant(
            invariant_type="TRANSACTIONAL_ATOMICITY",
            applicable_dimensions=["FAILURE"],
            compatible_operators=["FAIL_AFTER_PARTIAL_SIDE_EFFECT", "FAIL_BEFORE_COMMIT"],
            description="Multi-step operations must be atomic or fully compensated",
            confidence=0.85,
        ),
        create_invariant(
            invariant_type="EVENTUAL_CONSISTENCY",
            applicable_dimensions=["ORDERING", "REPLAY"],
            compatible_operators=["OUT_OF_ORDER_EVENT", "DUPLICATE_EVENT_DELIVERY"],
            description="Async processing must eventually converge to correct state",
            confidence=0.85,
        ),
        create_invariant(
            invariant_type="VERSION_MONOTONICITY",
            applicable_dimensions=["CONCURRENCY"],
            compatible_operators=["VERSION_CONFLICT", "PARALLEL_SAME_RESOURCE"],
            description="Concurrent updates must not silently overwrite newer versions",
            confidence=0.85,
        ),
        create_invariant(
            invariant_type="CROSS_SURFACE_CONSISTENCY",
            applicable_dimensions=["CROSS_SURFACE", "OBSERVATION_SURFACE"],
            compatible_operators=["OBSERVE_VIA_DB", "OBSERVE_VIA_EVENT", "OBSERVE_VIA_API"],
            description="Same business fact must be consistent across observation surfaces",
            confidence=0.85,
        ),
        create_invariant(
            invariant_type="SCALE_STABILITY",
            applicable_dimensions=["DATA_VOLUME", "CONCURRENCY_LEVEL"],
            compatible_operators=["SCALE_DATA_VOLUME", "SCALE_BATCH_SIZE", "SCALE_CONCURRENCY"],
            description="Business invariants must hold under scale pressure",
            confidence=0.8,
        ),
        create_invariant(
            invariant_type="COMPENSATION",
            applicable_dimensions=["FAILURE"],
            compatible_operators=["EXECUTE_COMPENSATION", "RETRY_AFTER_PARTIAL_FAILURE"],
            description="Failed operations must be compensable to consistent state",
            confidence=0.85,
        ),
    ]
    for inv in structural_invariants:
        graph.add(inv)

    return graph
