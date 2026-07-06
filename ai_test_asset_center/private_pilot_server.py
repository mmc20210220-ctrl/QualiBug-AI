from __future__ import annotations

"""Patched private pilot server entrypoint.

The legacy private pilot service is intentionally kept stable because it is a
large HTTP entrypoint. This wrapper installs the stricter backend customer
delivery gate, the scan campaign-context bridge, and private-pilot credential
safety guards before delegating to the original server runner.
"""

import contextvars
import json
import os
import secrets
from pathlib import Path
from typing import Any

from ai_test_asset_center import private_pilot_service as _service
from ai_test_asset_center.customer_delivery_gate import split_customer_delivery_tracks
from ai_test_asset_center.real_project_onboarding import ROOT, _safe_project_id, config_paths

PATCH_SOURCE = "ai_test_asset_center.private_pilot_server"
MAIN_CHAIN_NOT_READY_REASON = "MAIN_CHAIN_NOT_READY"
_CREDENTIAL_KEY_ENV = "QUALIBUG_CRED_ENC_KEY"
_MASKED_SECRET = "********"
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


def _credential_ref(project: str, service: str, path: str) -> str:
    safe_project = _safe_project_id(project)
    safe_service = _safe_project_id(service or "service")
    safe_path = str(path or "secret").replace("/", ".").replace(" ", "_")
    return f"qualibug://credentials/{safe_project}/{safe_service}/{safe_path}"


def _ensure_local_credential_encryption_key(root: Path) -> str:
    """Ensure private deployments encrypt service secrets at rest by default.

    ``credential_crypto.encrypt`` intentionally keeps compatibility by returning
    plaintext when QUALIBUG_CRED_ENC_KEY is missing. For the private pilot server
    we do not want that fallback for newly saved service credentials, so this
    wrapper provisions a local machine key and exports it for the current process.
    """
    existing = os.environ.get(_CREDENTIAL_KEY_ENV, "").strip()
    if existing:
        return "env"
    key_dir = root / "platform_workspace" / ".secrets"
    key_path = key_dir / "credential_encryption.key"
    try:
        key_dir.mkdir(parents=True, exist_ok=True)
        if key_path.exists():
            key = key_path.read_text(encoding="utf-8").strip()
        else:
            key = secrets.token_urlsafe(48)
            key_path.write_text(key, encoding="utf-8")
            try:
                key_path.chmod(0o600)
            except Exception:
                pass
        if key:
            os.environ[_CREDENTIAL_KEY_ENV] = key
            return "local_private_key_file"
    except Exception:
        pass
    return "missing"


def _mask_secret_field(container: dict[str, Any], key: str, project: str, service: str, ref_path: str) -> None:
    value = container.get(key)
    if value is None or value == "":
        return
    container[key] = _MASKED_SECRET
    container[f"{key}_configured"] = True
    container[f"{key}_ref"] = _credential_ref(project, service, ref_path)
    container[f"{key}_masked"] = True


def _mask_service_credentials_for_frontend(project: str, services: list[Any]) -> list[dict[str, Any]]:
    """Return service configs with credential values replaced by masked refs."""
    masked_services: list[dict[str, Any]] = []
    for raw in services:
        if not isinstance(raw, dict):
            continue
        svc = json.loads(json.dumps(raw, ensure_ascii=False, default=str))
        service_name = _text(svc.get("name")) or "service"

        for top_key in ("admin_pass", "bearer_token", "api_key", "token", "password"):
            _mask_secret_field(svc, top_key, project, service_name, f"service.{top_key}")

        auth = svc.get("auth")
        if isinstance(auth, dict):
            for role, role_cfg in list(auth.items()):
                if isinstance(role_cfg, dict):
                    _mask_secret_field(role_cfg, "password", project, service_name, f"auth.{role}.password")
                    _mask_secret_field(role_cfg, "token", project, service_name, f"auth.{role}.token")
                    _mask_secret_field(role_cfg, "api_key", project, service_name, f"auth.{role}.api_key")
                elif role in {"bearer_token", "api_key", "token", "password"} and role_cfg:
                    auth[role] = _MASKED_SECRET
                    auth[f"{role}_configured"] = True
                    auth[f"{role}_ref"] = _credential_ref(project, service_name, f"auth.{role}")
                    auth[f"{role}_masked"] = True

        db_cfg = svc.get("db")
        if isinstance(db_cfg, dict):
            _mask_secret_field(db_cfg, "password", project, service_name, "db.password")

        role_accounts = svc.get("role_accounts")
        if isinstance(role_accounts, list):
            for idx, account in enumerate(role_accounts):
                if isinstance(account, dict):
                    role = _text(account.get("role")) or str(idx)
                    _mask_secret_field(account, "password", project, service_name, f"role_accounts.{role}.password")

        masked_services.append(svc)
    return masked_services


def _credential_storage_status(root: Path) -> dict[str, Any]:
    source = _ensure_local_credential_encryption_key(root)
    return {
        "mode": "encrypted_at_rest",
        "key_source": source,
        "returns_plaintext": False,
        "frontend_secret_policy": "masked_refs_only",
        "config_file_policy": "encrypt_before_write",
    }


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
    """Build the V12 campaign_context from the frontend scan contract."""
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
    original_get_credentials = getattr(_service.PrivatePilotHandler, "_handle_get_service_credentials")
    original_save_credentials = getattr(_service.PrivatePilotHandler, "_handle_save_service_credentials")

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

    def _handle_get_service_credentials_masked(self: Any, project: str, root: Path) -> Any:
        _ensure_local_credential_encryption_key(root)
        config_path = root / "platform_workspace" / project / "multi_service_config.json"
        services: list[Any] = []
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8") or "{}")
                raw_services = data.get("services", []) if isinstance(data, dict) else []
                services = raw_services if isinstance(raw_services, list) else []
        except Exception:
            services = []
        return self._json({
            "project": project,
            "services": _mask_service_credentials_for_frontend(project, services),
            "credential_storage": _credential_storage_status(root),
        })

    def _handle_save_service_credentials_secure(
        self: Any,
        project: str,
        root: Path,
        body: dict[str, Any],
    ) -> Any:
        _ensure_local_credential_encryption_key(root)
        return original_save_credentials(self, project, root, body)

    scanner_module.scan = _scan_with_campaign_context
    _service.PrivatePilotHandler._handle_v12_scan = _handle_v12_scan_with_campaign_context
    _service.PrivatePilotHandler._handle_continuous_start = _handle_continuous_start_with_campaign_context
    _service.PrivatePilotHandler._handle_get_service_credentials = _handle_get_service_credentials_masked
    _service.PrivatePilotHandler._handle_save_service_credentials = _handle_save_service_credentials_secure
    _service._continuous_scan_loop = _continuous_scan_loop_with_campaign_context
    _service._ORIGINAL_V12_SCAN = original_scan  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = original_handler  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = original_continuous_start  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = original_continuous_loop  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS = original_get_credentials  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS = original_save_credentials  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = True  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = PATCH_SOURCE  # type: ignore[attr-defined]


def install_customer_delivery_gate_patch() -> None:
    """Route legacy delivery-track partitioning and scan context through backend gates."""
    _install_scan_campaign_context_patch()
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
    """Return runtime diagnostics for the delivery-gate and scan-context patches."""
    return {
        "patched": bool(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCHED", False)),
        "source": str(getattr(_service, "_CUSTOMER_DELIVERY_GATE_PATCH_SOURCE", "")),
        "has_original_partition": bool(getattr(_service, "_ORIGINAL_PARTITION_DELIVERY_TRACKS", None)),
        "has_original_normalizer": bool(getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None)),
        "active_partition_name": getattr(getattr(_service, "_partition_delivery_tracks", None), "__name__", ""),
        "active_normalizer_name": getattr(getattr(_service, "_normalize_command_center_envelope", None), "__name__", ""),
        "scan_campaign_context_patched": bool(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCHED", False)),
        "scan_campaign_context_patch_source": str(getattr(_service, "_SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE", "")),
        "continuous_scan_context_patched": bool(getattr(_service, "_ORIGINAL_CONTINUOUS_SCAN_LOOP", None)),
        "continuous_context_count": len(_CONTINUOUS_CAMPAIGN_CONTEXTS),
        "credential_response_masking_patched": bool(getattr(_service, "_ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS", None)),
        "credential_save_encryption_patched": bool(getattr(_service, "_ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS", None)),
    }


def restore_customer_delivery_gate_patch() -> None:
    """Restore the original partition, normalizer, scan, handler and loop functions for tests."""
    original_partition = getattr(_service, "_ORIGINAL_PARTITION_DELIVERY_TRACKS", None)
    original_normalizer = getattr(_service, "_ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE", None)
    if original_partition is not None:
        _service._partition_delivery_tracks = original_partition  # type: ignore[attr-defined]
    if original_normalizer is not None:
        _service._normalize_command_center_envelope = original_normalizer  # type: ignore[attr-defined]

    original_scan = getattr(_service, "_ORIGINAL_V12_SCAN", None)
    original_handler = getattr(_service, "_ORIGINAL_HANDLE_V12_SCAN", None)
    original_continuous_start = getattr(_service, "_ORIGINAL_HANDLE_CONTINUOUS_START", None)
    original_continuous_loop = getattr(_service, "_ORIGINAL_CONTINUOUS_SCAN_LOOP", None)
    original_get_credentials = getattr(_service, "_ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS", None)
    original_save_credentials = getattr(_service, "_ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS", None)
    if original_scan is not None:
        from ai_test_asset_center import __main__ as scanner_module

        scanner_module.scan = original_scan
    if original_handler is not None:
        _service.PrivatePilotHandler._handle_v12_scan = original_handler
    if original_continuous_start is not None:
        _service.PrivatePilotHandler._handle_continuous_start = original_continuous_start
    if original_continuous_loop is not None:
        _service._continuous_scan_loop = original_continuous_loop
    if original_get_credentials is not None:
        _service.PrivatePilotHandler._handle_get_service_credentials = original_get_credentials
    if original_save_credentials is not None:
        _service.PrivatePilotHandler._handle_save_service_credentials = original_save_credentials

    _service._CUSTOMER_DELIVERY_GATE_PATCHED = False  # type: ignore[attr-defined]
    _service._CUSTOMER_DELIVERY_GATE_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_PARTITION_DELIVERY_TRACKS = None  # type: ignore[attr-defined]
    _service._ORIGINAL_NORMALIZE_COMMAND_CENTER_ENVELOPE = None  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCHED = False  # type: ignore[attr-defined]
    _service._SCAN_CAMPAIGN_CONTEXT_PATCH_SOURCE = ""  # type: ignore[attr-defined]
    _service._ORIGINAL_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_V12_SCAN = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_CONTINUOUS_START = None  # type: ignore[attr-defined]
    _service._ORIGINAL_CONTINUOUS_SCAN_LOOP = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_GET_SERVICE_CREDENTIALS = None  # type: ignore[attr-defined]
    _service._ORIGINAL_HANDLE_SAVE_SERVICE_CREDENTIALS = None  # type: ignore[attr-defined]
    _CONTINUOUS_CAMPAIGN_CONTEXTS.clear()


def run_server() -> None:
    install_customer_delivery_gate_patch()
    _service.run_server()
