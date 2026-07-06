from __future__ import annotations

from pathlib import Path


def test_frontend_knowledge_preview_uses_source_id_query_param() -> None:
    client = Path("frontend/src/api/client.ts").read_text(encoding="utf-8")

    assert "knowledge/preview?source_id=" in client
    assert "knowledge/preview?sourceId=" not in client


def test_customer_safe_report_has_no_mojibake(tmp_path: Path) -> None:
    from ai_test_asset_center.customer_safe_report import contains_mojibake, render_customer_safe_report_html

    report_dir = tmp_path / "platform_outputs" / "demo" / "pipeline_reports"
    report_dir.mkdir(parents=True)
    (report_dir / "latest_pipeline_report.json").write_text(
        """
        {
          "stage1_industry": {"object_count": 3},
          "stage3_impact_analysis": {"llm_powered": 2},
          "stage2_discovery": {
            "findings": [
              {"severity": "P1", "title": "订单金额跨视图不一致", "category": "财务一致性", "confidence_score": 0.91, "evidence": "订单详情与汇总页金额不同"}
            ]
          }
        }
        """.strip(),
        encoding="utf-8",
    )
    (report_dir / "scan_history.json").write_text(
        "[{" + '"timestamp_utc":"2026-07-06T10:00:00Z","status":"completed","total_findings":1,"p0p1_count":1' + "}]",
        encoding="utf-8",
    )

    html = render_customer_safe_report_html("demo", tmp_path)

    assert "QualiBug AI 缺陷扫描报告" in html
    assert "订单金额跨视图不一致" in html
    assert "客户可交付缺陷" in html
    assert not contains_mojibake(html)


def test_customer_report_renderer_and_patch_are_extracted_from_entrypoint() -> None:
    entrypoint = Path("ai_test_asset_center/private_pilot_entrypoint.py").read_text(encoding="utf-8")
    patch_module = Path("ai_test_asset_center/private_pilot_customer_report_patch.py").read_text(encoding="utf-8")

    assert "from ai_test_asset_center.private_pilot_customer_report_patch import" in entrypoint
    assert "def render_customer_safe_report_html" not in entrypoint
    assert "MOJIBAKE_MARKERS" not in entrypoint
    assert "def _render_report_html_clean" not in entrypoint
    assert "def _render_report_html_clean" in patch_module


def test_customer_report_patch_replaces_legacy_renderer() -> None:
    from ai_test_asset_center import private_pilot_service as service
    from ai_test_asset_center.private_pilot_entrypoint import install_customer_report_patch, restore_customer_report_patch

    restore_customer_report_patch()
    original = service.PrivatePilotHandler._render_report_html

    install_customer_report_patch()
    patched = service.PrivatePilotHandler._render_report_html

    assert patched is not original
    assert getattr(service, "_CUSTOMER_REPORT_PATCHED", False) is True

    restore_customer_report_patch()
    assert service.PrivatePilotHandler._render_report_html is original
