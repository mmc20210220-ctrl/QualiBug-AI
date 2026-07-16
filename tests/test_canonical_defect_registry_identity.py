from __future__ import annotations

import pytest

from ai_test_asset_center.canonical_defect_registry import (
    CanonicalDefectRegistryError,
    derive_canonical_identity_evidence,
)


def _deliverable_attempt(*, assertion_kind: str) -> dict[str, object]:
    request_semantics = "a" * 64
    request_body = "b" * 64
    return {
        "terminal_status": "DELIVERABLE",
        "delivery_evidence_bundle": {
            "reproduction_receipt": {
                "receipt_id": "repro-1",
                "source_refs": [{"kind": "api", "locator": "POST /api/items"}],
                "step_observations": [{
                    "phase": "treatment",
                    "actor_ref": "actor:treatment",
                    "adapter": "http",
                    "method": "POST",
                    "operation_ref": "op:create-item",
                    "path_template": "/api/items",
                    "request_semantics_fingerprint": request_semantics,
                    "request_body_fingerprint": request_body,
                    "mutation_class": "create",
                    "mutation_selector": "body",
                    "mutation_operator": "set",
                    "status_code": 400,
                }],
            },
            "oracle_receipt": {
                "receipt_id": "oracle-1",
                "assertions": [{
                    "receipt_id": "assertion-1",
                    "status": "VIOLATION",
                    "kind": assertion_kind,
                    "expected": {"status": "rejected"},
                    "actual": {"status": "accepted"},
                    "observer_receipt_ids": ["observer-1"],
                    "source_refs": [{"kind": "api", "locator": "POST /api/items"}],
                }],
            },
            "observer_receipts": [{
                "receipt_id": "observer-1",
                "observer_id": "typed_assertion",
            }],
            "contract_evidence_receipts": [
                {
                    "receipt_id": "actor-treatment",
                    "kind": "actor",
                    "subject_id": "actor:treatment",
                    "status": "OBSERVED",
                    "evidence": {"role": "buyer"},
                },
                {
                    "receipt_id": "treatment-contract",
                    "kind": "treatment",
                    "status": "OBSERVED",
                    "evidence": {
                        "request_semantics_fingerprint": request_semantics,
                        "path_template": "/api/items",
                        "request_body_fingerprint": request_body,
                        "mutation_class": "create",
                        "mutation_selector": "body",
                        "mutation_operator": "set",
                    },
                },
            ],
        },
    }


def test_actor_insensitive_treatment_only_identity_does_not_require_control_step() -> None:
    evidence = derive_canonical_identity_evidence(
        _deliverable_attempt(assertion_kind="input_validation")
    )

    assert evidence["actor_relation"] == {
        "control_actor_class": "not_identity_defining",
        "treatment_actor_class": "not_identity_defining",
        "relation": "actor_insensitive_property",
    }
    assert evidence["observed_outcome"]["control_observation_class"] == "not_observed"
    assert evidence["observed_outcome"]["treatment_observation_class"] == "http:4xx"


def test_state_transition_treatment_only_identity_does_not_require_control_step() -> None:
    evidence = derive_canonical_identity_evidence(
        _deliverable_attempt(assertion_kind="state_transition")
    )

    assert evidence["actor_relation"]["relation"] == "actor_insensitive_property"
    assert evidence["observed_outcome"]["control_observation_class"] == "not_observed"


def test_actor_sensitive_treatment_only_identity_still_requires_control_step() -> None:
    with pytest.raises(
        CanonicalDefectRegistryError,
        match="CANONICAL_IDENTITY_INCOMPLETE:reproduction.control_step",
    ):
        derive_canonical_identity_evidence(
            _deliverable_attempt(assertion_kind="authorization")
        )
