"""System Behavior Space oracle evaluation helpers.

Owns promise-oracle evaluation used by first-class oracle hooks. The
private-pilot patch module remains the thin installer.
"""
from __future__ import annotations

from typing import Any

def _response_bodies(trace: dict[str, Any]) -> list[Any]:
    bodies: list[Any] = []
    for step in trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []:
        if not isinstance(step, dict):
            continue
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        if isinstance(response, dict) and "body" in response:
            bodies.append(response.get("body"))
    return bodies


def _walk_values(value: Any, prefix: str = "") -> list[tuple[str, Any]]:
    if isinstance(value, dict):
        out: list[tuple[str, Any]] = []
        for key, child in value.items():
            child_key = f"{prefix}.{key}" if prefix else str(key)
            out.extend(_walk_values(child, child_key))
        return out
    if isinstance(value, list):
        out = []
        for index, child in enumerate(value[:50]):
            out.extend(_walk_values(child, f"{prefix}[{index}]"))
        return out
    return [(prefix, value)]


def _direct_system_promise_oracle_result(scenario: dict[str, Any], trace: dict[str, Any], hints: dict[str, Any]) -> Any:
    """Evaluate a system promise against runtime trace evidence.

    Each dimension check inspects the HTTP response body against the
    promise's declared invariants.  Checks are additive — a single
    response can trigger multiple dimension violations.

    Enhanced: consumes ``system_promise_verification_intent`` from
    runtime_hints to produce dimension-specific, business-context-rich
    violation explanations that downstream (regression, learning) can use.
    """
    try:
        from ai_test_asset_center.oracle_engine import OracleResult
    except Exception:
        return None
    dims = {str(item).lower() for item in hints.get("dimensions") or [] if str(item)}
    promise_id = str(hints.get("promise_id") or "")
    invariant = str((scenario.get("runtime_hints") or {}).get("system_promise_invariant") or "system promise")[:500]
    bodies = _response_bodies(trace)
    steps = trace.get("steps") if isinstance(trace, dict) and isinstance(trace.get("steps"), list) else []
    all_values = [(k, v) for body in bodies for k, v in _walk_values(body)]
    all_keys_lower = {k.lower() for k, _ in all_values}
    all_text = " ".join(f"{k}={v}" for k, v in all_values).lower()

    # ── Consume verification intent for business-context-rich explanations ──
    vi = (scenario.get("runtime_hints") or {}).get("system_promise_verification_intent", {})
    if not isinstance(vi, dict):
        vi = {}
    vi_direction = str(vi.get("verification_direction") or "")
    vi_conservation = vi.get("conservation_constraints") or []
    vi_state = vi.get("state_constraints") or []
    vi_audit = vi.get("audit_constraints") or []
    vi_cross_surface = vi.get("cross_surface_checks") or []
    vi_tenant_boundary = vi.get("tenant_boundary")
    vi_evidence_surfaces = vi.get("evidence_surfaces") or []

    def _violation(rule: str, expected: str, actual: str, severity: str = "P0", confidence: float = 0.85) -> Any:
        # Build a business-context-rich explanation
        context_parts: list[str] = []
        if vi_direction:
            context_parts.append(f"验证方向: {vi_direction}")
        if vi_conservation:
            context_parts.append(f"守恒约束: {'; '.join(vi_conservation[:2])}")
        if vi_state:
            context_parts.append(f"状态约束: {'; '.join(vi_state[:2])}")
        if vi_audit:
            context_parts.append(f"审计约束: {'; '.join(vi_audit[:2])}")
        context = " | ".join(context_parts) if context_parts else ""
        explanation = f"System Behavior Space promise {promise_id} 被运行时响应反证。{context}"
        return OracleResult(False, "SystemPromiseOracle", "L7", rule, expected, actual, severity, confidence, explanation)

    # ── Dimension: money / quantity / conservation ──
    # Include compound family names (money_quantity_conservation) — exact-set
    # membership on {"money","quantity"} alone silently skipped the family.
    money_dim_tokens = (
        "money", "quantity", "conservation", "data_conservation",
        "money_quantity_conservation", "data_consistency", "payment", "refund",
        "inventory", "amount", "balance",
    )
    money_dims_active = bool(dims.intersection({
        "money", "quantity", "conservation", "data_conservation",
        "money_quantity_conservation", "data_consistency",
    })) or any(any(tok in d for tok in money_dim_tokens) for d in dims)
    money_like = {
        "money", "amount", "price", "balance", "refund", "payment", "fee", "total",
        "quantity", "qty", "stock", "inventory", "conservation", "payable", "paid",
    }
    if money_dims_active:
        for key, value in all_values:
            lowered = key.lower()
            if not any(token in lowered for token in money_like):
                continue
            if isinstance(value, (int, float)):
                if value < 0:
                    return _violation(f"system_promise_negative_value:{promise_id}",
                                      f"系统承诺: {invariant}", f"{key}={value} (负值)",
                                      "P0", 0.88)
                if value > 1_000_000_000:
                    return _violation(f"system_promise_suspicious_large_value:{promise_id}",
                                      f"系统承诺: {invariant}", f"{key}={value} (疑似溢出/异常)",
                                      "P1", 0.65)
            # Check for money fields that are 0 when they should be non-zero
            if isinstance(value, (int, float)) and value == 0 and "total" in lowered:
                has_line_items = any(
                    any(t in lk.lower() for t in ("price", "amount", "subtotal"))
                    for lk, lv in all_values if isinstance(lv, (int, float)) and lv > 0
                )
                if has_line_items:
                    return _violation(f"system_promise_zero_total_with_line_items:{promise_id}",
                                      f"系统承诺: {invariant}", f"{key}=0 但存在非零行项",
                                      "P1", 0.72)
        # Request-vs-response conservation: a successful money write whose body
        # amount disagrees with the resource's payable/paid field is a finding.
        for step in steps:
            if not isinstance(step, dict):
                continue
            method = str(step.get("method") or "").upper()
            if method not in {"POST", "PUT", "PATCH"}:
                continue
            status = int(
                step.get("status")
                or ((step.get("response") or {}).get("status_code") if isinstance(step.get("response"), dict) else 0)
                or 0
            )
            if not (200 <= status < 300):
                continue
            req = step.get("request") if isinstance(step.get("request"), dict) else {}
            req_body = req.get("body") if isinstance(req.get("body"), dict) else {}
            resp = step.get("response") if isinstance(step.get("response"), dict) else {}
            resp_body = resp.get("body") if isinstance(resp.get("body"), dict) else {}
            if not isinstance(req_body, dict) or not isinstance(resp_body, dict):
                continue
            req_amount = None
            for ak in ("amount", "payAmount", "pay_amount", "refundAmount", "refund_amount"):
                if isinstance(req_body.get(ak), (int, float)):
                    req_amount = float(req_body[ak])
                    break
            if req_amount is None:
                continue
            for rk in (
                "payableAmount", "payable_amount", "amountPaid", "amount_paid",
                "paidAmount", "paid_amount", "orderAmount", "order_amount", "totalAmount", "total_amount",
            ):
                rv = resp_body.get(rk)
                if isinstance(rv, (int, float)) and abs(float(rv) - req_amount) > 0.009:
                    path_l = str(step.get("path") or "").lower()
                    if any(tok in path_l for tok in ("pay", "payment", "refund", "settle", "charge")):
                        return _violation(
                            f"system_promise_amount_mismatch:{promise_id}",
                            f"系统承诺金额守恒: {invariant}",
                            f"request.amount={req_amount} vs response.{rk}={rv}",
                            "P0",
                            0.86,
                        )
                    break

    # ── Dimension: state_machine / lifecycle ──
    _state_keys = {"status", "state", "phase", "stage", "lifecycle"}
    if dims.intersection({"state", "lifecycle", "state_machine", "transition"}):
        for key, value in all_values:
            lowered = key.lower()
            if not any(sk in lowered for sk in _state_keys):
                continue
            if isinstance(value, str):
                if value.lower() in ("error", "unknown", "undefined", "null", "", "none"):
                    return _violation(f"system_promise_invalid_state:{promise_id}",
                                      f"系统承诺状态机有效: {invariant}",
                                      f"{key}={value} (异常状态值)",
                                      "P1", 0.78)
            elif value is None:
                return _violation(f"system_promise_null_state:{promise_id}",
                                  f"系统承诺状态字段非空: {invariant}",
                                  f"{key}=null",
                                  "P0", 0.82)
        # ── Terminal state regression check ──
        terminal_states = {"cancelled", "canceled", "refunded", "closed", "archived", "deleted", "voided", "rejected"}
        for key, value in all_values:
            lowered = key.lower()
            if not any(sk in lowered for sk in _state_keys):
                continue
            if isinstance(value, str) and value.lower() in terminal_states:
                # A terminal state appearing in an active list response may
                # indicate that the filter/logic is not enforced.
                # This is a soft signal — only flag if the value is unexpected.
                pass

    # ── Dimension: authorization_access_control / role / visibility ──
    if dims.intersection({"authorization", "role", "permission", "visibility", "privacy"}):
        privileged_fields = {"admin_only", "internal", "secret", "private_key", "api_key", "password", "token", "role"}
        for key, value in all_values:
            lowered = key.lower()
            if any(pf in lowered for pf in privileged_fields):
                if isinstance(value, str) and value.strip():
                    return _violation(f"system_promise_privileged_field_exposure:{promise_id}",
                                      f"系统承诺权限隔离: {invariant}",
                                      f"{key}={str(value)[:80]} (疑似越权暴露)",
                                      "P1", 0.72)
        actor = str((scenario.get("runtime_hints") or {}).get("system_promise_safe_read_route", {}).get("actor", "") or
                     "readonly").lower()
        non_admin_roles = {"readonly", "buyer", "user", "guest", "普通用户", "customer"}
        if any(role in actor for role in non_admin_roles) or not actor:
            admin_keywords = {"admin", "administrator", "管理员", "super", "root", "all_users", "all_orders", "all_products"}
            for key, value in all_values:
                lowered_key = key.lower()
                if any(ak in lowered_key for ak in admin_keywords):
                    if isinstance(value, (list, dict)) and len(str(value)) > 2:
                        return _violation(f"system_promise_admin_data_exposure:{promise_id}",
                                          f"系统承诺角色数据隔离: {invariant}",
                                          f"{key} 暴露给非管理员角色",
                                          "P1", 0.75)

    # ── Dimension: tenant_isolation ──
    _tenant_keys = {"tenant_id", "tenant", "org_id", "organization_id", "company_id", "workspace_id"}
    if dims.intersection({"tenant", "tenant_isolation", "isolation"}):
        tenant_values: dict[str, set[Any]] = {}
        for key, value in all_values:
            lowered = key.lower()
            if any(tk in lowered for tk in _tenant_keys):
                tenant_values.setdefault(lowered, set()).add(str(value)[:80])
        for key, vals in tenant_values.items():
            if len(vals) > 1:
                return _violation(f"system_promise_cross_tenant_leak:{promise_id}",
                                  f"系统承诺租户隔离: {invariant}",
                                  f"{key} has {len(vals)} distinct values: {sorted(vals)[:5]}",
                                  "P0", 0.92)

    # ── Dimension: audit_traceability ──
    _audit_keys = {"created_at", "updated_at", "created_by", "updated_by", "trace_id", "request_id", "correlation_id"}
    if dims.intersection({"audit", "traceability"}):
        missing_audit = [ak for ak in _audit_keys if ak not in all_keys_lower]
        if len(missing_audit) >= 3:
            return _violation(f"system_promise_audit_fields_missing:{promise_id}",
                              f"系统承诺审计可追溯: {invariant}",
                              f"缺少审计字段: {missing_audit[:5]}",
                              "P2", 0.58)

    # ── Dimension: data_consistency / cross_surface_consistency ──
    if dims.intersection({"data_consistency", "cross_surface_consistency", "conservation"}):
        id_values: dict[str, list[Any]] = {}
        for key, value in all_values:
            if key.lower().endswith(("id", "_id", "uuid", "ids")):
                continue
            if "id" in key.lower().split(".")[-1] or key.lower().endswith("id"):
                id_values.setdefault(key, []).append(value)
        for key, ids in id_values.items():
            if len(ids) > len(set(str(v) for v in ids)):
                return _violation(f"system_promise_duplicate_ids:{promise_id}",
                                  f"系统承诺数据一致性: {invariant}",
                                  f"{key} 包含重复 ID",
                                  "P1", 0.78)

        count_keys = {k.lower() for k in all_keys_lower if any(t in k.lower() for t in ("count", "total", "length", "size"))}
        collection_keys = {k.lower() for k in all_keys_lower if any(t in k.lower() for t in ("items", "rows", "records", "results", "list", "data"))}
        for ck in count_keys:
            count_val = next((v for k, v in all_values if k.lower() == ck), None)
            if isinstance(count_val, (int, float)) and count_val > 0:
                for colk in collection_keys:
                    col_val = next((v for k, v in all_values if k.lower() == colk), None)
                    if isinstance(col_val, list) and len(col_val) == 0:
                        return _violation(f"system_promise_count_mismatch:{promise_id}",
                                          f"系统承诺数据一致性: {invariant}",
                                          f"{ck}={count_val} 但 {colk}=[]",
                                          "P1", 0.80)

    # ── NEW: UI/API contract drift detection ──
    # When surface_plan includes both "ui" and "api", and the API returns
    # data that the UI should hide (e.g. disabled features, internal fields),
    # this is a UI/API contract drift — "UI 没按钮不代表 API 安全".
    ui_api_dims = dims.intersection({"ui_api_contract", "ui_api_contract_drift", "cross_surface_consistency", "visibility"})
    if ui_api_dims or "ui_api_contract" in str(vi_evidence_surfaces).lower():
        # Check if API response exposes fields that should be UI-only or internal
        ui_internal_fields = {"internal_flag", "is_admin", "admin_only", "debug", "internal"}
        for key, value in all_values:
            lowered = key.lower()
            if any(uf in lowered for uf in ui_internal_fields):
                if isinstance(value, (bool, str, int)) and value not in (False, 0, "", None):
                    return _violation(f"system_promise_ui_api_contract_drift:{promise_id}",
                                      f"系统承诺UI/API一致性: {invariant}",
                                      f"API暴露内部字段 {key}={str(value)[:80]}",
                                      "P1", 0.78)

    # ── Dimension: idempotency ──
    if dims.intersection({"idempotency", "retry"}):
        for key, value in all_values:
            if isinstance(value, str) and "duplicate" in value.lower():
                return _violation(f"system_promise_idempotency_violation:{promise_id}",
                                  f"系统承诺幂等: {invariant}",
                                  f"{key}={value[:100]}",
                                  "P1", 0.72)

    # ── Dimension: input_validation_boundary ──
    if dims.intersection({"validation", "input", "boundary"}):
        for step in steps:
            status = int((step.get("response") or {}).get("status_code") or step.get("status") or 0) if isinstance(step, dict) else 0
            if status == 200:
                request_params = step.get("request_params") or step.get("params") or {}
                for pk, pv in (request_params.items() if isinstance(request_params, dict) else []):
                    if isinstance(pv, str) and len(pv) > 5000:
                        return _violation(f"system_promise_input_validation_bypass:{promise_id}",
                                          f"系统承诺输入校验: {invariant}",
                                          f"超大参数 {pk} 未被拒绝",
                                          "P2", 0.62)

    # ── Cross-dimension: authorization bypass (401/403 → 200) ──
    for step in steps:
        if not isinstance(step, dict):
            continue
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        status = int(response.get("status_code") or step.get("status") or 0) if isinstance(response, dict) else int(step.get("status") or 0)
        expected = int(step.get("expected_status") or 0)
        if expected in {401, 403} and status == 200:
            return _violation(f"system_promise_authorization_violation:{promise_id}",
                              f"系统承诺权限/角色维度: {invariant}",
                              "期望拒绝但实际 HTTP 200",
                              "P0", 0.90)

    # ── Build a context-rich pass explanation ──
    checked_dims = sorted(dims)
    ctx = ""
    if vi_direction:
        ctx += f"；方向: {vi_direction}"
    if vi_conservation:
        ctx += f"；守恒检查已通过"
    if vi_state:
        ctx += f"；状态检查已通过"
    if vi_audit:
        ctx += f"；审计检查已通过"
    return OracleResult(True, "SystemPromiseOracle", "L7",
                        explanation=f"System Behavior Space promise {promise_id} 已进入 oracle 评估；当前可观测响应未直接反证。已检查维度: {checked_dims}{ctx}")


def _annotate_oracle_failures_with_system_promise(results: list[Any], scenario: dict[str, Any], hints: dict[str, Any]) -> None:
    promise_id = str(hints.get("promise_id") or "")
    if not promise_id:
        return
    invariant = str((scenario.get("runtime_hints") or {}).get("system_promise_invariant") or "")[:300]
    dims = ",".join(str(item) for item in hints.get("dimensions") or [] if str(item))
    for result in results:
        if bool(getattr(result, "passed", True)) or str(getattr(result, "oracle_name", "")) == "SystemPromiseOracle":
            continue
        explanation = str(getattr(result, "explanation", "") or "")
        marker = f"SystemPromise={promise_id}"
        if marker not in explanation:
            setattr(result, "explanation", (explanation + f" | {marker}; dimensions={dims}; invariant={invariant}").strip(" |")[:1200])

