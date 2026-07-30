"""Bind API semantic records to exact source-preserving artifact structure.

The API artifact adapter already owns JSON/YAML decoding, credential redaction and JSON
Pointer construction. This module consumes those Document IR blocks plus the existing
semantic records produced by ``_parse_source``. It never parses source bytes and never
changes interface meaning; it only restores exact operation/request/observation evidence.
"""
from __future__ import annotations

import re
import urllib.parse
from collections import defaultdict
from typing import Any, Iterable

API_ARTIFACT_SEMANTIC_BINDING_SCHEMA = (
    "qualibug.api-artifact-semantic-binding-receipt.v1"
)

_PRIMARY_NODE_KINDS = {
    "openapi": {"OPENAPI_OPERATION"},
    "postman": {"POSTMAN_REQUEST"},
    "har": {"HAR_ENTRY"},
}
_SOURCE_KIND_TO_ARTIFACT = {
    "openapi": "openapi",
    "postman": "postman",
    "har": "har",
    "har_traffic": "har",
}
_SAFE_CHILD_FIELDS = {
    "node_kind",
    "json_pointer",
    "source_locator",
    "block_id",
    "parameter_name",
    "parameter_location",
    "required",
    "media_type",
    "status_code",
    "security_schemes",
    "field_name",
    "header_name",
    "disabled",
    "script_kind",
    "script_line_count",
    "declared_test_names",
    "response_status",
    "elapsed_ms",
    "response_mime_type",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _normalized_text(value: Any) -> str:
    return re.sub(r"\s+", "", _text(value)).casefold()


def _normalized_method(value: Any) -> str:
    return _text(value).upper()


def _normalized_path(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "/"
    try:
        parsed = urllib.parse.urlsplit(raw)
        if parsed.scheme or parsed.netloc:
            raw = parsed.path or "/"
    except ValueError:
        pass
    raw = raw.split("?", 1)[0].split("#", 1)[0]
    raw = re.sub(r"/{2,}", "/", raw)
    if not raw.startswith("/") and not raw.startswith("{{"):
        raw = "/" + raw
    return raw.rstrip("/") or "/"


def _artifact_kind(document_ir: dict[str, Any]) -> str:
    return _text(_dict(document_ir.get("artifact_structure")).get("artifact_kind")).lower()


def _block_evidence(block: dict[str, Any]) -> dict[str, Any]:
    address = _dict(block.get("evidence_address"))
    return {
        "source_id": _text(block.get("source_id") or address.get("source_id")),
        "source_hash": _text(block.get("source_hash") or address.get("source_hash")),
        "block_id": _text(block.get("block_id")),
        "source_locator": _text(
            block.get("source_locator") or address.get("source_locator")
        ),
        "json_pointer": _text(block.get("json_pointer")),
        "address_kind": _text(address.get("address_kind"))
        or "EXACT_SOURCE_LOCATOR",
        "node_kind": _text(block.get("node_kind")),
    }


def _safe_child_projection(block: dict[str, Any]) -> dict[str, Any]:
    result = {
        key: block.get(key)
        for key in _SAFE_CHILD_FIELDS
        if key in block and block.get(key) not in (None, "", [], {})
    }
    result.setdefault("node_kind", _text(block.get("node_kind")))
    result.setdefault("json_pointer", _text(block.get("json_pointer")))
    result.setdefault("source_locator", _text(block.get("source_locator")))
    result.setdefault("block_id", _text(block.get("block_id")))
    result["credential_values_retained"] = False
    result["business_semantics_added"] = False
    return result


def _primary_blocks(
    document_ir: dict[str, Any], artifact_kind: str
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    allowed = _PRIMARY_NODE_KINDS.get(artifact_kind, set())
    primaries: list[dict[str, Any]] = []
    children: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for raw in _list(document_ir.get("blocks")):
        if not isinstance(raw, dict):
            continue
        block = dict(raw)
        node_kind = _text(block.get("node_kind"))
        if node_kind in allowed:
            primaries.append(block)
        parent = _text(block.get("parent_id"))
        if parent:
            children[parent].append(block)
    return primaries, children


def _operation_key(row: dict[str, Any]) -> tuple[str, str]:
    return (
        _normalized_method(row.get("method") or row.get("http_method")),
        _normalized_path(row.get("path") or row.get("api_path") or row.get("endpoint")),
    )


def _block_key(block: dict[str, Any]) -> tuple[str, str]:
    return (
        _normalized_method(block.get("http_method")),
        _normalized_path(block.get("api_path") or block.get("request_url")),
    )


def _operation_artifact_kind(operation: dict[str, Any], default: str) -> str:
    source_kind = _text(operation.get("source_kind") or operation.get("source")).lower()
    return _SOURCE_KIND_TO_ARTIFACT.get(source_kind, default)


def _disambiguate(
    operation: dict[str, Any], candidates: list[dict[str, Any]], artifact_kind: str
) -> list[dict[str, Any]]:
    if len(candidates) <= 1 or artifact_kind == "har":
        return candidates
    operation_id = _normalized_text(operation.get("operation_id"))
    if operation_id:
        exact = [
            row
            for row in candidates
            if _normalized_text(row.get("operation_id")) == operation_id
        ]
        if exact:
            return exact
    summary = _normalized_text(operation.get("summary") or operation.get("name"))
    if summary:
        exact = [
            row
            for row in candidates
            if summary
            in {
                _normalized_text(row.get("request_name")),
                _normalized_text(row.get("text")),
            }
        ]
        if exact:
            return exact
    return candidates


def _evidence_payload(
    candidates: list[dict[str, Any]],
    children_by_parent: dict[str, list[dict[str, Any]]],
    *,
    artifact_kind: str,
) -> dict[str, Any]:
    evidence = [_block_evidence(row) for row in candidates]
    declarations: list[dict[str, Any]] = []
    for candidate in candidates:
        parent_id = _text(candidate.get("block_id"))
        declarations.extend(
            _safe_child_projection(row)
            for row in children_by_parent.get(parent_id, [])
        )
    match_kind = "EXACT_OPERATION_BLOCK" if len(candidates) == 1 else "OBSERVATION_SET"
    return {
        "source_backed": bool(candidates),
        "artifact_kind": artifact_kind,
        "match_kind": match_kind,
        "block_id": evidence[0]["block_id"] if len(evidence) == 1 else "",
        "block_ids": [row["block_id"] for row in evidence if row["block_id"]],
        "source_locator": (
            evidence[0]["source_locator"] if len(evidence) == 1 else ""
        ),
        "source_locators": [
            row["source_locator"] for row in evidence if row["source_locator"]
        ],
        "json_pointer": evidence[0]["json_pointer"] if len(evidence) == 1 else "",
        "json_pointers": [
            row["json_pointer"] for row in evidence if row["json_pointer"]
        ],
        "evidence_addresses": evidence,
        "declared_structure": declarations,
        "credential_values_retained": False,
        "business_semantics_changed": False,
        "automatic_business_inference_used": False,
    }


def _bind_operations(
    operations: Iterable[dict[str, Any]],
    *,
    artifact_kind: str,
    primaries: list[dict[str, Any]],
    children: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int, int]:
    index: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for block in primaries:
        index[_block_key(block)].append(block)
    bound: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    exact_count = 0
    observation_set_count = 0
    for position, raw in enumerate(operations):
        if not isinstance(raw, dict):
            continue
        operation = dict(raw)
        row_kind = _operation_artifact_kind(operation, artifact_kind)
        candidates = index.get(_operation_key(operation), [])
        if row_kind != artifact_kind:
            candidates = []
        candidates = _disambiguate(operation, list(candidates), artifact_kind)
        if not candidates:
            unresolved.append(
                {
                    "record_kind": "operation",
                    "record_index": position,
                    "interface_id": _text(operation.get("interface_id")),
                    "method": _normalized_method(operation.get("method")),
                    "path": _normalized_path(operation.get("path")),
                    "reason": "API_ARTIFACT_OPERATION_BLOCK_NOT_FOUND",
                }
            )
            bound.append(operation)
            continue
        if artifact_kind != "har" and len(candidates) != 1:
            unresolved.append(
                {
                    "record_kind": "operation",
                    "record_index": position,
                    "interface_id": _text(operation.get("interface_id")),
                    "method": _normalized_method(operation.get("method")),
                    "path": _normalized_path(operation.get("path")),
                    "reason": "API_ARTIFACT_OPERATION_BLOCK_NOT_UNIQUE",
                    "candidate_block_ids": [
                        _text(row.get("block_id")) for row in candidates
                    ],
                }
            )
            bound.append(operation)
            continue
        payload = _evidence_payload(
            candidates,
            children,
            artifact_kind=artifact_kind,
        )
        operation["document_structure_evidence"] = payload
        operation["source_locators"] = payload["source_locators"]
        operation["json_pointers"] = payload["json_pointers"]
        if len(candidates) == 1:
            operation["source_locator"] = payload["source_locator"]
            operation["json_pointer"] = payload["json_pointer"]
            operation["document_block_id"] = payload["block_id"]
            exact_count += 1
        else:
            observation_set_count += 1
        bound.append(operation)
    return bound, unresolved, exact_count, observation_set_count


def _bind_har_errors(
    rows: Iterable[dict[str, Any]],
    primaries: list[dict[str, Any]],
    children: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], int]:
    index: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for block in primaries:
        method, path = _block_key(block)
        status = _text(block.get("response_status"))
        index[(method, path, status)].append(block)
    bound: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    bound_count = 0
    for position, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        item = dict(raw)
        key = (
            _normalized_method(item.get("method")),
            _normalized_path(item.get("endpoint") or item.get("path")),
            _text(item.get("status")),
        )
        candidates = index.get(key, [])
        if not candidates:
            unresolved.append(
                {
                    "record_kind": "har_error",
                    "record_index": position,
                    "method": key[0],
                    "path": key[1],
                    "status": key[2],
                    "reason": "HAR_ERROR_OBSERVATION_NOT_FOUND",
                }
            )
            bound.append(item)
            continue
        payload = _evidence_payload(candidates, children, artifact_kind="har")
        item["document_structure_evidence"] = payload
        item["source_locators"] = payload["source_locators"]
        item["json_pointers"] = payload["json_pointers"]
        bound_count += 1
        bound.append(item)
    return bound, unresolved, bound_count


def bind_api_artifact_semantics(
    parsed: dict[str, Any],
    document_ir: dict[str, Any],
    *,
    source_id: str = "",
) -> dict[str, Any]:
    """Attach exact API artifact evidence to existing semantic extraction records."""

    result = dict(parsed or {})
    artifact_kind = _artifact_kind(document_ir)
    if artifact_kind not in _PRIMARY_NODE_KINDS:
        result["api_artifact_semantic_binding_receipt"] = {
            "schema": API_ARTIFACT_SEMANTIC_BINDING_SCHEMA,
            "status": "NOT_APPLICABLE",
            "source_id": source_id,
            "artifact_kind": artifact_kind,
            "operation_count": len(_list(result.get("operations"))),
            "bound_operation_count": 0,
            "unresolved_count": 0,
            "business_semantics_changed": False,
        }
        return result

    primaries, children = _primary_blocks(document_ir, artifact_kind)
    operations, unresolved, exact_count, observation_count = _bind_operations(
        [row for row in _list(result.get("operations")) if isinstance(row, dict)],
        artifact_kind=artifact_kind,
        primaries=primaries,
        children=children,
    )
    result["operations"] = operations
    har_bound_count = 0
    if artifact_kind == "har":
        har_errors, har_unresolved, har_bound_count = _bind_har_errors(
            [row for row in _list(result.get("har_errors")) if isinstance(row, dict)],
            primaries,
            children,
        )
        result["har_errors"] = har_errors
        unresolved.extend(har_unresolved)

    bound_count = exact_count + observation_count
    status = (
        "COMPLETE"
        if not unresolved
        else "PARTIAL"
        if bound_count or har_bound_count
        else "BLOCKED"
    )
    result["api_artifact_semantic_binding_receipt"] = {
        "schema": API_ARTIFACT_SEMANTIC_BINDING_SCHEMA,
        "status": status,
        "source_id": source_id,
        "artifact_kind": artifact_kind,
        "primary_structure_block_count": len(primaries),
        "operation_count": len(operations),
        "bound_operation_count": bound_count,
        "exact_operation_binding_count": exact_count,
        "observation_set_binding_count": observation_count,
        "har_error_count": len(_list(result.get("har_errors"))),
        "bound_har_error_count": har_bound_count,
        "unresolved_count": len(unresolved),
        "unresolved": unresolved,
        "json_pointer_evidence_required": True,
        "credential_values_retained": False,
        "business_semantics_changed": False,
        "automatic_business_inference_used": False,
    }
    return result


__all__ = [
    "API_ARTIFACT_SEMANTIC_BINDING_SCHEMA",
    "bind_api_artifact_semantics",
]
