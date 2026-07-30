"""Project exact API artifact semantics into the canonical knowledge asset.

This is a composition stage, not a file parser. It consumes source-preserving Document IR and
builds one logical interface with an immutable source-record ledger. Identical parser-generated
interface IDs from multiple artifacts therefore share identity without stealing one another's
evidence. Repeated HAR entries remain runtime observations, never a fabricated design contract.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from typing import Any, Iterable

from .document_ir_api_semantics import enrich_parsed_api_artifact_semantics
from .enterprise_understanding.interface_runtime_contracts import (
    enrich_openapi_runtime_contracts,
)

API_ARTIFACT_ASSET_PROJECTION_SCHEMA = "qualibug.api-artifact-asset-projection.v2"
API_ARTIFACT_SOURCE_RECORD_SCHEMA = "qualibug.api-artifact-source-record.v1"
API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA = "qualibug.api-artifact-contract-conflict.v1"

_SOURCE_EVIDENCE_FIELDS = {
    "source_id",
    "source_locator",
    "json_pointer",
    "document_ir_block_id",
    "document_block_id",
    "evidence_address",
    "source_traceability",
}
_EVIDENCE_ONLY_KEYS = _SOURCE_EVIDENCE_FIELDS | {
    "block_id",
    "block_ids",
    "source_ids",
    "source_locators",
    "json_pointers",
    "evidence_addresses",
    "document_structure_evidence",
}
_CONTRACT_FIELDS = (
    "parameter_contracts",
    "request_body_fields",
    "request_body_media_types",
    "request_body_required",
    "request_body_contracts",
    "response_contracts",
    "security_requirements",
    "postman_request_variants",
    "request_variant_count",
    "script_contracts",
    "response_examples",
    "body_mode",
    "auth_type",
    "technical_declarations",
)


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


def _belongs_exactly_to_source(interface: dict[str, Any], source_id: str) -> bool:
    return bool(source_id and _text(interface.get("source_id")) == source_id)


def _interface_identity(row: dict[str, Any], artifact_kind: str = "") -> str:
    explicit = _text(row.get("interface_id"))
    if explicit:
        return explicit
    prefix = {
        "openapi": "api",
        "postman": "postman",
        "har": "har",
    }.get(artifact_kind, "api")
    return f"{prefix}:{_method(row.get('method'))}:{_path(row.get('path'))}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _strip_evidence(value: Any) -> Any:
    if isinstance(value, list):
        return [_strip_evidence(item) for item in value]
    if not isinstance(value, dict):
        return value
    return {
        str(key): _strip_evidence(item)
        for key, item in value.items()
        if str(key) not in _EVIDENCE_ONLY_KEYS
    }


def _contract_projection(row: dict[str, Any], artifact_kind: str) -> dict[str, Any]:
    result = {
        "artifact_kind": artifact_kind,
        "method": _method(row.get("method")),
        "path": _path(row.get("path")),
    }
    for field in _CONTRACT_FIELDS:
        if row.get(field) not in (None, "", [], {}):
            result[field] = _strip_evidence(deepcopy(row.get(field)))
    return result


def _contract_fingerprint(row: dict[str, Any], artifact_kind: str) -> str:
    return hashlib.sha256(
        _canonical_json(_contract_projection(row, artifact_kind)).encode("utf-8")
    ).hexdigest()


def _openapi_payload(structure: dict[str, Any]) -> dict[str, Any]:
    plain_text = _text(structure.get("plain_text"))
    if not plain_text:
        return {}
    try:
        value = json.loads(plain_text)
    except Exception:
        return {}
    return dict(value) if isinstance(value, dict) else {}


def _openapi_candidates(
    structure: dict[str, Any],
    *,
    source_id: str,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Rebuild source-local OpenAPI rows before global interface-id deduplication."""

    payload = _openapi_payload(structure)
    rows: list[dict[str, Any]] = []
    for raw in _list(structure.get("blocks")):
        if not isinstance(raw, dict) or _text(raw.get("node_kind")) != "OPENAPI_OPERATION":
            continue
        method = _method(raw.get("http_method"))
        path = _path(raw.get("api_path"))
        if not method:
            continue
        rows.append(
            {
                "interface_id": f"api:{method}:{path}",
                "source_id": source_id,
                "source_kind": "openapi",
                "method": method,
                "path": path,
                "operation_id": _text(raw.get("operation_id")),
                "summary": _text(raw.get("text")),
                "tags": list(raw.get("declared_tags") or []),
                "parameters": [],
            }
        )
    if payload and rows:
        rows = enrich_openapi_runtime_contracts(payload, rows)
    return rows, payload


def _source_seed(
    source: dict[str, Any],
    structure: dict[str, Any],
    *,
    source_id: str,
    artifact_kind: str,
    exact_asset_candidates: list[dict[str, Any]],
) -> dict[str, Any]:
    explicit = [
        dict(row)
        for row in _list(source.get("operations"))
        if isinstance(row, dict)
    ]
    candidates = explicit or [dict(row) for row in exact_asset_candidates]
    if artifact_kind == "openapi":
        generated, payload = _openapi_candidates(structure, source_id=source_id)
        operations = candidates or generated
        if payload and operations:
            operations = enrich_openapi_runtime_contracts(payload, operations)
        return {"operations": operations, "openapi": payload}
    return {"operations": candidates}


def _source_record(
    row: dict[str, Any],
    *,
    source_id: str,
    source_type: str,
    artifact_kind: str,
) -> dict[str, Any]:
    record = deepcopy(row)
    record.pop("api_artifact_source_records", None)
    record.update(
        {
            "schema": API_ARTIFACT_SOURCE_RECORD_SCHEMA,
            "source_id": source_id,
            "source_type": source_type,
            "artifact_kind": artifact_kind,
            "interface_id": _interface_identity(row, artifact_kind),
            "method": _method(row.get("method")),
            "path": _path(row.get("path")),
            "contract_fingerprint": _contract_fingerprint(row, artifact_kind),
            "credential_values_retained": False,
            "business_flow_inferred": False,
        }
    )
    return record


def _record_identity(record: dict[str, Any]) -> str:
    material = "\x1f".join(
        [
            _text(record.get("source_id")),
            _text(record.get("artifact_kind")),
            _text(record.get("interface_id")),
            _text(record.get("json_pointer")),
            _text(record.get("contract_fingerprint")),
        ]
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _merge_source_records(
    current: Iterable[Any], incoming: Iterable[dict[str, Any]]
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for raw in [*_list(current), *list(incoming)]:
        if not isinstance(raw, dict):
            continue
        record = deepcopy(raw)
        identity = _record_identity(record)
        if identity in seen:
            continue
        seen.add(identity)
        result.append(record)
    return result


def _record_observations(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(item)
        for item in _list(record.get("runtime_observations"))
        if isinstance(item, dict)
    ]


def _apply_record_ledger(
    canonical: dict[str, Any], records: list[dict[str, Any]]
) -> dict[str, Any]:
    row = deepcopy(canonical)
    row["api_artifact_source_records"] = records
    row["api_artifact_source_record_count"] = len(records)
    row["source_ids"] = sorted(
        {_text(record.get("source_id")) for record in records if _text(record.get("source_id"))}
    )
    row["source_locators"] = sorted(
        {
            locator
            for record in records
            for locator in [
                _text(record.get("source_locator")),
                *[_text(value) for value in _list(record.get("source_locators"))],
                *[
                    _text(item.get("source_locator"))
                    for item in _record_observations(record)
                ],
            ]
            if locator
        }
    )
    row["json_pointers"] = sorted(
        {
            pointer
            for record in records
            for pointer in [
                _text(record.get("json_pointer")),
                *[_text(value) for value in _list(record.get("json_pointers"))],
                *[
                    _text(item.get("json_pointer"))
                    for item in _record_observations(record)
                ],
            ]
            if pointer
        }
    )
    single_observation_set = (
        len(records) == 1
        and len(_record_observations(records[0])) > 1
    )
    if len(records) == 1 and not single_observation_set:
        record = records[0]
        for field in _SOURCE_EVIDENCE_FIELDS:
            if record.get(field) not in (None, "", [], {}):
                row[field] = deepcopy(record.get(field))
        row["evidence_scope"] = "SINGLE_SOURCE_DECLARATION"
        row["canonical_contract_source_id"] = _text(record.get("source_id"))
    else:
        for field in _SOURCE_EVIDENCE_FIELDS:
            row.pop(field, None)
        row["evidence_scope"] = (
            "SINGLE_SOURCE_RUNTIME_OBSERVATION_SET"
            if single_observation_set
            else "MULTI_SOURCE_DECLARATION_LEDGER"
        )
        row["canonical_contract_source_id"] = ""
        row["single_source_evidence_claim_forbidden"] = True
    if single_observation_set:
        observations = _record_observations(records[0])
        row["runtime_observations"] = observations
        row["observation_count"] = len(observations)
        row["runtime_observation_count"] = len(observations)
        row["runtime_observation_statuses"] = sorted(
            {
                _text(item.get("observed_status") or item.get("status"))
                for item in observations
                if _text(item.get("observed_status") or item.get("status"))
            }
        )
        row["contract_authority"] = "runtime_observation_only"
        row["design_contract_inference_allowed"] = False
        row["observation_authority"] = "HAR_RUNTIME_EVIDENCE"
    row["credential_values_retained"] = False
    row["business_flow_inferred"] = False
    return row


def enrich_asset_with_api_artifact_semantics(
    asset: dict[str, Any],
    structured_sources: Iterable[dict[str, Any]],
) -> dict[str, Any]:
    """Attach source ledgers before enterprise relationships consume interfaces."""

    result = dict(asset or {})
    interfaces = [
        deepcopy(row) for row in _list(result.get("interfaces")) if isinstance(row, dict)
    ]
    canonical_by_id: dict[str, dict[str, Any]] = {
        _interface_identity(row): row for row in interfaces
    }
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
        source_type = source_types.get(source_id, "")
        exact_candidates = [
            dict(row)
            for row in interfaces
            if _belongs_exactly_to_source(row, source_id)
            and _text(row.get("source_kind") or row.get("source")).lower()
            in _artifact_source_kind(artifact_kind)
        ]
        seed = _source_seed(
            source,
            structure,
            source_id=source_id,
            artifact_kind=artifact_kind,
            exact_asset_candidates=exact_candidates,
        )
        parsed = enrich_parsed_api_artifact_semantics(
            seed,
            structure,
            source_id=source_id,
            source_type=source_type,
        )
        enriched = [
            dict(row)
            for row in _list(parsed.get("operations"))
            if isinstance(row, dict)
        ]
        for operation in enriched:
            interface_id = _interface_identity(operation, artifact_kind)
            operation["interface_id"] = interface_id
            canonical = canonical_by_id.get(interface_id)
            if canonical is None:
                canonical = deepcopy(operation)
                canonical_by_id[interface_id] = canonical
                interfaces.append(canonical)
            existing_records = [
                dict(row)
                for row in _list(canonical.get("api_artifact_source_records"))
                if isinstance(row, dict)
            ]
            if not existing_records:
                canonical.update(deepcopy(operation))
            records = _merge_source_records(
                existing_records,
                [
                    _source_record(
                        operation,
                        source_id=source_id,
                        source_type=source_type,
                        artifact_kind=artifact_kind,
                    )
                ],
            )
            updated = _apply_record_ledger(canonical, records)
            canonical.clear()
            canonical.update(updated)
        receipt = _dict(parsed.get("api_artifact_semantic_receipt"))
        if receipt:
            receipt["source_record_count"] = len(enriched)
            receipts.append(receipt)
        processed_sources.append(source_id)

    conflicts: list[dict[str, Any]] = []
    for interface in interfaces:
        records = [
            dict(row)
            for row in _list(interface.get("api_artifact_source_records"))
            if isinstance(row, dict)
        ]
        declaration_records = [
            row for row in records if _text(row.get("artifact_kind")) in {"openapi", "postman"}
        ]
        fingerprints = {
            _text(row.get("contract_fingerprint"))
            for row in declaration_records
            if _text(row.get("contract_fingerprint"))
        }
        if len(fingerprints) <= 1:
            if declaration_records:
                interface["source_contract_alignment"] = "AGREED_OR_SINGLE_SOURCE"
            continue
        conflict = {
            "schema": API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA,
            "interface_id": _text(interface.get("interface_id")),
            "method": _method(interface.get("method")),
            "path": _path(interface.get("path")),
            "source_ids": sorted(
                {
                    _text(row.get("source_id"))
                    for row in declaration_records
                    if _text(row.get("source_id"))
                }
            ),
            "contract_fingerprints": sorted(fingerprints),
            "status": "UNRESOLVED_SOURCE_CONTRACT_CONFLICT_CANDIDATE",
            "automatic_winner_selected": False,
            "operator_action": "compare source declarations and select the authoritative version",
        }
        conflicts.append(conflict)
        interface["source_contract_alignment"] = "CONFLICT_CANDIDATE"
        interface["source_contract_conflict_candidate"] = True
        interface["automatic_contract_authority_selected"] = False

    source_record_count = sum(
        len(_list(interface.get("api_artifact_source_records"))) for interface in interfaces
    )
    exact_record_count = sum(
        1
        for interface in interfaces
        for record in _list(interface.get("api_artifact_source_records"))
        if isinstance(record, dict)
        and (
            (_text(record.get("json_pointer")) and _text(record.get("source_locator")))
            or (_record_observations(record))
            or (_list(record.get("json_pointers")) and _list(record.get("source_locators")))
        )
    )
    exact_interface_count = sum(
        1
        for interface in interfaces
        if _list(interface.get("api_artifact_source_records"))
        and (
            (_text(interface.get("json_pointer")) and _text(interface.get("source_locator")))
            or (_list(interface.get("json_pointers")) and _list(interface.get("source_locators")))
        )
    )
    har_observation_count = sum(
        sum(
            int(record.get("observation_count") or record.get("runtime_observation_count") or 0)
            for record in _list(interface.get("api_artifact_source_records"))
            if isinstance(record, dict)
        )
        for interface in interfaces
    )
    har_observation_set_count = sum(
        1
        for interface in interfaces
        for record in _list(interface.get("api_artifact_source_records"))
        if isinstance(record, dict)
        and int(record.get("observation_count") or record.get("runtime_observation_count") or 0) > 1
    )
    unresolved_count = sum(
        int(receipt.get("unresolved_interface_count") or 0) for receipt in receipts
    )
    projection_status = (
        "NOT_APPLICABLE"
        if not receipts
        else "PARTIAL"
        if unresolved_count or conflicts
        else "COMPLETE"
    )
    result["interfaces"] = interfaces
    result["api_artifact_contract_conflicts"] = conflicts
    result["api_artifact_semantic_projection"] = {
        "schema": API_ARTIFACT_ASSET_PROJECTION_SCHEMA,
        "status": projection_status,
        "processed_source_count": len(processed_sources),
        "processed_source_ids": processed_sources,
        "receipt_count": len(receipts),
        "receipts": receipts,
        "interface_count": len(interfaces),
        "source_record_count": source_record_count,
        "exact_pointer_source_record_count": exact_record_count,
        "exact_pointer_source_record_rate": (
            round(exact_record_count / source_record_count, 4)
            if source_record_count
            else 1.0
        ),
        "exact_pointer_interface_count": exact_interface_count,
        "exact_pointer_interface_rate": (
            round(exact_interface_count / len(interfaces), 4) if interfaces else 1.0
        ),
        "unresolved_interface_count": unresolved_count,
        "contract_conflict_candidate_count": len(conflicts),
        "har_runtime_observation_count": har_observation_count,
        "har_observation_set_count": har_observation_set_count,
        "source_scoped_projection": True,
        "logical_interface_identity_shared_across_sources": True,
        "single_source_evidence_claim_for_multi_source_interface_forbidden": True,
        "credential_values_retained": False,
        "business_flow_inferred": False,
        "container_parsing_performed": False,
    }
    summary = _dict(result.get("summary"))
    summary.update(
        {
            "api_artifact_source_count": len(processed_sources),
            "api_artifact_source_record_count": source_record_count,
            "api_artifact_exact_pointer_source_record_count": exact_record_count,
            "api_artifact_exact_pointer_interface_count": exact_interface_count,
            "api_artifact_unresolved_interface_count": unresolved_count,
            "api_artifact_contract_conflict_candidate_count": len(conflicts),
        }
    )
    result["summary"] = summary
    governance = _dict(result.get("governance"))
    governance.update(
        {
            "api_artifact_semantics_use_document_ir": bool(receipts),
            "api_artifact_evidence_is_source_scoped": True,
            "multi_source_api_evidence_uses_source_record_ledger": True,
            "single_source_pointer_cannot_represent_multiple_sources": True,
            "api_contract_conflict_winner_requires_authority_decision": True,
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
    "API_ARTIFACT_SOURCE_RECORD_SCHEMA",
    "API_ARTIFACT_CONTRACT_CONFLICT_SCHEMA",
    "enrich_asset_with_api_artifact_semantics",
]
