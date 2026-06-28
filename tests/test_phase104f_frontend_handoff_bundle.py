from __future__ import annotations

import json
import zipfile

from ai_test_asset_center.phase104_frontend_handoff_bundle import (
    PHASE104F_VERSION,
    build_frontend_handoff_bundle,
    main,
    validate_frontend_handoff_bundle,
    verify_checksums,
)


def test_phase104f_builds_frontend_handoff_bundle(tmp_path):
    output_dir = tmp_path / "frontend_handoff"

    bundle = build_frontend_handoff_bundle(output_dir=output_dir, scenario="manufacturing")

    assert bundle.version == PHASE104F_VERSION
    assert bundle.passed is True
    assert bundle.score == 100
    assert bundle.workspace_passed is True
    assert bundle.contract_acceptance_passed is True
    assert bundle.runtime_smoke_passed is True
    assert bundle.redaction_status == "safe"
    assert bundle.file_count >= 20
    assert bundle.checksum_count >= 20
    assert (output_dir / "workspace" / "contract" / "openapi.json").exists()
    assert (output_dir / "workspace" / "src" / "api" / "qualibugClient.ts").exists()
    assert (output_dir / "runtime_smoke" / "frontend_runtime_smoke_report.json").exists()
    assert (output_dir / "contract_acceptance" / "api_contract_acceptance_report.json").exists()
    assert (output_dir / "phase104_frontend_handoff_bundle.zip").exists()

    manifest = json.loads((output_dir / "phase104_frontend_handoff_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == PHASE104F_VERSION
    assert manifest["passed"] is True
    assert manifest["redaction_status"] == "safe"

    with zipfile.ZipFile(output_dir / "phase104_frontend_handoff_bundle.zip") as archive:
        names = set(archive.namelist())
    assert "workspace/contract/openapi.json" in names
    assert "handoff/DEV_QUICKSTART.md" in names
    assert "runtime_smoke/frontend_runtime_smoke_report.md" in names

    combined = "\n".join(path.read_text(encoding="utf-8") for path in output_dir.rglob("*.md"))
    assert "raw-token" not in combined
    assert "Traceback" not in combined


def test_phase104f_validate_and_checksum_detect_tamper(tmp_path):
    output_dir = tmp_path / "frontend_handoff"
    build_frontend_handoff_bundle(output_dir=output_dir)

    report = validate_frontend_handoff_bundle(output_dir)
    checksum_ok, findings = verify_checksums(output_dir)

    assert report.passed is True
    assert report.score == 100
    assert checksum_ok is True
    assert findings == []

    readme = output_dir / "handoff" / "DEV_QUICKSTART.md"
    readme.write_text(readme.read_text(encoding="utf-8") + "\nmanual edit\n", encoding="utf-8")
    checksum_ok, findings = verify_checksums(output_dir)

    assert checksum_ok is False
    assert any("checksum mismatch" in item for item in findings)


def test_phase104f_cli_build_and_validate_only(tmp_path, capsys):
    output_dir = tmp_path / "frontend_handoff"

    code = main(["--output-dir", str(output_dir), "--scenario", "manufacturing"])
    captured = capsys.readouterr()

    assert code == 0
    assert "phase104f-frontend-handoff-bundle-v1" in captured.out
    assert (output_dir / "frontend_handoff_bundle_acceptance_report.json").exists()

    code = main(["--output-dir", str(output_dir), "--validate-only"])
    captured = capsys.readouterr()

    assert code == 0
    assert '"passed": true' in captured.out
