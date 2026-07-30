"""Project operation-scoped database mapping candidates from exact API schema bindings.

This stage composes two already-separated facts:
1. an accepted OpenAPI operation→schema binding, and
2. a pending schema/field→database candidate.

The output remains pending authority. It is suitable for planning observers, but cannot be used
as a write target or oracle until an explicit business/storage authority accepts the mapping.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Iterable

API_OPERATION_DATABASE_PROJECTION_SCHEMA = (
    "qualibug.api-operation-database-candidate-projection.v1"
)
API_OPERATION_DATABASE_TABLE_CANDIDATE_SCHEMA = (
    "qualibug.api-operation-database-table-candidate.v1"
)
API_OPERATION_DATABASE_FIELD_CANDIDATE_SCHEMA = (
    "qualibug.api-operation-database-field-candidate.v1"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _dedupe(rows: Iterable[Any], identity_field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        identity = _text(row.get(identity_field))
        if not identity or identity in seen:
            continue
        seen.add(identity)
        result.append(row)
    return result


def enrich_asset_with_api_operation_database_candidates(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Attach operation-scoped storage candidates without accepting them."""
    result = dict(asset or {})
    bindings = [
        deepcopy(row)
        for row in _list(result.get("api_operation_schema_bindings"))
        if isinstance(row, dict)
        and _text(row.get("binding_id"))
        and _text(row.get("interface_id"))
        and _text(row.get("api_schema_entity_id"))
    ]
    entity_candidates = [
        deepcopy(row)
        for row in _list(result.get("api_database_entity_alignment_candidates"))
        if isinstance(row, dict)
        and _text(row.get("candidate_id"))
        and _text(row.get("api_entity_id"))
    ]
    field_candidates = [
        deepcopy(row)
        for row in _list(result.get("api_database_field_alignment_candidates"))
        if isinstance(row, dict)
        and _text(row.get("candidate_id"))
        and _text(row.get("entity_candidate_id"))
    ]

    entity_candidates_by_api_entity: dict[str, list[dict[str, Any]]] = {}
    for candidate in entity_candidates:
        entity_candidates_by_api_entity.setdefault(
            _text(candidate.get("api_entity_id")), []
        ).append(candidate)
    fields_by_entity_candidate: dict[str, list[dict[str, Any]]] = {}
    for candidate in field_candidates:
        fields_by_entity_candidate.setdefault(
            _text(candidate.get("entity_candidate_id")), []
        ).append(candidate)

    table_rows: list[dict[str, Any]] = []
    field_rows: list[dict[str, Any]] = []
    relationships = [
        deepcopy(row)
        for row in _list(result.get("relationships"))
        if isinstance(row, dict)
    ]
    used_entity_candidate_ids: set[str] = set()
    used_field_candidate_ids: set[str] = set()

    for binding in bindings:
        binding_id = _text(binding.get("binding_id"))
        interface_id = _text(binding.get("interface_id"))
        api_entity_id = _text(binding.get("api_schema_entity_id"))
        direction = _text(binding.get("direction"))
        for entity_candidate in entity_candidates_by_api_entity.get(api_entity_id, []):
            entity_candidate_id = _text(entity_candidate.get("candidate_id"))
            used_entity_candidate_ids.add(entity_candidate_id)
            for table_match in _list(entity_candidate.get("database_table_matches")):
                if not isinstance(table_match, dict):
                    continue
                table_id = _text(table_match.get("table_id"))
                if not table_id:
                    continue
                candidate_id = _stable_id(
                    "api_operation_database_table_candidate",
                    binding_id,
                    entity_candidate_id,
                    table_id,
                )
                row = {
                    "schema": API_OPERATION_DATABASE_TABLE_CANDIDATE_SCHEMA,
                    "candidate_id": candidate_id,
                    "operation_schema_binding_id": binding_id,
                    "interface_id": interface_id,
                    "method": _text(binding.get("method")),
                    "path": _text(binding.get("path")),
                    "direction": direction,
                    "response_status": _text(binding.get("response_status")),
                    "media_type": _text(binding.get("media_type")),
                    "api_schema_entity_id": api_entity_id,
                    "api_schema_id": _text(binding.get("api_schema_id")),
                    "api_schema_name": _text(binding.get("api_schema_name")),
                    "entity_alignment_candidate_id": entity_candidate_id,
                    "database_table_id": table_id,
                    "database_schema_name": _text(table_match.get("schema_name")),
                    "database_qualified_name": _text(
                        table_match.get("qualified_name")
                    ),
                    "status": "PENDING_STORAGE_AUTHORITY",
                    "observer_candidate_only": True,
                    "write_target_allowed": False,
                    "oracle_authority_allowed": False,
                    "automatic_mapping_allowed": False,
                    "business_object_confirmed": False,
                    "storage_mapping_confirmed": False,
                }
                table_rows.append(row)
                relationships.append(
                    {
                        "edge_id": f"edge:api-operation-database-table:{candidate_id}",
                        "from": interface_id,
                        "to": table_id,
                        "relation": "api_operation_database_table_alignment_candidate",
                        "confidence": 0.5,
                        "status": "pending_authority",
                        "derivation": "exact_operation_schema_plus_pending_entity_alignment",
                        "evidence": {
                            "candidate_id": candidate_id,
                            "operation_schema_binding_id": binding_id,
                            "entity_alignment_candidate_id": entity_candidate_id,
                            "direction": direction,
                            "automatic_mapping_allowed": False,
                            "write_target_allowed": False,
                            "oracle_authority_allowed": False,
                        },
                    }
                )

            for field_candidate in fields_by_entity_candidate.get(
                entity_candidate_id, []
            ):
                field_candidate_id = _text(field_candidate.get("candidate_id"))
                database_field_id = _text(field_candidate.get("database_field_id"))
                api_field_id = _text(field_candidate.get("api_field_id"))
                if not database_field_id or not api_field_id:
                    continue
                used_field_candidate_ids.add(field_candidate_id)
                candidate_id = _stable_id(
                    "api_operation_database_field_candidate",
                    binding_id,
                    field_candidate_id,
                )
                row = {
                    "schema": API_OPERATION_DATABASE_FIELD_CANDIDATE_SCHEMA,
                    "candidate_id": candidate_id,
                    "operation_schema_binding_id": binding_id,
                    "interface_id": interface_id,
                    "method": _text(binding.get("method")),
                    "path": _text(binding.get("path")),
                    "direction": direction,
                    "response_status": _text(binding.get("response_status")),
                    "media_type": _text(binding.get("media_type")),
                    "api_schema_entity_id": api_entity_id,
                    "api_field_id": api_field_id,
                    "api_field_name": _text(field_candidate.get("api_field_name")),
                    "api_property_path": deepcopy(
                        _list(field_candidate.get("api_property_path"))
                    ),
                    "field_alignment_candidate_id": field_candidate_id,
                    "database_table_id": _text(
                        field_candidate.get("database_table_id")
                    ),
                    "database_field_id": database_field_id,
                    "database_field_name": _text(
                        field_candidate.get("database_field_name")
                    ),
                    "type_compatibility": deepcopy(
                        _dict(field_candidate.get("type_compatibility"))
                    ),
                    "status": (
                        "TYPE_CONFLICT_REQUIRES_AUTHORITY"
                        if _text(
                            _dict(field_candidate.get("type_compatibility")).get(
                                "status"
                            )
                        )
                        == "INCOMPATIBLE"
                        else "PENDING_STORAGE_FIELD_AUTHORITY"
                    ),
                    "observer_candidate_only": True,
                    "write_target_allowed": False,
                    "oracle_authority_allowed": False,
                    "automatic_mapping_allowed": False,
                    "business_attribute_confirmed": False,
                    "storage_mapping_confirmed": False,
                }
                field_rows.append(row)
                relationships.append(
                    {
                        "edge_id": f"edge:api-operation-database-field:{candidate_id}",
                        "from": interface_id,
                        "to": database_field_id,
                        "relation": "api_operation_database_field_alignment_candidate",
                        "confidence": (
                            0.6
                            if _text(
                                _dict(field_candidate.get("type_compatibility")).get(
                                    "status"
                                )
                            )
                            == "COMPATIBLE"
                            else 0.4
                        ),
                        "status": "pending_authority",
                        "derivation": "exact_operation_schema_plus_pending_field_alignment",
                        "evidence": {
                            "candidate_id": candidate_id,
                            "operation_schema_binding_id": binding_id,
                            "field_alignment_candidate_id": field_candidate_id,
                            "direction": direction,
                            "type_compatibility": deepcopy(
                                _dict(field_candidate.get("type_compatibility"))
                            ),
                            "automatic_mapping_allowed": False,
                            "write_target_allowed": False,
                            "oracle_authority_allowed": False,
                        },
                    }
                )

    table_rows = _dedupe(
        [
            *_list(result.get("api_operation_database_table_candidates")),
            *table_rows,
        ],
        "candidate_id",
    )
    field_rows = _dedupe(
        [
            *_list(result.get("api_operation_database_field_candidates")),
            *field_rows,
        ],
        "candidate_id",
    )
    relationships = _dedupe(relationships, "edge_id")

    for candidate in entity_candidates:
        candidate_id = _text(candidate.get("candidate_id"))
        candidate["operation_scope_status"] = (
            "REFERENCED_BY_EXACT_API_OPERATION"
            if candidate_id in used_entity_candidate_ids
            else "NOT_REFERENCED_BY_EXACT_API_OPERATION"
        )
        candidate["operation_schema_binding_ids"] = sorted(
            {
                _text(row.get("operation_schema_binding_id"))
                for row in table_rows
                if _text(row.get("entity_alignment_candidate_id")) == candidate_id
            }
            - {""}
        )
    for candidate in field_candidates:
        candidate_id = _text(candidate.get("candidate_id"))
        candidate["operation_scope_status"] = (
            "REFERENCED_BY_EXACT_API_OPERATION"
            if candidate_id in used_field_candidate_ids
            else "NOT_REFERENCED_BY_EXACT_API_OPERATION"
        )
        candidate["operation_schema_binding_ids"] = sorted(
            {
                _text(row.get("operation_schema_binding_id"))
                for row in field_rows
                if _text(row.get("field_alignment_candidate_id")) == candidate_id
            }
            - {""}
        )

    result["api_database_entity_alignment_candidates"] = entity_candidates
    result["api_database_field_alignment_candidates"] = field_candidates
    result["api_operation_database_table_candidates"] = table_rows
    result["api_operation_database_field_candidates"] = field_rows
    result["relationships"] = relationships
    result["api_operation_database_candidate_projection"] = {
        "schema": API_OPERATION_DATABASE_PROJECTION_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not bindings
            else "PARTIAL"
            if table_rows or field_rows
            else "NO_STORAGE_CANDIDATES"
        ),
        "operation_schema_binding_count": len(bindings),
        "operation_database_table_candidate_count": len(table_rows),
        "operation_database_field_candidate_count": len(field_rows),
        "operation_scoped_entity_candidate_count": len(used_entity_candidate_ids),
        "operation_scoped_field_candidate_count": len(used_field_candidate_ids),
        "automatic_table_mapping_count": 0,
        "automatic_field_mapping_count": 0,
        "write_target_authority_count": 0,
        "oracle_authority_count": 0,
        "accepted_operation_schema_binding_is_not_storage_authority": True,
    }

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "api_operation_database_table_candidate_count": len(table_rows),
            "api_operation_database_field_candidate_count": len(field_rows),
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "api_operation_database_candidates_never_authorize_writes": True,
            "api_operation_database_candidates_never_authorize_oracles": True,
            "api_operation_database_projection_requires_exact_schema_binding": True,
            "api_operation_database_projection_requires_pending_storage_candidate": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "API_OPERATION_DATABASE_PROJECTION_SCHEMA",
    "API_OPERATION_DATABASE_TABLE_CANDIDATE_SCHEMA",
    "API_OPERATION_DATABASE_FIELD_CANDIDATE_SCHEMA",
    "enrich_asset_with_api_operation_database_candidates",
]
