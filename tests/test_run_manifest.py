"""Unit tests for the Run Manifest store (SPEC P0-4 §14-§16, §20-§21).

Covers the SPEC §15 commit ordering (references verified before the manifest
is atomically committed; a run is never SUCCESS before its manifest), failed
run policy (§16: metadata + error summary only, no large evidence), pinning
(§21), verify, live-ref collection and the Phase-8 retention skeleton.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from ai_test_asset_center.artifact_store import LocalArtifactStore
from ai_test_asset_center.run_manifest import (
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    RunManifestError,
    RunManifestStore,
    sanitize_run_id,
)


@pytest.fixture()
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "qualibug", compression="zstd")


@pytest.fixture()
def manifest_store(tmp_path: Path, store: LocalArtifactStore) -> RunManifestStore:
    return RunManifestStore(store, tmp_path / "runs")


class TestCommitOrdering:
    def test_success_requires_all_refs_present(self, manifest_store, store):
        missing = "sha256:" + "a" * 64
        with pytest.raises(RunManifestError) as exc:
            manifest_store.commit_success(
                "run_1",
                scan_result_ref=missing,
                evidence_bundle_refs=[store.put({"e": 1}, "EVIDENCE_BUNDLE_MANIFEST").artifact_id],
            )
        assert "run_manifest_refs_missing" in str(exc.value)
        # No manifest may exist for the failed commit (SPEC §15).
        with pytest.raises(RunManifestError):
            manifest_store.load("run_1")

    def test_success_commits_atomically_after_verification(self, manifest_store, store):
        ref = store.put({"result": "x" * 1000}, "SCAN_RESULT")
        evidence = store.put({"parts": {}}, "EVIDENCE_BUNDLE_MANIFEST").artifact_id
        manifest = manifest_store.commit_success(
            "run_1",
            scan_result_ref=ref.artifact_id,
            evidence_bundle_refs=[evidence],
            trace_refs=[],
        )
        assert manifest.status == RUN_STATUS_SUCCESS
        assert manifest.scan_result_ref == ref.artifact_id
        loaded = manifest_store.load("run_1")
        assert loaded.run_id == "run_1"
        assert loaded.evidence_bundle_refs == [evidence]
        assert manifest_store.verify("run_1")["valid"] is True
        assert (manifest_store.root / "runs" / "run_1" / "manifest.json").is_file()

    def test_verify_reports_missing_refs(self, manifest_store, store):
        ref = store.put({"a": 1}, "SCAN_RESULT")
        manifest_store.commit_success("run_1", scan_result_ref=ref.artifact_id)
        store.delete(ref.artifact_id)
        verification = manifest_store.verify("run_1")
        assert verification["valid"] is False
        assert ref.artifact_id in verification["missing_refs"]


class TestFailedRuns:
    def test_failed_run_keeps_only_small_surface(self, manifest_store, store):
        debug = store.put(b"debug trace", "DEBUG_TRACE").artifact_id
        manifest = manifest_store.commit_failed(
            "run_bad",
            error_summary="source contract blocked: SOURCE_PROVENANCE_MISSING",
            debug_trace_ref=debug,
        )
        assert manifest.status == RUN_STATUS_FAILED
        assert manifest.evidence_bundle_refs == []
        assert manifest.trace_refs == []
        assert manifest.error_summary
        loaded = manifest_store.load("run_bad")
        assert loaded.debug_trace_ref == debug

    def test_failed_run_drops_unverifiable_debug_ref(self, manifest_store):
        manifest = manifest_store.commit_failed(
            "run_bad",
            error_summary="boom",
            debug_trace_ref="sha256:" + "b" * 64,
        )
        assert manifest.debug_trace_ref is None


class TestPinAndRetention:
    def test_pinned_run_never_deleted(self, manifest_store, store):
        ref = store.put({"a": 1}, "SCAN_RESULT")
        manifest_store.commit_success("run_1", scan_result_ref=ref.artifact_id)
        manifest_store.pin("run_1", True)
        outcome = manifest_store.delete("run_1")
        assert outcome["deleted"] is False
        assert outcome["reason"] == "pinned"
        manifest_store.load("run_1")  # still there
        manifest_store.pin("run_1", False)
        assert manifest_store.delete("run_1")["deleted"] is True

    def test_live_refs_mark_and_sweep_source(self, manifest_store, store):
        shared = store.put({"shared": 1}, "HTTP_RESPONSE").artifact_id
        only_run1 = store.put({"r1": 1}, "HTTP_REQUEST").artifact_id
        manifest_store.commit_success("run_1", scan_result_ref=shared)
        manifest_store.commit_success("run_2", scan_result_ref=shared)
        live = manifest_store.collect_live_refs()
        assert shared in live
        # orphan artifact is NOT in the live set
        assert only_run1 not in live

    def test_retention_removes_oldest_manifests_only(self, manifest_store, store):
        ref = store.put({"a": 1}, "SCAN_RESULT")
        for index in range(6):
            manifest_store.commit_success(f"run_{index}", scan_result_ref=ref.artifact_id)
        # 5 kept + 1 oldest removed for SUCCESS runs (QUALIBUG_RUN_RETAIN floor)
        outcome = manifest_store.retain_runs(run_retain=5, failed_run_retain=3)
        assert outcome["removed_count"] == 1
        assert outcome["removed_run_ids"] == ["run_0"]
        assert manifest_store.load("run_5").run_id == "run_5"
        with pytest.raises(RunManifestError):
            manifest_store.load("run_0")


class TestSanitization:
    def test_run_id_sanitized(self):
        assert sanitize_run_id("scan_acme_1720000000000") == "scan_acme_1720000000000"
        assert sanitize_run_id("../evil/run") == ".._evil_run"
        with pytest.raises(RunManifestError):
            sanitize_run_id("")
        with pytest.raises(RunManifestError):
            sanitize_run_id("..")
