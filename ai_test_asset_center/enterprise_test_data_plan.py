"""Source-neutral enterprise test-data contract for Campaign execution."""
from __future__ import annotations

import hashlib
import json
from typing import Any


ALLOWED_STRATEGIES = (
    "reuse_verified_existing",
    "create_disposable",
    "approved_fixture_setup",
    "blocked_with_testability_gap",
)


def _text(value: Any, limit: int = 240) -> str:
    return str(value or "").strip()[:limit]


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")).hexdigest()[:20]


def validate_test_data_contract(contract: dict[str, Any] | None, *, environment_ref: str, scope_id: str) -> dict[str, Any]:
    """Validate data eligibility without generating or mutating any data."""
    contract = _dict(contract)
    strategy = _text(contract.get("strategy")) or "blocked_with_testability_gap"
    if strategy not in ALLOWED_STRATEGIES:
        strategy = "blocked_with_testability_gap"
    environment = _text(contract.get("environment_ref")) or _text(environment_ref)
    scope = _text(contract.get("scope_id")) or _text(scope_id)
    missing: list[str] = []
    if not environment:
        missing.append("ENVIRONMENT_REFERENCE_MISSING")
    if not scope:
        missing.append("CAMPAIGN_SCOPE_MISSING")
    provenance = _text(contract.get("provenance_ref"))
    creation = _text(contract.get("creation_receipt_ref"))
    cleanup = _text(contract.get("cleanup_receipt_ref"))
    disposable_scope = _text(contract.get("disposable_scope_ref"))
    fixture = _text(contract.get("approved_fixture_ref"))
    write_approved = contract.get("write_approved") is True
    if strategy == "reuse_verified_existing":
        if not provenance:
            missing.append("DATA_PROVENANCE_MISSING")
    elif strategy == "create_disposable":
        if not write_approved:
            missing.append("WRITE_APPROVAL_MISSING")
        if not disposable_scope:
            missing.append("DISPOSABLE_SCOPE_MISSING")
        if not creation:
            missing.append("DATA_CREATION_RECEIPT_MISSING")
        if not cleanup:
            missing.append("DATA_CLEANUP_RECEIPT_MISSING")
    elif strategy == "approved_fixture_setup":
        if not write_approved:
            missing.append("WRITE_APPROVAL_MISSING")
        if not fixture:
            missing.append("APPROVED_FIXTURE_REFERENCE_MISSING")
        if not creation:
            missing.append("DATA_CREATION_RECEIPT_MISSING")
        if not cleanup:
            missing.append("DATA_CLEANUP_RECEIPT_MISSING")
    else:
        missing.append("TEST_DATA_STRATEGY_NOT_APPROVED")
    status = "ready" if not missing else "blocked_with_testability_gap"
    return {
        "status": status,
        "strategy": strategy,
        "environment_ref": environment,
        "scope_id": scope,
        "provenance_ref": provenance,
        "creation_receipt_ref": creation,
        "cleanup_receipt_ref": cleanup,
        "disposable_scope_ref": disposable_scope,
        "approved_fixture_ref": fixture,
        "write_approved": write_approved,
        "missing_requirements": missing,
    }


def build_campaign_test_data_plan(campaign: dict[str, Any], selected_slices: list[dict[str, Any]], contract: dict[str, Any] | None) -> dict[str, Any]:
    """Return auditable plan rows; never infer entities, records or mutations."""
    campaign = _dict(campaign)
    validation = validate_test_data_contract(
        contract,
        environment_ref=_text(campaign.get("environment_ref")),
        scope_id=_text(campaign.get("scope_id")),
    )
    campaign_id = _text(campaign.get("campaign_id"))
    plans: list[dict[str, Any]] = []
    for slice_item in selected_slices if isinstance(selected_slices, list) else []:
        slice_item = _dict(slice_item)
        slice_id = _text(slice_item.get("slice_id"))
        if not slice_id:
            continue
        plans.append({
            "plan_id": "TDP_" + _hash({"campaign_id": campaign_id, "slice_id": slice_id}),
            "campaign_id": campaign_id,
            "behavior_slice_id": slice_id,
            "entity": _text(slice_item.get("entity")),
            "status": validation["status"],
            "strategy": validation["strategy"],
            "environment_ref": validation["environment_ref"],
            "scope_id": validation["scope_id"],
            "required_receipts": _required_receipts(validation["strategy"]),
            "missing_requirements": list(validation["missing_requirements"]),
        })
    return {
        "schema_version": "enterprise-test-data-plan-v1",
        "campaign_id": campaign_id,
        "status": validation["status"],
        "strategy": validation["strategy"],
        "environment_ref": validation["environment_ref"],
        "scope_id": validation["scope_id"],
        "write_approved": validation["write_approved"],
        "missing_requirements": validation["missing_requirements"],
        "plans": plans,
        "coverage_gaps": [
            {"kind": "TEST_DATA_GAP", "code": code, "campaign_id": campaign_id}
            for code in validation["missing_requirements"]
        ],
    }


def _required_receipts(strategy: str) -> list[str]:
    if strategy == "reuse_verified_existing":
        return ["data_provenance_ref"]
    if strategy == "create_disposable":
        return ["write_approval", "disposable_scope_ref", "creation_receipt_ref", "cleanup_receipt_ref"]
    if strategy == "approved_fixture_setup":
        return ["write_approval", "approved_fixture_ref", "creation_receipt_ref", "cleanup_receipt_ref"]
    return ["approved_test_data_strategy"]
