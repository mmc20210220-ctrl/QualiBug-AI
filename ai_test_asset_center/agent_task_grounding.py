"""Ground Agent Tasks on persisted understanding and existing runtime authorities.

This module never rebuilds enterprise understanding and never executes a scan.
It pins an already-materialized Test Intelligence projection, selects only
source-backed Test Targets, and reuses the existing ScanHandlersMixin preflight
contract to evaluate runtime readiness. Per-target runtime binding remains a
separate authority and is reported as blocked until real bindings exist.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .private_pilot_product_catalog import (
    _load_persisted_test_intelligence,
    _test_intelligence_source_fingerprint,
)
from .private_pilot_scan_handlers import ScanHandlersMixin


def _text(value: Any) -> str:
    return str(value or "").strip()


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = _text(item)
        if text and text not in seen:
            seen.add(text)
            result.append(text)
    return result


class _PreflightCapture(ScanHandlersMixin):
    """Capture the canonical scan preflight payload without writing HTTP."""

    def _json(
        self,
        body: Any,
        status: int = 200,
        extra_headers: dict[str, str] | None = None,
        **_: Any,
    ) -> Any:
        del status, extra_headers
        return body


def _scan_preflight_payload(
    project_id: str,
    root: Path,
    request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = _PreflightCapture()._handle_scan_preflight(
        project_id,
        root,
        dict(request or {}),
    )
    return payload if isinstance(payload, dict) else {}


def _snapshot_ref(fingerprint: str) -> str:
    digest = hashlib.sha256(fingerprint.encode("utf-8", errors="replace")).hexdigest()
    return f"uts_{digest}"


def _target_snapshot(
    obligation: dict[str, Any],
    design: dict[str, Any] | None,
) -> dict[str, Any]:
    evidence = obligation.get("evidence") if isinstance(obligation.get("evidence"), list) else []
    compact_evidence: list[dict[str, str]] = []
    for item in evidence:
        if not isinstance(item, dict):
            continue
        compact_evidence.append(
            {
                "source_id": _text(item.get("source_id")),
                "source_locator": _text(item.get("source_locator")),
                "quote_hash": _text(item.get("quote_hash")),
                "fact_id": _text(item.get("fact_id")),
            }
        )
    design = design if isinstance(design, dict) else {}
    action = design.get("action") if isinstance(design.get("action"), dict) else {}
    oracle = design.get("oracle") if isinstance(design.get("oracle"), dict) else {}
    return {
        "obligation_id": _text(obligation.get("obligation_id")),
        "obligation_kind": _text(obligation.get("obligation_kind")),
        "title": _text(obligation.get("title")),
        "objective": _text(obligation.get("objective")),
        "operation_ref": _text(obligation.get("operation_ref")),
        "actor_refs": _string_list(obligation.get("actor_refs")),
        "object_refs": _string_list(obligation.get("object_refs")),
        "source_refs": _string_list(obligation.get("source_refs")),
        "evidence": compact_evidence,
        "design_id": _text(design.get("design_id")),
        "execution_surface": _text(action.get("execution_surface")) or "NOT_SELECTED",
        "action_binding_status": _text(action.get("binding_status")) or "NOT_GROUNDED",
        "observer_binding_status": _text(design.get("observer_binding_status")) or "NOT_GROUNDED",
        "oracle_binding_status": _text(
            design.get("oracle_binding_status") or oracle.get("binding_status")
        ) or "NOT_GROUNDED",
    }


def _target_is_runtime_bound(target: dict[str, Any]) -> bool:
    surface = _text(target.get("execution_surface")).upper()
    return (
        surface not in {"", "NOT_SELECTED"}
        and _text(target.get("action_binding_status")).upper() == "GROUNDED"
        and _text(target.get("observer_binding_status")).upper() == "GROUNDED"
        and _text(target.get("oracle_binding_status")).upper() == "GROUNDED"
    )


def _grounding_key(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def build_agent_task_grounding(
    root: Path,
    *,
    tenant_id: str,
    project_id: str,
    task: dict[str, Any],
    requested_target_ids: list[str] | None = None,
    preflight_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one factual grounding evaluation without rebuilding or executing.

    The current persisted Test Intelligence projection is the only understanding
    input. A missing or stale projection is visible and blocks runtime work rather
    than triggering a hidden full understanding rebuild from the Agent Task path.
    """

    intent = _text(task.get("intent")).lower()
    persisted = _load_persisted_test_intelligence(root, tenant_id, project_id)
    if persisted is None:
        blockers = [
            {
                "code": "UNDERSTANDING_SNAPSHOT_NOT_MATERIALIZED",
                "message": "尚无可固定的持久化 Test Intelligence 快照，请先在 Knowledge 完成资料理解。",
                "source": "test_intelligence",
            }
        ]
        result = {
            "source_snapshot": {"status": "NOT_PINNED", "snapshot_ref": ""},
            "selected_test_targets": [],
            "selected_test_target_snapshot": [],
            "runtime_grounding_status": "NOT_REQUIRED" if intent == "analyze_requirements" else "BLOCKED",
            "runtime_context": {},
            "grounding_blockers": blockers,
            "grounding_summary": {
                "selected_target_count": 0,
                "runtime_bound_target_count": 0,
                "preflight_ready": False,
            },
            "task_status": "BLOCKED",
        }
        result["grounding_key"] = _grounding_key(result)
        return result

    persisted_fingerprint, analysis = persisted
    current_fingerprint = _test_intelligence_source_fingerprint(
        root,
        tenant_id,
        project_id,
    )
    is_current = persisted_fingerprint == current_fingerprint
    source_snapshot = {
        "status": "PINNED" if is_current else "PINNED_STALE",
        "snapshot_ref": _snapshot_ref(persisted_fingerprint),
        "source_revision_state": "CURRENT" if is_current else "STALE",
        "analysis_schema": _text(analysis.get("schema")),
        "source_count": int(
            (analysis.get("summary") or {}).get("source_count", 0)
            if isinstance(analysis.get("summary"), dict)
            else 0
        ),
    }

    obligations = [
        item for item in (analysis.get("obligations") or []) if isinstance(item, dict)
    ]
    obligation_by_id = {
        _text(item.get("obligation_id")): item
        for item in obligations
        if _text(item.get("obligation_id"))
    }
    designs = [
        item for item in (analysis.get("test_designs") or []) if isinstance(item, dict)
    ]
    design_by_obligation = {
        _text(item.get("source_obligation_id")): item
        for item in designs
        if _text(item.get("source_obligation_id"))
    }

    explicit_ids = _string_list(requested_target_ids or [])
    selection_blockers: list[dict[str, str]] = []
    if explicit_ids:
        missing = [target_id for target_id in explicit_ids if target_id not in obligation_by_id]
        selected_ids = [target_id for target_id in explicit_ids if target_id in obligation_by_id]
        if missing:
            selection_blockers.append(
                {
                    "code": "REQUESTED_TEST_TARGET_NOT_FOUND",
                    "message": f"{len(missing)} 个显式 Test Target 不存在于固定快照中。",
                    "source": "test_intelligence",
                }
            )
    elif intent == "verify_changes":
        selected_ids = []
        selection_blockers.append(
            {
                "code": "CHANGE_SCOPE_NOT_GROUNDED",
                "message": "当前任务没有真实变更范围依据，不能把全部 Test Targets 冒充为本次变更影响范围。",
                "source": "agent_task",
            }
        )
    elif intent == "analyze_requirements":
        selected_ids = []
    else:
        selected_ids = list(obligation_by_id)

    target_snapshot = [
        _target_snapshot(
            obligation_by_id[target_id],
            design_by_obligation.get(target_id),
        )
        for target_id in selected_ids
    ]
    runtime_bound_count = sum(1 for target in target_snapshot if _target_is_runtime_bound(target))

    blockers: list[dict[str, str]] = []
    if not is_current:
        blockers.append(
            {
                "code": "UNDERSTANDING_SNAPSHOT_STALE",
                "message": "已有持久化理解快照落后于当前资料 revision；Agent Task 不会在运行路径偷偷重建理解。",
                "source": "test_intelligence",
            }
        )
    blockers.extend(selection_blockers)

    preflight: dict[str, Any] = {}
    if intent != "analyze_requirements":
        preflight = _scan_preflight_payload(project_id, root, preflight_request)
        for reason in preflight.get("reasons") or []:
            if not isinstance(reason, dict):
                continue
            code = _text(reason.get("code"))
            if code:
                blockers.append(
                    {
                        "code": f"PREFLIGHT_{code}",
                        "message": _text(reason.get("message")) or code,
                        "source": "scan_preflight",
                    }
                )
        if not selected_ids and intent not in {"verify_changes"}:
            blockers.append(
                {
                    "code": "NO_TEST_TARGETS",
                    "message": "固定理解快照中没有可供当前任务规划的 Test Targets。",
                    "source": "test_intelligence",
                }
            )
        if selected_ids and runtime_bound_count < len(selected_ids):
            blockers.append(
                {
                    "code": "TEST_TARGET_RUNTIME_BINDING_PENDING",
                    "message": (
                        f"{len(selected_ids) - runtime_bound_count} 个已选 Test Targets 仍缺少真实 Action / Observer / Oracle Runtime Binding。"
                    ),
                    "source": "test_design",
                }
            )

    unique_blockers: list[dict[str, str]] = []
    seen_codes: set[str] = set()
    for blocker in blockers:
        code = _text(blocker.get("code"))
        if code and code not in seen_codes:
            seen_codes.add(code)
            unique_blockers.append(blocker)

    if intent == "analyze_requirements":
        runtime_grounding_status = "NOT_REQUIRED"
        task_status = "BLOCKED" if unique_blockers else "READY"
    else:
        runtime_grounding_status = "BLOCKED" if unique_blockers else "READY"
        task_status = "BLOCKED" if unique_blockers else "READY"

    runtime_context = {
        "preflight_schema": _text(preflight.get("schema_version")),
        "preflight_ready": bool(preflight.get("ready")) if preflight else False,
        "input_checks": preflight.get("input_checks") if isinstance(preflight.get("input_checks"), dict) else {},
        "target_policy_decision": (
            preflight.get("target_policy_decision")
            if isinstance(preflight.get("target_policy_decision"), dict)
            else {}
        ),
    }
    result = {
        "source_snapshot": source_snapshot,
        "selected_test_targets": selected_ids,
        "selected_test_target_snapshot": target_snapshot,
        "runtime_grounding_status": runtime_grounding_status,
        "runtime_context": runtime_context,
        "grounding_blockers": unique_blockers,
        "grounding_summary": {
            "selected_target_count": len(selected_ids),
            "runtime_bound_target_count": runtime_bound_count,
            "preflight_ready": bool(preflight.get("ready")) if preflight else False,
        },
        "task_status": task_status,
    }
    result["grounding_key"] = _grounding_key(result)
    return result
