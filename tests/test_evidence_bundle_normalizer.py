from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.evidence_bundle_normalizer import normalize_evidence_bundle


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")


def test_evidence_bundle_normalizer_enriches_from_issue_and_execution(tmp_path: Path) -> None:
    project = "demo"
    workspace_dir = tmp_path / "platform_workspace" / project / "real_project"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    _write_json(output_dir / "discovered_issues.json", {"items": [{"issue_id": "I1", "expected": "expected value", "actual": "actual value"}]})
    _write_json(workspace_dir / "probe_execution_result.json", {"items": [{
        "probe_id": "P1",
        "execution_id": "EXEC-1",
        "timestamp": "2026-07-06T12:00:00Z",
        "duration_seconds": 0.2,
        "response_status": 200,
        "request": {"method": "GET", "url": "/sample"},
        "response": {"status_code": 200},
    }]})
    _write_json(output_dir / "evidence_bundle.json", {"items": [{"issue_id": "I1", "probe_id": "P1"}]})

    report = normalize_evidence_bundle(project, tmp_path, persist=True)
    bundle = json.loads((output_dir / "evidence_bundle.json").read_text(encoding="utf-8"))
    item = bundle["items"][0]

    assert report["fully_normalized_count"] == 1
    assert item["request"]["method"] == "GET"
    assert item["response"]["status_code"] == 200
    assert item["expected"] == "expected value"
    assert item["actual"] == "actual value"
    assert item["execution_id"] == "EXEC-1"
    assert item["reproduction"]["is_synthetic"] is False
    assert item["is_synthetic"] is False
    assert (output_dir / "evidence_bundle_normalization_report.json").exists()
    assert (workspace_dir / "evidence_bundle_normalization_report.json").exists()


def test_evidence_bundle_normalizer_reports_missing_fields(tmp_path: Path) -> None:
    project = "demo"
    output_dir = tmp_path / "platform_outputs" / project / "real_project"
    _write_json(output_dir / "evidence_bundle.json", {"items": [{"issue_id": "I1", "request": {"method": "GET"}}]})

    report = normalize_evidence_bundle(project, tmp_path, persist=True)
    item_report = report["items"][0]

    assert report["fully_normalized_count"] == 0
    assert item_report["normalized"] is False
    assert "response" in item_report["missing_fields"]
    assert "expected" in item_report["missing_fields"]
    assert "actual" in item_report["missing_fields"]
    assert "reproduction_or_replay" in item_report["missing_fields"]
    assert "execution_receipt" in item_report["missing_fields"]
