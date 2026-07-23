from __future__ import annotations

"""Phase57: Document-evidence multi-industry business understanding.

This module does not require customers to select or maintain an industry
knowledge package.  It reads PRD/MRD/OpenAPI/interface descriptions and
constructs an explainable business model from document evidence:

    documents + APIs -> semantic evidence -> multi-label industry inference
      -> objects / roles / state machines / dependencies / invariants
      -> risk domains -> Oracles + high-value defect probes

The small semantic signature table below is a *language normalization prior*,
not a customer configuration package: an industry risk is activated only when
independent evidence exists in the supplied documents or interface contract.
When evidence is insufficient, the engine abstains to general-business mode.
"""

import argparse
import hashlib
import json
import math
import re
import enum
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from .real_project_onboarding import (
    ROOT,
    _html_escape,
    _load_json,
    _read_text,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)

PHASE = "phase57_multi_industry_business_reasoning"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# The structures represent reusable semantic concepts.  They are intentionally
# not customer-installed rule packs: every output is gated by evidence from the
# currently uploaded PRD/MRD/OpenAPI/endpoint descriptions.
INDUSTRY_SIGNATURES: dict[str, dict[str, Any]] = {
    "crm": {
        "name": "CRM / 销售客户",
        "objects": {
            "lead": ["lead", "leads", "线索"],
            "customer": ["customer", "account", "客户", "客户档案"],
            "contact": ["contact", "联系人"],
            "opportunity": ["opportunity", "deal", "商机", "销售机会"],
            "quote": ["quote", "quotation", "报价", "报价单"],
            "contract": ["contract", "agreement", "合同", "协议"],
        },
        "roles": {
            "sales": ["sales", "sales_rep", "account_manager", "销售", "客户经理"],
            "sales_manager": ["sales_manager", "sales director", "销售主管", "销售经理"],
            "contract_approver": ["contract_approver", "legal", "法务", "合同审批人"],
        },
        "flows": [
            {"object": "lead", "states": ["new", "contacted", "qualified", "converted", "lost"], "aliases": ["qualified", "converted", "lead status", "线索状态", "转化"]},
            {"object": "opportunity", "states": ["open", "proposal", "negotiation", "won", "lost"], "aliases": ["opportunity stage", "won", "lost", "商机阶段", "赢单", "丢单"]},
            {"object": "contract", "states": ["draft", "approved", "signed", "active", "void", "archived"], "aliases": ["contract status", "signed", "合同状态", "签署", "归档"]},
        ],
        "dependencies": [("lead", "customer", "converted_to"), ("customer", "opportunity", "owns"), ("opportunity", "quote", "priced_by"), ("quote", "contract", "accepted_as")],
        "risk_hints": [
            {"hint_id": "crm_owner_scope", "kind": "permission", "objects": ["lead", "customer", "opportunity"], "risk_category": "ownership_boundary"},
            {"hint_id": "crm_quote_contract_amount", "kind": "conservation", "objects": ["quote", "contract"], "risk_category": "amount_conservation"},
        ],
        "risks": [
            {"risk_type": "industry_ownership_boundary", "severity": "P1", "title": "CRM 负责人边界与客户归属风险", "object": "customer", "rule_id": "crm_owner_scope", "destructive": False},
            {"risk_type": "industry_quote_discount", "severity": "P1", "title": "CRM 报价折扣与合同金额规则风险", "object": "quote", "rule_id": "crm_quote_contract_amount", "destructive": True},
            {"risk_type": "industry_contract_state", "severity": "P1", "title": "CRM 合同状态机与终态保护风险", "object": "contract", "flow_object": "contract", "destructive": True},
        ],
    },
    "erp": {
        "name": "ERP / 供应链与经营资源",
        "objects": {
            "supplier": ["supplier", "vendor", "供应商", "供方"],
            "purchase_order": ["purchase_order", "purchase order", "采购订单", "采购单"],
            "goods_receipt": ["goods_receipt", "receipt", "收货单", "入库单"],
            "inventory": ["inventory", "stock", "warehouse_stock", "库存", "仓存"],
            "warehouse": ["warehouse", "仓库"],
            "invoice": ["invoice", "发票", "应付单"],
            "work_order": ["work_order", "production_order", "工单", "生产订单"],
        },
        "roles": {
            "buyer": ["buyer", "purchaser", "采购员"],
            "warehouse_operator": ["warehouse_operator", "warehouse_staff", "仓管", "库管"],
            "finance_clerk": ["finance", "accountant", "财务", "会计"],
            "approver": ["approver", "审核人", "审批人"],
        },
        "flows": [
            {"object": "purchase_order", "states": ["draft", "approved", "ordered", "received", "closed", "cancelled"], "aliases": ["purchase status", "received", "采购状态", "收货", "采购审批"]},
            {"object": "goods_receipt", "states": ["draft", "received", "inspected", "posted", "reversed"], "aliases": ["receipt status", "入库状态", "质检", "冲销"]},
            {"object": "work_order", "states": ["planned", "released", "in_progress", "completed", "closed", "cancelled"], "aliases": ["work order status", "生产状态", "完工", "工单状态"]},
        ],
        "dependencies": [("supplier", "purchase_order", "fulfills"), ("purchase_order", "goods_receipt", "received_as"), ("goods_receipt", "inventory", "changes"), ("purchase_order", "invoice", "three_way_match")],
        "risk_hints": [
            {"hint_id": "erp_inventory_conservation", "kind": "conservation", "objects": ["goods_receipt", "inventory", "warehouse"], "risk_category": "quantity_conservation"},
            {"hint_id": "erp_three_way_match", "kind": "reconciliation", "objects": ["purchase_order", "goods_receipt", "invoice"], "risk_category": "cross_entity_consistency"},
            {"hint_id": "erp_approval_matrix", "kind": "permission", "objects": ["purchase_order", "invoice"], "risk_category": "approval_boundary"},
        ],
        "risks": [
            {"risk_type": "industry_inventory_conservation", "severity": "P0", "title": "ERP 库存账实与库存变更守恒风险", "object": "inventory", "rule_id": "erp_inventory_conservation", "destructive": True},
            {"risk_type": "industry_three_way_match", "severity": "P1", "title": "ERP 采购-收货-发票三单匹配风险", "object": "invoice", "rule_id": "erp_three_way_match", "destructive": False},
            {"risk_type": "industry_approval_matrix", "severity": "P0", "title": "ERP 审批矩阵绕过风险", "object": "purchase_order", "rule_id": "erp_approval_matrix", "destructive": True},
        ],
    },
    "finance": {
        "name": "金融 / 资金账户",
        "objects": {
            "account": ["account", "wallet", "账户", "钱包"],
            "balance": ["balance", "available_balance", "余额", "可用余额"],
            "ledger": ["ledger", "journal", "账本", "流水", "分录"],
            "transaction": ["transaction", "transfer", "转账", "交易"],
            "settlement": ["settlement", "clearing", "结算", "清算"],
            "loan": ["loan", "credit", "授信", "贷款"],
            "repayment": ["repayment", "repay", "还款"],
        },
        "roles": {
            "account_holder": ["account_holder", "customer", "持有人", "客户"],
            "risk_officer": ["risk_officer", "risk", "风控", "风控员"],
            "finance_operator": ["finance_operator", "cashier", "资金员", "出纳"],
            "auditor": ["auditor", "audit", "审计"],
        },
        "flows": [
            {"object": "transaction", "states": ["initiated", "pending", "authorized", "settled", "reversed", "failed"], "aliases": ["transaction status", "settled", "reversed", "交易状态", "冲正", "清算"]},
            {"object": "loan", "states": ["applied", "approved", "disbursed", "repaying", "repaid", "defaulted", "cancelled"], "aliases": ["loan status", "disbursed", "还款状态", "放款", "逾期"]},
        ],
        "dependencies": [("account", "transaction", "initiates"), ("transaction", "ledger", "posts"), ("ledger", "balance", "reconciles"), ("loan", "repayment", "settled_by")],
        "risk_hints": [
            {"hint_id": "finance_double_entry", "kind": "conservation", "objects": ["transaction", "ledger", "balance"], "risk_category": "amount_conservation"},
            {"hint_id": "finance_account_boundary", "kind": "permission", "objects": ["account", "transaction"], "risk_category": "ownership_boundary"},
            {"hint_id": "finance_limit_policy", "kind": "constraint", "objects": ["transaction", "loan"], "risk_category": "limit_enforcement"},
        ],
        "risks": [
            {"risk_type": "industry_financial_conservation", "severity": "P0", "title": "金融余额、账本与交易金额守恒风险", "object": "ledger", "rule_id": "finance_double_entry", "destructive": True},
            {"risk_type": "industry_account_ownership", "severity": "P0", "title": "金融账户归属与资金越权风险", "object": "account", "rule_id": "finance_account_boundary", "destructive": False},
            {"risk_type": "industry_limit_enforcement", "severity": "P1", "title": "金融额度与风控门禁绕过风险", "object": "transaction", "rule_id": "finance_limit_policy", "destructive": True},
        ],
    },
    "healthcare": {
        "name": "医疗 / 患者诊疗",
        "objects": {
            "patient": ["patient", "患者", "病人"],
            "doctor": ["doctor", "physician", "医生", "医师"],
            "appointment": ["appointment", "visit", "挂号", "预约", "门诊"],
            "medical_record": ["medical_record", "medical record", "emr", "病历", "诊疗记录"],
            "diagnosis": ["diagnosis", "diagnostic", "诊断"],
            "prescription": ["prescription", "medication_order", "处方", "医嘱"],
        },
        "roles": {
            "doctor": ["doctor", "physician", "医生", "医师"],
            "nurse": ["nurse", "护士"],
            "patient": ["patient", "患者"],
            "clinic_admin": ["clinic_admin", "medical_admin", "门诊管理员", "医疗管理员"],
        },
        "flows": [
            {"object": "appointment", "states": ["scheduled", "checked_in", "consulted", "completed", "cancelled", "no_show"], "aliases": ["appointment status", "checked_in", "预约状态", "签到", "就诊完成"]},
            {"object": "prescription", "states": ["draft", "signed", "dispensed", "cancelled", "expired"], "aliases": ["prescription status", "dispensed", "处方状态", "发药", "签署"]},
        ],
        "dependencies": [("patient", "appointment", "books"), ("appointment", "medical_record", "creates"), ("doctor", "diagnosis", "records"), ("diagnosis", "prescription", "supports")],
        "risk_hints": [
            {"hint_id": "healthcare_sensitive_access", "kind": "permission", "objects": ["patient", "medical_record", "diagnosis"], "risk_category": "sensitive_data_boundary"},
            {"hint_id": "healthcare_appointment_capacity", "kind": "constraint", "objects": ["appointment", "doctor"], "risk_category": "temporal_capacity"},
            {"hint_id": "healthcare_prescription_authorization", "kind": "permission", "objects": ["doctor", "patient", "prescription"], "risk_category": "authorization_boundary"},
        ],
        "risks": [
            {"risk_type": "industry_sensitive_data_access", "severity": "P0", "title": "医疗敏感病历与患者隐私访问风险", "object": "medical_record", "rule_id": "healthcare_sensitive_access", "destructive": False},
            {"risk_type": "industry_appointment_capacity", "severity": "P1", "title": "医疗预约排班冲突与重复占用风险", "object": "appointment", "rule_id": "healthcare_appointment_capacity", "destructive": True},
            {"risk_type": "industry_prescription_authorization", "severity": "P0", "title": "医疗处方授权与状态规则风险", "object": "prescription", "rule_id": "healthcare_prescription_authorization", "destructive": True},
        ],
    },
    "education": {
        "name": "教育 / 课程与考试",
        "objects": {
            "student": ["student", "learner", "学生", "学员"],
            "teacher": ["teacher", "instructor", "老师", "教师"],
            "course": ["course", "课程"],
            "class": ["class", "cohort", "班级", "教学班"],
            "enrollment": ["enrollment", "enroll", "registration", "选课", "报名"],
            "exam": ["exam", "assessment", "考试", "测验"],
            "score": ["score", "grade", "成绩", "分数", "评分"],
        },
        "roles": {
            "student": ["student", "learner", "学生", "学员"],
            "teacher": ["teacher", "instructor", "老师", "教师"],
            "registrar": ["registrar", "教务", "教务员"],
            "exam_admin": ["exam_admin", "考试管理员"],
        },
        "flows": [
            {"object": "enrollment", "states": ["applied", "enrolled", "waitlisted", "withdrawn", "completed"], "aliases": ["enrollment status", "waitlisted", "选课状态", "候补", "退课"]},
            {"object": "exam", "states": ["draft", "scheduled", "open", "submitted", "graded", "published"], "aliases": ["exam status", "graded", "考试状态", "阅卷", "成绩发布"]},
        ],
        "dependencies": [("student", "enrollment", "owns"), ("course", "class", "delivered_as"), ("class", "enrollment", "contains"), ("exam", "score", "produces")],
        "risk_hints": [
            {"hint_id": "education_grade_integrity", "kind": "permission", "objects": ["teacher", "student", "score"], "risk_category": "write_authorization"},
            {"hint_id": "education_enrollment_capacity", "kind": "constraint", "objects": ["course", "class", "enrollment"], "risk_category": "capacity_conservation"},
        ],
        "risks": [
            {"risk_type": "industry_grade_integrity", "severity": "P0", "title": "教育成绩归属、写入权限与审计风险", "object": "score", "rule_id": "education_grade_integrity", "destructive": True},
            {"risk_type": "industry_enrollment_capacity", "severity": "P1", "title": "教育选课容量、候补与重复报名风险", "object": "enrollment", "rule_id": "education_enrollment_capacity", "destructive": True},
            {"risk_type": "industry_exam_state", "severity": "P1", "title": "教育考试状态机与成绩发布时序风险", "object": "exam", "flow_object": "exam", "destructive": True},
        ],
    },
    "saas_multitenant": {
        "name": "SaaS 多租户 / 组织协作",
        "objects": {
            "tenant": ["tenant", "organization", "org", "workspace", "租户", "组织", "工作区"],
            "membership": ["membership", "member", "成员", "成员关系"],
            "role": ["role", "rbac", "permission", "角色", "权限"],
            "subscription": ["subscription", "plan", "billing_plan", "订阅", "套餐", "计费计划"],
            "entitlement": ["entitlement", "feature_flag", "quota", "权益", "配额", "功能权限"],
            "audit_log": ["audit_log", "audit", "审计日志", "操作日志"],
        },
        "roles": {
            "tenant_admin": ["tenant_admin", "org_admin", "workspace_admin", "租户管理员", "组织管理员"],
            "member": ["member", "user", "成员", "普通用户"],
            "billing_admin": ["billing_admin", "账单管理员", "计费管理员"],
            "platform_admin": ["platform_admin", "super_admin", "平台管理员", "超级管理员"],
        },
        "flows": [
            {"object": "membership", "states": ["invited", "active", "suspended", "removed"], "aliases": ["membership status", "invited", "成员状态", "邀请", "移除成员"]},
            {"object": "subscription", "states": ["trial", "active", "past_due", "cancelled", "expired"], "aliases": ["subscription status", "past_due", "订阅状态", "试用", "到期"]},
        ],
        "dependencies": [("tenant", "membership", "contains"), ("membership", "role", "granted"), ("tenant", "subscription", "billed_by"), ("subscription", "entitlement", "enables")],
        "risk_hints": [
            {"hint_id": "saas_tenant_isolation", "kind": "permission", "objects": ["tenant", "membership"], "risk_category": "tenant_boundary"},
            {"hint_id": "saas_entitlement_enforcement", "kind": "constraint", "objects": ["subscription", "entitlement"], "risk_category": "entitlement_enforcement"},
            {"hint_id": "saas_role_separation", "kind": "permission", "objects": ["role", "audit_log"], "risk_category": "role_separation"},
        ],
        "risks": [
            {"risk_type": "industry_tenant_isolation", "severity": "P0", "title": "SaaS 多租户数据与关联边界泄漏风险", "object": "tenant", "rule_id": "saas_tenant_isolation", "destructive": False},
            {"risk_type": "industry_entitlement_enforcement", "severity": "P1", "title": "SaaS 套餐、配额与功能权益绕过风险", "object": "entitlement", "rule_id": "saas_entitlement_enforcement", "destructive": True},
            {"risk_type": "industry_subscription_state", "severity": "P1", "title": "SaaS 订阅状态机与到期生效风险", "object": "subscription", "flow_object": "subscription", "destructive": True},
        ],
    },
    "ecommerce": {
        "name": "电商 / 交易履约",
        "objects": {
            "product": ["product", "商品", "产品"],
            "sku": ["sku", "货品", "库存单元"],
            "cart": ["cart", "购物车"],
            "order": ["order", "订单"],
            "payment": ["payment", "pay", "支付"],
            "refund": ["refund", "退款", "退货款"],
            "inventory": ["inventory", "stock", "库存"],
            "fulfillment": ["fulfillment", "shipment", "delivery", "履约", "发货", "配送"],
            "coupon": ["coupon", "voucher", "优惠券", "券码"],
        },
        "roles": {
            "buyer": ["buyer", "customer", "消费者", "买家"],
            "merchant": ["merchant", "seller", "商家", "卖家"],
            "warehouse_operator": ["warehouse_operator", "仓管", "仓库人员"],
            "customer_service": ["customer_service", "客服"],
        },
        "flows": [
            {"object": "order", "states": ["created", "pending_payment", "paid", "fulfilled", "completed", "cancelled", "refunded"], "aliases": ["order status", "paid", "fulfilled", "订单状态", "已支付", "发货", "退款"]},
            {"object": "refund", "states": ["requested", "approved", "processing", "refunded", "rejected", "cancelled"], "aliases": ["refund status", "refunded", "退款状态", "退款申请"]},
        ],
        "dependencies": [("cart", "order", "checked_out_as"), ("order", "payment", "paid_by"), ("order", "inventory", "reserves"), ("order", "fulfillment", "fulfilled_by"), ("payment", "refund", "reversed_by")],
        "risk_hints": [
            {"hint_id": "ecom_order_payment_amount", "kind": "conservation", "objects": ["order", "payment", "refund"], "risk_category": "amount_conservation"},
            {"hint_id": "ecom_inventory_reservation", "kind": "conservation", "objects": ["order", "inventory", "sku"], "risk_category": "quantity_conservation"},
            {"hint_id": "ecom_coupon_ownership", "kind": "permission", "objects": ["coupon", "order"], "risk_category": "ownership_boundary"},
        ],
        "risks": [
            {"risk_type": "industry_payment_idempotency", "severity": "P0", "title": "电商支付重复记账与订单金额不一致风险", "object": "payment", "rule_id": "ecom_order_payment_amount", "destructive": True},
            {"risk_type": "industry_inventory_conservation", "severity": "P0", "title": "电商库存预留、扣减与释放守恒风险", "object": "inventory", "rule_id": "ecom_inventory_reservation", "destructive": True},
            {"risk_type": "industry_coupon_policy", "severity": "P1", "title": "电商优惠券归属、有效期和重复使用风险", "object": "coupon", "rule_id": "ecom_coupon_ownership", "destructive": True},
            {"risk_type": "industry_order_state", "severity": "P1", "title": "电商订单履约状态机与终态保护风险", "object": "order", "flow_object": "order", "destructive": True},
        ],
    },
}

# Cross-industry concepts always derive from the document evidence.  They do
# not imply a specific vertical and are useful as a low-confidence fallback.
GENERIC_OBJECT_ALIASES = {
    "user": ["user", "用户"],
    "organization": ["organization", "org", "组织"],
    "approval": ["approval", "approve", "审批", "审核"],
    "amount": ["amount", "price", "total", "金额", "价格", "总额"],
    "status": ["status", "state", "状态", "阶段"],
    "owner": ["owner", "assignee", "负责人", "归属人"],
}

PRIVATE_MARKERS = {"private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "current_bug_set", "bug_instance_id"}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


def _short_hash(value: Any, size: int = 16) -> str:
    return hashlib.sha256(_json(value).encode("utf-8", errors="replace")).hexdigest()[:size]


def _norm(value: Any) -> str:
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", str(value or "").strip().lower())


def _contains(text: str, phrase: str) -> bool:
    phrase = str(phrase or "").strip().lower()
    if not phrase:
        return False
    return phrase in text.lower()


def _safe_list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _path_template(path: Any) -> str:
    value = str(path or "/").strip() or "/"
    value = re.sub(r"https?://[^/]+", "", value).split("?", 1)[0]
    value = re.sub(r"/\d+(?=/|$)", "/{id}", value)
    return value if value.startswith("/") else "/" + value


def _flatten(value: Any, prefix: str = "", depth: int = 0) -> Iterable[tuple[str, Any]]:
    if depth > 8:
        return
    if isinstance(value, dict):
        for key, item in value.items():
            child = f"{prefix}.{key}" if prefix else str(key)
            yield child, item
            yield from _flatten(item, child, depth + 1)
    elif isinstance(value, list):
        for index, item in enumerate(value[:50]):
            child = f"{prefix}[{index}]"
            yield child, item
            yield from _flatten(item, child, depth + 1)


def _openapi_operations(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            method_u = str(method).upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"}:
                continue
            spec = spec if isinstance(spec, dict) else {}
            raw_parts = [
                str(path), method_u, str(spec.get("operationId") or ""), str(spec.get("summary") or ""),
                str(spec.get("description") or ""), _json(spec.get("tags") or []), _json(spec.get("parameters") or []),
                _json(spec.get("requestBody") or {}), _json(spec.get("responses") or {}), _json(spec.get("security") or []),
            ]
            rows.append({
                "method": method_u,
                "path": _path_template(path),
                "operation_id": spec.get("operationId") or "",
                "summary": spec.get("summary") or "",
                "description": spec.get("description") or "",
                "tags": [str(x) for x in _safe_list(spec.get("tags"))],
                "text": " ".join(raw_parts).lower(),
                "security": spec.get("security") or [],
            })
    return rows


def _schema_inventory(openapi: dict[str, Any]) -> list[dict[str, Any]]:
    schemas = ((openapi.get("components") or {}).get("schemas") or {}) if isinstance(openapi, dict) else {}
    records: list[dict[str, Any]] = []
    for name, schema in schemas.items():
        schema = schema if isinstance(schema, dict) else {}
        properties = schema.get("properties") if isinstance(schema.get("properties"), dict) else {}
        enums: list[dict[str, Any]] = []
        for key, item in _flatten(schema):
            if isinstance(item, dict) and isinstance(item.get("enum"), list):
                values = [str(v) for v in item.get("enum") if isinstance(v, (str, int, float))]
                if values:
                    enums.append({"path": key, "values": values[:30]})
        records.append({
            "name": str(name),
            "properties": sorted(str(key) for key in properties.keys()),
            "enums": enums,
            "text": f"{name} {_json(schema)}".lower(),
        })
    return records


def _load_project_documents(project: str, root: Path) -> tuple[dict[str, str], dict[str, Any], dict[str, Any]]:
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    docs: dict[str, str] = {}
    for name in ["prd.md", "mrd.md", "requirements.md", "business_rules.md", "interface.md", "api.md", "openapi_raw.txt"]:
        content = _read_text(paths["input_dir"] / name)
        if content:
            docs[name] = content
    openapi = _load_json(paths["workspace_dir"] / "normalized_openapi.json", {})
    if not isinstance(openapi, dict) or not openapi:
        openapi = _load_json(paths["input_dir"] / "openapi.json", {})
    if not isinstance(openapi, dict):
        openapi = {}
    return docs, openapi, cfg if isinstance(cfg, dict) else {}


def _evidence_for_aliases(aliases: Iterable[str], documents: dict[str, str], operations: list[dict[str, Any]], schemas: list[dict[str, Any]], limit: int = 8) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for alias in aliases:
        alias_s = str(alias).strip()
        if not alias_s:
            continue
        alias_l = alias_s.lower()
        for name, text in documents.items():
            if alias_l in text.lower():
                key = ("document", f"{name}:{alias_s}")
                if key not in seen:
                    seen.add(key)
                    result.append({"source": "document", "reference": name, "matched_term": alias_s})
        for op in operations:
            if alias_l in str(op.get("text") or ""):
                label = f"{op.get('method')} {op.get('path')}"
                key = ("operation", f"{label}:{alias_s}")
                if key not in seen:
                    seen.add(key)
                    result.append({"source": "operation", "reference": label, "matched_term": alias_s})
        for schema in schemas:
            if alias_l in str(schema.get("text") or ""):
                label = str(schema.get("name"))
                key = ("schema", f"{label}:{alias_s}")
                if key not in seen:
                    seen.add(key)
                    result.append({"source": "schema", "reference": label, "matched_term": alias_s})
        if len(result) >= limit:
            break
    return result[:limit]


def _signature_score(signature: dict[str, Any], documents: dict[str, str], operations: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> dict[str, Any]:
    object_hits: dict[str, list[dict[str, str]]] = {}
    for obj, aliases in (signature.get("objects") or {}).items():
        evidence = _evidence_for_aliases(aliases, documents, operations, schemas, limit=5)
        if evidence:
            object_hits[str(obj)] = evidence
    role_hits: dict[str, list[dict[str, str]]] = {}
    for role, aliases in (signature.get("roles") or {}).items():
        evidence = _evidence_for_aliases(aliases, documents, operations, schemas, limit=4)
        if evidence:
            role_hits[str(role)] = evidence
    flow_hits: dict[str, list[dict[str, str]]] = {}
    for flow in signature.get("flows") or []:
        evidence = _evidence_for_aliases(flow.get("aliases") or [], documents, operations, schemas, limit=4)
        if evidence:
            flow_hits[str(flow.get("object"))] = evidence

    route_hits = 0
    constraint_hits = 0
    combined = "\n".join(documents.values()).lower()
    for op in operations:
        path_text = f"{op.get('method')} {op.get('path')} {op.get('operation_id')}".lower()
        if any(_contains(path_text, alias) for aliases in (signature.get("objects") or {}).values() for alias in aliases):
            route_hits += 1
    for hint in signature.get("risk_hints") or []:
        terms: list[str] = []
        for obj in hint.get("objects") or []:
            terms.extend((signature.get("objects") or {}).get(obj, []))
        if sum(1 for term in terms if _contains(combined, term)) >= 2:
            constraint_hits += 1

    score = len(object_hits) * 1.65 + len(role_hits) * 0.85 + len(flow_hits) * 1.15 + min(route_hits, 6) * 0.55 + constraint_hits * 0.9
    confidence = round(min(0.98, 0.14 + 0.84 * (1.0 - math.exp(-score / 6.6))), 3)
    evidence = []
    for records in [*object_hits.values(), *role_hits.values(), *flow_hits.values()]:
        evidence.extend(records)
    return {
        "score": round(score, 3),
        "confidence": confidence,
        "object_hits": sorted(object_hits),
        "role_hits": sorted(role_hits),
        "flow_hits": sorted(flow_hits),
        "route_hit_count": route_hits,
        "constraint_hit_count": constraint_hits,
        "evidence": _dedupe_evidence(evidence, 14),
    }


def _dedupe_evidence(rows: Iterable[dict[str, Any]], limit: int = 20) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        marker = _json({"source": row.get("source"), "reference": row.get("reference"), "matched_term": row.get("matched_term")})
        if marker in seen:
            continue
        seen.add(marker)
        result.append(dict(row))
        if len(result) >= limit:
            break
    return result


# Minimum evidence score / confidence before an industry signature may activate
# vertical oracles, risks, or DSL rule packs. Below this, fall back to
# general-business inference only — never invent ecommerce/finance defaults.
_MIN_INDUSTRY_SCORE = 2.8
_MIN_INDUSTRY_CONFIDENCE = 0.58
_MIN_SECONDARY_INDUSTRY_SCORE = 3.8
_MIN_SECONDARY_INDUSTRY_CONFIDENCE = 0.62


def _select_industries(scores: dict[str, dict[str, Any]]) -> tuple[list[str], str]:
    ranked = sorted(scores.items(), key=lambda item: (-float(item[1].get("score") or 0), item[0]))
    if not ranked:
        return [], "unknown_general_business"
    top_industry, top_data = ranked[0]
    top = float(top_data.get("score") or 0)
    top_confidence = float(top_data.get("confidence") or 0)
    # Fail closed: weak keyword overlap must not activate a vertical oracle pack.
    if top < _MIN_INDUSTRY_SCORE or top_confidence < _MIN_INDUSTRY_CONFIDENCE:
        return [], "unknown_general_business"
    # Require at least one object hit so a lone role/flow keyword cannot select an industry.
    if len(top_data.get("object_hits") or []) < 1:
        return [], "unknown_general_business"
    selected = [top_industry]
    for industry, data in ranked[1:]:
        object_count = len(data.get("object_hits") or [])
        flow_count = len(data.get("flow_hits") or [])
        independent_evidence = object_count >= 3 or (object_count >= 2 and flow_count >= 1)
        score = float(data.get("score") or 0)
        confidence = float(data.get("confidence") or 0)
        if (
            independent_evidence
            and score >= max(_MIN_SECONDARY_INDUSTRY_SCORE, top * 0.55)
            and confidence >= _MIN_SECONDARY_INDUSTRY_CONFIDENCE
        ):
            selected.append(industry)
        if len(selected) >= 3:
            break
    return selected, "multi_industry" if len(selected) > 1 else "single_industry"


def _infer_objects(selected: list[str], documents: dict[str, str], operations: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for industry in selected:
        signature = INDUSTRY_SIGNATURES[industry]
        for obj, aliases in (signature.get("objects") or {}).items():
            evidence = _evidence_for_aliases(aliases, documents, operations, schemas, limit=8)
            if not evidence:
                continue
            record = merged.setdefault(str(obj), {
                "object": str(obj),
                "industries": [],
                "aliases": [],
                "evidence": [],
                "confidence": 0.0,
                "kind": "business_object",
            })
            record["industries"].append(industry)
            record["aliases"] = list(dict.fromkeys([*record["aliases"], *[str(x) for x in aliases]]))[:20]
            record["evidence"] = _dedupe_evidence([*record["evidence"], *evidence], 10)
            record["confidence"] = round(min(0.97, 0.45 + 0.08 * len(record["evidence"]) + 0.06 * len(record["industries"])), 3)
    # A controlled generic fallback avoids invented vertical labels.
    if not merged:
        for obj, aliases in GENERIC_OBJECT_ALIASES.items():
            evidence = _evidence_for_aliases(aliases, documents, operations, schemas, limit=5)
            if evidence:
                merged[obj] = {"object": obj, "industries": ["general_business"], "aliases": aliases, "evidence": evidence, "confidence": 0.42, "kind": "generic_business_object"}
    return sorted(merged.values(), key=lambda row: (-float(row.get("confidence") or 0), str(row.get("object"))))


def _infer_roles(selected: list[str], documents: dict[str, str], operations: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[str, dict[str, Any]] = {}
    for industry in selected:
        signature = INDUSTRY_SIGNATURES[industry]
        for role, aliases in (signature.get("roles") or {}).items():
            evidence = _evidence_for_aliases(aliases, documents, operations, schemas, limit=6)
            if not evidence:
                continue
            row = merged.setdefault(str(role), {"role": str(role), "industries": [], "evidence": [], "confidence": 0.0})
            row["industries"].append(industry)
            row["evidence"] = _dedupe_evidence([*row["evidence"], *evidence], 8)
            row["confidence"] = round(min(0.96, 0.44 + 0.1 * len(row["evidence"])), 3)
    return sorted(merged.values(), key=lambda row: (-float(row.get("confidence") or 0), str(row.get("role"))))


def _enum_states(schemas: list[dict[str, Any]]) -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for schema in schemas:
        schema_name = str(schema.get("name") or "schema")
        values: list[str] = []
        for enum in schema.get("enums") or []:
            enum_path = str(enum.get("path") or "").lower()
            if any(marker in enum_path for marker in ["status", "state", "stage", "lifecycle", "phase", "状态", "阶段"]):
                values.extend(str(v) for v in enum.get("values") or [])
        if values:
            found[schema_name] = list(dict.fromkeys(values))[:16]
    return found


def _infer_state_machines(selected: list[str], objects: list[dict[str, Any]], documents: dict[str, str], operations: list[dict[str, Any]], schemas: list[dict[str, Any]]) -> list[dict[str, Any]]:
    object_names = {str(row.get("object")) for row in objects}
    enum_states = _enum_states(schemas)
    rows: list[dict[str, Any]] = []
    for industry in selected:
        signature = INDUSTRY_SIGNATURES[industry]
        for flow in signature.get("flows") or []:
            obj = str(flow.get("object") or "")
            evidence = _evidence_for_aliases(flow.get("aliases") or [], documents, operations, schemas, limit=7)
            if obj not in object_names and not evidence:
                continue
            actual_enums: list[str] = []
            aliases = (signature.get("objects") or {}).get(obj, [])
            for schema_name, values in enum_states.items():
                if any(_contains(schema_name, alias) for alias in aliases):
                    actual_enums.extend(values)
            states = list(dict.fromkeys(actual_enums)) if len(set(actual_enums)) >= 2 else [str(s) for s in flow.get("states") or []]
            if len(states) < 2:
                continue
            rows.append({
                "state_machine_id": f"ISM_{_short_hash([industry, obj, states])}",
                "industry": industry,
                "object": obj,
                "states": states,
                "terminal_states": [state for state in states if str(state).lower() in {"cancelled", "canceled", "closed", "completed", "archived", "void", "repaid", "expired", "removed", "lost", "refunded", "rejected"}],
                "inference_mode": "schema_enum" if actual_enums else "document_evidence_flow",
                "confidence": round(min(0.95, 0.5 + 0.06 * len(evidence) + (0.12 if actual_enums else 0.0)), 3),
                "evidence": _dedupe_evidence(evidence, 8),
                "expected": f"{obj} 的状态只能按已识别业务流程推进；终态不得被非法重入。",
            })
    return sorted(rows, key=lambda row: (str(row.get("industry")), str(row.get("object"))))


def _infer_dependencies(selected: list[str], objects: list[dict[str, Any]]) -> list[dict[str, Any]]:
    object_names = {str(row.get("object")) for row in objects}
    rows: list[dict[str, Any]] = []
    for industry in selected:
        for source, target, relation in INDUSTRY_SIGNATURES[industry].get("dependencies") or []:
            if source in object_names and target in object_names:
                rows.append({"industry": industry, "source_object": source, "target_object": target, "relationship": relation, "confidence": 0.76})
    return rows


def _infer_rules_and_boundaries(selected: list[str], objects: list[dict[str, Any]], roles: list[dict[str, Any]], operations: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Infer rule candidates and boundaries from evidence-gated risk hints.

    Risk hints provide only the TYPE of risk to investigate; actual business
    formulas and assertions must be derived from project evidence (documents,
    schema, runtime observations).  No industry-specific formulas are embedded.
    """
    object_names = {str(row.get("object")) for row in objects}
    role_names = {str(row.get("role")) for row in roles}
    rules: list[dict[str, Any]] = []
    boundaries: list[dict[str, Any]] = []
    oracles: list[dict[str, Any]] = []
    # Generic oracle family derived from rule kind (industry-neutral)
    _KIND_TO_ORACLE = {
        "conservation": "conservation_oracle",
        "permission": "ownership_access_oracle",
        "constraint": "field_invariant_oracle",
        "reconciliation": "cross_entity_consistency_oracle",
    }
    for industry in selected:
        signature = INDUSTRY_SIGNATURES[industry]
        for hint in signature.get("risk_hints") or []:
            required_objects = set(str(x) for x in hint.get("objects") or [])
            matched = sorted(required_objects & object_names)
            if not matched:
                continue
            confidence = round(min(0.96, 0.55 + 0.08 * len(matched) + (0.06 if role_names else 0.0)), 3)
            kind = str(hint.get("kind") or "constraint")
            oracle_family = _KIND_TO_ORACLE.get(kind, "business_rule_oracle")
            hint_id = str(hint.get("hint_id") or "")
            risk_category = str(hint.get("risk_category") or "general")
            item = {
                "rule_id": hint_id,
                "industry": industry,
                "kind": kind,
                "objects": matched,
                "risk_category": risk_category,
                "oracle_family": oracle_family,
                "confidence": confidence,
                "evidence_status": "requires_project_evidence",
                "derivation": "risk_hint_candidate",
            }
            rules.append(item)
            oracles.append({
                "oracle_id": f"IOR_{_short_hash([industry, hint_id, oracle_family])}",
                "industry": industry,
                "oracle_family": oracle_family,
                "rule_id": hint_id,
                "objects": matched,
                "risk_category": risk_category,
                "evidence_type": "risk_hint_requires_document_confirmation",
                "execution_safety": "read_only_or_sandbox_required",
            })
            if kind == "permission":
                boundary_type = "tenant_boundary" if risk_category == "tenant_boundary" else "role_and_ownership_boundary"
                boundaries.append({
                    "boundary_id": f"IPB_{_short_hash([industry, hint_id])}",
                    "industry": industry,
                    "boundary_type": boundary_type,
                    "protected_objects": matched,
                    "recognized_roles": sorted(role_names),
                    "risk_category": risk_category,
                    "oracle_family": oracle_family,
                    "confidence": confidence,
                })
    # Any resource-ID detail route is an explicit ownership/access boundary.
    for op in operations:
        if "{" not in str(op.get("path") or "") or str(op.get("method") or "") not in {"GET", "PUT", "PATCH", "DELETE"}:
            continue
        matches = [obj for obj in object_names if any(_contains(str(op.get("text") or ""), alias) for industry in selected for alias in INDUSTRY_SIGNATURES[industry].get("objects", {}).get(obj, []))]
        if matches:
            boundaries.append({
                "boundary_id": f"IPB_{_short_hash([op.get('method'), op.get('path'), matches])}",
                "industry": "cross_industry",
                "boundary_type": "resource_ownership_detail_access",
                "protected_objects": sorted(set(matches)),
                "recognized_roles": sorted(role_names),
                "expected": "资源详情、更新和删除必须验证主体归属、角色和组织边界。",
                "oracle_family": "ownership_access_oracle",
                "confidence": 0.72,
                "operation": f"{op.get('method')} {op.get('path')}",
            })
    return rules, _dedupe_dicts(boundaries, "boundary_id"), _dedupe_dicts(oracles, "oracle_id")


def _dedupe_dicts(rows: list[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        marker = str(row.get(key) or _short_hash(row))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(row)
    return result


def _matching_operations(object_name: str, industries: list[str], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    aliases: list[str] = []
    for industry in industries:
        aliases.extend(INDUSTRY_SIGNATURES.get(industry, {}).get("objects", {}).get(object_name, []))
    candidates = [op for op in operations if any(_contains(str(op.get("text") or ""), alias) for alias in aliases)]
    if not candidates:
        candidates = [op for op in operations if object_name.replace("_", "") in _norm(op.get("path"))]
    return sorted(candidates, key=lambda op: (0 if op.get("method") in SAFE_METHODS else 1, str(op.get("path")), str(op.get("method"))))


def _risk_recipe(risk_type: str, rule: dict[str, Any] | None, flow: dict[str, Any] | None) -> tuple[str, str, str]:
    family = str((rule or {}).get("oracle_family") or "state_machine_oracle")
    expected = str((rule or {}).get("expected") or (flow or {}).get("expected") or "业务规则必须被后端强制执行。")
    recipes = {
        "industry_financial_conservation": "读取交易、账本和余额视图，验证金额差额与状态闭合；写入/重放仅在隔离沙箱执行。",
        "industry_inventory_conservation": "对比库存余额、预留、出入库与订单/收货事件，验证同一物料仓库维度的守恒。",
        "industry_tenant_isolation": "使用不同租户身份替换资源标识、筛选条件和关联参数，验证读取与引用均被拒绝。",
        "industry_sensitive_data_access": "以无治疗关系或低权限身份读取病历/诊断/处方，验证服务端拒绝并保留审计证据。",
        "industry_grade_integrity": "使用学生或非授课教师身份尝试读取/修改成绩，验证归属、角色和成绩发布状态。",
        "industry_payment_idempotency": "对同一支付意图检查幂等键、订单金额、支付流水和退款累计的一致性。",
        "industry_approval_matrix": "构造角色/金额/组织边界组合，验证审批节点、阈值和状态凭据不可绕过。",
    }
    recipe = recipes.get(risk_type, "围绕已识别对象、角色、状态与规则执行只读对账；任何写入、重放或并发验证仅生成隔离沙箱计划。")
    return family, expected, recipe


def _risk_domains(selected: list[str], objects: list[dict[str, Any]], rules: list[dict[str, Any]], state_machines: list[dict[str, Any]], operations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    object_names = {str(row.get("object")) for row in objects}
    rules_by_id = {str(row.get("rule_id")): row for row in rules}
    flows_by_object: dict[str, dict[str, Any]] = {str(row.get("object")): row for row in state_machines}
    rows: list[dict[str, Any]] = []
    for industry in selected:
        signature = INDUSTRY_SIGNATURES[industry]
        for risk in signature.get("risks") or []:
            obj = str(risk.get("object") or "")
            rule = rules_by_id.get(str(risk.get("rule_id") or ""))
            flow = flows_by_object.get(str(risk.get("flow_object") or obj))
            if obj not in object_names and not rule and not flow:
                continue
            family, expected, recipe = _risk_recipe(str(risk.get("risk_type")), rule, flow)
            matched = _matching_operations(obj, [industry], operations)
            rows.append({
                "risk_id": f"IRD_{_short_hash([industry, risk.get('risk_type'), obj])}",
                "industry": industry,
                "risk_type": str(risk.get("risk_type")),
                "severity": str(risk.get("severity") or "P1"),
                "title": str(risk.get("title") or "行业业务风险"),
                "business_object": obj,
                "rule_id": rule.get("rule_id") if rule else None,
                "oracle_family": family,
                "expected": expected,
                "bug_signal": "异常身份、状态、金额、数量或跨对象关系仍被服务端接受，或读取结果违反已识别业务规则。",
                "probe_recipe": recipe,
                "destructive": bool(risk.get("destructive")),
                "matched_operations": [f"{op.get('method')} {op.get('path')}" for op in matched[:6]],
                "confidence": round(min(0.96, 0.58 + 0.07 * len(matched) + (0.08 if rule else 0) + (0.05 if flow else 0)), 3),
            })
    # Generic state-transition risk for a discovered enum when no per-industry
    # specialized risk was emitted for that object.
    risk_object_pairs = {(str(row.get("industry")), str(row.get("business_object"))) for row in rows}
    for flow in state_machines:
        pair = (str(flow.get("industry")), str(flow.get("object")))
        if pair in risk_object_pairs:
            continue
        rows.append({
            "risk_id": f"IRD_{_short_hash([flow.get('industry'), 'state', flow.get('object')])}",
            "industry": flow.get("industry"),
            "risk_type": "industry_state_transition",
            "severity": "P1",
            "title": f"{flow.get('object')} 状态机非法流转风险",
            "business_object": flow.get("object"),
            "rule_id": None,
            "oracle_family": "state_machine_oracle",
            "expected": flow.get("expected"),
            "bug_signal": "状态跳步、终态重入或缺少状态凭证的请求仍成功。",
            "probe_recipe": "读取状态枚举、历史事件和详情视图进行一致性验证；状态推进写入仅在隔离沙箱验证。",
            "destructive": True,
            "matched_operations": [f"{op.get('method')} {op.get('path')}" for op in _matching_operations(str(flow.get("object")), [str(flow.get("industry"))], operations)[:6]],
            "confidence": float(flow.get("confidence") or 0.65),
        })
    order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    return sorted(rows, key=lambda row: (order.get(str(row.get("severity")), 9), -float(row.get("confidence") or 0), str(row.get("risk_type"))))


def _infer_modules(operations: list[dict[str, Any]], objects: list[dict[str, Any]], selected: list[str]) -> list[dict[str, Any]]:
    object_names = {str(row.get("object")) for row in objects}
    modules: dict[str, dict[str, Any]] = {}
    for op in operations:
        tokens = [token for token in re.split(r"[/{}]+", str(op.get("path") or "")) if token and token not in {"api", "v1", "v2"}]
        module = tokens[0] if tokens else "root"
        row = modules.setdefault(module, {"module": module, "operations": [], "objects": set(), "industries": set()})
        row["operations"].append(f"{op.get('method')} {op.get('path')}")
        text = str(op.get("text") or "")
        for industry in selected:
            for obj, aliases in INDUSTRY_SIGNATURES[industry].get("objects", {}).items():
                if obj in object_names and any(_contains(text, alias) for alias in aliases):
                    row["objects"].add(obj)
                    row["industries"].add(industry)
    result = []
    for row in modules.values():
        result.append({"module": row["module"], "operation_count": len(row["operations"]), "operations": row["operations"][:12], "objects": sorted(row["objects"]), "industries": sorted(row["industries"])})
    return sorted(result, key=lambda row: (-int(row.get("operation_count") or 0), str(row.get("module"))))


def _private_leak_check(data: Any) -> dict[str, Any]:
    text = _json(data).lower()
    leaks = sorted(marker for marker in PRIVATE_MARKERS if marker in text)
    return {"passed": not leaks, "leak_terms": leaks}


def infer_multi_industry_business_model(documents: dict[str, str], openapi: dict[str, Any], config: dict[str, Any] | None = None, project_id: str = "in_memory") -> dict[str, Any]:
    config = config or {}
    documents = {str(k): str(v) for k, v in (documents or {}).items() if str(v).strip()}
    openapi = openapi if isinstance(openapi, dict) else {}
    operations = _openapi_operations(openapi)
    schemas = _schema_inventory(openapi)
    scores = {industry: _signature_score(signature, documents, operations, schemas) for industry, signature in INDUSTRY_SIGNATURES.items()}
    selected, mode = _select_industries(scores)
    objects = _infer_objects(selected, documents, operations, schemas)
    roles = _infer_roles(selected, documents, operations, schemas)
    state_machines = _infer_state_machines(selected, objects, documents, operations, schemas)
    dependencies = _infer_dependencies(selected, objects)
    rules, permission_boundaries, oracles = _infer_rules_and_boundaries(selected, objects, roles, operations)
    risks = _risk_domains(selected, objects, rules, state_machines, operations)
    modules = _infer_modules(operations, objects, selected)
    score_rows = [
        {
            "industry": industry,
            "name": INDUSTRY_SIGNATURES[industry]["name"],
            **data,
            "selected": industry in selected,
        }
        for industry, data in sorted(scores.items(), key=lambda item: (-float(item[1].get("score") or 0), item[0]))
    ]
    profile = {
        "phase": PHASE,
        "project_id": project_id,
        "generated_at_utc": _now(),
        "inference_mode": mode,
        "input_summary": {"document_count": len(documents), "operation_count": len(operations), "schema_count": len(schemas), "configured_domain_hint": config.get("business_domain") or config.get("domain") or "auto"},
        "industry_recognition": score_rows,
        "recognized_industries": [
            {"industry": industry, "name": INDUSTRY_SIGNATURES[industry]["name"], "confidence": scores[industry]["confidence"], "score": scores[industry]["score"], "evidence": scores[industry]["evidence"]}
            for industry in selected
        ],
        "modules": modules,
        "business_objects": objects,
        "roles": roles,
        "state_machines": state_machines,
        "permission_boundaries": permission_boundaries,
        "data_dependencies": dependencies,
        "business_rules": rules,
        "industry_oracles": oracles,
        "risk_domains": risks,
        "governance": {
            "customer_industry_knowledge_pack_required": False,
            "manual_industry_selection_required": False,
            "evidence_gated_industry_activation": True,
            "unknown_or_low_confidence_falls_back_to_general_business": True,
            "min_industry_score": _MIN_INDUSTRY_SCORE,
            "min_industry_confidence": _MIN_INDUSTRY_CONFIDENCE,
            "write_and_replay_validation_requires_sandbox": True,
            "input_sources": ["PRD", "MRD", "requirements", "OpenAPI", "interface_descriptions"],
            "vertical_oracle_activation": (
                "only_recognized_industries_with_object_evidence"
                if selected
                else "suppressed_unknown_general_business"
            ),
        },
    }
    profile["summary"] = {
        "recognized_industry_count": len(selected),
        "recognized_industries": selected,
        "top_industry": selected[0] if selected else "unknown_general_business",
        "module_count": len(modules),
        "business_object_count": len(objects),
        "role_count": len(roles),
        "state_machine_count": len(state_machines),
        "permission_boundary_count": len(permission_boundaries),
        "data_dependency_count": len(dependencies),
        "business_rule_count": len(rules),
        "oracle_count": len(oracles),
        "risk_domain_count": len(risks),
        "p0_risk_domain_count": sum(1 for row in risks if row.get("severity") == "P0"),
        "evidence_backed": bool(selected),
        "claim_guard": {
            "absolute_industry_understanding_allowed": False,
            "approved_product_language": "系统基于 PRD、MRD、OpenAPI 与接口描述持续推断多行业业务模型，并将高置信度行业规则转化为可审计的 Oracle 与风险验证计划。",
            "prohibited_product_language": ["无需任何证据即可完全理解所有行业", "覆盖全部业务 Bug", "保证零缺陷"],
        },
    }
    profile["private_leak_check"] = _private_leak_check(profile)
    return profile


def _output_paths(project: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project)
    workspace = root / "platform_workspace" / project / "defect_discovery"
    output = root / "platform_outputs" / project / "multi_industry_business_reasoning"
    return {
        "workspace": workspace,
        "output": output,
        "profile": workspace / "multi_industry_business_profile.json",
        "report": output / "multi_industry_business_report.html",
        "probe_catalog": workspace / "multi_industry_business_probe_catalog.json",
    }


def build_multi_industry_business_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    documents, openapi, cfg = _load_project_documents(project, root)
    profile = infer_multi_industry_business_model(documents, openapi, cfg, project)
    paths = _output_paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["profile"], profile)
    _write_json(paths["output"] / "multi_industry_business_profile.json", profile)
    paths["report"].write_text(render_multi_industry_business_report(profile), encoding="utf-8")
    return profile


def load_multi_industry_business_profile(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    path = _output_paths(_safe_project_id(project_id), root)["profile"]
    data = _load_json(path, {})
    return data if isinstance(data, dict) and data else None


def generate_multi_industry_business_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str = "real_project_demo", root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    profile = load_multi_industry_business_profile(project, root) or build_multi_industry_business_profile(project, root)
    operations = _openapi_operations(openapi if isinstance(openapi, dict) else {})
    selected = [str(row.get("industry")) for row in profile.get("recognized_industries") or [] if str(row.get("industry")) in INDUSTRY_SIGNATURES]
    # Evidence gate: never emit vertical industry probes from low-confidence recognition.
    selected = [
        industry
        for industry in selected
        if float(next(
            (row.get("confidence") for row in profile.get("recognized_industries") or [] if row.get("industry") == industry),
            0.0,
        ) or 0.0) >= _MIN_INDUSTRY_CONFIDENCE
        and float(next(
            (row.get("score") for row in profile.get("recognized_industries") or [] if row.get("industry") == industry),
            0.0,
        ) or 0.0) >= _MIN_INDUSTRY_SCORE
    ]
    if not selected:
        return []
    max_count = int(max_count or cfg.get("max_probe_count") or 100)
    mode = str(cfg.get("discovery_mode") or "safe").lower()
    probes: list[dict[str, Any]] = []
    for risk in profile.get("risk_domains") or []:
        industry = str(risk.get("industry") or "")
        obj = str(risk.get("business_object") or "")
        candidates = _matching_operations(obj, [industry], operations)
        if not candidates:
            candidates = [{"method": "GET", "path": "/", "operation_id": "", "summary": "", "text": ""}]
        for op in candidates[:2]:
            method = str(op.get("method") or "GET").upper()
            destructive = bool(risk.get("destructive")) or method in WRITE_METHODS
            if destructive:
                execution_policy = "sandbox_required"
            elif method in SAFE_METHODS:
                execution_policy = "execute"
            else:
                execution_policy = "candidate_only"
            actor = "normal_user"
            risk_type = str(risk.get("risk_type") or "industry_business_rule")
            if any(token in risk_type for token in ["tenant", "ownership", "sensitive_data", "account_ownership", "grade_integrity"]):
                actor = "secondary_identity_required"
            probes.append({
                "probe_id": f"RP_INDUSTRY_{len(probes)+1:04d}",
                "source": "multi_industry_business_reasoning",
                "industry": industry,
                "industry_confidence": next((row.get("confidence") for row in profile.get("recognized_industries") or [] if row.get("industry") == industry), 0.5),
                "business_object": obj,
                "risk_type": risk_type,
                "industry_risk_domain_id": risk.get("risk_id"),
                "title": risk.get("title"),
                "severity": risk.get("severity") or "P1",
                "actor": actor,
                "method": method,
                "path": op.get("path") or "/",
                "operation_id": op.get("operation_id") or "",
                "expected": risk.get("expected"),
                "bug_signal": risk.get("bug_signal"),
                "oracle_family": risk.get("oracle_family"),
                "oracle_assertion": risk.get("expected"),
                "probe_recipe": risk.get("probe_recipe"),
                "destructive": destructive,
                "execution_policy": execution_policy,
                "confidence_prior": round(min(0.95, float(risk.get("confidence") or 0.6) + 0.05), 3),
                "matched_business_operations": risk.get("matched_operations") or [],
                "reasoning_trace": f"PRD/MRD/OpenAPI 证据识别为 {industry}，对象 {obj} 触发 {risk_type}。",
                "discovery_mode": mode,
            })
            if len(probes) >= max_count:
                break
        if len(probes) >= max_count:
            break
    paths = _output_paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["probe_catalog"], {"phase": PHASE, "project_id": project, "items": probes})
    return probes


def _evidence_labels(rows: Any) -> str:
    return "; ".join(
        f"{item.get('source')}:{item.get('reference')}"
        for item in rows if isinstance(item, dict)
    )


def render_multi_industry_business_report(profile: dict[str, Any]) -> str:
    summary = profile.get("summary") or {}
    selected_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("industry")),
            _html_escape(row.get("name")),
            _html_escape(row.get("confidence")),
            _html_escape(row.get("score")),
            _html_escape(_evidence_labels(row.get("evidence") or [])),
        )
        for row in profile.get("recognized_industries") or []
    )
    module_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("module")),
            _html_escape(row.get("operation_count")),
            _html_escape(", ".join(row.get("objects") or [])),
            _html_escape(", ".join(row.get("industries") or [])),
        )
        for row in profile.get("modules") or []
    )
    object_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("object")),
            _html_escape(", ".join(row.get("industries") or [])),
            _html_escape(row.get("confidence")),
            _html_escape(_evidence_labels(row.get("evidence") or [])),
        )
        for row in profile.get("business_objects") or []
    )
    state_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("industry")),
            _html_escape(row.get("object")),
            _html_escape(" → ".join(str(x) for x in row.get("states") or [])),
            _html_escape(", ".join(str(x) for x in row.get("terminal_states") or [])),
            _html_escape(row.get("inference_mode")),
        )
        for row in profile.get("state_machines") or []
    )
    boundary_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("industry")),
            _html_escape(row.get("boundary_type")),
            _html_escape(", ".join(row.get("protected_objects") or [])),
            _html_escape(row.get("oracle_family")),
            _html_escape(row.get("expected")),
        )
        for row in profile.get("permission_boundaries") or []
    )
    dependency_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("industry")),
            _html_escape(row.get("source_object")),
            _html_escape(row.get("relationship")),
            _html_escape(row.get("target_object")),
        )
        for row in profile.get("data_dependencies") or []
    )
    risk_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("severity")),
            _html_escape(row.get("industry")),
            _html_escape(row.get("risk_type")),
            _html_escape(row.get("business_object")),
            _html_escape(row.get("oracle_family")),
            _html_escape(row.get("matched_operations")),
        )
        for row in profile.get("risk_domains") or []
    )
    oracle_rows = "".join(
        "<tr><td>{}</td><td>{}</td><td>{}</td><td>{}</td></tr>".format(
            _html_escape(row.get("industry")),
            _html_escape(row.get("oracle_family")),
            _html_escape(", ".join(row.get("objects") or [])),
            _html_escape(row.get("assertion")),
        )
        for row in profile.get("industry_oracles") or []
    )
    cards = "".join(
        "<div class='card'><span>{}</span><b>{}</b></div>".format(_html_escape(k), _html_escape(v))
        for k, v in summary.items() if k not in {"claim_guard"}
    )
    claim = summary.get("claim_guard") or {}
    fallback_industries = '<tr><td colspan="5">没有达到行业识别阈值，已回退到通用业务模式。</td></tr>'
    fallback_modules = '<tr><td colspan="4">暂无接口模块</td></tr>'
    fallback_objects = '<tr><td colspan="4">暂无对象</td></tr>'
    fallback_states = '<tr><td colspan="5">未识别状态机</td></tr>'
    fallback_boundaries = '<tr><td colspan="5">未识别权限边界</td></tr>'
    fallback_dependencies = '<tr><td colspan="4">暂无</td></tr>'
    fallback_oracles = '<tr><td colspan="4">暂无</td></tr>'
    fallback_risks = '<tr><td colspan="6">暂无</td></tr>'
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>多行业业务理解报告</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.hero,.panel{{background:#fff;border:1px solid #e5e7eb;border-radius:18px;padding:22px;margin-bottom:18px;box-shadow:0 8px 24px #0001}}.grid{{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px}}.card{{border:1px solid #e5e7eb;border-radius:14px;padding:14px;background:#fafafa}}.card span{{display:block;color:#6b7280;font-size:12px}}.card b{{font-size:20px}}table{{width:100%;border-collapse:collapse}}td,th{{padding:9px;border-bottom:1px solid #e5e7eb;text-align:left;vertical-align:top}}.badge{{display:inline-block;padding:4px 10px;border-radius:999px;background:#ecfeff;color:#155e75}}@media(max-width:900px){{.grid{{grid-template-columns:1fr}}}}</style></head><body>
<section class='hero'><span class='badge'>Phase57</span><h1>多行业业务理解验证报告</h1><p>只基于 PRD/MRD/OpenAPI/接口描述推断行业、对象、角色、状态机、数据规则、权限边界与高价值风险。当前模式：<b>{_html_escape(profile.get('inference_mode'))}</b></p><p>产品表述：{_html_escape(claim.get('approved_product_language'))}</p></section>
<section class='panel'><h2>理解覆盖概览</h2><div class='grid'>{cards}</div></section>
<section class='panel'><h2>行业识别与证据</h2><table><thead><tr><th>行业</th><th>名称</th><th>置信度</th><th>分数</th><th>文档/接口证据</th></tr></thead><tbody>{selected_rows or fallback_industries}</tbody></table></section>
<section class='panel'><h2>模块与业务对象</h2><table><thead><tr><th>模块</th><th>接口数</th><th>对象</th><th>行业</th></tr></thead><tbody>{module_rows or fallback_modules}</tbody></table><br><table><thead><tr><th>对象</th><th>行业</th><th>置信度</th><th>证据</th></tr></thead><tbody>{object_rows or fallback_objects}</tbody></table></section>
<section class='panel'><h2>状态机与权限边界</h2><table><thead><tr><th>行业</th><th>对象</th><th>状态</th><th>终态</th><th>推断方式</th></tr></thead><tbody>{state_rows or fallback_states}</tbody></table><br><table><thead><tr><th>行业</th><th>边界类型</th><th>保护对象</th><th>Oracle</th><th>规则</th></tr></thead><tbody>{boundary_rows or fallback_boundaries}</tbody></table></section>
<section class='panel'><h2>数据依赖与行业 Oracle</h2><table><thead><tr><th>行业</th><th>源对象</th><th>关系</th><th>目标对象</th></tr></thead><tbody>{dependency_rows or fallback_dependencies}</tbody></table><br><table><thead><tr><th>行业</th><th>Oracle</th><th>对象</th><th>断言</th></tr></thead><tbody>{oracle_rows or fallback_oracles}</tbody></table></section>
<section class='panel'><h2>高价值风险域 → Probe / Radar</h2><table><thead><tr><th>等级</th><th>行业</th><th>风险域</th><th>对象</th><th>Oracle</th><th>匹配接口</th></tr></thead><tbody>{risk_rows or fallback_risks}</tbody></table></section>
</body></html>"""


def load_multi_industry_evaluation_samples() -> dict[str, dict[str, Any]]:
    """Load seven-industry evaluator fixtures, never customer runtime rules.

    The fixture is intentionally separate from the inference engine so it can
    validate that inputs from different industries produce materially different
    business objects and probes.  When a source distribution excludes examples,
    the compact fallback below keeps CLI/demo behavior deterministic.
    """
    fixture = Path(__file__).resolve().parents[1] / "examples" / "phase57_multi_industry_evaluation.json"
    try:
        raw = json.loads(fixture.read_text(encoding="utf-8", errors="replace"))
        cases = raw.get("cases") if isinstance(raw, dict) else None
        if isinstance(cases, dict) and all(isinstance(v, dict) for v in cases.values()):
            return {str(k): dict(v) for k, v in cases.items()}
    except Exception:
        pass
    return _demo_samples_fallback()


def _demo_samples() -> dict[str, dict[str, Any]]:
    return load_multi_industry_evaluation_samples()


def _demo_samples_fallback() -> dict[str, dict[str, Any]]:
    # Kept compact and purpose-built for evaluator/demo use.  They are not
    # runtime customer packs and are only used by CLI --demo/tests.
    return {
        "crm": {
            "prd": "销售通过 lead 创建客户 customer、商机 opportunity、报价 quote 和合同 contract。销售仅可见自己负责客户。合同 draft -> approved -> signed，超折扣需销售经理审批。",
            "paths": ["/leads", "/customers/{customer_id}", "/opportunities/{opportunity_id}", "/quotes", "/contracts/{contract_id}/sign"],
        },
        "erp": {
            "prd": "采购订单 purchase order 审批后收货 goods receipt 并更新仓库 inventory。采购订单、收货单、invoice 发票需三单匹配；库存按仓库和物料守恒。",
            "paths": ["/purchase-orders", "/goods-receipts", "/warehouses/{warehouse_id}/inventory", "/invoices"],
        },
        "finance": {
            "prd": "账户 account 余额 balance、ledger 账本与 transfer 转账交易必须一致。提现和额度 limit 需要风控审批，交易 initiated -> pending -> settled 或 reversed。",
            "paths": ["/accounts/{account_id}", "/transactions", "/ledger", "/withdrawals"],
        },
        "healthcare": {
            "prd": "患者 patient 只能被有治疗关系的 doctor 医生查看。appointment 预约不可冲突，prescription 处方需授权医生签署并绑定患者。",
            "paths": ["/patients/{patient_id}/records", "/appointments", "/prescriptions"],
        },
        "education": {
            "prd": "student 学生报名 enrollment 课程 course 不能超容量。teacher 教师才能发布 score 成绩；exam 考试 draft -> scheduled -> graded -> published。",
            "paths": ["/courses/{course_id}/enrollments", "/exams", "/scores/{score_id}"],
        },
        "saas_multitenant": {
            "prd": "tenant 租户和 workspace 工作区的数据必须隔离。membership 成员邀请后 active，subscription 订阅到期后 entitlement 权益失效。tenant_admin 管理成员但不能读取其他 tenant 数据。",
            "paths": ["/tenants/{tenant_id}/members", "/workspaces/{workspace_id}", "/subscriptions", "/entitlements"],
        },
        "ecommerce": {
            "prd": "用户将 sku 商品加入 cart 购物车并创建 order 订单，payment 支付与 refund 退款累计必须与订单金额一致。inventory 库存预留不能超卖，coupon 优惠券只能使用一次。",
            "paths": ["/carts", "/orders/{order_id}", "/payments", "/refunds", "/inventory/{sku_id}", "/coupons"],
        },
    }


def run_multi_industry_demo() -> dict[str, Any]:
    evaluations: list[dict[str, Any]] = []
    for expected, sample in _demo_samples().items():
        paths = {}
        for path in sample["paths"]:
            paths[path] = {
                "get": {"summary": f"Get {path}", "responses": {"200": {"description": "ok"}}},
                "post": {"summary": f"Create or update {path}", "responses": {"200": {"description": "ok"}}},
            }
        openapi = {"openapi": "3.0.3", "info": {"title": f"{expected} demo", "version": "1"}, "paths": paths}
        profile = infer_multi_industry_business_model({"prd.md": sample["prd"]}, openapi, project_id=f"demo_{expected}")
        recognized = [row.get("industry") for row in profile.get("recognized_industries") or []]
        risk_types = [row.get("risk_type") for row in profile.get("risk_domains") or []]
        passed = expected in recognized and bool(profile.get("business_objects")) and bool(risk_types)
        evaluations.append({
            "expected_industry": expected,
            "recognized_industries": recognized,
            "business_objects": [row.get("object") for row in profile.get("business_objects") or []],
            "risk_types": risk_types,
            "probe_intent_count": len(profile.get("risk_domains") or []),
            "passed": passed,
        })
    return {
        "phase": PHASE,
        "demo_name": "seven_industry_document_evidence_validation",
        "executed_at_utc": _now(),
        "summary": {"case_count": len(evaluations), "passed_count": sum(1 for row in evaluations if row["passed"]), "failed_count": sum(1 for row in evaluations if not row["passed"]), "all_passed": all(row["passed"] for row in evaluations)},
        "cases": evaluations,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Phase57 multi-industry business reasoning")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--demo", action="store_true")
    args = parser.parse_args(argv)
    if args.demo:
        print(json.dumps(run_multi_industry_demo(), ensure_ascii=False, indent=2))
        return 0
    profile = build_multi_industry_business_profile(args.project)
    print(json.dumps({"ok": True, "project_id": profile.get("project_id"), "summary": profile.get("summary"), "recognized_industries": profile.get("recognized_industries")}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
