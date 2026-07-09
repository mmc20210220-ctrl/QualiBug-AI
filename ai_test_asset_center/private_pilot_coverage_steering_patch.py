from __future__ import annotations

"""Coverage-gap and learning steering for existing V12 behavior scheduling.

This patch intentionally reuses the existing V12 scheduler and the existing
RiskCluePool persistence.  It does not create a new scanner or a second learning
engine.  It simply reorders already-materialized behavior slices using:

1. current coverage matrix gaps;
2. project/private-deployment learning weights from risk_clue_pool;
3. SaaS/platform sanitized learning weights from risk_clue_pool.
"""

import contextvars
import json
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
    "money": "money_quantity_conservation",
    "financial": "money_quantity_conservation",
    "quantity": "money_quantity_conservation",
    "concurrency": "concurrency_race_condition",
    "race": "concurrency_race_condition",
    "transition": "state_machine",
    "state_machine": "state_machine",
    "lifecycle": "state_machine",
    "invariant": "data_consistency",
    "dependency": "data_consistency",
    "source_observation": "visibility_disclosure",
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
    (("regression", "historical", "previous", "reopen"), "regression_historical_bug"),
)


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
        family = str(item.get("family") or "").strip()
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


def _slice_text(item: dict[str, Any]) -> str:
    fields: list[Any] = [
        item.get("risk_family"), item.get("family"), item.get("defect_family"),
        item.get("kind"), item.get("entity"), item.get("slice_id"), item.get("title"),
        item.get("description"), item.get("_selection_family"),
    ]
    fields.extend(item.get("states") if isinstance(item.get("states"), list) else [])
    fields.extend(item.get("endpoints") if isinstance(item.get("endpoints"), list) else [])
    for key, value in item.items():
        if str(key).startswith("_") and isinstance(value, (str, int, float)):
            fields.append(value)
    return " ".join(str(value or "") for value in fields).lower()


def _slice_risk_family(item: dict[str, Any]) -> str:
    for key in ("risk_family", "family", "defect_family"):
        value = str(item.get(key) or "").strip().lower().replace("-", "_").replace(" ", "_")
        if value:
            return value
    kind = str(item.get("kind") or "").strip().lower().replace("-", "_")
    if kind in _KIND_TO_RISK_FAMILY:
        return _KIND_TO_RISK_FAMILY[kind]
    text = _slice_text(item)
    for tokens, family in _TOKEN_TO_RISK_FAMILY:
        if any(token in text for token in tokens):
            return family
    return ""


def _slice_surfaces(item: dict[str, Any]) -> list[str]:
    text = _slice_text(item)
    surfaces: list[str] = []
    if any(token in text for token in ("api", "endpoint", "http", "/")):
        surfaces.append("api")
    if any(token in text for token in ("db", "table", "schema", "sql", "database")):
        surfaces.append("db")
    if any(token in text for token in ("ui", "page", "browser", "button", "form", "frontend")):
        surfaces.append("ui")
    if any(token in text for token in ("auth", "role", "permission", "tenant")):
        surfaces.append("auth")
    return sorted(set(surfaces))


def _learning_score(item: dict[str, Any], project_weights: dict[str, float], platform_weights: dict[str, float]) -> tuple[float, dict[str, Any]]:
    family = _slice_risk_family(item)
    surfaces = _slice_surfaces(item)
    project_score = float(project_weights.get(family) or 0.0) * 4.0
    platform_score = float(platform_weights.get(family) or 0.0) * 2.0
    for surface in surfaces:
        project_score += float(project_weights.get(f"surface:{surface}") or 0.0) * 0.8
        platform_score += float(platform_weights.get(f"surface:{surface}") or 0.0) * 0.4
    if len(surfaces) >= 2:
        combo = "+".join(surfaces)
        project_score += float(project_weights.get(f"surface_combo:{combo}") or 0.0) * 1.2
        platform_score += float(platform_weights.get(f"surface_combo:{combo}") or 0.0) * 0.6
    return min(project_score + platform_score, 35.0), {
        "family": family,
        "surfaces": surfaces,
        "project_learning_score": round(project_score, 3),
        "platform_learning_score": round(platform_score, 3),
    }


def _steer_slices(slices: list[dict[str, Any]], *, root: Path, project: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    coverage_weights = _coverage_gap_weights(root, project)
    project_weights, platform_weights = _learning_weights(root, project)
    if not coverage_weights and not project_weights and not platform_weights:
        return slices, {"status": "not_applied", "reason": "no_coverage_or_learning_weights"}

    indexed: list[tuple[float, int, dict[str, Any]]] = []
    coverage_count = 0
    learning_count = 0
    for index, raw in enumerate(slices):
        item = dict(raw) if isinstance(raw, dict) else {}
        family = _slice_risk_family(item)
        coverage_weight = float(coverage_weights.get(family, 0.0))
        if coverage_weight > 0:
            coverage_count += 1
            item["_coverage_steering_family"] = family
            item["_coverage_steering_weight"] = coverage_weight
            item["_coverage_steering_reason"] = "prioritize_current_coverage_matrix_gap"
        learning_weight, learning_detail = _learning_score(item, project_weights, platform_weights)
        if learning_weight > 0:
            learning_count += 1
            item["_learning_steering"] = learning_detail
            item["_learning_steering_weight"] = learning_weight
            item["_learning_steering_reason"] = "prioritize_from_project_and_platform_risk_clue_pool"
        total_weight = coverage_weight + learning_weight
        if total_weight > 0:
            item["priority"] = max(float(item.get("priority") or 0.0), 0.95 if coverage_weight >= 50 else 0.9 if learning_weight >= 10 else 0.86)
        indexed.append((total_weight, -index, item))

    indexed.sort(key=lambda row: (row[0], row[2].get("priority") or 0, row[1]), reverse=True)
    ordered = [item for _, _, item in indexed]
    return ordered, {
        "status": "applied" if (coverage_count or learning_count) else "not_applied",
        "reason": "coverage_and_learning_weights_prioritized" if (coverage_count and learning_count) else "coverage_gap_slices_prioritized" if coverage_count else "learning_weights_prioritized" if learning_count else "no_slice_matched_weights",
        "gap_family_weights": coverage_weights,
        "project_learning_weight_count": len(project_weights),
        "platform_learning_weight_count": len(platform_weights),
        "coverage_steered_slice_count": coverage_count,
        "learning_steered_slice_count": learning_count,
        "top_steered_slice_ids": [str(item.get("slice_id") or "") for item in ordered if item.get("_coverage_steering_weight") or item.get("_learning_steering_weight")][:12],
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
