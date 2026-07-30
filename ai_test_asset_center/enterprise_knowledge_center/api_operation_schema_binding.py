"""Bind exact OpenAPI operation schema references to source-scoped schema entities.

Only source-declared local ``$ref`` values with an exact method/path context are accepted.
The binding proves that an API operation uses an API schema; it does not prove a business
object or physical database mapping.
"""
from __future__ import annotations

import hashlib
from copy import deepcopy
from typing import Any, Iterable

API_OPERATION_SCHEMA_BINDING_SCHEMA = "qualibug.api-operation-schema-binding.v1"
API_OPERATION_SCHEMA_BINDING_RECEIPT_SCHEMA = (
    "qualibug.api-operation-schema-binding-receipt.v1"
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


def _method(value: Any) -> str:
    return _text(value).upper()


def _path(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "/"
    if "://" in raw:
        remainder = raw.split("://", 1)[1]
        raw = "/" + remainder.split("/", 1)[1] if "/" in remainder else "/"
    return raw.split("?", 1)[0] or "/"


def _interface_sources(interface: dict[str, Any]) -> set[str]:
    result = {
        _text(interface.get("source_id")),
        *[_text(value) for value in _list(interface.get("source_ids"))],
    }
    for raw in _list(interface.get("api_artifact_source_records")):
        if isinstance(raw, dict):
            result.add(_text(raw.get("source_id")))
    return result - {""}


def _component_schema_name(target_ref: str) -> str:
    prefix = "#/components/schemas/"
    if not target_ref.startswith(prefix):
        return ""
    token = target_ref[len(prefix) :]
    if not token or "/" in token:
        return ""
    return token.replace("~1", "/").replace("~0", "~")


def enrich_asset_with_api_operation_schema_bindings(
    asset: dict[str, Any],
) -> dict[str, Any]:
    """Attach accepted operation→API-schema edges only for exact OpenAPI refs."""
    result = dict(asset or {})
    interfaces = [
        deepcopy(row)
        for row in _list(result.get("interfaces"))
        if isinstance(row, dict)
        and _text(row.get("interface_id"))
        and _method(row.get("method"))
    ]
    entities = [
        deepcopy(row)
        for row in _list(result.get("openapi_schema_entities"))
        if isinstance(row, dict)
        and _text(row.get("entity_id"))
        and _text(row.get("source_id"))
        and _text(row.get("name"))
    ]
    references = [
        deepcopy(row)
        for row in _list(result.get("openapi_schema_references"))
        if isinstance(row, dict)
        and _text(row.get("reference_id"))
        and _text(row.get("source_id"))
        and _method(row.get("method"))
        and _text(row.get("api_path"))
    ]

    interfaces_by_signature: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for interface in interfaces:
        interfaces_by_signature.setdefault(
            (_method(interface.get("method")), _path(interface.get("path"))), []
        ).append(interface)

    entities_by_source_name: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for entity in entities:
        entities_by_source_name.setdefault(
            (_text(entity.get("source_id")), _text(entity.get("name"))), []
        ).append(entity)

    bindings: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    relationships = [
        deepcopy(row)
        for row in _list(result.get("relationships"))
        if isinstance(row, dict)
    ]

    for reference in references:
        source_id = _text(reference.get("source_id"))
        method = _method(reference.get("method"))
        api_path = _path(reference.get("api_path"))
        target_ref = _text(reference.get("target_ref") or reference.get("ref"))
        schema_name = _component_schema_name(target_ref)
        if not schema_name:
            continue

        interface_matches = [
            row
            for row in interfaces_by_signature.get((method, api_path), [])
            if source_id in _interface_sources(row)
        ]
        entity_matches = entities_by_source_name.get((source_id, schema_name), [])

        reference_id = _text(reference.get("reference_id"))
        if len(interface_matches) != 1:
            gap_id = _stable_id(
                "api_operation_schema_gap",
                reference_id,
                "interface",
                *sorted(_text(row.get("interface_id")) for row in interface_matches),
            )
            gaps.append(
                {
                    "gap_id": gap_id,
                    "kind": (
                        "OPENAPI_OPERATION_INTERFACE_NOT_FOUND"
                        if not interface_matches
                        else "OPENAPI_OPERATION_INTERFACE_AMBIGUOUS"
                    ),
                    "gap_type": "api_operation_schema_interface_resolution",
                    "reference_id": reference_id,
                    "source_id": source_id,
                    "method": method,
                    "path": api_path,
                    "interface_ids": sorted(
                        _text(row.get("interface_id")) for row in interface_matches
                    ),
                    "operator_action": (
                        "restore the source-local interface ledger or resolve duplicate "
                        "interface identities before schema binding"
                    ),
                    "blocks_operation_schema_binding": True,
                }
            )
            continue
        if len(entity_matches) != 1:
            gap_id = _stable_id(
                "api_operation_schema_gap",
                reference_id,
                "schema",
                *sorted(_text(row.get("entity_id")) for row in entity_matches),
            )
            gaps.append(
                {
                    "gap_id": gap_id,
                    "kind": (
                        "OPENAPI_REFERENCED_SCHEMA_ENTITY_NOT_FOUND"
                        if not entity_matches
                        else "OPENAPI_REFERENCED_SCHEMA_ENTITY_AMBIGUOUS"
                    ),
                    "gap_type": "api_operation_schema_entity_resolution",
                    "reference_id": reference_id,
                    "source_id": source_id,
                    "target_ref": target_ref,
                    "schema_name": schema_name,
                    "entity_ids": sorted(
                        _text(row.get("entity_id")) for row in entity_matches
                    ),
                    "operator_action": (
                        "repair the source-scoped OpenAPI schema projection before "
                        "operation binding"
                    ),
                    "blocks_operation_schema_binding": True,
                }
            )
            continue

        interface = interface_matches[0]
        entity = entity_matches[0]
        interface_id = _text(interface.get("interface_id"))
        entity_id = _text(entity.get("entity_id"))
        direction = _text(reference.get("direction")) or "schema"
        response_status = _text(reference.get("response_status"))
        media_type = _text(reference.get("media_type"))
        binding_id = _stable_id(
            "api_operation_schema_binding",
            source_id,
            interface_id,
            reference_id,
            entity_id,
            direction,
            response_status,
            media_type,
        )
        binding = {
            "schema": API_OPERATION_SCHEMA_BINDING_SCHEMA,
            "binding_id": binding_id,
            "interface_id": interface_id,
            "method": method,
            "path": api_path,
            "source_id": source_id,
            "reference_id": reference_id,
            "target_ref": target_ref,
            "api_schema_entity_id": entity_id,
            "api_schema_id": _text(entity.get("schema_id")),
            "api_schema_name": schema_name,
            "schema_field_ids": deepcopy(_list(entity.get("field_ids"))),
            "direction": direction,
            "context_kind": _text(reference.get("context_kind")),
            "response_status": response_status,
            "media_type": media_type,
            "json_pointer": _text(reference.get("json_pointer")),
            "source_locator": _text(reference.get("source_locator")),
            "evidence_address": deepcopy(_dict(reference.get("evidence_address"))),
            "status": "ACCEPTED_EXACT_OPENAPI_REFERENCE",
            "contract_authority": "OPENAPI_EXACT_SCHEMA_REFERENCE",
            "business_object_confirmed": False,
            "database_mapping_confirmed": False,
            "business_flow_inferred": False,
        }
        bindings.append(binding)
        relationships.append(
            {
                "edge_id": f"edge:api-operation-schema:{binding_id}",
                "from": interface_id,
                "to": entity_id,
                "relation": "api_operation_uses_schema",
                "confidence": 1.0,
                "status": "accepted",
                "derivation": "exact_openapi_local_schema_reference",
                "evidence": {
                    "binding_id": binding_id,
                    "source_id": source_id,
                    "reference_id": reference_id,
                    "target_ref": target_ref,
                    "direction": direction,
                    "response_status": response_status,
                    "media_type": media_type,
                    "json_pointer": _text(reference.get("json_pointer")),
                    "source_locator": _text(reference.get("source_locator")),
                },
            }
        )

    bindings = _dedupe(
        [*_list(result.get("api_operation_schema_bindings")), *bindings],
        "binding_id",
    )
    relationships = _dedupe(relationships, "edge_id")
    gaps = _dedupe(gaps, "gap_id")
    existing_gaps = [
        deepcopy(row)
        for row in _list(result.get("coverage_gaps"))
        if isinstance(row, dict)
    ]

    result["api_operation_schema_bindings"] = bindings
    result["relationships"] = relationships
    result["coverage_gaps"] = [*existing_gaps, *gaps]
    result["api_operation_schema_binding_receipt"] = {
        "schema": API_OPERATION_SCHEMA_BINDING_RECEIPT_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not references
            else "PARTIAL"
            if gaps
            else "COMPLETE"
        ),
        "operation_context_reference_count": len(references),
        "accepted_binding_count": len(bindings),
        "unresolved_binding_count": len(gaps),
        "exact_local_component_refs_only": True,
        "source_local_interface_resolution_required": True,
        "source_local_schema_resolution_required": True,
        "business_object_mapping_inferred": False,
        "database_mapping_inferred": False,
    }

    summary = _dict(result.get("summary"))
    summary.update(
        {
            "api_operation_schema_binding_count": len(bindings),
            "api_operation_schema_binding_gap_count": len(gaps),
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "api_operation_schema_binding_uses_exact_openapi_refs": True,
            "api_operation_schema_binding_is_source_scoped": True,
            "api_operation_schema_binding_does_not_confirm_business_objects": True,
            "api_operation_schema_binding_does_not_confirm_database_mapping": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "API_OPERATION_SCHEMA_BINDING_SCHEMA",
    "API_OPERATION_SCHEMA_BINDING_RECEIPT_SCHEMA",
    "enrich_asset_with_api_operation_schema_bindings",
]
