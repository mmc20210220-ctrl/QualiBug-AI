"""System Behavior Space scenario enrichment helpers.

Owns the scenario enricher body registered through
``register_scenario_enricher``. The private-pilot patch module remains the
thin installer and re-imports shared hint helpers for oracle/finding hooks.
"""
from __future__ import annotations

from typing import Any

from ai_test_asset_center.system_behavior_space import (
    SYSTEM_BEHAVIOR_SPACE_VERSION,
)

_SAFE_READ_METHODS = {"GET", "HEAD", "OPTIONS"}
_KNOWN_HTTP_METHODS = {"GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"}
_WRITE_METHODS = _KNOWN_HTTP_METHODS - _SAFE_READ_METHODS

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
    between a source-derived operation and a source-grounded authorization
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


def _enrich_system_behavior_scenario(
    item: Any,
    slice_meta: dict[str, Any],
    discovery_round: int,
    api_doc: str = "",
) -> Any:
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
    # entities with only source-derived write routes can still execute.
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
                            from ai_test_asset_center.semantic_scenario_generator import (
                                SemanticScenarioGenerator,
                            )
                            _post_body = SemanticScenarioGenerator._runtime_body_template(
                                api_doc, wr["method"], wr["path"],
                            )
                        except Exception:
                            _post_body = {}
                        if not isinstance(_post_body, dict) or not _post_body:
                            _post_body = _universal_post_body(wr["path"])
                        # Resolve body placeholders (orderId/addressId/...) before write.
                        try:
                            from ai_test_asset_center.semantic_scenario_generator import (
                                SemanticScenarioGenerator as _SSG,
                            )
                            _bind_steps, _ = _SSG._body_binding_resolve_steps(
                                _post_body if isinstance(_post_body, dict) else {},
                                actor="readonly",
                                start_order=next_order,
                                api_doc=api_doc,
                            )
                            for _bs in _bind_steps:
                                existing_steps.append(_bs)
                                next_order += 1
                        except Exception:
                            pass
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
                            from ai_test_asset_center.semantic_scenario_generator import (
                                SemanticScenarioGenerator,
                            )
                            _put_body = SemanticScenarioGenerator._runtime_body_template(
                                api_doc, wr["method"], safe_path,
                            )
                        except Exception:
                            _put_body = {}
                        if not isinstance(_put_body, dict):
                            _put_body = {}
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
