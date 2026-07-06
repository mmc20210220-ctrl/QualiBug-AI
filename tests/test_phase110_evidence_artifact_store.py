from __future__ import annotations

import json

from ai_test_asset_center.evidence_artifact_store import (
    load_evidence_bundle,
    persist_evidence_bundle,
    verify_evidence_bundle,
)


def test_persisted_bundle_is_redacted_and_integrity_verifiable(tmp_path):
    bundle = persist_evidence_bundle(
        "enterprise-project",
        root=tmp_path,
        run_id="scan-1",
        campaign={"campaign_id": "CMP_1"},
        runtime_contract={"source_manifest": {"source_id": "api", "source_hash": "a" * 64}, "authorization": "secret-token"},
        execution_status="executed",
        auto_har={"status": "captured", "entries": [{"request": {"headers": {"Authorization": "Bearer secret-token"}}}]},
        evidence_graphs=[{"id": "graph-1"}],
        findings=[{"title": "candidate"}],
    )

    manifest = load_evidence_bundle("enterprise-project", bundle["bundle_id"], root=tmp_path)
    result = verify_evidence_bundle("enterprise-project", bundle["bundle_id"], root=tmp_path)
    runtime_path = tmp_path / manifest["artifacts"][0]["path"]

    assert bundle["status"] == "persisted"
    assert bundle["evidence_level"] == "runtime_captured"
    assert result["valid"] is True
    assert manifest["campaign_id"] == "CMP_1"
    assert "secret-token" not in (tmp_path / bundle["manifest_ref"]).read_text(encoding="utf-8")
    assert runtime_path.exists() is False


def test_tampered_artifact_invalidates_bundle(tmp_path):
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
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    bundle_dir = manifest_path.parent
    first_artifact = bundle_dir / manifest["artifacts"][0]["path"]
    first_artifact.write_text('{"tampered":true}', encoding="utf-8")

    result = verify_evidence_bundle("enterprise-project", bundle["bundle_id"], root=tmp_path)

    assert result["valid"] is False
    assert result["code"] == "EVIDENCE_ARTIFACT_HASH_MISMATCH"
