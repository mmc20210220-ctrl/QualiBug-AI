from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.database_observer_runtime_materialization_projection import (
    project_database_observer_runtime_materializations,
)


def _contract(source: str) -> dict:
    return {
        "schema": "qualibug.database-observer-contract.v1",
        "observer_id": "observer:orders",
        "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
        "runtime_observer_authoritative": True,
        "read_only": True,
        "mutation_allowed": False,
        "write_target_allowed": False,
        "oracle_authority_allowed": False,
        "identity_predicates": [
            {"database_field_name": "id", "operator": "=", "value_source": source}
        ],
        "selected_identity_key": ["id"],
        "query_plan": {
            "operation": "SELECT_ONE",
            "projection": ["id", "status"],
            "parameterized": True,
            "maximum_rows": 2,
            "raw_sql": "",
        },
    }


def _asset(source: str, phase: str = "BEFORE_AND_AFTER") -> dict:
    template = {
        "template_id": "template:orders",
        "template_kind": "APPROVED_DATABASE_OBSERVER_SNAPSHOT",
        "observer_handler_id": "approved_database_readback",
        "observer_contract_ref": "observer:orders",
        "phase": phase,
        "identity_predicates": _contract(source)["identity_predicates"],
        "projection": ["id", "status"],
        "database_observer_contract": _contract(source),
    }
    return {
        "runtime_plans": [
            {
                "plan_id": "plan:orders",
                "oracle_query_templates": {"templates": [template]},
            }
        ],
        "runtime_materializations": [
            {
                "materialization_id": "materialization:orders",
                "runtime_plan_ref": "plan:orders",
                "status": "DRAFT_READY",
                "formal_runtime_materialization": True,
            }
        ],
        "runtime_materialization_unknowns": [],
        "runtime_materialization_gate": {"status": "PASS", "metrics": {}},
        "summary": {},
        "governance": {},
    }


def test_request_identity_effect_materializes_true_before_and_after_drafts() -> None:
    asset = _asset("request.body.id")
    model = {}

    result = project_database_observer_runtime_materializations(asset, model)

    drafts = result["runtime_materializations"][0]["database_observer_execution_drafts"]
    assert [row["observation_phase"] for row in drafts] == ["BEFORE", "AFTER"]
    assert all(row["query_executed"] is False for row in drafts)
    assert all(row["runtime_connection_bound"] is False for row in drafts)
    assert all(row["secret_values_retained"] is False for row in drafts)
    assert result["runtime_materialization_gate"]["status"] == "PASS"
    receipt = result["database_observer_runtime_materialization_projection"]
    assert receipt["execution_draft_count"] == 2
    assert receipt["query_execution_count"] == 0


def test_response_identity_effect_materializes_after_only() -> None:
    asset = _asset("response.body.id")
    model = {}

    result = project_database_observer_runtime_materializations(asset, model)

    drafts = result["runtime_materializations"][0]["database_observer_execution_drafts"]
    assert [row["observation_phase"] for row in drafts] == ["AFTER"]
    assert result["database_observer_runtime_materialization_projection"][
        "response_identity_before_snapshot_fabricated"
    ] is False


def test_before_with_response_only_identity_fails_closed() -> None:
    asset = _asset("response.body.id", phase="BEFORE")
    model = {}

    result = project_database_observer_runtime_materializations(asset, model)

    materialization = result["runtime_materializations"][0]
    assert materialization["status"] == "INCOMPLETE"
    assert materialization["formal_runtime_materialization"] is False
    assert result["runtime_materialization_gate"]["status"] == (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
    )
    assert any(
        row["reason_code"]
        == "RUNTIME_MATERIALIZATION_DATABASE_OBSERVER_PHASE_UNRESOLVED"
        for row in result["runtime_materialization_unknowns"]
    )
