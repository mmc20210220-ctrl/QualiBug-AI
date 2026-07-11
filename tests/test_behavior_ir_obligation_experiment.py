"""Phase 1–3: Behavior IR, obligations, experiments, assertions, contract oracles."""
from __future__ import annotations

import re
from pathlib import Path

from ai_test_asset_center.assertion_dsl import evaluate_assertion
from ai_test_asset_center.behavior_ir import (
    SCHEMA_VERSION,
    build_behavior_ir_from_knowledge_asset,
    validate_behavior_ir,
)
from ai_test_asset_center.contract_oracles import (
    demote_heuristic_business_oracle_finding,
    evaluate_contract_oracle,
)
from ai_test_asset_center.experiment_compiler import compile_experiment_for_obligation, compile_experiments
from ai_test_asset_center.obligation_compiler import compile_obligations_from_behavior_ir
from ai_test_asset_center.runtime_binding_graph import apply_binding, build_binding_plan, extract_placeholders
from ai_test_asset_center.adaptive_discovery_planner import plan_obligation_round


ROOT = Path(__file__).resolve().parents[1]
NEW_MODULES = [
    ROOT / "ai_test_asset_center" / "behavior_ir.py",
    ROOT / "ai_test_asset_center" / "test_obligation.py",
    ROOT / "ai_test_asset_center" / "obligation_compiler.py",
    ROOT / "ai_test_asset_center" / "experiment_contract.py",
    ROOT / "ai_test_asset_center" / "experiment_compiler.py",
    ROOT / "ai_test_asset_center" / "runtime_binding_graph.py",
    ROOT / "ai_test_asset_center" / "assertion_dsl.py",
    ROOT / "ai_test_asset_center" / "contract_oracles.py",
    ROOT / "ai_test_asset_center" / "adaptive_discovery_planner.py",
    ROOT / "ai_test_asset_center" / "execution_adapter.py",
]


def _sample_asset() -> dict:
    return {
        "sources": [{"source_id": "src-api", "filename": "api.md", "source_type": "api"}],
        "permission_matrix": [
            {"role": "viewer_role", "resource": "/resources/{id}", "actions": ["read"], "scope": "own"},
            {"role": "owner_role", "resource": "/resources/{id}", "actions": ["read", "write"], "scope": "own"},
        ],
        "rule_library": [
            {"rule_id": "rule-1", "statement": "resource quantity must be conserved across transfer", "kind": "conservation"},
        ],
        "state_machines": [
            {"entity": "resource", "states": ["draft", "active"]},
        ],
        "objects": [{"name": "resource", "kind": "entity"}],
        "operations": [
            {
                "operation_id": "list_resources",
                "method": "GET",
                "path": "/resources",
                "side_effect_class": "read",
            },
            {
                "operation_id": "get_resource",
                "method": "GET",
                "path": "/resources/{id}",
                "side_effect_class": "read",
            },
            {
                "operation_id": "update_resource",
                "method": "PUT",
                "path": "/resources/{id}",
                "side_effect_class": "write",
            },
        ],
    }


def test_behavior_ir_schema_and_source_refs() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a", source_snapshot_hash="abc")
    assert ir["schema_version"] == SCHEMA_VERSION
    assert validate_behavior_ir(ir) == []
    assert ir["operations"]
    assert ir["actors"]
    assert all(item.get("source_refs") for item in ir["operations"])
    assert all("password" not in str(item).lower() or "secret_ref" in str(item) for item in ir["actors"])
    assert all("credential_secret_ref" in item for item in ir["actors"])


def test_obligation_compiler_generic_templates() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _sample_asset(),
        project_id="proj-a",
        runtime_actors=[
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
            {"role": "owner_role", "account_ref": "owner_b", "secret_ref": "secret_ref:test_accounts:owner_b"},
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    assert compiled["obligation_count"] > 0
    families = {item["risk_family"] for item in compiled["obligations"]}
    assert "authorization" in families
    assert "isolation" in families
    # Obligations reference IR ids, not free-form industry endpoints as logic keys
    for obl in compiled["obligations"]:
        assert obl["obligation_id"].startswith("obl_")
        assert obl["compile_status"] == "PENDING"


def test_authorization_obligation_uses_source_permissions_not_actor_order() -> None:
    ir = build_behavior_ir_from_knowledge_asset(
        _sample_asset(),
        project_id="proj-a",
        runtime_actors=[
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
        ],
    )
    compiled = compile_obligations_from_behavior_ir(ir)
    actors = {item["id"]: item for item in ir["actors"]}
    ops = {item["id"]: item for item in ir["operations"]}
    update_op = next(item for item in ir["operations"] if item.get("operation_id") == "update_resource")
    auth_obl = next(
        item for item in compiled["obligations"]
        if item["risk_family"] == "authorization"
        and item["property"].get("operation_ref") == update_op["id"]
    )
    control = actors[auth_obl["property"]["control_actor_ref"]]
    treatment = actors[auth_obl["property"]["treatment_actor_ref"]]
    assert control["role"] == "owner_role"
    assert treatment["role"] == "viewer_role"
    assert ops[auth_obl["property"]["operation_ref"]]["operation_id"] == "update_resource"


def test_experiment_compiler_blocks_missing_binding() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a")
    compiled = compile_obligations_from_behavior_ir(ir)
    write_op = next(op for op in ir["operations"] if "{id}" in str(op.get("path")))
    actors = ir["actors"]
    write_obl = {
        "obligation_id": "obl_test_binding",
        "risk_family": "idempotency",
        "subject_refs": [write_op["id"]],
        "property": {"template": "idempotent_effect_cardinality", "operation_ref": write_op["id"]},
        "required_actors": [actors[0]["id"]] if actors else [],
        "required_operations": [write_op["id"]],
        "required_fixtures": [],
        "required_observers": ["http_response", "business_effect"],
        "cleanup_requirement": {"required": True, "mode": "reverse_order"},
        "source_refs": [],
        "confidence": 0.5,
        "compile_status": "PENDING",
    }
    experiment = compile_experiment_for_obligation(
        write_obl,
        behavior_ir=ir,
        environment_type="test",
    )
    receipt = experiment["compile_receipt"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert compiled["obligation_count"] > 0


def test_colon_path_params_block_at_compile_before_planning() -> None:
    asset = _sample_asset()
    asset["operations"] = [
        {
            "operation_id": "read_resources",
            "method": "GET",
            "path": "/resources",
            "side_effect_class": "read",
        },
        {
            "operation_id": "read_resource_by_id",
            "method": "GET",
            "path": "/resources/:id",
            "side_effect_class": "read",
        },
    ]
    ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id="proj-a",
        runtime_actors=[
            {"role": "viewer_role", "account_ref": "viewer_a", "secret_ref": "secret_ref:test_accounts:viewer_a"},
            {"role": "owner_role", "account_ref": "owner_a", "secret_ref": "secret_ref:test_accounts:owner_a"},
        ],
    )
    target_op = next(op for op in ir["operations"] if str(op.get("path")) == "/resources/:id")
    obligation = {
        "obligation_id": "obl_colon_path_binding",
        "risk_family": "isolation",
        "subject_refs": [target_op["id"]],
        "property": {"template": "tenant_isolation", "operation_ref": target_op["id"]},
        "required_actors": [actor["id"] for actor in ir["actors"][:2]],
        "required_operations": [target_op["id"]],
        "required_fixtures": [],
        "required_observers": ["http_response"],
        "cleanup_requirement": {"required": False},
        "source_refs": [],
        "confidence": 0.9,
        "compile_status": "PENDING",
    }

    experiment = compile_experiment_for_obligation(
        obligation,
        behavior_ir=ir,
        environment_type="test",
    )
    receipt = experiment["compile_receipt"]

    assert extract_placeholders("/resources/:id") == ["id"]
    assert receipt["status"] == "BLOCKED"
    assert receipt["reason_code"] == "BLOCKED_MISSING_BINDING"
    plan = plan_obligation_round(
        [obligation],
        experiments_by_obligation={obligation["obligation_id"]: experiment},
        budget=5,
    )
    assert plan["selected_count"] == 0


def test_experiment_compiler_blocks_production_environment() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a")
    obl = compile_obligations_from_behavior_ir(ir)["obligations"][0]
    blocked = compile_experiment_for_obligation(obl, behavior_ir=ir, environment_type="production")
    assert blocked["compile_receipt"]["status"] == "BLOCKED"
    assert blocked["compile_receipt"]["reason_code"] == "BLOCKED_UNSUPPORTED_ADAPTER"


def test_binding_priority_no_low_override() -> None:
    plan = [{"target": "id", "status": "bound", "source_priority": "experiment_setup_response", "value_fingerprint": "aaa"}]
    updated = apply_binding(plan, target="id", value="low", source_priority="schema_generated")
    assert updated[0]["source_priority"] == "experiment_setup_response"
    assert "override_rejected" in updated[0]


def test_assertion_concurrency_rejects_dual_2xx_alone() -> None:
    result = evaluate_assertion(
        {"kind": "concurrency_final_invariant", "assertion_id": "a1"},
        observations={"dual_2xx": True},
    )
    assert result["passed"] is False
    assert result["error"] == "dual_2xx_insufficient"


def test_assertion_isolation_requires_control() -> None:
    result = evaluate_assertion(
        {"kind": "isolation", "assertion_id": "a2"},
        observations={
            "owner_can_access": True,
            "viewer_can_access": False,
            "leak_detected": False,
            "control_succeeded": False,
        },
    )
    assert result["passed"] is False


def test_contract_oracle_harness_error_not_defect() -> None:
    verdict = evaluate_contract_oracle(
        experiment={"control_plan": [{"x": 1}], "treatment_plan": [{"y": 1}], "observers": [{"observer_id": "http_response"}], "assertions": [{"kind": "http_status", "expected": 403}]},
        evidence={"harness_error": True, "control_succeeded": True, "treatment_observation": {}, "http_response": True},
    )
    assert verdict["verdict"] == "harness_failure"
    assert verdict["customer_deliverable"] is False


def test_demote_heuristic_concurrency_finding() -> None:
    finding = {
        "id": "f1",
        "oracle": {"oracle_name": "ConcurrencyOracle"},
        "evidence": {"dual_2xx": True},
        "gate_passed": True,
        "customer_delivery_status": "defect",
    }
    demoted = demote_heuristic_business_oracle_finding(finding)
    assert demoted["customer_delivery_status"] == "clue"
    assert demoted["gate_passed"] is False


def test_source_grounded_permission_bypass_not_demoted() -> None:
    finding = {
        "id": "f_perm",
        "oracle": {"oracle_name": "PermissionOracle", "violated_rule": "unauthorized_access"},
        "source_refs": [{"kind": "permission_matrix", "source_id": "roles", "locator": "buyer->reports"}],
        "evidence": {
            "request": "GET /api/reports",
            "actor_token_present": True,
        },
        "before_after_snapshot": {
            "after": {"method": "GET", "path": "/api/reports", "status_code": 200, "body": {"items": [{"id": "r1"}]}}
        },
        "gate_passed": True,
        "customer_delivery_status": "defect",
    }
    kept = demote_heuristic_business_oracle_finding(finding)
    assert kept["customer_delivery_status"] == "defect"
    assert kept["gate_passed"] is True
    assert "oracle_demotion_reason" not in kept


def test_ungrounded_permission_signal_still_demoted() -> None:
    finding = {
        "id": "f_perm_clue",
        "oracle": {"oracle_name": "PermissionOracle", "violated_rule": "unauthorized_access"},
        "source_refs": [{"kind": "runtime_actor", "source_id": "runtime", "locator": "buyer"}],
        "evidence": {
            "request": "GET /api/reports",
            "actor_token_present": True,
        },
        "before_after_snapshot": {
            "after": {"method": "GET", "path": "/api/reports", "status_code": 200, "body": {"items": [{"id": "r1"}]}}
        },
        "gate_passed": True,
        "customer_delivery_status": "defect",
    }
    demoted = demote_heuristic_business_oracle_finding(finding)
    assert demoted["customer_delivery_status"] == "clue"
    assert demoted["gate_passed"] is False


def test_idempotency_oracle_marks_heuristic_as_clue_tier() -> None:
    from ai_test_asset_center.oracle_engine import IdempotencyOracle

    oracle = IdempotencyOracle()
    result = oracle.evaluate(
        {"title": "dup submit"},
        {
            "steps": [
                {"method": "POST", "path": "/api/resource", "response": {"status_code": 200}},
                {"method": "POST", "path": "/api/resource", "response": {"status_code": 200}},
            ]
        },
    )
    assert result.passed is False
    assert result.customer_deliverable is False
    assert result.oracle_tier == "internal_clue"


def test_obligation_execution_projection_counts() -> None:
    from ai_test_asset_center.discovery_quality_projection import build_obligation_execution_projection

    proj = build_obligation_execution_projection(
        {
            "test_obligations": {"count": 10},
            "experiment_compile": {"compiled_count": 7, "blocked_count": 3, "block_reason_counts": {"BLOCKED_MISSING_BINDING": 3}},
            "obligation_plan": {"selected_count": 5},
            "phases": {"oracle": {"traces_with_http": 4}},
            "findings": [],
        }
    )
    assert proj["obligation_total"] == 10
    assert proj["obligation_compiled"] == 7
    assert proj["obligation_blocked"] == 3
    assert proj["obligation_executed"] == 4
    assert proj["block_reason_counts"]["BLOCKED_MISSING_BINDING"] == 3


def test_adaptive_planner_prefers_compiled() -> None:
    ir = build_behavior_ir_from_knowledge_asset(_sample_asset(), project_id="proj-a")
    obl_pack = compile_obligations_from_behavior_ir(ir)
    exp_pack = compile_experiments(
        obl_pack["obligations"],
        behavior_ir=ir,
        environment_type="test",
    )
    by_oid = {item["obligation_id"]: item for item in exp_pack["experiments"]}
    plan = plan_obligation_round(obl_pack["obligations"], experiments_by_obligation=by_oid, budget=5)
    assert plan["selected_count"] <= 5
    assert all(item.get("obligation_id") for item in plan["selected"])


def test_adaptive_planner_reports_final_family_counts_and_reserves_breadth() -> None:
    obligations = [
        {
            "obligation_id": f"obl_auth_{index}",
            "risk_family": "authorization",
            "subject_refs": [f"subject_{index}"],
            "confidence": 0.95,
            "compile_status": "COMPILED",
        }
        for index in range(5)
    ] + [
        {
            "obligation_id": "obl_state",
            "risk_family": "state",
            "subject_refs": ["state_subject"],
            "confidence": 0.6,
            "compile_status": "COMPILED",
        },
        {
            "obligation_id": "obl_concurrency",
            "risk_family": "concurrency",
            "subject_refs": ["concurrency_subject"],
            "confidence": 0.6,
            "compile_status": "COMPILED",
        },
    ]

    plan = plan_obligation_round(obligations, budget=4)

    selected_families = [item["risk_family"] for item in plan["selected"]]
    assert {"authorization", "state", "concurrency"}.issubset(selected_families)
    assert plan["family_coverage"] == {
        family: selected_families.count(family)
        for family in sorted(set(selected_families))
    }


def test_adaptive_planner_risk_priority_comes_from_runtime_policy_data() -> None:
    common = {
        "subject_refs": ["same_subject"],
        "confidence": 0.8,
        "compile_status": "COMPILED",
    }
    obligations = [
        {**common, "obligation_id": "obl_a", "risk_family": "authorization"},
        {**common, "obligation_id": "obl_b", "risk_family": "source_declared_custom_risk"},
    ]

    plan = plan_obligation_round(
        obligations,
        budget=1,
        historical_yield={
            "risk:authorization": 0.2,
            "risk:source_declared_custom_risk": 1.0,
        },
    )

    assert plan["selected"][0]["obligation_id"] == "obl_b"


def test_no_industry_hardcoded_endpoint_drivers_in_new_modules() -> None:
    """Static guard: new modules must not hardcode benchmark/customer endpoint paths as drivers."""
    banned = re.compile(
        r"/api/orders|/api/cart|/api/coupons|/api/sku|benchmark_mall|/mall/",
        re.I,
    )
    for path in NEW_MODULES:
        text = path.read_text(encoding="utf-8")
        # Allow comments mentioning the prohibition, but not executable string literals driving logic.
        code_lines = [
            line for line in text.splitlines()
            if line.strip() and not line.strip().startswith("#") and not line.strip().startswith('"""') and not line.strip().startswith("'''")
        ]
        joined = "\n".join(code_lines)
        assert not banned.search(joined), f"industry/benchmark hardcoding found in {path.name}"
