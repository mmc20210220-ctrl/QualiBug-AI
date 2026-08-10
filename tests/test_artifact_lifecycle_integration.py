"""Integration test: 5 simulated Runs with ~80% shared evidence (SPEC §42).

Verifies the P0-4 core claim with real data (no hardcoded percentages):
- ``reused_artifacts > 0`` across runs;
- physical growth < logical growth (dedup actually saves space);
- the new architecture stores far less than the legacy one (which would copy
  the full bundle per run — 5x logical of a single run);
- the run-manifest post hook commits SUCCESS/FAILED manifests with the
  SPEC §15 ordering and a visible lifecycle summary (SPEC §37).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.artifact_gc import ArtifactGarbageCollector
from ai_test_asset_center.artifact_store import LocalArtifactStore
from ai_test_asset_center.evidence_artifactization import (
    persist_evidence_bundle_artifactized,
)
from ai_test_asset_center.run_manifest import RUN_STATUS_SUCCESS, RunManifestStore


def _finding(finding_id: str, body: str) -> dict:
    return {
        "canonical_defect_id": finding_id,
        "title": f"title-{finding_id}",
        "status": "confirmed",
        "raw_evidence": {
            "request_raw": {"method": "GET", "path": f"/api/{finding_id}", "body": body},
            "response_raw": {"status_code": 200, "body": body},
            "db_snapshot": {"before": {"qty": 5}, "after": {"qty": 4}},
        },
    }


def _shared_har() -> dict:
    return {
        "status": "captured",
        "entries": [
            {
                "request": {"method": "POST", "url": "http://target/api/orders",
                            "headers": [], "postData": {"text": "order body" * 50}},
                "response": {"status": 201, "content": {"text": "created" * 50}},
            },
            {
                "request": {"method": "GET", "url": "http://target/api/orders", "headers": []},
                "response": {"status": 200, "content": {"text": "list payload" * 50}},
            },
            {
                "request": {"method": "GET", "url": "http://target/api/products", "headers": []},
                "response": {"status": 200, "content": {"text": "products payload" * 50}},
            },
        ],
    }


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(workspace: Path) -> LocalArtifactStore:
    return LocalArtifactStore(workspace / ".qualibug", compression="zstd")


class TestFiveRunIntegration:
    def test_dedup_across_five_runs(self, workspace, store):
        run_count = 5
        stable_findings = [_finding("D-1", "x" * 4000), _finding("D-2", "y" * 4000)]
        volatile_findings = [_finding("D-3", "z" * 4000)]
        per_run_churn = [
            _finding("D-4", f"run-{index}-specific" + "q" * 3000)
            for index in range(run_count)
        ]

        lifecycle_summaries = []
        single_run_artifact_count = 0
        for index in range(run_count):
            findings = (
                stable_findings
                + volatile_findings
                + [per_run_churn[index]]  # ~20% evidence changes per run
            )
            stats_before = store.snapshot_stats()
            result = persist_evidence_bundle_artifactized(
                "proj",
                root=workspace,
                run_id=f"run_{index}",
                campaign={"campaign_id": "camp-1", "campaign_status": "completed"},
                runtime_contract={"source_manifest": {"source_id": "s1"}},
                execution_status="completed",
                auto_har=_shared_har(),
                evidence_graphs=[],
                findings=findings,
                ui_execution={"status": "not_requested"},
                artifact_store=store,
            )
            if index == 0:
                single_run_artifact_count = int(result["artifact_count"])
            lifecycle = {
                key: after - before
                for key, (before, after) in {
                    key: (stats_before.get(key, 0.0), store.snapshot_stats().get(key, 0.0))
                    for key in (
                        "artifact_new_count",
                        "artifact_reused_count",
                        "artifact_logical_bytes",
                        "artifact_physical_bytes",
                        "artifact_dedup_saved_bytes",
                    )
                }.items()
            }
            lifecycle_summaries.append(lifecycle)
            assert result["status"] == "persisted"

        stats = store.snapshot_stats()
        assert stats["artifact_reused_count"] > 0, "cross-run reuse required"
        physical_total = float(stats["artifact_physical_bytes"])
        logical_total = float(stats["artifact_logical_bytes"])
        assert physical_total < logical_total, "physical growth must stay below logical growth"

        # New architecture object count stays far below the legacy one —
        # legacy would physically copy every run's full bundle
        # (~5 x single-run artifact count).
        artifact_count = len(store.list_all())
        assert artifact_count < 5 * single_run_artifact_count, artifact_count

        # Legacy baseline: every run physically copies its full bundle, so
        # legacy physical bytes ~= 5 x one run's logical bytes. The new
        # architecture must stay below that.
        run0_logical = float(lifecycle_summaries[0]["artifact_logical_bytes"])
        legacy_physical_estimate = run_count * run0_logical
        assert physical_total < legacy_physical_estimate, (
            physical_total, legacy_physical_estimate
        )

        # Lifecycle summary per run is honest: new + reused > 0 and the
        # dedup ratio reflects reuse on later runs.
        assert lifecycle_summaries[0]["artifact_new_count"] > 0
        assert lifecycle_summaries[0]["artifact_reused_count"] == 0
        assert lifecycle_summaries[4]["artifact_reused_count"] > 0
        assert lifecycle_summaries[4]["artifact_dedup_saved_bytes"] > 0

    def test_manifest_commit_and_gc_roundtrip(self, workspace, store):
        manifest_store = RunManifestStore(store, workspace / "runs")
        shared = _shared_har()
        for index in range(3):
            result = persist_evidence_bundle_artifactized(
                "proj",
                root=workspace,
                run_id=f"run_{index}",
                campaign={"campaign_id": "camp-1"},
                runtime_contract={"source_manifest": {"source_id": "s1"}},
                execution_status="completed",
                auto_har=shared,
                evidence_graphs=[],
                findings=[_finding("D-1", "x" * 2000)],
                ui_execution={},
                artifact_store=store,
            )
            manifest_store.commit_success(
                f"run_{index}",
                scan_result_ref=store.put({"scan": index}, "SCAN_RESULT").artifact_id,
                evidence_bundle_refs=[result["artifact_manifest_ref"]],
            )
        assert len(manifest_store.list_runs()) == 3
        assert all(
            m.status == RUN_STATUS_SUCCESS for m in manifest_store.list_runs()
        )
        # Delete two runs; GC must keep the artifacts the third run still
        # references and reclaim the others' bundle manifests.
        manifest_store.delete("run_0")
        manifest_store.delete("run_1")
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["live_count"] > 0
        assert plan["garbage_count"] > 0
        assert plan["reclaimable_bytes"] > 0
        before_delete = len(store.list_all())
        outcome = gc.run(dry_run=False)
        remaining = len(store.list_all())
        assert remaining < before_delete
        assert outcome["deleted_count"] == plan["garbage_count"]


class TestRunManifestPostHook:
    def test_hook_commits_success_manifest(self, workspace, store, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "true")
        from ai_test_asset_center.run_manifest_hook import _commit_run_manifest

        output_root = workspace / "platform_outputs" / "proj"
        output_root.mkdir(parents=True)
        (output_root / "scan_result.json").write_text('{"success": true}', encoding="utf-8")
        result = persist_evidence_bundle_artifactized(
            "proj",
            root=workspace,
            run_id="scan_proj_1",
            campaign={"campaign_id": "camp-1"},
            runtime_contract={"source_manifest": {"source_id": "s1"}},
            execution_status="completed",
            auto_har={},
            evidence_graphs=[],
            findings=[],
            ui_execution={},
            artifact_store=store,
        )
        payload = {
            "scan_id": "scan_proj_1",
            "success": True,
            "execution_status": "completed",
            "evidence_bundle": result,
        }
        returned = _commit_run_manifest(payload, project="proj", root=workspace)
        receipt = returned["run_manifest_receipt"]
        assert receipt["status"] == "committed"
        assert receipt["manifest_status"] == RUN_STATUS_SUCCESS
        assert receipt["scan_result_ref"].startswith("sha256:")
        assert receipt["evidence_bundle_refs"] == [result["artifact_manifest_ref"]]
        assert receipt["lifecycle"]["artifact_new_count"] > 0
        manifest_store = RunManifestStore(store, workspace)
        manifest = manifest_store.load("scan_proj_1")
        assert manifest.status == RUN_STATUS_SUCCESS
        assert manifest.evidence_bundle_refs == [result["artifact_manifest_ref"]]

    def test_hook_commits_failed_manifest_without_evidence(self, workspace, store, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "true")
        from ai_test_asset_center.run_manifest_hook import _commit_run_manifest

        output_root = workspace / "platform_outputs" / "proj"
        output_root.mkdir(parents=True)
        (output_root / "scan_result.json").write_text('{"success": false}', encoding="utf-8")
        payload = {
            "scan_id": "scan_proj_failed",
            "success": False,
            "execution_status": "FAILED_SAFE",
            "error": "evidence_bundle_persistence_failed",
            "evidence_bundle": {"status": "not_created", "reason": "scan_blocked"},
        }
        returned = _commit_run_manifest(payload, project="proj", root=workspace)
        receipt = returned["run_manifest_receipt"]
        assert receipt["status"] == "committed"
        assert receipt["manifest_status"] == "FAILED"
        assert receipt["error_summary"]
        manifest_store = RunManifestStore(store, workspace)
        manifest = manifest_store.load("scan_proj_failed")
        assert manifest.status == "FAILED"
        assert manifest.evidence_bundle_refs == []
        assert manifest.error_summary

    def test_hook_skips_when_store_disabled(self, workspace, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "false")
        from ai_test_asset_center.run_manifest_hook import _commit_run_manifest

        payload = {"scan_id": "scan_x", "success": True}
        returned = _commit_run_manifest(payload, project="proj", root=workspace)
        assert returned == payload
        assert "run_manifest_receipt" not in returned
