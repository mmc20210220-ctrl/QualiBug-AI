from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    identity_structural_review_query as query,
)


def test_completed_review_queue_survives_candidate_merge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center import composition

    persisted_queue = {
        "schema": "qualibug.enterprise-identity-structural-review-queue.v1",
        "queue_id": "queue:completed",
        "task_count": 1,
        "pending_count": 0,
        "confirmed_count": 1,
        "rejected_count": 0,
        "stale_decision_count": 0,
        "tasks": [
            {
                "candidate_id": "candidate:orders",
                "review_status": "CONFIRMED",
                "decision_id": "decision:orders",
            }
        ],
    }
    asset = {
        "enterprise_understanding_model": {
            "identity_structural_candidates": [],
            "identity_structural_review_queue": deepcopy(persisted_queue),
        },
        "enterprise_identity_structural_review_queue": deepcopy(persisted_queue),
    }
    monkeypatch.setattr(
        composition,
        "load_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: asset,
    )
    monkeypatch.setattr(
        query,
        "load_authority_decision_ledger",
        lambda *_args, **_kwargs: {
            "decisions": [
                {
                    "schema": "qualibug.operator-authority-decision.v1",
                    "decision_kind": "IDENTITY_STRUCTURAL_CANDIDATE",
                    "decision_id": "decision:orders",
                    "candidate_id": "candidate:orders",
                }
            ]
        },
    )

    returned = query.get_identity_structural_review_queue("demo")

    assert returned == persisted_queue
    assert returned["tasks"][0]["review_status"] == "CONFIRMED"


def test_live_candidates_are_reprojected_against_latest_decisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center import composition

    candidate = {
        "candidate_id": "candidate:live",
        "candidate_entity_ids": ["entity:a", "entity:b"],
        "canonical_labels": {"entity:a": "A", "entity:b": "B"},
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
        "source_refs": {},
        "evidence": [],
    }
    asset = {
        "enterprise_understanding_model": {
            "identity_structural_candidates": [candidate],
            "metrics": {},
        }
    }
    monkeypatch.setattr(
        composition,
        "load_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: asset,
    )
    monkeypatch.setattr(
        query,
        "load_authority_decision_ledger",
        lambda *_args, **_kwargs: {"decisions": []},
    )

    returned = query.get_identity_structural_review_queue("demo")

    assert returned["task_count"] == 1
    assert returned["pending_count"] == 1
    assert returned["tasks"][0]["candidate_id"] == "candidate:live"
    assert returned["tasks"][0]["review_status"] == "PENDING_REVIEW"
