"""Unit tests for RunRetentionManager (SPEC P0-4, Phase 8, §20/§22/§23).

Covers: run-count retention deletes the OLDEST non-pinned manifests (SUCCESS
and FAILED groups independently), pinned runs exempt, GC only removes truly
unreferenced artifacts (shared artifacts survive), dry-run GC default vs
QUALIBUG_ARTIFACT_GC_ENABLE=true, quota eviction (oldest runs → GC →
recompute; never by artifact mtime) and scratch TTL cleanup (known temp
patterns only).
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_store import LocalArtifactStore
from ai_test_asset_center.run_manifest import RunManifestStore
from ai_test_asset_center.run_retention_manager import (
    RunRetentionManager,
    cleanup_stale_scratch,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(workspace: Path) -> LocalArtifactStore:
    return LocalArtifactStore(workspace / ".qualibug", compression="zstd")


@pytest.fixture()
def manifest_store(workspace: Path, store: LocalArtifactStore) -> RunManifestStore:
    return RunManifestStore(store, workspace)


def _commit_success(manifest_store, run_id: str, ref: str) -> None:
    manifest_store.commit_success(run_id, scan_result_ref=ref)


class TestRunRetention:
    def test_retention_removes_oldest_runs_only(self, workspace, store, manifest_store):
        shared = store.put({"shared": "payload"}, "SCAN_RESULT").artifact_id
        for index in range(7):
            _commit_success(manifest_store, f"run_{index}", shared)
        manager = RunRetentionManager(store, manifest_store, workspace)
        receipt = manager.run(run_retain=5, failed_run_retain=3, gc_enable=False)
        assert receipt["run_retention"]["removed_run_ids"] == ["run_0", "run_1"]
        assert receipt["run_retention"]["removed_count"] == 2
        remaining = [m.run_id for m in manifest_store.list_runs()]
        assert remaining == ["run_2", "run_3", "run_4", "run_5", "run_6"]
        # Shared artifact is still live for the remaining manifests.
        assert store.exists(shared)
        assert receipt["gc"]["dry_run"] is True

    def test_shared_artifact_survives_until_last_run_gone(self, workspace, store, manifest_store):
        shared = store.put({"shared": "payload"}, "SCAN_RESULT").artifact_id
        for index in range(3):
            _commit_success(manifest_store, f"run_{index}", shared)
        manager = RunRetentionManager(store, manifest_store, workspace)
        manager.run(run_retain=1, failed_run_retain=1, gc_enable=True, gc_grace_hours=0)
        assert store.exists(shared)  # newest run still references it
        # Deleting the last run makes the shared artifact garbage.
        manifest_store.delete("run_2")
        manager.run(run_retain=0, failed_run_retain=0, gc_enable=True, gc_grace_hours=0)
        assert not store.exists(shared)

    def test_failed_runs_retained_independently(self, workspace, store, manifest_store):
        ref = store.put({"a": 1}, "SCAN_RESULT").artifact_id
        for index in range(4):
            _commit_success(manifest_store, f"ok_{index}", ref)
        for index in range(5):
            manifest_store.commit_failed(
                f"bad_{index}", error_summary=f"failure-{index}"
            )
        manager = RunRetentionManager(store, manifest_store, workspace)
        receipt = manager.run(run_retain=2, failed_run_retain=3, gc_enable=False)
        assert receipt["run_retention"]["removed_run_ids"] == ["ok_0", "ok_1", "bad_0", "bad_1"]
        remaining = {m.run_id for m in manifest_store.list_runs()}
        assert remaining == {"ok_2", "ok_3", "bad_2", "bad_3", "bad_4"}

    def test_pinned_run_exempt_from_retention(self, workspace, store, manifest_store):
        ref = store.put({"pin": "me"}, "SCAN_RESULT").artifact_id
        manifest_store.commit_success("run_pinned", scan_result_ref=ref)
        manifest_store.pin("run_pinned", True)
        for index in range(6):
            _commit_success(manifest_store, f"run_{index}", ref)
        manager = RunRetentionManager(store, manifest_store, workspace)
        receipt = manager.run(run_retain=5, failed_run_retain=3, gc_enable=False)
        assert "run_pinned" not in receipt["run_retention"]["removed_run_ids"]
        manifest_store.load("run_pinned")
        assert receipt["gc"]["pinned_count"] >= 1

    def test_gc_real_delete_only_when_enabled(self, workspace, store, manifest_store, monkeypatch):
        orphan = store.put({"orphan": "y" * 50000}, "EXECUTION_OUTPUT").artifact_id
        manager = RunRetentionManager(store, manifest_store, workspace)
        monkeypatch.delenv("QUALIBUG_ARTIFACT_GC_ENABLE", raising=False)
        receipt = manager.run(run_retain=5, failed_run_retain=3, gc_grace_hours=0)
        assert receipt["gc"]["dry_run"] is True
        assert receipt["gc"]["deleted_count"] == 0
        assert store.exists(orphan)
        monkeypatch.setenv("QUALIBUG_ARTIFACT_GC_ENABLE", "true")
        receipt = manager.run(run_retain=5, failed_run_retain=3, gc_grace_hours=0)
        assert receipt["gc"]["dry_run"] is False
        assert receipt["gc"]["deleted_count"] == 1
        assert not store.exists(orphan)


class TestQuota:
    def test_quota_evicts_oldest_runs_then_gc(self, workspace, store, manifest_store):
        # Each run owns a distinct artifact; measure the real physical size
        # and set the quota below it so eviction is forced.
        refs = []
        for index in range(6):
            ref = store.put(
                {"run": index, "blob": "q" * 20000}, "SCAN_RESULT"
            ).artifact_id
            refs.append(ref)
            _commit_success(manifest_store, f"run_{index}", ref)
        physical_total = sum(
            int(store.metadata(ref).stored_size) for ref in store.list_all()
        )
        manager = RunRetentionManager(store, manifest_store, workspace)
        quota_gb = (physical_total * 0.55) / (1024.0 ** 3)
        receipt = manager.run(
            run_retain=100, failed_run_retain=3, max_gb=quota_gb,
            gc_enable=True, gc_grace_hours=0,
        )
        quota = receipt["quota"]
        assert quota["enabled"] is True
        assert quota["removed_count"] > 0
        assert quota["removed_count"] < 6  # newest runs survive
        assert quota["still_over"] is False
        # Oldest runs were evicted first.
        for run_id in quota["removed_run_ids"][:1]:
            with pytest.raises(Exception):
                manifest_store.load(run_id)
        # The newest run's artifact must still exist.
        assert store.exists(refs[-1])

    def test_quota_disabled_by_default(self, workspace, store, manifest_store):
        manager = RunRetentionManager(store, manifest_store, workspace)
        receipt = manager.run(run_retain=5, failed_run_retain=3, max_gb=0)
        assert receipt["quota"]["enabled"] is False

    def test_quota_never_touches_pinned(self, workspace, store, manifest_store):
        pinned_ref = store.put({"pin": "x" * 4000}, "SCAN_RESULT").artifact_id
        manifest_store.commit_success("run_pinned", scan_result_ref=pinned_ref)
        manifest_store.pin("run_pinned", True)
        manager = RunRetentionManager(store, manifest_store, workspace)
        receipt = manager.run(
            run_retain=100, failed_run_retain=3, max_gb=0.000001,
            gc_enable=True, gc_grace_hours=0,
        )
        assert "run_pinned" not in receipt["quota"]["removed_run_ids"]
        assert store.exists(pinned_ref)
        manifest_store.load("run_pinned")


class TestScratchTTL:
    def test_scratch_ttl_removes_only_known_temp_patterns(self, tmp_path):
        scratch = tmp_path / ".scratch"
        scratch.mkdir()
        old_tmp = scratch / "interrupt.q-abc.tmp"
        old_tmp.write_text("x")
        old_legacy = scratch / "scan_result.json.legacy"
        old_legacy.write_text("y")
        keep_script = scratch / "_diagnose.py"
        keep_script.write_text("z")
        keep_report = scratch / "REPORT_P04b.md"
        keep_report.write_text("w")
        old = time.time() - 30 * 3600
        for path in (old_tmp, old_legacy, keep_script, keep_report):
            os.utime(path, (old, old))
        receipt = cleanup_stale_scratch(tmp_path, ttl_hours=24)
        assert receipt["removed_count"] == 2
        assert not old_tmp.exists()
        assert not old_legacy.exists()
        assert keep_script.exists()
        assert keep_report.exists()

    def test_scratch_ttl_keeps_young_files(self, tmp_path):
        scratch = tmp_path / ".scratch"
        scratch.mkdir()
        young = scratch / "x.q-tmp.tmp"
        young.write_text("new")
        receipt = cleanup_stale_scratch(tmp_path, ttl_hours=24)
        assert receipt["removed_count"] == 0
        assert young.exists()
