"""Exploration Operator Registry — unified registration and applicability.

SPEC §10-12: Operators describe how to change system-space coordinates.
54+ operators across 9 categories. Each has applicability, risk, and cost.

Categories:
  ACTOR_SCOPE (7), STATE (6), RELATION (6), REPLAY (7), TEMPORAL (7),
  CONCURRENCY (7), TRANSACTION_FAILURE (9), BATCH_SCALE (8), SURFACE (10)

Operators do NOT contain: project names, entity names, endpoint paths,
bug IDs, or fixed expected results.
"""
from __future__ import annotations

import hashlib
import time
from typing import Any


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "op_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Operator Categories ───────────────────────────────────────────────────────

OPERATOR_CATEGORIES = frozenset({
    "ACTOR_SCOPE", "STATE", "RELATION", "REPLAY_IDEMPOTENCY",
    "TEMPORAL", "CONCURRENCY", "TRANSACTION_FAILURE",
    "BATCH_SCALE", "SURFACE",
})

RISK_LEVELS = frozenset({"LOW", "MEDIUM", "HIGH", "CRITICAL"})


# ─── Operator Definition ───────────────────────────────────────────────────────

def create_operator(
    *,
    operator_type: str,
    category: str,
    description: str = "",
    input_coordinate_requirements: dict[str, Any] | None = None,
    applicability_conditions: list[str] | None = None,
    transformation: str = "",
    required_bindings: list[str] | None = None,
    required_fixtures: list[str] | None = None,
    required_observers: list[str] | None = None,
    expected_discrimination: str = "",
    incompatible_operators: list[str] | None = None,
    risk_level: str = "LOW",
    cost_estimate: float = 1.0,
    source_module: str = "",
) -> dict[str, Any]:
    """Create an exploration operator definition."""
    if category not in OPERATOR_CATEGORIES:
        raise ValueError(f"invalid_category: {category}")
    if risk_level not in RISK_LEVELS:
        raise ValueError(f"invalid_risk_level: {risk_level}")

    op_id = _stable_id(category, operator_type)
    return {
        "operator_id": op_id,
        "operator_type": operator_type,
        "category": category,
        "version": 1,
        "description": description,
        "input_coordinate_requirements": input_coordinate_requirements or {},
        "applicability_conditions": list(applicability_conditions or []),
        "transformation": transformation,
        "required_bindings": list(required_bindings or []),
        "required_fixtures": list(required_fixtures or []),
        "required_observers": list(required_observers or []),
        "expected_discrimination": expected_discrimination,
        "incompatible_operators": list(incompatible_operators or []),
        "risk_level": risk_level,
        "cost_estimate": cost_estimate,
        "source_module": source_module,
        "created_at": time.time(),
    }


# ─── Default Operator Set (54+ operators) ──────────────────────────────────────

def _build_actor_scope_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="SWITCH_ACTOR", category="ACTOR_SCOPE",
            description="Execute same operation with a different actor identity",
            applicability_conditions=["has_multiple_actors", "operation_requires_auth"],
            transformation="actor_id → different_actor_id",
            expected_discrimination="authorization_boundary",
            source_module="actor_matrix_planning.py", cost_estimate=1.0),
        create_operator(operator_type="SWITCH_ROLE", category="ACTOR_SCOPE",
            description="Execute with same tenant but different role",
            applicability_conditions=["has_multiple_roles", "role_based_access"],
            transformation="role_id → different_role_id (same tenant)",
            expected_discrimination="role_authorization",
            source_module="actor_matrix_planning.py", cost_estimate=1.0),
        create_operator(operator_type="SWITCH_TENANT", category="ACTOR_SCOPE",
            description="Execute with same role but different tenant",
            applicability_conditions=["has_multiple_tenants", "tenant_isolation"],
            transformation="tenant_id → different_tenant_id (same role)",
            expected_discrimination="tenant_isolation",
            risk_level="MEDIUM", source_module="actor_matrix_planning.py", cost_estimate=1.0),
        create_operator(operator_type="SWITCH_ORGANIZATION", category="ACTOR_SCOPE",
            description="Execute with different organization scope",
            applicability_conditions=["has_organization_scope"],
            transformation="organization_scope → different_organization",
            expected_discrimination="organization_boundary",
            source_module="actor_matrix_planning.py", cost_estimate=1.0),
        create_operator(operator_type="SWITCH_RESOURCE_OWNER", category="ACTOR_SCOPE",
            description="Access resource owned by different actor",
            applicability_conditions=["has_ownership_field", "has_multiple_actors"],
            transformation="resource_owner → non_owner_actor",
            expected_discrimination="ownership_boundary",
            source_module="actor_matrix_planning.py", cost_estimate=1.5),
        create_operator(operator_type="USE_UNRELATED_RESOURCE", category="ACTOR_SCOPE",
            description="Access resource with no relation to actor",
            applicability_conditions=["has_resource_scope"],
            transformation="resource_scope → unrelated_resource",
            expected_discrimination="scope_boundary",
            source_module="actor_matrix_planning.py", cost_estimate=1.5),
        create_operator(operator_type="USE_CROSS_SCOPE_RESOURCE", category="ACTOR_SCOPE",
            description="Access resource from different scope boundary",
            applicability_conditions=["has_scope_boundary", "has_multiple_scopes"],
            transformation="scope → cross_scope_resource",
            expected_discrimination="cross_scope_access",
            risk_level="MEDIUM", source_module="actor_matrix_planning.py", cost_estimate=1.5),
    ]


def _build_state_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="MOVE_TO_ALLOWED_STATE", category="STATE",
            description="Move entity to an allowed state via valid path",
            applicability_conditions=["has_state_machine", "has_transition_path"],
            transformation="pre_state → allowed_target_state",
            expected_discrimination="valid_transition",
            source_module="state_path_exploration.py", cost_estimate=2.0),
        create_operator(operator_type="MOVE_TO_FORBIDDEN_STATE", category="STATE",
            description="Attempt transition to forbidden state",
            applicability_conditions=["has_state_machine", "has_forbidden_transitions"],
            transformation="pre_state → forbidden_target_state",
            expected_discrimination="state_boundary",
            risk_level="MEDIUM", source_module="state_path_exploration.py", cost_estimate=2.0),
        create_operator(operator_type="SKIP_REQUIRED_STATE", category="STATE",
            description="Skip intermediate required state in path",
            applicability_conditions=["has_multi_step_path"],
            transformation="state_path: skip intermediate step",
            expected_discrimination="lifecycle_integrity",
            risk_level="MEDIUM", source_module="state_path_exploration.py", cost_estimate=2.5),
        create_operator(operator_type="REPEAT_TERMINAL_TRANSITION", category="STATE",
            description="Repeat transition from terminal state",
            applicability_conditions=["has_terminal_state"],
            transformation="terminal_state → repeat_same_transition",
            expected_discrimination="terminal_state_protection",
            source_module="state_path_exploration.py", cost_estimate=2.0),
        create_operator(operator_type="REVERSE_TRANSITION", category="STATE",
            description="Attempt to reverse a completed transition",
            applicability_conditions=["has_state_machine", "has_bidirectional_potential"],
            transformation="current_state → previous_state (reverse)",
            expected_discrimination="irreversibility",
            risk_level="MEDIUM", source_module="state_path_exploration.py", cost_estimate=2.0),
        create_operator(operator_type="EXECUTE_FROM_INTERMEDIATE_STATE", category="STATE",
            description="Execute operation from unexpected intermediate state",
            applicability_conditions=["has_multi_step_path"],
            transformation="execute_target_op from intermediate_state",
            expected_discrimination="state_precondition",
            source_module="state_path_exploration.py", cost_estimate=2.5),
    ]


def _build_relation_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="SWITCH_RELATED_ENTITY", category="RELATION",
            description="Use different related entity instance",
            applicability_conditions=["has_entity_relation"],
            transformation="related_entity_id → different_instance",
            expected_discrimination="relation_validity",
            source_module="cross_entity_chain_planning.py", cost_estimate=2.0),
        create_operator(operator_type="REMOVE_REQUIRED_RELATION", category="RELATION",
            description="Remove or nullify a required relation",
            applicability_conditions=["has_required_relation"],
            transformation="relation_field → null/empty",
            expected_discrimination="relation_integrity",
            risk_level="MEDIUM", source_module="cross_entity_chain_planning.py", cost_estimate=2.0),
        create_operator(operator_type="USE_SELF_REFERENCE", category="RELATION",
            description="Use entity referencing itself",
            applicability_conditions=["has_self_reference_potential"],
            transformation="relation_target → self",
            expected_discrimination="self_reference_handling",
            source_module="cross_entity_chain_planning.py", cost_estimate=1.5),
        create_operator(operator_type="USE_CROSS_TENANT_RELATION", category="RELATION",
            description="Reference entity from different tenant",
            applicability_conditions=["has_entity_relation", "has_multiple_tenants"],
            transformation="relation_target → cross_tenant_entity",
            expected_discrimination="tenant_relation_isolation",
            risk_level="MEDIUM", source_module="cross_entity_chain_planning.py", cost_estimate=2.0),
        create_operator(operator_type="USE_STALE_RELATION", category="RELATION",
            description="Reference entity that has been modified/deleted",
            applicability_conditions=["has_entity_relation", "has_delete_operation"],
            transformation="relation_target → stale/deleted_entity",
            expected_discrimination="stale_reference_handling",
            risk_level="MEDIUM", source_module="cross_entity_chain_planning.py", cost_estimate=2.5),
        create_operator(operator_type="REORDER_ENTITY_CREATION", category="RELATION",
            description="Create dependent entity before prerequisite",
            applicability_conditions=["has_entity_dependency_chain"],
            transformation="creation_order → reversed",
            expected_discrimination="dependency_enforcement",
            source_module="cross_entity_chain_planning.py", cost_estimate=2.5),
    ]


def _build_replay_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="EXACT_REPLAY", category="REPLAY_IDEMPOTENCY",
            description="Replay exact same request",
            applicability_conditions=["has_write_operation"],
            transformation="request → identical_replay",
            expected_discrimination="idempotency",
            source_module="replay_engine.py", cost_estimate=1.5),
        create_operator(operator_type="SAME_KEY_SAME_PAYLOAD", category="REPLAY_IDEMPOTENCY",
            description="Same idempotency key, same payload",
            applicability_conditions=["has_idempotency_key"],
            transformation="idempotency_key=same, payload=same",
            expected_discrimination="key_based_idempotency",
            source_module="replay_engine.py", cost_estimate=1.5),
        create_operator(operator_type="SAME_KEY_DIFFERENT_PAYLOAD", category="REPLAY_IDEMPOTENCY",
            description="Same idempotency key, different payload",
            applicability_conditions=["has_idempotency_key"],
            transformation="idempotency_key=same, payload=different",
            expected_discrimination="key_collision_handling",
            risk_level="MEDIUM", source_module="replay_engine.py", cost_estimate=1.5),
        create_operator(operator_type="DIFFERENT_KEY_SAME_BUSINESS_IDENTITY", category="REPLAY_IDEMPOTENCY",
            description="Different key but same business entity",
            applicability_conditions=["has_business_identity_field"],
            transformation="key=different, business_identity=same",
            expected_discrimination="business_deduplication",
            source_module="replay_engine.py", cost_estimate=2.0),
        create_operator(operator_type="RETRY_AFTER_TIMEOUT", category="REPLAY_IDEMPOTENCY",
            description="Retry after response timeout",
            applicability_conditions=["has_write_operation"],
            transformation="first_attempt → timeout → retry",
            expected_discrimination="timeout_retry_safety",
            risk_level="MEDIUM", source_module="replay_engine.py", cost_estimate=2.5),
        create_operator(operator_type="REPLAY_AFTER_RESPONSE_LOSS", category="REPLAY_IDEMPOTENCY",
            description="Replay after response was lost",
            applicability_conditions=["has_write_operation"],
            transformation="execute → lose_response → replay",
            expected_discrimination="response_loss_recovery",
            risk_level="MEDIUM", source_module="replay_engine.py", cost_estimate=2.5),
        create_operator(operator_type="DUPLICATE_EVENT_DELIVERY", category="REPLAY_IDEMPOTENCY",
            description="Deliver same event twice to consumer",
            applicability_conditions=["has_event_surface"],
            transformation="event → duplicate_delivery",
            expected_discrimination="event_idempotency",
            source_module="replay_engine.py", cost_estimate=2.0),
    ]


def _build_temporal_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="REORDER", category="TEMPORAL",
            description="Reorder operation sequence",
            applicability_conditions=["has_multi_step_flow"],
            transformation="operation_order → reordered",
            expected_discrimination="order_dependency",
            source_module="temporal_experiment_planning.py", cost_estimate=2.0),
        create_operator(operator_type="DELAY", category="TEMPORAL",
            description="Insert delay between operations",
            applicability_conditions=["has_sequential_operations"],
            transformation="add delay between steps",
            expected_discrimination="timing_sensitivity",
            source_module="temporal_experiment_planning.py", cost_estimate=1.5),
        create_operator(operator_type="EXECUTE_BEFORE_VALID_FROM", category="TEMPORAL",
            description="Execute before temporal validity start",
            applicability_conditions=["has_temporal_validity"],
            transformation="execution_time < valid_from",
            expected_discrimination="temporal_boundary",
            source_module="temporal_experiment_planning.py", cost_estimate=2.0),
        create_operator(operator_type="EXECUTE_AFTER_EXPIRY", category="TEMPORAL",
            description="Execute after temporal expiry",
            applicability_conditions=["has_temporal_expiry"],
            transformation="execution_time > expiry",
            expected_discrimination="expiry_enforcement",
            source_module="temporal_experiment_planning.py", cost_estimate=2.0),
        create_operator(operator_type="USE_STALE_EVENT", category="TEMPORAL",
            description="Process event with outdated timestamp",
            applicability_conditions=["has_event_surface", "has_timestamp"],
            transformation="event_timestamp → stale",
            expected_discrimination="stale_event_handling",
            risk_level="MEDIUM", source_module="temporal_experiment_planning.py", cost_estimate=2.0),
        create_operator(operator_type="OUT_OF_ORDER_EVENT", category="TEMPORAL",
            description="Deliver events out of causal order",
            applicability_conditions=["has_event_sequence"],
            transformation="event_order → out_of_order",
            expected_discrimination="event_ordering",
            risk_level="MEDIUM", source_module="temporal_experiment_planning.py", cost_estimate=2.5),
        create_operator(operator_type="RETRY_AFTER_DELAY", category="TEMPORAL",
            description="Retry operation after significant delay",
            applicability_conditions=["has_write_operation"],
            transformation="execute → long_delay → retry",
            expected_discrimination="delayed_retry_safety",
            source_module="temporal_experiment_planning.py", cost_estimate=2.0),
    ]


def _build_concurrency_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="PARALLEL_SAME_ACTOR", category="CONCURRENCY",
            description="Same actor sends parallel requests",
            applicability_conditions=["has_write_operation"],
            transformation="single_request → parallel_same_actor",
            expected_discrimination="same_actor_race",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="PARALLEL_DIFFERENT_ACTOR", category="CONCURRENCY",
            description="Different actors send parallel requests to same resource",
            applicability_conditions=["has_write_operation", "has_multiple_actors"],
            transformation="single_actor → parallel_different_actors",
            expected_discrimination="multi_actor_race",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="PARALLEL_SAME_RESOURCE", category="CONCURRENCY",
            description="Parallel operations targeting same resource",
            applicability_conditions=["has_resource_id", "has_write_operation"],
            transformation="sequential → parallel_same_resource",
            expected_discrimination="resource_contention",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="PARALLEL_RELATED_RESOURCE", category="CONCURRENCY",
            description="Parallel operations on related resources",
            applicability_conditions=["has_entity_relation", "has_write_operation"],
            transformation="parallel on related entities",
            expected_discrimination="related_resource_race",
            risk_level="HIGH", cost_estimate=3.5),
        create_operator(operator_type="READ_WRITE_INTERLEAVING", category="CONCURRENCY",
            description="Interleave reads and writes",
            applicability_conditions=["has_read_and_write"],
            transformation="read/write → interleaved",
            expected_discrimination="dirty_read",
            risk_level="MEDIUM", cost_estimate=2.5),
        create_operator(operator_type="WRITE_WRITE_INTERLEAVING", category="CONCURRENCY",
            description="Interleave concurrent writes",
            applicability_conditions=["has_write_operation"],
            transformation="writes → concurrent_writes",
            expected_discrimination="lost_update",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="VERSION_CONFLICT", category="CONCURRENCY",
            description="Concurrent updates with version/ETag conflict",
            applicability_conditions=["has_version_field_or_etag"],
            transformation="concurrent_update → version_conflict",
            expected_discrimination="optimistic_locking",
            risk_level="HIGH", cost_estimate=3.0),
    ]


def _build_transaction_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="FAIL_BEFORE_FIRST_SIDE_EFFECT", category="TRANSACTION_FAILURE",
            description="Failure before any side effect occurs",
            applicability_conditions=["has_multi_step_operation"],
            transformation="inject_failure before first write",
            expected_discrimination="clean_failure",
            risk_level="MEDIUM", cost_estimate=2.5),
        create_operator(operator_type="FAIL_AFTER_PARTIAL_SIDE_EFFECT", category="TRANSACTION_FAILURE",
            description="Failure after partial side effects",
            applicability_conditions=["has_multi_step_operation", "has_multiple_side_effects"],
            transformation="inject_failure after partial writes",
            expected_discrimination="partial_failure_consistency",
            risk_level="HIGH", cost_estimate=3.5),
        create_operator(operator_type="FAIL_BEFORE_COMMIT", category="TRANSACTION_FAILURE",
            description="Failure just before transaction commit",
            applicability_conditions=["has_transaction_boundary"],
            transformation="inject_failure before commit",
            expected_discrimination="commit_failure_recovery",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="FAIL_AFTER_COMMIT_BEFORE_RESPONSE", category="TRANSACTION_FAILURE",
            description="Success committed but response lost",
            applicability_conditions=["has_write_operation"],
            transformation="commit_success → response_loss",
            expected_discrimination="response_loss_handling",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="FAIL_EVENT_PUBLISH", category="TRANSACTION_FAILURE",
            description="Event publish fails after business commit",
            applicability_conditions=["has_event_surface"],
            transformation="business_commit → event_publish_failure",
            expected_discrimination="event_publish_reliability",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="FAIL_DEPENDENCY_CALL", category="TRANSACTION_FAILURE",
            description="Downstream dependency call fails",
            applicability_conditions=["has_external_dependency"],
            transformation="dependency_call → failure",
            expected_discrimination="dependency_failure_handling",
            risk_level="HIGH", cost_estimate=3.0),
        create_operator(operator_type="RESTART_DURING_OPERATION", category="TRANSACTION_FAILURE",
            description="Service restart during operation",
            applicability_conditions=["has_long_running_operation"],
            transformation="restart during execution",
            expected_discrimination="crash_recovery",
            risk_level="CRITICAL", cost_estimate=4.0),
        create_operator(operator_type="EXECUTE_COMPENSATION", category="TRANSACTION_FAILURE",
            description="Trigger compensation/saga rollback",
            applicability_conditions=["has_compensation_operation"],
            transformation="partial_success → compensate",
            expected_discrimination="compensation_correctness",
            risk_level="HIGH", cost_estimate=3.5),
        create_operator(operator_type="RETRY_AFTER_PARTIAL_FAILURE", category="TRANSACTION_FAILURE",
            description="Retry after partial failure occurred",
            applicability_conditions=["has_multi_step_operation"],
            transformation="partial_failure → retry",
            expected_discrimination="retry_after_partial",
            risk_level="HIGH", cost_estimate=3.0),
    ]


def _build_batch_scale_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="SCALE_DATA_VOLUME", category="BATCH_SCALE",
            description="Increase data volume significantly",
            applicability_conditions=["has_list_or_batch_operation"],
            transformation="data_volume → 10x/100x",
            expected_discrimination="volume_stability",
            risk_level="MEDIUM", cost_estimate=3.0),
        create_operator(operator_type="SCALE_BATCH_SIZE", category="BATCH_SCALE",
            description="Increase batch size to boundary",
            applicability_conditions=["has_batch_operation"],
            transformation="batch_size → boundary_value",
            expected_discrimination="batch_boundary",
            risk_level="MEDIUM", cost_estimate=2.5),
        create_operator(operator_type="SCALE_CONCURRENCY", category="BATCH_SCALE",
            description="Increase concurrent request count",
            applicability_conditions=["has_write_operation"],
            transformation="concurrency → 10/50/100",
            expected_discrimination="concurrency_stability",
            risk_level="HIGH", cost_estimate=3.5),
        create_operator(operator_type="INCLUDE_INVALID_ITEM_IN_BATCH", category="BATCH_SCALE",
            description="Include invalid item in batch operation",
            applicability_conditions=["has_batch_operation"],
            transformation="batch_items → include_invalid",
            expected_discrimination="batch_validation",
            cost_estimate=2.0),
        create_operator(operator_type="PARTIAL_BATCH_FAILURE", category="BATCH_SCALE",
            description="Some items in batch fail",
            applicability_conditions=["has_batch_operation"],
            transformation="batch → partial_item_failure",
            expected_discrimination="partial_batch_handling",
            risk_level="MEDIUM", cost_estimate=2.5),
        create_operator(operator_type="DUPLICATE_ITEM_IN_BATCH", category="BATCH_SCALE",
            description="Include duplicate items in batch",
            applicability_conditions=["has_batch_operation"],
            transformation="batch_items → include_duplicates",
            expected_discrimination="batch_deduplication",
            cost_estimate=2.0),
        create_operator(operator_type="CROSS_SCOPE_BATCH", category="BATCH_SCALE",
            description="Batch operation across scope boundaries",
            applicability_conditions=["has_batch_operation", "has_scope_boundary"],
            transformation="batch → cross_scope_items",
            expected_discrimination="batch_scope_isolation",
            risk_level="MEDIUM", cost_estimate=2.5),
        create_operator(operator_type="LARGE_PAYLOAD", category="BATCH_SCALE",
            description="Send oversized payload",
            applicability_conditions=["has_write_operation"],
            transformation="payload_size → oversized",
            expected_discrimination="payload_boundary",
            cost_estimate=1.5),
    ]


def _build_surface_operators() -> list[dict[str, Any]]:
    return [
        create_operator(operator_type="EXECUTE_VIA_API", category="SURFACE",
            description="Execute operation via API surface",
            applicability_conditions=["has_api_binding"],
            transformation="execution_surface → API",
            expected_discrimination="api_behavior", cost_estimate=1.0),
        create_operator(operator_type="EXECUTE_VIA_UI", category="SURFACE",
            description="Execute operation via UI surface",
            applicability_conditions=["has_ui_binding"],
            transformation="execution_surface → UI",
            expected_discrimination="ui_behavior", cost_estimate=2.0),
        create_operator(operator_type="EXECUTE_VIA_FILE", category="SURFACE",
            description="Execute operation via file import",
            applicability_conditions=["has_file_import_surface"],
            transformation="execution_surface → FILE",
            expected_discrimination="file_import_behavior", cost_estimate=2.0),
        create_operator(operator_type="EXECUTE_VIA_EVENT", category="SURFACE",
            description="Execute operation via event/message",
            applicability_conditions=["has_event_surface"],
            transformation="execution_surface → EVENT",
            expected_discrimination="event_behavior", cost_estimate=2.0),
        create_operator(operator_type="EXECUTE_VIA_BATCH", category="SURFACE",
            description="Execute operation via batch job",
            applicability_conditions=["has_batch_surface"],
            transformation="execution_surface → BATCH",
            expected_discrimination="batch_behavior", cost_estimate=2.0),
        create_operator(operator_type="OBSERVE_VIA_API", category="SURFACE",
            description="Observe result via API response",
            applicability_conditions=["has_read_operation"],
            transformation="observation_surface → API",
            expected_discrimination="api_observation", cost_estimate=1.0),
        create_operator(operator_type="OBSERVE_VIA_UI", category="SURFACE",
            description="Observe result via UI state",
            applicability_conditions=["has_ui_binding"],
            transformation="observation_surface → UI",
            expected_discrimination="ui_observation", cost_estimate=2.0),
        create_operator(operator_type="OBSERVE_VIA_DB", category="SURFACE",
            description="Observe result via database state",
            applicability_conditions=["has_db_access"],
            transformation="observation_surface → DATABASE",
            expected_discrimination="db_observation", cost_estimate=1.5),
        create_operator(operator_type="OBSERVE_VIA_EVENT", category="SURFACE",
            description="Observe result via event stream",
            applicability_conditions=["has_event_surface"],
            transformation="observation_surface → EVENT_STREAM",
            expected_discrimination="event_observation", cost_estimate=1.5),
        create_operator(operator_type="OBSERVE_VIA_REPORT", category="SURFACE",
            description="Observe result via report/export",
            applicability_conditions=["has_report_surface"],
            transformation="observation_surface → REPORT",
            expected_discrimination="report_observation", cost_estimate=2.0),
    ]


def build_all_operators() -> list[dict[str, Any]]:
    """Build the complete set of 67 exploration operators."""
    operators = []
    operators.extend(_build_actor_scope_operators())      # 7
    operators.extend(_build_state_operators())             # 6
    operators.extend(_build_relation_operators())          # 6
    operators.extend(_build_replay_operators())            # 7
    operators.extend(_build_temporal_operators())          # 7
    operators.extend(_build_concurrency_operators())       # 7
    operators.extend(_build_transaction_operators())       # 9
    operators.extend(_build_batch_scale_operators())       # 8
    operators.extend(_build_surface_operators())           # 10
    return operators  # Total: 67


# ─── Operator Registry ─────────────────────────────────────────────────────────

class ExplorationOperatorRegistry:
    """Unified registry for exploration operators."""

    def __init__(self, *, project_id: str = ""):
        self.project_id = project_id
        self._operators: dict[str, dict[str, Any]] = {}
        self._version = 1

    @property
    def size(self) -> int:
        return len(self._operators)

    @property
    def version(self) -> int:
        return self._version

    def register(self, operator: dict[str, Any]) -> str:
        op_id = operator.get("operator_id", "")
        if not op_id:
            raise ValueError("operator_missing_id")
        self._operators[op_id] = operator
        self._version += 1
        return op_id

    def register_defaults(self) -> int:
        ops = build_all_operators()
        for op in ops:
            self._operators[op["operator_id"]] = op
        self._version += 1
        return len(ops)

    def get(self, operator_id: str) -> dict[str, Any] | None:
        return self._operators.get(operator_id)

    def get_by_type(self, operator_type: str) -> dict[str, Any] | None:
        for op in self._operators.values():
            if op.get("operator_type") == operator_type:
                return op
        return None

    def get_by_category(self, category: str) -> list[dict[str, Any]]:
        return [op for op in self._operators.values()
                if op.get("category") == category]

    def all_types(self) -> list[str]:
        return sorted(op["operator_type"] for op in self._operators.values())

    def all_categories(self) -> list[str]:
        return sorted({op["category"] for op in self._operators.values()})

    def coverage_summary(self) -> dict[str, Any]:
        by_cat: dict[str, int] = {}
        for op in self._operators.values():
            cat = op.get("category", "UNKNOWN")
            by_cat[cat] = by_cat.get(cat, 0) + 1
        return {
            "total_operators": self.size,
            "by_category": by_cat,
            "all_types": self.all_types(),
            "registry_version": self._version,
        }

    def export(self) -> dict[str, Any]:
        return {
            "schema_version": "qualibug.exploration-operator-registry.v1",
            "project_id": self.project_id,
            "registry_version": self._version,
            "operators": list(self._operators.values()),
            "summary": self.coverage_summary(),
        }


# ─── Operator Applicability (SPEC §12) ─────────────────────────────────────────

def check_applicability(
    operator: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
    binding_ledger: Any = None,
    invariant: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check if an operator is applicable given current system state.

    Returns:
        {operator_id, applicable, required_dimensions, satisfied_dimensions,
         missing_dimensions, reason}
    """
    op = operator if isinstance(operator, dict) else {}
    ir = behavior_ir if isinstance(behavior_ir, dict) else {}
    op_id = op.get("operator_id", "")
    op_type = op.get("operator_type", "")
    conditions = op.get("applicability_conditions", [])

    satisfied = []
    missing = []

    # Check each applicability condition
    entities = ir.get("entities", [])
    operations = ir.get("operations", [])
    actors = ir.get("actors", [])
    states = ir.get("states", [])
    relations = ir.get("relations", [])

    has_write = any(o.get("read_write") == "write" for o in operations if isinstance(o, dict))
    has_read = any(o.get("read_write") == "read" for o in operations if isinstance(o, dict))
    has_multi_actors = len(actors) >= 2
    has_states = len(states) >= 1
    has_relations = len(relations) >= 1
    has_multi_ops = len(operations) >= 2

    condition_checks = {
        "has_multiple_actors": has_multi_actors,
        "has_write_operation": has_write,
        "has_read_operation": has_read,
        "has_read_and_write": has_read and has_write,
        "operation_requires_auth": len(actors) >= 1,
        "has_multiple_roles": len({a.get("role") for a in actors if isinstance(a, dict)}) >= 2,
        "role_based_access": len(actors) >= 1,
        "has_multiple_tenants": len({a.get("tenant_scope") for a in actors if isinstance(a, dict) and a.get("tenant_scope")}) >= 2,
        "tenant_isolation": len(actors) >= 2,
        "has_organization_scope": any(a.get("organization_scope") for a in actors if isinstance(a, dict)),
        "has_ownership_field": True,  # Generic - always potentially applicable
        "has_resource_scope": True,
        "has_scope_boundary": len(actors) >= 2,
        "has_multiple_scopes": len({a.get("tenant_scope") for a in actors if isinstance(a, dict)}) >= 2,
        "has_state_machine": has_states,
        "has_transition_path": has_states and has_write,
        "has_forbidden_transitions": has_states,
        "has_multi_step_path": has_states and has_multi_ops,
        "has_terminal_state": any(s.get("terminal_values") for s in states if isinstance(s, dict)),
        "has_bidirectional_potential": has_states,
        "has_entity_relation": has_relations,
        "has_required_relation": has_relations,
        "has_self_reference_potential": has_relations,
        "has_delete_operation": any(o.get("method") == "DELETE" for o in operations if isinstance(o, dict)),
        "has_entity_dependency_chain": has_relations and has_multi_ops,
        "has_idempotency_key": has_write,
        "has_business_identity_field": has_write,
        "has_event_surface": False,  # Requires runtime detection
        "has_multi_step_flow": has_multi_ops,
        "has_sequential_operations": has_multi_ops,
        "has_temporal_validity": False,  # Requires field detection
        "has_temporal_expiry": False,
        "has_timestamp": True,
        "has_event_sequence": False,
        "has_version_field_or_etag": False,  # Requires schema detection
        "has_resource_id": has_write,
        "has_multi_step_operation": has_multi_ops,
        "has_multiple_side_effects": has_multi_ops,
        "has_transaction_boundary": has_write,
        "has_external_dependency": False,
        "has_long_running_operation": False,
        "has_compensation_operation": any(
            r.get("relation_type") == "compensates" for r in relations if isinstance(r, dict)
        ),
        "has_list_or_batch_operation": has_read,
        "has_batch_operation": False,  # Requires batch detection
        "has_api_binding": True,  # API always available
        "has_ui_binding": False,  # Requires UI detection
        "has_file_import_surface": False,
        "has_batch_surface": False,
        "has_db_access": False,  # Requires runtime config
        "has_report_surface": False,
    }

    for cond in conditions:
        if condition_checks.get(cond, False):
            satisfied.append(cond)
        else:
            missing.append(cond)

    applicable = len(missing) == 0
    reason = "" if applicable else f"missing: {', '.join(missing)}"

    return {
        "operator_id": op_id,
        "operator_type": op_type,
        "applicable": applicable,
        "required_dimensions": conditions,
        "satisfied_dimensions": satisfied,
        "missing_dimensions": missing,
        "reason": reason,
    }


def check_all_applicability(
    registry: ExplorationOperatorRegistry,
    *,
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    """Check applicability for all registered operators."""
    results = []
    for op in registry._operators.values():
        result = check_applicability(op, behavior_ir=behavior_ir)
        results.append(result)
    return results
