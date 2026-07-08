from pathlib import Path

from ai_test_asset_center.benchmark_compute import compute_benchmark


def test_compute_benchmark_returns_invariant_coverage_without_ground_truth(tmp_path: Path) -> None:
    metrics = compute_benchmark(
        "demo_project",
        [
            {
                "title": "普通用户可越权退款他人订单",
                "risk_type": "authorization_access_control",
                "confirmation_status": "confirmed",
                "expected": "普通用户不能退款他人订单",
                "actual": "接口返回成功",
                "_api_method": "POST",
                "_api_path": "/orders/123/refund",
                "raw_evidence": {
                    "request_raw": {"method": "POST", "path": "/orders/123/refund"},
                    "response_raw": {"status_code": 200},
                },
            }
        ],
        candidates=[
            {
                "title": "并发重复退款可能导致金额不一致",
                "category": "concurrency",
                "_api_method": "POST",
                "_api_path": "/orders/123/refund",
            }
        ],
        root=tmp_path,
    )

    assert metrics["benchmark_active"] is False
    assert metrics["ground_truth_available"] is False
    assert "recall" not in metrics
    matrix = metrics["coverage_matrix"]
    assert matrix["schema_version"] == "risk_invariant_coverage_v1"
    assert matrix["ontology_family_count"] >= 16
    assert matrix["covered_family_count"] >= 2
    family_rows = {row["family"]: row for row in matrix["families"]}
    assert family_rows["authorization_access_control"]["coverage_status"] == "confirmed_with_evidence"
    assert family_rows["concurrency_race_condition"]["coverage_status"] == "candidate_only"
