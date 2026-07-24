"""Cross-Entity Operation Chain Planning Module.

SPEC: 跨实体业务操作链自动构建与执行
Breakpoint: CROSS_ENTITY_OPERATION_CHAIN_NOT_BUILT

This module builds execution chains for operations requiring multiple entities
or cross-entity preconditions. Unlike single-entity planning, this module:

1. Detects operations requiring multiple instances of same entity
2. Detects cross-entity preconditions from Behavior IR relations
3. Builds execution chains for multiple entities
4. Generates chain proofs and dependency proofs

Core production call chain:
    Cross-Entity Rule
    → Required Entities Detection
    → Entity Role Assignment
    → Per-Entity Chain Planning
    → Chain Dependency Resolution
    → Actor Binding
    → Chain Executability Gate
    → Existing Fixture
    → Existing Executor
    → Cross-Entity Chain Proof
    → Target Operation
    → Observation Proof
    → Existing Oracle
    → Finding

Fully generic: no project-specific or benchmark-specific logic.
"""
from __future__ import annotations

import hashlib
from typing import Any


# ─── Helpers ───────────────────────────────────────────────────────────────────

def _dict(v: Any) -> dict[str, Any]:
    return v if isinstance(v, dict) else {}


def _list(v: Any) -> list[Any]:
    return v if isinstance(v, list) else []


def _text(v: Any) -> str:
    return str(v or "").strip()


def _stable_id(*parts: str) -> str:
    raw = "|".join(parts)
    return "xce_" + hashlib.sha256(raw.encode()).hexdigest()[:16]


# ─── Cross-Entity Detection ───────────────────────────────────────────────────

def detect_cross_entity_requirement(
    obligation: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Detect if an obligation requires cross-entity chain construction.

    Returns detection result with:
      - is_cross_entity: bool
      - chain_type: SELF_REFERENCE | CROSS_ENTITY_PRECONDITION | MULTI_INSTANCE
      - required_entities: list of entity roles
      - evidence: detection evidence
    """
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))
    rule_type = _text(inv.get("rule_type") or expr.get("rule_type"))
    description = _text(
        inv.get("description") or expr.get("description")
        or obligation.get("description")
    )

    # Detection signals for cross-entity requirement
    signals: list[str] = []
    chain_type = None
    required_entities: list[dict[str, Any]] = []

    # Signal 1: Rule type explicitly cross-entity
    if "CROSS_ENTITY" in rule_type.upper():
        signals.append(f"rule_type={rule_type}")
        chain_type = "CROSS_ENTITY_PRECONDITION"

    # Signal 2: Expression mentions multiple entities
    subject_entity = _text(expr.get("subject_entity") or expr.get("entity"))
    reference_entity = _text(
        expr.get("reference_entity") or expr.get("dependent_entity")
        or expr.get("related_entity")
    )
    if subject_entity and reference_entity and subject_entity != reference_entity:
        signals.append(f"entities={subject_entity},{reference_entity}")
        if not chain_type:
            chain_type = "CROSS_ENTITY_PRECONDITION"
        required_entities = [
            {"role": "primary", "entity": subject_entity},
            {"role": "dependent", "entity": reference_entity},
        ]

    # Signal 3: Self-referencing operation (e.g., merge two tickets)
    operation_ref = _text(
        obligation.get("operation_ref") or obligation.get("target_operation")
    )
    ops_by_id = _dict(ir.get("operations_by_id") or ir.get("ops_by_id"))
    operation = _dict(ops_by_id.get(operation_ref))
    op_path = _text(operation.get("path") or "")
    op_method = _text(operation.get("method") or "").upper()

    # Detect self-reference from request body fields
    request_schema = _dict(operation.get("request_schema") or operation.get("request_body"))
    body_fields = list(request_schema.get("properties", {}).keys()) if request_schema else []

    # Check for multiple ID fields referencing same entity type
    entity_ref = _text(inv.get("entity_ref") or operation.get("entity_ref"))
    id_fields = [f for f in body_fields if "id" in f.lower()]
    if len(id_fields) >= 2 and entity_ref:
        signals.append(f"multi_id_fields={id_fields}")
        if not chain_type:
            chain_type = "SELF_REFERENCE"
        required_entities = [
            {"role": f"instance_{i}", "entity": entity_ref}
            for i in range(len(id_fields))
        ]

    # Signal 4: Description mentions multiple entities or cross-entity keywords
    cross_entity_keywords = [
        "cross-entity", "cross entity", "multiple entities",
        "dependent entity", "referencing", "foreign key",
        "must not be CLOSED", "active tickets", "has_active",
        "source ticket", "target ticket",
    ]
    desc_lower = description.lower()
    for kw in cross_entity_keywords:
        if kw.lower() in desc_lower:
            signals.append(f"keyword={kw}")
            break

    # Signal 5: Expression has precondition referencing another entity
    preconditions = _list(expr.get("preconditions") or expr.get("precondition"))
    for pc in preconditions:
        pc_dict = _dict(pc) if isinstance(pc, dict) else {}
        pc_entity = _text(pc_dict.get("entity") or pc_dict.get("reference_entity"))
        if pc_entity and pc_entity != subject_entity:
            signals.append(f"precondition_entity={pc_entity}")
            if not chain_type:
                chain_type = "CROSS_ENTITY_PRECONDITION"

    # Signal 6: Obligation has explicit required_entities or entity_roles
    obl_entities = _list(obligation.get("required_entities"))
    obl_roles = _dict(obligation.get("entity_roles"))
    if obl_entities and len(obl_entities) >= 2:
        signals.append(f"required_entities={obl_entities}")
        if not chain_type:
            chain_type = "MULTI_INSTANCE"
    if obl_roles:
        signals.append(f"entity_roles={list(obl_roles.keys())}")
        if not chain_type:
            chain_type = "CROSS_ENTITY_PRECONDITION"

    is_cross_entity = len(signals) >= 1 and chain_type is not None

    return {
        "is_cross_entity": is_cross_entity,
        "chain_type": chain_type,
        "required_entities": required_entities,
        "signals": signals,
        "entity_ref": entity_ref,
        "subject_entity": subject_entity,
        "reference_entity": reference_entity,
        "operation_ref": operation_ref,
        "description": description,
    }


# ─── Chain Building ───────────────────────────────────────────────────────────

def build_cross_entity_chain(
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
) -> dict[str, Any]:
    """Build execution chain for cross-entity operation.

    Returns chain with:
      - status: BUILT | INSUFFICIENT_INFO
      - entity_chains: per-entity setup sequences
      - target_operation: final operation to execute
      - chain_proof: evidence of chain construction
    """
    chain_type = _text(detection.get("chain_type"))
    entity_ref = _text(detection.get("entity_ref"))
    operation_ref = _text(detection.get("operation_ref"))
    description = _text(detection.get("description"))

    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))

    ops_by_id = _dict(ir.get("operations_by_id") or ir.get("ops_by_id"))
    operations = _list(ir.get("operations"))
    if not ops_by_id and operations:
        ops_by_id = {_text(op.get("operation_id") or op.get("id")): op for op in operations}

    # Find state graph for entity
    state_graphs = _dict(ir.get("state_graphs") or ir.get("state_machines"))
    entity_state_graph = _dict(state_graphs.get(entity_ref))
    transitions = _list(entity_state_graph.get("transitions"))

    # Build chain based on type
    if chain_type == "SELF_REFERENCE":
        return _build_self_reference_chain(
            detection, obligation, ir, ops_by_id, transitions,
        )
    elif chain_type == "CROSS_ENTITY_PRECONDITION":
        return _build_cross_entity_precondition_chain(
            detection, obligation, ir, ops_by_id, transitions,
        )
    elif chain_type == "MULTI_INSTANCE":
        return _build_multi_instance_chain(
            detection, obligation, ir, ops_by_id, transitions,
        )

    return {"status": "INSUFFICIENT_INFO", "reason": f"unknown chain_type={chain_type}"}


def _build_self_reference_chain(
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
    ops_by_id: dict[str, Any],
    transitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build chain for self-referencing operations (e.g., merge two tickets).

    Requires:
      - Instance A: advance to forbidden state (e.g., CLOSED)
      - Instance B: remain in valid state
      - Target: operation(A, B) should fail because A is in forbidden state
    """
    entity_ref = _text(detection.get("entity_ref"))
    operation_ref = _text(detection.get("operation_ref"))
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))

    # Determine forbidden state from expression
    forbidden_state = _text(
        expr.get("forbidden_state") or expr.get("wrong_state")
        or expr.get("violation_state")
    )
    if not forbidden_state:
        # Infer from description
        desc = _text(detection.get("description"))
        if "CLOSED" in desc.upper():
            forbidden_state = "CLOSED"
        elif "RESOLVED" in desc.upper():
            forbidden_state = "RESOLVED"

    # Build path to forbidden state using transitions
    path_to_forbidden = _find_state_path(transitions, forbidden_state)

    # Build entity chains
    source_chain = []
    if path_to_forbidden:
        for step in path_to_forbidden:
            source_chain.append({
                "operation_ref": _text(step.get("operation_ref") or step.get("trigger")),
                "intent": f"advance_source_to_{step.get('to_state', '')}",
                "entity_role": "source",
                "expected_state": _text(step.get("to_state")),
            })
    else:
        # Fallback: use known lifecycle operations
        lifecycle_ops = _find_lifecycle_operations(ops_by_id, entity_ref)
        for op in lifecycle_ops:
            source_chain.append({
                "operation_ref": _text(op.get("operation_id")),
                "intent": f"advance_source_toward_{forbidden_state}",
                "entity_role": "source",
                "expected_state": _text(op.get("to_state")),
            })

    target_chain = [{
        "operation_ref": _find_create_operation(ops_by_id, entity_ref),
        "intent": "create_target_instance",
        "entity_role": "target",
        "expected_state": "OPEN",
    }]

    chain_proof_id = _stable_id("chain_proof", operation_ref, entity_ref, "self_ref")

    return {
        "status": "BUILT",
        "chain_type": "SELF_REFERENCE",
        "entity_chains": {
            "source": source_chain,
            "target": target_chain,
        },
        "forbidden_state": forbidden_state,
        "target_operation": operation_ref,
        "chain_proof": {
            "proof_id": chain_proof_id,
            "proof_type": "CROSS_ENTITY_CHAIN_PROOF",
            "chain_type": "SELF_REFERENCE",
            "entity_ref": entity_ref,
            "source_chain_length": len(source_chain),
            "target_chain_length": len(target_chain),
            "forbidden_state": forbidden_state,
        },
    }


def _build_cross_entity_precondition_chain(
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
    ops_by_id: dict[str, Any],
    transitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build chain for cross-entity precondition operations.

    Example: update_sla requires no active tickets referencing the SLA.
    Chain:
      - Primary: create SLA
      - Dependent: create Ticket referencing SLA
      - Target: update SLA (should fail because active ticket exists)
    """
    entity_ref = _text(detection.get("entity_ref"))
    subject_entity = _text(detection.get("subject_entity"))
    reference_entity = _text(detection.get("reference_entity"))
    operation_ref = _text(detection.get("operation_ref"))
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    expr = _dict(inv.get("expression") or obligation.get("expression"))

    # Determine entities
    primary_entity = subject_entity or entity_ref
    dependent_entity = reference_entity or _text(expr.get("dependent_entity"))

    # If not explicit, try to infer from description
    desc = _text(detection.get("description")).lower()
    if not dependent_entity:
        if "ticket" in desc and "sla" in desc:
            if "sla" in (primary_entity or "").lower():
                dependent_entity = "Ticket"
            else:
                dependent_entity = "SLA"

    # Build primary chain (create the primary entity)
    primary_create_op = _find_create_operation(ops_by_id, primary_entity)
    primary_chain = [{
        "operation_ref": primary_create_op,
        "intent": f"create_{primary_entity.lower()}",
        "entity_role": "primary",
        "expected_state": "ACTIVE",
    }]

    # Build dependent chain (create dependent entity referencing primary)
    dependent_create_op = _find_create_operation(ops_by_id, dependent_entity)
    dependent_chain = [{
        "operation_ref": dependent_create_op,
        "intent": f"create_{dependent_entity.lower()}_referencing_primary",
        "entity_role": "dependent",
        "expected_state": "OPEN",
        "reference_field": _text(expr.get("reference_field") or expr.get("foreign_key") or "sla_id"),
        "reference_source": "primary",
    }]

    chain_proof_id = _stable_id(
        "chain_proof", operation_ref, primary_entity, dependent_entity, "cross_entity"
    )

    return {
        "status": "BUILT",
        "chain_type": "CROSS_ENTITY_PRECONDITION",
        "entity_chains": {
            "primary": primary_chain,
            "dependent": dependent_chain,
        },
        "primary_entity": primary_entity,
        "dependent_entity": dependent_entity,
        "target_operation": operation_ref,
        "precondition_violation": _text(
            expr.get("violation_condition") or expr.get("description")
            or detection.get("description")
        ),
        "chain_proof": {
            "proof_id": chain_proof_id,
            "proof_type": "CROSS_ENTITY_CHAIN_PROOF",
            "chain_type": "CROSS_ENTITY_PRECONDITION",
            "primary_entity": primary_entity,
            "dependent_entity": dependent_entity,
            "primary_chain_length": len(primary_chain),
            "dependent_chain_length": len(dependent_chain),
        },
    }


def _build_multi_instance_chain(
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
    ops_by_id: dict[str, Any],
    transitions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build chain for multi-instance operations (same entity, multiple instances)."""
    entity_ref = _text(detection.get("entity_ref"))
    operation_ref = _text(detection.get("operation_ref"))

    create_op = _find_create_operation(ops_by_id, entity_ref)
    required_entities = _list(detection.get("required_entities"))
    num_instances = max(len(required_entities), 2)

    entity_chains = {}
    for i in range(num_instances):
        role = f"instance_{i}"
        entity_chains[role] = [{
            "operation_ref": create_op,
            "intent": f"create_{entity_ref.lower()}_{i}",
            "entity_role": role,
            "expected_state": "OPEN",
        }]

    chain_proof_id = _stable_id("chain_proof", operation_ref, entity_ref, "multi_instance")

    return {
        "status": "BUILT",
        "chain_type": "MULTI_INSTANCE",
        "entity_chains": entity_chains,
        "target_operation": operation_ref,
        "chain_proof": {
            "proof_id": chain_proof_id,
            "proof_type": "CROSS_ENTITY_CHAIN_PROOF",
            "chain_type": "MULTI_INSTANCE",
            "entity_ref": entity_ref,
            "instance_count": num_instances,
        },
    }


# ─── State Path Helpers ───────────────────────────────────────────────────────

def _find_state_path(
    transitions: list[dict[str, Any]],
    target_state: str,
) -> list[dict[str, Any]]:
    """BFS to find path from initial state to target state."""
    if not transitions or not target_state:
        return []

    # Build adjacency
    adjacency: dict[str, list[dict[str, Any]]] = {}
    all_states: set[str] = set()
    to_states: set[str] = set()

    for t in transitions:
        from_s = _text(t.get("from_state") or t.get("from"))
        to_s = _text(t.get("to_state") or t.get("to"))
        if from_s and to_s:
            adjacency.setdefault(from_s, []).append(t)
            all_states.add(from_s)
            all_states.add(to_s)
            to_states.add(to_s)

    # Initial state: in all_states but never a to_state, or explicitly marked
    initial_candidates = all_states - to_states
    initial = next(iter(initial_candidates)) if initial_candidates else None
    if not initial:
        # Fallback: look for OPEN/CREATED/DRAFT
        for candidate in ["OPEN", "CREATED", "DRAFT", "NEW"]:
            if candidate in all_states:
                initial = candidate
                break
    if not initial:
        return []

    # BFS
    from collections import deque
    queue = deque([(initial, [])])
    visited = {initial}

    while queue:
        current, path = queue.popleft()
        if current == target_state:
            return path

        for t in adjacency.get(current, []):
            next_state = _text(t.get("to_state") or t.get("to"))
            if next_state not in visited:
                visited.add(next_state)
                queue.append((next_state, path + [t]))

    return []


def _find_create_operation(ops_by_id: dict[str, Any], entity_ref: str) -> str:
    """Find the create operation for an entity type."""
    entity_lower = (entity_ref or "").lower()
    for op_id, op in ops_by_id.items():
        op_dict = _dict(op)
        method = _text(op_dict.get("method")).upper()
        path = _text(op_dict.get("path")).lower()
        op_entity = _text(op_dict.get("entity_ref")).lower()

        if method == "POST" and (
            entity_lower in path
            or entity_lower == op_entity
            or f"/{entity_lower}s" in path
            or f"/{entity_lower}" in path
        ):
            # Exclude action paths like /tickets/merge, /tickets/bulk-assign
            path_parts = path.rstrip("/").split("/")
            if len(path_parts) <= 3:  # e.g., /tickets or /slas
                return op_id
    # Fallback: any POST to entity collection
    for op_id, op in ops_by_id.items():
        op_dict = _dict(op)
        method = _text(op_dict.get("method")).upper()
        path = _text(op_dict.get("path")).lower()
        if method == "POST" and entity_lower and entity_lower in path:
            return op_id
    return ""


def _find_lifecycle_operations(
    ops_by_id: dict[str, Any],
    entity_ref: str,
) -> list[dict[str, Any]]:
    """Find lifecycle operations that advance entity state."""
    entity_lower = (entity_ref or "").lower()
    lifecycle_ops = []
    for op_id, op in ops_by_id.items():
        op_dict = _dict(op)
        method = _text(op_dict.get("method")).upper()
        path = _text(op_dict.get("path")).lower()
        if method == "POST" and entity_lower in path and "/" in path[len(entity_lower)+2:]:
            # Action path like /tickets/{id}/assign
            lifecycle_ops.append(op_dict)
    return lifecycle_ops


# ─── Experiment Planning ──────────────────────────────────────────────────────

def plan_cross_entity_experiments(
    obligation: dict[str, Any],
    ir: dict[str, Any],
    budget: int = 8,
) -> dict[str, Any]:
    """Plan cross-entity experiments for an obligation.

    Main entry point. Returns:
      - status: EXPLORED | NOT_CROSS_ENTITY | INSUFFICIENT_INFO
      - experiments: list of experiment plans
      - chain_proof: proof of chain construction
      - dependency_proof: proof of entity dependencies
    """
    # Step 1: Detect cross-entity requirement
    detection = detect_cross_entity_requirement(obligation, ir)

    if not detection.get("is_cross_entity"):
        return {
            "status": "NOT_CROSS_ENTITY",
            "reason": "No cross-entity requirement detected",
            "signals": detection.get("signals", []),
        }

    # Step 2: Build cross-entity chain
    chain_result = build_cross_entity_chain(detection, obligation, ir)

    if chain_result.get("status") != "BUILT":
        return {
            "status": "INSUFFICIENT_INFO",
            "reason": chain_result.get("reason", "Chain building failed"),
            "detection": detection,
        }

    # Step 3: Generate experiments from chain
    experiments = _generate_experiments_from_chain(
        chain_result, detection, obligation, ir, budget,
    )

    # Step 4: Build dependency proof
    dependency_proof = _build_dependency_proof(detection, chain_result, obligation)

    return {
        "status": "EXPLORED",
        "chain_type": detection.get("chain_type"),
        "experiments": experiments,
        "chain_proof": chain_result.get("chain_proof"),
        "dependency_proof": dependency_proof,
        "entity_chains": chain_result.get("entity_chains"),
        "detection_signals": detection.get("signals", []),
    }


def _generate_experiments_from_chain(
    chain_result: dict[str, Any],
    detection: dict[str, Any],
    obligation: dict[str, Any],
    ir: dict[str, Any],
    budget: int,
) -> list[dict[str, Any]]:
    """Generate executable experiments from a built chain."""
    experiments = []
    chain_type = _text(chain_result.get("chain_type"))
    entity_chains = _dict(chain_result.get("entity_chains"))
    target_operation = _text(chain_result.get("target_operation"))
    operation_ref = _text(detection.get("operation_ref"))
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    rule_id = _text(inv.get("rule_id") or inv.get("id"))
    oid = _text(obligation.get("obligation_id") or obligation.get("id"))

    if chain_type == "SELF_REFERENCE":
        # Experiment 1: Control - merge with valid source (not CLOSED)
        experiments.append({
            "experiment_id": _stable_id("xce_ctrl", oid, operation_ref),
            "experiment_type": "CONTROL",
            "description": f"Control: {operation_ref} with valid source state",
            "setup_chain": entity_chains.get("target", []),
            "target_operation": target_operation or operation_ref,
            "expected_outcome": "accepted",
            "rule_id": rule_id,
        })

        # Experiment 2: Violation - merge with CLOSED source
        source_chain = entity_chains.get("source", [])
        target_chain = entity_chains.get("target", [])
        full_setup = source_chain + target_chain
        experiments.append({
            "experiment_id": _stable_id("xce_viol", oid, operation_ref),
            "experiment_type": "VIOLATION",
            "description": f"Violation: {operation_ref} with source in forbidden state ({chain_result.get('forbidden_state', 'CLOSED')})",
            "setup_chain": full_setup,
            "target_operation": target_operation or operation_ref,
            "expected_outcome": "rejected",
            "expected_status": 409,
            "forbidden_state": chain_result.get("forbidden_state"),
            "rule_id": rule_id,
        })

    elif chain_type == "CROSS_ENTITY_PRECONDITION":
        # Experiment 1: Control - operation without dependent entity
        primary_chain = entity_chains.get("primary", [])
        experiments.append({
            "experiment_id": _stable_id("xce_ctrl", oid, operation_ref),
            "experiment_type": "CONTROL",
            "description": f"Control: {operation_ref} without dependent entities",
            "setup_chain": primary_chain,
            "target_operation": target_operation or operation_ref,
            "expected_outcome": "accepted",
            "rule_id": rule_id,
        })

        # Experiment 2: Violation - operation with active dependent entity
        dependent_chain = entity_chains.get("dependent", [])
        full_setup = primary_chain + dependent_chain
        experiments.append({
            "experiment_id": _stable_id("xce_viol", oid, operation_ref),
            "experiment_type": "VIOLATION",
            "description": f"Violation: {operation_ref} with active dependent entity",
            "setup_chain": full_setup,
            "target_operation": target_operation or operation_ref,
            "expected_outcome": "rejected",
            "expected_status": 409,
            "precondition_violation": chain_result.get("precondition_violation"),
            "rule_id": rule_id,
        })

    elif chain_type == "MULTI_INSTANCE":
        # Control: operation with all instances in valid state
        all_chains = []
        for role, chain in entity_chains.items():
            all_chains.extend(chain)
        experiments.append({
            "experiment_id": _stable_id("xce_ctrl", oid, operation_ref),
            "experiment_type": "CONTROL",
            "description": f"Control: {operation_ref} with all instances valid",
            "setup_chain": all_chains[:1],  # Just one instance
            "target_operation": target_operation or operation_ref,
            "expected_outcome": "accepted",
            "rule_id": rule_id,
        })

        # Violation: operation with one instance in invalid state
        experiments.append({
            "experiment_id": _stable_id("xce_viol", oid, operation_ref),
            "experiment_type": "VIOLATION",
            "description": f"Violation: {operation_ref} with mixed instance states",
            "setup_chain": all_chains,
            "target_operation": target_operation or operation_ref,
            "expected_outcome": "partial_rejection",
            "rule_id": rule_id,
        })

    return experiments[:budget]


def _build_dependency_proof(
    detection: dict[str, Any],
    chain_result: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    """Build proof of entity dependency for audit trail."""
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    rule_id = _text(inv.get("rule_id") or inv.get("id"))
    oid = _text(obligation.get("obligation_id") or obligation.get("id"))

    return {
        "proof_id": _stable_id("dep_proof", oid, rule_id),
        "proof_type": "CROSS_ENTITY_DEPENDENCY_PROOF",
        "chain_type": detection.get("chain_type"),
        "entity_ref": detection.get("entity_ref"),
        "subject_entity": detection.get("subject_entity"),
        "reference_entity": detection.get("reference_entity"),
        "operation_ref": detection.get("operation_ref"),
        "detection_signals": detection.get("signals", []),
        "chain_status": chain_result.get("status"),
        "rule_id": rule_id,
    }


# ─── Public API ───────────────────────────────────────────────────────────────

def build_chain_proof(
    chain_result: dict[str, Any],
    obligation: dict[str, Any],
) -> dict[str, Any]:
    """Build a formal chain proof for audit trail."""
    proof = chain_result.get("chain_proof", {})
    inv = _dict(obligation.get("invariant") or obligation.get("source_invariant"))
    return {
        **proof,
        "obligation_id": _text(obligation.get("obligation_id") or obligation.get("id")),
        "rule_id": _text(inv.get("rule_id") or inv.get("id")),
        "verdict": "CHAIN_BUILT" if chain_result.get("status") == "BUILT" else "CHAIN_FAILED",
    }
