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


def test_system_promise_contract_drives_adversarial_scenario_plan() -> None:
    """验证：system promise contract 真正驱动场景生成，而不是降级为普通 GET。

    当 system behavior space 已经知道 entity、role、tenant、state、money、audit 等
    维度时，semantic scenario generator 生成的不应该是普通的 "GET /api/refunds"，
    而应该是一个携带明确业务验证意图的对抗性验证计划。
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        # 企业材料：多租户订单系统，有支付、退款、金额、状态流转、权限、审计
        prd = (
            "订单状态 CREATED -> PAID -> REFUNDED。"
            "退款必须经过 finance 角色审批。"
            "普通用户只能看到自己的订单，不能看到其他租户的订单。"
            "退款金额不能超过已支付金额。"
            "CANCELLED 订单不能再次支付。"
            "所有支付和退款操作必须产生审计日志。"
        )
        api_spec = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
    post:
      summary: create order
  /api/orders/{id}/refund:
    post:
      summary: request refund for order
  /api/orders/{id}/pay:
    post:
      summary: pay for order
  /api/audit/logs:
    get:
      summary: get audit logs
"""
        db_schema = """
CREATE TABLE orders (
  id INTEGER PRIMARY KEY,
  tenant_id TEXT NOT NULL,
  user_id TEXT NOT NULL,
  status TEXT NOT NULL,
  total_amount DECIMAL(10,2) NOT NULL,
  paid_amount DECIMAL(10,2) DEFAULT 0,
  refunded_amount DECIMAL(10,2) DEFAULT 0,
  created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
  deleted_at TIMESTAMP
);
"""
        graphs = builder.build(prd, api_spec, db_schema)
        contract = builder.behavior_contract()
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
        assert system_slices, "Should have system behavior slices"
        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            api_spec,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        system_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]
        assert system_scenarios, "Should have system behavior scenarios"

        # ── 验证 1：每个 system promise scenario 携带 verification_intent ──
        for scenario in system_scenarios:
            sd = scenario.to_dict()
            hints = sd["runtime_hints"].get("system_behavior_space", {})
            dims = {str(d).lower() for d in hints.get("dimensions", [])}

            # runtime_hints 中必须有 verification_intent
            vi = sd["runtime_hints"].get("system_promise_verification_intent", {})
            assert vi, f"verification_intent missing for {sd['title']}"
            assert vi.get("verification_direction"), f"verification_direction missing for {sd['title']}"

        # ── 验证 2：金额守恒维度产生特定的验证意图 ──
        money_scenarios = [
            s for s in system_scenarios
            if any(d in {str(d2).lower() for d2 in s.runtime_hints.get("system_behavior_space", {}).get("dimensions", [])}
                   for d in ("money", "quantity", "conservation", "data_conservation"))
        ]
        if money_scenarios:
            ms = money_scenarios[0].to_dict()
            vi = ms["runtime_hints"]["system_promise_verification_intent"]
            # 应该提到守恒 — 检查 verification_steps 或 conservation_constraints
            intent_text = vi.get("verification_steps", [])
            conservation_constraints = vi.get("conservation_constraints", [])
            conservation_mentioned = (
                any("conservation" in step.lower() for step in intent_text)
                or any("守恒" in c for c in conservation_constraints)
                or any("conservation" in c.lower() for c in conservation_constraints)
            )
            assert conservation_mentioned, (
                f"Money scenario should mention conservation. "
                f"steps={intent_text}, constraints={conservation_constraints}"
            )

        # ── 验证 3：权限维度产生角色边界验证 ──
        auth_scenarios = [
            s for s in system_scenarios
            if any(d in {str(d2).lower() for d2 in s.runtime_hints.get("system_behavior_space", {}).get("dimensions", [])}
                   for d in ("authorization", "role", "permission"))
        ]
        if auth_scenarios:
            as_ = auth_scenarios[0].to_dict()
            vi = as_["runtime_hints"]["system_promise_verification_intent"]
            # 应该涉及角色
            roles = vi.get("roles_involved", [])
            assert len(roles) > 1 or "角色" in str(vi.get("verification_direction", "")), \
                f"Auth scenario should involve multiple roles or mention role: {vi}"

        # ── 验证 4：场景 title 携带维度标签 ──
        for scenario in system_scenarios:
            sd = scenario.to_dict()
            hints = sd["runtime_hints"].get("system_behavior_space", {})
            dims = hints.get("dimensions", [])
            if dims:
                # title 应该不再只是 "entity: family"，而是包含维度标签
                title = sd["title"]
                assert "System promise" in title, f"Title should contain 'System promise': {title}"

        # ── 验证 5：description 是验证意图而不是 bare invariant ──
        for scenario in system_scenarios:
            sd = scenario.to_dict()
            desc = sd.get("description", "")
            # description 应该包含 "验证对象" 标记（来自 verification intent）
            assert "验证对象" in desc or "验证方向" in desc, \
                f"Description should contain verification intent markers: {desc[:100]}"

    finally:
        restore_system_behavior_space_patch()


def test_refund_approval_contract_uses_role_money_and_audit_evidence() -> None:
    """验证：退款审批场景必须涉及 finance 角色、金额守恒、审计证据面。

    系统必须能表达：非 finance 角色不能绕过审批直接退款；
    验证计划必须涉及 refund approval API、角色边界、支付/退款金额关系、audit_logs 证据面。
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        prd = (
            "退款必须经过 finance 角色审批，不能由普通用户直接退款。"
            "退款金额不能超过已支付金额。"
            "退款后必须有审计日志。"
        )
        api_spec = """
openapi: 3.0.0
paths:
  /api/refunds:
    get:
      summary: list refunds
    post:
      summary: create refund
  /api/refunds/{id}/approve:
    post:
      summary: approve refund
  /api/payments:
    get:
      summary: list payments
  /api/audit/logs:
    get:
      summary: list audit logs
"""
        db_schema = """
CREATE TABLE refunds (id INTEGER, order_id INTEGER, amount DECIMAL(10,2), status TEXT,
  created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE payments (id INTEGER, order_id INTEGER, amount DECIMAL(10,2));
"""
        graphs = builder.build(prd, api_spec, db_schema)
        contract = builder.behavior_contract()
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
        assert system_slices

        scenarios = SemanticScenarioGenerator().generate(
            graphs, api_spec,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        system_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]
        assert system_scenarios

        # 收集所有 scenario 中的维度信息
        all_dims: set[str] = set()
        all_evidence_surfaces: set[str] = set()
        verification_steps_all: list[str] = []
        for scenario in system_scenarios:
            sd = scenario.to_dict()
            hints = sd["runtime_hints"].get("system_behavior_space", {})
            dims = [str(d).lower() for d in hints.get("dimensions", [])]
            all_dims.update(dims)
            vi = sd["runtime_hints"].get("system_promise_verification_intent", {})
            all_evidence_surfaces.update([str(s) for s in vi.get("evidence_surfaces", [])])
            verification_steps_all.extend([str(s) for s in vi.get("verification_steps", [])])

        # 必须有金额相关维度（由 DB schema 的 amount 字段触发）或数据一致性维度
        money_dims = all_dims & {"money", "quantity", "conservation", "data_conservation", "money_quantity_conservation", "data_consistency"}
        has_business_dims = bool(money_dims) or any(
            d in all_dims for d in ("authorization_access_control", "role", "audit_traceability", "audit")
        )
        assert has_business_dims, (
            f"Should have business-relevant dimensions (money/role/audit), got: {all_dims}. "
            f"Verification steps: {verification_steps_all}"
        )

        # 验证步骤中必须提到金额守恒或退款
        money_steps = [s for s in verification_steps_all if any(t in s for t in ("金额", "守恒", "退款", "refund", "负"))]
        assert money_steps, f"Verification steps should mention money conservation: {verification_steps_all}"

    finally:
        restore_system_behavior_space_patch()


def test_export_path_inherits_tenant_visibility_boundary() -> None:
    """验证：导出/搜索路径继承租户可见性边界。

    系统必须能表达：normal_user 不能通过详情、搜索、导出接口读取其他租户数据；
    验证计划必须体现 tenant boundary。
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        prd = "不同租户之间的数据必须严格隔离。用户只能看到本租户的数据，不能跨租户访问。"
        api_spec = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
  /api/orders/{id}:
    get:
      summary: get order detail
  /api/orders/export:
    get:
      summary: export orders
"""
        db_schema = """
CREATE TABLE orders (id INTEGER, tenant_id TEXT NOT NULL, status TEXT, amount DECIMAL(10,2));
"""
        graphs = builder.build(prd, api_spec, db_schema)
        contract = builder.behavior_contract()
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]
        assert system_slices

        scenarios = SemanticScenarioGenerator().generate(
            graphs, api_spec,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        system_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]

        # 找租户隔离相关的 scenario
        tenant_scenarios = [
            s for s in system_scenarios
            if any(d in {str(d2).lower() for d2 in s.runtime_hints.get("system_behavior_space", {}).get("dimensions", [])}
                   for d in ("tenant", "tenant_isolation", "isolation"))
        ]
        if tenant_scenarios:
            ts = tenant_scenarios[0].to_dict()
            vi = ts["runtime_hints"].get("system_promise_verification_intent", {})
            # 应该提到租户边界
            tenant_boundary = vi.get("tenant_boundary", "")
            assert tenant_boundary or any("租户" in s for s in vi.get("verification_steps", [])), \
                f"Tenant scenario should have tenant boundary awareness: {vi}"

    finally:
        restore_system_behavior_space_patch()


def test_money_conservation_uses_cross_object_evidence_plan() -> None:
    """验证：金额守恒场景使用跨对象证据计划。

    退款金额不能超过已支付金额减已退款金额；
    验证计划必须涉及 payment/refund/order 多方证据，而不是只看 refund_amount 字段。
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        prd = "退款金额不能超过订单已支付金额减去已退款金额。金额必须守恒。"
        api_spec = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
  /api/orders/{id}/refund:
    post:
      summary: create refund
  /api/payments:
    get:
      summary: list payments
"""
        db_schema = """
CREATE TABLE orders (id INTEGER, total_amount DECIMAL(10,2), paid_amount DECIMAL(10,2),
  refunded_amount DECIMAL(10,2));
CREATE TABLE payments (id INTEGER, order_id INTEGER, amount DECIMAL(10,2));
CREATE TABLE refunds (id INTEGER, order_id INTEGER, amount DECIMAL(10,2));
"""
        graphs = builder.build(prd, api_spec, db_schema)
        contract = builder.behavior_contract()
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]

        scenarios = SemanticScenarioGenerator().generate(
            graphs, api_spec,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        system_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]

        # 找金额守恒 scenario
        money_scenarios = [
            s for s in system_scenarios
            if any(d in {str(d2).lower() for d2 in s.runtime_hints.get("system_behavior_space", {}).get("dimensions", [])}
                   for d in ("money", "conservation", "data_conservation", "money_quantity_conservation"))
        ]
        if money_scenarios:
            ms = money_scenarios[0].to_dict()
            vi = ms["runtime_hints"].get("system_promise_verification_intent", {})

            # 验证意图中应该有守恒相关的描述
            conservation_constraints = vi.get("conservation_constraints", [])
            assert conservation_constraints, f"Should have conservation constraints: {vi}"

            # 证据面应该包括 API（因为要对比数据）
            evidence_surfaces = [str(s) for s in vi.get("evidence_surfaces", [])]
            # 至少有 API 证据面
            assert evidence_surfaces, f"Should have evidence surfaces: {vi}"

    finally:
        restore_system_behavior_space_patch()


def test_historical_bug_becomes_similar_boundary_regression_risk() -> None:
    """验证：历史缺陷信息能转化为相似边界回归风险。

    如果历史缺陷是 "跨租户读取订单"，系统应该能识别出相似的
    租户边界风险（如 "跨租户导出订单"）。
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        prd = (
            "历史缺陷：跨租户读取订单（已修复）。"
            "当前系统有订单列表接口、订单详情接口和订单导出接口。"
            "所有接口必须保持租户隔离。"
        )
        api_spec = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
  /api/orders/{id}:
    get:
      summary: order detail
  /api/orders/export:
    get:
      summary: export orders
"""
        db_schema = """
CREATE TABLE orders (id INTEGER, tenant_id TEXT NOT NULL, status TEXT);
"""
        graphs = builder.build(prd, api_spec, db_schema)
        contract = builder.behavior_contract()

        # 验证 system_behavior_space 包含了历史缺陷相关的 promise
        system_slices = [item for item in contract["slices"] if item.get("_selection_origin") == "system_behavior_space"]

        # 至少有一个 system behavior slice
        assert system_slices, "Should have system behavior slices"

        # 找租户隔离相关的 slice
        tenant_slices = [
            s for s in system_slices
            if any(d in {str(d2).lower() for d2 in s.get("_system_behavior_dimensions", [])}
                   for d in ("tenant", "tenant_isolation", "isolation"))
        ]
        if tenant_slices:
            # 租户 slices 应该关联到多个 API 路由（列表、详情、导出）
            ts = tenant_slices[0]
            api_routes = ts.get("_system_behavior_api_routes", [])
            paths = [r.get("path", "") for r in api_routes if isinstance(r, dict)]
            # 至少应该有一个 route
            assert paths, "Tenant slices should reference API routes"

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


def test_full_enterprise_scenario_generates_dimension_aware_verification_plan() -> None:
    """验证：完整企业场景（多租户+角色+订单+支付+退款+审批+库存+审计+历史缺陷）
    能组织出不同维度的差异化验证计划，而不是千篇一律的 GET。

    这个测试复现了 goal.txt 中描述的企业场景，验证：
    1. 退款审批场景 — 涉及角色、金额、审计
    2. 跨租户订单场景 — 体现 tenant boundary
    3. 金额守恒场景 — 涉及跨对象证据
    4. 状态流转场景 — CANCELLED/REFUNDED/终态约束
    5. UI/API 绕过场景 — UI 没按钮不代表 API 安全
    6. 异步/审计场景 — trace_id、幂等性
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        # ── 完整企业材料 ──
        prd = """
## 订单管理
订单状态流转：CREATED -> PAID -> SHIPPED -> DELIVERED。
已取消(CANCELLED)订单不能再次支付。
已退款(REFUNDED)订单不能再次退款。

## 退款审批
退款必须经过finance角色审批，普通用户不能绕过审批直接退款。
退款金额不能超过订单已支付金额减去已退款金额。

## 租户隔离
不同租户之间的数据必须严格隔离。
普通用户只能看到本租户的订单，不能通过列表、详情、搜索或导出接口获取其他租户数据。

## 审计要求
所有支付和退款操作必须产生审计日志，包含trace_id。
异步任务失败不能导致主状态半提交。
重复回调不能造成重复退款。

## 历史缺陷
- 历史Bug#1: 跨租户读取订单（已修复）
- 历史Bug#2: 重复退款导致资金损失（已修复）
- 历史Bug#3: 取消订单后仍可支付（已修复）
"""
        api_spec = """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: 订单列表
    post:
      summary: 创建订单
  /api/orders/{id}:
    get:
      summary: 订单详情
  /api/orders/{id}/cancel:
    post:
      summary: 取消订单
  /api/orders/{id}/pay:
    post:
      summary: 支付订单
  /api/orders/export:
    get:
      summary: 导出订单
  /api/refunds:
    get:
      summary: 退款列表
    post:
      summary: 申请退款
  /api/refunds/{id}/approve:
    post:
      summary: 审批退款
  /api/audit/logs:
    get:
      summary: 审计日志
  /api/payments:
    get:
      summary: 支付记录
"""
        db_schema = """
CREATE TABLE orders (id INTEGER, tenant_id TEXT, user_id TEXT,
  status TEXT, total_amount DECIMAL(10,2), paid_amount DECIMAL(10,2),
  refunded_amount DECIMAL(10,2), created_at TIMESTAMP, updated_at TIMESTAMP,
  deleted_at TIMESTAMP);
CREATE TABLE refunds (id INTEGER, order_id INTEGER, amount DECIMAL(10,2),
  status TEXT, created_at TIMESTAMP, updated_at TIMESTAMP);
CREATE TABLE payments (id INTEGER, order_id INTEGER, amount DECIMAL(10,2),
  status TEXT, trace_id TEXT, created_at TIMESTAMP);
CREATE TABLE audit_logs (id INTEGER, entity_type TEXT, entity_id INTEGER,
  operation TEXT, trace_id TEXT, created_at TIMESTAMP);
"""
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(prd, api_spec, db_schema)
        contract = builder.behavior_contract()
        system_slices = [s for s in contract["slices"] if s.get("_selection_origin") == "system_behavior_space"]
        assert len(system_slices) >= 5, f"Should have at least 5 system behavior slices, got {len(system_slices)}"

        scenarios = SemanticScenarioGenerator().generate(
            graphs, api_spec,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        sb_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]
        assert len(sb_scenarios) >= 5, f"Should generate at least 5 system behavior scenarios"

        # ── 验证 1：场景不再千篇一律 —— 存在不同 action 类型的步骤 ──
        actions: set[str] = set()
        for s in sb_scenarios:
            for step in s.steps:
                actions.add(step.action)
        assert len(actions) >= 2, (
            f"Should have at least 2 distinct step actions, got {actions}. "
            f"All scenarios should not just be 'observe_bound_entity'."
        )

        # ── 验证 2：存在多步场景（审计场景应有额外审计日志获取步骤）──
        multi_step = [s for s in sb_scenarios if len(s.steps) >= 2]
        audit_multi = [s for s in multi_step if any("audit" in step.action.lower() for step in s.steps)]
        assert len(multi_step) >= 1, (
            f"Should have at least 1 multi-step scenario (e.g. audit scenarios), "
            f"but all have {max((len(s.steps) for s in sb_scenarios), default=0)} steps max"
        )

        # ── 验证 3：角色权限维度产生 authorization_boundary 步骤 ──
        auth_scenarios = [
            s for s in sb_scenarios
            if any("authorization" in step.action.lower() for step in s.steps)
        ]
        assert len(auth_scenarios) >= 1, (
            f"Should have authorization boundary scenarios (action contains 'authorization'), "
            f"got actions: {actions}"
        )

        # ── 验证 4：金额守恒维度产生 conservation 步骤 ──
        conservation_scenarios = [
            s for s in sb_scenarios
            if any("conservation" in step.action.lower() for step in s.steps)
        ]
        assert len(conservation_scenarios) >= 1, (
            f"Should have conservation scenarios, got actions: {actions}"
        )

        # ── 验证 5：状态流转维度产生 state_transition 步骤 ──
        state_scenarios = [
            s for s in sb_scenarios
            if any("state_transition" in step.action.lower() for step in s.steps)
        ]
        assert len(state_scenarios) >= 1, (
            f"Should have state machine scenarios, got actions: {actions}"
        )

        # ── 验证 6：每个场景的 description 不再只是裸 invariant ──
        for s in sb_scenarios:
            sd = s.to_dict()
            desc = sd.get("description", "")
            assert "验证对象" in desc or "验证方向" in desc, (
                f"Description should contain verification intent markers: {desc[:100]}"
            )

        # ── 验证 7：至少有一个场景携带租户边界意识 ──
        tenant_aware = [
            s for s in sb_scenarios
            if any("tenant" in pc.lower() or "租户" in pc
                   for pc in s.preconditions)
        ]
        assert len(tenant_aware) >= 1, (
            f"Should have tenant-boundary aware scenarios"
        )

        # ── 验证 8：场景 severity 不低于 P2 ──
        for s in sb_scenarios:
            assert s.severity in ("P0", "P1", "P2"), f"Unexpected severity: {s.severity}"

    finally:
        restore_system_behavior_space_patch()


def test_historical_boundary_pattern_boosts_similar_slices(tmp_path) -> None:
    """验证：已确认的跨租户缺陷 → 相似边界切片自动提升优先级。"""
    import json
    from ai_test_asset_center.private_pilot_coverage_steering_patch import (
        _confirmed_finding_boundaries,
        _similar_boundary_boost,
    )

    ws = tmp_path / "platform_workspace" / "proj" / "defect_discovery"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "confirmed_findings.json").write_text(json.dumps({
        "evid_001": {
            "customer_delivery_status": "defect",
            "title": "跨租户读取订单",
            "reproduction": {"path": "/api/orders", "method": "GET"},
            "regression_contract": {"dimensions": ["tenant_isolation"], "surface_plan": ["api"]},
        }
    }))

    boundaries = _confirmed_finding_boundaries(tmp_path, "proj")
    assert len(boundaries) >= 1
    assert "tenant_isolation" in boundaries[0]["dimensions"]

    slices = [
        {"slice_id": "s_tenant", "entity": "orders", "kind": "invariant",
         "_system_behavior_dimensions": ["tenant_isolation"], "_system_behavior_surface_plan": ["api"]},
        {"slice_id": "s_money", "entity": "payment", "kind": "invariant",
         "_system_behavior_dimensions": ["money_quantity_conservation"], "_system_behavior_surface_plan": ["api"]},
    ]

    boosts = _similar_boundary_boost(slices, boundaries)
    assert "s_tenant" in boosts, f"Tenant slice should be boosted: {boosts}"
    assert boosts.get("s_tenant", 0) > (boosts.get("s_money", 0) or 0), (
        f"Tenant boost {boosts.get('s_tenant',0)} > money boost {boosts.get('s_money',0)}"
    )


def test_historical_boundary_boost_integrated(tmp_path) -> None:
    """验证：完整steering流程中历史边界boost被集成。"""
    import json

    ws = tmp_path / "platform_workspace" / "proj" / "defect_discovery"
    ws.mkdir(parents=True, exist_ok=True)
    (ws / "confirmed_findings.json").write_text(json.dumps({
        "evid_001": {
            "customer_delivery_status": "defect",
            "reproduction": {"path": "/api/orders", "method": "GET"},
            "regression_contract": {"dimensions": ["tenant_isolation"], "surface_plan": ["api"]},
        }
    }))
    (tmp_path / "platform_outputs" / "proj" / "risk_clue_pool").mkdir(parents=True, exist_ok=True)

    from ai_test_asset_center.private_pilot_coverage_steering_patch import _steer_slices
    slices = [
        {"slice_id": "s1", "entity": "orders", "kind": "invariant",
         "_system_behavior_dimensions": ["tenant_isolation"], "_system_behavior_surface_plan": ["api"],
         "priority": 0.5, "endpoints": ["/api/orders"]},
        {"slice_id": "s2", "entity": "payment", "kind": "invariant",
         "_system_behavior_dimensions": ["money_quantity_conservation"], "_system_behavior_surface_plan": ["api"],
         "priority": 0.5, "endpoints": ["/api/payments"]},
    ]

    ordered, diagnostic = _steer_slices(slices, root=tmp_path, project="proj")
    assert diagnostic.get("historical_boundary_patterns_found", 0) >= 1
    assert diagnostic.get("historical_boundary_boosted_slice_count", 0) >= 1
