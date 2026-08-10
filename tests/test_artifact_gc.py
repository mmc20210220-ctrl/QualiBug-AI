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


class TestReferenceContainerExpansion:
    """Transitive mark through reference containers (SPEC §12/§14/§26)."""

    def test_bundle_parts_live_while_any_run_references_the_bundle(self, store, manifest_store):
        part = store.put({"evidence": "payload"}, "HTTP_RESPONSE").artifact_id
        bundle = store.put(
            {"schema_version": "qualibug.evidence-bundle-manifest.v1",
             "parts": {"response_ref": part}},
            "EVIDENCE_BUNDLE_MANIFEST",
        ).artifact_id
        manifest_store.commit_success("run_1", evidence_bundle_refs=[bundle])
        manifest_store.commit_success("run_2", evidence_bundle_refs=[bundle])
        manifest_store.delete("run_1")
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert part in plan["live"]
        assert plan["garbage"] == []
        outcome = gc.run(dry_run=False)
        assert outcome["deleted_count"] == 0
        assert store.exists(part)
        # Deleting the last referencing run makes the part garbage too.
        manifest_store.delete("run_2")
        plan = gc.plan()
        assert part in plan["garbage"]
        assert bundle in plan["garbage"]

    def test_trace_attempt_refs_live_via_metadata(self, store, manifest_store):
        event = store.put({"obligation_id": "OB-1"}, "TRACE_EVENT").artifact_id
        metadata = store.put(
            {"schema_version": "qualibug.discovery-trace-ledger.v3",
             "attempt_refs": [event]},
            "TRACE_LEDGER",
        ).artifact_id
        manifest_store.commit_success("run_1", trace_refs=[metadata])
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert event in plan["live"]
        assert plan["garbage"] == []
        manifest_store.delete("run_1")
        plan = gc.plan()
        assert event in plan["garbage"]

    def test_report_artifact_refs_live_via_declared_list(self, store, manifest_store):
        heavy = store.put({"ledger": "x" * 1000}, "OBLIGATION_ATTEMPT_LEDGER").artifact_id
        report = store.put(
            {"schema_version": "qualibug.intelligence-report.v1",
             "artifact_refs": [heavy]},
            "INTELLIGENCE_REPORT",
        ).artifact_id
        manifest_store.commit_success("run_1", intelligence_report_ref=report)
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert heavy in plan["live"]
        assert plan["garbage"] == []

    def test_unparseable_live_container_aborts_gc(self, store, manifest_store):
        # Corrupt the bundle-manifest payload after commit: the GC must fail
        # closed (delete nothing) rather than sweep its unknown refs.
        bundle = store.put(
            {"schema_version": "qualibug.evidence-bundle-manifest.v1",
             "parts": {"response_ref": "sha256:" + "c" * 64}},
            "EVIDENCE_BUNDLE_MANIFEST",
        ).artifact_id
        manifest_store.commit_success("run_1", evidence_bundle_refs=[bundle])
        from ai_test_asset_center.artifact_store import parse_artifact_id, artifact_id_from_hash

        digest = parse_artifact_id(bundle)
        payload_path = store.root / "artifacts" / "sha256" / digest[:2] / f"{digest}.zst"
        payload_path.write_bytes(b"NOT-JSON-CORRUPTED")
        orphan = store.put({"o": 1}, "EXECUTION_OUTPUT").artifact_id
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        plan = gc.plan()
        assert plan["status"] == "ABORTED"
        assert plan["deleted_count"] == 0
        outcome = gc.run(dry_run=False)
        assert outcome["deleted_count"] == 0
        assert store.exists(orphan)
        assert store.exists(bundle)

    def test_gc_env_gate_defaults_to_dry_run(self, store, manifest_store, monkeypatch):
        orphan = store.put({"orphan": "y"}, "EXECUTION_OUTPUT").artifact_id
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        monkeypatch.delenv("QUALIBUG_ARTIFACT_GC_ENABLE", raising=False)
        outcome = gc.run()
        assert outcome["dry_run"] is True
        assert outcome["deleted_count"] == 0
        assert store.exists(orphan)
        monkeypatch.setenv("QUALIBUG_ARTIFACT_GC_ENABLE", "true")
        outcome = gc.run()
        assert outcome["dry_run"] is False
        assert outcome["deleted_count"] == 1
        assert not store.exists(orphan)

    def test_explicit_dry_run_flag_overrides_env(self, store, manifest_store, monkeypatch):
        orphan = store.put({"orphan": "y"}, "EXECUTION_OUTPUT").artifact_id
        gc = ArtifactGarbageCollector(store, manifest_store, grace_hours=0.0)
        monkeypatch.setenv("QUALIBUG_ARTIFACT_GC_ENABLE", "true")
        outcome = gc.run(dry_run=True)
        assert outcome["deleted_count"] == 0
        assert store.exists(orphan)
