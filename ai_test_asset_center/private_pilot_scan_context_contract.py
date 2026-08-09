from __future__ import annotations

"""Private-pilot scan campaign context contract helpers.

This module owns the pure contract-building layer for private-pilot scans:
source manifest normalization, immutable source loading, scan body preparation,
and campaign_context construction. Callers bind ``SCAN_CAMPAIGN_CONTEXT`` /
``CONTINUOUS_CAMPAIGN_CONTEXTS`` first-class; ``private_pilot_scan_context_patch``
only records runtime-support status for health surfaces.
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


def declared_adapters_from_scan_body(body: dict[str, Any]) -> list[str] | None:
    """Normalize an explicit adapter declaration without inferring capabilities.

    ``None`` means the caller did not submit the field. An explicit empty list is
    preserved, because it is materially different from a missing declaration.
    Unknown adapter names are rejected at the customer boundary instead of being
    silently retained in an immutable campaign contract and ignored later.
    """
    if "declared_adapters" not in body:
        return None
    raw = body.get("declared_adapters")
    if not isinstance(raw, list):
        raise ValueError("declared_adapters_not_list")

    from ai_test_asset_center.adapter_capability import (
        ADAPTER_TO_OBSERVATION_SURFACE,
    )

    known = set(ADAPTER_TO_OBSERVATION_SURFACE)
    adapters: list[str] = []
    for value in raw:
        if not isinstance(value, str):
            raise ValueError("declared_adapter_name_not_string")
        name = value.strip()
        if not name:
            raise ValueError("declared_adapter_name_empty")
        if len(name) > 80:
            raise ValueError("declared_adapter_name_too_long")
        if name not in known:
            raise ValueError(f"declared_adapter_unknown:{name}")
        if name not in adapters:
            adapters.append(name)
    return adapters


def formal_event_contracts_from_scan_body(
    body: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Preserve explicit event contracts for the strict source overlay.

    This boundary validates only the transport shape. Source identity,
    operation/actor identity, observer path, field bindings and observation
    windows remain owned by ``scan_event_contract_overlay``. An explicit empty
    list is preserved; malformed rows are rejected instead of disappearing.
    """
    if "event_formal_contracts" not in body:
        return None
    raw = body.get("event_formal_contracts")
    if not isinstance(raw, list):
        raise ValueError("event_formal_contracts_not_list")
    contracts: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"event_formal_contract_not_object:{index}")
        contracts.append(dict(value))
    return contracts


def _list_contract_field_from_scan_body(
    body: dict[str, Any],
    field: str,
    *,
    error_prefix: str,
) -> list[dict[str, Any]] | None:
    """Preserve explicit typed contract rows for the strict chain overlay.

    Transport-shape validation only; source identity, trigger/actor identity,
    observer fields, consumers, effects and windows remain owned by
    ``message_chain_contract_overlay``. An explicit empty list is preserved;
    malformed rows are rejected instead of disappearing.
    """
    if field not in body:
        return None
    raw = body.get(field)
    if not isinstance(raw, list):
        raise ValueError(f"{field}_not_list")
    rows: list[dict[str, Any]] = []
    for index, value in enumerate(raw):
        if not isinstance(value, dict):
            raise ValueError(f"{error_prefix}_not_object:{index}")
        rows.append(dict(value))
    return rows


def message_chain_contracts_from_scan_body(
    body: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Explicit source-bound message-chain contracts (event name/trigger/effects)."""
    return _list_contract_field_from_scan_body(
        body,
        "message_chain_contracts",
        error_prefix="message_chain_contract",
    )


def runtime_event_surfaces_from_scan_body(
    body: dict[str, Any],
) -> list[dict[str, Any]] | None:
    """Operator-declared runtime event surfaces (degradation channel).

    These rows carry no business semantics by default; the overlay admits them
    without source refs and the receipt marks the channel as runtime
    observation. They make the event face observable even when no written
    event contract exists.
    """
    return _list_contract_field_from_scan_body(
        body,
        "runtime_event_surfaces",
        error_prefix="runtime_event_surface",
    )


def _is_production_like_environment(environment_type: str = "") -> bool:
    # Use the same fail-closed classifier as the governed write executor.  The
    # TestOps helper intentionally recognizes only canonical environment names;
    # relying on it here misclassified names such as ``customer-production`` as
    # writable.  A single classifier keeps scan defaults and the final request
    # gate consistent.
    from ai_test_asset_center.sandbox_write_executor import is_production_environment

    return is_production_environment(as_text(environment_type))


def default_scan_execution_mode(body: dict[str, Any]) -> str:
    explicit = as_text(body.get("execution_mode"))
    if explicit:
        return explicit
    if not as_text(body.get("base_url")):
        return "safe_read_only"
    environment_type = as_text(
        body.get("environment_type")
        or body.get("environment_kind")
        or body.get("environment_class")
    )
    if _is_production_like_environment(environment_type):
        return "safe_read_only"
    from ai_test_asset_center.sandbox_write_executor import is_test_or_sandbox_environment

    # Unknown or undeclared targets are fail-closed for writes. A name/type
    # must explicitly carry a recognized non-production classification.
    if not is_test_or_sandbox_environment(environment_type):
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
        "environment_type",
        "environment_kind",
        "environment_class",
        "target_environment",
        "execution_approval_id",
        "execution_mode",
        # v12_pipeline reads context["campaign_rerun_key"] / ["campaign_restart_key"] to
        # start a fresh campaign instead of resuming one. Neither was in this passthrough,
        # so the mechanism was unreachable from the only entry point the product exposes.
        "campaign_rerun_key",
        "campaign_restart_key",
    ):
        value = as_text(body.get(key))
        if value:
            context[key] = value

    context["execution_mode"] = default_scan_execution_mode(body)

    adapters = declared_adapters_from_scan_body(body)
    if adapters is not None:
        context["declared_adapters"] = adapters

    event_contracts = formal_event_contracts_from_scan_body(body)
    if event_contracts is not None:
        context["event_formal_contracts"] = event_contracts

    chain_contracts = message_chain_contracts_from_scan_body(body)
    if chain_contracts is not None:
        context["message_chain_contracts"] = chain_contracts

    runtime_surfaces = runtime_event_surfaces_from_scan_body(body)
    if runtime_surfaces is not None:
        context["runtime_event_surfaces"] = runtime_surfaces

    # These controls are consumed by discovery_runtime_planning, but previously
    # the product's only customer scan entry discarded them while building the
    # immutable campaign context. Preserve explicit False as well as True, and
    # fail closed on non-boolean JSON values so a string such as "false" cannot
    # accidentally enable a discovery round.
    for key in (
        "agent_semantic_linking_enabled",
        "runtime_interface_discovery_enabled",
    ):
        if key not in body:
            continue
        value = body.get(key)
        if not isinstance(value, bool):
            raise ValueError(f"{key}_not_boolean")
        context[key] = value

    # Governed interface discovery (read-only GET probes that bind real path
    # ids) is part of normal execution on a declared non-production target:
    # without it, path placeholders like /orders/:id never bind a real id and
    # every write obligation on that operation defers at compile time. Default
    # it on when write execution is approved and the operator did not set the
    # control explicitly. safe_read_only (the operator kill switch) and an
    # explicit False both keep it off.
    if "runtime_interface_discovery_enabled" not in context:
        context["runtime_interface_discovery_enabled"] = (
            context.get("execution_mode") == "approved_sandbox_write"
        )

    if "runtime_interface_discovery_budget" in body:
        budget = body.get("runtime_interface_discovery_budget")
        if (
            isinstance(budget, bool)
            or not isinstance(budget, int)
            or not 1 <= budget <= 5000
        ):
            raise ValueError("runtime_interface_discovery_budget_invalid")
        context["runtime_interface_discovery_budget"] = budget

    test_data_contract = default_scan_test_data_contract(body)
    if test_data_contract:
        context["test_data_contract"] = test_data_contract

    release_policy = as_dict(body.get("release_policy"))
    if release_policy:
        context["release_policy"] = release_policy
    # Thread a client-supplied, source-bound runtime scenario contract through to
    # the V12 campaign context. This lets automation / CI / benchmark harnesses (and
    # the Run Center advanced mode) submit an EXPLICIT deterministic scenario set for
    # reproducible real execution instead of depending on LLM discovery to derive
    # scenarios. It weakens no downstream safety gate.
    runtime_scenario_contract = as_dict(body.get("runtime_scenario_contract"))
    if runtime_scenario_contract:
        context["runtime_scenario_contract"] = runtime_scenario_contract
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
