"""Project exact API artifact semantics into the canonical knowledge asset.

This is a composition stage, not a parser. It consumes interfaces already extracted by the
knowledge compiler and source-preserving Document IR prepared by the explicit composition
root. One source is processed at a time so identical method/path pairs from different
artifacts cannot steal each other's evidence.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Iterable

from .document_ir_api_semantics import enrich_parsed_api_artifact_semantics

API_ARTIFACT_ASSET_PROJECTION_SCHEMA = "qualibug.api-artifact-asset-projection.v1"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _method(value: Any) -> str:
    return _text(value).upper()


def _path(value: Any) -> str:
    raw = _text(value)
    if not raw:
        return "/"
    raw = re.sub(r"^https?://[^/]+", "", raw).split("?", 1)[0]
    return raw or "/"


def _artifact_kind(structure: dict[str, Any]) -> str:
    return _text(
        _dict(structure.get("artifact_structure")).get("artifact_kind")
        or _dict(structure.get("structure_receipt")).get("artifact_kind")
    ).lower()


def _source_type_map(asset: dict[str, Any]) -> dict[str, str]:
    return {
        _text(row.get("source_id")): _text(row.get("source_type"))
        for row in _list(asset.get("source_inventory"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    }


def _artifact_source_kind(kind: str) -> set[str]:
    return {
        "openapi": {"openapi"},
        "postman": {"postman"},
        "har": {"har", "har_traffic", "har_observation"},
    }.get(kind, set())


def _belongs_to_source(
    interface: dict[str, Any],
    *,
    source_id: str,
    artifact_kind: str,
) -> bool:
    row_source = _text(interface.get("source_id"))
    if row_source:
        return row_source == source_id
    row_kind = _text(interface.get("source_kind") or interface.get("source")).lower()
    return row_kind in _artifact_source_kind(artifact_kind)


def _interface_identity(row: dict[str, Any]) -> str:
    explicit = _text(row.get("interface_id"))
    if explicit:
        return explicit
    material = "\x1f".join(
        [
            _text(row.get("source_id")),
            _text(row.get("source_kind")),
            _method(row.get("method")),
            _path(row.get("path")),
            _text(row.get("operation_id")),
            _text(row.get("json_pointer")),
        ]
    )
    return "api:" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


def _merge_interfaces(
    original: list[dict[str, Any]],
    replacements: list[dict[str, Any]],
    *,
    source_id: str,
    artifact_kind: str,
) -> list[dict[str, Any]]:
    retained = [
        dict(row)
        for row in original
        if not _belongs_to_source(row, source_id=source_id, artifact_kind=artifact_kind)
    ]
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for raw in [*retained, *replacements]:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        row.setdefault("source_id", source_id if raw in replacements else row.get("source_id"))
        identity = _interface_identity(row)
        row.setdefault("interface_id", identity)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(row)
    return merged


def enrich_asset_with_api_artifact_semantics(
    asset: dict[str, Any],
    structured_sources: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Attach exact API declarations/observations before enterprise understanding runs."""

    result = dict(asset or {})
    interfaces = [
        dict(row) for row in _list(result.get("interfaces")) if isinstance(row, dict)
    ]
    source_types = _source_type_map(result)
    receipts: list[dict[str, Any]] = []
    processed_sources: list[str] = []
    for source in structured_sources:
        if not isinstance(source, dict):
            continue
        source_id = _text(source.get("source_id"))
        structure = _dict(source.get("document_structure"))
        artifact_kind = _artifact_kind(structure)
        if not source_id or artifact_kind not in {"openapi", "postman", "har"}:
            continue
        candidates = [
            dict(row)
            for row in interfaces
            if _belongs_to_source(row, source_id=source_id, artifact_kind=artifact_kind)
        ]
        parsed = enrich_parsed_api_artifact_semantics(
            {"operations": candidates},
            structure,
            source_id=source_id,
            source_type=source_types.get(source_id, ""),
        )
        enriched = [
            dict(row)
            for row in _list(parsed.get("operations"))
            if isinstance(row, dict)
        ]
        interfaces = _merge_interfaces(
            interfaces,
            enriched,
            source_id=source_id,
            artifact_kind=artifact_kind,
        )
        receipt = _dict(parsed.get("api_artifact_semantic_receipt"))
        if receipt:
            receipts.append(receipt)
        processed_sources.append(source_id)

    exact_count = sum(
        1
        for row in interfaces
        if _text(row.get("json_pointer")) and _text(row.get("source_locator"))
    )
    result["interfaces"] = interfaces
    result["api_artifact_semantic_projection"] = {
        "schema": API_ARTIFACT_ASSET_PROJECTION_SCHEMA,
        "status": "COMPLETE" if receipts else "NOT_APPLICABLE",
        "processed_source_count": len(processed_sources),
        "processed_source_ids": processed_sources,
        "receipt_count": len(receipts),
        "receipts": receipts,
        "interface_count": len(interfaces),
        "exact_pointer_interface_count": exact_count,
        "exact_pointer_interface_rate": (
            round(exact_count / len(interfaces), 4) if interfaces else 1.0
        ),
        "source_scoped_projection": True,
        "credential_values_retained": False,
        "business_flow_inferred": False,
        "container_parsing_performed": False,
    }
    summary = _dict(result.get("summary"))
    summary.update(
        {
            "api_artifact_source_count": len(processed_sources),
            "api_artifact_exact_pointer_interface_count": exact_count,
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "api_artifact_semantics_use_document_ir": bool(receipts),
            "api_artifact_evidence_is_source_scoped": True,
            "har_is_runtime_observation_not_design_contract": True,
            "postman_credentials_are_not_retained": True,
            "api_artifact_business_flow_inference_forbidden": True,
        }
    )
    result["governance"] = governance
    return result


__all__ = [
    "API_ARTIFACT_ASSET_PROJECTION_SCHEMA",
    "enrich_asset_with_api_artifact_semantics",
]
