from __future__ import annotations

from ai_test_asset_center.fact_first_loss_ledger import (
    LEDGER_SCHEMA,
    attach_fact_refs_to_planning_artifacts,
    build_fact_experimentability_report,
    build_fact_first_loss_ledger,
    extract_fact_refs,
)
from benchmark_evaluator.fact_first_loss_join import (
    JOIN_SCHEMA,
    build_evaluator_fact_first_loss_ledger,
    map_stage_loss_to_spec,
)


def _exp_ledger(items):
    return {
        "schema_version": "qualibug.fact-experimentability-ledger.v1",
        "accepted_fact_count": len(items),
        "receipt_count": len(items),
        "silent_drop_count": 0,
        "ledger_fingerprint": "fp-test",
        "status_counts": {},
        "items": items,
    }


def test_extract_fact_refs_from_source_refs() -> None:
    assert set(
        extract_fact_refs(
            {
                "source_refs": ["fact:abc", {"fact_id": "fact:def"}, "operation:x"],
                "fact_refs": ["fact:ghi"],
            }
        )
    ) == {"fact:abc", "fact:def", "fact:ghi"}


def test_conservation_across_receipt_to_first_loss() -> None:
    ledger = build_fact_first_loss_ledger(
        fact_experimentability_ledger=_exp_ledger(
            [
                {
                    "receipt_id": "fer_1",
                    "fact_ref": "fact:a",
                    "status": "MISSING_PRIMARY_OPERATION",
                    "blocker_codes": ["MISSING_PRIMARY_OPERATION"],
                    "risk_operator": "business_rule_violation",
                    "risk_level": "high",
                },
                {
                    "receipt_id": "fer_2",
                    "fact_ref": "fact:b",
                    "status": "NOT_TEST_WORTHY",
                    "blocker_codes": [],
                    "risk_operator": "not_test_worthy",
                    "risk_level": "none",
                },
            ]
        ),
        obligations=[],
        experiments=[],
        obligation_attempt_ledger={"attempts": [], "ledger_fingerprint": "att"},
        campaign_id="cmp-1",
        run_id="run-1",
    )
    assert ledger["schema_version"] == LEDGER_SCHEMA
    assert ledger["row_count"] == 2
    assert ledger["conservation"]["status"] == "PASS"
    assert ledger["conservation"]["receipt_to_row_conserved"] is True
    by_fact = {row["fact_ref"]: row for row in ledger["items"]}
    assert by_fact["fact:a"]["first_loss_stage"] == "OPERATION_BINDING_BLOCKED"
    assert by_fact["fact:b"]["first_loss_stage"] == "FACT_NOT_SELECTED"


def test_blocked_compile_maps_without_inventing_execution() -> None:
    ledger = build_fact_first_loss_ledger(
        fact_experimentability_ledger=_exp_ledger(
            [
                {
                    "receipt_id": "fer_ready",
                    "fact_ref": "fact:ready",
                    "status": "READY",
                    "blocker_codes": [],
                    "risk_operator": "authorization_bypass",
                    "risk_level": "high",
                }
            ]
        ),
        obligations=[
            {
                "obligation_id": "obl_1",
                "fact_refs": ["fact:ready"],
                "source_refs": ["fact:ready"],
            }
        ],
        experiments=[
            {
                "obligation_id": "obl_1",
                "experiment_id": "exp_1",
                "compile_receipt": {
                    "status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_FIXTURE",
                },
            }
        ],
        obligation_attempt_ledger={
            "attempts": [
                {
                    "obligation_id": "obl_1",
                    "experiment_id": "exp_1",
                    "selection_status": "COMPILE_BLOCKED",
                    "terminal_status": "BLOCKED",
                    "reason_code": "BLOCKED_MISSING_FIXTURE",
                }
            ],
            "ledger_fingerprint": "att-1",
        },
    )
    row = ledger["items"][0]
    assert row["first_loss_stage"] == "FIXTURE_BLOCKED"
    assert row["execution_refs"] == []
    assert "exp_1" in row["experiment_refs"]


def test_ready_without_obligation_is_obligation_not_generated() -> None:
    ledger = build_fact_first_loss_ledger(
        fact_experimentability_ledger=_exp_ledger(
            [
                {
                    "receipt_id": "fer_ready",
                    "fact_ref": "fact:orphan",
                    "status": "READY",
                    "blocker_codes": [],
                    "risk_operator": "business_rule_violation",
                    "risk_level": "medium",
                }
            ]
        )
    )
    assert ledger["items"][0]["first_loss_stage"] == "OBLIGATION_NOT_GENERATED"


def test_attach_fact_refs_does_not_claim_behavior_change() -> None:
    obligations = [{"obligation_id": "obl_1", "source_refs": ["fact:x"]}]
    experiments = [{"obligation_id": "obl_1", "experiment_id": "exp_1"}]
    receipt = attach_fact_refs_to_planning_artifacts(
        obligations=obligations,
        experiments=experiments,
        fact_experimentability_ledger={"ledger_fingerprint": "abc"},
    )
    assert receipt["changes_compile_or_execution_decisions"] is False
    assert obligations[0]["fact_refs"] == ["fact:x"]
    assert experiments[0]["fact_refs"] == ["fact:x"]


def test_experimentability_report_includes_counts() -> None:
    exp = _exp_ledger(
        [
            {
                "receipt_id": "fer_1",
                "fact_ref": "fact:a",
                "status": "READY",
                "blocker_codes": [],
                "risk_operator": "x",
                "risk_level": "high",
                "required_operation_refs": ["op:1"],
                "observer_refs": ["http_response"],
            }
        ]
    )
    exp.update(
        {
            "ready_count": 1,
            "blocked_count": 0,
            "not_test_worthy_count": 0,
            "high_risk_fact_count": 1,
            "status_counts": {"READY": 1},
        }
    )
    first_loss = build_fact_first_loss_ledger(fact_experimentability_ledger=exp)
    report = build_fact_experimentability_report(exp, first_loss_ledger=first_loss)
    assert report["receipt_count"] == 1
    assert report["ready_count"] == 1
    assert report["first_loss_stage_counts"]


def test_evaluator_join_maps_every_gt_bug() -> None:
    matrix = {
        "schema_version": "qualibug.discovery-stage-loss-matrix.v1",
        "ground_truth_bug_count": 3,
        "bugs": [
            {"bug_id": "BUG-1", "first_loss_stage": "hypothesis_generation"},
            {"bug_id": "BUG-2", "first_loss_stage": "execution"},
            {"bug_id": "BUG-3", "first_loss_stage": "delivered"},
        ],
    }
    joined = build_evaluator_fact_first_loss_ledger(
        stage_loss_matrix=matrix,
        matched_bug_ids=["BUG-3"],
        campaign_id="cmp-eval",
        run_id="run-eval",
    )
    assert joined["schema_version"] == JOIN_SCHEMA
    assert joined["row_count"] == 3
    assert joined["conservation"]["status"] == "PASS"
    assert joined["conservation"]["every_gt_bug_has_first_loss_stage"] is True
    by_id = {row["ground_truth_ref"]: row for row in joined["items"]}
    assert by_id["BUG-1"]["first_loss_stage"] == "HYPOTHESIS_NOT_GENERATED"
    assert by_id["BUG-2"]["first_loss_stage"] == "EXECUTION_BLOCKED"
    assert by_id["BUG-3"]["first_loss_stage"] == "TRUE_POSITIVE"


def test_map_stage_loss_to_spec() -> None:
    assert map_stage_loss_to_spec("selection") == "FACT_NOT_SELECTED"
    assert map_stage_loss_to_spec("TRUE_POSITIVE") == "TRUE_POSITIVE"
    assert map_stage_loss_to_spec("unknown_stage") == "EVALUATOR_NOT_MATCHED"
