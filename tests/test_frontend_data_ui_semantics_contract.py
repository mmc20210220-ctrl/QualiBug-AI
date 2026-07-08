from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND_DATA_TS = ROOT / "frontend" / "src" / "api" / "data.ts"


def _frontend_data_source() -> str:
    return FRONTEND_DATA_TS.read_text(encoding="utf-8")


def test_frontend_use_findings_data_consumes_command_center_defects_and_scan_meta_directly() -> None:
    source = _frontend_data_source()

    assert "function getReportFindings(raw: unknown): Finding[]" in source
    assert "field(raw, 'defects')" in source
    assert "setFindings(getReportFindings(raw))" in source
    assert "setClues(getReportClues(raw))" in source
    assert "setScanMeta(asRecord(record.scan_meta))" in source


def test_frontend_project_summary_counts_p0_from_defects_payload() -> None:
    source = _frontend_data_source()

    assert "function buildProjectSummary(raw: unknown, project: string): ProjectSummary" in source
    assert "const findings = getReportFindings(raw);" in source
    assert "findingsCount: findings.length" in source
    assert "clueCount: getReportClues(raw).length" in source
    assert "p0Count: findings.filter((finding) => finding.severity === 'P0').length" in source


def test_frontend_materialized_status_detection_still_uses_defects_payload() -> None:
    source = _frontend_data_source()

    assert "function hasMaterializedFindingData(raw: unknown): boolean" in source
    assert "return getReportFindings(raw).length > 0" in source
