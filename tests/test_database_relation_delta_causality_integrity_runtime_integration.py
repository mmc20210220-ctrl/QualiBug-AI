from __future__ import annotations

import sqlite3
from pathlib import Path

from ai_test_asset_center import database_observer_experiment_runtime as phase_runtime
from ai_test_asset_center.database_relation_delta_causality_integrity import (
    causal_scope_fingerprint,
    evaluate_database_relation_causal_delta_with_integrity,
)
from ai_test_asset_center.non_http_observers import install_non_http_observers
from ai_test_asset_center.operation_causality_runtime import (
    finalize_operation_causality_transport,
)
from tests.test_database_relation_delta_causality_runtime_integration import (
    _config,
    _database,
    _experiment,
    _transport_result,
)


install_non_http_observers()


def _freeze_exact_causal_scope(experiment: dict) -> dict:
    assertion = experiment["assertions"][0]
    scope = causal_scope_fingerprint(assertion)
    assertion["causal_scope_fingerprint"] = scope
    assertion["causal_attribution_contract"][
        "causal_scope_fingerprint"
    ] = scope
    assertion["database_relation_delta_binding"][
        "causal_scope_fingerprint"
    ] = scope
    for draft in experiment["database_relation_observer_execution_drafts"]:
        draft["causal_scope_fingerprint"] = scope
        draft["causal_attribution_contract"][
            "causal_scope_fingerprint"
        ] = scope
        contract = draft["database_relation_observer_contract"]
        contract["causal_scope_fingerprint"] = scope
        contract["causal_attribution_contract"][
            "causal_scope_fingerprint"
        ] = scope
    return experiment


def test_real_sqlite_integrity_chain_excludes_noise_and_clears_private_values(
    tmp_path: Path,
) -> None:
    project = "causal_delta_integrity_runtime"
    database = tmp_path / "causal-integrity.sqlite3"
    _database(database)
    _config(tmp_path, project, database)
    experiment = _freeze_exact_causal_scope(_experiment())
    observations: dict = {}

    before = phase_runtime.execute_database_observer_phase(
        experiment,
        phase="BEFORE",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "a-1"},
        observations=observations,
        steps_out=[],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    assert before["status"] == "OBSERVED"
    assert "_operation_causality_runtime_values" in observations

    transport = finalize_operation_causality_transport(
        exp=experiment,
        result=_transport_result(),
        observations=observations,
        campaign_id="campaign-1",
        execution_id="execution-1",
    )[0]
    assert transport["status"] == "ATTRIBUTED"
    assert transport["receipt_id"].startswith("causal_transport_")

    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE accounts SET balance = 90 WHERE id = 'a-1'"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, request_id, amount) "
        "VALUES ('current-operation', 'a-1', 'req-1', 10)"
    )
    connection.execute(
        "INSERT INTO ledger_entries(id, account_id, request_id, amount) "
        "VALUES ('concurrent-noise', 'a-1', 'other-request', 500)"
    )
    connection.commit()
    connection.close()

    after = phase_runtime.execute_database_observer_phase(
        experiment,
        phase="AFTER",
        root=tmp_path,
        project=project,
        runtime_contract={},
        runtime_bindings={"id": "a-1"},
        observations=observations,
        steps_out=[
            {
                "phase": "treatment",
                "step_id": "treatment-1",
                "operation_ref": "api:POST:/ledger",
                "status_code": 201,
                "body": {"accepted": True},
                "response": {
                    "status_code": 201,
                    "body": {"accepted": True},
                },
            }
        ],
        campaign_id="campaign-1",
        execution_id="execution-1",
    )
    assert after["status"] == "OBSERVED"
    assert "_operation_causality_runtime_values" not in observations
    assert "operation_causality_assertions" not in observations
    assert "operation_causality_experiment" not in observations

    payloads = {
        row["observation_phase"]: row["evidence"][
            "approved_database_relation_aggregate_snapshot"
        ]
        for row in observations[
            "approved_database_relation_phase_receipts"
        ]
    }
    assert str(payloads["AFTER"]["aggregate_values"]["related_value"]) == "10"
    assert payloads["AFTER"]["aggregate_values"]["related_scope_count"] == 1

    result = evaluate_database_relation_causal_delta_with_integrity(
        {
            "spec": experiment["assertions"][0],
            "observations": observations,
        }
    )
    assert result["passed"] is True
    assert result["reason_code"] == ""
    assert result["actual"]["root_delta"] == "-10"
    assert result["actual"]["relation_delta"] == "10"
    assert result["actual"]["difference"] == "0"
    assert result["actual"]["causal_scope_semantic_match"] is True
    assert result["actual"]["transport_receipt_integrity_valid"] is True
    assert result["actual"]["causal_value_fingerprint_match"] is True
