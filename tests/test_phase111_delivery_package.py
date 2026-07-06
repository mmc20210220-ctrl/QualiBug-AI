from __future__ import annotations

import pytest

from ai_test_asset_center.enterprise_delivery_package import DeliveryPackageError, create_delivery_package
from ai_test_asset_center.evidence_artifact_store import persist_evidence_bundle


def _scan_result(bundle: dict[str, str]) -> dict[str, object]:
    return {
        "campaign": {"campaign_id": "CMP_1", "campaign_status": "completed"},
        "runtime_contract": {"source_manifest": {"source_id": "api-contract", "source_hash": "a" * 64}},
        "release_gate": {"verdict": "not_ready", "status": "inconclusive"},
        "evidence_bundle": bundle,
    }


def test_delivery_package_contains_verified_evidence_and_authoritative_result(tmp_path):
    bundle = persist_evidence_bundle(
        "enterprise-project",
        root=tmp_path,
        run_id="scan-1",
        campaign={"campaign_id": "CMP_1"},
        runtime_contract={"source_manifest": {"source_id": "api-contract", "source_hash": "a" * 64}},
        execution_status="plan_only",
        auto_har={"status": "no_traffic"},
        evidence_graphs=[],
        findings=[],
    )

    package = create_delivery_package("enterprise-project", root=tmp_path, scan_result=_scan_result(bundle))

    assert package["status"] == "created"
    assert package["package_ref"].endswith(".zip")
    assert len(package["sha256"]) == 64


def test_delivery_package_refuses_unverified_evidence_or_missing_gate(tmp_path):
    bundle = persist_evidence_bundle(
        "enterprise-project",
        root=tmp_path,
        run_id="scan-1",
        campaign={},
        runtime_contract={},
        execution_status="plan_only",
        auto_har={"status": "no_traffic"},
        evidence_graphs=[],
        findings=[],
    )
    manifest_path = tmp_path / bundle["manifest_ref"]
    artifact = manifest_path.parent / "runtime_contract.json"
    artifact.write_text('{"tampered":true}', encoding="utf-8")

    with pytest.raises(DeliveryPackageError, match="delivery_evidence_bundle_not_verified"):
        create_delivery_package("enterprise-project", root=tmp_path, scan_result=_scan_result(bundle))

    with pytest.raises(DeliveryPackageError, match="delivery_release_gate_missing"):
        create_delivery_package("enterprise-project", root=tmp_path, scan_result={"evidence_bundle": bundle})
