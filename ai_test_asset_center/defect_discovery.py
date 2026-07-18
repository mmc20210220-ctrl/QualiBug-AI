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
from ai_test_asset_center.rag_probe_generator import generate_rag_enhanced_probes, summarize_rag_probes


PRIVATE_BLOCKLIST = ("private_ground_truth", "ground_truth_bugs", "bug_sets", "enabled_bugs", "bug_set")


PROBE_POLICY_PROFILES = {
    "baseline": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "journey_auto"},
    "feedback": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "journey_auto"},
    "rag": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "rag_enhanced", "journey_auto"},
    "rag_enhanced": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "rag_enhanced", "journey_auto"},
    "feedback_adjusted": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "feedback_adjusted", "journey_auto"},
    "adaptive": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "journey_auto"},
    "conservative": {"pattern_library"},
    "full_blind": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "rag_enhanced", "journey_auto"},
    "demo": {"generic_auto", "pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "adaptive_policy", "rag_enhanced", "journey_auto"},
}

def normalize_probe_policy_profile(profile: str | None = None, discovery_mode: str = "blind") -> str:
    raw = (profile or os.environ.get("PROBE_POLICY_PROFILE") or "adaptive").strip().lower()
    aliases = {
        "base": "baseline",
        "baseline_policy": "baseline",
        "feedback_learning": "feedback",
        "feedback_policy": "feedback",
        "adaptive_policy": "adaptive",
        "adaptive_v1": "adaptive",
        "rag_policy": "rag_enhanced",
        "rag_enhanced_policy": "rag_enhanced",
        "feedback_adjusted_policy": "feedback_adjusted",
        "human_feedback_policy": "feedback_adjusted",
        "qa_feedback": "feedback_adjusted",
        "rag_plus": "rag_enhanced",
        "full": "full_blind",
        "blind_full": "full_blind",
        "safe": "conservative",
    }
    raw = aliases.get(raw, raw)
    if normalize_discovery_mode(discovery_mode) == "demo" and raw not in {"baseline", "feedback", "adaptive", "conservative", "full_blind"}:
        return "demo"
    if raw not in PROBE_POLICY_PROFILES:
        return "adaptive"
    if raw == "demo" and normalize_discovery_mode(discovery_mode) != "demo":
        return "adaptive"
    return raw

def allowed_sources_for_policy(profile: str, discovery_mode: str = "blind") -> set[str]:
    normalized = normalize_probe_policy_profile(profile, discovery_mode)
    return set(PROBE_POLICY_PROFILES[normalized])

def filter_probes_by_policy(probes: list[dict], profile: str, discovery_mode: str = "blind") -> list[dict]:
    allowed = allowed_sources_for_policy(profile, discovery_mode)
    return [p for p in probes if p.get("source") in allowed]

ENTERPRISE_RISK_TAXONOMY = {
    "access_control": ["permission_bypass", "auth_bypass", "idor", "tenant_isolation", "role_escalation", "field_level_permission"],
    "workflow": ["state_flow", "approval_bypass", "step_skip", "rollback_consistency", "sla_timeout", "terminal_state_mutation"],
    "financial": ["money_consistency", "fee_calculation", "tax_consistency", "settlement_reconciliation", "rounding_precision", "credit_limit"],
    "quantity_asset": ["quantity_consistency", "quota_limit", "inventory_consistency", "capacity_limit", "negative_balance"],
    "data_quality": ["required_field_bypass", "enum_constraint", "duplicate_record", "referential_integrity", "soft_delete_visibility", "stale_cache"],
    "integration": ["callback_trust", "webhook_replay", "third_party_status_mapping", "message_ordering", "eventual_consistency"],
    "batch_import": ["file_upload_validation", "bulk_operation_partial_failure", "duplicate_import", "async_job_status", "large_payload_limit"],
    "audit_compliance": ["audit_log_missing", "sensitive_data_exposure", "privacy_scope", "data_retention", "export_permission"],
    "configuration": ["feature_flag_scope", "tenant_config_isolation", "pricing_config", "workflow_config", "default_value_risk"],
    "notification": ["notification_wrong_recipient", "notification_duplicate", "notification_missing", "template_variable_leak"],
    "search_report": ["search_scope_leak", "report_aggregation_error", "pagination_consistency", "sorting_filter_consistency", "export_consistency"],
    "time_concurrency": ["idempotency", "race_condition", "concurrent_update_lost", "time_window_boundary", "timezone_boundary"],
}


@dataclass
class DiscoveryConfig:
    project: str = "enterprise_shop"
    public_artifacts: Path = Path("enterprise_bug_factory/public_artifacts")
    workspace_root: Path = Path("platform_workspace")
    output_root: Path = Path("platform_outputs")
    discovery_mode: str = os.environ.get("DEFECT_DISCOVERY_MODE", "blind")


def ensure_public_path(path: Path) -> Path:
    text = str(path).replace("\\", "/").lower()
    if any(token in text for token in PRIVATE_BLOCKLIST):
        raise PermissionError(f"AI discovery cannot read private benchmark path: {path}")
    return path


def read_json(path: Path) -> Any:
    ensure_public_path(path)
    return json.loads(path.read_text(encoding="utf-8"))


def read_text(path: Path) -> str:
    ensure_public_path(path)
    return path.read_text(encoding="utf-8", errors="replace")


def read_json_if_exists(path: Path) -> Any:
    ensure_public_path(path)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def load_business_knowledge_model(config: DiscoveryConfig) -> dict:
    candidates = [
        config.public_artifacts / "business_knowledge_model.json",
        config.public_artifacts / "business_knowledge" / "business_knowledge_model.json",
        config.output_root / config.project / "business_knowledge" / "business_knowledge_model.json",
        config.workspace_root / config.project / "business_knowledge" / "business_knowledge_model.json",
        Path("platform_outputs") / config.project / "business_knowledge" / "business_knowledge_model.json",
    ]
    for path in candidates:
        data = read_json_if_exists(path)
        if isinstance(data, dict):
            data["_source_path"] = str(path)
            return data
    return {}


def enrich_business_model_with_knowledge(business_model: dict, knowledge_model: dict) -> dict:
    if not knowledge_model:
        business_model["enterprise_knowledge"] = {
            "enabled": False,
            "source": "",
            "module_count": 0,
            "risk_count": 0,
            "rule_count": 0,
            "scenario_count": 0,
        }
        return business_model
    business_model["business_knowledge_model"] = knowledge_model
    business_model["enterprise_knowledge"] = {
        "enabled": True,
        "source": knowledge_model.get("_source_path", ""),
        "module_count": len(knowledge_model.get("module_knowledge_map", []) or []),
        "risk_count": len(knowledge_model.get("risk_matrix", []) or []),
        "rule_count": len(knowledge_model.get("business_rules", []) or []),
        "scenario_count": len(knowledge_model.get("business_scenarios", []) or []),
    }
    knowledge_risks = set()
    for risk in knowledge_model.get("risk_matrix", []) or []:
        knowledge_risks.add(normalize_knowledge_risk(risk.get("risk") or risk.get("risk_id") or ""))
    for rule in knowledge_model.get("business_rules", []) or []:
        knowledge_risks.update(risks_from_text(rule.get("text", "")))
    for op in business_model.get("operations", []):
        text = f"{op.get('path')} {op.get('summary')} {op.get('resource')}".lower()
        hints = set(op.get("risk_hints", []))
        for risk in knowledge_risks:
            if operation_matches_any_knowledge_risk(op, {risk}):
                hints.add(risk)
        hints.update(risks_from_text(text))
        op["risk_hints"] = sorted(hints)
    return business_model


class HttpClient:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")

    def request(self, method: str, path: str, token: str | None = None, body: dict | None = None) -> dict:
        url = self.base_url + path
        data = None if body is None else json.dumps(body).encode("utf-8")
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, data=data, method=method.upper(), headers=headers)
        start = time.time()
        try:
            with urllib.request.urlopen(req, timeout=8) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
                parsed = json.loads(raw) if raw else {}
                return {"status_code": resp.status, "body": parsed, "duration_ms": round((time.time() - start) * 1000, 2)}
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else {}
            except Exception:
                parsed = {"raw": raw[:1000]}
            return {"status_code": exc.code, "body": parsed, "duration_ms": round((time.time() - start) * 1000, 2)}


def extract_business_rules(prd: str, openapi: dict) -> list[dict]:
    paths = sorted(openapi.get("paths", {}).keys())
    domains = ["permission", "idor", "tenant", "order", "stock", "coupon", "payment", "refund", "idempotency", "money"]
    return [{"rule_id": f"BR_{domain.upper()}", "domain": domain, "source": "PRD+OpenAPI", "paths": [p for p in paths if keyword_hits(domain, p)]} for domain in domains]


def _documented_request_contract(spec: dict) -> tuple[dict, dict]:
    request_body = spec.get("requestBody") if isinstance(spec.get("requestBody"), dict) else {}
    content = request_body.get("content") if isinstance(request_body.get("content"), dict) else {}
    media = content.get("application/json") if isinstance(content.get("application/json"), dict) else {}
    schema = media.get("schema") if isinstance(media.get("schema"), dict) else {}
    example = media.get("example") if isinstance(media.get("example"), dict) else {}
    if not example and isinstance(schema.get("example"), dict):
        example = schema["example"]
    if not example:
        examples = media.get("examples") if isinstance(media.get("examples"), dict) else {}
        for row in examples.values():
            value = row.get("value") if isinstance(row, dict) else None
            if isinstance(value, dict):
                example = value
                break
    return dict(schema), dict(example)


def infer_business_model(prd: str, openapi: dict, accounts: dict) -> dict:
    """Build a zero-config business model from the user's single input bundle.

    This intentionally reads only public materials: PRD/MRD-like text, OpenAPI and
    test account metadata. It does not rely on a hand-maintained industry pack.
    """
    paths = openapi.get("paths", {})
    operations = []
    resources: dict[str, dict] = {}
    for path, methods in paths.items():
        resource = resource_name(path)
        resources.setdefault(resource, {"name": resource, "paths": [], "risk_hints": set(), "operations": []})
        resources[resource]["paths"].append(path)
        for method, spec in methods.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            request_schema, request_example = _documented_request_contract(spec)
            op = {
                "method": method.upper(),
                "path": path,
                "resource": resource,
                "summary": str(spec.get("summary") or ""),
                "operation": classify_operation(method.upper(), path, str(spec.get("summary") or "")),
                "risk_hints": risk_hints_for(method.upper(), path, str(spec.get("summary") or ""), prd),
                "request_schema": request_schema,
                "request_example": request_example,
            }
            operations.append(op)
            resources[resource]["operations"].append(op)
            resources[resource]["risk_hints"].update(op["risk_hints"])
    roles = sorted({a.get("role") for a in accounts.get("accounts", []) if a.get("role")})
    tenants = sorted({a.get("tenant_id") for a in accounts.get("accounts", []) if a.get("tenant_id")})
    model = {
        "input_mode": "single_input_auto_understanding",
        "industry": infer_industry(prd, paths),
        "risk_taxonomy_version": "enterprise_taxonomy_v1",
        "risk_taxonomy": ENTERPRISE_RISK_TAXONOMY,
        "roles": roles,
        "tenants": tenants,
        "business_objects": [
            {"name": item["name"], "paths": sorted(set(item["paths"])), "risk_hints": sorted(item["risk_hints"])}
            for item in resources.values()
        ],
        "operations": operations,
        "inferred_invariants": infer_invariants_from_model(operations, roles, tenants),
        "semantic_graph": build_semantic_graph(operations, prd),
        "state_machines": infer_state_machines(operations, prd),
        "data_lineage": infer_data_lineage(operations, prd),
        "entity_dependencies": infer_entity_dependencies(operations),
        "business_scenarios": infer_business_scenarios(operations, prd),
        "generation_policy": {
            "manual_domain_pack_required": False,
            "uses_public_prd_openapi_accounts_only": True,
            "private_ground_truth_allowed": False,
        },
    }
    return model


def resource_name(path: str) -> str:
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
    if not parts:
        return "root"
    if parts[0] in {"admin", "tenant"} and len(parts) > 1:
        return parts[1]
    return parts[0]


def classify_operation(method: str, path: str, summary: str) -> str:
    text = f"{path} {summary}".lower()
    if "login" in text:
        return "authenticate"
    if "cancel" in text:
        return "state_cancel"
    if "refund" in text:
        return "refund"
    if "payment" in text or "pay" in text:
        return "payment"
    if "callback" in text:
        return "callback"
    if method == "GET":
        return "read"
    if method == "POST":
        return "create_or_action"
    if method in {"PUT", "PATCH"}:
        return "update"
    if method == "DELETE":
        return "delete"
    return "action"


def risk_hints_for(method: str, path: str, summary: str, prd: str) -> list[str]:
    text = f"{path} {summary}".lower()
    prd_text = prd.lower()
    hints = []
    if "admin" in path.lower() or "管理" in summary:
        hints.append("permission_bypass")
    if "{order_id}" in path or "{id}" in path or method in {"PUT", "PATCH", "DELETE"}:
        hints.append("idor")
    if "tenant" in text or "租户" in summary or "组织" in summary:
        hints.append("tenant_isolation")
    if any(k in text for k in ["amount", "price", "total", "pay", "refund", "金额", "费用"]):
        hints.append("money_consistency")
    if any(k in text for k in ["stock", "quantity", "product", "库存", "数量"]):
        hints.append("quantity_consistency")
    if any(k in text for k in ["status", "state", "cancel", "refund", "审批", "状态", "取消", "退款"]):
        hints.append("state_flow")
    if any(k in text for k in ["callback", "idempotency", "重复", "幂等", "回调"]):
        hints.append("idempotency")
    if "coupon" in text or "优惠" in text:
        hints.append("benefit_abuse")
    if method == "POST" and any(k in prd_text for k in ["重复", "幂等", "重复提交"]):
        hints.append("idempotency")
    if any(k in text for k in ["approve", "approval", "audit", "审核", "审批", "复核"]):
        hints.extend(["approval_bypass", "audit_log_missing", "state_flow"])
    if any(k in text for k in ["upload", "file", "import", "batch", "bulk", "导入", "上传", "批量", "文件", "excel"]):
        hints.extend(["file_upload_validation", "duplicate_import", "bulk_operation_partial_failure", "large_payload_limit"])
    if any(k in text for k in ["export", "report", "dashboard", "统计", "报表", "导出", "看板"]):
        hints.extend(["export_permission", "report_aggregation_error", "export_consistency"])
    if any(k in text for k in ["search", "filter", "sort", "page", "查询", "搜索", "筛选", "排序", "分页"]):
        hints.extend(["search_scope_leak", "pagination_consistency", "sorting_filter_consistency"])
    if any(k in text for k in ["notify", "notification", "sms", "email", "message", "通知", "短信", "邮件", "站内信"]):
        hints.extend(["notification_wrong_recipient", "notification_duplicate", "template_variable_leak"])
    if any(k in text for k in ["config", "setting", "feature", "flag", "配置", "开关", "规则", "策略"]):
        hints.extend(["feature_flag_scope", "tenant_config_isolation", "default_value_risk"])
    if any(k in text for k in ["delete", "archive", "disable", "删除", "归档", "禁用", "作废"]):
        hints.extend(["soft_delete_visibility", "terminal_state_mutation", "audit_log_missing"])
    if any(k in text for k in ["webhook", "callback", "event", "mq", "消息", "事件", "回调"]):
        hints.extend(["callback_trust", "webhook_replay", "message_ordering", "eventual_consistency"])
    if any(k in text for k in ["limit", "quota", "capacity", "额度", "限额", "配额", "容量"]):
        hints.extend(["quota_limit", "capacity_limit", "credit_limit"])
    if any(k in text for k in ["personal", "privacy", "sensitive", "phone", "idcard", "隐私", "敏感", "手机号", "身份证"]):
        hints.extend(["sensitive_data_exposure", "privacy_scope", "field_level_permission"])
    if any(k in text for k in ["date", "time", "expire", "schedule", "日期", "时间", "过期", "定时"]):
        hints.extend(["time_window_boundary", "timezone_boundary", "sla_timeout"])
    if method in {"PUT", "PATCH", "POST"} and any(k in prd_text for k in ["并发", "多人", "同时", "抢占", "锁定"]):
        hints.extend(["race_condition", "concurrent_update_lost"])
    return sorted(set(hints))


def infer_industry(prd: str, paths: dict) -> str:
    text = (prd + " " + " ".join(paths.keys())).lower()
    candidates = [
        ("ecommerce", ["order", "product", "stock", "coupon", "订单", "商品", "库存", "优惠券"]),
        ("finance", ["account", "balance", "transfer", "loan", "账户", "余额", "转账", "授信"]),
        ("crm", ["customer", "lead", "opportunity", "客户", "线索", "商机"]),
        ("erp", ["purchase", "warehouse", "supplier", "采购", "仓库", "供应商"]),
        ("healthcare", ["patient", "prescription", "medical", "患者", "处方", "病历"]),
        ("education", ["course", "student", "lesson", "课程", "学员", "课时"]),
    ]
    scores = [(name, sum(1 for word in words if word in text)) for name, words in candidates]
    scores.sort(key=lambda item: (-item[1], item[0]))
    # Fail closed: weak or tied keyword hits must not invent ecommerce (or any vertical).
    if not scores or scores[0][1] < 2:
        return "generic_enterprise_software"
    if len(scores) > 1 and scores[0][1] == scores[1][1]:
        return "generic_enterprise_software"
    return scores[0][0]


def infer_invariants_from_model(operations: list[dict], roles: list[str], tenants: list[str]) -> list[dict]:
    invariants = []
    for op in operations:
        for risk in op["risk_hints"]:
            invariants.append(
                {
                    "invariant_id": f"AUTO_{risk.upper()}_{len(invariants) + 1:03d}",
                    "risk_type": risk,
                    "resource": op["resource"],
                    "operation": op["operation"],
                    "path": op["path"],
                    "statement": invariant_statement(risk, op, roles, tenants),
                }
            )
    return invariants


def build_semantic_graph(operations: list[dict], prd: str) -> dict:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    for op in operations:
        resource = op["resource"]
        nodes.setdefault(resource, {"id": resource, "type": "business_object", "risk_hints": set(), "operations": []})
        nodes[resource]["risk_hints"].update(op.get("risk_hints", []))
        nodes[resource]["operations"].append(f"{op['method']} {op['path']}")
    resources = list(nodes)
    for src in resources:
        for dst in resources:
            if src == dst:
                continue
            relation = infer_relation(src, dst, prd)
            if relation:
                edges.append({"from": src, "to": dst, "relation": relation, "confidence": 0.68})
    return {
        "nodes": [{"id": v["id"], "type": v["type"], "risk_hints": sorted(v["risk_hints"]), "operations": v["operations"]} for v in nodes.values()],
        "edges": dedupe_edges(edges),
    }


def _resource_tokens(name: str) -> set[str]:
    raw = str(name or "").strip().lower().replace("-", "_")
    if not raw:
        return set()
    parts = [p for p in raw.split("_") if p]
    tokens = {raw, *parts}
    # Light plural/singular bridging without industry-specific dictionaries.
    for part in list(parts) + [raw]:
        if part.endswith("ies") and len(part) > 3:
            tokens.add(part[:-3] + "y")
        elif part.endswith("ses") and len(part) > 3:
            tokens.add(part[:-2])
        elif part.endswith("s") and len(part) > 2:
            tokens.add(part[:-1])
        else:
            tokens.add(part + "s")
            if part.endswith("y") and len(part) > 1:
                tokens.add(part[:-1] + "ies")
    return {t for t in tokens if t}


def _resource_in(name: str, families: set[str]) -> bool:
    return bool(_resource_tokens(name) & {f.lower() for f in families})


# Cross-industry role families. Membership is evidence of resource shape, not a
# default industry assignment — relations only fire when both endpoints exist.
_PAYMENT_LIKE = {
    "payment", "payments", "settlement", "settlements", "transaction", "transactions",
    "charge", "charges", "billing", "payout", "payouts",
}
_REFUND_LIKE = {"refund", "refunds", "chargeback", "chargebacks", "reversal", "reversals"}
_INVENTORY_LIKE = {
    "product", "products", "sku", "skus", "inventory", "stock", "material", "materials",
    "item", "items", "goods", "asset", "assets", "seat", "seats", "bed", "beds",
}
_CART_LIKE = {"cart", "carts", "basket", "baskets", "wishlist", "wishlists"}
_FULFILLMENT_PARENT_LIKE = {
    "order", "orders", "purchase_order", "purchase_orders", "work_order", "work_orders",
    "booking", "bookings", "reservation", "reservations", "enrollment", "enrollments",
    "appointment", "appointments", "ticket", "tickets", "claim", "claims",
    "invoice", "invoices", "loan", "loans", "shipment", "shipments",
    "prescription", "prescriptions", "requisition", "requisitions",
}
_OWNERSHIP_PARENT_LIKE = {
    "patient", "patients", "customer", "customers", "account", "accounts",
    "tenant", "tenants", "student", "students", "user", "users", "member", "members",
    "organization", "organizations", "lead", "leads",
}
_OWNED_RECORD_LIKE = {
    "record", "records", "medical_record", "medical_records", "profile", "profiles",
    "document", "documents", "note", "notes", "history", "histories",
    "grade", "grades", "score", "scores", "case", "cases",
}


def infer_relation(src: str, dst: str, prd: str) -> str | None:
    """Infer a directed business relation from resource roles + PRD co-occurrence.

    Never invents ecommerce-only edges for unrelated systems: hardcoded
    orders/cart/coupon pairs are replaced by cross-industry role families that
    only activate when both resources are present in the graph.
    """
    src_l = str(src or "").strip().lower()
    dst_l = str(dst or "").strip().lower()
    if not src_l or not dst_l or src_l == dst_l:
        return None

    # Exact legacy ecommerce pairs remain valid when those resources exist,
    # but are expressed via role families below rather than a closed mall map.
    if _resource_in(src_l, _FULFILLMENT_PARENT_LIKE) and _resource_in(dst_l, _INVENTORY_LIKE):
        return "consumes_or_locks"
    if _resource_in(src_l, _FULFILLMENT_PARENT_LIKE) and _resource_in(dst_l, _PAYMENT_LIKE):
        return "paid_by"
    if _resource_in(src_l, _FULFILLMENT_PARENT_LIKE) and _resource_in(dst_l, _REFUND_LIKE):
        return "refunded_by"
    if _resource_in(src_l, _PAYMENT_LIKE) and _resource_in(dst_l, _REFUND_LIKE):
        return "refund_depends_on_payment"
    if _resource_in(src_l, _CART_LIKE) and _resource_in(dst_l, _FULFILLMENT_PARENT_LIKE):
        return "checkout_to_order"
    if _resource_in(src_l, _FULFILLMENT_PARENT_LIKE) and _resource_in(dst_l, _CART_LIKE):
        return "created_from"
    if _resource_in(src_l, _OWNERSHIP_PARENT_LIKE) and _resource_in(dst_l, _OWNED_RECORD_LIKE):
        return "owns_or_scopes"
    if _resource_in(src_l, _OWNED_RECORD_LIKE) and _resource_in(dst_l, _OWNERSHIP_PARENT_LIKE):
        return "belongs_to"

    # Morphological nesting: patients -> patient_records, course -> course_enrollments
    src_tokens = _resource_tokens(src_l)
    dst_tokens = _resource_tokens(dst_l)
    if src_tokens & dst_tokens and (dst_l.startswith(src_l.rstrip("s") + "_") or src_l.startswith(dst_l.rstrip("s") + "_")):
        if len(dst_l) >= len(src_l):
            return "parent_child"
        return "belongs_to"

    text = (prd or "").lower()
    if src_l in text and dst_l in text:
        if any(k in text for k in ["审批", "审核", "approve", "workflow", "approval"]):
            return "workflow_related"
        if any(k in text for k in ["金额", "费用", "amount", "balance", "settlement", "ledger"]):
            return "financial_dependency"
        if any(k in text for k in ["组织", "租户", "tenant", "organization", "scope"]):
            return "scope_dependency"
        if any(k in text for k in ["归属", "负责", "owner", "ownership", "assigned"]):
            return "ownership_related"
        if any(k in text for k in ["依赖", "关联", "depends", "related", "reference"]):
            return "referenced_dependency"
    return None


def dedupe_edges(edges: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for edge in edges:
        key = (edge["from"], edge["to"], edge["relation"])
        if key in seen:
            continue
        seen.add(key)
        result.append(edge)
    return result


def infer_state_machines(operations: list[dict], prd: str) -> list[dict]:
    by_resource: dict[str, list[dict]] = {}
    for op in operations:
        by_resource.setdefault(op["resource"], []).append(op)
    machines = []
    for resource, ops in by_resource.items():
        transitions = []
        for op in ops:
            operation = op["operation"]
            if operation == "create_or_action":
                transitions.append({"from": "none", "action": f"{op['method']} {op['path']}", "to": "created", "guard": "required fields and ownership valid"})
            elif operation == "state_cancel":
                transitions.append({"from": "created", "action": f"{op['method']} {op['path']}", "to": "cancelled", "guard": "actor owns resource and resource is cancellable"})
            elif operation == "payment":
                transitions.append({"from": "created", "action": f"{op['method']} {op['path']}", "to": "paid", "guard": "amount equals payable amount and state is payable"})
            elif operation == "refund":
                transitions.append({"from": "paid", "action": f"{op['method']} {op['path']}", "to": "refunded", "guard": "paid amount exists and refund amount <= paid amount"})
            elif operation == "callback":
                transitions.append({"from": "pending", "action": f"{op['method']} {op['path']}", "to": "confirmed", "guard": "trusted signature and idempotency key valid"})
        if transitions:
            machines.append({"resource": resource, "states": sorted({t["from"] for t in transitions} | {t["to"] for t in transitions}), "transitions": transitions})
    return machines


def infer_data_lineage(operations: list[dict], prd: str) -> list[dict]:
    lineage = []
    for op in operations:
        path = op["path"]
        resource = op["resource"]
        risks = set(op.get("risk_hints", []))
        if "money_consistency" in risks:
            lineage.append({"field_family": "money", "resource": resource, "producer": f"{op['method']} {path}", "consumers": downstream_consumers(operations, {"payment", "refund", "report"}), "invariant": "amount fields must reconcile across create/pay/refund/report"})
        if "quantity_consistency" in risks:
            lineage.append({"field_family": "quantity", "resource": resource, "producer": f"{op['method']} {path}", "consumers": downstream_consumers(operations, {"order", "product", "report"}), "invariant": "quantity-like fields cannot become negative or exceed capacity"})
        if "tenant_isolation" in risks:
            lineage.append({"field_family": "scope", "resource": resource, "producer": f"{op['method']} {path}", "consumers": downstream_consumers(operations, {"search", "report", "export", "tenant"}), "invariant": "tenant or organization scope must be preserved across reads, reports and exports"})
    return dedupe_lineage(lineage)


def downstream_consumers(operations: list[dict], keywords: set[str]) -> list[str]:
    consumers = []
    for op in operations:
        text = f"{op['resource']} {op['path']} {op['summary']}".lower()
        if any(k in text for k in keywords):
            consumers.append(f"{op['method']} {op['path']}")
    return sorted(set(consumers))


def dedupe_lineage(items: list[dict]) -> list[dict]:
    seen = set()
    result = []
    for item in items:
        key = (item["field_family"], item["resource"], item["producer"])
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def infer_entity_dependencies(operations: list[dict]) -> list[dict]:
    dependencies = []
    resources = sorted({op["resource"] for op in operations})
    for resource in resources:
        if resource in {"login", "reset", "health"}:
            continue
        create_ops = [op for op in operations if op["resource"] == resource and op["method"] == "POST"]
        read_ops = [op for op in operations if op["resource"] == resource and op["method"] == "GET"]
        if create_ops and read_ops:
            dependencies.append({"resource": resource, "dependency": "create_then_read", "setup": f"{create_ops[0]['method']} {create_ops[0]['path']}", "assert": f"{read_ops[0]['method']} {read_ops[0]['path']}"})
        action_ops = [op for op in operations if op["resource"] == resource and op["operation"] not in {"read", "create_or_action", "authenticate"}]
        for action in action_ops:
            setup_ref = (
                f"{create_ops[0]['method']} {create_ops[0]['path']}"
                if create_ops
                else "create_or_seed_resource"
            )
            assert_ref = (
                f"{read_ops[0]['method']} {read_ops[0]['path']}"
                if read_ops
                else "read_resource_and_related_objects"
            )
            dependencies.append({
                "resource": resource,
                "dependency": "state_action_requires_existing_resource",
                "setup": setup_ref,
                "action": f"{action['method']} {action['path']}",
                "assert": assert_ref,
            })
    return dependencies


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
    """Build cross-resource scenarios from OpenAPI role families — not mall path lists."""
    scenarios: list[dict] = []
    by_resource: dict[str, list[dict]] = {}
    for op in operations:
        by_resource.setdefault(str(op.get("resource") or ""), []).append(op)

    def _ops_for_family(family: set[str], *, methods: set[str] | None = None, limit: int = 3) -> list[dict]:
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


_INVARIANT_TEMPLATES: dict[str, str] = {
    "permission_bypass": "非授权角色不得执行 {method} {path}",
    "idor": "用户不得读取或变更不属于自己的 {resource} 资源",
    "tenant_isolation": "不同租户之间的 {resource} 数据必须隔离",
    "money_consistency": "{resource} 的金额、支付、退款和汇总字段必须一致",
    "quantity_consistency": "{resource} 的数量、库存或额度不能越界",
    "state_flow": "{resource} 状态流转必须满足前置状态和后置一致性",
    "idempotency": "{method} {path} 重复提交或回调必须幂等",
    "benefit_abuse": "{resource} 的优惠、权益或折扣必须校验归属、门槛和次数",
    "approval_bypass": "{resource} 审批/复核流程不能跳过必要节点",
    "audit_log_missing": "{resource} 的关键操作必须留下可追踪审计记录",
    "file_upload_validation": "{resource} 文件上传或导入必须校验格式、大小和内容",
    "bulk_operation_partial_failure": "{resource} 批量操作必须处理部分成功、部分失败和回滚",
    "export_permission": "{resource} 导出必须遵守角色、租户和字段权限",
    "report_aggregation_error": "{resource} 报表统计口径必须和明细数据一致",
    "search_scope_leak": "{resource} 搜索和筛选不能越权返回其他组织或用户数据",
    "notification_wrong_recipient": "{resource} 通知不能发送给错误用户或泄露模板变量",
    "feature_flag_scope": "{resource} 配置开关必须按租户、组织和角色隔离",
    "soft_delete_visibility": "{resource} 删除、禁用或归档后不能在普通查询中可见",
    "callback_trust": "{resource} 外部回调不能无签名、无状态校验地改变业务状态",
    "race_condition": "{resource} 并发操作不能造成重复处理、丢失更新或资源越界",
    "time_window_boundary": "{resource} 时间窗口、过期和边界日期必须被严格校验",
}


def invariant_statement(risk: str, op: dict, roles: list[str], tenants: list[str]) -> str:
    template = _INVARIANT_TEMPLATES.get(risk)
    if template:
        return template.format(
            method=op.get("method", ""), path=op.get("path", ""), resource=op.get("resource", "")
        )
    return f"{op['resource']} 必须满足业务不变量"


def keyword_hits(domain: str, path: str) -> bool:
    """Match domain keywords against path resource tokens (industry-agnostic)."""
    path_l = str(path or "").lower()
    mapping = {
        "permission": ["admin", "login", "role", "permission", "auth"],
        "idor": ["{", "owner", "me", "self", "patient", "account", "member", "order", "booking", "claim", "record"],
        "tenant": ["tenant", "org", "organization", "workspace"],
        "order": list(_FULFILLMENT_PARENT_LIKE),
        "stock": list(_INVENTORY_LIKE | _FULFILLMENT_PARENT_LIKE),
        "coupon": ["coupon", "voucher", "promo", "benefit", "discount"],
        "payment": list(_PAYMENT_LIKE),
        "refund": list(_REFUND_LIKE),
        "idempotency": list(_FULFILLMENT_PARENT_LIKE) + ["callback", "idempotency", "retry"],
        "money": list(_PAYMENT_LIKE | _REFUND_LIKE | _FULFILLMENT_PARENT_LIKE) + ["coupon", "voucher", "ledger"],
    }
    tokens = mapping.get(domain, [])
    return any(word in path_l for word in tokens)


def build_invariants(rules: list[dict]) -> list[dict]:
    templates = {
        "permission": "非授权角色不能访问管理或受保护资源",
        "idor": "用户只能访问和变更自己的业务对象",
        "tenant": "租户数据必须按 tenant_id 强隔离",
        "order": "履约单状态创建、取消、支付、退款必须可查询且一致",
        "stock": "容量/库存不能超卖，交易状态变化必须同步库存",
        "coupon": "优惠/权益必须校验归属、有效期、门槛和使用次数",
        "payment": "支付/结算金额必须等于应付金额且状态流转合法",
        "refund": "退款必须基于已支付单据且金额不能超过支付金额",
        "idempotency": "重复请求不能重复创建、扣减容量或入账",
        "money": "金额计算不能为负，不能出现履约单和支付不一致",
    }
    return [{"invariant_id": f"INV_{r['domain'].upper()}", "domain": r["domain"], "statement": templates[r["domain"]], "paths": r["paths"]} for r in rules]


KNOWLEDGE_RISK_ALIASES = {
    "risk_permission_bypass": "permission_bypass",
    "permission_bypass": "permission_bypass",
    "auth_bypass": "auth_bypass",
    "idor": "idor",
    "tenant_isolation": "tenant_isolation",
    "risk_data_consistency": "state_consistency",
    "data_consistency": "state_consistency",
    "state_consistency": "state_consistency",
    "risk_state_transition": "state_flow",
    "state_transition": "state_flow",
    "state_flow": "state_flow",
    "risk_boundary_validation": "boundary_validation",
    "boundary_validation": "boundary_validation",
    "risk_financial_rule": "money_consistency",
    "financial_rule": "money_consistency",
    "money_consistency": "money_consistency",
    "quantity_consistency": "quantity_consistency",
    "stock_consistency": "quantity_consistency",
    "coupon_abuse": "benefit_abuse",
    "benefit_abuse": "benefit_abuse",
    "approval_bypass": "approval_bypass",
    "audit_log_missing": "audit_log_missing",
    "search_scope_leak": "search_scope_leak",
    "report_aggregation_error": "report_aggregation_error",
    "file_upload_validation": "file_upload_validation",
    "notification_wrong_recipient": "notification_wrong_recipient",
    "feature_flag_scope": "feature_flag_scope",
    "callback_trust": "callback_trust",
    "race_condition": "race_condition",
    "idempotency": "idempotency",
}


def normalize_knowledge_risk(value: str) -> str:
    raw = "".join(ch.lower() if ch.isalnum() else "_" for ch in str(value or "").replace("RISK_", "risk_")).strip("_")
    while "__" in raw:
        raw = raw.replace("__", "_")
    return KNOWLEDGE_RISK_ALIASES.get(raw, raw)


_RISK_KEYWORD_MAP: dict[str, tuple[str, ...]] = {
    "permission_bypass": ("admin", "permission", "role", "unauthorized", "forbidden", "权限", "角色", "越权", "未授权"),
    "idor": ("owner", "ownership", "他人", "归属", "本人", "idor"),
    "tenant_isolation": ("tenant", "租户", "组织", "门店", "机构"),
    "money_consistency": ("amount", "total", "payment", "refund", "discount", "金额", "费用", "支付", "退款", "折扣"),
    "quantity_consistency": ("stock", "quantity", "quota", "inventory", "库存", "数量", "额度", "限额"),
    "state_flow": ("status", "state", "transition", "cancel", "approve", "状态", "流转", "取消", "审批"),
    "idempotency": ("idempot", "duplicate", "重复", "幂等"),
    "benefit_abuse": ("coupon", "benefit", "promotion", "优惠", "权益", "券"),
    "boundary_validation": ("required", "invalid", "empty", "range", "missing", "必填", "非法", "为空", "边界"),
    "audit_log_missing": ("audit", "log", "审计", "日志", "留痕"),
}


def risks_from_text(text: str) -> set[str]:
    lower = str(text or "").lower()
    risks: set[str] = set()
    for risk_type, keywords in _RISK_KEYWORD_MAP.items():
        if any(k in lower for k in keywords):
            risks.add(risk_type)
    return risks


def operation_matches_any_knowledge_risk(op: dict, risks: set[str]) -> bool:
    method = op.get("method")
    hints = set(op.get("risk_hints", []))
    text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
    if hints & risks:
        return True
    if {"money_consistency", "benefit_abuse"} & risks and any(k in text for k in ["pay", "amount", "total", "refund", "coupon", "discount"]):
        return True
    if {"quantity_consistency"} & risks and any(k in text for k in ["stock", "quantity", "product", "inventory"]):
        return True
    if {"state_flow", "state_consistency"} & risks and any(k in text for k in ["status", "state", "cancel", "approve", "refund", "payment", "order", "状态", "流转", "审批", "取消"]):
        return True
    if {"permission_bypass", "auth_bypass"} & risks and any(k in text for k in ["admin", "permission", "role"]):
        return True
    if {"idor", "tenant_isolation"} & risks and ("{" in op.get("path", "") or "tenant" in text):
        return True
    if {"boundary_validation", "audit_log_missing"} & risks and method in {"POST", "PUT", "PATCH", "DELETE"}:
        return True
    if {"idempotency"} & risks and method in {"POST", "PUT", "PATCH", "DELETE"} and "login" not in text:
        return True
    return False


def normalize_discovery_mode(mode: str | None) -> str:
    normalized = (mode or "blind").strip().lower()
    if normalized in {"demo", "demo_mode", "compat"}:
        return "demo"
    return "blind"


def find_oracle_fallback_operation(operations: list[dict], risk_type: str) -> dict | None:
    risk = str(risk_type or "")
    ranked_patterns = {
        "coupon_abuse": ["apply-coupon", "coupon", "benefit", "promotion", "discount"],
        "money_consistency": ["payment", "pay", "refund", "apply-coupon", "amount", "total"],
        "refund_abuse": ["refund"],
        "payment_callback": ["callback", "payment"],
        "stock_consistency": ["stock", "inventory", "product", "order"],
        "state_flow": ["status", "state", "cancel", "approve", "order", "refund"],
        "state_consistency": ["status", "state", "cancel", "approve", "order", "refund", "payment"],
        "idempotency": ["order", "payment", "refund", "callback"],
    }.get(risk, [])
    best: tuple[int, dict] | None = None
    for op in operations:
        text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
        score = 0
        for index, pattern in enumerate(ranked_patterns):
            if pattern in text:
                score = max(score, len(ranked_patterns) - index)
        if risk in {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}:
            score += 5
        if score <= 0:
            continue
        if best is None or score > best[0]:
            best = (score, op)
    return best[1] if best else None


def _attach_source_request_contracts(probes: list[dict], business_model: dict) -> list[dict]:
    operations = {
        (
            str(op.get("method") or "").upper(),
            str(op.get("path") or "").split("?", 1)[0],
        ): op
        for op in business_model.get("operations", [])
        if isinstance(op, dict)
    }
    bound: list[dict] = []
    for probe_item in probes:
        item = dict(probe_item)
        key = (
            str(item.get("method") or "").upper(),
            str(item.get("path") or "").split("?", 1)[0],
        )
        operation = operations.get(key) or {}
        example = operation.get("request_example")
        schema = operation.get("request_schema")
        if isinstance(example, dict) and example:
            item["request_example"] = dict(example)
            item["request_contract_source"] = "openapi_documented_example"
        if isinstance(schema, dict) and schema:
            item["request_schema"] = dict(schema)
        bound.append(item)
    return bound


def generate_defect_probes(invariants: list[dict], business_model: dict | None = None, discovery_mode: str = "blind") -> list[dict]:
    model = business_model or {}
    mode = normalize_discovery_mode(discovery_mode)
    policy_profile = normalize_probe_policy_profile(os.environ.get("PROBE_POLICY_PROFILE"), mode)
    generic = generate_generic_defect_probes(model)
    pattern_library = generate_high_value_pattern_probes(model)
    business_knowledge = generate_business_knowledge_probes(model)
    business_adaptation = generate_business_adaptation_probes(model)
    high_value_memory = generate_high_value_memory_probes(model)
    risk_learning_profile = generate_risk_learning_profile_probes(model)
    high_value_attack_plan = generate_high_value_attack_plan_probes(model)
    capability_gap = generate_capability_gap_probes(model, [*business_knowledge, *business_adaptation, *high_value_memory, *risk_learning_profile, *high_value_attack_plan])
    oracle_gap = generate_oracle_gap_probes(model, [*business_knowledge, *business_adaptation, *high_value_memory, *risk_learning_profile, *high_value_attack_plan, *capability_gap])
    feedback_learning = generate_feedback_learning_probes(model)
    adaptive_policy = generate_adaptive_policy_probes(model)
    feedback_adjusted = generate_feedback_adjusted_policy_probes(model)
    rag_enhanced = generate_rag_enhanced_probes(model, prd=model.get("_source_prd", ""))
    journeys = generate_journey_defect_probes(model)
    combined = [*generic, *pattern_library, *business_knowledge, *business_adaptation, *high_value_memory, *risk_learning_profile, *high_value_attack_plan, *capability_gap, *oracle_gap, *feedback_learning, *adaptive_policy, *feedback_adjusted, *rag_enhanced, *journeys]
    combined = _attach_source_request_contracts(combined, model)
    combined = upgrade_high_value_oracle_probes(combined, model)
    combined = filter_probes_by_policy(combined, policy_profile, mode)
    seen = set()
    probes = []
    for item in combined:
        key = item["probe_id"]
        if key in seen:
            continue
        seen.add(key)
        probes.append(item)
    return enrich_probe_business_context(apply_probe_budget_policy(probes), model)


def inferred_business_context_for_probe(item: dict, business_model: dict) -> dict:
    api_template = str(item.get("api_template") or f"{item.get('method', '')} {item.get('path', '')}".strip())
    operations = business_model.get("operations", []) or []
    op = find_operation_by_api_template(operations, api_template)
    if not op and item.get("path"):
        op = find_operation_by_api_template(operations, f"{item.get('method')} {str(item.get('path')).split('?')[0]}")
    risk = str(item.get("risk_type") or "business_risk")
    domain, domain_label = business_risk_domain_for(risk)
    module = str((op or {}).get("resource") or business_object_for_api(api_template) or "business")
    operation = operation_for_method(str(item.get("method") or (op or {}).get("method") or ""), str(item.get("path") or (op or {}).get("path") or api_template))
    expected = knowledge_expected_statement(risk, op or {"resource": module, "method": item.get("method"), "path": item.get("path")})
    return {
        "knowledge_source": "auto_semantic_business_context",
        "industry": business_model.get("industry") or "generic_enterprise_software",
        "module": module,
        "domains": [module, domain],
        "risk_domain": domain,
        "risk_domain_label": domain_label,
        "business_object": business_object_for_api(api_template),
        "operation": operation,
        "business_rule": expected,
        "why_high_value": risk_business_impact_statement(risk),
        "api_template": api_template,
        "source_probe": item.get("source"),
    }


def risk_business_impact_statement(risk_type: str) -> str:
    risk = str(risk_type or "")
    mapping = {
        "permission_bypass": "权限绕过会导致非授权用户访问或修改关键业务数据。",
        "auth_bypass": "未登录访问会绕过身份边界，影响所有受保护业务流程。",
        "idor": "对象级越权会导致跨用户读取或操作订单、资产和业务对象。",
        "tenant_isolation": "跨租户隔离失败会造成企业客户数据泄露和合规风险。",
        "money_consistency": "金额不一致会直接造成资损、错账、退款和对账失败。",
        "stock_consistency": "库存容量不一致会造成超卖、额度透支和履约失败。",
        "state_flow": "非法状态流转会破坏订单、支付、审批等核心业务生命周期。",
        "state_consistency": "动作后状态不一致会导致前后台、异步任务和下游系统判断错误。",
        "coupon_abuse": "权益优惠滥用会造成重复抵扣、超额优惠和营销资损。",
        "idempotency": "幂等缺失会导致重复下单、重复扣款、重复退款或重复回调处理。",
        "refund_abuse": "退款滥用会造成超额退款、重复退款和跨单退款。",
        "payment_callback": "支付回调可信校验缺失会造成伪造支付、重复入账或状态污染。",
    }
    return mapping.get(risk, "该风险会破坏 PRD/OpenAPI 中隐含的业务规则和数据一致性。")


def enrich_probe_business_context(probes: list[dict], business_model: dict) -> list[dict]:
    enriched = []
    eligible_sources = {
        "generic_auto",
        "pattern_library",
        "feedback_learning",
        "feedback_adjusted",
        "adaptive_policy",
        "rag_enhanced",
        "high_value_memory",
        "business_knowledge",
        "business_adaptation_layer",
        "risk_learning_profile",
        "high_value_attack_plan",
        "capability_gap",
        "oracle_gap",
    }
    for item in probes:
        if item.get("business_context") or item.get("source") not in eligible_sources:
            enriched.append(item)
            continue
        next_item = dict(item)
        next_item["business_context"] = inferred_business_context_for_probe(next_item, business_model)
        evidence = list(next_item.get("evidence_required") or [])
        if "business_context" not in evidence:
            next_item["evidence_required"] = ["business_context", *evidence]
        enriched.append(next_item)
    return enriched


def upgrade_high_value_oracle_probes(probes: list[dict], business_model: dict) -> list[dict]:
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    eligible_sources = {
        "business_knowledge",
        "business_adaptation_layer",
        "risk_learning_profile",
        "high_value_attack_plan",
        "capability_gap",
        "oracle_gap",
        "high_value_memory",
        "pattern_library",
        "feedback_learning",
        "adaptive_policy",
        "feedback_adjusted",
        "rag_enhanced",
    }
    upgraded: list[dict] = []
    for item in probes:
        risk = str(item.get("risk_type") or "")
        if item.get("cross_step_oracle") or risk not in ORACLE_REQUIRED_RISKS or item.get("source") not in eligible_sources:
            upgraded.append(item)
            continue
        op = find_operation_by_api_template(operations, item.get("api_template") or f"{item.get('method')} {item.get('path')}")
        if not op:
            upgraded.append(item)
            continue
        steps = build_business_knowledge_steps(risk, op, operations)
        fallback_op = None
        if not steps:
            fallback_op = find_oracle_fallback_operation(operations, risk)
            if fallback_op:
                steps = build_business_knowledge_steps(risk, fallback_op, operations)
        if not steps:
            upgraded.append(item)
            continue
        oracle_op = fallback_op or op
        next_item = dict(item)
        next_item["steps"] = steps
        next_item["probe_type"] = f"{item.get('probe_type', 'probe')}_journey"
        if fallback_op:
            next_item["method"] = oracle_op.get("method")
            next_item["path"] = concrete_path(oracle_op.get("path", ""))
            next_item["api_template"] = operation_api_template(oracle_op)
            next_item["expected_status"] = expected_status_for_knowledge_risk(risk, oracle_op.get("method", "GET"))
        next_item["cross_step_oracle"] = build_cross_step_oracle(risk, "high_value_oracle_upgrade")
        next_item["evidence_required"] = ["actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        next_item["oracle_upgrade_context"] = {
            "source": item.get("source"),
            "reason": "高价值探针自动升级为跨步骤业务 Oracle，避免只依赖单接口状态码",
            "api_template": next_item.get("api_template"),
            "original_api_template": item.get("api_template"),
            "used_semantic_fallback": bool(fallback_op),
            "risk_type": risk,
        }
        upgraded.append(next_item)
    return upgraded


def apply_probe_budget_policy(probes: list[dict]) -> list[dict]:
    """Optionally reduce probes by a learned ROI budget policy.

    This is Phase10 governance: it is driven by workspace/output evaluator
    feedback, not by private ground truth or enabled bug sets. It is disabled by
    default and only activates when PROBE_EXECUTION_BUDGET or
    PROBE_BUDGET_POLICY_PATH is provided.
    """
    raw_budget = os.environ.get("PROBE_EXECUTION_BUDGET", "").strip()
    raw_policy_path = os.environ.get("PROBE_BUDGET_POLICY_PATH", "").strip()
    policy_path = Path(raw_policy_path or "platform_workspace/enterprise_shop/defect_discovery/budgeted_probe_policy.json")
    budget = 0
    try:
        budget = int(raw_budget) if raw_budget else 0
    except Exception:
        budget = 0
    if not raw_budget and not raw_policy_path:
        return probes
    policy = {}
    if policy_path.exists():
        try:
            policy = json.loads(policy_path.read_text(encoding="utf-8"))
        except Exception:
            policy = {}
    selected_ids = set(str(x) for x in policy.get("selected_probe_ids", []) if x)
    scores = {str(k): float(v) for k, v in (policy.get("score_by_probe_id", {}) or {}).items()}
    blocked_sources = set(policy.get("blocked_sources", []))
    filtered = [p for p in probes if p.get("source") not in blocked_sources]
    if selected_ids:
        selected = [p for p in filtered if str(p.get("probe_id")) in selected_ids]
        # Include mandatory P0 pattern probes if the policy was generated before
        # a new endpoint appeared, but still respect the top-level budget below.
        mandatory = [p for p in filtered if p.get("severity") in {"P0", "P1"} and p.get("source") in {"pattern_library", "business_knowledge", "business_adaptation_layer", "high_value_memory", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "feedback_learning", "feedback_adjusted", "rag_enhanced"}]
        by_id = {p["probe_id"]: p for p in [*selected, *mandatory]}
        filtered = list(by_id.values())
    if budget <= 0:
        budget = int(policy.get("budget") or 0)
    if budget <= 0:
        return filtered

    def rank(p: dict) -> tuple:
        pid = str(p.get("probe_id"))
        source_rank = {"business_knowledge": 13, "business_adaptation_layer": 12, "risk_learning_profile": 11, "high_value_attack_plan": 10, "capability_gap": 9, "oracle_gap": 8, "high_value_memory": 7, "pattern_library": 6, "feedback_adjusted": 5, "feedback_learning": 4, "adaptive_policy": 3, "rag_enhanced": 3, "journey_auto": 1, "generic_auto": 0}.get(p.get("source"), 0)
        severity_rank = {"P0": 3, "P1": 2, "P2": 1}.get(p.get("severity"), 0)
        return (scores.get(pid, 0.0), severity_rank, source_rank, pid)

    return sorted(filtered, key=rank, reverse=True)[:budget]


def generate_high_value_pattern_probes(business_model: dict) -> list[dict]:
    """Generate blind-mode probes from public OpenAPI/PRD semantics.

    Patterns activate only when matching endpoints exist. Paths and titles are
    derived from the real operations, never from a benchmark endpoint map.
    """
    operations = [op for op in (business_model.get("operations") or []) if isinstance(op, dict)]
    probes: list[dict] = []
    seen: set[str] = set()

    def add(
        probe_id: str,
        title: str,
        probe_type: str,
        risk_type: str,
        severity: str,
        actor: str,
        method: str,
        path: str,
        expected_status: int,
        api_template: str | None = None,
    ) -> None:
        if probe_id in seen:
            return
        seen.add(probe_id)
        probes.append(
            probe(
                probe_id,
                title,
                probe_type,
                risk_type,
                severity,
                actor,
                method,
                path,
                expected_status,
                api_template or f"{method} {str(path).split('?')[0]}",
                source="pattern_library",
            )
        )

    def _slug(path: str) -> str:
        cleaned = re.sub(r"[^a-zA-Z0-9]+", "_", str(path or "").strip("/")).strip("_").upper()
        return (cleaned or "RESOURCE")[:48]

    def _text(op: dict) -> str:
        return f"{op.get('path') or ''} {op.get('summary') or ''} {op.get('resource') or ''}".lower()

    login_ops = [
        op for op in operations
        if str(op.get("method") or "").upper() == "POST"
        and any(tok in str(op.get("path") or "").lower() for tok in ("/login", "/auth/login", "/signin", "/sign-in"))
    ]
    for op in login_ops[:1]:
        path = str(op.get("path"))
        pid = f"PATTERN_LOCKED_ACCOUNT_LOGIN_{_slug(path)}"
        add(pid, "锁定账号不得登录", "account_state_probe", "locked_account_bypass", "P1", "locked_user", "POST", path, 403, f"POST {path}")

    for op in operations:
        method = str(op.get("method") or "").upper()
        path = str(op.get("path") or "")
        if not path or path in {"/reset", "/health", "/openapi.json"}:
            continue
        text = _text(op)
        template = f"{method} {path}"
        slug = _slug(path)
        resource = str(op.get("resource") or path.strip("/").split("/")[0] or "resource")
        is_admin = any(tok in path.lower() for tok in ("/admin/", "/manage/", "/manager/", "/console/"))
        has_path_param = "{" in path and "}" in path
        is_collection_post = method == "POST" and not has_path_param
        is_terminal_action = method in {"POST", "PUT", "PATCH"} and any(
            tok in path.lower() for tok in ("/cancel", "/close", "/void", "/revoke", "/reject", "/withdraw")
        )
        is_benefit = method in {"POST", "PUT", "PATCH"} and any(
            tok in text for tok in ("coupon", "voucher", "promo", "promotion", "benefit", "discount", "补贴", "优惠", "券")
        )
        is_payment = method == "POST" and any(tok in text for tok in ("payment", "pay", "settlement", "charge", "支付", "结算"))
        is_callback = is_payment and any(tok in text for tok in ("callback", "webhook", "notify", "回调", "通知"))
        is_refund = method == "POST" and any(tok in text for tok in ("refund", "chargeback", "退款"))
        is_capacity = is_collection_post and any(
            tok in text
            for tok in (
                "order", "booking", "reservation", "enrollment", "appointment", "inventory",
                "stock", "seat", "capacity", "订单", "预约", "选课", "库存", "名额",
            )
        )
        is_tenant_surface = "tenant" in path.lower() or "tenant" in text

        if is_admin and method == "GET":
            pid_user = f"PATTERN_ADMIN_READ_FORBIDDEN_TO_USER_{slug}"
            pid_anon = f"PATTERN_ADMIN_READ_FORBIDDEN_TO_ANON_{slug}"
            add(pid_user, f"普通用户不得读取管理端资源 {path}", "permission_probe", "permission_bypass", "P0", "normal_user", method, path, 403, template)
            add(pid_anon, f"未登录用户不得读取管理端资源 {path}", "auth_probe", "auth_bypass", "P0", "anonymous", method, path, 401, template)
        if is_admin and method in {"POST", "PUT", "PATCH", "DELETE"}:
            pid_user = f"PATTERN_ADMIN_WRITE_FORBIDDEN_TO_USER_{slug}"
            pid_anon = f"PATTERN_ADMIN_WRITE_FORBIDDEN_TO_ANON_{slug}"
            add(pid_user, f"普通用户不得修改管理端资源 {path}", "permission_probe", "privilege_escalation", "P0", "normal_user", method, path, 403, template)
            add(pid_anon, f"未登录用户不得修改管理端资源 {path}", "auth_probe", "auth_bypass", "P0", "anonymous", method, path, 401, template)

        if has_path_param and method == "GET" and not is_admin:
            pid = f"PATTERN_IDOR_READ_{slug}"
            add(pid, f"用户不得查看他人{resource}", "idor_probe", "idor", "P0", "normal_user", method, path, 403, template)

        if is_terminal_action and not is_admin:
            pid_idor = f"PATTERN_IDOR_ACTION_{slug}"
            pid_state = f"PATTERN_STATE_ACTION_{slug}"
            add(pid_idor, f"用户不得越权执行 {path}", "idor_probe", "idor", "P1", "normal_user", method, path, 403, template)
            add(pid_state, f"终止/取消后状态必须一致 {path}", "state_transition_probe", "state_flow", "P1", "normal_user", method, path, 200, template)

        if is_tenant_surface and method == "GET":
            tenant_path = path if "tenant_id=" in path else (f"{path}{'&' if '?' in path else '?'}tenant_id=tenant_b")
            pid = f"PATTERN_TENANT_ISOLATION_{slug}"
            add(pid, f"租户用户不得读取其他租户数据 {path}", "tenant_probe", "tenant_isolation", "P0", "normal_user", method, tenant_path, 403, template)

        if is_capacity:
            pid_over = f"PATTERN_CAPACITY_OVERSELL_{slug}"
            pid_deduct = f"PATTERN_CAPACITY_DEDUCT_{slug}"
            pid_read = f"PATTERN_CREATE_READ_{slug}"
            pid_idem = f"PATTERN_CREATE_IDEMPOTENCY_{slug}"
            pid_tamper = f"PATTERN_CREATE_OWNER_TAMPER_{slug}"
            add(pid_over, f"容量/库存不足时不能创建 {path}", "stock_probe", "stock_consistency", "P0", "normal_user", method, path, 409, template)
            add(pid_deduct, f"创建成功后容量/库存必须扣减 {path}", "stock_probe", "stock_consistency", "P1", "normal_user", method, path, 200, template)
            add(pid_read, f"创建后必须可查询 {path}", "state_consistency_probe", "state_consistency", "P1", "normal_user", method, path, 200, template)
            add(pid_idem, f"同一幂等键不能重复创建 {path}", "idempotency_probe", "idempotency", "P1", "normal_user", method, path, 200, template)
            add(pid_tamper, f"创建时不能篡改租户或归属 {path}", "idor_probe", "idor", "P1", "normal_user", method, path, 403, template)

        if is_benefit:
            add(f"PATTERN_BENEFIT_REUSE_{slug}", "同一权益/优惠不能重复抵扣", "coupon_probe", "coupon_abuse", "P1", "normal_user", method, path, 409, template)
            add(f"PATTERN_BENEFIT_EXPIRED_{slug}", "过期权益/优惠不能使用", "coupon_probe", "coupon_abuse", "P1", "normal_user", method, path, 400, template)
            add(f"PATTERN_BENEFIT_THRESHOLD_{slug}", "不满足门槛不能使用权益/优惠", "coupon_probe", "coupon_abuse", "P1", "normal_user", method, path, 400, template)
            add(f"PATTERN_BENEFIT_OTHER_USER_{slug}", "用户不能使用他人权益/优惠", "coupon_probe", "coupon_abuse", "P0", "normal_user", method, path, 403, template)
            add(f"PATTERN_BENEFIT_OVER_DISCOUNT_{slug}", "优惠金额不能超过应付金额", "money_probe", "money_consistency", "P0", "normal_user", method, path, 400, template)

        if is_payment and not is_callback:
            add(f"PATTERN_PAYMENT_AMOUNT_MATCH_{slug}", "支付/结算金额必须匹配应付金额", "payment_probe", "money_consistency", "P0", "normal_user", method, path, 409, template)
            add(f"PATTERN_PAYMENT_STATUS_UPDATE_{slug}", "支付成功后业务状态必须更新", "payment_probe", "state_consistency", "P1", "normal_user", method, path, 200, template)
            add(f"PATTERN_PAYMENT_TERMINAL_STATE_{slug}", "已终止业务单不能再支付/结算", "payment_probe", "state_flow", "P0", "normal_user", method, path, 409, template)

        if is_callback:
            add(f"PATTERN_CALLBACK_IDEMPOTENT_{slug}", "支付/结算回调必须幂等", "payment_callback_probe", "idempotency", "P1", "system", method, path, 200, template)
            add(f"PATTERN_CALLBACK_AMOUNT_{slug}", "回调金额必须匹配应付金额", "payment_callback_probe", "money_consistency", "P0", "system", method, path, 409, template)
            add(f"PATTERN_CALLBACK_TERMINAL_STATE_{slug}", "已终止业务单不能被回调改为已支付", "payment_callback_probe", "state_flow", "P0", "system", method, path, 409, template)

        if is_refund:
            add(f"PATTERN_REFUND_DUPLICATE_{slug}", "同一业务单不能重复退款", "refund_probe", "refund_abuse", "P1", "normal_user", method, path, 409, template)
            add(f"PATTERN_REFUND_UNPAID_{slug}", "未支付业务单不能退款", "refund_probe", "refund_abuse", "P1", "normal_user", method, path, 409, template)
            add(f"PATTERN_REFUND_OVER_AMOUNT_{slug}", "退款金额不能超过已支付金额", "refund_probe", "money_consistency", "P0", "normal_user", method, path, 409, template)
            add(f"PATTERN_REFUND_STATE_{slug}", "退款后状态与库存/额度必须一致", "refund_probe", "state_consistency", "P1", "normal_user", method, path, 200, template)

    return probes


def generate_business_knowledge_probes(business_model: dict) -> list[dict]:
    knowledge = business_model.get("business_knowledge_model") or {}
    if not knowledge:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    if not operations:
        return []
    risk_items = collect_knowledge_risk_items(knowledge)
    module_by_domain = build_module_context_by_domain(knowledge)
    probes: list[dict] = []
    seq = 1
    for op in operations:
        op_risks = set(op.get("risk_hints", [])) | risks_from_text(f"{op.get('path')} {op.get('summary')} {op.get('resource')}")
        matched = [item for item in risk_items if item["risk_type"] in op_risks or operation_matches_any_knowledge_risk(op, {item["risk_type"]})]
        if not matched:
            continue
        for item in matched[:4]:
            if not risk_operation_compatible(item["risk_type"], op):
                continue
            if op["method"] == "GET" and item["risk_type"] in {"idempotency", "boundary_validation"}:
                continue
            probe_item = probe(
                f"BK_{item['risk_type'].upper()}_{seq:03d}",
                knowledge_probe_title(item["risk_type"], op, item),
                "business_knowledge_probe",
                executable_risk_type(item["risk_type"]),
                knowledge_probe_severity(item["risk_type"], item.get("priority")),
                knowledge_probe_actor(item["risk_type"]),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(item["risk_type"], op["method"]),
                f"{op['method']} {op['path']}",
                source="business_knowledge",
            )
            domains = set(item.get("domains", []))
            domains.add(op.get("resource", ""))
            probe_item["business_context"] = {
                "knowledge_source": knowledge.get("_source_path", ""),
                "module": choose_module_for_operation(op, module_by_domain),
                "rule_ids": item.get("rule_ids", [])[:5],
                "risk_ids": item.get("risk_ids", [])[:5],
                "scenario_ids": item.get("scenario_ids", [])[:5],
                "evidence": item.get("evidence", [])[:5],
                "domains": sorted(x for x in domains if x),
                "why_high_value": why_high_value(item["risk_type"]),
            }
            probe_item["expected"] = knowledge_expected_statement(item["risk_type"], op)
            probe_item["bug_signal"] = knowledge_bug_signal(item["risk_type"])
            knowledge_steps = build_business_knowledge_steps(item["risk_type"], op, operations)
            if knowledge_steps:
                probe_item["steps"] = knowledge_steps
                probe_item["probe_type"] = "business_knowledge_journey_probe"
                probe_item["evidence_required"] = ["business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(probe_item)
            seq += 1
            if len(probes) >= 96:
                return probes
    return probes


def parse_api_template(api: str) -> tuple[str, str]:
    parts = str(api or "").strip().split(" ", 1)
    if len(parts) == 2 and parts[0].upper() in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
        return parts[0].upper(), parts[1].strip()
    return "", str(api or "").strip()


def find_operation_by_api_template(operations: list[dict], api_template: str) -> dict | None:
    method, path = parse_api_template(api_template)
    if not method or not path:
        return None
    for op in operations:
        if op.get("method") == method and op.get("path") == path:
            return op
    for op in operations:
        if op.get("method") == method and concrete_path(op.get("path", "")) == concrete_path(path):
            return op
    return None


def operation_api_template(op: dict) -> str:
    return f"{op.get('method')} {op.get('path')}"


def memory_risk_aliases(risk_type: str) -> set[str]:
    return {
        "stock_consistency": {"stock_consistency", "quantity_consistency", "inventory_consistency", "quota_limit"},
        "coupon_abuse": {"coupon_abuse", "benefit_abuse", "money_consistency"},
        "refund_abuse": {"refund_abuse", "money_consistency", "state_flow", "state_consistency"},
        "payment_callback": {"payment_callback", "money_consistency", "idempotency", "callback_trust"},
        "state_consistency": {"state_consistency", "state_flow"},
    }.get(risk_type, {risk_type})


def related_resources_for_memory_pattern(business_model: dict, pattern: dict) -> set[str]:
    base = str(pattern.get("business_object") or "").lower()
    related = {base} if base else set()
    for edge in business_model.get("semantic_graph", {}).get("edges", []) or []:
        src = str(edge.get("from") or "").lower()
        dst = str(edge.get("to") or "").lower()
        if src == base and dst:
            related.add(dst)
        if dst == base and src:
            related.add(src)
    for lineage in business_model.get("data_lineage", []) or []:
        producer = str(lineage.get("producer") or "")
        consumers = [str(x) for x in lineage.get("consumers", []) or []]
        if base and (base == str(lineage.get("resource") or "").lower() or base in producer.lower() or any(base in c.lower() for c in consumers)):
            related.add(str(lineage.get("resource") or "").lower())
            for api in consumers:
                _, path = parse_api_template(api)
                if path:
                    related.add(resource_name(path).lower())
    return {item for item in related if item}


def score_memory_related_operation(op: dict, pattern: dict, related_resources: set[str]) -> int:
    risk = str(pattern.get("risk_type") or "")
    aliases = memory_risk_aliases(risk)
    op_risks = set(op.get("risk_hints", []))
    text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
    score = 0
    if str(op.get("resource") or "").lower() in related_resources:
        score += 4
    if op_risks & aliases:
        score += 5
    if operation_matches_any_knowledge_risk(op, aliases):
        score += 3
    if risk in {"money_consistency", "refund_abuse", "payment_callback"} and any(k in text for k in ["payment", "pay", "refund", "coupon", "amount", "total"]):
        score += 3
    if risk in {"state_flow", "state_consistency"} and any(k in text for k in ["order", "status", "state", "cancel", "payment", "refund"]):
        score += 3
    if risk in {"idor", "permission_bypass", "tenant_isolation", "auth_bypass"} and any(k in text for k in ["admin", "tenant", "{", "owner", "permission"]):
        score += 3
    if risk in {"stock_consistency", "quantity_consistency"} and any(k in text for k in ["stock", "quantity", "product", "inventory", "order"]):
        score += 3
    if op.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
        score += 1
    return score


def related_operations_for_memory_pattern(business_model: dict, operations: list[dict], pattern: dict, exact_op: dict | None) -> list[dict]:
    related_resources = related_resources_for_memory_pattern(business_model, pattern)
    exact_template = operation_api_template(exact_op) if exact_op else ""
    ranked: list[tuple[int, str, dict]] = []
    for op in operations:
        template = operation_api_template(op)
        if template == exact_template:
            continue
        score = score_memory_related_operation(op, pattern, related_resources)
        if score >= 5:
            ranked.append((score, template, op))
    ranked.sort(key=lambda item: (item[0], item[1]), reverse=True)
    return [op for _, _, op in ranked[:3]]


def generate_high_value_memory_probes(business_model: dict) -> list[dict]:
    memory = business_model.get("high_value_pattern_memory") or {}
    patterns = memory.get("top_patterns") or []
    if not patterns:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    probes: list[dict] = []
    seq = 1
    seen: set[tuple[str, str, str]] = set()
    for pattern in patterns[:24]:
        api_template = pattern.get("api_template") or pattern.get("affected_api") or ""
        exact_op = find_operation_by_api_template(operations, api_template)
        candidate_ops = ([exact_op] if exact_op else []) + related_operations_for_memory_pattern(business_model, operations, pattern, exact_op)
        if not candidate_ops:
            continue
        risk_type = executable_risk_type(str(pattern.get("risk_type") or "boundary_validation"))
        for idx, op in enumerate(candidate_ops):
            variant = "exact_replay" if idx == 0 and exact_op and operation_api_template(op) == operation_api_template(exact_op) else "semantic_expansion"
            key = (str(pattern.get("pattern_id")), operation_api_template(op), risk_type)
            if key in seen:
                continue
            seen.add(key)
            probe_item = probe(
                f"HVM_{risk_type.upper()}_{seq:03d}",
                f"{'历史高价值缺陷模式复测' if variant == 'exact_replay' else '历史高价值缺陷模式扩散'}：{pattern.get('title') or risk_type}",
                "high_value_memory_probe",
                risk_type,
                "P0" if pattern.get("value_tier") == "S" else "P1",
                knowledge_probe_actor(risk_type),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(risk_type, op["method"]),
                f"{op['method']} {op['path']}",
                source="high_value_memory",
            )
            reasons = [str(x) for x in pattern.get("reasons", []) if x]
            probe_item["memory_context"] = {
                "pattern_id": pattern.get("pattern_id"),
                "memory_variant": variant,
                "previous_api": api_template,
                "expanded_api": f"{op['method']} {op['path']}",
                "previous_value_tier": pattern.get("value_tier"),
                "previous_bug_value_score": pattern.get("bug_value_score"),
                "previous_probe_source": pattern.get("probe_source"),
                "reasons": reasons[:5],
            }
            probe_item["business_context"] = {
                "knowledge_source": memory.get("source", "discovered_high_value_findings"),
                "module": pattern.get("business_object") or op.get("resource") or "历史高价值缺陷模式",
                "domains": sorted(set(x for x in [pattern.get("business_object"), op.get("resource")] if x)),
                "why_high_value": ("历史 S/A 级缺陷模式复测：" if variant == "exact_replay" else "历史 S/A 级缺陷模式语义扩散：") + ("；".join(reasons[:3]) if reasons else str(pattern.get("risk_type") or "")),
            }
            probe_item["expected"] = f"历史高价值风险 {risk_type} 不应在 {op['method']} {op['path']} 或相邻业务链路再次出现"
            probe_item["bug_signal"] = "历史高价值缺陷模式再次命中" if variant == "exact_replay" else "历史高价值缺陷模式在相邻业务链路扩散命中"
            memory_steps = build_business_knowledge_steps(risk_type, op)
            if memory_steps:
                probe_item["steps"] = memory_steps
                probe_item["probe_type"] = "high_value_memory_journey_probe"
                probe_item["cross_step_oracle"] = build_cross_step_oracle(risk_type, variant)
                probe_item["evidence_required"] = ["memory_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(probe_item)
            seq += 1
            if len(probes) >= 72:
                return probes
    return probes


def build_cross_step_oracle(risk_type: str, variant: str = "exact_replay") -> dict:
    assertions = {
        "money_consistency": ["reject_mismatched_amount", "paid_amount_must_match_order_total", "refund_amount_must_not_exceed_paid_amount"],
        "stock_consistency": ["reject_quantity_boundary", "stock_must_not_go_negative", "stock_delta_must_match_order_quantity"],
        "state_flow": ["terminal_state_must_not_be_reopened", "cancelled_order_must_not_be_paid", "state_action_must_persist"],
        "state_consistency": ["state_action_must_persist", "query_state_must_match_action_result"],
        "idempotency": ["duplicate_submit_must_not_create_new_resource", "duplicate_callback_must_not_double_count_amount"],
        "coupon_abuse": ["benefit_amount_must_not_exceed_payable_amount", "duplicate_benefit_must_be_rejected"],
        "refund_abuse": ["refund_amount_must_not_exceed_paid_amount", "duplicate_refund_must_be_rejected"],
        "tenant_isolation": ["cross_scope_read_must_be_rejected", "cross_scope_write_must_be_rejected"],
        "idor": ["cross_scope_read_must_be_rejected", "cross_scope_write_must_be_rejected"],
    }.get(risk_type, ["business_invariant_must_hold"])
    return {
        "version": "cross_step_oracle_v1",
        "risk_type": risk_type,
        "variant": variant,
        "assertions": assertions,
    }


def profile_item_names(items: list[dict]) -> list[str]:
    return [str(item.get("name") or "") for item in items if item.get("name")]


def risk_learning_profile_risk_for_operation(op: dict, priority_risks: list[str]) -> str | None:
    op_risks = set(op.get("risk_hints", []))
    for risk in priority_risks:
        aliases = memory_risk_aliases(executable_risk_type(risk))
        if risk in op_risks or op_risks & aliases or operation_matches_any_knowledge_risk(op, aliases):
            return executable_risk_type(risk)
    return None


def generate_risk_learning_profile_probes(business_model: dict) -> list[dict]:
    profile = business_model.get("risk_learning_profile") or {}
    if not profile:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    priority_risks = profile_item_names(profile.get("priority_risks") or [])
    priority_apis = profile_item_names(profile.get("priority_apis") or [])
    priority_modules = set(profile_item_names(profile.get("priority_modules") or []))
    oracle_risks = set(profile_item_names(profile.get("oracle_priority_risks") or []))
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()

    def add_probe(op: dict, risk_type: str, reason: str) -> None:
        key = (operation_api_template(op), risk_type)
        if key in seen:
            return
        seen.add(key)
        seq = len(probes) + 1
        severity = "P0" if risk_type in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation", "money_consistency", "stock_consistency"} else "P1"
        item = probe(
            f"RLP_{risk_type.upper()}_{seq:03d}",
            f"风险学习画像驱动探针：{risk_type} {op['method']} {op['path']}",
            "risk_learning_profile_probe",
            risk_type,
            severity,
            knowledge_probe_actor(risk_type),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk_type, op["method"]),
            f"{op['method']} {op['path']}",
            source="risk_learning_profile",
        )
        item["risk_learning_context"] = {
            "profile_version": profile.get("version"),
            "reason": reason,
            "priority_risks": priority_risks[:5],
            "priority_modules": list(priority_modules)[:5],
            "learned_from_findings": profile.get("learned_from_findings"),
            "semantic_expansion_probe_count": profile.get("semantic_expansion_probe_count"),
        }
        item["business_context"] = {
            "knowledge_source": "enterprise_high_value_risk_learning_profile",
            "module": op.get("resource") or "风险学习画像",
            "domains": [op.get("resource") or ""],
            "why_high_value": f"由企业高价值缺陷学习画像推荐：{reason}",
        }
        steps = build_business_knowledge_steps(risk_type, op)
        if steps:
            item["steps"] = steps
            item["probe_type"] = "risk_learning_profile_journey_probe"
            if risk_type in oracle_risks or risk_type in {"money_consistency", "stock_consistency", "state_flow", "state_consistency", "idempotency"}:
                item["cross_step_oracle"] = build_cross_step_oracle(risk_type, "risk_learning_profile")
            item["evidence_required"] = ["risk_learning_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)

    for api in priority_apis[:12]:
        op = find_operation_by_api_template(operations, api)
        if not op:
            continue
        risk = risk_learning_profile_risk_for_operation(op, priority_risks) or (priority_risks[0] if priority_risks else "boundary_validation")
        add_probe(op, executable_risk_type(risk), f"重点接口 {api} 在历史高价值画像中权重靠前")
    for op in operations:
        if len(probes) >= 64:
            break
        risk = risk_learning_profile_risk_for_operation(op, priority_risks)
        if not risk:
            continue
        module = str(op.get("resource") or "")
        if module in priority_modules or operation_api_template(op) in priority_apis or len(probes) < 24:
            add_probe(op, risk, f"重点风险 {risk} 与接口 {op['method']} {op['path']} 匹配")
    return probes


def generate_high_value_attack_plan_probes(business_model: dict) -> list[dict]:
    plan = business_model.get("high_value_attack_plan") or {}
    focus = plan.get("top_focus") or []
    if not focus:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    total_budget = max(4, min(64, int(plan.get("next_run_probe_budget") or 24)))

    for focus_item in focus[:10]:
        if len(probes) >= total_budget:
            break
        risk = executable_risk_type(str(focus_item.get("risk_type") or ""))
        if risk not in ORACLE_REQUIRED_RISKS:
            continue
        priority = str(focus_item.get("priority") or "P1")
        if priority not in {"P0", "P1"}:
            continue
        per_risk_budget = max(1, min(8, int(focus_item.get("recommended_next_probe_count") or 3)))
        matched = [op for op in operations if risk_operation_compatible(risk, op)]
        matched.sort(key=lambda op: (operation_api_template(op) not in {str(api.get("name") or "") for api in (plan.get("priority_apis") or [])}, op.get("method") not in {"POST", "PUT", "PATCH"}, operation_api_template(op)))
        added = 0
        for op in matched:
            if len(probes) >= total_budget or added >= per_risk_budget:
                break
            template = operation_api_template(op)
            key = (template, risk)
            if key in seen:
                continue
            steps = build_business_knowledge_steps(risk, op)
            if not steps:
                continue
            seen.add(key)
            added += 1
            seq = len(probes) + 1
            item = probe(
                f"HVAP_{safe_pattern_token(risk)}_{seq:03d}",
                f"高价值攻击计划探针 {risk} {template}",
                "high_value_attack_plan_journey_probe",
                risk,
                "P0" if priority == "P0" or risk in {"money_consistency", "stock_consistency", "state_flow", "idor", "tenant_isolation"} else "P1",
                knowledge_probe_actor(risk),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(risk, op["method"]),
                template,
                source="high_value_attack_plan",
            )
            item["steps"] = steps
            item["cross_step_oracle"] = build_cross_step_oracle(risk, "high_value_attack_plan")
            item["attack_plan_context"] = {
                "plan_version": plan.get("version"),
                "priority": priority,
                "priority_score": focus_item.get("priority_score"),
                "gap_types": focus_item.get("gap_types") or [],
                "oracle_coverage_rate": focus_item.get("oracle_coverage_rate"),
                "recommended_next_probe_count": focus_item.get("recommended_next_probe_count"),
            }
            item["business_context"] = {
                "knowledge_source": "high_value_attack_plan",
                "module": op.get("resource") or "attack_plan",
                "domains": [op.get("resource") or ""],
                "why_high_value": f"上一轮攻击计划将 {risk} 识别为 {priority} 风险热点，自动转化为跨步骤 Oracle 探针",
            }
            item["evidence_required"] = ["attack_plan_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(item)
    return probes


def business_adaptation_executable_risk(risk_type: str) -> str:
    return {
        "payment": "money_consistency",
        "refund": "refund_abuse",
        "state_transition": "state_flow",
        "account_ownership": "idor",
        "ownership_boundary": "idor",
        "privacy_leak": "permission_bypass",
        "limit_bypass": "boundary_validation",
        "amount_limit": "money_consistency",
        "benefit_abuse": "coupon_abuse",
        "coupon_abuse": "coupon_abuse",
        "quote_discount": "money_consistency",
        "contract_state": "state_flow",
        "appointment_conflict": "state_consistency",
        "prescription_rule": "permission_bypass",
        "score_tampering": "permission_bypass",
        "enrollment_capacity": "stock_consistency",
        "tracking_tampering": "state_flow",
        "delivery_state": "state_flow",
        "moderation_bypass": "state_flow",
        "author_ownership": "idor",
        "audit_trace": "audit_log_missing",
    }.get(str(risk_type or ""), str(risk_type or "boundary_validation"))


def generate_business_adaptation_probes(business_model: dict) -> list[dict]:
    profile = business_model.get("business_adaptation_profile") or {}
    if not profile:
        return []
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    op_by_template = {operation_api_template(op): op for op in operations}
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    selected_domains = [str(item.get("domain") or "") for item in profile.get("selected_domains", []) if item.get("domain")]
    risk_matrix = profile.get("adaptive_risk_matrix") or []

    def add(op: dict, raw_risk: str, row: dict, reason: str) -> None:
        risk_type = business_adaptation_executable_risk(raw_risk)
        key = (operation_api_template(op), risk_type)
        if key in seen:
            return
        seen.add(key)
        seq = len(probes) + 1
        item = probe(
            f"BA_{risk_type.upper()}_{seq:03d}",
            f"业务适配画像探针：{row.get('title') or raw_risk} {op['method']} {op['path']}",
            "business_adaptation_probe",
            risk_type,
            str(row.get("severity") or "P1"),
            knowledge_probe_actor(risk_type),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk_type, op["method"]),
            f"{op['method']} {op['path']}",
            source="business_adaptation_layer",
        )
        item["business_adaptation_context"] = {
            "profile_phase": profile.get("phase"),
            "business_domains": selected_domains,
            "raw_risk_type": raw_risk,
            "adapted_risk_type": risk_type,
            "reason": reason,
            "expected": row.get("expected"),
            "bug_signal": row.get("bug_signal"),
        }
        item["business_context"] = {
            "knowledge_source": "business_adaptation_profile",
            "module": op.get("resource") or row.get("domain") or "业务适配画像",
            "domains": sorted(set([str(row.get("domain") or ""), *selected_domains, str(op.get("resource") or "")]) - {""}),
            "why_high_value": f"由 PRD/OpenAPI 自动识别业务域 {','.join(selected_domains) or 'auto'} 后生成：{reason}",
        }
        if row.get("expected"):
            item["expected"] = str(row.get("expected"))
        if row.get("bug_signal"):
            item["bug_signal"] = str(row.get("bug_signal"))
        steps = build_business_knowledge_steps(risk_type, op)
        if steps:
            item["steps"] = steps
            item["probe_type"] = "business_adaptation_journey_probe"
            item["cross_step_oracle"] = build_cross_step_oracle(risk_type, "business_adaptation_layer")
            item["evidence_required"] = ["business_adaptation_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)

    for row in risk_matrix[:32]:
        raw_risk = str(row.get("risk_type") or "")
        matched = [str(api) for api in row.get("matched_operations", []) or []]
        for api in matched[:6]:
            op = op_by_template.get(api) or find_operation_by_api_template(operations, api)
            if op:
                add(op, raw_risk, row, f"业务域风险矩阵命中 {raw_risk}")
        if len(probes) >= 96:
            return probes
    endpoint_map = profile.get("endpoint_domain_map") or []
    for item in endpoint_map:
        if len(probes) >= 96:
            break
        op = find_operation_by_api_template(operations, f"{item.get('method')} {item.get('path')}")
        if not op:
            continue
        for raw_risk in item.get("risk_types", [])[:4]:
            row = next((r for r in risk_matrix if r.get("risk_type") == raw_risk), {"risk_type": raw_risk, "severity": "P1", "title": f"{raw_risk} 业务风险"})
            add(op, str(raw_risk), row, f"接口风险映射命中 {raw_risk}")
    return probes


ORACLE_REQUIRED_RISKS = {
    "money_consistency",
    "stock_consistency",
    "state_flow",
    "state_consistency",
    "idempotency",
    "coupon_abuse",
    "refund_abuse",
    "payment_callback",
}


def generate_oracle_gap_probes(business_model: dict, existing_probes: list[dict]) -> list[dict]:
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    covered = {
        (p.get("api_template"), p.get("risk_type"))
        for p in existing_probes
        if p.get("cross_step_oracle") and p.get("api_template") and p.get("risk_type")
    }
    candidates: list[tuple[int, dict, str, str]] = []
    for op in operations:
        template = operation_api_template(op)
        op_risks = {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}
        text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
        if any(k in text for k in ["amount", "payment", "pay", "refund", "coupon", "discount", "price", "金额", "支付", "退款", "优惠"]):
            op_risks.add("money_consistency")
        if any(k in text for k in ["coupon", "voucher", "benefit", "promotion", "discount", "优惠", "权益", "券", "折扣"]):
            op_risks.add("coupon_abuse")
        if any(k in text for k in ["stock", "quantity", "inventory", "product", "库存", "数量"]):
            op_risks.add("stock_consistency")
        if any(k in text for k in ["status", "state", "cancel", "approve", "状态", "取消", "审批"]):
            op_risks.add("state_flow")
        if op.get("method") in {"POST", "PUT", "PATCH", "DELETE"} and "login" not in text:
            op_risks.add("idempotency")
        for risk in sorted(op_risks & ORACLE_REQUIRED_RISKS):
            if (template, risk) in covered:
                continue
            steps = build_business_knowledge_steps(risk, op)
            if not steps:
                continue
            score = 10
            if risk in {"money_consistency", "stock_consistency", "state_flow"}:
                score += 4
            if op.get("method") in {"POST", "PUT", "PATCH"}:
                score += 2
            candidates.append((score, op, risk, template))
    candidates.sort(key=lambda item: (item[0], item[3]), reverse=True)
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for _, op, risk, template in candidates[:48]:
        key = (template, risk)
        if key in seen:
            continue
        seen.add(key)
        seq = len(probes) + 1
        item = probe(
            f"ORACLE_GAP_{risk.upper()}_{seq:03d}",
            f"跨步骤 Oracle 覆盖缺口探针：{risk} {template}",
            "oracle_gap_probe",
            risk,
            "P0" if risk in {"money_consistency", "stock_consistency"} else "P1",
            knowledge_probe_actor(risk),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk, op["method"]),
            template,
            source="oracle_gap",
        )
        item["steps"] = build_business_knowledge_steps(risk, op)
        item["cross_step_oracle"] = build_cross_step_oracle(risk, "oracle_gap")
        item["oracle_gap_context"] = {
            "gap_type": "missing_cross_step_oracle",
            "api_template": template,
            "risk_type": risk,
            "reason": "高价值风险接口缺少跨步骤业务断言覆盖",
        }
        item["business_context"] = {
            "knowledge_source": "oracle_coverage_gap_analysis",
            "module": op.get("resource") or "Oracle覆盖缺口",
            "domains": [op.get("resource") or ""],
            "why_high_value": "高价值风险接口必须具备跨步骤 Oracle，避免只靠状态码漏检业务一致性问题",
        }
        item["evidence_required"] = ["oracle_gap_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)
    assessment = business_model.get("high_value_capability_assessment") or {}
    low_coverage_gaps = [
        item for item in assessment.get("capability_gaps") or []
        if item.get("gap_type") == "oracle_coverage_gap" and item.get("risk_type") in ORACLE_REQUIRED_RISKS
    ]
    if low_coverage_gaps:
        gap_priority = {
            str(item.get("risk_type")): max(0.0, float(item.get("target") or 0.9) - float(item.get("current") or 0))
            for item in low_coverage_gaps
        }
        closure_candidates: list[tuple[float, dict, str, str]] = []
        existing_oracle_count_by_risk: dict[str, int] = {}
        for p in existing_probes:
            if p.get("cross_step_oracle") and p.get("risk_type") in gap_priority:
                risk = str(p.get("risk_type"))
                existing_oracle_count_by_risk[risk] = existing_oracle_count_by_risk.get(risk, 0) + 1
        for op in operations:
            template = operation_api_template(op)
            text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
            hinted = {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}
            for risk, gap_size in gap_priority.items():
                steps = build_business_knowledge_steps(risk, op)
                if not steps:
                    continue
                relevance = 0.0
                if risk in hinted:
                    relevance += 5
                if risk == "money_consistency" and any(k in text for k in ["amount", "payment", "pay", "refund", "coupon", "discount", "price", "金额", "支付", "退款", "优惠"]):
                    relevance += 4
                if risk == "stock_consistency" and any(k in text for k in ["stock", "quantity", "inventory", "product", "库存", "数量"]):
                    relevance += 4
                if risk in {"state_flow", "state_consistency"} and any(k in text for k in ["status", "state", "cancel", "approve", "状态", "取消", "审批"]):
                    relevance += 4
                if risk == "coupon_abuse" and any(k in text for k in ["coupon", "voucher", "benefit", "promotion", "discount", "优惠", "权益", "券"]):
                    relevance += 4
                if risk == "idempotency" and op.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
                    relevance += 3
                if relevance <= 0:
                    continue
                scarcity = max(0, 8 - existing_oracle_count_by_risk.get(risk, 0))
                closure_candidates.append((gap_size * 20 + relevance + scarcity, op, risk, template))
        closure_candidates.sort(key=lambda item: (item[0], item[3]), reverse=True)
        per_risk_count: dict[str, int] = {}
        closure_seen = set(seen)
        for _, op, risk, template in closure_candidates:
            if len(probes) >= 96:
                break
            if per_risk_count.get(risk, 0) >= 6:
                continue
            key = (template, risk)
            if key in closure_seen:
                continue
            closure_seen.add(key)
            per_risk_count[risk] = per_risk_count.get(risk, 0) + 1
            seq = len(probes) + 1
            gap = next((item for item in low_coverage_gaps if item.get("risk_type") == risk), {})
            item = probe(
                f"ORACLE_GAP_CLOSURE_{risk.upper()}_{seq:03d}",
                f"低覆盖 Oracle 补强探针：{risk} {template}",
                "oracle_gap_closure_probe",
                risk,
                "P0" if risk in {"money_consistency", "stock_consistency", "state_flow", "state_consistency"} else "P1",
                knowledge_probe_actor(risk),
                op["method"],
                concrete_path(op["path"]),
                expected_status_for_knowledge_risk(risk, op["method"]),
                template,
                source="oracle_gap",
            )
            item["steps"] = build_business_knowledge_steps(risk, op)
            item["cross_step_oracle"] = build_cross_step_oracle(risk, "oracle_gap_closure")
            item["oracle_gap_context"] = {
                "gap_type": "low_oracle_coverage",
                "api_template": template,
                "risk_type": risk,
                "current": gap.get("current"),
                "target": gap.get("target", 0.9),
                "source_assessment_version": assessment.get("version"),
                "reason": "上一轮能力评估显示该高价值风险 Oracle 覆盖低于企业级 90% 目标，需要继续增加跨步骤业务断言。",
            }
            item["business_context"] = {
                "knowledge_source": "high_value_capability_assessment",
                "module": op.get("resource") or "Oracle覆盖补强",
                "domains": [op.get("resource") or ""],
                "why_high_value": "基于上一轮低覆盖风险自动生成更深的跨步骤 Oracle 探针，提升真实业务一致性缺陷发现能力。",
            }
            item["evidence_required"] = ["oracle_gap_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
            probes.append(item)
    return probes


def generate_capability_gap_probes(business_model: dict, existing_probes: list[dict]) -> list[dict]:
    assessment = business_model.get("high_value_capability_assessment") or {}
    gaps = [
        item for item in assessment.get("capability_gaps") or []
        if item.get("gap_type") == "oracle_coverage_gap" and item.get("risk_type") in ORACLE_REQUIRED_RISKS
    ]
    if not gaps:
        return []
    gap_priority = {
        str(item.get("risk_type")): float(item.get("target") or 0.9) - float(item.get("current") or 0)
        for item in gaps
    }
    operations = [op for op in business_model.get("operations", []) if op.get("path") not in {"/reset", "/health", "/openapi.json"}]
    candidates: list[tuple[float, dict, str, str]] = []
    for op in operations:
        template = operation_api_template(op)
        text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')}".lower()
        hinted = {business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []}
        for risk, gap_size in gap_priority.items():
            steps = build_business_knowledge_steps(risk, op)
            if not steps:
                continue
            relevance = 0.0
            if risk in hinted:
                relevance += 5
            if risk == "money_consistency" and any(k in text for k in ["amount", "payment", "pay", "refund", "coupon", "discount", "price", "金额", "支付", "退款", "优惠"]):
                relevance += 4
            if risk == "stock_consistency" and any(k in text for k in ["stock", "quantity", "inventory", "product", "库存", "数量"]):
                relevance += 4
            if risk in {"state_flow", "state_consistency"} and any(k in text for k in ["status", "state", "cancel", "approve", "状态", "取消", "审批"]):
                relevance += 4
            if risk == "coupon_abuse" and any(k in text for k in ["coupon", "voucher", "benefit", "promotion", "discount", "优惠", "权益", "券"]):
                relevance += 4
            if risk == "idempotency" and op.get("method") in {"POST", "PUT", "PATCH", "DELETE"}:
                relevance += 3
            if relevance <= 0:
                continue
            candidates.append((gap_size * 10 + relevance, op, risk, template))
    candidates.sort(key=lambda item: (item[0], item[3]), reverse=True)
    probes: list[dict] = []
    seen: set[tuple[str, str]] = set()
    per_risk_count: dict[str, int] = {}
    for _, op, risk, template in candidates:
        if len(probes) >= 48:
            break
        key = (template, risk)
        if key in seen:
            continue
        if per_risk_count.get(risk, 0) >= 8:
            continue
        seen.add(key)
        per_risk_count[risk] = per_risk_count.get(risk, 0) + 1
        seq = len(probes) + 1
        item = probe(
            f"CAP_GAP_{risk.upper()}_{seq:03d}",
            f"能力短板补强探针：{risk} {template}",
            "capability_gap_oracle_probe",
            risk,
            "P0" if risk in {"money_consistency", "stock_consistency", "state_flow"} else "P1",
            knowledge_probe_actor(risk),
            op["method"],
            concrete_path(op["path"]),
            expected_status_for_knowledge_risk(risk, op["method"]),
            template,
            source="capability_gap",
        )
        item["steps"] = build_business_knowledge_steps(risk, op)
        item["cross_step_oracle"] = build_cross_step_oracle(risk, "capability_gap")
        item["capability_gap_context"] = {
            "gap_type": "oracle_coverage_gap",
            "risk_type": risk,
            "current": next((g.get("current") for g in gaps if g.get("risk_type") == risk), None),
            "target": next((g.get("target") for g in gaps if g.get("risk_type") == risk), 0.9),
            "source_assessment_version": assessment.get("version"),
        }
        item["business_context"] = {
            "knowledge_source": "high_value_capability_assessment",
            "module": op.get("resource") or "capability_gap",
            "domains": [op.get("resource") or ""],
            "why_high_value": "基于上一轮能力评估自动补强低覆盖高价值风险的跨步骤 Oracle。",
        }
        item["evidence_required"] = ["capability_gap_context", "business_context", "actor_role", "step_requests", "step_responses", "cross_step_assertion"]
        probes.append(item)
    return probes


def build_business_knowledge_steps(
    risk_type: str,
    op: dict,
    operations: list[dict] | None = None,
) -> list[dict]:
    method = op.get("method")
    path = op.get("path", "")
    concrete = concrete_path(path)
    text = f"{path} {op.get('summary', '')} {op.get('resource', '')}".lower()
    resource = str(op.get("resource") or "resource")
    ops_ctx = operations if isinstance(operations, list) else None
    if ops_ctx is None and isinstance(op.get("_operations_context"), list):
        ops_ctx = op.get("_operations_context")
    read_path = concrete if method == "GET" else read_path_for_resource(resource, operations=ops_ctx)
    create_path = None
    cancel_method = "POST"
    cancel_path = None
    if isinstance(ops_ctx, list):
        for candidate in ops_ctx:
            if str(candidate.get("resource") or "") != resource:
                # Also allow fulfillment parent create/cancel when probing payment-like ops.
                cand_resource = str(candidate.get("resource") or "")
                if not (
                    _resource_in(resource, _PAYMENT_LIKE)
                    and _resource_in(cand_resource, _FULFILLMENT_PARENT_LIKE)
                ):
                    continue
            cand_method = str(candidate.get("method") or "").upper()
            cand_path = str(candidate.get("path") or "")
            cand_op = str(candidate.get("operation") or "")
            if create_path is None and cand_method == "POST" and (
                cand_op in {"create_or_action", ""} or cand_op == "create_or_action"
            ):
                if cand_op in {"create_or_action"} or (
                    cand_op == "" and not any(tok in cand_path.lower() for tok in ("cancel", "pay", "refund", "callback"))
                ):
                    create_path = concrete_path(cand_path)
            if cancel_path is None and (
                cand_op == "state_cancel"
                or any(tok in cand_path.lower() for tok in ("cancel", "void", "close", "terminate"))
            ):
                cancel_method = cand_method or "POST"
                cancel_path = concrete_path(cand_path)
    if risk_type in {"quantity_consistency", "stock_consistency"} and any(
        k in text for k in ["product", "stock", "inventory", "cart", "order", "booking", "seat", "capacity", "material", "预约", "库存", "名额"]
    ):
        action_method = method if method in {"POST", "PUT", "PATCH"} else "POST"
        action_path = concrete if method in {"POST", "PUT", "PATCH"} else path
        if risk_type == "stock_consistency":
            return [
                step("read_stock_before", "GET", read_path, body_hint="read"),
                step("submit_quantity_change", action_method, action_path, body_hint="create"),
                step("read_stock_after", "GET", read_path, body_hint="read"),
                step("attempt_boundary_quantity_change", action_method, action_path, body_hint="quantity"),
                step("read_stock_after_boundary", "GET", read_path, body_hint="read"),
            ]
        return [
            step("read_quantity_before", "GET", read_path, body_hint="read"),
            step("attempt_boundary_quantity_change", action_method, action_path, body_hint="quantity"),
            step("read_quantity_after", "GET", read_path, body_hint="read"),
        ]
    if risk_type in {"coupon_abuse", "benefit_abuse"} and any(
        k in text for k in ["coupon", "voucher", "promo", "benefit", "discount", "优惠", "券"]
    ):
        return [
            step("apply_benefit_first_time", method, concrete, body_hint="coupon_valid"),
            step("apply_benefit_duplicate", method, concrete, body_hint="coupon_valid"),
            step("apply_abusive_benefit", method, concrete, body_hint="coupon_over_discount"),
        ]
    if risk_type == "money_consistency" and any(k in text for k in ["coupon", "voucher", "promo", "benefit", "discount", "优惠", "券"]):
        return [step("apply_abusive_benefit", method, concrete, body_hint="coupon_over_discount")]
    if risk_type == "money_consistency" and any(k in text for k in ["payment", "pay", "settlement", "支付", "结算"]):
        return [
            step("submit_mismatched_payment", method, concrete, body_hint="payment_mismatch"),
            step("read_after_payment", "GET", read_path, body_hint="read"),
        ]
    if risk_type == "money_consistency" and "refund" in text:
        return [
            step("submit_over_refund", method, concrete, body_hint="refund_over"),
            step("read_after_refund", "GET", read_path, body_hint="read"),
        ]
    # Terminal-state mutation: create → cancel/close → attempt payment/settlement (not mall-only).
    if risk_type == "state_flow" and any(k in text for k in ["payment", "pay", "callback", "settlement", "支付", "回调", "结算"]):
        steps = []
        if create_path:
            steps.append(step("seed_fulfillment_resource", "POST", create_path, body_hint="create"))
        if cancel_path:
            steps.append(step("move_to_terminal_state", cancel_method, cancel_path, body_hint="cancel"))
        steps.append(step("attempt_payment_after_terminal_state", method, concrete, body_hint="payment"))
        steps.append(step("read_after_terminal_action", "GET", read_path, body_hint="read"))
        return steps
    if risk_type == "state_consistency" and any(k in text for k in ["payment", "pay", "callback", "settlement", "支付", "回调"]):
        return [
            step("execute_state_sync_action", method, concrete, body_hint="payment"),
            step("read_after_state_sync", "GET", read_path, body_hint="read"),
        ]
    if risk_type == "state_consistency" and "refund" in text:
        return [
            step("execute_refund_state_action", method, concrete, body_hint="refund"),
            step("read_after_refund_state", "GET", read_path, body_hint="read"),
        ]
    if risk_type in {"state_flow", "state_consistency", "approval_bypass"} and method in {"POST", "PUT", "PATCH"}:
        return [
            step("execute_business_state_action", method, concrete, body_hint="state"),
            step("read_business_state_after_action", "GET", read_path, body_hint="read"),
            step("repeat_business_state_action", method, concrete, body_hint="state"),
        ]
    if risk_type in {"state_flow", "state_consistency"} and method == "GET":
        seed = create_path or (path if not has_path_param_like(path) else read_path_for_resource(resource, operations=ops_ctx))
        return [
            step("execute_business_action_before_read", "POST", seed, body_hint="create"),
            step("read_business_state", method, concrete, body_hint="read"),
        ]
    if risk_type == "money_consistency" and method in {"POST", "PUT", "PATCH"}:
        return [
            step("submit_financial_boundary_value", method, concrete, body_hint="payment_mismatch"),
            step("repeat_financial_boundary_value", method, concrete, body_hint="payment_mismatch"),
        ]
    if risk_type == "idempotency" and method in {"POST", "PUT", "PATCH"}:
        return [
            step("first_submit", method, concrete, body_hint="create"),
            step("duplicate_submit", method, concrete, body_hint="create"),
        ]
    return []


def has_path_param_like(path: str) -> bool:
    return "{" in str(path or "") and "}" in str(path or "")


_RISK_COMPAT_RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...], tuple[str, ...], bool]] = {
    # (text_keywords, path_keywords, methods, path_excludes, text_or_method)
    # text_or_method=True means match if text keywords OR method matches (not AND)
    "auth_bypass":           (("login", "admin"), (), ("POST", "PUT", "PATCH", "DELETE"), (), True),
    "permission_bypass":     (("admin", "permission", "role", "approve", "export", "import", "管理", "权限", "审批", "导出"), (), (), (), False),
    "idor":                  ((), ("{",), (), ("product_id", "sku_id"), False),
    "tenant_isolation":      (("tenant", "租户", "组织", "org"), (), (), (), False),
    "money_consistency":     (("amount", "price", "total", "pay", "payment", "refund", "coupon", "discount", "金额", "支付", "退款", "优惠", "折扣"), (), (), (), False),
    "quantity_consistency":  (("stock", "quantity", "quota", "inventory", "product", "cart", "库存", "数量", "额度"), (), (), (), False),
    "state_flow":            (("status", "state", "cancel", "approve", "refund", "payment", "order", "状态", "流转", "取消", "审批"), (), (), (), False),
    "state_consistency":     (("status", "state", "cancel", "approve", "refund", "payment", "order", "状态", "流转", "取消", "审批"), (), (), (), False),
    "idempotency":           ((), (), ("POST", "PUT", "PATCH", "DELETE"), ("login",), False),
    "coupon_abuse":          (("coupon", "benefit", "promotion", "discount", "优惠", "权益", "券", "折扣"), (), (), (), False),
    "benefit_abuse":         (("coupon", "benefit", "promotion", "discount", "优惠", "权益", "券", "折扣"), (), (), (), False),
    "boundary_validation":   ((), (), ("POST", "PUT", "PATCH", "DELETE"), ("login",), False),
    "audit_log_missing":     (("admin", "approve", "order", "payment", "refund", "export", "import", "delete", "cancel", "审批", "订单", "支付", "退款", "导出", "导入", "删除", "取消"), (), (), ("login",), False),
}


def risk_operation_compatible(risk_type: str, op: dict) -> bool:
    method = str(op.get("method") or "").upper()
    text = f"{op.get('path', '')} {op.get('summary', '')} {op.get('resource', '')} {op.get('operation', '')}".lower()
    path = str(op.get("path", "")).lower()

    rule = _RISK_COMPAT_RULES.get(risk_type)
    if rule is not None:
        text_kw, path_kw, methods, excludes, text_or_method = rule
        if text_or_method:
            if any(k in text for k in text_kw) or method in methods:
                return True
            return False
        if text_kw and not any(k in text for k in text_kw):
            return False
        if path_kw and not any(k in path for k in path_kw):
            return False
        if methods and method not in methods:
            return False
        if excludes and any(k in path for k in excludes):
            return False
        return True

    return method in {"POST", "PUT", "PATCH", "DELETE"}


def collect_knowledge_risk_items(knowledge: dict) -> list[dict]:
    items: dict[str, dict] = {}

    def ensure(risk_type: str) -> dict:
        risk_type = normalize_knowledge_risk(risk_type)
        return items.setdefault(risk_type, {"risk_type": risk_type, "priority": "P1", "risk_ids": [], "rule_ids": [], "scenario_ids": [], "evidence": [], "domains": set()})

    for risk in knowledge.get("risk_matrix", []) or []:
        risk_type = normalize_knowledge_risk(risk.get("risk") or risk.get("risk_id") or "")
        if not risk_type:
            continue
        item = ensure(risk_type)
        item["priority"] = min_priority(item.get("priority"), risk.get("priority") or "P1")
        if risk.get("risk_id"):
            item["risk_ids"].append(risk["risk_id"])
        item["evidence"].extend(str(x) for x in risk.get("evidence", []) or [])
    for rule in knowledge.get("business_rules", []) or []:
        rule_risks = {normalize_knowledge_risk(r) for r in rule.get("risk_tags", []) or []} | risks_from_text(rule.get("text", ""))
        if not rule_risks:
            rule_risks = {normalize_knowledge_risk(rule.get("domain", ""))}
        for risk_type in rule_risks:
            item = ensure(risk_type)
            if rule.get("rule_id"):
                item["rule_ids"].append(rule["rule_id"])
            if rule.get("domain"):
                item["domains"].add(str(rule["domain"]))
            if rule.get("text"):
                item["evidence"].append(str(rule["text"])[:180])
    for scenario in knowledge.get("business_scenarios", []) or []:
        text = " ".join(str(scenario.get(k, "")) for k in ["name", "domain", "business_value"])
        scenario_risks = risks_from_text(text) or {normalize_knowledge_risk(scenario.get("domain", ""))}
        for risk_type in scenario_risks:
            item = ensure(risk_type)
            if scenario.get("scenario_id"):
                item["scenario_ids"].append(scenario["scenario_id"])
            if scenario.get("domain"):
                item["domains"].add(str(scenario["domain"]))
    for module in knowledge.get("module_knowledge_map", []) or []:
        for risk_type in module.get("risks", []) or []:
            item = ensure(risk_type)
            item["domains"].update(str(x) for x in module.get("domains", []) or [])
            if module.get("module"):
                item["evidence"].append(f"模块 {module['module']} 暴露 {risk_type} 风险")
    result = []
    for item in items.values():
        item["domains"] = sorted(item["domains"])
        item["risk_ids"] = sorted(set(item["risk_ids"]))
        item["rule_ids"] = sorted(set(item["rule_ids"]))
        item["scenario_ids"] = sorted(set(item["scenario_ids"]))
        item["evidence"] = list(dict.fromkeys(item["evidence"]))
        result.append(item)
    return sorted(result, key=lambda item: (priority_rank(item.get("priority")), item["risk_type"]), reverse=True)


def build_module_context_by_domain(knowledge: dict) -> dict[str, str]:
    result: dict[str, str] = {}
    for module in knowledge.get("module_knowledge_map", []) or []:
        name = str(module.get("module") or "")
        for domain in module.get("domains", []) or []:
            for alias in domain_aliases(str(domain)):
                result.setdefault(alias, name)
        for risk in module.get("risks", []) or []:
            result.setdefault(normalize_knowledge_risk(str(risk)), name)
    return result


def choose_module_for_operation(op: dict, module_by_domain: dict[str, str]) -> str:
    keys: list[str] = []
    for key in [op.get("resource"), *op.get("risk_hints", [])]:
        keys.extend(domain_aliases(str(key)))
    for key in keys:
        if key in module_by_domain:
            return module_by_domain[key]
    return "通用业务模块"


def domain_aliases(value: str) -> list[str]:
    raw = str(value or "").lower().strip()
    aliases = {raw}
    if raw.endswith("s"):
        aliases.add(raw[:-1])
    aliases.update(
        {
            "orders": "order",
            "order": "orders",
            "products": "inventory",
            "product": "inventory",
            "inventory": "products",
            "stock": "inventory",
            "payments": "payment",
            "payment": "payments",
            "refunds": "refund",
            "refund": "refunds",
            "cart": "coupon",
            "coupon": "cart",
        }.get(raw, raw).split("|")
    )
    return [item for item in aliases if item]


def priority_rank(priority: str | None) -> int:
    return {"P0": 3, "P1": 2, "P2": 1}.get(str(priority or "P1").upper(), 1)


def min_priority(left: str | None, right: str | None) -> str:
    return left if priority_rank(left) >= priority_rank(right) else right


def executable_risk_type(risk_type: str) -> str:
    return {
        "state_consistency": "state_consistency",
        "quantity_consistency": "stock_consistency",
        "benefit_abuse": "coupon_abuse",
        "boundary_validation": "boundary_validation",
    }.get(risk_type, risk_type)


def knowledge_probe_severity(risk_type: str, priority: str | None = None) -> str:
    if priority in {"P0", "P1", "P2"}:
        return priority
    if risk_type in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation", "money_consistency", "quantity_consistency", "state_flow"}:
        return "P0"
    return "P1"


def knowledge_probe_actor(risk_type: str) -> str:
    if risk_type in {"auth_bypass"}:
        return "anonymous"
    if risk_type in {"callback_trust"}:
        return "system"
    return "normal_user"


def expected_status_for_knowledge_risk(risk_type: str, method: str) -> int:
    if risk_type == "auth_bypass":
        return 401
    if risk_type in {"permission_bypass", "idor", "tenant_isolation", "approval_bypass", "search_scope_leak"}:
        return 403
    if risk_type in {"boundary_validation", "benefit_abuse", "coupon_abuse", "file_upload_validation"}:
        return 400
    if risk_type in {"money_consistency", "quantity_consistency", "state_flow", "idempotency", "callback_trust", "race_condition"}:
        return 409 if method in {"POST", "PUT", "PATCH", "DELETE"} else 200
    return 200


def knowledge_probe_title(risk_type: str, op: dict, item: dict) -> str:
    labels = {
        "permission_bypass": "业务知识要求非授权角色不得执行",
        "auth_bypass": "业务知识要求未登录用户不得执行",
        "idor": "业务知识要求不得越权访问或变更",
        "tenant_isolation": "业务知识要求跨租户数据必须隔离",
        "money_consistency": "业务知识要求金额/费用/支付必须一致",
        "quantity_consistency": "业务知识要求数量/库存/额度不能越界",
        "state_flow": "业务知识要求状态流转必须合法",
        "state_consistency": "业务知识要求上下游状态必须一致",
        "idempotency": "业务知识要求重复提交必须幂等",
        "benefit_abuse": "业务知识要求权益/优惠不能被滥用",
        "boundary_validation": "业务知识要求输入边界必须校验",
    }
    return f"{labels.get(risk_type, '业务知识要求校验高风险行为')} {op['method']} {op['path']}"


def knowledge_expected_statement(risk_type: str, op: dict) -> str:
    return f"根据企业业务知识，{op['method']} {op['path']} 必须满足 {risk_type} 业务不变量"


def knowledge_bug_signal(risk_type: str) -> str:
    return f"响应、状态或跨接口证据违反企业业务知识中的 {risk_type} 风险规则"


def why_high_value(risk_type: str) -> str:
    return {
        "permission_bypass": "直接影响权限边界和数据安全",
        "idor": "容易造成用户间数据越权",
        "tenant_isolation": "直接影响多租户企业隔离",
        "money_consistency": "影响资金、账务或费用正确性",
        "quantity_consistency": "影响库存、额度、容量等核心资产",
        "state_flow": "影响业务流程合法性和状态闭环",
        "state_consistency": "影响跨模块数据一致性",
        "idempotency": "容易导致重复创建、重复扣减或重复入账",
        "benefit_abuse": "容易造成权益、优惠、折扣损失",
        "boundary_validation": "容易绕过必填、枚举、范围和非法输入校验",
    }.get(risk_type, "来自企业业务知识资产中的高风险规则")


# Learned templates bind to OpenAPI by role — never emit synthetic mall IDs.
# Tuple: template_id, title, probe_type, risk_type, severity, actor, method, role, expected_status, variants
LEARNED_TEMPLATE_SPECS = [
    ("STOCK_NEGATIVE_QUANTITY", "容量/库存不足时不能创建", "learned_stock_probe", "stock_consistency", "P0", "normal_user", "POST", "capacity_create", 409, 2),
    ("ORDER_DUPLICATE_SUBMIT", "重复提交不能生成多个业务单", "learned_idempotency_probe", "idempotency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("IDEMPOTENCY_DUPLICATE_STOCK_DEDUCT", "重复提交不能重复扣减容量/库存", "learned_stock_idempotency_probe", "stock_consistency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("STOCK_NOT_DECREASED", "创建成功后容量/库存必须扣减", "learned_stock_probe", "stock_consistency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("STOCK_NOT_ROLLBACK", "取消后容量/库存必须回滚", "learned_stock_probe", "stock_consistency", "P1", "normal_user", "POST", "capacity_cancel", 200, 2),
    ("ORDER_CREATE_MISSING", "创建成功后必须可查询", "learned_state_probe", "state_consistency", "P1", "normal_user", "POST", "capacity_create", 200, 2),
    ("ORDER_CANCEL_STATE", "取消后状态必须进入终止态", "learned_state_probe", "state_flow", "P1", "normal_user", "POST", "capacity_cancel", 200, 2),
    ("IDOR_ORDER_ACCESS", "用户不能查看他人资源", "learned_idor_probe", "idor", "P0", "normal_user", "GET", "owned_detail", 403, 2),
    ("IDOR_ORDER_CANCEL", "用户不能越权取消他人资源", "learned_idor_probe", "idor", "P1", "normal_user", "POST", "capacity_cancel", 403, 2),
    ("IDOR_ADDRESS_MODIFY", "用户创建时不能篡改租户或归属", "learned_idor_probe", "idor", "P1", "normal_user", "POST", "capacity_create", 403, 2),
    ("TENANT_DATA_LEAK", "租户数据必须隔离", "learned_tenant_probe", "tenant_isolation", "P0", "normal_user", "GET", "tenant_list", 403, 2),
    ("AUTH_ROLE_DOWNGRADE_CACHE", "低权限用户不能访问管理端", "learned_permission_probe", "permission_bypass", "P1", "normal_user", "GET", "admin_read", 403, 1),
    ("PAYMENT_STATUS_NOT_UPDATED", "支付/结算成功后业务状态必须更新", "learned_payment_probe", "state_consistency", "P1", "normal_user", "POST", "payment_create", 200, 2),
    ("PAYMENT_CANCELLED_ORDER_ALLOWED", "已终止业务单不能继续支付/结算", "learned_payment_probe", "state_flow", "P0", "normal_user", "POST", "payment_create", 409, 2),
    ("ORDER_PAY_CANCELLED", "已终止业务单不能被回调改为已支付", "learned_callback_probe", "state_flow", "P0", "system", "POST", "payment_callback", 409, 2),
    ("PAYMENT_DUPLICATE_CALLBACK", "支付/结算回调必须幂等", "learned_callback_probe", "payment_callback", "P1", "system", "POST", "payment_callback", 200, 2),
    ("MONEY_PAY_TOTAL_DIFF", "回调金额必须匹配应付金额", "learned_money_probe", "money_consistency", "P0", "system", "POST", "payment_callback", 409, 2),
    ("REFUND_UNPAID_ORDER", "未支付业务单不能退款", "learned_refund_probe", "refund_abuse", "P1", "normal_user", "POST", "refund_create", 409, 2),
    ("REFUND_DUPLICATE", "同一退款请求不能重复处理", "learned_refund_probe", "refund_abuse", "P1", "normal_user", "POST", "refund_create", 409, 2),
    ("REFUND_OVER_AMOUNT", "退款金额不能超过已支付剩余金额", "learned_refund_probe", "money_consistency", "P0", "normal_user", "POST", "refund_create", 409, 2),
    ("REFUND_STATE_INCONSISTENCY", "退款后状态与库存/额度必须一致", "learned_refund_probe", "state_consistency", "P1", "normal_user", "POST", "refund_create", 200, 2),
    ("COUPON_THRESHOLD_BYPASS", "不满足门槛不能使用权益/优惠", "learned_coupon_probe", "coupon_abuse", "P1", "normal_user", "POST", "benefit_apply", 400, 1),
    ("MONEY_DISCOUNT_OVER_TOTAL", "优惠金额不能超过应付金额", "learned_money_probe", "money_consistency", "P0", "normal_user", "POST", "benefit_apply", 400, 1),
]

_CAPACITY_HINTS = (
    "order", "booking", "reservation", "enrollment", "appointment", "inventory",
    "stock", "seat", "capacity", "ticket", "claim", "requisition", "prescription",
    "订单", "预约", "选课", "库存", "名额",
)
_BENEFIT_HINTS = ("coupon", "voucher", "promo", "promotion", "benefit", "discount", "补贴", "优惠", "券")
_PAYMENT_HINTS = ("payment", "pay", "settlement", "charge", "支付", "结算")
_REFUND_HINTS = ("refund", "chargeback", "退款")
_CALLBACK_HINTS = ("callback", "webhook", "notify", "回调", "通知")


def normalize_synthetic_probe_path(path: str) -> str:
    """Return the source-bound path unchanged; synthetic IDs are forbidden."""
    return str(path or "")


def _operation_match_text(op: dict) -> str:
    return f"{op.get('path') or ''} {op.get('summary') or ''} {op.get('resource') or ''}".lower()


def _operations_for_learned_role(operations: list[dict], role: str, method: str) -> list[dict]:
    method_u = str(method or "").upper()
    rows: list[dict] = []
    for op in operations:
        if not isinstance(op, dict):
            continue
        op_method = str(op.get("method") or "").upper()
        if method_u and op_method != method_u:
            continue
        path = str(op.get("path") or "")
        if not path or path in {"/reset", "/health", "/openapi.json"}:
            continue
        text = _operation_match_text(op)
        has_param = "{" in path and "}" in path
        is_admin = any(tok in path.lower() for tok in ("/admin/", "/manage/", "/manager/", "/console/"))
        if role == "capacity_create":
            if has_param or not any(tok in text for tok in _CAPACITY_HINTS):
                continue
        elif role == "capacity_cancel":
            if not any(tok in path.lower() for tok in ("/cancel", "/close", "/void", "/revoke", "/withdraw")):
                continue
            if not any(tok in text for tok in _CAPACITY_HINTS):
                continue
        elif role == "owned_detail":
            if not has_param or op_method != "GET" or is_admin:
                continue
        elif role == "benefit_apply":
            if not any(tok in text for tok in _BENEFIT_HINTS):
                continue
        elif role == "payment_create":
            if any(tok in text for tok in _CALLBACK_HINTS) or not any(tok in text for tok in _PAYMENT_HINTS):
                continue
        elif role == "payment_callback":
            if not (any(tok in text for tok in _PAYMENT_HINTS) and any(tok in text for tok in _CALLBACK_HINTS)):
                continue
        elif role == "refund_create":
            if not any(tok in text for tok in _REFUND_HINTS):
                continue
        elif role == "admin_read":
            if not is_admin or op_method != "GET":
                continue
        elif role == "tenant_list":
            if "tenant" not in path.lower() and "tenant" not in text:
                continue
        else:
            continue
        rows.append(op)
    # Prefer shorter/more specific collection paths first for stable binding.
    return sorted(rows, key=lambda op: (len(str(op.get("path") or "")), str(op.get("path") or "")))


def _openapi_paths(business_model: dict) -> set[str]:
    return {
        str(op.get("path") or "")
        for op in (business_model.get("operations") or [])
        if isinstance(op, dict) and op.get("path")
    }


def _path_available_in_model(path: str, paths: set[str]) -> bool:
    if not path:
        return False
    candidates = {
        path.split("?", 1)[0],
        normalize_synthetic_probe_path(path).split("?", 1)[0],
    }
    return any(candidate in paths for candidate in candidates)


def _bind_policy_path_to_source(paths: set[str], *candidates: str) -> str:
    for candidate in candidates:
        base = str(candidate or "").split("?", 1)[0]
        if base in paths:
            return base
    return ""


def generate_feedback_learning_probes(business_model: dict) -> list[dict]:
    """Generate probes learned from prior miss analysis without reading hidden ground truth.

    Templates bind to real OpenAPI operations by semantic role and preserve the
    source-declared path.
    """
    operations = [op for op in (business_model.get("operations") or []) if isinstance(op, dict)]
    probes: list[dict] = []
    for template_id, title, probe_type, risk_type, severity, actor, method, role, expected_status, variants in LEARNED_TEMPLATE_SPECS:
        matched = _operations_for_learned_role(operations, role, method)
        if not matched:
            continue
        op = matched[0]
        path = str(op.get("path") or "")
        if role == "tenant_list" and "tenant_id=" not in path:
            path = f"{path}{'&' if '?' in path else '?'}tenant_id=tenant_b"
        api_template = f"{method} {str(op.get('path') or path).split('?')[0]}"
        resource = str(op.get("resource") or path.strip("/").split("/")[0] or "resource")
        bound_title = title if "{resource}" not in title else title.format(resource=resource)
        for idx in range(1, variants + 1):
            item = probe(
                f"LEARN_{template_id}_V{idx}",
                f"{bound_title}（反馈学习变体 {idx}）",
                probe_type,
                risk_type,
                severity,
                actor,
                method,
                path,
                expected_status,
                api_template,
                source="feedback_learning",
            )
            item["predicted_template_id"] = template_id
            item["learned_from"] = "phase3_missed_bug_analysis"
            item["learning_strategy"] = "template_level_probe_expansion"
            item["learned_role"] = role
            item["bound_operation"] = api_template
            item["variant_index"] = idx
            probes.append(item)
    return probes


def generate_adaptive_policy_probes(business_model: dict) -> list[dict]:
    """Generate probes from sanitized adaptive policy learned from evaluator feedback.

    The policy is created from benchmark evaluator outputs, not from private ground
    truth files. It stores only template-level strategy and priority, so it can be
    reused in blind mode without exposing bug instances or enabled bug sets.
    """
    paths = _openapi_paths(business_model)
    operations = [op for op in business_model.get("operations", []) if isinstance(op, dict)]

    try:
        policy = build_learned_probe_policy(Path("."))
    except Exception:
        return []
    probes: list[dict] = []
    for row in policy.get("template_policies", []):
        if row.get("priority_score", 0) < 0.45:
            continue
        method = str(row.get("method") or "GET").upper()
        raw_path = str(row.get("path") or (row.get("api_template") or " ").split(" ", 1)[-1]).strip()
        path = normalize_synthetic_probe_path(raw_path)
        api_template = str(row.get("api_template") or f"{method} {path.split('?')[0]}")
        api_path = normalize_synthetic_probe_path(api_template.split(" ", 1)[-1])
        bound_path = _bind_policy_path_to_source(paths, api_path, path)
        if not bound_path:
            risk_type = str(row.get("risk_type") or "")
            matches = [
                op for op in operations
                if str(op.get("method") or "").upper() == method
                and risk_type in {
                    str(risk) for risk in op.get("risk_hints", [])
                }
            ]
            if matches:
                bound_path = str(sorted(matches, key=lambda op: str(op.get("path") or ""))[0].get("path") or "")
        if not bound_path:
            continue
        path = bound_path
        variants = int(row.get("recommended_variants") or 1)
        for idx in range(1, variants + 1):
            item = probe(
                f"ADAPT_{row['template_id']}_V{idx}",
                f"{row.get('strategy', row['template_id'])}（自适应策略变体 {idx}）",
                row["probe_type"],
                row["risk_type"],
                row["severity"],
                row["actor"],
                method,
                path,
                int(row["expected_status"]),
                f"{method} {path.split('?')[0]}",
                source="adaptive_policy",
            )
            item["predicted_template_id"] = row["template_id"]
            item["adaptive_priority_score"] = row.get("priority_score", 0)
            item["learning_strategy"] = row.get("strategy")
            item["learned_from"] = "phase5_adaptive_probe_policy"
            item["variant_index"] = idx
            probes.append(item)
    return probes


def generate_feedback_adjusted_policy_probes(business_model: dict) -> list[dict]:
    """Generate Phase23 probes from QA human feedback policy.

    The policy is produced by ai_test_asset_center.feedback_policy_update from
    human_feedback.jsonl and human_feedback_policy_update.json. It is template
    level only, so blind discovery can use it without reading hidden ground truth,
    bug sets, or enabled bug switches.
    """
    paths = _openapi_paths(business_model)

    policy_path = Path(os.environ.get("FEEDBACK_ADJUSTED_POLICY_PATH", "platform_workspace/enterprise_shop/defect_discovery/feedback_adjusted_probe_policy.json"))
    if not policy_path.exists():
        return []
    try:
        policy = json.loads(policy_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if not (policy.get("private_leak_check") or {}).get("passed", True):
        return []
    probes: list[dict] = []
    for row in policy.get("template_policies", []) or []:
        if float(row.get("priority_score") or 0) < 0.45:
            continue
        raw_path = str(row.get("path") or (row.get("api_template") or f"{row.get('method','GET')} /").split(" ", 1)[-1])
        path = normalize_synthetic_probe_path(raw_path)
        api_template = f"{row.get('method') or 'GET'} {path.split('?')[0]}"
        bound_path = _bind_policy_path_to_source(paths, api_template.split(" ", 1)[-1], path)
        if not bound_path:
            continue
        path = bound_path
        api_template = f"{row.get('method') or 'GET'} {path}"
        variants = max(1, min(5, int(row.get("recommended_variants") or 1)))
        for idx in range(1, variants + 1):
            item = probe(
                f"HFADJ_{row['template_id']}_V{idx}",
                f"{row.get('title') or row['template_id']}（人工反馈增强变体 {idx}）",
                row.get("probe_type") or "feedback_adjusted_probe",
                row.get("risk_type") or "unknown",
                row.get("severity") or row.get("human_priority") or "P1",
                row.get("actor") or "normal_user",
                row.get("method") or "GET",
                path,
                int(row.get("expected_status") or 200),
                api_template,
                source="feedback_adjusted",
            )
            item["predicted_template_id"] = row["template_id"]
            item["feedback_priority_score"] = row.get("priority_score", 0)
            item["human_feedback_count"] = row.get("human_feedback_count", 0)
            item["learning_strategy"] = "feedback_driven_policy_update"
            item["learned_from"] = "phase23_human_feedback_policy_update"
            item["variant_index"] = idx
            probes.append(item)
    return probes

def generate_journey_defect_probes(business_model: dict) -> list[dict]:
    probes: list[dict] = []
    seq = 1
    operations = list(business_model.get("operations") or [])
    for dep in business_model.get("entity_dependencies", []):
        if dep.get("dependency") == "create_then_read":
            setup = parse_operation_ref(dep["setup"])
            assertion = parse_operation_ref(dep["assert"])
            if setup and assertion:
                probes.append(
                    journey_probe(
                        f"JOURNEY_CREATE_READ_{seq:03d}",
                        f"{dep['resource']} 创建后必须可查询且字段一致",
                        "journey_consistency_probe",
                        "state_consistency",
                        "P1",
                        [
                            step("create_resource", setup[0], concrete_path(setup[1]), body_hint="create"),
                            step("read_resource", assertion[0], concrete_path(assertion[1]), body_hint="read"),
                        ],
                        api_template=dep["setup"],
                    )
                )
                seq += 1
        if dep.get("dependency") == "state_action_requires_existing_resource":
            action = parse_operation_ref(dep["action"])
            if action:
                # Prefer documented create/setup path over invented POST /{resource}.
                setup_ref = parse_operation_ref(str(dep.get("setup") or ""))
                if not setup_ref:
                    create_ops = [
                        op for op in operations
                        if str(op.get("resource") or "") == str(dep.get("resource") or "")
                        and str(op.get("method") or "").upper() == "POST"
                    ]
                    if create_ops:
                        setup_ref = (str(create_ops[0]["method"]).upper(), str(create_ops[0]["path"]))
                seed_method = setup_ref[0] if setup_ref else "POST"
                seed_path = concrete_path(setup_ref[1]) if setup_ref else f"/{dep['resource']}"
                read_path = read_path_for_resource(dep["resource"], operations=operations)
                probes.append(
                    journey_probe(
                        f"JOURNEY_STATE_ACTION_{seq:03d}",
                        f"{dep['resource']} 状态动作后必须可复核",
                        "journey_state_probe",
                        "state_flow",
                        "P1",
                        [
                            step("seed_resource", seed_method, seed_path, body_hint="create"),
                            step("state_action", action[0], concrete_path(action[1]), body_hint="state"),
                            step("read_after_action", "GET", read_path, body_hint="read"),
                        ],
                        api_template=dep["action"],
                    )
                )
                seq += 1
    for lineage in business_model.get("data_lineage", []):
        consumers = lineage.get("consumers") or []
        if not consumers:
            continue
        producer = parse_operation_ref(lineage["producer"])
        consumer = parse_operation_ref(consumers[0])
        if not producer or not consumer:
            continue
        probes.append(
            journey_probe(
                f"JOURNEY_LINEAGE_{seq:03d}",
                f"{lineage['resource']} {lineage['field_family']} 数据血缘必须跨接口一致",
                "journey_lineage_probe",
                lineage_risk_type(lineage["field_family"]),
                "P1",
                [
                    step("produce_value", producer[0], concrete_path(producer[1]), body_hint=lineage["field_family"]),
                    step("consume_value", consumer[0], concrete_path(consumer[1]), body_hint="read"),
                ],
                api_template=lineage["producer"],
            )
        )
        seq += 1
    return probes


def parse_operation_ref(ref: str) -> tuple[str, str] | None:
    parts = ref.split(" ", 1)
    if len(parts) != 2:
        return None
    return parts[0].upper(), parts[1]


def read_path_for_resource(resource: str, operations: list[dict] | None = None) -> str:
    """Pick a documented GET detail path when available; else generic /{resource}/{id}."""
    name = str(resource or "").strip().strip("/")
    if not name:
        return "/"
    ops = list(operations or [])
    detail_candidates: list[str] = []
    collection_candidates: list[str] = []
    for op in ops:
        if str(op.get("method") or "").upper() != "GET":
            continue
        if str(op.get("resource") or "") != name and name not in str(op.get("path") or ""):
            continue
        path = str(op.get("path") or "")
        if not path:
            continue
        if "{" in path and "}" in path:
            detail_candidates.append(path)
        else:
            collection_candidates.append(path)
    if detail_candidates:
        # Prefer paths whose final segment is a placeholder (true detail reads).
        detail_candidates.sort(
            key=lambda p: (
                0 if p.rstrip("/").endswith("}") else 1,
                len(p),
            )
        )
        return concrete_path(detail_candidates[0])
    if collection_candidates:
        return concrete_path(collection_candidates[0])
    return f"/{name}/{{id}}"


def lineage_risk_type(field_family: str) -> str:
    return {"money": "money_consistency", "quantity": "stock_consistency", "scope": "tenant_isolation"}.get(field_family, "state_consistency")


def step(name: str, method: str, path: str, body_hint: str = "") -> dict:
    return {"name": name, "method": method, "path": path, "body_hint": body_hint}


def journey_probe(probe_id: str, title: str, probe_type: str, risk_type: str, severity: str, steps: list[dict], api_template: str) -> dict:
    return {
        "probe_id": probe_id,
        "title": title,
        "probe_type": probe_type,
        "risk_type": risk_type,
        "severity": severity,
        "actor": "normal_user",
        "method": steps[-1]["method"],
        "path": steps[-1]["path"],
        "api_template": api_template,
        "source": "journey_auto",
        "steps": steps,
        "expected_status": 200,
        "evidence_required": ["actor_role", "step_requests", "step_responses", "cross_step_assertion"],
        "expected": "多接口链路执行后，资源状态、字段和上下游数据保持一致",
        "bug_signal": "链路执行结果违反语义图、状态机或数据血缘不变量",
    }


def generate_generic_defect_probes(business_model: dict) -> list[dict]:
    probes: list[dict] = []
    operations = business_model.get("operations", [])
    roles = business_model.get("roles", [])
    has_normal_user = "normal_user" in roles or bool(roles)
    seq = 1
    for op in operations:
        method = op["method"]
        path = op["path"]
        if path in {"/reset", "/health", "/openapi.json"}:
            continue
        concrete = concrete_path(path)
        template = f"{method} {path}"
        risks = set(op.get("risk_hints", []))
        if "permission_bypass" in risks:
            probes.append(probe(f"GEN_PERMISSION_{seq:03d}", f"非授权角色不能执行 {template}", "generic_permission_probe", "permission_bypass", "P0", "normal_user" if has_normal_user else "anonymous", method, concrete, 403, template, source="generic_auto"))
            seq += 1
            probes.append(probe(f"GEN_ANON_PROTECTED_{seq:03d}", f"未登录用户不能执行受保护操作 {template}", "generic_auth_probe", "auth_bypass", "P0", "anonymous", method, concrete, 401, template, source="generic_auto"))
            seq += 1
        if "idor" in risks:
            probes.append(probe(f"GEN_IDOR_{seq:03d}", f"用户不能访问或变更他人资源 {template}", "generic_idor_probe", "idor", "P0", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if "tenant_isolation" in risks:
            tenant_path = concrete if "?" in concrete else f"{concrete}?tenant_id=tenant_b"
            probes.append(probe(f"GEN_TENANT_{seq:03d}", f"跨租户请求必须被隔离 {template}", "generic_tenant_probe", "tenant_isolation", "P0", "normal_user", method, tenant_path, 403, template, source="generic_auto"))
            seq += 1
        if "idempotency" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_IDEMPOTENCY_{seq:03d}", f"重复提交必须幂等 {template}", "generic_idempotency_probe", "idempotency", "P1", "normal_user", method, concrete, 200, template, source="generic_auto"))
            seq += 1
        if "money_consistency" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_MONEY_{seq:03d}", f"金额字段不能绕过一致性校验 {template}", "generic_money_probe", "money_consistency", "P0", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if "quantity_consistency" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_QUANTITY_{seq:03d}", f"数量或额度不能越界 {template}", "generic_quantity_probe", "stock_consistency", "P0", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if "state_flow" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_STATE_{seq:03d}", f"状态流转必须满足前置条件 {template}", "generic_state_probe", "state_flow", "P1", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if "benefit_abuse" in risks and method in {"POST", "PUT", "PATCH"}:
            probes.append(probe(f"GEN_BENEFIT_{seq:03d}", f"权益/优惠不能被滥用 {template}", "generic_benefit_probe", "coupon_abuse", "P1", "normal_user", method, concrete, 400, template, source="generic_auto"))
            seq += 1
        if risks & {"approval_bypass", "step_skip"}:
            probes.append(probe(f"GEN_APPROVAL_{seq:03d}", f"审批流程不能跳过必要节点 {template}", "generic_workflow_probe", "approval_bypass", "P0", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if risks & {"audit_log_missing", "sensitive_data_exposure", "privacy_scope", "export_permission"}:
            probes.append(probe(f"GEN_COMPLIANCE_{seq:03d}", f"关键操作必须满足审计、隐私和导出权限 {template}", "generic_compliance_probe", "audit_compliance", "P1", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if risks & {"file_upload_validation", "duplicate_import", "bulk_operation_partial_failure", "large_payload_limit"}:
            probes.append(probe(f"GEN_BATCH_IMPORT_{seq:03d}", f"批量导入/文件上传必须校验和处理部分失败 {template}", "generic_batch_probe", "batch_import", "P1", "normal_user", method, concrete, 400, template, source="generic_auto"))
            seq += 1
        if risks & {"report_aggregation_error", "search_scope_leak", "pagination_consistency", "sorting_filter_consistency", "export_consistency"}:
            probes.append(probe(f"GEN_REPORT_SEARCH_{seq:03d}", f"搜索、分页、报表和导出必须遵守范围和一致性 {template}", "generic_report_probe", "search_report", "P1", "normal_user", method, concrete, 403 if "search_scope_leak" in risks else 200, template, source="generic_auto"))
            seq += 1
        if risks & {"notification_wrong_recipient", "notification_duplicate", "template_variable_leak"}:
            probes.append(probe(f"GEN_NOTIFICATION_{seq:03d}", f"通知不能错发、重复发送或泄露模板变量 {template}", "generic_notification_probe", "notification_risk", "P1", "normal_user", method, concrete, 400, template, source="generic_auto"))
            seq += 1
        if risks & {"feature_flag_scope", "tenant_config_isolation", "pricing_config", "workflow_config", "default_value_risk"}:
            probes.append(probe(f"GEN_CONFIG_{seq:03d}", f"配置、规则和开关必须按租户/组织隔离 {template}", "generic_config_probe", "configuration_risk", "P1", "normal_user", method, concrete, 403, template, source="generic_auto"))
            seq += 1
        if risks & {"callback_trust", "webhook_replay", "third_party_status_mapping", "message_ordering", "eventual_consistency"}:
            probes.append(probe(f"GEN_INTEGRATION_{seq:03d}", f"外部回调、事件和集成状态必须可信且幂等 {template}", "generic_integration_probe", "integration_risk", "P1", "system", method, concrete, 409, template, source="generic_auto"))
            seq += 1
        if risks & {"race_condition", "concurrent_update_lost", "time_window_boundary", "timezone_boundary", "sla_timeout"}:
            probes.append(probe(f"GEN_TIME_CONCURRENCY_{seq:03d}", f"并发、时间窗口和时区边界必须一致 {template}", "generic_time_concurrency_probe", "time_concurrency", "P1", "normal_user", method, concrete, 409, template, source="generic_auto"))
            seq += 1
    return probes


def concrete_path(path: str) -> str:
    """Keep source-declared parameter placeholders; never invent resource IDs."""
    return str(path or "")


def probe(probe_id: str, title: str, probe_type: str, risk_type: str, severity: str, actor: str, method: str, path: str, expected_status: int, api_template: str | None = None, source: str = "generic_auto") -> dict:
    return {"probe_id": probe_id, "title": title, "probe_type": probe_type, "risk_type": risk_type, "severity": severity, "actor": actor, "method": method, "path": path, "api_template": api_template or f"{method} {path.split('?')[0]}", "source": source, "expected_status": expected_status, "evidence_required": ["actor_role", "request", "response_status", "response_body"], "expected": f"HTTP {expected_status} 或业务状态保持一致", "bug_signal": "实际响应违反业务不变量"}


def load_high_value_pattern_memory(workspace: Path) -> dict:
    path = workspace / "high_value_pattern_memory.json"
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if isinstance(data, dict) and data.get("version") == "high_value_pattern_memory_v1":
        return data
    return {}


def load_risk_learning_profile(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "risk_learning_profile.json", *(([output / "risk_learning_profile.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "enterprise_high_value_risk_learning_v1":
            return data
    return {}


def load_high_value_attack_plan(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "high_value_attack_plan.json", *(([output / "high_value_attack_plan.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "high_value_attack_plan_v1":
            return data
    return {}


def load_high_value_capability_assessment(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "high_value_capability_assessment.json", *(([output / "high_value_capability_assessment.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "high_value_capability_assessment_v1":
            return data
    return {}


def load_high_value_capability_trend(workspace: Path, output: Path | None = None) -> dict:
    for path in [workspace / "high_value_capability_trend.json", *(([output / "high_value_capability_trend.json"] if output else []))]:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("version") == "high_value_capability_trend_v1":
            return data
    return {}


def load_business_adaptation_profile(workspace: Path, output_root: Path, project: str) -> dict:
    candidates = [
        workspace / "business_adaptation_profile.json",
        output_root / project / "business_adaptation" / "business_adaptation_profile.json",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(data, dict) and data.get("adaptive_risk_matrix"):
            return data
    return {}


def build_lightweight_business_adaptation_profile(business_model: dict) -> dict:
    industry = str(business_model.get("industry") or "generic_enterprise_software")
    operations = business_model.get("operations", []) or []
    domain_name = {
        "ecommerce": "电商 / 交易履约",
        "finance": "金融 / 资金账户",
        "crm": "CRM / 销售客户",
        "erp": "ERP / 供应链",
        "healthcare": "医疗 / 患者诊疗",
        "education": "教育 / 课程考试",
    }.get(industry, "通用企业软件")
    risk_rows: dict[str, dict] = {}
    for op in operations:
        for raw_risk in op.get("risk_hints", []) or []:
            risk = business_adaptation_executable_risk(str(raw_risk))
            row = risk_rows.setdefault(
                risk,
                {
                    "risk_type": risk,
                    "domain": industry,
                    "severity": knowledge_probe_severity(risk),
                    "title": f"{risk} 行业业务风险",
                    "expected": knowledge_expected_statement(risk, op),
                    "bug_signal": knowledge_bug_signal(risk),
                    "matched_operations": [],
                },
            )
            row["matched_operations"].append(operation_api_template(op))
    matrix = []
    for row in risk_rows.values():
        row["matched_operations"] = sorted(set(row["matched_operations"]))[:12]
        row["matched_operation_count"] = len(row["matched_operations"])
        matrix.append(row)
    matrix.sort(key=lambda item: (item.get("severity") != "P0", -item.get("matched_operation_count", 0), item.get("risk_type", "")))
    return {
        "phase": "lightweight_business_adaptation_from_defect_discovery",
        "business_domain_mode": "auto_from_single_input",
        "selected_domains": [{"domain": industry, "name": domain_name, "score": max(1, len(matrix)), "risks": [row["risk_type"] for row in matrix[:12]]}],
        "operation_count": len(operations),
        "endpoint_domain_map": [
            {
                "method": op.get("method"),
                "path": op.get("path"),
                "matched_domains": [industry],
                "risk_types": [business_adaptation_executable_risk(r) for r in op.get("risk_hints", []) or []],
            }
            for op in operations
        ],
        "adaptive_risk_matrix": matrix,
        "governance": {"uses_only_current_prd_openapi_accounts": True, "manual_domain_pack_required": False},
    }


class DefectDiscoveryRunner:
    def __init__(self, config: DiscoveryConfig):
        self.config = config
        self.workspace = config.workspace_root / config.project / "defect_discovery"
        self.output = config.output_root / config.project / "defect_discovery"
        self.workspace.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)

    def run(self) -> dict:
        prd = read_text(self.config.public_artifacts / "prd.md")
        openapi = read_json(self.config.public_artifacts / "openapi.json")
        sut_config = read_json(self.config.public_artifacts / "sut_config.json")
        accounts = read_json(self.config.public_artifacts / "test_accounts.json")
        business_model = infer_business_model(prd, openapi, accounts)
        business_model["_source_prd"] = prd
        previous_capability_assessment = load_high_value_capability_assessment(self.workspace, self.output)
        previous_capability_trend = load_high_value_capability_trend(self.workspace, self.output)
        business_model["high_value_pattern_memory"] = load_high_value_pattern_memory(self.workspace)
        business_model["risk_learning_profile"] = load_risk_learning_profile(self.workspace, self.output)
        business_model["high_value_attack_plan"] = load_high_value_attack_plan(self.workspace, self.output)
        business_model["high_value_capability_assessment"] = previous_capability_assessment
        business_model["business_adaptation_profile"] = load_business_adaptation_profile(self.workspace, self.config.output_root, self.config.project)
        if not business_model["business_adaptation_profile"]:
            business_model["business_adaptation_profile"] = build_lightweight_business_adaptation_profile(business_model)
        business_knowledge_model = load_business_knowledge_model(self.config)
        business_model = enrich_business_model_with_knowledge(business_model, business_knowledge_model)
        rules = extract_business_rules(prd, openapi)
        invariants = build_invariants(rules)
        discovery_mode = normalize_discovery_mode(self.config.discovery_mode)
        probe_policy_profile = normalize_probe_policy_profile(os.environ.get("PROBE_POLICY_PROFILE"), discovery_mode)
        probes = generate_defect_probes(invariants, business_model, discovery_mode)
        scenario_coverage = evaluate_scenario_coverage(business_model["business_scenarios"], probes, accounts)
        readiness = build_execution_readiness_plan(business_model, scenario_coverage, probes, accounts)
        data_orchestration = build_scenario_data_orchestration(readiness, accounts)
        user_preparation = build_enterprise_user_preparation_guide(readiness, data_orchestration)
        probe_strategy = {
            "mode": "zero_config_auto_probe_generation",
            "discovery_mode": discovery_mode,
            "probe_policy_profile": probe_policy_profile,
            "allowed_probe_sources": sorted(allowed_sources_for_policy(probe_policy_profile, discovery_mode)),
            "manual_industry_pack_required": False,
            "business_knowledge_enabled": business_model["enterprise_knowledge"]["enabled"],
            "business_knowledge_source": business_model["enterprise_knowledge"]["source"],
            "business_knowledge_module_count": business_model["enterprise_knowledge"]["module_count"],
            "business_knowledge_risk_count": business_model["enterprise_knowledge"]["risk_count"],
            "business_knowledge_rule_count": business_model["enterprise_knowledge"]["rule_count"],
            "high_value_memory_enabled": bool((business_model.get("high_value_pattern_memory") or {}).get("top_patterns")),
            "high_value_memory_pattern_count": int((business_model.get("high_value_pattern_memory") or {}).get("pattern_count") or 0),
            "risk_learning_profile_enabled": bool(business_model.get("risk_learning_profile")),
            "risk_learning_profile_sample_count": int((business_model.get("risk_learning_profile") or {}).get("learned_from_findings") or 0),
            "high_value_attack_plan_enabled": bool(business_model.get("high_value_attack_plan")),
            "high_value_attack_plan_focus_count": int((business_model.get("high_value_attack_plan") or {}).get("total_focus_risks") or 0),
            "high_value_capability_assessment_enabled": bool(business_model.get("high_value_capability_assessment")),
            "high_value_capability_gap_count": len((business_model.get("high_value_capability_assessment") or {}).get("capability_gaps") or []),
            "business_adaptation_enabled": bool(business_model.get("business_adaptation_profile")),
            "business_adaptation_domain_count": len((business_model.get("business_adaptation_profile") or {}).get("selected_domains") or []),
            "inferred_industry": business_model["industry"],
            "business_object_count": len(business_model["business_objects"]),
            "operation_count": len(business_model["operations"]),
            "auto_invariant_count": len(business_model["inferred_invariants"]),
            "semantic_graph_nodes": len(business_model["semantic_graph"]["nodes"]),
            "semantic_graph_edges": len(business_model["semantic_graph"]["edges"]),
            "state_machine_count": len(business_model["state_machines"]),
            "data_lineage_count": len(business_model["data_lineage"]),
            "entity_dependency_count": len(business_model["entity_dependencies"]),
            "business_scenario_count": len(business_model["business_scenarios"]),
            "scenario_coverage_rate": scenario_coverage["coverage_rate"],
            "executable_scenario_rate": scenario_coverage["executable_rate"],
            "auto_preparable_scenarios": readiness["execution_readiness_plan"]["auto_preparable_scenarios"],
            "auto_orchestratable_scenarios": data_orchestration["auto_orchestratable_scenarios"],
            "enterprise_user_action_count": user_preparation["user_action_count"],
            "testability_gap_count": len(readiness["testability_gaps"]),
            "probe_count": len(probes),
            "probe_execution_budget": os.environ.get("PROBE_EXECUTION_BUDGET", ""),
            "probe_parallel_workers": os.environ.get("PROBE_PARALLEL_WORKERS", "1"),
            "probe_timeout_ms": os.environ.get("PROBE_TIMEOUT_MS", "8000"),
            "probe_budget_policy_path": os.environ.get("PROBE_BUDGET_POLICY_PATH", ""),
            "generic_probe_count": sum(1 for p in probes if p.get("source") == "generic_auto"),
            "journey_probe_count": sum(1 for p in probes if p.get("source") == "journey_auto"),
            "feedback_learning_probe_count": sum(1 for p in probes if p.get("source") == "feedback_learning"),
            "adaptive_policy_probe_count": sum(1 for p in probes if p.get("source") == "adaptive_policy"),
            "business_knowledge_probe_count": sum(1 for p in probes if p.get("source") == "business_knowledge"),
            "business_adaptation_probe_count": sum(1 for p in probes if p.get("source") == "business_adaptation_layer"),
            "high_value_memory_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory"),
            "high_value_memory_expansion_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory" and p.get("memory_context", {}).get("memory_variant") == "semantic_expansion"),
            "risk_learning_profile_probe_count": sum(1 for p in probes if p.get("source") == "risk_learning_profile"),
            "high_value_attack_plan_probe_count": sum(1 for p in probes if p.get("source") == "high_value_attack_plan"),
            "capability_gap_probe_count": sum(1 for p in probes if p.get("source") == "capability_gap"),
            "oracle_gap_probe_count": sum(1 for p in probes if p.get("source") == "oracle_gap"),
            "feedback_adjusted_probe_count": sum(1 for p in probes if p.get("source") == "feedback_adjusted"),
            "rag_enhanced_probe_count": sum(1 for p in probes if p.get("source") == "rag_enhanced"),
            "probe_policy_profile": probe_policy_profile,
            "allowed_probe_sources": sorted(allowed_sources_for_policy(probe_policy_profile, discovery_mode)),
            "note": "Probe sources are derived from project inputs and policy data. PROBE_POLICY_PROFILE controls the source mix.",
        }
        write_json(self.workspace / "business_model.json", business_model)
        write_json(self.workspace / "business_scenarios.json", business_model["business_scenarios"])
        write_json(self.workspace / "scenario_coverage.json", scenario_coverage)
        write_json(self.workspace / "test_data_requirements.json", {"items": readiness["test_data_requirements"]})
        write_json(self.workspace / "testability_gaps.json", {"items": readiness["testability_gaps"]})
        write_json(self.workspace / "execution_readiness_plan.json", readiness["execution_readiness_plan"])
        write_json(self.workspace / "scenario_data_orchestration.json", data_orchestration)
        write_json(self.workspace / "enterprise_user_preparation_guide.json", user_preparation)
        write_json(self.workspace / "business_rules.json", rules)
        write_json(self.workspace / "invariants.json", invariants)
        write_json(self.workspace / "defect_probes.json", probes)
        write_json(self.workspace / "business_knowledge_probes.json", {"count": sum(1 for p in probes if p.get("source") == "business_knowledge"), "items": [p for p in probes if p.get("source") == "business_knowledge"]})
        write_json(self.workspace / "business_adaptation_probes.json", {"count": sum(1 for p in probes if p.get("source") == "business_adaptation_layer"), "items": [p for p in probes if p.get("source") == "business_adaptation_layer"]})
        write_json(self.workspace / "high_value_memory_probes.json", {"count": sum(1 for p in probes if p.get("source") == "high_value_memory"), "items": [p for p in probes if p.get("source") == "high_value_memory"]})
        write_json(self.workspace / "risk_learning_profile_probes.json", {"count": sum(1 for p in probes if p.get("source") == "risk_learning_profile"), "items": [p for p in probes if p.get("source") == "risk_learning_profile"]})
        write_json(self.workspace / "high_value_attack_plan_probes.json", {"count": sum(1 for p in probes if p.get("source") == "high_value_attack_plan"), "items": [p for p in probes if p.get("source") == "high_value_attack_plan"]})
        write_json(self.workspace / "capability_gap_probes.json", {"count": sum(1 for p in probes if p.get("source") == "capability_gap"), "items": [p for p in probes if p.get("source") == "capability_gap"]})
        write_json(self.workspace / "oracle_gap_probes.json", {"count": sum(1 for p in probes if p.get("source") == "oracle_gap"), "items": [p for p in probes if p.get("source") == "oracle_gap"]})
        write_json(self.workspace / "feedback_learning_probes.json", {"count": sum(1 for p in probes if p.get("source") == "feedback_learning"), "items": [p for p in probes if p.get("source") == "feedback_learning"]})
        write_json(self.workspace / "adaptive_policy_probes.json", {"count": sum(1 for p in probes if p.get("source") == "adaptive_policy"), "items": [p for p in probes if p.get("source") == "adaptive_policy"]})
        write_json(self.workspace / "rag_enhanced_probes.json", {"count": sum(1 for p in probes if p.get("source") == "rag_enhanced"), "items": [p for p in probes if p.get("source") == "rag_enhanced"], "summary": summarize_rag_probes(probes)})
        write_json(self.workspace / "probe_generation_strategy.json", probe_strategy)
        client = HttpClient(sut_config["base_url"])
        execution = self.execute_probes(client, accounts, probes)
        discovered = select_discovered_bugs(execution)
        candidate_findings = [to_discovered_bug(item) for item in execution if item["assertion_result"] == "failed"]
        high_value_summary = build_high_value_summary(discovered)
        oracle_coverage_summary = build_oracle_coverage_summary(probes, discovered)
        high_value_pattern_memory = build_high_value_pattern_memory(discovered)
        risk_learning_profile = build_risk_learning_profile(discovered, high_value_summary, probe_strategy, high_value_pattern_memory)
        high_value_attack_plan = build_high_value_attack_plan(discovered, high_value_summary, oracle_coverage_summary, risk_learning_profile)
        high_value_issue_clusters = build_high_value_issue_clusters(discovered)
        business_risk_radar = build_business_risk_radar(high_value_summary, high_value_issue_clusters, oracle_coverage_summary)
        enterprise_release_gate_decision = build_enterprise_release_gate_decision(high_value_summary, high_value_issue_clusters, oracle_coverage_summary, high_value_attack_plan)
        cluster_fix_verification_plan = build_cluster_fix_verification_plan(enterprise_release_gate_decision, high_value_issue_clusters)
        high_value_capability_assessment = build_high_value_capability_assessment(high_value_summary, oracle_coverage_summary, high_value_issue_clusters, enterprise_release_gate_decision, cluster_fix_verification_plan, probe_strategy)
        high_value_self_improvement_report = build_high_value_self_improvement_report(previous_capability_assessment, high_value_capability_assessment, high_value_summary, probe_strategy)
        high_value_capability_trend = build_high_value_capability_trend(previous_capability_trend, high_value_capability_assessment, high_value_self_improvement_report, probe_strategy)
        bundle = [to_evidence(item) for item in execution]
        high_value_repro_evidence_pack = build_high_value_repro_evidence_pack(discovered, high_value_issue_clusters, business_risk_radar, cluster_fix_verification_plan, bundle)
        enterprise_bug_triage_matrix = build_enterprise_bug_triage_matrix(high_value_issue_clusters, business_risk_radar, enterprise_release_gate_decision, high_value_repro_evidence_pack)
        data = {
            "project": self.config.project,
            "discovery_mode": discovery_mode,
            "probe_policy_profile": probe_policy_profile,
            "rules": len(rules),
            "business_model": {
                "industry": business_model["industry"],
                "objects": len(business_model["business_objects"]),
                "operations": len(business_model["operations"]),
                "auto_invariants": len(business_model["inferred_invariants"]),
                "semantic_graph_nodes": len(business_model["semantic_graph"]["nodes"]),
                "semantic_graph_edges": len(business_model["semantic_graph"]["edges"]),
                "state_machines": len(business_model["state_machines"]),
                "data_lineage": len(business_model["data_lineage"]),
                "entity_dependencies": len(business_model["entity_dependencies"]),
                "business_scenarios": len(business_model["business_scenarios"]),
                "scenario_coverage_rate": scenario_coverage["coverage_rate"],
                "executable_scenario_rate": scenario_coverage["executable_rate"],
                "auto_preparable_scenarios": readiness["execution_readiness_plan"]["auto_preparable_scenarios"],
                "auto_orchestratable_scenarios": data_orchestration["auto_orchestratable_scenarios"],
                "enterprise_user_action_count": user_preparation["user_action_count"],
                "testability_gaps": len(readiness["testability_gaps"]),
                "business_knowledge_enabled": business_model["enterprise_knowledge"]["enabled"],
                "business_knowledge_modules": business_model["enterprise_knowledge"]["module_count"],
                "business_knowledge_risks": business_model["enterprise_knowledge"]["risk_count"],
                "business_knowledge_rules": business_model["enterprise_knowledge"]["rule_count"],
                "manual_industry_pack_required": False,
            },
            "invariants": len(invariants),
            "probes": len(probes),
            "business_knowledge_probe_count": sum(1 for p in probes if p.get("source") == "business_knowledge"),
            "business_adaptation_probe_count": sum(1 for p in probes if p.get("source") == "business_adaptation_layer"),
            "high_value_memory_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory"),
            "high_value_memory_expansion_probe_count": sum(1 for p in probes if p.get("source") == "high_value_memory" and p.get("memory_context", {}).get("memory_variant") == "semantic_expansion"),
            "risk_learning_profile_probe_count": sum(1 for p in probes if p.get("source") == "risk_learning_profile"),
            "high_value_attack_plan_probe_count": sum(1 for p in probes if p.get("source") == "high_value_attack_plan"),
            "capability_gap_probe_count": sum(1 for p in probes if p.get("source") == "capability_gap"),
            "oracle_gap_probe_count": sum(1 for p in probes if p.get("source") == "oracle_gap"),
            "generic_probe_count": sum(1 for p in probes if p.get("source") == "generic_auto"),
            "journey_probe_count": sum(1 for p in probes if p.get("source") == "journey_auto"),
            "feedback_learning_probe_count": sum(1 for p in probes if p.get("source") == "feedback_learning"),
            "adaptive_policy_probe_count": sum(1 for p in probes if p.get("source") == "adaptive_policy"),
            "rag_enhanced_probe_count": sum(1 for p in probes if p.get("source") == "rag_enhanced"),
            "discovered_bugs": discovered,
            "candidate_findings": len(candidate_findings),
            "deduplicated_findings": max(0, len(candidate_findings) - len(discovered)),
            "high_value_summary": high_value_summary,
            "oracle_coverage_summary": oracle_coverage_summary,
            "high_value_pattern_memory": high_value_pattern_memory,
            "risk_learning_profile": risk_learning_profile,
            "high_value_attack_plan": high_value_attack_plan,
            "high_value_issue_clusters": high_value_issue_clusters,
            "business_risk_radar": business_risk_radar,
            "enterprise_release_gate_decision": enterprise_release_gate_decision,
            "cluster_fix_verification_plan": cluster_fix_verification_plan,
            "high_value_repro_evidence_pack": high_value_repro_evidence_pack,
            "enterprise_bug_triage_matrix": enterprise_bug_triage_matrix,
            "high_value_capability_assessment": high_value_capability_assessment,
            "high_value_self_improvement_report": high_value_self_improvement_report,
            "high_value_capability_trend": high_value_capability_trend,
            "evidence_bundle": bundle,
            "scenario_coverage": scenario_coverage,
            "test_data_requirements": readiness["test_data_requirements"],
            "testability_gaps": readiness["testability_gaps"],
            "execution_readiness_plan": readiness["execution_readiness_plan"],
            "scenario_data_orchestration": data_orchestration,
            "enterprise_user_preparation_guide": user_preparation,
            "roi_metrics": roi_metrics(len(probes), len(discovered)),
        }
        write_json(self.workspace / "probe_execution_result.json", execution)
        write_json(self.workspace / "high_value_pattern_memory.json", high_value_pattern_memory)
        write_json(self.workspace / "risk_learning_profile.json", risk_learning_profile)
        write_json(self.workspace / "high_value_attack_plan.json", high_value_attack_plan)
        write_json(self.workspace / "high_value_issue_clusters.json", high_value_issue_clusters)
        write_json(self.workspace / "business_risk_radar.json", business_risk_radar)
        write_json(self.workspace / "enterprise_release_gate_decision.json", enterprise_release_gate_decision)
        write_json(self.workspace / "cluster_fix_verification_plan.json", cluster_fix_verification_plan)
        write_json(self.workspace / "high_value_repro_evidence_pack.json", high_value_repro_evidence_pack)
        write_json(self.workspace / "enterprise_bug_triage_matrix.json", enterprise_bug_triage_matrix)
        write_json(self.workspace / "high_value_capability_assessment.json", high_value_capability_assessment)
        write_json(self.workspace / "high_value_self_improvement_report.json", high_value_self_improvement_report)
        write_json(self.workspace / "high_value_capability_trend.json", high_value_capability_trend)
        write_json(self.workspace / "fix_regression_probes.json", cluster_fix_verification_plan["regression_probes"])
        write_json(self.workspace / "audit_logs.json", {"private_paths_accessed": [], "blocked_tokens": list(PRIVATE_BLOCKLIST)})
        write_json(self.output / "discovered_bugs.json", {"count": len(discovered), "discovery_mode": discovery_mode, "probe_policy_profile": probe_policy_profile, "bugs": discovered})
        write_json(self.output / "high_value_defect_summary.json", high_value_summary)
        write_json(self.output / "oracle_coverage_summary.json", oracle_coverage_summary)
        write_json(self.output / "high_value_pattern_memory.json", high_value_pattern_memory)
        write_json(self.output / "risk_learning_profile.json", risk_learning_profile)
        write_json(self.output / "high_value_attack_plan.json", high_value_attack_plan)
        write_json(self.output / "high_value_issue_clusters.json", high_value_issue_clusters)
        write_json(self.output / "business_risk_radar.json", business_risk_radar)
        write_json(self.output / "enterprise_release_gate_decision.json", enterprise_release_gate_decision)
        write_json(self.output / "cluster_fix_verification_plan.json", cluster_fix_verification_plan)
        write_json(self.output / "high_value_repro_evidence_pack.json", high_value_repro_evidence_pack)
        write_json(self.output / "enterprise_bug_triage_matrix.json", enterprise_bug_triage_matrix)
        write_json(self.output / "high_value_capability_assessment.json", high_value_capability_assessment)
        write_json(self.output / "high_value_self_improvement_report.json", high_value_self_improvement_report)
        write_json(self.output / "high_value_capability_trend.json", high_value_capability_trend)
        write_json(self.output / "candidate_findings.json", {"count": len(candidate_findings), "bugs": candidate_findings})
        write_json(self.output / "evidence_bundle.json", {"count": len(bundle), "items": bundle})
        write_json(self.output / "defect_discovery_data.json", data)
        write_json(self.output / "roi_metrics.json", data["roi_metrics"])
        (self.output / "bug_drafts.md").write_text(build_bug_drafts(discovered), encoding="utf-8")
        (self.output / "defect_discovery_report.html").write_text(build_report(data), encoding="utf-8")
        return data

    def execute_probes(self, client: HttpClient, accounts: dict, probes: list[dict]) -> list[dict]:
        workers_raw = os.environ.get("PROBE_PARALLEL_WORKERS", "1").strip()
        try:
            max_workers = max(1, int(workers_raw or "1"))
        except Exception:
            max_workers = 1
        timeout_raw = os.environ.get("PROBE_TIMEOUT_MS", "8000").strip()
        try:
            timeout_ms = max(1000, int(timeout_raw or "8000"))
        except Exception:
            timeout_ms = 8000

        def run_one(index_item: tuple[int, dict]) -> tuple[int, dict]:
            index, item = index_item
            local_client = HttpClient(client.base_url)
            start = time.time()
            try:
                local_client.request("POST", "/reset")
                tokens = login_accounts(local_client, accounts)
                result = execute_probe(local_client, tokens, item)
                result["execution_status"] = "completed"
            except Exception as exc:
                result = {
                    "probe": item,
                    "request": {"method": item.get("method"), "path": item.get("path")},
                    "response": {"status_code": 0, "body": {"error": str(exc)[:500]}, "duration_ms": 0},
                    "expected": item.get("expected"),
                    "actual": str(exc)[:500],
                    "assertion_result": "error",
                    "bug_signal": "probe execution error",
                    "confidence": 0.0,
                    "execution_status": "error",
                }
            result["execution_duration_ms"] = round((time.time() - start) * 1000, 2)
            result["execution_worker_mode"] = "parallel" if max_workers > 1 else "sequential"
            result["execution_timeout_ms"] = timeout_ms
            return index, result

        if max_workers <= 1:
            return [run_one((idx, item))[1] for idx, item in enumerate(probes)]

        # Phase11: opt-in parallel probe runner. For shared-state SUTs this is
        # intended for benchmark profiling and should be used with moderate worker
        # counts or isolated SUT instances. Results are restored to original probe
        # order so evaluator output remains stable.
        results: list[dict | None] = [None] * len(probes)
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = [pool.submit(run_one, (idx, item)) for idx, item in enumerate(probes)]
            for fut in as_completed(futures):
                idx, result = fut.result()
                results[idx] = result
        return [r for r in results if r is not None]


def login_accounts(client: HttpClient, accounts: dict) -> dict[str, str]:
    del client
    tokens: dict[str, str] = {}
    for account in accounts.get("accounts", []):
        if not isinstance(account, dict):
            continue
        status = str(account.get("status") or account.get("account_status") or "active").lower()
        if status in {"disabled", "locked", "inactive", "suspended"}:
            continue
        token = str(account.get("access_token") or account.get("token") or "").strip()
        role = str(account.get("role") or "").strip()
        username = str(account.get("username") or account.get("account_ref") or "").strip()
        if not token or not role:
            continue
        tokens.setdefault(role, token)
        if username:
            tokens[username] = token
    return tokens


def execute_probe(client: HttpClient, tokens: dict[str, str], item: dict) -> dict:
    method = str(item.get("method") or "GET").upper()
    steps = [step for step in item.get("steps", []) if isinstance(step, dict)]
    has_write = method in {"POST", "PUT", "PATCH", "DELETE"} or any(
        str(step.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        for step in steps
    )
    if has_write:
        return {
            "probe": item,
            "request": {
                "method": method,
                "path": str(item.get("path") or ""),
                "body": None,
                "actor": str(item.get("actor") or ""),
                "sent": False,
            },
            "response": {"status_code": 0, "body": {}},
            "expected": item.get("expected"),
            "actual": "legacy write path is not governed by Experiment Executor",
            "assertion_result": "blocked",
            "execution_status": "blocked",
            "reason_code": "BLOCKED_UNGOVERNED_LEGACY_WRITE",
            "bug_signal": item.get("bug_signal"),
            "confidence": 0.0,
        }
    path = str(item.get("path") or "")
    if "{" in path or "}" in path or ":" in path:
        return {
            "probe": item,
            "request": {"method": method, "path": path, "body": None, "sent": False},
            "response": {"status_code": 0, "body": {}},
            "expected": item.get("expected"),
            "actual": "source path binding is unresolved",
            "assertion_result": "blocked",
            "execution_status": "blocked",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "bug_signal": item.get("bug_signal"),
            "confidence": 0.0,
        }
    actor = str(item.get("actor") or "")
    token = None if actor in {"anonymous", "system"} else tokens.get(actor) or tokens.get("normal_user")
    return execute_generic_probe(client, token, item, {})

def execute_generic_probe(client: HttpClient, token: str | None, item: dict, body: dict) -> dict:
    response = client.request(item["method"], item["path"], token=token, body=body if item["method"] != "GET" else None)
    risk = item["risk_type"]
    if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation"}:
        failed = response["status_code"] < 400
        actual = response["status_code"]
    elif risk == "idempotency":
        second = client.request(item["method"], item["path"], token=token, body=body if item["method"] != "GET" else None)
        failed = idempotency_changed(response, second)
        actual = {"first": response["body"], "second": second["body"]}
        response = second
    elif risk == "time_concurrency":
        failed = response["status_code"] >= 500 or business_signal_suspicious(response["body"])
        actual = {"status_code": response["status_code"], "body": response["body"]}
    elif risk in {"money_consistency", "stock_consistency", "coupon_abuse", "state_flow", "state_consistency", "refund_abuse", "payment_callback", "batch_import", "notification_risk", "integration_risk"}:
        # For blind probes, a successful response to an adversarial input is itself
        # a signal when the probe expected rejection (400/403/409). Business payload
        # signals are still used for consistency checks that return 200.
        failed = (item.get("expected_status") in {400, 401, 403, 409} and response["status_code"] < 400) or (response["status_code"] < 400 and business_signal_suspicious(response["body"]))
        actual = {"status_code": response["status_code"], "body": response["body"]}
    elif risk in {"approval_bypass", "audit_compliance", "search_report", "configuration_risk", "privilege_escalation", "locked_account_bypass"}:
        failed = response["status_code"] < 400
        actual = {"status_code": response["status_code"], "body": response["body"]}
    else:
        failed = response["status_code"] != item["expected_status"]
        actual = response["status_code"]
    return {"probe": item, "request": {"method": item["method"], "path": item["path"], "body": body, "actor": item["actor"]}, "response": response, "expected": item["expected"], "actual": actual, "assertion_result": "failed" if failed else "passed", "bug_signal": item["bug_signal"], "confidence": 0.84 if failed else 0.62}


def idempotency_changed(first: dict, second: dict) -> bool:
    a = first.get("body") or {}
    b = second.get("body") or {}
    for key in ("order_id", "id", "payment_id", "refund_id"):
        if a.get(key) and b.get(key) and a.get(key) != b.get(key):
            return True
    for key in ("paid_amount", "refunded_amount", "stock", "balance", "amount"):
        if isinstance(a.get(key), (int, float)) and isinstance(b.get(key), (int, float)) and a.get(key) != b.get(key):
            return True
    return first.get("status_code") in {200, 201} and second.get("status_code") in {200, 201} and a != b


def business_signal_suspicious(body: object) -> bool:
    if not isinstance(body, dict):
        return False
    numeric_keys = ("total_amount", "payable_amount", "stock", "balance", "amount", "paid_amount", "refunded_amount")
    for key in numeric_keys:
        value = body.get(key)
        if isinstance(value, (int, float)) and value < 0:
            return True
    status = str(body.get("status") or "").lower()
    if status in {"cancelled_pending"}:
        return True
    return any(key in body for key in ("warning", "bug_signal"))


def body_for_probe(item: dict) -> dict:
    example = item.get("request_example")
    return dict(example) if isinstance(example, dict) else {}


def generic_body_for_probe(item: dict) -> dict:
    return body_for_probe(item)


def predicted_template_for_probe(p: dict) -> str:
    explicit = str(p.get("predicted_template_id") or "").strip()
    if explicit:
        return explicit
    risk = re.sub(r"[^A-Z0-9]+", "_", str(p.get("risk_type") or "generic").upper()).strip("_")
    method = re.sub(r"[^A-Z0-9]+", "_", str(p.get("method") or "UNKNOWN").upper()).strip("_")
    path = str(p.get("path") or "").split("?", 1)[0]
    route = re.sub(r"[^A-Z0-9]+", "_", path.upper()).strip("_") or "ROOT"
    return f"SOURCE_{risk or 'GENERIC'}_{method or 'UNKNOWN'}_{route[:80]}"


def evidence_signature_for(item: dict) -> str:
    p = item["probe"]
    status = item.get("response", {}).get("status_code")
    return f"{p.get('risk_type')}|{p.get('actor')}|{p.get('method')}|{str(p.get('path','')).split('?')[0]}|{status}"


def business_object_for_api(api: str) -> str:
    path = api.split(" ", 1)[-1].split("?", 1)[0].strip().lower()
    infrastructure = {"api", "service", "services", "admin", "manage", "manager", "console"}
    for segment in path.strip("/").split("/"):
        if not segment or segment.startswith(("{", ":")):
            continue
        if segment in infrastructure or re.fullmatch(r"v\d+", segment):
            continue
        return segment
    return "root"


def operation_for_method(method: str, path: str) -> str:
    text = path.lower()
    if "cancel" in text:
        return "cancel"
    if "callback" in text:
        return "callback"
    if "refund" in text:
        return "refund"
    if method.upper() == "GET":
        return "view"
    if method.upper() == "POST":
        return "create"
    return method.lower()


def to_discovered_bug(item: dict) -> dict:
    p = item["probe"]
    profile = high_value_profile(item)
    score = profile["total_score"]
    api = p.get("api_template") or f"{p['method']} {p['path'].split('?')[0]}"
    bug = {
        "discovered_bug_id": f"DISC_{p['probe_id']}",
        "probe_id": p["probe_id"],
        "title": p["title"],
        "risk_type": p["risk_type"],
        "predicted_risk_type": p["risk_type"],
        "predicted_template_id": predicted_template_for_probe(p),
        "severity": p["severity"],
        "related_apis": [api],
        "affected_api": api,
        "actor": p.get("actor"),
        "operation": operation_for_method(p.get("method", ""), p.get("path", "")),
        "business_object": business_object_for_api(api),
        "expected": item["expected"],
        "actual": item["actual"],
        "bug_signal": item["bug_signal"],
        "evidence_signature": evidence_signature_for(item),
        "confidence": item["confidence"],
        "bug_value_score": score,
        "high_value_profile": profile,
        "value_tier": value_tier(score),
        "evidence_ref": p["probe_id"],
        "discovery_mode": os.environ.get("DEFECT_DISCOVERY_MODE", "blind"),
        "probe_policy_profile": normalize_probe_policy_profile(os.environ.get("PROBE_POLICY_PROFILE"), os.environ.get("DEFECT_DISCOVERY_MODE", "blind")),
        "probe_source": p.get("source"),
    }
    context = item.get("business_context") or p.get("business_context")
    if context:
        bug["business_context"] = context
    memory_context = item.get("memory_context") or p.get("memory_context")
    if memory_context:
        bug["memory_context"] = memory_context
    risk_learning_context = item.get("risk_learning_context") or p.get("risk_learning_context")
    if risk_learning_context:
        bug["risk_learning_context"] = risk_learning_context
    adaptation_context = item.get("business_adaptation_context") or p.get("business_adaptation_context")
    if adaptation_context:
        bug["business_adaptation_context"] = adaptation_context
    oracle_gap_context = item.get("oracle_gap_context") or p.get("oracle_gap_context")
    if oracle_gap_context:
        bug["oracle_gap_context"] = oracle_gap_context
    capability_gap_context = item.get("capability_gap_context") or p.get("capability_gap_context")
    if capability_gap_context:
        bug["capability_gap_context"] = capability_gap_context
    attack_plan_context = item.get("attack_plan_context") or p.get("attack_plan_context")
    if attack_plan_context:
        bug["attack_plan_context"] = attack_plan_context
    return bug


def select_discovered_bugs(execution: list[dict]) -> list[dict]:
    failed = [item for item in execution if item["assertion_result"] == "failed"]
    promoted: list[dict] = []
    covered_exact: set[tuple[str, ...]] = set()
    covered_base_by_pattern: set[tuple[str, str]] = set()
    source_order = {"business_knowledge": 0, "business_adaptation_layer": 1, "risk_learning_profile": 2, "high_value_attack_plan": 3, "capability_gap": 4, "oracle_gap": 5, "high_value_memory": 6, "pattern_library": 7, "feedback_learning": 8, "feedback_adjusted": 9, "adaptive_policy": 10, "rag_enhanced": 11, "generic_auto": 12}
    allowed_sources = set(source_order)
    for item in sorted(failed, key=lambda x: source_order.get(x["probe"].get("source"), 9)):
        source = item["probe"].get("source")
        if source not in allowed_sources:
            continue
        if source == "generic_auto" and item["probe"].get("risk_type") == "time_concurrency":
            continue
        bug = to_discovered_bug(item)
        base_key = discovery_key(bug)
        if source in {"business_knowledge", "business_adaptation_layer", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "high_value_memory", "pattern_library", "feedback_learning", "feedback_adjusted", "adaptive_policy", "rag_enhanced"}:
            exact_key = (bug.get("risk_type", ""), bug.get("related_apis", [""])[0], bug.get("probe_id", ""))
            if exact_key in covered_exact:
                continue
            promoted.append(bug)
            covered_exact.add(exact_key)
            if source == "pattern_library":
                covered_base_by_pattern.add(base_key)
            continue
        if source == "generic_auto" and base_key in covered_base_by_pattern:
            continue
        exact_key = base_key
        if exact_key in covered_exact:
            continue
        promoted.append(bug)
        covered_exact.add(exact_key)
    return promoted

def discovery_key(bug: dict) -> tuple[str, str]:
    api = bug.get("related_apis", [""])[0]
    return bug.get("risk_type", ""), api


def to_evidence(item: dict) -> dict:
    evidence = {"probe_id": item["probe"]["probe_id"], "request": item["request"], "response": {"status_code": item["response"]["status_code"], "body_excerpt": json.dumps(item["response"]["body"], ensure_ascii=False)[:1000]}, "expected": item["expected"], "actual": item["actual"], "assertion_result": item["assertion_result"], "bug_signal": item["bug_signal"], "confidence": item["confidence"]}
    if item.get("business_context"):
        evidence["business_context"] = item["business_context"]
    elif item.get("probe", {}).get("business_context"):
        evidence["business_context"] = item["probe"]["business_context"]
    if item.get("memory_context"):
        evidence["memory_context"] = item["memory_context"]
    elif item.get("probe", {}).get("memory_context"):
        evidence["memory_context"] = item["probe"]["memory_context"]
    if item.get("risk_learning_context"):
        evidence["risk_learning_context"] = item["risk_learning_context"]
    elif item.get("probe", {}).get("risk_learning_context"):
        evidence["risk_learning_context"] = item["probe"]["risk_learning_context"]
    if item.get("business_adaptation_context"):
        evidence["business_adaptation_context"] = item["business_adaptation_context"]
    elif item.get("probe", {}).get("business_adaptation_context"):
        evidence["business_adaptation_context"] = item["probe"]["business_adaptation_context"]
    if item.get("oracle_gap_context"):
        evidence["oracle_gap_context"] = item["oracle_gap_context"]
    elif item.get("probe", {}).get("oracle_gap_context"):
        evidence["oracle_gap_context"] = item["probe"]["oracle_gap_context"]
    if item.get("capability_gap_context"):
        evidence["capability_gap_context"] = item["capability_gap_context"]
    elif item.get("probe", {}).get("capability_gap_context"):
        evidence["capability_gap_context"] = item["probe"]["capability_gap_context"]
    if item.get("attack_plan_context"):
        evidence["attack_plan_context"] = item["attack_plan_context"]
    elif item.get("probe", {}).get("attack_plan_context"):
        evidence["attack_plan_context"] = item["probe"]["attack_plan_context"]
    if "journey_steps" in item:
        evidence["journey_steps"] = [
            {
                "step": step_item["step"],
                "request": step_item["request"],
                "response": {
                    "status_code": step_item["response"]["status_code"],
                    "body_excerpt": json.dumps(step_item["response"]["body"], ensure_ascii=False)[:1000],
                },
            }
            for step_item in item["journey_steps"]
        ]
    return evidence


def high_value_profile(item: dict) -> dict:
    p = item["probe"]
    risk = str(p.get("risk_type") or "")
    source = str(p.get("source") or "")
    context = item.get("business_context") or p.get("business_context") or {}
    has_steps = bool(item.get("journey_steps") or p.get("steps"))
    step_count = len(item.get("journey_steps") or p.get("steps") or [])
    actual = item.get("actual") if isinstance(item.get("actual"), dict) else {}
    has_cross_step_oracle = bool(actual.get("cross_step_oracle") or p.get("cross_step_oracle"))
    evidence_strength = 18 if has_steps else 10
    if item.get("response", {}).get("body") is not None:
        evidence_strength += 4
    if context:
        evidence_strength += 5
    if step_count >= 3:
        evidence_strength += 3
    if has_cross_step_oracle:
        evidence_strength += 4
    business_impact = {
        "permission_bypass": 23,
        "auth_bypass": 22,
        "idor": 24,
        "tenant_isolation": 25,
        "money_consistency": 25,
        "stock_consistency": 23,
        "state_flow": 22,
        "state_consistency": 22,
        "coupon_abuse": 20,
        "refund_abuse": 21,
        "payment_callback": 20,
        "idempotency": 18,
        "boundary_validation": 15,
    }.get(risk, 12)
    source_weight = {
        "business_knowledge": 16,
        "business_adaptation_layer": 16,
        "risk_learning_profile": 16,
        "high_value_attack_plan": 16,
        "capability_gap": 16,
        "oracle_gap": 15,
        "high_value_memory": 15,
        "pattern_library": 13,
        "feedback_learning": 12,
        "feedback_adjusted": 12,
        "adaptive_policy": 11,
        "rag_enhanced": 10,
        "journey_auto": 9,
        "generic_auto": 6,
    }.get(source, 5)
    severity_weight = {"P0": 18, "P1": 12, "P2": 6}.get(str(p.get("severity") or "P2"), 4)
    reproducibility = 12 if item.get("assertion_result") == "failed" else 4
    if step_count >= 2:
        reproducibility += 3
    confidence_weight = int(float(item.get("confidence") or 0) * 10)
    security_financial_bonus = 6 if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation", "money_consistency", "refund_abuse", "payment_callback"} else 0
    total = min(100, business_impact + evidence_strength + source_weight + severity_weight + reproducibility + confidence_weight + security_financial_bonus)
    reasons = []
    if risk in {"permission_bypass", "auth_bypass", "idor", "tenant_isolation"}:
        reasons.append("权限/数据隔离风险")
    if risk in {"money_consistency", "refund_abuse", "payment_callback", "coupon_abuse"}:
        reasons.append("资金/权益损失风险")
    if has_steps:
        reasons.append(f"多步骤链路证据({step_count}步)")
    if has_cross_step_oracle:
        reasons.append("跨步骤业务断言命中")
    if context:
        reasons.append("命中企业业务知识")
    if source == "business_knowledge":
        reasons.append("业务知识驱动探针")
    if source == "business_adaptation_layer":
        reasons.append("业务适配画像驱动探针")
    if source == "risk_learning_profile":
        reasons.append("风险学习画像驱动探针")
    if source == "high_value_attack_plan":
        reasons.append("高价值攻击计划驱动探针")
    if source == "capability_gap":
        reasons.append("能力短板诊断驱动探针")
    if source == "oracle_gap":
        reasons.append("跨步骤 Oracle 覆盖缺口补齐")
    if source == "high_value_memory":
        reasons.append("历史高价值缺陷模式复测")
    return {
        "total_score": total,
        "tier": value_tier(total),
        "business_impact": min(30, business_impact + security_financial_bonus),
        "evidence_strength": min(30, evidence_strength),
        "source_weight": source_weight,
        "severity_weight": severity_weight,
        "reproducibility": min(20, reproducibility),
        "confidence_weight": confidence_weight,
        "step_count": step_count,
        "has_cross_step_oracle": has_cross_step_oracle,
        "has_business_context": bool(context),
        "reasons": reasons or ["接口行为违反预期"],
    }


def value_tier(score: int) -> str:
    if score >= 90:
        return "S"
    if score >= 80:
        return "A"
    if score >= 70:
        return "B"
    return "C"


def build_high_value_summary(bugs: list[dict]) -> dict:
    tier_counts: dict[str, int] = {}
    risk_counts: dict[str, int] = {}
    source_counts: dict[str, int] = {}
    for bug in bugs:
        tier_counts[bug.get("value_tier", "C")] = tier_counts.get(bug.get("value_tier", "C"), 0) + 1
        risk_counts[bug.get("risk_type", "unknown")] = risk_counts.get(bug.get("risk_type", "unknown"), 0) + 1
        source_counts[bug.get("probe_source", "unknown")] = source_counts.get(bug.get("probe_source", "unknown"), 0) + 1
    business_context_hits = sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("has_business_context"))
    journey_evidence_hits = sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("step_count", 0) >= 2)
    cross_step_oracle_hits = sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("has_cross_step_oracle"))
    return {
        "total_findings": len(bugs),
        "s_tier_findings": tier_counts.get("S", 0),
        "a_tier_findings": tier_counts.get("A", 0),
        "s_or_a_tier_findings": tier_counts.get("S", 0) + tier_counts.get("A", 0),
        "business_context_findings": business_context_hits,
        "journey_evidence_findings": journey_evidence_hits,
        "cross_step_oracle_findings": cross_step_oracle_hits,
        "tier_distribution": dict(sorted(tier_counts.items())),
        "risk_distribution": dict(sorted(risk_counts.items(), key=lambda item: item[1], reverse=True)),
        "source_distribution": dict(sorted(source_counts.items(), key=lambda item: item[1], reverse=True)),
        "top_findings": [
            {
                "title": bug.get("title"),
                "risk_type": bug.get("risk_type"),
                "value_tier": bug.get("value_tier"),
                "bug_value_score": bug.get("bug_value_score"),
                "reasons": bug.get("high_value_profile", {}).get("reasons", []),
                "probe_source": bug.get("probe_source"),
            }
            for bug in sorted(bugs, key=lambda item: item.get("bug_value_score", 0), reverse=True)[:10]
        ],
    }


def build_high_value_issue_clusters(bugs: list[dict]) -> dict:
    buckets: dict[tuple[str, str, str], list[dict]] = {}
    for bug in bugs:
        risk = str(bug.get("risk_type") or "unknown")
        template = str(bug.get("predicted_template_id") or risk or "unknown")
        business_object = str(bug.get("business_object") or "unknown")
        buckets.setdefault((risk, template, business_object), []).append(bug)

    severity_rank = {"P0": 3, "P1": 2, "P2": 1}
    source_distribution: dict[str, int] = {}
    risk_distribution: dict[str, int] = {}
    fix_labels = {
        "permission_bypass": "统一收敛服务端权限校验，补齐角色、资源、动作三元校验，并加入越权回归链路。",
        "auth_bypass": "所有受保护接口强制登录态校验，覆盖匿名、过期 token、锁定账号和降权后的访问。",
        "idor": "对象级权限必须在服务端按当前用户/租户二次校验，禁止只依赖前端传入 ID。",
        "tenant_isolation": "租户字段必须来自可信上下文，查询、写入、缓存和导出链路都要做租户隔离断言。",
        "money_consistency": "建立金额守恒校验，覆盖下单、优惠、支付、退款、流水、余额与舍入误差。",
        "stock_consistency": "建立库存/额度守恒校验，覆盖扣减、回滚、重复提交、并发边界和失败补偿。",
        "state_flow": "用状态机白名单约束流转，阻断终态重开、取消后支付、回调乱序等非法路径。",
        "state_consistency": "动作完成后必须做查询一致性校验，覆盖异步同步、审批、支付和退款结果。",
        "coupon_abuse": "权益核销要校验归属、门槛、有效期、叠加、重复使用和金额上限。",
        "refund_abuse": "退款必须绑定原订单、原支付和可退余额，防止重复退、超额退和跨单退。",
        "payment_callback": "支付回调必须验签、验金额、验订单状态并保证幂等。",
        "idempotency": "关键写操作补齐幂等键和重放保护，覆盖下单、支付、退款、回调和审批。",
    }

    clusters = []
    for index, ((risk, template, business_object), items) in enumerate(buckets.items(), start=1):
        ranked = sorted(items, key=lambda bug: (int(bug.get("bug_value_score") or 0), float(bug.get("confidence") or 0)), reverse=True)
        representative = ranked[0]
        affected_apis = sorted({str(api) for bug in items for api in (bug.get("related_apis") or [bug.get("affected_api") or "unknown"]) if api})
        source_counts: dict[str, int] = {}
        for bug in items:
            source = str(bug.get("probe_source") or "unknown")
            source_counts[source] = source_counts.get(source, 0) + 1
        for source in source_counts:
            source_distribution[source] = source_distribution.get(source, 0) + 1
        risk_distribution[risk] = risk_distribution.get(risk, 0) + 1
        max_score = max(int(bug.get("bug_value_score") or 0) for bug in items)
        max_confidence = max(float(bug.get("confidence") or 0) for bug in items)
        max_severity = max((str(bug.get("severity") or "P2") for bug in items), key=lambda item: severity_rank.get(item, 0))
        s_or_a_count = sum(1 for bug in items if bug.get("value_tier") in {"S", "A"})
        has_cross_step_oracle = any(bug.get("high_value_profile", {}).get("has_cross_step_oracle") for bug in items)
        has_business_context = any(bug.get("high_value_profile", {}).get("has_business_context") or bug.get("business_context") for bug in items)
        max_step_count = max(int(bug.get("high_value_profile", {}).get("step_count") or 0) for bug in items)
        evidence_strength = max(int(bug.get("high_value_profile", {}).get("evidence_strength") or 0) for bug in items)
        release_gate = "block" if (max_severity == "P0" or (max_score >= 90 and has_cross_step_oracle)) else "review" if max_score >= 80 else "monitor"
        cluster_id = f"HVIC_{safe_pattern_token(risk)}_{safe_pattern_token(template)}_{index:03d}"
        clusters.append({
            "cluster_id": cluster_id,
            "risk_type": risk,
            "predicted_template_id": template,
            "business_object": business_object,
            "finding_count": len(items),
            "s_or_a_count": s_or_a_count,
            "max_value_score": max_score,
            "max_confidence": round(max_confidence, 3),
            "value_tier": value_tier(max_score),
            "severity": max_severity,
            "affected_apis": affected_apis[:12],
            "affected_api_count": len(affected_apis),
            "probe_sources": dict(sorted(source_counts.items(), key=lambda item: item[1], reverse=True)),
            "has_cross_step_oracle": has_cross_step_oracle,
            "has_business_context": has_business_context,
            "max_step_count": max_step_count,
            "evidence_strength": evidence_strength,
            "representative_title": representative.get("title"),
            "representative_bug_id": representative.get("discovered_bug_id"),
            "representative_probe_id": representative.get("probe_id"),
            "recommended_owner": business_object if business_object != "unknown" else risk,
            "recommended_fix": fix_labels.get(risk, "根据 PRD/OpenAPI 补齐业务不变量、异常状态和跨步骤一致性校验。"),
            "release_gate": release_gate,
        })

    gate_rank = {"block": 3, "review": 2, "monitor": 1}
    clusters.sort(key=lambda item: (gate_rank.get(item["release_gate"], 0), item["max_value_score"], item["finding_count"], item["s_or_a_count"]), reverse=True)
    total = len(bugs)
    return {
        "version": "high_value_issue_clusters_v1",
        "total_findings": total,
        "cluster_count": len(clusters),
        "blocker_cluster_count": sum(1 for item in clusters if item["release_gate"] == "block"),
        "review_cluster_count": sum(1 for item in clusters if item["release_gate"] == "review"),
        "monitor_cluster_count": sum(1 for item in clusters if item["release_gate"] == "monitor"),
        "compression_rate": round(1 - (len(clusters) / total), 3) if total else 0,
        "risk_cluster_distribution": dict(sorted(risk_distribution.items(), key=lambda item: item[1], reverse=True)),
        "source_cluster_distribution": dict(sorted(source_distribution.items(), key=lambda item: item[1], reverse=True)),
        "top_clusters": clusters[:20],
    }


def business_risk_domain_for(risk_type: str) -> tuple[str, str]:
    risk = str(risk_type or "unknown")
    mapping = {
        "permission_bypass": ("access_isolation", "权限与访问隔离"),
        "auth_bypass": ("access_isolation", "权限与访问隔离"),
        "idor": ("access_isolation", "权限与访问隔离"),
        "tenant_isolation": ("access_isolation", "权限与访问隔离"),
        "privilege_escalation": ("access_isolation", "权限与访问隔离"),
        "locked_account_bypass": ("access_isolation", "权限与访问隔离"),
        "money_consistency": ("financial_integrity", "资金与金额一致性"),
        "refund_abuse": ("financial_integrity", "资金与金额一致性"),
        "payment_callback": ("financial_integrity", "资金与金额一致性"),
        "callback_trust": ("financial_integrity", "资金与金额一致性"),
        "stock_consistency": ("capacity_inventory", "库存容量与额度"),
        "quantity_consistency": ("capacity_inventory", "库存容量与额度"),
        "coupon_abuse": ("benefit_abuse", "权益优惠与滥用"),
        "state_flow": ("workflow_state", "状态流转与业务流程"),
        "state_consistency": ("workflow_state", "状态流转与业务流程"),
        "eventual_consistency": ("workflow_state", "状态流转与业务流程"),
        "message_ordering": ("workflow_state", "状态流转与业务流程"),
        "idempotency": ("concurrency_idempotency", "并发幂等与重复提交"),
        "race_condition": ("concurrency_idempotency", "并发幂等与重复提交"),
        "concurrent_update_lost": ("concurrency_idempotency", "并发幂等与重复提交"),
        "webhook_replay": ("concurrency_idempotency", "并发幂等与重复提交"),
        "audit_log_missing": ("audit_compliance", "审计合规与可追溯"),
        "audit_compliance": ("audit_compliance", "审计合规与可追溯"),
        "boundary_validation": ("boundary_validation", "边界校验与输入约束"),
    }
    return mapping.get(risk, ("domain_specific", "行业特定业务风险"))


def build_business_risk_radar(summary: dict, issue_clusters: dict, oracle_coverage: dict) -> dict:
    domain_rows: dict[str, dict] = {}
    for risk, count in (summary.get("risk_distribution") or {}).items():
        domain, label = business_risk_domain_for(str(risk))
        row = domain_rows.setdefault(
            domain,
            {
                "domain": domain,
                "label": label,
                "finding_count": 0,
                "cluster_count": 0,
                "blocker_cluster_count": 0,
                "s_or_a_estimated": 0,
                "risk_types": {},
                "oracle_required_risks": 0,
                "oracle_covered_risks": 0,
                "min_oracle_coverage_rate": None,
            },
        )
        row["finding_count"] += int(count or 0)
        row["risk_types"][str(risk)] = int(count or 0)
    for cluster in issue_clusters.get("top_clusters") or []:
        domain, label = business_risk_domain_for(str(cluster.get("risk_type") or ""))
        row = domain_rows.setdefault(
            domain,
            {
                "domain": domain,
                "label": label,
                "finding_count": 0,
                "cluster_count": 0,
                "blocker_cluster_count": 0,
                "s_or_a_estimated": 0,
                "risk_types": {},
                "oracle_required_risks": 0,
                "oracle_covered_risks": 0,
                "min_oracle_coverage_rate": None,
            },
        )
        row["cluster_count"] += 1
        row["s_or_a_estimated"] += int(cluster.get("s_or_a_count") or 0)
        if cluster.get("release_gate") == "block":
            row["blocker_cluster_count"] += 1
    for coverage in oracle_coverage.get("risk_coverage") or []:
        risk = str(coverage.get("risk_type") or "")
        domain, label = business_risk_domain_for(risk)
        row = domain_rows.setdefault(
            domain,
            {
                "domain": domain,
                "label": label,
                "finding_count": 0,
                "cluster_count": 0,
                "blocker_cluster_count": 0,
                "s_or_a_estimated": 0,
                "risk_types": {},
                "oracle_required_risks": 0,
                "oracle_covered_risks": 0,
                "min_oracle_coverage_rate": None,
            },
        )
        row["oracle_required_risks"] += 1
        if int(coverage.get("oracle_probe_count") or 0) > 0:
            row["oracle_covered_risks"] += 1
        rate = float(coverage.get("oracle_coverage_rate") or 0)
        if row["min_oracle_coverage_rate"] is None or rate < row["min_oracle_coverage_rate"]:
            row["min_oracle_coverage_rate"] = rate
    rows = []
    for row in domain_rows.values():
        oracle_rate = row["min_oracle_coverage_rate"]
        if oracle_rate is None:
            oracle_rate = 1.0 if not row["finding_count"] else 0.0
        row["min_oracle_coverage_rate"] = round(float(oracle_rate), 3)
        exposure = min(100, row["finding_count"] * 2 + row["blocker_cluster_count"] * 10 + row["cluster_count"] * 3)
        confidence = min(100, int(row["min_oracle_coverage_rate"] * 70) + min(30, row["cluster_count"] * 3))
        row["exposure_score"] = int(exposure)
        row["confidence_score"] = int(confidence)
        row["radar_score"] = max(0, min(100, int(exposure * 0.65 + (100 - confidence) * 0.35)))
        if row["blocker_cluster_count"] > 0 or row["radar_score"] >= 70:
            row["priority"] = "P0"
        elif row["cluster_count"] > 0 or row["radar_score"] >= 40:
            row["priority"] = "P1"
        else:
            row["priority"] = "P2"
        row["risk_types"] = dict(sorted(row["risk_types"].items(), key=lambda item: item[1], reverse=True))
        rows.append(row)
    rows.sort(key=lambda item: (item["priority"] == "P0", item["radar_score"], item["finding_count"]), reverse=True)
    return {
        "version": "business_risk_radar_v1",
        "domain_count": len(rows),
        "p0_domain_count": sum(1 for item in rows if item["priority"] == "P0"),
        "p1_domain_count": sum(1 for item in rows if item["priority"] == "P1"),
        "top_domains": rows[:10],
        "radar_summary": [
            f"{item['label']}：{item['finding_count']} 个发现，{item['blocker_cluster_count']} 个阻断问题簇"
            for item in rows[:5]
        ],
    }


def build_enterprise_release_gate_decision(summary: dict, issue_clusters: dict, oracle_coverage: dict, attack_plan: dict) -> dict:
    clusters = list(issue_clusters.get("top_clusters") or [])
    blocker_count = int(issue_clusters.get("blocker_cluster_count") or 0)
    review_count = int(issue_clusters.get("review_cluster_count") or 0)
    total_findings = int(summary.get("total_findings") or issue_clusters.get("total_findings") or 0)
    s_or_a = int(summary.get("s_or_a_tier_findings") or 0)
    oracle_rate = float(oracle_coverage.get("oracle_coverage_rate") or 0)
    compression_rate = float(issue_clusters.get("compression_rate") or 0)
    risk_score = min(
        100,
        blocker_count * 6
        + review_count * 2
        + min(30, s_or_a // 8)
        + (15 if oracle_rate < 0.8 else 8 if oracle_rate < 0.9 else 0),
    )
    if blocker_count > 0:
        gate = "block_release"
        decision = "不建议发布"
    elif review_count > 0 or oracle_rate < 0.9:
        gate = "hold_for_review"
        decision = "人工复核后发布"
    else:
        gate = "allow_release"
        decision = "允许发布"

    release_blockers = [
        {
            "cluster_id": item.get("cluster_id"),
            "risk_type": item.get("risk_type"),
            "business_object": item.get("business_object"),
            "finding_count": item.get("finding_count"),
            "max_value_score": item.get("max_value_score"),
            "affected_apis": item.get("affected_apis", [])[:5],
            "recommended_owner": item.get("recommended_owner"),
            "recommended_fix": item.get("recommended_fix"),
            "representative_bug_id": item.get("representative_bug_id"),
        }
        for item in clusters
        if item.get("release_gate") == "block"
    ][:10]
    fix_queue = []
    for index, item in enumerate(clusters[:15], start=1):
        gate_level = item.get("release_gate") or "review"
        fix_queue.append({
            "rank": index,
            "cluster_id": item.get("cluster_id"),
            "priority": "P0" if gate_level == "block" else "P1" if gate_level == "review" else "P2",
            "release_gate": gate_level,
            "risk_type": item.get("risk_type"),
            "business_object": item.get("business_object"),
            "recommended_owner": item.get("recommended_owner"),
            "finding_count": item.get("finding_count"),
            "affected_api_count": item.get("affected_api_count"),
            "recommended_fix": item.get("recommended_fix"),
            "acceptance_criteria": [
                "修复后同一问题簇的代表探针必须全部通过",
                "相关接口的跨步骤业务 Oracle 必须通过",
                "新增回归用例进入日常迭代套件",
            ],
        })

    blocking_reasons = []
    if blocker_count:
        blocking_reasons.append(f"存在 {blocker_count} 个阻断发布问题簇")
    if s_or_a:
        blocking_reasons.append(f"存在 {s_or_a} 个 S/A 高价值发现")
    if oracle_rate < 0.9:
        blocking_reasons.append(f"跨步骤业务 Oracle 覆盖率 {round(oracle_rate * 100)}%，低于 90% 目标")
    if not blocking_reasons:
        blocking_reasons.append("未发现阻断发布问题簇")

    return {
        "version": "enterprise_release_gate_decision_v1",
        "gate": gate,
        "decision": decision,
        "release_risk_score": risk_score,
        "total_findings": total_findings,
        "s_or_a_tier_findings": s_or_a,
        "issue_cluster_count": int(issue_clusters.get("cluster_count") or 0),
        "blocker_cluster_count": blocker_count,
        "review_cluster_count": review_count,
        "compression_rate": compression_rate,
        "oracle_coverage_rate": oracle_rate,
        "blocking_reasons": blocking_reasons,
        "release_blockers": release_blockers,
        "fix_queue": fix_queue,
        "next_run_probe_budget": attack_plan.get("next_run_probe_budget"),
        "quality_bar": {
            "block_when_blocker_cluster_exists": True,
            "target_oracle_coverage_rate": 0.9,
            "require_regression_for_fixed_clusters": True,
        },
    }


def split_api_signature(api: str) -> tuple[str, str]:
    parts = str(api or "").strip().split(" ", 1)
    if len(parts) == 2 and parts[0].isalpha():
        return parts[0].upper(), parts[1].strip() or "/"
    return "GET", str(api or "/").strip() or "/"


def build_cluster_fix_verification_plan(release_gate_decision: dict, issue_clusters: dict) -> dict:
    cluster_by_id = {str(item.get("cluster_id")): item for item in issue_clusters.get("top_clusters") or [] if item.get("cluster_id")}
    plan_items = []
    regression_probes = []
    for queue_item in release_gate_decision.get("fix_queue") or []:
        cluster_id = str(queue_item.get("cluster_id") or "")
        cluster = cluster_by_id.get(cluster_id, {})
        affected_apis = list(cluster.get("affected_apis") or [])
        if not affected_apis and queue_item.get("affected_apis"):
            affected_apis = list(queue_item.get("affected_apis") or [])
        if not affected_apis:
            affected_apis = ["GET /"]
        priority = str(queue_item.get("priority") or "P1")
        risk_type = str(queue_item.get("risk_type") or cluster.get("risk_type") or "business_risk")
        owner = str(queue_item.get("recommended_owner") or cluster.get("recommended_owner") or queue_item.get("business_object") or "unknown")
        fix = str(queue_item.get("recommended_fix") or cluster.get("recommended_fix") or "修复后必须确认原缺陷信号不再复现。")
        criteria = list(queue_item.get("acceptance_criteria") or [])
        representative_bug_id = str(cluster.get("representative_bug_id") or "")
        verification_id = f"FIX_CLUSTER_{safe_pattern_token(cluster_id)}"
        api_checks = []
        for api_index, api in enumerate(affected_apis[:5], start=1):
            method, path = split_api_signature(api)
            probe_id = f"REG_{safe_pattern_token(cluster_id)}_{api_index:02d}"
            expected = "；".join(criteria) if criteria else "修复后原缺陷信号不应复现，业务规则保持正确。"
            api_checks.append({
                "method": method,
                "path": path,
                "expected_after_fix": expected,
            })
            regression_probes.append({
                "regression_probe_id": probe_id,
                "issue_id": cluster_id,
                "title": f"{risk_type} 修复回归：{path}",
                "risk_type": risk_type,
                "severity": priority,
                "method": method,
                "path": path,
                "actor": "normal_user",
                "expected": expected,
                "source": "high_value_issue_cluster_gate",
                "cluster_id": cluster_id,
                "representative_bug_id": representative_bug_id,
            })
        plan_items.append({
            "verification_id": verification_id,
            "cluster_id": cluster_id,
            "representative_bug_id": representative_bug_id,
            "priority": priority,
            "release_gate": queue_item.get("release_gate"),
            "risk_type": risk_type,
            "business_object": queue_item.get("business_object") or cluster.get("business_object"),
            "recommended_owner": owner,
            "finding_count": queue_item.get("finding_count"),
            "recommended_fix": fix,
            "acceptance_criteria": criteria,
            "api_checks": api_checks,
            "regression_probe_ids": [item["regression_probe_id"] for item in regression_probes if item.get("cluster_id") == cluster_id],
        })

    p0_count = sum(1 for item in plan_items if item.get("priority") == "P0")
    p1_count = sum(1 for item in plan_items if item.get("priority") == "P1")
    return {
        "version": "cluster_fix_verification_plan_v1",
        "source_gate": release_gate_decision.get("gate"),
        "verification_count": len(plan_items),
        "p0_verification_count": p0_count,
        "p1_verification_count": p1_count,
        "regression_probe_count": len(regression_probes),
        "items": plan_items,
        "regression_probes": {
            "version": "fix_regression_probes_from_issue_clusters_v1",
            "items": regression_probes,
        },
    }


def evidence_reproduction_steps(evidence: dict, fallback_api: str) -> list[dict]:
    steps = []
    journey_steps = evidence.get("journey_steps") or []
    if journey_steps:
        for index, step in enumerate(journey_steps[:8], start=1):
            request = step.get("request") if isinstance(step.get("request"), dict) else {}
            response = step.get("response") if isinstance(step.get("response"), dict) else {}
            steps.append({
                "step_no": index,
                "name": step.get("step") or f"step_{index}",
                "method": request.get("method"),
                "url": request.get("url") or request.get("path"),
                "request": request,
                "response_status": response.get("status_code"),
                "response_excerpt": response.get("body_excerpt"),
            })
        return steps
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
    response = evidence.get("response") if isinstance(evidence.get("response"), dict) else {}
    steps.append({
        "step_no": 1,
        "name": "execute_representative_probe",
        "method": request.get("method"),
        "url": request.get("url") or request.get("path") or fallback_api,
        "request": request,
        "response_status": response.get("status_code"),
        "response_excerpt": response.get("body_excerpt"),
    })
    return steps


def build_high_value_repro_evidence_pack(discovered: list[dict], issue_clusters: dict, business_risk_radar: dict, fix_plan: dict, evidence_bundle: list[dict]) -> dict:
    bug_by_id = {str(item.get("discovered_bug_id")): item for item in discovered if item.get("discovered_bug_id")}
    bugs_by_probe = {str(item.get("probe_id")): item for item in discovered if item.get("probe_id")}
    evidence_by_probe = {str(item.get("probe_id")): item for item in evidence_bundle if item.get("probe_id")}
    fix_by_cluster = {str(item.get("cluster_id")): item for item in fix_plan.get("items") or [] if item.get("cluster_id")}
    domain_rows = {str(item.get("domain")): item for item in business_risk_radar.get("top_domains") or [] if item.get("domain")}
    items = []
    domain_summary: dict[str, dict] = {}
    gate_rank = {"block": 3, "review": 2, "monitor": 1}
    clusters = sorted(
        list(issue_clusters.get("top_clusters") or []),
        key=lambda item: (gate_rank.get(str(item.get("release_gate") or ""), 0), int(item.get("max_value_score") or 0), int(item.get("finding_count") or 0)),
        reverse=True,
    )
    for cluster in clusters[:20]:
        cluster_id = str(cluster.get("cluster_id") or "")
        representative_bug = bug_by_id.get(str(cluster.get("representative_bug_id") or ""))
        if not representative_bug and cluster.get("representative_probe_id"):
            representative_bug = bugs_by_probe.get(str(cluster.get("representative_probe_id")))
        if not representative_bug:
            for bug in discovered:
                if str(bug.get("risk_type") or "") == str(cluster.get("risk_type") or "") and str(bug.get("business_object") or "") == str(cluster.get("business_object") or ""):
                    representative_bug = bug
                    break
        representative_bug = representative_bug or {}
        probe_id = str(representative_bug.get("probe_id") or cluster.get("representative_probe_id") or "")
        evidence = evidence_by_probe.get(probe_id, {})
        fix_item = fix_by_cluster.get(cluster_id, {})
        risk_type = str(cluster.get("risk_type") or representative_bug.get("risk_type") or "business_risk")
        risk_domain, risk_domain_label = business_risk_domain_for(risk_type)
        domain_row = domain_rows.get(risk_domain, {})
        affected_apis = list(cluster.get("affected_apis") or representative_bug.get("related_apis") or [])
        if not affected_apis and representative_bug.get("affected_api"):
            affected_apis = [representative_bug.get("affected_api")]
        fallback_api = affected_apis[0] if affected_apis else "GET /"
        release_gate = str(cluster.get("release_gate") or fix_item.get("release_gate") or "review")
        priority = str(fix_item.get("priority") or ("P0" if release_gate == "block" else "P1" if release_gate == "review" else "P2"))
        reproduction_steps = evidence_reproduction_steps(evidence, fallback_api)
        request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {}
        response = evidence.get("response") if isinstance(evidence.get("response"), dict) else {}
        item = {
            "pack_id": f"REPRO_{safe_pattern_token(cluster_id or risk_type)}",
            "cluster_id": cluster_id,
            "priority": priority,
            "release_gate": release_gate,
            "risk_domain": risk_domain,
            "risk_domain_label": risk_domain_label,
            "risk_domain_priority": domain_row.get("priority"),
            "risk_type": risk_type,
            "business_object": cluster.get("business_object") or representative_bug.get("business_object"),
            "representative_bug_id": representative_bug.get("discovered_bug_id") or cluster.get("representative_bug_id"),
            "representative_probe_id": probe_id,
            "representative_title": cluster.get("representative_title") or representative_bug.get("title"),
            "affected_apis": affected_apis[:10],
            "finding_count": int(cluster.get("finding_count") or 0),
            "max_value_score": int(cluster.get("max_value_score") or representative_bug.get("bug_value_score") or 0),
            "value_tier": cluster.get("value_tier") or representative_bug.get("value_tier"),
            "has_cross_step_oracle": bool(cluster.get("has_cross_step_oracle") or representative_bug.get("high_value_profile", {}).get("has_cross_step_oracle")),
            "has_business_context": bool(cluster.get("has_business_context") or representative_bug.get("business_context")),
            "evidence_strength": int(cluster.get("evidence_strength") or representative_bug.get("high_value_profile", {}).get("evidence_strength") or 0),
            "reproduction_steps": reproduction_steps,
            "request_excerpt": {
                "method": request.get("method"),
                "url": request.get("url") or request.get("path"),
                "body": request.get("body"),
                "headers": request.get("headers"),
            },
            "response_excerpt": {
                "status_code": response.get("status_code"),
                "body_excerpt": response.get("body_excerpt"),
            },
            "expected": evidence.get("expected") or representative_bug.get("expected"),
            "actual": evidence.get("actual") or representative_bug.get("actual"),
            "bug_signal": evidence.get("bug_signal") or representative_bug.get("bug_signal"),
            "recommended_fix": fix_item.get("recommended_fix") or cluster.get("recommended_fix"),
            "acceptance_criteria": list(fix_item.get("acceptance_criteria") or []),
            "expected_after_fix": [check.get("expected_after_fix") for check in (fix_item.get("api_checks") or []) if check.get("expected_after_fix")][:5],
            "regression_probe_ids": list(fix_item.get("regression_probe_ids") or []),
            "recommended_owner": fix_item.get("recommended_owner") or cluster.get("recommended_owner"),
        }
        items.append(item)
        summary = domain_summary.setdefault(
            risk_domain,
            {
                "domain": risk_domain,
                "label": risk_domain_label,
                "pack_count": 0,
                "p0_pack_count": 0,
                "cross_step_pack_count": 0,
            },
        )
        summary["pack_count"] += 1
        if priority == "P0":
            summary["p0_pack_count"] += 1
        if item["has_cross_step_oracle"]:
            summary["cross_step_pack_count"] += 1
    domain_items = sorted(domain_summary.values(), key=lambda item: (item["p0_pack_count"], item["pack_count"]), reverse=True)
    return {
        "version": "high_value_repro_evidence_pack_v1",
        "pack_count": len(items),
        "p0_pack_count": sum(1 for item in items if item.get("priority") == "P0"),
        "cross_step_pack_count": sum(1 for item in items if item.get("has_cross_step_oracle")),
        "business_context_pack_count": sum(1 for item in items if item.get("has_business_context")),
        "domain_count": len(domain_items),
        "domain_summary": domain_items,
        "items": items,
    }


def root_cause_for_risk(risk_type: str) -> tuple[str, str, str]:
    risk = str(risk_type or "unknown")
    mapping = {
        "permission_bypass": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "auth_bypass": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "idor": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "tenant_isolation": ("tenant_boundary_gap", "租户隔离缺口", "租户边界没有贯穿查询、写入、导出、缓存或异步链路。"),
        "privilege_escalation": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "locked_account_bypass": ("access_control_gap", "权限校验缺口", "服务端未对当前用户、角色或租户上下文做完整授权校验。"),
        "money_consistency": ("conservation_oracle_gap", "金额守恒缺口", "金额、流水、支付、优惠或退款链路缺少端到端守恒校验。"),
        "refund_abuse": ("conservation_oracle_gap", "金额守恒缺口", "金额、流水、支付、优惠或退款链路缺少端到端守恒校验。"),
        "payment_callback": ("callback_trust_gap", "回调可信校验缺口", "外部回调缺少签名、订单状态、金额和幂等校验。"),
        "callback_trust": ("callback_trust_gap", "回调可信校验缺口", "外部回调缺少签名、订单状态、金额和幂等校验。"),
        "stock_consistency": ("inventory_conservation_gap", "库存容量守恒缺口", "库存、容量或额度链路缺少扣减、回滚、并发和失败补偿校验。"),
        "quantity_consistency": ("inventory_conservation_gap", "库存容量守恒缺口", "库存、容量或额度链路缺少扣减、回滚、并发和失败补偿校验。"),
        "coupon_abuse": ("benefit_policy_gap", "权益策略校验缺口", "优惠、权益或额度缺少归属、门槛、有效期、叠加和上限校验。"),
        "state_flow": ("state_machine_guard_gap", "状态机防护缺口", "业务状态流转没有用白名单和终态保护约束非法路径。"),
        "state_consistency": ("state_machine_guard_gap", "状态机防护缺口", "动作后查询、异步同步和跨系统状态缺少一致性校验。"),
        "eventual_consistency": ("state_machine_guard_gap", "状态机防护缺口", "动作后查询、异步同步和跨系统状态缺少一致性校验。"),
        "message_ordering": ("state_machine_guard_gap", "状态机防护缺口", "动作后查询、异步同步和跨系统状态缺少一致性校验。"),
        "idempotency": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "race_condition": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "concurrent_update_lost": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "webhook_replay": ("idempotency_concurrency_gap", "幂等并发缺口", "关键写操作缺少幂等键、重放保护、锁或唯一约束。"),
        "audit_log_missing": ("audit_traceability_gap", "审计追溯缺口", "关键操作缺少可追溯审计、操作者、对象和变更前后状态。"),
        "audit_compliance": ("audit_traceability_gap", "审计追溯缺口", "关键操作缺少可追溯审计、操作者、对象和变更前后状态。"),
        "boundary_validation": ("input_contract_gap", "输入契约校验缺口", "接口缺少边界、枚举、格式、范围或组合约束校验。"),
    }
    return mapping.get(risk, ("business_rule_gap", "业务规则缺口", "PRD/OpenAPI 中的业务约束没有沉淀为可执行校验。"))


def build_enterprise_bug_triage_matrix(issue_clusters: dict, business_risk_radar: dict, release_gate_decision: dict, repro_pack: dict) -> dict:
    pack_by_cluster = {str(item.get("cluster_id")): item for item in repro_pack.get("items") or [] if item.get("cluster_id")}
    domain_by_key = {str(item.get("domain")): item for item in business_risk_radar.get("top_domains") or [] if item.get("domain")}
    queue = []
    root_summary: dict[str, dict] = {}
    owner_summary: dict[str, dict] = {}
    gate_rank = {"block": 3, "review": 2, "monitor": 1}
    for index, cluster in enumerate(issue_clusters.get("top_clusters") or [], start=1):
        cluster_id = str(cluster.get("cluster_id") or "")
        pack = pack_by_cluster.get(cluster_id, {})
        risk_type = str(cluster.get("risk_type") or pack.get("risk_type") or "business_risk")
        root_key, root_label, root_reason = root_cause_for_risk(risk_type)
        domain_key = str(pack.get("risk_domain") or business_risk_domain_for(risk_type)[0])
        domain_label = str(pack.get("risk_domain_label") or business_risk_domain_for(risk_type)[1])
        domain = domain_by_key.get(domain_key, {})
        release_gate = str(cluster.get("release_gate") or pack.get("release_gate") or "review")
        priority = str(pack.get("priority") or ("P0" if release_gate == "block" else "P1" if release_gate == "review" else "P2"))
        owner = str(pack.get("recommended_owner") or cluster.get("recommended_owner") or cluster.get("business_object") or root_key)
        has_repro = bool(pack.get("reproduction_steps"))
        has_acceptance = bool(pack.get("acceptance_criteria") or pack.get("expected_after_fix"))
        max_score = int(cluster.get("max_value_score") or pack.get("max_value_score") or 0)
        finding_count = int(cluster.get("finding_count") or pack.get("finding_count") or 0)
        blocker_bonus = 35 if release_gate == "block" else 18 if release_gate == "review" else 0
        domain_bonus = 15 if domain.get("priority") == "P0" else 8 if domain.get("priority") == "P1" else 0
        evidence_bonus = 10 if has_repro and has_acceptance else 5 if has_repro else 0
        triage_score = min(100, int(max_score * 0.45 + min(25, finding_count) + blocker_bonus + domain_bonus + evidence_bonus))
        if priority == "P0" or triage_score >= 90:
            sla = "24h"
        elif priority == "P1" or triage_score >= 75:
            sla = "72h"
        else:
            sla = "next_iteration"
        row = {
            "rank": index,
            "cluster_id": cluster_id,
            "priority": priority,
            "sla": sla,
            "triage_score": triage_score,
            "release_gate": release_gate,
            "risk_type": risk_type,
            "risk_domain": domain_key,
            "risk_domain_label": domain_label,
            "root_cause_type": root_key,
            "root_cause_label": root_label,
            "root_cause_hypothesis": root_reason,
            "recommended_owner": owner,
            "business_object": cluster.get("business_object") or pack.get("business_object"),
            "finding_count": finding_count,
            "max_value_score": max_score,
            "affected_apis": list(cluster.get("affected_apis") or pack.get("affected_apis") or [])[:8],
            "representative_bug_id": cluster.get("representative_bug_id") or pack.get("representative_bug_id"),
            "representative_title": cluster.get("representative_title") or pack.get("representative_title"),
            "repro_pack_id": pack.get("pack_id"),
            "ready_for_fix": bool(has_repro and has_acceptance),
            "recommended_fix": pack.get("recommended_fix") or cluster.get("recommended_fix"),
            "acceptance_criteria": list(pack.get("acceptance_criteria") or [])[:5],
            "regression_probe_ids": list(pack.get("regression_probe_ids") or [])[:8],
        }
        queue.append(row)
        root = root_summary.setdefault(
            root_key,
            {
                "root_cause_type": root_key,
                "root_cause_label": root_label,
                "cluster_count": 0,
                "p0_count": 0,
                "ready_for_fix_count": 0,
                "affected_domains": {},
                "recommended_owners": {},
                "max_triage_score": 0,
            },
        )
        root["cluster_count"] += 1
        root["p0_count"] += 1 if priority == "P0" else 0
        root["ready_for_fix_count"] += 1 if row["ready_for_fix"] else 0
        root["affected_domains"][domain_key] = root["affected_domains"].get(domain_key, 0) + 1
        root["recommended_owners"][owner] = root["recommended_owners"].get(owner, 0) + 1
        root["max_triage_score"] = max(root["max_triage_score"], triage_score)
        owner_row = owner_summary.setdefault(owner, {"owner": owner, "cluster_count": 0, "p0_count": 0, "ready_for_fix_count": 0, "max_triage_score": 0})
        owner_row["cluster_count"] += 1
        owner_row["p0_count"] += 1 if priority == "P0" else 0
        owner_row["ready_for_fix_count"] += 1 if row["ready_for_fix"] else 0
        owner_row["max_triage_score"] = max(owner_row["max_triage_score"], triage_score)
    queue.sort(key=lambda item: (item["priority"] == "P0", gate_rank.get(item["release_gate"], 0), item["triage_score"], item["finding_count"]), reverse=True)
    for idx, row in enumerate(queue, start=1):
        row["rank"] = idx
    root_causes = []
    for row in root_summary.values():
        row["affected_domains"] = dict(sorted(row["affected_domains"].items(), key=lambda item: item[1], reverse=True))
        row["recommended_owners"] = dict(sorted(row["recommended_owners"].items(), key=lambda item: item[1], reverse=True))
        root_causes.append(row)
    root_causes.sort(key=lambda item: (item["p0_count"], item["max_triage_score"], item["cluster_count"]), reverse=True)
    owners = sorted(owner_summary.values(), key=lambda item: (item["p0_count"], item["max_triage_score"], item["cluster_count"]), reverse=True)
    return {
        "version": "enterprise_bug_triage_matrix_v1",
        "source_gate": release_gate_decision.get("gate"),
        "triage_count": len(queue),
        "p0_count": sum(1 for item in queue if item.get("priority") == "P0"),
        "ready_for_fix_count": sum(1 for item in queue if item.get("ready_for_fix")),
        "root_cause_count": len(root_causes),
        "owner_count": len(owners),
        "top_root_causes": root_causes[:10],
        "owner_workload": owners[:15],
        "queue": queue[:30],
    }


def build_high_value_capability_assessment(summary: dict, oracle_coverage: dict, issue_clusters: dict, release_gate_decision: dict, fix_plan: dict, strategy: dict) -> dict:
    total = max(1, int(summary.get("total_findings") or 0))
    s_or_a = int(summary.get("s_or_a_tier_findings") or 0)
    business_hits = int(summary.get("business_context_findings") or 0)
    journey_hits = int(summary.get("journey_evidence_findings") or 0)
    cross_step_hits = int(summary.get("cross_step_oracle_findings") or 0)
    cluster_count = int(issue_clusters.get("cluster_count") or 0)
    blocker_count = int(issue_clusters.get("blocker_cluster_count") or 0)
    compression_rate = float(issue_clusters.get("compression_rate") or 0)
    oracle_rate = float(oracle_coverage.get("oracle_coverage_rate") or 0)
    verification_count = int(fix_plan.get("verification_count") or 0)
    regression_count = int(fix_plan.get("regression_probe_count") or 0)
    probe_count = max(1, int(strategy.get("probe_count") or 0))
    high_value_ratio = s_or_a / total
    business_ratio = business_hits / total
    journey_ratio = journey_hits / total
    oracle_hit_ratio = cross_step_hits / total
    source_count = len([name for name, count in (summary.get("source_distribution") or {}).items() if int(count or 0) > 0])
    risk_count = len([name for name, count in (summary.get("risk_distribution") or {}).items() if int(count or 0) > 0])
    probe_efficiency = min(1.0, s_or_a / probe_count)
    dimensions = {
        "high_value_yield": min(100, int(high_value_ratio * 100)),
        "business_understanding": min(100, int(business_ratio * 70 + min(30, risk_count * 2))),
        "oracle_depth": min(100, int(oracle_rate * 70 + oracle_hit_ratio * 30)),
        "actionability": min(100, int(compression_rate * 45 + min(35, blocker_count * 2) + (20 if cluster_count else 0))),
        "closed_loop_asset": min(100, int(min(60, verification_count * 3) + min(40, regression_count * 2))),
        "learning_diversity": min(100, int(min(45, source_count * 7) + min(35, risk_count * 2) + probe_efficiency * 20)),
    }
    overall_score = int(round(
        dimensions["high_value_yield"] * 0.18
        + dimensions["business_understanding"] * 0.16
        + dimensions["oracle_depth"] * 0.20
        + dimensions["actionability"] * 0.18
        + dimensions["closed_loop_asset"] * 0.14
        + dimensions["learning_diversity"] * 0.14
    ))
    if overall_score >= 90:
        maturity = "enterprise_ready"
        maturity_label = "企业级可落地"
    elif overall_score >= 75:
        maturity = "pilot_ready"
        maturity_label = "试点可落地"
    elif overall_score >= 60:
        maturity = "needs_hardening"
        maturity_label = "需要增强后试点"
    else:
        maturity = "early_stage"
        maturity_label = "早期能力"

    gaps = []
    for item in oracle_coverage.get("risk_coverage") or []:
        rate = float(item.get("oracle_coverage_rate") or 0)
        if rate < 0.9:
            gaps.append({
                "gap_type": "oracle_coverage_gap",
                "risk_type": item.get("risk_type"),
                "current": rate,
                "target": 0.9,
                "recommendation": "补齐跨步骤业务 Oracle，优先覆盖状态、金额、库存、幂等等高损失链路。",
            })
    if business_ratio < 0.5:
        gaps.append({
            "gap_type": "business_context_gap",
            "current": round(business_ratio, 3),
            "target": 0.5,
            "recommendation": "继续从 PRD/OpenAPI 中抽取业务对象、角色、状态机和数据守恒规则，提高业务命中率。",
        })
    if verification_count < max(1, min(10, blocker_count)):
        gaps.append({
            "gap_type": "closure_asset_gap",
            "current": verification_count,
            "target": min(10, blocker_count),
            "recommendation": "把阻断发布问题簇转成修复验证和回归探针，避免一次性报告无法沉淀。",
        })

    next_actions = [
        {
            "action": "raise_oracle_coverage",
            "priority": "P0" if oracle_rate < 0.9 else "P1",
            "detail": "下一轮优先补齐 Oracle 覆盖率低于 90% 的风险类型。",
        },
        {
            "action": "expand_business_context",
            "priority": "P1" if business_ratio < 0.6 else "P2",
            "detail": "继续基于需求文档和接口语义扩展业务规则、角色边界、数据依赖和状态机。",
        },
        {
            "action": "stabilize_fix_regression_assets",
            "priority": "P0" if blocker_count and regression_count == 0 else "P1",
            "detail": "把 P0/P1 问题簇固化到修复验证、回归套件和 CI 发布门禁。",
        },
    ]
    return {
        "version": "high_value_capability_assessment_v1",
        "overall_score": overall_score,
        "maturity": maturity,
        "maturity_label": maturity_label,
        "dimensions": dimensions,
        "evidence": {
            "total_findings": int(summary.get("total_findings") or 0),
            "s_or_a_tier_findings": s_or_a,
            "business_context_findings": business_hits,
            "journey_evidence_findings": journey_hits,
            "cross_step_oracle_findings": cross_step_hits,
            "oracle_coverage_rate": oracle_rate,
            "issue_cluster_count": cluster_count,
            "blocker_cluster_count": blocker_count,
            "compression_rate": compression_rate,
            "verification_count": verification_count,
            "regression_probe_count": regression_count,
            "probe_count": int(strategy.get("probe_count") or 0),
            "source_count": source_count,
            "risk_count": risk_count,
            "release_gate": release_gate_decision.get("gate"),
        },
        "capability_gaps": gaps[:10],
        "next_actions": next_actions,
    }


def build_high_value_self_improvement_report(previous_assessment: dict, current_assessment: dict, summary: dict, strategy: dict) -> dict:
    previous = previous_assessment if isinstance(previous_assessment, dict) else {}
    current = current_assessment if isinstance(current_assessment, dict) else {}
    previous_evidence = previous.get("evidence") or {}
    current_evidence = current.get("evidence") or {}
    previous_dimensions = previous.get("dimensions") or {}
    current_dimensions = current.get("dimensions") or {}
    source_distribution = summary.get("source_distribution") or {}
    capability_findings = int(source_distribution.get("capability_gap") or 0)
    capability_probe_count = int(strategy.get("capability_gap_probe_count") or 0)

    def delta(now: float | int | None, old: float | int | None) -> float:
        return round(float(now or 0) - float(old or 0), 3)

    improvements = {
        "overall_score_delta": delta(current.get("overall_score"), previous.get("overall_score")),
        "oracle_coverage_delta": delta(current_evidence.get("oracle_coverage_rate"), previous_evidence.get("oracle_coverage_rate")),
        "oracle_depth_delta": delta(current_dimensions.get("oracle_depth"), previous_dimensions.get("oracle_depth")),
        "total_findings_delta": delta(current_evidence.get("total_findings"), previous_evidence.get("total_findings")),
        "s_or_a_delta": delta(current_evidence.get("s_or_a_tier_findings"), previous_evidence.get("s_or_a_tier_findings")),
        "cross_step_oracle_findings_delta": delta(current_evidence.get("cross_step_oracle_findings"), previous_evidence.get("cross_step_oracle_findings")),
        "business_context_findings_delta": delta(current_evidence.get("business_context_findings"), previous_evidence.get("business_context_findings")),
    }
    closed_loop_active = capability_probe_count > 0 and capability_findings > 0
    if improvements["overall_score_delta"] > 0 or improvements["oracle_coverage_delta"] > 0:
        verdict = "improving"
        verdict_label = "自优化有效"
    elif closed_loop_active:
        verdict = "learning_active"
        verdict_label = "已启动学习闭环"
    else:
        verdict = "needs_more_signal"
        verdict_label = "需要更多有效反馈"
    return {
        "version": "high_value_self_improvement_report_v1",
        "mode": "capability_gap_closed_loop",
        "verdict": verdict,
        "verdict_label": verdict_label,
        "previous_score": previous.get("overall_score"),
        "current_score": current.get("overall_score"),
        "previous_maturity": previous.get("maturity"),
        "current_maturity": current.get("maturity"),
        "improvements": improvements,
        "capability_gap_contribution": {
            "enabled": bool(strategy.get("high_value_capability_assessment_enabled")),
            "input_gap_count": int(strategy.get("high_value_capability_gap_count") or 0),
            "probe_count": capability_probe_count,
            "finding_count": capability_findings,
            "s_or_a_estimated": capability_findings,
            "finding_rate": round(capability_findings / capability_probe_count, 3) if capability_probe_count else 0,
        },
        "remaining_gaps": current.get("capability_gaps") or [],
        "next_actions": current.get("next_actions") or [],
    }


def build_high_value_capability_trend(previous_trend: dict, assessment: dict, self_improvement: dict, strategy: dict) -> dict:
    history = list((previous_trend or {}).get("history") or [])
    evidence = assessment.get("evidence") or {}
    contribution = self_improvement.get("capability_gap_contribution") or {}
    run_index = int((history[-1].get("run_index") if history else 0) or 0) + 1
    snapshot = {
        "run_index": run_index,
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "overall_score": assessment.get("overall_score"),
        "maturity": assessment.get("maturity"),
        "maturity_label": assessment.get("maturity_label"),
        "total_findings": evidence.get("total_findings"),
        "s_or_a_tier_findings": evidence.get("s_or_a_tier_findings"),
        "cross_step_oracle_findings": evidence.get("cross_step_oracle_findings"),
        "oracle_coverage_rate": evidence.get("oracle_coverage_rate"),
        "oracle_depth": (assessment.get("dimensions") or {}).get("oracle_depth"),
        "business_context_findings": evidence.get("business_context_findings"),
        "issue_cluster_count": evidence.get("issue_cluster_count"),
        "blocker_cluster_count": evidence.get("blocker_cluster_count"),
        "capability_gap_probe_count": contribution.get("probe_count") or strategy.get("capability_gap_probe_count"),
        "capability_gap_finding_count": contribution.get("finding_count"),
        "self_improvement_verdict": self_improvement.get("verdict"),
    }
    history.append(snapshot)
    history = history[-30:]

    def trend_delta(key: str, window: int) -> float:
        if len(history) < 2:
            return 0.0
        start = history[max(0, len(history) - window)].get(key)
        end = history[-1].get(key)
        try:
            return round(float(end or 0) - float(start or 0), 3)
        except Exception:
            return 0.0

    score_delta_last = trend_delta("overall_score", 2)
    oracle_delta_last = trend_delta("oracle_coverage_rate", 2)
    score_delta_5 = trend_delta("overall_score", 5)
    oracle_delta_5 = trend_delta("oracle_coverage_rate", 5)
    if score_delta_last > 0 or oracle_delta_last > 0:
        trend = "up"
        trend_label = "能力上升"
    elif score_delta_last < 0 or oracle_delta_last < 0:
        trend = "down"
        trend_label = "能力下降"
    else:
        trend = "stable"
        trend_label = "能力稳定"
    return {
        "version": "high_value_capability_trend_v1",
        "history_count": len(history),
        "latest": snapshot,
        "trend": trend,
        "trend_label": trend_label,
        "deltas": {
            "last_run_score_delta": score_delta_last,
            "last_run_oracle_coverage_delta": oracle_delta_last,
            "last_5_runs_score_delta": score_delta_5,
            "last_5_runs_oracle_coverage_delta": oracle_delta_5,
        },
        "history": history,
    }


def build_oracle_coverage_summary(probes: list[dict], bugs: list[dict]) -> dict:
    required = [
        p
        for p in probes
        if p.get("risk_type") in ORACLE_REQUIRED_RISKS and (p.get("steps") or p.get("source") in {"business_knowledge", "business_adaptation_layer", "risk_learning_profile", "high_value_attack_plan", "capability_gap", "oracle_gap", "high_value_memory"})
    ]
    covered = [p for p in required if p.get("cross_step_oracle")]
    gap_probes = [p for p in probes if p.get("source") == "oracle_gap"]
    by_risk: dict[str, dict] = {}
    for p in required:
        risk = str(p.get("risk_type") or "unknown")
        item = by_risk.setdefault(risk, {"risk_type": risk, "required_probe_count": 0, "oracle_probe_count": 0, "gap_probe_count": 0, "finding_count": 0})
        item["required_probe_count"] += 1
        if p.get("cross_step_oracle"):
            item["oracle_probe_count"] += 1
        if p.get("source") == "oracle_gap":
            item["gap_probe_count"] += 1
    for bug in bugs:
        risk = str(bug.get("risk_type") or "unknown")
        if risk not in by_risk:
            continue
        if bug.get("high_value_profile", {}).get("has_cross_step_oracle"):
            by_risk[risk]["finding_count"] += 1
    for item in by_risk.values():
        total = max(1, int(item["required_probe_count"]))
        item["oracle_coverage_rate"] = round(item["oracle_probe_count"] / total, 3)
    total_required = len(required)
    total_covered = len(covered)
    return {
        "version": "oracle_coverage_summary_v1",
        "required_probe_count": total_required,
        "oracle_probe_count": total_covered,
        "oracle_coverage_rate": round(total_covered / total_required, 3) if total_required else 0,
        "oracle_gap_probe_count": len(gap_probes),
        "cross_step_oracle_findings": sum(1 for bug in bugs if bug.get("high_value_profile", {}).get("has_cross_step_oracle")),
        "covered_risk_count": sum(1 for item in by_risk.values() if item.get("oracle_probe_count", 0) > 0),
        "risk_coverage": sorted(by_risk.values(), key=lambda item: (item["oracle_coverage_rate"], -item["required_probe_count"], item["risk_type"])),
        "top_gaps": [
            {
                "risk_type": item["risk_type"],
                "required_probe_count": item["required_probe_count"],
                "oracle_probe_count": item["oracle_probe_count"],
                "oracle_coverage_rate": item["oracle_coverage_rate"],
            }
            for item in sorted(by_risk.values(), key=lambda item: (item["oracle_coverage_rate"], -item["required_probe_count"]))[:8]
            if item["oracle_coverage_rate"] < 1
        ],
    }


def build_high_value_attack_plan(bugs: list[dict], summary: dict, oracle_coverage: dict, risk_profile: dict) -> dict:
    risk_rows: dict[str, dict] = {}
    for risk, count in (summary.get("risk_distribution") or {}).items():
        risk_rows.setdefault(str(risk), {"risk_type": str(risk), "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
        risk_rows[str(risk)]["finding_count"] = int(count or 0)
    for bug in bugs:
        risk = str(bug.get("risk_type") or "unknown")
        row = risk_rows.setdefault(risk, {"risk_type": risk, "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
        if bug.get("value_tier") in {"S", "A"}:
            row["s_or_a_count"] += 1
        row["max_score"] = max(int(row.get("max_score") or 0), int(bug.get("bug_value_score") or 0))
    for item in risk_profile.get("priority_risks") or []:
        risk = str(item.get("name") or "")
        if risk:
            risk_rows.setdefault(risk, {"risk_type": risk, "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
            risk_rows[risk]["learned_weight"] = int(item.get("weight") or 0)
    for item in oracle_coverage.get("risk_coverage") or []:
        risk = str(item.get("risk_type") or "")
        if not risk:
            continue
        row = risk_rows.setdefault(risk, {"risk_type": risk, "finding_count": 0, "s_or_a_count": 0, "max_score": 0, "learned_weight": 0, "oracle_coverage_rate": 1.0, "oracle_gap_count": 0})
        rate = float(item.get("oracle_coverage_rate") or 0)
        required = int(item.get("required_probe_count") or 0)
        covered = int(item.get("oracle_probe_count") or 0)
        row["oracle_coverage_rate"] = rate
        row["oracle_gap_count"] = max(0, required - covered)

    action_labels = {
        "permission_bypass": "扩展横向/纵向权限绕过链路，覆盖普通用户、匿名用户、降权后缓存权限。",
        "auth_bypass": "补未登录和锁定账号链路，验证所有受保护资源的登录态门禁。",
        "idor": "扩展跨用户资源 ID 替换和状态变更链路，覆盖读、改、取消、导出。",
        "tenant_isolation": "扩展跨租户读写链路，验证查询参数、请求体和缓存隔离。",
        "money_consistency": "增强金额守恒 Oracle，覆盖支付、优惠、退款、流水、余额和舍入误差。",
        "stock_consistency": "增强库存/容量守恒 Oracle，覆盖扣减、回滚、重复提交和并发边界。",
        "state_flow": "增强状态机非法流转探针，覆盖取消后支付、终态重开、回调乱序。",
        "state_consistency": "增强动作后查询一致性探针，覆盖支付、退款、审批、异步状态同步。",
        "coupon_abuse": "增强权益滥用探针，覆盖重复抵扣、归属、门槛、过期和超额优惠。",
        "idempotency": "增强幂等重放探针，覆盖重复下单、重复回调、重复退款和幂等键冲突。",
    }

    focus_items = []
    for row in risk_rows.values():
        gap_factor = int((1.0 - float(row.get("oracle_coverage_rate", 1.0))) * 40)
        score = int(row.get("s_or_a_count") or 0) * 8 + int(row.get("finding_count") or 0) * 2 + int(row.get("learned_weight") or 0) + int(row.get("max_score") or 0) // 10 + gap_factor + int(row.get("oracle_gap_count") or 0)
        if score <= 0:
            continue
        risk = row["risk_type"]
        gap_types = []
        if float(row.get("oracle_coverage_rate", 1.0)) < 0.8:
            gap_types.append("oracle_coverage_gap")
        if int(row.get("s_or_a_count") or 0) >= 3:
            gap_types.append("high_value_cluster")
        if int(row.get("learned_weight") or 0) >= 10:
            gap_types.append("learning_weight_hotspot")
        if int(row.get("oracle_gap_count") or 0) > 0:
            gap_types.append("probe_assertion_gap")
        focus_items.append({
            **row,
            "priority_score": score,
            "priority": "P0" if score >= 80 else "P1" if score >= 45 else "P2",
            "gap_types": gap_types or ["continuous_regression_focus"],
            "recommended_next_probe_count": max(2, min(12, 2 + int(row.get("oracle_gap_count") or 0) + int(row.get("s_or_a_count") or 0) // 4)),
            "recommended_action": action_labels.get(risk, "基于当前 PRD/OpenAPI 继续扩展业务链路 Oracle 和异常边界探针。"),
        })
    focus_items.sort(key=lambda item: (item["priority_score"], item["s_or_a_count"], item["oracle_gap_count"]), reverse=True)
    return {
        "version": "high_value_attack_plan_v1",
        "mode": "closed_loop_gap_to_probe_strategy",
        "total_focus_risks": len(focus_items),
        "top_focus": focus_items[:10],
        "next_run_probe_budget": sum(int(item["recommended_next_probe_count"]) for item in focus_items[:6]),
        "quality_bar": {
            "target_oracle_coverage_rate": 0.9,
            "target_cross_step_oracle_findings": max(int((summary or {}).get("cross_step_oracle_findings") or 0), 1),
            "block_release_when_p0_oracle_fails": True,
        },
        "executive_summary": [
            f"下一轮优先攻击 {item['risk_type']}：{item['recommended_action']}"
            for item in focus_items[:5]
        ],
    }


def safe_pattern_token(value: str) -> str:
    token = "".join(ch if ch.isalnum() else "_" for ch in str(value or "").upper())
    while "__" in token:
        token = token.replace("__", "_")
    return token.strip("_")[:80] or "UNKNOWN"


def build_high_value_pattern_memory(bugs: list[dict]) -> dict:
    high_value = [bug for bug in bugs if bug.get("value_tier") in {"S", "A"}]
    risk_weights: dict[str, int] = {}
    api_weights: dict[str, int] = {}
    source_weights: dict[str, int] = {}
    for bug in high_value:
        risk = str(bug.get("risk_type") or "unknown")
        api = str(bug.get("affected_api") or (bug.get("related_apis") or ["unknown"])[0])
        source = str(bug.get("probe_source") or "unknown")
        score = int(bug.get("bug_value_score") or 0)
        weight = 3 if bug.get("value_tier") == "S" else 2
        if score >= 95:
            weight += 1
        risk_weights[risk] = risk_weights.get(risk, 0) + weight
        api_weights[api] = api_weights.get(api, 0) + weight
        source_weights[source] = source_weights.get(source, 0) + weight

    top_patterns = []
    for bug in sorted(high_value, key=lambda item: item.get("bug_value_score", 0), reverse=True)[:36]:
        api = str(bug.get("affected_api") or (bug.get("related_apis") or [""])[0])
        risk = str(bug.get("risk_type") or "unknown")
        top_patterns.append(
            {
                "pattern_id": f"MEM_{safe_pattern_token(risk)}_{safe_pattern_token(api)}",
                "risk_type": risk,
                "api_template": api,
                "affected_api": api,
                "title": bug.get("title"),
                "value_tier": bug.get("value_tier"),
                "bug_value_score": bug.get("bug_value_score"),
                "probe_source": bug.get("probe_source"),
                "business_object": bug.get("business_object"),
                "reasons": bug.get("high_value_profile", {}).get("reasons", []),
                "recommended_probe_variants": 3 if bug.get("value_tier") == "S" else 2,
            }
        )
    recommendations = [
        f"下一轮优先覆盖 {risk} 风险，当前历史权重 {weight}"
        for risk, weight in sorted(risk_weights.items(), key=lambda item: item[1], reverse=True)[:8]
    ]
    return {
        "version": "high_value_pattern_memory_v1",
        "source": "discovered_high_value_findings",
        "pattern_count": len(top_patterns),
        "risk_weights": dict(sorted(risk_weights.items(), key=lambda item: item[1], reverse=True)),
        "api_weights": dict(sorted(api_weights.items(), key=lambda item: item[1], reverse=True)),
        "source_weights": dict(sorted(source_weights.items(), key=lambda item: item[1], reverse=True)),
        "top_patterns": top_patterns,
        "next_run_recommendations": recommendations,
    }


def build_risk_learning_profile(bugs: list[dict], summary: dict, strategy: dict, memory: dict) -> dict:
    high_value = [bug for bug in bugs if bug.get("value_tier") in {"S", "A"}]
    risk_weights: dict[str, int] = dict(memory.get("risk_weights") or {})
    api_weights: dict[str, int] = dict(memory.get("api_weights") or {})
    module_weights: dict[str, int] = {}
    oracle_weights: dict[str, int] = {}
    for bug in high_value:
        tier = bug.get("value_tier")
        score = int(bug.get("bug_value_score") or 0)
        weight = 5 if tier == "S" else 3
        if score >= 95:
            weight += 2
        risk = str(bug.get("risk_type") or "unknown")
        api = str(bug.get("affected_api") or (bug.get("related_apis") or ["unknown"])[0])
        module = str(bug.get("business_object") or "通用业务对象")
        risk_weights[risk] = risk_weights.get(risk, 0) + weight
        api_weights[api] = api_weights.get(api, 0) + weight
        module_weights[module] = module_weights.get(module, 0) + weight
        if bug.get("high_value_profile", {}).get("has_cross_step_oracle"):
            oracle_weights[risk] = oracle_weights.get(risk, 0) + weight

    def top_items(items: dict[str, int], limit: int = 8) -> list[dict]:
        return [{"name": name, "weight": weight} for name, weight in sorted(items.items(), key=lambda item: item[1], reverse=True)[:limit]]

    priority_risks = top_items(risk_weights)
    priority_modules = top_items(module_weights)
    recommendations = [f"下一轮优先攻击 {item['name']}，历史高价值权重 {item['weight']}" for item in priority_risks[:5]]
    recommendations.extend(f"重点覆盖业务对象/模块：{item['name']}，优先组合权限、数据一致性和状态流转探针" for item in priority_modules[:3])
    if oracle_weights:
        recommendations.append("保留跨步骤业务 Oracle：这些风险已出现链路级证据，优先作为发布门禁阻断条件")
    return {
        "version": "enterprise_high_value_risk_learning_v1",
        "mode": "closed_loop_probe_generation",
        "learned_from_findings": len(high_value),
        "memory_pattern_count": int(memory.get("pattern_count") or 0),
        "probe_count": strategy.get("probe_count") or 0,
        "memory_probe_count": strategy.get("high_value_memory_probe_count") or 0,
        "semantic_expansion_probe_count": strategy.get("high_value_memory_expansion_probe_count") or 0,
        "cross_step_oracle_findings": (summary or {}).get("cross_step_oracle_findings", 0),
        "priority_risks": priority_risks,
        "priority_apis": top_items(api_weights),
        "priority_modules": priority_modules,
        "oracle_priority_risks": top_items(oracle_weights, 5),
        "next_run_recommendations": recommendations[:10],
    }


def value_score(severity: str, confidence: float, item: dict) -> int:
    base = {"P0": 88, "P1": 76, "P2": 58}.get(severity, 45)
    completeness = 8 if item["response"]["body"] is not None else 3
    return min(100, int(base + completeness + confidence * 4))


def roi_metrics(probes: int, bugs: int) -> dict:
    return {"estimated_test_design_hours_saved": round(probes * 0.45, 1), "estimated_bug_report_hours_saved": round(bugs * 0.5, 1), "manual_probe_hours_avoided": round(probes * 0.7, 1)}


def build_bug_drafts(bugs: list[dict]) -> str:
    lines = ["# 缺陷草稿", ""]
    for bug in bugs:
        lines += [f"## {bug['title']}", f"- Severity: {bug['severity']}", f"- Score: {bug['bug_value_score']}", f"- API: {', '.join(bug['related_apis'])}", f"- Expected: {bug['expected']}", f"- Actual: {bug['actual']}", ""]
    return "\n".join(lines)


def build_report(data: dict) -> str:
    bugs = data["discovered_bugs"]
    mode = data.get("discovery_mode", "blind")
    model = data.get("business_model", {})
    rows = "\n".join(
        f"<tr><td>{b.get('value_tier','-')}</td><td>{b['severity']}</td><td>{b['title']}</td><td>{b['risk_type']}</td><td>{b['bug_value_score']}</td><td>{', '.join((b.get('high_value_profile') or {}).get('reasons', [])[:3])}</td><td>{b['confidence']}</td></tr>"
        for b in bugs
    )
    coverage = data.get("scenario_coverage", {})
    hv = data.get("high_value_summary", {})
    knowledge_note = f"业务知识增强：{'已启用' if model.get('business_knowledge_enabled') else '未启用'}，模块 {model.get('business_knowledge_modules', 0)}，规则 {model.get('business_knowledge_rules', 0)}，风险 {model.get('business_knowledge_risks', 0)}，业务知识探针 {data.get('business_knowledge_probe_count', 0)}。"
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8"><title>缺陷发现报告</title><style>body{{font-family:Arial,'Microsoft YaHei',sans-serif;margin:28px;color:#172033}}.kpi{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px}}.card{{border:1px solid #d8dee9;padding:14px;border-radius:8px;background:#fff}}table{{border-collapse:collapse;width:100%;margin-top:18px}}td,th{{border:1px solid #d8dee9;padding:8px;text-align:left;vertical-align:top}}.note{{background:#f8fafc;border:1px solid #d8dee9;padding:14px;border-radius:8px;margin:18px 0}}</style></head><body><h1>AI 高价值缺陷发现报告</h1><div class="note"><b>Discovery Mode：</b>{mode}。运行时探针仅来自当前项目资料与策略数据。</div><div class="note"><b>单输入自动理解：</b>系统从公开需求文档、接口文档和测试账号自动识别行业、业务对象、业务场景、操作、不变量、语义图、状态机和数据血缘；无需人工维护行业知识包。识别行业：{model.get('industry','-')}，业务场景：{model.get('business_scenarios','-')}，业务对象：{model.get('objects','-')}，操作：{model.get('operations','-')}，自动不变量：{model.get('auto_invariants','-')}，语义边：{model.get('semantic_graph_edges','-')}，状态机：{model.get('state_machines','-')}，数据血缘：{model.get('data_lineage','-')}。</div><div class="note"><b>{knowledge_note}</b></div><div class="kpi"><div class="card">场景覆盖率<br><b>{coverage.get('coverage_rate','-')}</b></div><div class="card">场景可执行率<br><b>{coverage.get('executable_rate','-')}</b></div><div class="card">阻塞场景<br><b>{coverage.get('blocked_scenarios','-')}</b></div><div class="card">缺陷探针<br><b>{data['probes']}</b></div><div class="card">业务知识探针<br><b>{data.get('business_knowledge_probe_count', 0)}</b></div><div class="card">发现缺陷<br><b>{len(bugs)}</b></div><div class="card">业务知识命中<br><b>{hv.get('business_context_findings', 0)}</b></div><div class="card">链路证据缺陷<br><b>{hv.get('journey_evidence_findings', 0)}</b></div><div class="card">S/A 级缺陷<br><b>{hv.get('s_or_a_tier_findings', 0)}</b></div><div class="card">节省工时<br><b>{data['roi_metrics']['estimated_test_design_hours_saved']}</b></div></div><h2>发现列表</h2><table><tr><th>价值层级</th><th>严重级别</th><th>标题</th><th>风险</th><th>价值分</th><th>评分原因</th><th>置信度</th></tr>{rows}</table></body></html>"""


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Run AI defect discovery")
    parser.add_argument("--project", default=os.environ.get("PROJECT", "enterprise_shop"))
    parser.add_argument("--public-artifacts", default=os.environ.get("PUBLIC_ARTIFACTS", "enterprise_bug_factory/public_artifacts"))
    parser.add_argument("--discovery-mode", default=os.environ.get("DEFECT_DISCOVERY_MODE", "blind"))
    args = parser.parse_args(argv)
    runner = DefectDiscoveryRunner(DiscoveryConfig(project=args.project, public_artifacts=Path(args.public_artifacts), discovery_mode=args.discovery_mode))
    data = runner.run()
    print(json.dumps({"project": data.get("project"), "discovery_mode": data.get("discovery_mode"), "probes": data.get("probes"), "discovered_bugs": len(data.get("discovered_bugs", []))}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
