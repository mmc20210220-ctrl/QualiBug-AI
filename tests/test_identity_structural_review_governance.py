from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_structural_review import (
    ACTION_CONFIRM_ALIAS,
    DECISION_KIND,
    apply_identity_structural_review_decisions,
    identity_structural_candidate_fingerprint,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_structural_review_governance import (
    ADMISSION_SCHEMA,
    govern_identity_structural_review_decision_admission,
    preserve_identity_structural_review_registry_merges,
)


def _candidate(candidate_id: str, left: str, right: str) -> dict:
    return {
        "candidate_id": candidate_id,
        "candidate_entity_ids": [left, right],
        "canonical_labels": {
            left: f"label:{left}",
            right: f"label:{right}",
        },
        "matched_dimensions": [
            "EXACT_OPERATION_SET",
            "EXACT_LIFECYCLE_TOPOLOGY",
        ],
        "matched_operation_names": ["create", "approve"],
        "matched_lifecycle_states": ["draft", "pending", "done"],
        "matched_lifecycle_transitions": [
            "draft>pending|ALLOWED",
            "pending>done|ALLOWED",
        ],
        "matched_relation_context": [],
        "source_refs": {left: [f"source:{left}"], right: [f"source:{right}"]},
        "evidence": [
            {
                "source_id": "source",
                "source_locator": candidate_id,
                "quote": candidate_id,
            }
        ],
        "status": "CANDIDATE_ONLY",
    }


def _decision(candidate: dict, canonical: str, suffix: str) -> dict:
    return {
        "schema": "qualibug.operator-authority-decision.v1",
        "decision_kind": DECISION_KIND,
        "decision_id": f"decision:{suffix}",
        "candidate_id": candidate["candidate_id"],
        "conflict_id": candidate["candidate_id"],
        "candidate_fingerprint": identity_structural_candidate_fingerprint(candidate),
        "participant_entity_ids": candidate["candidate_entity_ids"],
        "action": ACTION_CONFIRM_ALIAS,
        "canonical_entity_id": canonical,
    }


def _model(*candidates: dict) -> dict:
    return {
        "identity_structural_candidates": list(candidates),
        "business_objects": [],
        "gate": {"status": "PASS", "entry_allowed": True},
        "metrics": {},
    }


def _resolution() -> dict:
    return {
        "clusters": [],
        "registry": {},
        "gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
    }


def test_overlapping_confirmations_fail_closed_before_alias_projection() -> None:
    candidate_ab = _candidate("candidate:ab", "entity:a", "entity:b")
    candidate_bc = _candidate("candidate:bc", "entity:b", "entity:c")
    asset = {
        "business_fact_ledger": {"items": []},
        "identity_structural_review_decisions": [
            _decision(candidate_ab, "entity:a", "ab"),
            _decision(candidate_bc, "entity:b", "bc"),
        ],
        "enterprise_identity_registry": {
            "entities": [
                {"entity_id": "entity:a"},
                {"entity_id": "entity:b"},
                {"entity_id": "entity:c"},
            ]
        },
    }
    model = _model(candidate_ab, candidate_bc)

    governed = govern_identity_structural_review_decision_admission(asset, model)

    admission = governed["identity_structural_review_admission"]
    assert admission["schema"] == ADMISSION_SCHEMA
    assert admission["status"] == "BLOCKED_OVERLAPPING_CONFIRMATIONS"
    assert admission["overlapping_entity_ids"] == ["entity:b"]
    assert admission["blocked_decision_ids"] == ["decision:ab", "decision:bc"]
    assert admission["current_identity_gate_changed"] is False
    assert admission["automatic_conflict_winner_allowed"] is False
    assert asset["identity_structural_review_decisions"] == []

    projected = apply_identity_structural_review_decisions(
        asset, governed, _resolution()
    )
    receipt = projected["identity_structural_review_receipt"]
    assert receipt["applied_confirmation_count"] == 0
    assert receipt["rebuild_required"] is False
    assert asset["business_fact_ledger"]["items"] == []
    assert len(asset["enterprise_identity_registry"]["entities"]) == 3


def test_disjoint_confirmations_preserve_every_registry_merge_receipt() -> None:
    candidate_ab = _candidate("candidate:ab", "entity:a", "entity:b")
    candidate_cd = _candidate("candidate:cd", "entity:c", "entity:d")
    asset = {
        "business_fact_ledger": {"items": []},
        "identity_structural_review_decisions": [
            _decision(candidate_ab, "entity:a", "ab"),
            _decision(candidate_cd, "entity:c", "cd"),
        ],
        "enterprise_identity_registry": {
            "entities": [
                {"entity_id": "entity:a"},
                {"entity_id": "entity:b"},
                {"entity_id": "entity:c"},
                {"entity_id": "entity:d"},
            ]
        },
    }
    model = _model(candidate_ab, candidate_cd)

    governed = govern_identity_structural_review_decision_admission(asset, model)
    assert governed["identity_structural_review_admission"]["status"] == "PASS"
    projected = apply_identity_structural_review_decisions(
        asset, governed, _resolution()
    )
    receipt = projected["identity_structural_review_receipt"]
    assert receipt["applied_confirmation_count"] == 2
    assert receipt["rebuild_required"] is True
    assert len(asset["business_fact_ledger"]["items"]) == 2

    preserved = preserve_identity_structural_review_registry_merges(asset, receipt)

    assert preserved["operator_authorized_merge_count"] == 2
    merges = preserved["operator_authorized_merges"]
    assert [row["decision_id"] for row in merges] == ["decision:ab", "decision:cd"]
    assert [row["canonical_entity_id"] for row in merges] == [
        "entity:a",
        "entity:c",
    ]
    registry = asset["enterprise_identity_registry"]
    assert registry["operator_authorized_merges"] == merges
    assert registry["operator_authorized_merge"] == merges[-1]
    assert {row["entity_id"] for row in registry["entities"]} == {
        "entity:a",
        "entity:c",
    }
    assert all(row["automatic_merge"] is False for row in merges)
