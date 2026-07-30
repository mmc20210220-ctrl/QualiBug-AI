"""Explicit governed Job-asset stage for the knowledge composition root.

The legacy implementation installed governed normalizers by replacing functions in
``_job_assets`` and ``job_platform_contract``.  This stage calls the governed
functions directly, so repeated builds have one deterministic call graph and do not
change process-global behavior.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from ..job_platform_contract import ASYNC_OPERATION_KIND
from . import _job_assets as _base
from .job_asset_governance import (
    merge_cross_source_job_assets,
    normalize_job_definition_with_governance,
    to_async_operation_with_governance,
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def enrich_job_assets_with_governance(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Discover, normalize, merge and project Jobs through explicit authorities."""
    resolved_options = dict(options or {})
    connector_sources, connector_definitions, connector_gaps = _base._connector_job_sources(
        project_id, root, resolved_options
    )
    source_definitions, source_gaps = _base._definitions_from_source_inventory(asset, root)
    explicit_definitions = [
        dict(row)
        for row in _list(resolved_options.get("job_definitions"))
        if isinstance(row, dict)
    ]
    definitions = [*connector_definitions, *source_definitions, *explicit_definitions]

    normalized: list[dict[str, Any]] = []
    normalize_gaps: list[dict[str, Any]] = []
    for index, raw in enumerate(definitions):
        try:
            normalized.append(
                normalize_job_definition_with_governance(
                    raw,
                    source_refs=_list(raw.get("source_refs")),
                )
            )
        except (TypeError, ValueError) as exc:
            normalize_gaps.append(
                {
                    "kind": "JOB_DEFINITION_NOT_NORMALIZED",
                    "definition_index": index,
                    "platform_job_id": raw.get("platform_job_id") or raw.get("job_id"),
                    "reason": str(exc),
                }
            )

    job_assets = merge_cross_source_job_assets(
        _base._dedupe(normalized, "job_asset_id")
    )
    async_operations = _base._dedupe(
        [to_async_operation_with_governance(row) for row in job_assets],
        "operation_id",
    )

    model = _dict(asset.get("enterprise_understanding_model"))
    if model:
        non_job_operations = [
            dict(row)
            for row in _list(model.get("operations"))
            if isinstance(row, dict)
            and _text(row.get("operation_kind")) != ASYNC_OPERATION_KIND
        ]
        model["operations"] = _base._dedupe(
            [*non_job_operations, *async_operations], "operation_id"
        )
        metrics = _dict(model.get("metrics"))
        metrics["async_job_operation_count"] = len(async_operations)
        model["metrics"] = metrics
        asset["enterprise_understanding_model"] = model

    old_gaps = [
        dict(row)
        for row in _list(asset.get("coverage_gaps"))
        if isinstance(row, dict)
        and not _text(row.get("kind")).startswith("JOB_")
        and _text(row.get("kind")) != "ASYNC_JOB_SOURCE_FACT_CONFLICT"
    ]
    job_gaps = [
        *connector_gaps,
        *source_gaps,
        *normalize_gaps,
        *[gap for row in job_assets for gap in _base._job_gap_rows(row)],
    ]
    for row in job_assets:
        for conflict in _list(row.get("source_fact_conflicts")):
            if not isinstance(conflict, dict):
                continue
            job_gaps.append(
                {
                    "kind": "ASYNC_JOB_SOURCE_FACT_CONFLICT",
                    "job_asset_id": row.get("job_asset_id"),
                    "platform_job_id": row.get("platform_job_id"),
                    "conflict": dict(conflict),
                    "blocks_formal_job_behavior": True,
                }
            )

    status_counts: dict[str, int] = {}
    for row in job_assets:
        status = _text(_dict(row.get("testability")).get("execution_status")) or "UNKNOWN"
        status_counts[status] = status_counts.get(status, 0) + 1

    summary = _dict(asset.get("summary"))
    summary.update(
        {
            "job_platform_source_count": len(connector_sources),
            "job_asset_count": len(job_assets),
            "job_async_operation_count": len(async_operations),
            "job_execution_ready_count": status_counts.get("EXECUTION_READY", 0),
            "job_partially_executable_count": status_counts.get("PARTIALLY_EXECUTABLE", 0),
            "job_unsafe_count": status_counts.get("UNSAFE", 0),
            "job_cross_source_merged_count": sum(
                1
                for row in job_assets
                if len(_list(row.get("merged_source_job_asset_ids"))) > 1
            ),
            "job_source_conflict_count": sum(
                len(_list(row.get("source_fact_conflicts"))) for row in job_assets
            ),
            "job_customer_confirmation_required_count": 0,
            "customer_manual_job_creation_count": 0,
            "customer_manual_job_step_configuration_count": 0,
            "customer_manual_job_oracle_authoring_count": 0,
            "customer_manual_job_cleanup_authoring_count": 0,
        }
    )
    asset["summary"] = summary
    asset["job_platform_sources"] = connector_sources
    asset["job_assets"] = job_assets
    asset["async_operations"] = async_operations
    asset["job_asset_summary"] = {
        "schema": _base.JOB_ASSET_ENRICHMENT_SCHEMA,
        "source_count": len(connector_sources),
        "asset_count": len(job_assets),
        "execution_status_counts": status_counts,
        "coverage_gap_count": len(job_gaps),
        "cross_source_merge_enabled": True,
        "source_conflicts_block_formal_behavior": True,
        "automatic_discovery_only": True,
        "manual_job_editor_present": False,
        "customer_effort_contract": {
            "manual_job_creation": 0,
            "manual_step_configuration": 0,
            "manual_field_binding": 0,
            "manual_test_case_authoring": 0,
            "manual_oracle_authoring": 0,
            "manual_cleanup_authoring": 0,
            "long_text_input": 0,
        },
        "fact_authority_contract": (
            "source code and platform configuration describe implemented behavior; "
            "they are not automatically promoted to business expectation"
        ),
    }
    asset["coverage_gaps"] = [*old_gaps, *job_gaps]
    return asset


__all__ = ["enrich_job_assets_with_governance"]
