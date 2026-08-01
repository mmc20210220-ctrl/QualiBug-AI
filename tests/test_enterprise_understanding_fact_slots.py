from __future__ import annotations

from copy import deepcopy

from benchmark_evaluator.enterprise_understanding.fact_slots import evaluate_business_fact_slots


LOCATOR = "rules.docx#paragraph=2"


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
                "source_locators": [LOCATOR],
            }
        ],
        "business_behaviors": [],
    }


def _complete_ground_truth() -> dict:
    value = deepcopy(_ground_truth())
    value["explicit_fact_scope_complete"] = True
    value["explicit_fact_scope_locators"] = [LOCATOR]
    return value


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
        "source_spans": [{"source_id": "source:rules", "locator": LOCATOR}],
        "claims": [
            {
                "claim_id": f"claim:{fact_id}",
                "claim_type": "PRIMARY_OPERATION",
                "predicate": "审批通过",
                "subject_refs": ["经理"],
                "object_refs": ["订单"],
            }
        ],
    }


def _extra_fact() -> dict:
    return {
        "fact_id": "fact:extra",
        "fact_type": "BUSINESS_RULE",
        "status": "ACCEPTED",
        "subject": {"actor_refs": ["系统"], "entity_refs": ["发票"]},
        "object": {"entity_refs": ["发票"]},
        "action": {"canonical": "删除", "raw": "删除"},
        "modality": "MUST",
        "source_spans": [{"source_id": "source:rules", "locator": LOCATOR}],
        "claims": [
            {
                "claim_id": "claim:extra",
                "claim_type": "PRIMARY_OPERATION",
                "predicate": "删除",
                "subject_refs": ["系统"],
                "object_refs": ["发票"],
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
    assert result["metrics"]["source_locator_annotated_fact_count"] == 1
    assert result["metrics"]["source_locator_exact_fact_count"] == 1
    assert result["metrics"]["source_locator_exact_accuracy"] == 1.0
    assert result["metrics"]["accepted_fact_scope_complete"] is False
    assert result["metrics"]["accepted_fact_precision"] is None
    assert result["metrics"]["false_accepted_rate"] is None
    assert result["evidence_address_measurement_contract"][
        "annotated_fact_denominator_includes_missing_facts"
    ] is True
    assert result["evidence_address_measurement_contract"][
        "annotated_fact_denominator_includes_ambiguous_facts"
    ] is True
    assert result["fuzzy_or_llm_alignment_used"] is False
    assert result["automatic_winner_used"] is False


def test_complete_scope_measures_one_supported_accepted_fact() -> None:
    result = evaluate_business_fact_slots(
        _complete_ground_truth(),
        {"business_fact_ledger": {"items": [_fact()]}},
    )

    assert result["metrics"]["accepted_fact_scope_complete"] is True
    assert result["metrics"]["accepted_fact_scope_locator_count"] == 1
    assert result["metrics"]["accepted_fact_count_in_scope"] == 1
    assert result["metrics"]["supported_accepted_fact_count"] == 1
    assert result["metrics"]["false_accepted_fact_count"] == 0
    assert result["metrics"]["accepted_fact_precision"] == 1.0
    assert result["metrics"]["false_accepted_rate"] == 0.0
    assert result["false_accepted_facts"] == []


def test_extra_accepted_fact_in_closed_scope_reduces_precision() -> None:
    result = evaluate_business_fact_slots(
        _complete_ground_truth(),
        {"business_fact_ledger": {"items": [_fact(), _extra_fact()]}},
    )

    assert result["metrics"]["accepted_fact_count_in_scope"] == 2
    assert result["metrics"]["supported_accepted_fact_count"] == 1
    assert result["metrics"]["false_accepted_fact_count"] == 1
    assert result["metrics"]["accepted_fact_precision"] == 0.5
    assert result["metrics"]["false_accepted_rate"] == 0.5
    assert result["false_accepted_facts"][0]["candidate_id"] == "fact:extra"
    assert result["false_accepted_facts"][0]["reason"].startswith(
        "ACCEPTED_FACT_NOT_SELECTED"
    )
    assert result["accepted_fact_precision_contract"][
        "unannotated_scope_blocks_remain_in_precision_scope"
    ] is True


def test_fact_slot_measurement_does_not_choose_between_duplicate_candidates() -> None:
    asset = {
        "business_fact_ledger": {
            "schema": "qualibug.business-fact-ledger.v2",
            "items": [_fact("fact:1"), _fact("fact:2")],
        }
    }
    result = evaluate_business_fact_slots(_complete_ground_truth(), asset)
    assert result["metrics"]["ambiguous_fact_count"] == 1
    assert result["metrics"]["source_locator_annotated_fact_count"] == 1
    assert result["metrics"]["source_locator_exact_fact_count"] == 0
    assert result["metrics"]["source_locator_ambiguous_fact_count"] == 1
    assert result["metrics"]["source_locator_exact_accuracy"] == 0.0
    assert result["metrics"]["accepted_fact_count_in_scope"] == 2
    assert result["metrics"]["supported_accepted_fact_count"] == 0
    assert result["metrics"]["false_accepted_fact_count"] == 2
    assert result["metrics"]["accepted_fact_precision"] == 0.0
    assert result["metrics"]["false_accepted_rate"] == 1.0
    assert result["alignments"][0]["alignment_status"] == "AMBIGUOUS"
    assert result["alignments"][0]["candidate_id"] == ""
    assert set(result["alignments"][0]["candidate_ids"]) == {"fact:1", "fact:2"}
    assert {row["candidate_id"] for row in result["false_accepted_facts"]} == {
        "fact:1",
        "fact:2",
    }


def test_wrong_source_locator_is_not_hidden_by_other_exact_slots() -> None:
    fact = _fact()
    fact["source_spans"] = [
        {"source_id": "source:rules", "locator": "rules.docx#paragraph=99"}
    ]
    result = evaluate_business_fact_slots(
        _ground_truth(),
        {"business_fact_ledger": {"items": [fact]}},
    )

    locator = result["alignments"][0]["slot_alignments"]["source_locators"]
    assert locator["status"] == "WRONG"
    assert result["metrics"]["source_locator_wrong_fact_count"] == 1
    assert result["metrics"]["source_locator_exact_fact_count"] == 0
    assert result["metrics"]["source_locator_exact_accuracy"] == 0.0
    assert result["metrics"]["exact_fact_count"] == 0
    assert result["metrics"]["partial_fact_count"] == 1


def test_missing_fact_counts_as_missing_evidence_address() -> None:
    result = evaluate_business_fact_slots(
        _ground_truth(),
        {"business_fact_ledger": {"items": []}},
    )

    assert result["metrics"]["missing_fact_count"] == 1
    assert result["metrics"]["source_locator_annotated_fact_count"] == 1
    assert result["metrics"]["source_locator_missing_fact_count"] == 1
    assert result["metrics"]["source_locator_exact_accuracy"] == 0.0


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
    assert result["metrics"]["source_locator_exact_accuracy"] is None
    assert result["metrics"]["accepted_fact_precision"] is None
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


def test_extra_semantic_condition_is_wrong_not_exact_subset() -> None:
    fact = _fact()
    fact["condition_frame"] = {
        "kind": "ALL",
        "combinator": "AND",
        "conditions": [
            "状态为待审批",
            "所属部门一致",
            "错误附加条件",
        ],
    }

    result = evaluate_business_fact_slots(
        _ground_truth(),
        {"business_fact_ledger": {"items": [fact]}},
    )

    condition = result["alignments"][0]["slot_alignments"]["condition_frame"]
    assert condition["status"] == "WRONG"
    assert result["metrics"]["exact_fact_count"] == 0
    assert result["metrics"]["wrong_slot_count"] == 1
    assert result["candidate_selection_contract"][
        "annotated_semantic_coordinate_lists_require_exact_cardinality"
    ] is True
