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


def test_core_behavior_space_model_extracts_bug_discovery_inputs() -> None:
    from ai_test_asset_center.behavior_space_model import build_behavior_space_model

    model = build_behavior_space_model(PRD_TEXT, API_TEXT, DB_SCHEMA)

    assert model["schema_version"] == "behavior-space-model-v1"
    assert model["purpose"] == "core_bug_discovery_model"
    assert model["customer_safe"] is True
    assert model["coverage_model"]["actor_count"] >= 2
    assert model["coverage_model"]["entity_count"] >= 1
    assert model["coverage_model"]["api_operation_count"] >= 4
    assert model["coverage_model"]["permission_rule_count"] >= 2
    assert model["coverage_model"]["data_constraint_count"] >= 4
    assert model["coverage_model"]["risk_point_count"] >= 3


def test_core_behavior_space_model_contains_permissions_and_constraints() -> None:
    from ai_test_asset_center.behavior_space_model import build_behavior_space_model

    model = build_behavior_space_model(PRD_TEXT, API_TEXT, DB_SCHEMA)
    permission_rules = "\n".join(item["rule"] for item in model["permissions"])
    data_rules = "\n".join(item["rule"] for item in model["data_constraints"])

    assert "other tenant order" in permission_rules
    assert "only read own order" in permission_rules
    assert "CHECK" in data_rules
    assert "REFERENCES orders" in data_rules


def test_core_behavior_space_model_prioritizes_bug_risk_points() -> None:
    from ai_test_asset_center.behavior_space_model import build_behavior_space_model

    model = build_behavior_space_model(PRD_TEXT, API_TEXT, DB_SCHEMA)
    risk_types = {item["risk_type"] for item in model["risk_points"]}
    oracle_types = {item["oracle_needed"] for item in model["risk_points"]}
    titles = "\n".join(item["title"] for item in model["risk_points"])

    assert "permission" in risk_types
    assert "money" in risk_types
    assert "state" in risk_types or "business_rule" in risk_types
    assert "permission_oracle" in oracle_types
    assert "business_invariant_oracle" in oracle_types or "state_transition_oracle" in oracle_types
    assert "unpaid order cannot be refunded" in titles


def test_core_behavior_space_model_keeps_gaps_when_sources_are_incomplete() -> None:
    from ai_test_asset_center.behavior_space_model import build_behavior_space_model

    model = build_behavior_space_model("# order\nCREATED -> PAID", API_TEXT, "")
    gap_codes = {item.get("code") for item in model["coverage_gaps"] if isinstance(item, dict)}

    assert "ACTOR_MODEL_MISSING" in gap_codes
    assert "DATA_CONSTRAINTS_MISSING" in gap_codes
