"""Unit tests for fine-grained evidence artifactization (SPEC P0-4 §12/§13/§33).

Covers the fine-grained split (HTTP_REQUEST/HTTP_RESPONSE/DB_SNAPSHOT/
EXECUTION_OUTPUT/METADATA + HAR per-entry refs), cross-run dedup of unchanged
evidence pieces, Dual Read hydration through the legacy bundle API, the
report-view read surface and legacy-bundle read compatibility.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.artifact_store import LocalArtifactStore
from ai_test_asset_center.evidence_artifact_store import (
    EvidenceArtifactError,
    load_evidence_bundle,
    persist_evidence_bundle,
    verify_evidence_bundle,
)
from ai_test_asset_center.evidence_artifactization import (
    load_evidence_bundle_report_view,
    load_evidence_bundle_v2,
    persist_evidence_bundle_artifactized,
    verify_evidence_bundle_v2,
)


def _finding(finding_id: str, body: str) -> dict:
    return {
        "canonical_defect_id": finding_id,
        "title": f"title-{finding_id}",
        "status": "confirmed",
        "evidence": {"status_code": 200, "payload_summary": body[:80]},
        "raw_evidence": {
            "request_raw": {"method": "GET", "path": f"/api/{finding_id}", "body": body},
            "response_raw": {"status_code": 200, "body": body},
            "db_snapshot": {"before": {"qty": 5}, "after": {"qty": 4}},
        },
    }


def _har() -> dict:
    return {
        "status": "captured",
        "entries": [
            {
                "request": {"method": "POST", "url": "http://target/api/orders",
                            "headers": [], "postData": {"text": "order body"}},
                "response": {"status": 201, "statusText": "Created",
                             "content": {"text": "created"}},
            },
            {
                "request": {"method": "GET", "url": "http://target/api/orders", "headers": []},
                "response": {"status": 200, "content": {"text": "list payload"}},
            },
        ],
    }


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(workspace: Path) -> LocalArtifactStore:
    return LocalArtifactStore(workspace / ".qualibug", compression="zstd")


class TestArtifactizedPersist:
    def _persist(self, workspace, store, run_id, findings=None, har=None):
        return persist_evidence_bundle_artifactized(
            "proj",
            root=workspace,
            run_id=run_id,
            campaign={"campaign_id": "camp-1", "campaign_status": "completed"},
            runtime_contract={"source_manifest": {"source_id": "s1"}},
            execution_status="completed",
            auto_har=har if har is not None else _har(),
            evidence_graphs=[],
            findings=findings if findings is not None else [
                _finding("D-1", "x" * 3000), _finding("D-2", "y" * 3000),
            ],
            ui_execution={"status": "not_requested"},
            artifact_store=store,
        )

    def test_split_parts_and_pointer(self, workspace, store):
        result = self._persist(workspace, store, "run_1")
        assert result["status"] == "persisted"
        assert result["artifactized"] is True
        assert result["bundle_id"].startswith("evb_")
        assert result["artifact_manifest_ref"].startswith("sha256:")
        pointer = workspace / "platform_workspace" / "proj" / "evidence_bundles" / result["bundle_id"] / "manifest.pointer.json"
        assert pointer.is_file()
        # 8 pieces: metadata + execution_output + db_snapshot + 2 req + 2 resp
        # + bundle manifest itself
        assert result["artifact_count"] == 8

    def test_unchanged_pieces_dedup_across_runs(self, workspace, store):
        self._persist(workspace, store, "run_1")
        before = len(store.list_all())
        self._persist(workspace, store, "run_2")
        after = len(store.list_all())
        # run_2 differs only in its own bundle manifest (+1) — all evidence
        # pieces (request/response/db/execution/metadata) are reused.
        assert after - before == 1, (before, after)
        stats = store.snapshot_stats()
        assert stats["artifact_reused_count"] >= 7

    def test_changed_piece_creates_only_that_piece(self, workspace, store):
        base = [_finding("D-1", "x" * 3000), _finding("D-2", "y" * 3000)]
        self._persist(workspace, store, "run_1", findings=base)
        before = len(store.list_all())
        changed = [_finding("D-1", "x" * 3000), _finding("D-2", "y" * 3000 + "-v2")]
        self._persist(workspace, store, "run_2", findings=changed)
        after = len(store.list_all())
        # execution_output + db_snapshot + bundle manifest changed (3 new),
        # requests/responses/metadata unchanged (dedup).
        assert 1 <= after - before <= 3, (before, after)

    def test_redaction_boundary_parity_preserved(self, workspace, store):
        # The existing redaction boundary (key-name based, shared with the
        # legacy bundle writer) must apply identically to artifactized parts.
        findings = [_finding("D-1", "x" * 100)]
        findings[0]["raw_evidence"]["request_raw"]["password"] = "hunter2"
        findings[0]["raw_evidence"]["request_raw"]["headers"] = [
            {"name": "Authorization", "value": "Bearer SECRET-TOKEN"}
        ]
        self._persist(workspace, store, "run_1", findings=findings)
        result = self._persist(workspace, store, "run_1", findings=findings)
        manifest = store.get_json(result["artifact_manifest_ref"])
        execution_ref = manifest["parts"]["execution_output_ref"]
        payload = store.get_json(execution_ref)
        request_raw = payload["findings"][0]["raw_evidence"]["request_raw"]
        assert request_raw["password"] == "<REDACTED>"
        # non-key-based content is out of the existing boundary's scope — the
        # artifactized writer must behave exactly like the legacy writer here.
        headers = request_raw["headers"]
        assert headers == [{"name": "Authorization", "value": "Bearer SECRET-TOKEN"}]

    def test_identity_scope_mismatch_still_fails(self, workspace, store):
        registry = {
            "schema_version": "qualibug.canonical-defect-registry.v3",
            "status": "VERIFIED",
            "canonical_defect_ids": ["D-1"],
            "delivery_occurrence_finding_ids": ["O-1"],
            "canonical_defect_count": 1,
            "delivery_occurrence_count": 1,
        }
        with pytest.raises(EvidenceArtifactError):
            persist_evidence_bundle_artifactized(
                "proj",
                root=workspace,
                run_id="run_1",
                campaign={},
                runtime_contract={},
                execution_status="completed",
                auto_har={},
                evidence_graphs=[],
                findings=[_finding("D-2", "z")],  # mismatched finding id
                canonical_defect_registry=registry,
                delivery_occurrences=[{"finding_id": "O-1"}],
                artifact_store=store,
            )


class TestDualRead:
    def test_legacy_api_hydrates_artifactized_bundle(self, workspace, store):
        result = self._persist_bundle(workspace, store, "run_1")
        manifest = load_evidence_bundle("proj", result["bundle_id"], root=workspace)
        assert manifest["artifactized"] is True
        assert manifest["bundle_id"] == result["bundle_id"]
        assert manifest["bundle_sha256"] == result["bundle_sha256"]
        assert manifest["artifacts"], "hydrated part records expected"
        assert manifest["parts"]["execution_output_ref"].startswith("sha256:")

    def test_legacy_verify_passes_for_artifactized_bundle(self, workspace, store):
        result = self._persist_bundle(workspace, store, "run_1")
        verification = verify_evidence_bundle("proj", result["bundle_id"], root=workspace)
        assert verification["valid"] is True
        assert verification["checked"] > 0

    def test_legacy_bundle_still_readable(self, workspace):
        # Legacy bundles (no pointer file) keep the old layout and readers.
        legacy = persist_evidence_bundle(
            "proj",
            root=workspace,
            run_id="legacy_1",
            campaign={"campaign_id": "camp-1"},
            runtime_contract={"source_manifest": {"source_id": "s1"}},
            execution_status="completed",
            auto_har={},
            evidence_graphs=[],
            findings=[_finding("D-1", "x" * 100)],
            ui_execution={},
        )
        manifest = load_evidence_bundle("proj", legacy["bundle_id"], root=workspace)
        assert manifest.get("artifactized") is not True
        assert verify_evidence_bundle("proj", legacy["bundle_id"], root=workspace)["valid"] is True

    def test_report_view_reads_without_disk_copy(self, workspace, store):
        result = self._persist_bundle(workspace, store, "run_1")
        view = load_evidence_bundle_report_view("proj", result["bundle_id"], root=workspace, artifact_store=store)
        assert view["campaign"]["campaign_id"] == "camp-1"
        assert len(view["findings"]) == 2
        # nothing but the pointer exists in the bundle directory
        bundle_dir = workspace / "platform_workspace" / "proj" / "evidence_bundles" / result["bundle_id"]
        assert sorted(path.name for path in bundle_dir.iterdir()) == ["manifest.pointer.json"]

    def _persist_bundle(self, workspace, store, run_id):
        return persist_evidence_bundle_artifactized(
            "proj",
            root=workspace,
            run_id=run_id,
            campaign={"campaign_id": "camp-1", "campaign_status": "completed"},
            runtime_contract={"source_manifest": {"source_id": "s1"}},
            execution_status="completed",
            auto_har=_har(),
            evidence_graphs=[],
            findings=[_finding("D-1", "x" * 3000), _finding("D-2", "y" * 3000)],
            ui_execution={"status": "not_requested"},
            artifact_store=store,
        )

    def test_v2_verify_detects_missing_part(self, workspace, store):
        result = self._persist_bundle(workspace, store, "run_1")
        manifest = store.get_json(result["artifact_manifest_ref"])
        missing_ref = manifest["parts"]["execution_output_ref"]
        store.delete(missing_ref)
        verification = verify_evidence_bundle_v2("proj", result["bundle_id"], root=workspace, artifact_store=store)
        assert verification["valid"] is False
        assert verification["code"] == "EVIDENCE_ARTIFACT_MISSING"
