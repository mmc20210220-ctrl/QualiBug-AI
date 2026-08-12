"""Project source-authoritative database identity relations into Behavior IR."""
from __future__ import annotations
from typing import Any

from .database_body_reference_projection_common import (
    SCHEMA_VERSION, RELATION_SCHEMA, _dict, _list, _text, _stable_id,
    _operation_ref, _operation_index, _approved_contract, _contract_operation,
    _approved_request_field_bindings, _authoritative_fk, _parent_table_entity,
)
from .database_body_reference_source_fk import _project_source_bound_operation_fk_relations

def project_database_body_reference_relations(
    model: dict[str, Any],
    asset: dict[str, Any] | None,
) -> dict[str, Any]:
    """Return ``model`` with source-authoritative body-reference receipts attached."""
    result = dict(model or {})
    data = _dict(asset)
    contracts = [
        contract
        for contract in (_approved_contract(row) for row in _list(data.get("database_observer_contracts")))
        if contract
    ]
    foreign_keys = [
        relation
        for relation in (_authoritative_fk(row) for row in _list(data.get("database_model_relationships")))
        if relation
    ]
    operation_index = _operation_index(result)

    receipts: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for contract in contracts:
        operation = _contract_operation(contract, operation_index)
        if not operation:
            continue
        operation_ref = _operation_ref(operation)
        child_table_id = _text(contract.get("database_table_id"))
        for binding in _approved_request_field_bindings(contract):
            child_column = _text(binding.get("database_field_name"))
            for relationship in foreign_keys:
                if _text(relationship.get("child_table_id")) != child_table_id:
                    continue
                child_columns = [_text(value) for value in _list(relationship.get("child_columns")) if _text(value)]
                if child_column not in child_columns:
                    continue
                parent_columns = [_text(value) for value in _list(relationship.get("parent_columns")) if _text(value)]
                target_entity_ref, parent_mapping_decisions = _parent_table_entity(
                    _text(relationship.get("parent_table_id")),
                    parent_columns,
                    contracts,
                    operation_index,
                    result,
                )
                if not target_entity_ref:
                    continue
                ordinal = child_columns.index(child_column)
                body_path = _text(binding.get("body_path"))
                identity = (operation_ref, body_path, target_entity_ref)
                if identity in seen:
                    continue
                seen.add(identity)
                source_id = _text(relationship.get("source_id"))
                source_locator = _text(relationship.get("source_locator"))
                receipts.append({
                    "schema": RELATION_SCHEMA,
                    "id": _stable_id("body_reference", operation_ref, body_path, target_entity_ref),
                    "status": "RESOLVED",
                    "reason_code": "",
                    "operation_ref": operation_ref,
                    "body_path": body_path,
                    "target_entity_ref": target_entity_ref,
                    "child_database_table_id": child_table_id,
                    "child_database_column": child_column,
                    "parent_database_table_id": _text(relationship.get("parent_table_id")),
                    "parent_database_column": parent_columns[ordinal],
                    "database_relationship_id": _text(relationship.get("relationship_id")),
                    "field_mapping_decision_id": _text(binding.get("mapping_decision_id")),
                    "child_table_mapping_decision_id": _text(contract.get("table_mapping_decision_id")),
                    "parent_table_mapping_decision_ids": parent_mapping_decisions,
                    "authority": "operator_approved_api_db_mapping+database_model_exact_fk+source_bound_entity",
                    "source_refs": [{
                        "source_id": source_id,
                        "locator": source_locator,
                        "kind": "database_foreign_key",
                    }],
                })

    for projected in _project_source_bound_operation_fk_relations(
        result, data, foreign_keys
    ):
        identity = (
            _text(projected.get("operation_ref")),
            _text(projected.get("body_path")),
            _text(projected.get("target_entity_ref")),
        )
        if identity in seen:
            continue
        seen.add(identity)
        receipts.append(projected)

    result["body_reference_relations"] = sorted(
        receipts,
        key=lambda row: (_text(row.get("operation_ref")), _text(row.get("body_path")), _text(row.get("target_entity_ref"))),
    )
    result["database_body_reference_projection_receipt"] = {
        "schema": SCHEMA_VERSION,
        "status": "COMPLETE" if receipts else "NOT_APPLICABLE",
        "approved_contract_count": len(contracts),
        "exact_foreign_key_count": len(foreign_keys),
        "resolved_body_reference_count": len(receipts),
        "source_bound_operation_fk_reference_count": sum(
            1
            for row in receipts
            if _text(row.get("authority")) == "source_bound_operation_entity+database_model_exact_fk"
        ),
        "field_name_inference_allowed": False,
        "route_shape_inference_allowed": False,
        "identifier_punctuation_normalization_allowed": True,
        "operator_approved_mapping_required_for_database_observer_authority": True,
        "source_bound_operation_entity_can_authorize_body_fk_only": True,
        "exact_database_foreign_key_required": True,
    }
    return result


__all__ = ["SCHEMA_VERSION", "RELATION_SCHEMA", "project_database_body_reference_relations"]
