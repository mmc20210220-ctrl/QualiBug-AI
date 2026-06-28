from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.phase103_delivery_bundle import build_delivery_bundle
from ai_test_asset_center.phase103_delivery_release import build_delivery_release, main, verify_delivery_release


def test_phase103z_builds_release_ledger_with_checksums(tmp_path: Path) -> None:
    bundle = tmp_path / "delivery"
    build_delivery_bundle(scenarios=("manufacturing",), output_dir=bundle)

    release = build_delivery_release(bundle, output_dir=tmp_path / "release", require_zip=True)

    assert release["passed"] is True
    assert release["acceptance_score"] >= 90
    assert release["artifact_count"] > 10
    release_dir = tmp_path / "release"
    assert (release_dir / "release_manifest.json").exists()
    assert (release_dir / "release_manifest.md").exists()
    assert (release_dir / "CHECKSUMS.sha256").exists()
    assert (release_dir / "CUSTOMER_RELEASE_NOTES.md").exists()
    assert (release_dir / "RELEASE_RECEIPT.md").exists()
    checksums = (release_dir / "CHECKSUMS.sha256").read_text(encoding="utf-8")
    assert "delivery_manifest.json" in checksums
    serialized = json.dumps(release, ensure_ascii=False)
    assert "raw-manufacturing-token" not in serialized
    assert "DemoPasswordShouldBeRedacted" not in serialized


def test_phase103z_verifies_and_detects_tampered_release(tmp_path: Path) -> None:
    bundle = tmp_path / "delivery"
    build_delivery_bundle(scenarios=("ecommerce",), output_dir=bundle)
    release_dir = tmp_path / "release"
    build_delivery_release(bundle, output_dir=release_dir, require_zip=True)

    ok = verify_delivery_release(bundle, release_dir)
    assert ok["passed"] is True

    one_pager = bundle / "commercial" / "01_one_pager.md"
    one_pager.write_text(one_pager.read_text(encoding="utf-8") + "\nTampered after release.\n", encoding="utf-8")
    tampered = verify_delivery_release(bundle, release_dir)
    assert tampered["passed"] is False
    failed = {check["key"] for check in tampered["failed_checks"]}
    assert "checksum_verify" in failed


def test_phase103z_cli_build_first_then_verify(tmp_path: Path, capsys) -> None:
    bundle = tmp_path / "cli_bundle"
    release_dir = tmp_path / "cli_release"

    code = main([
        "--build-first",
        "--scenario",
        "saas",
        "--bundle-dir",
        str(bundle),
        "--output-dir",
        str(release_dir),
    ])
    captured = capsys.readouterr().out
    assert code == 0
    assert "artifact_count" in captured
    assert (release_dir / "release_manifest.json").exists()

    verify_code = main(["--verify", "--bundle-dir", str(bundle), "--output-dir", str(release_dir)])
    assert verify_code == 0
    verification = json.loads((release_dir / "release_verification_report.json").read_text(encoding="utf-8"))
    assert verification["passed"] is True
