import json
from pathlib import Path

from ai_test_asset_center.customer_safe_report import contains_mojibake, render_customer_safe_report_html


def _write_report(root: Path, project: str, payload: dict) -> None:
    report_dir = root / "platform_outputs" / project / "pipeline_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "latest_pipeline_report.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_scan_result(root: Path, project: str, payload: dict) -> None:
    out_dir = root / "platform_outputs" / project
    out_dir.mkdir(parents=True)
    (out_dir / "scan_result.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _assert_no_fix_advice_boundary(html: str) -> None:
    assert "修复建议" not in html
    assert "修复方案" not in html
    assert "修复代码" not in html
    assert "根因承诺" in html
    assert "QualiBug-AI 只提供缺陷事实" in html


def test_customer_report_renders_backend_release_gate(tmp_path: Path) -> None:
    project = "demo_project"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "fail",
            "checks": [
                {"name": "修复后回归 Gate", "status": "fail", "detail": "最近一次回归失败：1 个探针失败。", "source": "regression_run"},
                {"name": "P0 缺陷阻塞", "status": "fail", "detail": "存在 P0 缺陷", "source": "scan_result"},
            ],
            "honesty_rule": "Release gate reports existing release checks plus persisted regression state.",
        },
        "real_findings": [{"severity": "P1", "title": "越权访问", "defect_family": "tenant_isolation", "confidence_score": 95, "evidence": {"summary": "HTTP 200"}}],
    })

    html = render_customer_safe_report_html(project, tmp_path)

    assert "发布门禁与客户处理后回归" in html
    assert "当前发布结论：阻塞" in html
    assert "客户处理后回归 Gate" in html
    assert "P0 缺陷阻塞" in html
    assert "Release gate reports existing release checks" in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)


def test_customer_report_falls_back_to_regression_run_result(tmp_path: Path) -> None:
    project = "run_project"
    _write_report(tmp_path, project, {"real_findings": []})
    run_dir = tmp_path / "platform_outputs" / project / "regression_run"
    run_dir.mkdir(parents=True)
    (run_dir / "regression_run_result.json").write_text(json.dumps({
        "summary": {"passed_count": 2, "failed_count": 0, "needs_review_count": 1},
        "ci_feedback": {"gate_status": "manual_approval_required", "ci_message": "存在需复核探针"},
    }, ensure_ascii=False), encoding="utf-8")

    html = render_customer_safe_report_html(project, tmp_path)

    assert "当前发布结论：待处理" in html
    assert "最近一次回归仍需人工复核" in html
    assert "客户处理后回归 Gate" in html
    assert "发布门禁</span><strong>待处理" in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)


def test_customer_report_merges_existing_gate_with_latest_regression_run(tmp_path: Path) -> None:
    project = "merge_project"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "pass",
            "checks": [
                {"name": "P0 缺陷阻塞", "status": "pass", "detail": "无 P0 缺陷", "source": "report_gate"},
            ],
            "honesty_rule": "Existing report release gate rule.",
            "source": "report_gate",
        },
    })
    run_dir = tmp_path / "platform_outputs" / project / "regression_run"
    run_dir.mkdir(parents=True)
    (run_dir / "regression_run_result.json").write_text(json.dumps({
        "summary": {"passed_count": 1, "failed_count": 1, "needs_review_count": 0},
        "ci_feedback": {"gate_status": "failed", "ci_message": "存在回归失败"},
    }, ensure_ascii=False), encoding="utf-8")

    html = render_customer_safe_report_html(project, tmp_path)

    assert "当前发布结论：阻塞" in html
    assert "客户处理后回归 Gate" in html
    assert "最近一次回归失败" in html
    assert "客户内部处理或复核后，必须再次执行回归验证" in html
    assert "P0 缺陷阻塞" in html
    assert "report_gate+regression_run_result" in html
    assert "Existing report release gate rule" in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)


def test_latest_regression_run_overrides_stale_report_regression_gate(tmp_path: Path) -> None:
    project = "stale_project"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "pass",
            "checks": [
                {"name": "修复后回归 Gate", "status": "pass", "detail": "旧报告记录：回归通过。", "source": "report_gate"},
                {"name": "P0 缺陷阻塞", "status": "pass", "detail": "无 P0 缺陷", "source": "report_gate"},
            ],
            "source": "report_gate",
        },
    })
    run_dir = tmp_path / "platform_outputs" / project / "regression_run"
    run_dir.mkdir(parents=True)
    (run_dir / "regression_run_result.json").write_text(json.dumps({
        "summary": {"passed_count": 3, "failed_count": 1, "needs_review_count": 0},
        "ci_feedback": {"gate_status": "failed", "ci_message": "最新回归失败"},
    }, ensure_ascii=False), encoding="utf-8")

    html = render_customer_safe_report_html(project, tmp_path)

    assert "当前发布结论：阻塞" in html
    assert "最近一次回归失败" in html
    assert "旧报告记录：回归通过" not in html
    assert "regression_run_result" in html
    assert "P0 缺陷阻塞" in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)


def test_report_does_not_equate_release_gate_pass_with_handoff_safe(tmp_path: Path) -> None:
    project = "handoff_project"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "pass",
            "checks": [
                {"name": "修复后回归 Gate", "status": "pass", "detail": "最近一次回归通过。", "source": "regression_run"},
            ],
            "source": "report_gate",
        },
        "commercial_assets": {
            "commercial_handoff": {"safe_for_customer": False, "acceptance_status": "hold_for_validation"},
            "tracker_sync": {"payload_status": "hold_for_validation"},
            "delivery_package": {"release_verdict": "pass"},
        },
    })

    html = render_customer_safe_report_html(project, tmp_path)

    assert "当前发布结论：通过" in html
    assert "交付安全</span><strong>待复核" in html
    assert "商业交付 Handoff" in html
    assert "safe_for_customer：false" in html
    assert "发布门禁通过并不等同于商业交付安全" in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)


def test_report_latest_release_gate_blocks_stale_handoff_safe(tmp_path: Path) -> None:
    project = "stale_handoff_project"
    _write_report(tmp_path, project, {
        "commercial_assets": {
            "commercial_handoff": {"safe_for_customer": True, "acceptance_status": "accepted"},
            "tracker_sync": {"payload_status": "ready", "payload_gate_status": "pass"},
            "delivery_package": {"release_verdict": "pass"},
        },
    })
    _write_scan_result(tmp_path, project, {
        "release_gate": {
            "overall_status": "fail",
            "checks": [
                {"name": "修复后回归 Gate", "status": "fail", "detail": "最新回归失败。", "source": "scan_result"},
            ],
            "source": "scan_result",
        }
    })

    html = render_customer_safe_report_html(project, tmp_path)

    assert "当前发布结论：阻塞" in html
    assert "交付安全</span><strong>被门禁阻塞" in html
    assert "safe_for_customer：false" in html
    assert "acceptance：blocked_by_release_gate" in html
    assert "最新回归失败" in html
    assert "accepted" not in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)


def test_report_string_false_handoff_is_not_safe(tmp_path: Path) -> None:
    project = "string_false_project"
    _write_report(tmp_path, project, {
        "release_gate": {
            "overall_status": "pass",
            "checks": [{"name": "修复后回归 Gate", "status": "pass", "detail": "回归通过。", "source": "regression_run"}],
        },
        "commercial_assets": {
            "commercial_handoff": {"safe_for_customer": "false", "acceptance_status": "hold_for_validation"},
            "tracker_sync": {"payload_status": "hold_for_validation"},
            "delivery_package": {"release_verdict": "pass"},
        },
    })

    html = render_customer_safe_report_html(project, tmp_path)

    assert "当前发布结论：通过" in html
    assert "交付安全</span><strong>待复核" in html
    assert "safe_for_customer：false" in html
    assert "商业交付可进入验收" not in html
    _assert_no_fix_advice_boundary(html)
    assert not contains_mojibake(html)
