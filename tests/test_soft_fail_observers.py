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
    # When a body carries a decision flag (success/ok/valid/…), the top-level
    # ``code`` key is the entity's own code (e.g. a coupon code), not a
    # rejection status; the decision flag is the authoritative soft-reject
    # signal. The status token still surfaces through the ``status`` key.
    receipt = _observe_http_response(
        {},
        {"status_code": 200, "body": {"success": False, "status": "DENIED"}},
    )
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["business_rejected"] is True
    assert receipt["evidence"]["outcome_status"] == "DENIED"


def test_observer_receipt_survives_artifact_redaction() -> None:
    """outcome_status must not match sensitive *_token keys or redaction breaks fingerprints."""
    from ai_test_asset_center.artifact_redactor import redact_artifact
    from ai_test_asset_center.observer_contracts_base import (
        bind_observer_receipt_lineage,
        validate_observer_receipt,
    )

    receipt = bind_observer_receipt_lineage(
        _observe_http_response(
            {"status_code": 200, "body": {"status": "ACTIVE"}},
            {"status_code": 200, "body": {"status": "DISABLED"}},
        ),
        campaign_id="CMP_test",
        execution_id="exec_test",
    )
    validate_observer_receipt(receipt)
    redacted, _ = redact_artifact({"observer_receipts": [receipt]})
    validate_observer_receipt(redacted["observer_receipts"][0])
    assert redacted["observer_receipts"][0]["evidence"]["outcome_status"] == "DISABLED"


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


def test_declared_effect_observers_join_via_shared_entity() -> None:
    """Write produces entity E; read observes E — join without inventing paths."""

    behavior_ir = {
        "entities": [{
            "id": "ent-payment",
            "name": "payment",
            "kind": "business_object",
        }],
        "operations": [
            {
                "id": "op-manual-success",
                "method": "POST",
                "path": "/api/payments/admin/manual-success",
                "read_write": "write",
            },
            {
                "id": "op-read-payment",
                "method": "GET",
                "path": "/api/payments/order/{orderId}",
                "read_write": "read",
            },
        ],
        "relations": [
            {
                "id": "rel-produces",
                "relation_type": "produces",
                "from_ref": "op-manual-success",
                "to_ref": "ent-payment",
                "operation_ref": "op-manual-success",
                "source_refs": [{"source_id": "api-doc"}],
                "status": "accepted",
            },
            {
                "id": "rel-observes",
                "relation_type": "observes",
                "from_ref": "op-read-payment",
                "to_ref": "ent-payment",
                "operation_ref": "op-read-payment",
                "source_refs": [{"source_id": "api-doc"}],
                "status": "accepted",
            },
        ],
    }
    resolvers = declared_effect_observers(
        behavior_ir["operations"][0],
        behavior_ir=behavior_ir,
    )
    assert any(row.get("operation_ref") == "op-read-payment" for row in resolvers)
