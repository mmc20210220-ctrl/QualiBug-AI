"""Unit tests for intelligence report deduplication (SPEC P0-4, Phase 6, §25/§43).

Covers: heavy payloads replaced by summary + artifact refs, finding raw
evidence stripped to evidence_refs, all embedded refs declared in the
top-level artifact_refs, report size drop, legacy (store-disabled) mode, and
ref verification.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.artifact_store import LocalArtifactStore
from ai_test_asset_center.intelligence_report_artifactization import (
    compact_intelligence_report,
    report_embed_max_bytes,
    verify_report_refs,
    write_intelligence_report,
)

LEGACY_BUNDLE_SCHEMA = "qualibug-evidence-bundle-v2"


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    return tmp_path


@pytest.fixture()
def store(workspace: Path) -> LocalArtifactStore:
    return LocalArtifactStore(workspace / ".qualibug", compression="zstd")


def _big_finding(finding_id: str) -> dict:
    return {
        "finding_id": finding_id,
        "canonical_defect_id": finding_id,
        "title": f"title-{finding_id}",
        "status": "confirmed",
        "severity": "P1",
        "raw_evidence": {
            "request_raw": {"method": "POST", "body": "x" * 50000},
            "response_raw": {"body": "y" * 50000},
        },
        "reproduction_steps": ["step 1", "step 2"],
    }


def _big_delivery_occurrences() -> list[dict]:
    """Plain heavy payload (no sealed-schema validator) used as the fixture
    for the generic artifactize-above-threshold mechanism."""
    return [
        {"finding_id": f"F-{i}", "body": "z" * 20000}
        for i in range(40)
    ]


def _bundle_with_execution_output(store: LocalArtifactStore) -> str:
    execution_ref = store.put({"findings": [_big_finding("D-1")]}, "EXECUTION_OUTPUT")
    manifest = {
        "schema_version": "qualibug.evidence-bundle-manifest.v1",
        "bundle_id": "evb_test",
        "parts": {
            "execution_output_ref": execution_ref.artifact_id,
            "metadata_ref": store.put({"campaign": {"campaign_id": "c1"}}, "METADATA").artifact_id,
        },
    }
    return store.put(manifest, "EVIDENCE_BUNDLE_MANIFEST").artifact_id


class TestReportCompaction:
    def test_heavy_payload_becomes_summary_plus_ref(self, store):
        payload = {
            "project": "proj",
            "generated_at_utc": "2026-08-09T00:00:00Z",
            "real_findings": [_big_finding("D-1")],
            "findings": [_big_finding("D-1")],
            "delivery_occurrences": _big_delivery_occurrences(),
            "campaign": {"campaign_id": "c1"},
        }
        compact, refs, stats = compact_intelligence_report(payload, store)
        assert "delivery_occurrences" not in compact
        assert compact["delivery_occurrences_ref"].startswith("sha256:")
        assert compact["delivery_occurrences_summary"]["artifactized"] is True
        assert compact["delivery_occurrences_summary"]["ref"].startswith("sha256:")
        assert stats["artifactized_keys"] == ["delivery_occurrences"]
        assert len(refs) == 1
        # Summary keys remain intact for the frontend read surface.
        assert compact["campaign"] == {"campaign_id": "c1"}
        assert compact["project"] == "proj"

    def test_findings_strip_raw_evidence_and_carry_refs(self, store):
        bundle_ref = _bundle_with_execution_output(store)
        payload = {"real_findings": [_big_finding("D-1")], "findings": [_big_finding("D-1")]}
        compact, refs, _stats = compact_intelligence_report(
            payload, store, bundle_manifest_ref=bundle_ref
        )
        for key in ("real_findings", "findings"):
            finding = compact[key][0]
            assert "raw_evidence" not in finding
            assert finding["title"] == "title-D-1"
            assert finding["status"] == "confirmed"
            assert finding["evidence_refs"] == [
                store.get_json(bundle_ref)["parts"]["execution_output_ref"]
            ]
        assert any(
            ref == store.get_json(bundle_ref)["parts"]["execution_output_ref"]
            for ref in compact["artifact_refs"]
        )

    def test_report_size_drops_clearly(self, store):
        payload = {
            "project": "proj",
            "real_findings": [_big_finding(f"D-{i}") for i in range(20)],
            "findings": [_big_finding(f"D-{i}") for i in range(20)],
            "delivery_occurrences": _big_delivery_occurrences(),
        }
        original_bytes = len(json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        compact, _refs, stats = compact_intelligence_report(payload, store)
        compact_bytes = len(json.dumps(compact, ensure_ascii=False).encode("utf-8"))
        assert compact_bytes < original_bytes
        # The removed embedded payload is the dominant part of the report.
        assert stats["removed_embedded_bytes"] > original_bytes * 0.6

    def test_small_payloads_stay_embedded(self, store):
        payload = {"campaign": {"campaign_id": "c1"}, "coverage_gaps": []}
        compact, refs, stats = compact_intelligence_report(payload, store)
        assert compact["campaign"] == {"campaign_id": "c1"}
        assert refs == []
        assert stats["artifactized_keys"] == []

    def test_write_report_store_mode_and_legacy_mode(self, workspace, store, monkeypatch):
        report_path = workspace / "platform_outputs" / "proj" / "intelligence_report.json"
        payload = {"project": "proj", "real_findings": [_big_finding("D-1")],
                   "delivery_occurrences": _big_delivery_occurrences()}
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "true")
        receipt = write_intelligence_report(report_path, payload, root=workspace)
        assert receipt["mode"] == "artifact_store"
        written = json.loads(report_path.read_text(encoding="utf-8"))
        assert "delivery_occurrences" not in written
        assert written["delivery_occurrences_ref"].startswith("sha256:")
        assert written["real_findings"][0]["title"] == "title-D-1"

        legacy_path = workspace / "platform_outputs" / "proj" / "legacy_report.json"
        monkeypatch.setenv("QUALIBUG_ARTIFACT_STORE_ENABLED", "false")
        receipt = write_intelligence_report(legacy_path, payload, root=workspace)
        assert receipt["mode"] == "legacy"
        legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        assert "delivery_occurrences" in legacy  # unchanged legacy shape

    def test_verify_report_refs(self, store):
        bundle_ref = _bundle_with_execution_output(store)
        payload = {"real_findings": [_big_finding("D-1")]}
        compact, _refs, _stats = compact_intelligence_report(
            payload, store, bundle_manifest_ref=bundle_ref
        )
        report_ref = store.put(compact, "INTELLIGENCE_REPORT").artifact_id
        verification = verify_report_refs(store, report_ref)
        assert verification["valid"] is True
        assert verification["checked"] >= 1
        # Deleting a referenced artifact makes the report invalid.
        execution_ref = store.get_json(bundle_ref)["parts"]["execution_output_ref"]
        store.delete(execution_ref)
        verification = verify_report_refs(store, report_ref)
        assert verification["valid"] is False
        assert execution_ref in verification["missing_refs"]

    def test_embed_threshold_env(self, monkeypatch):
        monkeypatch.setenv("QUALIBUG_REPORT_EMBED_MAX_BYTES", "12345")
        assert report_embed_max_bytes() == 12345
        monkeypatch.delenv("QUALIBUG_REPORT_EMBED_MAX_BYTES")
        assert report_embed_max_bytes() == 256 * 1024


class TestReportRedactionContract:
    """SPEC §46: the report payload must be redacted before it enters the
    write path; compact_intelligence_report fails closed on unredacted
    material instead of persisting it.

    Regression (run25): the scan report assembled the raw obligation-attempt
    ledger — whose before_ref/after_ref observation refs carry
    password-assignment evidence (``control_before:/api/auth/debug/token:
    <plaintext>``) — into the payload, so the first store-mode run died with
    ``report_payload_unredacted:obligation_attempt_ledger`` after a full
    45-minute planning run. The fix redacts the assembled payload before the
    write path (reseal keeps the ledger fingerprint self-consistent).
    """

    @staticmethod
    def _ledger_with_password_assignment() -> dict:
        return {
            "schema_version": "qualibug.obligation-attempt-ledger.v1",
            "run_id": "RUN_x",
            "campaign_id": "CMP_x",
            "identity": {
                "run_id": "RUN_x",
                "campaign_id": "CMP_x",
                "target_id": "target",
                "environment_id": "test",
                "policy_version": "v1",
                "evaluation_mode": "operational",
                "source_snapshot_hash": "hash",
                "mainline_contract_fingerprint": "fp",
                "missing_fields": [],
                "status": "COMPLETE",
            },
            "selected_count": 1,
            "terminal_count": 1,
            "accounted_count": 1,
            "complete": True,
            "terminal_status_counts": {"DELIVERABLE": 1},
            "selection_status_counts": {"SELECTED": 1},
            "attempts": [
                {
                    "candidate_id": "C-x",
                    "obligation_id": "obl-x",
                    "selection_status": "SELECTED",
                    "terminal_status": "DELIVERABLE",
                    "terminal_stage": "gate",
                    "finding_id": "F-x",
                    "stages": [
                        {"stage": "compile", "status": "completed"},
                        {"stage": "execution", "status": "completed"},
                        {"stage": "gate", "status": "completed"},
                    ],
                    "delivery_evidence_bundle": {
                        "finding": {
                            "finding_id": "F-x",
                            "raw_evidence": {
                                "steps": [
                                    {
                                        "governance_receipt": {
                                            "before_ref": (
                                                "control_before:/api/auth/debug/token:"
                                                "eyJhbGciOiJIUzI1NiJ9.example.plaintext"
                                            ),
                                            "after_ref": (
                                                "control_after:/api/auth/debug/token:"
                                                "eyJhbGciOiJIUzI1NiJ9.example.plaintext"
                                            ),
                                        }
                                    }
                                ]
                            }
                        }
                    },
                }
            ],
        }

    @staticmethod
    def _payload_with_ledger() -> dict:
        return {
            "project": "proj",
            "obligation_attempt_ledger": (
                TestReportRedactionContract._ledger_with_password_assignment()
            ),
            "canonical_defect_registry": {},
            "formal_delivery_authority": {},
            "delivery_occurrences": [],
            "real_findings": [],
            "findings": [],
        }

    def test_unredacted_ledger_fails_closed(self, store, monkeypatch):
        """The guard must keep rejecting unredacted material: the scan-side
        fix is upstream redaction, never a relaxation of this fail-closed
        check. A small embed threshold forces the artifactize branch for the
        minimal fixture (the real ledger is tens of MB)."""
        monkeypatch.setenv("QUALIBUG_REPORT_EMBED_MAX_BYTES", "1024")
        payload = self._payload_with_ledger()
        with pytest.raises(
            ValueError, match="report_payload_unredacted:obligation_attempt_ledger"
        ):
            compact_intelligence_report(payload, store)

    def test_redacted_payload_compacts_and_stores_clean_ledger(
        self, store, monkeypatch
    ):
        """The upstream fix: redact_and_validate the assembled payload before
        the write path. The compacted report stores the resealed ledger
        artifact with the plaintext password-assignment evidence gone and the
        ledger schema preserved."""
        from ai_test_asset_center.artifact_redactor import redact_and_validate

        monkeypatch.setenv("QUALIBUG_REPORT_EMBED_MAX_BYTES", "1024")
        payload = self._payload_with_ledger()
        redacted, _receipt = redact_and_validate(payload)
        compact, refs, stats = compact_intelligence_report(redacted, store)
        ledger_ref = compact.get("obligation_attempt_ledger_ref")
        assert ledger_ref
        stored = store.get_json(ledger_ref)
        assert stored.get("schema_version") == "qualibug.obligation-attempt-ledger.v1"
        text = json.dumps(stored, ensure_ascii=False)
        assert "eyJhbGciOiJIUzI1NiJ9.example.plaintext" not in text
        # Heavy payload artifactized, not embedded.
        assert "obligation_attempt_ledger" not in compact
        assert "obligation_attempt_ledger_ref" in compact
