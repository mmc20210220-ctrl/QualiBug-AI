"""Per-scan benchmark and invariant-coverage metrics computation.

This module bridges the scan pipeline with the benchmark evaluator so that
after every scan the system can compute recall, precision, FPR, FNR, evidence
completeness, reproduction success rate, and regression success rate — but
ONLY when a ground truth file exists.

When ground truth is not available, this module still returns a non-benchmark
coverage matrix derived from real findings/candidates.  That matrix is not a
fabricated recall number; it is an honest product-facing map of which risk
families and business invariants were touched by the current scan, which were
confirmed, and which remain gaps.  This prevents the product from being trapped
at a fixed "20 bug types" ceiling while still avoiding fake 99% claims.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any


_RISK_FAMILY_ONTOLOGY: dict[str, dict[str, Any]] = {
    "authorization_access_control": {
        "display_name": "权限与访问控制",
        "aliases": ("auth", "permission", "role", "forbidden", "401", "403", "unauthorized", "access", "admin", "readonly", "越权", "权限", "角色"),
        "invariants": (
            "actor_must_be_authenticated",
            "actor_must_have_required_role",
            "read_only_actor_cannot_write",
            "disabled_actor_cannot_access",
            "frontend_visibility_must_not_replace_backend_authz",
        ),
    },
    "tenant_isolation": {
        "display_name": "租户/组织/数据隔离",
        "aliases": ("tenant", "org", "workspace", "company", "isolation", "cross-tenant", "租户", "组织", "隔离", "跨租户"),
        "invariants": (
            "tenant_id_must_match_actor_scope",
            "cross_tenant_object_access_must_be_rejected",
            "tenant_scoped_search_must_not_leak_other_tenants",
            "tenant_scoped_export_must_not_leak_other_tenants",
        ),
    },
    "state_machine": {
        "display_name": "状态机与生命周期",
        "aliases": ("state", "status", "transition", "lifecycle", "cancel", "close", "reopen", "refund", "状态", "流转", "生命周期"),
        "invariants": (
            "object_must_follow_allowed_state_transition",
            "terminal_state_must_not_accept_mutation",
            "state_transition_side_effects_must_be_consistent",
            "invalid_transition_must_be_rejected",
        ),
    },
    "money_quantity_conservation": {
        "display_name": "金额/数量/库存/积分守恒",
        "aliases": ("money", "amount", "price", "balance", "stock", "inventory", "quantity", "points", "coupon", "payment", "refund", "金额", "余额", "库存", "积分", "优惠券", "支付", "退款"),
        "invariants": (
            "money_must_not_be_created_or_lost",
            "refund_amount_must_not_exceed_paid_amount",
            "inventory_must_not_go_negative",
            "discount_must_not_exceed_policy",
            "ledger_must_balance_with_business_state",
        ),
    },
    "idempotency_duplicate_submit": {
        "display_name": "幂等与重复提交",
        "aliases": ("idempot", "duplicate", "replay", "retry", "double", "重复", "幂等", "重放"),
        "invariants": (
            "duplicate_request_must_not_create_duplicate_side_effects",
            "retry_must_return_consistent_business_result",
            "idempotency_key_must_bind_to_original_operation",
        ),
    },
    "concurrency_race_condition": {
        "display_name": "并发竞态",
        "aliases": ("concurrent", "race", "parallel", "lock", "atomic", "并发", "竞态", "锁", "原子"),
        "invariants": (
            "concurrent_mutations_must_preserve_invariants",
            "unique_resource_must_not_be_double_allocated",
            "balance_or_stock_update_must_be_atomic",
        ),
    },
    "data_consistency": {
        "display_name": "数据一致性",
        "aliases": ("consistency", "database", "db", "before_after", "snapshot", "mismatch", "drift", "一致", "数据库", "前后", "漂移"),
        "invariants": (
            "api_response_must_match_persisted_state",
            "master_detail_records_must_be_consistent",
            "cache_view_must_eventually_match_database",
            "derived_counts_must_match_source_records",
        ),
    },
    "input_validation_boundary": {
        "display_name": "输入校验与边界",
        "aliases": ("validation", "boundary", "invalid", "null", "empty", "overflow", "unicode", "injection", "xss", "sql", "边界", "非法", "空值", "注入"),
        "invariants": (
            "invalid_input_must_be_rejected",
            "numeric_bounds_must_be_enforced",
            "string_and_file_limits_must_be_enforced",
            "structured_input_must_not_escape_parser_or_storage",
        ),
    },
    "visibility_disclosure": {
        "display_name": "可见性与数据泄露",
        "aliases": ("visibility", "disclosure", "leak", "exposure", "export", "search", "list", "visible", "泄露", "可见", "导出", "搜索"),
        "invariants": (
            "list_detail_export_visibility_must_be_consistent",
            "hidden_object_must_not_be_discoverable_by_search",
            "sensitive_fields_must_be_masked_or_omitted",
        ),
    },
    "workflow_approval": {
        "display_name": "审批流/工作流",
        "aliases": ("workflow", "approval", "approve", "reject", "review", "handoff", "审批", "工作流", "驳回", "复核"),
        "invariants": (
            "approval_step_must_be_executed_by_authorized_actor",
            "rejected_workflow_must_not_continue_as_approved",
            "required_approval_chain_must_not_be_skipped",
        ),
    },
    "async_eventual_consistency": {
        "display_name": "异步任务/消息/回调",
        "aliases": ("async", "eventual", "queue", "message", "callback", "webhook", "job", "task", "cron", "异步", "消息", "回调", "任务"),
        "invariants": (
            "async_side_effect_must_eventually_complete",
            "message_must_not_be_lost_or_double_consumed",
            "callback_replay_must_be_idempotent",
            "failed_async_task_must_be_observable_and_retryable",
        ),
    },
    "cache_stale_state": {
        "display_name": "缓存与状态漂移",
        "aliases": ("cache", "stale", "ttl", "redis", "缓存", "过期", "脏读"),
        "invariants": (
            "cache_must_not_serve_forbidden_or_deleted_state",
            "cache_invalidation_must_follow_write_side_effect",
            "stale_read_must_not_violate_business_decision",
        ),
    },
    "audit_traceability": {
        "display_name": "审计与可追踪性",
        "aliases": ("audit", "trace", "log", "receipt", "ledger", "审计", "日志", "追踪", "流水"),
        "invariants": (
            "sensitive_action_must_create_audit_event",
            "audit_event_must_bind_actor_action_target_and_time",
            "audit_trail_must_not_expose_secrets",
        ),
    },
    "configuration_environment": {
        "display_name": "配置/环境/发布风险",
        "aliases": ("config", "environment", "deploy", "release", "feature flag", "secret", "配置", "环境", "部署", "发布"),
        "invariants": (
            "environment_boundary_must_be_explicit",
            "secret_must_not_be_rendered_or_logged",
            "feature_flag_must_not_bypass_safety_gate",
        ),
    },
    "ui_api_contract_drift": {
        "display_name": "UI/API 契约漂移",
        "aliases": ("ui", "frontend", "page", "button", "form", "api contract", "contract", "页面", "按钮", "表单", "契约"),
        "invariants": (
            "ui_action_must_call_expected_api_contract",
            "api_success_must_be_visible_in_ui_state",
            "ui_error_state_must_match_api_failure",
        ),
    },
    "regression_historical_bug": {
        "display_name": "历史 bug 回归",
        "aliases": ("regression", "historical", "previous bug", "reopen", "回归", "历史缺陷", "复发"),
        "invariants": (
            "historical_bug_probe_must_remain_passing_after_fix",
            "resolved_defect_must_not_reappear_in_same_scope",
            "regression_suite_must_track_customer_ready_defects",
        ),
    },
}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8") or "null")
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _safe_float(value: Any, fallback: float = 0.0) -> float:
    try:
        v = float(value)
        return v if v == v else fallback  # NaN guard
    except (TypeError, ValueError):
        return fallback


def _method_path_key(finding: dict[str, Any]) -> tuple[str, str]:
    """Extract a stable (method, path) key from a finding for matching."""
    method = str(finding.get("method") or finding.get("_api_method") or "").upper().strip()
    path = str(finding.get("path") or finding.get("_api_path") or "").strip().rstrip("/")
    # Normalize path params
    path = re.sub(r"/\d+", "/{id}", path)
    path = re.sub(r"/\{[^}]+\}", "/{id}", path)
    return (method, path)


def _text_blob(item: dict[str, Any]) -> str:
    fields = [
        item.get("risk_family"), item.get("family"), item.get("defect_family"),
        item.get("risk_type"), item.get("category"), item.get("type"),
        item.get("title"), item.get("summary"), item.get("description"),
        item.get("expected"), item.get("actual"), item.get("path"), item.get("_api_path"),
    ]
    raw_evidence = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    request_raw = raw_evidence.get("request_raw") if isinstance(raw_evidence.get("request_raw"), dict) else {}
    response_raw = raw_evidence.get("response_raw") if isinstance(raw_evidence.get("response_raw"), dict) else {}
    fields.extend([request_raw.get("path"), request_raw.get("method"), response_raw.get("status_code")])
    return " ".join(str(v or "") for v in fields).lower()


def _explicit_family(item: dict[str, Any]) -> str:
    for key in ("risk_family", "family", "defect_family", "risk_type", "category", "type"):
        value = str(item.get(key) or "").strip().lower()
        if value in _RISK_FAMILY_ONTOLOGY:
            return value
        normalized = value.replace("-", "_").replace(" ", "_")
        if normalized in _RISK_FAMILY_ONTOLOGY:
            return normalized
    return ""


def _risk_family_for_item(item: dict[str, Any]) -> str:
    explicit = _explicit_family(item)
    if explicit:
        return explicit
    blob = _text_blob(item)
    best_family = ""
    best_hits = 0
    for family, spec in _RISK_FAMILY_ONTOLOGY.items():
        hits = 0
        for alias in spec.get("aliases", ()):
            token = str(alias or "").lower().strip()
            if token and token in blob:
                hits += 1
        if hits > best_hits:
            best_family, best_hits = family, hits
    return best_family or "unclassified"


def _invariants_for_item(item: dict[str, Any], family: str) -> list[str]:
    explicit = item.get("invariant") or item.get("business_invariant") or item.get("oracle")
    if isinstance(explicit, str) and explicit.strip():
        return [explicit.strip()[:160]]
    if isinstance(explicit, list):
        values = [str(v).strip()[:160] for v in explicit if str(v).strip()]
        if values:
            return values[:8]
    spec = _RISK_FAMILY_ONTOLOGY.get(family) or {}
    invariants = [str(v) for v in spec.get("invariants", ()) if str(v)]
    return invariants[:3] if invariants else ["unclassified_invariant"]


def _evidence_profile(item: dict[str, Any]) -> dict[str, bool]:
    raw = item.get("raw_evidence") if isinstance(item.get("raw_evidence"), dict) else {}
    reproduction = item.get("reproduction") if isinstance(item.get("reproduction"), dict) else {}
    db_evidence = item.get("db_evidence") if isinstance(item.get("db_evidence"), dict) else {}
    request_raw = raw.get("request_raw") if isinstance(raw.get("request_raw"), dict) else {}
    response_raw = raw.get("response_raw") if isinstance(raw.get("response_raw"), dict) else {}
    ui_result = raw.get("ui_execution_result") if isinstance(raw.get("ui_execution_result"), dict) else {}
    return {
        "has_request": bool(item.get("request") or request_raw or item.get("_api_path") or item.get("path")),
        "has_response": bool(item.get("response") or response_raw or reproduction.get("har_evidence")),
        "has_assertion": bool(item.get("expected") and item.get("actual")) or bool(item.get("assertion") or item.get("oracle_result")),
        "has_db_evidence": bool(db_evidence and db_evidence.get("status") == "captured"),
        "has_ui_evidence": bool(ui_result or item.get("har_evidence") or item.get("ui_verification")),
        "has_regression_probe": bool(item.get("regression") or item.get("regression_probe") or item.get("regression_suggestions")),
    }


def _is_confirmed(item: dict[str, Any]) -> bool:
    status = str(item.get("confirmation_status") or item.get("bug_status") or "").strip().lower()
    if status in {"confirmed", "validated", "validated_candidate", "reproduced"}:
        return True
    if item.get("customer_delivery_status") == "defect" and item.get("gate_passed"):
        return True
    return False


def _coverage_matrix(
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    *,
    truth: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build an honest risk-family and invariant coverage matrix.

    This is not benchmark recall unless a ground-truth set is supplied.  It is a
    product coverage view derived from real scan outputs, so it is safe to show
    even for arbitrary customer projects.
    """
    all_items = [f for f in list(findings or []) + list(candidates or []) if isinstance(f, dict)]
    family_rows: dict[str, dict[str, Any]] = {}
    invariant_rows: dict[str, dict[str, Any]] = {}

    for family, spec in _RISK_FAMILY_ONTOLOGY.items():
        family_rows[family] = {
            "family": family,
            "display_name": spec.get("display_name") or family,
            "target_invariant_count": len(spec.get("invariants", ()) or ()),
            "confirmed_count": 0,
            "candidate_count": 0,
            "evidence_complete_count": 0,
            "touched_invariants": [],
            "coverage_status": "gap",
        }
        for invariant in spec.get("invariants", ()) or ():
            invariant_rows[str(invariant)] = {
                "invariant": str(invariant),
                "family": family,
                "confirmed_count": 0,
                "candidate_count": 0,
                "evidence_complete_count": 0,
                "coverage_status": "gap",
            }

    unclassified_count = 0
    for item in all_items:
        family = _risk_family_for_item(item)
        if family == "unclassified":
            unclassified_count += 1
            continue
        row = family_rows.setdefault(family, {
            "family": family,
            "display_name": family,
            "target_invariant_count": 0,
            "confirmed_count": 0,
            "candidate_count": 0,
            "evidence_complete_count": 0,
            "touched_invariants": [],
            "coverage_status": "gap",
        })
        confirmed = _is_confirmed(item)
        if confirmed:
            row["confirmed_count"] += 1
        else:
            row["candidate_count"] += 1
        evidence = _evidence_profile(item)
        evidence_complete = evidence["has_request"] and evidence["has_response"] and evidence["has_assertion"]
        if evidence_complete:
            row["evidence_complete_count"] += 1
        for invariant in _invariants_for_item(item, family):
            if invariant not in row["touched_invariants"]:
                row["touched_invariants"].append(invariant)
            inv_row = invariant_rows.setdefault(invariant, {
                "invariant": invariant,
                "family": family,
                "confirmed_count": 0,
                "candidate_count": 0,
                "evidence_complete_count": 0,
                "coverage_status": "gap",
            })
            if confirmed:
                inv_row["confirmed_count"] += 1
            else:
                inv_row["candidate_count"] += 1
            if evidence_complete:
                inv_row["evidence_complete_count"] += 1

    for row in family_rows.values():
        if row["confirmed_count"] and row["evidence_complete_count"]:
            row["coverage_status"] = "confirmed_with_evidence"
        elif row["confirmed_count"]:
            row["coverage_status"] = "confirmed_needs_evidence"
        elif row["candidate_count"]:
            row["coverage_status"] = "candidate_only"
        row["touched_invariant_count"] = len(row.get("touched_invariants") or [])
        row["touched_invariants"] = list(row.get("touched_invariants") or [])[:12]

    for row in invariant_rows.values():
        if row["confirmed_count"] and row["evidence_complete_count"]:
            row["coverage_status"] = "confirmed_with_evidence"
        elif row["confirmed_count"]:
            row["coverage_status"] = "confirmed_needs_evidence"
        elif row["candidate_count"]:
            row["coverage_status"] = "candidate_only"

    truth_family_totals: dict[str, int] = {}
    for bug in truth or []:
        if not isinstance(bug, dict):
            continue
        family = _risk_family_for_item(bug)
        if family != "unclassified":
            truth_family_totals[family] = truth_family_totals.get(family, 0) + 1
    for family, count in truth_family_totals.items():
        family_rows.setdefault(family, {"family": family, "display_name": family})["ground_truth_total"] = count

    rows = sorted(family_rows.values(), key=lambda row: (row.get("coverage_status") == "gap", str(row.get("family") or "")))
    invariant_list = sorted(invariant_rows.values(), key=lambda row: (row.get("coverage_status") == "gap", str(row.get("family") or ""), str(row.get("invariant") or "")))
    covered_families = [row for row in rows if row.get("coverage_status") != "gap"]
    confirmed_families = [row for row in rows if str(row.get("coverage_status")) .startswith("confirmed")]
    total_target_families = len(_RISK_FAMILY_ONTOLOGY)
    return {
        "schema_version": "risk_invariant_coverage_v1",
        "ontology_family_count": total_target_families,
        "ontology_invariant_count": sum(len(spec.get("invariants", ()) or ()) for spec in _RISK_FAMILY_ONTOLOGY.values()),
        "covered_family_count": len(covered_families),
        "confirmed_family_count": len(confirmed_families),
        "family_coverage_rate": round(len(covered_families) / total_target_families, 4) if total_target_families else 0.0,
        "confirmed_family_rate": round(len(confirmed_families) / total_target_families, 4) if total_target_families else 0.0,
        "unclassified_signal_count": unclassified_count,
        "families": rows,
        "invariants": invariant_list[:200],
        "honesty_note": "This is risk/invariant coverage from scan outputs, not bug recall unless ground_truth_available is true.",
    }


def compute_benchmark(
    project: str,
    findings: list[dict[str, Any]],
    candidates: list[dict[str, Any]] | None = None,
    *,
    root: Path | None = None,
    ground_truth_path: str = "",
) -> dict[str, Any]:
    """Compute benchmark metrics for a scan run.

    With ground truth, returns benchmark recall/precision.  Without ground truth,
    returns only the non-fabricated risk/invariant coverage matrix.
    """
    root = Path(root or os.environ.get("QUALIBUG_WORKSPACE_ROOT", Path.cwd()))

    # Resolve ground truth path
    gt_path: Path | None = None
    if ground_truth_path:
        gt_path = Path(ground_truth_path)
    elif os.environ.get("QUALIBUG_BENCHMARK_GROUND_TRUTH"):
        gt_path = Path(os.environ["QUALIBUG_BENCHMARK_GROUND_TRUTH"])
    else:
        # Try project-local benchmark dir first, then known absolute paths
        candidates_paths = [
            root / "platform_workspace" / project / "benchmark_ground_truth" / "bugs.json",
            root.parent / "benchmark_mall" / "hidden_ground_truth" / "bugs.json",
        ]
        # Only add absolute desktop paths when they actually exist
        _desktop = Path("C:/Users/Test/Desktop/qualibug_enterprise_benchmark_v0_5_windows_native_stable/qualibug_enterprise_benchmark_v0_5_windows_native_stable/hidden_ground_truth/bugs.json")
        if _desktop.exists():
            candidates_paths.append(_desktop)
        for p in candidates_paths:
            if p.exists():
                gt_path = p
                break

    if gt_path is None or not gt_path.exists():
        return {
            "benchmark_active": False,
            "ground_truth_available": False,
            "reason": "ground_truth_missing",
            "coverage_matrix": _coverage_matrix(findings, candidates),
        }

    truth_data = _read_json(gt_path)
    truth_bugs = truth_data.get("bugs", [])
    if not truth_bugs:
        return {
            "benchmark_active": False,
            "ground_truth_available": False,
            "reason": "ground_truth_empty",
            "coverage_matrix": _coverage_matrix(findings, candidates),
        }

    # ── Match findings against ground truth ──
    all_findings = list(findings) + (list(candidates) if candidates else [])

    # Build lookup: (method, path) → ground_truth_bug
    gt_by_path: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for bug in truth_bugs:
        trigger = bug.get("trigger", "")
        method = bug.get("method", "GET").upper()
        path = _method_path_key({"path": trigger, "method": method})[1]
        key = (method, path)
        gt_by_path.setdefault(key, []).append(bug)

    # Also build by bug_id for exact matching
    gt_by_id: dict[str, dict[str, Any]] = {}
    for bug in truth_bugs:
        bid = str(bug.get("bug_id") or bug.get("id") or "")
        if bid:
            gt_by_id[bid] = bug

    matched_gt_ids: set[str] = set()
    matched_pairs: list[dict[str, Any]] = []
    false_positives: list[dict[str, Any]] = []

    for finding in all_findings:
        key = _method_path_key(finding)
        candidates_gt = gt_by_path.get(key, [])
        matched = False
        for gt in candidates_gt:
            gt_id = str(gt.get("bug_id") or gt.get("id") or "")
            if gt_id in matched_gt_ids:
                continue
            matched_gt_ids.add(gt_id)
            matched_pairs.append({
                "finding_title": finding.get("title", ""),
                "finding_severity": finding.get("severity", ""),
                "gt_bug_id": gt_id,
                "gt_title": gt.get("title", ""),
                "gt_severity": gt.get("severity", ""),
                "gt_type": gt.get("type", ""),
                "gt_risk_family": _risk_family_for_item(gt),
            })
            matched = True
            break
        if not matched and finding.get("customer_delivery_status") == "defect":
            false_positives.append(finding)

    total_gt = len(truth_bugs)
    total_found = len(all_findings)
    true_pos = len(matched_pairs)
    false_pos = len(false_positives)
    false_neg = max(0, total_gt - true_pos)

    # ── Sub-metrics ──
    p0p1_gt = [b for b in truth_bugs if b.get("severity") in ("P0", "P1", "critical", "high")]
    p0p1_found = [m for m in matched_pairs if m.get("gt_severity") in ("P0", "P1", "critical", "high")]

    # Evidence completeness: % of confirmed findings that have request + response + assertion
    confirmed = [f for f in findings if f.get("confirmation_status") in ("confirmed", "validated_candidate")]
    evidence_complete = 0
    for f in confirmed:
        profile = _evidence_profile(f)
        if profile["has_request"] and profile["has_response"] and profile["has_assertion"]:
            evidence_complete += 1

    # Reproduction success rate
    repro_total = len(confirmed)
    repro_success = len([f for f in confirmed if (f.get("reproduction") or {}).get("is_synthetic") is not True and f.get("gate_passed")])

    # Regression success rate (from findings that have regression data)
    reg_total = len([f for f in findings if (f.get("regression") or {}).get("included_in_suite")])
    reg_passed = len([f for f in findings if (f.get("regression") or {}).get("latest_status") == "passed"])

    metrics = {
        "benchmark_active": True,
        "ground_truth_available": True,
        "ground_truth_source": str(gt_path),
        "ground_truth_bug_count": total_gt,
        "scan_findings_total": total_found,
        "true_positives": true_pos,
        "false_positives": false_pos,
        "false_negatives": false_neg,
        "recall": round(true_pos / total_gt, 4) if total_gt else 0,
        "precision": round(true_pos / total_found, 4) if total_found else 0,
        "false_positive_rate": round(false_pos / total_found, 4) if total_found else 0,
        "false_negative_rate": round(false_neg / total_gt, 4) if total_gt else 0,
        "f1_score": round(2 * true_pos / (2 * true_pos + false_pos + false_neg), 4) if (2 * true_pos + false_pos + false_neg) > 0 else 0,
        "high_value_recall": round(len(p0p1_found) / len(p0p1_gt), 4) if p0p1_gt else 0,
        "evidence_completeness_rate": round(evidence_complete / len(confirmed), 4) if confirmed else 0,
        "evidence_complete_count": evidence_complete,
        "evidence_total_count": len(confirmed),
        "reproduction_success_rate": round(repro_success / repro_total, 4) if repro_total else 0,
        "regression_success_rate": round(reg_passed / reg_total, 4) if reg_total else 0,
        "regression_total_count": reg_total,
        "regression_passed_count": reg_passed,
        "matched_bugs": matched_pairs[:50],
        "missed_bug_ids": [b.get("bug_id") for b in truth_bugs if b.get("bug_id") not in matched_gt_ids],
        "bug_type_breakdown": _bug_type_breakdown(matched_pairs, truth_bugs),
        "risk_family_breakdown": _risk_family_breakdown(matched_pairs, truth_bugs),
        "coverage_matrix": _coverage_matrix(findings, candidates, truth=truth_bugs),
    }
    return metrics


def _bug_type_breakdown(
    matched: list[dict[str, Any]],
    truth: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-bug-type recall breakdown."""
    type_map: dict[str, dict[str, int]] = {}
    for bug in truth:
        btype = str(bug.get("type") or "other").strip() or "other"
        entry = type_map.setdefault(btype, {"total": 0, "detected": 0})
        entry["total"] += 1

    gt_ids_matched = {m["gt_bug_id"] for m in matched}
    for bug in truth:
        btype = str(bug.get("type") or "other").strip() or "other"
        if bug.get("bug_id") in gt_ids_matched:
            type_map.setdefault(btype, {"total": 0, "detected": 0})["detected"] += 1

    return type_map


def _risk_family_breakdown(
    matched: list[dict[str, Any]],
    truth: list[dict[str, Any]],
) -> dict[str, dict[str, int]]:
    """Per-risk-family recall breakdown decoupled from fixed bug type labels."""
    family_map: dict[str, dict[str, int]] = {}
    for bug in truth:
        family = _risk_family_for_item(bug)
        entry = family_map.setdefault(family, {"total": 0, "detected": 0})
        entry["total"] += 1

    gt_ids_matched = {m["gt_bug_id"] for m in matched}
    for bug in truth:
        family = _risk_family_for_item(bug)
        if bug.get("bug_id") in gt_ids_matched:
            family_map.setdefault(family, {"total": 0, "detected": 0})["detected"] += 1

    return family_map


def persist_benchmark_result(
    project: str,
    metrics: dict[str, Any],
    *,
    root: Path | None = None,
) -> Path:
    """Persist benchmark/coverage metrics to platform_outputs so the command center can read them."""
    if not metrics:
        return Path()
    root = Path(root or Path.cwd())
    out_dir = root / "platform_outputs" / project.replace("/", "_") / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "benchmark_metrics.json"
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


# ═════════════════════════════════════════════════════════════════════════════
# End-to-End Benchmark Pipeline
# ═════════════════════════════════════════════════════════════════════════════

def run_benchmark_end_to_end(
    industry: str,
    bug_count: int = 50,
    seed: int | None = None,
    *,
    root: str | Path | None = None,
    findings: list[dict[str, Any]] | None = None,
    candidates: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Run a complete benchmark pipeline end-to-end for one industry.

    Flow:
      1. BenchmarkBugFactory generates known bugs
      2. Ground truth written to PRIVATE_BLOCKLIST path
      3. Public artifacts generated for blind discovery
      4. Runtime seeds built for benchmark_runtime target
      5. If findings/candidates provided, compute benchmark metrics
      6. Baseline snapshot recorded and compared

    This is the SINGLE entry point for a complete benchmark run.
    It never fabricates data — all metrics are computed from real inputs.

    Args:
        industry: Industry ID (crm, ecommerce, erp, finance, medical, education, saas).
        bug_count: Number of bug instances to generate.
        seed: Random seed for reproducibility.
        root: Workspace root directory.
        findings: Optional list of discovery findings (from a scan).
        candidates: Optional list of candidate findings.

    Returns:
        Dict with full pipeline result including paths, counts, and metrics.
    """
    from .benchmark_bug_factory import BenchmarkBugFactory, validate_ground_truth_integrity
    from .benchmark_baseline_tracker import BenchmarkBaselineTracker

    root_path = Path(root or os.environ.get("QUALIBUG_WORKSPACE_ROOT", Path.cwd()))

    result: dict[str, Any] = {
        "pipeline": "benchmark_end_to_end.v1",
        "industry": industry,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stages": {},
    }

    # ── Stage 1: Generate bugs ──────────────────────────────────────
    try:
        factory = BenchmarkBugFactory(industry)
        bugs = factory.generate(count=bug_count, seed=seed)

        result["stages"]["bug_generation"] = {
            "status": "ok",
            "bug_count": len(bugs),
            "templates_used": sorted({b["template_id"] for b in bugs}),
            "risk_types": sorted({b["risk_type"] for b in bugs}),
            "severity_distribution": {
                sev: len([b for b in bugs if b["severity"] == sev])
                for sev in sorted({b["severity"] for b in bugs})
            },
        }
    except Exception as e:
        result["stages"]["bug_generation"] = {"status": "failed", "error": str(e)}
        return result

    # ── Stage 2: Write ground truth ─────────────────────────────────
    try:
        gt_path = factory.write_ground_truth(bugs, output_dir=root_path)
        integrity = validate_ground_truth_integrity(gt_path)

        result["stages"]["ground_truth"] = {
            "status": "ok" if integrity["valid"] else "invalid",
            "path": str(gt_path),
            "integrity": integrity,
        }
    except Exception as e:
        result["stages"]["ground_truth"] = {"status": "failed", "error": str(e)}

    # ── Stage 3: Public artifacts ───────────────────────────────────
    try:
        public = factory.generate_public_artifacts(bugs, output_dir=root_path)
        result["stages"]["public_artifacts"] = {
            "status": "ok",
            "files": {k: str(v) for k, v in public.items()},
        }
    except Exception as e:
        result["stages"]["public_artifacts"] = {"status": "failed", "error": str(e)}

    # ── Stage 4: Runtime seeds ──────────────────────────────────────
    try:
        seeds = factory.build_runtime_seeds(bugs)
        seeds_path = root_path / "platform_workspace" / industry / "oracle" / "BUG_GROUND_TRUTH.json"
        seeds_path.parent.mkdir(parents=True, exist_ok=True)
        seeds_path.write_text(json.dumps(seeds, ensure_ascii=False, indent=2), encoding="utf-8")

        result["stages"]["runtime_seeds"] = {
            "status": "ok",
            "seed_count": seeds.get("total_seeds", 0),
            "path": str(seeds_path),
        }
    except Exception as e:
        result["stages"]["runtime_seeds"] = {"status": "failed", "error": str(e)}

    # ── Stage 5: Compute metrics (if findings provided) ─────────────
    if findings or candidates:
        try:
            truth_bugs = bugs  # Use the generated bugs as ground truth
            all_findings = list(findings or []) + list(candidates or [])

            metrics = compute_benchmark(
                industry,
                findings or [],
                candidates,
                root=root_path,
                ground_truth_path=str(gt_path) if 'gt_path' in dir() else "",
            )

            result["stages"]["metrics"] = {
                "status": "ok",
                "benchmark_active": metrics.get("benchmark_active", False),
                "recall": metrics.get("recall"),
                "precision": metrics.get("precision"),
                "f1_score": metrics.get("f1_score"),
                "true_positives": metrics.get("true_positives"),
                "false_positives": metrics.get("false_positives"),
                "false_negatives": metrics.get("false_negatives"),
            }

            # ── Stage 6: Baseline tracking ─────────────────────────
            tracker = BenchmarkBaselineTracker(industry, root=root_path)
            snapshot = tracker.record_run(
                metrics,
                ground_truth_bug_count=len(bugs),
                scan_findings_total=len(all_findings),
                true_positives=metrics.get("true_positives", 0),
                false_positives=metrics.get("false_positives", 0),
                false_negatives=metrics.get("false_negatives", 0),
            )

            result["stages"]["baseline"] = {
                "status": "ok",
                "run_id": snapshot.run_id,
                "total_runs": tracker.get_run_count(),
            }

            # Detect regressions if we have at least 2 runs
            if tracker.get_run_count() >= 2:
                regressions = tracker.detect_regressions()
                result["stages"]["regression_check"] = regressions

        except Exception as e:
            result["stages"]["metrics"] = {"status": "failed", "error": str(e)}
    else:
        result["stages"]["metrics"] = {
            "status": "skipped",
            "reason": "No findings or candidates provided — metrics require scan output",
        }

    # ── Summary ─────────────────────────────────────────────────────
    stage_statuses = [
        s.get("status") for s in result["stages"].values()
        if isinstance(s, dict)
    ]
    all_ok = all(s == "ok" for s in stage_statuses if s != "skipped")
    result["overall_status"] = "ok" if all_ok else "partial"

    return result
