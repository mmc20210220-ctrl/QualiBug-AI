"""Unit tests for trace ledger artifactization (SPEC P0-4, Phase 4, §26).

Covers: metadata + event payload refs split, cross-run event dedup, exact
round-trip hydration (fingerprint contract preserved), the Single-Write dual
persist (artifact store vs legacy file) and the Dual-Read round loader
(store-first, legacy fallback).
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_store import (
    TRACE_EVENT,
    TRACE_LEDGER,
    LocalArtifactStore,
)
from ai_test_asset_center.run_manifest import RunManifestStore
from ai_test_asset_center.trace_artifactization import (
    TraceArtifactizationError,
    artifactize_trace_ledger,
    hydrate_trace_ledger,
    load_round_trace_ledgers,
    persist_trace_ledger_output,
)


def _fingerprint(payload: dict) -> str:
    canonical = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _minimal_ledger(run_id: str, obligation_ids: list[str]) -> dict:
    attempts = [{"obligation_id": oid} for oid in obligation_ids]
    payload = {
        "schema_version": "qualibug.discovery-trace-ledger.v3",
        "created_at_utc": f"2026-08-09T00:00:0{len(obligation_ids) % 10}Z",
        "run_id": run_id,
        "policy_id": "policy-1",
        "target_id": "target-1",
        "project_id": "proj",
        "industry": "retail",
        "evaluation_mode": "operational",
        "campaign_id": "camp-1",
        "attempt_ledger_fingerprint": "f" * 64,
        "attempt_count": len(attempts),
        "trace_count": len(attempts),
        "delivery_occurrence_finding_ids": [],
        "canonical_defect_ids": [],
        "outcome_counts": {},
        "terminal_status_counts": {},
        "failure_signature_counts": {},
        "stage_loss": {},
        "pipeline_health": {"status": "OK"},
        "aggregate_stage_events": {},
        "redaction_contract": {
            "raw_request_bodies_persisted": False,
            "raw_response_bodies_persisted": False,
            "credentials_persisted": False,
            "ground_truth_persisted": False,
            "target_private_paths_persisted": False,
        },
        "attempts": attempts,
    }
    payload["ledger_fingerprint"] = _fingerprint(payload)
    return payload


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(workspace: Path) -> LocalArtifactStore:
    return LocalArtifactStore(workspace / ".qualibug", compression="zstd")


class TestTraceArtifactization:
    def test_split_into_metadata_and_event_refs(self, store):
        ledger = _minimal_ledger("run_1", ["OB-1", "OB-2", "OB-3"])
        outcome = artifactize_trace_ledger(store, ledger)
        assert outcome["attempt_count"] == 3
        assert len(outcome["attempt_refs"]) == 3
        meta = store.get_json(outcome["metadata_ref"])
        assert meta["schema_version"] == "qualibug.discovery-trace-ledger.v3"
        assert meta["attempt_refs"] == outcome["attempt_refs"]
        assert "attempts" not in meta
        assert store.metadata(outcome["metadata_ref"]).artifact_type == TRACE_LEDGER
        for ref in outcome["attempt_refs"]:
            assert store.metadata(ref).artifact_type == TRACE_EVENT

    def test_identical_events_dedup_across_runs(self, store):
        ledger_1 = _minimal_ledger("run_1", ["OB-1", "OB-2"])
        ledger_2 = _minimal_ledger("run_2", ["OB-1", "OB-2"])  # same rows
        out_1 = artifactize_trace_ledger(store, ledger_1)
        out_2 = artifactize_trace_ledger(store, ledger_2)
        assert out_1["attempt_refs"] == out_2["attempt_refs"]
        # 2 shared events + 2 metadata artifacts = 4 physical objects
        assert len(store.list_all()) == 4

    def test_hydrate_round_trip_preserves_fingerprint(self, store):
        ledger = _minimal_ledger("run_9", ["OB-A", "OB-B", "OB-C"])
        outcome = artifactize_trace_ledger(store, ledger)
        hydrated = hydrate_trace_ledger(store, outcome["metadata_ref"])
        assert hydrated == ledger
        assert hydrated["ledger_fingerprint"] == ledger["ledger_fingerprint"]

    def test_hydrate_fails_loudly_on_missing_event(self, store):
        ledger = _minimal_ledger("run_9", ["OB-A", "OB-B"])
        outcome = artifactize_trace_ledger(store, ledger)
        store.delete(outcome["attempt_refs"][0])
        with pytest.raises(TraceArtifactizationError):
            hydrate_trace_ledger(store, outcome["metadata_ref"])

    def test_unredacted_ledger_rejected(self, store):
        ledger = _minimal_ledger("run_x", ["OB-1"])
        ledger["redaction_contract"]["credentials_persisted"] = True
        with pytest.raises(TraceArtifactizationError):
            artifactize_trace_ledger(store, ledger)

    def test_persist_dual_mode_store_enabled(self, workspace, store, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "true")
        ledger = _minimal_ledger("run_1", ["OB-1"])
        evolution = workspace / "platform_outputs" / "proj" / "discovery_evolution"
        outcome = persist_trace_ledger_output(ledger, evolution, root=workspace)
        assert outcome["mode"] == "artifact_store"
        assert outcome["ref"].startswith("sha256:")
        assert not list((evolution / "trace_ledgers").glob("*/*.trace-ledger.json"))

    def test_persist_dual_mode_legacy_fallback(self, workspace, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "false")
        ledger = _minimal_ledger("run_1", ["OB-1"])
        evolution = workspace / "platform_outputs" / "proj" / "discovery_evolution"
        outcome = persist_trace_ledger_output(ledger, evolution, root=workspace)
        assert outcome["mode"] == "legacy"
        assert outcome["ref"].endswith(".trace-ledger.json")
        files = list((evolution / "trace_ledgers").glob("*/*.trace-ledger.json"))
        assert len(files) == 1


class TestTraceDualRead:
    def test_load_round_ledgers_store_first(self, workspace, store, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "true")
        manifest_store = RunManifestStore(store, workspace)
        for index in range(3):
            ledger = _minimal_ledger(f"run_{index}", [f"OB-{index}"])
            outcome = artifactize_trace_ledger(store, ledger)
            manifest_store.commit_success(
                f"run_{index}",
                trace_refs=[outcome["metadata_ref"]],
                scan_result_ref=store.put({"s": index}, "SCAN_RESULT").artifact_id,
            )
        ledgers = load_round_trace_ledgers("proj", workspace, store=store)
        assert [ledger["run_id"] for ledger in ledgers] == ["run_0", "run_1", "run_2"]
        assert all(ledger["attempt_count"] == 1 for ledger in ledgers)

    def test_load_round_ledgers_legacy_fallback(self, workspace, monkeypatch):
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "false")
        ledger = _minimal_ledger("run_legacy", ["OB-1"])
        evolution = workspace / "platform_outputs" / "proj" / "discovery_evolution"
        persist_trace_ledger_output(ledger, evolution, root=workspace)
        ledgers = load_round_trace_ledgers("proj", workspace)
        assert [ledger["run_id"] for ledger in ledgers] == ["run_legacy"]
