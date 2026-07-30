"""Compose exact database-model facts into the canonical enterprise asset."""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable

from .document_ir_database_model_semantics import project_database_model_semantics

DATABASE_MODEL_ASSET_SCHEMA = "qualibug.database-model-asset-projection.v1"
DATABASE_MODEL_CONFLICT_SCHEMA = "qualibug.database-model-contract-conflict.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _artifact_kind(structure: dict[str, Any]) -> str:
    artifact = _dict(structure.get("artifact_structure"))
    receipt = _dict(structure.get("structure_receipt"))
    return _text(artifact.get("artifact_kind") or receipt.get("artifact_kind")).lower()


def _source_type_map(asset: dict[str, Any]) -> dict[str, str]:
    return {
        _text(row.get("source_id")): _text(row.get("source_type"))
        for row in _list(asset.get("source_inventory"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    }


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


def _merge_table(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(existing)
    row["columns"] = sorted(
        {_text(value) for value in _list(row.get("columns")) if _text(value)}
        | {_text(value) for value in _list(incoming.get("columns")) if _text(value)}
    )
    row["identity_fields"] = sorted(
        {_text(value) for value in _list(row.get("identity_fields")) if _text(value)}
        | {_text(value) for value in _list(incoming.get("identity_fields")) if _text(value)}
    )
    row["foreign_keys"] = sorted(
        {_text(value) for value in _list(row.get("foreign_keys")) if _text(value)}
        | {_text(value) for value in _list(incoming.get("foreign_keys")) if _text(value)}
    )
    row["field_dictionary"] = _dedupe(
        [*_list(row.get("field_dictionary")), *_list(incoming.get("field_dictionary"))],
        "field_id",
    )
    row["database_model_declarations"] = _dedupe(
        [
            *_list(row.get("database_model_declarations")),
            *_list(incoming.get("database_model_declarations")),
        ],
        "declaration_id",
    )
    row["source_refs"] = sorted(
        {
            *[_text(value) for value in _list(row.get("source_refs")) if _text(value)],
            _text(incoming.get("source_id")),
        }
        - {""}
    )
    if not _text(row.get("source_locator")) and _text(incoming.get("source_locator")):
        row["source_locator"] = incoming.get("source_locator")
        row["evidence_address"] = deepcopy(_dict(incoming.get("evidence_address")))
    for field in ("schema_name", "qualified_name", "table_kind", "description"):
        if not _text(row.get(field)) and incoming.get(field) not in (None, ""):
            row[field] = deepcopy(incoming.get(field))
    row["database_model_source_count"] = len(row["database_model_declarations"])
    return row


def _merge_tables(existing: Iterable[Any], projected: Iterable[Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [deepcopy(row) for row in existing if isinstance(row, dict)]
    by_id = {_text(row.get("table_id")): row for row in rows if _text(row.get("table_id"))}
    for raw in projected:
        if not isinstance(raw, dict):
            continue
        incoming = deepcopy(raw)
        table_id = _text(incoming.get("table_id"))
        if not table_id:
            continue
        current = by_id.get(table_id)
        if current is None:
            incoming["source_refs"] = sorted({_text(incoming.get("source_id"))} - {""})
            incoming["database_model_source_count"] = len(_list(incoming.get("database_model_declarations")))
            rows.append(incoming)
            by_id[table_id] = incoming
            continue
        merged = _merge_table(current, incoming)
        current.clear()
        current.update(merged)
    return rows


def _contract_projection(declaration: dict[str, Any]) -> dict[str, Any]:
    return {
        "qualified_name": _text(declaration.get("qualified_name")),
        "table_kind": _text(declaration.get("table_kind")),
        "columns": sorted(_text(value) for value in _list(declaration.get("columns")) if _text(value)),
        "identity_fields": sorted(_text(value) for value in _list(declaration.get("identity_fields")) if _text(value)),
        "foreign_keys": sorted(_text(value) for value in _list(declaration.get("foreign_keys")) if _text(value)),
        "indexes": sorted(
            [
                {
                    "name": _text(row.get("name")),
                    "columns": sorted(_text(value) for value in _list(row.get("columns")) if _text(value)),
                    "unique": bool(row.get("unique")),
                    "partial": bool(row.get("partial")),
                }
                for row in _list(declaration.get("indexes"))
                if isinstance(row, dict)
            ],
            key=lambda row: (row["name"], tuple(row["columns"]), row["unique"], row["partial"]),
        ),
    }


def _contract_fingerprint(declaration: dict[str, Any]) -> str:
    payload = json.dumps(_contract_projection(declaration), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _conflict_candidates(tables: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    conflicts: list[dict[str, Any]] = []
    for table in tables:
        declarations = [deepcopy(row) for row in _list(table.get("database_model_declarations")) if isinstance(row, dict)]
        if len(declarations) < 2:
            continue
        by_fingerprint: dict[str, list[dict[str, Any]]] = {}
        for declaration in declarations:
            fingerprint = _contract_fingerprint(declaration)
            by_fingerprint.setdefault(fingerprint, []).append(declaration)
        if len(by_fingerprint) <= 1:
            continue
        table_id = _text(table.get("table_id"))
        conflicts.append(
            {
                "schema": DATABASE_MODEL_CONFLICT_SCHEMA,
                "conflict_id": _stable_id("database_model_conflict", table_id, *sorted(by_fingerprint)),
                "table_id": table_id,
                "qualified_name": _text(table.get("qualified_name") or table.get("name")),
                "status": "UNRESOLVED",
                "reason_code": "DATABASE_MODEL_SOURCE_DECLARATIONS_DISAGREE",
                "source_ids": sorted({_text(row.get("source_id")) for row in declarations if _text(row.get("source_id"))}),
                "variants": [
                    {
                        "contract_fingerprint": fingerprint,
                        "source_ids": sorted({_text(row.get("source_id")) for row in rows if _text(row.get("source_id"))}),
                        "contract": _contract_projection(rows[0]),
                        "evidence": [
                            {"source_id": _text(row.get("source_id")), "source_locator": _text(row.get("source_locator"))}
                            for row in rows
                        ],
                    }
                    for fingerprint, rows in sorted(by_fingerprint.items())
                ],
                "automatic_winner_selected": False,
                "operator_authority_required": True,
            }
        )
    return conflicts


def enrich_asset_with_database_model_facts(
    asset: dict[str, Any],
    structured_sources: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Attach exact database facts before enterprise understanding consumes tables."""
    result = dict(asset or {})
    source_types = _source_type_map(result)
    projected_tables: list[dict[str, Any]] = []
    projected_fields: list[dict[str, Any]] = []
    relationships: list[dict[str, Any]] = []
    indexes: list[dict[str, Any]] = []
    source_records: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []

    for raw_source in structured_sources:
        if not isinstance(raw_source, dict):
            continue
        source = dict(raw_source)
        source_id = _text(source.get("source_id"))
        structure = _dict(source.get("document_structure"))
        if not source_id or _artifact_kind(structure) != "database_model":
            continue
        projection = project_database_model_semantics(
            structure,
            source_id=source_id,
            source_type=source_types.get(source_id, "database_schema") or "database_schema",
        )
        projected_tables.extend(deepcopy(row) for row in _list(projection.get("tables")) if isinstance(row, dict))
        projected_fields.extend(deepcopy(row) for row in _list(projection.get("field_dictionary")) if isinstance(row, dict))
        relationships.extend(deepcopy(row) for row in _list(projection.get("relationships")) if isinstance(row, dict))
        indexes.extend(deepcopy(row) for row in _list(projection.get("indexes")) if isinstance(row, dict))
        source_records.append(
            {
                "schema": "qualibug.database-model-source-record.v1",
                "source_id": source_id,
                "source_type": source_types.get(source_id, "database_schema") or "database_schema",
                **deepcopy(_dict(projection.get("database_model"))),
                "status": _text(projection.get("status")),
                "table_count": int(projection.get("table_count") or 0),
                "field_count": int(projection.get("field_count") or 0),
                "relationship_count": int(projection.get("relationship_count") or 0),
                "index_count": int(projection.get("index_count") or 0),
                "exact_fact_rate": float(projection.get("exact_fact_rate") or 0),
                "database_rows_read": int(projection.get("database_rows_read") or 0),
            }
        )
        receipts.append(
            {
                key: deepcopy(value)
                for key, value in projection.items()
                if key not in {"tables", "field_dictionary", "relationships", "indexes", "database_model"}
            }
        )

    tables = _merge_tables(result.get("tables") or [], projected_tables)
    fields = _dedupe([*_list(result.get("field_dictionary")), *projected_fields], "field_id")
    relationships = _dedupe([*_list(result.get("database_model_relationships")), *relationships], "relationship_id")
    indexes = _dedupe([*_list(result.get("database_model_indexes")), *indexes], "index_id")
    source_records = _dedupe([*_list(result.get("database_model_sources")), *source_records], "source_id")
    conflicts = _conflict_candidates(tables)

    edges = [deepcopy(row) for row in _list(result.get("relationships")) if isinstance(row, dict)]
    for relationship in relationships:
        relationship_id = _text(relationship.get("relationship_id"))
        edges.append(
            {
                "edge_id": f"edge:database-foreign-key:{relationship_id}",
                "from": _text(relationship.get("child_table_id")),
                "to": _text(relationship.get("parent_table_id")),
                "relation": "database_foreign_key",
                "confidence": 1.0,
                "status": "accepted",
                "derivation": "database_model_source_declaration",
                "evidence": {
                    "source_id": _text(relationship.get("source_id")),
                    "source_locator": _text(relationship.get("source_locator")),
                    "child_columns": deepcopy(_list(relationship.get("child_columns"))),
                    "parent_columns": deepcopy(_list(relationship.get("parent_columns"))),
                    "delete_rule": _text(relationship.get("delete_rule")),
                    "update_rule": _text(relationship.get("update_rule")),
                },
            }
        )
    edges = _dedupe(edges, "edge_id")

    blocked_source_count = sum(1 for row in source_records if _text(row.get("status")) == "BLOCKED")
    status = "NOT_APPLICABLE" if not source_records else "PARTIAL" if blocked_source_count or conflicts else "COMPLETE"
    exact_fact_count = sum(
        1
        for row in [*projected_tables, *projected_fields, *relationships, *indexes]
        if _text(row.get("source_locator"))
    )
    total_fact_count = len(projected_tables) + len(projected_fields) + len(relationships) + len(indexes)

    result["tables"] = tables
    result["field_dictionary"] = fields
    result["relationships"] = edges
    result["database_model_sources"] = source_records
    result["database_model_relationships"] = relationships
    result["database_model_indexes"] = indexes
    result["database_model_conflicts"] = conflicts
    result["database_model_fact_projection"] = {
        "schema": DATABASE_MODEL_ASSET_SCHEMA,
        "status": status,
        "processed_source_count": len(source_records),
        "processed_source_ids": [_text(row.get("source_id")) for row in source_records],
        "receipt_count": len(receipts),
        "receipts": receipts,
        "projected_table_count": len(projected_tables),
        "projected_field_count": len(projected_fields),
        "relationship_count": len(relationships),
        "index_count": len(indexes),
        "foreign_key_edge_count": sum(1 for row in edges if _text(row.get("relation")) == "database_foreign_key"),
        "conflict_count": len(conflicts),
        "blocked_source_count": blocked_source_count,
        "exact_fact_rate": round(exact_fact_count / total_fact_count, 4) if total_fact_count else 1.0,
        "source_scoped_declaration_ledger": True,
        "automatic_conflict_winner_selected": False,
        "database_rows_read": 0,
        "business_flow_inferred": False,
    }

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "database_model_source_count": len(source_records),
            "database_model_table_count": len(projected_tables),
            "database_model_field_count": len(projected_fields),
            "database_model_relationship_count": len(relationships),
            "database_model_index_count": len(indexes),
            "database_model_conflict_count": len(conflicts),
        }
    )
    result["summary"] = summary

    governance = _dict(result.get("governance"))
    governance.update(
        {
            "database_model_facts_use_document_ir": bool(source_records),
            "database_model_rows_are_never_read": True,
            "database_model_document_order_is_not_business_flow": True,
            "database_model_source_declarations_are_preserved": True,
            "database_model_conflicts_require_authority": True,
            "database_model_projection_precedes_enterprise_understanding": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "DATABASE_MODEL_ASSET_SCHEMA",
    "DATABASE_MODEL_CONFLICT_SCHEMA",
    "enrich_asset_with_database_model_facts",
]
