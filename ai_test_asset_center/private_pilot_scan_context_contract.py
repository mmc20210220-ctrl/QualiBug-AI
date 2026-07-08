from __future__ import annotations

"""Private-pilot scan campaign context contract helpers.

This module owns the pure contract-building layer for private-pilot scans:
source manifest normalization, immutable source loading, scan body preparation,
and campaign_context construction. Runtime patch installation lives in
``private_pilot_scan_context_patch``.
"""

import contextvars
import os
from pathlib import Path
from typing import Any

SCAN_CAMPAIGN_CONTEXT: contextvars.ContextVar[dict[str, Any] | None] = contextvars.ContextVar(
    "qualibug_private_pilot_scan_campaign_context",
    default=None,
)
CONTINUOUS_CAMPAIGN_CONTEXTS: dict[tuple[str, str], dict[str, Any]] = {}


def as_dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def as_text(value: Any) -> str:
    return str(value or "").strip()


def _is_production_like_environment(environment_ref: str, environment_type: str = "") -> bool:
    try:
        from ai_test_asset_center.enterprise_testops_control_plane import _is_production

        return bool(_is_production({
            "name": as_text(environment_ref),
            "type": as_text(environment_type),
            "production_protected": False,
        }))
    except Exception:
        fingerprint = f"{as_text(environment_ref)} {as_text(environment_type)}".lower()
        return any(token in fingerprint for token in ("prod", "production", "live"))


def default_scan_execution_mode(body: dict[str, Any]) -> str:
    explicit = as_text(body.get("execution_mode"))
    if explicit:
        return explicit
    if not as_text(body.get("base_url")):
        return "safe_read_only"
    if _is_production_like_environment(
        as_text(body.get("environment_ref") or body.get("target_environment")),
        as_text(body.get("environment_class")),
    ):
        return "safe_read_only"
    return "approved_sandbox_write"


def default_scan_test_data_contract(body: dict[str, Any]) -> dict[str, Any]:
    contract = as_dict(body.get("test_data_contract"))
    if contract:
        return contract
    strategy = as_text(body.get("test_data_strategy"))
    if strategy:
        return {"strategy": strategy}
    if default_scan_execution_mode(body) != "approved_sandbox_write":
        return {}
    scope_ref = (
        as_text(body.get("scope_id"))
        or as_text(body.get("environment_ref"))
        or as_text(body.get("target_environment"))
    )
    if not scope_ref:
        return {}
    return {
        "strategy": "create_disposable",
        "write_approved": True,
        "disposable_scope_ref": scope_ref,
    }


def default_scan_ui_execution_requests(body: dict[str, Any]) -> list[dict[str, Any]]:
    if isinstance(body.get("ui_execution_requests"), list) and body.get("ui_execution_requests"):
        return [dict(item) for item in body["ui_execution_requests"] if isinstance(item, dict)]
    if body.get("disable_ui_execution_autogen") is True:
        return []
    base_url = as_text(body.get("ui_base_url") or body.get("base_url"))
    if not base_url:
        return []
    bridge = as_dict(body.get("page_agent_bridge"))
    bridge_url = as_text(bridge.get("url") or os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_URL"))
    if not bridge_url:
        return []
    scope_ref = as_text(body.get("scope_id") or body.get("target_environment") or body.get("environment_ref"))
    manifest = source_manifest_from_body(body)
    page_hints = [
        hint
        for hint in (
            f"scan scope: {scope_ref}" if scope_ref else "",
            f"source manifest: {as_text(manifest.get('source_id'))}" if as_text(manifest.get("source_id")) else "",
            "capture the landing page state after the application becomes stable",
        )
        if hint
    ]
    return [
        {
            "request_id": "ui_observe_entrypoint",
            "title": "Auto-generated UI entrypoint observation",
            "provider": "page_agent",
            "task": "Open the approved application entry URL and capture stable UI evidence for this scan.",
            "start_url": base_url,
            "execution_mode": "safe_read_only",
            "browser_plan": {
                "execution_mode": "safe_read_only",
                "steps": [
                    {"action": "goto", "url": base_url, "wait_until": "networkidle"},
                    {"action": "wait_for_load", "state": "networkidle"},
                    {"action": "screenshot", "full_page": True},
                ],
            },
            "page_hints": page_hints,
            "success_criteria": {},
            "metadata": {
                "auto_generated": True,
                "request_origin": "private_pilot_scan_context_contract",
                "bridge_mode": "page_agent_browser_plan",
            },
        }
    ]


def continuous_context_key(root: Path, project: str) -> tuple[str, str]:
    return (str(root), str(project))


def current_scan_campaign_context() -> dict[str, Any] | None:
    return SCAN_CAMPAIGN_CONTEXT.get()


def source_manifest_from_body(body: dict[str, Any]) -> dict[str, str]:
    manifest = as_dict(body.get("source_manifest"))
    for key in ("source_id", "source_hash", "source_version_id", "source_origin"):
        value = as_text(body.get(key))
        if value and not as_text(manifest.get(key)):
            manifest[key] = value
    cleaned: dict[str, str] = {}
    for key in ("source_id", "source_hash", "source_version_id", "source_origin", "source_type"):
        value = as_text(manifest.get(key))
        if value:
            cleaned[key] = value
    return cleaned


def load_source_content_from_manifest(project: str, root: Path, manifest: dict[str, str]) -> str:
    source_hash = as_text(manifest.get("source_hash")).lower().removeprefix("sha256:")
    if not source_hash:
        return ""
    try:
        from ai_test_asset_center.enterprise_source_registry import load_source_content

        return load_source_content(project, source_hash, root=root)
    except Exception:
        return ""


def latest_registered_source(project: str, root: Path) -> tuple[str, dict[str, str]]:
    try:
        from ai_test_asset_center.enterprise_source_registry import list_source_assets, load_source_content

        assets = list_source_assets(project, root=root)
    except Exception:
        return "", {}
    if not assets:
        return "", {}

    def _sort_key(item: dict[str, Any]) -> tuple[str, str]:
        return (as_text(item.get("updated_at_utc")), as_text(item.get("source_id")))

    latest = max((item for item in assets if isinstance(item, dict)), key=_sort_key, default={})
    source_hash = as_text(latest.get("latest_source_hash")).lower().removeprefix("sha256:")
    source_id = as_text(latest.get("source_id"))
    if not source_id or not source_hash:
        return "", {}
    try:
        content = load_source_content(project, source_hash, root=root)
    except Exception:
        return "", {}
    manifest = {
        "source_id": source_id,
        "source_hash": source_hash,
        "source_version_id": as_text(latest.get("latest_version_id")),
        "source_origin": "registered_source_registry",
        "source_type": as_text(latest.get("source_type")),
    }
    return content, {key: value for key, value in manifest.items() if value}


def prepare_scan_body_for_campaign(project: str, root: Path, body: dict[str, Any]) -> dict[str, Any]:
    """Fill scan body gaps from the immutable source registry before legacy routing."""
    prepared = dict(body or {})
    api_doc = as_text(prepared.get("api_doc") or prepared.get("api_doc_text"))
    manifest = source_manifest_from_body(prepared)

    if not api_doc and manifest:
        content = load_source_content_from_manifest(project, root, manifest)
        if content:
            prepared["api_doc"] = content
            prepared["source_manifest"] = manifest
            return prepared

    if not api_doc and not manifest:
        content, latest_manifest = latest_registered_source(project, root)
        if content and latest_manifest:
            prepared["api_doc"] = content
            prepared["source_manifest"] = latest_manifest
            return prepared

    if manifest:
        prepared["source_manifest"] = manifest
    return prepared


def build_campaign_context_from_scan_body(body: dict[str, Any]) -> dict[str, Any]:
    """Build the V12 campaign_context from the frontend scan contract."""
    context: dict[str, Any] = {}
    manifest = source_manifest_from_body(body)
    if manifest:
        context["source_manifest"] = manifest

    for key in (
        "base_url",
        "ui_base_url",
        "ui_base_url_source",
        "scope_id",
        "environment_ref",
        "target_environment",
        "execution_approval_id",
        "execution_mode",
    ):
        value = as_text(body.get(key))
        if value:
            context[key] = value

    context["execution_mode"] = default_scan_execution_mode(body)

    test_data_contract = default_scan_test_data_contract(body)
    if test_data_contract:
        context["test_data_contract"] = test_data_contract

    release_policy = as_dict(body.get("release_policy"))
    if release_policy:
        context["release_policy"] = release_policy
    page_agent_bridge = as_dict(body.get("page_agent_bridge"))
    if page_agent_bridge:
        context["page_agent_bridge"] = page_agent_bridge
    external_signal_requests = body.get("external_signal_requests")
    if isinstance(external_signal_requests, list) and external_signal_requests:
        context["external_signal_requests"] = external_signal_requests
    ui_execution_requests = default_scan_ui_execution_requests(body)
    if ui_execution_requests:
        context["ui_execution_requests"] = ui_execution_requests
    ui_test_data_requests = body.get("ui_test_data_requests")
    if isinstance(ui_test_data_requests, list) and ui_test_data_requests:
        context["ui_test_data_requests"] = ui_test_data_requests
    return context
