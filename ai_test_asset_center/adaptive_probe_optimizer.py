from __future__ import annotations

import json
from pathlib import Path
from typing import Any

SEVERITY_WEIGHT = {"P0": 1.0, "P1": 0.82, "P2": 0.55, "P3": 0.35}

# Public, reusable probe recipes. These are not benchmark answers; they are generic
# defect-discovery strategies that can be activated by feedback from missed-template
# analysis. The AI discovery runtime consumes only this sanitized policy.
ADAPTIVE_TEMPLATE_LIBRARY: dict[str, dict[str, Any]] = {
    "AUTH_VERTICAL_BYPASS": {"probe_type": "adaptive_permission_probe", "risk_type": "permission_bypass", "severity": "P0", "actor": "normal_user", "method": "GET", "path": "/admin/orders", "expected_status": 403, "api_template": "GET /admin/orders", "strategy": "role_boundary_negative_check"},
    "AUTH_USER_WRITE_ADMIN": {"probe_type": "adaptive_permission_probe", "risk_type": "privilege_escalation", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/admin/products/p100", "expected_status": 403, "api_template": "POST /admin/products/{product_id}", "strategy": "non_admin_write_admin_resource"},
    "AUTH_UNAUTH_ACCESS": {"probe_type": "adaptive_auth_probe", "risk_type": "auth_bypass", "severity": "P0", "actor": "anonymous", "method": "GET", "path": "/admin/orders", "expected_status": 401, "api_template": "GET /admin/orders", "strategy": "anonymous_protected_resource_check"},
    "AUTH_LOCKED_USER_BYPASS": {"probe_type": "adaptive_account_state_probe", "risk_type": "locked_account_bypass", "severity": "P1", "actor": "locked_user", "method": "POST", "path": "/login", "expected_status": 403, "api_template": "POST /login", "strategy": "locked_identity_login_negative_check"},
    "AUTH_ROLE_DOWNGRADE_CACHE": {"probe_type": "adaptive_permission_probe", "risk_type": "permission_bypass", "severity": "P1", "actor": "normal_user", "method": "GET", "path": "/admin/orders", "expected_status": 403, "api_template": "GET /admin/orders", "strategy": "permission_cache_boundary_check"},
    "IDOR_ORDER_ACCESS": {"probe_type": "adaptive_idor_probe", "risk_type": "idor", "severity": "P0", "actor": "normal_user", "method": "GET", "path": "/orders/o900", "expected_status": 403, "api_template": "GET /orders/{order_id}", "strategy": "cross_owner_read_negative_check"},
    "IDOR_ORDER_CANCEL": {"probe_type": "adaptive_idor_probe", "risk_type": "idor", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders/o900/cancel", "expected_status": 403, "api_template": "POST /orders/{order_id}/cancel", "strategy": "cross_owner_state_action_negative_check"},
    "IDOR_ADDRESS_MODIFY": {"probe_type": "adaptive_idor_probe", "risk_type": "idor", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 403, "api_template": "POST /orders", "strategy": "owner_or_tenant_tamper_negative_check"},
    "TENANT_DATA_LEAK": {"probe_type": "adaptive_tenant_probe", "risk_type": "tenant_isolation", "severity": "P0", "actor": "normal_user", "method": "GET", "path": "/tenant/orders?tenant_id=tenant_b", "expected_status": 403, "api_template": "GET /tenant/orders", "strategy": "cross_tenant_scope_negative_check"},
    "STOCK_OVERSELL": {"probe_type": "adaptive_stock_probe", "risk_type": "stock_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 409, "api_template": "POST /orders", "strategy": "quantity_above_available_stock"},
    "STOCK_NEGATIVE_QUANTITY": {"probe_type": "adaptive_stock_probe", "risk_type": "stock_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 409, "api_template": "POST /orders", "strategy": "negative_stock_boundary_check"},
    "STOCK_NOT_DECREASED": {"probe_type": "adaptive_state_probe", "risk_type": "stock_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "strategy": "pre_post_inventory_delta_check"},
    "STOCK_NOT_ROLLBACK": {"probe_type": "adaptive_state_probe", "risk_type": "stock_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders/{order_id}/cancel", "expected_status": 200, "api_template": "POST /orders/{order_id}/cancel", "strategy": "cancel_inventory_rollback_check"},
    "ORDER_CREATE_MISSING": {"probe_type": "adaptive_state_probe", "risk_type": "state_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "strategy": "create_then_read_consistency"},
    "ORDER_CANCEL_STATE": {"probe_type": "adaptive_state_probe", "risk_type": "state_flow", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders/{order_id}/cancel", "expected_status": 200, "api_template": "POST /orders/{order_id}/cancel", "strategy": "cancel_terminal_state_check"},
    "ORDER_DUPLICATE_SUBMIT": {"probe_type": "adaptive_idempotency_probe", "risk_type": "idempotency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "strategy": "repeat_same_idempotency_key_order"},
    "IDEMPOTENCY_DUPLICATE_ORDER": {"probe_type": "adaptive_idempotency_probe", "risk_type": "idempotency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "strategy": "duplicate_order_key_should_return_same_order"},
    "IDEMPOTENCY_DUPLICATE_STOCK_DEDUCT": {"probe_type": "adaptive_stock_idempotency_probe", "risk_type": "stock_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "strategy": "duplicate_order_key_stock_delta_once"},
    "COUPON_DOUBLE_DISCOUNT": {"probe_type": "adaptive_coupon_probe", "risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 409, "api_template": "POST /cart/apply-coupon", "strategy": "same_coupon_reuse_negative_check"},
    "COUPON_EXPIRED_ALLOWED": {"probe_type": "adaptive_coupon_probe", "risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 400, "api_template": "POST /cart/apply-coupon", "strategy": "expired_coupon_negative_check"},
    "COUPON_THRESHOLD_BYPASS": {"probe_type": "adaptive_coupon_probe", "risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 400, "api_template": "POST /cart/apply-coupon", "strategy": "coupon_min_amount_negative_check"},
    "COUPON_OWNERSHIP_BYPASS": {"probe_type": "adaptive_coupon_probe", "risk_type": "coupon_abuse", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 403, "api_template": "POST /cart/apply-coupon", "strategy": "coupon_owner_negative_check"},
    "MONEY_DISCOUNT_OVER_TOTAL": {"probe_type": "adaptive_money_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 400, "api_template": "POST /cart/apply-coupon", "strategy": "discount_cannot_exceed_total"},
    "PAYMENT_AMOUNT_MISMATCH": {"probe_type": "adaptive_payment_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/payments", "expected_status": 409, "api_template": "POST /payments", "strategy": "payment_amount_must_equal_order_total"},
    "MONEY_PAY_TOTAL_DIFF": {"probe_type": "adaptive_payment_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 409, "api_template": "POST /payments/callback", "strategy": "callback_amount_must_equal_order_total"},
    "PAYMENT_STATUS_NOT_UPDATED": {"probe_type": "adaptive_payment_probe", "risk_type": "state_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/payments", "expected_status": 200, "api_template": "POST /payments", "strategy": "payment_should_update_order_status"},
    "PAYMENT_CANCELLED_ORDER_ALLOWED": {"probe_type": "adaptive_payment_probe", "risk_type": "state_flow", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/payments", "expected_status": 409, "api_template": "POST /payments", "strategy": "cancelled_order_payment_negative_check"},
    "ORDER_PAY_CANCELLED": {"probe_type": "adaptive_callback_probe", "risk_type": "state_flow", "severity": "P0", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 409, "api_template": "POST /payments/callback", "strategy": "cancelled_order_callback_negative_check"},
    "PAYMENT_DUPLICATE_CALLBACK": {"probe_type": "adaptive_callback_probe", "risk_type": "payment_callback", "severity": "P1", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 200, "api_template": "POST /payments/callback", "strategy": "same_callback_id_idempotency"},
    "IDEMPOTENCY_DUPLICATE_PAYMENT": {"probe_type": "adaptive_callback_probe", "risk_type": "idempotency", "severity": "P1", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 200, "api_template": "POST /payments/callback", "strategy": "duplicate_payment_callback_should_not_double_post"},
    "REFUND_DUPLICATE": {"probe_type": "adaptive_refund_probe", "risk_type": "refund_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 409, "api_template": "POST /refunds", "strategy": "same_refund_id_idempotency"},
    "REFUND_UNPAID_ORDER": {"probe_type": "adaptive_refund_probe", "risk_type": "refund_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 409, "api_template": "POST /refunds", "strategy": "refund_unpaid_order_negative_check"},
    "REFUND_OVER_AMOUNT": {"probe_type": "adaptive_refund_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 409, "api_template": "POST /refunds", "strategy": "refund_amount_cannot_exceed_paid"},
    "REFUND_STATE_INCONSISTENCY": {"probe_type": "adaptive_refund_probe", "risk_type": "state_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 200, "api_template": "POST /refunds", "strategy": "refund_state_and_inventory_consistency"},
}

PRIVATE_KEYS = {"bug_id", "bug_instance_id", "trigger_condition", "actual_bug_behavior", "enabled_bugs", "ground_truth_bugs", "current_bug_set"}


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def latest_feedback_dirs(root: Path) -> list[Path]:
    names = ["benchmark_outputs", "benchmark_outputs_phase4_200", "benchmark_outputs_phase3_200"]
    return [root / name for name in names if (root / name).exists()]


def build_learned_probe_policy(root: Path = Path("."), project: str = "enterprise_shop", output_path: Path | None = None) -> dict:
    dirs = latest_feedback_dirs(root)
    improvement_items: list[dict] = []
    scorecards: list[dict] = []
    false_positive_templates: set[str] = set()
    for directory in dirs:
        plan_path = directory / "probe_improvement_plan.json"
        if plan_path.exists():
            data = read_json(plan_path)
            improvement_items.extend(data if isinstance(data, list) else data.get("items", []))
        score_path = directory / "benchmark_scorecard.json"
        if score_path.exists():
            card = read_json(score_path)
            scorecards.append(card)
            for fp in card.get("false_positives", []):
                template = fp.get("predicted_template_id") or fp.get("template_id")
                if template:
                    false_positive_templates.add(str(template))
    seen: dict[str, dict] = {}
    for item in improvement_items:
        template = str(item.get("missed_template") or item.get("template_id") or "").strip()
        if not template or template not in ADAPTIVE_TEMPLATE_LIBRARY:
            continue
        recipe = dict(ADAPTIVE_TEMPLATE_LIBRARY[template])
        missed_count = int(item.get("missed_count") or 1)
        severity = str(item.get("severity") or recipe.get("severity") or "P2")
        fp_penalty = 0.35 if template in false_positive_templates else 0.0
        priority = round(min(1.0, 0.18 + min(missed_count, 20) / 25 + SEVERITY_WEIGHT.get(severity, 0.5) * 0.35 - fp_penalty), 4)
        row = {
            "template_id": template,
            "priority_score": priority,
            "risk_type": recipe["risk_type"],
            "severity": severity,
            "probe_type": recipe["probe_type"],
            "actor": recipe["actor"],
            "method": recipe["method"],
            "path": recipe["path"],
            "expected_status": recipe["expected_status"],
            "api_template": recipe["api_template"],
            "strategy": recipe["strategy"],
            "recommended_variants": recommended_variants(priority, missed_count),
            "source_feedback": "probe_improvement_plan",
        }
        if template not in seen or row["priority_score"] > seen[template]["priority_score"]:
            seen[template] = row
    ordered = sorted(seen.values(), key=lambda x: (x["priority_score"], SEVERITY_WEIGHT.get(x.get("severity"), 0)), reverse=True)
    policy = {
        "policy_version": "phase5_adaptive_probe_policy_v1",
        "project": project,
        "created_from": [str(d) for d in dirs],
        "private_answers_allowed": False,
        "contains_instance_answers": False,
        "safe_policy_fields": ["template_id", "risk_type", "severity", "strategy", "priority_score", "probe_recipe"],
        "baseline_metrics": [card.get("metrics", {}) for card in scorecards[-3:]],
        "template_policies": ordered,
        "suppressed_templates": sorted(false_positive_templates),
    }
    output_path = output_path or root / "platform_workspace" / project / "defect_discovery" / "learned_probe_policy.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(policy, ensure_ascii=False, indent=2), encoding="utf-8")
    return policy


def recommended_variants(priority: float, missed_count: int) -> int:
    if priority >= 0.85 or missed_count >= 10:
        return 3
    if priority >= 0.65 or missed_count >= 4:
        return 2
    return 1


def validate_policy_is_safe(policy: dict) -> None:
    text = json.dumps(policy, ensure_ascii=False).lower()
    for token in PRIVATE_KEYS:
        if token in text:
            raise ValueError(f"Unsafe adaptive policy contains private token: {token}")


def main() -> int:
    policy = build_learned_probe_policy(Path("."))
    validate_policy_is_safe(policy)
    print(json.dumps({"policy_version": policy["policy_version"], "templates": len(policy["template_policies"]), "out": "platform_workspace/enterprise_shop/defect_discovery/learned_probe_policy.json"}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


def build_adaptive_probe_plan(
    findings: list[dict[str, Any]],
    *,
    base_url: str = "",
    max_probes: int = 50,
) -> list[dict[str, Any]]:
    """Build a prioritized probe execution plan from findings + adaptive templates.
    
    Matches findings to adaptive probe templates by risk_type/category,
    prioritizes by severity × confidence, returns top N probes ready to execute.
    """
    import urllib.parse
    
    # Build template index by risk type
    template_index: dict[str, list[dict[str, Any]]] = {}
    for tid, tmpl in ADAPTIVE_TEMPLATE_LIBRARY.items():
        risk = tmpl.get("risk_type", "")
        if risk not in template_index:
            template_index[risk] = []
        template_index[risk].append(tmpl)

    # Match findings to templates and build probe plan
    probes: list[dict[str, Any]] = []
    seen_paths: set[str] = set()

    # Risk type mapping between finding categories and template risk types
    risk_map = {
        "error_contract": ["permission_bypass", "auth_bypass"],
        "permission_boundary": ["permission_bypass", "auth_bypass", "idor"],
        "idempotency_gap": ["idempotency", "state_consistency"],
        "idempotent_side_effect": ["idempotency", "stock_consistency"],
        "spec_structure": ["state_consistency"],
        "conservation": ["stock_consistency", "money_consistency"],
        "causality_coverage": ["state_flow", "state_consistency"],
        "unreachable": ["auth_bypass"],
        "async_observability_gap": ["state_flow"],
    }

    for finding in findings:
        risk_type = str(finding.get("risk_type", finding.get("category", "")))
        severity = str(finding.get("severity", "P2"))
        confidence = float(finding.get("confidence_score", 0.7))
        path = str(finding.get("path", ""))
        method = str(finding.get("method", ""))

        # Score: P0=3, P1=2, P2=1, P3=0.5
        sev_weight = {"P0": 3.0, "P1": 2.0, "P2": 1.0, "P3": 0.5}.get(severity, 1.0)
        priority = sev_weight * confidence

        # Look up mapped risk types, then find matching templates
        mapped = risk_map.get(risk_type, [risk_type])
        for mtype in mapped:
            templates = template_index.get(mtype, [])
            for tmpl in templates:
                probe_path = tmpl.get("path", path)
                if probe_path in seen_paths:
                    continue
                actual_path = path or probe_path
                if not actual_path:
                    continue
                probes.append({
                    "id": f"ADAPT-{len(probes)}",
                    "method": method or tmpl.get("method", "GET"),
                    "path": actual_path,
                    "expected_status": tmpl.get("expected_status", 200),
                    "actor": tmpl.get("actor", ""),
                    "severity": tmpl.get("severity", "P1"),
                    "risk_type": tmpl.get("risk_type", risk_type),
                    "strategy": tmpl.get("strategy", ""),
                    "priority": priority,
                })
                seen_paths.add(probe_path)
                break
            if templates:
                break  # matched one template group, move on

    # Sort by priority descending
    probes.sort(key=lambda p: p.get("priority", 0), reverse=True)
    return probes[:max_probes]
