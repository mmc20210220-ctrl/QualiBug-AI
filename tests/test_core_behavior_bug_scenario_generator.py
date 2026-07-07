from __future__ import annotations


PRD_TEXT = """
# order lifecycle
角色: buyer, admin
CREATED -> PAID when buyer pays the order
PAID -> REFUNDED when buyer requests refund
buyer can only read own order
buyer cannot read other tenant order
refund_amount must be less than or equal to paid_amount
unpaid order cannot be refunded

# forbidden order transitions
PAID -> CREATED is forbidden
DELETED -> PAID is forbidden
""".strip()


API_TEXT = """
openapi: 3.0.0
info:
  title: Order API
  version: 1.0.0
paths:
  /api/orders/{orderId}:
    get:
      responses:
        '200': {description: ok}
    delete:
      responses:
        '204': {description: deleted}
  /api/orders/{orderId}/pay:
    post:
      responses:
        '201': {description: paid}
  /api/refunds:
    post:
      responses:
        '201': {description: created}
""".strip()


DB_SCHEMA = """
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('CREATED','PAID','REFUNDED','DELETED')),
  amount_cents INTEGER NOT NULL CHECK (amount_cents >= 0)
);
CREATE TABLE refunds (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(id),
  refund_amount_cents INTEGER NOT NULL CHECK (refund_amount_cents >= 0)
);
""".strip()


def _model() -> dict:
    from ai_test_asset_center.behavior_space_model import build_behavior_space_model

    return build_behavior_space_model(PRD_TEXT, API_TEXT, DB_SCHEMA)


def test_bug_scenario_generator_creates_core_bug_hunting_scenarios() -> None:
    from ai_test_asset_center.behavior_bug_scenario_generator import generate_bug_hunting_scenarios

    result = generate_bug_hunting_scenarios(_model())
    categories = {item["category"] for item in result["scenarios"]}

    assert result["schema_version"] == "behavior-bug-scenarios-v1"
    assert result["purpose"] == "core_bug_discovery_scenarios"
    assert result["customer_safe"] is True
    assert result["scenario_count"] >= 5
    assert "permission_boundary" in categories
    assert "business_invariant" in categories
    assert "data_constraint" in categories
    assert result["coverage_summary"]["requires_write_approval"] >= 1


def test_bug_scenarios_keep_oracles_and_evidence_contract() -> None:
    from ai_test_asset_center.behavior_bug_scenario_generator import generate_bug_hunting_scenarios

    result = generate_bug_hunting_scenarios(_model())
    oracle_types = {item["oracle_needed"] for item in result["scenarios"]}

    assert "permission_oracle" in oracle_types
    assert "business_invariant_oracle" in oracle_types or "state_transition_oracle" in oracle_types
    assert "data_consistency_oracle" in oracle_types
    for item in result["scenarios"]:
        assert item["scenario_id"].startswith("BUGSCN_")
        assert item["evidence_to_collect"]
        assert "oracle verdict and source_refs used for judgment" in item["evidence_to_collect"]
        assert item["source_refs"] or item["category"] == "data_constraint"


def test_bug_scenarios_include_cross_tenant_and_money_probes() -> None:
    from ai_test_asset_center.behavior_bug_scenario_generator import generate_bug_hunting_scenarios

    result = generate_bug_hunting_scenarios(_model())
    titles = "\n".join(item["title"] for item in result["scenarios"])
    tags = {tag for item in result["scenarios"] for tag in item["coverage_tags"]}

    assert "other tenant order" in titles
    assert "refund_amount" in titles or "unpaid order cannot be refunded" in titles
    assert "tenant_boundary" in tags
    assert "money" in tags
    assert "negative_path" in tags


def test_bug_scenarios_mark_write_paths_as_approval_required() -> None:
    from ai_test_asset_center.behavior_bug_scenario_generator import generate_bug_hunting_scenarios

    result = generate_bug_hunting_scenarios(_model())
    write_scenarios = [item for item in result["scenarios"] if item["execution_policy"] == "approved_sandbox_write_required"]

    assert write_scenarios
    for item in write_scenarios:
        assert "WRITE_APPROVAL_AND_CLEANUP_CONTRACT_REQUIRED" in item["evidence_gaps"]
        assert any(step["method"] in {"POST", "PUT", "PATCH", "DELETE"} for step in item["steps"])


def test_bug_scenarios_report_gaps_without_api_operations() -> None:
    from ai_test_asset_center.behavior_bug_scenario_generator import generate_bug_hunting_scenarios

    model = _model()
    model["api_operations"] = []
    result = generate_bug_hunting_scenarios(model)
    gap_codes = {item["code"] for item in result["coverage_gaps"]}

    assert "API_OPERATIONS_MISSING" in gap_codes
    assert result["coverage_summary"]["plan_only"] >= 1
