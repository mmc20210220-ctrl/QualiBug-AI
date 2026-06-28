from __future__ import annotations

import json

from ai_test_asset_center.phase105_frontend_experience_hub_v2 import build_frontend_experience_hub_v2
from ai_test_asset_center.phase105_frontend_interaction_acceptance import (
    FRONTEND_INTERACTION_ACCEPTANCE_JSON,
    PHASE105K_VERSION,
    run_frontend_interaction_acceptance,
    scan_frontend_interaction_for_secret_leaks,
    validate_frontend_interaction_acceptance,
)


def test_phase105k_build_first_validates_frontend_interaction_gate(tmp_path) -> None:
    hub_dir = tmp_path / "hub"
    output_dir = tmp_path / "acceptance"

    result = run_frontend_interaction_acceptance(
        hub_dir=hub_dir,
        output_dir=output_dir,
        build_first=True,
        scenario="manufacturing",
        api_base_url="http://127.0.0.1:8790",
    )

    assert result["acceptance"]["version"] == PHASE105K_VERSION
    assert result["acceptance"]["passed"] is True
    assert result["acceptance"]["score"] == 100
    assert (output_dir / FRONTEND_INTERACTION_ACCEPTANCE_JSON).exists()
    assert "Phase105K 前端显示层交互验收报告" in (output_dir / "frontend_interaction_acceptance_report.md").read_text(encoding="utf-8")
    assert (hub_dir / "pages" / "test_execution" / "test_execution.html").exists()

    payload = json.loads((output_dir / FRONTEND_INTERACTION_ACCEPTANCE_JSON).read_text(encoding="utf-8"))
    assert payload["passed"] is True
    assert any(check["key"] == "navigation_closure" for check in payload["checks"])


def test_phase105k_detects_broken_navigation_and_secret_leak(tmp_path) -> None:
    build_frontend_experience_hub_v2(tmp_path, create_zip=False)
    index_path = tmp_path / "index.html"
    index_text = index_path.read_text(encoding="utf-8")
    index_path.write_text(index_text.replace("pages/test_execution/test_execution.html", "missing/test_execution.html"), encoding="utf-8")
    (tmp_path / "assets" / "qualibug_frontend_hub_v2.js").write_text("const bad = 'client_secret=raw';", encoding="utf-8")

    report = validate_frontend_interaction_acceptance(tmp_path)
    assert report.passed is False
    details = "\n".join(check.detail for check in report.checks)
    assert "test_execution" in details or "AI 测试" in details
    assert "client_secret=" in details
    assert scan_frontend_interaction_for_secret_leaks(tmp_path)


def test_phase105k_validate_existing_hub_to_separate_output(tmp_path) -> None:
    hub_dir = tmp_path / "hub"
    output_dir = tmp_path / "report"
    build_frontend_experience_hub_v2(hub_dir, scenario="saas")

    result = run_frontend_interaction_acceptance(hub_dir=hub_dir, output_dir=output_dir, build_first=False)

    assert result["acceptance"]["passed"] is True
    assert result["manifest"]["redaction_status"] == "safe"
    assert (output_dir / "frontend_interaction_acceptance_manifest.json").exists()
    assert not (hub_dir / "frontend_interaction_acceptance_report.json").exists()
