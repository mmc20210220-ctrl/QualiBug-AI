from ai_test_asset_center.business_state_graph import BusinessStateGraph, BusinessStateGraphBuilder, behavior_slice_id
from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator


SCHEMA = """
CREATE TABLE cases (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('CREATED', 'CLOSED', 'ARCHIVED'))
);
"""

API = """
### GET /api/cases/:id
### POST /api/cases/:id/close
"""

RULES = """
# Case lifecycle
CREATED -> CLOSED

Forbidden transitions:
- CLOSED -> CREATED
- ARCHIVED -> CREATED

# Case rules
A closed case must not be reopened.
"""

DEPENDENCY_SCHEMA = """
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);

CREATE TABLE refunds (
  id TEXT PRIMARY KEY,
  order_id TEXT NOT NULL REFERENCES orders(id),
  status TEXT NOT NULL
);
"""

DEPENDENCY_API = """
### GET /api/orders
### GET /api/orders/:id
### POST /api/refunds
"""

DEPENDENCY_WRITE_API = """
### GET /api/orders
### GET /api/orders/:id

### POST /api/refunds

请求：

```json
{"orderId":"<order_id>","amount":100,"reason":"不想要了"}
```
"""

MULTI_DEPENDENCY_SCHEMA = """
CREATE TABLE users (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);

CREATE TABLE addresses (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL
);

CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  address_id TEXT NOT NULL REFERENCES addresses(id),
  status TEXT NOT NULL
);
"""

MULTI_DEPENDENCY_WRITE_API = """
### GET /api/users
### GET /api/addresses

### POST /api/orders

请求：

```json
{"userId":"<user_id>","addressId":"<address_id>","items":[{"sku":"SKU-001","quantity":1}]}
```
"""

INVARIANT_SCHEMA = """
CREATE TABLE orders (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('PENDING_PAYMENT', 'PAID', 'CANCELLED'))
);
"""

INVARIANT_API = """
### GET /api/orders
### GET /api/orders/:id

### POST /api/orders/:id/cancel

取消订单。

### POST /api/payments/pay

请求：

```json
{"orderId":"<order_id>","amount":6899,"channel":"BALANCE","idempotencyKey":"abc-001"}
```
"""

INVARIANT_RULES = """
### 3.2 支付
1. 订单必须处于 `PENDING_PAYMENT` 状态；
1. 同一订单只能成功支付一次；

### 3.3 取消订单
1. 订单处于 `PAID` 状态；
1. 已支付订单不能直接取消，只能发起退款；
"""

COUPON_VALIDATE_API = """
### POST /api/coupons/validate

校验优惠券是否可用。

请求：

```json
{"code":"NEW100","items":[{"sku":"SKU-PHONE-001","qty":1,"price":6999}],"totalAmount":6999}
```
"""

COUPON_SCHEMA = """
CREATE TABLE coupons (
  id TEXT PRIMARY KEY,
  status TEXT NOT NULL CHECK (status IN ('ACTIVE', 'DISABLED'))
);

CREATE TABLE coupon_usage (
  id TEXT PRIMARY KEY,
  coupon_id TEXT NOT NULL REFERENCES coupons(id),
  user_id TEXT NOT NULL
);
"""

COUPON_RULES = """
### 优惠券校验
1. 优惠券必须处于 `ACTIVE` 状态；
"""


def test_behavior_graph_uses_only_entities_and_states_in_current_project_sources():
    graphs = BusinessStateGraphBuilder().build(RULES, API, SCHEMA)

    assert set(graphs) == {"case"}
    assert {"CREATED", "CLOSED", "ARCHIVED"}.issubset(graphs["case"].states)
    assert any(item.is_forbidden and item.from_state == "CLOSED" for item in graphs["case"].transitions)
    assert any("must not be reopened" in rule for node in graphs["case"].states.values() for rule in node.invariants)
    assert "order" not in graphs
    assert "payment" not in graphs


def test_source_scenarios_do_not_invent_admin_or_fallback_routes():
    graphs = BusinessStateGraphBuilder().build(RULES, API, SCHEMA)
    scenarios = SemanticScenarioGenerator().generate(graphs, API)

    assert scenarios
    assert all(item.execution_policy.startswith("plan_only") for item in scenarios)
    assert all(not item.actors for item in scenarios)
    assert all(not item.steps for item in scenarios)
    assert all("admin" not in str(item.to_dict()).lower() for item in scenarios)


def test_dependency_slice_reuses_target_entity_observation_routes():
    builder = BusinessStateGraphBuilder()
    builder.build("", DEPENDENCY_API, DEPENDENCY_SCHEMA)

    dependency_slice = next(item for item in builder.behavior_slices if item.kind == "dependency" and item.entity == "refund")

    assert dependency_slice.endpoints == ["/api/orders", "/api/orders/:id"]
    assert dependency_slice.evidence_gaps == []


def test_dependency_slice_generates_runtime_observation_scenario_when_route_exists():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build("", DEPENDENCY_API, DEPENDENCY_SCHEMA)
    dependency_slice = next(item for item in builder.behavior_slices if item.kind == "dependency" and item.entity == "refund")

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        DEPENDENCY_API,
        active_slice_ids={dependency_slice.slice_id},
        active_slices=[dependency_slice.to_dict()],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_kind == "dependency"
    assert scenario.execution_policy == "safe_read_only"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/orders"


def test_dependency_slice_generates_source_grounded_write_scenario_when_example_exists():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build("", DEPENDENCY_WRITE_API, DEPENDENCY_SCHEMA)
    dependency_slice = next(item for item in builder.behavior_slices if item.kind == "dependency" and item.entity == "refund")

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        DEPENDENCY_WRITE_API,
        active_slice_ids={dependency_slice.slice_id},
        active_slices=[dependency_slice.to_dict()],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_kind == "dependency"
    assert scenario.execution_policy == "approved_sandbox_write"
    assert len(scenario.steps) == 3
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/orders"
    assert scenario.steps[0].extract_from_response == ["id"]
    assert scenario.steps[1].api_method == "POST"
    assert scenario.steps[1].api_path == "/api/refunds"
    assert scenario.steps[1].body_template == {"orderId": "{id}", "amount": 100, "reason": "不想要了"}
    assert scenario.steps[2].api_method == "GET"
    assert scenario.steps[2].api_path == "/api/orders"


def test_dependency_slice_does_not_upgrade_write_when_other_placeholders_remain():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build("", MULTI_DEPENDENCY_WRITE_API, MULTI_DEPENDENCY_SCHEMA)
    dependency_slice = next(item for item in builder.behavior_slices if item.kind == "dependency" and item.entity == "order" and item.endpoints == ["/api/users"])

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        MULTI_DEPENDENCY_WRITE_API,
        active_slice_ids={dependency_slice.slice_id},
        active_slices=[dependency_slice.to_dict()],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_kind == "dependency"
    assert scenario.execution_policy == "safe_read_only"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/users"


def test_invariant_slice_generates_forbidden_cancel_write_scenario():
    graph = BusinessStateGraph("order")
    graph.add_state(
        "PAID",
        invariants=["已支付订单不能直接取消，只能发起退款；"],
        source_refs=[{"source_type": "requirement", "locator": "line:6", "quote": "已支付订单不能直接取消，只能发起退款；"}],
    )
    graphs = {"order": graph}
    slice_id = behavior_slice_id("invariant", "order", "PAID", "已支付订单不能直接取消，只能发起退款；")
    invariant_slice = {
        "slice_id": slice_id,
        "entity": "order",
        "kind": "invariant",
        "states": ["PAID"],
        "endpoints": ["/api/orders", "/api/orders/:id"],
        "priority": 0.55,
        "source_refs": [{"source_type": "requirement", "locator": "line:6", "quote": "已支付订单不能直接取消，只能发起退款；"}],
        "evidence_gaps": [],
    }

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        INVARIANT_API,
        active_slice_ids={slice_id},
        active_slices=[invariant_slice],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_kind == "invariant"
    assert scenario.execution_policy == "approved_sandbox_write"
    assert scenario.category == "state_machine"
    assert scenario.is_forbidden_path is True
    assert len(scenario.steps) == 3
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/orders"
    assert scenario.steps[1].api_method == "POST"
    assert scenario.steps[1].api_path == "/api/orders/{id}/cancel"
    assert scenario.steps[1].expected_status == 409
    assert scenario.steps[2].api_method == "GET"
    assert scenario.steps[2].api_path == "/api/orders"


def test_invariant_slice_does_not_guess_payment_route_outside_slice_binding():
    graph = BusinessStateGraph("order")
    graph.add_state(
        "PENDING_PAYMENT",
        invariants=["同一订单只能成功支付一次；"],
        source_refs=[{"source_type": "requirement", "locator": "line:3", "quote": "同一订单只能成功支付一次；"}],
    )
    graphs = {"order": graph}
    slice_id = behavior_slice_id("invariant", "order", "PENDING_PAYMENT", "同一订单只能成功支付一次；")
    invariant_slice = {
        "slice_id": slice_id,
        "entity": "order",
        "kind": "invariant",
        "states": ["PENDING_PAYMENT"],
        "endpoints": ["/api/orders", "/api/orders/:id"],
        "priority": 0.55,
        "source_refs": [{"source_type": "requirement", "locator": "line:3", "quote": "同一订单只能成功支付一次；"}],
        "evidence_gaps": [],
    }

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        INVARIANT_API,
        active_slice_ids={slice_id},
        active_slices=[invariant_slice],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_kind == "invariant"
    assert scenario.execution_policy == "safe_read_only"
    assert scenario.category == "invariant"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/orders"
    assert all("/api/payments" not in step.api_path for step in scenario.steps)


def test_nearby_requirement_context_does_not_override_slice_route_binding():
    graph = BusinessStateGraph("order")
    source_refs = [
        {"source_type": "requirement", "locator": "line:39", "quote": "订单必须处于 `PENDING_PAYMENT` 状态；"},
        {"source_type": "requirement", "locator": "line:41", "quote": "同一订单只能成功支付一次；"},
        {"source_type": "requirement", "locator": "line:42", "quote": "支付成功后订单状态变为 `PAID`；"},
        {"source_type": "requirement", "locator": "line:48", "quote": "已支付订单不能直接取消，只能发起退款；"},
    ]
    graph.add_state(
        "PENDING_PAYMENT",
        invariants=["订单必须处于 `PENDING_PAYMENT` 状态；"],
        source_refs=source_refs,
    )
    graphs = {"order": graph}
    slice_id = behavior_slice_id("invariant", "order", "PENDING_PAYMENT", "订单必须处于 `PENDING_PAYMENT` 状态；")
    invariant_slice = {
        "slice_id": slice_id,
        "entity": "order",
        "kind": "invariant",
        "states": ["PENDING_PAYMENT"],
        "endpoints": ["/api/orders", "/api/orders/:id"],
        "priority": 0.55,
        "source_refs": source_refs,
        "evidence_gaps": [],
    }

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        INVARIANT_API,
        active_slice_ids={slice_id},
        active_slices=[invariant_slice],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_kind == "invariant"
    assert scenario.execution_policy == "safe_read_only"
    assert scenario.category == "invariant"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/orders"


def test_query_like_post_route_can_back_source_observation_for_coupon_invariants():
    builder = BusinessStateGraphBuilder()
    builder.build(COUPON_RULES, COUPON_VALIDATE_API, COUPON_SCHEMA)

    invariant_slices = [
        item for item in builder.behavior_slices
        if item.kind == "invariant" and item.entity == "coupon"
    ]

    assert invariant_slices
    assert any("/api/coupons/validate" in item.endpoints for item in invariant_slices)
    assert all("OBSERVATION_ROUTE_NOT_SOURCE_BOUND" not in item.evidence_gaps for item in invariant_slices)


def test_coupon_invariant_generates_runtime_validate_scenario_from_source_rule():
    builder = BusinessStateGraphBuilder()
    graphs = builder.build(COUPON_RULES, COUPON_VALIDATE_API, COUPON_SCHEMA)
    invariant_slice = next(
        item for item in builder.behavior_slices
        if item.kind == "invariant" and item.entity == "coupon"
    )

    scenarios = SemanticScenarioGenerator().generate(
        graphs,
        COUPON_VALIDATE_API,
        active_slice_ids={invariant_slice.slice_id},
        active_slices=[invariant_slice.to_dict()],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.execution_policy == "approved_sandbox_write"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "POST"
    assert scenario.steps[0].api_path == "/api/coupons/validate"
    assert "CouponOracle.inactive_coupon_must_be_invalid" in scenario.oracle_rules


def test_coupon_invariant_prefers_category_rule_over_section_wide_expiry_tokens():
    generator = SemanticScenarioGenerator()

    assert generator._coupon_rule_key(
        "coupon",
        "ACTIVE",
        "6. 类目券只能用于指定类目；",
        [
            "1. 优惠券必须在有效期内；",
            "6. 类目券只能用于指定类目；",
        ],
    ) == "coupon_category_scope_must_match"


def test_coupon_disabled_state_falls_back_to_inactive_rule_when_invariant_is_generic():
    generator = SemanticScenarioGenerator()

    assert generator._coupon_rule_key("coupon", "DISABLED", "7. 折扣券必须遵守封顶金额。", [
        "1. 优惠券必须在有效期内；",
        "2. 优惠券状态必须为 ACTIVE；",
    ]) == "inactive_coupon_must_be_invalid"


def test_dependency_slice_can_reuse_parent_coupon_observation_route_for_coupon_usage():
    builder = BusinessStateGraphBuilder()
    builder.build("", COUPON_VALIDATE_API, COUPON_SCHEMA)

    dependency_slices = [
        item for item in builder.behavior_slices
        if item.kind == "dependency" and item.entity == "coupon_usage"
    ]

    assert dependency_slices
    assert any("/api/coupons/validate" in item.endpoints for item in dependency_slices)
    assert all("CROSS_ENTITY_OBSERVATION_CONTRACT_MISSING" not in item.evidence_gaps for item in dependency_slices)


def test_active_slice_fallback_generates_preview_for_state_less_invariant():
    slice_id = "BHV_inventory_unbound"
    scenarios = SemanticScenarioGenerator().generate(
        {},
        "",
        active_slice_ids={slice_id},
        active_slices=[{
            "slice_id": slice_id,
            "entity": "inventory",
            "kind": "invariant",
            "states": [],
            "endpoints": ["/api/products", "/api/products/:sku", "/api/reports/inventory-risk"],
            "priority": 0.55,
            "source_refs": [
                {"source_type": "requirement", "locator": "line:65", "quote": "4. 不允许库存为负；"},
                {"source_type": "database_schema", "locator": "inventory", "quote": "CREATE TABLE inventory"},
            ],
            "evidence_gaps": ["STATE_ANCHOR_NOT_SOURCE_BOUND"],
        }],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_id == slice_id
    assert scenario.behavior_slice_kind == "invariant"
    assert scenario.execution_policy == "safe_read_only"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/products"


def test_active_slice_fallback_preserves_source_observation_contract_slice():
    slice_id = "BHV_cart_source_observation"
    scenarios = SemanticScenarioGenerator().generate(
        {},
        "",
        active_slice_ids={slice_id},
        active_slices=[{
            "slice_id": slice_id,
            "entity": "cart",
            "kind": "source_observation",
            "states": [],
            "endpoints": ["/api/cart/items", "/api/coupons/validate"],
            "priority": 0.3,
            "source_refs": [{"source_type": "api_document", "locator": "line:69", "quote": "查询当前用户购物车。"}],
            "evidence_gaps": [],
        }],
        allow_source_runtime=True,
    )

    assert len(scenarios) == 1
    scenario = scenarios[0]
    assert scenario.behavior_slice_id == slice_id
    assert scenario.behavior_slice_kind == "source_observation"
    assert scenario.execution_policy == "safe_read_only"
    assert len(scenario.steps) == 1
    assert scenario.steps[0].api_method == "GET"
    assert scenario.steps[0].api_path == "/api/cart/items"
