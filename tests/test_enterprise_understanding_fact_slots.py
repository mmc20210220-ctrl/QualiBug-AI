from __future__ import annotations

from benchmark_evaluator.enterprise_understanding.fact_slots import evaluate_business_fact_slots


def _ground_truth() -> dict:
    return {
        "business_rules": [
            {
                "ground_truth_id": "gt:rule:approve",
                "annotation_status": "CONFIRMED",
                "criticality": "P0",
                "operation": "审批通过",
                "object_refs": ["订单"],
                "fact_type": "PERMISSION_RULE",
                "actor_refs": ["经理"],
                "modality": "MAY",
                "condition_frame": {
                    "kind": "ALL",
                    "combinator": "AND",
                    "conditions": ["状态为待审批", "所属部门一致"],
                },
                "exception_scope": [],
                "state_effects": [{"from_state": "待审批", "to_state": "已审批"}],
                "source_locators": ["rules.docx#paragraph=2"],
            }
        ],
        "business_behaviors": [],
    }


def _fact(fact_id: str = "fact:approve") -> dict:
    return {
        "fact_id": fact_id,
        "fact_type": "PERMISSION_RULE",
        "status": "ACCEPTED",
        "subject": {"actor_refs": ["经理"], "entity_refs": ["订单"]},
        "object": {"entity_refs": ["订单"]},
        "action": {"canonical": "审批通过", "raw": "审批通过"},
        "modality": "MAY",
        "condition_frame": {
            "kind": "ALL",
            "combinator": "AND",
            "conditions": ["状态为待审批", "所属部门一致"],
        },
        "exception_scope": [],
        "state_effects": [{"from_state": "待审批", "to_state": "已审批"}],
        "source_spans": [{"source_id": "source:rules", "locator": "rules.docx#paragraph=2"}],
        "claims": [
            {
                "claim_id": "claim:approve",
                "claim_type": "PRIMARY_OPERATION",
                "predicate": "审批通过",
                "subject_refs": ["经理"],
                "object_refs": ["订单"],
            }
        ],
    }


def test_fact_slot_measurement_requires_exact_typed_slots() -> None:
    asset = {"business_fact_ledger": {"schema": "qualibug.business-fact-ledger.v2", "items": [_fact()]}}
    result = evaluate_business_fact_slots(_ground_truth(), asset)
    assert result["status"] == "PASS"
    assert result["metrics"]["annotated_fact_count"] == 1
    assert result["metrics"]["exact_fact_count"] == 1
    assert result["metrics"]["slot_exact_accuracy"] == 1.0
    assert result["metrics"]["p0_exact_fact_recall"] == 1.0
    assert result["fuzzy_or_llm_alignment_used"] is False
    assert result["automatic_winner_used"] is False


def test_fact_slot_measurement_does_not_choose_between_duplicate_candidates() -> None:
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [_fact("fact:1"), _fact("fact:2")],
        }
    }
    result = evaluate_business_fact_slots(_ground_truth(), asset)
    assert result["metrics"]["ambiguous_fact_count"] == 1
    assert result["alignments"][0]["alignment_status"] == "AMBIGUOUS"
    assert result["alignments"][0]["candidate_id"] == ""
    assert set(result["alignments"][0]["candidate_ids"]) == {"fact:1", "fact:2"}


def test_fact_type_disambiguates_same_operation_and_object() -> None:
    permission = _fact("fact:permission")
    transition = _fact("fact:transition")
    transition["fact_type"] = "STATE_TRANSITION"
    transition["modality"] = "ASSERTS"
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [permission, transition],
        }
    }

    result = evaluate_business_fact_slots(_ground_truth(), asset)

    assert result["metrics"]["ambiguous_fact_count"] == 0
    assert result["alignments"][0]["candidate_id"] == "fact:permission"
    assert result["candidate_selection_contract"]["fact_type_exact"] is True


def test_complete_object_coordinates_disambiguate_relation_targets() -> None:
    ground_truth = {
        "business_rules": [
            {
                "ground_truth_id": "gt:relation:header",
                "annotation_status": "CONFIRMED",
                "criticality": "P1",
                "operation": "COMPOSED_OF",
                "object_refs": ["采购订单", "订单头"],
                "fact_type": "OBJECT_RELATION",
            }
        ],
        "business_behaviors": [],
    }
    header = {
        "fact_id": "fact:header",
        "fact_type": "OBJECT_RELATION",
        "status": "ACCEPTED",
        "subject": {"actor_refs": [], "entity_refs": ["采购订单"]},
        "object": {"entity_refs": ["订单头"]},
        "predicate": "COMPOSED_OF",
    }
    line = {
        "fact_id": "fact:line",
        "fact_type": "OBJECT_RELATION",
        "status": "ACCEPTED",
        "subject": {"actor_refs": [], "entity_refs": ["采购订单"]},
        "object": {"entity_refs": ["订单明细"]},
        "predicate": "COMPOSED_OF",
    }
    asset = {"business_fact_ledger": {"items": [header, line]}}

    result = evaluate_business_fact_slots(ground_truth, asset)

    assert result["metrics"]["ambiguous_fact_count"] == 0
    assert result["alignments"][0]["candidate_id"] == "fact:header"
    assert result["alignments"][0]["slot_alignments"]["object_refs"]["status"] == "EXACT"
    assert result["candidate_selection_contract"][
        "ground_truth_objects_must_be_subset_of_candidate_objects"
    ] is True


def test_fact_slot_measurement_stays_not_measured_without_slot_annotations() -> None:
    ground_truth = {
        "business_rules": [
            {
                "ground_truth_id": "gt:rule:minimal",
                "annotation_status": "CONFIRMED",
                "criticality": "P1",
                "operation": "提交",
                "object_refs": ["订单"],
            }
        ],
        "business_behaviors": [],
    }
    result = evaluate_business_fact_slots(ground_truth, {"business_fact_ledger": {"items": []}})
    assert result["status"] == "NOT_MEASURED"
    assert result["metrics"] == {}
