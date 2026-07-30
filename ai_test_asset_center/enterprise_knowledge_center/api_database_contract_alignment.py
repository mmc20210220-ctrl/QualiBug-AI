"""Build fail-visible OpenAPI-schema to database-model alignment candidates.

The stage is deliberately conservative. It first scopes field comparison through an exact
schema-name/table-name candidate, then compares exact field names inside that candidate pair.
Names and type compatibility are supporting evidence only: no business object, table or field
identity is accepted without an explicit authority decision.
"""
from __future__ import annotations

import hashlib
import re
from copy import deepcopy
from typing import Any, Iterable

API_DATABASE_ALIGNMENT_SCHEMA = "qualibug.api-database-contract-alignment.v1"
API_DATABASE_ENTITY_CANDIDATE_SCHEMA = (
    "qualibug.api-schema-database-table-alignment-candidate.v1"
)
API_DATABASE_FIELD_CANDIDATE_SCHEMA = (
    "qualibug.api-field-database-column-alignment-candidate.v1"
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


def _database_model_table(row: dict[str, Any]) -> bool:
    return bool(_list(row.get("database_model_declarations"))) or _text(
        row.get("derivation")
    ) == "database_model_document_ir"


def _database_model_field(row: dict[str, Any]) -> bool:
    return (
        _text(row.get("evidence_kind")) == "SOURCE_DECLARED_DATABASE_MODEL"
        or _text(row.get("derivation")) == "database_model_document_ir"
        or _text(row.get("evidence_derivation")) == "database_model_document_ir"
    )


def _api_type_category(field: dict[str, Any]) -> str:
    raw = _text(field.get("type") or field.get("schema_type")).lower()
    if raw == "integer":
        return "integer"
    if raw == "number":
        return "number"
    if raw == "boolean":
        return "boolean"
    if raw in {"object", "array"}:
        return "json"
    if raw == "string":
        return "string"
    return ""


def _database_type_category(field: dict[str, Any]) -> str:
    raw = _text(field.get("type") or field.get("data_type")).upper()
    if not raw:
        return ""
    base = re.split(r"[\s(<]", raw, maxsplit=1)[0]
    if base in {
        "INT",
        "INTEGER",
        "BIGINT",
        "SMALLINT",
        "TINYINT",
        "MEDIUMINT",
        "SERIAL",
        "BIGSERIAL",
        "SMALLSERIAL",
    }:
        return "integer"
    if base in {
        "DECIMAL",
        "NUMERIC",
        "NUMBER",
        "REAL",
        "FLOAT",
        "DOUBLE",
        "MONEY",
        "SMALLMONEY",
    }:
        return "number"
    if base in {"BOOL", "BOOLEAN"} or raw.startswith("BIT(1"):
        return "boolean"
    if base in {"JSON", "JSONB"}:
        return "json"
    if base in {
        "CHAR",
        "NCHAR",
        "VARCHAR",
        "NVARCHAR",
        "VARCHAR2",
        "TEXT",
        "TINYTEXT",
        "MEDIUMTEXT",
        "LONGTEXT",
        "CLOB",
        "NCLOB",
        "UUID",
        "DATE",
        "TIME",
        "TIMESTAMP",
        "DATETIME",
        "DATETIME2",
        "ENUM",
        "SET",
        "XML",
    }:
        return "string"
    if base in {"BLOB", "BINARY", "VARBINARY", "BYTEA", "IMAGE"}:
        return "binary"
    return ""


def _type_compatibility(
    api_field: dict[str, Any], database_field: dict[str, Any]
) -> dict[str, Any]:
    api_category = _api_type_category(api_field)
    database_category = _database_type_category(database_field)
    if not api_category or not database_category:
        status = "UNKNOWN"
    elif api_category == database_category:
        status = "COMPATIBLE"
    elif {api_category, database_category} <= {"integer", "number"}:
        status = "NUMERIC_COMPATIBLE_WITH_RANGE_RISK"
    else:
        status = "INCOMPATIBLE"
    return {
        "status": status,
        "api_declared_type": _text(
            api_field.get("type") or api_field.get("schema_type")
        ),
        "api_declared_format": _text(
            api_field.get("format") or api_field.get("schema_format")
        ),
        "api_type_category": api_category,
        "database_declared_type": _text(
            database_field.get("type") or database_field.get("data_type")
        ),
        "database_type_category": database_category,
        "supporting_evidence_only": True,
    }


def _field_name(row: dict[str, Any]) -> str:
    return _text(row.get("field_name") or row.get("name")) or (
        _text(_list(row.get("property_path"))[-1])
        if _list(row.get("property_path"))
        else ""
    )


def enrich_asset_with_api_database_alignment_candidates(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Attach entity- and field-level API/database candidates without accepting identity."""
    result = dict(asset or {})
    api_entities = [
        deepcopy(row)
        for row in _list(result.get("openapi_schema_entities"))
        if isinstance(row, dict)
        and _text(row.get("entity_id"))
        and _text(row.get("name"))
    ]
    api_fields = [
        deepcopy(row)
        for row in _list(result.get("openapi_schema_fields"))
        if isinstance(row, dict)
        and _text(row.get("field_fact_id"))
        and _text(row.get("schema_id"))
    ]
    database_tables = [
        deepcopy(row)
        for row in _list(result.get("tables"))
        if isinstance(row, dict)
        and _text(row.get("table_id"))
        and _text(row.get("name"))
        and _database_model_table(row)
    ]
    database_fields = [
        deepcopy(row)
        for row in _list(result.get("field_dictionary"))
        if isinstance(row, dict)
        and _text(row.get("field_id"))
        and _text(row.get("table_id"))
        and _database_model_field(row)
    ]

    api_fields_by_schema: dict[str, list[dict[str, Any]]] = {}
    for field in api_fields:
        api_fields_by_schema.setdefault(_text(field.get("schema_id")), []).append(field)
    database_fields_by_table: dict[str, list[dict[str, Any]]] = {}
    for field in database_fields:
        database_fields_by_table.setdefault(_text(field.get("table_id")), []).append(field)
    tables_by_exact_name: dict[str, list[dict[str, Any]]] = {}
    for table in database_tables:
        tables_by_exact_name.setdefault(_text(table.get("name")), []).append(table)

    entity_candidates: list[dict[str, Any]] = []
    field_candidates: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    relationships = [
        deepcopy(row)
        for row in _list(result.get("relationships"))
        if isinstance(row, dict)
    ]

    for entity in api_entities:
        entity_name = _text(entity.get("name"))
        matches = sorted(
            tables_by_exact_name.get(entity_name, []),
            key=lambda row: _text(row.get("table_id")),
        )
        if not matches:
            continue
        entity_id = _text(entity.get("entity_id"))
        schema_id = _text(entity.get("schema_id"))
        status = (
            "PENDING_BUSINESS_OBJECT_AUTHORITY"
            if len(matches) == 1
            else "AMBIGUOUS_REQUIRES_AUTHORITY"
        )
        candidate_id = _stable_id(
            "api_database_entity_candidate",
            entity_id,
            *[_text(row.get("table_id")) for row in matches],
        )
        candidate = {
            "schema": API_DATABASE_ENTITY_CANDIDATE_SCHEMA,
            "candidate_id": candidate_id,
            "api_entity_id": entity_id,
            "api_schema_id": schema_id,
            "api_schema_name": entity_name,
            "api_source_id": _text(entity.get("source_id")),
            "database_table_matches": [
                {
                    "table_id": _text(table.get("table_id")),
                    "table_name": _text(table.get("name")),
                    "schema_name": _text(table.get("schema_name")),
                    "qualified_name": _text(
                        table.get("qualified_name") or table.get("name")
                    ),
                    "source_refs": deepcopy(_list(table.get("source_refs"))),
                    "source_locator": _text(table.get("source_locator")),
                }
                for table in matches
            ],
            "status": status,
            "operator_authority_required": True,
            "automatic_merge_allowed": False,
            "automatic_winner_selected": False,
            "exact_case_sensitive_name_match": True,
            "name_match_is_supporting_not_identity_authority": True,
            "business_object_confirmed": False,
        }
        entity_candidates.append(candidate)

        for table in matches:
            table_id = _text(table.get("table_id"))
            relationships.append(
                {
                    "edge_id": f"edge:api-database-entity-candidate:{candidate_id}:{table_id}",
                    "from": entity_id,
                    "to": table_id,
                    "relation": "api_schema_database_table_alignment_candidate",
                    "confidence": 0.5,
                    "status": "pending_authority",
                    "derivation": "exact_name_scoped_candidate",
                    "evidence": {
                        "candidate_id": candidate_id,
                        "exact_case_sensitive_name_match": True,
                        "automatic_merge_allowed": False,
                    },
                }
            )

            db_by_name: dict[str, list[dict[str, Any]]] = {}
            for database_field in database_fields_by_table.get(table_id, []):
                db_by_name.setdefault(
                    _text(database_field.get("field")), []
                ).append(database_field)

            for api_field in api_fields_by_schema.get(schema_id, []):
                api_field_name = _field_name(api_field)
                if not api_field_name:
                    continue
                field_matches = db_by_name.get(api_field_name, [])
                for database_field in field_matches:
                    database_field_id = _text(database_field.get("field_id"))
                    api_field_id = _text(api_field.get("field_fact_id"))
                    compatibility = _type_compatibility(api_field, database_field)
                    field_candidate_id = _stable_id(
                        "api_database_field_candidate",
                        api_field_id,
                        database_field_id,
                    )
                    field_status = (
                        "TYPE_CONFLICT_REQUIRES_AUTHORITY"
                        if compatibility["status"] == "INCOMPATIBLE"
                        else "PENDING_ENTITY_AND_FIELD_AUTHORITY"
                        if len(matches) > 1
                        else "PENDING_FIELD_AUTHORITY"
                    )
                    field_candidate = {
                        "schema": API_DATABASE_FIELD_CANDIDATE_SCHEMA,
                        "candidate_id": field_candidate_id,
                        "entity_candidate_id": candidate_id,
                        "api_entity_id": entity_id,
                        "api_schema_id": schema_id,
                        "api_field_id": api_field_id,
                        "api_field_name": api_field_name,
                        "api_property_path": deepcopy(
                            _list(api_field.get("property_path"))
                        ),
                        "api_direction": _text(api_field.get("direction")),
                        "api_source_id": _text(api_field.get("source_id")),
                        "api_source_locator": _text(api_field.get("source_locator")),
                        "database_table_id": table_id,
                        "database_field_id": database_field_id,
                        "database_field_name": _text(database_field.get("field")),
                        "database_source_id": _text(database_field.get("source_id")),
                        "database_source_locator": _text(
                            database_field.get("source_locator")
                        ),
                        "type_compatibility": compatibility,
                        "status": field_status,
                        "operator_authority_required": True,
                        "automatic_mapping_allowed": False,
                        "exact_case_sensitive_field_name_match": True,
                        "field_name_match_is_supporting_not_identity_authority": True,
                        "comparison_scoped_by_entity_candidate": True,
                        "business_attribute_confirmed": False,
                    }
                    field_candidates.append(field_candidate)
                    relationships.append(
                        {
                            "edge_id": (
                                "edge:api-database-field-candidate:"
                                f"{field_candidate_id}"
                            ),
                            "from": api_field_id,
                            "to": database_field_id,
                            "relation": "api_field_database_column_alignment_candidate",
                            "confidence": (
                                0.6
                                if compatibility["status"] == "COMPATIBLE"
                                else 0.45
                            ),
                            "status": "pending_authority",
                            "derivation": "entity_scoped_exact_field_name_candidate",
                            "evidence": {
                                "candidate_id": field_candidate_id,
                                "entity_candidate_id": candidate_id,
                                "type_compatibility": deepcopy(compatibility),
                                "automatic_mapping_allowed": False,
                            },
                        }
                    )
                    if compatibility["status"] == "INCOMPATIBLE":
                        gaps.append(
                            {
                                "kind": "API_DATABASE_FIELD_TYPE_CONFLICT_CANDIDATE",
                                "gap_type": "api_database_field_type_conflict",
                                "candidate_id": field_candidate_id,
                                "api_field_id": api_field_id,
                                "database_field_id": database_field_id,
                                "api_declared_type": compatibility[
                                    "api_declared_type"
                                ],
                                "database_declared_type": compatibility[
                                    "database_declared_type"
                                ],
                                "operator_action": (
                                    "confirm transformation, DTO/storage separation, "
                                    "or correct the conflicting source declaration"
                                ),
                                "blocks_automatic_mapping": True,
                            }
                        )

        if len(matches) > 1:
            gaps.append(
                {
                    "kind": "API_SCHEMA_DATABASE_TABLE_ALIGNMENT_AMBIGUOUS",
                    "gap_type": "api_schema_database_table_ambiguous",
                    "candidate_id": candidate_id,
                    "api_entity_id": entity_id,
                    "api_schema_name": entity_name,
                    "candidate_table_ids": [
                        _text(row.get("table_id")) for row in matches
                    ],
                    "operator_action": (
                        "provide business-object, schema or source authority before mapping"
                    ),
                    "blocks_automatic_mapping": True,
                }
            )

    entity_candidates = _dedupe(entity_candidates, "candidate_id")
    field_candidates = _dedupe(field_candidates, "candidate_id")
    relationships = _dedupe(relationships, "edge_id")
    existing_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
    ]
    gaps = _dedupe(gaps, "candidate_id")

    result["api_database_entity_alignment_candidates"] = entity_candidates
    result["api_database_field_alignment_candidates"] = field_candidates
    result["relationships"] = relationships
    result["coverage_gaps"] = [*existing_gaps, *gaps]
    result["api_database_contract_alignment"] = {
        "schema": API_DATABASE_ALIGNMENT_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not api_entities or not database_tables
            else "PARTIAL"
            if gaps or entity_candidates
            else "NO_EXACT_CANDIDATES"
        ),
        "api_entity_count": len(api_entities),
        "api_field_count": len(api_fields),
        "database_model_table_count": len(database_tables),
        "database_model_field_count": len(database_fields),
        "entity_candidate_count": len(entity_candidates),
        "field_candidate_count": len(field_candidates),
        "ambiguous_entity_candidate_count": sum(
            1
            for row in entity_candidates
            if _text(row.get("status")) == "AMBIGUOUS_REQUIRES_AUTHORITY"
        ),
        "type_conflict_candidate_count": sum(
            1
            for row in field_candidates
            if _text(row.get("status")) == "TYPE_CONFLICT_REQUIRES_AUTHORITY"
        ),
        "automatic_entity_mapping_count": 0,
        "automatic_field_mapping_count": 0,
        "field_comparison_requires_entity_candidate": True,
        "exact_case_sensitive_names_only": True,
        "camel_snake_plural_inference_used": False,
        "type_compatibility_is_supporting_only": True,
        "business_semantics_inferred": False,
    }

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "api_database_entity_alignment_candidate_count": len(
                entity_candidates
            ),
            "api_database_field_alignment_candidate_count": len(field_candidates),
            "api_database_type_conflict_candidate_count": sum(
                1
                for row in field_candidates
                if _text(row.get("status")) == "TYPE_CONFLICT_REQUIRES_AUTHORITY"
            ),
        }
    )
    result["summary"] = summary

    governance = _dict(result.get("governance"))
    governance.update(
        {
            "api_database_alignment_never_auto_merges_by_name": True,
            "api_database_field_alignment_is_entity_scoped": True,
            "api_database_type_compatibility_is_not_identity_authority": True,
            "api_database_ambiguous_table_match_requires_authority": True,
            "api_database_business_object_confirmation_required": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "API_DATABASE_ALIGNMENT_SCHEMA",
    "API_DATABASE_ENTITY_CANDIDATE_SCHEMA",
    "API_DATABASE_FIELD_CANDIDATE_SCHEMA",
    "enrich_asset_with_api_database_alignment_candidates",
]
