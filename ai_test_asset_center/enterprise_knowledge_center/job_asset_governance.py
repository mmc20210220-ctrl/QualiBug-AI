"""Preserve source-declared Job governance fields through existing normalization.

The established Job asset and enterprise-operation schemas remain authoritative.  This
module only retains execution identities that the base normalizer previously dropped:
actor, connector, success states and an optional operator-governance receipt.  It never
creates a second Job model and never upgrades implementation evidence to a business Oracle.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from .. import job_platform_contract as _contract

_INSTALL_MARKER = "_qualibug_job_asset_governance_installed"
_BASE_NORMALIZE = _contract.normalize_job_definition
_BASE_TO_OPERATION = _contract.to_async_operation
_CODE_SUFFIXES = {
    ".java", ".kt", ".kts", ".groovy", ".py", ".js", ".ts", ".tsx", ".go", ".cs",
    ".rb", ".php", ".scala", ".sh", ".sql",
}
_DOCUMENT_SUFFIXES = {".md", ".txt", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique_text(values: Iterable[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _evidence_channel(row: dict[str, Any]) -> str:
    explicit = _text(row.get("source_kind") or row.get("kind")).upper()
    if explicit in {
        "JOB_PLATFORM", "PLATFORM_CONFIGURATION", "SOURCE_CODE", "BUSINESS_DOCUMENT",
        "RUNTIME", "OPERATOR_GOVERNANCE",
    }:
        return explicit
    if _text(row.get("connector_id")) or _text(row.get("external_ref")).lower().startswith(
        "job_platform:"
    ):
        return "JOB_PLATFORM"
    derivation = _text(row.get("derivation")).lower()
    if "runtime" in derivation or "observ" in derivation:
        return "RUNTIME"
    locator = _text(row.get("source_locator") or row.get("locator") or row.get("asset_ref"))
    suffix = Path(locator.split("#", 1)[0]).suffix.lower()
    if suffix in _CODE_SUFFIXES or "source_backed_job_discovery" in derivation:
        return "SOURCE_CODE"
    if suffix in _DOCUMENT_SUFFIXES:
        return "BUSINESS_DOCUMENT"
    return "SOURCE_ASSET"


def _valid_governance_receipt(raw: Any) -> dict[str, Any]:
    receipt = _dict(raw)
    status = _text(receipt.get("status")).upper()
    scope = _text(receipt.get("authority_scope") or receipt.get("scope")).upper()
    if (
        not _text(receipt.get("receipt_id"))
        or status not in {"CONFIRMED", "ACCEPTED", "VALID"}
        or not scope
        or not (
            _text(receipt.get("confirmed_by"))
            or _text(receipt.get("actor_ref"))
            or _text(receipt.get("operator_id"))
        )
    ):
        return {}
    return dict(receipt)


def normalize_job_definition_with_governance(
    raw: dict[str, Any],
    *,
    source_refs: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    """Run the existing normalizer, then retain exact source-declared identities."""
    asset = _BASE_NORMALIZE(raw, source_refs=source_refs)
    runtime_raw = _dict(raw.get("runtime"))
    evidence = [row for row in _list(asset.get("evidence")) if isinstance(row, dict)]

    actor_refs = _unique_text(
        [
            *_list(raw.get("actor_refs")),
            raw.get("actor_ref"),
            raw.get("execution_actor_ref"),
            raw.get("service_account_ref"),
            runtime_raw.get("actor_ref"),
            runtime_raw.get("service_account_ref"),
        ]
    )
    success_states = _unique_text(
        [
            *_list(runtime_raw.get("success_states")),
            *_list(raw.get("success_states")),
        ]
    )
    connector_ids = _unique_text(
        [
            raw.get("connector_id"),
            runtime_raw.get("connector_id"),
            *[row.get("connector_id") for row in evidence],
        ]
    )
    governance = _valid_governance_receipt(
        raw.get("operator_governance_receipt") or raw.get("governance_receipt")
    )
    channels = _unique_text(_evidence_channel(row) for row in evidence)
    if governance:
        channels.append("OPERATOR_GOVERNANCE")

    asset["actor_refs"] = actor_refs
    asset["runtime"] = {
        **_dict(asset.get("runtime")),
        "success_states": success_states,
        **({"connector_id": connector_ids[0]} if len(connector_ids) == 1 else {}),
    }
    asset["connector_id"] = connector_ids[0] if len(connector_ids) == 1 else ""
    asset["connector_identity_candidates"] = connector_ids
    asset["evidence_channels"] = channels
    asset["operator_governance_receipt"] = governance
    authority = _dict(asset.get("fact_authority"))
    authority["implementation_confirmation_basis"] = (
        "EXPLICIT_OPERATOR_GOVERNANCE"
        if governance
        else "CROSS_SOURCE_IMPLEMENTATION_EVIDENCE"
        if len(set(channels) - {"SOURCE_ASSET"}) >= 2
        else "SINGLE_SOURCE_IMPLEMENTATION_EVIDENCE"
    )
    authority["runtime_integrity_behavior_eligible"] = bool(
        governance or len(set(channels) - {"SOURCE_ASSET"}) >= 2
    )
    authority["formal_business_oracle_eligible"] = False
    asset["fact_authority"] = authority
    return asset


def to_async_operation_with_governance(job_asset: dict[str, Any]) -> dict[str, Any]:
    """Project retained identities into the existing enterprise operation."""
    operation = _BASE_TO_OPERATION(job_asset)
    actor_refs = _unique_text(_list(job_asset.get("actor_refs")))
    runtime = _dict(job_asset.get("runtime"))
    contract = _dict(operation.get("async_contract"))
    operation["actor_refs"] = actor_refs
    operation["method"] = "JOB"
    operation["adapter"] = "job_platform"
    operation["read_write"] = (
        "read" if not _list(contract.get("write_set")) else "write"
    )
    operation["async_contract"] = {
        **contract,
        "connector_id": _text(job_asset.get("connector_id")),
        "actor_refs": actor_refs,
        "runtime": {
            **_dict(contract.get("runtime")),
            "success_states": _list(runtime.get("success_states")),
            **(
                {"connector_id": _text(job_asset.get("connector_id"))}
                if _text(job_asset.get("connector_id"))
                else {}
            ),
        },
        "operator_governance_receipt": _dict(
            job_asset.get("operator_governance_receipt")
        ),
        "evidence_channels": _list(job_asset.get("evidence_channels")),
    }
    return operation


def install_job_asset_governance() -> None:
    """Install on the existing normalizer/projection call sites; idempotent."""
    if getattr(_contract, _INSTALL_MARKER, False):
        return
    from . import _job_assets

    _contract.normalize_job_definition = normalize_job_definition_with_governance
    _contract.to_async_operation = to_async_operation_with_governance
    _job_assets.normalize_job_definition = normalize_job_definition_with_governance
    _job_assets.to_async_operation = to_async_operation_with_governance
    setattr(_contract, _INSTALL_MARKER, True)


__all__ = [
    "normalize_job_definition_with_governance",
    "to_async_operation_with_governance",
    "install_job_asset_governance",
]
