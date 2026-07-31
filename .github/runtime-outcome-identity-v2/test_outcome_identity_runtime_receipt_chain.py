from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center import assertion_dsl
from ai_test_asset_center import canonical_defect_registry
from ai_test_asset_center import contract_oracles
from ai_test_asset_center import customer_delivery_gate_v2
from ai_test_asset_center import experiment_outcome_finalizer
from ai_test_asset_center import observer_contracts


def _observer(outcome_ref: str, observer_id: str) -> dict:
    return observer_contracts.build_observer_receipt(
        observer_id=observer_id,
        status="OBSERVED",
        evidence={"status_code": 500},
        campaign_id="campaign:1",
        execution_id="execution:1",
        semantic_role="MANDATORY_OUTCOME",
        outcome_ref=outcome_ref,
        oracle_template_ref=f"oracle:{outcome_ref}",
        assertion_requirement_ref=f"requirement:{outcome_ref}",
    )


def _assertion(outcome_ref: str, assertion_id: str, expected: int = 200) -> dict:
    return {
        "assertion_id": assertion_id,
        "kind": "http_status",
        "expected": expected,
        "mandatory": True,
        "semantic_role": "MANDATORY_OUTCOME",
        "outcome_ref": outcome_ref,
        "oracle_template_ref": f"oracle:{outcome_ref}",
        "assertion_requirement_ref": f"requirement:{outcome_ref}",
        "canonical_outcome_identity_required": True,
    }


def test_observer_receipt_content_addresses_outcome_identity() -> None:
    receipt = _observer("outcome:state", "entity_state")
    validated = observer_contracts.validate_observer_receipt(receipt)

    assert validated["outcome_ref"] == "outcome:state"
    assert validated["semantic_role"] == "MANDATORY_OUTCOME"
    tampered = deepcopy(validated)
    tampered["outcome_ref"] = "outcome:permission"
    with pytest.raises(ValueError, match="outcome_identity|fingerprint"):
        observer_contracts.validate_observer_receipt(tampered)


def test_assertion_uses_matching_outcome_receipt_only() -> None:
    state = _observer("outcome:state", "entity_state")
    permission = _observer("outcome:permission", "http_response")

    receipt = assertion_dsl.evaluate_assertion(
        _assertion("outcome:state", "assertion:state"),
        observations={
            "status_code": 500,
            "observer_receipts": [state, permission],
        },
        campaign_id="campaign:1",
        execution_id="execution:1",
    )
    validated = assertion_dsl.validate_assertion_receipt(receipt)

    assert validated["status"] == "VIOLATION"
    assert validated["outcome_ref"] == "outcome:state"
    assert validated["observer_receipt_ids"] == [state["receipt_id"]]

    missing = assertion_dsl.evaluate_assertion(
        _assertion("outcome:state", "assertion:state"),
        observations={
            "status_code": 500,
            "observer_receipts": [permission],
        },
        campaign_id="campaign:1",
        execution_id="execution:1",
    )
    assert missing["status"] == "INDETERMINATE"
    assert missing["reason_code"] == "ASSERTION_OUTCOME_OBSERVER_RECEIPT_MISSING"


def test_multiple_violated_outcomes_fail_closed_in_oracle_projection() -> None:
    assertions = [
        {
            "status": "VIOLATION",
            "outcome_ref": "outcome:permission",
        },
        {
            "status": "VIOLATION",
            "outcome_ref": "outcome:state",
        },
    ]
    projection = contract_oracles._canonical_projection(
        {
            "canonical_outcome_identity_required": True,
            "mandatory_outcome_refs": [
                "outcome:permission",
                "outcome:state",
            ],
        },
        assertions,
    )
    reasons = contract_oracles._identity_reason_codes(projection)

    assert projection["canonical_outcome_identity_complete"] is False
    assert projection["primary_violation_outcome_ref"] == ""
    assert "MULTIPLE_VIOLATED_OUTCOMES_REQUIRE_SEPARATE_FINDINGS" in reasons


def test_finalizer_stamps_one_verified_outcome_into_finding() -> None:
    assertion = {
        "status": "VIOLATION",
        "receipt_id": "assert:state",
        "outcome_ref": "outcome:state",
        "oracle_template_ref": "oracle:state",
        "assertion_requirement_ref": "requirement:state",
    }
    result = experiment_outcome_finalizer._stamp_finding_outcome_identity(
        {
            "status": "EXECUTED",
            "oracle_verdict": {
                "status": "VIOLATION",
                "canonical_outcome_identity_required": True,
                "primary_violation_outcome_ref": "outcome:state",
                "assertions": [assertion],
            },
            "finding": {
                "finding_id": "finding:1",
                "oracle": {},
                "evidence": {},
                "raw_evidence": {},
            },
        }
    )

    assert result["finding"]["outcome_ref"] == "outcome:state"
    assert result["finding"]["assertion_receipt_id"] == "assert:state"
    assert result["finding"]["evidence"]["outcome_ref"] == "outcome:state"


def test_delivery_active_chain_rejects_foreign_outcome_receipt(monkeypatch) -> None:
    monkeypatch.setattr(
        customer_delivery_gate_v2,
        "_original_validate_active_chain",
        lambda **_kwargs: ("DELIVERABLE", []),
    )
    state = _observer("outcome:state", "entity_state")
    permission = _observer("outcome:permission", "http_response")
    assertion = assertion_dsl.evaluate_assertion(
        _assertion("outcome:state", "assertion:state"),
        observations={
            "status_code": 500,
            "observer_receipts": [state],
        },
        campaign_id="campaign:1",
        execution_id="execution:1",
    )
    assertion = dict(assertion)
    assertion["observer_receipt_ids"] = [permission["receipt_id"]]
    # Re-seal the deliberately foreign reference so the Gate tests semantics,
    # not a malformed assertion fingerprint.
    base = assertion_dsl._original_assertion_receipt(
        assertion_id=assertion["assertion_id"],
        kind=assertion["kind"],
        status=assertion["status"],
        reason_code=assertion["reason_code"],
        expected=assertion["expected"],
        actual=assertion["actual"],
        error=assertion["error"],
        observer_receipt_ids=assertion["observer_receipt_ids"],
        source_refs=assertion["source_refs"],
        harness_error=assertion["harness_error"],
        campaign_id=assertion["campaign_id"],
        execution_id=assertion["execution_id"],
    )
    assertion = assertion_dsl._seal_assertion_receipt(
        base,
        {
            "semantic_role": "MANDATORY_OUTCOME",
            "outcome_ref": "outcome:state",
            "oracle_template_ref": "oracle:outcome:state",
            "assertion_requirement_ref": "requirement:outcome:state",
            "canonical_outcome_identity_bound": True,
        },
    )

    status, reasons = customer_delivery_gate_v2._validate_active_chain(
        execution={},
        contracts=[],
        observers=[state, permission],
        oracle={
            "canonical_outcome_identity_required": True,
            "primary_violation_outcome_ref": "outcome:state",
            "assertions": [assertion],
        },
        reproduction={},
    )
    assert status == "BLOCKED"
    assert reasons == ["OUTCOME_OBSERVER_RECEIPT_MISSING"]


def test_canonical_registry_requires_one_violated_outcome() -> None:
    assertion = {
        "status": "VIOLATION",
        "outcome_ref": "outcome:state",
    }
    selected = canonical_defect_registry._one_violation(
        {
            "canonical_outcome_identity_required": True,
            "primary_violation_outcome_ref": "outcome:state",
            "assertions": [assertion],
        }
    )
    assert selected is assertion

    with pytest.raises(Exception, match="one_violated_outcome"):
        canonical_defect_registry._one_violation(
            {
                "canonical_outcome_identity_required": True,
                "primary_violation_outcome_ref": "outcome:state",
                "assertions": [
                    assertion,
                    {
                        "status": "VIOLATION",
                        "outcome_ref": "outcome:permission",
                    },
                ],
            }
        )


def test_finalizer_activates_canonical_mode_from_explicit_assertion_refs() -> None:
    normalized = experiment_outcome_finalizer._normalize_experiment_outcome_identity(
        {
            "assertions": [
                {
                    "assertion_id": "assertion:state",
                    "outcome_ref": "outcome:state",
                    "observer_id": "entity_state",
                }
            ],
            "observers": [{"observer_id": "entity_state"}],
        }
    )

    assert normalized["canonical_outcome_identity_required"] is True
    assert normalized["mandatory_outcome_refs"] == ["outcome:state"]
    assert normalized["assertions"][0]["canonical_outcome_identity_required"] is True
    assert normalized["observers"][0]["outcome_ref"] == "outcome:state"
