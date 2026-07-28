"""Add browser/device-matrix detail to the professional UI coverage projection."""
from __future__ import annotations

import copy
import sys
from collections import Counter
from typing import Any

from . import professional_ui_coverage_projection as _coverage
from .enterprise_knowledge_center._formal_ui_browser_matrix_guard import (
    AGGREGATION_POLICY,
    SCHEMA_VERSION,
    SUPPORTED_ENGINES,
)
from .formal_ui_surface import EVIDENCE_KEY, OBSERVER_ID, RISK_FAMILY
from .professional_ui_browser_matrix_verdict_guard import (
    install_professional_ui_browser_matrix_verdict_guard,
)

_INSTALL_MARKER = "_qualibug_browser_matrix_coverage_installed"
_ORIGINAL_BUILDER = "_qualibug_professional_coverage_before_browser_matrix"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _ui_obligations(result: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(result.get("test_obligations")).get("obligations"))
        if isinstance(row, dict) and _text(row.get("risk_family")) == RISK_FAMILY
    ]


def _declared_matrices(result: dict[str, Any]) -> list[dict[str, Any]]:
    matrices: list[dict[str, Any]] = []
    for obligation in _ui_obligations(result):
        request = _dict(_dict(obligation.get("property")).get("ui_request"))
        matrix = _dict(request.get("browser_matrix"))
        if matrix:
            matrices.append(copy.deepcopy(matrix))
    return matrices


def _execution_rows(result: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _dict(_dict(result.get("experiment_execution")).get("results"))
    return [dict(row) for row in rows.values() if isinstance(row, dict)]


def _observed_matrix_receipts(result: dict[str, Any]) -> list[dict[str, Any]]:
    receipts: list[dict[str, Any]] = []
    for execution in _execution_rows(result):
        for observer in _list(execution.get("observer_receipts")):
            if not isinstance(observer, dict):
                continue
            if _text(observer.get("observer_id")) != OBSERVER_ID:
                continue
            ui_evidence = _dict(_dict(observer.get("evidence")).get(EVIDENCE_KEY))
            matrix = _dict(ui_evidence.get("browser_matrix"))
            if matrix:
                receipts.append(copy.deepcopy(matrix))
    return receipts


def _matrix_projection(result: dict[str, Any]) -> dict[str, Any]:
    matrices = _declared_matrices(result)
    receipts = _observed_matrix_receipts(result)
    declared_engines: Counter[str] = Counter()
    declared_devices: Counter[str] = Counter()
    declared_locales: Counter[str] = Counter()
    declared_profile_count = 0
    declared_mobile_count = 0
    declared_touch_count = 0
    for matrix in matrices:
        for profile in _list(matrix.get("profiles")):
            row = _dict(profile)
            declared_profile_count += 1
            declared_engines[_text(row.get("browser_engine"))] += 1
            declared_devices[_text(row.get("device_class"))] += 1
            declared_locales[_text(row.get("locale"))] += 1
            declared_mobile_count += int(row.get("is_mobile") is True)
            declared_touch_count += int(row.get("has_touch") is True)

    observed_statuses: Counter[str] = Counter()
    observed_engines: Counter[str] = Counter()
    observed_devices: Counter[str] = Counter()
    observed_profile_count = 0
    all_profiles_executed_count = 0
    incomplete_matrix_count = 0
    violation_matrix_count = 0
    runtime_failure_matrix_count = 0
    for receipt in receipts:
        status = _text(receipt.get("status"))
        if status == "ALL_PROFILES_EXECUTED":
            all_profiles_executed_count += 1
        elif status == "VIOLATION_OBSERVED":
            violation_matrix_count += 1
        elif status == "PROFILE_EXECUTION_FAILED":
            runtime_failure_matrix_count += 1
            incomplete_matrix_count += 1
        else:
            incomplete_matrix_count += 1
        for profile in _list(receipt.get("profiles")):
            row = _dict(profile)
            observed_profile_count += 1
            observed_statuses[_text(row.get("status")) or "unknown"] += 1
            observed_engines[_text(row.get("browser_engine"))] += 1
            observed_devices[_text(row.get("device_class"))] += 1

    return {
        "schema_version": SCHEMA_VERSION,
        "aggregation_policy": AGGREGATION_POLICY,
        "declared_matrix_contract_count": len(matrices),
        "declared_profile_count": declared_profile_count,
        "declared_engine_profile_counts": dict(sorted(declared_engines.items())),
        "declared_device_class_profile_counts": dict(sorted(declared_devices.items())),
        "declared_locale_profile_counts": dict(sorted(declared_locales.items())),
        "declared_mobile_profile_count": declared_mobile_count,
        "declared_touch_profile_count": declared_touch_count,
        "observed_matrix_receipt_count": len(receipts),
        "observed_profile_count": observed_profile_count,
        "observed_profile_status_counts": dict(sorted(observed_statuses.items())),
        "observed_engine_profile_counts": dict(sorted(observed_engines.items())),
        "observed_device_class_profile_counts": dict(sorted(observed_devices.items())),
        "all_profiles_executed_matrix_count": all_profiles_executed_count,
        "violation_observed_matrix_count": violation_matrix_count,
        "runtime_failure_matrix_count": runtime_failure_matrix_count,
        "incomplete_matrix_count": incomplete_matrix_count,
        "declared_profiles_without_observation_count": max(
            0,
            declared_profile_count - observed_profile_count,
        ),
        "supported_browser_engines": sorted(SUPPORTED_ENGINES),
        "property_held_requires_all_profiles": True,
        "one_typed_profile_failure_can_prove_violation": True,
        "runtime_failures_are_formal_violations": False,
        "bundled_engine_required": True,
        "system_browser_fallback_used": False,
        "interactive_matrix_supported": False,
        "cross_engine_visual_baseline_supported": False,
        "provider_findings_consumed": False,
    }


def install_professional_ui_browser_matrix_coverage() -> None:
    # Verdict and content-address integrity must be active before the coverage
    # projection starts consuming matrix receipts.
    install_professional_ui_browser_matrix_verdict_guard()
    if getattr(_coverage, _INSTALL_MARKER, False):
        return
    original = getattr(
        _coverage,
        _ORIGINAL_BUILDER,
        _coverage.build_professional_ui_coverage,
    )
    setattr(_coverage, _ORIGINAL_BUILDER, original)

    def build_with_browser_matrix(result: dict[str, Any]) -> dict[str, Any]:
        payload = copy.deepcopy(original(result))
        matrix = _matrix_projection(result)
        payload["browser_device_matrix"] = matrix
        boundary = _dict(payload.get("capability_boundary"))
        boundary.update({
            "cross_browser_matrix_supported": True,
            "cross_browser_matrix_engines": sorted(SUPPORTED_ENGINES),
            "device_profile_matrix_supported": True,
            "matrix_property_held_requires_all_profiles": True,
            "matrix_violation_can_be_profile_specific": True,
            "matrix_runtime_failure_is_violation": False,
            "matrix_bundled_browser_engines_required": True,
            "matrix_system_browser_fallback_supported": False,
            "cross_browser_interactive_matrix_supported": False,
            "cross_browser_visual_baseline_supported": False,
        })
        payload["capability_boundary"] = boundary
        return payload

    _coverage.build_professional_ui_coverage = build_with_browser_matrix
    # discovery_ui_loss_projection imports the function by value. Rebind only when
    # that module is already loaded and still points to the exact old builder.
    loss_module = sys.modules.get("ai_test_asset_center.discovery_ui_loss_projection")
    if loss_module is not None and getattr(
        loss_module,
        "build_professional_ui_coverage",
        None,
    ) is original:
        loss_module.build_professional_ui_coverage = build_with_browser_matrix
    setattr(_coverage, _INSTALL_MARKER, True)


__all__ = ["install_professional_ui_browser_matrix_coverage"]
