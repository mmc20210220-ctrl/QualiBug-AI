"""Project source-declared API artifact structure into interface semantic contracts.

This stage runs after Document IR and after the generic semantic parser. It does not parse file
containers and never infers business flow. OpenAPI declarations remain contract authority,
Postman collections remain executable request/example authority, and HAR remains runtime
observation evidence rather than a design contract.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
from typing import Any, Iterable

from .enterprise_understanding.interface_runtime_contracts import (
    enrich_openapi_runtime_contracts,
)

API_ARTIFACT_SEMANTIC_RECEIPT_SCHEMA = "qualibug.api-artifact-semantic-projection.v1"
POSTMAN_RUNTIME_CONTRACT_SCHEMA = "qualibug.postman-runtime-contract-metadata.v1"
HAR_RUNTIME_OBSERVATION_SCHEMA = "qualibug.har-runtime-observation.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _method(value: Any) -> str:
    return _text(value).upper()


def _path(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "/"
    raw = re.sub(r"^https?://[^/]+", "", raw)
    raw = raw.split("?", 1)[0]
    return raw or "/"


def _safe_slug(value: str, limit: int = 64) -> str:
    normalized = re.sub(r"[^a-zA-Z0-9_\-]+", "_", _text(value)).strip("_")
    if normalized:
        return normalized[:limit]
    return hashlib.sha256(_text(value).encode("utf-8")).hexdigest()[:16]


def _evidence(block: dict[str, Any]) -> dict[str, Any]:
    return {
        "document_ir_block_id": _text(block.get("block_id")),
        "source_locator": _text(block.get("source_locator")),
        "json_pointer": _text(block.get("json_pointer")),
        "evidence_address": _dict(block.get("evidence_address")),
        "source_traceability": "EXACT_JSON_POINTER",
    }


def _artifact_blocks(document_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(document_ir.get("blocks"))
        if isinstance(row, dict) and _text(row.get("node_kind"))
    ]


def _children_by_parent(blocks: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for block in blocks:
        parent = _text(block.get("parent_id"))
        if parent:
            result[parent].append(dict(block))
    return result


def _dedupe_operation_rows(existing: Iterable[Any]) -> list[dict[str, Any]]:
    """Collapse shallow parser duplicates onto one method/path interface identity."""

    rows: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in existing:
        if not isinstance(raw, dict):
            continue
        candidate = dict(raw)
        key = (_method(candidate.get("method")), _path(candidate.get("path")))
        current = by_key.get(key)
        if current is None:
            by_key[key] = candidate
            rows.append(candidate)
            continue
        for field, value in candidate.items():
            if field in {"tags", "parameters", "tokens"}:
                merged = []
                for item in [*_list(current.get(field)), *_list(value)]:
                    if item not in merged:
                        merged.append(item)
                current[field] = merged
            elif current.get(field) in (None, "", [], {}):
                current[field] = value
        current["identity_duplicate_count"] = int(current.get("identity_duplicate_count") or 1) + 1
    return rows


def _operation_index(rows: Iterable[Any]) -> dict[tuple[str, str], dict[str, Any]]:
    """Index the actual row objects so enrichments cannot disappear into copies."""

    result: dict[tuple[str, str], dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        key = (_method(raw.get("method")), _path(raw.get("path")))
        result.setdefault(key, raw)
    return result


def _attach_openapi_evidence(
    rows: list[dict[str, Any]],
    blocks: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    by_key = _operation_index(rows)
    children = _children_by_parent(blocks)
    for block in blocks:
        if _text(block.get("node_kind")) != "OPENAPI_OPERATION":
            continue
        key = (_method(block.get("http_method")), _path(block.get("api_path")))
        row = by_key.get(key)
        if row is None:
            row = {
                "interface_id": f"api:{key[0]}:{key[1]}",
                "source_kind": "openapi",
                "method": key[0],
                "path": key[1],
                "operation_id": _text(block.get("operation_id"))
                or _safe_slug(f"{key[0]}_{key[1]}"),
                "summary": _text(block.get("text")),
                "tags": list(block.get("declared_tags") or []),
                "parameters": [],
            }
            rows.append(row)
            by_key[key] = row
        row.update(
            {
                **_evidence(block),
                "interface_id": _text(row.get("interface_id"))
                or f"api:{key[0]}:{key[1]}",
                "source_kind": "openapi",
                "method": key[0],
                "path": key[1],
                "contract_authority": "OPENAPI_SOURCE_DECLARATION",
                "business_flow_inferred": False,
            }
        )
        child_rows = children.get(_text(block.get("block_id"))) or []
        parameter_evidence: dict[tuple[str, str], dict[str, Any]] = {}
        response_evidence: dict[str, dict[str, Any]] = {}
        request_body_evidence: list[dict[str, Any]] = []
        security_evidence: list[dict[str, Any]] = []
        for child in child_rows:
            kind = _text(child.get("node_kind"))
            if kind == "OPENAPI_PARAMETER":
                parameter_evidence[
                    (
                        _text(child.get("parameter_name")),
                        _text(child.get("parameter_location")).upper(),
                    )
                ] = _evidence(child)
            elif kind == "OPENAPI_RESPONSE":
                response_evidence[_text(child.get("status_code"))] = _evidence(child)
            elif kind == "OPENAPI_REQUEST_BODY":
                request_body_evidence.append(_evidence(child))
            elif kind == "OPENAPI_SECURITY_REQUIREMENT":
                security_evidence.append(_evidence(child))
        for descriptor in _list(row.get("parameter_contracts")):
            if not isinstance(descriptor, dict):
                continue
            match = parameter_evidence.get(
                (_text(descriptor.get("name")), _text(descriptor.get("location")).upper())
            )
            if match:
                descriptor.update(match)
        for descriptor in _list(row.get("request_body_fields")):
            if isinstance(descriptor, dict) and request_body_evidence:
                descriptor.update(request_body_evidence[0])
                descriptor["evidence_scope"] = "REQUEST_BODY_DECLARATION"
        for descriptor in _list(row.get("response_contracts")):
            if not isinstance(descriptor, dict):
                continue
            match = response_evidence.get(_text(descriptor.get("status")))
            if match:
                descriptor.update(match)
        for index, descriptor in enumerate(_list(row.get("security_requirements"))):
            if isinstance(descriptor, dict) and security_evidence:
                descriptor.update(security_evidence[min(index, len(security_evidence) - 1)])
        row["credential_values_retained"] = False
    return rows


def _postman_parameter_contract(block: dict[str, Any]) -> dict[str, Any]:
    kind = _text(block.get("node_kind"))
    location = {
        "POSTMAN_QUERY_PARAMETER": "QUERY",
        "POSTMAN_PATH_VARIABLE": "PATH",
        "POSTMAN_HEADER": "HEADER",
    }.get(kind, "UNSPECIFIED")
    name = _text(block.get("field_name") or block.get("header_name"))
    return {
        "name": name,
        "field": name,
        "location": location,
        "required": location == "PATH",
        "disabled": bool(block.get("disabled")),
        "value_retained": False,
        "source": kind,
        **_evidence(block),
    }


def _merge_parameter_contracts(variants: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, bool]] = set()
    for variant in variants:
        for descriptor in _list(variant.get("parameter_contracts")):
            if not isinstance(descriptor, dict):
                continue
            key = (
                _text(descriptor.get("name")),
                _text(descriptor.get("location")),
                bool(descriptor.get("disabled")),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(dict(descriptor))
    return result


def _postman_rows(
    existing: Iterable[Any],
    blocks: list[dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    rows = _dedupe_operation_rows(existing)
    by_key = _operation_index(rows)
    children = _children_by_parent(blocks)
    for block in blocks:
        if _text(block.get("node_kind")) != "POSTMAN_REQUEST":
            continue
        method = _method(block.get("http_method")) or "GET"
        path = _path(block.get("api_path"))
        key = (method, path)
        row = by_key.get(key)
        name = _text(block.get("request_name")) or "Postman request"
        if row is None:
            row = {
                "interface_id": f"postman:{method}:{path}",
                "source_id": source_id,
                "source_kind": "postman",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(name),
                "summary": name,
                "tags": ["postman"],
                "parameters": [],
                "tokens": sorted({method.lower(), path.lower(), name.lower()}),
            }
            rows.append(row)
            by_key[key] = row
        child_rows = children.get(_text(block.get("block_id"))) or []
        parameter_contracts = [
            _postman_parameter_contract(child)
            for child in child_rows
            if _text(child.get("node_kind"))
            in {"POSTMAN_QUERY_PARAMETER", "POSTMAN_PATH_VARIABLE", "POSTMAN_HEADER"}
        ]
        scripts = [
            {
                "script_kind": _text(child.get("script_kind")),
                "script_line_count": int(child.get("script_line_count") or 0),
                "declared_test_names": [
                    _text(value)
                    for value in _list(child.get("declared_test_names"))
                    if _text(value)
                ],
                "script_source_retained_in_contract": False,
                **_evidence(child),
            }
            for child in child_rows
            if _text(child.get("node_kind")) == "POSTMAN_SCRIPT"
        ]
        response_examples = [
            {
                "status": _text(child.get("status_code")),
                "example_body_retained_in_contract": False,
                **_evidence(child),
            }
            for child in child_rows
            if _text(child.get("node_kind")) == "POSTMAN_RESPONSE_EXAMPLE"
        ]
        auth_type = _text(block.get("auth_type")).upper()
        security = []
        if auth_type:
            security.append(
                {
                    "type": auth_type,
                    "credential_value_retained": False,
                    "source": "POSTMAN_REQUEST_AUTH_DECLARATION",
                    **_evidence(block),
                }
            )
        variant = {
            "request_name": name,
            "folder_path": list(block.get("folder_path") or []),
            "body_mode": _text(block.get("body_mode")),
            "auth_type": auth_type,
            "parameter_contracts": parameter_contracts,
            "script_contracts": scripts,
            "response_examples": response_examples,
            "security_requirements": security,
            "credential_values_retained": False,
            **_evidence(block),
        }
        variants = [
            dict(item)
            for item in _list(row.get("postman_request_variants"))
            if isinstance(item, dict)
        ]
        if not any(
            _text(item.get("json_pointer")) == _text(variant.get("json_pointer"))
            for item in variants
        ):
            variants.append(variant)
        row.update(
            {
                **_evidence(block),
                "interface_id": _text(row.get("interface_id"))
                or f"postman:{method}:{path}",
                "runtime_contract_schema": POSTMAN_RUNTIME_CONTRACT_SCHEMA,
                "source_id": source_id or row.get("source_id"),
                "source_kind": "postman",
                "method": method,
                "path": path,
                "postman_request_variants": variants,
                "request_variant_count": len(variants),
                "request_name": _text(variants[0].get("request_name")),
                "folder_path": list(variants[0].get("folder_path") or []),
                "body_mode": _text(variants[0].get("body_mode")),
                "auth_type": _text(variants[0].get("auth_type")),
                "parameter_contracts": _merge_parameter_contracts(variants),
                "script_contracts": [
                    dict(script)
                    for item in variants
                    for script in _list(item.get("script_contracts"))
                    if isinstance(script, dict)
                ],
                "response_examples": [
                    dict(example)
                    for item in variants
                    for example in _list(item.get("response_examples"))
                    if isinstance(example, dict)
                ],
                "security_requirements": [
                    dict(requirement)
                    for item in variants
                    for requirement in _list(item.get("security_requirements"))
                    if isinstance(requirement, dict)
                ],
                "request_contract_locations_preserved": True,
                "credential_values_retained": False,
                "contract_authority": "POSTMAN_SOURCE_DECLARATION",
                "business_flow_inferred": False,
            }
        )
    return rows


def _har_field(block: dict[str, Any]) -> dict[str, Any]:
    kind = _text(block.get("node_kind"))
    location = {
        "HAR_REQUEST_HEADER": "REQUEST_HEADER",
        "HAR_QUERY_PARAMETER": "QUERY",
        "HAR_REQUEST_COOKIE": "COOKIE",
        "HAR_RESPONSE_HEADER": "RESPONSE_HEADER",
    }.get(kind, "UNSPECIFIED")
    return {
        "name": _text(block.get("field_name")),
        "location": location,
        "value_retained": False,
        "source": kind,
        **_evidence(block),
    }


def _har_rows(
    existing: Iterable[Any],
    blocks: list[dict[str, Any]],
    source_id: str,
) -> list[dict[str, Any]]:
    rows = _dedupe_operation_rows(existing)
    by_key = _operation_index(rows)
    children = _children_by_parent(blocks)
    for block in blocks:
        if _text(block.get("node_kind")) != "HAR_ENTRY":
            continue
        method = _method(block.get("http_method")) or "GET"
        path = _path(block.get("api_path"))
        key = (method, path)
        row = by_key.get(key)
        if row is None:
            row = {
                "interface_id": f"har:{method}:{path}",
                "source_id": source_id,
                "source_kind": "har_observation",
                "method": method,
                "path": path,
                "operation_id": _safe_slug(f"observed_{method}_{path}"),
                "summary": _text(block.get("text")),
                "tags": ["har", "runtime_observation"],
                "parameters": [],
                "tokens": sorted({method.lower(), path.lower(), "har"}),
            }
            rows.append(row)
            by_key[key] = row
        field_observations = [
            _har_field(child)
            for child in children.get(_text(block.get("block_id"))) or []
            if _text(child.get("node_kind"))
            in {
                "HAR_REQUEST_HEADER",
                "HAR_QUERY_PARAMETER",
                "HAR_REQUEST_COOKIE",
                "HAR_RESPONSE_HEADER",
            }
        ]
        observation = {
            "observed_status": block.get("response_status"),
            "elapsed_ms": block.get("elapsed_ms"),
            "started_at": _text(block.get("started_at")),
            "response_mime_type": _text(block.get("response_mime_type")),
            "field_observations": field_observations,
            "credential_values_retained": False,
            **_evidence(block),
        }
        observations = [
            dict(item)
            for item in _list(row.get("runtime_observations"))
            if isinstance(item, dict)
        ]
        if not any(
            _text(item.get("json_pointer")) == _text(observation.get("json_pointer"))
            for item in observations
        ):
            observations.append(observation)
        statuses: dict[str, int] = defaultdict(int)
        elapsed: list[float] = []
        for item in observations:
            status = _text(item.get("observed_status")) or "unknown"
            statuses[status] += 1
            try:
                elapsed.append(float(item.get("elapsed_ms")))
            except (TypeError, ValueError):
                pass
        row.update(
            {
                **_evidence(block),
                "interface_id": _text(row.get("interface_id"))
                or f"har:{method}:{path}",
                "operation_id": _text(row.get("operation_id"))
                or _safe_slug(f"observed_{method}_{path}"),
                "runtime_observation_schema": HAR_RUNTIME_OBSERVATION_SCHEMA,
                "source_id": source_id or row.get("source_id"),
                "source_kind": "har_observation",
                "method": method,
                "path": path,
                "runtime_observations": observations,
                "observation_count": len(observations),
                "observed_status_distribution": dict(sorted(statuses.items())),
                "observed_error_count": sum(
                    count
                    for status, count in statuses.items()
                    if status.isdigit() and int(status) >= 400
                ),
                "minimum_elapsed_ms": min(elapsed) if elapsed else None,
                "maximum_elapsed_ms": max(elapsed) if elapsed else None,
                "latest_observation": dict(observations[-1]),
                "credential_values_retained": False,
                "observation_authority": "HAR_RUNTIME_EVIDENCE",
                "contract_authority": False,
                "business_flow_inferred": False,
            }
        )
    return rows


def enrich_parsed_api_artifact_semantics(
    parsed: dict[str, Any],
    document_ir: dict[str, Any],
    *,
    source_id: str,
    source_type: str = "",
) -> dict[str, Any]:
    """Attach source-declared interface semantics and exact evidence to a parsed source."""

    result = dict(parsed or {})
    receipt = _dict(document_ir.get("structure_receipt"))
    artifact_kind = _text(receipt.get("artifact_kind")).lower()
    if artifact_kind not in {"openapi", "postman", "har"}:
        return result

    blocks = _artifact_blocks(document_ir)
    operations = [
        dict(row) for row in _list(result.get("operations")) if isinstance(row, dict)
    ]
    before = len(operations)
    if artifact_kind == "openapi":
        openapi = _dict(result.get("openapi") or result.get("payload"))
        operations = enrich_openapi_runtime_contracts(openapi, operations)
        operations = _attach_openapi_evidence(operations, blocks)
    elif artifact_kind == "postman":
        operations = _postman_rows(operations, blocks, source_id)
    else:
        operations = _har_rows(operations, blocks, source_id)

    exact_count = sum(
        1
        for row in operations
        if _text(row.get("source_locator")) and _text(row.get("json_pointer"))
    )
    variant_count = sum(int(row.get("request_variant_count") or 0) for row in operations)
    observation_count = sum(int(row.get("observation_count") or 0) for row in operations)
    result["operations"] = operations
    result["api_artifact_semantic_receipt"] = {
        "schema": API_ARTIFACT_SEMANTIC_RECEIPT_SCHEMA,
        "artifact_kind": artifact_kind,
        "source_id": source_id,
        "declared_source_type": source_type,
        "operation_count_before_projection": before,
        "operation_count_after_projection": len(operations),
        "postman_request_variant_count": variant_count,
        "har_runtime_observation_count": observation_count,
        "exact_operation_evidence_count": exact_count,
        "exact_operation_evidence_rate": round(exact_count / len(operations), 4)
        if operations
        else 0.0,
        "credential_values_retained": False,
        "source_declared_semantics_added": True,
        "business_flow_inferred": False,
        "document_adapter_added_business_semantics": False,
        "har_is_runtime_observation_not_design_contract": artifact_kind == "har",
    }
    return result


__all__ = [
    "API_ARTIFACT_SEMANTIC_RECEIPT_SCHEMA",
    "POSTMAN_RUNTIME_CONTRACT_SCHEMA",
    "HAR_RUNTIME_OBSERVATION_SCHEMA",
    "enrich_parsed_api_artifact_semantics",
]
