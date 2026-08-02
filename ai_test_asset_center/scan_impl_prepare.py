"""Pre-pipeline scan preparation: context, source, contracts, and preflight.

Extracted from ``__main__._scan_impl`` so the scan entrypoint stays focused on
pipeline invocation and post-run projection.
"""
from __future__ import annotations

import time
import logging
from pathlib import Path
from typing import Any, Optional

from .product_scan_mainline import (
    _apply_scan_execution_defaults,
    _bind_discovery_mainline_identity,
    _first_text,
    _gap,
    _reject_evaluator_private_context,
    _safe_project,
    _scan_campaign_context_defaults,
)
from .enterprise_pilot_runtime import (
    _bind_preflight_test_credentials,
    _has_login_material,
)
from .scan_execution_outcome import _blocked_result
from .scan_source_runtime import (
    _load_project_prd_text,
    _load_registered_source,
    _load_schema_assets,
    _runtime_contract,
    _source_catalog,
    _source_contract,
    _source_manifest,
)


_LOGGER = logging.getLogger(__name__)


def prepare_scan_before_pipeline(
    project: str,
    root: Optional[Path] = None,
    *,
    prd_text: str = "",
    api_doc_path: str = "",
    api_doc_text: str = "",
    base_url: str = "",
    output_dir: Optional[Path] = None,
    save_report: bool = True,
    campaign_context: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """Resolve context/source/contracts before ``run_v12_pipeline``.

    Returns ``{"status": "early", "result": ...}`` on fail-closed exits, or
    ``{"status": "ready", ...prepared fields...}`` when the pipeline may run.
    """
    context = dict(campaign_context or {})
    # First-class merge of private-pilot ContextVar campaign context. Nested or
    # continuous callers may bind pending metadata without monkey-patching scan().
    try:
        from .private_pilot_scan_context_contract import current_scan_campaign_context

        pending_context = current_scan_campaign_context()
    except Exception as exc:
        _LOGGER.warning(
            "scan_campaign_context_resolution_failed error_type=%s",
            type(exc).__name__,
            exc_info=True,
        )
        pending_context = None
    if isinstance(pending_context, dict) and pending_context:
        for key, value in pending_context.items():
            if key == "base_url":
                continue
            if value and (key not in context or not context.get(key)):
                context[key] = value
        if pending_context.get("base_url") and not base_url:
            base_url = str(pending_context["base_url"]).rstrip("/")
    _reject_evaluator_private_context(context)
    root = Path(root or Path.cwd())
    project = str(project or "").strip()
    if not project:
        return {
            "status": "early",
            "result": {"success": False, "error": "project is required"},
        }

    # Session health gate: auto-detect and recover from stale/corrupt sessions.
    # Long-running loops can leave behind FAILED_TERMINAL or orphaned RUNNING
    # leases that block all subsequent scan() calls.
    try:
        from .loop_runtime import LoopRuntimeSession

        session_health = LoopRuntimeSession.ensure_session_healthy(
            project, root / "platform_outputs" / _safe_project(project)
        )
        if not session_health.get("can_proceed", True):
            return {
                "status": "early",
                "result": {
                    "success": False,
                    "error": "session_unhealthy",
                    "session_health": session_health,
                    "hint": "A stale or corrupt loop session is blocking scans. "
                    "Try restarting the backend service or manually running: "
                    "LoopRuntimeSession.force_reset_stale_session(project_id, output_dir)",
                },
            }
        if session_health.get("action") == "auto_reset":
            import sys as _sys

            print(
                f"[scan] auto-recovered from stale session: "
                f"{session_health.get('reset_summary', {}).get('cleaned', [])}",
                file=_sys.stderr,
                flush=True,
            )
    except Exception as exc:
        # Never block a scan due to a session-health check failure itself;
        # the check is advisory.
        _LOGGER.warning(
            "session_health_check_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
            exc_info=True,
        )

    context_defaults = _scan_campaign_context_defaults(project, root)
    if context_defaults.get("scope_id") and not str(context.get("scope_id") or "").strip():
        context["scope_id"] = context_defaults["scope_id"]
    if context_defaults.get("environment_ref") and not str(
        context.get("environment_ref") or context.get("target_environment") or ""
    ).strip():
        context["environment_ref"] = context_defaults["environment_ref"]
    if context_defaults.get("environment_type") and not _first_text(
        context.get("environment_type"),
        context.get("environment_kind"),
        context.get("environment_class"),
    ):
        context["environment_type"] = context_defaults["environment_type"]
    # Keep every product entrypoint on the same execution contract.
    context = _apply_scan_execution_defaults(context, base_url)
    if api_doc_path and not api_doc_text:
        try:
            api_doc_text = Path(api_doc_path).read_text(encoding="utf-8")
        except OSError as exc:
            return {
                "status": "early",
                "result": {
                    "success": False,
                    "error": f"api_doc_path is unreadable: {exc}",
                },
            }
    if not str(api_doc_text or "").strip():
        api_doc_text = _load_registered_source(project, root, context)
    if not str(api_doc_text or "").strip():
        return {
            "status": "early",
            "result": {
                "success": False,
                "error": (
                    "api_doc_text, api_doc_path, or a registered "
                    "source_manifest is required"
                ),
            },
        }

    # Keep immutable source identity separate from the derived, merged API
    # catalog. Enrichment may add other registered documents for planning, but
    # it must never rewrite the primary source hash recorded by the customer.
    source_api_doc_text = api_doc_text
    try:
        from .api_doc_assets import enrich_api_spec_text

        api_doc_text = enrich_api_spec_text(root, project, api_doc_text)
    except Exception as exc:
        _LOGGER.warning(
            "api_spec_enrichment_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
            exc_info=True,
        )
    context["_source_verification_text"] = source_api_doc_text

    started = time.time()
    manifest = _source_manifest(root, project, context, api_doc_path, source_api_doc_text)
    context["source_manifest"] = {
        "source_id": manifest["source_id"],
        "source_hash": manifest["source_hash"],
        "source_version_id": manifest["source_version_id"],
        "source_origin": manifest["source_origin"],
    }
    provenance_gaps = _source_contract(manifest)
    approved_base_url, runtime_gaps, initial_runtime_contract = _runtime_contract(
        context, base_url, manifest
    )
    if base_url and context.get("runtime_scenario_contract"):
        from .runtime_scenario_contract_gate import runtime_scenario_contract_gaps

        scenario_gaps = runtime_scenario_contract_gaps(context)
        if scenario_gaps:
            missing_requirements = sorted(
                {
                    str(item.get("code") or "")
                    for item in scenario_gaps
                    if str(item.get("code") or "")
                }
            )
            blocked_runtime_contract = {
                **initial_runtime_contract,
                "status": "blocked",
                "reason": "runtime_scenario_contract_blocked",
                "approved_base_url": "",
                "missing_requirements": missing_requirements,
            }
            return {
                "status": "early",
                "result": _blocked_result(
                    project,
                    root,
                    started,
                    provenance_gaps + runtime_gaps + scenario_gaps,
                    blocked_runtime_contract,
                    context,
                    save_report,
                    output_dir,
                ),
            }
    if provenance_gaps:
        return {
            "status": "early",
            "result": _blocked_result(
                project,
                root,
                started,
                provenance_gaps + runtime_gaps,
                initial_runtime_contract,
                context,
                save_report,
                output_dir,
            ),
        }

    input_gaps: list[dict[str, str]] = []
    if not str(prd_text or "").strip():
        prd_text = _load_project_prd_text(root, project)
    if not str(prd_text or "").strip():
        input_gaps.append(
            _gap(
                "PRD_SOURCE_MISSING",
                "No requirement source was supplied; only API/schema facts can be planned.",
            )
        )
        prd_text = _source_catalog(api_doc_text)
    schema_text = _load_schema_assets(root, project)
    if not schema_text:
        input_gaps.append(
            _gap(
                "DATABASE_SCHEMA_MISSING",
                "No project-scoped schema asset is available for data observation planning.",
            )
        )
    input_gaps.extend(runtime_gaps)

    diagnostics: dict[str, Any] = {"ready": True, "checks": []}
    diagnostics_config: dict[str, Any] = {}
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
        profile = registry.get("test_profile") if isinstance(registry, dict) else {}
        if isinstance(profile, dict):
            diagnostics_config = dict(profile)
    except Exception as exc:
        _LOGGER.warning(
            "connector_registry_load_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
            exc_info=True,
        )
        diagnostics_config = {}
    credential_source = "not_resolved"
    try:
        from .scan_diagnostics import run_preflight

        if base_url and not diagnostics_config.get("api_base_url"):
            diagnostics_config["api_base_url"] = base_url
        # Preflight must consume the same exact target grant as the runtime
        # executor.
        if approved_base_url:
            diagnostics_config["approved_base_url"] = approved_base_url
        environment_kind = str(
            context.get("environment_kind")
            or context.get("environment_type")
            or context.get("target_environment_kind")
            or ""
        ).strip()
        if environment_kind:
            diagnostics_config["environment_kind"] = environment_kind
            diagnostics_config["environment_type"] = environment_kind
        diagnostics_config.setdefault(
            "execution_mode", str(context.get("execution_mode") or "")
        )
        credential_source = _bind_preflight_test_credentials(
            project,
            root,
            diagnostics_config,
        )
        diagnostics_config["credential_source"] = credential_source
        diagnostics = dict(run_preflight(diagnostics_config, api_doc_text))
        diagnostics["credential_source"] = credential_source
    except Exception as exc:
        _LOGGER.warning(
            "preflight_failed project=%s error_type=%s",
            project,
            type(exc).__name__,
            exc_info=True,
        )
        diagnostics = {
            "ready": False,
            "checks": [],
            "summary": f"preflight_unavailable:{type(exc).__name__}",
            "credential_source": credential_source,
        }

    context = _bind_discovery_mainline_identity(
        project=project,
        context=context,
        started=started,
    )
    return {
        "status": "ready",
        "project": project,
        "root": root,
        "context": context,
        "prd_text": prd_text,
        "api_doc_text": api_doc_text,
        "base_url": base_url,
        "approved_base_url": approved_base_url,
        "started": started,
        "manifest": manifest,
        "initial_runtime_contract": initial_runtime_contract,
        "input_gaps": input_gaps,
        "diagnostics": diagnostics,
        "schema_text": schema_text,
        "output_dir": output_dir,
        "save_report": save_report,
    }
