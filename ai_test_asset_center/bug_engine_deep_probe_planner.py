"""Deep probe planner for the QualiBug bug engine.

The discovery loop intentionally starts with conservative read-only probes.  Once
validated candidates exist, the engine needs a safe way to decide what to attack
next without silently firing mutating requests against a customer's system.

This module compiles a *plan* only.  It does not execute probes, does not promote
findings, and does not include raw response bodies.  The plan is designed to be
fed into the next disposable-sandbox execution stage or reviewed by an operator.
"""
from __future__ import annotations

import json
import re
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


_STATE_WORDS = (
    "status", "state", "release", "close", "cancel", "start", "complete", "approve", "reject",
    "retry", "submit", "force", "activate", "deactivate", "状态", "审批", "取消", "完成", "关闭",
)
_IDEMPOTENCY_WORDS = (
    "post", "create", "callback", "event", "retry", "submit", "payment", "refund", "order", "receive",
    "ship", "issue", "consume", "integration", "idempotency", "externalref", "message", "重复", "幂等",
)
_CONSERVATION_WORDS = (
    "inventory", "warehouse", "stock", "qty", "quantity", "amount", "balance", "transaction", "order",
    "material", "bom", "lot", "serial", "库存", "数量", "金额", "守恒", "扣减", "入库", "出库",
)
_AUTH_WORDS = ("auth", "permission", "认证", "权限", "anonymous", "匿名", "低权限")


_METHOD_RE = re.compile(r"^(GET|POST|PUT|PATCH|DELETE)\s+(.+)$", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _text(value: Any, limit: int = 500) -> str:
    return str(value or "")[:limit]


def _extract_discovery(result: dict[str, Any]) -> dict[str, Any]:
    return result.get("discovery_result") if isinstance(result.get("discovery_result"), dict) else result


def _parse_call(call: str) -> tuple[str, str]:
    m = _METHOD_RE.match(str(call or "").strip())
    if not m:
        return "GET", ""
    return m.group(1).upper(), m.group(2).strip()


def _calls(finding: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    calls = evidence.get("calls") if isinstance(evidence.get("calls"), list) else []
    return [c for c in calls if isinstance(c, dict)]


def _first_endpoint(finding: dict[str, Any]) -> tuple[str, str]:
    calls = _calls(finding)
    if calls:
        return _parse_call(str(calls[0].get("call") or ""))
    title = str(finding.get("title") or "")
    m = re.search(r"(/api/[\w\-/{}/?=&%.]+)", title)
    return ("GET", m.group(1)) if m else ("GET", "")


def _validated_findings(result: dict[str, Any]) -> list[dict[str, Any]]:
    discovery = _extract_discovery(result)
    findings = discovery.get("findings") if isinstance(discovery.get("findings"), list) else []
    out = []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        verdict = str(finding.get("verdict") or "").lower()
        gate = ((finding.get("evidence") or {}).get("finding_gate") or {}) if isinstance(finding.get("evidence"), dict) else {}
        gate_verdict = str(gate.get("verdict") or "").lower()
        if verdict in {"validated_candidate", "validated"} or gate_verdict == "validated_candidate":
            out.append(finding)
    return out


def _route_map_from_result(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Best-effort route extraction from actual runtime calls.

    We prefer runtime evidence over docs because it is already normalized to the
    target's live paths.  No response values are copied into the plan.
    """
    routes: dict[tuple[str, str], dict[str, Any]] = {}
    discovery = _extract_discovery(result)
    findings = discovery.get("findings") if isinstance(discovery.get("findings"), list) else []
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        for call in _calls(finding):
            method, path = _parse_call(str(call.get("call") or ""))
            if not path:
                continue
            routes[(method, path)] = {"method": method, "path": path, "source": "runtime_evidence"}
    return list(routes.values())


def _parse_openapi_routes(api_spec: str | dict[str, Any] | None) -> list[dict[str, Any]]:
    """Extract route metadata from OpenAPI JSON text/dict or Markdown fragments."""
    if not api_spec:
        return []
    routes: list[dict[str, Any]] = []
    spec: dict[str, Any] = {}
    if isinstance(api_spec, dict):
        spec = api_spec
    elif isinstance(api_spec, str) and api_spec.lstrip().startswith("{"):
        try:
            spec = json.loads(api_spec)
        except Exception:
            spec = {}
    if spec:
        for path, methods in (spec.get("paths") or {}).items():
            if not isinstance(methods, dict):
                continue
            for method, details in methods.items():
                method_u = str(method).upper()
                if method_u not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                    continue
                details = details if isinstance(details, dict) else {}
                body = details.get("requestBody") if isinstance(details.get("requestBody"), dict) else {}
                content = body.get("content") if isinstance(body.get("content"), dict) else {}
                schema = ((content.get("application/json") or {}).get("schema") or {}) if isinstance(content.get("application/json"), dict) else {}
                routes.append({
                    "method": method_u,
                    "path": path if str(path).startswith("/api/") else "/api" + str(path),
                    "summary": _text(details.get("summary") or details.get("operationId") or "", 160),
                    "has_body": bool(body),
                    "schema_keys": sorted((schema.get("properties") or {}).keys())[:20] if isinstance(schema, dict) else [],
                    "source": "openapi",
                })
    if isinstance(api_spec, str):
        route_re = re.compile(r"(?:^|[\s`])(GET|POST|PUT|PATCH|DELETE)\s+`?(/(?:api/)?[A-Za-z0-9_/{}/?&=.-]+)`?", re.I | re.M)
        seen = {(r["method"], r["path"]) for r in routes}
        for m in route_re.finditer(api_spec):
            method = m.group(1).upper()
            path = m.group(2).rstrip("`.，,。")
            if not path.startswith("/api/"):
                path = "/api" + path if path.startswith("/") else "/api/" + path
            if (method, path) not in seen:
                routes.append({"method": method, "path": path, "summary": "", "has_body": method != "GET", "schema_keys": [], "source": "api_text"})
                seen.add((method, path))
    return routes


def _merge_routes(*groups: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[str, str], dict[str, Any]] = {}
    for group in groups:
        for route in group:
            method = str(route.get("method") or "GET").upper()
            path = str(route.get("path") or "").strip()
            if not path:
                continue
            key = (method, path)
            existing = merged.get(key, {})
            merged[key] = {**route, **existing, "method": method, "path": path, "sources": sorted(set(existing.get("sources", [])) | {str(route.get("source") or "unknown")})}
    return sorted(merged.values(), key=lambda r: (str(r.get("method")), str(r.get("path"))))


def _classify_route(route: dict[str, Any]) -> list[str]:
    method = str(route.get("method") or "GET").upper()
    hay = f"{route.get('path','')} {route.get('summary','')} {' '.join(route.get('schema_keys') or [])}".lower()
    kinds: list[str] = []
    if method in {"POST", "PUT", "PATCH", "DELETE"} and any(w in hay for w in _STATE_WORDS):
        kinds.append("state_transition")
    if method in {"POST", "PUT", "PATCH"} and any(w in hay for w in _IDEMPOTENCY_WORDS):
        kinds.append("idempotency_replay")
    if any(w in hay for w in _CONSERVATION_WORDS):
        kinds.append("amount_inventory_conservation")
    if method == "GET" and any(w in hay for w in ("export", "all", "audit", "users", "notification")):
        kinds.append("auth_boundary_regression")
    return kinds


def _risk_from_auth_finding(finding: dict[str, Any]) -> dict[str, Any]:
    method, path = _first_endpoint(finding)
    title_expected = f"{finding.get('title','')} {finding.get('expected','')}".lower()
    return {
        "kind": "auth_boundary_regression",
        "priority": "P0" if str(finding.get("severity")) == "P0" else "P1",
        "endpoint": f"{method} {path}".strip(),
        "source": "validated_candidate",
        "why": "已验证匿名/低权限读取业务数据，必须固化为回归断言并扩展到同根因接口。" if any(w in title_expected for w in _AUTH_WORDS) else "已验证候选需要回归保护。",
        "execution_policy": "read_only_safe",
        "oracle": "no_auth_status in {401,403} and low_privilege does not receive non-empty business data",
    }


def build_deep_probe_plan(
    result: dict[str, Any],
    *,
    api_spec: str | dict[str, Any] | None = None,
    project_id: str = "real_project_demo",
    max_items: int = 24,
) -> dict[str, Any]:
    """Build a safe next-frontier plan from validated findings + route metadata."""
    validated = _validated_findings(result)
    routes = _merge_routes(_route_map_from_result(result), _parse_openapi_routes(api_spec))
    actions: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for finding in validated:
        method, path = _first_endpoint(finding)
        key = ("auth_boundary_regression", f"{method} {path}".strip())
        if path and key not in seen:
            actions.append(_risk_from_auth_finding(finding))
            seen.add(key)

    for route in routes:
        endpoint = f"{route.get('method')} {route.get('path')}"
        for kind in _classify_route(route):
            key = (kind, endpoint)
            if key in seen:
                continue
            destructive = str(route.get("method") or "GET").upper() in {"POST", "PUT", "PATCH", "DELETE"}
            if kind == "state_transition":
                priority = "P1"
                oracle = "illegal transition returns 4xx and before/after state remains unchanged"
                why = "状态流转接口可能存在越权推进、强制改状态或流程绕过。"
            elif kind == "idempotency_replay":
                priority = "P1"
                oracle = "same business key/idempotency key replay must not create duplicate side effects"
                why = "创建/回调/重试类接口需要重复提交和幂等回放验证。"
            elif kind == "amount_inventory_conservation":
                priority = "P1"
                oracle = "quantity/amount totals reconcile across before/action/after observers"
                why = "库存、订单、交易和物料接口需要数量/金额守恒验证。"
            else:
                priority = "P1"
                oracle = "anonymous and low-privilege access must be rejected or empty"
                why = "同类敏感读取接口需要权限边界回归扩展。"
            actions.append({
                "kind": kind,
                "priority": priority,
                "endpoint": endpoint,
                "source": ",".join(route.get("sources") or [route.get("source", "route_map")]),
                "why": why,
                "execution_policy": "disposable_sandbox_required" if destructive else "read_only_safe",
                "oracle": oracle,
                "requires_explicit_approval": bool(destructive),
                "schema_keys": route.get("schema_keys", []),
            })
            seen.add(key)

    priority_order = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    kind_order = {
        "auth_boundary_regression": 0,
        "state_transition": 1,
        "idempotency_replay": 2,
        "amount_inventory_conservation": 3,
    }
    actions = sorted(actions, key=lambda a: (
        priority_order.get(str(a.get("priority")), 9),
        kind_order.get(str(a.get("kind")), 9),
        str(a.get("execution_policy")) != "read_only_safe",
        str(a.get("endpoint")),
    ))
    # Keep the plan varied.  Without this, large inventory/GET surfaces can crowd
    # out state/idempotency probes that are more commercially valuable.
    per_kind_limit = max(2, int(max_items or 1) // 4)
    selected: list[dict[str, Any]] = []
    per_kind: Counter[str] = Counter()
    for action in actions:
        kind = str(action.get("kind"))
        if per_kind[kind] >= per_kind_limit:
            continue
        selected.append(action)
        per_kind[kind] += 1
        if len(selected) >= max(1, int(max_items or 1)):
            break
    if len(selected) < min(len(actions), max(1, int(max_items or 1))):
        for action in actions:
            if action in selected:
                continue
            selected.append(action)
            if len(selected) >= max(1, int(max_items or 1)):
                break
    actions = selected
    by_kind = Counter(str(a.get("kind")) for a in actions)
    by_policy = Counter(str(a.get("execution_policy")) for a in actions)
    return {
        "report_version": "phase92f-deep-probe-plan-v1",
        "generated_at": _now(),
        "project_id": project_id,
        "summary": {
            "validated_candidates_seen": len(validated),
            "routes_seen": len(routes),
            "planned_actions": len(actions),
            "by_kind": dict(sorted(by_kind.items())),
            "by_execution_policy": dict(sorted(by_policy.items())),
            "safety_note": "Mutating/stateful probes are compiled as disposable_sandbox_required and are not executed by autorun without explicit sandbox approval.",
        },
        "actions": actions,
    }


def _markdown(plan: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append("# QualiBug Deep Probe Plan")
    lines.append("")
    lines.append(f"Generated: {plan.get('generated_at')}")
    lines.append(f"Project: `{plan.get('project_id')}`")
    lines.append("")
    s = plan.get("summary") or {}
    lines.append("## Summary")
    lines.append("")
    lines.append(f"- Validated candidates seen: **{s.get('validated_candidates_seen', 0)}**")
    lines.append(f"- Routes seen: {s.get('routes_seen', 0)}")
    lines.append(f"- Planned next actions: **{s.get('planned_actions', 0)}**")
    lines.append(f"- By execution policy: `{json.dumps(s.get('by_execution_policy', {}), ensure_ascii=False)}`")
    lines.append(f"- Safety: {s.get('safety_note')}")
    lines.append("")
    lines.append("## Next-frontier actions")
    lines.append("")
    for idx, action in enumerate(plan.get("actions") or [], start=1):
        lines.append(f"### {idx}. {action.get('kind')} — `{action.get('endpoint')}`")
        lines.append("")
        lines.append(f"- Priority: `{action.get('priority')}`")
        lines.append(f"- Execution policy: `{action.get('execution_policy')}`")
        lines.append(f"- Why: {action.get('why')}")
        lines.append(f"- Oracle: {action.get('oracle')}")
        if action.get("requires_explicit_approval"):
            lines.append("- Approval: requires disposable sandbox approval before execution.")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def build_deep_probe_plan_files(
    result: dict[str, Any],
    output_dir: str | Path,
    *,
    api_spec: str | dict[str, Any] | None = None,
    project_id: str = "real_project_demo",
    max_items: int = 24,
) -> dict[str, Any]:
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    plan = build_deep_probe_plan(result, api_spec=api_spec, project_id=project_id, max_items=max_items)
    json_path = output / "deep_probe_plan.json"
    md_path = output / "deep_probe_plan.md"
    json_path.write_text(json.dumps(plan, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    md_path.write_text(_markdown(plan), encoding="utf-8")
    return {
        "json": str(json_path),
        "markdown": str(md_path),
        "planned_actions": int((plan.get("summary") or {}).get("planned_actions", 0) or 0),
        "by_kind": (plan.get("summary") or {}).get("by_kind", {}),
        "by_execution_policy": (plan.get("summary") or {}).get("by_execution_policy", {}),
    }
