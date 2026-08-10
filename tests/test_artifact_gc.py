"""Unit tests for the reference garbage collector (SPEC P0-4 §17-§19, §36).

Covers Test 5 (artifact shared by two runs survives deleting one run), Test 6
(deleting both runs makes it garbage), Test 7 (pinned runs protect refs),
Test 8/9 (grace period), Test 10 (knowledge.db untouched) and the dry-run
baseline (§36: live/garbage/reclaimable/protected/pinned).
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_gc import ArtifactGarbageCollector
from ai_test_asset_center.artifact_store import LocalArtifactStore
from ai_test_asset_center.run_manifest import RunManifestStore


@pytest.fixture()
def store(tmp_path: Path) -> LocalArtifactStore:
    return LocalArtifactStore(tmp_path / "qualibug", compression="zstd")


@pytest.fixture()
def manifest_store(tmp_path: Path, store: LocalArtifactStore) -> RunManifestStore:
    return RunManifestStore(store, tmp_path / "runs")


class TestReferenceGC:
    def test_5_delete_one_run_keeps_shared_artifact(self, store, manifest_store):
        shared = store.put({"shared": "payload"}, "HTTP_RESPONSE").artifact_id
        manifest_store.commit_success("run_1", scan_result_ref=shared)
        manifest_store.commit_success("run_2", scan_result_ref=shared)
        manifest_store.delete("run_1")
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["garbage"] == []
        assert shared in plan["live"]
        assert store.exists(shared)

    def test_6_delete_all_runs_artifact_collected(self, store, manifest_store):
        shared = store.put({"shared": "payload"}, "HTTP_RESPONSE").artifact_id
        manifest_store.commit_success("run_1", scan_result_ref=shared)
        manifest_store.commit_success("run_2", scan_result_ref=shared)
        manifest_store.delete("run_1")
        manifest_store.delete("run_2")
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["garbage"] == [shared]
        outcome = gc.run(dry_run=False)
        assert outcome["deleted_count"] == 1
        assert not store.exists(shared)

    def test_7_pinned_run_protects_artifacts(self, store, manifest_store):
        ref = store.put({"important": "evidence"}, "EXECUTION_OUTPUT").artifact_id
        manifest_store.commit_success("run_pinned", scan_result_ref=ref)
        manifest_store.pin("run_pinned", True)
        manifest_store.delete("run_pinned")  # refused by pin
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["garbage"] == []
        assert ref in plan["live"]
        assert plan["pinned_count"] == 1

    def test_8_9_grace_period_protects_young_orphans(self, store, manifest_store):
        orphan = store.put({"orphan": "young"}, "EXECUTION_OUTPUT").artifact_id
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=24.0)
        plan = gc.plan()
        assert plan["garbage"] == []
        assert orphan in plan["protected"]
        assert plan["protected_count"] == 1
        # After the grace period the same orphan becomes garbage.
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["garbage"] == [orphan]

    def test_dry_run_deletes_nothing(self, store, manifest_store):
        orphan = store.put({"orphan": "x"}, "EXECUTION_OUTPUT").artifact_id
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["dry_run"] is True
        assert plan["deleted_count"] == 0
        assert plan["reclaimable_bytes"] > 0
        assert store.exists(orphan)
        outcome = gc.run(dry_run=True)
        assert outcome["deleted_count"] == 0
        assert store.exists(orphan)

    def test_reclaimable_uses_stored_bytes(self, store, manifest_store):
        store.put({"reclaim": "y" * 100000}, "EXECUTION_OUTPUT")
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["garbage_count"] == 1
        assert plan["reclaimable_bytes"] <= plan["reclaimable_logical_bytes"]
        assert plan["reclaimable_bytes"] > 0

    def test_10_knowledge_db_untouched_by_gc(self, tmp_path, store):
        knowledge = tmp_path / "platform_outputs" / "proj" / "knowledge.db"
        knowledge.parent.mkdir(parents=True)
        knowledge.write_bytes(b"SQLITE-CONTENT")
        store.put({"a": 1}, "EXECUTION_OUTPUT")
        gc = ArtifactGarbageCollector(store, None, grace_hours=0.0)
        gc.run(dry_run=False)
        assert knowledge.read_bytes() == b"SQLITE-CONTENT"
        assert knowledge.is_file()
