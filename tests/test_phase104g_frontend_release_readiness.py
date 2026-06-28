from __future__ import annotations

import json
import zipfile

from ai_test_asset_center.phase104_frontend_release_readiness import (
    PHASE104G_VERSION,
    build_frontend_release_readiness,
    main,
    validate_frontend_release_readiness,
    verify_release_checksums,
)


def test_phase104g_builds_frontend_release_readiness_bundle(tmp_path):
    output_dir = tmp_path / "frontend_release"

    bundle = build_frontend_release_readiness(output_dir=output_dir, scenario="manufacturing")

    assert bundle.version == PHASE104G_VERSION
    assert bundle.passed is True
    assert bundle.score == 100
    assert bundle.handoff_passed is True
    assert bundle.handoff_checksum_ok is True
    assert bundle.redaction_status == "safe"
    assert bundle.release_gate_count >= 8
    assert bundle.file_count >= 30
    assert bundle.checksum_count >= 25
    assert (output_dir / "handoff_bundle" / "phase104_frontend_handoff_manifest.json").exists()
    assert (output_dir / "release" / "FRONTEND_CUTOVER_PLAN.md").exists()
    assert (output_dir / "release" / "FRONTEND_ROLLBACK_PLAN.md").exists()
    assert (output_dir / "release" / "FRONTEND_SIGNOFF_LEDGER.md").exists()
    assert (output_dir / "phase104_frontend_release_readiness_bundle.zip").exists()

    manifest = json.loads((output_dir / "phase104_frontend_release_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE104G_VERSION
    assert manifest["passed"] is True
    assert manifest["handoff_passed"] is True
    assert manifest["handoff_checksum_ok"] is True

    with zipfile.ZipFile(output_dir / "phase104_frontend_release_readiness_bundle.zip") as archive:
        names = set(archive.namelist())
    assert "handoff_bundle/workspace/contract/openapi.json" in names
    assert "release/FRONTEND_SIGNOFF_LEDGER.md" in names
    assert "release_readiness_report.md" in names

    combined = "\n".join(path.read_text(encoding="utf-8") for path in output_dir.rglob("*.md"))
    assert "raw-token" not in combined
    assert "Traceback" not in combined


def test_phase104g_validate_and_checksum_detect_tamper(tmp_path):
    output_dir = tmp_path / "frontend_release"
    build_frontend_release_readiness(output_dir=output_dir)

    report = validate_frontend_release_readiness(output_dir)
    checksum_ok, findings = verify_release_checksums(output_dir)

    assert report.passed is True
    assert report.score == 100
    assert checksum_ok is True
    assert findings == []

    cutover = output_dir / "release" / "FRONTEND_CUTOVER_PLAN.md"
    cutover.write_text(cutover.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")

    checksum_ok, findings = verify_release_checksums(output_dir)
    report = validate_frontend_release_readiness(output_dir)

    assert checksum_ok is False
    assert any("checksum mismatch" in item for item in findings)
    assert report.passed is False


def test_phase104g_cli_build_and_validate_only(tmp_path, capsys):
    output_dir = tmp_path / "frontend_release"

    code = main(["--output-dir", str(output_dir), "--scenario", "manufacturing"])
    captured = capsys.readouterr()

    assert code == 0
    assert "phase104g-frontend-release-readiness-v1" in captured.out
    assert (output_dir / "release_readiness_report.json").exists()

    code = main(["--output-dir", str(output_dir), "--validate-only"])
    captured = capsys.readouterr()

    assert code == 0
    assert '"passed": true' in captured.out
