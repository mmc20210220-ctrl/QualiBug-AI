"""Field facts derived only from exact OpenAPI schema Document IR blocks.

The source JSON has already been sanitized and structurally parsed by the guarded adapter.
This module does not parse JSON again and does not invent behavior. It translates source-
declared schema/property blocks into stable field facts with request/response/component
context and exact evidence suitable for later business-rule linking.
"""
from __future__ import annotations

import hashlib
from typing import Any

OPENAPI_SCHEMA_FACT_PROJECTION_SCHEMA = "qualibug.openapi-schema-field-facts.v1"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _stable_id(prefix: str, *parts: Any) -> str:
    material = "\x1f".join(_text(value) for value in parts)
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _decode_pointer(pointer: str) -> list[str]:
    value = str(pointer or "")
    if value in {"", "/"}:
        return []
    return [
        token.replace("~1", "/").replace("~0", "~")
        for token in value.lstrip("/").split("/")
    ]


def _context(pointer: str) -> dict[str, Any]:
    tokens = _decode_pointer(pointer)
    result: dict[str, Any] = {
        "context_kind": "schema",
        "schema_name": "",
        "api_path": "",
        "method": "",
        "direction": "component",
        "response_status": "",
        "media_type": "",
        "parameter_index": None,
    }
    if len(tokens) >= 3 and tokens[:2] == ["components", "schemas"]:
        result["context_kind"] = "component_schema"
        result["schema_name"] = tokens[2]
        result["direction"] = "component"
        return result
    if len(tokens) < 2 or tokens[0] != "paths":
        return result

    result["api_path"] = tokens[1]
    if len(tokens) >= 4 and tokens[2] == "parameters":
        result["context_kind"] = "path_parameter_schema"
        result["direction"] = "parameter"
        result["parameter_index"] = int(tokens[3]) if tokens[3].isdigit() else None
        return result
    if len(tokens) < 3 or tokens[2].lower() not in _HTTP_METHODS:
        result["context_kind"] = "path_schema"
        return result

    result["method"] = tokens[2].upper()
    tail = tokens[3:]
    if "requestBody" in tail:
        result["context_kind"] = "request_schema"
        result["direction"] = "request"
        if "content" in tail:
            index = tail.index("content")
            if index + 1 < len(tail):
                result["media_type"] = tail[index + 1]
    elif "responses" in tail:
        result["context_kind"] = "response_schema"
        result["direction"] = "response"
        index = tail.index("responses")
        if index + 1 < len(tail):
            result["response_status"] = tail[index + 1]
        if "content" in tail:
            content_index = tail.index("content")
            if content_index + 1 < len(tail):
                result["media_type"] = tail[content_index + 1]
    elif "parameters" in tail:
        result["context_kind"] = "operation_parameter_schema"
        result["direction"] = "parameter"
        index = tail.index("parameters")
        if index + 1 < len(tail) and tail[index + 1].isdigit():
            result["parameter_index"] = int(tail[index + 1])
    else:
        result["context_kind"] = "operation_schema"
    return result


def _evidence(block: dict[str, Any]) -> dict[str, Any]:
    address = _dict(block.get("evidence_address"))
    locator = _text(block.get("source_locator") or address.get("source_locator"))
    address_kind = _text(address.get("address_kind"))
    exact = address_kind in {
        "EXACT_SOURCE_LOCATOR",
        "PAGE_BBOX",
        "SPREADSHEET_CELL",
        "PRESENTATION_SHAPE",
    } or "#block=" in locator
    return {
        "block_id": _text(block.get("block_id")),
        "source_id": _text(block.get("source_id") or address.get("source_id")),
        "source_hash": _text(block.get("source_hash") or address.get("source_hash")),
        "source_filename": _text(block.get("source_filename") or address.get("filename")),
        "source_locator": locator,
        "json_pointer": _text(block.get("json_pointer") or address.get("json_pointer")),
        "address_kind": address_kind or ("EXACT_SOURCE_LOCATOR" if exact else "SOURCE_LOCATOR"),
        "exact": exact,
    }


def project_openapi_schema_facts(
    document_ir: dict[str, Any],
    *,
    source_id: str,
    source_type: str = "",
) -> dict[str, Any]:
    """Project schema definitions, fields and references from exact Document IR blocks."""

    definitions: list[dict[str, Any]] = []
    fields: list[dict[str, Any]] = []
    references: list[dict[str, Any]] = []
    exact_evidence_count = 0
    evidence_count = 0

    projection_receipt = _dict(document_ir.get("openapi_schema_projection_receipt"))
    reference_status = {
        _text(row.get("source_pointer")): dict(row)
        for row in projection_receipt.get("references") or []
        if isinstance(row, dict) and _text(row.get("source_pointer"))
    }

    for raw in document_ir.get("blocks") or []:
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        node_kind = _text(block.get("node_kind"))
        if node_kind not in {"OPENAPI_SCHEMA", "OPENAPI_SCHEMA_PROPERTY"}:
            continue
        pointer = _text(block.get("json_pointer"))
        evidence = _evidence(block)
        evidence_count += 1
        exact_evidence_count += 1 if evidence["exact"] else 0
        context = _context(pointer)
        common = {
            "source_id": source_id,
            "source_type": source_type,
            "json_pointer": pointer,
            "schema_type": _text(block.get("schema_type")),
            "schema_format": _text(block.get("schema_format")),
            "description": _text(block.get("description")),
            "ref": _text(block.get("ref")),
            "nullable": bool(block.get("nullable")),
            "read_only": bool(block.get("read_only")),
            "write_only": bool(block.get("write_only")),
            "deprecated": bool(block.get("deprecated")),
            "constraints": _dict(block.get("constraints")),
            "evidence": evidence,
            **context,
            "business_semantics_inferred": False,
            "derivation": "openapi_schema_document_ir_block",
        }
        if node_kind == "OPENAPI_SCHEMA":
            definition = {
                "schema_definition_id": _stable_id(
                    "openapi_schema_definition", source_id, pointer
                ),
                "schema_label": _text(block.get("schema_label")),
                **common,
            }
            definitions.append(definition)
        else:
            property_path = [
                _text(value) for value in _list(block.get("property_path")) if _text(value)
            ]
            field_name = _text(block.get("property_name")) or (
                property_path[-1] if property_path else ""
            )
            field = {
                "schema_field_id": _stable_id(
                    "openapi_schema_field", source_id, pointer
                ),
                "field_name": field_name,
                "field_path": property_path,
                "field_path_text": ".".join(property_path),
                "required": bool(block.get("required")),
                "enum_values": list(_dict(block.get("constraints")).get("enum") or []),
                **common,
            }
            fields.append(field)
        ref = _text(block.get("ref"))
        if ref:
            status = reference_status.get(pointer, {})
            references.append(
                {
                    "schema_reference_id": _stable_id(
                        "openapi_schema_reference", source_id, pointer, ref
                    ),
                    "source_id": source_id,
                    "source_pointer": pointer,
                    "ref": ref,
                    "local": bool(status.get("local", ref.startswith("#/"))),
                    "resolved": bool(status.get("resolved")),
                    "evidence": evidence,
                    **context,
                    "business_semantics_inferred": False,
                }
            )

    return {
        "schema": OPENAPI_SCHEMA_FACT_PROJECTION_SCHEMA,
        "source_id": source_id,
        "source_type": source_type,
        "schema_definitions": definitions,
        "schema_fields": fields,
        "schema_references": references,
        "schema_definition_count": len(definitions),
        "schema_field_count": len(fields),
        "required_field_count": sum(1 for row in fields if row["required"]),
        "enum_field_count": sum(1 for row in fields if row["enum_values"]),
        "request_field_count": sum(1 for row in fields if row["direction"] == "request"),
        "response_field_count": sum(1 for row in fields if row["direction"] == "response"),
        "component_field_count": sum(1 for row in fields if row["direction"] == "component"),
        "parameter_field_count": sum(1 for row in fields if row["direction"] == "parameter"),
        "reference_count": len(references),
        "unresolved_reference_count": sum(1 for row in references if not row["resolved"]),
        "exact_evidence_rate": (
            round(exact_evidence_count / evidence_count, 4) if evidence_count else 1.0
        ),
        "container_reparse_performed": False,
        "source_declared_constraints_only": True,
        "business_semantics_inferred": False,
    }


__all__ = [
    "OPENAPI_SCHEMA_FACT_PROJECTION_SCHEMA",
    "project_openapi_schema_facts",
]
