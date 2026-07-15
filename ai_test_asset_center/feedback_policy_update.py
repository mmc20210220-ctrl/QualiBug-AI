from __future__ import annotations

import argparse
import html
import json
import time
from pathlib import Path
from typing import Any

DEFAULT_FEEDBACK_DIR = Path("benchmark_outputs/human_feedback")
DEFAULT_WORKSPACE = Path("platform_workspace/enterprise_shop/defect_discovery")
DEFAULT_OUT = Path("benchmark_outputs/feedback_policy")

PRIVATE_LEAK_TERMS = {
    "bug_instance_id",
    "enabled_bugs",
    "current_bug_set",
    "enabled_ids",
    "private_ground_truth",
    "ground_truth_bugs",
    "bug_sets/",
    "bug_sets\\",
    "hidden_test_instance",
}

# Safe template-to-probe defaults. These are template-level strategies only;
# no hidden bug instance ids, enabled bugs, or ground truth answers are used.
TEMPLATE_SPEC_FALLBACKS: dict[str, dict[str, Any]] = {
    "AUTH_VERTICAL_BYPASS": {"probe_type": "permission_probe", "risk_type": "permission_bypass", "severity": "P0", "actor": "normal_user", "method": "GET", "path": "/admin/orders", "expected_status": 403, "api_template": "GET /admin/orders", "title": "普通用户不得访问管理员资源"},
    "AUTH_UNAUTH_ACCESS": {"probe_type": "auth_probe", "risk_type": "auth_bypass", "severity": "P0", "actor": "anonymous", "method": "GET", "path": "/admin/orders", "expected_status": 401, "api_template": "GET /admin/orders", "title": "未登录用户不得访问受保护资源"},
    "AUTH_LOCKED_USER_BYPASS": {"probe_type": "account_state_probe", "risk_type": "locked_account_bypass", "severity": "P1", "actor": "locked_user", "method": "POST", "path": "/login", "expected_status": 403, "api_template": "POST /login", "title": "锁定账号不得登录"},
    "AUTH_ROLE_DOWNGRADE_CACHE": {"probe_type": "permission_probe", "risk_type": "permission_bypass", "severity": "P1", "actor": "normal_user", "method": "GET", "path": "/admin/orders", "expected_status": 403, "api_template": "GET /admin/orders", "title": "降权后缓存权限不能继续访问后台"},
    "IDOR_ORDER_ACCESS": {"probe_type": "idor_probe", "risk_type": "idor", "severity": "P0", "actor": "normal_user", "method": "GET", "path": "/orders/o900", "expected_status": 403, "api_template": "GET /orders/{order_id}", "title": "用户不能查看他人订单"},
    "IDOR_ADDRESS_MODIFY": {"probe_type": "idor_probe", "risk_type": "idor", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 403, "api_template": "POST /orders", "title": "用户不能提交他人归属或跨租户数据"},
    "IDOR_ORDER_CANCEL": {"probe_type": "idor_probe", "risk_type": "idor", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders/o900/cancel", "expected_status": 403, "api_template": "POST /orders/{order_id}/cancel", "title": "用户不能取消他人订单"},
    "TENANT_DATA_LEAK": {"probe_type": "tenant_probe", "risk_type": "tenant_isolation", "severity": "P0", "actor": "normal_user", "method": "GET", "path": "/tenant/orders?tenant_id=tenant_b", "expected_status": 403, "api_template": "GET /tenant/orders", "title": "租户数据必须隔离"},
    "STOCK_OVERSELL": {"probe_type": "stock_probe", "risk_type": "stock_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 409, "api_template": "POST /orders", "title": "库存不足不能下单"},
    "STOCK_NOT_DECREASED": {"probe_type": "stock_probe", "risk_type": "stock_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "title": "下单成功后库存必须扣减"},
    "STOCK_NOT_ROLLBACK": {"probe_type": "stock_probe", "risk_type": "stock_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders/{order_id}/cancel", "expected_status": 200, "api_template": "POST /orders/{order_id}/cancel", "title": "取消订单后库存必须回滚"},
    "STOCK_NEGATIVE_QUANTITY": {"probe_type": "stock_probe", "risk_type": "stock_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 409, "api_template": "POST /orders", "title": "库存不能被扣成负数"},
    "COUPON_DOUBLE_DISCOUNT": {"probe_type": "coupon_probe", "risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 409, "api_template": "POST /cart/apply-coupon", "title": "同一优惠券不能重复抵扣"},
    "COUPON_EXPIRED_ALLOWED": {"probe_type": "coupon_probe", "risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 400, "api_template": "POST /cart/apply-coupon", "title": "过期优惠券不能使用"},
    "COUPON_THRESHOLD_BYPASS": {"probe_type": "coupon_probe", "risk_type": "coupon_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 400, "api_template": "POST /cart/apply-coupon", "title": "不满足门槛不能使用优惠券"},
    "COUPON_OWNERSHIP_BYPASS": {"probe_type": "coupon_probe", "risk_type": "coupon_abuse", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 403, "api_template": "POST /cart/apply-coupon", "title": "用户不能使用他人优惠券"},
    "MONEY_DISCOUNT_OVER_TOTAL": {"probe_type": "money_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/cart/apply-coupon", "expected_status": 400, "api_template": "POST /cart/apply-coupon", "title": "优惠金额不能超过订单金额"},
    "PAYMENT_AMOUNT_MISMATCH": {"probe_type": "payment_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/payments", "expected_status": 409, "api_template": "POST /payments", "title": "支付金额必须等于订单金额"},
    "PAYMENT_DUPLICATE_CALLBACK": {"probe_type": "payment_callback_probe", "risk_type": "payment_callback", "severity": "P1", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 200, "api_template": "POST /payments/callback", "title": "支付回调必须幂等"},
    "PAYMENT_CANCELLED_ORDER_ALLOWED": {"probe_type": "payment_probe", "risk_type": "state_flow", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/payments", "expected_status": 409, "api_template": "POST /payments", "title": "已取消订单不能继续支付"},
    "PAYMENT_STATUS_NOT_UPDATED": {"probe_type": "payment_probe", "risk_type": "state_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/payments", "expected_status": 200, "api_template": "POST /payments", "title": "支付成功后订单状态必须更新"},
    "MONEY_PAY_TOTAL_DIFF": {"probe_type": "payment_callback_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 409, "api_template": "POST /payments/callback", "title": "支付回调金额必须等于订单金额"},
    "ORDER_PAY_CANCELLED": {"probe_type": "payment_callback_probe", "risk_type": "state_flow", "severity": "P0", "actor": "system", "method": "POST", "path": "/payments/callback", "expected_status": 409, "api_template": "POST /payments/callback", "title": "已取消订单不能被支付回调改为已支付"},
    "REFUND_DUPLICATE": {"probe_type": "refund_probe", "risk_type": "refund_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 409, "api_template": "POST /refunds", "title": "同一退款请求不能重复处理"},
    "REFUND_UNPAID_ORDER": {"probe_type": "refund_probe", "risk_type": "refund_abuse", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 409, "api_template": "POST /refunds", "title": "未支付订单不能退款"},
    "REFUND_OVER_AMOUNT": {"probe_type": "refund_probe", "risk_type": "money_consistency", "severity": "P0", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 409, "api_template": "POST /refunds", "title": "退款金额不能超过已支付剩余金额"},
    "REFUND_STATE_INCONSISTENCY": {"probe_type": "refund_probe", "risk_type": "state_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/refunds", "expected_status": 200, "api_template": "POST /refunds", "title": "退款后订单状态和库存必须一致"},
    "IDEMPOTENCY_DUPLICATE_ORDER": {"probe_type": "idempotency_probe", "risk_type": "idempotency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "title": "同一幂等键不能创建多个订单"},
    "ORDER_DUPLICATE_SUBMIT": {"probe_type": "idempotency_probe", "risk_type": "idempotency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "title": "重复提交不能生成多个订单"},
    "IDEMPOTENCY_DUPLICATE_STOCK_DEDUCT": {"probe_type": "stock_idempotency_probe", "risk_type": "stock_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "title": "重复提交不能重复扣减库存"},
    "ORDER_CREATE_MISSING": {"probe_type": "state_consistency_probe", "risk_type": "state_consistency", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders", "expected_status": 200, "api_template": "POST /orders", "title": "订单创建成功后必须可查询"},
    "ORDER_CANCEL_STATE": {"probe_type": "state_transition_probe", "risk_type": "state_flow", "severity": "P1", "actor": "normal_user", "method": "POST", "path": "/orders/{order_id}/cancel", "expected_status": 200, "api_template": "POST /orders/{order_id}/cancel", "title": "订单取消后状态必须正确"},
}


def read_json(path: Path, default: Any = None) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default
    return default


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def iter_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except Exception:
            continue
        if isinstance(item, dict):
            rows.append(item)
    return rows


def validate_no_private_leak(obj: Any) -> dict[str, Any]:
    text = json.dumps(obj, ensure_ascii=False).lower()
    text = text.replace("ground_truth_not_required", "answer_not_required")
    leaks = sorted(term for term in PRIVATE_LEAK_TERMS if term.lower() in text)
    return {"passed": not leaks, "leak_terms": leaks}


def _severity_weight(severity: str | None) -> float:
    return {"P0": 1.0, "P1": 0.75, "P2": 0.4, "P3": 0.15}.get(str(severity or "P2"), 0.25)


def _safe_template(template: Any) -> str:
    value = str(template or "unknown").strip().upper().replace(" ", "_")
    return "".join(ch for ch in value if ch.isalnum() or ch in "_-.:")[:80] or "UNKNOWN"


def build_feedback_adjusted_policy(
    feedback_dir: Path = DEFAULT_FEEDBACK_DIR,
    workspace_dir: Path = DEFAULT_WORKSPACE,
    out_dir: Path = DEFAULT_OUT,
) -> dict[str, Any]:
    policy_update = read_json(feedback_dir / "human_feedback_policy_update.json", {}) or read_json(workspace_dir / "human_feedback_policy_update.json", {}) or {}
    feedback_rows = iter_jsonl(feedback_dir / "human_feedback.jsonl")
    preference_rows = iter_jsonl(feedback_dir / "preference_pairs_from_human_feedback.jsonl")

    template_scores: dict[str, dict[str, Any]] = {}
    risk_suppression: dict[str, dict[str, Any]] = {}
    for row in policy_update.get("raise_template_weights", []) or []:
        template = _safe_template(row.get("template_id"))
        entry = template_scores.setdefault(template, {"template_id": template, "score": 0.0, "feedback_count": 0, "source": "human_feedback_policy_update", "priority": row.get("priority") or "P1"})
        delta = float(row.get("weight_delta") or 0)
        entry["score"] = round(float(entry["score"]) + delta + _severity_weight(row.get("priority")) * 0.1, 6)
        entry["feedback_count"] += int(row.get("feedback_count") or 1)
    for row in policy_update.get("suppress_risk_weights", []) or []:
        risk = str(row.get("risk_type") or "unknown")
        entry = risk_suppression.setdefault(risk, {"risk_type": risk, "score_delta": 0.0, "feedback_count": 0})
        entry["score_delta"] = round(float(entry["score_delta"]) + float(row.get("weight_delta") or -0.1), 6)
        entry["feedback_count"] += int(row.get("feedback_count") or 1)

    valid_high_value = 0
    false_positive_rows = 0
    missed_confirmed = 0
    for fb in feedback_rows:
        if fb.get("review_type") == "discovered_bug":
            if fb.get("is_false_positive") is True or fb.get("is_valid_bug") is False:
                false_positive_rows += 1
            if fb.get("is_valid_bug") is True and fb.get("is_high_value") is True:
                valid_high_value += 1
        if fb.get("review_type") == "missed_template" and fb.get("should_add_probe") is True:
            missed_confirmed += 1

    selected_templates: list[dict[str, Any]] = []
    for template, score in sorted(template_scores.items(), key=lambda kv: (-float(kv[1].get("score") or 0), kv[0])):
        spec = TEMPLATE_SPEC_FALLBACKS.get(template)
        if not spec:
            continue
        priority = str(score.get("priority") or spec.get("severity") or "P1")
        variants = 1 + min(3, max(0, int(score.get("feedback_count") or 1) - 1))
        if priority in {"P0", "P1"} and float(score.get("score") or 0) >= 0.2:
            variants += 1
        selected_templates.append({
            **spec,
            "template_id": template,
            "priority_score": round(min(1.0, 0.5 + float(score.get("score") or 0)), 6),
            "recommended_variants": min(5, variants),
            "human_feedback_count": int(score.get("feedback_count") or 0),
            "human_priority": priority,
            "strategy": "feedback_adjusted_policy",
        })

    policy = {
        "phase": "phase23_feedback_driven_policy_update",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "policy_name": "feedback_adjusted_probe_policy",
        "template_policies": selected_templates,
        "risk_suppression": sorted(risk_suppression.values(), key=lambda x: (float(x.get("score_delta") or 0), str(x.get("risk_type")))) ,
        "summary": {
            "feedback_rows": len(feedback_rows),
            "preference_pairs": len(preference_rows),
            "valid_high_value_feedback_rows": valid_high_value,
            "false_positive_feedback_rows": false_positive_rows,
            "confirmed_missed_templates": missed_confirmed,
            "templates_raised": len(selected_templates),
            "risks_suppressed": len(risk_suppression),
        },
        "governance": {
            "source": "human_feedback_only",
            "does_not_read_ground_truth": True,
            "does_not_read_private_bug_switches": True,
            "requires_quality_gate_before_default_promotion": True,
        },
    }
    leak = validate_no_private_leak(policy)
    policy["private_leak_check"] = leak
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "feedback_adjusted_probe_policy.json", policy)
    write_json(workspace_dir / "feedback_adjusted_probe_policy.json", policy)
    write_json(out_dir / "feedback_policy_update_summary.json", {"summary": policy["summary"], "private_leak_check": leak, "policy_path": str(out_dir / "feedback_adjusted_probe_policy.json")})
    (out_dir / "feedback_policy_update_report.html").write_text(build_report(policy), encoding="utf-8")
    return policy


def build_report(policy: dict[str, Any]) -> str:
    summary = policy.get("summary") or {}
    check = policy.get("private_leak_check") or {}
    rows = "".join(f"<tr><td>{html.escape(str(k))}</td><td>{html.escape(str(v))}</td></tr>" for k, v in summary.items())
    templates = "".join(
        f"<tr><td>{html.escape(str(t.get('template_id')))}</td><td>{html.escape(str(t.get('risk_type')))}</td><td>{html.escape(str(t.get('severity')))}</td><td>{html.escape(str(t.get('recommended_variants')))}</td><td>{html.escape(str(t.get('priority_score')))}</td></tr>"
        for t in (policy.get("template_policies") or [])
    ) or "<tr><td colspan='5'>No feedback-adjusted templates yet. Review bugs or missed templates first.</td></tr>"
    risks = "".join(
        f"<tr><td>{html.escape(str(r.get('risk_type')))}</td><td>{html.escape(str(r.get('score_delta')))}</td><td>{html.escape(str(r.get('feedback_count')))}</td></tr>"
        for r in (policy.get("risk_suppression") or [])
    ) or "<tr><td colspan='3'>No suppressed risk weights.</td></tr>"
    return f"""<!doctype html><html><head><meta charset='utf-8'><title>Phase23 Feedback-driven Policy Update</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,Arial,sans-serif;background:#f7f8fb;color:#172033;margin:32px}}.card{{background:#fff;border-radius:14px;padding:22px;margin:16px 0;box-shadow:0 8px 24px rgba(15,23,42,.08)}}table{{border-collapse:collapse;width:100%}}td,th{{border-bottom:1px solid #e5e7eb;padding:9px;text-align:left}}.ok{{color:#047857;font-weight:800}}.bad{{color:#b91c1c;font-weight:800}}code{{background:#eef2ff;border-radius:5px;padding:2px 6px}}</style></head><body>
<h1>Phase23 Feedback-driven Policy Update</h1>
<div class='card'><h2>Summary</h2><table>{rows}</table></div>
<div class='card'><h2>Governance</h2><p>Private leak check: <span class='{'ok' if check.get('passed') else 'bad'}'>{html.escape(str(check.get('passed')))}</span></p><p>Leak terms: <code>{html.escape(str(check.get('leak_terms') or []))}</code></p><p>Policy is built from QA human feedback, not from private ground truth or enabled bug sets.</p></div>
<div class='card'><h2>Templates Raised by QA Feedback</h2><table><thead><tr><th>Template</th><th>Risk</th><th>Severity</th><th>Variants</th><th>Priority score</th></tr></thead><tbody>{templates}</tbody></table></div>
<div class='card'><h2>Risk Types Suppressed / Review Needed</h2><table><thead><tr><th>Risk type</th><th>Weight delta</th><th>Feedback count</th></tr></thead><tbody>{risks}</tbody></table></div>
<div class='card'><h2>Next Run</h2><p>Run with <code>PROBE_POLICY_PROFILE=feedback_adjusted</code> or <code>RUN_DEFECT_DISCOVERY_FEEDBACK_ADJUSTED.cmd</code> to apply this policy.</p></div>
</body></html>"""


def main() -> None:
    parser = argparse.ArgumentParser(description="Phase23 Feedback-driven Policy Update")
    parser.add_argument("--feedback-dir", default=str(DEFAULT_FEEDBACK_DIR))
    parser.add_argument("--workspace-dir", default=str(DEFAULT_WORKSPACE))
    parser.add_argument("--out-dir", default=str(DEFAULT_OUT))
    args = parser.parse_args()
    policy = build_feedback_adjusted_policy(Path(args.feedback_dir), Path(args.workspace_dir), Path(args.out_dir))
    print(json.dumps({
        "phase": policy.get("phase"),
        "summary": policy.get("summary"),
        "private_leak_check": policy.get("private_leak_check"),
        "report": str(Path(args.out_dir) / "feedback_policy_update_report.html"),
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
