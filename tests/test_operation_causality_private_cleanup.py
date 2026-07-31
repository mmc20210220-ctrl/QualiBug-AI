from __future__ import annotations

from pathlib import Path

from ai_test_asset_center import database_observer_experiment_runtime as phase_runtime
from ai_test_asset_center.non_http_observers import install_non_http_observers
from tests.test_database_relation_delta_causality_runtime_integration import (
    _config,
    _database,
    _experiment,
)


install_non_http_observers()


def test_before_refusal_clears_private_causal_runtime_state(
    tmp_path: Path,
) -> None:
    project = "causal_private_cleanup"
    database = tmp_path / "causal-private.sqlite3"
    _database(database)
    _config(tmp_path, project, database)
    experiment = _experiment()
    for draft in experiment["database_relation_observer_execution_drafts"]:
        contract = draft["database_relation_observer_contract"]
        causal_predicate = [
            row
            for row in contract["relation_predicates"]
            if row.get("predicate_kind") == "OPERATION_ATTRIBUTION"
        ][0]
        causal_predicate["child_database_field_name"] = "password"
        contract["causal_attribution_contract"][
            "child_database_field_name"
        ] = "password"
    observations: dict = {}

    result = phase_runtime.execute_database_observer_phase(
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

    assert result["status"] == "INDETERMINATE"
    assert result["blocked"] is True
    assert "_operation_causality_runtime_values" not in observations
    assert "operation_causality_assertions" not in observations
    assert "operation_causality_experiment" not in observations
    relation_receipt = observations[
        "approved_database_relation_phase_receipts"
    ][0]
    assert relation_receipt["reason_code"] == (
        "DATABASE_RELATION_CAUSAL_CONTRACT_REFUSED"
    )
