"""Business model inference: config, HTTP client, semantic graph, dependencies."""
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
    from ._probes import normalize_knowledge_risk, operation_matches_any_knowledge_risk, risks_from_text  # lazy
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
    from ._probes import keyword_hits  # lazy
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
    from ._scenarios import infer_business_scenarios  # lazy
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
    from ._probes import invariant_statement  # lazy
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


