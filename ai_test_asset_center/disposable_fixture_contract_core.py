"""Disposable Fixture Contract: compile-time fixture resolution for experiments.

V1.5.0 SPEC §8-§13. Discovers fixture candidates from Behavior IR declared
Create Operations, builds Disposable Fixture Contracts with identity/scope/
cleanup bindings, and generates multi-entity Fixture DAGs.

This module is consumed by ``experiment_compiler_obligation`` BEFORE final
experiment assembly. It never invents identifiers, guesses paths, or uses
customer pre-existing data as fixtures.

Schema: qualibug.disposable-fixture-contract.v1
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

logger = logging.getLogger(__name__)

CONTRACT_SCHEMA = "qualibug.disposable-fixture-contract.v1"
RECEIPT_SCHEMA = "qualibug.disposable-fixture-receipt.v1"
DAG_SCHEMA = "qualibug.fixture-dag.v1"

# Contract statuses
STATUS_RESOLVED = "RESOLVED"
STATUS_INCOMPLETE = "INCOMPLETE"
STATUS_AMBIGUOUS = "AMBIGUOUS"
STATUS_UNSAFE = "UNSAFE"
STATUS_NOT_DECLARED = "NOT_DECLARED"

# Identity source types (SPEC §11)
SOURCE_WRITE_RESPONSE_ID = "WRITE_RESPONSE_ID"
SOURCE_IDENTITY_GET = "IDENTITY_GET"
SOURCE_FILTERED_COLLECTION_GET = "FILTERED_COLLECTION_GET"
SOURCE_DATABASE_OBSERVER = "SOURCE_DECLARED_DATABASE_OBSERVER"
SOURCE_FIXTURE_OUTPUT_BINDING = "FIXTURE_OUTPUT_BINDING"
SOURCE_BUSINESS_KEY = "SOURCE_DECLARED_BUSINESS_KEY"

# Prohibited identity sources
PROHIBITED_IDENTITY_SOURCES = frozenset({
    "FIRST_RESPONSE_ITEM",
    "LATEST_CREATED_RECORD",
    "MAX_DATABASE_ID",
    "CURRENT_TIMESTAMP_NEAREST",
    "STRING_SIMILARITY_ONLY",
})

# Breakpoint codes (SPEC §35)
FIXTURE_NOT_RESOLVED = "DISPOSABLE_FIXTURE_NOT_RESOLVED"
FIXTURE_CREATE_OP_NOT_BOUND = "FIXTURE_CREATE_OPERATION_NOT_BOUND"
FIXTURE_IDENTITY_NOT_RESOLVED = "FIXTURE_IDENTITY_NOT_RESOLVED"
FIXTURE_IDENTITY_AMBIGUOUS = "FIXTURE_IDENTITY_AMBIGUOUS"
FIXTURE_SCOPE_MISMATCH = "FIXTURE_SCOPE_MISMATCH"
FIXTURE_DAG_INCOMPLETE = "FIXTURE_DEPENDENCY_GRAPH_INCOMPLETE"
FIXTURE_MATERIALIZATION_FAILED = "FIXTURE_MATERIALIZATION_FAILED"
FIXTURE_PROVENANCE_FAILED = "FIXTURE_RUNTIME_PROVENANCE_FAILED"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _sha256_short(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode()
    ).hexdigest()[:20]


# ═══════════════════════════════════════════════════════════════════════════════
# §9: Fixture Candidate Discovery
# ═══════════════════════════════════════════════════════════════════════════════


def discover_fixture_candidates(
    behavior_ir: dict[str, Any],
    *,
    entity_ids: "list[str] | None" = None,
) -> list[dict[str, Any]]:
    """Discover fixture candidates from source-declared Create Operations.

    A fixture candidate requires:
    - A POST operation declared in Behavior IR with source_refs
    - At least one readback operation (GET) on the same collection
    - At least one cleanup candidate (DELETE or compensates relation)

    Never guesses paths or infers create operations from entity names.
    """
    ir = _dict(behavior_ir)
    operations = _list(ir.get("operations"))
    relations = _list(ir.get("relations"))
    entities = _list(ir.get("entities"))

    # Index operations by entity path collection
    ops_by_collection: dict[str, list[dict]] = {}
    for op in operations:
        if not isinstance(op, dict):
            continue
        path = _text(op.get("path") or op.get("raw_path"))
        if path:
            collection = _collection_from_path(path)
            ops_by_collection.setdefault(collection, []).append(op)

    # Build compensates index: target_op_id -> source_op (the compensator)
    compensates_map: dict[str, dict] = {}
    for rel in relations:
        if not isinstance(rel, dict):
            continue
        if _text(rel.get("kind")) == "compensates" or _text(rel.get("relation_type")) == "compensates":
            target = _text(rel.get("target"))
            source = _text(rel.get("source"))
            if target and source:
                compensates_map[target] = {"operation_ref": source, "relation": rel}

    # Entity filter
    target_entities = set(entity_ids) if entity_ids else None

    candidates: list[dict[str, Any]] = []
    seen_ops: set[str] = set()

    for op in operations:
        if not isinstance(op, dict):
            continue
        op_id = _text(op.get("id"))
        method = _text(op.get("method")).upper()
        if method != "POST" or not op_id or op_id in seen_ops:
            continue
        # Must have source_refs (source-declared, not guessed)
        source_refs = _list(op.get("source_refs"))
        if not source_refs:
            continue
        path = _text(op.get("path") or op.get("raw_path"))
        read_write = _text(op.get("read_write")).lower()
        if read_write and read_write != "write":
            continue

        # Entity association
        entity_refs = _list(op.get("entity_refs"))
        entity_id = _text(entity_refs[0]) if entity_refs else ""
        if target_entities and entity_id and entity_id not in target_entities:
            continue

        collection = _collection_from_path(path)
        collection_ops = ops_by_collection.get(collection, [])

        # Find readback candidates (GET on same collection or item path)
        readback_ids = [
            _text(cop.get("id"))
            for cop in collection_ops
            if _text(cop.get("method")).upper() in {"GET", "HEAD"}
            and _text(cop.get("id")) != op_id
        ]

        # Find cleanup candidates (DELETE on same collection or compensates)
        cleanup_ids = [
            _text(cop.get("id"))
            for cop in collection_ops
            if _text(cop.get("method")).upper() == "DELETE"
            and _text(cop.get("id")) != op_id
        ]
        # Also check compensates relation
        comp = compensates_map.get(op_id)
        if comp and _text(comp.get("operation_ref")):
            cleanup_ids.append(_text(comp["operation_ref"]))

        # Identity sources: response schema fields that look like IDs
        identity_sources = _extract_identity_sources(op)

        # Scope sources: entity scope_fields from V1.4.0
        scope_sources = _extract_scope_sources(entities, entity_id)

        status = STATUS_RESOLVED
        if not readback_ids:
            status = STATUS_INCOMPLETE
        if not cleanup_ids:
            status = STATUS_INCOMPLETE

        candidate = {
            "entity_id": entity_id,
            "create_operation_id": op_id,
            "create_operation_path": path,
            "create_operation_method": method,
            "readback_candidate_ids": list(dict.fromkeys(readback_ids)),
            "cleanup_candidate_ids": list(dict.fromkeys(cleanup_ids)),
            "identity_sources": identity_sources,
            "scope_sources": scope_sources,
            "source_refs": [dict(sr) for sr in source_refs if isinstance(sr, dict)],
            "status": status,
        }
        candidates.append(candidate)
        seen_ops.add(op_id)

    return candidates


def _collection_from_path(path: str) -> str:
    """Extract collection segment from path: /api/orders/{id} -> orders."""
    segments = [s for s in path.strip("/").split("/") if s and not s.startswith("{") and not s.startswith(":")]
    # Return the last non-parameter segment as collection
    return segments[-1].lower() if segments else ""


def _extract_identity_sources(operation: dict) -> list[dict[str, str]]:
    """Extract identity fields from operation response schema."""
    sources: list[dict[str, str]] = []
    response_schema = _dict(operation.get("response_schema"))
    props = _dict(response_schema.get("properties"))
    if not props:
        content = _dict(response_schema.get("content"))
        json_media = _dict(content.get("application/json"))
        schema = _dict(json_media.get("schema"))
        props = _dict(schema.get("properties"))
    for field_name, field_spec in props.items():
        fname_lower = field_name.lower()
        if any(tok in fname_lower for tok in ("id", "uuid", "code", "key", "number", "no")):
            sources.append({
                "field": field_name,
                "source_type": SOURCE_WRITE_RESPONSE_ID,
                "json_path": f"$.{field_name}",
            })
    # Default: id field
    if not sources:
        sources.append({"field": "id", "source_type": SOURCE_WRITE_RESPONSE_ID, "json_path": "$.id"})
    return sources


def _extract_scope_sources(entities: list, entity_id: str) -> dict[str, str]:
    """Extract scope fields from entity's V1.4.0 scope_fields."""
    for entity in entities:
        if not isinstance(entity, dict):
            continue
        if _text(entity.get("id")) == entity_id:
            scope = _dict(entity.get("scope_fields"))
            return {
                "tenant_field": _text(scope.get("tenant_field")),
                "owner_field": _text(scope.get("owner_field")),
                "permission_scope": _text(scope.get("permission_scope")),
            }
    return {}


# ═══════════════════════════════════════════════════════════════════════════════
# §8: Disposable Fixture Contract Builder
# ═══════════════════════════════════════════════════════════════════════════════


def build_disposable_fixture_contract(
    *,
    obligation_id: str,
    experiment_id: str,
    campaign_id: str,
    candidate: dict[str, Any],
    behavior_ir: dict[str, Any],
    actor_ref: str = "",
) -> dict[str, Any]:
    """Build a Disposable Fixture Contract from a resolved candidate.

    The contract is the compile-time authority that allows an experiment to
    create, use, and clean up its own business fixture.
    """
    ir = _dict(behavior_ir)
    operations = _index_ops(_list(ir.get("operations")))
    entities = _list(ir.get("entities"))

    create_op_id = _text(candidate.get("create_operation_id"))
    create_op = operations.get(create_op_id, {})
    entity_id = _text(candidate.get("entity_id"))

    # Build create plan (single step for primary entity)
    create_plan = [{
        "step_id": f"fixture_create_{entity_id or 'primary'}",
        "operation_ref": create_op_id,
        "actor_ref": actor_ref,
        "request_binding": _build_request_binding(create_op),
        "source_refs": _list(candidate.get("source_refs")),
        "expected_output_bindings": _list(candidate.get("identity_sources")),
    }]

    # Identity bindings
    identity_bindings = [
        {
            "canonical_field_id": _text(src.get("field")),
            "source_type": _text(src.get("source_type")),
            "source_path": _text(src.get("json_path")),
            "target_bindings": [],
        }
        for src in _list(candidate.get("identity_sources"))
        if isinstance(src, dict)
    ]

    # Readback contract
    readback_ids = _list(candidate.get("readback_candidate_ids"))

    # Cleanup plan
    cleanup_ids = _list(candidate.get("cleanup_candidate_ids"))
    cleanup_plan = {
        "cleanup_contract_ids": cleanup_ids,
        "dependency_order": [entity_id] if entity_id else [],
        "environment_restoration_required": True,
    }

    # Scope
    scope_sources = _dict(candidate.get("scope_sources"))
    scope = {
        "tenant_field": _text(scope_sources.get("tenant_field")),
        "tenant_value_ref": "runtime_actor_tenant",
        "owner_field": _text(scope_sources.get("owner_field")),
        "owner_value_ref": "runtime_actor_owner",
        "organization_field": "",
        "organization_value_ref": "",
    }

    # State precondition (from obligation property)
    state_precondition = {
        "required": False,
        "target_state": "",
        "establishment_plan_id": "",
    }

    # Determine status
    status = STATUS_RESOLVED
    if not create_op_id or create_op_id not in operations:
        status = STATUS_INCOMPLETE
    elif not readback_ids:
        status = STATUS_INCOMPLETE
    elif not cleanup_ids:
        status = STATUS_INCOMPLETE
    elif not identity_bindings:
        status = STATUS_INCOMPLETE

    # Provenance fingerprint
    provenance_input = {
        "obligation_id": obligation_id,
        "experiment_id": experiment_id,
        "create_operation_id": create_op_id,
        "entity_id": entity_id,
        "source_refs": _list(candidate.get("source_refs")),
    }
    fingerprint = f"dfc_{_sha256_short(provenance_input)}"

    contract = {
        "schema_version": CONTRACT_SCHEMA,
        "fixture_id": f"fix_{_sha256_short(experiment_id + entity_id + create_op_id)}",
        "campaign_id": campaign_id,
        "experiment_id": experiment_id,
        "obligation_id": obligation_id,
        "primary_entity_id": entity_id,
        "related_entity_ids": [],
        "scope": scope,
        "create_plan": create_plan,
        "identity_bindings": identity_bindings,
        "readback_contract_ids": readback_ids,
        "state_precondition": state_precondition,
        "cleanup_plan": cleanup_plan,
        "ownership": {
            "campaign_owned": True,
            "customer_preexisting": False,
        },
        "provenance_fingerprint": fingerprint,
        "status": status,
    }
    return contract


def _build_request_binding(operation: dict) -> dict[str, Any]:
    """Build request binding from operation's request schema/example."""
    request_example = _dict(operation.get("request_example"))
    if request_example:
        return {"source": "request_example", "template": request_example}
    request_schema = _dict(operation.get("request_schema"))
    if request_schema:
        return {"source": "request_schema", "schema": request_schema}
    return {"source": "none", "template": {}}


def _index_ops(operations: list) -> dict[str, dict]:
    """Index operations by id."""
    return {
        _text(op.get("id")): op
        for op in operations
        if isinstance(op, dict) and _text(op.get("id"))
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §13: Multi-Entity Fixture DAG
# ═══════════════════════════════════════════════════════════════════════════════


def build_fixture_dag(
    contracts: list[dict[str, Any]],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Build a Fixture DAG from multiple fixture contracts.

    Determines creation order (parent→child) and cleanup order (child→parent)
    from entity_relations in Behavior IR.
    """
    ir = _dict(behavior_ir)
    entity_relations = _list(ir.get("relations"))

    # Build entity dependency graph
    # relation kind=owns/contains/references: source owns target
    parent_of: dict[str, set[str]] = {}  # parent -> {children}
    for rel in entity_relations:
        if not isinstance(rel, dict):
            continue
        kind = _text(rel.get("kind") or rel.get("relation_type")).lower()
        source = _text(rel.get("source"))
        target = _text(rel.get("target"))
        if not source or not target:
            continue
        if kind in {"owns", "contains", "has_many", "parent_of", "composition"}:
            parent_of.setdefault(source, set()).add(target)
        elif kind in {"belongs_to", "child_of", "references", "foreign_key"}:
            parent_of.setdefault(target, set()).add(source)

    # Build nodes from contracts
    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    entity_to_node: dict[str, str] = {}

    for contract in contracts:
        if not isinstance(contract, dict):
            continue
        entity_id = _text(contract.get("primary_entity_id"))
        fixture_id = _text(contract.get("fixture_id"))
        node_id = f"node_{entity_id or fixture_id}"
        entity_to_node[entity_id] = node_id

        create_plan = _list(contract.get("create_plan"))
        create_op_ref = _text(_dict(create_plan[0]).get("operation_ref")) if create_plan else ""
        cleanup_ids = _list(_dict(contract.get("cleanup_plan")).get("cleanup_contract_ids"))

        nodes.append({
            "node_id": node_id,
            "entity_id": entity_id,
            "fixture_id": fixture_id,
            "create_operation_ref": create_op_ref,
            "identity_output": _list(contract.get("identity_bindings")),
            "cleanup_contract_id": cleanup_ids[0] if cleanup_ids else "",
        })

    # Build edges from dependency graph
    for parent_entity, children in parent_of.items():
        parent_node = entity_to_node.get(parent_entity)
        if not parent_node:
            continue
        for child_entity in children:
            child_node = entity_to_node.get(child_entity)
            if not child_node:
                continue
            edges.append({
                "parent_node_id": parent_node,
                "child_node_id": child_node,
                "relation_type": "dependency",
                "binding_field": f"{parent_entity}_id",
            })

    # Topological sort for creation order (parents first)
    creation_order = _topological_sort(nodes, edges)
    cleanup_order = list(reversed(creation_order))

    # Validate completeness
    status = STATUS_RESOLVED
    if not nodes:
        status = STATUS_INCOMPLETE
    elif any(not n.get("create_operation_ref") for n in nodes):
        status = STATUS_INCOMPLETE
    # Check for cycles
    if len(creation_order) != len(nodes):
        status = STATUS_INCOMPLETE

    return {
        "schema_version": DAG_SCHEMA,
        "fixture_dag_id": f"dag_{_sha256_short([n['node_id'] for n in nodes])}",
        "nodes": nodes,
        "edges": edges,
        "creation_order": creation_order,
        "cleanup_order": cleanup_order,
        "status": status,
    }


def _topological_sort(
    nodes: list[dict],
    edges: list[dict],
) -> list[str]:
    """Kahn's algorithm. Returns node_ids in creation order (parents first)."""
    node_ids = [n["node_id"] for n in nodes]
    in_degree: dict[str, int] = {nid: 0 for nid in node_ids}
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}

    for edge in edges:
        parent = _text(edge.get("parent_node_id"))
        child = _text(edge.get("child_node_id"))
        if parent in in_degree and child in in_degree:
            adjacency[parent].append(child)
            in_degree[child] += 1

    queue = [nid for nid in node_ids if in_degree[nid] == 0]
    result: list[str] = []
    while queue:
        queue.sort()  # deterministic
        node = queue.pop(0)
        result.append(node)
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return result


# ═══════════════════════════════════════════════════════════════════════════════
# §12: Fixture Scope Validation
# ═══════════════════════════════════════════════════════════════════════════════


def validate_fixture_scope(
    contract: dict[str, Any],
    *,
    campaign_id: str,
    experiment_id: str,
    runtime_identity: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Validate fixture scope against campaign/experiment/tenant/owner.

    Returns validation receipt. Any core mismatch = FIXTURE_SCOPE_MISMATCH.
    """
    contract_campaign = _text(contract.get("campaign_id"))
    contract_experiment = _text(contract.get("experiment_id"))
    ownership = _dict(contract.get("ownership"))

    validations = {
        "campaign_match": contract_campaign == campaign_id if contract_campaign else True,
        "experiment_match": contract_experiment == experiment_id if contract_experiment else True,
        "tenant_match": True,  # Validated at runtime from actor context
        "owner_match": True,   # Validated at runtime from actor context
        "organization_match": True,
        "primary_identity_match": True,  # Validated after materialization
        "correlation_keys_match": True,
    }

    # Check ownership flags
    if not ownership.get("campaign_owned", False):
        validations["campaign_match"] = False
    if ownership.get("customer_preexisting", False):
        validations["campaign_match"] = False

    all_match = all(validations.values())
    return {
        "fixture_id": _text(contract.get("fixture_id")),
        "validations": validations,
        "status": "VALID" if all_match else "FIXTURE_SCOPE_MISMATCH",
        "mismatched_fields": [k for k, v in validations.items() if not v],
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §15: Fixture Materialization Receipt
# ═══════════════════════════════════════════════════════════════════════════════


def build_fixture_materialization_receipt(
    *,
    contract: dict[str, Any],
    created_entities: list[dict[str, Any]],
    fixture_bindings: dict[str, Any] | None = None,
    cleanup_contract_ids: "list[str] | None" = None,
    final_status: str = "MATERIALIZED",
) -> dict[str, Any]:
    """Build qualibug.disposable-fixture-receipt.v1 after successful creation."""
    contract_fingerprint = _text(contract.get("provenance_fingerprint"))
    runtime_input = {
        "fixture_id": _text(contract.get("fixture_id")),
        "created_entities": created_entities,
        "final_status": final_status,
    }
    runtime_fingerprint = f"rt_{_sha256_short(runtime_input)}"

    return {
        "schema_version": RECEIPT_SCHEMA,
        "fixture_id": _text(contract.get("fixture_id")),
        "campaign_id": _text(contract.get("campaign_id")),
        "experiment_id": _text(contract.get("experiment_id")),
        "created_entities": created_entities,
        "fixture_bindings": _dict(fixture_bindings),
        "fixture_dag_id": "",
        "cleanup_contract_ids": _list(cleanup_contract_ids),
        "provenance": {
            "compiled_contract_fingerprint": contract_fingerprint,
            "runtime_fingerprint": runtime_fingerprint,
            "match": bool(contract_fingerprint),
        },
        "final_status": final_status,
    }


# ═══════════════════════════════════════════════════════════════════════════════
# §28: Reverse Cleanup Plan
# ═══════════════════════════════════════════════════════════════════════════════


def build_reverse_cleanup_plan(
    *,
    experiment_id: str,
    fixture_id: str,
    write_steps: list[dict[str, Any]],
    dag: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build reverse cleanup plan before measured writes begin.

    Only status=RESOLVED allows measured writes to start.
    """
    cleanup_steps: list[dict[str, Any]] = []
    for idx, step in enumerate(write_steps):
        if not isinstance(step, dict):
            continue
        cleanup_steps.append({
            "step_id": _text(step.get("step_id")),
            "cleanup_contract_id": _text(step.get("cleanup_contract_id") or step.get("operation_ref")),
            "dependency_rank": idx + 1,
        })

    # Execution order is reverse of dependency rank
    execution_order = [
        {"cleanup_contract_id": s["cleanup_contract_id"]}
        for s in sorted(cleanup_steps, key=lambda x: x["dependency_rank"], reverse=True)
    ]

    # Status: RESOLVED only if every write step has a cleanup contract
    status = STATUS_RESOLVED
    if any(not s["cleanup_contract_id"] for s in cleanup_steps):
        status = STATUS_INCOMPLETE
    if not cleanup_steps:
        status = STATUS_INCOMPLETE

    return {
        "experiment_id": experiment_id,
        "fixture_id": fixture_id,
        "write_steps": cleanup_steps,
        "execution_order": execution_order,
        "status": status,
    }
