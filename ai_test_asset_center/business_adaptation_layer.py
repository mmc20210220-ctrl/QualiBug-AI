from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .real_project_onboarding import ROOT, _html_escape, _load_json, _read_text, _safe_project_id, _write_json, config_paths, load_real_project_config


MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}


DOMAIN_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "ecommerce": {
        "name": "电商 / 交易履约",
        "keywords": ["order", "checkout", "cart", "coupon", "payment", "refund", "inventory", "sku", "订单", "购物车", "优惠券", "支付", "退款", "库存"],
        "entities": ["user", "product", "cart", "coupon", "order", "payment", "refund", "inventory"],
        "risks": ["permission_bypass", "idor", "coupon_abuse", "stock_consistency", "payment", "refund", "idempotency"],
        "critical_paths": ["checkout", "payment", "refund", "order", "inventory", "coupon"],
    },
    "finance": {
        "name": "金融 / 资金账户",
        "keywords": ["account", "balance", "transfer", "withdraw", "deposit", "repay", "loan", "settlement", "ledger", "limit", "账户", "余额", "转账", "提现", "充值", "放款", "还款", "清结算", "账本", "额度"],
        "entities": ["account", "balance", "transaction", "ledger", "transfer", "withdrawal", "loan", "repayment"],
        "risks": ["money_consistency", "account_ownership", "limit_bypass", "idempotency", "state_transition", "audit_trace"],
        "critical_paths": ["transfer", "withdraw", "deposit", "repay", "loan", "settlement", "ledger", "balance", "payment"],
    },
    "healthcare": {
        "name": "医疗 / 患者诊疗",
        "keywords": ["patient", "doctor", "appointment", "clinic", "prescription", "medical", "record", "diagnosis", "药", "患者", "医生", "挂号", "预约", "处方", "病历", "诊断"],
        "entities": ["patient", "doctor", "appointment", "prescription", "medical_record", "clinic"],
        "risks": ["privacy_leak", "appointment_conflict", "prescription_rule", "idor", "permission_bypass", "audit_trace"],
        "critical_paths": ["patient", "doctor", "appointment", "schedule", "prescription", "record", "diagnosis"],
    },
    "education": {
        "name": "教育 / 课程考试",
        "keywords": ["course", "class", "student", "teacher", "exam", "score", "grade", "enroll", "lesson", "课程", "学生", "老师", "考试", "成绩", "选课", "报名"],
        "entities": ["student", "teacher", "course", "class", "exam", "score", "enrollment"],
        "risks": ["score_tampering", "enrollment_capacity", "permission_bypass", "idor", "state_transition"],
        "critical_paths": ["course", "class", "enroll", "exam", "score", "grade", "student", "teacher"],
    },
    "workflow": {
        "name": "OA / 审批流",
        "keywords": ["approval", "approve", "reject", "workflow", "expense", "reimburse", "leave", "invoice", "申请", "审批", "驳回", "报销", "请假", "发票", "流程"],
        "entities": ["applicant", "approver", "approval", "expense", "invoice", "workflow_task"],
        "risks": ["approval_bypass", "amount_limit", "state_transition", "permission_bypass", "audit_trace"],
        "critical_paths": ["approval", "approve", "reject", "workflow", "expense", "reimburse", "leave", "invoice"],
    },
    "crm": {
        "name": "CRM / 销售客户",
        "keywords": ["lead", "customer", "contact", "opportunity", "quote", "contract", "deal", "sales", "客户", "线索", "商机", "报价", "合同", "销售"],
        "entities": ["lead", "customer", "contact", "opportunity", "quote", "contract"],
        "risks": ["ownership_boundary", "quote_discount", "contract_state", "permission_bypass", "audit_trace"],
        "critical_paths": ["lead", "customer", "contact", "opportunity", "quote", "contract", "deal"],
    },
    "logistics": {
        "name": "物流 / 履约轨迹",
        "keywords": ["shipment", "delivery", "parcel", "tracking", "route", "warehouse", "carrier", "waybill", "物流", "快递", "配送", "运单", "仓库", "轨迹", "签收"],
        "entities": ["shipment", "parcel", "waybill", "warehouse", "carrier", "tracking_event"],
        "risks": ["tracking_tampering", "delivery_state", "ownership_boundary", "permission_bypass", "idempotency"],
        "critical_paths": ["shipment", "delivery", "parcel", "tracking", "route", "warehouse", "waybill"],
    },
    "content": {
        "name": "内容 / 社区审核",
        "keywords": ["post", "comment", "content", "moderation", "review", "publish", "report", "ban", "帖子", "评论", "内容", "审核", "发布", "举报", "封禁"],
        "entities": ["user", "post", "comment", "content", "moderation_case", "report"],
        "risks": ["moderation_bypass", "author_ownership", "state_transition", "permission_bypass", "audit_trace"],
        "critical_paths": ["post", "comment", "content", "moderation", "review", "publish", "report", "ban"],
    },
}


RISK_PLAYBOOKS: dict[str, dict[str, Any]] = {
    "money_consistency": {"severity": "P0", "title": "资金金额 / 账本一致性风险", "expected": "余额、流水、订单/交易状态必须一致，不能出现负数、重复入账或金额不守恒", "bug_signal": "接口成功但金额、余额、流水或状态存在不一致", "destructive": True},
    "account_ownership": {"severity": "P0", "title": "账户归属越权风险", "expected": "只能访问或操作本人/授权账户", "bug_signal": "普通用户可读取或操作其他账户资源", "destructive": False},
    "limit_bypass": {"severity": "P1", "title": "额度 / 风控限制绕过风险", "expected": "提现、转账、授信、报销等金额必须受额度和风控规则限制", "bug_signal": "超过额度或绕过限制仍返回成功", "destructive": True},
    "privacy_leak": {"severity": "P0", "title": "隐私数据泄露风险", "expected": "患者、客户、学生等敏感资料只能被授权角色访问", "bug_signal": "非授权角色拿到敏感详情、列表或历史记录", "destructive": False},
    "appointment_conflict": {"severity": "P1", "title": "预约冲突 / 资源占用风险", "expected": "同一医生、教室、仓库、配送资源在同一时段不能被重复占用", "bug_signal": "冲突预约或重复占用仍成功", "destructive": True},
    "prescription_rule": {"severity": "P0", "title": "处方规则绕过风险", "expected": "处方必须由授权医生开具，药品、剂量、患者关系和状态必须校验", "bug_signal": "非授权开方、超量开方或状态非法仍成功", "destructive": True},
    "score_tampering": {"severity": "P0", "title": "成绩 / 评价篡改风险", "expected": "成绩只能由授权教师或流程写入，学生不可越权修改", "bug_signal": "非授权角色可修改成绩或评价结果", "destructive": True},
    "enrollment_capacity": {"severity": "P1", "title": "选课 / 报名容量超限风险", "expected": "课程、考试、活动名额必须校验容量、资格和重复报名", "bug_signal": "超名额、重复报名或不满足资格仍成功", "destructive": True},
    "approval_bypass": {"severity": "P0", "title": "审批流跳步 / 越权审批风险", "expected": "审批必须按角色、顺序、金额阈值和状态机推进", "bug_signal": "申请人自审、跳过节点、越级通过或非法状态流转成功", "destructive": True},
    "amount_limit": {"severity": "P1", "title": "金额阈值规则绕过风险", "expected": "报销、报价、合同、授信等金额必须命中正确审批/折扣/额度规则", "bug_signal": "超过阈值未触发审批或异常金额仍成功", "destructive": True},
    "ownership_boundary": {"severity": "P1", "title": "业务归属边界风险", "expected": "销售、客服、老师、医生等只能操作自己负责或授权范围内的数据", "bug_signal": "跨负责人/跨部门访问或操作成功", "destructive": False},
    "quote_discount": {"severity": "P1", "title": "报价折扣权限风险", "expected": "报价折扣、合同金额和审批权限必须一致", "bug_signal": "低权限用户可设置超权限折扣或绕过审批", "destructive": True},
    "contract_state": {"severity": "P1", "title": "合同状态机风险", "expected": "合同创建、审批、签署、作废、归档必须遵守状态机", "bug_signal": "非法状态流转仍成功", "destructive": True},
    "tracking_tampering": {"severity": "P1", "title": "物流轨迹篡改风险", "expected": "运单轨迹只能由授权系统/人员按时间顺序写入", "bug_signal": "普通用户可写入轨迹、逆序轨迹或伪造签收", "destructive": True},
    "delivery_state": {"severity": "P1", "title": "履约状态机风险", "expected": "出库、揽收、运输、签收、拒收、退回必须遵守状态机", "bug_signal": "非法履约状态跳转仍成功", "destructive": True},
    "moderation_bypass": {"severity": "P1", "title": "内容审核绕过风险", "expected": "内容发布、举报、封禁、审核状态必须按规则控制", "bug_signal": "未审核内容可发布、封禁用户可操作或审核状态非法流转", "destructive": True},
    "author_ownership": {"severity": "P1", "title": "作者归属越权风险", "expected": "用户只能编辑/删除自己的内容，管理员操作必须留痕", "bug_signal": "普通用户可编辑/删除他人内容", "destructive": True},
    "state_transition": {"severity": "P1", "title": "业务状态机非法流转风险", "expected": "核心对象状态只能按 PRD 允许路径流转", "bug_signal": "非法状态跳转、回退或重复提交仍成功", "destructive": True},
    "audit_trace": {"severity": "P2", "title": "审计留痕缺失风险", "expected": "敏感操作应记录操作者、对象、前后状态和时间", "bug_signal": "敏感操作成功但无可追踪审计证据", "destructive": False},
    "permission_bypass": {"severity": "P1", "title": "角色权限绕过风险", "expected": "接口必须按 RBAC / ABAC / 数据权限校验", "bug_signal": "低权限角色访问高权限接口返回成功", "destructive": False},
    "idor": {"severity": "P1", "title": "水平越权 / IDOR 风险", "expected": "资源详情和操作必须校验归属", "bug_signal": "替换 ID 后可访问或操作他人资源", "destructive": False},
    "idempotency": {"severity": "P1", "title": "幂等 / 重复提交风险", "expected": "创建、支付、审批、预约、发货等重复请求不能产生重复业务结果", "bug_signal": "重复创建、重复扣减、重复审批或重复状态推进", "destructive": True},
}


ENDPOINT_RISK_HINTS: list[tuple[str, list[str]]] = [
    ("money_consistency", ["balance", "ledger", "transaction", "transfer", "withdraw", "deposit", "settlement", "余额", "流水", "转账", "提现"]),
    ("account_ownership", ["account", "wallet", "card", "账户", "钱包", "银行卡"]),
    ("limit_bypass", ["limit", "quota", "withdraw", "transfer", "loan", "额度", "限额", "提现", "授信"]),
    ("privacy_leak", ["patient", "medical", "record", "diagnosis", "customer", "student", "患者", "病历", "诊断", "客户", "学生"]),
    ("appointment_conflict", ["appointment", "schedule", "booking", "slot", "预约", "排班", "时段"]),
    ("prescription_rule", ["prescription", "medicine", "drug", "处方", "药"]),
    ("score_tampering", ["score", "grade", "exam", "成绩", "考试", "评分"]),
    ("enrollment_capacity", ["enroll", "course", "class", "capacity", "选课", "报名", "课程", "名额"]),
    ("approval_bypass", ["approval", "approve", "workflow", "review", "审批", "审核", "流程"]),
    ("amount_limit", ["expense", "reimburse", "invoice", "amount", "quote", "contract", "报销", "发票", "金额", "报价", "合同"]),
    ("ownership_boundary", ["lead", "customer", "opportunity", "owner", "sales", "线索", "客户", "商机", "负责人"]),
    ("quote_discount", ["quote", "discount", "price", "报价", "折扣", "价格"]),
    ("contract_state", ["contract", "sign", "archive", "合同", "签署", "归档"]),
    ("tracking_tampering", ["tracking", "waybill", "shipment", "轨迹", "运单", "物流"]),
    ("delivery_state", ["delivery", "parcel", "warehouse", "sign", "配送", "包裹", "仓库", "签收"]),
    ("moderation_bypass", ["moderation", "review", "report", "ban", "审核", "举报", "封禁"]),
    ("author_ownership", ["post", "comment", "content", "author", "帖子", "评论", "内容", "作者"]),
]


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


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
            joined = " ".join([
                str(path),
                method_u,
                str(spec.get("operationId") or ""),
                str(spec.get("summary") or ""),
                str(spec.get("description") or ""),
                _normalize_text(spec.get("tags") or []),
                _normalize_text(spec.get("parameters") or []),
                _normalize_text(spec.get("requestBody") or {}),
            ])
            rows.append({"method": method_u, "path": str(path), "operation_id": spec.get("operationId"), "summary": spec.get("summary") or "", "text": joined})
    return rows


def _load_project_text(project: str, root: Path) -> str:
    paths = config_paths(project, root)
    pieces: list[str] = []
    for name in ["prd.md", "requirements.md", "business_rules.md", "openapi_raw.txt"]:
        pieces.append(_read_text(paths["input_dir"] / name))
    for rel in ["business_knowledge_model.json", "real_project_risk_profile.json"]:
        data = _load_json(paths["workspace_dir"] / rel, {})
        if data:
            pieces.append(_normalize_text(data))
    return "\n".join(p for p in pieces if p)


def _score_domains(text: str, operations: list[dict[str, Any]], cfg: dict[str, Any]) -> dict[str, float]:
    combined = (text + "\n" + "\n".join(op["text"] for op in operations)).lower()
    scores: dict[str, float] = {}
    configured = str(cfg.get("business_domain") or cfg.get("domain") or "").lower().strip()
    for domain, playbook in DOMAIN_PLAYBOOKS.items():
        keywords = [str(k).lower() for k in playbook.get("keywords") or []]
        score = 0.0
        hits = set()
        for kw in keywords:
            if kw and kw in combined:
                hits.add(kw)
                score += 1.0
        for op in operations:
            op_text = op["text"].lower()
            for critical in playbook.get("critical_paths") or []:
                if str(critical).lower() in op_text:
                    score += 1.4
        if configured == domain:
            score += 8.0
        scores[domain] = round(score, 4)
    return dict(sorted(scores.items(), key=lambda kv: (-kv[1], kv[0])))


# Fail-closed: a single weak keyword must not invent a vertical domain pack.
# Aligns with multi_industry_business_reasoning evidence gate (no ecommerce default).
_MIN_DOMAIN_SCORE = 2.0


def _select_domains(scores: dict[str, float]) -> list[str]:
    """Select domains only from positive evidence. Never default to ecommerce."""
    positive = [(d, s) for d, s in scores.items() if float(s or 0) >= _MIN_DOMAIN_SCORE]
    if not positive:
        return []
    top_score = positive[0][1]
    selected = [d for d, s in positive if s >= max(_MIN_DOMAIN_SCORE, top_score * 0.55)]
    return selected[:3]


def _operation_risks(op: dict[str, Any], selected_domains: list[str]) -> list[str]:
    text = op["text"].lower()
    risks: list[str] = []
    for risk, hints in ENDPOINT_RISK_HINTS:
        if any(h.lower() in text for h in hints):
            risks.append(risk)
    if re.search(r"/(admin|manage|manager|backend|console|staff)(/|$)|管理员|后台|管理", text):
        risks.append("permission_bypass")
    if "{" in op["path"] and "}" in op["path"] and op["method"] in {"GET", "PUT", "PATCH", "DELETE"}:
        risks.append("idor")
    if op["method"] in MUTATION_METHODS and any(word in text for word in ["create", "submit", "apply", "pay", "approve", "book", "publish", "创建", "提交", "申请", "支付", "审批", "预约", "发布"]):
        risks.append("idempotency")
    for domain in selected_domains:
        for risk in DOMAIN_PLAYBOOKS.get(domain, {}).get("risks") or []:
            # Domain default risks are only added when the operation text contains a critical path word,
            # avoiding noisy one-size-fits-all probes.
            criticals = DOMAIN_PLAYBOOKS.get(domain, {}).get("critical_paths") or []
            if any(str(c).lower() in text for c in criticals):
                risks.append(str(risk))
    out: list[str] = []
    seen: set[str] = set()
    for risk in risks:
        if risk not in seen and risk in RISK_PLAYBOOKS:
            seen.add(risk)
            out.append(risk)
    return out[:5]


def _infer_entities(text: str, operations: list[dict[str, Any]], selected_domains: list[str]) -> list[dict[str, Any]]:
    combined = (text + "\n" + "\n".join(op["text"] for op in operations)).lower()
    rows: list[dict[str, Any]] = []
    for domain in selected_domains:
        for entity in DOMAIN_PLAYBOOKS.get(domain, {}).get("entities") or []:
            evidence = []
            if str(entity).lower() in combined:
                evidence.append(entity)
            for op in operations:
                if str(entity).lower() in op["text"].lower() or str(entity).replace("_", "-").lower() in op["text"].lower():
                    evidence.append(f"{op['method']} {op['path']}")
            if evidence:
                rows.append({"entity": entity, "domain": domain, "evidence": evidence[:6]})
    return rows


def _domain_risk_matrix(selected_domains: list[str], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for domain in selected_domains:
        for risk in DOMAIN_PLAYBOOKS.get(domain, {}).get("risks") or []:
            playbook = RISK_PLAYBOOKS.get(risk, {})
            matched = [f"{op['method']} {op['path']}" for op in operations if risk in _operation_risks(op, [domain])]
            rows.append({
                "risk_type": risk,
                "domain": domain,
                "severity": playbook.get("severity", "P2"),
                "title": playbook.get("title") or risk,
                "expected": playbook.get("expected") or "业务规则必须被后端强制校验",
                "bug_signal": playbook.get("bug_signal") or "异常请求仍返回成功或产生业务副作用",
                "matched_operation_count": len(matched),
                "matched_operations": matched[:12],
            })
    # De-duplicate cross-domain risks while preserving matched operation union.
    merged: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row["risk_type"]
        if key not in merged:
            merged[key] = dict(row)
            merged[key]["domains"] = [row["domain"]]
        else:
            merged[key]["domains"].append(row["domain"])
            ops = list(dict.fromkeys([*merged[key].get("matched_operations", []), *row.get("matched_operations", [])]))
            merged[key]["matched_operations"] = ops[:12]
            merged[key]["matched_operation_count"] = len(ops)
    return sorted(merged.values(), key=lambda r: (str(r.get("severity")), -int(r.get("matched_operation_count") or 0), str(r.get("risk_type"))))


def build_business_adaptation_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {}) or _load_json(paths["input_dir"] / "openapi.json", {})
    if not isinstance(openapi, dict):
        openapi = {}
    operations = _openapi_operations(openapi)
    text = _load_project_text(project, root)
    scores = _score_domains(text, operations, cfg)
    selected = _select_domains(scores)
    configured_mode = str(cfg.get("business_domain") or cfg.get("domain") or "auto")
    domain_mode = (
        configured_mode
        if configured_mode and configured_mode not in {"auto", "unknown", "general", "general_business"}
        else ("multi_domain" if len(selected) > 1 else (selected[0] if selected else "unknown_general_business"))
    )
    profile = {
        "phase": "phase34_business_adaptation_layer",
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "business_domain_mode": domain_mode,
        "selected_domains": [{"domain": d, "name": DOMAIN_PLAYBOOKS[d]["name"], "score": scores.get(d, 0), "risks": DOMAIN_PLAYBOOKS[d]["risks"], "entities": DOMAIN_PLAYBOOKS[d]["entities"]} for d in selected],
        "domain_scores": scores,
        "domain_selection": {
            "min_score": _MIN_DOMAIN_SCORE,
            "status": "ok" if selected else "unknown_general_business",
            "operator_note": (
                None
                if selected
                else "No domain pack activated: evidence below threshold. Cross-cutting endpoint risks may still apply; empty domain list is not ecommerce."
            ),
        },
        "operation_count": len(operations),
        "endpoint_domain_map": [
            {
                "method": op["method"],
                "path": op["path"],
                "operation_id": op.get("operation_id"),
                "matched_domains": [d for d in selected if any(str(c).lower() in op["text"].lower() for c in DOMAIN_PLAYBOOKS[d].get("critical_paths") or [])],
                "risk_types": _operation_risks(op, selected),
            }
            for op in operations
        ],
        "business_entity_map": _infer_entities(text, operations, selected),
        "adaptive_risk_matrix": _domain_risk_matrix(selected, operations),
        "governance": {
            "uses_only_real_project_public_inputs": True,
            "uses_no_benchmark_answer_files": True,
            "input_sources": ["prd.md", "openapi.json", "real_project_config.json", "optional business_knowledge_model.json"],
        },
    }
    profile["private_leak_check"] = _private_leak_check(profile)
    out_dir = root / "platform_outputs" / project / "business_adaptation"
    ws_dir = root / "platform_workspace" / project / "defect_discovery"
    _write_json(out_dir / "business_adaptation_profile.json", profile)
    _write_json(ws_dir / "business_adaptation_profile.json", profile)
    (out_dir / "business_adaptation_report.html").write_text(render_business_adaptation_report(profile), encoding="utf-8")
    return profile


def load_business_adaptation_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = root / "platform_workspace" / project / "defect_discovery" / "business_adaptation_profile.json"
    if path.exists():
        data = _load_json(path, {})
        if isinstance(data, dict):
            return data
    return None


def generate_business_adaptive_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    profile = load_business_adaptation_profile(project, root) or build_business_adaptation_profile(project, root)
    selected_domains = [str(d.get("domain")) for d in profile.get("selected_domains") or [] if d.get("domain") in DOMAIN_PLAYBOOKS]
    # Empty selected_domains is intentional (unknown_general_business). Still emit
    # cross-cutting ENDPOINT_RISK_HINTS probes — never invent an ecommerce pack.
    operations = _openapi_operations(openapi if isinstance(openapi, dict) else {})
    mode = str(cfg.get("discovery_mode") or "safe").lower()
    allow_destructive = bool(cfg.get("allow_destructive_tests"))
    max_count = int(max_count or cfg.get("max_probe_count") or 100)
    probes: list[dict[str, Any]] = []
    for op in operations:
        risks = _operation_risks(op, selected_domains)
        for risk in risks:
            playbook = RISK_PLAYBOOKS.get(risk, {})
            destructive = bool(playbook.get("destructive")) or op["method"] in MUTATION_METHODS
            execution_policy = "execute"
            if destructive and (mode == "safe" or not allow_destructive):
                execution_policy = "candidate_only"
            elif mode == "standard" and destructive and not allow_destructive:
                execution_policy = "candidate_only"
            matched_domain = next(
                (d for d in selected_domains if risk in DOMAIN_PLAYBOOKS[d].get("risks", [])),
                (selected_domains[0] if selected_domains else "unknown_general_business"),
            )
            probes.append({
                "probe_id": f"RP_ADAPT_{len(probes)+1:04d}",
                "source": "business_adaptation_layer",
                "business_domain": matched_domain,
                "risk_type": risk,
                "title": playbook.get("title") or f"{risk} 业务风险",
                "actor": "normal_user",
                "path": op["path"],
                "method": op["method"],
                "operation_id": op.get("operation_id"),
                "severity": playbook.get("severity", "P2"),
                "expected": playbook.get("expected") or "业务规则必须被后端强制校验",
                "bug_signal": playbook.get("bug_signal") or "异常请求仍返回成功或产生业务副作用",
                "destructive": destructive,
                "execution_policy": execution_policy,
                "confidence_prior": 0.68 if risk in {"money_consistency", "privacy_leak", "approval_bypass", "score_tampering"} else 0.58,
                "matched_domains": selected_domains,
                "adaptation_reason": "由 PRD + OpenAPI 自动识别业务域和风险剧本生成",
                "discovery_mode": mode,
            })
            if len(probes) >= max_count:
                return probes
    # Ensure each selected non-ecommerce domain contributes at least one candidate even when OpenAPI names are sparse.
    if not probes:
        for domain in selected_domains:
            for risk in DOMAIN_PLAYBOOKS.get(domain, {}).get("risks", [])[:3]:
                playbook = RISK_PLAYBOOKS.get(risk, {})
                probes.append({
                    "probe_id": f"RP_ADAPT_{len(probes)+1:04d}",
                    "source": "business_adaptation_layer",
                    "business_domain": domain,
                    "risk_type": risk,
                    "title": playbook.get("title") or f"{risk} 业务风险候选",
                    "actor": "normal_user",
                    "path": "/",
                    "method": "GET",
                    "severity": playbook.get("severity", "P2"),
                    "expected": playbook.get("expected") or "业务规则必须被后端强制校验",
                    "bug_signal": playbook.get("bug_signal") or "需要结合业务链路确认",
                    "destructive": False,
                    "execution_policy": "candidate_only",
                    "confidence_prior": 0.4,
                    "matched_domains": selected_domains,
                    "adaptation_reason": "业务域已识别，但 OpenAPI 命名不足，生成待确认风险候选",
                    "discovery_mode": mode,
                })
                if len(probes) >= max_count:
                    return probes
    return probes[:max_count]


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = json.dumps(data, ensure_ascii=False).lower()
    leaks = sorted(m for m in PRIVATE_MARKERS if m in text)
    return {"passed": not leaks, "leak_terms": leaks}


def render_business_adaptation_report(profile: dict[str, Any]) -> str:
    domains = profile.get("selected_domains") or []
    rows = "".join(f"<tr><td>{_html_escape(d.get('domain'))}</td><td>{_html_escape(d.get('name'))}</td><td>{_html_escape(d.get('score'))}</td><td>{_html_escape(', '.join(d.get('risks') or []))}</td></tr>" for d in domains)
    endpoint_rows = "".join(f"<tr><td>{_html_escape(x.get('method'))} {_html_escape(x.get('path'))}</td><td>{_html_escape(', '.join(x.get('matched_domains') or []))}</td><td>{_html_escape(', '.join(x.get('risk_types') or []))}</td></tr>" for x in (profile.get("endpoint_domain_map") or [])[:120])
    risk_rows = "".join(f"<tr><td>{_html_escape(r.get('severity'))}</td><td>{_html_escape(r.get('risk_type'))}</td><td>{_html_escape(r.get('title'))}</td><td>{_html_escape(r.get('matched_operation_count'))}</td><td>{_html_escape(r.get('expected'))}</td></tr>" for r in profile.get("adaptive_risk_matrix") or [])
    leak = profile.get("private_leak_check") or {}
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>Business Adaptation Layer</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfdf5;color:#047857}}</style></head><body>
<section class='hero'><span class='badge'>Phase34</span><h1>企业业务适配层</h1><p>项目：{_html_escape(profile.get('project_name'))} · 识别模式：{_html_escape(profile.get('business_domain_mode'))} · 接口数：{_html_escape(profile.get('operation_count'))}</p><p>私有答案泄露检查：<b>{_html_escape('passed' if leak.get('passed') else 'failed')}</b></p></section>
<section class='panel'><h2>识别出的业务域</h2><table><thead><tr><th>Domain</th><th>名称</th><th>分数</th><th>风险剧本</th></tr></thead><tbody>{rows or '<tr><td colspan="4">暂无</td></tr>'}</tbody></table></section>
<section class='panel'><h2>自适应风险矩阵</h2><table><thead><tr><th>等级</th><th>风险</th><th>标题</th><th>匹配接口数</th><th>期望规则</th></tr></thead><tbody>{risk_rows or '<tr><td colspan="5">暂无</td></tr>'}</tbody></table></section>
<section class='panel'><h2>接口 → 业务风险映射</h2><table><thead><tr><th>接口</th><th>业务域</th><th>风险类型</th></tr></thead><tbody>{endpoint_rows or '<tr><td colspan="3">暂无接口</td></tr>'}</tbody></table></section>
</body></html>"""


def main(argv: list[str] | None = None) -> int:
    argv = argv or []
    import os
    project = os.environ.get("REAL_PROJECT_ID") or (argv[0] if argv else "real_project_demo")
    profile = build_business_adaptation_profile(project)
    print(json.dumps({"ok": True, "project_id": profile.get("project_id"), "selected_domains": profile.get("selected_domains"), "operation_count": profile.get("operation_count")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
