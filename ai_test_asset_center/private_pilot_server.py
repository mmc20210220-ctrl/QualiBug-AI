from __future__ import annotations

"""Patched private pilot server entrypoint.

The legacy private pilot service is intentionally kept stable because it is a
large HTTP implementation. This module supplies command-center diagnostics,
the scan campaign-context bridge, and credential safety adapters to the one
supported launch authority in ``private_pilot_entrypoint``. Customer-delivery
classification itself is bound directly in the core service.
"""

import contextvars
import json
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks
from ai_test_asset_center.private_pilot_credentials_patch import (
    install_service_credentials_patch,
    restore_service_credentials_patch,
)
from ai_test_asset_center.real_project_onboarding import ROOT, _safe_project_id, config_paths

PATCH_SOURCE = "ai_test_asset_center.private_pilot_server"
MAIN_CHAIN_NOT_READY_REASON = "MAIN_CHAIN_NOT_READY"
_SCAN_CAMPAIGN_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "qualibug_private_pilot_scan_campaign_context",
    default=None,
)
_CONTINUOUS_CAMPAIGN_CONTEXTS: dict[tuple[str, str], dict[str, Any]] = {}


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8") or "{}")
        return value if isinstance(value, dict) else {}
    except Exception:
        return {}


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _continuous_context_key(root: Path, project: str) -> tuple[str, str]:
    return (str(root), str(project))


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


def _source_manifest_from_body(body: dict[str, Any]) -> dict[str, str]:
    manifest = _dict(body.get("source_manifest"))
    for key in ("source_id", "source_hash", "source_version_id", "source_origin"):
        value = _text(body.get(key))
        if value and not _text(manifest.get(key)):
            manifest[key] = value
    cleaned: dict[str, str] = {}
    for key in ("source_id", "source_hash", "source_version_id", "source_origin", "source_type"):
        value = _text(manifest.get(key))
        if value:
            cleaned[key] = value
    return cleaned


def _load_source_content_from_manifest(project: str, root: Path, manifest: dict[str, str]) -> str:
    source_hash = _text(manifest.get("source_hash")).lower().removeprefix("sha256:")
    if not source_hash:
        return ""
    try:
        from ai_test_asset_center.enterprise_source_registry import load_source_content

        return load_source_content(project, source_hash, root=root)
    except Exception:
        return ""


def _latest_registered_source(project: str, root: Path) -> tuple[str, dict[str, str]]:
    try:
        from ai_test_asset_center.enterprise_source_registry import list_source_assets, load_source_content

        assets = list_source_assets(project, root=root)
    except Exception:
        return "", {}
    if not assets:
        return "", {}

    def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
        return (_text(item.get("updated_at_utc")), _text(item.get("source_id")))

    latest = max((item for item in assets if isinstance(item, dict)), key=_sort_key, default={})
    source_hash = _text(latest.get("latest_source_hash")).lower().removeprefix("sha256:")
    source_id = _text(latest.get("source_id"))
    if not source_id or not source_hash:
        return "", {}
    try:
        content = load_source_content(project, source_hash, root=root)
    except Exception:
        return "", {}
    manifest = {
        "source_id": source_id,
        "source_hash": source_hash,
        "source_version_id": _text(latest.get("latest_version_id")),
        "source_origin": "registered_source_registry",
        "source_type": _text(latest.get("source_type")),
    }
    return content, {key: value for key, value in manifest.items() if value}


def _prepare_scan_body_for_campaign(project: str, root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Fill scan body gaps from the immutable source registry before legacy routing.

    The legacy handler tries connector/fallback API docs whenever ``api_doc`` is
    missing. That is unsafe when the frontend already submitted a source_manifest:
    a fallback document would not match the source hash, causing a false source
    mismatch. This helper loads the registered source content first so the V12
    source contract remains bound to the intended immutable asset.
    """
    prepared = dict(body or {})
    api_doc = _text(prepared.get("api_doc") or prepared.get("api_doc_text"))
    manifest = _source_manifest_from_body(prepared)

    if not api_doc and manifest:
        content = _load_source_content_from_manifest(project, root, manifest)
        if content:
            prepared["api_doc"] = content
            prepared["source_manifest"] = manifest
            return prepared

    if not api_doc and not manifest:
        content, latest_manifest = _latest_registered_source(project, root)
        if content and latest_manifest:
            prepared["api_doc"] = content
            prepared["source_manifest"] = latest_manifest
            return prepared

    if manifest:
        prepared["source_manifest"] = manifest
    return prepared


def _build_campaign_context_from_scan_body(body: dict[str, Any]) -> dict[str, Any]:
    """Build the V12 campaign_context from the shared private-pilot contract helper."""
    try:
        from ai_test_asset_center.private_pilot_scan_context_contract import build_campaign_context_from_scan_body

        return build_campaign_context_from_scan_body(body)
    except Exception:
        context: dict[str, Any] = {}
        manifest = _source_manifest_from_body(body)
        if manifest:
            context["source_manifest"] = manifest

        for key in (
            "base_url",
            "scope_id",
            "environment_ref",
            "target_environment",
            "execution_approval_id",
            "execution_mode",
        ):
            value = _text(body.get(key))
            if value:
                context[key] = value

        if "execution_mode" not in context:
            context["execution_mode"] = "safe_read_only"

        test_data_contract = _dict(body.get("test_data_contract"))
        if test_data_contract:
            context["test_data_contract"] = test_data_contract
        elif _text(body.get("test_data_strategy")):
            context["test_data_contract"] = {"strategy": _text(body.get("test_data_strategy"))}

        release_policy = _dict(body.get("release_policy"))
        if release_policy:
            context["release_policy"] = release_policy
        return context


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


def _install_scan_campaign_context_patch() -> None:
    if getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False):
        return

    from ai_test_asset_center import __main__ as scanner_module

    original_scan = getattr(scanner_module, "scan")
    original_handler = getattr(_service.PrivatePilotHandler, "_handle_v12_scan")
    original_continuous_start = getattr(_service.PrivatePilotHandler, "_handle_continuous_start")
    original_continuous_loop = getattr(_service, "_continuous_scan_loop")

    def _scan_with_campaign_context(*args: Any, **kwargs: Any) -> dict[str, Any]:
        pending_context = _SCAN_CAMPAIGN_CONTEXT.get()
        if pending_context:
            explicit_context = kwargs.get("campaign_context")
            merged = dict(explicit_context) if isinstance(explicit_context, dict) else {}
            for key, value in pending_context.items():
                if key == "base_url":
                    continue
                if value and (key not in merged or not merged.get(key)):
                    merged[key] = value
            kwargs["campaign_context"] = merged
            if pending_context.get("base_url") and not kwargs.get("base_url"):
                kwargs["base_url"] = str(pending_context["base_url"]).rstrip("/")
        return original_scan(*args, **kwargs)

    def _handle_v12_scan_with_campaign_context(
        self: Any,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> Any:
        prepared_body = _prepare_scan_body_for_campaign(project, root, body)
        campaign_context = _build_campaign_context_from_scan_body(prepared_body)
        token = _SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)
        try:
            return original_handler(self, project, root, actor, prepared_body)
        finally:
            _SCAN_CAMPAIGN_CONTEXT.reset(token)

    def _handle_continuous_start_with_campaign_context(
        self: Any,
        project: str,
        root: Path,
        actor: dict[str, str],
        body: dict[str, Any],
    ) -> Any:
        prepared_body = _prepare_scan_body_for_campaign(project, root, body)
        campaign_context = _build_campaign_context_from_scan_body(prepared_body)
        if campaign_context:
            _CONTINUOUS_CAMPAIGN_CONTEXTS[_continuous_context_key(root, project)] = campaign_context
        return original_continuous_start(self, project, root, actor, prepared_body)

    def _continuous_scan_loop_with_campaign_context(root: Path, project: str, tenant_id: str, interval_s: int) -> Any:
        campaign_context = _CONTINUOUS_CAMPAIGN_CONTEXTS.get(_continuous_context_key(root, project))
        token = _SCAN_CAMPAIGN_CONTEXT.set(campaign_context or None)
        try:
            return original_continuous_loop(root, project, tenant_id, interval_s)
        finally:
            _SCAN_CAMPAIGN_CONTEXT.reset(token)

    scanner_module.scan = _scan_with_campaign_context
    _service.PrivatePilotHandler._handle_v12_scan = _handle_v12_scan_with_campaign_context
    _service.PrivatePilotHandler._handle_continuous_start = _handle_continuous_start_with_campaign_context
    _service._continuous_scan_loop = _continuous_scan_loop_with_campaign_context
    _service._ORIGINAL_V12_SCAN = original_scan  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = original_handler  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = original_continuous_start  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = original_continuous_loop  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def install_command_center_runtime_support() -> None:
    """Install command-center diagnostics and auxiliary runtime adapters."""
    _install_scan_campaign_context_patch()
    install_service_credentials_patch(patch_source=PATCH_SOURCE)
    if getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False):
        return

    from ai_test_asset_center.private_pilot_command_center_envelope import register_envelope_post_hook

    def _delivery_gate_envelope_hook(payload: dict[str, Any]) -> dict[str, Any]:
        normalized = _inject_delivery_gate_patch_status(payload)
        normalized = _inject_evidence_normalization_report(normalized)
        return _inject_main_chain_contract(normalized)

    register_envelope_post_hook("customer_delivery_gate_diagnostics", _delivery_gate_envelope_hook)
    _service._CUSTOMER_DELIVERY_GATE_PATCHED = True  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_MODE = "first_class_hook"  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = True  # type: ignore[attr-defined]


def customer_delivery_gate_patch_status() -> dict[str, Any]:
    """Return runtime diagnostics for the delivery-gate and scan-context patches."""
    core_gate_direct = getattr(_service, "_partition_delivery_tracks", None) is split_customer_delivery_tracks
    diagnostics_installed = bool(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False))
    return {
        "patched": core_gate_direct,
        "source": "ai_test_asset_center.private_pilot_service" if core_gate_direct else "",
        "core_gate_direct": core_gate_direct,
        "runtime_partition_override": False,
        "diagnostics_installed": diagnostics_installed,
        "diagnostics_source": str(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCH_SOURCE", "")),
        "has_original_normalizer": bool(getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None))
        or getattr(_service, "_CUSTOMER_DELIVERY_GATE_MODE", "") == "first_class_hook",
        "active_partition_name": getattr(getattr(_service, "_partition_delivery_tracks", None), "__name__", ""),
        "active_normalizer_name": (
            "first_class_hooks"
            if getattr(_service, "_CUSTOMER_DELIVERY_GATE_MODE", "") == "first_class_hook"
            else getattr(getattr(_service, "_normalize_command_center_envelope", None), "__name__", "")
        ),
        "scan_campaign_context_patched": bool(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False)),
        "scan_campaign_context_patch_source": str(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE", "")),
        "continuous_scan_context_patched": bool(getattr(_service, "_ORIGINAL_CONTINUOUS_SCAN_LOOP", None)),
        "continuous_context_count": len(_CONTINUOUS_CAMPAIGN_CONTEXTS),
        "credential_response_masking_patched": bool(getattr(_service, "_ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS", None)),
        "credential_save_encryption_patched": bool(getattr(_service, "_ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS", None)),
    }


def restore_customer_delivery_gate_patch() -> None:
    """Restore the original partition, normalizer, scan, handler and loop functions for tests."""
    restore_service_credentials_patch()
    from ai_test_asset_center.private_pilot_command_center_envelope import register_envelope_post_hook

    register_envelope_post_hook("customer_delivery_gate_diagnostics", None)
    if hasattr(_service, "_CUSTOMER_DELIVERY_GATE_MODE"):
        delattr(_service, "_CUSTOMER_DELIVERY_GATE_MODE")
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = None  # type: ignore[attr-defined]

    original_scan = getattr(_service, "_ORIGINAL_V12_SCAN", None)
    original_handler = getattr(_service, "_ORIGINAL_HANDLE_V12_SCAN", None)
    original_continuous_start = getattr(_service, "_ORIGINAL_HANDLE_CONTINUOUS_START", None)
    original_continuous_loop = getattr(_service, "_ORIGINAL_CONTINUOUS_SCAN_LOOP", None)
    if original_scan is not None:
        from ai_test_asset_center import __main__ as scanner_module

        scanner_module.scan = original_scan
    if original_handler is not None:
        _service.PrivatePilotHandler._handle_v12_scan = original_handler
    if original_continuous_start is not None:
        _service.PrivatePilotHandler._handle_continuous_start = original_continuous_start
    if original_continuous_loop is not None:
        _service._continuous_scan_loop = original_continuous_loop

    _service._CUSTOMER_DELIVERY_GATE_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = None  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
    _CONTINUOUS_CAMPAIGN_CONTEXTS.clear()


if __name__ == "__main__":
    raise SystemExit(
        "Unsupported launch path. Use qualibug-server "
        "(ai_test_asset_center.private_pilot_entrypoint:run_server)."
    )
