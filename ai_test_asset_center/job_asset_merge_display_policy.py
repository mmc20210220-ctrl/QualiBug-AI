"""Keep Job display labels out of formal cross-source conflict authority.

A platform may call a Job ``日报聚合`` while source code declares the exact handler key
``report-daily``.  Those labels are presentation aliases, not contradictory execution
facts.  This compatibility installer wraps the existing Job asset merge function only when
that module is already part of the active enterprise-knowledge composition.  Exact fields
such as cron, handler, connector, actor and terminal states retain their fail-closed conflict
semantics.
"""
from __future__ import annotations

import sys
from typing import Any

_INSTALL_MARKER = "_qualibug_display_name_merge_policy"
_GOVERNANCE_MODULE = (
    "ai_test_asset_center.enterprise_knowledge_center.job_asset_governance"
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _unique(values: list[Any]) -> list[str]:
    result: list[str] = []
    for value in values:
        text = _text(value)
        if text and text not in result:
            result.append(text)
    return result


def _is_platform_view(asset: dict[str, Any]) -> bool:
    for row in _list(asset.get("evidence")):
        if not isinstance(row, dict):
            continue
        if _text(row.get("source_kind") or row.get("kind")).upper() in {
            "JOB_PLATFORM",
            "PLATFORM_CONFIGURATION",
        }:
            return True
        if _text(row.get("connector_id")) or _text(
            row.get("external_ref")
        ).lower().startswith("job_platform:"):
            return True
    return False


def _preferred_display_name(assets: list[dict[str, Any]]) -> tuple[str, list[str]]:
    platform_job_id = next(
        (_text(row.get("platform_job_id")) for row in assets if _text(row.get("platform_job_id"))),
        "",
    )
    variants = _unique([row.get("display_name") for row in assets])
    platform_labels = _unique(
        [
            row.get("display_name")
            for row in assets
            if _is_platform_view(row)
            and _text(row.get("display_name"))
            and _text(row.get("display_name")) != platform_job_id
        ]
    )
    descriptive_labels = [value for value in variants if value != platform_job_id]
    preferred = (
        platform_labels[0]
        if platform_labels
        else descriptive_labels[0]
        if descriptive_labels
        else variants[0]
        if variants
        else platform_job_id
    )
    return preferred, variants


def install_job_asset_display_name_policy() -> bool:
    """Patch the loaded merge authority without importing a second composition root."""
    governance = sys.modules.get(_GOVERNANCE_MODULE)
    if governance is None:
        return False
    current = getattr(governance, "_merge_job_asset_group", None)
    if not callable(current):
        return False
    if getattr(current, _INSTALL_MARKER, False):
        return True
    original = current

    def merge_without_display_conflict(
        assets: list[dict[str, Any]],
    ) -> dict[str, Any]:
        preferred, variants = _preferred_display_name(assets)
        normalized_inputs: list[dict[str, Any]] = []
        for raw in assets:
            row = dict(raw)
            if preferred:
                row["display_name"] = preferred
            prior_conflicts = [
                dict(conflict)
                for conflict in _list(row.get("source_fact_conflicts"))
                if isinstance(conflict, dict)
                and _text(conflict.get("field")) != "display_name"
            ]
            row["source_fact_conflicts"] = prior_conflicts
            normalized_inputs.append(row)

        merged = dict(original(normalized_inputs))
        remaining_conflicts = [
            dict(conflict)
            for conflict in _list(merged.get("source_fact_conflicts"))
            if isinstance(conflict, dict)
            and _text(conflict.get("field")) != "display_name"
        ]
        merged["source_fact_conflicts"] = remaining_conflicts
        merged["display_name"] = preferred or _text(merged.get("display_name"))
        merged["display_name_variants"] = variants
        merged["display_name_conflict_policy"] = "NON_AUTHORITATIVE_VARIANT"

        authority = _dict(merged.get("fact_authority"))
        if remaining_conflicts:
            authority["implementation_confirmation_basis"] = (
                "CONFLICTED_SOURCE_EVIDENCE"
            )
            authority["runtime_integrity_behavior_eligible"] = False
        else:
            channels = {
                _text(value)
                for value in _list(merged.get("evidence_channels"))
                if _text(value) and _text(value) != "SOURCE_ASSET"
            }
            governance_receipt = _dict(
                merged.get("operator_governance_receipt")
            )
            authority["implementation_confirmation_basis"] = (
                "EXPLICIT_OPERATOR_GOVERNANCE"
                if governance_receipt
                else "CROSS_SOURCE_IMPLEMENTATION_EVIDENCE"
                if len(channels) >= 2
                else "SINGLE_SOURCE_IMPLEMENTATION_EVIDENCE"
            )
            authority["runtime_integrity_behavior_eligible"] = bool(
                governance_receipt or len(channels) >= 2
            )
        authority["formal_business_oracle_eligible"] = False
        merged["fact_authority"] = authority
        return merged

    setattr(merge_without_display_conflict, _INSTALL_MARKER, True)
    merge_without_display_conflict._qualibug_original_merge = original  # type: ignore[attr-defined]
    setattr(governance, "_merge_job_asset_group", merge_without_display_conflict)
    return True


__all__ = ["install_job_asset_display_name_policy"]
