"""Authority-scoped projection of shadow findings for the private evaluator.

Product scope remains empty during replay/shadow runs.  This boundary is the
only place that may reinterpret an explicitly recorded semantic delivery-gate
result as evaluator input.
"""
from __future__ import annotations

from typing import Any

from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)


EVALUATOR_PROJECTION_SCHEMA = "qualibug.evaluator-only-finding-projection.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _finding_id(item: dict[str, Any]) -> str:
    return _text(
        item.get("finding_id")
        or item.get("id")
        or item.get("bug_id")
        or item.get("risk_id")
    )


def _shadow_rows(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    outer = scan_result.get("shadow_findings")
    v12 = _dict(scan_result.get("v12"))
    nested = v12.get("shadow_findings")
    outer_rows = [dict(row) for row in _list(outer) if isinstance(row, dict)]
    nested_rows = [dict(row) for row in _list(nested) if isinstance(row, dict)]
    if outer is not None and not isinstance(outer, list):
        raise MainlineContractError("evaluator_shadow_findings_not_list")
    if nested is not None and not isinstance(nested, list):
        raise MainlineContractError("evaluator_v12_shadow_findings_not_list")
    if outer_rows and nested_rows:
        outer_ids = [_finding_id(row) for row in outer_rows]
        nested_ids = [_finding_id(row) for row in nested_rows]
        if outer_ids != nested_ids:
            raise MainlineContractError("evaluator_shadow_projection_ambiguous")
    return nested_rows or outer_rows


def build_evaluator_only_projection(
    scan_result: dict[str, Any],
) -> dict[str, Any]:
    """Project explicit semantic shadow gates into evaluator-only scope."""

    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    contract = validate_mainline_run_contract(
        _dict(result.get("mainline_run") or v12.get("mainline_run"))
    )
    if not contract["private_evaluator_observation_allowed"]:
        raise MainlineContractError("private_evaluator_observation_not_allowed")

    fingerprint = contract["contract_fingerprint"]
    deliverable: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in _shadow_rows(result):
        finding_id = _finding_id(row)
        if not finding_id:
            raise MainlineContractError("evaluator_shadow_finding_id_missing")
        observed_fingerprint = _text(
            _dict(row.get("mainline_run")).get("contract_fingerprint")
            or row.get("mainline_contract_fingerprint")
        )
        if observed_fingerprint != fingerprint:
            raise MainlineContractError(
                f"evaluator_shadow_authority_fingerprint_mismatch:{finding_id}"
            )
        semantic_status = _text(
            row.get("semantic_delivery_gate_status")
            or row.get("delivery_gate_status")
        ).upper()
        if semantic_status not in {"DELIVERABLE", "REJECTED"}:
            raise MainlineContractError(
                f"evaluator_shadow_semantic_gate_missing:{finding_id}"
            )
        projected = {
            **row,
            "finding_id": finding_id,
            "id": finding_id,
            "finding_class": "evaluator_shadow",
            "evaluator_scope": "private_evaluator",
        }
        if semantic_status == "DELIVERABLE":
            deliverable.append(projected)
        else:
            candidates.append(projected)

    return {
        "schema_version": EVALUATOR_PROJECTION_SCHEMA,
        "authority_scope": "private_evaluator",
        "run_id": contract["run_id"],
        "campaign_id": contract["campaign_id"],
        "target_id": contract["target_id"],
        "evaluation_mode": contract["evaluation_mode"],
        "mainline_contract_fingerprint": fingerprint,
        "source_shadow_count": len(deliverable) + len(candidates),
        "findings": deliverable,
        "candidates": candidates,
    }
