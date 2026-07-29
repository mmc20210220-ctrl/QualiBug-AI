"""Preserve and merge source-declared Job facts on the existing Job asset authority.

The established Job asset and enterprise-operation schemas remain authoritative. This
module retains execution identities that the base normalizer previously dropped and merges
multiple source views of the same ``(platform_type, platform_job_id)`` before behavior
governance. Conflicting exact facts are recorded and block formal promotion; no source wins
silently and no second Job model is created.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Iterable

from .. import job_platform_contract as _contract

_INSTALL_MARKER = "_qualibug_job_asset_governance_installed"
_MERGE_MARKER = "_qualibug_job_asset_cross_source_merge"
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


def _unique_dicts(values: Iterable[Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, dict):
            continue
        key = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
        if key in seen:
            continue
        seen.add(key)
        result.append(dict(value))
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
    if governance and "OPERATOR_GOVERNANCE" not in channels:
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
    asset["source_fact_conflicts"] = _unique_dicts(_list(raw.get("source_fact_conflicts")))
    authority = _dict(asset.get("fact_authority"))
    authority["implementation_confirmation_basis"] = (
        "CONFLICTED_SOURCE_EVIDENCE"
        if asset["source_fact_conflicts"]
        else "EXPLICIT_OPERATOR_GOVERNANCE"
        if governance
        else "CROSS_SOURCE_IMPLEMENTATION_EVIDENCE"
        if len(set(channels) - {"SOURCE_ASSET"}) >= 2
        else "SINGLE_SOURCE_IMPLEMENTATION_EVIDENCE"
    )
    authority["runtime_integrity_behavior_eligible"] = bool(
        not asset["source_fact_conflicts"]
        and (governance or len(set(channels) - {"SOURCE_ASSET"}) >= 2)
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
        "source_fact_conflicts": _list(job_asset.get("source_fact_conflicts")),
    }
    return operation


def _scalar(
    assets: list[dict[str, Any]],
    getter,
    field: str,
    conflicts: list[dict[str, Any]],
) -> str:
    values = _unique_text(getter(row) for row in assets)
    if len(values) > 1:
        conflicts.append({
            "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
            "field": field,
            "values": values,
        })
    return values[0] if values else ""


def _merged_trigger(
    assets: list[dict[str, Any]], conflicts: list[dict[str, Any]]
) -> dict[str, Any]:
    rows = [_dict(row.get("trigger")) for row in assets]
    cron = _scalar(assets, lambda row: _dict(row.get("trigger")).get("cron"), "trigger.cron", conflicts)
    timezone = _scalar(assets, lambda row: _dict(row.get("trigger")).get("timezone"), "trigger.timezone", conflicts)
    event = _scalar(assets, lambda row: _dict(row.get("trigger")).get("event"), "trigger.event", conflicts)
    topic = _scalar(assets, lambda row: _dict(row.get("trigger")).get("topic"), "trigger.topic", conflicts)
    dependency_ref = _scalar(
        assets,
        lambda row: _dict(row.get("trigger")).get("dependency_ref"),
        "trigger.dependency_ref",
        conflicts,
    )
    manual_entry_ref = _scalar(
        assets,
        lambda row: _dict(row.get("trigger")).get("manual_entry_ref"),
        "trigger.manual_entry_ref",
        conflicts,
    )
    types = _unique_text(_text(row.get("type")).upper() for row in rows if _text(row.get("type")).upper() != "UNKNOWN")
    # Manual invocation is an additional platform capability, not a contradiction
    # with the Job's automatic trigger.
    automatic = [value for value in types if value not in {"MANUAL", "API"}]
    if len(automatic) > 1:
        conflicts.append({
            "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
            "field": "trigger.type",
            "values": automatic,
        })
    trigger_type = (
        "CRON" if cron else automatic[0] if automatic else "MANUAL" if manual_entry_ref or "MANUAL" in types else types[0] if types else "UNKNOWN"
    )
    return {
        "type": trigger_type,
        "cron": cron,
        "timezone": timezone,
        "event": event,
        "topic": topic,
        "dependency_ref": dependency_ref,
        "manual_entry_ref": manual_entry_ref,
        "supported_trigger_types": types,
    }


def _merged_runtime(
    assets: list[dict[str, Any]], conflicts: list[dict[str, Any]]
) -> dict[str, Any]:
    scalar_fields = (
        "trigger_ref", "run_identity_ref", "status_query_ref", "step_query_ref",
        "log_query_ref", "cancel_ref", "connector_id",
    )
    runtime: dict[str, Any] = {}
    for field in scalar_fields:
        runtime[field] = _scalar(
            assets,
            lambda row, name=field: _dict(row.get("runtime")).get(name),
            f"runtime.{field}",
            conflicts,
        )
    runtime["terminal_states"] = _unique_text(
        value
        for row in assets
        for value in _list(_dict(row.get("runtime")).get("terminal_states"))
    )
    runtime["success_states"] = _unique_text(
        value
        for row in assets
        for value in _list(_dict(row.get("runtime")).get("success_states"))
    )
    return runtime


def _merged_policy(
    assets: list[dict[str, Any]], conflicts: list[dict[str, Any]]
) -> dict[str, Any]:
    fields = (
        "max_attempts", "backoff_ms", "timeout_ms", "allow_concurrent", "lock_type",
        "shard_count", "misfire_policy",
    )
    result: dict[str, Any] = {}
    for field in fields:
        values: list[Any] = []
        for row in assets:
            value = _dict(row.get("execution_policy")).get(field)
            if value not in (None, "") and value not in values:
                values.append(value)
        if len(values) > 1:
            conflicts.append({
                "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
                "field": f"execution_policy.{field}",
                "values": values,
            })
        if values:
            result[field] = values[0]
    return result


def _merge_job_asset_group(assets: list[dict[str, Any]]) -> dict[str, Any]:
    if len(assets) == 1:
        return dict(assets[0])
    conflicts: list[dict[str, Any]] = []
    platform_type = _scalar(assets, lambda row: row.get("platform_type"), "platform_type", conflicts)
    platform_job_id = _scalar(assets, lambda row: row.get("platform_job_id"), "platform_job_id", conflicts)
    handler = _scalar(assets, lambda row: _dict(row.get("identity")).get("handler"), "identity.handler", conflicts)
    service = _scalar(assets, lambda row: _dict(row.get("identity")).get("service"), "identity.service", conflicts)
    module = _scalar(assets, lambda row: _dict(row.get("identity")).get("module"), "identity.module", conflicts)
    version = _scalar(assets, lambda row: _dict(row.get("identity")).get("version"), "identity.version", conflicts)
    display_name = _scalar(assets, lambda row: row.get("display_name"), "display_name", conflicts) or platform_job_id
    evidence = _unique_dicts(
        evidence
        for row in assets
        for evidence in _list(row.get("evidence"))
    )
    actor_refs = _unique_text(
        value for row in assets for value in _list(row.get("actor_refs"))
    )
    connector_ids = _unique_text(
        [
            *[row.get("connector_id") for row in assets],
            *[
                value
                for row in assets
                for value in _list(row.get("connector_identity_candidates"))
            ],
        ]
    )
    if len(connector_ids) > 1:
        conflicts.append({
            "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
            "field": "connector_id",
            "values": connector_ids,
        })
    behavior_rows = [_dict(row.get("behavior")) for row in assets]
    behavior = {
        "selection_predicates": _unique_dicts(
            value for row in behavior_rows for value in _list(row.get("selection_predicates"))
        ),
        "process_steps": _unique_dicts(
            value for row in behavior_rows for value in _list(row.get("process_steps"))
        ),
        "object_refs": _unique_text(
            value for row in behavior_rows for value in _list(row.get("object_refs"))
        ),
        "read_set": _unique_text(
            value for row in behavior_rows for value in _list(row.get("read_set"))
        ),
        "write_set": _unique_text(
            value for row in behavior_rows for value in _list(row.get("write_set"))
        ),
        "expected_effects": _unique_dicts(
            value for row in behavior_rows for value in _list(row.get("expected_effects"))
        ),
        "external_calls": _unique_text(
            value for row in behavior_rows for value in _list(row.get("external_calls"))
        ),
        "messages": _unique_text(
            value for row in behavior_rows for value in _list(row.get("messages"))
        ),
        "transaction_boundaries": _unique_text(
            value for row in behavior_rows for value in _list(row.get("transaction_boundaries"))
        ),
        "compensation_paths": _unique_text(
            value for row in behavior_rows for value in _list(row.get("compensation_paths"))
        ),
    }
    cleanup = {
        field: _scalar(
            assets,
            lambda row, name=field: _dict(row.get("cleanup")).get(name),
            f"cleanup.{field}",
            conflicts,
        )
        for field in ("mode", "cleanup_ref", "verification_ref", "sandbox_reset_ref")
    }
    governance_receipts = _unique_dicts(
        _dict(row.get("operator_governance_receipt"))
        for row in assets
        if _dict(row.get("operator_governance_receipt"))
    )
    if len(governance_receipts) > 1:
        conflicts.append({
            "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
            "field": "operator_governance_receipt",
            "values": [row.get("receipt_id") for row in governance_receipts],
        })
    raw = {
        "platform_type": platform_type,
        "platform_job_id": platform_job_id,
        "display_name": display_name,
        "handler": handler,
        "service": service,
        "module": module,
        "version": version,
        "actor_refs": actor_refs,
        "connector_id": connector_ids[0] if len(connector_ids) == 1 else "",
        "trigger": _merged_trigger(assets, conflicts),
        "runtime": _merged_runtime(assets, conflicts),
        "execution_policy": _merged_policy(assets, conflicts),
        "behavior": behavior,
        "cleanup": cleanup,
        "source_refs": evidence,
        "operator_governance_receipt": governance_receipts[0]
        if len(governance_receipts) == 1
        else {},
        "source_fact_conflicts": _unique_dicts(
            [
                *conflicts,
                *[
                    conflict
                    for row in assets
                    for conflict in _list(row.get("source_fact_conflicts"))
                ],
            ]
        ),
    }
    merged = normalize_job_definition_with_governance(raw, source_refs=evidence)
    merged["merged_source_job_asset_ids"] = _unique_text(
        row.get("job_asset_id") for row in assets
    )
    return merged


def merge_cross_source_job_assets(assets: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge exact platform Job identities; preserve conflicts instead of guessing."""
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    passthrough: list[dict[str, Any]] = []
    for raw in assets:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        key = (_text(row.get("platform_type")).lower(), _text(row.get("platform_job_id")))
        if not all(key):
            passthrough.append(row)
            continue
        groups.setdefault(key, []).append(row)
    merged = [_merge_job_asset_group(rows) for _key, rows in sorted(groups.items())]
    return [*merged, *passthrough]


def _refresh_enriched_job_assets(enriched: dict[str, Any]) -> dict[str, Any]:
    from . import _job_assets

    merged = merge_cross_source_job_assets(
        row for row in _list(enriched.get("job_assets")) if isinstance(row, dict)
    )
    async_operations = [to_async_operation_with_governance(row) for row in merged]
    model = _dict(enriched.get("enterprise_understanding_model"))
    if model:
        model["operations"] = [
            dict(row)
            for row in _list(model.get("operations"))
            if isinstance(row, dict)
            and _text(row.get("operation_kind")) != _contract.ASYNC_OPERATION_KIND
        ] + async_operations
        metrics = _dict(model.get("metrics"))
        metrics["async_job_operation_count"] = len(async_operations)
        model["metrics"] = metrics
        enriched["enterprise_understanding_model"] = model

    old_gaps = [
        dict(row)
        for row in _list(enriched.get("coverage_gaps"))
        if isinstance(row, dict)
        and (
            not _text(row.get("kind")).startswith("JOB_")
            or not _text(row.get("job_asset_id"))
        )
    ]
    asset_gaps = [gap for row in merged for gap in _job_assets._job_gap_rows(row)]
    for row in merged:
        for conflict in _list(row.get("source_fact_conflicts")):
            if not isinstance(conflict, dict):
                continue
            asset_gaps.append({
                "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
                "job_asset_id": row.get("job_asset_id"),
                "platform_job_id": row.get("platform_job_id"),
                "conflict": dict(conflict),
                "blocks_formal_job_behavior": True,
            })
    status_counts: dict[str, int] = {}
    for row in merged:
        status = _text(_dict(row.get("testability")).get("execution_status")) or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1
    summary = _dict(enriched.get("summary"))
    summary.update({
        "job_asset_count": len(merged),
        "job_async_operation_count": len(async_operations),
        "job_execution_ready_count": status_counts.get("EXECUTION_READY", 0),
        "job_partially_executable_count": status_counts.get("PARTIALLY_EXECUTABLE", 0),
        "job_unsafe_count": status_counts.get("UNSAFE", 0),
        "job_cross_source_merged_count": sum(
            1 for row in merged if len(_list(row.get("merged_source_job_asset_ids"))) > 1
        ),
        "job_source_conflict_count": sum(
            len(_list(row.get("source_fact_conflicts"))) for row in merged
        ),
    })
    enriched["summary"] = summary
    enriched["job_assets"] = merged
    enriched["async_operations"] = async_operations
    asset_summary = _dict(enriched.get("job_asset_summary"))
    asset_summary.update({
        "asset_count": len(merged),
        "execution_status_counts": status_counts,
        "coverage_gap_count": len(asset_gaps),
        "cross_source_merge_enabled": True,
        "source_conflicts_block_formal_behavior": True,
    })
    enriched["job_asset_summary"] = asset_summary
    enriched["coverage_gaps"] = [*old_gaps, *asset_gaps]
    return enriched


def install_job_asset_governance() -> None:
    """Install on existing normalization, enrichment and projection call sites."""
    if getattr(_contract, _INSTALL_MARKER, False):
        return
    from . import _job_assets

    _contract.normalize_job_definition = normalize_job_definition_with_governance
    _contract.to_async_operation = to_async_operation_with_governance
    _job_assets.normalize_job_definition = normalize_job_definition_with_governance
    _job_assets.to_async_operation = to_async_operation_with_governance

    current_enrich = _job_assets.enrich_job_assets
    if not getattr(current_enrich, _MERGE_MARKER, False):
        def enrich_with_cross_source_merge(*args: Any, **kwargs: Any) -> dict[str, Any]:
            return _refresh_enriched_job_assets(current_enrich(*args, **kwargs))

        setattr(enrich_with_cross_source_merge, _MERGE_MARKER, True)
        enrich_with_cross_source_merge._qualibug_original_enrich = current_enrich  # type: ignore[attr-defined]
        _job_assets.enrich_job_assets = enrich_with_cross_source_merge
        parent = sys.modules.get(__package__)
        if parent is not None:
            setattr(parent, "enrich_job_assets", enrich_with_cross_source_merge)

    setattr(_contract, _INSTALL_MARKER, True)


__all__ = [
    "normalize_job_definition_with_governance",
    "to_async_operation_with_governance",
    "merge_cross_source_job_assets",
    "install_job_asset_governance",
]
