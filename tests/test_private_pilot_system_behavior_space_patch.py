from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    install_system_behavior_space_patch,
    restore_system_behavior_space_patch,
)
from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator


API_SPEC = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
    post:
      summary: create order
  /api/orders/{id}/refund:
    post:
      summary: refund order
"""

DB_SCHEMA = """
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  status TEXT NOT NULL,
  total_amount DECIMAL(10,2) NOT NULL,
  deleted_at TIMESTAMP,
  updated_at TIMESTAMP
);
"""


def test_private_pilot_builder_contract_carries_system_behavior_space() -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        builder.build(
            "订单状态 CREATED -> PAID。退款成功后订单状态变为 REFUNDED。普通用户只能看自己的订单。",
            API_SPEC,
            DB_SCHEMA,
        )
        contract = builder.behavior_contract()
        space = contract["system_behavior_space"]

        assert space["version"] == "system_behavior_space.v1"
        assert space["summary"]["promise_count"] >= 1
        assert space["summary"]["probe_candidate_count"] >= 1
        assert contract["summary"]["system_behavior_goal"] == "open_ended_system_promise_discovery_across_all_surfaces"
        assert contract["summary"]["system_probe_candidate_count"] == space["summary"]["probe_candidate_count"]
        assert contract["summary"]["system_behavior_materialized_slice_count"] >= 1
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
        assert system_slices
        assert all(item["kind"] == "invariant" for item in system_slices)
        assert any(item.get("_system_behavior_promise_id") for item in system_slices)
        assert any(item.get("source") == "system_behavior_space" for item in contract["coverage_gaps"])
    finally:
        restore_system_behavior_space_patch()


def test_system_behavior_slice_metadata_reaches_scenario_runtime_hints() -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(
            "普通用户只能看自己的订单。金额必须一致。",
            API_SPEC,
            DB_SCHEMA,
        )
        contract = builder.behavior_contract()
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
        assert system_slices

        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            API_SPEC,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        system_scenarios = [item for item in scenarios if item.selection_origin == "system_behavior_space"]

        assert system_scenarios
        scenario = system_scenarios[0].to_dict()
        assert scenario["category"] == "system_promise"
        assert scenario["behavior_slice_kind"] == "system_promise"
        assert "SystemPromiseOracle.open_ended_promise_violation" in scenario["oracle_rules"]
        assert scenario["runtime_hints"]["system_behavior_space"]["promise_id"]
        assert scenario["runtime_hints"]["system_behavior_space"]["surface_plan"]
    finally:
        restore_system_behavior_space_patch()
