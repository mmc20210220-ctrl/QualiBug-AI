"""Regression: hydrated envelopes reseal the obligation-attempt ledger before
authority re-derivation.

run25b failure: the sharded scan-result writer reloads the ledger shard and
``hydrate_refs`` expands ``$qualibug_artifact_ref``/``$qualibug_blob_ref``
markers into their real subtrees AFTER the ledger was sealed. The content
changes while ``ledger_fingerprint`` still reflects the marker-bearing bytes,
so ``_rederive_redaction_sensitive_authority`` rejected the perfectly redacted
envelope with ``obligation_attempt_ledger_fingerprint_mismatch`` at the very
last step of the scan (write_scan_result).

The fix reseals the ledger from its current content before rebuilding the
fingerprint-bound authority artifacts; reseal is a pure re-derivation
(idempotent on a consistent ledger), and a genuinely corrupt ledger still
fails closed.
"""
from __future__ import annotations

import copy

import pytest

from ai_test_asset_center._delivery_validation_cache import (
    clear_delivery_validation_caches,
)
from ai_test_asset_center.artifact_redactor import (
    _rederive_redaction_sensitive_authority,
)
from ai_test_asset_center.obligation_attempt_ledger import (
    reseal_obligation_attempt_ledger,
    validate_obligation_attempt_ledger,
)


@pytest.fixture(autouse=True)
def _isolated_caches() -> None:
    """The ledger validation cache is module-global; clear it between tests so
    a cached validated ledger from one test cannot leak into another."""
    clear_delivery_validation_caches()


def _sealed_ledger() -> dict:
    attempt = {
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
                                    "eyJhbGciOiJIUzI1NiJ9.plaintext"
                                ),
                            }
                        }
                    ]
                },
            }
        },
    }
    ledger = {
        "schema_version": "qualibug.obligation-attempt-ledger.v1",
        "run_id": "RUN_x",
        "campaign_id": "CMP_x",
        "identity": {
            "run_id": "RUN_x",
            "campaign_id": "CMP_x",
            "target_id": "t",
            "environment_id": "test",
            "policy_version": "v1",
            "evaluation_mode": "operational",
            "source_snapshot_hash": "h",
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
        "attempts": [attempt],
    }
    return reseal_obligation_attempt_ledger(ledger)


def _authority_scope(ledger: dict) -> dict:
    return {
        "obligation_attempt_ledger": ledger,
        "canonical_defect_registry": {
            "schema_version": "qualibug.canonical-defect-registry.v3",
            "entries": {},
        },
        "mainline_run": {
            "schema_version": "qualibug.discovery-mainline-run.v1",
            "mainline_authority": "legacy_champion",
            "run_id": "RUN_x",
            "campaign_id": "CMP_x",
            "target_id": "t",
            "environment_id": "test",
            "policy_version": "v1",
            "evaluation_mode": "operational",
            "customer_outputs_published": True,
            "product_evaluation_submission_published": True,
            "private_evaluator_observation_allowed": False,
            "contract_fingerprint": "x",
        },
        "delivery_occurrences": [],
        "formal_delivery_authority": {},
    }


def _patch_builders(monkeypatch: pytest.MonkeyPatch) -> dict:
    captured: dict = {}

    def fake_authority(**kwargs: object) -> dict:
        captured["authority_ledger"] = kwargs.get("obligation_attempt_ledger")
        return {
            "schema_version": "qualibug.formal-delivery-authority.v1",
            "authority": "fake",
        }

    def fake_registry(**kwargs: object) -> dict:
        captured["registry_ledger"] = kwargs.get("obligation_attempt_ledger")
        return {
            "schema_version": "qualibug.canonical-defect-registry.v3",
            "entries": {},
        }

    monkeypatch.setattr(
        "ai_test_asset_center.formal_delivery_authority.build_formal_delivery_authority_receipt",
        fake_authority,
    )
    monkeypatch.setattr(
        "ai_test_asset_center._canonical_defect_registry_mechanics.build_canonical_defect_registry",
        fake_registry,
    )
    return captured


def test_hydrated_ledger_content_change_is_resealed_before_rebuild(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A content change after sealing (hydrate_refs expanding a marker into
    its real subtree) must not reject the envelope: the fingerprint is
    re-derived from the current content, and the authority rebuild receives
    the resealed (validated) ledger."""
    captured = _patch_builders(monkeypatch)
    ledger = _sealed_ledger()
    # Simulate the hydrate_refs expansion: the sealed bytes changed, the
    # fingerprint did not.
    ledger["attempts"][0]["delivery_evidence_bundle"]["finding"]["raw_evidence"][
        "steps"
    ][0]["governance_receipt"]["before_ref"] = (
        "control_before:/api/auth/debug/token:EXPANDED-FROM-MARKER"
    )
    with pytest.raises(Exception):
        # Sanity: the mutated ledger genuinely breaks the seal.
        validate_obligation_attempt_ledger(ledger)
    out = _rederive_redaction_sensitive_authority(_authority_scope(ledger))
    # The rebuilt authority used the resealed ledger (validated + fingerprint
    # consistent with the expanded content).
    assert captured["authority_ledger"] is not None
    assert captured["registry_ledger"] is not None
    validate_obligation_attempt_ledger(captured["registry_ledger"])
    # The expanded content survived the reseal (no data loss).
    steps = captured["registry_ledger"]["attempts"][0][
        "delivery_evidence_bundle"
    ]["finding"]["raw_evidence"]["steps"]
    assert steps[0]["governance_receipt"]["before_ref"].endswith(
        "EXPANDED-FROM-MARKER"
    )
    assert out["canonical_defect_registry"]


def test_consistent_ledger_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reseal is idempotent: an already-consistent ledger passes through
    unchanged (no content rewrite)."""
    captured = _patch_builders(monkeypatch)
    ledger = _sealed_ledger()
    snapshot = copy.deepcopy(ledger)
    out = _rederive_redaction_sensitive_authority(_authority_scope(ledger))
    assert captured["registry_ledger"] == snapshot


def test_genuinely_corrupt_ledger_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A corrupt ledger (bad schema) fails reseal and must keep failing
    closed — the fix never papers over real corruption."""
    _patch_builders(monkeypatch)
    ledger = _sealed_ledger()
    ledger["schema_version"] = "not-a-ledger-schema"
    with pytest.raises(Exception):
        _rederive_redaction_sensitive_authority(_authority_scope(ledger))
