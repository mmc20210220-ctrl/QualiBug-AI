"""Exact source-structure projection for OpenAPI schemas.

This module enriches the one guarded API-artifact adapter. It does not parse another file
format and does not create business rules. Sanitized OpenAPI objects are projected into exact
JSON-Pointer-backed schema/property blocks so the downstream semantic layer can reason from
source-declared field constraints without treating the whole JSON blob as the only evidence.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Iterable

from .contract import DocumentSource

OPENAPI_SCHEMA_PROJECTION_SCHEMA = "qualibug.openapi-schema-projection.v1"
_MAX_SCHEMA_BLOCKS = 30_000
_MAX_SCHEMA_DEPTH = 32
_MAX_METADATA_TEXT = 2_000
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options", "trace"}
_SCHEMA_VARIANTS = ("allOf", "oneOf", "anyOf")
_CONSTRAINT_FIELDS = (
    "minLength",
    "maxLength",
    "minimum",
    "maximum",
    "exclusiveMinimum",
    "exclusiveMaximum",
    "multipleOf",
    "pattern",
    "minItems",
    "maxItems",
    "uniqueItems",
    "minProperties",
    "maxProperties",
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _pointer_token(value: Any) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _pointer(*parts: Any) -> str:
    return "/" + "/".join(_pointer_token(part) for part in parts) if parts else "/"


def _join_pointer(pointer: str, *parts: Any) -> str:
    base = str(pointer or "/")
    if base == "/":
        return _pointer(*parts)
    return base.rstrip("/") + "/" + "/".join(_pointer_token(part) for part in parts)


def exact_json_pointer_locator(filename: str, pointer: str) -> str:
    """Use the canonical exact-block marker understood by evidence closure."""

    return f"{filename or 'api-artifact.json'}#block=json-pointer:{pointer or '/'}"


def _stable_id(prefix: str, source_id: str, pointer: str, node_kind: str) -> str:
    material = "\x1f".join((source_id, pointer, node_kind))
    return f"{prefix}:{hashlib.sha256(material.encode('utf-8')).hexdigest()[:24]}"


def _bounded(value: Any, limit: int = _MAX_METADATA_TEXT) -> Any:
    if isinstance(value, str):
        return value[:limit]
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    if isinstance(value, list):
        return [_bounded(item, max(80, limit // max(1, len(value)))) for item in value[:100]]
    if isinstance(value, dict):
        return {
            str(key)[:160]: _bounded(item, max(80, limit // max(1, len(value))))
            for key, item in list(value.items())[:100]
        }
    return str(value)[:limit]


def _canonical_json(value: Any) -> str:
    return json.dumps(_bounded(value), ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _schema_type(schema: dict[str, Any]) -> str:
    declared = schema.get("type")
    if isinstance(declared, list):
        return "|".join(_text(item) for item in declared if _text(item))
    if _text(declared):
        return _text(declared)
    if isinstance(schema.get("properties"), dict):
        return "object"
    if isinstance(schema.get("items"), dict):
        return "array"
    if _text(schema.get("$ref")):
        return "ref"
    return "unspecified"


def _constraint_metadata(schema: dict[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name in _CONSTRAINT_FIELDS:
        if name in schema:
            result[name] = _bounded(schema.get(name))
    if "enum" in schema:
        result["enum"] = _bounded(_list(schema.get("enum")))
    if "const" in schema:
        result["const"] = _bounded(schema.get("const"))
    if "default" in schema:
        result["default"] = _bounded(schema.get("default"))
        result["default_present"] = True
    if "example" in schema:
        result["example_present"] = True
        result["example_hash"] = hashlib.sha256(
            _canonical_json(schema.get("example")).encode("utf-8")
        ).hexdigest()
    if "examples" in schema:
        result["examples_present"] = True
        result["examples_hash"] = hashlib.sha256(
            _canonical_json(schema.get("examples")).encode("utf-8")
        ).hexdigest()
    return result


def _schema_summary(
    *,
    label: str,
    schema: dict[str, Any],
    required: bool = False,
) -> str:
    bits = [label, f"type={_schema_type(schema)}"]
    if _text(schema.get("format")):
        bits.append(f"format={_text(schema.get('format'))}")
    if required:
        bits.append("required=true")
    for flag, rendered in (
        ("nullable", "nullable=true"),
        ("readOnly", "readOnly=true"),
        ("writeOnly", "writeOnly=true"),
        ("deprecated", "deprecated=true"),
    ):
        if schema.get(flag) is True:
            bits.append(rendered)
    if _text(schema.get("$ref")):
        bits.append(f"ref={_text(schema.get('$ref'))}")
    if isinstance(schema.get("enum"), list):
        bits.append(f"enum={_canonical_json(schema.get('enum'))}")
    for name in _CONSTRAINT_FIELDS:
        if name in schema:
            bits.append(f"{name}={_text(schema.get(name))}")
    return " ".join(bits)[:4_000]


def _block(
    source: DocumentSource,
    *,
    pointer: str,
    node_kind: str,
    block_type: str,
    order: int,
    text: str,
    parent_id: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    locator = exact_json_pointer_locator(source.filename, pointer)
    row: dict[str, Any] = {
        "block_id": _stable_id("openapi_schema_block", source.source_id, pointer, node_kind),
        "type": block_type,
        "parent_id": parent_id,
        "order": order,
        "region": "body",
        "text": text,
        "source_locator": locator,
        "json_pointer": pointer,
        "node_kind": node_kind,
        "excluded_from_plain_text_projection": True,
        "structure_evidence": {
            "method": "source_declared_openapi_schema_json_pointer",
            "artifact_structure_only": True,
            "business_semantics_added": False,
        },
        "evidence_address": {
            "address_kind": "EXACT_SOURCE_LOCATOR",
            "source_locator": locator,
            "json_pointer": pointer,
        },
    }
    if metadata:
        row.update(metadata)
    return row


@dataclass
class _ProjectionState:
    source: DocumentSource
    payload: dict[str, Any]
    blocks: list[dict[str, Any]]
    seen_pointers: set[str] = field(default_factory=set)
    referenced_uris: list[dict[str, Any]] = field(default_factory=list)
    gap_rows: list[dict[str, Any]] = field(default_factory=list)
    schema_count: int = 0
    property_count: int = 0
    variant_count: int = 0
    array_items_count: int = 0
    additional_properties_count: int = 0
    truncated: bool = False

    def append(self, row: dict[str, Any]) -> bool:
        if len(self.blocks) >= _MAX_SCHEMA_BLOCKS:
            self.truncated = True
            return False
        pointer = _text(row.get("json_pointer"))
        if pointer and pointer in self.seen_pointers:
            return True
        if pointer:
            self.seen_pointers.add(pointer)
        self.blocks.append(row)
        return True


def _resolve_local_pointer(payload: dict[str, Any], ref: str) -> bool:
    if not ref.startswith("#/"):
        return False
    current: Any = payload
    for token in ref[2:].split("/"):
        key = token.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return False
    return True


def _record_ref(state: _ProjectionState, pointer: str, ref: str) -> None:
    local = ref.startswith("#/")
    resolved = _resolve_local_pointer(state.payload, ref) if local else False
    state.referenced_uris.append(
        {
            "source_pointer": pointer,
            "ref": ref,
            "local": local,
            "resolved": resolved,
        }
    )
    if local and not resolved:
        state.gap_rows.append(
            {
                "kind": "OPENAPI_LOCAL_SCHEMA_REF_UNRESOLVED",
                "reason_code": "OPENAPI_LOCAL_SCHEMA_REF_UNRESOLVED",
                "count": 1,
                "status": "LOCAL_SCHEMA_REFERENCE_TARGET_MISSING",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": exact_json_pointer_locator(state.source.filename, pointer),
                "json_pointer": pointer,
                "ref": ref,
            }
        )
    elif not local:
        state.gap_rows.append(
            {
                "kind": "OPENAPI_EXTERNAL_SCHEMA_REF_NOT_RESOLVED",
                "reason_code": "OPENAPI_EXTERNAL_SCHEMA_REF_NOT_RESOLVED",
                "count": 1,
                "status": "EXTERNAL_SCHEMA_REFERENCE_REQUIRES_CONNECTED_SOURCE",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": exact_json_pointer_locator(state.source.filename, pointer),
                "json_pointer": pointer,
                "ref": ref,
            }
        )


def _walk_schema(
    state: _ProjectionState,
    schema: dict[str, Any],
    *,
    pointer: str,
    label: str,
    parent_id: str = "",
    required: bool = False,
    property_path: tuple[str, ...] = (),
    depth: int = 0,
    root: bool = False,
) -> None:
    if depth > _MAX_SCHEMA_DEPTH:
        state.gap_rows.append(
            {
                "kind": "OPENAPI_SCHEMA_DEPTH_LIMIT_EXCEEDED",
                "reason_code": "OPENAPI_SCHEMA_DEPTH_LIMIT_EXCEEDED",
                "count": 1,
                "status": "SCHEMA_TREE_NOT_FULLY_PROJECTED",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": exact_json_pointer_locator(state.source.filename, pointer),
                "json_pointer": pointer,
                "depth_limit": _MAX_SCHEMA_DEPTH,
            }
        )
        return
    if pointer in state.seen_pointers:
        return

    ref = _text(schema.get("$ref"))
    node_kind = "OPENAPI_SCHEMA" if root else "OPENAPI_SCHEMA_PROPERTY"
    block_type = "HEADING" if root else "KEY_VALUE"
    metadata = {
        "schema_label": label,
        "schema_type": _schema_type(schema),
        "schema_format": _text(schema.get("format")),
        "property_name": property_path[-1] if property_path else "",
        "property_path": list(property_path),
        "required": bool(required),
        "nullable": bool(schema.get("nullable")),
        "read_only": bool(schema.get("readOnly")),
        "write_only": bool(schema.get("writeOnly")),
        "deprecated": bool(schema.get("deprecated")),
        "description": _text(schema.get("description"))[:4_000],
        "ref": ref,
        "constraints": _constraint_metadata(schema),
        "business_semantics_added": False,
    }
    row = _block(
        state.source,
        pointer=pointer,
        node_kind=node_kind,
        block_type=block_type,
        order=len(state.blocks) + 1,
        text=_schema_summary(label=label, schema=schema, required=required),
        parent_id=parent_id,
        metadata=metadata,
    )
    if not state.append(row):
        return
    state.schema_count += 1 if root else 0
    state.property_count += 0 if root else 1
    current_parent = row["block_id"]
    if ref:
        _record_ref(state, pointer, ref)

    required_names = {_text(item) for item in _list(schema.get("required")) if _text(item)}
    for property_name, raw_property in _dict(schema.get("properties")).items():
        property_schema = _dict(raw_property)
        property_pointer = _join_pointer(pointer, "properties", property_name)
        _walk_schema(
            state,
            property_schema,
            pointer=property_pointer,
            label=f"property {property_name}",
            parent_id=current_parent,
            required=str(property_name) in required_names,
            property_path=(*property_path, str(property_name)),
            depth=depth + 1,
            root=False,
        )
        if state.truncated:
            return

    items = schema.get("items")
    if isinstance(items, dict):
        state.array_items_count += 1
        _walk_schema(
            state,
            dict(items),
            pointer=_join_pointer(pointer, "items"),
            label="array items",
            parent_id=current_parent,
            property_path=(*property_path, "[]"),
            depth=depth + 1,
            root=False,
        )

    for variant_name in _SCHEMA_VARIANTS:
        for index, raw_variant in enumerate(_list(schema.get(variant_name))):
            if not isinstance(raw_variant, dict):
                continue
            state.variant_count += 1
            _walk_schema(
                state,
                dict(raw_variant),
                pointer=_join_pointer(pointer, variant_name, index),
                label=f"{variant_name}[{index}]",
                parent_id=current_parent,
                property_path=property_path,
                depth=depth + 1,
                root=False,
            )

    additional = schema.get("additionalProperties")
    if isinstance(additional, dict):
        state.additional_properties_count += 1
        _walk_schema(
            state,
            dict(additional),
            pointer=_join_pointer(pointer, "additionalProperties"),
            label="additionalProperties",
            parent_id=current_parent,
            property_path=(*property_path, "*"),
            depth=depth + 1,
            root=False,
        )


def _iter_inline_schema_roots(payload: dict[str, Any]) -> Iterable[tuple[str, str, dict[str, Any]]]:
    paths = _dict(payload.get("paths"))
    for api_path, raw_path in paths.items():
        path_item = _dict(raw_path)
        for method, raw_operation in path_item.items():
            method_lower = str(method).lower()
            if method_lower not in _HTTP_METHODS or not isinstance(raw_operation, dict):
                continue
            operation = dict(raw_operation)
            for index, raw_parameter in enumerate(_list(operation.get("parameters"))):
                parameter = _dict(raw_parameter)
                schema = _dict(parameter.get("schema"))
                if schema:
                    yield (
                        _pointer("paths", api_path, method, "parameters", index, "schema"),
                        f"{method_lower.upper()} {api_path} parameter {_text(parameter.get('name')) or index}",
                        schema,
                    )
            request_body = _dict(operation.get("requestBody"))
            for media_type, raw_media in _dict(request_body.get("content")).items():
                schema = _dict(_dict(raw_media).get("schema"))
                if schema:
                    yield (
                        _pointer(
                            "paths",
                            api_path,
                            method,
                            "requestBody",
                            "content",
                            media_type,
                            "schema",
                        ),
                        f"{method_lower.upper()} {api_path} request {media_type}",
                        schema,
                    )
            for status, raw_response in _dict(operation.get("responses")).items():
                response = _dict(raw_response)
                for media_type, raw_media in _dict(response.get("content")).items():
                    schema = _dict(_dict(raw_media).get("schema"))
                    if schema:
                        yield (
                            _pointer(
                                "paths",
                                api_path,
                                method,
                                "responses",
                                status,
                                "content",
                                media_type,
                                "schema",
                            ),
                            f"{method_lower.upper()} {api_path} response {status} {media_type}",
                            schema,
                        )


def normalize_api_json_pointer_locators(
    document_ir: dict[str, Any],
    *,
    filename: str,
) -> dict[str, Any]:
    """Rebase every existing API block onto the canonical exact source-address syntax."""

    result = dict(document_ir or {})
    for collection_name in ("blocks", "sections", "tables", "pages"):
        rows: list[dict[str, Any]] = []
        for raw in result.get(collection_name) or []:
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            pointer = _text(row.get("json_pointer"))
            if pointer:
                locator = exact_json_pointer_locator(filename, pointer)
                row["source_locator"] = locator
                address = dict(row.get("evidence_address") or {})
                address.update(
                    {
                        "address_kind": "EXACT_SOURCE_LOCATOR",
                        "source_locator": locator,
                        "json_pointer": pointer,
                    }
                )
                row["evidence_address"] = address
            rows.append(row)
        if collection_name in result or rows:
            result[collection_name] = rows
    unsupported: list[dict[str, Any]] = []
    for raw in result.get("unsupported_content") or []:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        pointer = _text(row.get("json_pointer"))
        if pointer:
            row["source_locator"] = exact_json_pointer_locator(filename, pointer)
        unsupported.append(row)
    result["unsupported_content"] = unsupported
    return result


def apply_openapi_schema_projection(
    document_ir: dict[str, Any],
    *,
    payload: dict[str, Any],
    source: DocumentSource,
) -> dict[str, Any]:
    """Append exact schema/property blocks to an existing guarded OpenAPI Document IR."""

    result = normalize_api_json_pointer_locators(document_ir, filename=source.filename)
    blocks = [dict(row) for row in result.get("blocks") or [] if isinstance(row, dict)]
    state = _ProjectionState(
        source=source,
        payload=dict(payload or {}),
        blocks=blocks,
        seen_pointers={_text(row.get("json_pointer")) for row in blocks if _text(row.get("json_pointer"))},
    )

    for schema_name, raw_schema in _dict(_dict(payload.get("components")).get("schemas")).items():
        if not isinstance(raw_schema, dict):
            continue
        _walk_schema(
            state,
            dict(raw_schema),
            pointer=_pointer("components", "schemas", schema_name),
            label=f"schema {schema_name}",
            property_path=(),
            root=True,
        )
        if state.truncated:
            break

    if not state.truncated:
        for pointer, label, schema in _iter_inline_schema_roots(payload):
            _walk_schema(
                state,
                schema,
                pointer=pointer,
                label=label,
                root=True,
            )
            if state.truncated:
                break

    unsupported = [
        dict(row)
        for row in result.get("unsupported_content") or []
        if isinstance(row, dict)
    ]
    unsupported.extend(state.gap_rows)
    if state.truncated:
        unsupported.append(
            {
                "kind": "OPENAPI_SCHEMA_BLOCK_LIMIT_EXCEEDED",
                "reason_code": "OPENAPI_SCHEMA_BLOCK_LIMIT_EXCEEDED",
                "count": 1,
                "status": "SCHEMA_STRUCTURE_NOT_FULLY_PROJECTED",
                "severity": "P0",
                "blocks_formal_understanding": True,
                "included_in_plain_text_authority": False,
                "source_locator": exact_json_pointer_locator(source.filename, "/"),
                "block_limit": _MAX_SCHEMA_BLOCKS,
            }
        )

    critical = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if bool(row.get("blocks_formal_understanding"))
    )
    status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
    receipt = dict(result.get("structure_receipt") or {})
    receipt.update(
        {
            "status": status,
            "openapi_schema_projection": True,
            "openapi_schema_projection_schema": OPENAPI_SCHEMA_PROJECTION_SCHEMA,
            "openapi_schema_count": state.schema_count,
            "openapi_schema_property_count": state.property_count,
            "openapi_schema_variant_count": state.variant_count,
            "openapi_schema_array_items_count": state.array_items_count,
            "openapi_schema_additional_properties_count": state.additional_properties_count,
            "openapi_schema_reference_count": len(state.referenced_uris),
            "openapi_unresolved_reference_count": len(state.gap_rows),
            "openapi_schema_projection_truncated": state.truncated,
            "json_pointer_exact_locator_format": "#block=json-pointer:<pointer>",
            "unsupported_content": unsupported,
            "unsupported_content_count": sum(int(row.get("count") or 0) for row in unsupported),
            "critical_unsupported_content_count": critical,
        }
    )
    result.update(
        {
            "blocks": state.blocks,
            "unsupported_content": unsupported,
            "structure_receipt": receipt,
            "openapi_schema_projection_receipt": {
                "schema": OPENAPI_SCHEMA_PROJECTION_SCHEMA,
                "status": status,
                "schema_count": state.schema_count,
                "property_count": state.property_count,
                "variant_count": state.variant_count,
                "array_items_count": state.array_items_count,
                "additional_properties_count": state.additional_properties_count,
                "references": state.referenced_uris,
                "gap_count": len(state.gap_rows) + (1 if state.truncated else 0),
                "exact_json_pointer_addresses": True,
                "business_semantics_added": False,
            },
        }
    )
    return result


__all__ = [
    "OPENAPI_SCHEMA_PROJECTION_SCHEMA",
    "exact_json_pointer_locator",
    "normalize_api_json_pointer_locators",
    "apply_openapi_schema_projection",
]
