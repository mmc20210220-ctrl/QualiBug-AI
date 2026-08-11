"""Discovery-evaluation contract tests with governed scoring expectations.

The historical cases live in ``discovery_evaluation_contract_cases`` and remain
fully collected here.  Only tests whose expected semantics changed with the
truthful seeded-benchmark scoring contract are overridden below.
"""
from __future__ import annotations

from tests import discovery_evaluation_contract_cases as _cases
from tests.discovery_evaluation_contract_cases import *  # noqa: F401,F403

# Pytest fixtures beginning with an underscore are not imported by ``*``.
_evaluator_hmac_key = _cases._evaluator_hmac_key


def test_aggregate_uses_hidden_truth_and_clean_false_positive_measurement(tmp_path) -> None:
    manifest = _cases.load_evaluation_manifest(_cases._manifest(tmp_path))
    receipts = []
    for target in manifest.targets:
        findings = (
            [_cases._customer_deliverable_clean_finding()]
            if target.expectation == "clean"
            else [_cases._matched_finding(target.target_id)]
        )
        receipts.append(_cases._receipt(manifest, target.target_id, findings))

    serialized_receipts = _cases.json.dumps(receipts, ensure_ascii=False)
    assert "matched_bug_ids" not in serialized_receipts
    assert "canonical_unmatched" not in serialized_receipts
    assert "gt_unmatched" not in serialized_receipts

    report = _cases.aggregate_evaluation_receipts(manifest, receipts)

    # Hidden seeded GT measures coverage/recall, not precision.  Commercial FP
    # authority comes from the independently clean target.
    assert report["claim_status"] == "MEASURED"
    assert report["commercial_promotion_evidence_ready"] is False
    assert report["commercial_promotion_not_ready_reason"] == (
        "seeded_precision_not_measured"
    )
    assert report["seeded_precision_measurement_status"] == "NOT_MEASURED"

    assert report["held_in"]["true_positives"] == 1
    assert report["held_in"]["micro_recall"] == 1.0
    assert report["held_in"]["micro_precision"] is None
    assert report["held_in"]["precision_measurement_status"] == "NOT_MEASURED"

    assert report["held_out"]["true_positives"] == 3
    assert report["held_out"]["micro_recall"] == 1.0
    assert report["held_out"]["micro_precision"] is None
    assert report["held_out"]["benchmark_match_rate"] == 1.0
    assert report["held_out_macro_industry_recall"] == 1.0

    assert report["clean"]["customer_deliverable_false_positives"] == 1
    assert report["clean"]["critical_high_false_positives"] == 1
    assert report["operational"]["complete"] is True
    assert report["operational"]["total_request_count"] == 60
    assert report["operational"]["total_estimated_cost_usd"] == 6.25

    serialized = _cases.json.dumps(report, ensure_ascii=False)
    assert "ground_truth_source" not in serialized
    assert "matched_bug_ids" not in serialized
    assert "canonical_unmatched" not in serialized
    assert "gt_unmatched" not in serialized


def test_paired_evidence_requires_four_real_identical_evaluations(tmp_path) -> None:
    manifest = _cases.load_evaluation_manifest(_cases._manifest(tmp_path))
    champion_replay = _cases._policy_report(manifest, "champion", "replay")
    challenger_replay = _cases._policy_report(manifest, "challenger", "replay")
    champion_shadow = _cases._policy_report(manifest, "champion", "shadow")
    challenger_shadow = _cases._policy_report(manifest, "challenger", "shadow")

    # Dataset identity can still be frozen, but these reports are deliberately
    # not promotion evidence because seeded precision has no GT authority.
    with _cases.pytest.raises(
        _cases.EvaluationContractError,
        match="lacks commercial promotion evidence",
    ):
        _cases.build_paired_evaluation_evidence(
            manifest,
            champion_replay=champion_replay,
            challenger_replay=challenger_replay,
            champion_shadow=champion_shadow,
            challenger_shadow=challenger_shadow,
        )

    with _cases.pytest.raises(
        _cases.EvaluationContractError,
        match="seeded_precision_not_measured",
    ):
        _cases.policy_metrics_from_evaluation_reports(
            challenger_replay,
            challenger_shadow,
        )


def test_goal_status_passes_gate_d_only_with_measured_absolute_thresholds(tmp_path) -> None:
    manifest = _cases.load_evaluation_manifest(_cases._manifest(tmp_path))
    report = _cases._policy_report(manifest, "policy-champion", "replay")

    status = _cases.assess_discovery_goal_status(
        evaluation_report=report,
        baseline_cost_per_true_positive_usd=10.0,
        consecutive_non_regressive_windows=3,
    )

    gate_d = status["gates"]["gate_d_capability_breakthrough"]
    assert gate_d["measurement_status"] == "NOT_MEASURED"
    assert gate_d["status"] == "NOT_MEASURED"
    assert gate_d["passed"] is False
    precision_check = next(
        item
        for item in gate_d["checks"]
        if item["name"] == "held_out_micro_precision"
    )
    assert precision_check["measurement_status"] == "NOT_MEASURED"
    assert precision_check["actual"] is None
    assert precision_check["reason"] == "metric_missing"
    assert status["commercial_claim_status"] == "NOT_MEASURED"


def test_goal_status_blocks_clean_target_p0_p1_false_positives(tmp_path) -> None:
    manifest = _cases.load_evaluation_manifest(_cases._manifest(tmp_path))
    receipts = []
    for target in manifest.targets:
        findings = (
            [_cases._customer_deliverable_clean_finding()]
            if target.expectation == "clean"
            else [_cases._matched_finding(target.target_id)]
        )
        receipts.append(_cases._receipt(manifest, target.target_id, findings))
    report = _cases.aggregate_evaluation_receipts(manifest, receipts)

    status = _cases.assess_discovery_goal_status(
        evaluation_report=report,
        baseline_cost_per_true_positive_usd=10.0,
    )

    # The clean target still proves a high-value FP and therefore fails its
    # own check.  The overall gate stays NOT_MEASURED because seeded precision
    # is independently unavailable; it must not be promoted to a measured fail
    # or pass by substituting a fabricated precision value.
    gate_d = status["gates"]["gate_d_capability_breakthrough"]
    assert gate_d["status"] == "NOT_MEASURED"
    assert gate_d["passed"] is False
    clean_check = next(
        item
        for item in gate_d["checks"]
        if item["name"] == "clean_critical_high_false_positives"
    )
    assert clean_check["measurement_status"] == "MEASURED"
    assert clean_check["passed"] is False
    assert clean_check["actual"] == 1.0
    assert status["commercial_claim_status"] == "NOT_MEASURED"
