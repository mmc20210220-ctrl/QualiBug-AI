from __future__ import annotations

from ai_test_asset_center.regression_runner import _build_ci_feedback


def test_empty_regression_suite_requires_more_validation() -> None:
    summary = {
        "total_probe_count": 0,
        "needs_review_count": 0,
    }

    result = _build_ci_feedback(
        project="benchmark_mall_v05_p0probe",
        mode="release",
        summary=summary,
        failures=[],
    )

    assert result["gate_status"] == "manual_approval_required"
    assert result["exit_code"] == 1
    assert result["release_gate_override"] == "continue_regression"
    assert result["manual_review_required"] is True
    assert "回归套件为空" in result["ci_message"]
