from __future__ import annotations

"""Expose risk/invariant coverage matrix in the customer command center.

The scanner persists benchmark metrics after each run.  When seeded ground truth
is missing, those metrics still contain an honest ``coverage_matrix`` derived
from real findings/candidates.  This patch lifts that matrix into the command
center's top-level data contract so the frontend and customer API can render it
without treating it as benchmark recall.
"""

import json
from pathlib import Path
from typing import Any

PATCH_SOURCE = "ai_test_asset_center.private_pilot_coverage_matrix_patch"


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


def _project_from_payload(payload: dict[str, Any]) -> str:
    data = _as_dict(payload.get("data"))
    for value in (
        data.get("project_id"),
        data.get("project"),
        payload.get("project_id"),
        payload.get("project"),
    ):
        text = str(value or "").strip()
        if text:
            return _safe_project(text)
    return ""


def _coverage_summary(matrix: dict[str, Any]) -> dict[str, Any]:
    if not matrix:
        return {}
    families = matrix.get("families") if isinstance(matrix.get("families"), list) else []
    invariants = matrix.get("invariants") if isinstance(matrix.get("invariants"), list) else []
    gap_families = [row for row in families if isinstance(row, dict) and row.get("coverage_status") == "gap"]
    candidate_only = [row for row in families if isinstance(row, dict) and row.get("coverage_status") == "candidate_only"]
    confirmed = [row for row in families if isinstance(row, dict) and str(row.get("coverage_status") or "").startswith("confirmed")]
    return {
        "schema_version": str(matrix.get("schema_version") or ""),
        "ontology_family_count": int(matrix.get("ontology_family_count") or len(families) or 0),
        "ontology_invariant_count": int(matrix.get("ontology_invariant_count") or len(invariants) or 0),
        "covered_family_count": int(matrix.get("covered_family_count") or 0),
        "confirmed_family_count": int(matrix.get("confirmed_family_count") or len(confirmed) or 0),
        "family_coverage_rate": float(matrix.get("family_coverage_rate") or 0.0),
        "confirmed_family_rate": float(matrix.get("confirmed_family_rate") or 0.0),
        "candidate_only_family_count": len(candidate_only),
        "gap_family_count": len(gap_families),
        "unclassified_signal_count": int(matrix.get("unclassified_signal_count") or 0),
        "honesty_note": str(matrix.get("honesty_note") or "Coverage matrix is not bug recall unless benchmark ground truth is available."),
    }


def _legacy_family_coverage(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Return the older Dashboard-friendly risk_family_coverage map.

    The newer matrix stores rows as a list.  Some frontend code already expects a
    keyed map with coverage_rate / covered_items / total_items, so keep that
    compatibility while preserving the richer list form.
    """
    families = matrix.get("families") if isinstance(matrix.get("families"), list) else []
    result: dict[str, dict[str, Any]] = {}
    for row in families:
        if not isinstance(row, dict):
            continue
        family = str(row.get("family") or "").strip()
        if not family:
            continue
        status = str(row.get("coverage_status") or "gap")
        target = int(row.get("target_invariant_count") or row.get("ground_truth_total") or 1)
        touched = int(row.get("touched_invariant_count") or len(row.get("touched_invariants") or []) or 0)
        confirmed = int(row.get("confirmed_count") or 0)
        candidates = int(row.get("candidate_count") or 0)
        covered = max(confirmed + candidates, touched, 1 if status != "gap" else 0)
        total = max(target, covered, 1)
        result[family] = {
            "display_name": str(row.get("display_name") or family),
            "coverage_status": status,
            "coverage_rate": round(covered / total, 4) if total else 0.0,
            "execution_rate": round(confirmed / total, 4) if total else 0.0,
            "covered_items": covered,
            "total_items": total,
            "confirmed_count": confirmed,
            "candidate_count": candidates,
            "evidence_complete_count": int(row.get("evidence_complete_count") or 0),
        }
    return result


def _legacy_invariant_coverage(matrix: dict[str, Any]) -> dict[str, dict[str, Any]]:
    invariants = matrix.get("invariants") if isinstance(matrix.get("invariants"), list) else []
    result: dict[str, dict[str, Any]] = {}
    for row in invariants:
        if not isinstance(row, dict):
            continue
        invariant = str(row.get("invariant") or "").strip()
        if not invariant:
            continue
        status = str(row.get("coverage_status") or "gap")
        confirmed = int(row.get("confirmed_count") or 0)
        candidates = int(row.get("candidate_count") or 0)
        covered = max(confirmed + candidates, 1 if status != "gap" else 0)
        result[invariant] = {
            "family": str(row.get("family") or ""),
            "coverage_status": status,
            "coverage_rate": 1.0 if covered else 0.0,
            "covered_items": covered,
            "total_items": 1,
            "confirmed_count": confirmed,
            "candidate_count": candidates,
            "evidence_complete_count": int(row.get("evidence_complete_count") or 0),
        }
    return result


def _coverage_gap_items(matrix: dict[str, Any]) -> list[dict[str, Any]]:
    gaps: list[dict[str, Any]] = []
    families = matrix.get("families") if isinstance(matrix.get("families"), list) else []
    for row in families:
        if not isinstance(row, dict) or row.get("coverage_status") != "gap":
            continue
        family = str(row.get("family") or "").strip()
        if not family:
            continue
        gaps.append({
            "kind": "RISK_FAMILY_COVERAGE_GAP",
            "code": family.upper(),
            "family": family,
            "title": str(row.get("display_name") or family),
            "reason": "该风险家族当前没有真实 confirmed 或 candidate 覆盖，不代表系统无风险。",
            "next_action": "补充对应业务资料、接口、角色账号、测试数据或执行授权后重新运行 Campaign。",
        })
    return gaps


def _behavior_slice_seed_contract(matrix: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    families = [row for row in (matrix.get("families") if isinstance(matrix.get("families"), list) else []) if isinstance(row, dict)]
    invariants = [row for row in (matrix.get("invariants") if isinstance(matrix.get("invariants"), list) else []) if isinstance(row, dict)]
    actionable_families = [row for row in families if str(row.get("coverage_status") or "") in {"gap", "candidate_only"}]
    confirmed_families = [row for row in families if str(row.get("coverage_status") or "").startswith("confirmed")]
    can_seed = bool(families or invariants)
    missing_inputs: list[str] = []
    if not families:
        missing_inputs.append("risk_family_rows")
    if not invariants:
        missing_inputs.append("business_invariant_rows")
    return {
        "status": "ready" if can_seed else "insufficient_coverage_matrix",
        "source": "coverage_matrix",
        "can_seed_behavior_slices": can_seed,
        "seed_family_count": len(families),
        "seed_invariant_count": len(invariants),
        "gap_or_candidate_family_count": len(actionable_families),
        "confirmed_family_count": len(confirmed_families),
        "priority_families": [str(row.get("family") or "") for row in actionable_families[:8] if str(row.get("family") or "").strip()],
        "seed_dimensions": ["risk_family", "business_invariant", "coverage_status", "confirmed_count", "candidate_count", "evidence_complete_count"],
        "required_missing_inputs": missing_inputs,
        "next_planner_step": "Use existing coverage_matrix families/invariants to prioritize the next behavior slice batch; do not create synthetic findings or claim recall without ground truth.",
        "honesty_rule": summary.get("honesty_note") or "Coverage matrix can seed behavior slices, but it is not full system understanding and not benchmark recall without ground truth.",
    }


def _normalize_matrix_for_dashboard(matrix: dict[str, Any], summary: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(matrix)
    normalized.setdefault("summary", {
        "risk_family_count": summary.get("ontology_family_count", 0),
        "invariant_count": summary.get("ontology_invariant_count", 0),
        "covered_family_count": summary.get("covered_family_count", 0),
        "confirmed_family_count": summary.get("confirmed_family_count", 0),
        "family_coverage_rate": summary.get("family_coverage_rate", 0.0),
        "confirmed_family_rate": summary.get("confirmed_family_rate", 0.0),
    })
    normalized.setdefault("risk_family_coverage", _legacy_family_coverage(normalized))
    normalized.setdefault("invariant_coverage", _legacy_invariant_coverage(normalized))
    normalized.setdefault("behavior_slice_seed_contract", _behavior_slice_seed_contract(normalized, summary))
    return normalized


def _evidence_classification(data: dict[str, Any]) -> dict[str, int]:
    defects = data.get("defects") if isinstance(data.get("defects"), list) else []
    clues = data.get("clues") if isinstance(data.get("clues"), list) else []
    risks = data.get("risks") if isinstance(data.get("risks"), list) else []
    return {
        "confirmed": len([item for item in defects if isinstance(item, dict)]),
        "candidate": len([item for item in risks if isinstance(item, dict)]) + len([item for item in clues if isinstance(item, dict)]),
        "clue": len([item for item in clues if isinstance(item, dict)]),
    }


def _load_benchmark_metrics(project: str, root: Path) -> dict[str, Any]:
    if not project:
        return {}
    return _read_json(root / "platform_outputs" / _safe_project(project) / "benchmark" / "benchmark_metrics.json")


def _load_scan_result(project: str, root: Path) -> dict[str, Any]:
    if not project:
        return {}
    from .scan_result_store import load_scan_result

    return load_scan_result(
        root / "platform_outputs" / _safe_project(project) / "scan_result.json",
        keys=[
            "coverage_steering", "behavior_slice_ledger", "phases",
            "v12.coverage_steering", "v12.behavior_slice_ledger", "v12.phases",
            "benchmark_metrics", "pipeline_health", "discovery_funnel",
        ],
    )


def _coverage_steering_from(data: dict[str, Any], scan_result: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        _as_dict(data.get("coverage_steering")),
        _as_dict(_as_dict(data.get("scan_meta")).get("coverage_steering")),
        _as_dict(scan_result.get("coverage_steering")),
        _as_dict(_as_dict(scan_result.get("behavior_slice_ledger")).get("coverage_steering")),
        _as_dict(_as_dict(_as_dict(scan_result.get("phases")).get("incremental_discovery")).get("coverage_steering")),
        _as_dict(_as_dict(scan_result.get("v12")).get("coverage_steering")),
        _as_dict(_as_dict(_as_dict(scan_result.get("v12")).get("behavior_slice_ledger")).get("coverage_steering")),
    ]
    for item in candidates:
        if item and str(item.get("status") or item.get("reason") or "").strip():
            return item
    return {}


def _inject_coverage_steering(data: dict[str, Any], steering: dict[str, Any]) -> None:
    if not steering:
        return
    payload = dict(steering)
    payload.setdefault("honesty_rule", "Coverage steering only reorders existing source-grounded behavior slices; it does not create findings or synthetic coverage.")
    data["coverage_steering"] = payload

    scan_meta = _as_dict(data.get("scan_meta"))
    scan_meta["coverage_steering"] = payload
    data["scan_meta"] = scan_meta

    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics["coverage_steering_status"] = str(payload.get("status") or "")
    value_metrics["coverage_steered_slice_count"] = int(payload.get("steered_slice_count") or 0)
    data["value_metrics"] = value_metrics

    executive = _as_dict(data.get("executive_summary"))
    status = str(payload.get("status") or "")
    steered = int(payload.get("steered_slice_count") or 0)
    if status == "applied":
        executive["coverage_steering_label"] = f"已按覆盖缺口优先调度 {steered} 个行为 slice"
    elif status:
        executive["coverage_steering_label"] = f"覆盖调度未启用：{payload.get('reason') or status}"
    data["executive_summary"] = executive

    contract = _as_dict(data.get("data_contract"))
    contract["coverage_steering"] = {
        "display_key": "coverage_steering",
        "source": "platform_outputs/<project>/scan_result.json:coverage_steering",
        "honesty_rule": payload["honesty_rule"],
        "customer_meaning": "Explains whether the next behavior-slice batch was reordered to close current risk-family coverage gaps.",
    }
    data["data_contract"] = contract


def _regression_suite_refresh_from(data: dict[str, Any], scan_result: dict[str, Any]) -> dict[str, Any]:
    candidates = [
        _as_dict(data.get("regression_suite_refresh")),
        _as_dict(_as_dict(data.get("scan_meta")).get("regression_suite_refresh")),
        _as_dict(scan_result.get("regression_suite_refresh")),
    ]
    for item in candidates:
        if item and str(item.get("status") or item.get("reason") or "").strip():
            return item
    return {}


def _inject_regression_suite_refresh(data: dict[str, Any], refresh: dict[str, Any]) -> None:
    if not refresh:
        return
    payload = dict(refresh)
    payload.setdefault("honesty_rule", "Regression suite refresh only materializes probes from existing evidence-backed sources; it does not execute regression or claim the fix passed.")
    summary = _as_dict(payload.get("summary"))
    data["regression_suite_refresh"] = payload
    data["regression_suite"] = _as_dict(data.get("regression_suite")) or {
        "ref": str(payload.get("suite_ref") or ""),
        "total_probe_count": int(summary.get("total_probe_count") or 0),
        "smoke_count": int(summary.get("smoke_count") or 0),
        "release_count": int(summary.get("release_count") or 0),
        "full_count": int(summary.get("full_count") or 0),
        "confirmed_ledger_probe_count": int(summary.get("confirmed_ledger_probe_count") or 0),
        "ci_gate_recommendation": str(summary.get("ci_gate_recommendation") or ""),
    }

    scan_meta = _as_dict(data.get("scan_meta"))
    scan_meta["regression_suite_refresh"] = payload
    scan_meta["regression_suite_probe_count"] = int(summary.get("total_probe_count") or 0)
    scan_meta["confirmed_ledger_regression_probe_count"] = int(summary.get("confirmed_ledger_probe_count") or 0)
    data["scan_meta"] = scan_meta

    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics["regression_suite_refresh_status"] = str(payload.get("status") or "")
    value_metrics["regression_suite_probe_count"] = int(summary.get("total_probe_count") or 0)
    value_metrics["confirmed_ledger_regression_probe_count"] = int(summary.get("confirmed_ledger_probe_count") or 0)
    data["value_metrics"] = value_metrics

    executive = _as_dict(data.get("executive_summary"))
    status = str(payload.get("status") or "")
    if status == "refreshed":
        executive["regression_suite_refresh_label"] = f"已自动刷新 {int(summary.get('total_probe_count') or 0)} 个回归探针"
    elif status:
        executive["regression_suite_refresh_label"] = f"回归套件未刷新：{payload.get('reason') or status}"
    data["executive_summary"] = executive

    contract = _as_dict(data.get("data_contract"))
    contract["regression_suite_refresh"] = {
        "display_key": "regression_suite_refresh",
        "source": "platform_outputs/<project>/scan_result.json:regression_suite_refresh",
        "honesty_rule": payload["honesty_rule"],
        "customer_meaning": "Explains whether confirmed findings were materialized into the smoke/release/full regression suite after the scan.",
    }
    data["data_contract"] = contract


def inject_coverage_matrix(payload: dict[str, Any], *, root: Path | None = None) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    data = payload.get("data") if isinstance(payload.get("data"), dict) else None
    if data is None:
        return payload

    project = _project_from_payload(payload)
    if not project:
        project = _project_from_payload({"data": data})
    resolved_root = Path(root or Path.cwd())
    scan_result = _load_scan_result(project, resolved_root)
    steering = _coverage_steering_from(data, scan_result)
    _inject_coverage_steering(data, steering)
    refresh = _regression_suite_refresh_from(data, scan_result)
    _inject_regression_suite_refresh(data, refresh)

    scan_meta = _as_dict(data.get("scan_meta"))
    benchmark_metrics = _as_dict(scan_meta.get("benchmark_metrics"))
    if not benchmark_metrics:
        benchmark_metrics = _load_benchmark_metrics(project, resolved_root)
        if benchmark_metrics:
            scan_meta["benchmark_metrics"] = benchmark_metrics
            data["scan_meta"] = scan_meta

    matrix = _as_dict(data.get("coverage_matrix")) or _as_dict(benchmark_metrics.get("coverage_matrix"))
    if not matrix:
        payload["data"] = data
        return payload

    summary = _coverage_summary(matrix)
    dashboard_matrix = _normalize_matrix_for_dashboard(matrix, summary)
    behavior_seed = _as_dict(dashboard_matrix.get("behavior_slice_seed_contract"))
    data["coverage_matrix"] = dashboard_matrix
    data["coverage_matrix_summary"] = summary
    data["behavior_slice_seed_contract"] = behavior_seed
    data["evidence_classification"] = _evidence_classification(data)

    # Surface matrix gaps through the existing Dashboard coverage-gap UI.  Do not
    # overwrite backend execution gaps; only append missing risk-family gaps.
    existing_gaps = data.get("coverage_gaps") if isinstance(data.get("coverage_gaps"), list) else []
    existing_codes = {str(item.get("code") or item.get("family") or "") for item in existing_gaps if isinstance(item, dict)}
    appended_gaps = [item for item in _coverage_gap_items(dashboard_matrix) if str(item.get("code") or item.get("family") or "") not in existing_codes]
    data["coverage_gaps"] = list(existing_gaps) + appended_gaps

    value_metrics = _as_dict(data.get("value_metrics"))
    value_metrics["coverage_matrix_summary"] = summary
    value_metrics["risk_invariant_coverage_rate"] = summary.get("family_coverage_rate", 0.0)
    value_metrics["risk_invariant_confirmed_rate"] = summary.get("confirmed_family_rate", 0.0)
    value_metrics["risk_family_gap_count"] = summary.get("gap_family_count", 0)
    value_metrics["behavior_slice_seed_status"] = str(behavior_seed.get("status") or "")
    value_metrics["behavior_slice_seed_family_count"] = int(behavior_seed.get("seed_family_count") or 0)
    value_metrics["behavior_slice_seed_invariant_count"] = int(behavior_seed.get("seed_invariant_count") or 0)
    data["value_metrics"] = value_metrics

    executive = _as_dict(data.get("executive_summary"))
    executive["coverage_matrix_summary"] = summary
    executive["risk_invariant_coverage_label"] = (
        f"风险家族覆盖 {round(float(summary.get('family_coverage_rate') or 0) * 100)}%，"
        f"确认覆盖 {round(float(summary.get('confirmed_family_rate') or 0) * 100)}%"
    )
    executive["risk_family_gap_count"] = summary.get("gap_family_count", 0)
    if behavior_seed:
        executive["behavior_slice_seed_label"] = f"可用覆盖矩阵种子：{behavior_seed.get('seed_family_count', 0)} 个风险家族 / {behavior_seed.get('seed_invariant_count', 0)} 个业务不变量"
    data["executive_summary"] = executive

    contract = _as_dict(data.get("data_contract"))
    contract["coverage_matrix"] = {
        "display_key": "coverage_matrix",
        "summary_key": "coverage_matrix_summary",
        "source": "platform_outputs/<project>/benchmark/benchmark_metrics.json:coverage_matrix",
        "frontend_compatibility_keys": ["risk_family_coverage", "invariant_coverage", "summary", "behavior_slice_seed_contract"],
        "honesty_rule": "Coverage matrix is risk/invariant coverage from scan outputs; it is not recall unless benchmark_active and ground_truth_available are true.",
    }
    contract["behavior_slice_seed_contract"] = {
        "display_key": "behavior_slice_seed_contract",
        "source": "coverage_matrix.families + coverage_matrix.invariants",
        "honesty_rule": str(behavior_seed.get("honesty_rule") or "Coverage matrix can seed behavior slices, but it is not full system understanding."),
        "customer_meaning": "Uses the existing risk/invariant coverage matrix to prioritize the next target-driven behavior slice batch.",
    }
    data["data_contract"] = contract
    payload["data"] = data
    return payload


def install_coverage_matrix_patch(*, patch_source: str = PATCH_SOURCE, root: Path | None = None) -> None:
    from ai_test_asset_center import private_pilot_service as service
    from ai_test_asset_center.private_pilot_command_center_envelope import register_envelope_post_hook

    if getattr(service, "_COVERAGE_MATRIX_PATCHED", False):
        return

    def _coverage_matrix_hook(payload: dict[str, Any]) -> dict[str, Any]:
        try:
            return inject_coverage_matrix(payload, root=root or service._root())
        except Exception:
            return payload

    register_envelope_post_hook("coverage_matrix", _coverage_matrix_hook)
    service._COVERAGE_MATRIX_PATCHED = True  # type: ignore[attr-defined]
    service._COVERAGE_MATRIX_PATCH_SOURCE = patch_source  # type: ignore[attr-defined]
    service._COVERAGE_MATRIX_MODE = "first_class_hook"  # type: ignore[attr-defined]


def restore_coverage_matrix_patch() -> None:
    from ai_test_asset_center import private_pilot_service as service
    from ai_test_asset_center.private_pilot_command_center_envelope import register_envelope_post_hook

    register_envelope_post_hook("coverage_matrix", None)
    service._COVERAGE_MATRIX_PATCHED = False  # type: ignore[attr-defined]
    if hasattr(service, "_COVERAGE_MATRIX_MODE"):
        delattr(service, "_COVERAGE_MATRIX_MODE")
