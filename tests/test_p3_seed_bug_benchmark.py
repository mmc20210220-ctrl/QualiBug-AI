from __future__ import annotations


def _scan_result_with_seeded_observations() -> dict:
    return {
        "auto_har": {
            "status": "captured",
            "entries": [
                {
                    "request": {"method": "POST", "url": "http://local/api/refunds"},
                    "response": {"status": 201, "content": {"text": '{"refund_id":"r_1","order_id":"ord_unpaid","status":"created"}'}},
                },
                {
                    "request": {"method": "GET", "url": "http://local/api/tenants/tenant_b/orders/ord_1"},
                    "response": {"status": 200, "content": {"text": '{"id":"ord_1","tenant_id":"tenant_a","amount_cents":1299}'}},
                },
                {
                    "request": {"method": "POST", "url": "http://local/api/payments"},
                    "response": {"status": 201, "content": {"text": '{"order_amount_cents":1299,"payment_amount_cents":999,"status":"paid"}'}},
                },
                {
                    "request": {"method": "POST", "url": "http://local/api/orders/ord_1/pay"},
                    "response": {"status": 500, "content": {"text": '{"error":"duplicate_payment_state_crash"}'}},
                },
                {
                    "request": {"method": "GET", "url": "http://local/api/orders/deleted_order"},
                    "response": {"status": 200, "content": {"text": '{"id":"deleted_order","deleted":true}'}},
                },
            ],
        }
    }


def _seed_defects() -> list[dict]:
    return [
        {
            "id": "BUG_REFUND_UNPAID_ORDER",
            "title": "Unpaid order can be refunded",
            "kind": "should_reject_but_succeeded",
            "method": "POST",
            "path": "/api/refunds",
            "severity": "P0",
        },
        {
            "id": "BUG_CROSS_TENANT_ORDER_READ",
            "title": "Cross-tenant order is readable",
            "kind": "field_equals_forbidden_value",
            "method": "GET",
            "path": "/api/tenants/{tenantId}/orders/{orderId}",
            "field": "tenant_id",
            "forbidden_value": "tenant_a",
            "severity": "P0",
        },
        {
            "id": "BUG_PAYMENT_AMOUNT_MISMATCH",
            "title": "Payment amount differs from order amount",
            "kind": "field_mismatch",
            "method": "POST",
            "path": "/api/payments",
            "left": "order_amount_cents",
            "right": "payment_amount_cents",
            "severity": "P1",
        },
        {
            "id": "BUG_DUPLICATE_PAYMENT_500",
            "title": "Duplicate payment causes server error",
            "kind": "unexpected_server_error",
            "method": "POST",
            "path": "/api/orders/{orderId}/pay",
            "severity": "P0",
        },
        {
            "id": "BUG_DELETED_ORDER_STILL_READABLE",
            "title": "Deleted order remains readable",
            "kind": "should_reject_but_succeeded",
            "method": "GET",
            "path": "/api/orders/{orderId}",
            "severity": "P1",
        },
    ]


def test_p3_seed_bug_benchmark_detects_seeded_business_defects() -> None:
    from ai_test_asset_center.p3_seed_bug_benchmark import evaluate_seed_bug_benchmark

    report = evaluate_seed_bug_benchmark(_scan_result_with_seeded_observations(), _seed_defects())
    found_ids = {item["seed_id"] for item in report["findings"]}

    assert report["schema_version"] == "p3-seed-bug-benchmark-v1"
    assert report["total_seed_defects"] == 5
    assert report["found_count"] == 5
    assert report["missed_count"] == 0
    assert report["detection_rate"] == 1.0
    assert report["grade"] == "passed"
    assert "BUG_REFUND_UNPAID_ORDER" in found_ids
    assert "BUG_CROSS_TENANT_ORDER_READ" in found_ids
    assert "BUG_PAYMENT_AMOUNT_MISMATCH" in found_ids
    assert "BUG_DUPLICATE_PAYMENT_500" in found_ids
    assert "BUG_DELETED_ORDER_STILL_READABLE" in found_ids


def test_p3_seed_bug_benchmark_reports_missed_defects() -> None:
    from ai_test_asset_center.p3_seed_bug_benchmark import evaluate_seed_bug_benchmark

    report = evaluate_seed_bug_benchmark({"auto_har": {"entries": []}}, _seed_defects())

    assert report["found_count"] == 0
    assert report["missed_count"] == 5
    assert report["detection_rate"] == 0.0
    assert report["grade"] == "failed"
    assert {item["status"] for item in report["missed"]} == {"missed"}


def test_p3_seed_bug_benchmark_supports_raw_http_observations() -> None:
    from ai_test_asset_center.p3_seed_bug_benchmark import evaluate_seed_bug_benchmark

    report = evaluate_seed_bug_benchmark(
        [
            {
                "method": "POST",
                "path": "/api/refunds",
                "status": 201,
                "body": {"refund_id": "r_1", "order_id": "ord_unpaid"},
            }
        ],
        [_seed_defects()[0]],
    )

    assert report["found_count"] == 1
    assert report["findings"][0]["seed_id"] == "BUG_REFUND_UNPAID_ORDER"
