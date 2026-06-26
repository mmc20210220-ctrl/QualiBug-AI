from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Iterable

from .real_project_onboarding import ROOT, _html_escape, _load_json, _read_text, _safe_project_id, _write_json, config_paths, load_real_project_config
from .business_adaptation_layer import build_business_adaptation_profile
from .enterprise_strategy_learning import load_enterprise_strategy_learning, weights_by_key
from .enterprise_test_knowledge import load_enterprise_test_knowledge, build_enterprise_test_knowledge

PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}
MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
TOKEN_RE = re.compile(r"[A-Za-z0-9_\-{}]+|[\u4e00-\u9fff]+")

STAGE_KEYWORDS: dict[str, list[str]] = {
    "auth.login": ["login", "signin", "auth/login", "token", "登录", "认证"],
    "identity.profile": ["profile", "me", "account", "user", "用户", "账户"],
    "cart.add": ["cart", "basket", "购物车", "additem", "add-item"],
    "coupon.apply": ["coupon", "voucher", "discount", "promo", "优惠", "折扣"],
    "order.create": ["order", "checkout", "trade", "下单", "结算", "订单"],
    "order.detail": ["order", "detail", "{order", "订单", "详情"],
    "payment.create": ["payment", "pay", "transaction", "支付", "付款"],
    "payment.callback": ["callback", "notify", "webhook", "支付回调", "回调"],
    "refund.create": ["refund", "return", "退款", "退货"],
    "stock.query": ["stock", "inventory", "sku", "库存"],
    "admin.review": ["admin", "manage", "manager", "后台", "管理员", "运营"],
    "approval.submit": ["submit", "apply", "expense", "leave", "申请", "提交", "报销"],
    "approval.approve": ["approve", "approval", "workflow", "review", "审批", "审核"],
    "approval.cancel": ["cancel", "withdraw", "撤回", "取消"],
    "finance.balance": ["balance", "wallet", "account", "余额", "钱包", "账户"],
    "finance.transfer": ["transfer", "remit", "withdraw", "deposit", "转账", "提现", "充值"],
    "finance.ledger": ["ledger", "statement", "flow", "流水", "账单", "明细"],
    "health.appointment": ["appointment", "booking", "schedule", "预约", "挂号"],
    "health.record": ["patient", "medical", "record", "prescription", "患者", "病历", "处方"],
    "education.course": ["course", "enroll", "class", "选课", "课程"],
    "education.exam": ["exam", "score", "grade", "考试", "成绩"],
    "crm.customer": ["customer", "lead", "opportunity", "客户", "线索", "商机"],
    "crm.contract": ["quote", "contract", "price", "报价", "合同"],
    "logistics.shipment": ["shipment", "waybill", "delivery", "express", "运单", "配送", "物流"],
    "content.publish": ["post", "article", "publish", "content", "发布", "内容"],
    "content.moderate": ["moderate", "review", "ban", "审核", "封禁"],
}

FLOW_TEMPLATES: list[dict[str, Any]] = [
    {
        "flow_type": "ecommerce_purchase",
        "business_domain": "ecommerce",
        "title": "电商下单支付履约链路",
        "required_any": ["order.create", "payment.create", "payment.callback", "coupon.apply", "stock.query"],
        "ordered_stages": ["auth.login", "cart.add", "coupon.apply", "order.create", "payment.create", "payment.callback", "order.detail", "stock.query"],
        "risks": ["coupon_abuse", "stock_consistency", "payment", "idempotency", "order_state"],
        "severity": "P0",
    },
    {
        "flow_type": "ecommerce_refund",
        "business_domain": "ecommerce",
        "title": "支付后退款/退货状态链路",
        "required_any": ["refund.create", "payment.create", "order.detail"],
        "ordered_stages": ["auth.login", "order.detail", "payment.create", "refund.create", "order.detail", "finance.ledger"],
        "risks": ["refund", "money_consistency", "idempotency", "order_state"],
        "severity": "P0",
    },
    {
        "flow_type": "finance_transfer",
        "business_domain": "finance",
        "title": "资金账户转账/提现一致性链路",
        "required_any": ["finance.balance", "finance.transfer", "finance.ledger"],
        "ordered_stages": ["auth.login", "finance.balance", "finance.transfer", "finance.ledger", "finance.balance"],
        "risks": ["money_consistency", "idempotency", "permission_bypass", "tenant_isolation"],
        "severity": "P0",
    },
    {
        "flow_type": "approval_workflow",
        "business_domain": "workflow",
        "title": "OA 审批提交/审批/撤回链路",
        "required_any": ["approval.submit", "approval.approve", "approval.cancel"],
        "ordered_stages": ["auth.login", "approval.submit", "approval.approve", "approval.cancel", "approval.approve"],
        "risks": ["approval_bypass", "state_transition", "idempotency", "permission_bypass"],
        "severity": "P0",
    },
    {
        "flow_type": "healthcare_appointment_record",
        "business_domain": "healthcare",
        "title": "医疗预约/病历/处方隐私链路",
        "required_any": ["health.appointment", "health.record"],
        "ordered_stages": ["auth.login", "health.appointment", "health.record"],
        "risks": ["privacy_leak", "state_transition", "idor", "permission_bypass"],
        "severity": "P0",
    },
    {
        "flow_type": "education_exam_course",
        "business_domain": "education",
        "title": "选课/考试/成绩链路",
        "required_any": ["education.course", "education.exam"],
        "ordered_stages": ["auth.login", "education.course", "education.exam"],
        "risks": ["permission_bypass", "state_transition", "business_rule", "idor"],
        "severity": "P1",
    },
    {
        "flow_type": "crm_quote_contract",
        "business_domain": "crm",
        "title": "CRM 客户/报价/合同链路",
        "required_any": ["crm.customer", "crm.contract"],
        "ordered_stages": ["auth.login", "crm.customer", "crm.contract", "admin.review"],
        "risks": ["permission_bypass", "privacy_leak", "money_consistency", "state_transition"],
        "severity": "P1",
    },
    {
        "flow_type": "logistics_delivery",
        "business_domain": "logistics",
        "title": "物流运单/配送/签收链路",
        "required_any": ["logistics.shipment", "order.detail"],
        "ordered_stages": ["auth.login", "order.detail", "logistics.shipment"],
        "risks": ["state_transition", "idor", "tenant_isolation", "business_rule"],
        "severity": "P1",
    },
    {
        "flow_type": "content_publish_moderate",
        "business_domain": "content",
        "title": "内容发布/审核/封禁链路",
        "required_any": ["content.publish", "content.moderate"],
        "ordered_stages": ["auth.login", "content.publish", "content.moderate", "content.publish"],
        "risks": ["permission_bypass", "state_transition", "business_rule", "privacy_leak"],
        "severity": "P1",
    },
]

RISK_EXPECTATIONS: dict[str, dict[str, str]] = {
    "coupon_abuse": {"expected": "优惠券跨购物车/跨用户/重复提交时金额只能被正确抵扣一次。", "bug_signal": "同一券在多次提交、订单重放或跨用户链路中重复抵扣成功。"},
    "stock_consistency": {"expected": "下单、支付失败、取消和退款后库存锁定/扣减/回滚必须一致。", "bug_signal": "流程完成后库存、订单数量或 SKU 锁定状态不一致。"},
    "payment": {"expected": "支付创建、回调、订单状态和流水入账必须金额一致且幂等。", "bug_signal": "重复回调、金额篡改或状态乱序后仍产生成功入账。"},
    "refund": {"expected": "退款金额、退款状态、订单状态和流水必须受状态机约束。", "bug_signal": "超额退款、重复退款或未支付订单退款成功。"},
    "money_consistency": {"expected": "余额、流水、订单/转账状态在完整链路前后必须守恒。", "bug_signal": "接口均返回成功但余额、流水或业务状态不守恒。"},
    "idempotency": {"expected": "重复提交同一步或回放同一业务请求不得产生重复业务结果。", "bug_signal": "重复订单、重复扣款、重复审批或重复入账。"},
    "order_state": {"expected": "订单只能按 PRD 定义的状态机前进，不能跳步/回退/重复完成。", "bug_signal": "支付前发货、退款后支付成功、取消后继续履约等非法状态成功。"},
    "approval_bypass": {"expected": "审批必须按角色、节点顺序和金额阈值流转。", "bug_signal": "申请人自审、低权限越级审批或跳过审批节点成功。"},
    "state_transition": {"expected": "业务对象状态必须按合法路径流转。", "bug_signal": "状态跳步、回退、重复推进或审核后再次修改成功。"},
    "permission_bypass": {"expected": "跨角色链路中低权限账号不能调用高权限步骤。", "bug_signal": "普通用户执行管理员/审批/审核步骤返回 2xx 或产生状态变化。"},
    "privacy_leak": {"expected": "流程中产生的客户/患者/学生/合同等敏感数据只能被授权主体访问。", "bug_signal": "替换 ID 或跨角色读取到敏感字段。"},
    "idor": {"expected": "链路产出的资源 ID 必须校验归属。", "bug_signal": "替换订单/客户/病历/运单 ID 后仍可读取或修改。"},
    "tenant_isolation": {"expected": "跨租户链路中的所有读写必须带租户边界。", "bug_signal": "A 租户账号可读取或推进 B 租户流程。"},
    "business_rule": {"expected": "后端必须强制执行 PRD 中定义的流程前置条件和不变量。", "bug_signal": "跳过前置步骤或构造非法参数后业务仍成功。"},
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


def _tokenize(text: str) -> set[str]:
    return {m.group(0).lower() for m in TOKEN_RE.finditer(text or "") if len(m.group(0)) > 1 or any("\u4e00" <= ch <= "\u9fff" for ch in m.group(0))}


def _counter(items: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for item in items:
        key = str(item or "unknown")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))


def _openapi_operations(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            spec = spec if isinstance(spec, dict) else {}
            text = " ".join([
                method_u,
                str(path),
                _safe_text(spec.get("operationId") or "", 600),
                _safe_text(spec.get("summary") or "", 800),
                _safe_text(spec.get("description") or "", 1200),
                _safe_text(spec.get("tags") or [], 1000),
                _safe_text(spec.get("parameters") or [], 1500),
                _safe_text(spec.get("requestBody") or {}, 1500),
            ])
            rows.append({"operation_id": spec.get("operationId"), "method": method_u, "path": str(path), "summary": spec.get("summary") or "", "text": text, "tokens": sorted(_tokenize(text))})
    return rows


def _load_openapi(project: str, root: Path) -> dict[str, Any]:
    paths = config_paths(project, root)
    data = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {})
    if not isinstance(data, dict) or not data.get("paths"):
        data = _load_json(paths["input_dir"] / "openapi.json", {})
    return data if isinstance(data, dict) else {}


def _stage_score(stage: str, op: dict[str, Any]) -> float:
    text = (op.get("text") or "").lower()
    tokens = set(op.get("tokens") or [])
    score = 0.0
    for kw in STAGE_KEYWORDS.get(stage, []):
        k = kw.lower()
        if k in text:
            score += 1.0 if len(k) >= 5 else 0.65
        if k in tokens:
            score += 0.7
    method = str(op.get("method") or "GET").upper()
    if stage.endswith(("create", "apply", "approve", "submit", "transfer", "callback", "publish", "moderate")) and method in MUTATION_METHODS:
        score += 0.35
    if stage.endswith(("detail", "query", "record", "ledger", "balance")) and method == "GET":
        score += 0.25
    path = str(op.get("path") or "").lower()
    if "admin" in stage and "admin" in path:
        score += 0.8
    return round(score, 4)


def _classify_operations(operations: list[dict[str, Any]], max_per_stage: int = 3) -> dict[str, list[dict[str, Any]]]:
    stage_map: dict[str, list[dict[str, Any]]] = {}
    for stage in STAGE_KEYWORDS:
        matches = []
        for op in operations:
            score = _stage_score(stage, op)
            if score > 0:
                matches.append({"stage": stage, "score": score, **{k: op.get(k) for k in ["method", "path", "summary", "operation_id"]}})
        stage_map[stage] = sorted(matches, key=lambda x: (-float(x.get("score") or 0), str(x.get("path") or "")))[:max_per_stage]
    return {k: v for k, v in stage_map.items() if v}


def _selected_domains(profile: dict[str, Any], cfg: dict[str, Any]) -> list[str]:
    manual = str(cfg.get("business_domain") or "auto").lower()
    domains: list[str] = []
    if manual and manual != "auto":
        domains.append(manual)
    for row in profile.get("selected_domains") or []:
        if isinstance(row, dict) and row.get("domain"):
            domains.append(str(row.get("domain")).lower())
    dedup: list[str] = []
    for d in domains:
        if d not in dedup:
            dedup.append(d)
    return dedup


def _flow_template_score(template: dict[str, Any], stage_map: dict[str, list[dict[str, Any]]], domains: list[str], prd_text: str) -> tuple[float, list[str]]:
    reasons: list[str] = []
    score = 0.0
    domain = str(template.get("business_domain") or "unknown")
    domain_matched = domain in domains
    if domain_matched:
        score += 1.4
        reasons.append(f"匹配业务域 {domain}")
    required = list(template.get("required_any") or [])
    present_required = [s for s in required if stage_map.get(s)]
    # If a project has a clear manual/auto business domain, keep the graph focused on
    # matching domain templates. Otherwise generic words such as post/review/order can
    # activate unrelated workflows and dilute probe quality.
    if domains and not domain_matched:
        return 0.0, []
    if present_required:
        score += 0.85 + 0.18 * min(5, len(present_required))
        reasons.append("命中关键阶段 " + "/".join(present_required[:4]))
    ordered = list(template.get("ordered_stages") or [])
    present_ordered = [s for s in ordered if stage_map.get(s)]
    score += 0.12 * len(present_ordered)
    if len(present_ordered) >= 2:
        reasons.append(f"可形成 {len(present_ordered)} 步链路")
    prd_tokens = _tokenize(prd_text)
    risk_hits = [r for r in (template.get("risks") or []) if any(tok in prd_tokens for tok in [r, r.replace("_", ""), "支付", "退款", "审批", "库存", "优惠", "余额", "病历", "成绩"])]
    if risk_hits:
        score += min(0.4, 0.12 * len(risk_hits))
        reasons.append("PRD 命中流程风险")
    return round(score, 4), reasons[:6]


def _representative_operation(stage_map: dict[str, list[dict[str, Any]]], stages: list[str], prefer_mutation: bool = True) -> dict[str, Any]:
    candidates: list[dict[str, Any]] = []
    for stage in stages:
        candidates.extend(stage_map.get(stage) or [])
    if not candidates:
        return {"method": "GET", "path": "/", "stage": "unknown"}
    if prefer_mutation:
        for c in candidates:
            if str(c.get("method") or "GET").upper() in MUTATION_METHODS:
                return c
    return candidates[0]


def _build_flow_instance(template: dict[str, Any], idx: int, stage_map: dict[str, list[dict[str, Any]]], score: float, reasons: list[str], strategy: dict[str, Any] | None, knowledge: dict[str, Any] | None) -> dict[str, Any]:
    ordered = [s for s in (template.get("ordered_stages") or []) if stage_map.get(s)]
    nodes: list[dict[str, Any]] = []
    for step_no, stage in enumerate(ordered, start=1):
        op = (stage_map.get(stage) or [{}])[0]
        nodes.append({
            "node_id": f"N{step_no:02d}",
            "stage": stage,
            "method": op.get("method") or "GET",
            "path": op.get("path") or "/",
            "operation_id": op.get("operation_id"),
            "summary": op.get("summary") or "",
            "match_score": op.get("score") or 0,
        })
    edges = []
    for a, b in zip(nodes, nodes[1:]):
        edges.append({"from": a["node_id"], "to": b["node_id"], "dependency": _edge_dependency(a.get("stage", ""), b.get("stage", ""))})
    risks = list(template.get("risks") or ["business_rule"])
    # Strategy learning can slightly reorder flow-level risks.
    if isinstance(strategy, dict):
        risk_weights = weights_by_key(strategy.get("risk_type_weights") or [], "risk_type")
        risks = sorted(risks, key=lambda r: -float((risk_weights.get(r) or {}).get("weight") or 1.0))
    context_docs = []
    if isinstance(knowledge, dict):
        contexts = knowledge.get("operation_contexts") or []
        endpoints = {f"{n['method']} {n['path']}" for n in nodes}
        for ctx in contexts:
            if isinstance(ctx, dict) and str(ctx.get("endpoint")) in endpoints:
                for c in ctx.get("top_contexts") or []:
                    if isinstance(c, dict) and c.get("doc_id"):
                        context_docs.append(str(c.get("doc_id")))
    return {
        "flow_id": f"FLOW_{idx:04d}",
        "flow_type": template.get("flow_type"),
        "business_domain": template.get("business_domain"),
        "title": template.get("title"),
        "flow_score": score,
        "activation_reasons": reasons,
        "nodes": nodes,
        "edges": edges,
        "risk_types": risks,
        "severity": template.get("severity") or "P1",
        "knowledge_doc_ids": sorted(set(context_docs))[:12],
        "scenario_count": max(1, min(4, len(risks))),
        "missing_stages": [s for s in (template.get("ordered_stages") or []) if not stage_map.get(s)],
    }


def _edge_dependency(stage_a: str, stage_b: str) -> str:
    if "login" in stage_a:
        return "认证上下文传递 token/session"
    if "order" in stage_a and "payment" in stage_b:
        return "订单号、金额和状态传递到支付步骤"
    if "payment" in stage_a and ("refund" in stage_b or "order" in stage_b):
        return "支付结果驱动退款/订单状态"
    if "coupon" in stage_a and "order" in stage_b:
        return "优惠券抵扣结果进入订单金额"
    if "approval.submit" == stage_a and "approval" in stage_b:
        return "申请单 ID 和审批节点流转"
    if "balance" in stage_a or "ledger" in stage_b:
        return "资金前后置校验"
    return "上一步业务结果作为下一步输入"


def _flow_risk_score(flow: dict[str, Any], risk: str) -> float:
    score = float(flow.get("flow_score") or 0) / 4.0
    if risk in {"payment", "refund", "money_consistency", "approval_bypass", "privacy_leak"}:
        score += 0.22
    if len(flow.get("nodes") or []) >= 4:
        score += 0.18
    if flow.get("knowledge_doc_ids"):
        score += 0.08
    return round(max(0.05, min(1.0, score)), 6)


def _probe_steps(flow: dict[str, Any], risk: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    nodes = flow.get("nodes") or []
    for i, node in enumerate(nodes, start=1):
        stage = str(node.get("stage") or "")
        mutation_note = "记录响应中的业务 ID / 金额 / 状态，供后续步骤复用。"
        if risk in {"idempotency", "payment", "refund"} and i == len(nodes):
            mutation_note = "重复执行该步骤或回放上一步业务 ID，验证幂等和状态机。"
        if risk in {"permission_bypass", "approval_bypass", "privacy_leak", "idor", "tenant_isolation"} and ("admin" in stage or "approve" in stage or i == len(nodes)):
            mutation_note = "切换为低权限/其他用户/其他租户身份，验证后端权限和归属校验。"
        steps.append({
            "step": i,
            "stage": stage,
            "method": node.get("method") or "GET",
            "path": node.get("path") or "/",
            "actor": "normal_user" if risk in {"permission_bypass", "approval_bypass", "privacy_leak", "idor", "tenant_isolation"} else "normal_user",
            "assertion": mutation_note,
        })
    return steps


def generate_business_flow_scenario_probes(flow_graph: dict[str, Any], max_count: int = 80) -> list[dict[str, Any]]:
    probes: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for flow in sorted(flow_graph.get("flows") or [], key=lambda f: (-float(f.get("flow_score") or 0), str(f.get("flow_type") or ""))):
        nodes = flow.get("nodes") or []
        if len(nodes) < 2:
            continue
        for risk in flow.get("risk_types") or ["business_rule"]:
            key = (str(flow.get("flow_id")), str(risk))
            if key in seen:
                continue
            seen.add(key)
            rep = _representative_from_flow(flow, risk)
            exp = RISK_EXPECTATIONS.get(str(risk), RISK_EXPECTATIONS["business_rule"])
            destructive = any(str(n.get("method") or "GET").upper() in MUTATION_METHODS for n in nodes) or risk in {"payment", "refund", "idempotency", "money_consistency", "approval_bypass"}
            probes.append({
                "probe_id": f"FLOW_SCN_{len(probes)+1:04d}",
                "source": "enterprise_business_flow_graph",
                "flow_id": flow.get("flow_id"),
                "flow_type": flow.get("flow_type"),
                "business_domain": flow.get("business_domain") or "unknown",
                "risk_type": str(risk),
                "title": f"多接口链路场景：{flow.get('title')} · {risk}",
                "actor": "normal_user",
                "method": rep.get("method") or "GET",
                "path": rep.get("path") or "/",
                "severity": "P0" if str(risk) in {"payment", "refund", "money_consistency", "approval_bypass", "privacy_leak"} else flow.get("severity") or "P1",
                "expected": exp["expected"],
                "bug_signal": exp["bug_signal"],
                "destructive": destructive,
                "execution_policy": "candidate_only",
                "flow_steps": _probe_steps(flow, str(risk)),
                "flow_node_count": len(nodes),
                "flow_edge_count": len(flow.get("edges") or []),
                "flow_score": flow.get("flow_score"),
                "knowledge_doc_ids": flow.get("knowledge_doc_ids") or [],
                "priority_hint": _flow_risk_score(flow, str(risk)),
            })
            if len(probes) >= max_count:
                return probes
    return probes


def _representative_from_flow(flow: dict[str, Any], risk: str) -> dict[str, Any]:
    nodes = list(flow.get("nodes") or [])
    if not nodes:
        return {"method": "GET", "path": "/"}
    risk_terms = {
        "payment": ["payment", "callback", "pay"],
        "refund": ["refund"],
        "coupon_abuse": ["coupon", "discount"],
        "stock_consistency": ["stock", "inventory"],
        "approval_bypass": ["approve", "approval", "review"],
        "money_consistency": ["balance", "ledger", "transfer", "wallet"],
        "privacy_leak": ["record", "patient", "customer", "student", "profile"],
        "idor": ["detail", "order", "customer", "record"],
    }.get(risk, [])
    for node in reversed(nodes):
        text = f"{node.get('stage')} {node.get('path')} {node.get('summary')}".lower()
        if any(t in text for t in risk_terms):
            return node
    for node in reversed(nodes):
        if str(node.get("method") or "GET").upper() in MUTATION_METHODS:
            return node
    return nodes[-1]


def build_business_flow_graph(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    options = options or {}
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    paths = config_paths(project, root)
    openapi = _load_openapi(project, root)
    operations = _openapi_operations(openapi)
    profile = build_business_adaptation_profile(project, root)
    strategy = load_enterprise_strategy_learning(project, root)
    knowledge = load_enterprise_test_knowledge(project, root)
    if knowledge is None and not options.get("skip_knowledge_build"):
        try:
            knowledge = build_enterprise_test_knowledge(project, root, options={"skip_probe_preview": True})
        except Exception:
            knowledge = None
    prd_text = "\n".join(_read_text(paths["input_dir"] / name) for name in ["prd.md", "requirements.md", "business_rules.md"])
    domains = _selected_domains(profile, cfg)
    stage_map = _classify_operations(operations)
    flows: list[dict[str, Any]] = []
    for template in FLOW_TEMPLATES:
        score, reasons = _flow_template_score(template, stage_map, domains, prd_text)
        if score < float(options.get("min_flow_score", 1.0) or 1.0):
            continue
        flow = _build_flow_instance(template, len(flows) + 1, stage_map, score, reasons, strategy, knowledge)
        if len(flow.get("nodes") or []) >= 2:
            flows.append(flow)
    flows = sorted(flows, key=lambda f: (-float(f.get("flow_score") or 0), str(f.get("flow_type") or "")))[: int(options.get("max_flows", 20) or 20)]
    scenario_probes = generate_business_flow_scenario_probes({"flows": flows}, max_count=int(options.get("scenario_probe_count", 80) or 80))
    summary = {
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at_utc": _now(),
        "openapi_operation_count": len(operations),
        "classified_stage_count": len(stage_map),
        "flow_count": len(flows),
        "scenario_probe_count": len(scenario_probes),
        "business_domains": domains,
        "flow_type_distribution": _counter([str(f.get("flow_type")) for f in flows]),
        "risk_distribution": _counter([str(r) for f in flows for r in (f.get("risk_types") or [])]),
        "source_fusion": {
            "business_adaptation": bool(profile),
            "strategy_learning": bool(strategy),
            "enterprise_knowledge": bool(knowledge),
        },
    }
    graph = {
        "phase": "phase38_business_flow_graph",
        "project_id": project,
        "summary": summary,
        "stage_map": stage_map,
        "flows": flows,
        "scenario_probes": scenario_probes,
        "governance": {
            "real_project_mode": True,
            "uses_only_real_project_public_inputs": True,
            "uses_no_benchmark_answer_files": True,
            "flow_inputs": ["openapi", "prd", "business_adaptation_profile", "enterprise_strategy_learning", "enterprise_test_knowledge"],
            "execution_policy": "multi-interface scenarios are generated as candidate_only by default",
        },
    }
    graph["private_leak_check"] = _private_leak_check(graph)
    out_dir = root / "platform_outputs" / project / "business_flow_graph"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "business_flow_graph.json", graph)
    _write_json(out_dir / "business_flow_graph_summary.json", {"summary": summary, "private_leak_check": graph["private_leak_check"]})
    _write_json(ws_dir / "business_flow_graph.json", graph)
    _write_json(ws_dir / "business_flow_scenario_probes.json", {"items": scenario_probes})
    _write_text(out_dir / "business_flow_graph_report.html", render_business_flow_graph_report(graph))
    return graph


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted([m for m in PRIVATE_MARKERS if m.lower() in text])
    return {"passed": not leaks, "leak_terms": leaks}


def load_business_flow_graph(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = root / "platform_workspace" / project / "defect_discovery" / "business_flow_graph.json"
    if not path.exists():
        path = root / "platform_outputs" / project / "business_flow_graph" / "business_flow_graph.json"
    if not path.exists():
        return None
    data = _load_json(path, {})
    return data if isinstance(data, dict) else None


def generate_business_flow_probes(openapi: dict[str, Any] | None = None, cfg: dict[str, Any] | None = None, project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    graph = load_business_flow_graph(project, root)
    if graph is None:
        graph = build_business_flow_graph(project, root, options={"scenario_probe_count": max_count or 80})
    probes = list(graph.get("scenario_probes") or [])
    if max_count is not None:
        probes = probes[: int(max_count)]
    return [p for p in probes if isinstance(p, dict)]


def render_business_flow_graph_report(graph: dict[str, Any]) -> str:
    summary = graph.get("summary") or {}
    cards = "".join(f"<div class='card'><span>{_html_escape(k)}</span><b>{_html_escape(v)}</b></div>" for k, v in summary.items() if k not in {"flow_type_distribution", "risk_distribution", "source_fusion"})
    flow_rows = []
    for flow in graph.get("flows") or []:
        steps = " → ".join(f"{n.get('method')} {n.get('path')}" for n in (flow.get("nodes") or [])[:8])
        flow_rows.append(f"<tr><td>{_html_escape(flow.get('flow_id'))}</td><td>{_html_escape(flow.get('title'))}</td><td>{_html_escape(flow.get('flow_score'))}</td><td>{_html_escape(', '.join(flow.get('risk_types') or []))}</td><td>{_html_escape(steps)}</td></tr>")
    probe_rows = []
    for p in (graph.get("scenario_probes") or [])[:80]:
        probe_rows.append(f"<tr><td>{_html_escape(p.get('probe_id'))}</td><td>{_html_escape(p.get('severity'))}</td><td>{_html_escape(p.get('risk_type'))}</td><td>{_html_escape(p.get('method'))} {_html_escape(p.get('path'))}</td><td>{_html_escape(len(p.get('flow_steps') or []))}</td><td>{_html_escape(p.get('bug_signal'))}</td></tr>")
    stage_rows = []
    for stage, ops in (graph.get("stage_map") or {}).items():
        endpoints = "；".join(f"{o.get('method')} {o.get('path')}" for o in (ops or [])[:3])
        stage_rows.append(f"<tr><td>{_html_escape(stage)}</td><td>{_html_escape(endpoints)}</td></tr>")
    leak = graph.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Business Flow Graph</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#fef3c7;color:#92400e}}</style></head><body>
<section class='hero'><span class='badge'>Phase38</span><h1>企业业务链路图谱 + 多接口场景探针</h1><p>从 OpenAPI、PRD、业务适配画像、策略学习和企业知识库中识别跨接口业务流程，生成流程级攻击/测试场景。</p><p>私有数据泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>图谱概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>阶段识别</h2><table><thead><tr><th>业务阶段</th><th>匹配接口</th></tr></thead><tbody>{''.join(stage_rows) or '<tr><td colspan="2">暂无阶段</td></tr>'}</tbody></table></section>
<section class='panel'><h2>业务链路</h2><table><thead><tr><th>ID</th><th>链路</th><th>Score</th><th>风险</th><th>步骤</th></tr></thead><tbody>{''.join(flow_rows) or '<tr><td colspan="5">暂无链路</td></tr>'}</tbody></table></section>
<section class='panel'><h2>多接口场景探针</h2><table><thead><tr><th>ID</th><th>等级</th><th>风险</th><th>代表接口</th><th>步骤数</th><th>缺陷信号</th></tr></thead><tbody>{''.join(probe_rows) or '<tr><td colspan="6">暂无探针</td></tr>'}</tbody></table></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    graph = build_business_flow_graph(project)
    print(json.dumps({"ok": True, "project_id": project, "summary": graph.get("summary"), "private_leak_check": graph.get("private_leak_check")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
