"""Project exact API artifact semantics into the canonical knowledge asset.

This is a composition stage, not a parser. It consumes interfaces already extracted by the
knowledge compiler and source-preserving Document IR prepared by the explicit composition
root. One source is processed at a time so identical method/path pairs from different
artifacts cannot steal each other's evidence. Repeated HAR entries remain an observation
set instead of being collapsed into a fabricated design contract.
"""
from __future__ import annotations

import hashlib
import re
from collections import defaultdict
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
    replacement_rows = [dict(row) for row in replacements if isinstance(row, dict)]
    for row in [*retained, *replacement_rows]:
        if row in replacement_rows and not _text(row.get("source_id")):
            row["source_id"] = source_id
        identity = _interface_identity(row)
        row.setdefault("interface_id", identity)
        if identity in seen:
            continue
        seen.add(identity)
        merged.append(row)
    return merged


def _har_observation_sets(
    structure: dict[str, Any],
) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for raw in _list(structure.get("blocks")):
        if not isinstance(raw, dict) or _text(raw.get("node_kind")) != "HAR_ENTRY":
            continue
        row = dict(raw)
        key = (_method(row.get("http_method")), _path(row.get("api_path")))
        address = _dict(row.get("evidence_address"))
        grouped[key].append(
            {
                "block_id": _text(row.get("block_id")),
                "json_pointer": _text(row.get("json_pointer")),
                "source_locator": _text(
                    row.get("source_locator") or address.get("source_locator")
                ),
                "status": row.get("response_status"),
                "elapsed_ms": row.get("elapsed_ms"),
                "response_mime_type": _text(row.get("response_mime_type")),
                "address_kind": _text(address.get("address_kind"))
                or "EXACT_SOURCE_LOCATOR",
                "credential_values_retained": False,
            }
        )
    return grouped


def _attach_har_observation_sets(
    operations: list[dict[str, Any]], structure: dict[str, Any]
) -> list[dict[str, Any]]:
    observations = _har_observation_sets(structure)
    result: list[dict[str, Any]] = []
    for raw in operations:
        row = dict(raw)
        key = (_method(row.get("method")), _path(row.get("path")))
        items = observations.get(key, [])
        if items:
            row["runtime_observations"] = items
            row["runtime_observation_count"] = len(items)
            row["source_locators"] = [
                item["source_locator"] for item in items if item["source_locator"]
            ]
            row["json_pointers"] = [
                item["json_pointer"] for item in items if item["json_pointer"]
            ]
            row["runtime_observation_statuses"] = sorted(
                {
                    str(item["status"])
                    for item in items
                    if item.get("status") not in (None, "")
                }
            )
            row["source_kind"] = "har_observation"
            row["contract_authority"] = "runtime_observation_only"
            row["design_contract_inference_allowed"] = False
            if len(items) == 1:
                row["source_locator"] = items[0]["source_locator"]
                row["json_pointer"] = items[0]["json_pointer"]
                row["document_ir_block_id"] = items[0]["block_id"]
            else:
                row.pop("source_locator", None)
                row.pop("json_pointer", None)
                row.pop("document_ir_block_id", None)
        result.append(row)
    return result


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
        if artifact_kind == "har":
            enriched = _attach_har_observation_sets(enriched, structure)
        interfaces = _merge_interfaces(
            interfaces,
            enriched,
            source_id=source_id,
            artifact_kind=artifact_kind,
        )
        receipt = _dict(parsed.get("api_artifact_semantic_receipt"))
        if receipt:
            receipt["runtime_observation_set_count"] = sum(
                1 for row in enriched if int(row.get("runtime_observation_count") or 0) > 1
            )
            receipts.append(receipt)
        processed_sources.append(source_id)

    exact_count = sum(
        1
        for row in interfaces
        if (
            _text(row.get("json_pointer")) and _text(row.get("source_locator"))
        )
        or (
            _list(row.get("json_pointers")) and _list(row.get("source_locators"))
        )
    )
    unresolved_count = sum(
        int(receipt.get("unresolved_interface_count") or 0) for receipt in receipts
    )
    projection_status = (
        "NOT_APPLICABLE"
        if not receipts
        else "PARTIAL"
        if unresolved_count
        else "COMPLETE"
    )
    result["interfaces"] = interfaces
    result["api_artifact_semantic_projection"] = {
        "schema": API_ARTIFACT_ASSET_PROJECTION_SCHEMA,
        "status": projection_status,
        "processed_source_count": len(processed_sources),
        "processed_source_ids": processed_sources,
        "receipt_count": len(receipts),
        "receipts": receipts,
        "interface_count": len(interfaces),
        "exact_pointer_interface_count": exact_count,
        "exact_pointer_interface_rate": (
            round(exact_count / len(interfaces), 4) if interfaces else 1.0
        ),
        "unresolved_interface_count": unresolved_count,
        "har_runtime_observation_count": sum(
            int(row.get("runtime_observation_count") or 0) for row in interfaces
        ),
        "har_observation_set_count": sum(
            1 for row in interfaces if int(row.get("runtime_observation_count") or 0) > 1
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
            "api_artifact_unresolved_interface_count": unresolved_count,
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "api_artifact_semantics_use_document_ir": bool(receipts),
            "api_artifact_evidence_is_source_scoped": True,
            "har_is_runtime_observation_not_design_contract": True,
            "har_repeated_observations_are_not_collapsed": True,
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
