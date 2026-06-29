from __future__ import annotations

from ai_test_asset_center.performance_monitor import PerformanceMetrics
from ai_test_asset_center.performance_oracles import evaluate_performance_oracles
from ai_test_asset_center.stability_oracles import evaluate_stability_oracles


def test_runtime_oracles_detect_performance_and_stability_patterns() -> None:
    executions = [
        {"probe_id": "p1", "probe": {"method": "GET", "path": "/api/orders"}, "response_status": 200, "error": None, "duration_seconds": 0.2},
        {"probe_id": "p2", "probe": {"method": "GET", "path": "/api/orders"}, "response_status": 503, "error": "timeout waiting upstream", "duration_seconds": 2.8},
        {"probe_id": "p3", "probe": {"method": "GET", "path": "/api/orders"}, "response_status": 500, "error": "timeout waiting upstream", "duration_seconds": 3.1},
        {"probe_id": "p4", "probe": {"method": "GET", "path": "/api/health"}, "response_status": 200, "error": None, "duration_seconds": 0.15},
    ]
    PerformanceMetrics.reset()
    PerformanceMetrics.record("probe.execute", 0.2)
    PerformanceMetrics.record("probe.execute", 2.9)
    PerformanceMetrics.record("probe.execute", 3.2)

    perf_issues = evaluate_performance_oracles(executions, PerformanceMetrics.get_summary(), request_timeout_seconds=5)
    stability_issues = evaluate_stability_oracles(executions, request_timeout_seconds=5)

    assert any(issue["defect_family"] == "performance" for issue in perf_issues)
    assert any(issue["defect_family"] == "stability" for issue in stability_issues)
    assert any("flaky" in issue["title"] or "重试风暴" in issue["title"] for issue in stability_issues)

