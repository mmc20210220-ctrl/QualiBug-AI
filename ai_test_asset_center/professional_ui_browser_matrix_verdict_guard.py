"""Preserve typed verdict semantics and content-addressed receipt integrity.

A failed browser profile is not automatically a UI defect.  Only the existing
``UI_EXPECTATION_UNSATISFIED`` typed assertion path may mark a matrix violation;
engine/bootstrap/runtime failures remain INDETERMINATE.  The guard also rebuilds
the observer receipt after matrix evidence is added, because observer receipts are
content-addressed and must never be mutated after their id is computed.

Matrix execution injects profile-owned viewport/media steps.  Therefore a failed
child's completed-step index cannot be compared with the unexpanded source plan.
The typed failure action and code are carried in the matrix receipt and rebound to
the existing formal UI assertion evidence without guessing a DOM target.

The source contract may declare a User-Agent, but execution results retain only its
fingerprint.  This avoids duplicating client-identifying strings into result and
observer artifacts while preserving exact source authority in the obligation.
"""
from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

from . import formal_ui_surface as _formal
from . import observer_contracts_base as _observers
from . import professional_ui_browser_matrix as _matrix
from . import ui_execution_adapter as _adapter
from .enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
    normalize_browser_matrix,
)

_INSTALL_MARKER = "_qualibug_browser_matrix_verdict_guard_installed"
_ORIGINAL_AGGREGATE = "_qualibug_matrix_aggregate_before_verdict_guard"
_ORIGINAL_ADAPTER = "_qualibug_matrix_adapter_before_verdict_guard"
_ORIGINAL_HANDLER = "_qualibug_matrix_observer_before_verdict_guard"
_TYPED_VIOLATION_PREFIX = "UI_EXPECTATION_UNSATISFIED:"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any, *, limit: int = 1000) -> str:
    return str(value or "").strip()[:limit]


def _is_typed_violation(result: dict[str, Any]) -> bool:
    return (
        _text(result.get("status"), limit=40).lower() == "failed"
        and _text(result.get("reason")).startswith(_TYPED_VIOLATION_PREFIX)
    )


def _typed_failure(reason: Any) -> tuple[str, str]:
    text = _text(reason)
    if not text.startswith(_TYPED_VIOLATION_PREFIX):
        return "", ""
    parts = text.split(":", 3)
    action = _text(parts[1], limit=100) if len(parts) > 1 else ""
    code = _text(parts[2], limit=160) if len(parts) > 2 else ""
    return action, code


def _sanitized_matrix_for_result(matrix: dict[str, Any]) -> dict[str, Any]:
    profiles = []
    for value in _list(matrix.get("profiles")):
        row = copy.deepcopy(_dict(value))
        user_agent = _text(row.pop("user_agent", None), limit=500)
        row["user_agent_fingerprint"] = (
            _matrix._fingerprint(user_agent) if user_agent else ""
        )
        profiles.append(row)
    return {
        "schema_version": _text(matrix.get("schema_version")),
        "aggregation_policy": _text(matrix.get("aggregation_policy")),
        "profiles": profiles,
        "raw_user_agent_in_execution_result": False,
    }


def _matrix_receipt_from_children(
    receipt: dict[str, Any],
    child_results: list[tuple[dict[str, Any], dict[str, Any], dict[str, Any]]],
) -> dict[str, Any]:
    output = copy.deepcopy(receipt)
    typed_count = sum(
        1 for _profile, result, _runtime in child_results if _is_typed_violation(result)
    )
    runtime_failure_count = sum(
        1
        for _profile, result, _runtime in child_results
        if _text(result.get("status"), limit=40).lower() == "failed"
        and not _is_typed_violation(result)
    )
    incomplete = any(
        _text(result.get("status"), limit=40).lower() != "executed"
        for _profile, result, _runtime in child_results
    )
    output["status"] = (
        "VIOLATION_OBSERVED"
        if typed_count
        else "PROFILE_EXECUTION_FAILED"
        if runtime_failure_count
        else "INCOMPLETE"
        if incomplete
        else "ALL_PROFILES_EXECUTED"
    )
    output["typed_violation_profile_count"] = typed_count
    output["runtime_failure_profile_count"] = runtime_failure_count
    output["violation_observed"] = typed_count > 0
    output["runtime_failures_are_formal_violations"] = False
    output["property_held_requires_all_profiles"] = True
    output["violation_requires_one_typed_profile_failure"] = True
    output["raw_user_agent_in_receipt"] = False
    profile_rows = [
        copy.deepcopy(_dict(value)) for value in _list(output.get("profiles"))
    ]
    for index, (_profile, result, _runtime) in enumerate(child_results):
        if index >= len(profile_rows):
            break
        action, code = _typed_failure(result.get("reason"))
        profile_rows[index]["typed_failure_action"] = action
        profile_rows[index]["typed_failure_code"] = code
        profile_rows[index]["raw_failure_text_in_receipt"] = False
    output["profiles"] = profile_rows
    return output


def _synthetic_blocked_receipt(
    matrix: dict[str, Any],
    reason: str,
) -> dict[str, Any]:
    profiles = []
    for profile in _list(matrix.get("profiles")):
        row = _dict(profile)
        profiles.append({
            "profile_id": _text(row.get("profile_id"), limit=80),
            "browser_engine": _text(row.get("browser_engine"), limit=20),
            "browser_version": "",
            "playwright_version": "",
            "bundled_engine_required": True,
            "system_browser_fallback_used": False,
            "device_class": _text(row.get("device_class"), limit=20),
            "viewport_width": int(row.get("viewport_width") or 0),
            "viewport_height": int(row.get("viewport_height") or 0),
            "device_scale_factor": float(row.get("device_scale_factor") or 1),
            "is_mobile": row.get("is_mobile") is True,
            "has_touch": row.get("has_touch") is True,
            "locale": _text(row.get("locale"), limit=40),
            "timezone_id": _text(row.get("timezone_id"), limit=100),
            "color_scheme": _text(row.get("color_scheme"), limit=20),
            "reduced_motion": _text(row.get("reduced_motion"), limit=20),
            "user_agent_fingerprint": (
                _matrix._fingerprint(row.get("user_agent"))
                if _text(row.get("user_agent"), limit=500)
                else ""
            ),
            "status": "blocked",
            "execution_status": "not_executed",
            "reason_code": _text(reason, limit=160).split(":", 1)[0],
            "typed_failure_action": "",
            "typed_failure_code": "",
            "completed_step_count": 0,
            "artifact_fingerprints": [],
            "duration_ms": 0,
            "raw_console_in_receipt": False,
            "raw_network_urls_in_receipt": False,
            "raw_failure_text_in_receipt": False,
        })
    return {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "status": "INCOMPLETE",
        "profile_count": len(profiles),
        "executed_profile_count": 0,
        "failed_profile_count": 0,
        "blocked_profile_count": len(profiles),
        "all_profiles_executed": False,
        "typed_violation_profile_count": 0,
        "runtime_failure_profile_count": 0,
        "violation_observed": False,
        "runtime_failures_are_formal_violations": False,
        "property_held_requires_all_profiles": True,
        "violation_requires_one_typed_profile_failure": True,
        "profiles": profiles,
        "provider_findings_consumed": False,
        "interactive_matrix_supported": False,
        "cross_engine_visual_baseline_supported": False,
        "raw_user_agent_in_receipt": False,
    }


def _rebuild_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    row = _dict(receipt)
    return _observers._receipt(
        observer_id=_text(row.get("observer_id")),
        status=_text(row.get("status")),
        reason_code=_text(row.get("reason_code")),
        evidence=copy.deepcopy(_dict(row.get("evidence"))),
        campaign_id=_text(row.get("campaign_id")),
        execution_id=_text(row.get("execution_id")),
    )


def _apply_matrix_verdict_to_receipt(receipt: dict[str, Any]) -> dict[str, Any]:
    output = copy.deepcopy(_dict(receipt))
    evidence = _dict(output.get("evidence"))
    ui_evidence = _dict(evidence.get(_formal.EVIDENCE_KEY))
    matrix_receipt = _dict(ui_evidence.get("browser_matrix"))
    if not matrix_receipt:
        return output
    matrix_status = _text(matrix_receipt.get("status"), limit=80)
    if matrix_status == "VIOLATION_OBSERVED":
        typed_profile = next(
            (
                _dict(row)
                for row in _list(matrix_receipt.get("profiles"))
                if _text(_dict(row).get("typed_failure_action"), limit=100)
            ),
            {},
        )
        ui_evidence["expectation_satisfied"] = False
        ui_evidence["violation_observed"] = True
        ui_evidence["failed_step_index"] = 0
        ui_evidence["failed_expectation"] = {
            "action": _text(typed_profile.get("typed_failure_action"), limit=100),
            "matrix_profile_id": _text(typed_profile.get("profile_id"), limit=80),
            "browser_engine": _text(typed_profile.get("browser_engine"), limit=20),
        }
        ui_evidence["failure_type"] = _text(
            typed_profile.get("typed_failure_code"),
            limit=160,
        )
        output["status"] = "OBSERVED"
        output["reason_code"] = ""
    elif matrix_status in {"INCOMPLETE", "PROFILE_EXECUTION_FAILED"}:
        ui_evidence["expectation_satisfied"] = None
        ui_evidence["violation_observed"] = False
        output["status"] = "INDETERMINATE"
        output["reason_code"] = (
            "UI_BROWSER_MATRIX_PROFILE_RUNTIME_FAILED"
            if matrix_status == "PROFILE_EXECUTION_FAILED"
            else "UI_BROWSER_MATRIX_INCOMPLETE"
        )
    elif matrix_status == "ALL_PROFILES_EXECUTED":
        ui_evidence["expectation_satisfied"] = True
        ui_evidence["violation_observed"] = False
        output["status"] = "OBSERVED"
        output["reason_code"] = ""
    evidence[_formal.EVIDENCE_KEY] = ui_evidence
    output["evidence"] = evidence
    return output


def install_professional_ui_browser_matrix_verdict_guard() -> None:
    if getattr(_matrix, _INSTALL_MARKER, False):
        return
    original_aggregate = getattr(
        _matrix,
        _ORIGINAL_AGGREGATE,
        _matrix._aggregate_result,
    )
    original_adapter = getattr(
        _adapter,
        _ORIGINAL_ADAPTER,
        _adapter._playwright_request_result,
    )
    original_handler = _observers._REGISTERED_OBSERVER_HANDLERS.get(
        _formal.OBSERVER_ID,
        _formal._ui_observer_handler,
    )
    setattr(_matrix, _ORIGINAL_AGGREGATE, original_aggregate)
    setattr(_adapter, _ORIGINAL_ADAPTER, original_adapter)
    setattr(_matrix, _ORIGINAL_HANDLER, original_handler)

    def aggregate_with_typed_verdict(
        request: dict[str, Any],
        matrix: dict[str, Any],
        child_results: list[
            tuple[dict[str, Any], dict[str, Any], dict[str, Any]]
        ],
    ) -> dict[str, Any]:
        result = original_aggregate(request, matrix, child_results)
        receipt = _matrix_receipt_from_children(
            _dict(result.get("browser_matrix_receipt")),
            child_results,
        )
        result["browser_matrix"] = _sanitized_matrix_for_result(matrix)
        result["browser_matrix_receipt"] = receipt
        result["matrix_results"] = copy.deepcopy(_list(receipt.get("profiles")))
        _matrix._LAST_RECEIPT.set(copy.deepcopy(receipt))
        return result

    def adapter_with_blocked_matrix_receipt(
        project_id: str,
        request: dict[str, Any],
        runtime_contract: dict[str, Any],
        *,
        root: Path,
        run_id: str,
    ) -> dict[str, Any]:
        result = original_adapter(
            project_id,
            request,
            runtime_contract,
            root=root,
            run_id=run_id,
        )
        if not isinstance(_dict(request).get("browser_matrix"), dict):
            return result
        if _dict(result.get("browser_matrix_receipt")):
            return result
        reason = _text(result.get("reason"))
        if not reason.startswith("UI_BROWSER_MATRIX_ORCHESTRATION_ERROR_"):
            return result
        try:
            matrix = normalize_browser_matrix(request.get("browser_matrix"))
        except ValueError:
            return result
        receipt = _synthetic_blocked_receipt(matrix, reason)
        result["browser_matrix"] = _sanitized_matrix_for_result(matrix)
        result["browser_matrix_receipt"] = receipt
        result["matrix_results"] = copy.deepcopy(receipt["profiles"])
        _matrix._LAST_RECEIPT.set(copy.deepcopy(receipt))
        return result

    def observer_with_recomputed_receipt(envelope: dict[str, Any]) -> dict[str, Any]:
        receipt = original_handler(envelope)
        ui_evidence = _dict(
            _dict(_dict(receipt).get("evidence")).get(_formal.EVIDENCE_KEY)
        )
        if not _dict(ui_evidence.get("browser_matrix")):
            return receipt
        return _rebuild_receipt(_apply_matrix_verdict_to_receipt(receipt))

    _matrix._aggregate_result = aggregate_with_typed_verdict
    _adapter._playwright_request_result = adapter_with_blocked_matrix_receipt
    _formal._ui_observer_handler = observer_with_recomputed_receipt
    _observers._REGISTERED_OBSERVER_HANDLERS[
        _formal.OBSERVER_ID
    ] = observer_with_recomputed_receipt
    setattr(_matrix, _INSTALL_MARKER, True)


__all__ = [
    "_apply_matrix_verdict_to_receipt",
    "_rebuild_receipt",
    "install_professional_ui_browser_matrix_verdict_guard",
]
