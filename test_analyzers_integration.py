#!/usr/bin/env python3
"""
测试分析器集成到 Phase92A 证据管道
"""

import sys
import os
import tempfile
import json
import sqlite3
import shutil
from pathlib import Path

# 添加项目根目录到路径
project_root = Path(__file__).parent
sys.path.insert(0, str(project_root))


def test_analyzers_adapter():
    """测试分析器适配器模块"""
    print("=" * 60)
    print("Test 1: Import analyzers adapter")
    print("=" * 60)

    try:
        from ai_test_asset_center.analyzers_adapter import (
            AnalyzersAdapter,
            build_analyzer_hypotheses,
            get_analyzer_engine_names,
        )
        from ai_test_asset_center.discovery_engine import (
            _apply_execution_budget_profile,
            _plan_execution_budget,
            _summarize_execution_feedback,
        )
        from ai_test_asset_center.budget_feedback_store import (
            load_budget_feedback_profile,
            persist_budget_feedback_profile,
            resolve_budget_learning_context,
        )
        from ai_test_asset_center.approver_identity_resolver import (
            resolve_approver_context,
            save_approver_identity_registry,
        )
        from ai_test_asset_center.deployment_config_resolver import (
            approve_deployment_config_drift,
            build_deployment_config_snapshot,
            detect_deployment_config_drift,
            evaluate_deployment_drift_unlock,
            persist_deployment_config_snapshot,
            required_deployment_drift_roles,
            resolve_deployment_config,
            validate_deployment_drift_approval,
        )
        from ai_test_asset_center.hypothesis_schema import validate_hypothesis
        from ai_test_asset_center.policy_registry import ExecutionPolicy
        import ai_test_asset_center.real_project_onboarding as onboarding_module
        from ai_test_asset_center.real_project_onboarding import (
            approve_current_deployment_drift,
            init_approver_identity_templates,
            inspect_identity_status,
            inspect_approver_identity_resolution,
            inspect_approver_identity_inputs,
            inspect_deployment_drift,
            save_approver_identity_inputs,
            save_real_project_inputs,
        )
        from ai_test_asset_center.agent_discovery_loop import build_agent_discovery_loop
        from ai_test_asset_center.stage_reason_all_v2 import _dedupe_hypotheses, _prioritize_hypotheses
        print("[OK] Analyzers adapter imported successfully")
    except Exception as e:
        print(f"[FAIL] Analyzers adapter import failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 2: Get analyzer engine names")
    print("=" * 60)

    try:
        engine_names = get_analyzer_engine_names()
        print(f"[OK] Successfully retrieved {len(engine_names)} analyzers:")
        for name in engine_names:
            print(f"  - {name}")
    except Exception as e:
        print(f"[FAIL] Failed to get analyzer list: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 3: Initialize analyzers adapter")
    print("=" * 60)

    try:
        adapter = AnalyzersAdapter()
        print(f"[OK] Analyzers adapter initialized successfully")
        print(f"  Loaded analyzers: {list(adapter.analyzers.keys())}")
    except Exception as e:
        print(f"[FAIL] Failed to initialize analyzer adapter: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 4: Build analyzer hypotheses")
    print("=" * 60)

    try:
        prd_text = """
        # Order Management System
        Users can create orders, order amount must be greater than 0.
        After order creation, status is pending payment, becomes completed after payment.
        """

        api_spec = """
        {
            "openapi": "3.0.0",
            "paths": {
                "/api/orders": {
                    "post": {"summary": "Create order"},
                    "get": {"summary": "Get order list"}
                }
            }
        }
        """

        hypotheses_by_engine = build_analyzer_hypotheses(prd_text, api_spec)
        print(f"[OK] Analyzer hypotheses built successfully")

        total_hypotheses = 0
        valid_hypotheses = 0
        for engine_name, hypotheses in hypotheses_by_engine.items():
            count = len(hypotheses)
            total_hypotheses += count
            print(f"  - {engine_name}: {count} hypotheses")
            for hypothesis in hypotheses:
                validation = validate_hypothesis(hypothesis)
                if validation.valid:
                    valid_hypotheses += 1

        print(f"Total hypotheses generated: {total_hypotheses}")
        print(f"Executable hypotheses validated: {valid_hypotheses}")
        if total_hypotheses and valid_hypotheses == 0:
            print("[FAIL] No analyzer hypothesis passed validate_hypothesis")
            return False
    except Exception as e:
        print(f"[FAIL] Failed to build analyzer hypotheses: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 5: Fallback route binding without explicit endpoints")
    print("=" * 60)

    try:
        class SyntheticViolation:
            def __init__(self):
                self.title = "Order amount validation may be missing"
                self.description = "Create order should reject invalid amount"
                self.severity = "P1"
                self.category = "business_rules"
                self.expected_behavior = "POST create order should reject amount <= 0"
                self.actual_behavior = "The API may accept invalid order amount"
                self.evidence = {"rule": "order amount must be greater than 0"}
                self.reproduction_steps = ["Create an order with amount 0"]
                self.related_endpoints = []

        api_spec_parsed = {
            "paths": {
                "/api/orders": {
                    "post": {"summary": "Create order", "description": "Create a new order"},
                    "get": {"summary": "Get order list"},
                }
            }
        }
        synthetic = SyntheticViolation()
        fallback_hypothesis = adapter._convert_to_hypothesis(synthetic, "business_rules", 0, api_spec_parsed)
        if not fallback_hypothesis:
            print("[FAIL] Fallback binding produced no hypothesis")
            return False
        validation = validate_hypothesis(fallback_hypothesis)
        print(f"[OK] Fallback verification_method: {fallback_hypothesis.get('verification_method')}")
        if not validation.valid:
            print(f"[FAIL] Fallback hypothesis is not executable: {validation.errors}")
            return False
    except Exception as e:
        print(f"[FAIL] Fallback route binding test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 6: Merge duplicate hypotheses across LLM and analyzer")
    print("=" * 60)

    try:
        llm_hypothesis = {
            "hypothesis_id": "llm_1",
            "title": "Order amount validation may be missing",
            "severity": "P1",
            "category": "business_rules",
            "risk_type": "business_rules",
            "expected_behavior": "POST create order should reject amount <= 0",
            "actual_behavior": "The service may accept invalid order amount in some flows",
            "description": "The LLM suspects the create order flow lacks numeric boundary checks.",
            "verification_method": {},
            "why_this_matters": "Invalid financial data can enter the order lifecycle.",
            "_reasoner_engine": "causality",
        }
        analyzer_hypothesis = {
            "hypothesis_id": "analyzer_1",
            "title": "Order amount validation may be missing",
            "severity": "P1",
            "category": "business_rules",
            "risk_type": "business_rules",
            "expected_behavior": "POST create order should reject amount <= 0",
            "actual_behavior": "The API may accept invalid order amount",
            "description": "Create order should reject invalid amount",
            "verification_method": {"step1": "POST /api/orders", "step2": "GET /api/orders"},
            "evidence": {"rule": "order amount must be greater than 0"},
            "_reasoner_engine": "business_rules",
            "_hypothesis_source": "local_analyzer",
        }
        merged = _dedupe_hypotheses([llm_hypothesis, analyzer_hypothesis])
        if len(merged) != 1:
            print(f"[FAIL] Expected 1 merged hypothesis, got {len(merged)}")
            return False
        merged_hypothesis = merged[0]
        validation = validate_hypothesis(merged_hypothesis)
        print(f"[OK] Merged sources: {merged_hypothesis.get('_merged_sources')}")
        print(f"[OK] Merged verification_method: {merged_hypothesis.get('verification_method')}")
        if not validation.valid:
            print(f"[FAIL] Merged hypothesis is not executable: {validation.errors}")
            return False
        merged_sources = merged_hypothesis.get("_merged_sources", [])
        if "causality" not in merged_sources or "business_rules" not in merged_sources:
            print(f"[FAIL] Merge did not preserve both sources: {merged_sources}")
            return False
    except Exception as e:
        print(f"[FAIL] Hypothesis merge test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 7: Prioritize merged executable hypotheses")
    print("=" * 60)

    try:
        low_priority = {
            "hypothesis_id": "single_source_read",
            "title": "Viewer list may expose extra fields",
            "severity": "P2",
            "category": "authorization",
            "risk_type": "authorization",
            "expected_behavior": "Viewer list should hide internal fields",
            "verification_method": {"step1": "GET /api/viewers"},
            "_reasoner_engine": "authorization",
        }
        ranked = _prioritize_hypotheses([low_priority, merged_hypothesis])
        first = ranked[0]
        print(f"[OK] Top prioritized hypothesis: {first.get('title')}")
        if first.get("hypothesis_id") != merged_hypothesis.get("hypothesis_id"):
            print("[FAIL] Merged dual-source hypothesis was not prioritized first")
            return False
    except Exception as e:
        print(f"[FAIL] Hypothesis prioritization test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 8: Plan execution budget tiers")
    print("=" * 60)

    try:
        budget_settings = {
            "enabled": True,
            "tier_a_max_hypotheses": 0,
            "tier_b_max_hypotheses": 0,
            "tier_c_max_hypotheses": 0,
            "overall_max_hypotheses": 2,
            "route_surface_size": 1,
            "tier_a_async_delay_seconds": 3.0,
            "tier_b_async_delay_seconds": 0.5,
            "tier_c_async_delay_seconds": 0.0,
            "tier_b_trim_steps_to": 3,
            "tier_c_trim_steps_to": 1,
        }
        medium_hypothesis = {
            "hypothesis_id": "single_source_write",
            "title": "Order write should trigger read observer",
            "severity": "P2",
            "category": "state_machine",
            "risk_type": "state_machine",
            "expected_behavior": "POST create order should be observable by follow-up read",
            "verification_method": {
                "step1": "GET /api/orders",
                "step2": "POST /api/orders",
                "step3": "GET /api/orders",
                "step4": "GET /api/order-events",
            },
            "_reasoner_engine": "state_machine",
        }
        weak_hypothesis = {
            "hypothesis_id": "weak_semantic_signal",
            "title": "Order list may sort inconsistently",
            "severity": "P3",
            "category": "consistency",
            "risk_type": "consistency",
            "expected_behavior": "",
            "verification_method": {},
            "_reasoner_engine": "consistency",
        }
        feedback_summary = _summarize_execution_feedback([])
        plan, budget_summary = _plan_execution_budget(
            [merged_hypothesis, medium_hypothesis, weak_hypothesis],
            budget_settings,
            feedback_summary,
        )
        plan_summary = [(item.get("hypothesis", {}).get("hypothesis_id"), item.get("tier"), item.get("budget_action")) for item in plan]
        print(f"[OK] Budget plan: {plan_summary}")
        print(f"[OK] Budget summary: {budget_summary}")
        expected = [
            ("analyzer_1", "A", "full"),
            ("single_source_write", "B", "light"),
            ("weak_semantic_signal", "DEFER", "deferred"),
        ]
        if plan_summary != expected:
            print(f"[FAIL] Unexpected budget plan: {plan_summary}")
            return False
        if budget_summary.get("dual_source_count") != 1 or budget_summary.get("target_execute") != 2:
            print(f"[FAIL] Unexpected dynamic budget summary: {budget_summary}")
            return False
    except Exception as e:
        print(f"[FAIL] Execution budget planning test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 9: Trim execution profile for tier B")
    print("=" * 60)

    try:
        trimmed_vm, async_delay = _apply_execution_budget_profile(
            medium_hypothesis["verification_method"],
            "B",
            budget_settings,
        )
        print(f"[OK] Tier B trimmed verification_method: {trimmed_vm}")
        print(f"[OK] Tier B async_delay: {async_delay}")
        if "step4" in trimmed_vm or trimmed_vm.get("step3") != "GET /api/orders":
            print("[FAIL] Tier B profile did not trim heavy observer steps as expected")
            return False
        if async_delay != 0.5:
            print(f"[FAIL] Unexpected tier B async delay: {async_delay}")
            return False
    except Exception as e:
        print(f"[FAIL] Execution budget profile test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 10: Migrate legacy fixed budget caps")
    print("=" * 60)

    try:
        legacy_policy = ExecutionPolicy(
            tier_a_max_hypotheses=8,
            tier_b_max_hypotheses=12,
            tier_c_max_hypotheses=0,
        )
        print(
            "[OK] Migrated legacy caps:",
            legacy_policy.tier_a_max_hypotheses,
            legacy_policy.tier_b_max_hypotheses,
            legacy_policy.tier_c_max_hypotheses,
        )
        if legacy_policy.tier_a_max_hypotheses != 0 or legacy_policy.tier_b_max_hypotheses != 0:
            print("[FAIL] Legacy fixed caps were not migrated to dynamic uncapped mode")
            return False
    except Exception as e:
        print(f"[FAIL] Legacy budget migration test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 11: Learn budget weights from tier hit rates")
    print("=" * 60)

    try:
        adaptive_candidates = [
            merged_hypothesis,
            medium_hypothesis,
            {
                "hypothesis_id": "single_source_read_2",
                "title": "Order state may not refresh after payment",
                "severity": "P1",
                "category": "state_machine",
                "risk_type": "state_machine",
                "expected_behavior": "GET order should reflect paid state after payment callback",
                "verification_method": {"step1": "GET /api/orders", "step2": "GET /api/orders"},
                "_reasoner_engine": "temporal",
            },
        ]
        neutral_plan, neutral_summary = _plan_execution_budget(
            adaptive_candidates,
            {
                **budget_settings,
                "overall_max_hypotheses": 3,
                "route_surface_size": 1,
            },
            _summarize_execution_feedback([]),
        )
        learned_feedback = _summarize_execution_feedback([
            {
                "verdict": "confirmed",
                "evidence": {"execution_budget": {"tier": "A", "action": "full"}},
            },
            {
                "verdict": "confirmed",
                "evidence": {"execution_budget": {"tier": "A", "action": "full"}},
            },
            {
                "verdict": "falsified",
                "evidence": {"execution_budget": {"tier": "B", "action": "light"}},
            },
        ])
        learned_plan, learned_summary = _plan_execution_budget(
            adaptive_candidates,
            {
                **budget_settings,
                "overall_max_hypotheses": 3,
                "route_surface_size": 1,
            },
            learned_feedback,
        )
        print(f"[OK] Neutral summary: {neutral_summary}")
        print(f"[OK] Learned summary: {learned_summary}")
        if learned_summary.get("tier_a_ratio", 0.0) <= neutral_summary.get("tier_a_ratio", 0.0):
            print("[FAIL] Tier A ratio did not increase after strong Tier A hit rate feedback")
            return False
        if len(learned_plan) != len(adaptive_candidates):
            print("[FAIL] Learned plan size changed unexpectedly")
            return False
    except Exception as e:
        print(f"[FAIL] Budget learning test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 12: Private deployment local-only learning")
    print("=" * 60)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            local_context = resolve_budget_learning_context(
                project_id="tenant_proj_local",
                root=Path(temp_dir),
                policy_overrides={
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "local_only",
                    "deployment_scope_id": "customer_a",
                    "environment_class": "sandbox",
                },
            )
            persist_budget_feedback_profile(
                local_context,
                {
                    "reviewed_count": 3,
                    "confirmed_count": 2,
                    "falsified_count": 1,
                    "hit_rate": 2 / 3,
                    "by_tier": {"A": {"reviewed_count": 2, "confirmed_count": 2, "falsified_count": 0, "hit_rate": 1.0}},
                },
            )
            loaded_local = load_budget_feedback_profile(local_context)
            print(f"[OK] Local-only context: {local_context}")
            print(f"[OK] Local-only summary: {loaded_local}")
            if not local_context.get("project_store_path").exists():
                print("[FAIL] Project-local learning store was not created")
                return False
            if local_context.get("deployment_store_path").exists():
                print("[FAIL] Local-only mode should not create a deployment-wide learning store")
                return False
            if loaded_local.get("reviewed_count") != 3:
                print(f"[FAIL] Local-only summary did not round-trip: {loaded_local}")
                return False
    except Exception as e:
        print(f"[FAIL] Local-only learning test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 13: Deployment import/sync learning modes")
    print("=" * 60)

    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            shared_root = Path(temp_dir)
            deployment_context = resolve_budget_learning_context(
                project_id="tenant_proj_seed",
                root=shared_root,
                policy_overrides={
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "sanitized_export_import",
                    "deployment_scope_id": "customer_group",
                    "environment_class": "sandbox",
                },
            )
            persist_budget_feedback_profile(
                deployment_context,
                {
                    "reviewed_count": 10,
                    "confirmed_count": 5,
                    "falsified_count": 5,
                    "hit_rate": 0.5,
                    "by_tier": {"B": {"reviewed_count": 4, "confirmed_count": 1, "falsified_count": 3, "hit_rate": 0.25}},
                },
                source_mode="sanitized_import",
            )
            import_only_context = resolve_budget_learning_context(
                project_id="tenant_proj_consumer",
                root=shared_root,
                policy_overrides={
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "import_only",
                    "deployment_scope_id": "customer_group",
                    "environment_class": "sandbox",
                },
            )
            loaded_import = load_budget_feedback_profile(import_only_context)
            print(f"[OK] Import-only summary: {loaded_import}")
            if not import_only_context.get("allow_deployment_read"):
                print("[FAIL] Import-only mode should allow deployment-level read")
                return False
            if import_only_context.get("allow_deployment_write"):
                print("[FAIL] Import-only mode should not allow deployment-level write")
                return False
            if abs(float(loaded_import.get("hit_rate", 0.0)) - 0.5) > 1e-9:
                print(f"[FAIL] Import-only mode did not read deployment profile: {loaded_import}")
                return False
    except Exception as e:
        print(f"[FAIL] Deployment import/sync mode test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 14: Resolve deployment config precedence")
    print("=" * 60)

    old_env = {
        "QUALIBUG_PROJECT_ID": os.environ.get("QUALIBUG_PROJECT_ID"),
        "QUALIBUG_DEPLOYMENT_MODE": os.environ.get("QUALIBUG_DEPLOYMENT_MODE"),
        "QUALIBUG_LEARNING_SYNC_MODE": os.environ.get("QUALIBUG_LEARNING_SYNC_MODE"),
        "QUALIBUG_DEPLOYMENT_SCOPE_ID": os.environ.get("QUALIBUG_DEPLOYMENT_SCOPE_ID"),
        "QUALIBUG_ENVIRONMENT_CLASS": os.environ.get("QUALIBUG_ENVIRONMENT_CLASS"),
    }
    try:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            save_real_project_inputs(
                "resolver_proj",
                {
                    "deployment_mode": "dedicated_cloud",
                    "learning_sync_mode": "import_only",
                    "deployment_scope_id": "project_scope",
                    "environment_class": "staging",
                },
                root=root,
            )
            os.environ["QUALIBUG_PROJECT_ID"] = "resolver_proj"
            os.environ["QUALIBUG_DEPLOYMENT_MODE"] = "public_saas"
            os.environ["QUALIBUG_LEARNING_SYNC_MODE"] = "sanitized_api_sync"
            os.environ["QUALIBUG_DEPLOYMENT_SCOPE_ID"] = "env_scope"
            os.environ["QUALIBUG_ENVIRONMENT_CLASS"] = "prod_like"

            resolved = resolve_deployment_config(root=root)
            override_resolved = resolve_deployment_config(
                root=root,
                overrides={
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "customer_hub_sync",
                    "deployment_scope_id": "override_scope",
                },
            )
            print(f"[OK] Resolved config: {resolved}")
            print(f"[OK] Override config: {override_resolved}")
            if resolved.get("deployment_mode") != "public_saas":
                print("[FAIL] Environment variable did not override project config")
                return False
            if resolved.get("learning_sync_mode") != "sanitized_api_sync":
                print("[FAIL] Environment variable sync mode did not override project config")
                return False
            if resolved.get("_sources", {}).get("deployment_mode") != "env":
                print(f"[FAIL] Source tracking incorrect: {resolved.get('_sources')}")
                return False
            if override_resolved.get("deployment_mode") != "private_deployment":
                print("[FAIL] Explicit override did not take highest precedence")
                return False
            if override_resolved.get("_sources", {}).get("deployment_mode") != "override":
                print(f"[FAIL] Override source tracking incorrect: {override_resolved.get('_sources')}")
                return False
    except Exception as e:
        print(f"[FAIL] Deployment config resolver test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    print()
    print("=" * 60)
    print("Test 15: Audit deployment config snapshot in loop outputs")
    print("=" * 60)

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        project_id = "audit_proj"
        report = build_agent_discovery_loop(
            project_id=project_id,
            root=root,
            options={
                "max_next_actions": 2,
                "deployment_config": {
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "customer_hub_sync",
                    "deployment_scope_id": "audit_scope",
                    "environment_class": "staging",
                },
            },
        )
        print(f"[OK] Loop report deployment config: {report.get('deployment_config')}")
        if report.get("deployment_config", {}).get("learning_sync_mode") != "customer_hub_sync":
            print("[FAIL] Loop report missing deployment config snapshot")
            return False
        dispatch_path = root / "platform_outputs" / project_id / "agent_discovery_loop" / "next_best_experiment_manifest.json"
        dispatch_payload = json.loads(dispatch_path.read_text(encoding="utf-8"))
        if dispatch_payload.get("deployment_config", {}).get("deployment_scope_id") != "audit_scope":
            print("[FAIL] Dispatch manifest missing deployment config snapshot")
            return False
        ledger_path = root / "platform_workspace" / project_id / "agent_discovery_loop" / "canonical_discovery_ledger.sqlite3"
        with sqlite3.connect(ledger_path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM loop_events WHERE event_type = 'loop_iteration_planned' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        payload = json.loads(row[0]) if row and row[0] else {}
        print(f"[OK] Loop event payload: {payload}")
        if payload.get("deployment_config", {}).get("environment_class") != "staging":
            print("[FAIL] Loop event payload missing deployment config snapshot")
            return False
    except Exception as e:
        print(f"[FAIL] Deployment config audit test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 16: Detect deployment config drift across loop runs")
    print("=" * 60)

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        project_id = "drift_proj"
        build_agent_discovery_loop(
            project_id=project_id,
            root=root,
            options={
                "max_next_actions": 2,
                "deployment_config": {
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "local_only",
                    "deployment_scope_id": "tenant_a",
                    "environment_class": "sandbox",
                },
            },
        )
        report = build_agent_discovery_loop(
            project_id=project_id,
            root=root,
            options={
                "max_next_actions": 2,
                "deployment_config": {
                    "deployment_mode": "public_saas",
                    "learning_sync_mode": "sanitized_api_sync",
                    "deployment_scope_id": "tenant_b",
                    "environment_class": "prod_like",
                },
            },
        )
        drift = report.get("deployment_config_drift", {})
        print(f"[OK] Drift summary: {drift}")
        if drift.get("status") != "drifted":
            print("[FAIL] Second run did not report drift")
            return False
        if drift.get("severity") != "high":
            print("[FAIL] High-risk deployment drift was not classified as high")
            return False
        changed = set(drift.get("changed_fields", []) or [])
        if not {"deployment_mode", "deployment_scope_id", "environment_class"}.issubset(changed):
            print(f"[FAIL] Missing expected changed fields: {changed}")
            return False
        dispatch_path = root / "platform_outputs" / project_id / "agent_discovery_loop" / "next_best_experiment_manifest.json"
        dispatch_payload = json.loads(dispatch_path.read_text(encoding="utf-8"))
        if dispatch_payload.get("deployment_config_drift", {}).get("severity") != "high":
            print("[FAIL] Dispatch manifest missing drift summary")
            return False
        ledger_path = root / "platform_workspace" / project_id / "agent_discovery_loop" / "canonical_discovery_ledger.sqlite3"
        with sqlite3.connect(ledger_path) as connection:
            row = connection.execute(
                "SELECT payload_json FROM loop_events WHERE event_type = 'loop_iteration_planned' ORDER BY rowid DESC LIMIT 1"
            ).fetchone()
        payload = json.loads(row[0]) if row and row[0] else {}
        if payload.get("deployment_config_drift", {}).get("severity") != "high":
            print("[FAIL] Loop event payload missing drift summary")
            return False
    except Exception as e:
        print(f"[FAIL] Deployment drift detection test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 17: Approve drift with graded unlock")
    print("=" * 60)

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        previous_snapshot = build_deployment_config_snapshot(
            {
                "project_id": "unlock_proj",
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_a",
                "environment_class": "sandbox",
                "policy_version": "v1.0.0-baseline",
            }
        )
        current_snapshot = build_deployment_config_snapshot(
            {
                "project_id": "unlock_proj",
                "deployment_mode": "public_saas",
                "learning_sync_mode": "sanitized_api_sync",
                "deployment_scope_id": "tenant_b",
                "environment_class": "prod_like",
                "policy_version": "v1.0.0-baseline",
            }
        )
        drift_summary = detect_deployment_config_drift(current_snapshot, previous_snapshot)
        unapproved_unlock = evaluate_deployment_drift_unlock(current_snapshot, drift_summary, root=root)
        approval_record = approve_deployment_config_drift(
            current_snapshot,
            approver="ops_admin",
            unlock_level="normal",
            ttl_hours=24,
            root=root,
        )
        approved_unlock = evaluate_deployment_drift_unlock(current_snapshot, drift_summary, root=root)
        print(f"[OK] Unapproved unlock: {unapproved_unlock}")
        print(f"[OK] Approval record: {approval_record}")
        print(f"[OK] Approved unlock: {approved_unlock}")
        if unapproved_unlock.get("status") != "unapproved":
            print("[FAIL] Drift should require approval before unlock")
            return False
        if approved_unlock.get("status") != "approved_limited":
            print("[FAIL] High-risk drift should only allow limited unlock")
            return False
        if approved_unlock.get("effective_unlock_level") != "limited":
            print("[FAIL] High-risk drift did not downgrade normal request to limited unlock")
            return False

        budget_candidates = [
            merged_hypothesis,
            medium_hypothesis,
            {
                "hypothesis_id": "exec_candidate_3",
                "title": "Authorization boundary may drift",
                "severity": "P1",
                "verification_method": {"step1": "POST /api/orders", "step2": "GET /api/orders"},
                "_merged_sources": ["authorization", "local_analyzer"],
            },
        ]
        base_settings = {
            **budget_settings,
            "overall_max_hypotheses": 3,
            "route_surface_size": 1,
            "drift_unlock_status": "unapproved",
            "drift_effective_unlock_level": "restricted",
            "drift_severity": "high",
        }
        restricted_plan, restricted_summary = _plan_execution_budget(
            budget_candidates,
            base_settings,
            _summarize_execution_feedback([]),
        )
        limited_plan, limited_summary = _plan_execution_budget(
            budget_candidates,
            {
                **budget_settings,
                "overall_max_hypotheses": 3,
                "route_surface_size": 1,
                "drift_unlock_status": "approved_limited",
                "drift_effective_unlock_level": "limited",
                "drift_severity": "high",
            },
            _summarize_execution_feedback([]),
        )
        normal_plan, normal_summary = _plan_execution_budget(
            budget_candidates,
            {
                **budget_settings,
                "overall_max_hypotheses": 3,
                "route_surface_size": 1,
                "drift_unlock_status": "approved_normal",
                "drift_effective_unlock_level": "normal",
                "drift_severity": "low",
            },
            _summarize_execution_feedback([]),
        )
        print(f"[OK] Restricted summary: {restricted_summary}")
        print(f"[OK] Limited summary: {limited_summary}")
        print(f"[OK] Normal summary: {normal_summary}")
        if not (
            restricted_summary.get("target_execute", 0)
            <= limited_summary.get("target_execute", 0)
            <= normal_summary.get("target_execute", 0)
        ):
            print("[FAIL] Graded unlock did not progressively relax execution budget")
            return False
        if restricted_summary.get("drift_guard", {}).get("status") != "unapproved":
            print("[FAIL] Restricted drift guard status missing from budget summary")
            return False
    except Exception as e:
        print(f"[FAIL] Drift approval unlock test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 18: Onboarding approval entrypoints")
    print("=" * 60)

    temp_dir = None
    old_real_project_id = os.environ.get("REAL_PROJECT_ID")
    old_onboarding_root = onboarding_module.ROOT
    old_cli_env = {
        "QUALIBUG_DEPLOYMENT_MODE": os.environ.get("QUALIBUG_DEPLOYMENT_MODE"),
        "QUALIBUG_LEARNING_SYNC_MODE": os.environ.get("QUALIBUG_LEARNING_SYNC_MODE"),
        "QUALIBUG_DEPLOYMENT_SCOPE_ID": os.environ.get("QUALIBUG_DEPLOYMENT_SCOPE_ID"),
        "QUALIBUG_ENVIRONMENT_CLASS": os.environ.get("QUALIBUG_ENVIRONMENT_CLASS"),
    }
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        onboarding_module.ROOT = root
        project_id = "approve_cli_proj"
        save_real_project_inputs(
            project_id,
            {
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_a",
                "environment_class": "sandbox",
            },
            root=root,
        )
        build_agent_discovery_loop(
            project_id=project_id,
            root=root,
            options={
                "deployment_config": {
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "local_only",
                    "deployment_scope_id": "tenant_a",
                    "environment_class": "sandbox",
                }
            },
        )
        status_before = inspect_deployment_drift(
            project_id,
            root=root,
            overrides={
                "deployment_mode": "public_saas",
                "learning_sync_mode": "sanitized_api_sync",
                "deployment_scope_id": "tenant_b",
                "environment_class": "prod_like",
            },
        )
        print(f"[OK] Drift status before approval: {status_before}")
        if status_before.get("deployment_drift_unlock", {}).get("status") != "unapproved":
            print("[FAIL] Drift inspection should require approval before onboarding approval entrypoint runs")
            return False
        rejection_result = approve_current_deployment_drift(
            project_id,
            approver="qa_lead_user",
            approver_role="qa_lead",
            unlock_level="normal",
            ttl_hours=12,
            comment="should be rejected for high risk drift",
            root=root,
            overrides={
                "deployment_mode": "public_saas",
                "learning_sync_mode": "sanitized_api_sync",
                "deployment_scope_id": "tenant_b",
                "environment_class": "prod_like",
            },
        )
        print(f"[OK] Function rejection result: {rejection_result}")
        if rejection_result.get("ok"):
            print("[FAIL] High-risk drift should reject qa_lead approval")
            return False
        required_roles = rejection_result.get("required_roles") or []
        if "security_owner" not in required_roles:
            print("[FAIL] Rejection result did not expose required approver roles")
            return False
        approval_result = approve_current_deployment_drift(
            project_id,
            approver="security_reviewer",
            approver_role="security_owner",
            unlock_level="normal",
            ttl_hours=12,
            comment="planned rollout",
            root=root,
            overrides={
                "deployment_mode": "public_saas",
                "learning_sync_mode": "sanitized_api_sync",
                "deployment_scope_id": "tenant_b",
                "environment_class": "prod_like",
            },
        )
        print(f"[OK] Function approval result: {approval_result}")
        if not approval_result.get("ok"):
            print("[FAIL] Function approval entrypoint did not approve drift")
            return False
        if approval_result.get("deployment_drift_unlock", {}).get("status") != "approved_limited":
            print("[FAIL] Approval entrypoint did not return graded unlock result")
            return False
        os.environ["REAL_PROJECT_ID"] = project_id
        os.environ["QUALIBUG_DEPLOYMENT_MODE"] = "public_saas"
        os.environ["QUALIBUG_LEARNING_SYNC_MODE"] = "sanitized_api_sync"
        os.environ["QUALIBUG_DEPLOYMENT_SCOPE_ID"] = "tenant_b"
        os.environ["QUALIBUG_ENVIRONMENT_CLASS"] = "prod_like"
        cli_rc = onboarding_module.main([
            "approve-drift",
            project_id,
            "--approver",
            "cli_admin",
            "--approver-role",
            "admin",
            "--actor-id",
            "cli_admin_subject",
            "--project-binding",
            project_id,
            "--scope-binding",
            "tenant_b",
            "--environment-binding",
            "prod_like",
            "--identity-source",
            "sso_claims",
            "--unlock-level",
            "normal",
            "--ttl-hours",
            "6",
            "--comment",
            "cli approval refresh",
        ])
        print(f"[OK] CLI approval return code: {cli_rc}")
        if cli_rc != 0:
            print("[FAIL] CLI approval entrypoint returned non-zero exit code")
            return False
    except Exception as e:
        print(f"[FAIL] Onboarding approval entrypoint test failed during setup: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        onboarding_module.ROOT = old_onboarding_root
        for key, value in old_cli_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        if old_real_project_id is None:
            os.environ.pop("REAL_PROJECT_ID", None)
        else:
            os.environ["REAL_PROJECT_ID"] = old_real_project_id
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 19: Drift role matrix exposure")
    print("=" * 60)

    try:
        role_matrix = required_deployment_drift_roles(
            {
                "project_id": "role_matrix_proj",
                "deployment_mode": "public_saas",
                "learning_sync_mode": "sanitized_api_sync",
                "deployment_scope_id": "tenant_b",
                "environment_class": "prod_like",
                "policy_version": "v1.0.0-baseline",
            },
            {
                "status": "drifted",
                "severity": "high",
                "changed_fields": ["deployment_mode", "environment_class"],
            },
        )
        print(f"[OK] Required roles for high-risk drift: {role_matrix}")
        if role_matrix != ["security_owner", "testops_admin", "admin"]:
            print("[FAIL] High-risk drift role matrix is not stable")
            return False
    except Exception as e:
        print(f"[FAIL] Drift role matrix test failed: {e}")
        import traceback
        traceback.print_exc()
        return False

    print()
    print("=" * 60)
    print("Test 20: Context-aware approval scope binding")
    print("=" * 60)

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        previous_snapshot = build_deployment_config_snapshot(
            {
                "project_id": "context_proj",
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_a",
                "environment_class": "sandbox",
                "policy_version": "v1.0.0-baseline",
            }
        )
        current_snapshot = build_deployment_config_snapshot(
            {
                "project_id": "context_proj",
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_a",
                "environment_class": "sandbox",
                "policy_version": "v1.0.1-review",
            }
        )
        drift_summary = detect_deployment_config_drift(current_snapshot, previous_snapshot)
        rejected_validation = validate_deployment_drift_approval(
            current_snapshot,
            drift_summary,
            approver="tenant_owner_bad",
            approver_role="tenant_admin",
            approver_context={
                "actor_id": "tenant_owner_bad",
                "project_bindings": "context_proj",
                "deployment_scope_bindings": "tenant_b",
                "environment_bindings": "sandbox",
                "identity_source": "tenant_rbac",
            },
        )
        approved_validation = validate_deployment_drift_approval(
            current_snapshot,
            drift_summary,
            approver="tenant_owner_good",
            approver_role="tenant_admin",
            approver_context={
                "actor_id": "tenant_owner_good",
                "project_bindings": "context_proj",
                "deployment_scope_bindings": "tenant_a",
                "environment_bindings": "sandbox",
                "identity_source": "tenant_rbac",
            },
        )
        print(f"[OK] Rejected validation: {rejected_validation}")
        print(f"[OK] Approved validation: {approved_validation}")
        if rejected_validation.get("context_allowed"):
            print("[FAIL] Mismatched tenant scope should reject tenant_admin approval context")
            return False
        if not approved_validation.get("overall_allowed"):
            print("[FAIL] Matching tenant scope context should approve tenant_admin validation")
            return False

        approval_record = approve_deployment_config_drift(
            current_snapshot,
            approver="tenant_owner_good",
            approver_role="tenant_admin",
            approver_context={
                "actor_id": "tenant_owner_good",
                "project_bindings": "context_proj",
                "deployment_scope_bindings": "tenant_a",
                "environment_bindings": "sandbox",
                "identity_source": "tenant_rbac",
            },
            unlock_level="normal",
            ttl_hours=8,
            root=root,
        )
        unlock = evaluate_deployment_drift_unlock(current_snapshot, drift_summary, root=root)
        print(f"[OK] Context-aware approval record: {approval_record}")
        print(f"[OK] Context-aware unlock: {unlock}")
        if unlock.get("status") != "approved_normal":
            print("[FAIL] Low-risk drift with matching scope context should unlock normally")
            return False
        if (
            ((unlock.get("approval") or {}).get("approver_context") or {}).get("deployment_scope_bindings")
            != ["tenant_a"]
        ):
            print("[FAIL] Approval record did not persist normalized approver context")
            return False
    except Exception as e:
        print(f"[FAIL] Context-aware approval scope test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 21: Auto-resolve approver context from identity registry")
    print("=" * 60)

    temp_dir = None
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        project_id = "identity_registry_proj"
        save_approver_identity_registry(
            project_id,
            {
                "project_members": [
                    {
                        "actor_id": "tenant_owner_auto",
                        "roles": ["tenant_admin"],
                        "project_ids": [project_id],
                        "environment_classes": ["sandbox"],
                    }
                ],
                "tenant_rbac": [
                    {
                        "actor_id": "tenant_owner_auto",
                        "roles": ["tenant_admin"],
                        "tenant_ids": ["tenant_auto"],
                    }
                ],
                "sso_claims": [
                    {
                        "actor_id": "tenant_owner_auto",
                        "roles": ["tenant_admin"],
                        "project_ids": [project_id],
                        "tenant_ids": ["tenant_auto"],
                        "environment_classes": ["sandbox"],
                        "identity_source": "sso_claims",
                    }
                ],
            },
            root=root,
        )
        current_snapshot = build_deployment_config_snapshot(
            {
                "project_id": project_id,
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_auto",
                "environment_class": "sandbox",
                "policy_version": "v1.0.0-baseline",
                "_sources": {
                    "deployment_mode": "override",
                    "learning_sync_mode": "override",
                    "deployment_scope_id": "override",
                    "environment_class": "override",
                },
            }
        )
        previous_snapshot = build_deployment_config_snapshot(
            {
                "project_id": project_id,
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_auto",
                "environment_class": "sandbox",
                "policy_version": "v1.0.0-baseline",
                "_sources": {
                    "deployment_mode": "project_config",
                    "learning_sync_mode": "project_config",
                    "deployment_scope_id": "project_config",
                    "environment_class": "project_config",
                },
            }
        )
        persist_deployment_config_snapshot(previous_snapshot, root=root)
        drift_summary = detect_deployment_config_drift(current_snapshot, previous_snapshot)
        auto_context = resolve_approver_context(
            project_id,
            approver="tenant_owner_auto",
            approver_role="tenant_admin",
            current_snapshot=current_snapshot,
            root=root,
        )
        print(f"[OK] Auto-resolved approver context: {auto_context}")
        if auto_context.get("deployment_scope_bindings") != ["tenant_auto"]:
            print("[FAIL] Tenant RBAC scope was not auto-resolved")
            return False
        approval_result = approve_current_deployment_drift(
            project_id,
            approver="tenant_owner_auto",
            approver_role="tenant_admin",
            unlock_level="normal",
            ttl_hours=8,
            root=root,
            overrides={
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_auto",
                "environment_class": "sandbox",
            },
        )
        print(f"[OK] Auto-resolved approval result: {approval_result}")
        if not approval_result.get("ok"):
            print("[FAIL] Auto-resolved approver context did not allow approval")
            return False
        resolved_context = approval_result.get("resolved_approver_context") or {}
        if resolved_context.get("identity_source") != "sso_claims":
            print("[FAIL] Strongest identity source should prefer sso_claims when available")
            return False
        if "tenant_rbac" not in set(resolved_context.get("resolution_sources") or []):
            print("[FAIL] Resolution trace did not include tenant_rbac source")
            return False
    except Exception as e:
        print(f"[FAIL] Auto-resolve approver context test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 22: Resolve approver identity entrypoint")
    print("=" * 60)

    temp_dir = None
    old_onboarding_root = onboarding_module.ROOT
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        onboarding_module.ROOT = root
        project_id = "resolve_approver_proj"
        save_approver_identity_registry(
            project_id,
            {
                "project_members": [
                    {
                        "actor_id": "qa_resolver",
                        "roles": ["qa_lead"],
                        "project_ids": [project_id],
                        "environment_classes": ["sandbox"],
                    }
                ],
                "sso_claims": [
                    {
                        "actor_id": "qa_resolver",
                        "roles": ["qa_lead"],
                        "project_ids": [project_id],
                        "environment_classes": ["sandbox"],
                        "identity_source": "sso_claims",
                    }
                ],
            },
            root=root,
        )
        save_real_project_inputs(
            project_id,
            {
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_preview",
                "environment_class": "sandbox",
            },
            root=root,
        )
        build_agent_discovery_loop(
            project_id=project_id,
            root=root,
            options={
                "deployment_config": {
                    "deployment_mode": "private_deployment",
                    "learning_sync_mode": "local_only",
                    "deployment_scope_id": "tenant_preview",
                    "environment_class": "sandbox",
                }
            },
        )
        inspection = inspect_approver_identity_resolution(
            project_id,
            approver="qa_resolver",
            approver_role="qa_lead",
            root=root,
            overrides={
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_preview",
                "environment_class": "sandbox",
            },
        )
        print(f"[OK] Approver resolution inspection: {inspection}")
        if not inspection.get("approval_validation", {}).get("overall_allowed"):
            print("[FAIL] Identity resolution inspection should approve matching qa_lead context")
            return False
        if not str((inspection.get("identity_registry_paths") or {}).get("registry", "")).endswith("approver_identity_registry.json"):
            print("[FAIL] Inspection did not expose registry file locations")
            return False
        cli_rc = onboarding_module.main([
            "resolve-approver",
            project_id,
            "--approver",
            "qa_resolver",
            "--approver-role",
            "qa_lead",
        ])
        print(f"[OK] Resolve-approver CLI return code: {cli_rc}")
        if cli_rc != 0:
            print("[FAIL] resolve-approver CLI entrypoint returned non-zero exit code")
            return False
    except Exception as e:
        print(f"[FAIL] Resolve approver identity entrypoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        onboarding_module.ROOT = old_onboarding_root
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 23: Save approver identity registry entrypoint")
    print("=" * 60)

    temp_dir = None
    old_onboarding_root = onboarding_module.ROOT
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        onboarding_module.ROOT = root
        project_id = "save_identity_proj"
        save_result = save_approver_identity_inputs(
            project_id,
            registry={
                "project_members": [
                    {
                        "actor_id": "ops_owner",
                        "roles": ["project_owner"],
                        "project_ids": [project_id],
                        "environment_classes": ["staging"],
                    }
                ]
            },
            tenant_rbac=[
                {
                    "actor_id": "ops_owner",
                    "roles": ["project_owner"],
                    "tenant_ids": ["tenant_stage"],
                }
            ],
            sso_claims=[
                {
                    "actor_id": "ops_owner",
                    "roles": ["project_owner"],
                    "project_ids": [project_id],
                    "tenant_ids": ["tenant_stage"],
                    "environment_classes": ["staging"],
                    "identity_source": "sso_claims",
                }
            ],
            root=root,
        )
        print(f"[OK] Save approver identity result: {save_result}")
        if not save_result.get("ok"):
            print("[FAIL] save_approver_identity_inputs should report success")
            return False
        save_real_project_inputs(
            project_id,
            {
                "deployment_mode": "dedicated_cloud",
                "learning_sync_mode": "import_only",
                "deployment_scope_id": "tenant_stage",
                "environment_class": "staging",
            },
            root=root,
        )
        inspection = inspect_approver_identity_resolution(
            project_id,
            approver="ops_owner",
            approver_role="project_owner",
            root=root,
        )
        print(f"[OK] Inspection after save entrypoint: {inspection}")
        if inspection.get("resolved_approver_context", {}).get("identity_source") != "sso_claims":
            print("[FAIL] Saved identity inputs were not consumed by resolver")
            return False
        cli_rc = onboarding_module.main([
            "save-approver-identity",
            project_id,
            "--project-members-json",
            '[{"actor_id":"qa_saved","roles":["qa_lead"],"project_ids":["save_identity_proj"],"environment_classes":["staging"]}]',
            "--sso-claims-json",
            '[{"actor_id":"qa_saved","roles":["qa_lead"],"project_ids":["save_identity_proj"],"environment_classes":["staging"],"identity_source":"sso_claims"}]',
        ])
        print(f"[OK] Save-approver-identity CLI return code: {cli_rc}")
        if cli_rc != 0:
            print("[FAIL] save-approver-identity CLI entrypoint returned non-zero exit code")
            return False
    except Exception as e:
        print(f"[FAIL] Save approver identity entrypoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        onboarding_module.ROOT = old_onboarding_root
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 24: Approver identity templates and onboarding hints")
    print("=" * 60)

    temp_dir = None
    old_onboarding_root = onboarding_module.ROOT
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        onboarding_module.ROOT = root
        project_id = "template_identity_proj"
        save_real_project_inputs(
            project_id,
            {
                "deployment_mode": "dedicated_cloud",
                "learning_sync_mode": "import_only",
                "deployment_scope_id": "tenant_template",
                "environment_class": "staging",
            },
            root=root,
        )
        missing_inputs = inspect_approver_identity_inputs(project_id, root=root)
        print(f"[OK] Missing identity inputs inspection: {missing_inputs}")
        if missing_inputs.get("has_any_inputs"):
            print("[FAIL] Fresh project should not report existing identity inputs")
            return False
        if "init-approver-identity-template" not in str(missing_inputs.get("suggested_command") or ""):
            print("[FAIL] Missing identity inputs inspection should suggest template initialization")
            return False

        onboarding_result = onboarding_module.run_onboarding_check(project_id, root=root)
        print(f"[OK] Onboarding with missing identity inputs: {onboarding_result}")
        identity_checks = [item for item in onboarding_result.get("checks", []) if item.get("name") == "approver_identity_inputs"]
        if not identity_checks or identity_checks[0].get("status") != "missing":
            print("[FAIL] Onboarding should expose missing approver identity inputs")
            return False

        template_result = init_approver_identity_templates(project_id, root=root)
        print(f"[OK] Init identity templates result: {template_result}")
        if not template_result.get("ok"):
            print("[FAIL] Identity template initialization should write sample files")
            return False
        cli_rc = onboarding_module.main([
            "init-approver-identity-template",
            project_id,
            "--overwrite",
        ])
        print(f"[OK] Init-approver-identity-template CLI return code: {cli_rc}")
        if cli_rc != 0:
            print("[FAIL] init-approver-identity-template CLI entrypoint returned non-zero exit code")
            return False

        configured_inputs = inspect_approver_identity_inputs(project_id, root=root)
        print(f"[OK] Configured identity inputs inspection: {configured_inputs}")
        if not configured_inputs.get("has_any_inputs"):
            print("[FAIL] Template generation should create usable identity input files")
            return False
        if configured_inputs.get("section_counts", {}).get("sso_claims", 0) <= 0:
            print("[FAIL] Generated templates should include sample SSO claims")
            return False
    except Exception as e:
        print(f"[FAIL] Approver identity templates and hints test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        onboarding_module.ROOT = old_onboarding_root
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("Test 25: Identity status entrypoint")
    print("=" * 60)

    temp_dir = None
    old_onboarding_root = onboarding_module.ROOT
    try:
        temp_dir = tempfile.mkdtemp()
        root = Path(temp_dir)
        onboarding_module.ROOT = root
        project_id = "identity_status_proj"
        save_real_project_inputs(
            project_id,
            {
                "deployment_mode": "private_deployment",
                "learning_sync_mode": "local_only",
                "deployment_scope_id": "tenant_status",
                "environment_class": "sandbox",
            },
            root=root,
        )
        missing_status = inspect_identity_status(project_id, root=root)
        print(f"[OK] Missing identity status: {missing_status}")
        if missing_status.get("identity_inputs", {}).get("has_any_inputs"):
            print("[FAIL] identity-status should report missing inputs for fresh project")
            return False
        missing_cli_rc = onboarding_module.main(["identity-status", project_id])
        print(f"[OK] Identity-status CLI missing return code: {missing_cli_rc}")
        if missing_cli_rc != 2:
            print("[FAIL] identity-status CLI should return 2 when identity inputs are missing")
            return False

        init_approver_identity_templates(project_id, root=root, overwrite=True)
        configured_status = inspect_identity_status(
            project_id,
            approver="project_owner_demo",
            approver_role="project_owner",
            root=root,
        )
        print(f"[OK] Configured identity status: {configured_status}")
        if not configured_status.get("identity_inputs", {}).get("has_any_inputs"):
            print("[FAIL] identity-status should report configured inputs after template init")
            return False
        preview = configured_status.get("approver_preview") or {}
        if preview.get("resolved_approver_context", {}).get("identity_source") not in {"local_config", "sso_claims"}:
            print("[FAIL] identity-status approver preview did not expose resolved identity source")
            return False
        configured_cli_rc = onboarding_module.main([
            "identity-status",
            project_id,
            "--approver",
            "project_owner_demo",
            "--approver-role",
            "project_owner",
        ])
        print(f"[OK] Identity-status CLI configured return code: {configured_cli_rc}")
        if configured_cli_rc != 0:
            print("[FAIL] identity-status CLI should return 0 when identity inputs exist")
            return False
    except Exception as e:
        print(f"[FAIL] Identity status entrypoint test failed: {e}")
        import traceback
        traceback.print_exc()
        return False
    finally:
        onboarding_module.ROOT = old_onboarding_root
        if temp_dir:
            shutil.rmtree(temp_dir, ignore_errors=True)

    print()
    print("=" * 60)
    print("All tests passed!")
    print("=" * 60)
    return True


def main():
    success = test_analyzers_adapter()
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())
