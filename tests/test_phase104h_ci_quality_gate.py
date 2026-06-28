from __future__ import annotations

import json
import zipfile

from ai_test_asset_center.phase104_ci_quality_gate import (
    PHASE104H_VERSION,
    build_ci_quality_gate,
    main,
    validate_ci_quality_gate,
    verify_ci_checksums,
)


def test_phase104h_builds_ci_quality_gate_bundle(tmp_path):
    output_dir = tmp_path / "ci_gate"

    bundle = build_ci_quality_gate(output_dir=output_dir, scenario="manufacturing")

    assert bundle.version == PHASE104H_VERSION
    assert bundle.passed is True
    assert bundle.score == 100
    assert bundle.release_readiness_passed is True
    assert bundle.release_checksum_ok is True
    assert bundle.redaction_status == "safe"
    assert bundle.gate_count >= 9
    assert bundle.file_count >= 40
    assert bundle.checksum_count >= 35
    assert (output_dir / ".github" / "workflows" / "qualibug_phase104_quality_gate.yml").exists()
    assert (output_dir / "scripts" / "Run-Phase104QualityGate.ps1").exists()
    assert (output_dir / "docs" / "CI_QUALITY_GATE_RUNBOOK.md").exists()
    assert (output_dir / "frontend_release_readiness" / "release_readiness_report.json").exists()
    assert (output_dir / "phase104_ci_quality_gate_bundle.zip").exists()

    manifest = json.loads((output_dir / "phase104_ci_quality_gate_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE104H_VERSION
    assert manifest["passed"] is True
    assert manifest["release_readiness_passed"] is True
    assert manifest["release_checksum_ok"] is True

    workflow = (output_dir / ".github" / "workflows" / "qualibug_phase104_quality_gate.yml").read_text(encoding="utf-8")
    assert "python -m pytest -q" in workflow
    assert "phase104_frontend_release_readiness" in workflow
    assert "actions/upload-artifact@v4" in workflow

    with zipfile.ZipFile(output_dir / "phase104_ci_quality_gate_bundle.zip") as archive:
        names = set(archive.namelist())
    assert ".github/workflows/qualibug_phase104_quality_gate.yml" in names
    assert "docs/CI_QUALITY_GATE_RUNBOOK.md" in names
    assert "frontend_release_readiness/release_readiness_report.md" in names

    combined = "\n".join(path.read_text(encoding="utf-8") for path in output_dir.rglob("*.md"))
    assert "raw-token" not in combined
    assert "Traceback" not in combined


def test_phase104h_validate_and_checksum_detect_tamper(tmp_path):
    output_dir = tmp_path / "ci_gate"
    build_ci_quality_gate(output_dir=output_dir)

    report = validate_ci_quality_gate(output_dir)
    checksum_ok, findings = verify_ci_checksums(output_dir)

    assert report.passed is True
    assert report.score == 100
    assert checksum_ok is True
    assert findings == []

    runbook = output_dir / "docs" / "CI_QUALITY_GATE_RUNBOOK.md"
    runbook.write_text(runbook.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")

    checksum_ok, findings = verify_ci_checksums(output_dir)
    report = validate_ci_quality_gate(output_dir)

    assert checksum_ok is False
    assert any("checksum mismatch" in item for item in findings)
    assert report.passed is False


def test_phase104h_cli_build_and_validate_only(tmp_path, capsys):
    output_dir = tmp_path / "ci_gate"

    code = main(["--output-dir", str(output_dir), "--scenario", "manufacturing"])
    captured = capsys.readouterr()

    assert code == 0
    assert "phase104h-ci-quality-gate-v1" in captured.out
    assert (output_dir / "ci_quality_gate_report.json").exists()

    code = main(["--output-dir", str(output_dir), "--validate-only"])
    captured = capsys.readouterr()

    assert code == 0
    assert '"passed": true' in captured.out
