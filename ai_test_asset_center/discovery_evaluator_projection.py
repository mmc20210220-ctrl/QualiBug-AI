"""Authority-scoped projection of shadow findings for the private evaluator.

Product scope remains empty during replay/shadow runs.  This boundary is the
only place that may reinterpret an explicitly recorded semantic delivery-gate
result as evaluator input.
"""
from __future__ import annotations

from typing import Any

from .canonical_defect_registry import (
    CanonicalDefectRegistryError,
    canonical_representative_findings,
    validate_canonical_defect_registry,
    validate_defect_identity_consistency,
)
from .discovery_mainline_contract import (
    MainlineContractError,
    validate_mainline_run_contract,
)
from .discovery_quality_projection import (
    build_formal_count_projection,
)
from .formal_delivery_scope import formal_customer_deliverable_findings
from .formal_delivery_authority import (
    build_formal_delivery_authority_receipt,
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
    """Project only immutable Gate-v2 + attempt-ledger authority."""

    result = _dict(scan_result)
    v12 = _dict(result.get("v12"))
    contract = validate_mainline_run_contract(
        _dict(result.get("mainline_run") or v12.get("mainline_run"))
    )
    if not contract["private_evaluator_observation_allowed"]:
        raise MainlineContractError("private_evaluator_observation_not_allowed")

    fingerprint = contract["contract_fingerprint"]
    shadow_rows = _shadow_rows(result)
    shadow_ids = [_finding_id(row) for row in shadow_rows]
    if not all(shadow_ids):
        raise MainlineContractError("evaluator_shadow_finding_id_missing")
    if len(shadow_ids) != len(set(shadow_ids)):
        raise MainlineContractError("evaluator_shadow_finding_id_duplicate")

    ledger = _dict(
        result.get("obligation_attempt_ledger")
        or v12.get("obligation_attempt_ledger")
    )
    if not ledger:
        raise MainlineContractError("evaluator_shadow_attempt_ledger_missing")
    delivery_occurrences = (
        result.get("delivery_occurrences")
        if "delivery_occurrences" in result
        else v12.get("delivery_occurrences")
    )
    if not isinstance(delivery_occurrences, list):
        raise MainlineContractError(
            "evaluator_delivery_occurrences_missing"
        )
    verified_deliverable = formal_customer_deliverable_findings(
        delivery_occurrences,
        obligation_attempt_ledger=ledger or None,
    )
    deliverable_ids = sorted(_finding_id(row) for row in verified_deliverable)
    try:
        canonical_registry = validate_canonical_defect_registry(
            _dict(
                result.get("canonical_defect_registry")
                or v12.get("canonical_defect_registry")
            ),
            mainline_run=contract,
            deliverable_occurrences=verified_deliverable,
            obligation_attempt_ledger=ledger,
        )
        canonical_findings = canonical_representative_findings(
            canonical_registry,
            deliverable_occurrences=verified_deliverable,
        )
        submitted_canonical = (
            result.get("evaluator_canonical_findings")
            if "evaluator_canonical_findings" in result
            else v12.get("evaluator_canonical_findings")
        )
        if not isinstance(submitted_canonical, list):
            raise CanonicalDefectRegistryError(
                "EVALUATOR_CANONICAL_FINDINGS_MISSING"
            )
        if submitted_canonical != canonical_findings:
            raise CanonicalDefectRegistryError(
                "EVALUATOR_CANONICAL_FINDINGS_MISMATCH"
            )
        defect_identity_consistency = validate_defect_identity_consistency(
            _dict(
                result.get("defect_identity_consistency")
                or v12.get("defect_identity_consistency")
            ),
            required_occurrence_scopes={
                "delivery_gate_ids",
                "registry_occurrence_ids",
                "formal_projection_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
            },
            allowed_occurrence_scopes={
                "delivery_gate_ids",
                "registry_occurrence_ids",
                "formal_projection_occurrence_ids",
                "product_projection_occurrence_ids",
                "formal_authority_occurrence_ids",
                "evaluator_submission_occurrence_ids",
                "trace_ledger_occurrence_ids",
            },
            allowed_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "product_projection_ids",
                "evaluator_projection_ids",
                "evaluator_submission_ids",
            },
        )
    except CanonicalDefectRegistryError as exc:
        raise MainlineContractError(
            f"evaluator_canonical_authority_invalid:{exc}"
        ) from exc
    formal_count_projection = build_formal_count_projection(
        findings=verified_deliverable,
        candidate_findings=[],
        obligation_attempt_ledger=ledger or None,
        mainline_run=contract,
        canonical_defect_registry=canonical_registry,
    )
    submitted_formal = _dict(
        result.get("formal_count_projection")
        or v12.get("formal_count_projection")
    )
    if submitted_formal != formal_count_projection:
        raise MainlineContractError(
            "evaluator_formal_count_projection_mismatch"
        )
    formal_delivery_authority = build_formal_delivery_authority_receipt(
        mainline_run=contract,
        findings=verified_deliverable,
        obligation_attempt_ledger=ledger,
    )
    deliverable_id_set = set(deliverable_ids)

    candidates: list[dict[str, Any]] = []
    for row in shadow_rows:
        finding_id = _finding_id(row)
        observed_fingerprint = _text(
            _dict(row.get("mainline_run")).get("contract_fingerprint")
            or row.get("mainline_contract_fingerprint")
        )
        if observed_fingerprint != fingerprint:
            raise MainlineContractError(
                f"evaluator_shadow_authority_fingerprint_mismatch:{finding_id}"
            )
        projected = {
            **row,
            "finding_id": finding_id,
            "id": finding_id,
            "finding_class": "evaluator_shadow",
            "evaluator_scope": "private_evaluator",
        }
        if finding_id not in deliverable_id_set:
            candidates.append(projected)

    return {
        "schema_version": EVALUATOR_PROJECTION_SCHEMA,
        "authority_scope": "private_evaluator",
        "run_id": contract["run_id"],
        "campaign_id": contract["campaign_id"],
        "target_id": contract["target_id"],
        "evaluation_mode": contract["evaluation_mode"],
        "mainline_contract_fingerprint": fingerprint,
        "source_shadow_count": len(shadow_rows),
        "findings": canonical_findings,
        "delivery_occurrences": verified_deliverable,
        "candidates": candidates,
        "obligation_attempt_ledger": ledger,
        "canonical_defect_registry": canonical_registry,
        "formal_count_projection": formal_count_projection,
        "defect_identity_consistency": defect_identity_consistency,
        "formal_delivery_authority": formal_delivery_authority,
    }
