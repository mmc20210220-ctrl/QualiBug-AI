"""Attach source-declared OpenAPI technical structure to logical operations.

Document ingestion already preserves parameters, request/response declarations and schema
properties as exact JSON-Pointer blocks. This module performs the missing semantic ownership
step: inline declarations belong only to their operation, while component schemas belong to an
operation only when reachable through that operation's local ``$ref`` closure.

It does not parse containers, infer business sequence, or choose between conflicting sources.
"""
from __future__ import annotations

from collections import defaultdict, deque
from copy import deepcopy
from typing import Any, Iterable

OPENAPI_TECHNICAL_DECLARATION_SCHEMA = "qualibug.openapi-technical-declaration.v1"
OPENAPI_TECHNICAL_CLOSURE_SCHEMA = "qualibug.openapi-technical-closure.v1"

_DECLARATION_KINDS = {
    "OPENAPI_PARAMETER",
    "OPENAPI_REQUEST_BODY",
    "OPENAPI_RESPONSE",
    "OPENAPI_SECURITY_REQUIREMENT",
    "OPENAPI_SCHEMA",
    "OPENAPI_SCHEMA_PROPERTY",
}
_METADATA_FIELDS = {
    "parameter_name",
    "parameter_location",
    "required",
    "media_type",
    "status_code",
    "security_schemes",
    "schema_label",
    "schema_type",
    "schema_format",
    "property_name",
    "property_path",
    "nullable",
    "read_only",
    "write_only",
    "deprecated",
    "description",
    "ref",
    "constraints",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _method(value: Any) -> str:
    return _text(value).lower()


def _path(value: Any) -> str:
    raw = _text(value)
    return raw or "/"


def _pointer_token(value: str) -> str:
    return str(value).replace("~", "~0").replace("/", "~1")


def _operation_pointer(path: str, method: str) -> str:
    return f"/paths/{_pointer_token(path)}/{_pointer_token(method)}"


def _decode_pointer_token(value: str) -> str:
    return str(value).replace("~1", "/").replace("~0", "~")


def _resolve_local_pointer(payload: dict[str, Any], ref: str) -> Any:
    if not str(ref or "").startswith("#/"):
        return None
    current: Any = payload
    for token in str(ref)[2:].split("/"):
        key = _decode_pointer_token(token)
        if isinstance(current, dict) and key in current:
            current = current[key]
        elif isinstance(current, list) and key.isdigit() and int(key) < len(current):
            current = current[int(key)]
        else:
            return None
    return current


def _walk_local_refs(value: Any) -> Iterable[str]:
    if isinstance(value, dict):
        ref = _text(value.get("$ref"))
        if ref.startswith("#/"):
            yield ref
        for child in value.values():
            yield from _walk_local_refs(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_local_refs(child)


def _reachable_local_refs(payload: dict[str, Any], roots: Iterable[Any]) -> set[str]:
    queue = deque()
    for root in roots:
        queue.extend(_walk_local_refs(root))
    seen: set[str] = set()
    while queue:
        ref = _text(queue.popleft())
        if not ref or ref in seen:
            continue
        seen.add(ref)
        resolved = _resolve_local_pointer(payload, ref)
        if resolved is not None:
            queue.extend(_walk_local_refs(resolved))
    return seen


def _children_by_parent(blocks: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        parent = _text(block.get("parent_id"))
        if parent:
            result[parent].append(block)
    return result


def _descendant_ids(
    root_id: str,
    children: dict[str, list[dict[str, Any]]],
) -> set[str]:
    result: set[str] = set()
    queue = deque([root_id])
    while queue:
        parent = queue.popleft()
        for child in children.get(parent, []):
            block_id = _text(child.get("block_id"))
            if not block_id or block_id in result:
                continue
            result.add(block_id)
            queue.append(block_id)
    return result


def _declaration(block: dict[str, Any], *, ownership: str) -> dict[str, Any]:
    row: dict[str, Any] = {
        "schema": OPENAPI_TECHNICAL_DECLARATION_SCHEMA,
        "node_kind": _text(block.get("node_kind")),
        "document_ir_block_id": _text(block.get("block_id")),
        "parent_id": _text(block.get("parent_id")),
        "text": _text(block.get("text"))[:4_000],
        "json_pointer": _text(block.get("json_pointer")),
        "source_locator": _text(block.get("source_locator")),
        "evidence_address": deepcopy(_dict(block.get("evidence_address"))),
        "source_traceability": "EXACT_JSON_POINTER",
        "ownership": ownership,
        "source_declared": True,
        "business_semantics_added": False,
        "credential_values_retained": False,
    }
    for field in _METADATA_FIELDS:
        value = block.get(field)
        if value not in (None, "", [], {}):
            row[field] = deepcopy(value)
    return row


def _declaration_identity(row: dict[str, Any]) -> tuple[str, str]:
    return (_text(row.get("node_kind")), _text(row.get("json_pointer")))


def _operation_roots(
    payload: dict[str, Any],
    *,
    path: str,
    method: str,
) -> list[Any]:
    path_item = _dict(_dict(payload.get("paths")).get(path))
    operation = _dict(path_item.get(method))
    return [*_list(path_item.get("parameters")), operation]


def _component_pointer(ref: str) -> str:
    return str(ref or "")[1:] if str(ref or "").startswith("#/") else ""


def attach_openapi_technical_declarations(
    rows: Iterable[dict[str, Any]],
    blocks: Iterable[dict[str, Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Attach exact inline and ref-reachable technical declarations to each operation."""

    operations = [row for row in rows if isinstance(row, dict)]
    source_blocks = [dict(block) for block in blocks if isinstance(block, dict)]
    children = _children_by_parent(source_blocks)
    operation_blocks: dict[tuple[str, str], dict[str, Any]] = {}
    for block in source_blocks:
        if _text(block.get("node_kind")) != "OPENAPI_OPERATION":
            continue
        key = (_text(block.get("http_method")).upper(), _path(block.get("api_path")))
        operation_blocks[key] = block

    for operation in operations:
        method_upper = _text(operation.get("method")).upper()
        method_lower = method_upper.lower()
        path = _path(operation.get("path"))
        operation_block = operation_blocks.get((method_upper, path))
        pointer = (
            _text(operation_block.get("json_pointer"))
            if operation_block is not None
            else _operation_pointer(path, method_lower)
        )
        descendant_ids = (
            _descendant_ids(_text(operation_block.get("block_id")), children)
            if operation_block is not None
            else set()
        )
        refs = _reachable_local_refs(
            payload,
            _operation_roots(payload, path=path, method=method_lower),
        )
        component_prefixes = {
            _component_pointer(ref)
            for ref in refs
            if _component_pointer(ref)
        }

        declarations: list[dict[str, Any]] = []
        seen: set[tuple[str, str]] = set()
        for block in source_blocks:
            kind = _text(block.get("node_kind"))
            if kind not in _DECLARATION_KINDS:
                continue
            block_id = _text(block.get("block_id"))
            block_pointer = _text(block.get("json_pointer"))
            ownership = ""
            if block_id and block_id in descendant_ids:
                ownership = "DOCUMENT_IR_PARENT_DESCENDANT"
            elif pointer and (
                block_pointer == pointer or block_pointer.startswith(pointer + "/")
            ):
                ownership = "INLINE_OPERATION_POINTER_PREFIX"
            elif any(
                block_pointer == prefix or block_pointer.startswith(prefix + "/")
                for prefix in component_prefixes
            ):
                ownership = "LOCAL_REF_COMPONENT_CLOSURE"
            if not ownership:
                continue
            declaration = _declaration(block, ownership=ownership)
            identity = _declaration_identity(declaration)
            if identity in seen:
                continue
            seen.add(identity)
            declarations.append(declaration)

        declarations.sort(
            key=lambda row: (
                _text(row.get("json_pointer")),
                _text(row.get("node_kind")),
            )
        )
        exact_count = sum(
            1
            for row in declarations
            if _text(row.get("json_pointer")) and _text(row.get("source_locator"))
        )
        operation["technical_declarations"] = declarations
        operation["technical_declaration_count"] = len(declarations)
        operation["exact_technical_declaration_count"] = exact_count
        operation["exact_technical_declaration_rate"] = (
            round(exact_count / len(declarations), 4) if declarations else 1.0
        )
        operation["openapi_local_ref_closure"] = {
            "schema": OPENAPI_TECHNICAL_CLOSURE_SCHEMA,
            "operation_pointer": pointer,
            "reachable_local_refs": sorted(refs),
            "reachable_component_pointers": sorted(component_prefixes),
            "technical_declaration_count": len(declarations),
            "exact_technical_declaration_count": exact_count,
            "unrelated_components_attached": False,
            "external_refs_resolved": False,
            "business_flow_inferred": False,
        }
    return operations


__all__ = [
    "OPENAPI_TECHNICAL_DECLARATION_SCHEMA",
    "OPENAPI_TECHNICAL_CLOSURE_SCHEMA",
    "attach_openapi_technical_declarations",
]
