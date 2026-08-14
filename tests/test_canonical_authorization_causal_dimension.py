from __future__ import annotations

import hashlib
import json

import pytest

from ai_test_asset_center import canonical_defect_registry as registry
from ai_test_asset_center.authorization_oracle_causality import SCHEMA_VERSION


def _receipt(dimension: str) -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "PASSED",
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "campaign_id": "campaign-1",
        "execution_id": "exec-1",
        "reason_codes": [],
        "comparison_dimension": dimension,
        "comparison_contract_fingerprint": "a" * 64,
        "compile_binding_graph_fingerprint": "b" * 64,
        "runtime_resource_identity_fingerprint": "c" * 64,
        "control_target_reached": True,
        "treatment_target_reached": True,
        "single_identity_dimension_proven": True,
        "same_resource_proven": True,
        "verified_receipt_ids": ["receipt-1"],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        **payload,
        "receipt_id": "auth_causality_"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
    }


def _attempt(dimension: str) -> dict:
    receipt = _receipt(dimension)
    return {
        "delivery_evidence_bundle": {
            "finding": {
                "authorization_causality_receipt": receipt,
                "oracle": {
                    "authorization_causality_proven": True,
                    "authorization_causality_receipt_id": receipt["receipt_id"],
                },
            }
        }
    }


def _base_evidence() -> dict:
    return {
        "actor_relation": {
            "control_actor_class": "resource_owner",
            "treatment_actor_class": "any_actor",
            "relation": "control_to_treatment",
        },
        "proof": {
            "evidence_actor_classes": ["buyer", "auditor"],
        },
    }


def _not_applicable_receipt() -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "NOT_APPLICABLE",
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "campaign_id": "campaign-1",
        "execution_id": "exec-1",
        "reason_codes": [],
        "comparison_contract_fingerprint": "",
        "runtime_resource_identity_fingerprint": "",
        "verified_receipt_ids": [],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        **payload,
        "receipt_id": "auth_causality_"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
    }


def _indeterminate_receipt() -> dict:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "INDETERMINATE",
        "experiment_id": "exp-1",
        "obligation_id": "obl-1",
        "campaign_id": "campaign-1",
        "execution_id": "exec-1",
        "reason_codes": ["AUTHORIZATION_CAUSAL_OBSERVER_INDETERMINATE"],
        "comparison_dimension": "ROLE_PERMISSION",
        "comparison_contract_fingerprint": "a" * 64,
        "compile_binding_graph_fingerprint": "b" * 64,
        "runtime_resource_identity_fingerprint": "c" * 64,
        "control_target_reached": True,
        "treatment_target_reached": True,
        "single_identity_dimension_proven": False,
        "same_resource_proven": False,
        "verified_receipt_ids": [],
    }
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return {
        **payload,
        "receipt_id": "auth_causality_"
        + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:24],
    }


def test_receipted_comparison_dimension_enters_stable_identity() -> None:
    role = registry._with_authorization_causal_dimension(
        _base_evidence(),
        attempt=_attempt("ROLE_PERMISSION"),
    )
    tenant = registry._with_authorization_causal_dimension(
        _base_evidence(),
        attempt=_attempt("TENANT_SCOPE"),
    )

    assert role["actor_relation"]["comparison_dimension"] == "ROLE_PERMISSION"
    assert tenant["actor_relation"]["comparison_dimension"] == "TENANT_SCOPE"
    assert role["actor_relation"] != tenant["actor_relation"]

    # Concrete role breadth remains proof/evidence and never becomes the causal
    # identity dimension.
    assert role["proof"]["evidence_actor_classes"] == ["buyer", "auditor"]
    assert role["proof"]["authorization_comparison_dimension"] == "ROLE_PERMISSION"


def test_authorization_claim_without_sealed_receipt_fails_closed() -> None:
    attempt = {
        "delivery_evidence_bundle": {
            "finding": {
                "oracle": {
                    "authorization_causality_proven": True,
                    "authorization_causality_receipt_id": "missing-receipt",
                }
            }
        }
    }
    with pytest.raises(
        registry.CanonicalDefectRegistryError,
        match="authorization.causality_receipt",
    ):
        registry._with_authorization_causal_dimension(
            _base_evidence(),
            attempt=attempt,
        )


def test_non_authorization_identity_stays_unchanged() -> None:
    evidence = _base_evidence()
    assert registry._with_authorization_causal_dimension(
        evidence,
        attempt={"delivery_evidence_bundle": {"finding": {}}},
    ) == evidence


def test_not_applicable_causality_returns_unchanged_identity() -> None:
    # No comparison contract → NOT_APPLICABLE receipt → no causal dimension.
    attempt = {
        "delivery_evidence_bundle": {
            "finding": {
                "authorization_causality_receipt": _not_applicable_receipt(),
                "oracle": {},
            }
        }
    }
    evidence = _base_evidence()
    result = registry._with_authorization_causal_dimension(
        evidence,
        attempt=attempt,
    )
    assert result == evidence
    assert "comparison_dimension" not in result.get("actor_relation", {})


def test_not_applicable_causality_with_claimed_proof_fails_closed() -> None:
    # NOT_APPLICABLE receipt but the finding simultaneously claims causal proof
    # is self-contradictory → still fail closed.
    receipt = _not_applicable_receipt()
    attempt = {
        "delivery_evidence_bundle": {
            "finding": {
                "authorization_causality_receipt": receipt,
                "oracle": {
                    "authorization_causality_proven": True,
                    "authorization_causality_receipt_id": receipt["receipt_id"],
                },
            }
        }
    }
    with pytest.raises(
        registry.CanonicalDefectRegistryError,
        match="authorization.causality_reference",
    ):
        registry._with_authorization_causal_dimension(
            _base_evidence(),
            attempt=attempt,
        )


def test_indeterminate_causality_still_fails_closed() -> None:
    # INDETERMINATE denotes genuinely incomplete causal proof; keep failing.
    attempt = {
        "delivery_evidence_bundle": {
            "finding": {
                "authorization_causality_receipt": _indeterminate_receipt(),
                "oracle": {},
            }
        }
    }
    with pytest.raises(
        registry.CanonicalDefectRegistryError,
        match="authorization.causality_status",
    ):
        registry._with_authorization_causal_dimension(
            _base_evidence(),
            attempt=attempt,
        )
