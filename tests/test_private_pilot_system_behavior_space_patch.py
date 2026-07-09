import json

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


def _first_system_promise_scenario():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build("普通用户只能看自己的订单。金额必须一致。", API_SPEC, DB_SCHEMA)
    contract = builder.behavior_contract()
    system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        API_SPEC,
        active_slices=system_slices,
        allow_source_runtime=True,
    )
    return next(item for item in scenarios if item.selection_origin == "system_behavior_space")


def _first_money_system_promise_scenario():
    """Return the first system promise scenario whose dimensions include money-related terms."""
    builder = BusinessStateGraphBuilder()
    graphs = builder.build("普通用户只能看自己的订单。金额必须一致。", API_SPEC, DB_SCHEMA)
    contract = builder.behavior_contract()
    system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        API_SPEC,
        active_slices=system_slices,
        allow_source_runtime=True,
    )
    money_dims = {"money", "quantity", "conservation", "data_consistency"}
    for item in scenarios:
        if item.selection_origin != "system_behavior_space":
            continue
        hints = item.runtime_hints.get("system_behavior_space", {}) if hasattr(item, "runtime_hints") and isinstance(item.runtime_hints, dict) else {}
        dims = {str(d).lower() for d in hints.get("dimensions", [])}
        if dims.intersection(money_dims):
            return item
    raise AssertionError("No system promise scenario with money-related dimensions found")


def _make_system_promise_finding(tmp_path):
    from ai_test_asset_center import v12_pipeline
    from ai_test_asset_center.oracle_engine import EvidenceGraphBuilder, OracleEngine

    # Use a money-related scenario so the oracle detects negative-value violations
    # and the regression contract carries money dimensions for learning.
    scenario_obj = _first_money_system_promise_scenario()
    scenario = scenario_obj.to_dict()
    trace = {
        "steps": [
            {
                "action": "observe_system_promise_surface",
                "method": "GET",
                "path": "/api/orders",
                "status": 200,
                "expected_status": 200,
                "response": {"status_code": 200, "body": {"items": [{"id": 1, "total_amount": -1}]}},
            }
        ]
    }
    system_result = next(item for item in OracleEngine().evaluate(scenario, trace, None) if item.oracle_name == "SystemPromiseOracle")
    evidence = EvidenceGraphBuilder().build(scenario, trace, None, [system_result])
    finding = v12_pipeline._confirmed_oracle_finding(
        scenario_obj,
        trace,
        system_result,
        evidence,
        campaign_id="campaign-1",
        discovery_round=1,
        base_url="http://example.test",
    )
    saved = v12_pipeline._persist_confirmed_findings(tmp_path, "proj", [finding])
    assert saved == 1
    return finding


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
        # Priority 1: all 7 required _system_behavior_* fields must be present on every slice
        for item in system_slices:
            assert item.get("_system_behavior_promise_id"), f"slice missing _system_behavior_promise_id: {item.get('slice_id')}"
            assert item.get("_system_behavior_dimensions") and isinstance(item["_system_behavior_dimensions"], list), f"slice missing _system_behavior_dimensions: {item.get('slice_id')}"
            assert item.get("_system_behavior_surface_plan") and isinstance(item["_system_behavior_surface_plan"], list), f"slice missing _system_behavior_surface_plan: {item.get('slice_id')}"
            assert isinstance(item.get("_system_behavior_api_routes"), list), f"slice missing _system_behavior_api_routes: {item.get('slice_id')}"
            assert item.get("_system_behavior_required_assets") and isinstance(item["_system_behavior_required_assets"], list), f"slice missing _system_behavior_required_assets: {item.get('slice_id')}"
            assert item.get("_selection_family"), f"slice missing _selection_family: {item.get('slice_id')}"
            assert item.get("_system_behavior_probe_id"), f"slice missing _system_behavior_probe_id: {item.get('slice_id')}"
        assert any(item.get("source") == "system_behavior_space" for item in contract["coverage_gaps"])
    finally:
        restore_system_behavior_space_patch()


def test_system_behavior_slice_metadata_reaches_scenario_runtime_hints() -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        scenario = _first_system_promise_scenario().to_dict()

        assert scenario["category"] == "system_promise"
        assert scenario["behavior_slice_kind"] == "system_promise"
        assert "SystemPromiseOracle.open_ended_promise_violation" in scenario["oracle_rules"]

        hints = scenario["runtime_hints"]["system_behavior_space"]
        # Priority 1: all 7 required structured metadata fields must be present
        assert hints["promise_id"], "promise_id missing"
        assert isinstance(hints.get("dimensions"), list), "dimensions missing or not list"
        assert isinstance(hints.get("surface_plan"), list), "surface_plan missing or not list"
        assert isinstance(hints.get("api_routes"), list), "api_routes missing or not list"
        # api_routes entries, when present, must have method and path keys
        for route in hints["api_routes"]:
            assert isinstance(route, dict), f"api_route not dict: {route}"
            assert "method" in route, f"api_route missing method: {route}"
            assert "path" in route, f"api_route missing path: {route}"
        assert isinstance(hints.get("required_assets"), list), "required_assets missing or not list"
        assert hints["source_slice_id"], "source_slice_id missing"
        assert hints["source_family"], "source_family missing"
    finally:
        restore_system_behavior_space_patch()


def test_system_promise_scenario_does_not_invent_safe_get_for_write_only_route() -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(
            "退款金额必须守恒。",
            """
openapi: 3.0.0
paths:
  /api/refunds:
    post:
      summary: create refund
""",
            "CREATE TABLE refunds (id INTEGER, refund_amount DECIMAL(10,2));",
        )
        contract = builder.behavior_contract()
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
        assert system_slices
        assert any(route.get("method") == "POST" for item in system_slices for route in item.get("_system_behavior_api_routes", []))

        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            API_SPEC,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        scenario = next(item for item in scenarios if item.selection_origin == "system_behavior_space").to_dict()

        assert scenario["execution_policy"] == "plan_only_requires_fixture"
        assert scenario["steps"] == []
        assert scenario["runtime_hints"]["system_promise_execution_guard"] == "plan_only_no_source_bound_safe_read_route"
        assert "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" in scenario["evidence_gaps"]
    finally:
        restore_system_behavior_space_patch()


def test_system_promise_oracle_links_dimension_violation_to_evidence() -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        from ai_test_asset_center.oracle_engine import EvidenceGraphBuilder, OracleEngine

        # Use a money-related scenario so the oracle's dimension check triggers
        # the negative-value detection path.
        scenario = _first_money_system_promise_scenario().to_dict()
        trace = {
            "steps": [
                {
                    "method": "GET",
                    "path": "/api/orders",
                    "expected_status": 200,
                    "response": {"status_code": 200, "body": {"items": [{"id": 1, "total_amount": -1}]}},
                }
            ]
        }

        results = OracleEngine().evaluate(scenario, trace, None)
        system_result = next(item for item in results if item.oracle_name == "SystemPromiseOracle")
        assert not system_result.passed
        assert system_result.violated_rule.startswith("system_promise_")

        evidence = EvidenceGraphBuilder().build(scenario, trace, None, [system_result]).to_dict()
        assert evidence["scenario"]["system_promise_id"] == scenario["runtime_hints"]["system_behavior_space"]["promise_id"]
        assert evidence["scenario"]["system_behavior_space_evidence"]["dimensions"]
    finally:
        restore_system_behavior_space_patch()


def test_system_promise_finding_and_regression_ledger_keep_contract(tmp_path) -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        finding = _make_system_promise_finding(tmp_path)

        assert finding["system_promise_id"]
        assert finding["regression_contract"]["contract_type"] == "system_behavior_promise_regression"
        assert finding["raw_evidence"]["system_behavior_space"]["dimensions"]

        ledger_path = tmp_path / "platform_workspace" / "proj" / "defect_discovery" / "confirmed_findings.json"
        ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
        row = ledger[finding["evidence_id"]]
        assert row["system_promise_id"] == finding["system_promise_id"]
        assert row["regression_contract"]["system_behavior_space"]["promise_id"] == finding["system_promise_id"]
        assert row["system_behavior_dimensions"]
    finally:
        restore_system_behavior_space_patch()


def test_system_promise_contract_reaches_regression_suite_runner_and_history(tmp_path) -> None:
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        from ai_test_asset_center import regression_runner, regression_suite_builder

        finding = _make_system_promise_finding(tmp_path)
        probes = regression_suite_builder._load_confirmed_findings_regression_probes("proj", tmp_path)
        probe = next(item for item in probes if item.get("confirmed_evidence_id") == finding["evidence_id"])

        assert probe["system_promise_id"] == finding["system_promise_id"]
        assert probe["regression_contract"]["contract_type"] == "system_behavior_promise_regression"

        item = regression_runner._judge_probe(probe, {"reachable": True, "status_code": 200, "body_excerpt": "ok", "error": ""})
        assert item["system_promise_id"] == finding["system_promise_id"]
        assert item["regression_contract_type"] == "system_behavior_promise_regression"

        reverification = regression_runner._reverify_confirmed_findings(
            "proj",
            tmp_path,
            {"base_url": ""},
            {},
            0.1,
            True,
        )
        verdict = next(v for v in reverification["verdicts"] if v["evidence_id"] == finding["evidence_id"])
        assert verdict["system_promise_id"] == finding["system_promise_id"]
        assert reverification["system_promise_reverification_count"] >= 1

        result_payload = {
            "summary": {"generated_at": "now", "suite_mode": "release", "suite_mode_label": "Release"},
            "ci_feedback": {"gate_status": "manual_approval_required", "ci_message": "review"},
            "items": [item],
        }
        history = regression_runner._append_regression_history("proj", tmp_path, result_payload)
        history_item = next(row for row in history[-1]["items"] if row.get("system_promise_id") == finding["system_promise_id"])

        assert history_item["regression_contract_type"] == "system_behavior_promise_regression"
        assert result_payload["risk_clue_pool_learning_refresh"]["status"] == "refreshed"
        assert history[-1]["risk_clue_pool_learning_refresh"]["project_system_promise_signal_count"] >= 1

        pool_path = tmp_path / "platform_outputs" / "proj" / "risk_clue_pool" / "risk_clues.json"
        pool = json.loads(pool_path.read_text(encoding="utf-8"))
        assert pool["project_learning"]["system_promise_signal_count"] >= 1
        assert pool["project_learning"]["priority_weights"]["money_quantity_conservation"] > 0
    finally:
        restore_system_behavior_space_patch()
