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


def test_runtime_scenario_contract_requires_write_approval_for_write_policy() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    contract = {
        "execution_policy": "approved_sandbox_write",
        "actor": {"id": "customer_qa_lead"},
        "scenarios": [
            {
                "id": "create-order",
                "entity": "orders",
                "steps": [{"method": "POST", "path": "/api/orders", "expected_status": 201}],
                "cleanup_steps": [{"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204}],
            }
        ],
    }

    assert SemanticScenarioGenerator().generate({}, runtime_scenario_contract=contract) == []
    contract["write_approved"] = True
    scenarios = SemanticScenarioGenerator().generate({}, runtime_scenario_contract=contract)
    assert len(scenarios) == 1
    assert scenarios[0].steps[0].api_method == "POST"
    assert scenarios[0].cleanup_steps[0].api_method == "DELETE"


def test_runtime_scenario_contract_requires_cleanup_for_write_policy() -> None:
    from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator

    scenarios = SemanticScenarioGenerator().generate(
        {},
        runtime_scenario_contract={
            "execution_policy": "approved_sandbox_write",
            "write_approved": True,
            "actor": {"id": "customer_qa_lead"},
            "scenarios": [
                {
                    "id": "create-order",
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


def test_runtime_scenario_contract_gate_reports_missing_write_requirements() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import runtime_scenario_contract_gaps

    gaps = runtime_scenario_contract_gaps(
        {
            "test_data_contract": {"strategy": "create_disposable", "write_approved": False},
            "runtime_scenario_contract": {
                "execution_policy": "approved_sandbox_write",
                "actor": {"id": "customer_qa_lead"},
                "scenarios": [
                    {
                        "id": "create-order",
                        "entity": "orders",
                        "steps": [{"method": "POST", "path": "/api/orders", "expected_status": 201}],
                    }
                ],
            },
        }
    )
    codes = {item["code"] for item in gaps}

    assert "WRITE_APPROVAL_MISSING" in codes
    assert "CLEANUP_CONTRACT_MISSING" in codes


def test_runtime_scenario_contract_gate_accepts_approved_write_with_cleanup() -> None:
    from ai_test_asset_center.runtime_scenario_contract_gate import runtime_scenario_contract_gaps

    gaps = runtime_scenario_contract_gaps(
        {
            "test_data_contract": {"strategy": "create_disposable", "write_approved": True},
            "runtime_scenario_contract": {
                "execution_policy": "approved_sandbox_write",
                "actor": {"id": "customer_qa_lead"},
                "scenarios": [
                    {
                        "id": "create-order",
                        "entity": "orders",
                        "steps": [{"method": "POST", "path": "/api/orders", "expected_status": 201}],
                        "cleanup_steps": [{"method": "DELETE", "path": "/api/orders/{id}", "expected_status": 204}],
                    }
                ],
            },
        }
    )

    assert gaps == []
