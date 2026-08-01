"""Reconcile technical identity Unknowns after authoritative bindings resolve.

All technical binding projections use this single post-condition authority.  It removes
only ``CROSS_SOURCE_IDENTITY_UNRESOLVED`` records for artifacts that now have a formal
binding, updates aggregate unresolved lists and their evidence, and preserves every other
Unknown reason unchanged.
"""
from __future__ import annotations

from typing import Any, Iterable

from .schema import as_dict, as_list, dedupe_evidence, text

UNRESOLVED_TECHNICAL_IDENTITY = "CROSS_SOURCE_IDENTITY_UNRESOLVED"


def _reason(row: dict[str, Any]) -> str:
    reason = text(row.get("reason_code") or row.get("kind"))
    if reason:
        return reason
    details = as_dict(row.get("details"))
    return (
        UNRESOLVED_TECHNICAL_IDENTITY
        if "unresolved_artifacts" in details
        else ""
    )


def _artifact_ref(row: dict[str, Any]) -> str:
    details = as_dict(row.get("details"))
    return text(details.get("artifact_ref") or row.get("artifact_ref"))


def reconcile_resolved_technical_identity_unknowns(
    unknowns: Iterable[dict[str, Any]], resolved_artifacts: Iterable[str]
) -> list[dict[str, Any]]:
    """Remove resolved artifacts from direct and aggregate technical Unknowns.

    The function is intentionally fail-closed: only the canonical unresolved-identity
    reason is mutated.  Validation, authorization and other Unknown kinds remain intact
    even when they happen to mention the same artifact.
    """
    resolved = {text(value) for value in resolved_artifacts if text(value)}
    reconciled: list[dict[str, Any]] = []
    for raw in unknowns:
        if not isinstance(raw, dict):
            continue
        row = dict(raw)
        if _reason(row) != UNRESOLVED_TECHNICAL_IDENTITY:
            reconciled.append(row)
            continue

        if _artifact_ref(row) in resolved:
            continue

        details = dict(as_dict(row.get("details")))
        if "unresolved_artifacts" not in details:
            reconciled.append(row)
            continue

        unresolved = [
            dict(value)
            for value in as_list(details.get("unresolved_artifacts"))
            if isinstance(value, dict)
            and text(value.get("artifact_ref")) not in resolved
        ]
        if not unresolved:
            continue

        details["unresolved_artifacts"] = unresolved
        row["details"] = details
        row["question"] = (
            f"存在{len(unresolved)}个技术资产尚未通过源声明绑定到业务身份；"
            "系统不会按名称相似自动合并。"
        )
        remaining = {
            text(value.get("artifact_ref"))
            for value in unresolved
            if text(value.get("artifact_ref"))
        }
        row["evidence"] = dedupe_evidence(
            evidence
            for evidence in as_list(row.get("evidence"))
            if isinstance(evidence, dict)
            and (
                not text(evidence.get("asset_ref"))
                or text(evidence.get("asset_ref")) in remaining
            )
        )
        reconciled.append(row)
    return reconciled


__all__ = [
    "UNRESOLVED_TECHNICAL_IDENTITY",
    "reconcile_resolved_technical_identity_unknowns",
]
