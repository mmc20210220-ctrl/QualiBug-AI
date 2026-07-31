"""Carry stable enterprise entity identities through behavior and execution IR."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from .identity_types import IDENTITY_BINDING_SCHEMA
from .schema import as_dict, as_list, dedupe_evidence, stable_id, text, unique_text

_ACCEPTED_BINDING_STATUS = {"BOUND", "BOUND_CHANNEL_ONLY", "RESOLVED"}


def _entity_refs_for_labels(mapping: dict[str, str], labels: Iterable[Any]) -> list[str]:
    return unique_text(mapping.get(text(label)) for label in labels)


def _fact_entity_lookup(asset: dict[str, Any]) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for fact in as_list(as_dict(asset.get("business_fact_ledger")).get("items")):
        if not isinstance(fact, dict) or not text(fact.get("fact_id")):
            continue
        refs = unique_text(
            [
                *as_list(as_dict(fact.get("subject")).get("resolved_entity_refs")),
                *as_list(as_dict(fact.get("object")).get("resolved_entity_refs")),
                *as_list(fact.get("identity_resolution_refs")),
            ]
        )
        result[text(fact.get("fact_id"))] = refs
    return result


def _binding_row(
    *,
    entity_id: str,
    artifact_type: str,
    artifact_ref: Any,
    relation: str,
    behavior_ref: str,
    evidence: Iterable[dict[str, Any]],
) -> dict[str, Any] | None:
    artifact_ref = text(artifact_ref)
    if not entity_id or not artifact_ref:
        return None
    return {
        "schema": IDENTITY_BINDING_SCHEMA,
        "binding_id": stable_id(
            "identity_binding", entity_id, artifact_type, artifact_ref, relation
        ),
        "entity_id": entity_id,
        "artifact_type": artifact_type,
        "artifact_ref": artifact_ref,
        "relation": relation,
        "status": "RESOLVED",
        "behavior_refs": unique_text([behavior_ref]),
        "identity_field_bindings": [],
        "derivation": "governed_implementation_binding_projection",
        "evidence": dedupe_evidence(evidence),
    }


def _authoritative_rows(values: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for value in values:
        if not isinstance(value, dict):
            continue
        status = text(value.get("status"))
        authoritative = value.get("authoritative")
        if status and status not in _ACCEPTED_BINDING_STATUS:
            continue
        if authoritative is False:
            continue
        result.append(value)
    return result


def _observer_candidates(binding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("condition_observer_bindings", "effect_observer_bindings"):
        for slot in as_list(binding.get(key)):
            if not isinstance(slot, dict) or text(slot.get("status")) != "BOUND":
                continue
            rows.extend(_authoritative_rows(as_list(slot.get("bindings"))))
    return rows


def _merge_bindings(rows: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict) or not text(row.get("binding_id")):
            continue
        binding_id = text(row.get("binding_id"))
        existing = merged.get(binding_id)
        if existing is None:
            merged[binding_id] = dict(row)
            continue
        existing["behavior_refs"] = unique_text(
            [*as_list(existing.get("behavior_refs")), *as_list(row.get("behavior_refs"))]
        )
        existing["evidence"] = dedupe_evidence(
            [*as_list(existing.get("evidence")), *as_list(row.get("evidence"))]
        )
    return sorted(merged.values(), key=lambda row: text(row.get("binding_id")))


def _project_bindings_from_implementation(
    model: dict[str, Any], behavior_entities: dict[str, list[str]]
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for binding in as_list(model.get("behavior_implementation_bindings")):
        if not isinstance(binding, dict):
            continue
        behavior_ref = text(binding.get("behavior_ref"))
        entity_refs = behavior_entities.get(behavior_ref, [])
        if not entity_refs:
            continue
        api_rows = _authoritative_rows(as_list(binding.get("api_operation_bindings")))
        api_rows.extend(_authoritative_rows(as_list(binding.get("response_observer_bindings"))))
        ui_rows = _authoritative_rows(as_list(binding.get("ui_action_bindings")))
        observers = _observer_candidates(binding)
        for entity_id in entity_refs:
            for row in api_rows:
                projected = _binding_row(
                    entity_id=entity_id,
                    artifact_type="API_OPERATION",
                    artifact_ref=row.get("interface_id") or row.get("operation_id"),
                    relation="EXPOSES_ENTITY",
                    behavior_ref=behavior_ref,
                    evidence=as_list(row.get("evidence")),
                )
                if projected:
                    rows.append(projected)
            for row in ui_rows:
                projected = _binding_row(
                    entity_id=entity_id,
                    artifact_type="UI_VIEW",
                    artifact_ref=row.get("ui_spec_id") or row.get("component_id"),
                    relation="VIEW_OF",
                    behavior_ref=behavior_ref,
                    evidence=as_list(row.get("evidence")),
                )
                if projected:
                    rows.append(projected)
            for row in observers:
                projected = _binding_row(
                    entity_id=entity_id,
                    artifact_type="DATABASE_TABLE",
                    artifact_ref=(
                        row.get("table_id")
                        or row.get("table_ref")
                        or row.get("table")
                        or row.get("source_table")
                    ),
                    relation="IMPLEMENTS_ENTITY",
                    behavior_ref=behavior_ref,
                    evidence=as_list(row.get("evidence")),
                )
                if projected:
                    projected["identity_field_bindings"] = [
                        {
                            "technical_field": text(
                                row.get("field")
                                or row.get("field_ref")
                                or row.get("field_path")
                            ),
                            "source_backed": True,
                        }
                    ] if text(row.get("field") or row.get("field_ref") or row.get("field_path")) else []
                    rows.append(projected)
    return rows


def _propagate_collection(
    rows: list[Any],
    *,
    refs_by_parent: dict[str, list[str]],
    parent_keys: tuple[str, ...],
    id_keys: tuple[str, ...],
) -> dict[str, list[str]]:
    result: dict[str, list[str]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        parent_refs = unique_text(
            [
                *(raw.get(key) for key in parent_keys),
                *(value for key in parent_keys for value in as_list(raw.get(f"{key}s"))),
            ]
        )
        entity_refs = unique_text(
            [
                *as_list(raw.get("business_entity_refs")),
                *(entity for parent in parent_refs for entity in refs_by_parent.get(parent, [])),
            ]
        )
        raw["business_entity_refs"] = entity_refs
        raw["identity_resolution_status"] = "RESOLVED" if entity_refs else "UNRESOLVED"
        raw["identity_execution_allowed"] = bool(entity_refs)
        row_id = next((text(raw.get(key)) for key in id_keys if text(raw.get(key))), "")
        if row_id:
            result[row_id] = entity_refs
    return result


def project_identity_to_downstream(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    term_resolution = as_dict(model.get("term_resolution"))
    label_to_entity = dict(as_dict(term_resolution.get("alias_to_entity")))
    fact_entities = _fact_entity_lookup(asset)
    behaviors = [
        row for row in as_list(model.get("business_behaviors")) if isinstance(row, dict)
    ]
    behavior_entities: dict[str, list[str]] = {}
    unresolved_behavior_refs: list[str] = []
    for behavior in behaviors:
        behavior_ref = text(behavior.get("behavior_id"))
        refs = unique_text(
            [
                *_entity_refs_for_labels(label_to_entity, as_list(behavior.get("object_refs"))),
                *(
                    entity
                    for source_ref in as_list(behavior.get("source_refs"))
                    for entity in fact_entities.get(text(source_ref), [])
                ),
            ]
        )
        behavior["business_entity_refs"] = refs
        behavior["identity_resolution_status"] = "RESOLVED" if refs else "UNRESOLVED"
        behavior["identity_execution_allowed"] = bool(refs)
        if behavior_ref:
            behavior_entities[behavior_ref] = refs
        if not refs and as_list(behavior.get("object_refs")):
            unresolved_behavior_refs.append(behavior_ref)
            behavior["unresolved_semantics"] = unique_text(
                [*as_list(behavior.get("unresolved_semantics")), "BEHAVIOR_IDENTITY_UNRESOLVED"]
            )

    projected = _project_bindings_from_implementation(model, behavior_entities)
    all_identity_bindings = _merge_bindings(
        [
            *[row for row in as_list(model.get("identity_bindings")) if isinstance(row, dict)],
            *projected,
        ]
    )
    model["identity_bindings"] = all_identity_bindings
    by_entity: dict[str, list[str]] = defaultdict(list)
    for row in all_identity_bindings:
        by_entity[text(row.get("entity_id"))].append(text(row.get("binding_id")))
    for behavior in behaviors:
        refs = as_list(behavior.get("business_entity_refs"))
        behavior["identity_binding_refs"] = unique_text(
            binding_id for entity_id in refs for binding_id in by_entity.get(text(entity_id), [])
        )

    implementation_bindings = [
        row
        for row in as_list(model.get("behavior_implementation_bindings"))
        if isinstance(row, dict)
    ]
    for binding in implementation_bindings:
        refs = behavior_entities.get(text(binding.get("behavior_ref")), [])
        binding["business_entity_refs"] = refs
        binding["identity_binding_refs"] = unique_text(
            binding_id for entity_id in refs for binding_id in by_entity.get(text(entity_id), [])
        )
        binding["identity_resolution_ready"] = bool(refs)
        if not refs:
            binding["scenario_planning_ready"] = False
            binding["execution_ready"] = False
            if text(binding.get("status")) not in {"CONFLICTED", "AMBIGUOUS"}:
                binding["status"] = "PARTIAL"
            binding["identity_reason_code"] = "BEHAVIOR_IDENTITY_UNRESOLVED"

    behavior_gate = dict(as_dict(model.get("behavior_ir_gate")))
    behavior_gate["metrics"] = {
        **as_dict(behavior_gate.get("metrics")),
        "identity_resolved_behavior_count": len(behaviors) - len(unresolved_behavior_refs),
        "identity_unresolved_behavior_count": len(unresolved_behavior_refs),
    }
    if unresolved_behavior_refs:
        behavior_gate.update(
            {
                "status": "BLOCKED_BEHAVIOR_IDENTITY_UNRESOLVED",
                "entry_allowed": False,
                "execution_allowed": False,
                "unresolved_behavior_refs": unresolved_behavior_refs,
            }
        )
    model["behavior_ir_gate"] = behavior_gate

    implementation_gate = dict(as_dict(model.get("implementation_binding_gate")))
    unresolved_implementation = [
        text(row.get("binding_id"))
        for row in implementation_bindings
        if not bool(row.get("identity_resolution_ready"))
    ]
    implementation_gate["metrics"] = {
        **as_dict(implementation_gate.get("metrics")),
        "identity_unresolved_binding_count": len(unresolved_implementation),
    }
    if unresolved_implementation:
        implementation_gate.update(
            {
                "status": "PARTIAL_IMPLEMENTATION_BINDING_IDENTITY_UNRESOLVED",
                "entry_allowed": False,
                "scenario_planning_allowed": False,
                "execution_allowed": False,
                "identity_unresolved_binding_refs": unresolved_implementation,
            }
        )
    model["implementation_binding_gate"] = implementation_gate

    scenario_entities = _propagate_collection(
        as_list(model.get("scenario_ir")),
        refs_by_parent=behavior_entities,
        parent_keys=("behavior_ref", "source_behavior_ref"),
        id_keys=("scenario_id", "scenario_ir_id"),
    )
    contract_entities = _propagate_collection(
        as_list(model.get("scenario_execution_contracts")),
        refs_by_parent=scenario_entities,
        parent_keys=("scenario_ref", "scenario_ir_ref"),
        id_keys=("contract_id", "execution_contract_id"),
    )
    plan_entities = _propagate_collection(
        as_list(model.get("runtime_plans")),
        refs_by_parent={**scenario_entities, **contract_entities},
        parent_keys=("scenario_ref", "scenario_ir_ref", "execution_contract_ref"),
        id_keys=("runtime_plan_id", "plan_id"),
    )
    _propagate_collection(
        as_list(model.get("runtime_materializations")),
        refs_by_parent={**scenario_entities, **contract_entities, **plan_entities},
        parent_keys=("scenario_ref", "execution_contract_ref", "runtime_plan_ref"),
        id_keys=("runtime_materialization_id", "materialization_id"),
    )

    model["identity_execution_admission"] = {
        "schema": "qualibug.enterprise-identity-execution-admission.v1",
        "status": "BLOCKED" if unresolved_behavior_refs else "PASS",
        "entry_allowed": not unresolved_behavior_refs,
        "unresolved_behavior_refs": unresolved_behavior_refs,
        "behavior_count": len(behaviors),
        "identity_binding_count": len(all_identity_bindings),
        "name_only_execution_allowed": False,
    }
    resolution = as_dict(asset.get("enterprise_identity_resolution"))
    resolution["bindings"] = all_identity_bindings
    asset["enterprise_identity_resolution"] = resolution
    asset["enterprise_understanding_model"] = model
    return model
