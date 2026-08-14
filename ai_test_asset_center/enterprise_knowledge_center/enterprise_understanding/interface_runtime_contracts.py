"""Preserve source-declared OpenAPI runtime contract metadata without retaining secrets.

The existing parser remains the authority for interface identity. This additive wrapper keeps
parameter locations, request-body field paths, response contracts and security scheme names so a
later Runtime Plan compiler does not have to guess Query/Header/Path/Body placement. Operation
summary and description are also preserved as one exact source excerpt so the existing
``exact_source_section`` relationship authority can bind a source rule without token similarity.
"""
from __future__ import annotations

from functools import wraps
from typing import Any, Iterable

from .schema import as_dict, as_list, text, unique_text

OPENAPI_RUNTIME_CONTRACT_SCHEMA = "qualibug.openapi-runtime-contract-metadata.v1"
_HTTP_METHODS = {"get", "post", "put", "patch", "delete", "head", "options"}
_LOCATION_MAP = {
    "path": "PATH",
    "query": "QUERY",
    "header": "HEADER",
    "cookie": "COOKIE",
    "body": "BODY",
    "formdata": "FORM",
    "form": "FORM",
}


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _resolve_local_ref(document: dict[str, Any], value: Any) -> dict[str, Any]:
    row = dict(value) if isinstance(value, dict) else {}
    ref = text(row.get("$ref"))
    if not ref.startswith("#/"):
        return row
    current: Any = document
    for token in ref[2:].split("/"):
        token = token.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or token not in current:
            return row
        current = current[token]
    return dict(current) if isinstance(current, dict) else row


def _schema_type(schema: dict[str, Any]) -> str:
    value = text(schema.get("type")).upper()
    if value:
        return value
    if isinstance(schema.get("properties"), dict):
        return "OBJECT"
    if schema.get("items"):
        return "ARRAY"
    return "UNSPECIFIED"


def _schema_fields(
    document: dict[str, Any],
    schema_value: Any,
    *,
    location: str,
    media_type: str = "",
    prefix: str = "",
    required: Iterable[Any] = (),
    depth: int = 0,
    seen_refs: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    if depth > 5:
        return []
    raw = dict(schema_value) if isinstance(schema_value, dict) else {}
    ref = text(raw.get("$ref"))
    if ref and ref in seen_refs:
        return []
    schema = _resolve_local_ref(document, raw)
    next_seen = (*seen_refs, ref) if ref else seen_refs
    rows: list[dict[str, Any]] = []

    for composition in ("allOf",):
        for child in _dicts(schema.get(composition)):
            rows.extend(
                _schema_fields(
                    document,
                    child,
                    location=location,
                    media_type=media_type,
                    prefix=prefix,
                    required=required,
                    depth=depth + 1,
                    seen_refs=next_seen,
                )
            )

    required_names = {text(value) for value in required if text(value)}
    required_names |= {
        text(value) for value in as_list(schema.get("required")) if text(value)
    }
    properties = as_dict(schema.get("properties"))
    for name, property_value in properties.items():
        property_schema = _resolve_local_ref(document, property_value)
        field_path = f"{prefix}.{name}" if prefix else text(name)
        row = {
            "field": field_path,
            "name": text(name),
            "location": location,
            "required": text(name) in required_names,
            "schema_type": _schema_type(property_schema),
            "format": text(property_schema.get("format")),
            "enum": as_list(property_schema.get("enum")),
            "nullable": bool(property_schema.get("nullable")),
            "media_type": media_type,
            "source": "OPENAPI_SCHEMA_PROPERTY",
        }
        rows.append(
            {key: value for key, value in row.items() if value not in ("", [], None)}
        )
        rows.extend(
            _schema_fields(
                document,
                property_schema,
                location=location,
                media_type=media_type,
                prefix=field_path,
                depth=depth + 1,
                seen_refs=next_seen,
            )
        )
    items = _resolve_local_ref(document, schema.get("items"))
    if items and prefix:
        rows.extend(
            _schema_fields(
                document,
                items,
                location=location,
                media_type=media_type,
                prefix=f"{prefix}[]",
                depth=depth + 1,
                seen_refs=next_seen,
            )
        )
    return rows


def _parameter_contract(
    document: dict[str, Any], value: Any, *, source: str
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    parameter = _resolve_local_ref(document, value)
    name = text(parameter.get("name"))
    location_raw = text(parameter.get("in")).lower()
    location = _LOCATION_MAP.get(location_raw, location_raw.upper())
    schema = _resolve_local_ref(document, parameter.get("schema") or parameter)
    if not name or not location:
        return None, []
    row = {
        "name": name,
        "field": name,
        "location": location,
        "required": bool(parameter.get("required")) or location == "PATH",
        "schema_type": _schema_type(schema),
        "format": text(schema.get("format")),
        "enum": as_list(schema.get("enum")),
        "source": source,
    }
    body_fields: list[dict[str, Any]] = []
    if location == "BODY":
        body_fields = _schema_fields(
            document,
            schema,
            location="BODY",
            required=as_list(schema.get("required")),
        )
    return (
        {key: value for key, value in row.items() if value not in ("", [], None)},
        body_fields,
    )


def _request_body_contract(
    document: dict[str, Any], value: Any
) -> tuple[list[dict[str, Any]], list[str], bool]:
    request_body = _resolve_local_ref(document, value)
    content = as_dict(request_body.get("content"))
    fields: list[dict[str, Any]] = []
    media_types: list[str] = []
    for media_type, media in content.items():
        media_types.append(text(media_type))
        schema = as_dict(_resolve_local_ref(document, as_dict(media).get("schema")))
        fields.extend(
            _schema_fields(
                document,
                schema,
                location="BODY",
                media_type=text(media_type),
                required=as_list(schema.get("required")),
            )
        )
    return fields, unique_text(media_types), bool(request_body.get("required"))


def _response_contracts(
    document: dict[str, Any], responses_value: Any
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for status, raw in as_dict(responses_value).items():
        response = _resolve_local_ref(document, raw)
        content = as_dict(response.get("content"))
        media_types = unique_text(content.keys())
        fields: list[dict[str, Any]] = []
        for media_type, media in content.items():
            schema = as_dict(
                _resolve_local_ref(document, as_dict(media).get("schema"))
            )
            fields.extend(
                _schema_fields(
                    document,
                    schema,
                    location="RESPONSE_BODY",
                    media_type=text(media_type),
                )
            )
        if not content and response.get("schema"):
            fields.extend(
                _schema_fields(
                    document,
                    response.get("schema"),
                    location="RESPONSE_BODY",
                )
            )
        rows.append(
            {
                "status": text(status),
                "description": text(response.get("description")),
                "media_types": media_types,
                "fields": fields,
                "source": "OPENAPI_RESPONSE",
            }
        )
    return rows


def _security_requirements(
    document: dict[str, Any], operation: dict[str, Any]
) -> list[dict[str, Any]]:
    selected = operation.get("security")
    if selected is None:
        selected = document.get("security")
    rows: list[dict[str, Any]] = []
    schemes = as_dict(as_dict(document.get("components")).get("securitySchemes"))
    if not schemes:
        schemes = as_dict(as_dict(document.get("securityDefinitions")))
    for requirement in _dicts(selected):
        for scheme_name, scopes in requirement.items():
            scheme = _resolve_local_ref(document, schemes.get(scheme_name))
            rows.append(
                {
                    "scheme": text(scheme_name),
                    "type": text(scheme.get("type")).upper(),
                    "in": text(scheme.get("in")).upper(),
                    "name": text(scheme.get("name")),
                    "scheme_name": text(scheme.get("scheme")),
                    "bearer_format": text(scheme.get("bearerFormat")),
                    "scopes": unique_text(as_list(scopes)),
                    "credential_value_retained": False,
                }
            )
    return rows


def _operation_source_excerpt(
    row: dict[str, Any], operation: dict[str, Any]
) -> str:
    """Preserve exact operation prose for the existing exact-source linker."""

    parts = unique_text(
        [
            text(row.get("source_excerpt")),
            text(operation.get("summary")),
            text(operation.get("description")),
        ]
    )
    return "\n".join(parts)


def enrich_openapi_runtime_contracts(
    openapi: dict[str, Any], rows: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    by_id = {
        text(row.get("interface_id")): dict(row)
        for row in rows
        if isinstance(row, dict)
    }
    for path, path_item_value in as_dict(openapi.get("paths")).items():
        path_item = as_dict(path_item_value)
        path_parameters = as_list(path_item.get("parameters"))
        for method, operation_value in path_item.items():
            if (
                text(method).lower() not in _HTTP_METHODS
                or not isinstance(operation_value, dict)
            ):
                continue
            operation = dict(operation_value)
            interface_id = f"api:{text(method).upper()}:{path}"
            row = by_id.get(interface_id)
            if row is None:
                continue
            parameter_contracts: list[dict[str, Any]] = []
            request_body_fields: list[dict[str, Any]] = []
            for raw in [*path_parameters, *as_list(operation.get("parameters"))]:
                descriptor, body_fields = _parameter_contract(
                    openapi, raw, source="OPENAPI_PARAMETER"
                )
                if descriptor:
                    parameter_contracts.append(descriptor)
                request_body_fields.extend(body_fields)
            body_fields, media_types, body_required = _request_body_contract(
                openapi, operation.get("requestBody")
            )
            request_body_fields.extend(body_fields)
            path_names = {
                segment[1:-1]
                for segment in text(path).split("/")
                if segment.startswith("{") and segment.endswith("}")
            }
            declared_path_names = {
                text(value.get("name"))
                for value in parameter_contracts
                if text(value.get("location")) == "PATH"
            }
            for name in sorted(path_names - declared_path_names):
                parameter_contracts.append(
                    {
                        "name": name,
                        "field": name,
                        "location": "PATH",
                        "required": True,
                        "schema_type": "UNSPECIFIED",
                        "source": "OPENAPI_PATH_TEMPLATE",
                    }
                )
            source_excerpt = _operation_source_excerpt(row, operation)
            row.update(
                {
                    "runtime_contract_schema": OPENAPI_RUNTIME_CONTRACT_SCHEMA,
                    "parameter_contracts": parameter_contracts,
                    "request_body_fields": request_body_fields,
                    "request_body_media_types": media_types,
                    "request_body_required": body_required,
                    "response_contracts": _response_contracts(
                        openapi, operation.get("responses")
                    ),
                    "security_requirements": _security_requirements(
                        openapi, operation
                    ),
                    "openapi_summary": text(operation.get("summary")),
                    "openapi_description": text(operation.get("description")),
                    "source_excerpt": source_excerpt,
                    "source_excerpt_authority": (
                        "OPENAPI_OPERATION_SUMMARY_DESCRIPTION"
                    ),
                    "source_excerpt_exact_source_declared": bool(source_excerpt),
                    "request_contract_locations_preserved": True,
                    "credential_values_retained": False,
                }
            )
    return list(by_id.values())


def install_interface_runtime_contract_parser() -> None:
    """Install an idempotent additive wrapper on the existing OpenAPI parser.

    ``_parse_source`` lives in ``_parsing_mechanics`` and resolves
    ``_openapi_operations`` from that module's globals, so the add-on must patch
    the mechanics module (``_core``) rather than the ``_parsing`` facade — a
    facade-only patch is a silent no-op after the mechanics split. Importing
    ``_parsing`` first keeps its security-stamped facade installed into the
    mechanics globals so the wrapper enriches operations that already carry
    security provenance.
    """
    from .. import _parsing  # noqa: F401  (installs security facade into mechanics)
    from .. import _parsing_mechanics as _core

    current = _core._openapi_operations
    if getattr(current, "_qualibug_runtime_contract_metadata", False):
        return
    original = current

    @wraps(original)
    def wrapped(openapi: dict[str, Any], source_id: str = ""):
        return enrich_openapi_runtime_contracts(
            openapi, original(openapi, source_id=source_id)
        )

    wrapped._qualibug_runtime_contract_metadata = True  # type: ignore[attr-defined]
    wrapped._qualibug_original_openapi_operations = original  # type: ignore[attr-defined]
    _core._openapi_operations = wrapped


__all__ = [
    "OPENAPI_RUNTIME_CONTRACT_SCHEMA",
    "enrich_openapi_runtime_contracts",
    "install_interface_runtime_contract_parser",
]
