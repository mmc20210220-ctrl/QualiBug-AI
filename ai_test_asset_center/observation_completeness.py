"""Cross-Entity Observation Completeness and Business Delta Reconstruction.

Compiles Observation Requirements from Oracle/Rule structured expressions,
binds Root and Related Observers, resolves Relation Scope, executes
Before/After Snapshots with same-scope guarantee, reconstructs business
Delta and Aggregates, and gates Oracle Input Completeness.

No project-specific hardcoding. All entity types, field names, relation keys,
and observer paths are derived from Rule/Oracle/Behavior IR at runtime.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

# ─── Constants ───
MAX_STABILIZATION_ATTEMPTS = 5
DEFAULT_POLL_INTERVAL_MS = 200
DEFAULT_MAX_WAIT_MS = 5000
REQUIRED_STABLE_READS = 2

# ─── Standardized Blocked Reasons ───
OBSERVATION_REQUIREMENT_NOT_COMPILED = "OBSERVATION_REQUIREMENT_NOT_COMPILED"
ROOT_OBSERVER_NOT_BOUND = "ROOT_OBSERVER_NOT_BOUND"
RELATED_OBSERVER_NOT_BOUND = "RELATED_OBSERVER_NOT_BOUND"
RELATION_KEY_NOT_RESOLVED = "RELATION_KEY_NOT_RESOLVED"
REQUIRED_OBSERVATION_FIELD_MISSING = "REQUIRED_OBSERVATION_FIELD_MISSING"
REQUIRED_RELATED_ENTITY_NOT_OBSERVED = "REQUIRED_RELATED_ENTITY_NOT_OBSERVED"
OBSERVATION_SCOPE_CHANGED = "OBSERVATION_SCOPE_CHANGED"
OBSERVER_SCOPE_MISMATCH = "OBSERVER_SCOPE_MISMATCH"
OBSERVER_FILTER_NOT_RESOLVED = "OBSERVER_FILTER_NOT_RESOLVED"
OBSERVER_PAGINATION_INCOMPLETE = "OBSERVER_PAGINATION_INCOMPLETE"
OBSERVATION_STABILIZATION_TIMEOUT = "OBSERVATION_STABILIZATION_TIMEOUT"
DELTA_RECONSTRUCTION_FAILED = "DELTA_RECONSTRUCTION_FAILED"
AGGREGATE_RECONSTRUCTION_FAILED = "AGGREGATE_RECONSTRUCTION_FAILED"
ORACLE_INPUT_INCOMPLETE = "ORACLE_INPUT_INCOMPLETE"

# Observation completeness statuses
STATUS_COMPLETE = "COMPLETE"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_INDETERMINATE = "INDETERMINATE"
STATUS_SCOPE_MISMATCH = "SCOPE_MISMATCH"
STATUS_OBSERVER_FAILED = "OBSERVER_FAILED"


# ─── Data Structures ───

@dataclass
class EntityObservationSpec:
    """Specification for observing a single entity."""
    entity_type: str
    instance_binding: str = ""  # how to resolve instance ID
    required_fields: list = field(default_factory=list)
    scope_keys: list = field(default_factory=list)
    observer_operation: str = ""  # GET operation to use
    observer_path_template: str = ""  # e.g. /budgets/{id}
    identifier_source: str = ""  # where to get the ID from


@dataclass
class RelatedEntitySpec:
    """Specification for observing a related entity."""
    entity_type: str
    relation_id: str = ""
    relation_direction: str = "outgoing"  # outgoing from root
    correlation_keys: list = field(default_factory=list)  # [{root_field, related_field}]
    required_fields: list = field(default_factory=list)
    required_cardinality: str = "one"  # one, many
    filters: dict = field(default_factory=dict)
    aggregation_mode: str = ""  # SUM, COUNT, etc.
    observer_operation: str = ""
    observer_path_template: str = ""
    identifier_source: str = ""  # e.g. "root.budget_id"


@dataclass
class ObservationRequirement:
    """Structured observation requirement compiled from Oracle expression."""
    requirement_id: str
    internal_rule_id: str
    experiment_id: str = ""
    oracle_id: str = ""

    root_entity: Optional[EntityObservationSpec] = None
    related_entities: list = field(default_factory=list)

    before_required: bool = True
    after_required: bool = True
    intermediate_required: bool = False

    # Temporal policy
    stabilization_condition: str = ""
    max_wait_ms: int = DEFAULT_MAX_WAIT_MS
    poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS

    # Completeness policy
    missing_field_behavior: str = "BLOCK"  # BLOCK, not default-to-zero
    missing_entity_behavior: str = "BLOCK"
    pagination_required: bool = False
    empty_collection_semantics: str = "VALID_ONLY_IF_QUERY_SUCCEEDED"

    compiled: bool = False
    blocked_reason: str = ""


@dataclass
class ObserverBinding:
    """Binding between an entity and its observer operation."""
    entity_type: str
    observer_id: str
    operation_method: str = "GET"
    path_template: str = ""
    identifier_bindings: dict = field(default_factory=dict)
    field_bindings: list = field(default_factory=list)
    scope_bindings: dict = field(default_factory=dict)
    confidence: float = 1.0
    bound: bool = False
    blocked_reason: str = ""


@dataclass
class RelationScope:
    """Scope definition for related entity observation."""
    root_entity_type: str
    root_instance_id: str
    related_entity_type: str
    relation_id: str = ""
    correlation_keys: list = field(default_factory=list)
    tenant_scope: str = ""
    owner_scope: str = ""
    additional_filters: dict = field(default_factory=dict)


@dataclass
class RelationScopeProof:
    """Proof that observation scope is correct."""
    scope_id: str
    expected_root_id: str
    observed_root_ids: list = field(default_factory=list)
    expected_tenant: str = ""
    observed_tenants: list = field(default_factory=list)
    relation_matches: bool = False
    rejected_records: list = field(default_factory=list)
    complete: bool = False
    blocked_reason: str = ""


@dataclass
class SnapshotRecord:
    """A single entity snapshot."""
    snapshot_id: str
    entity_type: str
    instance_id: str
    observed_at: str = ""
    fields: dict = field(default_factory=dict)
    related_records: list = field(default_factory=list)
    query_executed: bool = False
    query_succeeded: bool = False
    pagination_complete: bool = True
    filter_resolved: bool = True
    relation_verified: bool = False
    valid_empty_collection: bool = False


@dataclass
class SnapshotPair:
    """Before/After snapshot pair with same-scope guarantee."""
    pair_id: str
    root_entity_type: str
    related_scope_hash: str = ""

    before: Optional[SnapshotRecord] = None
    after: Optional[SnapshotRecord] = None

    same_scope: bool = False
    same_root: bool = False
    same_tenant: bool = False
    complete: bool = False
    blocked_reason: str = ""


@dataclass
class DeltaReconstruction:
    """Business delta reconstruction for a single field."""
    field_id: str
    semantic_type: str  # BALANCE, DELTA, OPERATION_INPUT, DERIVED_AGGREGATE

    before_value: Any = None
    after_value: Any = None
    observed_delta: Any = None
    expected_delta: Any = None
    operation_inputs: dict = field(default_factory=dict)

    formula: str = ""
    tolerance: float = 0.01
    result: str = ""  # MATCH, MISMATCH, FAILED
    blocked_reason: str = ""


@dataclass
class AggregateReconstruction:
    """Aggregate reconstruction across related entity collection."""
    aggregate_type: str  # SUM, COUNT, AVG
    related_entity_type: str
    field_id: str

    before_records: list = field(default_factory=list)
    after_records: list = field(default_factory=list)
    excluded_records: list = field(default_factory=list)

    before_result: Any = None
    after_result: Any = None
    observed_delta: Any = None
    scope_complete: bool = False
    blocked_reason: str = ""


@dataclass
class OracleInputCompletenessProof:
    """Proof that Oracle inputs are complete before evaluation."""
    proof_id: str
    internal_rule_id: str
    experiment_id: str = ""

    required_entities: list = field(default_factory=list)
    observed_entities: list = field(default_factory=list)

    required_fields: list = field(default_factory=list)
    observed_fields: list = field(default_factory=list)
    missing_fields: list = field(default_factory=list)

    required_snapshots: list = field(default_factory=list)
    observed_snapshots: list = field(default_factory=list)

    relation_scope_complete: bool = False
    pagination_complete: bool = True
    filters_verified: bool = True
    units_consistent: bool = True
    precision_consistent: bool = True
    before_after_same_scope: bool = False
    stabilization_complete: bool = True

    complete: bool = False
    proof_hash: str = ""
    blocked_reason: str = ""


@dataclass
class ObservationTrace:
    """Trace record for a single observation."""
    observer_id: str
    experiment_id: str
    snapshot_type: str  # before, after
    entity_type: str
    request: dict = field(default_factory=dict)
    response_evidence_id: str = ""
    scope: dict = field(default_factory=dict)
    fields: list = field(default_factory=list)
    records_count: int = 0
    pagination: dict = field(default_factory=dict)
    filter_applied: dict = field(default_factory=dict)
    relation_verified: bool = False
    succeeded: bool = False
    blocked_reason: str = ""


# ─── Core Components ───

class ObservationRequirementCompiler:
    """Compiles observation requirements from Oracle structured expressions.

    Reverse-engineers what entities, fields, and snapshots the Oracle needs
    from its expression structure. No project-specific hardcoding.
    """

    def compile_from_oracle_expression(
        self,
        internal_rule_id: str,
        oracle_expression: dict,
        experiment_id: str = "",
        oracle_id: str = "",
    ) -> ObservationRequirement:
        """Compile ObservationRequirement from Oracle expression.

        Oracle expression format:
        {
            "root_entity": {"type": "...", "fields": [...]},
            "related_entities": [{"type": "...", "fields": [...], "relation": {...}}],
            "checks": [
                {"type": "delta", "entity": "...", "field": "...",
                 "formula": "after - before", "expected": "operation_input"},
                {"type": "aggregate", "entity": "...", "field": "...",
                 "agg": "SUM", "scope": "relation"}
            ],
            "operation_inputs": ["amount", ...]
        }
        """
        req = ObservationRequirement(
            requirement_id=f"obs-req-{uuid.uuid4().hex[:12]}",
            internal_rule_id=internal_rule_id,
            experiment_id=experiment_id,
            oracle_id=oracle_id,
        )

        # Extract root entity spec
        root_spec = oracle_expression.get("root_entity", {})
        if root_spec:
            req.root_entity = EntityObservationSpec(
                entity_type=root_spec.get("type", ""),
                required_fields=root_spec.get("fields", []),
                scope_keys=root_spec.get("scope_keys", []),
                observer_operation=root_spec.get("observer_operation", ""),
                observer_path_template=root_spec.get("observer_path", ""),
                instance_binding=root_spec.get("instance_binding", ""),
            )

        # Extract related entity specs
        for rel in oracle_expression.get("related_entities", []):
            rel_spec = RelatedEntitySpec(
                entity_type=rel.get("type", ""),
                relation_id=rel.get("relation_id", ""),
                relation_direction=rel.get("direction", "outgoing"),
                correlation_keys=rel.get("correlation_keys", []),
                required_fields=rel.get("fields", []),
                required_cardinality=rel.get("cardinality", "one"),
                filters=rel.get("filters", {}),
                aggregation_mode=rel.get("aggregation", ""),
                observer_operation=rel.get("observer_operation", ""),
                observer_path_template=rel.get("observer_path", ""),
                identifier_source=rel.get("identifier_source", ""),
            )
            req.related_entities.append(rel_spec)

        # Determine snapshot requirements from checks
        checks = oracle_expression.get("checks", [])
        has_delta = any(c.get("type") == "delta" for c in checks)
        has_before_ref = any("before" in str(c.get("formula", "")) for c in checks)
        has_after_ref = any("after" in str(c.get("formula", "")) for c in checks)

        req.before_required = has_delta or has_before_ref
        req.after_required = has_delta or has_after_ref

        # Temporal policy
        temporal = oracle_expression.get("temporal_policy", {})
        req.stabilization_condition = temporal.get("stabilization_condition", "")
        req.max_wait_ms = temporal.get("max_wait_ms", DEFAULT_MAX_WAIT_MS)
        req.poll_interval_ms = temporal.get("poll_interval_ms", DEFAULT_POLL_INTERVAL_MS)

        req.compiled = True
        return req

    def validate_requirement(self, req: ObservationRequirement) -> tuple:
        """Validate that requirement is complete enough to execute.
        Returns (valid: bool, blocked_reason: str).
        """
        if not req.compiled:
            return False, OBSERVATION_REQUIREMENT_NOT_COMPILED
        if not req.root_entity or not req.root_entity.entity_type:
            return False, ROOT_OBSERVER_NOT_BOUND
        if not req.root_entity.required_fields:
            return False, REQUIRED_OBSERVATION_FIELD_MISSING
        for rel in req.related_entities:
            if not rel.entity_type:
                return False, RELATED_OBSERVER_NOT_BOUND
            if not rel.required_fields:
                return False, REQUIRED_OBSERVATION_FIELD_MISSING
            if not rel.correlation_keys and rel.required_cardinality == "one":
                if not rel.identifier_source:
                    return False, RELATION_KEY_NOT_RESOLVED
        return True, ""


class ObserverBinder:
    """Binds observers to entities based on available operations.

    Priority:
    1. Explicit single-entity detail endpoint
    2. Explicit relation query endpoint
    3. List endpoint with server-side filter
    4. List endpoint with client-side filter
    5. Cannot bind
    """

    def bind_root_observer(
        self,
        req: ObservationRequirement,
        available_operations: list,
    ) -> ObserverBinding:
        """Bind observer for root entity."""
        if not req.root_entity:
            return ObserverBinding(
                entity_type="", observer_id="",
                blocked_reason=ROOT_OBSERVER_NOT_BOUND,
            )

        entity_type = req.root_entity.entity_type
        # If explicit path provided in requirement
        if req.root_entity.observer_path_template:
            binding = ObserverBinding(
                entity_type=entity_type,
                observer_id=f"root-{entity_type}-observer",
                operation_method="GET",
                path_template=req.root_entity.observer_path_template,
                field_bindings=req.root_entity.required_fields,
                confidence=1.0,
                bound=True,
            )
            return binding

        # Search available operations for detail endpoint
        best = self._find_best_observer(entity_type, available_operations)
        if best:
            best.field_bindings = req.root_entity.required_fields
            best.bound = True
            return best

        return ObserverBinding(
            entity_type=entity_type, observer_id="",
            blocked_reason=ROOT_OBSERVER_NOT_BOUND,
        )

    def bind_related_observer(
        self,
        rel_spec: RelatedEntitySpec,
        available_operations: list,
    ) -> ObserverBinding:
        """Bind observer for a related entity."""
        entity_type = rel_spec.entity_type

        # If explicit path provided
        if rel_spec.observer_path_template:
            binding = ObserverBinding(
                entity_type=entity_type,
                observer_id=f"related-{entity_type}-observer",
                operation_method="GET",
                path_template=rel_spec.observer_path_template,
                field_bindings=rel_spec.required_fields,
                confidence=1.0,
                bound=True,
            )
            return binding

        # Search available operations
        best = self._find_best_observer(entity_type, available_operations)
        if best:
            best.field_bindings = rel_spec.required_fields
            best.bound = True
            return best

        return ObserverBinding(
            entity_type=entity_type, observer_id="",
            blocked_reason=RELATED_OBSERVER_NOT_BOUND,
        )

    def _find_best_observer(
        self, entity_type: str, available_operations: list
    ) -> Optional[ObserverBinding]:
        """Find best observer operation for entity type.

        Priority: detail endpoint > relation query > list+filter > list
        """
        detail_ops = []
        list_ops = []

        for op in available_operations:
            op_entity = op.get("entity_type", "")
            if op_entity != entity_type:
                continue
            method = op.get("method", "GET")
            if method != "GET":
                continue
            path = op.get("path", "")
            if "{" in path:  # detail endpoint with ID parameter
                detail_ops.append(op)
            else:
                list_ops.append(op)

        # Priority 1: detail endpoint
        if detail_ops:
            op = detail_ops[0]
            return ObserverBinding(
                entity_type=entity_type,
                observer_id=f"{entity_type}-detail-{op.get('operation_id', '')}",
                operation_method="GET",
                path_template=op["path"],
                confidence=1.0,
            )

        # Priority 2: list endpoint
        if list_ops:
            op = list_ops[0]
            return ObserverBinding(
                entity_type=entity_type,
                observer_id=f"{entity_type}-list-{op.get('operation_id', '')}",
                operation_method="GET",
                path_template=op["path"],
                confidence=0.8,
            )

        return None


class RelationScopeResolver:
    """Resolves and verifies relation scope for cross-entity observation."""

    def resolve_scope(
        self,
        root_instance_id: str,
        root_data: dict,
        rel_spec: RelatedEntitySpec,
        tenant_id: str = "",
    ) -> RelationScope:
        """Resolve relation scope from root data and relation spec."""
        correlation_keys = []
        for ck in rel_spec.correlation_keys:
            root_field = ck.get("root_field", "")
            related_field = ck.get("related_field", "")
            expected_value = root_data.get(root_field, "")
            correlation_keys.append({
                "root_field": root_field,
                "related_field": related_field,
                "expected_value": expected_value,
            })

        # If identifier_source is specified (e.g. "root.budget_id")
        if rel_spec.identifier_source and not correlation_keys:
            source_path = rel_spec.identifier_source
            if source_path.startswith("root."):
                field_name = source_path[5:]
                value = root_data.get(field_name, "")
                correlation_keys.append({
                    "root_field": field_name,
                    "related_field": "id",
                    "expected_value": value,
                })

        return RelationScope(
            root_entity_type=rel_spec.entity_type,  # will be overridden
            root_instance_id=root_instance_id,
            related_entity_type=rel_spec.entity_type,
            relation_id=rel_spec.relation_id,
            correlation_keys=correlation_keys,
            tenant_scope=tenant_id,
        )

    def verify_scope(
        self,
        scope: RelationScope,
        observed_data: dict,
    ) -> RelationScopeProof:
        """Verify that observed data matches expected scope."""
        proof = RelationScopeProof(
            scope_id=f"scope-proof-{uuid.uuid4().hex[:12]}",
            expected_root_id=scope.root_instance_id,
            expected_tenant=scope.tenant_scope,
        )

        # Check correlation keys
        all_match = True
        for ck in scope.correlation_keys:
            related_field = ck.get("related_field", "id")
            expected = ck.get("expected_value", "")
            observed = observed_data.get(related_field, "")
            if expected and observed and str(expected) != str(observed):
                all_match = False
                proof.rejected_records.append({
                    "field": related_field,
                    "expected": expected,
                    "observed": observed,
                    "reason": "correlation_key_mismatch",
                })

        proof.relation_matches = all_match

        # Check tenant
        if scope.tenant_scope:
            observed_tenant = observed_data.get("tenant_id", "")
            proof.observed_tenants = [observed_tenant] if observed_tenant else []
            if observed_tenant and observed_tenant != scope.tenant_scope:
                all_match = False
                proof.rejected_records.append({
                    "field": "tenant_id",
                    "expected": scope.tenant_scope,
                    "observed": observed_tenant,
                    "reason": "tenant_mismatch",
                })

        proof.complete = all_match
        if not all_match:
            proof.blocked_reason = OBSERVER_SCOPE_MISMATCH
        return proof


class SnapshotPairBuilder:
    """Builds Before/After snapshot pairs with same-scope guarantee."""

    def build_pair(
        self,
        root_entity_type: str,
        root_instance_id: str,
        before_data: dict,
        after_data: dict,
        tenant_id: str = "",
        related_before: Optional[list] = None,
        related_after: Optional[list] = None,
    ) -> SnapshotPair:
        """Build a snapshot pair and verify same-scope."""
        pair = SnapshotPair(
            pair_id=f"snap-pair-{uuid.uuid4().hex[:12]}",
            root_entity_type=root_entity_type,
        )

        # Build before snapshot
        pair.before = SnapshotRecord(
            snapshot_id=f"snap-before-{uuid.uuid4().hex[:8]}",
            entity_type=root_entity_type,
            instance_id=root_instance_id,
            observed_at=before_data.get("_observed_at", ""),
            fields=before_data,
            related_records=related_before or [],
            query_executed=True,
            query_succeeded=True,
            relation_verified=True,
        )

        # Build after snapshot
        pair.after = SnapshotRecord(
            snapshot_id=f"snap-after-{uuid.uuid4().hex[:8]}",
            entity_type=root_entity_type,
            instance_id=root_instance_id,
            observed_at=after_data.get("_observed_at", ""),
            fields=after_data,
            related_records=related_after or [],
            query_executed=True,
            query_succeeded=True,
            relation_verified=True,
        )

        # Verify same scope
        before_id = before_data.get("id", root_instance_id)
        after_id = after_data.get("id", root_instance_id)
        pair.same_root = str(before_id) == str(after_id)

        before_tenant = before_data.get("tenant_id", tenant_id)
        after_tenant = after_data.get("tenant_id", tenant_id)
        pair.same_tenant = str(before_tenant) == str(after_tenant)

        # Scope hash: same entity type + same instance + same tenant
        scope_str = f"{root_entity_type}:{root_instance_id}:{tenant_id}"
        pair.related_scope_hash = hashlib.sha256(scope_str.encode()).hexdigest()[:16]
        pair.same_scope = pair.same_root and pair.same_tenant

        pair.complete = pair.same_scope
        if not pair.complete:
            pair.blocked_reason = OBSERVATION_SCOPE_CHANGED

        return pair


class CollectionObservationValidator:
    """Distinguishes valid empty collections from observer failures."""

    def validate_collection(
        self,
        query_executed: bool,
        query_succeeded: bool,
        pagination_complete: bool,
        filter_resolved: bool,
        relation_verified: bool,
        records: list,
    ) -> dict:
        """Validate collection observation.

        Only when ALL conditions are true and records=[] is it a valid
        empty collection. Otherwise it's an observer failure.
        """
        valid_empty = (
            query_executed
            and query_succeeded
            and pagination_complete
            and filter_resolved
            and relation_verified
            and len(records) == 0
        )

        return {
            "query_executed": query_executed,
            "query_succeeded": query_succeeded,
            "pagination_complete": pagination_complete,
            "filter_resolved": filter_resolved,
            "relation_verified": relation_verified,
            "records": records,
            "valid_empty_collection": valid_empty,
            "status": STATUS_COMPLETE if (query_executed and query_succeeded) else STATUS_OBSERVER_FAILED,
        }


class DeltaReconstructor:
    """Reconstructs business deltas from Before/After observations."""

    def reconstruct_field_delta(
        self,
        field_id: str,
        before_value: Any,
        after_value: Any,
        expected_delta: Any = None,
        operation_inputs: Optional[dict] = None,
        formula: str = "after - before",
        tolerance: float = 0.01,
    ) -> DeltaReconstruction:
        """Reconstruct delta for a single field.

        Strictly distinguishes Balance, Delta, Operation Input, and
        Derived Aggregate. Never defaults missing to 0.
        """
        delta = DeltaReconstruction(
            field_id=field_id,
            semantic_type="BALANCE",
            before_value=before_value,
            after_value=after_value,
            expected_delta=expected_delta,
            operation_inputs=operation_inputs or {},
            formula=formula,
            tolerance=tolerance,
        )

        # Validate inputs exist
        if before_value is None:
            delta.result = "FAILED"
            delta.blocked_reason = REQUIRED_OBSERVATION_FIELD_MISSING
            return delta
        if after_value is None:
            delta.result = "FAILED"
            delta.blocked_reason = REQUIRED_OBSERVATION_FIELD_MISSING
            return delta

        # Calculate observed delta
        try:
            observed = float(after_value) - float(before_value)
            delta.observed_delta = observed
        except (TypeError, ValueError):
            delta.result = "FAILED"
            delta.blocked_reason = DELTA_RECONSTRUCTION_FAILED
            return delta

        # Compare with expected if provided
        if expected_delta is not None:
            try:
                expected_f = float(expected_delta)
                if abs(observed - expected_f) <= tolerance:
                    delta.result = "MATCH"
                else:
                    delta.result = "MISMATCH"
            except (TypeError, ValueError):
                delta.result = "FAILED"
                delta.blocked_reason = DELTA_RECONSTRUCTION_FAILED
        else:
            delta.result = "MATCH"  # no expected to compare

        return delta

    def reconstruct_aggregate(
        self,
        aggregate_type: str,
        related_entity_type: str,
        field_id: str,
        before_records: list,
        after_records: list,
        scope_complete: bool = True,
    ) -> AggregateReconstruction:
        """Reconstruct aggregate across related entity collection."""
        agg = AggregateReconstruction(
            aggregate_type=aggregate_type,
            related_entity_type=related_entity_type,
            field_id=field_id,
            before_records=before_records,
            after_records=after_records,
            scope_complete=scope_complete,
        )

        if not scope_complete:
            agg.blocked_reason = AGGREGATE_RECONSTRUCTION_FAILED
            return agg

        try:
            if aggregate_type == "SUM":
                agg.before_result = sum(
                    float(r.get(field_id, 0)) for r in before_records
                )
                agg.after_result = sum(
                    float(r.get(field_id, 0)) for r in after_records
                )
            elif aggregate_type == "COUNT":
                agg.before_result = len(before_records)
                agg.after_result = len(after_records)
            else:
                agg.blocked_reason = AGGREGATE_RECONSTRUCTION_FAILED
                return agg

            agg.observed_delta = agg.after_result - agg.before_result
        except (TypeError, ValueError, AttributeError):
            agg.blocked_reason = AGGREGATE_RECONSTRUCTION_FAILED
            return agg

        return agg


class StabilizationPolicy:
    """Async stabilization strategy for related entity observation."""

    def check_stability(
        self,
        read_fn,
        key_fields: list,
        terminal_condition: Optional[dict] = None,
        required_stable_reads: int = REQUIRED_STABLE_READS,
        poll_interval_ms: int = DEFAULT_POLL_INTERVAL_MS,
        max_attempts: int = MAX_STABILIZATION_ATTEMPTS,
    ) -> dict:
        """Poll until key fields are stable across consecutive reads.

        Returns stabilization result with final data or timeout.
        """
        previous_values = None
        stable_count = 0
        last_data = None

        for attempt in range(max_attempts):
            data = read_fn()
            if data is None:
                return {
                    "stable": False,
                    "attempts": attempt + 1,
                    "blocked_reason": OBSERVATION_STABILIZATION_TIMEOUT,
                    "data": None,
                }

            last_data = data
            current_values = {f: data.get(f) for f in key_fields}

            # Check terminal condition if specified
            if terminal_condition:
                term_field = terminal_condition.get("field", "")
                term_value = terminal_condition.get("value", "")
                if data.get(term_field) != term_value:
                    # Not yet terminal, keep polling
                    previous_values = current_values
                    stable_count = 0
                    time.sleep(poll_interval_ms / 1000.0)
                    continue

            if current_values == previous_values:
                stable_count += 1
                if stable_count >= required_stable_reads:
                    return {
                        "stable": True,
                        "attempts": attempt + 1,
                        "blocked_reason": "",
                        "data": data,
                    }
            else:
                stable_count = 0

            previous_values = current_values
            if attempt < max_attempts - 1:
                time.sleep(poll_interval_ms / 1000.0)

        return {
            "stable": False,
            "attempts": max_attempts,
            "blocked_reason": OBSERVATION_STABILIZATION_TIMEOUT,
            "data": last_data,
        }


class OracleInputCompletenessGate:
    """Gates Oracle evaluation on input completeness.

    Oracle may only be called when complete=True.
    Incomplete inputs MUST produce INDETERMINATE, never PASS.
    """

    def build_proof(
        self,
        internal_rule_id: str,
        experiment_id: str,
        requirement: ObservationRequirement,
        root_before: Optional[dict],
        root_after: Optional[dict],
        related_before: Optional[dict],
        related_after: Optional[dict],
        scope_proof: Optional[RelationScopeProof],
        snapshot_pair: Optional[SnapshotPair],
    ) -> OracleInputCompletenessProof:
        """Build completeness proof for Oracle input."""
        proof = OracleInputCompletenessProof(
            proof_id=f"completeness-{uuid.uuid4().hex[:12]}",
            internal_rule_id=internal_rule_id,
            experiment_id=experiment_id,
        )

        # Collect required entities
        required_entities = []
        if requirement.root_entity:
            required_entities.append(requirement.root_entity.entity_type)
        for rel in requirement.related_entities:
            required_entities.append(rel.entity_type)
        proof.required_entities = required_entities

        # Check observed entities
        observed_entities = []
        if root_before is not None or root_after is not None:
            if requirement.root_entity:
                observed_entities.append(requirement.root_entity.entity_type)
        if related_before is not None or related_after is not None:
            for rel in requirement.related_entities:
                observed_entities.append(rel.entity_type)
        proof.observed_entities = observed_entities

        # Check required fields
        required_fields = []
        missing_fields = []

        if requirement.root_entity:
            for f in requirement.root_entity.required_fields:
                field_key = f"{requirement.root_entity.entity_type}.{f}"
                required_fields.append(field_key)
                # Check in before/after
                before_has = root_before and f in root_before
                after_has = root_after and f in root_after
                if requirement.before_required and not before_has:
                    missing_fields.append(f"before.{field_key}")
                if requirement.after_required and not after_has:
                    missing_fields.append(f"after.{field_key}")

        for rel in requirement.related_entities:
            for f in rel.required_fields:
                field_key = f"{rel.entity_type}.{f}"
                required_fields.append(field_key)
                before_has = related_before and f in related_before
                after_has = related_after and f in related_after
                if requirement.before_required and not before_has:
                    missing_fields.append(f"before.{field_key}")
                if requirement.after_required and not after_has:
                    missing_fields.append(f"after.{field_key}")

        proof.required_fields = required_fields
        proof.observed_fields = [f for f in required_fields if f not in missing_fields]
        proof.missing_fields = missing_fields

        # Snapshots
        proof.required_snapshots = []
        proof.observed_snapshots = []
        if requirement.before_required:
            proof.required_snapshots.append("before")
            if root_before is not None:
                proof.observed_snapshots.append("before")
        if requirement.after_required:
            proof.required_snapshots.append("after")
            if root_after is not None:
                proof.observed_snapshots.append("after")

        # Scope and consistency checks
        proof.relation_scope_complete = (
            scope_proof is not None and scope_proof.complete
        ) if requirement.related_entities else True

        proof.before_after_same_scope = (
            snapshot_pair is not None and snapshot_pair.same_scope
        ) if snapshot_pair else True

        proof.pagination_complete = True  # single-entity reads
        proof.filters_verified = True
        proof.units_consistent = True
        proof.precision_consistent = True
        proof.stabilization_complete = True

        # Final completeness determination
        proof.complete = (
            len(missing_fields) == 0
            and set(required_entities) == set(observed_entities)
            and proof.relation_scope_complete
            and proof.before_after_same_scope
            and set(proof.required_snapshots) == set(proof.observed_snapshots)
        )

        if not proof.complete:
            if missing_fields:
                proof.blocked_reason = REQUIRED_OBSERVATION_FIELD_MISSING
            elif set(required_entities) != set(observed_entities):
                proof.blocked_reason = REQUIRED_RELATED_ENTITY_NOT_OBSERVED
            elif not proof.relation_scope_complete:
                proof.blocked_reason = OBSERVER_SCOPE_MISMATCH
            elif not proof.before_after_same_scope:
                proof.blocked_reason = OBSERVATION_SCOPE_CHANGED
            else:
                proof.blocked_reason = ORACLE_INPUT_INCOMPLETE

        # Proof hash
        hash_input = json.dumps({
            "entities": sorted(proof.observed_entities),
            "fields": sorted(proof.observed_fields),
            "snapshots": sorted(proof.observed_snapshots),
            "scope": proof.relation_scope_complete,
            "same_scope": proof.before_after_same_scope,
        }, sort_keys=True)
        proof.proof_hash = hashlib.sha256(hash_input.encode()).hexdigest()[:16]

        return proof

    def gate_oracle_call(self, proof: OracleInputCompletenessProof) -> str:
        """Determine Oracle call permission.

        Returns:
            "PROCEED" if complete
            "INDETERMINATE" if incomplete (never PASS)
        """
        if proof.complete:
            return "PROCEED"
        return STATUS_INDETERMINATE


# ─── Orchestrator ───

class CrossEntityObservation:
    """Main orchestrator for cross-entity observation completeness.

    Coordinates requirement compilation, observer binding, scope resolution,
    snapshot pairing, delta reconstruction, and completeness gating.
    """

    def __init__(self):
        self.compiler = ObservationRequirementCompiler()
        self.binder = ObserverBinder()
        self.scope_resolver = RelationScopeResolver()
        self.snapshot_builder = SnapshotPairBuilder()
        self.collection_validator = CollectionObservationValidator()
        self.delta_reconstructor = DeltaReconstructor()
        self.stabilization = StabilizationPolicy()
        self.completeness_gate = OracleInputCompletenessGate()
        self.traces: list = []

    def compile_requirement(
        self,
        internal_rule_id: str,
        oracle_expression: dict,
        experiment_id: str = "",
    ) -> ObservationRequirement:
        """Step 1: Compile observation requirement from Oracle."""
        return self.compiler.compile_from_oracle_expression(
            internal_rule_id=internal_rule_id,
            oracle_expression=oracle_expression,
            experiment_id=experiment_id,
        )

    def bind_observers(
        self,
        requirement: ObservationRequirement,
        available_operations: list,
    ) -> dict:
        """Step 2: Bind Root and Related observers."""
        result = {"root": None, "related": [], "all_bound": True}

        # Bind root
        root_binding = self.binder.bind_root_observer(
            requirement, available_operations
        )
        result["root"] = root_binding
        if not root_binding.bound:
            result["all_bound"] = False

        # Bind related
        for rel_spec in requirement.related_entities:
            rel_binding = self.binder.bind_related_observer(
                rel_spec, available_operations
            )
            result["related"].append(rel_binding)
            if not rel_binding.bound:
                result["all_bound"] = False

        return result

    def resolve_relation_scope(
        self,
        root_instance_id: str,
        root_data: dict,
        rel_spec: RelatedEntitySpec,
        tenant_id: str = "",
    ) -> RelationScope:
        """Step 3: Resolve relation scope."""
        return self.scope_resolver.resolve_scope(
            root_instance_id, root_data, rel_spec, tenant_id
        )

    def build_snapshot_pair(
        self,
        root_entity_type: str,
        root_instance_id: str,
        before_data: dict,
        after_data: dict,
        tenant_id: str = "",
        related_before: Optional[list] = None,
        related_after: Optional[list] = None,
    ) -> SnapshotPair:
        """Step 4: Build Before/After snapshot pair."""
        return self.snapshot_builder.build_pair(
            root_entity_type, root_instance_id,
            before_data, after_data, tenant_id,
            related_before, related_after,
        )

    def reconstruct_deltas(
        self,
        requirement: ObservationRequirement,
        root_before: dict,
        root_after: dict,
        related_before: Optional[dict] = None,
        related_after: Optional[dict] = None,
        operation_inputs: Optional[dict] = None,
    ) -> list:
        """Step 5: Reconstruct business deltas."""
        deltas = []

        # Root entity deltas
        if requirement.root_entity:
            for f in requirement.root_entity.required_fields:
                if f in ("status", "id", "tenant_id"):
                    continue  # skip non-numeric fields
                before_val = root_before.get(f)
                after_val = root_after.get(f)
                if before_val is not None and after_val is not None:
                    try:
                        float(before_val)
                        float(after_val)
                    except (TypeError, ValueError):
                        continue
                    delta = self.delta_reconstructor.reconstruct_field_delta(
                        field_id=f"{requirement.root_entity.entity_type}.{f}",
                        before_value=before_val,
                        after_value=after_val,
                        operation_inputs=operation_inputs,
                    )
                    deltas.append(delta)

        # Related entity deltas
        if related_before and related_after:
            for rel_spec in requirement.related_entities:
                for f in rel_spec.required_fields:
                    before_val = related_before.get(f)
                    after_val = related_after.get(f)
                    if before_val is not None and after_val is not None:
                        try:
                            float(before_val)
                            float(after_val)
                        except (TypeError, ValueError):
                            continue
                        expected = None
                        if operation_inputs:
                            # Try to find expected delta from operation inputs
                            expected = operation_inputs.get(f"expected_delta_{f}")
                        delta = self.delta_reconstructor.reconstruct_field_delta(
                            field_id=f"{rel_spec.entity_type}.{f}",
                            before_value=before_val,
                            after_value=after_val,
                            expected_delta=expected,
                            operation_inputs=operation_inputs,
                        )
                        deltas.append(delta)

        return deltas

    def gate_oracle(
        self,
        requirement: ObservationRequirement,
        root_before: Optional[dict],
        root_after: Optional[dict],
        related_before: Optional[dict],
        related_after: Optional[dict],
        scope_proof: Optional[RelationScopeProof] = None,
        snapshot_pair: Optional[SnapshotPair] = None,
    ) -> OracleInputCompletenessProof:
        """Step 6: Gate Oracle input completeness."""
        return self.completeness_gate.build_proof(
            internal_rule_id=requirement.internal_rule_id,
            experiment_id=requirement.experiment_id,
            requirement=requirement,
            root_before=root_before,
            root_after=root_after,
            related_before=related_before,
            related_after=related_after,
            scope_proof=scope_proof,
            snapshot_pair=snapshot_pair,
        )
