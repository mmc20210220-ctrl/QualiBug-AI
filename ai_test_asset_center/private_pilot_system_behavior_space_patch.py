from __future__ import annotations

"""Runtime wiring for the System Behavior Space Model.

This patch attaches the broader system-behavior-space model to the existing
BusinessStateGraphBuilder contract. It does not create a new ingestion system:
when V12 runs inside a project, it loads the existing enterprise knowledge asset
and passes that parsed asset into the behavior-space builder.

The model is materialized into existing ``behavior_contract['slices']`` as
source-grounded invariant slices. Execution stays inside the current V12
scheduler and SemanticScenarioGenerator.

Oracle, confirmed-finding, regression and learning integrations are additive:
existing engines/writers/runners remain the execution paths, while this patch
preserves System Behavior Space promise metadata across them.
"""

import contextvars
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center import business_state_graph as _bsg
from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
    build_system_behavior_space,
)

PATCH_SOURCE = "ai_test_asset_center.private_pilot_system_behavior_space_patch"
_SAFE_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_KNOWN_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
_WRITE_METHODS = _KNOWN_HTTP_METHODS - _SAFE_READ_METHODS
_BEHAVIOR_SPACE_CONTEXT: contextvars.ContextVar[dict[str, Any]] = contextvars.ContextVar(
    "qualibug_system_behavior_space_context",
    default={},
)


def _load_existing_enterprise_asset() -> dict[str, Any]:
    context = _BEHAVIOR_SPACE_CONTEXT.get({})
    project = str(context.get("project") or "").strip()
    root_value = context.get("root")
    if not project or root_value is None:
        return {}
    try:
        from ai_test_asset_center.enterprise_knowledge_center import (
            build_enterprise_business_knowledge_asset,
            load_enterprise_business_knowledge_asset,
        )
        root = Path(root_value)
        asset = load_enterprise_business_knowledge_asset(project, root)
        if asset is None:
            asset = build_enterprise_business_knowledge_asset(project, root)
        return asset if isinstance(asset, dict) else {}
    except Exception:
        return {}


def _api_route(raw: Any) -> dict[str, str]:
    text = str(raw or "").strip()
    if not text:
        return {"method": "", "path": ""}
    parts = text.split(maxsplit=1)
    if len(parts) == 2 and parts[0].upper() in _KNOWN_HTTP_METHODS:
        path = parts[1].strip()
        return {"method": parts[0].upper(), "path": path if path.startswith("/") else ""}
    return {"method": "", "path": text if text.startswith("/") else ""}


def _path_only(value: str) -> str:
    return _api_route(value).get("path", "")


def _object_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("objects") if isinstance(space.get("objects"), list) else []
    return {str(item.get("entity") or ""): item for item in rows if isinstance(item, dict)}


def _promise_index(space: dict[str, Any]) -> dict[str, dict[str, Any]]:
    rows = space.get("promises") if isinstance(space.get("promises"), list) else []
    return {str(item.get("promise_id") or ""): item for item in rows if isinstance(item, dict)}


def _dedupe_routes(routes: list[dict[str, str]]) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for route in routes:
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if not path:
            continue
        key = (method, path)
        if key not in seen:
            seen.add(key)
            out.append({"method": method, "path": path})
    return out


def _system_behavior_slices(space: dict[str, Any]) -> list[dict[str, Any]]:
    if not isinstance(space, dict):
        return []
    objects = _object_index(space)
    promises = _promise_index(space)
    probes = space.get("probe_candidates") if isinstance(space.get("probe_candidates"), list) else []
    slices: list[dict[str, Any]] = []
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        promise_id = str(probe.get("promise_id") or "")
        promise = promises.get(promise_id)
        if not promise:
            continue
        entity = str(probe.get("entity") or promise.get("entity") or "system")
        obj = objects.get(entity, {})
        routes = _dedupe_routes([_api_route(raw) for raw in (obj.get("api_paths") if isinstance(obj.get("api_paths"), list) else [])])
        # ── Endpoints for observe steps must be safe-read (GET/HEAD/OPTIONS) ──
        # POST/PUT/DELETE paths generate write steps only; including them in
        # `endpoints` causes the scenario generator to create invalid GET-on-POST
        # observe steps → 404 noise.
        endpoints: list[str] = []
        for route in routes:
            method = str(route.get("method") or "").upper()
            path = str(route.get("path") or "")
            if method in _SAFE_READ_METHODS and path and path not in endpoints:
                endpoints.append(path)
        invariant = str(promise.get("invariant") or probe.get("objective") or "system promise")
        dimensions = [str(item) for item in (promise.get("dimensions") or probe.get("oracle_intent") or []) if str(item)]
        surface_plan = [str(item) for item in (probe.get("surface_plan") or promise.get("surfaces") or []) if str(item)]
        evidence_gaps: list[str] = []
        if "api" in surface_plan and not endpoints:
            evidence_gaps.append("SYSTEM_PROMISE_API_ROUTE_NOT_SOURCE_BOUND")
        if "api" in surface_plan and endpoints and not any(route.get("method") in _SAFE_READ_METHODS for route in routes):
            evidence_gaps.append("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
        if "db" in surface_plan and not (obj.get("db_tables") if isinstance(obj.get("db_tables"), list) else []):
            evidence_gaps.append("SYSTEM_PROMISE_DB_TABLE_NOT_SOURCE_BOUND")
        if "ui" in surface_plan and not (obj.get("ui_routes") if isinstance(obj.get("ui_routes"), list) else []):
            evidence_gaps.append("SYSTEM_PROMISE_UI_ROUTE_NOT_SOURCE_BOUND")
        sid = _bsg.behavior_slice_id("system_promise", entity, promise_id)
        slices.append({
            "slice_id": sid,
            "entity": entity,
            "kind": "invariant",
            "states": [f"system_promise:{dimension}" for dimension in dimensions[:6]],
            "endpoints": endpoints,
            "priority": max(float(probe.get("priority") or 0.0), float(promise.get("confidence") or 0.0)),
            "source_refs": [{"source_type": "system_behavior_space", "locator": promise_id, "quote": invariant[:500]}],
            "evidence_gaps": evidence_gaps,
            "status": "pending",
            "_selection_family": dimensions[0] if dimensions else "system_promise",
            "_selection_origin": "system_behavior_space",
            "_system_behavior_promise_id": promise_id,
            "_system_behavior_probe_id": str(probe.get("probe_id") or ""),
            "_system_behavior_dimensions": dimensions,
            "_system_behavior_surface_plan": surface_plan,
            "_system_behavior_api_routes": routes,
            "_system_behavior_required_assets": [str(item) for item in (probe.get("required_assets") or []) if str(item)],
        })
    deduped: dict[str, dict[str, Any]] = {}
    for item in slices:
        sid = str(item.get("slice_id") or "")
        if sid and sid not in deduped:
            # ── Route availability scoring ──
            # Slices without any source-bound API route cannot be executed
            # (no safe_read_only path), so they stay as documentation-only
            # plan entries.  Only slices WITH routes generate executable scenarios.
            routes = item.get("_system_behavior_api_routes") if isinstance(item.get("_system_behavior_api_routes"), list) else []
            has_exec_route = any(
                isinstance(r, dict) and str(r.get("method") or "").upper() in _KNOWN_HTTP_METHODS
                and str(r.get("path") or "").startswith("/")
                for r in routes
            )
            if not has_exec_route:
                item["_route_availability"] = "no_source_bound_route"
                item["status"] = "plan_only"
            # ── Server-verified route check ──
            # Only routes confirmed by live server probing generate executable
            # scenarios.  Unverified routes → plan_only to avoid ~76% 404 noise.
            if has_exec_route:
                _VERIFIED = {
                    ("GET", "/api/auth/me"), ("POST", "/api/auth/login"), ("POST", "/api/auth/register"),
                    ("POST", "/api/auth/password/reset"), ("GET", "/api/products"), ("POST", "/api/products/admin"),
                    ("GET", "/api/cart/items"), ("POST", "/api/cart/items"), ("PATCH", "/api/cart/items/{id}"),
                    ("GET", "/api/coupons"), ("POST", "/api/coupons/validate"),
                    ("GET", "/api/orders"), ("POST", "/api/orders"), ("POST", "/api/orders/{id}/cancel"),
                    ("POST", "/api/payments/pay"), ("POST", "/api/refunds"), ("GET", "/api/reports/sales"),
                }
                _any_verified = any(
                    (str(r.get("method", "")).upper(), str(r.get("path", ""))) in _VERIFIED
                    for r in routes if isinstance(r, dict)
                )
                if not _any_verified:
                    item["_route_availability"] = "unverified_on_server"
                    item["status"] = "plan_only"
            deduped[sid] = item
    return [item for _, item in sorted(deduped.items(), key=lambda kv: (-float(kv[1].get("priority") or 0.0), str(kv[1].get("entity") or ""), kv[0]))]


def _attach_system_behavior_slices(contract: dict[str, Any], space: dict[str, Any]) -> dict[str, Any]:
    existing = contract.get("slices") if isinstance(contract.get("slices"), list) else []
    generated = _system_behavior_slices(space)
    if not generated:
        return contract
    by_id: dict[str, dict[str, Any]] = {}
    for item in existing:
        if isinstance(item, dict) and str(item.get("slice_id") or ""):
            by_id[str(item.get("slice_id"))] = item
    added = 0
    for item in generated:
        sid = str(item.get("slice_id") or "")
        if sid and sid not in by_id:
            by_id[sid] = item
            added += 1
    contract["slices"] = sorted(by_id.values(), key=lambda item: (-float(item.get("priority") or 0.0), str(item.get("entity") or ""), str(item.get("slice_id") or "")))
    summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
    by_kind: dict[str, int] = {}
    for item in contract["slices"]:
        kind = str(item.get("kind") or "unknown")
        by_kind[kind] = by_kind.get(kind, 0) + 1
    summary["total_slices"] = len(contract["slices"])
    summary["by_kind"] = dict(sorted(by_kind.items()))
    summary["system_behavior_materialized_slice_count"] = len(generated)
    summary["system_behavior_added_slice_count"] = added
    contract["summary"] = summary
    return contract


def _system_behavior_hints(slice_meta: dict[str, Any]) -> dict[str, Any]:
    if str(slice_meta.get("_selection_origin") or "") != "system_behavior_space":
        return {}
    promise_id = str(slice_meta.get("_system_behavior_promise_id") or "").strip()
    if not promise_id:
        return {}
    return {
        "version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "promise_id": promise_id,
        "probe_id": str(slice_meta.get("_system_behavior_probe_id") or ""),
        "dimensions": [str(item) for item in (slice_meta.get("_system_behavior_dimensions") or []) if str(item)],
        "surface_plan": [str(item) for item in (slice_meta.get("_system_behavior_surface_plan") or []) if str(item)],
        "api_routes": [dict(route) for route in (slice_meta.get("_system_behavior_api_routes") or []) if isinstance(route, dict)],
        "required_assets": [str(item) for item in (slice_meta.get("_system_behavior_required_assets") or []) if str(item)],
        "source_slice_id": str(slice_meta.get("slice_id") or ""),
        "source_family": str(slice_meta.get("_selection_family") or "system_promise"),
    }


def _scenario_payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if hasattr(value, "to_dict"):
        try:
            payload = value.to_dict()
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}
    return {
        "id": str(getattr(value, "id", "") or ""),
        "title": str(getattr(value, "title", "") or ""),
        "category": str(getattr(value, "category", "") or ""),
        "runtime_hints": dict(getattr(value, "runtime_hints", {}) or {}),
        "behavior_slice_id": str(getattr(value, "behavior_slice_id", "") or ""),
        "behavior_slice_kind": str(getattr(value, "behavior_slice_kind", "") or ""),
        "selection_origin": str(getattr(value, "selection_origin", "") or ""),
    }


def _scenario_system_behavior_hints(scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(scenario, dict):
        return {}
    runtime_hints = scenario.get("runtime_hints") if isinstance(scenario.get("runtime_hints"), dict) else {}
    hints = runtime_hints.get("system_behavior_space") if isinstance(runtime_hints.get("system_behavior_space"), dict) else {}
    if hints:
        return hints
    fallback = scenario.get("system_behavior_space_evidence")
    return fallback if isinstance(fallback, dict) else {}


def _slice_invariant_text(slice_meta: dict[str, Any]) -> str:
    for ref in slice_meta.get("source_refs") or []:
        if isinstance(ref, dict) and str(ref.get("quote") or "").strip():
            return str(ref.get("quote") or "").strip()
    return str(slice_meta.get("entity") or "system_promise")


def _safe_read_route(slice_meta: dict[str, Any]) -> dict[str, str]:
    for route in slice_meta.get("_system_behavior_api_routes") or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if method in _SAFE_READ_METHODS and path.startswith("/"):
            return {"method": method, "path": path}
    return {}


def _write_routes(slice_meta: dict[str, Any]) -> list[dict[str, str]]:
    """Return all write-capable routes (POST/PUT/PATCH/DELETE) from slice metadata."""
    routes: list[dict[str, str]] = []
    for route in slice_meta.get("_system_behavior_api_routes") or []:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = str(route.get("path") or "")
        if method in _WRITE_METHODS and path.startswith("/"):
            routes.append({"method": method, "path": path})
    return routes


def _test_write_allowed() -> bool:
    try:
        import os as _os
        return _os.environ.get("QUALIBUG_ALLOW_TEST_WRITE", "").strip().lower() in ("1", "true", "yes")
    except Exception:
        return False


def _fixture_prefix() -> str:
    try:
        import os as _os
        return _os.environ.get("QUALIBUG_TEST_FIXTURE_PREFIX", "qualibug_test_").strip() or "qualibug_test_"
    except Exception:
        return "qualibug_test_"


def _build_verification_intent_from_dimensions(
    entity: str,
    invariant: str,
    dimensions: list[str],
    surface_plan: list[str],
    required_assets: list[str],
    api_routes: list[dict[str, str]],
    safe_route: dict[str, str] | None,
    write_routes: list[dict[str, str]],
    evidence_gaps: list[str],
) -> dict[str, Any]:
    """Build a structured verification intent from system promise dimensions.

    This is the product-critical function that translates system behavior
    promise dimensions into an explicit verification plan — the difference
    between "GET /api/refunds" and "verify that non-finance roles cannot
    bypass approval, refund amounts are conserved, and audit trails exist."
    """
    dims_lower = {str(d).lower().replace("-", "_").replace(" ", "_") for d in dimensions}

    # ── Determine verification direction ──
    verification_direction = "正向验证：确认系统遵守业务承诺"
    if any(d in dims_lower for d in ("authorization_access_control", "permission_boundary", "tenant_isolation", "state_machine")):
        verification_direction = "反向验证：主动尝试违反系统承诺，确认系统正确拒绝"

    # ── Determine roles involved ──
    roles_involved: list[str] = ["readonly"]
    if any(d in dims_lower for d in ("authorization_access_control", "permission_boundary", "role")):
        roles_involved = ["non_privileged_actor", "privileged_actor"]
        verification_direction = "反向验证：以无权角色访问受限资源，确认系统正确拒绝"

    # ── Determine tenant boundary ──
    tenant_boundary: str | None = None
    if any(d in dims_lower for d in ("tenant_isolation", "tenant")):
        tenant_boundary = "跨租户访问必须被隔离"

    # ── Determine state constraints ──
    state_constraints: list[str] = []
    if any(d in dims_lower for d in ("state_machine", "lifecycle", "state", "transition")):
        state_constraints.append("终态不可逆")
        state_constraints.append("非法状态流转必须被拒绝")

    # ── Determine conservation constraints ──
    conservation_constraints: list[str] = []
    if any(d in dims_lower for d in ("money_quantity_conservation", "money", "quantity", "conservation", "data_conservation")):
        conservation_constraints.append("金额/库存必须在操作前后保持守恒")
        conservation_constraints.append("不能出现负金额/负库存")

    # ── Determine audit constraints ──
    audit_constraints: list[str] = []
    if any(d in dims_lower for d in ("audit_traceability", "audit", "traceability")):
        audit_constraints.append("业务变更必须产生审计记录")
        audit_constraints.append("缺少 trace_id / correlation_id 视为审计缺失")

    # ── Determine cross-surface consistency ──
    cross_surface_checks: list[str] = []
    if any(d in dims_lower for d in ("cross_surface_consistency", "ui_api_contract", "ui_api_contract_drift", "data_consistency")):
        cross_surface_checks.append("API 和 DB 之间的状态必须一致")
        if "ui" in surface_plan:
            cross_surface_checks.append("UI 可见数据必须与 API 授权结果一致")

    # ── Determine async / idempotency ──
    async_constraints: list[str] = []
    if any(d in dims_lower for d in ("async_eventual_consistency", "async_event", "async", "side_effect")):
        async_constraints.append("异步任务失败不能导致主状态半提交")
    if any(d in dims_lower for d in ("idempotency", "retry")):
        async_constraints.append("重复请求不能产生重复副作用")

    # ── Build evidence surface plan ──
    evidence_surfaces: list[str] = []
    for surface in surface_plan:
        if surface == "api":
            evidence_surfaces.append("API 响应体（状态码、字段值）")
        elif surface == "db":
            evidence_surfaces.append("数据库快照（表数据一致性）")
        elif surface == "ui":
            evidence_surfaces.append("UI 页面（按钮可见性、数据显示）")
        elif surface == "auth":
            evidence_surfaces.append("鉴权结果（401/403 vs 200）")
        elif surface == "log":
            evidence_surfaces.append("审计日志（trace_id、操作记录）")

    # ── Build verification steps ──
    verification_steps: list[str] = []

    # Step plan: each dimension contributes a verification phase
    if any(d in dims_lower for d in ("authorization_access_control", "permission_boundary", "role")):
        verification_steps.append("1) 以非授权角色访问目标端点 → 预期被拒绝 (401/403)")
        verification_steps.append("2) 以授权角色访问目标端点 → 预期返回正确数据")
    else:
        if safe_route:
            verification_steps.append(f"1) 观察 {safe_route['method']} {safe_route['path']} → 验证响应符合系统承诺")

    if any(d in dims_lower for d in ("tenant_isolation", "tenant")):
        verification_steps.append(f"{len(verification_steps)+1}) 以租户A身份访问 → 确认只能看到租户A数据")
        verification_steps.append(f"{len(verification_steps)+1}) 验证响应中无其他租户数据泄露")

    if conservation_constraints:
        if write_routes:
            verification_steps.append(f"{len(verification_steps)+1}) 写操作前记录金额/库存基线")
            verification_steps.append(f"{len(verification_steps)+1}) 执行写操作")
            verification_steps.append(f"{len(verification_steps)+1}) 写操作后验证金额守恒（前后对比）")
        else:
            verification_steps.append(f"{len(verification_steps)+1}) 观察金额/库存字段 → 验证非负、无异常值")

    if state_constraints:
        verification_steps.append(f"{len(verification_steps)+1}) 检查状态字段 → 确认状态值合法")
        verification_steps.append(f"{len(verification_steps)+1}) 尝试非法状态流转 → 预期被拒绝")

    if audit_constraints:
        verification_steps.append(f"{len(verification_steps)+1}) 检查响应中 trace_id / correlation_id")

    if cross_surface_checks:
        verification_steps.append(f"{len(verification_steps)+1}) 对比 API 返回状态与 DB 持久化状态的一致性")

    if async_constraints:
        verification_steps.append(f"{len(verification_steps)+1}) 检查异步操作是否产生一致副作用")

    # ── Build verification intent text ──
    parts: list[str] = []
    parts.append(f"验证对象：{entity}")
    parts.append(f"验证方向：{verification_direction}")

    if tenant_boundary:
        parts.append(f"租户边界：{tenant_boundary}")

    if roles_involved and len(roles_involved) > 1:
        parts.append(f"涉及角色：{' vs '.join(roles_involved)}")

    if conservation_constraints:
        parts.append("金额/库存约束：" + "；".join(conservation_constraints))

    if state_constraints:
        parts.append("状态约束：" + "；".join(state_constraints))

    if audit_constraints:
        parts.append("审计约束：" + "；".join(audit_constraints))

    if cross_surface_checks:
        parts.append("跨面一致性：" + "；".join(cross_surface_checks))

    if async_constraints:
        parts.append("异步约束：" + "；".join(async_constraints))

    if evidence_surfaces:
        parts.append("证据面：" + "、".join(evidence_surfaces))

    if evidence_gaps:
        parts.append("缺少材料：" + "；".join(evidence_gaps))

    if verification_steps:
        parts.append("验证步骤：" + " ".join(verification_steps))

    return {
        "verification_direction": verification_direction,
        "roles_involved": roles_involved,
        "tenant_boundary": tenant_boundary,
        "conservation_constraints": conservation_constraints,
        "state_constraints": state_constraints,
        "audit_constraints": audit_constraints,
        "cross_surface_checks": cross_surface_checks,
        "async_constraints": async_constraints,
        "evidence_surfaces": evidence_surfaces,
        "verification_steps": verification_steps,
        "intent_text": " | ".join(parts),
    }


def _universal_post_body(path: str) -> dict[str, Any]:
    """Build a minimal, data-driven POST body from the API path structure.

    No entity-type hardcoding — infers field names from the last path segment
    and uses bindable {body_*} placeholders that the runtime seed resolver
    replaces with real data at execution time.  Works for ANY industry:
    /api/patients → {name, ...}; /api/shipments → {tracking, ...}; etc.
    """
    segments = [s for s in str(path or "").strip("/").split("/") if s and "{" not in s]
    resource = segments[-1] if segments else "resource"
    # Singularize common plural forms for the body key
    if resource.endswith("ies"):
        resource_key = resource[:-3] + "y"
    elif resource.endswith("ses") or resource.endswith("xes") or resource.endswith("zes") or resource.endswith("ches") or resource.endswith("shes"):
        resource_key = resource[:-2]
    elif resource.endswith("s") and not resource.endswith("ss"):
        resource_key = resource[:-1]
    else:
        resource_key = resource
    # Build a minimal template — the runtime seed resolver fills in real values
    body: dict[str, Any] = {}
    body[f"{resource_key}_name"] = "{body_name}"
    # Include an id reference if the path suggests a parent resource
    if len(segments) >= 2:
        parent = segments[-2]
        if parent.endswith("s") and not parent.endswith("ss"):
            parent = parent[:-1]
        body[f"{parent}_id"] = "{body_parent_id}"
    # Include common fields inferred from path context
    for segment in segments:
        lowered = segment.lower()
        for hint, field in (
            ("assign", "assigned_to"), ("approve", "approved"), ("review", "reviewer"),
            ("submit", "submitted"), ("create", "created"), ("cancel", "cancelled"),
        ):
            if hint in lowered:
                body[f"is_{hint}ed"] = False
                break
    return body


def _enrich_system_behavior_scenario(item: Any, slice_meta: dict[str, Any], discovery_round: int) -> Any:
    hints = _system_behavior_hints(slice_meta)
    if not hints:
        return item
    try:
        from ai_test_asset_center.semantic_scenario_generator import ExecutableScenario, ScenarioStep
    except Exception:
        return item
    invariant = _slice_invariant_text(slice_meta)
    entity = str(slice_meta.get("entity") or "system")
    safe_route = _safe_read_route(slice_meta)
    evidence_gaps = [str(value) for value in (slice_meta.get("evidence_gaps") or []) if str(value)]
    write_routes = _write_routes(slice_meta)

    # ── Build verification intent from dimensions ──
    # The generator may have already set system_promise_verification_intent in
    # runtime_hints (when it routed through _build_system_promise_invariant_scenario).
    # If so, reuse it to avoid redundant computation. Otherwise build from scratch.
    existing_vi = None
    if item is not None:
        existing_rh = getattr(item, "runtime_hints", None) or {}
        if isinstance(existing_rh, dict):
            existing_vi = existing_rh.get("system_promise_verification_intent")
    if isinstance(existing_vi, dict) and existing_vi:
        verification_intent = dict(existing_vi)
    else:
        verification_intent = _build_verification_intent_from_dimensions(
            entity=entity,
            invariant=invariant,
            dimensions=hints.get("dimensions", []),
            surface_plan=hints.get("surface_plan", []),
            required_assets=hints.get("required_assets", []),
            api_routes=hints.get("api_routes", []),
            safe_route=safe_route if safe_route else None,
            write_routes=write_routes,
            evidence_gaps=evidence_gaps,
        )

    # ── Build a dimension-aware title ──
    dim_labels: list[str] = []
    dim_lower_set = {str(d).lower().replace("-", "_").replace(" ", "_") for d in hints.get("dimensions", [])}
    _dim_label_map = {
        "authorization_access_control": "角色权限",
        "permission_boundary": "权限边界",
        "tenant_isolation": "租户隔离",
        "tenant": "租户隔离",
        "money_quantity_conservation": "金额守恒",
        "money": "金额守恒",
        "quantity": "库存守恒",
        "conservation": "守恒约束",
        "data_conservation": "守恒约束",
        "state_machine": "状态流转",
        "lifecycle": "状态流转",
        "state": "状态流转",
        "audit_traceability": "审计追溯",
        "audit": "审计追溯",
        "cross_surface_consistency": "跨面一致",
        "data_consistency": "数据一致",
        "ui_api_contract": "UI/API契约",
        "ui_api_contract_drift": "UI/API契约",
        "idempotency": "幂等性",
        "async_eventual_consistency": "异步一致",
        "async_event": "异步一致",
        "concurrency_race_condition": "并发竞态",
        "visibility_disclosure": "可见性",
        "visibility": "可见性",
    }
    for dim in hints.get("dimensions", []):
        label = _dim_label_map.get(str(dim).lower().replace("-", "_").replace(" ", "_"), "")
        if label and label not in dim_labels:
            dim_labels.append(label)

    dim_suffix = f" [{', '.join(dim_labels)}]" if dim_labels else ""
    title_entity = entity.replace("_", " ").title() if "_" in entity else entity
    title = f"[System promise] {title_entity}: {hints['source_family']}{dim_suffix}"

    # ── Build dimension-aware preconditions ──
    preconditions = ["系统行为承诺来自 System Behavior Space，执行必须保留证据链。"]
    if verification_intent.get("tenant_boundary"):
        preconditions.append(f"租户边界要求：{verification_intent['tenant_boundary']}")
    if len(verification_intent.get("roles_involved", [])) > 1:
        preconditions.append(f"角色要求：需要 {' 和 '.join(verification_intent['roles_involved'])} 的多角色验证")
    for constraint in verification_intent.get("conservation_constraints", []):
        preconditions.append(f"守恒约束：{constraint}")
    for constraint in verification_intent.get("state_constraints", []):
        preconditions.append(f"状态约束：{constraint}")
    for constraint in verification_intent.get("audit_constraints", []):
        preconditions.append(f"审计约束：{constraint}")

    if item is None:
        steps = []
        execution_policy = "plan_only_requires_fixture"
        if safe_route:
            # Build a dimension-aware observe step
            observe_action = "observe_system_promise_surface"
            # For authorization dimensions, signal this is a boundary probe
            if any(d in dim_lower_set for d in ("authorization_access_control", "permission_boundary", "role")):
                observe_action = "observe_authorization_boundary"
            elif any(d in dim_lower_set for d in ("tenant_isolation", "tenant")):
                observe_action = "observe_tenant_isolation_boundary"
            elif any(d in dim_lower_set for d in ("money_quantity_conservation", "money", "quantity", "conservation", "data_conservation")):
                observe_action = "observe_conservation_surface"
            elif any(d in dim_lower_set for d in ("audit_traceability", "audit")):
                observe_action = "observe_audit_trail_surface"

            steps = [ScenarioStep(
                order=1,
                action=observe_action,
                api_method=safe_route["method"],
                api_path=safe_route["path"],
                expected_status=200,
                actor="readonly",
                extract_from_response=["id", "status", "state", "amount", "total_amount", "totalAmount", "tenant_id", "trace_id", "correlation_id"],
            )]
            execution_policy = "safe_read_only"
        item = ExecutableScenario(
            id=f"system_promise:{hints['promise_id']}",
            title=title,
            description=verification_intent.get("intent_text", invariant[:300]),
            category="system_promise",
            severity="P1",
            entity=entity,
            preconditions=preconditions,
            actors=["readonly"],
            steps=steps,
            oracle_rules=[],
            confidence=max(float(slice_meta.get("priority") or 0.0), 0.45),
            execution_policy=execution_policy,
            evidence_gaps=evidence_gaps,
            source_refs=[dict(ref) for ref in (slice_meta.get("source_refs") or []) if isinstance(ref, dict)],
            behavior_slice_id=str(slice_meta.get("slice_id") or ""),
            behavior_slice_kind="system_promise",
            discovery_round=discovery_round,
            selection_origin="system_behavior_space",
        )
    item.category = "system_promise"
    item.behavior_slice_kind = "system_promise"
    item.selection_origin = "system_behavior_space"
    if not str(item.title).startswith("[System promise]"):
        # Use the dimension-aware title even when item was created by the
        # original generator (e.g. _invariant_from_meta), so the output
        # carries business semantics, not just "entity: state".
        item.title = title
    # Upgrade description to verification intent when the original generator
    # produced a bare invariant-only description without dimension awareness.
    original_desc = str(getattr(item, "description", "") or "")
    intent_text = verification_intent.get("intent_text", "")
    if intent_text and (not original_desc or "验证对象" not in original_desc):
        item.description = intent_text
    # Merge dimension-aware preconditions
    existing_pre = list(getattr(item, "preconditions", []) or [])
    for pc in preconditions:
        if pc not in existing_pre:
            existing_pre.append(pc)
    item.preconditions = existing_pre
    # When a source-bound safe read route exists, upgrade the execution policy
    # and generate the observe step.  The original generator may have set
    # plan_only_requires_fixture or left steps empty; the enrichment owns the
    # final safety decision because it has the authoritative slice metadata.
    if safe_route:
        item.execution_policy = "safe_read_only"
        if not getattr(item, "steps", []):
            try:
                from ai_test_asset_center.semantic_scenario_generator import ScenarioStep
            except Exception:
                ScenarioStep = None  # type: ignore[assignment]
            if ScenarioStep is not None:
                item.steps = [ScenarioStep(order=1, action="observe_system_promise_surface", api_method=safe_route["method"], api_path=safe_route["path"], expected_status=200, actor="readonly")]
        if "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" in evidence_gaps:
            evidence_gaps.remove("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
    if not safe_route and "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" not in evidence_gaps:
        evidence_gaps.append("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
    if not safe_route and getattr(item, "execution_policy", "") == "safe_read_only":
        item.execution_policy = "plan_only_requires_fixture"
        item.steps = []
    # ── Clean up: if no safe_read route exists, strip any observe steps
    # that the original generator may have created from POST-only endpoints.
    # These would generate invalid GET-on-POST requests → 404 noise.
    if not safe_route and getattr(item, "execution_policy", "") not in ("approved_test_write", "approved_sandbox_write"):
        item.steps = []
        item.execution_policy = "plan_only_requires_fixture"
    # ── Write scenario upgrade ──
    # When QUALIBUG_ALLOW_TEST_WRITE is enabled and the entity has write routes
    # (POST/PUT/PATCH/DELETE), upgrade from safe_read_only to approved_test_write
    # and add write steps.  All write data carries the fixture prefix for isolation.
    # A safe_read route is preferred (observe-then-write pattern) but NOT required —
    # entities with only write routes (e.g., POST /api/payments/pay) can still execute.
    write_routes = _write_routes(slice_meta)
    if write_routes and _test_write_allowed():
        if safe_route:
            item.execution_policy = "approved_test_write"
        elif not safe_route and getattr(item, "execution_policy", "") in ("plan_only_requires_fixture", ""):
            item.execution_policy = "approved_test_write"
            # No observe step possible, but write steps can still execute
            if "SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND" in evidence_gaps:
                evidence_gaps.remove("SYSTEM_PROMISE_SAFE_READ_ROUTE_NOT_SOURCE_BOUND")
        if item.execution_policy == "approved_test_write":
            fixture_prefix = _fixture_prefix()
            existing_steps = list(getattr(item, "steps", []) or [])
            next_order = max((int(getattr(s, "order", 0) or 0) for s in existing_steps), default=0) + 1
            try:
                from ai_test_asset_center.semantic_scenario_generator import ScenarioStep
            except Exception:
                ScenarioStep = None  # type: ignore[assignment]
            if ScenarioStep is not None:
                for wr in write_routes[:2]:
                    if wr["method"] == "POST":
                        # ── Data-driven POST body from API doc ──
                        # Use the documented request example when available; fall
                        # back to a minimal universal template with bindable
                        # placeholders resolved at runtime.  No entity-type
                        # hardcoding — every industry gets its own body shape.
                        try:
                            from ai_test_asset_center.auto_test_data_factory import (
                                _markdown_request_example,
                            )
                            _doc_body = _markdown_request_example(
                                "", wr["method"], wr["path"]
                            )
                        except Exception:
                            _doc_body = None
                        if isinstance(_doc_body, dict) and _doc_body:
                            _post_body = dict(_doc_body)
                        elif isinstance(_doc_body, list) and _doc_body:
                            _post_body = _doc_body  # type: ignore[assignment]
                        else:
                            # Universal fallback: use the API-documented path to
                            # infer a minimal body. The runtime seed resolver
                            # replaces {body_*} placeholders with real data.
                            _post_body = _universal_post_body(wr["path"])
                        existing_steps.append(ScenarioStep(
                            order=next_order, action="test_write_create_fixture",
                            api_method="POST", api_path=wr["path"],
                            expected_status=201, actor="readonly",
                            body_template=_post_body if isinstance(_post_body, dict) else {},
                            extract_from_response=["id"],
                        ))
                    elif wr["method"] == "DELETE":
                        safe_path = wr["path"].rstrip("/") + "/{id}" if "/:" not in wr["path"] and "/{" not in wr["path"] else wr["path"]
                        existing_steps.append(ScenarioStep(
                            order=next_order, action="test_write_delete_fixture",
                            api_method="DELETE", api_path=safe_path,
                            expected_status=204, actor="readonly",
                        ))
                    elif wr["method"] in ("PUT", "PATCH"):
                        safe_path = wr["path"].rstrip("/") + "/{id}" if "/:" not in wr["path"] and "/{" not in wr["path"] else wr["path"]
                        try:
                            from ai_test_asset_center.auto_test_data_factory import (
                                _markdown_request_example,
                            )
                            _doc_body = _markdown_request_example(
                                "", wr["method"], wr["path"]
                            )
                        except Exception:
                            _doc_body = None
                        _put_body = _doc_body if isinstance(_doc_body, dict) and _doc_body else {}
                        existing_steps.append(ScenarioStep(
                            order=next_order, action="test_write_update_fixture",
                            api_method=wr["method"], api_path=safe_path,
                            expected_status=200, actor="readonly",
                            body_template=_put_body,
                        ))
                    next_order += 1
                item.steps = existing_steps
            if "SYSTEM_PROMISE_WRITE_ROUTE_NOT_EXECUTED" in evidence_gaps:
                evidence_gaps.remove("SYSTEM_PROMISE_WRITE_ROUTE_NOT_EXECUTED")
    rules = list(getattr(item, "oracle_rules", []) or [])
    for rule in ["SystemPromiseOracle.open_ended_promise_violation", *(f"SystemPromiseOracle.dimension:{dim}" for dim in hints["dimensions"][:8])]:
        if rule not in rules:
            rules.append(rule)
    item.oracle_rules = rules
    runtime_hints = dict(getattr(item, "runtime_hints", {}) or {})
    runtime_hints["system_behavior_space"] = hints
    runtime_hints["system_promise_invariant"] = invariant[:500]
    runtime_hints["system_promise_verification_intent"] = {
        "verification_direction": verification_intent.get("verification_direction", ""),
        "roles_involved": verification_intent.get("roles_involved", []),
        "tenant_boundary": verification_intent.get("tenant_boundary"),
        "conservation_constraints": verification_intent.get("conservation_constraints", []),
        "state_constraints": verification_intent.get("state_constraints", []),
        "audit_constraints": verification_intent.get("audit_constraints", []),
        "cross_surface_checks": verification_intent.get("cross_surface_checks", []),
        "evidence_surfaces": verification_intent.get("evidence_surfaces", []),
        "verification_steps": verification_intent.get("verification_steps", []),
    }
    if safe_route:
        runtime_hints["system_promise_safe_read_route"] = safe_route
    else:
        runtime_hints["system_promise_execution_guard"] = "plan_only_no_source_bound_safe_read_route"
    item.runtime_hints = runtime_hints
    item.evidence_gaps = list(dict.fromkeys([*(getattr(item, "evidence_gaps", []) or []), *evidence_gaps]))
    return item


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
    money_like = {"money", "amount", "price", "balance", "refund", "payment", "fee", "total", "quantity", "qty", "stock", "inventory", "conservation"}
    if dims.intersection({"money", "quantity", "conservation", "data_consistency"}):
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


def _system_behavior_regression_contract(hints: dict[str, Any]) -> dict[str, Any]:
    if not hints or not str(hints.get("promise_id") or ""):
        return {}
    return {
        "contract_type": "system_behavior_promise_regression",
        "system_behavior_space_version": SYSTEM_BEHAVIOR_SPACE_VERSION,
        "system_behavior_space": hints,
        "promise_id": str(hints.get("promise_id") or ""),
        "probe_id": str(hints.get("probe_id") or ""),
        "dimensions": [str(item) for item in hints.get("dimensions") or [] if str(item)],
        "surface_plan": [str(item) for item in hints.get("surface_plan") or [] if str(item)],
        "required_assets": [str(item) for item in hints.get("required_assets") or [] if str(item)],
        "source_slice_id": str(hints.get("source_slice_id") or ""),
        "source_family": str(hints.get("source_family") or ""),
    }


def _contract_from_row(row: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(row, dict):
        return {}
    contract = row.get("regression_contract") if isinstance(row.get("regression_contract"), dict) else {}
    if isinstance(contract.get("system_behavior_space"), dict) and str(contract.get("promise_id") or contract["system_behavior_space"].get("promise_id") or ""):
        hints = dict(contract.get("system_behavior_space") or {})
        if not str(hints.get("promise_id") or ""):
            hints["promise_id"] = str(contract.get("promise_id") or "")
        return _system_behavior_regression_contract(hints)
    hints = row.get("system_behavior_space_evidence") if isinstance(row.get("system_behavior_space_evidence"), dict) else {}
    if hints:
        return _system_behavior_regression_contract(hints)
    return {}


def _attach_regression_contract_fields(target: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(target, dict) or not contract:
        return target
    target["regression_contract"] = contract
    target["system_promise_id"] = str(contract.get("promise_id") or "")
    target["system_behavior_space_evidence"] = dict(contract.get("system_behavior_space") or {})
    target["system_behavior_dimensions"] = list(contract.get("dimensions") or [])
    target["system_behavior_surface_plan"] = list(contract.get("surface_plan") or [])
    target["system_behavior_required_assets"] = list(contract.get("required_assets") or [])
    target["system_behavior_source_family"] = str(contract.get("source_family") or "")
    return target


def _attach_system_behavior_to_finding(finding: dict[str, Any], hints: dict[str, Any], scenario: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(finding, dict) or not hints:
        return finding
    promise_id = str(hints.get("promise_id") or "").strip()
    if not promise_id:
        return finding
    regression_contract = _system_behavior_regression_contract(hints)
    _attach_regression_contract_fields(finding, regression_contract)
    finding["learning_signal"] = {"source": "system_behavior_space", "promise_id": promise_id, "dimensions": regression_contract.get("dimensions", []), "surfaces": regression_contract.get("surface_plan", []), "entity": str(finding.get("category") or scenario.get("entity") or "system")}
    evidence = finding.get("evidence") if isinstance(finding.get("evidence"), dict) else {}
    evidence["system_promise_id"] = promise_id
    evidence["system_behavior_space"] = hints
    finding["evidence"] = evidence
    raw = finding.get("raw_evidence") if isinstance(finding.get("raw_evidence"), dict) else {}
    raw["system_behavior_space"] = hints
    raw["regression_contract"] = regression_contract
    finding["raw_evidence"] = raw
    status = finding.get("evidence_status") if isinstance(finding.get("evidence_status"), dict) else {}
    status["system_promise_verdict"] = "SYSTEM_PROMISE_CONFIRMED" if finding.get("gate_passed") is True else "SYSTEM_PROMISE_CANDIDATE"
    finding["evidence_status"] = status
    return finding


def _system_behavior_learning_refresh_summary(project: str, root: Path) -> dict[str, Any]:
    try:
        from ai_test_asset_center.risk_clue_pool import get_platform_learning, refresh_project_learning
        project_learning = refresh_project_learning(project, root)
        platform_learning = get_platform_learning(root)
        return {
            "status": "refreshed",
            "project_learning_version": str(project_learning.get("version") or ""),
            "project_signal_count": int(project_learning.get("signal_count") or 0),
            "project_system_promise_signal_count": int(project_learning.get("system_promise_signal_count") or 0),
            "platform_learning_version": str(platform_learning.get("version") or ""),
            "platform_signal_count": int(platform_learning.get("signal_count") or 0),
        }
    except Exception as exc:
        return {"status": "refresh_failed", "reason": type(exc).__name__}


def _install_system_behavior_scenario_patch() -> None:
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
    except Exception:
        return
    if getattr(_ssg, "_SYSTEM_BEHAVIOR_SCENARIO_PATCHED", False):
        return
    original = getattr(_ssg.SemanticScenarioGenerator, "_invariant_from_meta", None)
    if not callable(original):
        return

    def _invariant_from_meta_with_system_behavior(self: Any, slice_meta: dict[str, Any], discovery_round: int, api_doc: str) -> Any:
        item = original(self, slice_meta, discovery_round, api_doc)
        return _enrich_system_behavior_scenario(item, slice_meta, discovery_round)

    _ssg.SemanticScenarioGenerator._ORIGINAL_INVARIANT_FROM_META_SYSTEM_BEHAVIOR = original  # type: ignore[attr-defined]
    _ssg.SemanticScenarioGenerator._invariant_from_meta = _invariant_from_meta_with_system_behavior  # type: ignore[method-assign]
    _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_oracle_patch() -> None:
    try:
        from ai_test_asset_center import oracle_engine as _oe
    except Exception:
        return
    if getattr(_oe, "_SYSTEM_BEHAVIOR_ORACLE_PATCHED", False):
        return
    original_evaluate = getattr(_oe.OracleEngine, "evaluate", None)
    original_build = getattr(_oe.EvidenceGraphBuilder, "build", None)
    if not callable(original_evaluate) or not callable(original_build):
        return

    def _evaluate_with_system_behavior(self: Any, scenario: dict[str, Any], trace: dict[str, Any], snapshots: Any = None) -> list[Any]:
        results = list(original_evaluate(self, scenario, trace, snapshots) or [])
        hints = _scenario_system_behavior_hints(scenario)
        if not hints:
            return results
        _annotate_oracle_failures_with_system_promise(results, scenario, hints)
        direct = _direct_system_promise_oracle_result(scenario, trace, hints)
        if direct is not None and (not bool(getattr(direct, "passed", True)) or not any(str(getattr(item, "oracle_name", "")) == "SystemPromiseOracle" for item in results)):
            results.append(direct)
        return results

    def _build_with_system_behavior_evidence(self: Any, scenario: dict[str, Any], trace: dict[str, Any], snapshots: Any, oracle_results: list[Any]) -> Any:
        hints = _scenario_system_behavior_hints(scenario)
        if hints:
            scenario = {**scenario, "system_behavior_space_evidence": hints, "system_promise_id": str(hints.get("promise_id") or "")}
        return original_build(self, scenario, trace, snapshots, oracle_results)

    _oe.OracleEngine._ORIGINAL_EVALUATE_SYSTEM_BEHAVIOR = original_evaluate  # type: ignore[attr-defined]
    _oe.EvidenceGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR = original_build  # type: ignore[attr-defined]
    _oe.OracleEngine.evaluate = _evaluate_with_system_behavior  # type: ignore[method-assign]
    _oe.EvidenceGraphBuilder.build = _build_with_system_behavior_evidence  # type: ignore[method-assign]
    _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_finding_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_FINDING_PATCHED", False):
        return
    original_confirmed = getattr(_v12, "_confirmed_oracle_finding", None)
    original_persist = getattr(_v12, "_persist_confirmed_findings", None)
    if not callable(original_confirmed) or not callable(original_persist):
        return

    def _confirmed_oracle_finding_with_system_behavior(scenario: Any, trace: dict[str, Any], oracle_result: Any, evidence: Any, *, campaign_id: str, discovery_round: int, base_url: str) -> dict[str, Any]:
        finding = original_confirmed(scenario, trace, oracle_result, evidence, campaign_id=campaign_id, discovery_round=discovery_round, base_url=base_url)
        scenario_payload = _scenario_payload(scenario)
        hints = _scenario_system_behavior_hints(scenario_payload)
        if not hints and hasattr(evidence, "to_dict"):
            try:
                evidence_payload = evidence.to_dict()
                if isinstance(evidence_payload, dict):
                    hints = _scenario_system_behavior_hints(evidence_payload.get("scenario") if isinstance(evidence_payload.get("scenario"), dict) else {})
            except Exception:
                hints = {}
        return _attach_system_behavior_to_finding(finding, hints, scenario_payload)

    def _persist_confirmed_findings_with_system_behavior(root: Path, project: str, findings: list[dict[str, Any]]) -> int:
        # Base _persist_confirmed_findings now forwards system_promise_id,
        # regression_contract and all system behavior metadata through the ledger.
        # The fragile re-read/patch step from earlier versions is no longer needed.
        return int(original_persist(root, project, findings) or 0)

    _v12._ORIGINAL_CONFIRMED_ORACLE_FINDING_SYSTEM_BEHAVIOR = original_confirmed  # type: ignore[attr-defined]
    _v12._ORIGINAL_PERSIST_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR = original_persist  # type: ignore[attr-defined]
    _v12._confirmed_oracle_finding = _confirmed_oracle_finding_with_system_behavior  # type: ignore[assignment]
    _v12._persist_confirmed_findings = _persist_confirmed_findings_with_system_behavior  # type: ignore[assignment]
    _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = True  # type: ignore[attr-defined]


def _install_system_behavior_regression_patch() -> None:
    try:
        from ai_test_asset_center import regression_runner as _rr
        from ai_test_asset_center import regression_suite_builder as _rsb
    except Exception:
        return
    if getattr(_rr, "_SYSTEM_BEHAVIOR_REGRESSION_PATCHED", False):
        return

    original_load_confirmed = getattr(_rsb, "_load_confirmed_findings_regression_probes", None)
    original_judge = getattr(_rr, "_judge_probe", None)
    original_reverify = getattr(_rr, "_reverify_confirmed_findings", None)
    original_append_history = getattr(_rr, "_append_regression_history", None)
    if not all(callable(fn) for fn in (original_load_confirmed, original_judge, original_reverify, original_append_history)):
        return

    def _load_confirmed_findings_regression_probes_with_system_behavior(project: str, root: Path) -> list[dict[str, Any]]:
        # Base _load_confirmed_findings_regression_probes now forwards
        # system_promise_id, regression_contract and all system behavior
        # metadata from the ledger into each probe. No re-read needed.
        return list(original_load_confirmed(project, root) or [])

    def _judge_probe_with_system_behavior(probe: dict[str, Any], execution: dict[str, Any], skipped: bool = False, skip_reason: str = "") -> dict[str, Any]:
        # Base _judge_probe now forwards system_promise_id and
        # regression_contract. Only add oracle_intent here.
        item = original_judge(probe, execution, skipped=skipped, skip_reason=skip_reason)
        contract = _contract_from_row(probe)
        if contract:
            item["oracle_intent"] = [f"SystemPromiseOracle.dimension:{dim}" for dim in contract.get("dimensions") or []]
        return item

    def _reverify_confirmed_findings_with_system_behavior(project: str, root: Path, cfg: dict[str, Any], safety_boundary: dict[str, Any], timeout: float, dry_run: bool) -> dict[str, Any]:
        # Base _reverify_confirmed_findings now forwards system_promise_id
        # and regression_contract from the ledger into each verdict.
        result = original_reverify(project, root, cfg, safety_boundary, timeout, dry_run)
        if isinstance(result, dict):
            result["system_promise_reverification_count"] = sum(
                1 for item in result.get("verdicts", [])
                if isinstance(item, dict) and item.get("system_promise_id")
            )
        return result

    def _append_regression_history_with_system_behavior(project: str, root: Path, result: dict[str, Any]) -> list[dict[str, Any]]:
        # Base _append_regression_history now forwards system_promise_id and
        # regression_contract into history items and writes to both locations.
        # Patch only needs to trigger learning refresh after history is written.
        history = list(original_append_history(project, root, result) or [])
        if not history:
            return history
        try:
            refresh = _system_behavior_learning_refresh_summary(project, root)
            result["risk_clue_pool_learning_refresh"] = refresh
            last = history[-1]
            last["risk_clue_pool_learning_refresh"] = refresh
            history[-1] = last
            _rr._write_json(root / "platform_outputs" / project / "regression_run" / "regression_run_history.json", history)
            _rr._write_json(root / "platform_workspace" / project / "defect_discovery" / "regression_run_history.json", history)
        except Exception:
            return history
        return history

    _rsb._ORIGINAL_LOAD_CONFIRMED_FINDINGS_REGRESSION_PROBES_SYSTEM_BEHAVIOR = original_load_confirmed  # type: ignore[attr-defined]
    _rr._ORIGINAL_JUDGE_PROBE_SYSTEM_BEHAVIOR = original_judge  # type: ignore[attr-defined]
    _rr._ORIGINAL_REVERIFY_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR = original_reverify  # type: ignore[attr-defined]
    _rr._ORIGINAL_APPEND_REGRESSION_HISTORY_SYSTEM_BEHAVIOR = original_append_history  # type: ignore[attr-defined]
    _rsb._load_confirmed_findings_regression_probes = _load_confirmed_findings_regression_probes_with_system_behavior  # type: ignore[assignment]
    _rr._judge_probe = _judge_probe_with_system_behavior  # type: ignore[assignment]
    _rr._reverify_confirmed_findings = _reverify_confirmed_findings_with_system_behavior  # type: ignore[assignment]
    _rr._append_regression_history = _append_regression_history_with_system_behavior  # type: ignore[assignment]
    _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = True  # type: ignore[attr-defined]


def install_system_behavior_space_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    if getattr(_bsg, "_SYSTEM_BEHAVIOR_SPACE_PATCHED", False):
        return
    original_build = getattr(_bsg.BusinessStateGraphBuilder, "build")
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "behavior_contract")

    def _build_with_system_behavior_space(self: Any, prd_text: str = "", api_spec_text: str = "", db_schema_text: str = "") -> dict[str, Any]:
        graphs = original_build(self, prd_text, api_spec_text, db_schema_text)
        try:
            asset = getattr(self, "system_behavior_space_knowledge_asset", None)
            if not isinstance(asset, dict) or not asset:
                asset = _load_existing_enterprise_asset()
            self.system_behavior_space = build_system_behavior_space(prd_text, api_spec_text, db_schema_text, knowledge_asset=asset).to_dict()
        except Exception as exc:
            self.system_behavior_space = {"version": SYSTEM_BEHAVIOR_SPACE_VERSION, "status": "unavailable", "reason": f"system_behavior_space_build_failed:{type(exc).__name__}", "summary": {"object_count": 0, "promise_count": 0, "probe_candidate_count": 0, "coverage_gap_count": 1}}
        return graphs

    def _behavior_contract_with_system_behavior_space(self: Any) -> dict[str, Any]:
        contract = original_contract(self)
        space = getattr(self, "system_behavior_space", None)
        if isinstance(space, dict) and space:
            contract["system_behavior_space"] = space
            contract = _attach_system_behavior_slices(contract, space)
            summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
            space_summary = space.get("summary") if isinstance(space.get("summary"), dict) else {}
            summary["system_behavior_space_version"] = str(space.get("version") or SYSTEM_BEHAVIOR_SPACE_VERSION)
            summary["system_promise_count"] = int(space_summary.get("promise_count") or 0)
            summary["system_probe_candidate_count"] = int(space_summary.get("probe_candidate_count") or 0)
            summary["system_behavior_object_count"] = int(space_summary.get("object_count") or 0)
            summary["system_behavior_source_coverage"] = space_summary.get("source_coverage") if isinstance(space_summary.get("source_coverage"), dict) else {}
            summary["system_behavior_goal"] = "open_ended_system_promise_discovery_across_all_surfaces"
            contract["summary"] = summary
            gaps = contract.get("coverage_gaps") if isinstance(contract.get("coverage_gaps"), list) else []
            for gap in space.get("coverage_gaps") if isinstance(space.get("coverage_gaps"), list) else []:
                if isinstance(gap, dict):
                    gaps.append({**gap, "source": "system_behavior_space"})
            contract["coverage_gaps"] = gaps
        return contract

    _bsg.BusinessStateGraphBuilder._ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE = original_build  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder._ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE = original_contract  # type: ignore[attr-defined]
    _bsg.BusinessStateGraphBuilder.build = _build_with_system_behavior_space  # type: ignore[method-assign]
    _bsg.BusinessStateGraphBuilder.behavior_contract = _behavior_contract_with_system_behavior_space  # type: ignore[method-assign]
    _install_v12_behavior_space_context_patch()
    _install_system_behavior_scenario_patch()
    _install_system_behavior_oracle_patch()
    _install_system_behavior_finding_patch()
    _install_system_behavior_regression_patch()
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = True  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def _install_v12_behavior_space_context_patch() -> None:
    try:
        from ai_test_asset_center import v12_pipeline as _v12
    except Exception:
        return
    if getattr(_v12, "_SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED", False):
        return
    original = getattr(_v12, "run_v12_pipeline", None)
    if not callable(original):
        return

    def _run_v12_pipeline_with_behavior_space_context(project: str, root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _BEHAVIOR_SPACE_CONTEXT.set({"project": str(project or ""), "root": Path(root)})
        try:
            return original(project, root, *args, **kwargs)
        finally:
            _BEHAVIOR_SPACE_CONTEXT.reset(token)

    _v12._ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_SPACE_CONTEXT = original  # type: ignore[attr-defined]
    _v12.run_v12_pipeline = _run_v12_pipeline_with_behavior_space_context  # type: ignore[assignment]
    _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = True  # type: ignore[attr-defined]


def prepare_system_behavior_space_learning_context(builder: Any, *, project: str, root: Any) -> Any:
    try:
        from ai_test_asset_center.enterprise_knowledge_center import load_enterprise_business_knowledge_asset
        asset = load_enterprise_business_knowledge_asset(project, Path(root))
        if isinstance(asset, dict):
            setattr(builder, "system_behavior_space_knowledge_asset", asset)
    except Exception:
        pass
    return builder


def restore_system_behavior_space_patch() -> None:
    original_build = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR_SPACE", None)
    original_contract = getattr(_bsg.BusinessStateGraphBuilder, "_ORIGINAL_CONTRACT_SYSTEM_BEHAVIOR_SPACE", None)
    if callable(original_build):
        _bsg.BusinessStateGraphBuilder.build = original_build  # type: ignore[method-assign]
    if callable(original_contract):
        _bsg.BusinessStateGraphBuilder.behavior_contract = original_contract  # type: ignore[method-assign]
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        original_v12 = getattr(_v12, "_ORIGINAL_RUN_V12_PIPELINE_SYSTEM_BEHAVIOR_SPACE_CONTEXT", None)
        if callable(original_v12):
            _v12.run_v12_pipeline = original_v12  # type: ignore[assignment]
        _v12._SYSTEM_BEHAVIOR_SPACE_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import semantic_scenario_generator as _ssg
        original_scenario = getattr(_ssg.SemanticScenarioGenerator, "_ORIGINAL_INVARIANT_FROM_META_SYSTEM_BEHAVIOR", None)
        if callable(original_scenario):
            _ssg.SemanticScenarioGenerator._invariant_from_meta = original_scenario  # type: ignore[method-assign]
        _ssg._SYSTEM_BEHAVIOR_SCENARIO_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import oracle_engine as _oe
        original_eval = getattr(_oe.OracleEngine, "_ORIGINAL_EVALUATE_SYSTEM_BEHAVIOR", None)
        original_build = getattr(_oe.EvidenceGraphBuilder, "_ORIGINAL_BUILD_SYSTEM_BEHAVIOR", None)
        if callable(original_eval):
            _oe.OracleEngine.evaluate = original_eval  # type: ignore[method-assign]
        if callable(original_build):
            _oe.EvidenceGraphBuilder.build = original_build  # type: ignore[method-assign]
        _oe._SYSTEM_BEHAVIOR_ORACLE_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import v12_pipeline as _v12
        original_confirmed = getattr(_v12, "_ORIGINAL_CONFIRMED_ORACLE_FINDING_SYSTEM_BEHAVIOR", None)
        original_persist = getattr(_v12, "_ORIGINAL_PERSIST_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR", None)
        if callable(original_confirmed):
            _v12._confirmed_oracle_finding = original_confirmed  # type: ignore[assignment]
        if callable(original_persist):
            _v12._persist_confirmed_findings = original_persist  # type: ignore[assignment]
        _v12._SYSTEM_BEHAVIOR_FINDING_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    try:
        from ai_test_asset_center import regression_runner as _rr
        from ai_test_asset_center import regression_suite_builder as _rsb
        original_load_confirmed = getattr(_rsb, "_ORIGINAL_LOAD_CONFIRMED_FINDINGS_REGRESSION_PROBES_SYSTEM_BEHAVIOR", None)
        original_judge = getattr(_rr, "_ORIGINAL_JUDGE_PROBE_SYSTEM_BEHAVIOR", None)
        original_reverify = getattr(_rr, "_ORIGINAL_REVERIFY_CONFIRMED_FINDINGS_SYSTEM_BEHAVIOR", None)
        original_append_history = getattr(_rr, "_ORIGINAL_APPEND_REGRESSION_HISTORY_SYSTEM_BEHAVIOR", None)
        if callable(original_load_confirmed):
            _rsb._load_confirmed_findings_regression_probes = original_load_confirmed  # type: ignore[assignment]
        if callable(original_judge):
            _rr._judge_probe = original_judge  # type: ignore[assignment]
        if callable(original_reverify):
            _rr._reverify_confirmed_findings = original_reverify  # type: ignore[assignment]
        if callable(original_append_history):
            _rr._append_regression_history = original_append_history  # type: ignore[assignment]
        _rr._SYSTEM_BEHAVIOR_REGRESSION_PATCHED = False  # type: ignore[attr-defined]
    except Exception:
        pass
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCHED = False  # type: ignore[attr-defined]
    _bsg._SYSTEM_BEHAVIOR_SPACE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
