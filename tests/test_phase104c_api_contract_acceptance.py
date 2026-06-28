from __future__ import annotations

import json

from ai_test_asset_center.phase104_api_contract_acceptance import (
    run_api_contract_acceptance,
    validate_contract_artifacts,
)
from ai_test_asset_center.phase104_api_contract_exporter import export_api_contract


def test_phase104c_accepts_exported_contract_and_runtime_smoke(tmp_path) -> None:
    contract_dir = tmp_path / "contract"
    export_api_contract(contract_dir)

    report = validate_contract_artifacts(contract_dir, scenario="manufacturing", live_smoke=True)

    assert report.passed is True
    assert report.score == 100
    keys = {check.key for check in report.checks}
    assert "required_files" in keys
    assert "openapi_routes" in keys
    assert "frontend_client_methods" in keys
    assert "runtime_mutation_flow" in keys
    assert "runtime_redaction" in keys
    assert report.artifacts["redaction_status"] == "safe"


def test_phase104c_writes_json_and_markdown_reports(tmp_path) -> None:
    data = run_api_contract_acceptance(
        contract_dir=tmp_path / "contract",
        output_dir=tmp_path / "acceptance",
        build_first=True,
        scenario="ecommerce",
    )

    json_path = tmp_path / "acceptance" / "api_contract_acceptance_report.json"
    md_path = tmp_path / "acceptance" / "api_contract_acceptance_report.md"

    assert data["passed"] is True
    assert json_path.exists()
    assert md_path.exists()
    assert "Phase104C API 合同验收报告" in md_path.read_text(encoding="utf-8")

    saved = json.loads(json_path.read_text(encoding="utf-8"))
    assert saved["scenario"] == "ecommerce"
    assert saved["score"] == 100
    combined = json.dumps(saved, ensure_ascii=False) + md_path.read_text(encoding="utf-8")
    assert "DemoPasswordShouldBeRedacted" not in combined
    assert "SESSION=raw" not in combined
    assert "Bearer raw" not in combined


def test_phase104c_detects_missing_contract_artifacts(tmp_path) -> None:
    contract_dir = tmp_path / "broken_contract"
    contract_dir.mkdir()
    (contract_dir / "openapi.json").write_text("{}", encoding="utf-8")

    report = validate_contract_artifacts(contract_dir, live_smoke=False)

    assert report.passed is False
    first = report.checks[0]
    assert first.key == "required_files"
    assert first.passed is False
    assert "API_CONTRACT.md" in first.detail
