import json
from pathlib import Path

from ai_test_asset_center.customer_safe_report import contains_mojibake, render_customer_safe_report_html


def _write_report(root: Path, project: str, payload: dict) -> None:
    report_dir = root / "platform_outputs" / project / "pipeline_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "latest_pipeline_report.json").write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


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

    assert "发布门禁与修复后回归" in html
    assert "当前发布结论：阻塞" in html
    assert "修复后回归 Gate" in html
    assert "P0 缺陷阻塞" in html
    assert "Release gate reports existing release checks" in html
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
    assert "修复后回归 Gate" in html
    assert "发布门禁</span><strong>待处理" in html
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
    assert "修复后回归 Gate" in html
    assert "最近一次回归失败" in html
    assert "P0 缺陷阻塞" in html
    assert "report_gate+regression_run_result" in html
    assert "Existing report release gate rule" in html
    assert not contains_mojibake(html)
