from __future__ import annotations

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.observer_contracts_base import (
    _business_outcome_from_body,
    _observe_http_response,
)
from ai_test_asset_center.runtime_binding_graph import declared_effect_observers


def test_business_outcome_detects_soft_reject_envelope() -> None:
    outcome = _business_outcome_from_body({
        "success": False,
        "error": {"code": "COUPON_DENIED", "message": "rejected"},
    })
    assert outcome["business_rejected"] is True
    assert outcome["success_flag"] is False


def test_http_response_observer_surfaces_soft_reject() -> None:
    receipt = _observe_http_response(
        {},
        {"status_code": 200, "body": {"success": False, "code": "DENIED"}},
    )
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["business_rejected"] is True


def test_http_status_class_fails_on_soft_business_reject() -> None:
    result = evaluate_assertion(
        {
            "assertion_id": "a1",
            "kind": "http_status_class",
            "expected_class": 2,
        },
        observations={
            "status_code": 200,
            "business_rejected": True,
        },
    )
    assert result["status"] == "VIOLATION"
    assert result["reason_code"] == "HTTP_SOFT_BUSINESS_REJECTED"


def test_declared_effect_observers_follow_observes_relation() -> None:
    behavior_ir = {
        "operations": [
            {
                "id": "op-use-coupon",
                "method": "POST",
                "path": "/api/coupons/use",
                "read_write": "write",
                "request_example": {"code": "SAVE10"},
            },
            {
                "id": "op-read-coupon",
                "method": "GET",
                "path": "/api/coupons/{code}",
                "read_write": "read",
            },
        ],
        "relations": [{
            "id": "rel-observes",
            "relation_type": "observes",
            "from_ref": "op-use-coupon",
            "to_ref": "op-read-coupon",
            "operation_ref": "op-use-coupon",
        }],
    }
    resolvers = declared_effect_observers(
        behavior_ir["operations"][0],
        behavior_ir=behavior_ir,
    )
    assert any(row.get("operation_ref") == "op-read-coupon" for row in resolvers)
