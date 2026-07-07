from __future__ import annotations


def test_runtime_scenario_contract_generates_read_only_executable_scenario() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    scenarios = SemanticScenarioGenerator().generate(
        {},
        runtime_scenario_contract={
            "execution_policy": "safe_read_only",
            "actor": {"id": "customer_qa_lead"},
            "scenarios": [
                {
                    "id": "read-orders",
                    "entity": "orders",
                    "steps": [{"method": "GET", "path": "/api/orders", "expected_status": 200}],
                }
            ],
        },
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.id == "read-orders"
    assert scenario.execution_policy == "safe_read_only"
    assert scenario.behavior_slice_kind == "runtime_contract"
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/orders"
    assert scenario.evidence_gaps == []


def test_runtime_scenario_contract_rejects_write_step_under_safe_read_only() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    scenarios = SemanticScenarioGenerator().generate(
        {},
        runtime_scenario_contract={
            "execution_policy": "safe_read_only",
            "actor": {"id": "customer_qa_lead"},
            "scenarios": [
                {
                    "id": "write-orders",
                    "entity": "orders",
                    "steps": [{"method": "POST", "path": "/api/orders", "expected_status": 201}],
                }
            ],
        },
    )

    assert scenarios == []


def test_runtime_scenario_contract_honors_selected_slice_binding() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    contract = {
        "execution_policy": "safe_read_only",
        "actor": {"id": "customer_qa_lead"},
        "scenarios": [
            {
                "id": "read-orders",
                "entity": "orders",
                "behavior_slice_id": "slice-orders-read",
                "steps": [{"method": "GET", "path": "/api/orders", "expected_status": 200}],
            }
        ],
    }

    assert SemanticScenarioGenerator().generate({}, active_slice_ids={"other-slice"}, runtime_scenario_contract=contract) == []
    selected = SemanticScenarioGenerator().generate({}, active_slice_ids={"slice-orders-read"}, runtime_scenario_contract=contract)
    assert len(selected) == 1
    assert selected[0].behavior_slice_id == "slice-orders-read"
