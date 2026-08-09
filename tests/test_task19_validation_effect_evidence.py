"""Task 19: VALIDATION_EFFECT_AMBIGUOUS root-cause regression tests.

run9 measured 56 VALIDATION_EFFECT_AMBIGUOUS tokens (28 obligations): the
validation treatment arm executed and returned 2xx, but the effect was judged
zero because the effect window compared only the before/after READBACK pair
and dropped the write RESPONSE itself. A 2xx write whose response body
materializes a NEW entity identity (absent from the observed pre-state and
from the request) is the target's own acceptance statement — the malformed
input was accepted and an effect occurred (validation miss => violation).
Fail-closed cases (null/echo-only response bodies) must stay AMBIGUOUS.
"""
from __future__ import annotations

from ai_test_asset_center.observer_contracts_base import (
    _effect_window,
    _observe_business_effect,
)
from ai_test_asset_center.assertion_dsl_validation_base import evaluate_assertion


def _step(
    *,
    phase: str = "treatment",
    write_status: int = 201,
    write_body: dict | None,
    before_body: dict | list,
    after_body: dict | list,
    request_body: dict | None = None,
) -> dict:
    governance = {
        "before": {"status_code": 200, "body": before_body},
        "write": {"status_code": write_status, "body": write_body},
        "after": {"status_code": 200, "body": after_body},
    }
    if request_body is not None:
        governance["materialized_request_body"] = request_body
    return {
        "phase": phase,
        "method": "POST",
        "path": "/api/collection",
        "status_code": write_status,
        "body": write_body,
        "governance_receipt": governance,
    }


def _assertion() -> dict:
    return {
        "kind": "validation_rejection",
        "expected_class": 4,
        "expected_effect_count": 0,
        "assertion_id": "assert_validation_effect",
        "business_effect_requirement": "NOT_APPLICABLE",
    }


def _verdict(steps: list[dict]) -> dict:
    receipt = _observe_business_effect(steps)
    evidence = receipt["evidence"]
    observations = {
        "status_code": steps[-1]["status_code"],
        "treatment_effect_count": (
            evidence.get("treatment_effect_count") or evidence.get("effect_count")
        ),
        "zero_effect_on_accepted_write": evidence.get(
            "zero_effect_on_accepted_write"
        ),
        "business_rejected": evidence.get("business_rejected"),
        "business_effect_observer_receipt": receipt,
    }
    return evaluate_assertion(_assertion(), observations=observations)


_CREATED = {
    "id": "5b37bc1f-074a-8a80-808b-000000000001",
    "sku": "SKU-NEG-001",
    "title": "负价商品",
    "category": "数码",
    "price": "-50.00",
    "status": "DRAFT",
}
_REQUEST = {"sku": "SKU-NEG-001", "title": "负价商品", "price": -50.00}


def test_write_response_materialization_decides_ambiguous_to_violation() -> None:
    """run9 shape: treatment 2xx created the entity (new id in the write
    response), the readback surface shows no change — the effect must now be
    judged present (violation), not ambiguous."""
    before = [{"sku": "OLD-1", "price": "10.00", "status": "ON_SALE"}]
    steps = [_step(write_body=_CREATED, before_body=before, after_body=before,
                   request_body=_REQUEST)]
    window, reason = _effect_window(steps)
    assert reason == ""
    assert window["effect_count"] == 1
    assert window["effect_basis"] == "write_response_new_identity"
    result = _verdict(steps)
    assert result["status"] == "VIOLATION"
    assert result["reason_code"] == "VALIDATION_REJECTION_NOT_ENFORCED"


def test_null_write_response_stays_ambiguous_fail_closed() -> None:
    """2xx with a null/empty response body and an unchanged readback carries
    no materialization evidence — must stay VALIDATION_EFFECT_AMBIGUOUS."""
    before = [{"sku": "OLD-1", "price": "10.00", "status": "ON_SALE"}]
    steps = [_step(write_status=200, write_body=None, before_body=before,
                   after_body=before)]
    result = _verdict(steps)
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_EFFECT_AMBIGUOUS"


def test_response_echoing_request_identity_stays_ambiguous() -> None:
    """A response that only echoes the request's own identity is not
    materialization evidence (the identity existed in the request) — fail
    closed."""
    before = [{"sku": "OLD-1", "price": "10.00", "status": "ON_SALE"}]
    request_body = {"id": "client-supplied-id", "status": "ok"}
    steps = [_step(write_status=200, write_body={"id": "client-supplied-id"},
                   before_body=before, after_body=before,
                   request_body=request_body)]
    result = _verdict(steps)
    assert result["status"] == "INDETERMINATE"
    assert result["reason_code"] == "VALIDATION_EFFECT_AMBIGUOUS"


def test_business_rejection_priority_preserved() -> None:
    """2xx + explicit business rejection in the response stays PASS even when
    the response also carries a new identity."""
    before = [{"sku": "OLD-1", "price": "10.00", "status": "ON_SALE"}]
    rejected = {**_CREATED, "success": False,
                "message": "rejected: price must be positive"}
    steps = [_step(write_body=rejected, before_body=before, after_body=before,
                   request_body=_REQUEST)]
    result = _verdict(steps)
    assert result["status"] == "PASS"
    assert result["reason_code"] == "VALIDATION_BUSINESS_REJECTED"


def test_no_fabrication_when_pre_state_unobserved() -> None:
    """P0-1 truthfulness: a 2xx write response alone (observation GETs failed)
    must NOT fabricate effect evidence."""
    step = {
        "phase": "control",
        "method": "POST",
        "path": "/api/collection",
        "status_code": 200,
        "body": {"id": 99},
        "governance_receipt": {
            "before": {"status_code": 500, "body": None},
            "write": {"status_code": 200, "body": {"id": 99}},
            "after": {"status_code": 500, "body": None},
        },
    }
    evidence, reason = _effect_window([step])
    assert evidence == {}
    assert reason == "BUSINESS_EFFECT_OBSERVATION_FAILED"


def test_unchanged_readback_without_write_evidence_stays_zero() -> None:
    """Same entity before/after with a plain response body: honest zero."""
    step = _step(
        write_body={"id": "OLD-1", "name": "same"},
        before_body=[{"id": "OLD-1", "name": "same"}],
        after_body=[{"id": "OLD-1", "name": "same"}],
        request_body={"id": "OLD-1", "name": "same"},
    )
    evidence, reason = _effect_window([step])
    assert reason == ""
    assert evidence["effect_count"] == 0
    assert "effect_basis" not in evidence
