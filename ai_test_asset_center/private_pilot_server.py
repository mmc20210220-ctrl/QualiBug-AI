from __future__ import annotations

"""Patched private pilot server entrypoint.

The legacy private pilot service is intentionally kept stable because it is a
large HTTP entrypoint. This wrapper installs the stricter backend customer
delivery gate before delegating to the original server runner.
"""

import json
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks
from ai_test_asset_center.real_project_onboarding import ROOT, _safe_project_id, config_paths

PATCH_SOURCE = "ai_test_asset_center.private_pilot_server"
MAIN_CHAIN_NOT_READY_REASON = "MAIN_CHAIN_NOT_READY"


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _resolve_project_id(payload: dict[str, Any]) -> str:
    data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
    candidates = [
        payload.get("project_id"),
        payload.get("project"),
        data.get("project_id") if isinstance(data, dict) else None,
        data.get("project") if isinstance(data, dict) else None,
    ]
    for candidate in candidates:
        text = str(candidate or "").strip()
        if text:
            return _safe_project_id(text)
    return "real_project_demo"


def _load_main_chain_contract(payload: dict[str, Any]) -> dict[str, Any]:
    project = _resolve_project_id(payload)
    paths = config_paths(project, ROOT)
    for path in (
        paths["output_dir"] / "main_chain_contract.json",
        paths["workspace_dir"] / "main_chain_contract.json",
    ):
        contract = _read_json(path)
        if contract:
            return contract
    return {}


def _load_evidence_normalization_report(payload: dict[str, Any]) -> dict[str, Any]:
    project = _resolve_project_id(payload)
    paths = config_paths(project, ROOT)
    for path in (
        paths["output_dir"] / "evidence_bundle_normalization_report.json",
        paths["workspace_dir"] / "evidence_bundle_normalization_report.json",
    ):
        report = _read_json(path)
        if report:
            return report
    return {}


def _evidence_normalization_summary(report: dict[str, Any]) -> dict[str, Any]:
    items = report.get("items") if isinstance(report.get("items"), list) else []
    missing_fields: dict[str, int] = {}
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in item.get("missing_fields") or []:
            key = str(field or "").strip()
            if key:
                missing_fields[key] = missing_fields.get(key, 0) + 1
    return {
        "input_item_count": int(report.get("input_item_count") or 0),
        "output_item_count": int(report.get("output_item_count") or 0),
        "fully_normalized_count": int(report.get("fully_normalized_count") or 0),
        "blocked_item_count": sum(1 for item in items if isinstance(item, dict) and item.get("normalized") is not True),
        "missing_fields": missing_fields,
    }


def _main_chain_contract_summary(contract: dict[str, Any]) -> dict[str, Any]:
    summary = contract.get("summary") if isinstance(contract.get("summary"), dict) else {}
    return {
        "chain_ready": bool(contract.get("chain_ready")),
        "customer_defect_delivery_ready": bool(contract.get("customer_defect_delivery_ready")),
        "first_blocked_stage": str(summary.get("first_blocked_stage") or ""),
        "first_blocked_next_action": str(summary.get("first_blocked_next_action") or ""),
        "passed_stage_count": int(summary.get("passed_stage_count") or 0),
        "partial_stage_count": int(summary.get("partial_stage_count") or 0),
        "missing_stage_count": int(summary.get("missing_stage_count") or 0),
    }


def _append_unique(values: Any, item: str) -> list[str]:
    result = [str(value) for value in values if str(value)] if isinstance(values, list) else []
    if item not in result:
        result.append(item)
    return result


def _apply_main_chain_readiness_guard(data: dict[str, Any], contract_summary: dict[str, Any]) -> None:
    """Prevent customer-delivery readiness claims when the main chain is not closed."""
    if contract_summary.get("chain_ready") is True:
        return

    blocker = {
        "reason": MAIN_CHAIN_NOT_READY_REASON,
        "stage": contract_summary.get("first_blocked_stage") or "unknown",
        "next_action": contract_summary.get("first_blocked_next_action") or "补齐企业资料、解析、计划、执行、缺陷发现和证据链闭合。",
    }
    data["customer_defect_delivery_ready"] = False
    data["main_chain_delivery_blocker"] = blocker
    data["delivery_blockers"] = _append_unique(data.get("delivery_blockers"), MAIN_CHAIN_NOT_READY_REASON)

    for key in ("scan_meta", "value_metrics", "data_contract", "delivery_tracks", "executive_summary"):
        section = data.get(key)
        if not isinstance(section, dict):
            continue
        section["customer_defect_delivery_ready"] = False
        section["main_chain_ready"] = False
        section["main_chain_delivery_blocker"] = blocker
        section["delivery_blockers"] = _append_unique(section.get("delivery_blockers"), MAIN_CHAIN_NOT_READY_REASON)

    executive_summary = data.get("executive_summary")
    if isinstance(executive_summary, dict):
        executive_summary["release_ready"] = False
        executive_summary["customer_delivery_ready"] = False
        executive_summary["main_chain_first_blocked_stage"] = blocker["stage"]
        executive_summary["main_chain_first_blocked_next_action"] = blocker["next_action"]
        executive_summary["delivery_readiness_label"] = "主链路未闭合，禁止声明客户交付就绪"


def _inject_evidence_normalization_report(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    report = _load_evidence_normalization_report(payload)
    if not report:
        return payload
    summary = _evidence_normalization_summary(report)
    payload["evidence_bundle_normalization_report"] = report
    payload["evidence_bundle_normalization_summary"] = summary
    data = payload.get("data")
    if isinstance(data, dict):
        data["evidence_bundle_normalization_report"] = report
        data["evidence_bundle_normalization_summary"] = summary
        for key in ("data_contract", "delivery_tracks", "executive_summary"):
            section = data.get(key)
            if isinstance(section, dict):
                section["evidence_bundle_normalization_summary"] = summary
        executive_summary = data.get("executive_summary")
        if isinstance(executive_summary, dict):
            executive_summary["evidence_fully_normalized_count"] = summary["fully_normalized_count"]
            executive_summary["evidence_blocked_item_count"] = summary["blocked_item_count"]
    return payload


def _inject_main_chain_contract(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    contract = _load_main_chain_contract(payload)
    if not contract:
        return payload
    contract_summary = _main_chain_contract_summary(contract)
    payload["main_chain_contract"] = contract
    payload["main_chain_contract_summary"] = contract_summary
    payload["customer_defect_delivery_ready"] = bool(contract_summary["customer_defect_delivery_ready"])
    if not contract_summary["chain_ready"]:
        payload["delivery_blockers"] = _append_unique(payload.get("delivery_blockers"), MAIN_CHAIN_NOT_READY_REASON)
    data = payload.get("data")
    if isinstance(data, dict):
        data["main_chain_contract"] = contract
        data["main_chain_contract_summary"] = contract_summary
        data_contract = data.get("data_contract")
        if isinstance(data_contract, dict):
            data_contract["main_chain_contract"] = contract_summary
        delivery_tracks = data.get("delivery_tracks")
        if isinstance(delivery_tracks, dict):
            delivery_tracks["main_chain_contract"] = contract_summary
        executive_summary = data.get("executive_summary")
        if isinstance(executive_summary, dict):
            executive_summary["main_chain_ready"] = contract_summary["chain_ready"]
            executive_summary["main_chain_first_blocked_stage"] = contract_summary["first_blocked_stage"]
            executive_summary["main_chain_first_blocked_next_action"] = contract_summary["first_blocked_next_action"]
        _apply_main_chain_readiness_guard(data, contract_summary)
    return payload


def _inject_delivery_gate_patch_status(payload: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return payload
    status = customer_delivery_gate_patch_status()
    payload["customer_delivery_gate_patch"] = status
    data = payload.get("data")
    if isinstance(data, dict):
        data["customer_delivery_gate_patch"] = status
        data_contract = data.get("data_contract")
        if isinstance(data_contract, dict):
            data_contract["customer_delivery_gate_patch"] = status
        delivery_tracks = data.get("delivery_tracks")
        if isinstance(delivery_tracks, dict):
            delivery_tracks["customer_delivery_gate_patch"] = status
    return payload


def install_customer_delivery_gate_patch() -> None:
    """Route legacy delivery-track partitioning and response diagnostics through the backend gate."""
    if getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False):
        return

    original_partition = getattr(_service, "_partition_delivery_tracks", None)
    original_normalizer = getattr(_service, "_normalize_command_center_envelope", None)

    def _strict_partition_delivery_tracks(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        safe_items = [item for item in items if isinstance(item, dict)]
        return split_customer_delivery_tracks(safe_items)

    def _strict_normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = original_normalizer(payload) if callable(original_normalizer) else payload
        normalized = _inject_delivery_gate_patch_status(normalized)
        normalized = _inject_evidence_normalization_report(normalized)
        return _inject_main_chain_contract(normalized)

    _service._ORIGINAL_PARTITION_DELIVERY_TRACKS = original_partition  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = original_normalizer  # type: ignore[attr-defined]
    _service._partition_delivery_tracks = _strict_partition_delivery_tracks  # type: ignore[attr-defined]
    _service._normalize_command_center_envelope = _strict_normalize_command_center_envelope  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def customer_delivery_gate_patch_status() -> dict[str, Any]:
    """Return runtime diagnostics for the delivery-gate patch."""
    return {
        "patched": bool(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False)),
        "source": str(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCH_SOURCE", "")),
        "has_original_partition": bool(getattr(_service, "_ORIGINAL_PARTITION_DELIVERY_TRACKS", None)),
        "has_original_normalizer": bool(getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None)),
        "active_partition_name": getattr(getattr(_service, "_partition_delivery_tracks", None), "__name__", ""),
        "active_normalizer_name": getattr(getattr(_service, "_normalize_command_center_envelope", None), "__name__", ""),
    }


def restore_customer_delivery_gate_patch() -> None:
    """Restore the original partition and normalizer functions for isolated tests or diagnostics."""
    original_partition = getattr(_service, "_ORIGINAL_PARTITION_DELIVERY_TRACKS", None)
    original_normalizer = getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None)
    if original_partition is not None:
        _service._partition_delivery_tracks = original_partition  # type: ignore[attr-defined]
    if original_normalizer is not None:
        _service._normalize_command_center_envelope = original_normalizer  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_PARTITION_DELIVERY_TRACKS = None  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = None  # type: ignore[attr-defined]


def run_server() -> None:
    install_customer_delivery_gate_patch()
    _service.run_server()
