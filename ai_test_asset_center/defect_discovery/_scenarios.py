"""Business scenario generation, execution readiness, data orchestration."""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_test_asset_center.adaptive_probe_optimizer import build_learned_probe_policy

from ._common import *  # noqa: F401,F403
from ._model import *  # noqa: F401,F403


def infer_business_scenarios(operations: list[dict], prd: str) -> list[dict]:
    scenarios: list[dict] = []
    by_resource: dict[str, list[dict]] = {}
    for op in operations:
        by_resource.setdefault(op["resource"], []).append(op)
    for resource, ops in by_resource.items():
        if resource in {"login", "reset", "health"}:
            continue
        create_ops = [op for op in ops if op["method"] == "POST" and op["operation"] == "create_or_action"]
        read_ops = [op for op in ops if op["method"] == "GET"]
        state_ops = [op for op in ops if op["operation"] in {"state_cancel", "payment", "refund", "callback"}]
        if create_ops and read_ops:
            scenarios.append(
                scenario(
                    f"SCN_{resource.upper()}_CREATE_READ",
                    f"{resource} 创建后查询一致性",
                    "core_lifecycle",
                    [create_ops[0], read_ops[0]],
                    ["state_consistency", "idor", "tenant_isolation"],
                )
            )
        if create_ops and state_ops:
            scenarios.append(
                scenario(
                    f"SCN_{resource.upper()}_STATE_FLOW",
                    f"{resource} 状态流转一致性",
                    "state_machine",
                    [create_ops[0], *state_ops[:3]],
                    ["state_flow", "idempotency", "audit_log_missing"],
                )
            )
    scenarios.extend(cross_resource_scenarios(operations, prd))
    return dedupe_scenarios(scenarios)


def scenario(scenario_id: str, title: str, scenario_type: str, ops: list[dict], risks: list[str]) -> dict:
    return {
        "scenario_id": scenario_id,
        "title": title,
        "scenario_type": scenario_type,
        "steps": [f"{op['method']} {op['path']}" for op in ops],
        "resources": sorted({op["resource"] for op in ops}),
        "risk_focus": sorted(set(risks)),
        "oracle": "跨步骤状态、归属、金额/数量和下游可见性保持一致",
        "source": "single_input_auto_planner",
    }


def cross_resource_scenarios(operations: list[dict], prd: str) -> list[dict]:
    from ._model import _FULFILLMENT_PARENT_LIKE, _INVENTORY_LIKE, _OWNED_RECORD_LIKE, _OWNERSHIP_PARENT_LIKE, _PAYMENT_LIKE, _REFUND_LIKE, _resource_in  # lazy: avoid circular import
    """Build cross-resource scenarios from OpenAPI role families — not mall path lists."""
    scenarios: list[dict] = []
    by_resource: dict[str, list[dict]] = {}
    for op in operations:
        by_resource.setdefault(str(op.get("resource") or ""), []).append(op)

    def _ops_for_family(family: set[str], *, methods: set[str] | None = None, limit: int = 3) -> list[dict]:
        from ._model import _FULFILLMENT_PARENT_LIKE, _INVENTORY_LIKE, _OWNED_RECORD_LIKE, _OWNERSHIP_PARENT_LIKE, _PAYMENT_LIKE, _REFUND_LIKE, _resource_in  # lazy: avoid circular import
        matched: list[dict] = []
        for resource, ops in by_resource.items():
            if not _resource_in(resource, family):
                continue
            for op in ops:
                if methods and str(op.get("method") or "").upper() not in methods:
                    continue
                matched.append(op)
                if len(matched) >= limit:
                    return matched
        return matched

    fulfillment = _ops_for_family(_FULFILLMENT_PARENT_LIKE, limit=4)
    payment = _ops_for_family(_PAYMENT_LIKE, methods={"POST", "PUT", "PATCH"}, limit=3)
    refund = _ops_for_family(_REFUND_LIKE, methods={"POST", "PUT", "PATCH"}, limit=2)
    inventory = _ops_for_family(_INVENTORY_LIKE, limit=3)
    ownership = _ops_for_family(_OWNERSHIP_PARENT_LIKE, limit=3)
    owned = _ops_for_family(_OWNED_RECORD_LIKE, limit=3)

    financial_flow = fulfillment[:2] + payment[:1] + refund[:1]
    if len(financial_flow) >= 2 and fulfillment and (payment or refund):
        scenarios.append(
            scenario(
                "SCN_FULFILLMENT_PAY_REFUND",
                "履约单支付/结算与退款资金一致性",
                "cross_resource_financial",
                financial_flow[:4],
                ["money_consistency", "state_flow", "refund_abuse"],
            )
        )

    cancel_ops = [
        op for op in fulfillment
        if str(op.get("operation") or "") == "state_cancel"
        or any(tok in str(op.get("path") or "").lower() for tok in ("cancel", "void", "close", "terminate"))
    ]
    stock_flow = inventory[:1] + fulfillment[:1] + cancel_ops[:1]
    if len(stock_flow) >= 2:
        scenarios.append(
            scenario(
                "SCN_CAPACITY_FULFILL_CANCEL",
                "容量/库存与履约取消一致性",
                "cross_resource_quantity",
                stock_flow[:3],
                ["quantity_consistency", "stock_consistency", "rollback_consistency"],
            )
        )

    scope_flow = ownership[:2] + owned[:2]
    if ownership and owned:
        scenarios.append(
            scenario(
                "SCN_OWNER_SCOPE_ISOLATION",
                "主体与从属记录数据范围隔离",
                "scope_and_permission",
                scope_flow[:4],
                ["tenant_isolation", "permission_bypass", "search_scope_leak", "idor"],
            )
        )
    elif any("tenant" in str(op.get("path") or "").lower() or "admin" in str(op.get("path") or "").lower() for op in operations):
        tenant_ops = [
            op for op in operations
            if any(tok in str(op.get("path") or "").lower() for tok in ("tenant", "admin", "org"))
        ][:3]
        if tenant_ops:
            scenarios.append(
                scenario(
                    "SCN_TENANT_ADMIN_SCOPE",
                    "租户与后台数据范围隔离",
                    "scope_and_permission",
                    tenant_ops,
                    ["tenant_isolation", "permission_bypass", "search_scope_leak"],
                )
            )

    approval_ops = [op for op in operations if "approval_bypass" in op.get("risk_hints", [])]
    export_ops = [op for op in operations if "export_permission" in op.get("risk_hints", [])]
    import_ops = [op for op in operations if "file_upload_validation" in op.get("risk_hints", [])]
    notify_ops = [op for op in operations if "notification_wrong_recipient" in op.get("risk_hints", [])]
    config_ops = [op for op in operations if "feature_flag_scope" in op.get("risk_hints", [])]
    if approval_ops:
        scenarios.append(scenario("SCN_APPROVAL_AUDIT", "审批流程与审计一致性", "workflow_audit", approval_ops[:3], ["approval_bypass", "audit_log_missing", "step_skip"]))
    if import_ops:
        scenarios.append(scenario("SCN_IMPORT_VALIDATE_ROLLBACK", "批量导入校验与部分失败处理", "batch_import", import_ops[:2], ["file_upload_validation", "duplicate_import", "bulk_operation_partial_failure"]))
    if export_ops:
        scenarios.append(scenario("SCN_REPORT_EXPORT_SCOPE", "报表导出范围与统计一致性", "report_export", export_ops[:2], ["export_permission", "report_aggregation_error", "privacy_scope"]))
    if notify_ops:
        scenarios.append(scenario("SCN_NOTIFICATION_RECIPIENT", "通知接收人与模板变量安全", "notification", notify_ops[:2], ["notification_wrong_recipient", "notification_duplicate", "template_variable_leak"]))
    if config_ops:
        scenarios.append(scenario("SCN_CONFIG_SCOPE", "租户配置与功能开关隔离", "configuration", config_ops[:2], ["feature_flag_scope", "tenant_config_isolation", "default_value_risk"]))
    return scenarios


def dedupe_scenarios(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = item["scenario_id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def evaluate_scenario_coverage(scenarios: list[dict], probes: list[dict], accounts: dict) -> dict:
    account_roles = {item.get("role") for item in accounts.get("accounts", [])}
    tenant_count = len({item.get("tenant_id") for item in accounts.get("accounts", []) if item.get("tenant_id")})
    probe_refs = [{"probe_id": p["probe_id"], "api": p.get("api_template") or f"{p['method']} {p['path'].split('?')[0]}", "risk_type": p["risk_type"], "source": p.get("source")} for p in probes]
    items = []
    for scenario_item in scenarios:
        covered_probes = []
        missing_steps = []
        for step_ref in scenario_item.get("steps", []):
            matched = [p for p in probe_refs if api_ref_compatible(p["api"], step_ref)]
            if matched:
                covered_probes.extend(matched)
            else:
                missing_steps.append(step_ref)
        blockers = []
        if "tenant_isolation" in scenario_item.get("risk_focus", []) and tenant_count < 2:
            blockers.append("需要至少两个租户账号")
        if any(risk in scenario_item.get("risk_focus", []) for risk in ["permission_bypass", "approval_bypass", "export_permission"]) and len(account_roles) < 2:
            blockers.append("需要至少两个不同角色账号")
        if missing_steps:
            blockers.append("缺少可匹配探针步骤：" + ", ".join(missing_steps[:3]))
        executable = not blockers
        covered = bool(covered_probes)
        items.append(
            {
                "scenario_id": scenario_item["scenario_id"],
                "title": scenario_item["title"],
                "scenario_type": scenario_item["scenario_type"],
                "risk_focus": scenario_item["risk_focus"],
                "step_count": len(scenario_item.get("steps", [])),
                "covered": covered,
                "executable": executable,
                "coverage_status": "covered" if covered and executable else "blocked" if blockers else "uncovered",
                "covered_probe_ids": sorted({p["probe_id"] for p in covered_probes}),
                "blocked_reasons": blockers,
            }
        )
    total = len(items)
    covered_count = sum(1 for item in items if item["covered"])
    executable_count = sum(1 for item in items if item["executable"])
    return {
        "scenario_count": total,
        "covered_scenarios": covered_count,
        "executable_scenarios": executable_count,
        "blocked_scenarios": sum(1 for item in items if item["blocked_reasons"]),
        "coverage_rate": round(covered_count / total, 4) if total else 0,
        "executable_rate": round(executable_count / total, 4) if total else 0,
        "items": items,
    }


def build_execution_readiness_plan(model: dict, scenario_coverage: dict, probes: list[dict], accounts: dict) -> dict:
    """Infer data, account, DB and dependency needs from the generated business model.

    The goal is to keep the input model zero-config: enterprise users provide PRD/OpenAPI/accounts,
    and the platform derives what must exist before a probe or journey can be executed reliably.
    """
    account_items = accounts.get("accounts", [])
    roles = sorted({item.get("role") for item in account_items if item.get("role")})
    tenants = sorted({item.get("tenant_id") for item in account_items if item.get("tenant_id")})
    scenarios_by_id = {item["scenario_id"]: item for item in model.get("business_scenarios", [])}
    requirements = []
    for coverage_item in scenario_coverage.get("items", []):
        scenario_item = scenarios_by_id.get(coverage_item["scenario_id"], {})
        risk_focus = set(coverage_item.get("risk_focus", []))
        resources = scenario_item.get("resources", [])
        requirements.append(
            {
                "scenario_id": coverage_item["scenario_id"],
                "title": coverage_item.get("title"),
                "automation_status": readiness_status(coverage_item),
                "required_accounts": required_accounts_for_risks(risk_focus, roles, tenants),
                "seed_data": seed_data_for_scenario(resources, risk_focus),
                "database_checkpoints": database_checkpoints_for_scenario(resources, risk_focus, model),
                "external_dependencies": external_dependencies_for_risks(risk_focus),
                "file_fixtures": file_fixtures_for_risks(risk_focus),
                "cleanup_strategy": cleanup_strategy_for_scenario(resources),
                "covered_probe_ids": coverage_item.get("covered_probe_ids", []),
                "blocked_reasons": coverage_item.get("blocked_reasons", []),
            }
        )
    gaps = build_testability_gaps(requirements)
    # Hard blockers: cannot produce reliable evidence without real data sources
    hard_blockers = [g for g in gaps if g["gap_type"] in (
        "database_checkpoint_template", "external_dependency_stub",
        "missing_account_role", "missing_tenant_account",
    )]
    return {
        "mode": "auto_inferred_from_single_input",
        "manual_test_data_design_required": False,
        "account_pool_summary": {"roles": roles, "tenants": tenants, "account_count": len(account_items)},
        "test_data_requirements": requirements,
        "testability_gaps": gaps,
        "hard_blockers": hard_blockers,  # Must be resolved before execution
        "hard_blocker_count": len(hard_blockers),
        "execution_readiness_plan": {
            "scenario_count": len(requirements),
            "auto_preparable_scenarios": sum(1 for item in requirements if item["automation_status"] == "auto_preparable"),
            "needs_environment_config_scenarios": sum(1 for item in requirements if item["automation_status"] == "needs_environment_config"),
            "blocked_scenarios": sum(1 for item in requirements if item["automation_status"] == "blocked"),
            "probe_count": len(probes),
            "next_actions": readiness_next_actions(requirements, gaps),
        },
    }


def build_scenario_data_orchestration(readiness: dict, accounts: dict) -> dict:
    requirements = readiness.get("test_data_requirements", [])
    account_items = accounts.get("accounts", [])
    account_aliases = build_account_aliases(account_items)
    scenarios = []
    for item in requirements:
        run_id = f"run_${{{item['scenario_id'].lower()}}}"
        scenarios.append(
            {
                "scenario_id": item["scenario_id"],
                "title": item.get("title"),
                "automation_status": item.get("automation_status"),
                "run_id_template": run_id,
                "account_bindings": account_bindings_for_requirement(item, account_items, account_aliases),
                "setup_steps": setup_steps_for_requirement(item, run_id),
                "assertion_steps": assertion_steps_for_requirement(item),
                "cleanup_steps": cleanup_steps_for_requirement(item, run_id),
                "blocked_reasons": item.get("blocked_reasons", []),
            }
        )
    blocked = sum(1 for item in scenarios if item["blocked_reasons"])
    return {
        "mode": "scenario_scoped_data_orchestration",
        "manual_fixture_authoring_required": False,
        "safety_policy": {
            "scope_key": "test_run_id",
            "destructive_cleanup_allowed": False,
            "cleanup_requires_scope_filter": True,
            "prefer_business_api_seed": True,
            "database_seed_requires_isolated_environment": True,
        },
        "account_aliases": account_aliases,
        "scenario_count": len(scenarios),
        "blocked_scenarios": blocked,
        "auto_orchestratable_scenarios": len(scenarios) - blocked,
        "scenarios": scenarios,
    }


def build_enterprise_user_preparation_guide(readiness: dict, orchestration: dict) -> dict:
    requirements = readiness.get("test_data_requirements", [])
    gaps = readiness.get("testability_gaps", [])
    account_summary = readiness.get("account_pool_summary", {})
    account_actions = simple_account_actions(requirements, account_summary)
    db_actions = simple_database_actions(gaps, requirements)
    dependency_actions = simple_dependency_actions(requirements)
    file_actions = simple_file_actions(requirements)
    required_actions = account_actions + db_actions + dependency_actions + file_actions
    required_actions = dedupe_user_actions(required_actions)
    return {
        "mode": "enterprise_user_minimum_preparation",
        "goal": "只展示企业用户必须提供的少量配置。测试记录尽量由平台自动生成。",
        "readiness_level": "ready" if not required_actions else "needs_simple_config",
        "user_action_count": len(required_actions),
        "must_prepare": required_actions,
        "platform_auto_handles": [
            "生成场景级测试运行标识",
            "根据需求和接口风险模型生成种子数据",
            "为探针自动绑定正向和反向测试账号",
            "根据数据血缘生成接口或读模型校验",
            "生成带测试运行标识或测试租户保护的清理步骤",
        ],
        "one_minute_checklist": one_minute_checklist(required_actions),
        "advanced_outputs": {
            "test_data_requirements": "test_data_requirements.json",
            "scenario_data_orchestration": "scenario_data_orchestration.json",
            "testability_gaps": "testability_gaps.json",
        },
        "summary": {
            "scenario_count": orchestration.get("scenario_count", 0),
            "auto_orchestratable_scenarios": orchestration.get("auto_orchestratable_scenarios", 0),
            "database_checkpoint_scenarios": len({g.get("scenario_id") for g in gaps if g.get("gap_type") == "database_checkpoint_template"}),
        },
    }


def simple_account_actions(requirements: list[dict], account_summary: dict) -> list[dict]:
    max_roles = max((item.get("required_accounts", {}).get("minimum_roles", 1) for item in requirements), default=1)
    max_tenants = max((item.get("required_accounts", {}).get("minimum_tenants", 1) for item in requirements), default=1)
    roles = account_summary.get("roles") or []
    tenants = account_summary.get("tenants") or []
    actions = []
    if len(roles) < max_roles:
        actions.append(
            {
                "id": "prepare_role_accounts",
                "title": "准备多角色账号",
                "what_to_fill": f"至少 {max_roles} 类角色，例如管理员和普通用户",
                "why": "权限绕过、审批绕过和后台范围校验需要正向账号和反向账号。",
                "example": {"管理员": "管理员账号", "普通用户": "普通业务用户账号"},
                "required": True,
            }
        )
    if len(tenants) < max_tenants:
        actions.append(
            {
                "id": "prepare_tenant_accounts",
                "title": "准备跨租户账号",
                "what_to_fill": f"至少 {max_tenants} 个租户或组织",
                "why": "租户隔离和跨组织越权校验需要来自不同数据范围的账号。",
                "example": {"租户A用户": "alice", "租户B用户": "bob"},
                "required": True,
            }
        )
    return actions


def simple_database_actions(gaps: list[dict], requirements: list[dict]) -> list[dict]:
    if not any(g.get("gap_type") == "database_checkpoint_template" for g in gaps):
        return []
    resources = sorted({cp.get("resource") for item in requirements for cp in item.get("database_checkpoints", []) if cp.get("resource")})
    return [
        {
            "id": "bind_readonly_database_or_read_model",
            "title": "绑定只读数据库或读模型连接",
            "what_to_fill": "提供一个测试、开发或验收环境的只读连接；如果企业不允许连数据库，可选择接口或报表兜底校验",
            "why": "金额、库存、状态和租户一致性问题，如果能和持久化数据交叉校验，发现结果会更可靠。",
            "example": {"环境": "测试环境", "权限": "只读", "资源": resources[:8]},
            "required": True,  # Without real DB, deep consistency checks are impossible
        }
    ]


def simple_dependency_actions(requirements: list[dict]) -> list[dict]:
    deps = sorted({dep.get("type") for item in requirements for dep in item.get("external_dependencies", []) if dep.get("type")})
    if not deps:
        return []
    return [
        {
            "id": "configure_external_capture_stubs",
            "title": "配置外部依赖捕获服务",
            "what_to_fill": "在测试环境提供回调、消息队列或通知捕获地址",
            "why": "回调、消息和通知需要可观测，同时不能向真实客户发送消息。",
            "example": {"dependencies": deps},
            "required": True,
        }
    ]


def simple_file_actions(requirements: list[dict]) -> list[dict]:
    fixtures = sorted({fixture.get("name") for item in requirements for fixture in item.get("file_fixtures", []) if fixture.get("name")})
    if not fixtures:
        return []
    return [
        {
            "id": "confirm_file_import_format",
            "title": "确认导入文件格式",
            "what_to_fill": "上传一个有效导入模板，或提供导入接口的文件格式说明",
            "why": "平台知道企业导入格式后，才能自动生成重复数据、部分失败等测试文件。",
            "example": {"fixtures": fixtures},
            "required": True,
        }
    ]


def dedupe_user_actions(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = item["id"]
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def one_minute_checklist(actions: list[dict]) -> list[str]:
    if not actions:
        return ["当前输入不需要人工编写测试数据。", "可直接运行高价值缺陷发现。", "如需更深的数据一致性校验，可选绑定数据库或读模型只读连接。"]
        return [f"{index}. {item['title']}：{item['what_to_fill']}" for index, item in enumerate(actions, start=1)]


def build_account_aliases(account_items: list[dict]) -> list[dict]:
    aliases = []
    for index, account in enumerate(account_items, start=1):
        aliases.append(
            {
                "alias": f"{account.get('role') or 'account'}_{index}",
                "username": account.get("username"),
                "role": account.get("role"),
                "tenant_id": account.get("tenant_id"),
            }
        )
    return aliases


def account_bindings_for_requirement(item: dict, account_items: list[dict], aliases: list[dict]) -> list[dict]:
    required = item.get("required_accounts", {})
    bindings = []
    for role in (required.get("role_types") or [])[: max(1, required.get("minimum_roles", 1))]:
        match = next((alias for alias in aliases if alias.get("role") == role), None)
        bindings.append({"purpose": f"role:{role}", "alias": match.get("alias") if match else "", "required": True})
    tenant_ids = required.get("tenant_ids") or []
    for tenant in tenant_ids[: max(1, required.get("minimum_tenants", 1))]:
        match = next((alias for alias in aliases if alias.get("tenant_id") == tenant), None)
        bindings.append({"purpose": f"tenant:{tenant}", "alias": match.get("alias") if match else "", "required": True})
    for negative in required.get("negative_actors", []):
        bindings.append({"purpose": f"negative:{negative}", "alias": negative_actor_alias(negative, account_items, aliases), "required": True})
    return dedupe_bindings(bindings)


def negative_actor_alias(negative: str, account_items: list[dict], aliases: list[dict]) -> str:
    if negative == "lower_privilege_role":
        match = next((alias for alias in aliases if alias.get("role") not in {"admin", "owner", "manager"}), None)
        return match.get("alias") if match else ""
    if negative in {"cross_tenant_actor", "resource_non_owner"}:
        tenants = sorted({item.get("tenant_id") for item in account_items if item.get("tenant_id")})
        if len(tenants) > 1:
            match = next((alias for alias in aliases if alias.get("tenant_id") == tenants[-1]), None)
            return match.get("alias") if match else ""
    return ""


def dedupe_bindings(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = (item.get("purpose"), item.get("alias"))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def setup_steps_for_requirement(item: dict, run_id: str) -> list[dict]:
    steps = []
    for seed in item.get("seed_data", []):
        resource = seed["resource"]
        count = max(1, int(seed.get("minimum_records") or 1))
        for index in range(1, count + 1):
            steps.append(
                {
                    "step_id": f"seed_{resource}_{index}",
                    "operation": "seed_resource",
                    "preferred_channel": seed.get("creation_mode") or "business_api",
                    "resource": resource,
                    "record_alias": f"{resource}_{index}",
                    "payload_template": payload_template_for_seed(seed, run_id, index),
                    "capture": ["id", "tenant_id", "owner_user_id", "status"],
                }
            )
    for dep in item.get("external_dependencies", []):
        steps.append({"step_id": f"stub_{dep['type']}", "operation": "start_dependency_stub", "dependency": dep})
    for fixture in item.get("file_fixtures", []):
        steps.append({"step_id": f"file_{fixture['name']}", "operation": "prepare_file_fixture", "fixture": fixture})
    return steps


def payload_template_for_seed(seed: dict, run_id: str, index: int) -> dict:
    resource = seed.get("resource") or "resource"
    payload: dict[str, object] = {
        "test_run_id": run_id,
        "external_key": f"{resource}_${{test_run_id}}_{index}",
        "tenant_id": "${tenant_id}",
        "owner_user_id": "${owner_user_id}",
    }
    for field in seed.get("field_requirements", []):
        payload[field] = sample_value_for_field(field, index)
    if seed.get("state_requirements"):
        payload["status"] = seed["state_requirements"][0]
    return payload


def sample_value_for_field(field: str, index: int) -> object:
    if field in {"amount", "paid_amount", "refund_amount"}:
        return round(100 + index * 3.17, 2)
    if field == "currency":
        return "CNY"
    if field in {"quantity", "stock", "limit"}:
        return 10 + index
    if field in {"benefit_owner"}:
        return "${owner_user_id}"
    if field == "usage_limit":
        return 1
    if field == "valid_time_window":
        return {"starts_at": "${now_minus_1h}", "ends_at": "${now_plus_1d}"}
    return f"${{{field}}}"


def assertion_steps_for_requirement(item: dict) -> list[dict]:
    steps = []
    for checkpoint in item.get("database_checkpoints", []):
        steps.append(
            {
                "step_id": f"assert_{checkpoint['resource']}",
                "operation": "assert_data_consistency",
                "resource": checkpoint["resource"],
                "fields": checkpoint.get("fields", []),
                "preferred_channel": "database_or_read_model",
                "fallback_channel": checkpoint.get("fallback_without_db"),
                "scope_filter": {"test_run_id": "${test_run_id}", "tenant_id": "${tenant_id}"},
            }
        )
    if not steps:
        steps.append(
            {
                "step_id": "assert_probe_observable_state",
                "operation": "assert_via_probe_response",
                "preferred_channel": "api_response",
                "scope_filter": {"test_run_id": "${test_run_id}"},
            }
        )
    return steps


def cleanup_steps_for_requirement(item: dict, run_id: str) -> list[dict]:
    cleanup = item.get("cleanup_strategy") or {}
    steps = []
    for resource in cleanup.get("preferred_order", []):
        steps.append(
            {
                "step_id": f"cleanup_{resource}",
                "operation": "cleanup_resource",
                "resource": resource,
                "preferred_channel": "business_api_or_scoped_database_cleanup",
                "scope_filter": {"test_run_id": run_id, "tenant_id": "${tenant_id}"},
                "guard": "must_include_test_run_id_or_test_tenant",
            }
        )
    return steps


def readiness_status(coverage_item: dict) -> str:
    if coverage_item.get("blocked_reasons"):
        return "blocked"
    if not coverage_item.get("executable"):
        return "needs_environment_config"
    return "auto_preparable"


def required_accounts_for_risks(risks: set[str], roles: list[str], tenants: list[str]) -> dict:
    required = {
        "minimum_roles": 1,
        "minimum_tenants": 1,
        "role_types": roles[:],
        "tenant_ids": tenants[:],
        "negative_actors": [],
    }
    if risks & {"permission_bypass", "approval_bypass", "export_permission"}:
        required["minimum_roles"] = 2
        required["negative_actors"].append("lower_privilege_role")
    if risks & {"tenant_isolation", "search_scope_leak", "privacy_scope"}:
        required["minimum_tenants"] = 2
        required["negative_actors"].append("cross_tenant_actor")
    if risks & {"idor"}:
        required["negative_actors"].append("resource_non_owner")
    return required


def seed_data_for_scenario(resources: list[str], risks: set[str]) -> list[dict]:
    items = []
    for resource in resources:
        if resource in {"login", "reset", "health"}:
            continue
        item = {
            "resource": resource,
            "creation_mode": "api_seed_or_existing_fixture",
            "minimum_records": 2 if risks & {"tenant_isolation", "idor"} else 1,
            "ownership_dimensions": ["tenant_id", "owner_user_id"] if risks & {"tenant_isolation", "idor"} else ["primary_owner"],
            "state_requirements": [],
            "field_requirements": [],
        }
        if risks & {"state_flow", "approval_bypass", "rollback_consistency"}:
            item["state_requirements"].extend(["initial_state", "actionable_state", "terminal_state"])
        if risks & {"money_consistency", "refund_abuse", "report_aggregation_error"}:
            item["field_requirements"].extend(["amount", "paid_amount", "refund_amount", "currency"])
        if risks & {"quantity_consistency", "stock_consistency", "quota_limit", "capacity_limit"}:
            item["field_requirements"].extend(["quantity", "stock", "limit"])
        if risks & {"benefit_abuse"}:
            item["field_requirements"].extend(["benefit_owner", "usage_limit", "valid_time_window"])
        items.append(item)
    return items


def database_checkpoints_for_scenario(resources: list[str], risks: set[str], model: dict) -> list[dict]:
    lineage = model.get("data_lineage", [])
    checkpoints = []
    for resource in resources:
        fields = sorted({item["field_family"] for item in lineage if item.get("resource") == resource})
        if not fields and risks & {"money_consistency", "quantity_consistency", "state_flow", "tenant_isolation"}:
            fields = sorted(risks & {"money_consistency", "quantity_consistency", "state_flow", "tenant_isolation"})
        if fields:
            checkpoints.append(
                {
                    "resource": resource,
                    "checkpoint_type": "post_api_db_or_read_model_assertion",
                    "fields": fields,
                    "requires_database_connection": True,
                    "fallback_without_db": "assert_via_read_api_and_report_export",
                }
            )
    return checkpoints


def external_dependencies_for_risks(risks: set[str]) -> list[dict]:
    deps = []
    if risks & {"callback_trust", "webhook_replay", "message_ordering", "eventual_consistency"}:
        deps.append({"type": "webhook_or_mq_stub", "purpose": "simulate trusted and replayed callbacks"})
    if risks & {"notification_wrong_recipient", "notification_duplicate", "template_variable_leak"}:
        deps.append({"type": "notification_sink", "purpose": "capture sms/email/site-message recipients and templates"})
    return deps


def file_fixtures_for_risks(risks: set[str]) -> list[dict]:
    if not risks & {"file_upload_validation", "duplicate_import", "bulk_operation_partial_failure"}:
        return []
    return [
        {"name": "valid_import_file", "purpose": "happy path import baseline"},
        {"name": "duplicate_rows_file", "purpose": "duplicate and idempotency validation"},
        {"name": "partial_invalid_file", "purpose": "partial failure and rollback validation"},
    ]


def cleanup_strategy_for_scenario(resources: list[str]) -> dict:
    return {
        "mode": "scenario_scoped",
        "keys": ["test_run_id", "tenant_id", "owner_user_id"],
        "resources": [r for r in resources if r not in {"login", "reset", "health"}],
        "preferred_order": list(reversed([r for r in resources if r not in {"login", "reset", "health"}])),
    }


def build_testability_gaps(requirements: list[dict]) -> list[dict]:
    gaps = []
    for item in requirements:
        for reason in item.get("blocked_reasons", []):
            gaps.append({"scenario_id": item["scenario_id"], "gap_type": "blocked_coverage", "detail": reason, "owner": "platform_or_environment"})
        required = item.get("required_accounts", {})
        if len(required.get("role_types", [])) < required.get("minimum_roles", 1):
            gaps.append({"scenario_id": item["scenario_id"], "gap_type": "missing_account_role", "detail": "need more role diversity", "owner": "account_pool"})
        if len(required.get("tenant_ids", [])) < required.get("minimum_tenants", 1):
            gaps.append({"scenario_id": item["scenario_id"], "gap_type": "missing_tenant_account", "detail": "need cross-tenant accounts", "owner": "account_pool"})
        if item.get("database_checkpoints"):
            gaps.append({"scenario_id": item["scenario_id"], "gap_type": "database_checkpoint_template", "detail": "DB/read-model assertion template generated", "owner": "environment_config"})
        if item.get("external_dependencies"):
            gaps.append({"scenario_id": item["scenario_id"], "gap_type": "external_dependency_stub", "detail": "mock/capture endpoint required", "owner": "environment_config"})
    return gaps


def readiness_next_actions(requirements: list[dict], gaps: list[dict]) -> list[str]:
    actions = ["Generate scenario-scoped seed data before probe execution", "Clean up by test_run_id after execution"]
    if any(g["gap_type"] == "database_checkpoint_template" for g in gaps):
        actions.append("Bind database/read-model connection to enable deep consistency checks")
    if any(g["gap_type"] in {"missing_account_role", "missing_tenant_account"} for g in gaps):
        actions.append("Expand account pool with required roles and tenant pairs")
    if any(g["gap_type"] == "external_dependency_stub" for g in gaps):
        actions.append("Configure notification/webhook/MQ capture stubs")
    if any(item["automation_status"] == "blocked" for item in requirements):
        actions.append("Regenerate probes for blocked scenario steps or add missing public API documentation")
    return actions


def api_ref_compatible(left: str, right: str) -> bool:
    left_method, left_path = split_api_ref(left)
    right_method, right_path = split_api_ref(right)
    if left_method and right_method and left_method != right_method:
        return False
    return path_template_compatible(left_path, right_path)


def split_api_ref(ref: str) -> tuple[str, str]:
    parts = ref.split(" ", 1)
    if len(parts) == 2 and parts[0].isupper():
        return parts[0], parts[1]
    return "", ref


def path_template_compatible(left: str, right: str) -> bool:
    left_parts = left.split("?")[0].strip("/").split("/")
    right_parts = right.split("?")[0].strip("/").split("/")
    if len(left_parts) != len(right_parts):
        return False
    for a, b in zip(left_parts, right_parts):
        if a == b:
            continue
        if a.startswith("{") and a.endswith("}"):
            continue
        if b.startswith("{") and b.endswith("}"):
            continue
        if a.startswith("o") and a[1:].isdigit() and b.startswith("{"):
            continue
        if b.startswith("o") and b[1:].isdigit() and a.startswith("{"):
            continue
        if a.startswith("p") and a[1:].isdigit() and b.startswith("{"):
            continue
        if b.startswith("p") and b[1:].isdigit() and a.startswith("{"):
            continue
        return False
    return True


