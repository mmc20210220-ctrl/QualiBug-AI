from __future__ import annotations

"""Coverage-gap steering for existing V12 behavior-slice scheduling.

This patch does not create a new scanner.  It wraps the existing
``v12_pipeline._schedule_behavior_slices`` function and reorders the already
materialized behavior slices so uncovered risk families are attempted earlier in
later rounds.

Why this exists:
- the coverage matrix tells the customer which risk families are gaps;
- without feedback into scheduling, the product only reports gaps after the fact;
- this patch closes that loop by steering the next slice batch toward the gaps.

It is deliberately conservative:
- no synthetic findings;
- no new source documents;
- no new execution semantics;
- if no coverage matrix exists, the original scheduler is untouched.
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


def _steering_weights(root: Path, project: str) -> dict[str, int]:
    matrix = _coverage_matrix(root, project)
    rows = matrix.get("families") if isinstance(matrix.get("families"), list) else []
    weights: dict[str, int] = {}
    for row in rows:
        item = _as_dict(row)
        family = str(item.get("family") or "").strip()
        status = str(item.get("coverage_status") or "").strip()
        if family and status in _FAMILY_WEIGHT:
            weights[family] = max(weights.get(family, 0), _FAMILY_WEIGHT[status])
    return weights


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


def _steer_slices_by_coverage_gap(slices: list[dict[str, Any]], *, root: Path, project: str) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    weights = _steering_weights(root, project)
    if not weights:
        return slices, {"status": "not_applied", "reason": "coverage_matrix_without_actionable_gaps"}

    indexed: list[tuple[int, int, dict[str, Any]]] = []
    steered_count = 0
    for index, raw in enumerate(slices):
        item = dict(raw) if isinstance(raw, dict) else {}
        family = _slice_risk_family(item)
        weight = int(weights.get(family, 0))
        if weight > 0:
            steered_count += 1
            item["_coverage_steering_family"] = family
            item["_coverage_steering_weight"] = weight
            item["_coverage_steering_reason"] = "prioritize_current_coverage_matrix_gap"
            item["priority"] = max(float(item.get("priority") or 0.0), 0.95 if weight >= 50 else 0.88)
        indexed.append((weight, -index, item))

    indexed.sort(key=lambda row: (row[0], row[2].get("priority") or 0, row[1]), reverse=True)
    ordered = [item for _, _, item in indexed]
    return ordered, {
        "status": "applied" if steered_count else "not_applied",
        "reason": "gap_family_slices_prioritized" if steered_count else "no_slice_matched_gap_family",
        "gap_family_weights": weights,
        "steered_slice_count": steered_count,
        "top_steered_slice_ids": [str(item.get("slice_id") or "") for item in ordered if item.get("_coverage_steering_weight")][:12],
    }


def install_coverage_steering_patch(*, patch_source: str = PATCH_SOURCE) -> None:
    from ai_test_asset_center import v12_pipeline

    if getattr(v12_pipeline, "_COVERAGE_STEERING_PATCHED", False):
        return

    original_run = getattr(v12_pipeline, "run_v12_pipeline")
    original_schedule = getattr(v12_pipeline, "_schedule_behavior_slices")

    def _run_with_coverage_steering(project: str, root: Path, *args: Any, **kwargs: Any) -> dict[str, Any]:
        token = _COVERAGE_STEERING_CONTEXT.set({"project": str(project), "root": Path(root)})
        try:
            result = original_run(project, root, *args, **kwargs)
        finally:
            _COVERAGE_STEERING_CONTEXT.reset(token)
        return result

    def _schedule_with_coverage_steering(slices: list[dict[str, Any]], settings: dict[str, int], history: list[dict[str, Any]] | None) -> dict[str, Any]:
        context = _COVERAGE_STEERING_CONTEXT.get() or {}
        project = str(context.get("project") or "").strip()
        root = Path(context.get("root") or Path.cwd())
        diagnostic: dict[str, Any] = {"status": "not_applied", "reason": "missing_project_context"}
        steered_slices = slices
        if project:
            steered_slices, diagnostic = _steer_slices_by_coverage_gap(slices, root=root, project=project)
        selection = original_schedule(steered_slices, settings, history)
        if isinstance(selection, dict):
            selection["coverage_steering"] = diagnostic
            if diagnostic.get("status") == "applied":
                mode = str(selection.get("selection_mode") or "")
                if "coverage_gap_steered" not in mode:
                    selection["selection_mode"] = f"{mode}+coverage_gap_steered" if mode else "coverage_gap_steered"
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
