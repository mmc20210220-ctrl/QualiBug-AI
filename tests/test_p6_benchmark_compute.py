"""P6 contract test: benchmark metrics computation against seeded ground truth.

Verifies that:
- ``compute_benchmark`` returns empty when no ground truth exists
- With ground truth, computes recall/precision/FPR/FNR correctly
- ``_method_path_key`` normalizes path params for matching
- ``persist_benchmark_result`` writes and the command center can read it back
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ai_test_asset_center.benchmark_compute import (
    _method_path_key,
    compute_benchmark,
    persist_benchmark_result,
)


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def test_method_path_key_normalizes_path_params() -> None:
    assert _method_path_key({"method": "GET", "path": "/api/orders/123"}) == ("GET", "/api/orders/{id}")
    assert _method_path_key({"method": "POST", "_api_path": "/api/tenants/{tenantId}/orders/{orderId}"}) == ("POST", "/api/tenants/{id}/orders/{id}")
    assert _method_path_key({"method": "", "_api_method": "DELETE", "path": "/api/items/42"}) == ("DELETE", "/api/items/{id}")


def test_compute_benchmark_returns_empty_when_no_ground_truth(tmp_path: Path) -> None:
    result = compute_benchmark("test", [], root=tmp_path)
    assert result == {}, f"Expected empty dict when no ground truth, got: {result}"


def test_compute_benchmark_with_ground_truth(tmp_path: Path) -> None:
    # Write ground truth inside the test temp dir
    gt_path = tmp_path / "ground_truth" / "bugs.json"
    gt_bugs = [
        {"bug_id": "BUG_001", "title": "SQL Injection in login", "type": "security", "severity": "P0", "trigger": "/api/login", "method": "POST"},
        {"bug_id": "BUG_002", "title": "Missing auth on orders", "type": "auth", "severity": "P1", "trigger": "/api/orders", "method": "GET"},
        {"bug_id": "BUG_003", "title": "Double refund allowed", "type": "idempotency", "severity": "P0", "trigger": "/api/refunds", "method": "POST"},
    ]
    _write_json(gt_path, {"bugs": gt_bugs})

    # Scan findings — found BUG_001 and BUG_003, missed BUG_002
    findings = [
        {"title": "SQL Injection", "method": "POST", "path": "/api/login", "severity": "P0", "confirmation_status": "confirmed",
         "raw_evidence": {"request_raw": {"method": "POST"}, "response_raw": {"status_code": 200}}, "expected": "no SQL error", "actual": "SQL error",
         "reproduction": {"is_synthetic": False}, "gate_passed": True},
        {"title": "Double Refund", "method": "POST", "path": "/api/refunds", "severity": "P0", "confirmation_status": "confirmed",
         "raw_evidence": {"request_raw": {"method": "POST"}, "response_raw": {"status_code": 201}}, "expected": "409 conflict", "actual": "201 created",
         "reproduction": {"is_synthetic": False}, "gate_passed": True},
    ]

    result = compute_benchmark("test", findings, root=tmp_path, ground_truth_path=str(gt_path))

    assert result.get("benchmark_active") is True
    assert result.get("ground_truth_bug_count") == 3
    assert result.get("true_positives") == 2
    assert result.get("false_negatives") == 1
    assert result.get("recall") == round(2 / 3, 4)
    assert result.get("precision") == 1.0  # both findings matched
    assert result.get("false_positive_rate") == 0.0
    assert result.get("high_value_recall") == round(2 / 3, 4)  # BUG_001(P0)+BUG_003(P0) found, BUG_002(P1) missed among 3 high-value bugs
    # Evidence completeness: both have request + response + assertion
    assert result.get("evidence_completeness_rate") == 1.0
    # Reproduction success: both gate_passed and not synthetic
    assert result.get("reproduction_success_rate") == 1.0
    # Missed bug
    assert "BUG_002" in result.get("missed_bug_ids", [])
    # Bug type breakdown
    assert result.get("bug_type_breakdown", {}).get("security", {}).get("detected") == 1


def test_persist_and_read_back(tmp_path: Path) -> None:
    metrics = {
        "benchmark_active": True,
        "recall": 0.8,
        "precision": 0.9,
        "f1_score": 0.85,
    }
    path = persist_benchmark_result("test_project", metrics, root=tmp_path)
    assert path.exists()
    read_back = json.loads(path.read_text(encoding="utf-8"))
    assert read_back.get("recall") == 0.8
    assert read_back.get("precision") == 0.9


def test_compute_benchmark_no_false_fabrication(tmp_path: Path) -> None:
    """Without ground truth, must return empty — never fabricate numbers."""
    # Use a nested subdirectory so root.parent doesn't accidentally hit
    # the benchmark_mall written by a prior test in the same session.
    isolated = tmp_path / "nested" / "project_dir"
    isolated.mkdir(parents=True)
    result = compute_benchmark("empty", [], root=isolated)
    assert result == {}
    # Even with findings but no ground truth
    result = compute_benchmark("empty", [{"title": "fake"}], root=isolated)
    assert result == {}
