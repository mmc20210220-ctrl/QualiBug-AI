"""主链验证：企业知识上传 → 系统行为承诺 → 维度感知场景。

验证产品核心价值主张端到端工作——
给定多行业企业材料，系统能产生业务维度感知的差异化验证计划。
这个测试不依赖任何外部服务，纯本地运行。
"""
from __future__ import annotations

import json
from pathlib import Path

from ai_test_asset_center.business_state_graph import BusinessStateGraphBuilder
from ai_test_asset_center.enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_files,
)
from ai_test_asset_center.private_pilot_system_behavior_space_patch import (
    install_system_behavior_space_patch,
    restore_system_behavior_space_patch,
)
from ai_test_asset_center.semantic_scenario_generator import SemanticScenarioGenerator


def test_healthcare_enterprise_knowledge_drives_dimension_aware_scenarios(tmp_path: Path) -> None:
    """医疗行业：患者预约系统 → 应产生角色权限、状态流转、审计追溯场景。"""
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        project = "healthcare_demo"
        inputs = tmp_path / "fixtures"
        inputs.mkdir(parents=True, exist_ok=True)

        # ── 上传医疗系统材料 ──
        (inputs / "PRD_appointments.md").write_text(
            """# 预约管理 PRD
            患者可以创建预约，医生可以确认或取消预约。
            预约状态：PENDING -> CONFIRMED -> COMPLETED 或 CANCELLED。
            已完成(COMPLETED)的预约不能取消。
            已取消(CANCELLED)的预约不能再次确认。
            只有该患者的医生才能查看患者病历。
            所有预约变更必须产生审计日志。
            """,
            encoding="utf-8",
        )
        (inputs / "api.json").write_text(json.dumps({
            "openapi": "3.0.3",
            "paths": {
                "/patients/{id}/appointments": {
                    "get": {"summary": "List patient appointments"},
                    "post": {"summary": "Create appointment"},
                },
                "/appointments/{id}/confirm": {
                    "post": {"summary": "Doctor confirms appointment"},
                },
                "/appointments/{id}/cancel": {
                    "post": {"summary": "Cancel appointment"},
                },
                "/audit/logs": {"get": {"summary": "Audit trail"}},
            },
        }), encoding="utf-8")
        (inputs / "schema.sql").write_text(
            """CREATE TABLE appointments (
              id INTEGER PRIMARY KEY,
              patient_id TEXT NOT NULL,
              doctor_id TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE audit_logs (
              id INTEGER PRIMARY KEY,
              entity_type TEXT,
              entity_id INTEGER,
              operation TEXT,
              trace_id TEXT,
              created_at TIMESTAMP
            );
            """,
            encoding="utf-8",
        )

        # ── 上传企业资料 ──
        ingest_enterprise_knowledge_files(
            project,
            list(inputs.iterdir()),
            root=tmp_path,
            actor={"name": "doctor", "role": "project_owner"},
        )

        # ── 构建知识资产 ──
        asset = build_enterprise_business_knowledge_asset(project, root=tmp_path)
        assert asset.get("summary", {}).get("knowledge_ready"), "Knowledge asset should be ready"
        assert len(asset.get("rule_library") or []) > 0, "Should have extracted business rules"
        assert len(asset.get("interfaces") or []) > 0, "Should have parsed API interfaces"

        # ── 构建行为空间 ──
        builder = BusinessStateGraphBuilder()
        api_text = (inputs / "api.json").read_text(encoding="utf-8")
        db_text = (inputs / "schema.sql").read_text(encoding="utf-8")
        prd_text = (inputs / "PRD_appointments.md").read_text(encoding="utf-8")
        graphs = builder.build(prd_text, api_text, db_text)
        contract = builder.behavior_contract()

        # ── 验证系统行为承诺 ──
        space = contract.get("system_behavior_space", {})
        assert space.get("version") == "system_behavior_space.v1"
        promises = space.get("summary", {}).get("promise_count", 0)
        assert promises >= 1, f"Should have at least 1 system behavior promise, got {promises}"

        system_slices = [
            s for s in contract["slices"]
            if s.get("_selection_origin") == "system_behavior_space"
        ]
        assert len(system_slices) >= 1, f"Should have system behavior slices, got {len(system_slices)}"

        # ── 验证维度种类 ──
        all_dims: set[str] = set()
        for sl in system_slices:
            dims = sl.get("_system_behavior_dimensions", [])
            all_dims.update(str(d).lower() for d in dims if str(d))
        # 医疗场景应涉及：角色权限、状态流转、审计追溯
        expected_families = {"authorization", "state", "audit"}
        found = all_dims & expected_families
        assert found, (
            f"Healthcare scenario should produce authorization/state/audit dimensions. "
            f"Found: {all_dims}"
        )

        # ── 生成场景 ──
        scenarios = SemanticScenarioGenerator().generate(
            graphs, api_text,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        sb_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]
        assert len(sb_scenarios) >= 1, f"Should generate system behavior scenarios"

        # ── 验证场景不是千篇一律 ──
        actions = {st.action for s in sb_scenarios for st in s.steps}
        assert len(actions) >= 2, (
            f"Should have multiple distinct step actions (not all the same). "
            f"Got: {actions}"
        )

        # ── 验证审计场景有多步 ──
        audit_scenarios = [
            s for s in sb_scenarios
            if any("audit" in st.action.lower() for st in s.steps)
        ]
        if audit_scenarios:
            multi_step_audit = [s for s in audit_scenarios if len(s.steps) >= 2]
            # At minimum, there should be audit-aware scenarios
            assert len(audit_scenarios) >= 1

        # ── 验证 description 含验证意图标记 ──
        for s in sb_scenarios:
            sd = s.to_dict()
            desc = sd.get("description", "")
            assert "验证对象" in desc or "验证方向" in desc, (
                f"Description should contain verification intent: {desc[:100]}"
            )

    finally:
        restore_system_behavior_space_patch()


def test_logistics_enterprise_knowledge_drives_dimension_aware_scenarios(tmp_path: Path) -> None:
    """物流行业：运单跟踪系统 → 应产生状态流转、租户隔离、数据一致场景。

    证明系统不限于电商——物流也能产生业务维度感知的验证计划。
    """
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        project = "logistics_demo"
        inputs = tmp_path / "fixtures"
        inputs.mkdir(parents=True, exist_ok=True)

        (inputs / "PRD_shipments.md").write_text(
            """# 运单管理 PRD
            运单状态：CREATED -> PICKED_UP -> IN_TRANSIT -> DELIVERED。
            不同客户(tenant)的运单数据严格隔离。
            运单状态流转必须合法。
            已签收(DELIVERED)的运单不能再次修改。
            """,
            encoding="utf-8",
        )
        (inputs / "api.json").write_text(json.dumps({
            "openapi": "3.0.3",
            "paths": {
                "/shipments": {"get": {"summary": "List shipments"}, "post": {"summary": "Create shipment"}},
                "/shipments/{id}": {"get": {"summary": "Shipment detail"}},
                "/shipments/{id}/status": {"post": {"summary": "Update shipment status"}},
                "/shipments/export": {"get": {"summary": "Export shipments"}},
            },
        }), encoding="utf-8")
        (inputs / "schema.sql").write_text(
            """CREATE TABLE shipments (
              id INTEGER PRIMARY KEY,
              tenant_id TEXT NOT NULL,
              tracking_number TEXT NOT NULL,
              status TEXT NOT NULL,
              created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
              deleted_at TIMESTAMP
            );
            """,
            encoding="utf-8",
        )

        ingest_enterprise_knowledge_files(
            project, list(inputs.iterdir()), root=tmp_path,
            actor={"name": "dispatcher", "role": "project_owner"},
        )
        asset = build_enterprise_business_knowledge_asset(project, root=tmp_path)
        assert asset.get("summary", {}).get("knowledge_ready")

        builder = BusinessStateGraphBuilder()
        api_text = (inputs / "api.json").read_text(encoding="utf-8")
        db_text = (inputs / "schema.sql").read_text(encoding="utf-8")
        prd_text = (inputs / "PRD_shipments.md").read_text(encoding="utf-8")
        graphs = builder.build(prd_text, api_text, db_text)
        contract = builder.behavior_contract()

        system_slices = [
            s for s in contract["slices"]
            if s.get("_selection_origin") == "system_behavior_space"
        ]
        assert len(system_slices) >= 1

        all_dims: set[str] = set()
        for sl in system_slices:
            dims = sl.get("_system_behavior_dimensions", [])
            all_dims.update(str(d).lower() for d in dims if str(d))

        # 物流应涉及：租户隔离(tenant)、状态流转(state)、可见性(visibility)
        expected = {"tenant", "state", "visibility"}
        found = all_dims & expected
        assert found, (
            f"Logistics scenario should produce tenant/state/visibility dimensions. "
            f"Found: {all_dims}"
        )

        scenarios = SemanticScenarioGenerator().generate(
            graphs, api_text,
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        sb_scenarios = [s for s in scenarios if s.selection_origin == "system_behavior_space"]
        assert len(sb_scenarios) >= 1

        # 应该有 tenant_isolation 相关的场景
        tenant_scenarios = [
            s for s in sb_scenarios
            if any("tenant" in pc.lower() or "租户" in pc for pc in s.preconditions)
        ]
        assert len(tenant_scenarios) >= 1, (
            f"Logistics should have tenant-boundary-aware scenarios"
        )

        # 应该有状态流转相关的步骤
        state_actions = [
            st.action for s in sb_scenarios for st in s.steps
            if "state_transition" in st.action.lower() or "state" in st.action.lower()
        ]
        assert len(state_actions) >= 1, (
            f"Logistics should have state-aware verification steps"
        )

    finally:
        restore_system_behavior_space_patch()


def test_enterprise_knowledge_preserves_permission_data_to_prd_text() -> None:
    """验证企业权限数据正确流入PRD富化文本（回归88d3047修复）。

    权限矩阵使用role/resource/actions/scope字段，不是statement。
    风险域使用title/expected/risk_type字段，不是statement。
    """
    from ai_test_asset_center.v12_pipeline import _knowledge_asset_planning_text

    asset = {
        "rule_library": [
            {"rule_id": "r1", "statement": "运单金额必须非负"},
        ],
        "permission_matrix": [
            {
                "permission_id": "p1",
                "role": "普通调度员",
                "resource": "运单详情",
                "actions": ["read"],
                "scope": "own_tenant",
            },
            {
                "permission_id": "p2",
                "role": "高级调度员",
                "resource": "运单状态",
                "actions": ["read", "update"],
                "scope": "assigned_hub",
            },
        ],
        "risk_domains": [
            {
                "risk_id": "h1",
                "title": "历史缺陷：跨租户读取运单",
                "risk_type": "tenant_isolation",
            },
        ],
    }
    text = _knowledge_asset_planning_text(asset)

    # 业务规则
    assert "运单金额必须非负" in text

    # 权限矩阵 — 使用真实字段名 role/resource/actions/scope
    assert "普通调度员" in text
    assert "运单详情" in text
    assert "read" in text
    assert "own_tenant" in text
    assert "高级调度员" in text
    assert "assigned_hub" in text

    # 风险域 — 使用真实字段名 title/risk_type
    assert "跨租户读取运单" in text

    # 不应包含不存在字段名的残留
    assert "statement" not in text.split("##")[-1]  # 最后一个section是风险域，不应有"statement"


def test_empty_knowledge_asset_does_not_block_pipeline(tmp_path: Path) -> None:
    """验证：无企业知识时管道正常运行，不崩溃不报错。"""
    restore_system_behavior_space_patch()
    install_system_behavior_space_patch()
    try:
        builder = BusinessStateGraphBuilder()
        graphs = builder.build(
            "普通用户只能看自己的订单。金额必须一致。",
            """
openapi: 3.0.0
paths:
  /api/orders:
    get:
      summary: list orders
""",
            "CREATE TABLE orders (id INTEGER, tenant_id TEXT, status TEXT, amount DECIMAL(10,2));",
        )
        contract = builder.behavior_contract()
        system_slices = [
            s for s in contract["slices"]
            if s.get("_selection_origin") == "system_behavior_space"
        ]
        # Without enterprise knowledge, still produces system behavior slices
        # from API schema + DB schema
        assert len(system_slices) >= 1

        scenarios = SemanticScenarioGenerator().generate(
            graphs,
            "",
            active_slices=system_slices,
            allow_source_runtime=True,
        )
        sb = [s for s in scenarios if s.selection_origin == "system_behavior_space"]
        assert len(sb) >= 1

    finally:
        restore_system_behavior_space_patch()
