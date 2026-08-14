"""Path-level OpenAPI parameter schema projection using the canonical schema walker.

OpenAPI permits parameters directly under a path item. They are inherited by operations, but
their source evidence remains ``/paths/{path}/parameters/{index}/schema``. This module reuses
the one schema walker and only supplies the correct source roots; it is not another parser.
"""
from __future__ import annotations

from typing import Any

from .contract import DocumentSource
from .openapi_schema_projection import (
    OPENAPI_SCHEMA_PROJECTION_SCHEMA,
    _ProjectionState,
    _dict,
    _list,
    _pointer,
    _text,
    _walk_schema,
    normalize_api_json_pointer_locators,
)


def apply_openapi_path_parameter_schema_projection(
    document_ir: dict[str, Any],
    *,
    payload: dict[str, Any],
    source: DocumentSource,
) -> dict[str, Any]:
    """Append path-level parameter schemas at their exact source-declared pointers."""

    result = normalize_api_json_pointer_locators(document_ir, filename=source.filename)
    blocks = [dict(row) for row in result.get("blocks") or [] if isinstance(row, dict)]
    state = _ProjectionState(
        source=source,
        payload=dict(payload or {}),
        blocks=blocks,
        seen_pointers={
            _text(row.get("json_pointer"))
            for row in blocks
            if _text(row.get("json_pointer"))
        },
    )
    for api_path, raw_path in _dict(payload.get("paths")).items():
        path_item = _dict(raw_path)
        for index, raw_parameter in enumerate(_list(path_item.get("parameters"))):
            parameter = _dict(raw_parameter)
            schema = _dict(parameter.get("schema"))
            if not schema:
                continue
            name = _text(parameter.get("name")) or str(index)
            _walk_schema(
                state,
                schema,
                pointer=_pointer("paths", api_path, "parameters", index, "schema"),
                label=f"path {api_path} parameter {name}",
                root=True,
            )
            if state.truncated:
                break
        if state.truncated:
            break

    unsupported = [
        dict(row)
        for row in result.get("unsupported_content") or []
        if isinstance(row, dict)
    ]
    unsupported.extend(state.gap_rows)
    receipt = dict(result.get("structure_receipt") or {})
    previous_projection = dict(result.get("openapi_schema_projection_receipt") or {})
    previous_refs = [
        dict(row)
        for row in previous_projection.get("references") or []
        if isinstance(row, dict)
    ]
    previous_schema_count = int(receipt.get("openapi_schema_count") or 0)
    previous_property_count = int(receipt.get("openapi_schema_property_count") or 0)
    previous_variant_count = int(receipt.get("openapi_schema_variant_count") or 0)
    previous_items_count = int(receipt.get("openapi_schema_array_items_count") or 0)
    previous_additional_count = int(
        receipt.get("openapi_schema_additional_properties_count") or 0
    )
    critical = sum(
        int(row.get("count") or 0)
        for row in unsupported
        if bool(row.get("blocks_formal_understanding"))
    )
    status = "BLOCKED" if critical else "PARTIAL" if unsupported else "COMPLETE"
    receipt.update(
        {
            "status": status,
            "openapi_schema_projection": True,
            "openapi_schema_projection_schema": OPENAPI_SCHEMA_PROJECTION_SCHEMA,
            "openapi_path_parameter_schema_projection": True,
            "openapi_path_parameter_schema_count": state.schema_count,
            "path_level_parameter_schema_pointer_correctness": True,
            "openapi_schema_count": previous_schema_count + state.schema_count,
            "openapi_schema_property_count": previous_property_count + state.property_count,
            "openapi_schema_variant_count": previous_variant_count + state.variant_count,
            "openapi_schema_array_items_count": previous_items_count + state.array_items_count,
            "openapi_schema_additional_properties_count": (
                previous_additional_count + state.additional_properties_count
            ),
            "openapi_schema_reference_count": len(previous_refs) + len(state.referenced_uris),
            "openapi_unresolved_reference_count": sum(
                1
                for row in unsupported
                if str(row.get("reason_code") or "")
                in {
                    "OPENAPI_LOCAL_SCHEMA_REF_UNRESOLVED",
                    "OPENAPI_EXTERNAL_SCHEMA_REF_NOT_RESOLVED",
                }
            ),
            "unsupported_content": unsupported,
            "unsupported_content_count": sum(
                int(row.get("count") or 0) for row in unsupported
            ),
            "critical_unsupported_content_count": critical,
        }
    )
    result.update(
        {
            "blocks": state.blocks,
            "unsupported_content": unsupported,
            "structure_receipt": receipt,
            "openapi_schema_projection_receipt": {
                **previous_projection,
                "schema": OPENAPI_SCHEMA_PROJECTION_SCHEMA,
                "status": status,
                "schema_count": previous_schema_count + state.schema_count,
                "property_count": previous_property_count + state.property_count,
                "variant_count": previous_variant_count + state.variant_count,
                "array_items_count": previous_items_count + state.array_items_count,
                "additional_properties_count": (
                    previous_additional_count + state.additional_properties_count
                ),
                "references": [*previous_refs, *state.referenced_uris],
                "gap_count": sum(
                    int(row.get("count") or 0)
                    for row in unsupported
                    if str(row.get("reason_code") or "").startswith("OPENAPI_")
                ),
                "path_level_parameter_schema_count": state.schema_count,
                "exact_json_pointer_addresses": True,
                "business_semantics_added": False,
            },
        }
    )
    return result


__all__ = ["apply_openapi_path_parameter_schema_projection"]
