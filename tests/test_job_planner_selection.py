from __future__ import annotations

from ai_test_asset_center.adaptive_discovery_planner import plan_obligation_round
from ai_test_asset_center.job_async_protocol import register_job_async_protocol


def _obligation(obligation_id: str, family: str, confidence: float) -> dict:
    return {
        "obligation_id": obligation_id,
        "risk_family": family,
        "subject_refs": [f"subject:{obligation_id}"],
        "required_operations": [f"operation:{obligation_id}"],
        "property": {
            "operation_ref": f"operation:{obligation_id}",
        },
        "confidence": confidence,
        "compile_status": "PENDING",
    }


def _compiled_experiment(obligation_id: str) -> dict:
    return {
        "experiment_id": f"experiment:{obligation_id}",
        "obligation_id": obligation_id,
        "compile_receipt": {"status": "COMPILED"},
        "treatment_plan": [
            {
                "step_id": f"step:{obligation_id}",
                "method": "GET",
                "path": f"/api/{obligation_id}",
            }
        ],
    }


def test_process_family_gets_one_existing_planner_slot_when_budget_covers_present_families() -> None:
    register_job_async_protocol()
    obligations = [
        _obligation("job-runtime-integrity", "process", 0.6),
        *[
            _obligation(f"validation-{index}", "validation", 0.95)
            for index in range(8)
        ],
    ]
    experiments = {
        row["obligation_id"]: _compiled_experiment(row["obligation_id"])
        for row in obligations
    }
    operations = [
        {
            "id": row["required_operations"][0],
            "method": "GET",
            "path": f"/api/{row['obligation_id']}",
        }
        for row in obligations
    ]

    plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=experiments,
        behavior_ir={"operations": operations},
        budget=2,
    )

    selected_ids = {row["obligation_id"] for row in plan["selected"]}
    assert "job-runtime-integrity" in selected_ids
    assert plan["family_coverage"]["process"] == 1
    assert plan["selected_count"] == 2
