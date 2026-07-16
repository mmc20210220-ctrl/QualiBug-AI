from __future__ import annotations

from benchmark_evaluator.miss_diagnosis import (
    _collect_executed_paths,
    _collect_execution_blobs,
)


def test_miss_diagnosis_collects_v12_experiment_execution_paths() -> None:
    scan_result = {
        "v12": {
            "experiment_execution": {
                "results": [{
                    "steps": [
                        {
                            "phase": "control",
                            "method": "POST",
                            "path": "/api/orders",
                            "status_code": 201,
                            "body": {"id": "order-1", "status": "CREATED"},
                            "governance_receipt": {
                                "before": {
                                    "url": "http://127.0.0.1:51646/api/orders",
                                    "status": 200,
                                    "body": [],
                                },
                                "write": {
                                    "url": "http://127.0.0.1:51646/api/orders",
                                    "status": 201,
                                    "body": {"id": "order-1"},
                                },
                                "after": {
                                    "url": "http://127.0.0.1:51646/api/orders/order-1",
                                    "status": 200,
                                    "body": {"id": "order-1"},
                                },
                            },
                        },
                        {
                            "phase": "blocked",
                            "method": "GET",
                            "path": "/api/payments/order/{orderId}",
                            "status_code": 0,
                        },
                        {
                            "phase": "control_response_bound_effect_observation",
                            "method": "GET",
                            "path": "/api/orders/order-1",
                            "status_code": 200,
                            "body": {"id": "order-1"},
                        },
                    ],
                }],
            },
        },
    }

    assert _collect_executed_paths(scan_result) == {
        "/api/orders",
        "/api/orders/order-1",
    }


def test_miss_diagnosis_collects_v12_execution_blobs() -> None:
    scan_result = {
        "v12": {
            "experiment_execution": {
                "results": [{
                    "steps": [{
                        "phase": "treatment",
                        "method": "POST",
                        "path": "/api/coupons/validate",
                        "status_code": 200,
                        "body": {"couponCode": "NEW100", "discount": 100},
                    }],
                }],
            },
        },
    }

    joined = "\n".join(_collect_execution_blobs(scan_result))

    assert "/api/coupons/validate" in joined
    assert "couponcode" in joined.lower()
