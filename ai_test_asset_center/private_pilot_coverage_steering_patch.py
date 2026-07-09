from __future__ import annotations

"""Coverage-gap and learning steering for existing V12 behavior scheduling.

This patch intentionally reuses the existing V12 scheduler and the existing
RiskCluePool persistence.  It does not create a new scanner or a second learning
engine.  It simply reorders already-materialized behavior slices using:

1. current coverage matrix gaps;
2. project/private-deployment learning weights from risk_clue_pool;
3. SaaS/platform sanitized learning weights from risk_clue_pool.

System Behavior Space slices are handled through the same mechanism.  Their
structured dimensions and surface plans are normalized into the same risk-family
keys that RiskCluePool learns from regression contracts.
"""

import contextvars
import json
import re
from pathlib import Path
from typing import Any

PATCH_SOURCE = "ai_test_asset_center.private_pilot_coverage_steering_patch"
_COVERAGE_STEERING_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "qualibug_coverage_steering_context",
    default=None,
)

_FAMILY_WEIGHT = {
    "gap": 50,
    "candidate_only": 25,
    "confirmed_needs_evidence": 10,
}

_KIND_TO_RISK_FAMILY = {
    "permission": "authorization_access_control",
    "authz": "authorization_access_control",
    "authorization": "authorization_access_control",
    "isolation": "tenant_isolation",
    "tenant_isolation": "tenant_isolation",
    "tenant": "tenant_isolation",
    "money": "money_quantity_conservation",
    "financial": "money_quantity_conservation",
    "quantity": "money_quantity_conservation",
    "conservation": "money_quantity_conservation",
    "concurrency": "concurrency_race_condition",
    "race": "concurrency_race_condition",
    "transition": "state_machine",
    "state_machine": "state_machine",
    "lifecycle": "state_machine",
    "state": "state_machine",
    "invariant": "data_consistency",
    "dependency": "data_consistency",
    "data_consistency": "data_consistency",
    "source_observation": "visibility_disclosure",
    "visibility": "visibility_disclosure",
    "ui_api_contract": "ui_api_contract_drift",
    "ui_contract": "ui_api_contract_drift",
    "validation": "ui_api_contract_drift",
    "audit": "audit_traceability",
    "traceability": "audit_traceability",
    "async": "async_eventual_consistency",
    "side_effect": "async_eventual_consistency",
    "eventual_consistency": "async_eventual_consistency",
    "idempotency": "idempotency",
    "retry": "idempotency",
    "regression": "historical_regression",
    "historical_bug": "historical_regression",
}

_TOKEN_TO_RISK_FAMILY = (
    (("tenant", "org", "workspace", "isolation", "cross"), "tenant_isolation"),
    (("permission", "auth", "role", "forbidden", "unauthorized", "readonly", "admin"), "authorization_access_control"),
    (("money", "amount", "balance", "stock", "inventory", "quantity", "coupon", "payment", "refund", "price"), "money_quantity_conservation"),
    (("concurrent", "race", "double", "parallel", "lock", "atomic"), "concurrency_race_condition"),
    (("state", "status", "transition", "lifecycle", "cancel", "close", "reopen"), "state_machine"),
    (("input", "validation", "boundary", "invalid", "null", "empty", "overflow", "injection", "xss", "sql"), "input_validation_boundary"),
    (("workflow", "approval", "approve", "reject", "review"), "workflow_approval"),
    (("async", "queue", "message", "callback", "webhook", "job", "task", "cron"), "async_eventual_consistency"),
    (("cache", "stale", "ttl", "redis"), "cache_stale_state"),
    (("audit", "trace", "log", "receipt", "ledger"), "audit_traceability"),
    (("ui", "frontend", "page", "button", "form", "contract"), "ui_api_contract_drift"),
    (("regression", "historical", "previous", "reopen"), "historical_regression"),
)

_SURFACE_ALIASES = {
    "api": "api",
    "http": "api",
    "endpoint": "api",
    "db": "db",
    "database": "db",
    "table": "db",
    "sql": "db",
    "ui": "ui",
    "browser": "ui",
    "page": "ui",
    "auth": "auth",
    "role": "auth",
    "permission": "auth",
    "log": "log",
    "trace": "log",
    "audit": "log",
    "async": "async",
    "queue": "async",
    "event": "async",
}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_project(value: str) -> str:
    return str(value or "").replace("/", "_").strip() or "unscoped"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8") or "{}")
        return payload if isinstance(payload, dict) else {}
    except Exception:
        return {}


def _coverage_matrix(root: Path, project: str) -> dict[str, Any]:
    metrics = _read_json(root / "platform_outputs" / _safe_project(project) / "benchmark" / "benchmark_metrics.json")
    return _as_dict(metrics.get("coverage_matrix"))


def _coverage_gap_weights(root: Path, project: str) -> dict[str, float]:
    matrix = _coverage_matrix(root, project)
    rows = matrix.get("families") if isinstance(matrix.get("families"), list) else []
    weights: dict[str, float] = {}
    for row in rows:
        item = _as_dict(row)
        family = _normalize_family(str(item.get("family") or ""))
        status = str(item.get("coverage_status") or "").strip()
        if family and status in _FAMILY_WEIGHT:
            weights[family] = max(weights.get(family, 0.0), float(_FAMILY_WEIGHT[status]))
    return weights


def _learning_weights(root: Path, project: str) -> tuple[dict[str, float], dict[str, float]]:
    try:
        from .risk_clue_pool import get_platform_learning_weights, get_project_learning_weights

        project_weights = get_project_learning_weights(project, root)
        platform_weights = get_platform_learning_weights(root)
    except Exception:
        project_weights, platform_weights = {}, {}
    return project_weights, platform_weights


def _normalize_token(value: Any) -> str:
    return re.sub(r"[^a-z0-9_]+", "_", str(value or "").strip().lower().replace("-", "_")).strip("_")


def _normalize_family(value: Any) -> str:
    token = _normalize_token(value)
    if not token:
        return ""
    return _KIND_TO_RISK_FAMILY.get(token, token)


def _normalize_surface(value: Any) -> str:
    token = _normalize_token(value)
    if not token:
        return ""
    return _SURFACE_ALIASES.get(token, token)


def _deep_get(value: Any, key: str) -> Any:
    if isinstance(value, dict):
        if key in value:
            return value.get(key)
        for child in value.values():
            found = _deep_get(child, key)
            if found not in (None, ""):
                return found
    if isinstance(value, list):
        for child in value:
            found = _deep_get(child, key)
            if found not in (None, ""):
                return found
    return None


def _regression_contract(item: dict[str, Any]) -> dict[str, Any]:
    contract = item.get("regression_contract") if isinstance(item.get("regression_contract"), dict) else {}
    if contract:
        return contract
    evidence = item.get("system_behavior_space_evidence") if isinstance(item.get("system_behavior_space_evidence"), dict) else {}
    if evidence:
        return {"system_behavior_space": evidence, "dimensions": evidence.get("dimensions") or [], "surface_plan": evidence.get("surface_plan") or []}
    return {}


def _structured_families(item: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("_system_behavior_dimensions", "system_behavior_dimensions"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    contract = _regression_contract(item)
    if isinstance(contract, dict):
        values.extend(contract.get("dimensions") if isinstance(contract.get("dimensions"), list) else [])
        hints = contract.get("system_behavior_space") if isinstance(contract.get("system_behavior_space"), dict) else {}
        values.extend(hints.get("dimensions") if isinstance(hints.get("dimensions"), list) else [])
    raw_deep = _deep_get(item, "dimensions")
    if isinstance(raw_deep, list):
        values.extend(raw_deep)
    return sorted({family for family in (_normalize_family(value) for value in values) if family})


def _structured_surfaces(item: dict[str, Any]) -> list[str]:
    values: list[Any] = []
    for key in ("_system_behavior_surface_plan", "system_behavior_surface_plan"):
        raw = item.get(key)
        if isinstance(raw, list):
            values.extend(raw)
    contract = _regression_contract(item)
    if isinstance(contract, dict):
        values.extend(contract.get("surface_plan") if isinstance(contract.get("surface_plan"), list) else [])
        hints = contract.get("system_behavior_space") if isinstance(contract.get("system_behavior_space"), dict) else {}
        values.extend(hints.get("surface_plan") if isinstance(hints.get("surface_plan"), list) else [])
    raw_deep = _deep_get(item, "surfaces") or _deep_get(item, "surface_plan")
    if isinstance(raw_deep, list):
        values.extend(raw_deep)
    return sorted({surface for surface in (_normalize_surface(value) for value in values) if surface})


def _slice_text(item: dict[str, Any]) -> str:
    fields: list[Any] = [
        item.get("risk_family"), item.get("family"), item.get("defect_family"),
        item.get("kind"), item.get("entity"), item.get("slice_id"), item.get("title"),
        item.get("description"), item.get("_selection_family"), item.get("system_promise_id"),
    ]
    fields.extend(item.get("states") if isinstance(item.get("states"), list) else [])
    fields.extend(item.get("endpoints") if isinstance(item.get("endpoints"), list) else [])
    fields.extend(item.get("_system_behavior_dimensions") if isinstance(item.get("_system_behavior_dimensions"), list) else [])
    fields.extend(item.get("_system_behavior_surface_plan") if isinstance(item.get("_system_behavior_surface_plan"), list) else [])
    for key, value in item.items():
        if str(key).startswith("_") and isinstance(value, (str, int, float)):
            fields.append(value)
    return " ".join(str(value or "") for value in fields).lower()


def _slice_risk_families(item: dict[str, Any]) -> list[str]:
    families: list[str] = []
    families.extend(_structured_families(item))
    for key in ("risk_family", "family", "defect_family"):
        value = _normalize_family(item.get(key))
        if value:
            families.append(value)
    kind_family = _normalize_family(item.get("kind"))
    if kind_family:
        families.append(kind_family)
    selection_family = _normalize_family(item.get("_selection_family"))
    if selection_family:
        families.append(selection_family)
    text = _slice_text(item)
    for tokens, family in _TOKEN_TO_RISK_FAMILY:
        if any(token in text for token in tokens):
            families.append(family)
    return sorted(dict.fromkeys(families))


def _slice_risk_family(item: dict[str, Any]) -> str:
    families = _slice_risk_families(item)
    return families[0] if families else ""


def _slice_surfaces(item: dict[str, Any]) -> list[str]:
    surfaces: list[str] = []
    surfaces.extend(_structured_surfaces(item))
    text = _slice_text(item)
    if any(token in text for token in ("api", "endpoint", "http", "/")):
        surfaces.append("api")
    if any(token in text for token in ("db", "table", "schema", "sql", "database")):
        surfaces.append("db")
    if any(token in text for token in ("ui", "page", "browser", "button", "form", "frontend")):
        surfaces.append("ui")
    if any(token in text for token in ("auth", "role", "permission", "tenant")):
        surfaces.append("auth")
    return sorted(dict.fromkeys(_normalize_surface(surface) for surface in surfaces if _normalize_surface(surface)))


def _learning_score(item: dict[str, Any], project_weights: dict[str, float], platform_weights: dict[str, float]) -> tuple[float, dict[str, Any]]:
    families = _slice_risk_families(item)
    surfaces = _slice_surfaces(item)
    project_score = 0.0
    platform_score = 0.0
    matched_families: list[str] = []
    for family in families:
        p = float(project_weights.get(family) or 0.0) * 4.0
        q = float(platform_weights.get(family) or 0.0) * 2.0
        if p or q:
            matched_families.append(family)
        project_score += p
        platform_score += q
    for surface in surfaces:
        project_score += float(project_weights.get(f"surface:{surface}") or 0.0) * 0.8
        platform_score += float(platform_weights.get(f"surface:{surface}") or 0.0) * 0.4
    if len(surfaces) >= 2:
        combo = "+".join(surfaces)
        project_score += float(project_weights.get(f"surface_combo:{combo}") or 0.0) * 1.2
        platform_score += float(platform_weights.get(f"surface_combo:{combo}") or 0.0) * 0.6
    return min(project_score + platform_score, 35.0), {
        "families": families,
        "matched_families": matched_families,
        "surfaces": surfaces,
        "project_learning_score": round(project_score, 3),
        "platform_learning_score": round(platform_score, 3),
        "system_behavior_slice": bool(item.get("_selection_origin") == "system_behavior_space" or item.get("system_promise_id")),
    }


def _confirmed_finding_boundaries(root: Path, project: str) -> list[dict[str, Any]]:
    """Extract (entity, dimension, surface) boundary patterns from confirmed findings.

    When a confirmed defect like "跨租户读取订单" exists, this extracts the
    pattern (entity=order, dimension=tenant_isolation, surface=api) so that
    similar slices — "跨租户导出订单", "跨租户查看退款" — can be prioritized.
    """
    ws = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    ledger = _read_json(ws / "confirmed_findings.json")
    if not isinstance(ledger, dict):
        return []
    boundaries: list[dict[str, Any]] = []
    for evidence_id, defect in ledger.items():
        if not isinstance(defect, dict):
            continue
        # Only confirmed/defect entries carry reliable boundary patterns
        if str(defect.get("customer_delivery_status") or "") != "defect":
            continue
        # Extract entity from reproduction path
        repro = defect.get("reproduction") if isinstance(defect.get("reproduction"), dict) else {}
        path = str(repro.get("path") or "")
        entity = _entity_from_path(path)
        # Extract dimensions from system behavior contract
        dims: list[str] = []
        contract = defect.get("regression_contract") if isinstance(defect.get("regression_contract"), dict) else {}
        if isinstance(contract.get("dimensions"), list):
            dims = [_normalize_family(d) for d in contract["dimensions"] if str(d)]
        if not dims:
            dims_raw = defect.get("system_behavior_dimensions")
            if isinstance(dims_raw, list):
                dims = [_normalize_family(d) for d in dims_raw if str(d)]
        # Extract surfaces
        surfaces: list[str] = []
        if isinstance(contract.get("surface_plan"), list):
            surfaces = [_normalize_surface(s) for s in contract["surface_plan"] if str(s)]
        if not surfaces:
            surfaces_raw = defect.get("system_behavior_surface_plan")
            if isinstance(surfaces_raw, list):
                surfaces = [_normalize_surface(s) for s in surfaces_raw if str(s)]
        if entity and dims:
            boundaries.append({
                "entity": entity,
                "dimensions": dims,
                "surfaces": surfaces,
                "evidence_id": str(evidence_id),
                "source": "confirmed_finding_ledger",
            })
    # Also extract from risk_clue_pool project learning
    pool = _read_json(root / "platform_outputs" / _safe_project(project) / "risk_clue_pool" / "risk_clues.json")
    if isinstance(pool, dict):
        learning = pool.get("project_learning") if isinstance(pool.get("project_learning"), dict) else {}
        for signal in learning.get("signals") or []:
            if not isinstance(signal, dict):
                continue
            if signal.get("signal_kind") != "system_behavior_promise":
                continue
            if str(signal.get("regression_status") or "") != "regression_failed":
                continue
            entity_hint = str(signal.get("entity_hint") or "")
            dims = [_normalize_family(d) for d in (signal.get("dimensions") or []) if str(d)]
            if entity_hint and dims:
                boundaries.append({
                    "entity": entity_hint,
                    "dimensions": dims,
                    "surfaces": [_normalize_surface(s) for s in (signal.get("surfaces") or []) if str(s)],
                    "evidence_id": str(signal.get("signal_id") or ""),
                    "source": "risk_clue_pool_regression_failed",
                })
    return boundaries


def _entity_from_path(path: str) -> str:
    """Extract entity name from an API path, structurally (no hardcoded maps)."""
    parts = [p for p in str(path or "").strip("/").split("/") if p and "{" not in p and ":" not in p]
    # Skip common API prefixes
    skip = {"api", "apis", "rest", "v1", "v2", "v3", "v4", "public", "internal"}
    for part in parts:
        if part.lower() not in skip:
            return re.sub(r"[^A-Za-z0-9_]+", "_", part.lower()).strip("_")[:60]
    return ""


def _entity_overlap(a: str, b: str) -> float:
    """Compute entity similarity as normalized token overlap (0.0-1.0)."""
    if not a or not b:
        return 0.0
    a_tokens = set(re.split(r"[_\-\s]+", a.lower()))
    b_tokens = set(re.split(r"[_\-\s]+", b.lower()))
    if not a_tokens or not b_tokens:
        return 0.0
    # Exact match gets 1.0
    if a_tokens == b_tokens:
        return 1.0
    # One contains the other
    if a_tokens.issubset(b_tokens) or b_tokens.issubset(a_tokens):
        return 0.85
    intersection = a_tokens & b_tokens
    union = a_tokens | b_tokens
    return len(intersection) / len(union) if union else 0.0


def _similar_boundary_boost(
    slices: list[dict[str, Any]],
    boundaries: list[dict[str, Any]],
) -> dict[str, float]:
    """Compute priority boosts for slices that share entity+dimension patterns
    with confirmed historical defects.

    A slice about "跨租户导出订单" (entity=order, dim=tenant_isolation) gets
    boosted when a confirmed finding about "跨租户读取订单" (entity=order,
    dim=tenant_isolation) exists — the boundary is similar, the risk is real.
    """
    if not boundaries:
        return {}
    boosts: dict[str, float] = {}
    for idx, raw in enumerate(slices):
        item = dict(raw) if isinstance(raw, dict) else {}
        slice_id = str(item.get("slice_id") or "")
        if not slice_id:
            continue
        slice_entity = _normalize_token(item.get("entity") or "")
        if not slice_entity:
            continue
        slice_families = _slice_risk_families(item)
        slice_surfaces = _slice_surfaces(item)
        best_boost = 0.0
        best_match: dict[str, Any] = {}
        for boundary in boundaries:
            b_entity = _normalize_token(boundary.get("entity") or "")
            b_dims = set(boundary.get("dimensions") or [])
            b_surfaces = set(boundary.get("surfaces") or [])

            # Dimension overlap: how many risk families match?
            dim_overlap = len(b_dims & set(slice_families))
            if dim_overlap == 0:
                continue

            # Entity overlap: same or related entity?
            entity_score = _entity_overlap(slice_entity, b_entity)

            # Surface overlap
            surface_score = len(b_surfaces & set(slice_surfaces)) * 0.3 if b_surfaces else 0.0

            # Combined boost: dimension match is primary, entity similarity amplifies
            boost = dim_overlap * 3.0 + entity_score * 4.0 + surface_score
            if boost > best_boost:
                best_boost = boost
                best_match = {
                    "matched_dimensions": sorted(b_dims & set(slice_families)),
                    "matched_entity": b_entity if entity_score > 0.5 else "",
                    "source": boundary.get("source", ""),
                    "evidence_id": boundary.get("evidence_id", ""),
                }

        if best_boost > 0:
            boosts[slice_id] = best_boost
            item["_historical_boundary_match"] = best_match
            item["_historical_boundary_boost"] = best_boost

    return boosts


def _steer_slices(slices: list[dict[str, Any]], *, root: Path, project: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    coverage_weights = _coverage_gap_weights(root, project)
    project_weights, platform_weights = _learning_weights(root, project)

    # ── Historical boundary pattern boost ──
    # When confirmed defects exist for a specific entity+dimension combination,
    # similar slices (same dimension, related entity) get priority — this is
    # how "历史缺陷→相似边界回归风险" works in practice.
    boundaries = _confirmed_finding_boundaries(root, project)
    boundary_boosts = _similar_boundary_boost(slices, boundaries)

    if not coverage_weights and not project_weights and not platform_weights and not boundary_boosts:
        return slices, {"status": "not_applied", "reason": "no_coverage_or_learning_weights"}

    indexed: list[tuple[float, int, dict[str, Any]]] = []
    coverage_count = 0
    learning_count = 0
    boundary_count = 0
    system_behavior_learning_count = 0
    for index, raw in enumerate(slices):
        item = dict(raw) if isinstance(raw, dict) else {}
        slice_id = str(item.get("slice_id") or "")
        families = _slice_risk_families(item)
        coverage_matches = {family: float(coverage_weights.get(family, 0.0)) for family in families if float(coverage_weights.get(family, 0.0)) > 0}
        coverage_weight = max(coverage_matches.values()) if coverage_matches else 0.0
        if coverage_weight > 0:
            coverage_count += 1
            item["_coverage_steering_families"] = sorted(coverage_matches)
            item["_coverage_steering_family"] = sorted(coverage_matches)[0]
            item["_coverage_steering_weight"] = coverage_weight
            item["_coverage_steering_reason"] = "prioritize_current_coverage_matrix_gap"
        learning_weight, learning_detail = _learning_score(item, project_weights, platform_weights)
        if learning_weight > 0:
            learning_count += 1
            if learning_detail.get("system_behavior_slice"):
                system_behavior_learning_count += 1
            item["_learning_steering"] = learning_detail
            item["_learning_steering_weight"] = learning_weight
            item["_learning_steering_reason"] = "prioritize_from_project_and_platform_risk_clue_pool"
        # ── Historical boundary boost ──
        boundary_boost = boundary_boosts.get(slice_id, 0.0)
        if boundary_boost > 0:
            boundary_count += 1
            item["_historical_boundary_boost"] = boundary_boost
            item["_historical_boundary_reason"] = "prioritize_similar_boundary_regression_risk_from_confirmed_findings"
        total_weight = coverage_weight + learning_weight + boundary_boost
        if total_weight > 0:
            # Historical boundary matches are the strongest signal — a real bug
            # already happened at a similar boundary, so the risk is confirmed.
            if boundary_boost >= 5:
                item["priority"] = max(float(item.get("priority") or 0.0), 0.97)
            elif coverage_weight >= 50:
                item["priority"] = max(float(item.get("priority") or 0.0), 0.95)
            elif learning_weight >= 10:
                item["priority"] = max(float(item.get("priority") or 0.0), 0.90)
            else:
                item["priority"] = max(float(item.get("priority") or 0.0), 0.86)
        indexed.append((total_weight, -index, item))

    indexed.sort(key=lambda row: (row[0], row[2].get("priority") or 0, row[1]), reverse=True)
    ordered = [item for _, _, item in indexed]
    return ordered, {
        "status": "applied" if (coverage_count or learning_count or boundary_count) else "not_applied",
        "reason": "coverage_and_learning_weights_prioritized" if (coverage_count and learning_count) else "coverage_gap_slices_prioritized" if coverage_count else "learning_weights_prioritized" if learning_count else "historical_boundary_boost_prioritized" if boundary_count else "no_slice_matched_weights",
        "gap_family_weights": coverage_weights,
        "project_learning_weight_count": len(project_weights),
        "platform_learning_weight_count": len(platform_weights),
        "coverage_steered_slice_count": coverage_count,
        "learning_steered_slice_count": learning_count,
        "historical_boundary_boosted_slice_count": boundary_count,
        "historical_boundary_patterns_found": len(boundaries),
        "system_behavior_learning_steered_slice_count": system_behavior_learning_count,
        "top_steered_slice_ids": [str(item.get("slice_id") or "") for item in ordered if item.get("_coverage_steering_weight") or item.get("_learning_steering_weight") or item.get("_historical_boundary_boost")][:12],
    }


def _attach_coverage_steering_result(result: dict[str, Any], diagnostic: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(result, dict) or not diagnostic:
        return result
    payload = {
        **diagnostic,
        "patch_source": PATCH_SOURCE,
        "honesty_rule": "Coverage and learning steering only reorder existing source-grounded behavior slices; they do not create findings or synthetic coverage.",
    }
    result["coverage_steering"] = payload
    phases = result.get("phases") if isinstance(result.get("phases"), dict) else {}
    incremental = phases.get("incremental_discovery") if isinstance(phases.get("incremental_discovery"), dict) else {}
    incremental["coverage_steering"] = payload
    phases["incremental_discovery"] = incremental
    result["phases"] = phases
    ledger = result.get("behavior_slice_ledger") if isinstance(result.get("behavior_slice_ledger"), dict) else {}
    ledger["coverage_steering"] = payload
    result["behavior_slice_ledger"] = ledger
    return result


def install_coverage_steering_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    from ai_test_asset_center import v12_pipeline

    if getattr(v12_pipeline, "_COVERAGE_STEERING_PATCHED", False):
        return

    original_run = getattr(v12_pipeline, "run_v12_pipeline")
    original_schedule = getattr(v12_pipeline, "_schedule_behavior_slices")

    def _run_with_coverage_steering(project: str, root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        context_payload: dict[str, Any] = {"project": str(project), "root": Path(root), "last_coverage_steering": {}}
        token = _COVERAGE_STEERING_CONTEXT.set(context_payload)
        try:
            result = original_run(project, root, *args, **kwargs)
            return _attach_coverage_steering_result(result, _as_dict(context_payload.get("last_coverage_steering")))
        finally:
            _COVERAGE_STEERING_CONTEXT.reset(token)

    def _schedule_with_coverage_steering(slices: list[dict[str, Any]], settings: dict[str, int], history: list[dict[str, Any]] | None) -> dict[str, Any]:
        context = _COVERAGE_STEERING_CONTEXT.get() or {}
        project = str(context.get("project") or "").strip()
        root = Path(context.get("root") or Path.cwd())
        diagnostic: dict[str, Any] = {"status": "not_applied", "reason": "missing_project_context"}
        steered_slices = slices
        if project:
            steered_slices, diagnostic = _steer_slices(slices, root=root, project=project)
        context["last_coverage_steering"] = diagnostic
        selection = original_schedule(steered_slices, settings, history)
        if isinstance(selection, dict):
            selection["coverage_steering"] = diagnostic
            if diagnostic.get("status") == "applied":
                mode = str(selection.get("selection_mode") or "")
                if "coverage_learning_steered" not in mode:
                    selection["selection_mode"] = f"{mode}+coverage_learning_steered" if mode else "coverage_learning_steered"
        return selection

    v12_pipeline._ORIGINAL_COVERAGE_STEERING_RUN = original_run  # type: ignore[attr-defined]
    v12_pipeline._ORIGINAL_COVERAGE_STEERING_SCHEDULE = original_schedule  # type: ignore[attr-defined]
    v12_pipeline.run_v12_pipeline = _run_with_coverage_steering  # type: ignore[assignment]
    v12_pipeline._schedule_behavior_slices = _schedule_with_coverage_steering  # type: ignore[assignment]
    v12_pipeline._COVERAGE_STEERING_PATCHED = True  # type: ignore[attr-defined]
    v12_pipeline._COVERAGE_STEERING_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]


def restore_coverage_steering_patch() -> None:
    from ai_test_asset_center import v12_pipeline

    original_run = getattr(v12_pipeline, "_ORIGINAL_COVERAGE_STEERING_RUN", None)
    original_schedule = getattr(v12_pipeline, "_ORIGINAL_COVERAGE_STEERING_SCHEDULE", None)
    if callable(original_run):
        v12_pipeline.run_v12_pipeline = original_run  # type: ignore[assignment]
    if callable(original_schedule):
        v12_pipeline._schedule_behavior_slices = original_schedule  # type: ignore[assignment]
    v12_pipeline._COVERAGE_STEERING_PATCHED = False  # type: ignore[attr-defined]
