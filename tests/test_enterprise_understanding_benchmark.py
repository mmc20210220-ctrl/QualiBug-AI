from __future__ import annotations

from copy import deepcopy

from benchmark_evaluator.enterprise_understanding import (
    align_enterprise_understanding,
    analyse_miss_root_causes,
    calculate_benchmark_metrics,
    run_benchmark,
    validate_ground_truth,
)


def _ground_truth(*, scope_complete: bool = True) -> dict:
    return {
        "schema": "qualibug.enterprise-understanding-ground-truth.v1",
        "project_id": "benchmark-demo",
        "scope_complete": scope_complete,
        "minimum_profile": {
            "business_objects": 1,
            "actors": 1,
            "operations": 1,
            "business_behaviors": 1,
            "state_transitions": 1,
            "expected_unknowns": 1,
            "bug_dependencies": 1,
        },
        "business_objects": [
            {
                "ground_truth_id": "gt:object:order",
                "canonical_name": "订单",
                "aliases": ["销售订单"],
                "criticality": "P0",
                "source_refs": ["source:policy"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "actors": [
            {
                "ground_truth_id": "gt:actor:warehouse",
                "canonical_name": "仓管员",
                "aliases": ["仓库管理员"],
                "criticality": "P1",
                "source_refs": ["source:policy"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "operations": [
            {
                "ground_truth_id": "gt:operation:ship",
                "canonical_name": "发货",
                "aliases": ["订单发货"],
                "object_refs": ["订单"],
                "criticality": "P0",
                "source_refs": ["source:policy"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "object_relations": [],
        "lifecycles": [],
        "state_transitions": [
            {
                "ground_truth_id": "gt:transition:approved-shipped",
                "object_ref": "订单",
                "from_state": "已审核",
                "to_state": "已发货",
                "criticality": "P0",
                "source_refs": ["source:policy"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "business_rules": [],
        "business_behaviors": [
            {
                "ground_truth_id": "gt:behavior:ship-approved-order",
                "actor_refs": ["仓管员"],
                "operation": "发货",
                "object_refs": ["订单"],
                "preconditions": [
                    {"field": "订单状态", "operator": "EQUALS", "value": "已审核"}
                ],
                "permission_decision": "ALLOW",
                "state_effects": [{"from_state": "已审核", "to_state": "已发货"}],
                "criticality": "P0",
                "source_refs": ["source:policy"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "conflicts": [],
        "expected_unknowns": [
            {
                "ground_truth_id": "gt:unknown:cleanup",
                "canonical_name": "取消后的库存释放规则未明确",
                "reason_code": "CANCEL_COMPENSATION_UNRESOLVED",
                "criticality": "P1",
                "source_refs": ["source:policy"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "bug_dependencies": [
            {
                "ground_truth_id": "gt:bug:031",
                "bug_id": "BUG-031",
                "required_ground_truth_ids": [
                    "gt:operation:ship",
                    "gt:behavior:ship-approved-order",
                ],
                "criticality": "P0",
                "source_refs": ["source:bug-catalog"],
                "annotation_status": "CONFIRMED",
            }
        ],
    }


def _asset() -> dict:
    return {
        "sources": [
            {"source_id": "source:policy", "filename": "policy.md"},
            {"source_id": "source:bug-catalog", "filename": "bugs.json"},
        ],
        "enterprise_understanding_model": {
            "business_objects": [
                {
                    "object_id": "object:order",
                    "name": "订单",
                    "aliases": ["销售订单"],
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:policy"}],
                }
            ],
            "actors": [
                {
                    "actor_id": "actor:warehouse",
                    "name": "仓管员",
                    "aliases": ["仓库管理员"],
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:policy"}],
                }
            ],
            "operations": [
                {
                    "operation_id": "operation:ship",
                    "name": "发货",
                    "raw_action_names": ["订单发货"],
                    "object_refs": ["object:order"],
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:policy"}],
                }
            ],
            "object_relations": [],
            "lifecycles": [
                {
                    "lifecycle_id": "lifecycle:order",
                    "object_ref": "object:order",
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:policy"}],
                    "transitions": [
                        {
                            "transition_id": "transition:approved-shipped",
                            "from_state": "已审核",
                            "to_state": "已发货",
                            "completeness": "COMPLETE",
                            "evidence": [{"source_id": "source:policy"}],
                        }
                    ],
                }
            ],
            "rules": [],
            "business_behaviors": [
                {
                    "behavior_id": "behavior:ship-approved-order",
                    "actor_refs": ["actor:warehouse"],
                    "operation_ref": "发货",
                    "object_refs": ["object:order"],
                    "preconditions": [
                        {
                            "field_candidate": "订单状态",
                            "operator_candidate": "EQUALS",
                            "value_candidate": {"raw": "已审核"},
                        }
                    ],
                    "permission_decision": "ALLOW",
                    "state_effects": [{"from_state": "已审核", "to_state": "已发货"}],
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:policy"}],
                }
            ],
            "unknowns": [
                {
                    "unknown_id": "unknown:cleanup",
                    "reason_code": "CANCEL_COMPENSATION_UNRESOLVED",
                    "kind": "BUSINESS_RULE_UNKNOWN",
                    "status": "UNRESOLVED",
                    "evidence": [{"source_id": "source:policy"}],
                }
            ],
            "conflicts": [],
        },
    }


def test_evaluator_measures_existing_model_without_mutation(tmp_path) -> None:
    ground_truth = _ground_truth()
    asset = _asset()
    before = deepcopy(asset)

    result = run_benchmark(ground_truth, asset, output_dir=str(tmp_path))

    assert result["status"] == "PASS"
    assert result["metrics"]["business_object_recall"] == 1.0
    assert result["metrics"]["actor_recall"] == 1.0
    assert result["metrics"]["operation_recall"] == 1.0
    assert result["metrics"]["business_behavior_recall"] == 1.0
    assert result["metrics"]["state_transition_recall"] == 1.0
    assert result["metrics"]["expected_unknown_exposure_rate"] == 1.0
    assert result["metrics"]["unexpected_unknown_count"] == 0
    assert result["metrics"]["bug_dependency_metrics"]["bug_dependency_rule_coverage_rate"] == 1.0
    assert result["metrics"]["false_confirmation_metrics"]["false_confirmation_rate"] == 0.0
    assert result["root_cause_analysis"]["misses"] == []
    assert result["workflow_receipt"]["hidden_ground_truth_entered_product_runtime"] is False
    assert result["workflow_receipt"]["model_writeback_allowed"] is False
    assert result["ground_truth_fingerprint"]
    assert result["product_asset_fingerprint"]
    assert asset == before
    assert (tmp_path / "workflow_receipt.json").exists()
    assert (tmp_path / "metric_summary.json").exists()
    assert (tmp_path / "report.md").exists()


def test_missing_source_precedes_downstream_behavior_gap() -> None:
    ground_truth = validate_ground_truth(_ground_truth())
    asset = _asset()
    asset["sources"] = [{"source_id": "source:bug-catalog"}]
    asset["enterprise_understanding_model"]["business_behaviors"] = []
    asset["enterprise_understanding_model"]["operations"] = []

    alignment = align_enterprise_understanding(ground_truth, asset)
    roots = analyse_miss_root_causes(ground_truth, asset, alignment)

    behavior = next(
        row for row in roots["misses"]
        if row["ground_truth_id"] == "gt:behavior:ship-approved-order"
    )
    assert behavior["root_cause"] == "SOURCE_NOT_PARSED"
    assert roots["bug_dependency_root_causes"][0]["earliest_root_cause"] == "SOURCE_NOT_PARSED"


def test_wrong_operation_object_binding_is_not_recall() -> None:
    ground_truth = validate_ground_truth(_ground_truth())
    asset = _asset()
    model = asset["enterprise_understanding_model"]
    model["business_objects"].append(
        {
            "object_id": "object:customer",
            "name": "客户",
            "status": "CONFIRMED",
            "evidence": [{"source_id": "source:policy"}],
        }
    )
    model["operations"][0]["object_refs"] = ["object:customer"]

    alignment = align_enterprise_understanding(ground_truth, asset)
    metrics = calculate_benchmark_metrics(ground_truth, alignment)
    operation = next(
        row for row in alignment["alignments"]
        if row["ground_truth_id"] == "gt:operation:ship"
    )

    assert operation["alignment_status"] == "WRONG_BINDING"
    assert metrics["operation_recall"] == 0.0
    assert metrics["operation_object_binding_accuracy"] == 0.0


def test_unexpected_unknown_is_separate_from_missing_expected_unknown() -> None:
    ground_truth = validate_ground_truth(_ground_truth())
    asset = _asset()
    asset["enterprise_understanding_model"]["unknowns"].append(
        {
            "unknown_id": "unknown:should-have-resolved",
            "reason_code": "OBJECT_ALIAS_UNRESOLVED",
            "status": "UNRESOLVED",
            "evidence": [{"source_id": "source:policy"}],
        }
    )

    alignment = align_enterprise_understanding(ground_truth, asset)
    metrics = calculate_benchmark_metrics(ground_truth, alignment)

    assert metrics["expected_unknown_exposure_rate"] == 1.0
    assert metrics["unexpected_unknown_count"] == 1
    assert alignment["unexpected_unknowns"][0]["alignment_status"] == "UNKNOWN_SHOULD_HAVE_BEEN_RESOLVED"


def test_false_confirmation_rate_requires_complete_scope() -> None:
    ground_truth = validate_ground_truth(_ground_truth(scope_complete=False))
    asset = _asset()
    asset["enterprise_understanding_model"]["business_objects"].append(
        {
            "object_id": "object:unannotated",
            "name": "未标注对象",
            "status": "CONFIRMED",
            "evidence": [{"source_id": "source:policy"}],
        }
    )

    metrics = calculate_benchmark_metrics(
        ground_truth,
        align_enterprise_understanding(ground_truth, asset),
    )

    receipt = metrics["false_confirmation_metrics"]
    assert receipt["status"] == "NOT_MEASURABLE_INCOMPLETE_GROUND_TRUTH_SCOPE"
    assert receipt["false_confirmation_rate"] is None
    assert receipt["unmatched_confirmed_candidate_count"] == 1


def test_minimum_profile_shortfall_is_fail_visible() -> None:
    ground_truth = _ground_truth()
    ground_truth["minimum_profile"]["business_objects"] = 10

    validated = validate_ground_truth(ground_truth)
    result = run_benchmark(validated, _asset())

    assert validated["validation_receipt"]["status"] == "BENCHMARK_GROUND_TRUTH_INCOMPLETE"
    assert validated["validation_receipt"]["shortfalls"] == [
        {"collection": "business_objects", "expected_minimum": 10, "actual": 1}
    ]
    assert result["status"] == "BENCHMARK_GROUND_TRUTH_INCOMPLETE"
    assert result["workflow_receipt"]["model_writeback_allowed"] is False
