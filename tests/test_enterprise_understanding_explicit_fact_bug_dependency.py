from __future__ import annotations

import json

from benchmark_evaluator.enterprise_understanding.explicit_fact_bug_dependency import (
    analyze_explicit_fact_bug_dependencies,
    main,
)


def _measurement(status: str = "PASS") -> dict:
    return {
        "status": status,
        "alignments": [
            {
                "ground_truth_id": "gt:fact:exact",
                "alignment_status": "EXACT",
            },
            {
                "ground_truth_id": "gt:fact:partial",
                "alignment_status": "PARTIAL",
            },
            {
                "ground_truth_id": "gt:fact:missing",
                "alignment_status": "MISSING",
            },
        ],
    }


def _ground_truth() -> dict:
    return {
        "bug_dependencies": [
            {
                "ground_truth_id": "gt:bug:exact",
                "annotation_status": "CONFIRMED",
                "criticality": "P0",
                "bug_id": "BUG-EXACT",
                "required_ground_truth_ids": ["gt:fact:exact"],
            },
            {
                "ground_truth_id": "gt:bug:partial",
                "annotation_status": "CONFIRMED",
                "criticality": "P0",
                "bug_id": "BUG-PARTIAL",
                "required_ground_truth_ids": [
                    "gt:fact:exact",
                    "gt:fact:partial",
                ],
            },
            {
                "ground_truth_id": "gt:bug:missing",
                "annotation_status": "CONFIRMED",
                "criticality": "P1",
                "bug_id": "BUG-MISSING",
                "required_ground_truth_ids": ["gt:fact:missing"],
            },
            {
                "ground_truth_id": "gt:bug:mixed",
                "annotation_status": "CONFIRMED",
                "criticality": "P1",
                "bug_id": "BUG-MIXED",
                "required_ground_truth_ids": [
                    "gt:fact:exact",
                    "gt:object:order",
                ],
            },
            {
                "ground_truth_id": "gt:bug:non-slot",
                "annotation_status": "CONFIRMED",
                "criticality": "P2",
                "bug_id": "BUG-NON-SLOT",
                "required_ground_truth_ids": ["gt:object:order"],
            },
        ]
    }


def _by_bug(result: dict) -> dict[str, dict]:
    return {str(row.get("bug_id")): row for row in result["bugs"]}


def test_exact_fact_dependencies_are_not_confused_with_full_bug_readiness() -> None:
    result = analyze_explicit_fact_bug_dependencies(
        _ground_truth(),
        _measurement(),
    )
    bugs = _by_bug(result)
    metrics = result["metrics"]

    assert result["status"] == "PASS"
    assert metrics["bug_dependency_count"] == 5
    assert metrics["bugs_with_explicit_fact_dependencies"] == 4
    assert metrics["explicit_fact_dependency_exact_bug_count"] == 2
    assert metrics["explicit_fact_dependency_exact_rate"] == 0.5
    assert metrics["bug_ready_from_explicit_fact_slots_only_count"] == 1
    assert metrics["bug_ready_from_explicit_fact_slots_only_rate"] == 0.2
    assert metrics["missing_explicit_fact_dependency_count"] == 1
    assert metrics["non_fact_slot_dependency_count"] == 2

    assert bugs["BUG-EXACT"]["all_explicit_fact_dependencies_exact"] is True
    assert bugs["BUG-EXACT"]["bug_ready_from_explicit_fact_slots_only"] is True

    assert bugs["BUG-PARTIAL"]["explicit_fact_dependency_status"] == (
        "EXPLICIT_FACT_DEPENDENCIES_PARTIAL"
    )
    assert bugs["BUG-PARTIAL"]["bug_ready_from_explicit_fact_slots_only"] is False
    assert bugs["BUG-PARTIAL"]["partial_or_wrong_explicit_fact_dependency_ids"] == [
        "gt:fact:partial"
    ]

    assert bugs["BUG-MISSING"]["explicit_fact_dependency_status"] == (
        "EXPLICIT_FACT_DEPENDENCIES_MISSING"
    )
    assert bugs["BUG-MISSING"]["missing_explicit_fact_dependency_ids"] == [
        "gt:fact:missing"
    ]

    assert bugs["BUG-MIXED"]["all_explicit_fact_dependencies_exact"] is True
    assert bugs["BUG-MIXED"][
        "all_required_dependencies_represented_by_fact_slots"
    ] is False
    assert bugs["BUG-MIXED"]["bug_ready_from_explicit_fact_slots_only"] is False
    assert bugs["BUG-MIXED"]["non_fact_slot_dependency_ids"] == [
        "gt:object:order"
    ]

    assert bugs["BUG-NON-SLOT"]["explicit_fact_dependency_status"] == (
        "NOT_APPLICABLE_NO_EXPLICIT_FACT_DEPENDENCY"
    )
    assert result["partial_fact_is_bug_ready"] is False
    assert result["non_fact_slot_dependency_is_assumed_understood"] is False
    assert result["automatic_winner_used"] is False
    assert result["fuzzy_or_llm_alignment_used"] is False


def test_non_pass_fact_slot_measurement_blocks_dependency_claim() -> None:
    result = analyze_explicit_fact_bug_dependencies(
        _ground_truth(),
        _measurement("NOT_MEASURED"),
    )

    assert result["status"] == "BLOCKED_FACT_SLOT_MEASUREMENT_NOT_PASS"
    assert result["metrics"] == {}
    assert result["bugs"] == []


def test_missing_bug_dependency_annotations_remains_not_measured() -> None:
    result = analyze_explicit_fact_bug_dependencies(
        {"bug_dependencies": []},
        _measurement(),
    )

    assert result["status"] == "NOT_MEASURED_NO_BUG_DEPENDENCIES"
    assert result["metrics"]["bug_dependency_count"] == 0
    assert result["metrics"]["explicit_fact_dependency_exact_rate"] is None


def test_cli_writes_machine_readable_dependency_analysis(tmp_path) -> None:
    ground_truth = tmp_path / "ground_truth.json"
    measurement = tmp_path / "measurement.json"
    output = tmp_path / "explicit_fact_bug_dependency.json"
    ground_truth.write_text(
        json.dumps(_ground_truth(), ensure_ascii=False),
        encoding="utf-8",
    )
    measurement.write_text(
        json.dumps(_measurement(), ensure_ascii=False),
        encoding="utf-8",
    )

    code = main(
        [
            "--ground-truth",
            str(ground_truth),
            "--measurement",
            str(measurement),
            "--output",
            str(output),
        ]
    )

    assert code == 0
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted["status"] == "PASS"
    assert persisted["metrics"]["bug_dependency_count"] == 5
