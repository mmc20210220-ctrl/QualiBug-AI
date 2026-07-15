from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _fetch,
    _html_escape,
    _join_url,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)
from .business_flow_graph import build_business_flow_graph, load_business_flow_graph

PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}
MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
MONEY_KEYS = {"amount", "pay_amount", "payment_amount", "refund_amount", "total_amount", "price", "balance", "ledger_amount"}
ID_KEY_ALIASES = {
    "order": ["order_id", "orderId", "id"],
    "payment": ["payment_id", "paymentId", "transaction_id", "transactionId"],
    "refund": ["refund_id", "refundId"],
    "sku": ["sku", "sku_id", "skuId", "product_id", "productId"],
    "coupon": ["coupon_id", "couponId", "voucher_id", "voucherId"],
    "application": ["application_id", "applicationId", "approval_id", "approvalId"],
}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _safe_text(value: Any, limit: int = 4000) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        text = value
    else:
        try:
            text = json.dumps(value, ensure_ascii=False)
        except Exception:
            text = str(value)
    lower = text.lower()
    if any(marker.lower() in lower for marker in PRIVATE_MARKERS):
        return ""
    return text.replace("\x00", " ")[:limit]


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted([m for m in PRIVATE_MARKERS if m.lower() in text])
    return {"passed": not leaks, "leak_terms": leaks}


def _execution_paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    return {
        "workspace_dir": root / "platform_workspace" / project / "business_flow_execution",
        "defect_workspace_dir": root / "platform_workspace" / project / "defect_discovery",
        "output_dir": root / "platform_outputs" / project / "business_flow_execution",
    }


def load_business_flow_execution_result(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    candidates = [
        root / "platform_workspace" / project / "business_flow_execution" / "business_flow_execution_result.json",
        root / "platform_outputs" / project / "business_flow_execution" / "business_flow_execution_result.json",
    ]
    for path in candidates:
        if path.exists():
            data = _load_json(path, {})
            return data if isinstance(data, dict) else None
    return None


def _normalize_execution_mode(raw: Any) -> str:
    mode = str(raw or "dry_run").strip().lower().replace("-", "_")
    return mode if mode in {"dry_run", "safe_live", "full_live"} else "dry_run"


def _is_write_step(step: dict[str, Any]) -> bool:
    return str(step.get("method") or "GET").upper() in MUTATION_METHODS


def _classify_stage(stage: str, path: str) -> str:
    text = f"{stage} {path}".lower()
    if "login" in text or "auth" in text:
        return "auth"
    if "coupon" in text or "voucher" in text:
        return "coupon"
    if "cart" in text or "basket" in text:
        return "cart"
    if "order" in text or "checkout" in text:
        return "order"
    if "payment" in text or "pay" in text or "callback" in text:
        return "payment"
    if "refund" in text or "return" in text:
        return "refund"
    if "stock" in text or "inventory" in text or "sku" in text:
        return "stock"
    if "balance" in text or "wallet" in text:
        return "balance"
    if "ledger" in text or "statement" in text or "flow" in text:
        return "ledger"
    if "approve" in text or "approval" in text or "review" in text:
        return "approval"
    return "business"


def _default_context() -> dict[str, Any]:
    return {
        "order_id": "FLOW_ORDER_001",
        "payment_id": "FLOW_PAYMENT_001",
        "transaction_id": "FLOW_TX_001",
        "refund_id": "FLOW_REFUND_001",
        "sku": "FLOW_SKU_001",
        "coupon_id": "FLOW_COUPON_001",
        "application_id": "FLOW_APP_001",
        "quantity": 1,
        "amount": 100.0,
        "currency": "CNY",
        "status_sequence": [],
        "money_observations": [],
        "inventory_observations": [],
        "ledger_observations": [],
    }


def _path_param_value(name: str, context: dict[str, Any]) -> str:
    key = name.lower().replace("-", "_")
    if "order" in key:
        return str(context.get("order_id") or "FLOW_ORDER_001")
    if "payment" in key or "transaction" in key or "trade" in key:
        return str(context.get("payment_id") or context.get("transaction_id") or "FLOW_PAYMENT_001")
    if "refund" in key:
        return str(context.get("refund_id") or "FLOW_REFUND_001")
    if "sku" in key or "product" in key or "item" in key:
        return str(context.get("sku") or "FLOW_SKU_001")
    if "coupon" in key or "voucher" in key:
        return str(context.get("coupon_id") or "FLOW_COUPON_001")
    if "tenant" in key:
        return str(context.get("tenant_id") or "FLOW_TENANT_A")
    if "user" in key or "customer" in key or "patient" in key or "student" in key:
        return str(context.get("subject_id") or "FLOW_SUBJECT_001")
    return str(context.get(key) or "1")


def _render_path(path: str, context: dict[str, Any]) -> str:
    def repl(match: re.Match[str]) -> str:
        return _path_param_value(match.group(1), context)

    return re.sub(r"\{([^{}]+)\}", repl, str(path or "/"))


def _body_for_step(step: dict[str, Any], context: dict[str, Any], risk: str) -> dict[str, Any] | None:
    method = str(step.get("method") or "GET").upper()
    if method == "GET":
        return None
    stage = _classify_stage(str(step.get("stage") or ""), str(step.get("path") or ""))
    amount = float(context.get("amount") or 100.0)
    if stage == "auth":
        return {"username": "${normal_user.username}", "password": "${normal_user.password}"}
    if stage == "cart":
        return {"sku": context.get("sku"), "quantity": context.get("quantity", 1)}
    if stage == "coupon":
        return {"coupon_id": context.get("coupon_id"), "cart_id": context.get("cart_id", "FLOW_CART_001"), "expected_once_only": True}
    if stage == "order":
        return {"cart_id": context.get("cart_id", "FLOW_CART_001"), "coupon_id": context.get("coupon_id"), "amount": amount, "currency": context.get("currency", "CNY")}
    if stage == "payment":
        if "callback" in str(step.get("path") or "").lower() or "callback" in str(step.get("stage") or "").lower():
            return {"order_id": context.get("order_id"), "transaction_id": context.get("transaction_id"), "amount": amount, "status": "SUCCESS", "idempotency_key": "FLOW_IDEMPOTENCY_PAYMENT_001"}
        return {"order_id": context.get("order_id"), "amount": amount, "currency": context.get("currency", "CNY"), "idempotency_key": "FLOW_IDEMPOTENCY_PAYMENT_001"}
    if stage == "refund":
        refund_amount = amount + 1 if risk == "refund" else amount
        return {"order_id": context.get("order_id"), "payment_id": context.get("payment_id"), "amount": refund_amount, "reason": "phase39_consistency_check", "idempotency_key": "FLOW_IDEMPOTENCY_REFUND_001"}
    if stage == "approval":
        return {"application_id": context.get("application_id"), "action": "approve", "comment": "phase39_flow_assertion"}
    return {"flow_context": {"order_id": context.get("order_id"), "amount": amount}, "risk_type": risk}


def _try_json(text: str) -> Any:
    try:
        return json.loads(text or "")
    except Exception:
        return None


def _flatten_json(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    rows: list[tuple[str, Any]] = []
    if isinstance(value, dict):
        for k, v in value.items():
            key = f"{prefix}.{k}" if prefix else str(k)
            rows.extend(_flatten_json(v, key))
    elif isinstance(value, list):
        for i, v in enumerate(value[:10]):
            rows.extend(_flatten_json(v, f"{prefix}[{i}]"))
    else:
        rows.append((prefix, value))
    return rows


def _capture_response_context(context: dict[str, Any], step: dict[str, Any], body_text: str) -> dict[str, Any]:
    data = _try_json(body_text)
    if data is None:
        return context
    stage = _classify_stage(str(step.get("stage") or ""), str(step.get("path") or ""))
    for key, value in _flatten_json(data):
        leaf = key.split(".")[-1].split("[")[0]
        leaf_lower = leaf.lower()
        if leaf_lower in {"id", "orderid", "order_id"} and stage == "order":
            context["order_id"] = str(value)
        if leaf_lower in {"paymentid", "payment_id", "transactionid", "transaction_id"}:
            context["payment_id"] = str(value)
            if "transaction" in leaf_lower:
                context["transaction_id"] = str(value)
        if leaf_lower in {"refundid", "refund_id"}:
            context["refund_id"] = str(value)
        if leaf_lower in {"cartid", "cart_id"}:
            context["cart_id"] = str(value)
        if leaf_lower in {"status", "state", "orderstatus", "order_state"}:
            context.setdefault("status_sequence", []).append({"stage": stage, "key": key, "value": str(value)})
        if leaf_lower in {k.lower() for k in MONEY_KEYS}:
            try:
                context.setdefault("money_observations", []).append({"stage": stage, "key": key, "value": float(value)})
            except Exception:
                pass
        if leaf_lower in {"stock", "inventory", "available", "quantity", "qty"} and stage == "stock":
            try:
                context.setdefault("inventory_observations", []).append({"stage": stage, "key": key, "value": float(value)})
            except Exception:
                pass
        if stage == "ledger":
            context.setdefault("ledger_observations", []).append({"key": key, "value": value})
    return context


def _step_can_execute(step: dict[str, Any], mode: str, cfg: dict[str, Any], options: dict[str, Any]) -> tuple[bool, str]:
    if mode == "dry_run":
        return False, "dry_run_planned_only"
    method = str(step.get("method") or "GET").upper()
    if method == "GET":
        return True, "safe_get_allowed"
    if mode == "safe_live":
        return False, "safe_live_blocks_write_step"
    allow_destructive = bool(cfg.get("allow_destructive_tests")) and bool(options.get("allow_destructive_execution", False))
    if mode == "full_live" and allow_destructive:
        return True, "full_live_write_allowed_by_project_policy"
    return False, "write_step_requires_full_live_and_allow_destructive_execution"


def _execute_or_plan_step(base_url: str, step: dict[str, Any], context: dict[str, Any], token: str | None, mode: str, cfg: dict[str, Any], options: dict[str, Any], risk: str) -> tuple[dict[str, Any], dict[str, Any]]:
    method = str(step.get("method") or "GET").upper()
    rendered_path = _render_path(str(step.get("path") or "/"), context)
    body = _body_for_step({**step, "method": method}, context, risk)
    can_execute, policy_reason = _step_can_execute({**step, "method": method}, mode, cfg, options)
    record: dict[str, Any] = {
        "step": step.get("step"),
        "stage": step.get("stage") or "business",
        "actor": step.get("actor") or "normal_user",
        "method": method,
        "path": rendered_path,
        "body_blueprint": body,
        "execution_policy_reason": policy_reason,
        "executed": False,
        "status_code": None,
        "error": None,
        "response_excerpt": "",
    }
    if not base_url:
        record["error"] = "missing_base_url"
        return record, context
    if not can_execute:
        record["error"] = policy_reason
        return record, context
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if body is not None:
        headers["Content-Type"] = "application/json"
    response = _fetch(_join_url(base_url, rendered_path), method=method, body=json.dumps(body, ensure_ascii=False).encode("utf-8") if body is not None else None, headers=headers, timeout=int(cfg.get("request_timeout_seconds") or 10))
    record.update({
        "executed": True,
        "status_code": response.get("status_code"),
        "error": response.get("error"),
        "response_excerpt": _safe_text(response.get("body") or "", 800),
    })
    if response.get("body"):
        context = _capture_response_context(context, record, str(response.get("body") or ""))
    return record, context


def _status_norm(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", "-")


def _has_success_status(step_records: list[dict[str, Any]]) -> bool:
    return any(isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300 for r in step_records)


def _assertion(assertion_id: str, assertion_type: str, severity: str, expected: str, actual: str, status: str, bug_signal: str, evidence_refs: list[str] | None = None) -> dict[str, Any]:
    return {
        "assertion_id": assertion_id,
        "assertion_type": assertion_type,
        "severity": severity,
        "expected": expected,
        "actual": actual,
        "status": status,
        "bug_signal": bug_signal,
        "evidence_refs": evidence_refs or [],
    }


def _evaluate_assertions(probe: dict[str, Any], step_records: list[dict[str, Any]], context: dict[str, Any]) -> list[dict[str, Any]]:
    risk = str(probe.get("risk_type") or "business_rule")
    flow_id = str(probe.get("flow_id") or probe.get("probe_id") or "FLOW")
    prefix = f"ASSERT_{flow_id}_{risk}".replace("-", "_")
    assertions: list[dict[str, Any]] = []
    executed_any = any(r.get("executed") for r in step_records)
    successful_any = _has_success_status(step_records)
    money = context.get("money_observations") or []
    statuses = context.get("status_sequence") or []
    inventory = context.get("inventory_observations") or []

    def review(actual: str, typ: str, expected: str, severity: str = "P1") -> dict[str, Any]:
        return _assertion(f"{prefix}_{len(assertions)+1:02d}", typ, severity, expected, actual, "needs_evidence", "需要真实响应或状态快照完成断言", [str(r.get("step")) for r in step_records[:8]])

    if risk in {"payment", "money_consistency", "refund"}:
        if len(money) >= 2:
            values = [float(m.get("value") or 0) for m in money]
            spread = max(values) - min(values)
            failed = spread > max(0.01, abs(values[0]) * 0.05)
            assertions.append(_assertion(
                f"{prefix}_{len(assertions)+1:02d}",
                "money_conservation",
                "P0",
                "订单、支付、退款、余额和流水金额必须守恒。",
                f"捕获金额 {values[:8]}，最大差异 {round(spread, 4)}",
                "failed" if failed else "passed",
                "金额在多接口链路前后不一致，可能造成资损。",
                [str(r.get("step")) for r in step_records],
            ))
        else:
            assertions.append(review("未捕获到足够金额字段", "money_conservation", "链路执行后应能比较订单/支付/退款/流水金额", "P0"))
    if risk in {"stock_consistency", "business_rule"} or "stock" in str(probe.get("flow_type") or ""):
        if len(inventory) >= 2:
            values = [float(x.get("value") or 0) for x in inventory]
            assertions.append(_assertion(
                f"{prefix}_{len(assertions)+1:02d}",
                "inventory_conservation",
                "P1",
                "下单、支付失败、退款或取消后库存变更必须符合数量规则。",
                f"捕获库存序列 {values[:8]}",
                "needs_evidence" if len(values) < 3 else "passed",
                "库存扣减/回滚和订单履约状态不一致。",
                [str(r.get("step")) for r in step_records],
            ))
        else:
            assertions.append(review("未捕获到库存前后置快照", "inventory_conservation", "至少需要库存前置和后置快照", "P1"))
    if risk in {"payment", "refund", "order_state", "state_transition", "approval_bypass"}:
        seq = [_status_norm(s.get("value")) for s in statuses if s.get("value") is not None]
        illegal_pairs = {("refunded", "paid"), ("cancelled", "paid"), ("closed", "paid"), ("approved", "draft"), ("rejected", "approved")}
        failed_pair = None
        for a, b in zip(seq, seq[1:]):
            if (a, b) in illegal_pairs:
                failed_pair = (a, b)
                break
        if seq:
            assertions.append(_assertion(
                f"{prefix}_{len(assertions)+1:02d}",
                "state_machine",
                "P0" if risk in {"payment", "refund", "approval_bypass"} else "P1",
                "业务对象状态只能沿合法状态机推进，不能回退、跳步或重复完成。",
                f"捕获状态序列 {seq[:10]}",
                "failed" if failed_pair else "passed",
                f"状态非法流转 {failed_pair}" if failed_pair else "状态机序列未发现明显非法回退。",
                [str(r.get("step")) for r in step_records],
            ))
        else:
            assertions.append(review("未捕获到状态字段", "state_machine", "链路应返回订单/退款/审批状态字段", "P1"))
    if risk in {"idempotency", "payment", "refund", "coupon_abuse"}:
        write_steps = [r for r in step_records if r.get("method") in MUTATION_METHODS]
        if executed_any and successful_any and len(write_steps) >= 1:
            assertions.append(_assertion(
                f"{prefix}_{len(assertions)+1:02d}",
                "idempotency_replay",
                "P0" if risk in {"payment", "refund"} else "P1",
                "重复提交同一幂等键/业务 ID 不应产生重复订单、重复扣款、重复退款或重复抵扣。",
                "已生成幂等回放请求蓝图；需要对相同 idempotency_key 进行二次执行比对。",
                "needs_replay",
                "重复回放可能产生重复业务结果。",
                [str(r.get("step")) for r in write_steps[:3]],
            ))
        else:
            assertions.append(review("写步骤未执行或缺少成功响应，已保留幂等回放蓝图", "idempotency_replay", "同一请求体/幂等键重复执行结果必须唯一", "P0" if risk in {"payment", "refund"} else "P1"))
    if risk in {"permission_bypass", "approval_bypass", "privacy_leak", "idor", "tenant_isolation"}:
        target_steps = [r for r in step_records if r.get("actor") in {"normal_user", "other_user", "tenant_b_user"}]
        got_2xx = any(isinstance(r.get("status_code"), int) and 200 <= int(r.get("status_code")) < 300 for r in target_steps)
        if executed_any:
            assertions.append(_assertion(
                f"{prefix}_{len(assertions)+1:02d}",
                "authorization_boundary",
                "P0" if risk in {"approval_bypass", "tenant_isolation", "privacy_leak"} else "P1",
                "低权限、跨用户或跨租户身份不能推进高权限步骤或读取他人敏感资源。",
                "低权限/跨边界步骤返回 2xx" if got_2xx else "未观察到低权限成功响应",
                "failed" if got_2xx else "passed",
                "访问控制边界被绕过。",
                [str(r.get("step")) for r in target_steps[:6]],
            ))
        else:
            assertions.append(review("权限边界步骤未真实执行", "authorization_boundary", "跨角色/跨租户步骤应返回 401/403 或业务拒绝", "P0" if risk in {"approval_bypass", "tenant_isolation"} else "P1"))
    if not assertions:
        assertions.append(review("未命中特定风险断言，生成通用业务不变量检查", "business_invariant", "流程完成后核心业务对象必须保持 PRD 不变量", "P2"))
    return assertions


def _candidate_issues_from_assertions(probe: dict[str, Any], assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for a in assertions:
        status = str(a.get("status") or "")
        if status not in {"failed", "needs_evidence", "needs_replay"}:
            continue
        confidence = 0.84 if status == "failed" else (0.56 if status == "needs_replay" else 0.48)
        severity = a.get("severity") or probe.get("severity") or "P2"
        issues.append({
            "issue_id": f"FLOW_ASSERT_{probe.get('probe_id')}_{a.get('assertion_id')}",
            "title": f"链路一致性断言：{probe.get('title') or probe.get('flow_type')} · {a.get('assertion_type')}",
            "risk_type": probe.get("risk_type") or "business_rule",
            "severity": severity if status == "failed" else ("P2" if severity == "P0" else severity),
            "confidence": confidence,
            "status": "needs_human_review",
            "expected": a.get("expected"),
            "actual": a.get("actual"),
            "bug_signal": a.get("bug_signal"),
            "flow_id": probe.get("flow_id"),
            "probe_id": probe.get("probe_id"),
            "assertion_id": a.get("assertion_id"),
        })
    return issues


def _load_accounts(project: str, root: Path) -> dict[str, Any]:
    paths = config_paths(project, root)
    data = _load_json(paths["input_dir"] / "test_accounts.json", {})
    return data if isinstance(data, dict) else {}


def _token_for_actor(accounts: dict[str, Any], actor: str) -> str | None:
    row = accounts.get(actor) or accounts.get("normal_user") or accounts.get("normal") or {}
    return str(row.get("token")) if isinstance(row, dict) and row.get("token") else None


def run_business_flow_execution(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = dict(options or {})
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)

    # ── Phase78A: Unified Safe HTTP Transport ──
    from .unified_http_transport import ExecutionPolicy, SafeHttpTransport, set_global_transport
    env = str(cfg.get('environment') or cfg.get('target_environment') or '').lower()
    policy = ExecutionPolicy(environment=env, allow_destructive=bool(cfg.get('allow_destructive_tests')))
    transport = SafeHttpTransport(policy=policy, base_url=str(cfg.get('base_url') or ''))
    set_global_transport(transport)

    if policy.is_production:
        return {
            "status": "blocked_by_production_safety_gate",
            "project": project, "environment": env,
            "reason": "Production env detected. Flow execution blocked. Only dry_run allowed.",
            "allowed_actions": ["dry_run", "static_analysis", "gap_report"],
            "blocked_actions": ["http_request", "write_operation", "observer", "polling", "snapshot"],
            "executions": [], "assertions": [], "candidate_issues": [], "http_request_count": 0,
        }

    requested_mode = _normalize_execution_mode(options.get("execution_mode") or cfg.get("business_flow_execution_mode") or "dry_run")
    base_url = str(cfg.get("base_url") or "").strip()
    mode = requested_mode if base_url else "dry_run"
    max_probes = int(options.get("max_probe_count") or options.get("scenario_probe_count") or 30)
    graph = load_business_flow_graph(project, root) or build_business_flow_graph(project, root, options={"skip_knowledge_build": True, "scenario_probe_count": max(80, max_probes)})
    scenario_probes = [dict(p) for p in (graph.get("scenario_probes") or [])[:max_probes]]
    accounts = _load_accounts(project, root)
    executions: list[dict[str, Any]] = []
    all_assertions: list[dict[str, Any]] = []
    candidate_issues: list[dict[str, Any]] = []
    for probe in scenario_probes:
        context = _default_context()
        step_records: list[dict[str, Any]] = []
        for step in probe.get("flow_steps") or []:
            actor = str(step.get("actor") or probe.get("actor") or "normal_user")
            record, context = _execute_or_plan_step(base_url, {**step, "actor": actor}, context, _token_for_actor(accounts, actor), mode, cfg, options, str(probe.get("risk_type") or "business_rule"))
            step_records.append(record)
        assertions = _evaluate_assertions(probe, step_records, context)
        for a in assertions:
            a["probe_id"] = probe.get("probe_id")
            a["flow_id"] = probe.get("flow_id")
            a["risk_type"] = probe.get("risk_type")
        execution = {
            "execution_id": f"FLOW_EXEC_{len(executions)+1:04d}",
            "probe_id": probe.get("probe_id"),
            "flow_id": probe.get("flow_id"),
            "flow_type": probe.get("flow_type"),
            "risk_type": probe.get("risk_type"),
            "title": probe.get("title"),
            "execution_mode": mode,
            "step_count": len(step_records),
            "live_step_count": sum(1 for r in step_records if r.get("executed")),
            "planned_step_count": sum(1 for r in step_records if not r.get("executed")),
            "captured_context_digest": _context_digest(context),
            "steps": step_records,
            "assertions": assertions,
        }
        executions.append(execution)
        all_assertions.extend(assertions)
        candidate_issues.extend(_candidate_issues_from_assertions(probe, assertions))
    summary = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "requested_execution_mode": requested_mode,
        "execution_mode": mode,
        "base_url_configured": bool(base_url),
        "allow_destructive_tests": bool(cfg.get("allow_destructive_tests")),
        "flow_probe_count": len(scenario_probes),
        "executed_flow_count": len(executions),
        "total_step_count": sum(e.get("step_count", 0) for e in executions),
        "live_step_count": sum(e.get("live_step_count", 0) for e in executions),
        "dry_run_step_count": sum(e.get("planned_step_count", 0) for e in executions),
        "assertion_count": len(all_assertions),
        "passed_assertion_count": sum(1 for a in all_assertions if a.get("status") == "passed"),
        "failed_assertion_count": sum(1 for a in all_assertions if a.get("status") == "failed"),
        "review_assertion_count": sum(1 for a in all_assertions if a.get("status") in {"needs_evidence", "needs_replay"}),
        "candidate_issue_count": len(candidate_issues),
        "generated_at_utc": _now(),
    }
    result = {
        "phase": "phase39_business_flow_execution_assertions",
        "summary": summary,
        "source_flow_graph_summary": graph.get("summary", {}),
        "executions": executions,
        "assertions": all_assertions,
        "candidate_issues": candidate_issues,
        "governance": {
            "real_project_mode": True,
            "default_execution_mode": "dry_run",
            "safe_live_executes_get_only": True,
            "write_steps_require_full_live_and_allow_destructive_execution": True,
            "uses_no_benchmark_answer_files": True,
            "inputs": ["business_flow_graph", "real_project_config", "test_accounts_optional_tokens"],
        },
    }
    leak = _private_leak_check(result)
    result["private_leak_check"] = leak
    paths = _execution_paths(project, root)
    for key in ("workspace_dir", "defect_workspace_dir", "output_dir"):
        paths[key].mkdir(parents=True, exist_ok=True)
    _write_json(paths["workspace_dir"] / "business_flow_execution_result.json", result)
    _write_json(paths["defect_workspace_dir"] / "business_flow_execution_result.json", result)
    _write_json(paths["output_dir"] / "business_flow_execution_result.json", result)
    _write_json(paths["output_dir"] / "business_flow_execution_summary.json", {"summary": summary, "private_leak_check": leak})
    _write_text(paths["output_dir"] / "business_flow_execution_report.html", render_business_flow_execution_report(result))
    return result


def _context_digest(context: dict[str, Any]) -> dict[str, Any]:
    return {
        "ids": {k: context.get(k) for k in ["order_id", "payment_id", "transaction_id", "refund_id", "sku", "coupon_id", "application_id"] if context.get(k)},
        "status_count": len(context.get("status_sequence") or []),
        "money_observation_count": len(context.get("money_observations") or []),
        "inventory_observation_count": len(context.get("inventory_observations") or []),
        "ledger_observation_count": len(context.get("ledger_observations") or []),
    }


def render_business_flow_execution_report(result: dict[str, Any]) -> str:
    summary = result.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k not in {"project_id", "project_name"})
    execution_rows = []
    for e in (result.get("executions") or [])[:80]:
        assertion_bad = sum(1 for a in e.get("assertions") or [] if a.get("status") in {"failed", "needs_evidence", "needs_replay"})
        execution_rows.append(f"<tr><td>{_html_escape(e.get('execution_id'))}</td><td>{_html_escape(e.get('flow_id'))}</td><td>{_html_escape(e.get('risk_type'))}</td><td>{_html_escape(e.get('step_count'))}</td><td>{_html_escape(e.get('live_step_count'))}</td><td>{_html_escape(assertion_bad)}</td><td>{_html_escape(e.get('title'))}</td></tr>")
    assertion_rows = []
    for a in (result.get("assertions") or [])[:120]:
        assertion_rows.append(f"<tr><td>{_html_escape(a.get('status'))}</td><td>{_html_escape(a.get('severity'))}</td><td>{_html_escape(a.get('assertion_type'))}</td><td>{_html_escape(a.get('risk_type'))}</td><td>{_html_escape(a.get('expected'))}</td><td>{_html_escape(a.get('actual'))}</td></tr>")
    leak = result.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Business Flow Execution Assertions</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#dcfce7;color:#166534}}</style></head><body>
<section class='hero'><span class='badge'>Phase39</span><h1>多接口链路自动执行器 + 状态一致性断言引擎</h1><p>把 Phase38 的 flow_steps 编排为可执行请求蓝图/安全实时执行，并对金额、库存、订单状态、流水、审批状态和权限边界做一致性断言。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>执行概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>链路执行</h2><table><thead><tr><th>ID</th><th>Flow</th><th>风险</th><th>步骤</th><th>Live</th><th>待确认断言</th><th>标题</th></tr></thead><tbody>{''.join(execution_rows) or '<tr><td colspan="7">暂无链路执行</td></tr>'}</tbody></table></section>
<section class='panel'><h2>一致性断言</h2><table><thead><tr><th>状态</th><th>等级</th><th>断言</th><th>风险</th><th>期望</th><th>实际</th></tr></thead><tbody>{''.join(assertion_rows) or '<tr><td colspan="6">暂无断言</td></tr>'}</tbody></table></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    mode = os.environ.get("BUSINESS_FLOW_EXECUTION_MODE") or (argv[1] if len(argv) > 1 else "dry_run")
    result = run_business_flow_execution(project, options={"execution_mode": mode})
    print(json.dumps({"ok": True, "project_id": project, "summary": result.get("summary"), "private_leak_check": result.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
