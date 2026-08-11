"""Canonical schema helpers for enterprise business understanding.

This layer models what the enterprise materials say. It does not create tests,
findings, probes, or industry assumptions. Every formal model entry must remain
traceable to an original source span or an existing source-backed knowledge asset.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from typing import Any, Iterable

MODEL_SCHEMA = "qualibug.enterprise-business-understanding-model.v1"
OBJECT_SCHEMA = "qualibug.enterprise-business-object.v1"
ACTOR_SCHEMA = "qualibug.enterprise-business-actor.v1"
OPERATION_SCHEMA = "qualibug.enterprise-business-operation.v1"
RELATION_SCHEMA = "qualibug.enterprise-business-object-relation.v1"
LIFECYCLE_SCHEMA = "qualibug.enterprise-business-lifecycle.v1"
PROCESS_SCHEMA = "qualibug.enterprise-business-process.v1"
BEHAVIOR_SCHEMA = "qualibug.enterprise-business-behavior.v1"
BEHAVIOR_ROW_LEDGER_SCHEMA = "qualibug.decision-matrix-row-ledger.v1"
BEHAVIOR_GATE_SCHEMA = "qualibug.enterprise-business-behavior-gate.v1"
IMPLEMENTATION_BINDING_SCHEMA = "qualibug.business-behavior-implementation-binding.v1"
IMPLEMENTATION_BINDING_GATE_SCHEMA = "qualibug.business-behavior-implementation-binding-gate.v1"
BINDING_IDENTITY_SCHEMA = "qualibug.implementation-binding-identity-graph.v1"
BINDING_IDENTITY_GATE_SCHEMA = "qualibug.implementation-binding-identity-gate.v1"
SCENARIO_IR_SCHEMA = "qualibug.enterprise-test-scenario-ir.v1"
SCENARIO_IR_GATE_SCHEMA = "qualibug.enterprise-test-scenario-ir-gate.v1"
SCENARIO_EXECUTION_CONTRACT_SCHEMA = "qualibug.scenario-execution-contract.v1"
SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA = "qualibug.scenario-execution-contract-gate.v1"
RUNTIME_PLAN_SCHEMA = "qualibug.runtime-plan.v1"
RUNTIME_PLAN_GATE_SCHEMA = "qualibug.runtime-plan-gate.v1"
RUNTIME_MATERIALIZATION_SCHEMA = "qualibug.runtime-materialization-contract.v1"
RUNTIME_MATERIALIZATION_GATE_SCHEMA = "qualibug.runtime-materialization-gate.v1"
UNKNOWN_SCHEMA = "qualibug.enterprise-business-unknown.v1"
GATE_SCHEMA = "qualibug.enterprise-understanding-model-gate.v1"


def text(value: Any) -> str:
    return str(value or "").strip()


def as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def clone_asset_for_understanding_projection(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Clone mutable cognition state while sharing finalized heavy evidence.

    Document Structure IR is finalized before cognition starts and remains
    read-only throughout the builder. A prior-pass understanding model is also
    read-only and replaced atomically by the caller. ``deepcopy``'s memo keeps
    those branches shared while retaining full isolation for mutable state.
    """
    shared = (
        asset.get("document_structure_assets"),
        asset.get("enterprise_understanding_model"),
    )
    memo = {id(value): value for value in shared if value is not None}
    return deepcopy(asset, memo)


def stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(
        json.dumps(part, ensure_ascii=False, sort_keys=True, default=str)
        if isinstance(part, (dict, list, tuple, set))
        else text(part)
        for part in parts
    )
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:20]}"


def unique_text(values: Iterable[Any]) -> list[str]:
    return sorted({text(value) for value in values if text(value)})


def source_evidence(
    *,
    source_id: Any = "",
    source_locator: Any = "",
    quote: Any = "",
    quote_hash: Any = "",
    fact_id: Any = "",
    asset_ref: Any = "",
    derivation: str = "source_span",
) -> dict[str, Any]:
    evidence = {
        "source_id": text(source_id),
        "source_locator": text(source_locator),
        "quote": text(quote),
        "quote_hash": text(quote_hash),
        "fact_id": text(fact_id),
        "asset_ref": text(asset_ref),
        "derivation": text(derivation) or "source_span",
    }
    return {key: value for key, value in evidence.items() if value}


def is_source_backed_evidence(row: dict[str, Any]) -> bool:
    """Return whether evidence satisfies the formal source-traceability contract."""
    evidence = as_dict(row)
    source_identity = text(evidence.get("source_id"))
    source_anchor = text(
        evidence.get("source_locator")
        or evidence.get("asset_ref")
        or evidence.get("document_block_id")
        or evidence.get("document_node_id")
    )
    exact_content = text(evidence.get("quote") or evidence.get("quote_hash"))
    return bool(source_identity and source_anchor and exact_content)


def evidence_from_fact(fact: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    attachment = as_dict(fact.get("structural_span_attachment"))
    for span in as_list(fact.get("source_spans")):
        if not isinstance(span, dict):
            continue
        row = source_evidence(
            source_id=span.get("source_id"),
            source_locator=span.get("locator") or span.get("source_locator"),
            quote=span.get("quote"),
            quote_hash=span.get("quote_hash"),
            fact_id=fact.get("fact_id"),
        )
        block_id = text(
            span.get("document_block_id")
            or attachment.get("document_block_id")
            or as_dict(fact.get("document_structure_alignment")).get("block_id")
        )
        if block_id:
            row["document_block_id"] = block_id
        if text(attachment.get("node_id")):
            row["document_node_id"] = text(attachment.get("node_id"))
        if text(attachment.get("section_node_id")):
            row["section_node_id"] = text(attachment.get("section_node_id"))
        if is_source_backed_evidence(row):
            rows.append(row)
    if not rows and text(fact.get("fact_id")):
        row = source_evidence(
            source_id=fact.get("source_id"),
            source_locator=fact.get("source_locator") or attachment.get("source_locator"),
            quote=fact.get("source_quote") or fact.get("quote"),
            quote_hash=fact.get("quote_hash"),
            fact_id=fact.get("fact_id"),
        )
        if text(attachment.get("document_block_id")):
            row["document_block_id"] = text(attachment.get("document_block_id"))
        if text(attachment.get("node_id")):
            row["document_node_id"] = text(attachment.get("node_id"))
        if is_source_backed_evidence(row):
            rows.append(row)
    return dedupe_evidence(rows)


def dedupe_evidence(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, ...]] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = {key: value for key, value in raw.items() if value not in (None, "", [], {})}
        key = (
            text(row.get("source_id")),
            text(row.get("source_locator")),
            text(row.get("quote_hash")),
            text(row.get("fact_id")),
            text(row.get("asset_ref")),
            text(row.get("derivation")),
        )
        if key in seen:
            continue
        seen.add(key)
        result.append(row)
    return result


def new_unknown(
    kind: str,
    question: str,
    *,
    related_objects: Iterable[Any] = (),
    related_operations: Iterable[Any] = (),
    evidence: Iterable[dict[str, Any]] = (),
    severity: str = "P1",
    blocks_formal_understanding: bool = False,
    reason_code: str = "",
    details: dict[str, Any] | None = None,
) -> dict[str, Any]:
    object_refs = unique_text(related_objects)
    operation_refs = unique_text(related_operations)
    evidence_rows = dedupe_evidence(evidence)
    unknown_id = stable_id(
        "understanding_unknown",
        kind,
        question,
        object_refs,
        operation_refs,
        [row.get("fact_id") or row.get("source_locator") for row in evidence_rows],
    )
    return {
        "schema": UNKNOWN_SCHEMA,
        "unknown_id": unknown_id,
        "kind": text(kind) or "BUSINESS_DEFINITION_UNKNOWN",
        "question": text(question),
        "related_object_refs": object_refs,
        "related_operation_refs": operation_refs,
        "severity": text(severity) or "P1",
        "blocks_formal_understanding": bool(blocks_formal_understanding),
        "reason_code": text(reason_code) or text(kind),
        "details": dict(details or {}),
        "evidence": evidence_rows,
        "resolution_status": "UNRESOLVED",
        "automatic_inference_allowed": False,
    }


def empty_model() -> dict[str, Any]:
    return {
        "schema": MODEL_SCHEMA,
        "language_contract": "CHINESE_SOURCE_TEXT_IS_FACT_AUTHORITY",
        "translation_as_fact_authority": False,
        "quality_claim": "MODEL_COMPLETENESS_PROJECTION_NOT_RECALL",
        "business_objects": [],
        "actors": [],
        "operations": [],
        "object_relations": [],
        "lifecycles": [],
        "processes": [],
        "rules": [],
        "decision_matrix_row_ledger": [],
        "business_behaviors": [],
        "behavior_conflicts": [],
        "behavior_ir_gate": {
            "schema": BEHAVIOR_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "metrics": {},
        },
        "behavior_implementation_bindings": [],
        "implementation_binding_unknowns": [],
        "implementation_binding_conflicts": [],
        "implementation_evidence_index": [],
        "implementation_binding_gate": {
            "schema": IMPLEMENTATION_BINDING_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "scenario_planning_allowed": False,
            "execution_allowed": False,
            "metrics": {},
        },
        "binding_identity_graph": {
            "schema": BINDING_IDENTITY_SCHEMA,
            "action_surface_bindings": [],
            "contract_field_bindings": [],
            "runtime_value_bindings": [],
            "observer_bindings": [],
            "formal_ui_surface_bindings": [],
        },
        "binding_identity_unknowns": [],
        "binding_identity_relationships": [],
        "binding_identity_gate": {
            "schema": BINDING_IDENTITY_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "binding_identity_ready": False,
            "execution_allowed": False,
            "metrics": {},
        },
        "scenario_ir": [],
        "scenario_ir_unknowns": [],
        "scenario_ir_evidence_index": [],
        "scenario_ir_relationships": [],
        "scenario_ir_gate": {
            "schema": SCENARIO_IR_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "scenario_ir_ready": False,
            "execution_allowed": False,
            "metrics": {},
        },
        "scenario_execution_contracts": [],
        "scenario_execution_contract_unknowns": [],
        "scenario_execution_contract_evidence_index": [],
        "scenario_execution_contract_relationships": [],
        "scenario_execution_contract_gate": {
            "schema": SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "execution_contract_ready": False,
            "execution_allowed": False,
            "metrics": {},
        },
        "runtime_plans": [],
        "runtime_plan_unknowns": [],
        "runtime_plan_evidence_index": [],
        "runtime_plan_relationships": [],
        "runtime_plan_gate": {
            "schema": RUNTIME_PLAN_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "runtime_plan_ready": False,
            "execution_allowed": False,
            "metrics": {},
        },
        "runtime_materializations": [],
        "runtime_materialization_unknowns": [],
        "runtime_materialization_evidence_index": [],
        "runtime_materialization_relationships": [],
        "runtime_materialization_gate": {
            "schema": RUNTIME_MATERIALIZATION_GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "runtime_materialization_ready": False,
            "execution_allowed": False,
            "metrics": {},
        },
        "unknowns": [],
        "conflicts": [],
        "evidence_index": [],
        "metrics": {},
        "gate": {
            "schema": GATE_SCHEMA,
            "status": "NOT_BUILT",
            "entry_allowed": False,
            "critical_unknowns": [],
        },
    }


def validate_model_shape(model: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structural violations without silently repairing the model."""
    violations: list[dict[str, Any]] = []
    if text(model.get("schema")) != MODEL_SCHEMA:
        violations.append({"code": "MODEL_SCHEMA_INVALID", "value": model.get("schema")})
    for key in (
        "business_objects",
        "actors",
        "operations",
        "object_relations",
        "lifecycles",
        "processes",
        "rules",
        "decision_matrix_row_ledger",
        "business_behaviors",
        "behavior_conflicts",
        "unknowns",
        "conflicts",
        "evidence_index",
    ):
        if not isinstance(model.get(key), list):
            violations.append({"code": "MODEL_COLLECTION_INVALID", "field": key})
    # Downstream fields remain optional for persisted pre-v1 models. When present,
    # their containers must match the canonical runtime-understanding contract.
    for key in (
        "behavior_implementation_bindings",
        "implementation_binding_unknowns",
        "implementation_binding_conflicts",
        "implementation_evidence_index",
        "binding_identity_unknowns",
        "binding_identity_relationships",
        "scenario_ir",
        "scenario_ir_unknowns",
        "scenario_ir_evidence_index",
        "scenario_ir_relationships",
        "scenario_execution_contracts",
        "scenario_execution_contract_unknowns",
        "scenario_execution_contract_evidence_index",
        "scenario_execution_contract_relationships",
        "runtime_plans",
        "runtime_plan_unknowns",
        "runtime_plan_evidence_index",
        "runtime_plan_relationships",
        "runtime_materializations",
        "runtime_materialization_unknowns",
        "runtime_materialization_evidence_index",
        "runtime_materialization_relationships",
    ):
        if key in model and not isinstance(model.get(key), list):
            violations.append({"code": "MODEL_COLLECTION_INVALID", "field": key})
    if not isinstance(model.get("behavior_ir_gate"), dict):
        violations.append({"code": "MODEL_OBJECT_INVALID", "field": "behavior_ir_gate"})
    for key in (
        "implementation_binding_gate",
        "binding_identity_graph",
        "binding_identity_gate",
        "scenario_ir_gate",
        "scenario_execution_contract_gate",
        "runtime_plan_gate",
        "runtime_materialization_gate",
    ):
        if key in model and not isinstance(model.get(key), dict):
            violations.append({"code": "MODEL_OBJECT_INVALID", "field": key})

    graph = as_dict(model.get("binding_identity_graph"))
    if graph:
        if text(graph.get("schema")) != BINDING_IDENTITY_SCHEMA:
            violations.append(
                {
                    "code": "BINDING_IDENTITY_GRAPH_SCHEMA_INVALID",
                    "value": graph.get("schema"),
                }
            )
        for key in (
            "action_surface_bindings",
            "contract_field_bindings",
            "runtime_value_bindings",
            "observer_bindings",
            "formal_ui_surface_bindings",
        ):
            if not isinstance(graph.get(key), list):
                violations.append(
                    {"code": "MODEL_COLLECTION_INVALID", "field": f"binding_identity_graph.{key}"}
                )

    for collection in (
        "business_objects",
        "actors",
        "operations",
        "object_relations",
        "lifecycles",
        "processes",
        "business_behaviors",
    ):
        for index, row in enumerate(as_list(model.get(collection))):
            if not isinstance(row, dict):
                violations.append({"code": "MODEL_ENTRY_INVALID", "field": collection, "index": index})
                continue
            evidence = row.get("evidence")
            if not isinstance(evidence, list) or not evidence:
                violations.append(
                    {
                        "code": "FORMAL_ENTRY_WITHOUT_EVIDENCE",
                        "field": collection,
                        "index": index,
                        "id": row.get("object_id")
                        or row.get("actor_id")
                        or row.get("operation_id")
                        or row.get("relation_id")
                        or row.get("lifecycle_id")
                        or row.get("process_id")
                        or row.get("behavior_id"),
                    }
                )
    return violations


__all__ = [
    "MODEL_SCHEMA",
    "OBJECT_SCHEMA",
    "ACTOR_SCHEMA",
    "OPERATION_SCHEMA",
    "RELATION_SCHEMA",
    "LIFECYCLE_SCHEMA",
    "PROCESS_SCHEMA",
    "BEHAVIOR_SCHEMA",
    "BEHAVIOR_ROW_LEDGER_SCHEMA",
    "BEHAVIOR_GATE_SCHEMA",
    "IMPLEMENTATION_BINDING_SCHEMA",
    "IMPLEMENTATION_BINDING_GATE_SCHEMA",
    "BINDING_IDENTITY_SCHEMA",
    "BINDING_IDENTITY_GATE_SCHEMA",
    "SCENARIO_IR_SCHEMA",
    "SCENARIO_IR_GATE_SCHEMA",
    "SCENARIO_EXECUTION_CONTRACT_SCHEMA",
    "SCENARIO_EXECUTION_CONTRACT_GATE_SCHEMA",
    "RUNTIME_PLAN_SCHEMA",
    "RUNTIME_PLAN_GATE_SCHEMA",
    "RUNTIME_MATERIALIZATION_SCHEMA",
    "RUNTIME_MATERIALIZATION_GATE_SCHEMA",
    "UNKNOWN_SCHEMA",
    "GATE_SCHEMA",
    "text",
    "as_dict",
    "as_list",
    "stable_id",
    "unique_text",
    "source_evidence",
    "evidence_from_fact",
    "dedupe_evidence",
    "new_unknown",
    "empty_model",
    "validate_model_shape",
]
