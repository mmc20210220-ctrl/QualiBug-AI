"""Helper functions: login, probes, diagnosis, funnel building."""
from __future__ import annotations

import json
import os
import time
import urllib.request
import urllib.error
from collections import Counter
from pathlib import Path
from typing import Any

from ._common import *  # noqa: F401,F403


def _live_mode_or_plan(configured_mode: Any, live_execution_allowed: bool) -> str:
    """Preserve a requested read-only mode only after the shared safety gate."""
    return str(configured_mode or "plan_only").lower() if live_execution_allowed else "plan_only"


def _fetch_json_or_text(url: str, method: str = "GET", body: Any | None = None, token: str | None = None, timeout: int = 10) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    raw_body = None
    started_at = time.perf_counter()
    if body is not None:
        raw_body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = f"Bearer {token}"
    try:
        req = urllib.request.Request(url, data=raw_body, method=method, headers=headers)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            text = resp.read(300_000).decode("utf-8", errors="replace")
            return {"ok": 200 <= resp.status < 400, "status_code": resp.status, "body": text, "error": None, "duration_seconds": time.perf_counter() - started_at}
    except urllib.error.HTTPError as exc:
        text = ""
        try:
            text = exc.read(300_000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return {"ok": False, "status_code": exc.code, "body": text, "error": str(exc), "duration_seconds": time.perf_counter() - started_at}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": "", "error": str(exc), "duration_seconds": time.perf_counter() - started_at}


def _apply_browser_health_probe_policy(
    probes: list[dict[str, Any]],
    browser_health: dict[str, Any],
) -> list[dict[str, Any]]:
    reason_code = str(browser_health.get("reason_code") or "")
    if reason_code in {"", "OK"}:
        return probes
    updated: list[dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        source = str(probe.get("source") or "")
        family = str(probe.get("defect_family") or resolve_defect_family(probe).get("family_id") or "")
        if source not in _BROWSER_UI_BLOCKED_SOURCES and family not in {"ui", "uiux", "compatibility"}:
            updated.append(probe)
            continue
        evidence = probe.get("evidence") if isinstance(probe.get("evidence"), dict) else {}
        updated.append(
            {
                **probe,
                "execution_policy": "candidate_only",
                "status": "capability_blocked",
                "confidence_prior": min(float(probe.get("confidence_prior") or 0.35), 0.2),
                "capability_gate": "browser_ui_unavailable",
                "capability_gate_reason_code": reason_code,
                "capability_gate_reason": str(browser_health.get("reason") or ""),
                "evidence": {
                    **evidence,
                    "browser_ui_health": {
                        "reason_code": reason_code,
                        "severity": browser_health.get("severity"),
                        "reason": browser_health.get("reason"),
                        "action": browser_health.get("action"),
                    },
                },
            }
        )
    return updated


def _augment_risk_plan_with_browser_health(
    risk_plan: dict[str, Any] | None,
    probes: list[dict[str, Any]],
    browser_health: dict[str, Any],
) -> dict[str, Any] | None:
    if not isinstance(risk_plan, dict):
        return risk_plan
    reason_code = str(browser_health.get("reason_code") or "")
    updated = dict(risk_plan)
    updated["selected_probes"] = [dict(p) for p in probes if isinstance(p, dict)]
    summary = dict(updated.get("summary") or {})
    blocked = [p for p in probes if str(p.get("capability_gate") or "") == "browser_ui_unavailable"]
    blocked_sources: dict[str, int] = {}
    fallback_family_counts: dict[str, int] = {}
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        family = str(probe.get("defect_family") or resolve_defect_family(probe).get("family_id") or "unknown")
        if str(probe.get("capability_gate") or "") == "browser_ui_unavailable":
            source = str(probe.get("source") or "unknown")
            blocked_sources[source] = blocked_sources.get(source, 0) + 1
        else:
            fallback_family_counts[family] = fallback_family_counts.get(family, 0) + 1
    fallback_families = [item[0] for item in sorted(fallback_family_counts.items(), key=lambda kv: (-kv[1], kv[0]))[:5]]
    summary.update(
        {
            "browser_ui_budget_constrained": reason_code not in {"", "OK"},
            "browser_ui_reason_code": reason_code,
            "browser_ui_blocked_probe_count": len(blocked),
            "browser_ui_blocked_source_distribution": dict(sorted(blocked_sources.items(), key=lambda kv: (-kv[1], kv[0]))),
            "browser_ui_fallback_families": fallback_families,
        }
    )
    updated["summary"] = summary
    return updated


def _extract_token(login_response: dict[str, Any]) -> str | None:
    try:
        data = json.loads(login_response.get("body") or "{}")
    except Exception:
        return None
    for key in ("token", "access_token", "jwt"):
        if data.get(key):
            return str(data[key])
    return None


def _login(cfg: dict[str, Any], account: dict[str, Any], timeout: int) -> dict[str, Any]:
    username = account.get("username") or account.get("user")
    password = account.get("password") or account.get("pass")
    if not username or not password:
        return {"token": account.get("token"), "response": {"skipped": True, "message": "账号无用户名密码，使用静态 token 或跳过"}}
    url = _join_url(str(cfg.get("base_url") or ""), str(cfg.get("login_api") or "/auth/login"))
    response = _fetch_json_or_text(url, "POST", {"username": username, "password": password}, timeout=timeout)
    return {"token": _extract_token(response) or account.get("token"), "response": response}


def _path_keywords(path: str, method: str) -> set[str]:
    text = f"{method} {path}".lower()
    keys: set[str] = set()
    mapping = {
        "admin": ["admin", "manage"],
        "order": ["order", "orders"],
        "coupon": ["coupon", "voucher", "discount"],
        "stock": ["stock", "inventory"],
        "payment": ["payment", "pay", "callback"],
        "refund": ["refund"],
        "tenant": ["tenant", "org", "organization"],
        "address": ["address"],
        "delete": ["delete", "remove"],
        "checkout": ["checkout"],
        "cart": ["cart"],
    }
    for key, words in mapping.items():
        if any(w in text for w in words):
            keys.add(key)
    if method.upper() in {"POST", "PUT", "PATCH", "DELETE"}:
        keys.add("mutation")
    return keys



def _load_enterprise_history_patterns(project_id: str, root: Path | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = root / "platform_workspace" / project / "defect_discovery" / "enterprise_bug_pattern_library.json"
    data = _load_json(path, {})
    if isinstance(data, dict) and isinstance(data.get("items"), list):
        return [item for item in data["items"] if isinstance(item, dict)]
    return []


def _history_pattern_matches_path(pattern: dict[str, Any], path: str, method: str) -> bool:
    text = f"{method} {path}".lower()
    for api in pattern.get("related_apis") or []:
        api_text = str(api).lower().replace("{id}", "")
        if api_text and (api_text in text or text in api_text):
            return True
    for kw in pattern.get("keywords") or []:
        kw = str(kw).lower()
        if len(kw) >= 3 and kw in text:
            return True
    risk = str(pattern.get("risk_type") or "")
    keys = _path_keywords(path, method)
    return (risk == "permission_bypass" and "admin" in keys) or (risk == "idor" and "order" in keys) or (risk == "coupon_abuse" and "coupon" in keys) or (risk == "stock_consistency" and ("stock" in keys or "inventory" in keys or "checkout" in keys)) or (risk == "payment" and "payment" in keys) or (risk == "refund" and "refund" in keys) or (risk == "tenant_isolation" and "tenant" in keys)


def generate_history_informed_probes(openapi: dict[str, Any], cfg: dict[str, Any], project_id: str, root: Path | None = None, max_count: int | None = None) -> list[dict[str, Any]]:
    root = root or ROOT
    mode = str(cfg.get("discovery_mode") or "safe").lower()
    allow_destructive = bool(cfg.get("allow_destructive_tests"))
    max_count = int(max_count or max(20, int(cfg.get("max_probe_count") or 100) // 2))
    patterns = _load_enterprise_history_patterns(project_id, root)
    probes: list[dict[str, Any]] = []
    if not patterns:
        return probes
    for pattern in sorted(patterns, key=lambda p: (-float(p.get("confidence_prior") or 0), str(p.get("risk_type") or ""))):
        risk = str(pattern.get("risk_type") or "business_rule")
        severity = str(pattern.get("severity") or "P2")
        destructive = risk in DESTRUCTIVE_RISK_TYPES or risk in {"payment", "refund", "idempotency"}
        if destructive and (mode == "safe" or not allow_destructive):
            # Preserve the enterprise risk signal, but do not execute destructive probes unless allowed.
            execution_policy = "candidate_only"
        else:
            execution_policy = "execute"
        matched_any = False
        for path, methods in (openapi.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            for method in methods.keys():
                method_u = str(method).upper()
                if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                if not _history_pattern_matches_path(pattern, path, method_u):
                    continue
                matched_any = True
                probes.append({
                    "probe_id": f"RP_HIST_{len(probes)+1:04d}",
                    "source": "enterprise_history_rag",
                    "history_pattern_id": pattern.get("pattern_id"),
                    "risk_type": risk,
                    "title": f"历史缺陷模式复测：{pattern.get('module') or risk}",
                    "actor": "normal_user",
                    "path": path,
                    "method": method_u,
                    "severity": severity,
                    "expected": pattern.get("recommended_probe_strategy") or "历史缺陷模式不应复现",
                    "bug_signal": pattern.get("business_impact") or "与历史缺陷模式相同的异常响应或状态变化",
                    "destructive": destructive,
                    "execution_policy": execution_policy,
                    "confidence_prior": pattern.get("confidence_prior", 0.6),
                    "matched_keywords": pattern.get("keywords", [])[:10],
                    "discovery_mode": mode,
                })
                if len(probes) >= max_count:
                    return probes
        if not matched_any and len(probes) < max_count:
            related = (pattern.get("related_apis") or [""])[0] or "/"
            probes.append({
                "probe_id": f"RP_HIST_{len(probes)+1:04d}",
                "source": "enterprise_history_rag",
                "history_pattern_id": pattern.get("pattern_id"),
                "risk_type": risk,
                "title": f"历史缺陷模式候选：{pattern.get('module') or risk}",
                "actor": "normal_user",
                "path": related,
                "method": "GET",
                "severity": severity,
                "expected": pattern.get("recommended_probe_strategy") or "历史缺陷模式不应复现",
                "bug_signal": pattern.get("business_impact") or "与历史缺陷模式相同的异常响应或状态变化",
                "destructive": destructive,
                "execution_policy": "candidate_only",
                "confidence_prior": pattern.get("confidence_prior", 0.6),
                "matched_keywords": pattern.get("keywords", [])[:10],
                "discovery_mode": mode,
            })
    return probes

def generate_real_project_probes(openapi: dict[str, Any], cfg: dict[str, Any], max_count: int | None = None) -> list[dict[str, Any]]:
    mode = str(cfg.get("discovery_mode") or "safe").lower()
    allow_destructive = bool(cfg.get("allow_destructive_tests"))
    max_count = int(max_count or cfg.get("max_probe_count") or 100)
    probes: list[dict[str, Any]] = []
    for path, methods in (openapi.get("paths") or {}).items():
        if not isinstance(methods, dict):
            continue
        for method, spec in methods.items():
            method_u = method.upper()
            if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            keys = _path_keywords(path, method_u)
            base = {
                "path": path,
                "method": method_u,
                "operation_id": (spec or {}).get("operationId") if isinstance(spec, dict) else None,
                "source": "real_project_pattern",
                "discovery_mode": mode,
            }
            if "admin" in keys:
                probes.append({**base, "probe_id": f"RP_AUTH_ADMIN_{len(probes)+1:04d}", "risk_type": "permission_bypass", "title": "普通用户访问管理员接口", "actor": "normal_user", "expected": "403/401", "bug_signal": "返回 2xx 且包含业务数据", "destructive": False, "severity": "P1"})
            if "order" in keys and "GET" == method_u and ("{" in path or "}" in path):
                probes.append({**base, "probe_id": f"RP_IDOR_ORDER_{len(probes)+1:04d}", "risk_type": "idor", "title": "订单详情疑似水平越权", "actor": "normal_user", "expected": "只能访问本人订单", "bug_signal": "可访问他人资源或无归属校验", "destructive": False, "severity": "P1"})
            if "tenant" in keys:
                probes.append({**base, "probe_id": f"RP_TENANT_{len(probes)+1:04d}", "risk_type": "tenant_isolation", "title": "租户数据隔离风险", "actor": "normal_user", "expected": "跨租户访问应拒绝", "bug_signal": "返回其他租户数据", "destructive": False, "severity": "P0"})
            if mode in {"standard", "aggressive"}:
                if "coupon" in keys:
                    probes.append({**base, "probe_id": f"RP_COUPON_{len(probes)+1:04d}", "risk_type": "coupon_abuse", "title": "优惠券规则绕过风险", "actor": "normal_user", "expected": "优惠券门槛、归属、有效期和重复使用受控", "bug_signal": "异常优惠仍可使用或重复抵扣", "destructive": method_u != "GET", "severity": "P1"})
                if "stock" in keys or "inventory" in keys or "checkout" in keys:
                    probes.append({**base, "probe_id": f"RP_STOCK_{len(probes)+1:04d}", "risk_type": "stock_consistency", "title": "库存一致性风险", "actor": "normal_user", "expected": "库存不足不可下单且库存变更一致", "bug_signal": "库存不足成功或状态不一致", "destructive": method_u != "GET", "severity": "P1"})
                if "payment" in keys:
                    probes.append({**base, "probe_id": f"RP_PAYMENT_{len(probes)+1:04d}", "risk_type": "payment", "title": "支付金额和状态一致性风险", "actor": "normal_user", "expected": "支付金额、订单状态和回调幂等一致", "bug_signal": "金额不一致仍成功或重复回调重复入账", "destructive": True, "severity": "P0"})
                if "refund" in keys:
                    probes.append({**base, "probe_id": f"RP_REFUND_{len(probes)+1:04d}", "risk_type": "refund", "title": "退款状态和金额一致性风险", "actor": "normal_user", "expected": "退款金额、状态和幂等受控", "bug_signal": "重复退款、超额退款或未支付退款成功", "destructive": True, "severity": "P0"})
            if mode == "aggressive" and allow_destructive:
                if method_u in {"POST", "PUT", "PATCH"} and ("order" in keys or "checkout" in keys or "payment" in keys):
                    probes.append({**base, "probe_id": f"RP_IDEMPOTENCY_{len(probes)+1:04d}", "risk_type": "idempotency", "title": "重复提交 / 幂等风险", "actor": "normal_user", "expected": "相同幂等键重复提交不应产生重复业务结果", "bug_signal": "重复创建订单、重复扣款或重复扣库存", "destructive": True, "severity": "P1"})
    filtered = []
    for p in probes:
        if p.get("destructive") and (mode == "safe" or not allow_destructive and mode == "aggressive"):
            continue
        if mode == "standard" and p.get("risk_type") in DESTRUCTIVE_RISK_TYPES and not allow_destructive:
            # standard keeps non-destructive risk candidates, but avoids direct payment/refund mutation execution.
            p = {**p, "execution_policy": "candidate_only"}
        filtered.append(p)
        if len(filtered) >= max_count:
            break
    return filtered


def _status_suspicious(probe: dict[str, Any], response: dict[str, Any]) -> tuple[bool, float, str]:
    code = response.get("status_code")
    risk = probe.get("risk_type")
    if code is None:
        return False, 0.2, "接口未执行成功，作为风险候选保留但不判定为缺陷"
    if risk in {"permission_bypass", "idor", "tenant_isolation"} and 200 <= int(code) < 300:
        return True, 0.82, "访问控制类探针返回 2xx，需要 QA 确认是否越权"
    if risk in {"coupon_abuse", "stock_consistency", "payment", "refund", "idempotency"} and 200 <= int(code) < 300:
        return True, 0.66, "业务一致性类探针返回成功，需要结合状态数据确认"
    return False, 0.35, "响应未触发明显缺陷信号"


def _append_adapter_issue(
    issues: list[dict[str, Any]],
    evidence_items: list[dict[str, Any]],
    issue: dict[str, Any],
) -> None:
    if not isinstance(issue, dict):
        return
    family = resolve_defect_family(issue)
    normalized_issue = dict(issue)
    normalized_issue.setdefault("defect_family", family.get("family_id"))
    normalized_issue.setdefault("status", "needs_human_review")
    normalized_issue.setdefault("qa_feedback_status", "pending")
    normalized_issue.setdefault("risk_type", normalized_issue.get("defect_family") or "scenario_flow")
    normalized_issue.setdefault("issue_id", f"ISSUE_ADAPTER_{len(issues)+1:04d}")
    normalized_issue.setdefault("evidence", {})
    issues.append(normalized_issue)
    evidence = normalized_issue.get("evidence") if isinstance(normalized_issue.get("evidence"), dict) else {}
    request = evidence.get("request") if isinstance(evidence.get("request"), dict) else {
        "method": normalized_issue.get("method") or "GET",
        "url": normalized_issue.get("path") or normalized_issue.get("route") or normalized_issue.get("risk_type") or "adapter_signal",
    }
    response = evidence.get("response") if isinstance(evidence.get("response"), dict) else evidence
    evidence_items.append(
        {
            "issue_id": normalized_issue["issue_id"],
            "probe_id": normalized_issue.get("probe_id"),
            "request": request,
            "response": response,
            "expected": normalized_issue.get("expected"),
            "actual": normalized_issue.get("actual"),
            "confidence": normalized_issue.get("confidence"),
        }
    )


def _top_reason_rows(values: list[str], *, limit: int = 5) -> list[dict[str, Any]]:
    counter = Counter(str(value).strip() for value in values if str(value).strip())
    return [
        {"reason_code": reason_code, "count": count}
        for reason_code, count in counter.most_common(limit)
    ]


def _safe_rate(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round(numerator / denominator, 3)


def _build_low_discovery_diagnosis(
    funnel: dict[str, Any],
    blocker_summary: dict[str, Any],
    *,
    probe_count: int,
    issue_count: int,
) -> dict[str, Any]:
    candidate_count = int(((funnel.get("candidate_generation") or {}).get("output_count")) or 0)
    selected_count = int(((funnel.get("probe_selection") or {}).get("output_count")) or 0)
    executed_count = int(((funnel.get("execution") or {}).get("output_count")) or 0)
    verifier_passed_count = int(((funnel.get("verification") or {}).get("output_count")) or 0)
    validated_bug_count = int(((funnel.get("formal_accounting") or {}).get("output_count")) or 0)

    category_rows = [
        {
            "category": "no_candidate",
            "label": "无候选",
            "count": 1 if candidate_count <= 0 else 0,
            "stage": "candidate_generation",
            "top_blockers": list((funnel.get("candidate_generation") or {}).get("top_blockers") or []),
            "next_action": "补充知识资产、输入桥接或候选生成召回。",
        },
        {
            "category": "not_selected",
            "label": "未入选",
            "count": max(0, candidate_count - selected_count),
            "stage": "probe_selection",
            "top_blockers": list((funnel.get("probe_selection") or {}).get("top_blockers") or []),
            "next_action": "复盘 probe 预算、validated yield 权重和关键路径优先级。",
        },
        {
            "category": "execution_failed",
            "label": "执行失败",
            "count": max(0, selected_count - executed_count),
            "stage": "execution",
            "top_blockers": list((funnel.get("execution") or {}).get("top_blockers") or []),
            "next_action": "优先排查环境接入、路径对齐、权限与安全门禁。",
        },
        {
            "category": "verification_failed",
            "label": "验证失败",
            "count": max(0, issue_count - verifier_passed_count),
            "stage": "verification",
            "top_blockers": list((funnel.get("verification") or {}).get("top_blockers") or []),
            "next_action": "收紧 verifier 前先补路径命中、响应判定和拒绝原因归因。",
        },
        {
            "category": "evidence_insufficient",
            "label": "证据不足",
            "count": max(0, verifier_passed_count - validated_bug_count),
            "stage": "formal_accounting",
            "top_blockers": list((funnel.get("formal_accounting") or {}).get("top_blockers") or []),
            "next_action": "补复现步骤、证据引用和可交付 reproduction pack。",
        },
    ]

    validated_bug_discovery_rate = _safe_rate(validated_bug_count, probe_count)
    is_zero_validated_bug = validated_bug_count <= 0
    is_low_discovery = is_zero_validated_bug or (
        probe_count > 0 and validated_bug_discovery_rate < 0.1
    )

    if candidate_count <= 0:
        primary_row = category_rows[0]
    elif selected_count <= 0:
        primary_row = category_rows[1]
    elif executed_count <= 0:
        primary_row = category_rows[2]
    elif verifier_passed_count <= 0:
        primary_row = category_rows[3]
    elif validated_bug_count <= 0:
        primary_row = category_rows[4]
    else:
        ranked_rows = sorted(
            (row for row in category_rows if int(row.get("count") or 0) > 0),
            key=lambda row: int(row.get("count") or 0),
            reverse=True,
        )
        primary_row = ranked_rows[0] if ranked_rows else {
            "category": "validated",
            "label": "已形成 validated bug",
            "count": validated_bug_count,
            "stage": "formal_accounting",
            "top_blockers": [],
            "next_action": "继续围绕高价值路径扩面，稳定 validated yield。",
        }

    primary_blockers = list(primary_row.get("top_blockers") or [])
    primary_reason_code = str((primary_blockers[0] or {}).get("reason_code") or "") if primary_blockers else ""

    return {
        "reporting_basis": "validated_bug",
        "probe_count": probe_count,
        "issue_count": issue_count,
        "candidate_count": candidate_count,
        "selected_count": selected_count,
        "executed_count": executed_count,
        "verifier_passed_count": verifier_passed_count,
        "validated_bug_count": validated_bug_count,
        "validated_bug_discovery_rate": validated_bug_discovery_rate,
        "is_zero_validated_bug": is_zero_validated_bug,
        "is_low_discovery": is_low_discovery,
        "primary_category": str(primary_row.get("category") or ""),
        "primary_label": str(primary_row.get("label") or ""),
        "primary_stage": str(primary_row.get("stage") or ""),
        "primary_reason_code": primary_reason_code,
        "recommended_action": str(primary_row.get("next_action") or ""),
        "category_rows": category_rows,
        "top_blockers": list(blocker_summary.get("top_blockers") or []),
    }


def _execution_attempted(execution: dict[str, Any]) -> bool:
    error = str(execution.get("error") or "")
    if error in {
        "candidate_only_or_missing_base_url",
        "destructive_probe_blocked",
        "write_probe_blocked_by_safety_gate",
        "delegated_to_business_assurance_coverage",
        "delegated_to_enterprise_business_knowledge_asset",
        "non_http_audit_source",
    }:
        return False
    return execution.get("response_status") is not None or execution.get("duration_seconds") is not None or bool(error)


def _strict_verifier_for_issue(issue: dict[str, Any]) -> dict[str, Any]:
    """V13 strict verifier: each candidate issue MUST pass ALL gates to become ready_bug.

    Gates (all must pass):
    1. request method/path matches the declared bug's API reference
    2. response status/body is consistent with bug description
    3. has explicit expected/actual comparison
    4. has failed_assertions list (at least one non-trivial assertion)
    5. has reproduction_steps (not synthetic/generated placeholders)
    6. has evidence_refs (links back to HAR entries, DB snapshots, etc.)
    7. verification.verdict == "validated_bug"
    8. is_reproducible == True
    9. gate_passed == True

    Issues that fail ANY gate are classified as:
    - "internal_validation_lead" (partial evidence, needs human)
    - "coverage_gap" (quality assurance gap, not a bug)
    - "rejected_evidence" (evidence contradicts the claim)
    - "route_blocked" / "auth_blocked" / "environment_blocked" (execution blocked)

    Only issues that pass ALL gates may enter the customer-facing data.risks list.
    """
    result: dict[str, Any] = {
        "passes_strict_verifier": False,
        "verdict": "pending",
        "failed_gates": [],
        "reasons": [],
    }

    # ── Gate 1: Request method/path consistency ──
    repro = issue.get("reproduction") or {}
    har = issue.get("har_evidence") or {}
    api_path = (repro.get("path") or issue.get("repro_path") or
                issue.get("_api_path") or har.get("path") or "")
    api_method = (repro.get("method") or issue.get("repro_method") or
                  issue.get("_api_method") or har.get("method") or "")
    if not api_method or not api_path:
        result["failed_gates"].append("no_api_reference")
        result["reasons"].append("缺少请求方法和路径，无法追溯到 API 调用")

    # ── Gate 2: Response status/body consistency ──
    har_status = har.get("status_code") or 0
    har_body = har.get("response_body") or ""
    title = str(issue.get("title") or "")
    description = str(issue.get("description") or "")
    claim_text = f"{title} {description}"

    # Check for claimed vs actual status mismatch.
    # Only flag when the claim *asserts* a specific error was returned
    # (e.g. "返回500", "HTTP 500"), NOT when it says something *should* be
    # returned (e.g. "should return 401", "预期返回 401").
    import re as _re2
    # Match patterns like "返回500", "HTTP 500" — claims about observed status
    claimed_observed = set(
        int(m) for m in _re2.findall(
            r'(?:返回|HTTP\s*|状态码\s*|responded?\s+with\s+|got\s+)(\d{3})',
            claim_text, _re2.IGNORECASE
        )
        if 400 <= int(m) <= 599
    )
    if har_status and 200 <= har_status < 300 and claimed_observed:
        result["failed_gates"].append("status_contradiction")
        result["reasons"].append(
            f"声明-证据矛盾：声称返回错误码 {claimed_observed}，实际响应 {har_status}（成功）"
        )

    # ── Gate 3: expected/actual comparison ──
    expected = issue.get("expected") or issue.get("expected_behavior") or ""
    actual = issue.get("actual") or issue.get("actual_behavior") or ""
    if not expected:
        result["failed_gates"].append("missing_expected_behavior")
        result["reasons"].append("缺少预期行为，无法判断业务规则应当如何成立")
    if not actual:
        result["failed_gates"].append("missing_actual_behavior")
        result["reasons"].append("缺少实际行为，无法证明系统当前表现与预期不一致")
    if not expected and not actual:
        result["failed_gates"].append("no_expected_actual")

    # ── Gate 4: failed_assertions ──
    failed_assertions = issue.get("failed_assertions") or []
    if not failed_assertions or len(failed_assertions) == 0:
        result["failed_gates"].append("no_failed_assertions")
        result["reasons"].append("缺少失败断言，无法证明业务规则被违反")

    # ── Gate 5: reproduction_steps (not synthetic) ──
    repro_steps = repro.get("steps") or []
    is_synthetic = bool(repro.get("is_synthetic")) or False
    if not repro_steps or len(repro_steps) == 0 or is_synthetic:
        result["failed_gates"].append("no_real_reproduction_steps")
        result["reasons"].append("缺少真实复现步骤（当前仅有合成/生成占位步骤）")

    # ── Gate 6: evidence_refs ──
    evidence_refs = issue.get("evidence_refs") or []
    if not evidence_refs or len(evidence_refs) == 0:
        result["failed_gates"].append("no_evidence_refs")
        result["reasons"].append("缺少证据引用，无法追溯到 HAR/DB/日志等原始证据")

    # ── Gate 7: verification.verdict ──
    verification = issue.get("verification") or {}
    verdict = str(verification.get("verdict") or "").strip()
    if verdict != "validated_bug":
        result["failed_gates"].append("verdict_not_validated")
        result["reasons"].append(
            f"verification.verdict 不是 validated_bug（当前: {verdict or '缺失'}）"
        )

    # ── Gate 8: is_reproducible ──
    if not bool(issue.get("is_reproducible")):
        result["failed_gates"].append("not_reproducible")
        result["reasons"].append("is_reproducible 不为 True")

    # ── Gate 9: gate_passed ──
    if not bool(issue.get("gate_passed")):
        result["failed_gates"].append("gate_not_passed")
        result["reasons"].append("gate_passed 不为 True")

    # ── Final classification ──
    if not result["failed_gates"]:
        result["passes_strict_verifier"] = True
        result["verdict"] = "validated_bug"
        result["value_lane"] = "ready_bug"
    elif any(g in ("no_api_reference", "no_evidence_refs", "no_real_reproduction_steps")
             for g in result["failed_gates"]):
        result["verdict"] = "internal_validation_lead"
        result["value_lane"] = "validation_lead"
    elif any(g in ("status_contradiction", "verdict_not_validated")
             for g in result["failed_gates"]):
        result["verdict"] = "rejected_evidence"
        result["value_lane"] = "rejected_evidence"
    elif "no_failed_assertions" in result["failed_gates"]:
        result["verdict"] = "coverage_gap"
        result["value_lane"] = "coverage_gap"
    else:
        result["verdict"] = "internal_validation_lead"
        result["value_lane"] = "validation_lead"

    return result


def _build_discovery_funnel(
    probes: list[dict[str, Any]],
    executions: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    risk_plan: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    plan_summary = (risk_plan or {}).get("summary") if isinstance(risk_plan, dict) else {}
    skipped_probes = (risk_plan or {}).get("skipped_probes") if isinstance(risk_plan, dict) else []
    candidate_probe_count = int(plan_summary.get("candidate_probe_count") or len(probes))
    selected_probe_count = int(plan_summary.get("selected_probe_count") or len(probes))
    skipped_probe_count = int(plan_summary.get("skipped_probe_count") or max(0, candidate_probe_count - selected_probe_count))
    selection_reasons = [
        str(item.get("reason") or "")
        for item in skipped_probes
        if isinstance(item, dict)
    ]

    executed_items = [item for item in executions if isinstance(item, dict) and _execution_attempted(item)]
    execution_reasons = [
        str(item.get("error") or "")
        for item in executions
        if isinstance(item, dict) and not _execution_attempted(item) and str(item.get("error") or "").strip()
    ]
    execution_failure_reasons = [
        f"request_{str(item.get('error') or '').strip()}"
        for item in executed_items
        if str(item.get("error") or "").strip()
    ]

    accounting_rows = [classify_issue_accounting(item) for item in issues if isinstance(item, dict)]

    # ── V13 Strict Verification: every issue must pass ALL 9 gates ──
    strict_verifier_results = [_strict_verifier_for_issue(item) for item in issues if isinstance(item, dict)]
    for i, issue in enumerate(issues):
        if i < len(strict_verifier_results):
            sr = strict_verifier_results[i]
            issue["_strict_verifier_passed"] = sr["passes_strict_verifier"]
            issue["_strict_verifier_verdict"] = sr["verdict"]
            issue["_strict_verifier_failed_gates"] = sr["failed_gates"]
            issue["_strict_verifier_reasons"] = sr["reasons"]
            issue["_value_lane"] = sr.get("value_lane", "validation_lead")
            # Only promote to ready_bug if strict verifier passes
            if sr["passes_strict_verifier"]:
                issue["gate_passed"] = True
                issue["is_reproducible"] = True
                issue["verification"] = issue.get("verification") or {}
                issue["verification"]["verdict"] = "validated_bug"
                issue["bug_status"] = "reproduced"
            elif sr["verdict"] == "rejected_evidence":
                issue["gate_passed"] = False
                issue["is_reproducible"] = False
                issue["bug_status"] = "not_reproduced"
            elif sr["verdict"] in ("coverage_gap", "internal_validation_lead"):
                issue["gate_passed"] = False
                issue["is_reproducible"] = False
                if issue.get("bug_status") != "reproduced":
                    issue["bug_status"] = "suspected"

    verifier_passed_count = sum(1 for item in accounting_rows if item.get("verifier_passed"))
    strict_verifier_passed_count = sum(1 for sr in strict_verifier_results if sr["passes_strict_verifier"])
    validated_bug_count = sum(1 for item in accounting_rows if item.get("strict_validated_bug"))
    # Use strict verifier count as the primary validated_bug metric
    validated_bug_count = max(validated_bug_count, strict_verifier_passed_count)
    candidate_issue_count = sum(1 for item in accounting_rows if item.get("accounting_state") == "candidate")
    pending_finding_count = sum(1 for item in accounting_rows if item.get("accounting_state") == "pending")
    saleable_count = sum(1 for item in accounting_rows if item.get("saleable"))
    coverage_gap_count = sum(1 for item in accounting_rows if item.get("quality_tier") == "coverage_gap")
    unexecuted_count = sum(1 for item in accounting_rows if item.get("quality_tier") == "unexecuted")

    # Tag issues with quality tier for reporting
    for i, issue in enumerate(issues):
        if i < len(accounting_rows):
            issue["_saleable"] = accounting_rows[i].get("saleable", False)
            issue["_quality_tier"] = accounting_rows[i].get("quality_tier", "unknown")
    verification_reasons = [
        reason
        for item in accounting_rows
        for reason in item.get("blocker_reason_codes") or []
        if str(reason).startswith("missing_strict_verifier") or str(reason).startswith("verifier_")
    ]
    accounting_reasons = [
        reason
        for item in accounting_rows
        for reason in item.get("blocker_reason_codes") or []
        if str(reason) in {"missing_reproduction", "missing_evidence_refs", "quality_assurance_gap"}
    ]

    funnel = {
        "candidate_generation": {
            "stage": "candidate_generation",
            "input_count": candidate_probe_count,
            "output_count": candidate_probe_count,
            "drop_count": 0,
            "conversion_rate": 1.0 if candidate_probe_count else 0.0,
            "top_blockers": [],
        },
        "probe_selection": {
            "stage": "probe_selection",
            "input_count": candidate_probe_count,
            "output_count": selected_probe_count,
            "drop_count": skipped_probe_count,
            "conversion_rate": round(selected_probe_count / max(1, candidate_probe_count), 3),
            "top_blockers": _top_reason_rows(selection_reasons),
        },
        "execution": {
            "stage": "execution",
            "input_count": selected_probe_count,
            "output_count": len(executed_items),
            "drop_count": max(0, selected_probe_count - len(executed_items)),
            "conversion_rate": round(len(executed_items) / max(1, selected_probe_count), 3),
            "top_blockers": _top_reason_rows([*execution_reasons, *execution_failure_reasons]),
        },
        "verification": {
            "stage": "verification",
            "input_count": len(issues),
            "output_count": verifier_passed_count,
            "drop_count": max(0, len(issues) - verifier_passed_count),
            "conversion_rate": round(verifier_passed_count / max(1, len(issues)), 3),
            "top_blockers": _top_reason_rows(verification_reasons),
        },
        "formal_accounting": {
            "stage": "formal_accounting",
            "input_count": verifier_passed_count,
            "output_count": validated_bug_count,
            "drop_count": max(0, verifier_passed_count - validated_bug_count),
            "conversion_rate": round(validated_bug_count / max(1, verifier_passed_count), 3),
            "top_blockers": _top_reason_rows(accounting_reasons),
        },
    }
    blocker_summary = {
        "selection": funnel["probe_selection"]["top_blockers"],
        "execution": funnel["execution"]["top_blockers"],
        "verification": funnel["verification"]["top_blockers"],
        "formal_accounting": funnel["formal_accounting"]["top_blockers"],
        "top_blockers": _top_reason_rows(
            [
                *selection_reasons,
                *execution_reasons,
                *execution_failure_reasons,
                *verification_reasons,
                *accounting_reasons,
            ]
        ),
        "candidate_issue_count": candidate_issue_count,
        "pending_finding_count": pending_finding_count,
        "validated_bug_count": validated_bug_count,
    }
    blocker_summary["low_discovery_diagnosis"] = _build_low_discovery_diagnosis(
        funnel,
        blocker_summary,
        probe_count=len(probes),
        issue_count=len(issues),
    )
    return funnel, blocker_summary


