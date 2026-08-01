from __future__ import annotations

from copy import deepcopy

import pytest

from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
    identity_structural_review as review,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_registry_governance import (
    govern_identity_registry,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.identity_structural_review import (
    ACTION_CONFIRM_ALIAS,
    ACTION_REJECT_CANDIDATE,
    DECISION_KIND,
    REVIEW_QUEUE_SCHEMA,
    REVIEW_RECEIPT_SCHEMA,
    apply_identity_structural_review_decisions,
    finalize_identity_structural_review_measurement,
    identity_structural_candidate_fingerprint,
    project_identity_structural_review_queue,
    scrub_operator_structural_review_mentions,
)


def _evidence(ref: str) -> list[dict]:
    return [
        {
            "source_id": "source",
            "source_locator": ref,
            "quote": ref,
            "quote_hash": f"hash:{ref}",
        }
    ]


def _candidate() -> dict:
    return {
        "candidate_id": "candidate:orders",
        "candidate_entity_ids": ["entity:order", "entity:sales-order"],
        "canonical_labels": {
            "entity:order": "订单",
            "entity:sales-order": "销售单",
        },
        "strength": "STRONG_STRUCTURAL_CANDIDATE",
        "matched_dimensions": [
            "EXACT_OPERATION_SET",
            "EXACT_LIFECYCLE_TOPOLOGY",
            "SHARED_RELATION_NEIGHBORHOOD",
        ],
        "matched_operation_names": ["创建", "审批"],
        "matched_lifecycle_states": ["草稿", "待审批", "完成"],
        "matched_lifecycle_transitions": [
            "草稿>待审批|ALLOWED",
            "待审批>完成|ALLOWED",
        ],
        "matched_relation_context": ["OUT|REFERENCES|entity:customer"],
        "source_refs": {
            "entity:order": ["operation:a", "lifecycle:a", "relation:a"],
            "entity:sales-order": [
                "operation:b",
                "lifecycle:b",
                "relation:b",
            ],
        },
        "evidence": _evidence("candidate:orders"),
        "status": "CANDIDATE_ONLY",
        "automatic_resolution_allowed": False,
        "automatic_entity_union_allowed": False,
    }


def _model() -> dict:
    candidate = _candidate()
    return {
        "identity_structural_candidates": [candidate],
        "identity_structural_evidence": {
            "candidate_pairs": [candidate],
            "candidate_count": 1,
        },
        "business_objects": [
            {"entity_id": "entity:order", "name": "订单"},
            {"entity_id": "entity:sales-order", "name": "销售单"},
        ],
        "gate": {"status": "PASS", "entry_allowed": True},
        "metrics": {},
    }


def _asset() -> dict:
    return {
        "business_fact_ledger": {"items": []},
        "enterprise_identity_registry": {
            "schema": "qualibug.enterprise-identity-registry.v1",
            "entities": [
                {"entity_id": "entity:order", "canonical_label": "订单"},
                {
                    "entity_id": "entity:sales-order",
                    "canonical_label": "销售单",
                },
            ],
        },
        "enterprise_identity_benchmark": {
            "status": "MEASURED",
            "benchmark_id": "benchmark:before",
            "metrics": {
                "pairwise_precision": 1.0,
                "pairwise_recall": 0.5,
                "pairwise_f1": 0.666667,
                "false_negative_pair_count": 1,
            },
        },
    }


def _resolution() -> dict:
    return {
        "clusters": [
            {
                "entity_id": "entity:order",
                "member_mention_ids": ["mention:order"],
            },
            {
                "entity_id": "entity:sales-order",
                "member_mention_ids": ["mention:sales-order"],
            },
        ],
        "registry": deepcopy(_asset()["enterprise_identity_registry"]),
        "gate": {"status": "PASS", "entry_allowed": True, "metrics": {}},
    }


def _decision(action: str, *, fingerprint: str = "", canonical: str = "") -> dict:
    candidate = _candidate()
    return {
        "schema": "qualibug.operator-authority-decision.v1",
        "decision_kind": DECISION_KIND,
        "decision_id": f"decision:{action}",
        "candidate_id": candidate["candidate_id"],
        "conflict_id": candidate["candidate_id"],
        "candidate_fingerprint": fingerprint
        or identity_structural_candidate_fingerprint(candidate),
        "participant_entity_ids": candidate["candidate_entity_ids"],
        "action": action,
        "canonical_entity_id": canonical,
    }


def test_review_queue_is_product_candidate_queue_not_ground_truth() -> None:
    asset = _asset()
    model = _model()

    queue = project_identity_structural_review_queue(asset, model)

    assert queue["schema"] == REVIEW_QUEUE_SCHEMA
    assert queue["task_count"] == 1
    assert queue["pending_count"] == 1
    assert queue["tasks"][0]["requires_explicit_canonical_entity_selection"] is True
    assert queue["uses_existing_operator_authority_ledger"] is True
    assert queue["blind_ground_truth_workflow_used"] is False
    assert queue["product_candidates_enter_ground_truth"] is False
    assert queue["automatic_resolution_allowed"] is False


def test_confirmed_candidate_projects_one_term_alias_and_authorized_registry_retirement() -> None:
    asset = _asset()
    asset["identity_structural_review_decisions"] = [
        _decision(ACTION_CONFIRM_ALIAS, canonical="entity:order")
    ]
    model = _model()
    resolution = _resolution()
    original_clusters = deepcopy(resolution["clusters"])
    original_benchmark = deepcopy(asset["enterprise_identity_benchmark"])

    projected = apply_identity_structural_review_decisions(asset, model, resolution)

    receipt = projected["identity_structural_review_receipt"]
    assert receipt["schema"] == REVIEW_RECEIPT_SCHEMA
    assert receipt["status"] == "REBUILD_REQUIRED"
    assert receipt["rebuild_required"] is True
    assert receipt["applied_confirmation_count"] == 1
    facts = asset["business_fact_ledger"]["items"]
    assert len(facts) == 1
    fact = facts[0]
    assert fact["kind"] == "TERM_ALIAS"
    assert fact["status"] == "ACCEPTED"
    assert fact["canonical_term"] == "订单"
    assert fact["alias"] == "销售单"
    assert fact["generated_from_structural_identity_review"] is True
    assert fact["identity_evidence_class"] == "EXPLICIT_ALIAS"
    assert fact["automatic_resolution_allowed"] is False
    registry_ids = {
        row["entity_id"] for row in asset["enterprise_identity_registry"]["entities"]
    }
    assert registry_ids == {"entity:order"}
    authority = asset["enterprise_identity_registry"]["operator_authorized_merge"]
    assert authority["canonical_entity_id"] == "entity:order"
    assert authority["retired_entity_ids"] == ["entity:sales-order"]
    assert resolution["clusters"] == original_clusters
    assert asset["enterprise_identity_benchmark"] == original_benchmark
    assert receipt["ground_truth_mutated"] is False


def test_rejected_candidate_never_creates_alias_or_rebuild() -> None:
    asset = _asset()
    asset["identity_structural_review_decisions"] = [
        _decision(ACTION_REJECT_CANDIDATE)
    ]

    projected = apply_identity_structural_review_decisions(
        asset, _model(), _resolution()
    )

    receipt = projected["identity_structural_review_receipt"]
    assert receipt["rebuild_required"] is False
    assert receipt["rejected_count"] == 1
    assert receipt["applied_confirmation_count"] == 0
    assert asset["business_fact_ledger"]["items"] == []
    assert len(asset["enterprise_identity_registry"]["entities"]) == 2


def test_candidate_fingerprint_drift_fails_closed() -> None:
    asset = _asset()
    asset["identity_structural_review_decisions"] = [
        _decision(
            ACTION_CONFIRM_ALIAS,
            fingerprint="stale:fingerprint",
            canonical="entity:order",
        )
    ]

    projected = apply_identity_structural_review_decisions(
        asset, _model(), _resolution()
    )

    receipt = projected["identity_structural_review_receipt"]
    assert receipt["rebuild_required"] is False
    assert receipt["stale_decision_count"] == 1
    assert receipt["stale_decisions"][0]["reason_code"] == (
        "STRUCTURAL_CANDIDATE_FINGERPRINT_DRIFT"
    )
    assert asset["business_fact_ledger"]["items"] == []


def test_operator_alias_mentions_are_removed_from_external_benchmark_universe() -> None:
    asset = _asset()
    asset["business_fact_ledger"]["items"] = [
        {
            "kind": "TERM_ALIAS",
            "generated_from_structural_identity_review": True,
            "operator_authority_decision_id": "decision:confirm",
            "source_spans": _evidence("operator:decision"),
        }
    ]
    result = {
        "mentions": [
            {
                "mention_id": "mention:order",
                "raw_label": "订单",
                "source_id": "prd",
                "source_kind": "BUSINESS_FACT",
            },
            {
                "mention_id": "mention:sales-order",
                "raw_label": "销售单",
                "source_id": "api",
                "source_kind": "BUSINESS_FACT",
            },
            {
                "mention_id": "mention:operator:canonical",
                "raw_label": "订单",
                "source_id": "operator-authority",
                "source_kind": "TERM_ALIAS",
            },
            {
                "mention_id": "mention:operator:alias",
                "raw_label": "销售单",
                "source_id": "operator-authority",
                "source_kind": "TERM_ALIAS",
            },
        ],
        "edges": [
            {
                "edge_id": "edge:source",
                "left_mention_id": "mention:order",
                "right_mention_id": "mention:sales-order",
            },
            {
                "edge_id": "edge:operator",
                "left_mention_id": "mention:operator:canonical",
                "right_mention_id": "mention:operator:alias",
            },
        ],
        "clusters": [
            {
                "entity_id": "entity:order",
                "canonical_label": "订单",
                "labels": ["订单", "销售单"],
                "member_mention_ids": [
                    "mention:order",
                    "mention:sales-order",
                    "mention:operator:canonical",
                    "mention:operator:alias",
                ],
                "accepted_identity_edge_ids": ["edge:source", "edge:operator"],
            }
        ],
        "bindings": [],
        "conflicts": [],
        "mention_to_entity": {
            "mention:order": "entity:order",
            "mention:sales-order": "entity:order",
            "mention:operator:canonical": "entity:order",
            "mention:operator:alias": "entity:order",
        },
        "gate": {"metrics": {"mention_count": 4, "identity_edge_count": 2}},
    }

    scrubbed = scrub_operator_structural_review_mentions(asset, result)

    assert [row["mention_id"] for row in scrubbed["mentions"]] == [
        "mention:order",
        "mention:sales-order",
    ]
    assert [row["edge_id"] for row in scrubbed["edges"]] == ["edge:source"]
    cluster = scrubbed["clusters"][0]
    assert cluster["member_mention_ids"] == [
        "mention:order",
        "mention:sales-order",
    ]
    assert cluster["operator_identity_merge_authorized"] is True
    assert cluster["operator_identity_merge_decision_refs"] == ["decision:confirm"]
    assert scrubbed["gate"]["metrics"]["mention_count"] == 2
    projection = scrubbed["operator_structural_review_projection"]
    assert projection["synthetic_mentions_removed_from_benchmark_universe"] == 2
    assert projection["ground_truth_universe_changed"] is False

    prior_registry = {
        "schema": "qualibug.enterprise-identity-registry.v1",
        "entities": [
            {"entity_id": "entity:order", "canonical_label": "订单"},
        ],
        "operator_authorized_merge": {
            "decision_id": "decision:confirm",
            "canonical_entity_id": "entity:order",
            "retired_entity_ids": ["entity:sales-order"],
            "automatic_merge": False,
        },
    }
    governed = govern_identity_registry(prior_registry, scrubbed)
    governed_cluster = governed["clusters"][0]
    assert governed_cluster["accepted_identity_edge_ids"] == ["edge:source"]
    registry = governed["registry"]
    assert registry["operator_authorized_merge"] == prior_registry[
        "operator_authorized_merge"
    ]
    registry_receipt = governed["registry_recompute_receipt"]
    assert registry_receipt["operator_authorized_retired_entity_ids"] == [
        "entity:sales-order"
    ]
    assert registry_receipt["retired_entity_ids"] == ["entity:sales-order"]
    assert registry_receipt["automatic_entity_merge_used"] is False


def test_measurement_receipt_reports_precision_recall_delta_without_mutating_truth() -> None:
    asset = _asset()
    pending = {
        "schema": REVIEW_RECEIPT_SCHEMA,
        "applied_confirmation_count": 1,
        "rejected_count": 0,
        "stale_decision_count": 0,
        "measurement_before": deepcopy(asset["enterprise_identity_benchmark"]),
        "ground_truth_mutated": False,
    }
    asset["enterprise_identity_benchmark"] = {
        "status": "MEASURED",
        "benchmark_id": "benchmark:after",
        "metrics": {
            "pairwise_precision": 1.0,
            "pairwise_recall": 1.0,
            "pairwise_f1": 1.0,
            "false_negative_pair_count": 0,
        },
    }
    model = {"metrics": {}}

    projected = finalize_identity_structural_review_measurement(
        asset, model, pending
    )

    receipt = projected["identity_structural_review_receipt"]
    assert receipt["status"] == "APPLIED"
    assert receipt["measurement_status"] == "MEASURED"
    assert receipt["measurement_delta"]["pairwise_recall"] == 0.5
    assert receipt["measurement_delta"]["false_negative_pair_count"] == -1.0
    assert receipt["ground_truth_mutated"] is False
    assert projected["metrics"][
        "enterprise_identity_structural_review_measurement_comparable"
    ] is True


def test_record_review_decision_appends_to_existing_authority_ledger(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ai_test_asset_center.enterprise_knowledge_center import composition

    asset = _asset()
    asset["enterprise_understanding_model"] = _model()
    existing = {
        "schema": "qualibug.operator-authority-decision-ledger.v1",
        "project_id": "demo",
        "decisions": [
            {
                "schema": "qualibug.operator-authority-decision.v1",
                "decision_id": "decision:business-conflict",
                "conflict_id": "conflict:business",
                "action": "SELECT_FACT",
            }
        ],
        "audit_receipts": [],
    }
    captured: dict = {}

    monkeypatch.setattr(
        composition,
        "load_enterprise_business_knowledge_asset",
        lambda *_args, **_kwargs: asset,
    )
    monkeypatch.setattr(
        review,
        "load_authority_decision_ledger",
        lambda *_args, **_kwargs: deepcopy(existing),
    )

    def fake_save(ledger: dict, *_args: object, **_kwargs: object) -> None:
        captured.update(deepcopy(ledger))

    monkeypatch.setattr(review, "save_authority_decision_ledger", fake_save)

    result = review.record_identity_structural_review_decision(
        "demo",
        candidate_id="candidate:orders",
        action=ACTION_CONFIRM_ALIAS,
        canonical_entity_id="entity:order",
        rationale="资料结构一致，人工确认同一对象",
        actor={"name": "owner", "role": "OWNER"},
        rebuild=False,
    )

    assert result["ok"] is True
    assert result["ground_truth_mutated"] is False
    assert len(captured["decisions"]) == 2
    assert captured["decisions"][0]["decision_id"] == "decision:business-conflict"
    structural = captured["decisions"][1]
    assert structural["decision_kind"] == DECISION_KIND
    assert structural["candidate_id"] == "candidate:orders"
    assert structural["canonical_entity_id"] == "entity:order"
    assert structural["automatic_entity_union_allowed"] is False
    assert len(captured["audit_receipts"]) == 1
