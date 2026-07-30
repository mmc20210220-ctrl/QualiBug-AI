"""Normalize exact OpenAPI schema Document IR facts into canonical API assets."""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .openapi_schema_fact_projection import project_openapi_schema_facts

OPENAPI_SCHEMA_FACT_ASSET_SCHEMA = "qualibug.openapi-schema-fact-asset.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _artifact_kind(structure: dict[str, Any]) -> str:
    return _text(
        _dict(structure.get("artifact_structure")).get("artifact_kind")
        or _dict(structure.get("structure_receipt")).get("artifact_kind")
    ).lower()


def _dedupe(rows: Iterable[Any], identity_field: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        row = deepcopy(raw)
        identity = _text(row.get(identity_field))
        if identity and identity not in seen:
            seen.add(identity)
            result.append(row)
    return result


def _evidence(raw: dict[str, Any]) -> dict[str, Any]:
    evidence = _dict(raw.get("evidence") or raw.get("evidence_address"))
    locator = _text(raw.get("source_locator") or evidence.get("source_locator"))
    pointer = _text(
        raw.get("json_pointer")
        or raw.get("source_pointer")
        or evidence.get("json_pointer")
    )
    return {
        **deepcopy(evidence),
        "source_locator": locator,
        "json_pointer": pointer,
        "exact": bool(evidence.get("exact") or (locator and pointer)),
    }


def _definition_name(raw: dict[str, Any]) -> str:
    name = _text(raw.get("name") or raw.get("schema_name") or raw.get("schema_label"))
    return name.removeprefix("schema ").strip()


def _normalize_definition(raw: dict[str, Any]) -> dict[str, Any]:
    evidence = _evidence(raw)
    schema_id = _text(raw.get("schema_id") or raw.get("schema_definition_id"))
    return {
        **deepcopy(raw),
        "schema_id": schema_id,
        "schema_definition_id": schema_id,
        "name": _definition_name(raw),
        "type": _text(raw.get("type") or raw.get("schema_type")),
        "format": _text(raw.get("format") or raw.get("schema_format")),
        "json_pointer": _text(raw.get("json_pointer") or evidence.get("json_pointer")),
        "source_locator": _text(
            raw.get("source_locator") or evidence.get("source_locator")
        ),
        "evidence_address": evidence,
    }


def _owner(raw: dict[str, Any], definitions: list[dict[str, Any]]) -> str:
    explicit = _text(raw.get("schema_id"))
    if explicit:
        return explicit
    source_id = _text(raw.get("source_id"))
    schema_name = _text(raw.get("schema_name"))
    if schema_name:
        matches = [
            row
            for row in definitions
            if _text(row.get("source_id")) == source_id
            and _text(row.get("name")) == schema_name
        ]
        if len(matches) == 1:
            return _text(matches[0].get("schema_id"))
    pointer = _text(
        raw.get("json_pointer")
        or raw.get("source_pointer")
        or _evidence(raw).get("json_pointer")
    )
    matches = [
        row
        for row in definitions
        if _text(row.get("source_id")) == source_id
        and _text(row.get("json_pointer"))
        and (
            pointer == _text(row.get("json_pointer"))
            or pointer.startswith(_text(row.get("json_pointer")).rstrip("/") + "/")
        )
    ]
    matches.sort(key=lambda row: len(_text(row.get("json_pointer"))), reverse=True)
    return _text(matches[0].get("schema_id")) if matches else ""


def _property_path(raw: dict[str, Any]) -> list[str]:
    path = [
        _text(value)
        for value in _list(raw.get("property_path") or raw.get("field_path"))
        if _text(value)
    ]
    schema_name = _text(raw.get("schema_name"))
    if schema_name and path and path[0] == schema_name:
        return path[1:]
    return path


def _normalize_field(
    raw: dict[str, Any], definitions: list[dict[str, Any]]
) -> dict[str, Any]:
    evidence = _evidence(raw)
    field_id = _text(raw.get("field_fact_id") or raw.get("schema_field_id"))
    path = _property_path(raw)
    name = _text(raw.get("field_name") or raw.get("name")) or (
        path[-1] if path else ""
    )
    return {
        **deepcopy(raw),
        "field_fact_id": field_id,
        "schema_field_id": field_id,
        "schema_id": _owner(raw, definitions),
        "name": name,
        "field_name": name,
        "property_path": path,
        "field_path": path,
        "field_path_text": ".".join(path),
        "type": _text(raw.get("type") or raw.get("schema_type")),
        "format": _text(raw.get("format") or raw.get("schema_format")),
        "json_pointer": _text(raw.get("json_pointer") or evidence.get("json_pointer")),
        "source_locator": _text(
            raw.get("source_locator") or evidence.get("source_locator")
        ),
        "evidence_address": evidence,
    }


def _normalize_reference(
    raw: dict[str, Any], definitions: list[dict[str, Any]]
) -> dict[str, Any]:
    evidence = _evidence(raw)
    reference_id = _text(
        raw.get("reference_id")
        or raw.get("reference_fact_id")
        or raw.get("schema_reference_id")
    )
    target_ref = _text(raw.get("target_ref") or raw.get("ref"))
    resolved = bool(raw.get("resolved")) or _text(raw.get("resolution_status")) == "RESOLVED"
    local = bool(raw.get("local", target_ref.startswith("#/")))
    reason = _text(raw.get("unresolved_reason"))
    if local and not resolved and not reason:
        reason = "OPENAPI_LOCAL_REF_TARGET_NOT_FOUND"
    return {
        **deepcopy(raw),
        "reference_id": reference_id,
        "reference_fact_id": reference_id,
        "schema_reference_id": reference_id,
        "schema_id": _owner(raw, definitions),
        "target_ref": target_ref,
        "ref": target_ref,
        "local": local,
        "resolved": resolved,
        "resolution_status": "RESOLVED" if resolved else "UNRESOLVED",
        "unresolved_reason": reason,
        "json_pointer": _text(
            raw.get("json_pointer")
            or raw.get("source_pointer")
            or evidence.get("json_pointer")
        ),
        "source_locator": _text(
            raw.get("source_locator") or evidence.get("source_locator")
        ),
        "evidence_address": evidence,
    }


def _entities(
    definitions: list[dict[str, Any]],
    fields: list[dict[str, Any]],
    references: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    fields_by_schema: dict[str, list[dict[str, Any]]] = {}
    refs_by_schema: dict[str, list[dict[str, Any]]] = {}
    for field in fields:
        fields_by_schema.setdefault(_text(field.get("schema_id")), []).append(field)
    for reference in references:
        refs_by_schema.setdefault(_text(reference.get("schema_id")), []).append(reference)
    result: list[dict[str, Any]] = []
    for definition in definitions:
        schema_id = _text(definition.get("schema_id"))
        owned_fields = fields_by_schema.get(schema_id, [])
        owned_refs = refs_by_schema.get(schema_id, [])
        result.append(
            {
                "schema": "qualibug.openapi-schema-entity.v1",
                "entity_id": f"api_schema_entity:{schema_id}",
                "schema_id": schema_id,
                "source_id": _text(definition.get("source_id")),
                "source_type": _text(definition.get("source_type")),
                "name": _text(definition.get("name")),
                "logical_schema_name": _text(definition.get("name")),
                "declared_type": _text(definition.get("type")),
                "context_kind": _text(definition.get("context_kind")),
                "field_ids": [
                    _text(row.get("field_fact_id"))
                    for row in owned_fields
                    if _text(row.get("field_fact_id"))
                ],
                "reference_ids": [
                    _text(row.get("reference_id"))
                    for row in owned_refs
                    if _text(row.get("reference_id"))
                ],
                "field_count": len(owned_fields),
                "reference_count": len(owned_refs),
                "json_pointer": _text(definition.get("json_pointer")),
                "source_locator": _text(definition.get("source_locator")),
                "evidence_address": deepcopy(
                    _dict(definition.get("evidence_address"))
                ),
                "source_traceability": "EXACT_JSON_POINTER",
                "contract_authority": "OPENAPI_SOURCE_DECLARATION",
                "database_table": False,
                "business_object_confirmed": False,
                "business_flow_inferred": False,
            }
        )
    return result


def enrich_asset_with_openapi_schema_facts(
    asset: dict[str, Any], structured_sources: Iterable[dict[str, Any]]
) -> dict[str, Any]:
    """Attach normalized, source-scoped OpenAPI schema facts."""
    result = dict(asset or {})
    source_types = {
        _text(row.get("source_id")): _text(row.get("source_type"))
        for row in _list(result.get("source_inventory"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    }
    definitions: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    processed: list[str] = []

    for raw_source in structured_sources:
        if not isinstance(raw_source, dict):
            continue
        source_id = _text(raw_source.get("source_id"))
        structure = _dict(raw_source.get("document_structure"))
        if not source_id or _artifact_kind(structure) != "openapi":
            continue
        projection = project_openapi_schema_facts(
            structure,
            source_id=source_id,
            source_type=source_types.get(source_id, "openapi") or "openapi",
        )
        source_definitions = [
            _normalize_definition(row)
            for row in _list(projection.get("schema_definitions"))
            if isinstance(row, dict)
        ]
        definitions.extend(source_definitions)
        fields.extend(
            _normalize_field(row, source_definitions)
            for row in _list(projection.get("schema_fields"))
            if isinstance(row, dict)
        )
        references.extend(
            _normalize_reference(row, source_definitions)
            for row in _list(projection.get("schema_references"))
            if isinstance(row, dict)
        )
        receipts.append(
            {
                key: deepcopy(value)
                for key, value in projection.items()
                if key not in {
                    "schema_definitions",
                    "schema_fields",
                    "schema_references",
                }
            }
        )
        processed.append(source_id)

    definitions = _dedupe(
        [*_list(result.get("openapi_schema_definitions")), *definitions], "schema_id"
    )
    fields = _dedupe(
        [*_list(result.get("openapi_schema_fields")), *fields], "field_fact_id"
    )
    references = _dedupe(
        [*_list(result.get("openapi_schema_references")), *references], "reference_id"
    )
    entities = _entities(definitions, fields, references)
    unresolved = sum(
        1 for row in references if _text(row.get("resolution_status")) != "RESOLVED"
    )
    all_facts = [*definitions, *fields, *references]
    exact = sum(
        1
        for row in all_facts
        if _text(row.get("json_pointer")) and _text(row.get("source_locator"))
    )

    result["openapi_schema_definitions"] = definitions
    result["openapi_schema_fields"] = fields
    result["openapi_schema_references"] = references
    result["openapi_schema_entities"] = entities
    result["openapi_schema_fact_projection"] = {
        "schema": OPENAPI_SCHEMA_FACT_ASSET_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not processed
            else "PARTIAL"
            if unresolved
            else "COMPLETE"
        ),
        "processed_source_count": len(processed),
        "processed_source_ids": processed,
        "receipt_count": len(receipts),
        "receipts": receipts,
        "schema_definition_count": len(definitions),
        "schema_field_count": len(fields),
        "schema_reference_count": len(references),
        "schema_entity_count": len(entities),
        "unresolved_reference_count": unresolved,
        "unowned_field_count": sum(
            1 for row in fields if not _text(row.get("schema_id"))
        ),
        "exact_fact_count": exact,
        "exact_fact_rate": round(exact / len(all_facts), 4) if all_facts else 1.0,
        "source_scoped_identity": True,
        "same_name_cross_source_auto_merge": False,
        "database_table_projection_used": False,
        "container_parsing_performed": False,
        "business_flow_inferred": False,
    }

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "openapi_schema_definition_count": len(definitions),
            "openapi_schema_field_count": len(fields),
            "openapi_schema_reference_count": len(references),
            "openapi_schema_entity_count": len(entities),
            "openapi_schema_unresolved_reference_count": unresolved,
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "openapi_schema_facts_use_document_ir": bool(processed),
            "openapi_schema_facts_are_source_scoped": True,
            "openapi_schema_models_are_not_database_tables": True,
            "openapi_schema_same_name_cross_source_merge_requires_authority": True,
            "openapi_schema_unresolved_refs_are_fail_visible": True,
            "openapi_schema_business_object_confirmation_required": True,
            "openapi_schema_asset_contract_matches_current_projector": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "OPENAPI_SCHEMA_FACT_ASSET_SCHEMA",
    "enrich_asset_with_openapi_schema_facts",
]
