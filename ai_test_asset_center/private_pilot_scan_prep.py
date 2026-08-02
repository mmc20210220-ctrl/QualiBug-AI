"""Scan preparation, follow-up UI requests, local approval, and ingest auto-scan.

Extracted from ``private_pilot_service`` so the HTTP handler module stays thinner.
Symbols remain importable from ``private_pilot_service`` for compatibility.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
import hashlib
from typing import Any
from urllib.parse import urljoin

from .private_pilot_continuous import _update_continuous_state
from .private_pilot_debug_client import _dbg_fingerprint_payload, _dbg_report
from .private_pilot_json_io import _write_json_object_atomic
from .private_pilot_project_assets import _first_text, _truthy_env
from .real_project_onboarding import _safe_project_id


def _run_ingest_auto_scan(
    *,
    root: Path,
    project: str,
    body: dict[str, Any],
    raw: bytes,
    doc_type: str,
    source_manifest: dict[str, Any],
) -> None:
    """Run an ingest-triggered scan and persist a failure receipt before raising."""
    phase = "runtime_defaults"
    try:
        scan_context: dict[str, Any] = {}
        if source_manifest.get("source_id") and source_manifest.get("source_hash"):
            scan_context["source_manifest"] = dict(source_manifest)
        defaults = _resolve_scan_runtime_defaults(project, root, body)
        if defaults.get("scope_id"):
            scan_context["scope_id"] = defaults["scope_id"]
        if defaults.get("environment_ref"):
            scan_context["environment_ref"] = defaults["environment_ref"]
        api_text = raw.decode("utf-8", errors="replace") if doc_type in {"openapi", "markdown_api"} else ""

        phase = "scan"
        from .__main__ import scan as scan_project

        scan_result = scan_project(
            project,
            root,
            api_doc_text=api_text,
            campaign_context=scan_context,
            save_report=True,
        )
        if not isinstance(scan_result, dict):
            raise TypeError("ingest auto-scan result must be an object")

        phase = "state_update"
        _update_continuous_state(root, project, scan_result)
    except Exception as exc:
        _write_json_object_atomic(
            root / "platform_outputs" / project / "auto_scan_last_error.json",
            {
                "schema": "qualibug.auto-scan-failure.v1",
                "project": project,
                "doc_type": doc_type,
                "phase": phase,
                "error_type": type(exc).__name__,
                "error": str(exc),
                "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            },
        )
        raise


def _http_url_text(value: Any) -> str:
    text = str(value or "").strip()
    if text.startswith(("http://", "https://")):
        return text
    return ""
def _frontend_entry_url_candidates(frontend_urls: Any) -> list[dict[str, Any]]:
    if not isinstance(frontend_urls, dict):
        return []
    candidates: list[dict[str, Any]] = []
    for key, value in sorted(frontend_urls.items(), key=lambda item: str(item[0]).lower()):
        url = ""
        explicit_default = False
        if isinstance(value, dict):
            url = _first_text(
                _http_url_text(value.get("ui_base_url")),
                _http_url_text(value.get("url")),
                _http_url_text(value.get("base_url")),
                _http_url_text(value.get("entry_url")),
                _http_url_text(value.get("target_url")),
                _http_url_text(value.get("href")),
            )
            explicit_default = any(
                value.get(flag) is True for flag in ("default", "primary", "is_default", "is_primary")
            )
        else:
            url = _http_url_text(value)
            explicit_default = str(key or "").strip().lower() in {"default", "primary", "main", "entry"}
        if url:
            candidates.append({"key": str(key or "").strip(), "url": url, "default": explicit_default})
    return candidates
def _resolve_ui_base_url_from_profile(profile: dict[str, Any]) -> tuple[str, bool, str]:
    if not isinstance(profile, dict):
        return "", False, ""
    explicit = _first_text(
        _http_url_text(profile.get("ui_base_url")),
        _http_url_text(profile.get("target_ui_base_url")),
        _http_url_text(profile.get("frontend_default_url")),
        _http_url_text(profile.get("entry_url")),
    )
    if explicit:
        return explicit, False, "connector_registry.test_profile.ui_base_url"
    candidates = _frontend_entry_url_candidates(profile.get("frontend_urls"))
    default_candidates = [item for item in candidates if item.get("default") is True]
    if default_candidates:
        default_entry = default_candidates[0]
        return (
            str(default_entry.get("url") or ""),
            False,
            f"connector_registry.test_profile.frontend_urls.{str(default_entry.get('key') or 'default').strip()}",
        )
    if len(candidates) == 1:
        single_entry = candidates[0]
        return (
            str(single_entry.get("url") or ""),
            False,
            f"connector_registry.test_profile.frontend_urls.{str(single_entry.get('key') or 'default').strip()}",
        )
    return "", len(candidates) > 1, ""
def _is_local_private_service(server: Any) -> bool:
    server_host = str(getattr(server, "server_address", ("", 0))[0] or "")
    # Same fail-closed opt-in as AuthScopeMixin: QUALIBUG_LOCAL_DEV_ACTOR defaults
    # OFF. Default-on here previously treated every localhost private-pilot bind as
    # "local dev mode" for SSRF allow_internal and scan prep, even when auth was
    # requiring real actor headers -- an inconsistent, invisible safety hole.
    return (
        server_host in {"127.0.0.1", "localhost", "::1"}
        and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        and _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "")
    )
def _validate_scan_base_url(base_url: str, *, local_dev_mode: bool) -> None:
    if not str(base_url or "").strip():
        return
    from .ssrf_guard import validate_url

    validate_url(str(base_url).strip(), allow_internal=local_dev_mode)
def _resolve_scan_runtime_defaults(project: str, root: Path, body: dict[str, Any]) -> dict[str, Any]:
    scope_id = _first_text(body.get("scope_id"), os.environ.get("QUALIBUG_SCOPE_ID"))
    environment_ref = _first_text(
        body.get("environment_ref"),
        body.get("target_environment"),
        os.environ.get("QUALIBUG_ENVIRONMENT_REF"),
        os.environ.get("QUALIBUG_TARGET_ENVIRONMENT"),
    )
    environment_type = _first_text(
        body.get("environment_type"),
        body.get("environment_kind"),
        body.get("environment_class"),
        os.environ.get("QUALIBUG_ENVIRONMENT_TYPE"),
        os.environ.get("QUALIBUG_ENVIRONMENT_KIND"),
    )
    ui_base_url = ""
    ui_base_url_source = ""
    explicit_ui_base_url = _http_url_text(body.get("ui_base_url"))
    if explicit_ui_base_url:
        ui_base_url = explicit_ui_base_url
        ui_base_url_source = "request_body.ui_base_url"
    else:
        env_ui_base_url = _first_text(
            _http_url_text(os.environ.get("QUALIBUG_BROWSER_UI_BASE_URL")),
            _http_url_text(os.environ.get("QUALIBUG_TARGET_UI_BASE_URL")),
        )
        if env_ui_base_url:
            ui_base_url = env_ui_base_url
            ui_base_url_source = (
                "env.QUALIBUG_BROWSER_UI_BASE_URL"
                if _http_url_text(os.environ.get("QUALIBUG_BROWSER_UI_BASE_URL"))
                else "env.QUALIBUG_TARGET_UI_BASE_URL"
            )
    registry_profile: dict[str, Any] = {}
    try:
        from .enterprise_pilot_runtime import load_connector_registry

        registry = load_connector_registry(project, root)
        profile = registry.get("test_profile") if isinstance(registry, dict) else {}
        if isinstance(profile, dict):
            registry_profile = profile
    except Exception:
        registry_profile = {}
    try:
        from .deployment_config_resolver import resolve_deployment_config

        deployment = resolve_deployment_config(project_id=project, root=root)
    except Exception:
        deployment = {}
    deployment_sources = deployment.get("_sources") if isinstance(deployment.get("_sources"), dict) else {}
    deployment_environment_type = (
        deployment.get("environment_class")
        if str(deployment_sources.get("environment_class") or "") in {"project_config", "env", "override"}
        else ""
    )
    scope_id = _first_text(
        scope_id,
        registry_profile.get("scope_id"),
        registry_profile.get("deployment_scope_id"),
        registry_profile.get("project_scope_id"),
        deployment.get("deployment_scope_id"),
    )
    environment_ref = _first_text(
        environment_ref,
        registry_profile.get("environment_ref"),
        registry_profile.get("target_environment"),
        registry_profile.get("environment_class"),
        registry_profile.get("environment"),
        deployment.get("environment_class"),
    )
    environment_type = _first_text(
        environment_type,
        registry_profile.get("environment_type"),
        registry_profile.get("environment_kind"),
        registry_profile.get("environment_class"),
        deployment.get("environment_type"),
        deployment.get("environment_kind"),
        deployment_environment_type,
    )
    ambiguous_ui_targets = False
    if not ui_base_url:
        resolved_ui_base_url, ambiguous_ui_targets, resolved_ui_base_url_source = _resolve_ui_base_url_from_profile(registry_profile)
        ui_base_url = _first_text(ui_base_url, resolved_ui_base_url)
        ui_base_url_source = _first_text(ui_base_url_source, resolved_ui_base_url_source)
    return {
        "scope_id": scope_id[:160],
        "environment_ref": environment_ref[:160],
        "environment_type": environment_type[:80].lower(),
        "ui_base_url": ui_base_url[:500],
        "ui_base_url_source": ui_base_url_source[:200],
        "ui_target_resolution": "ambiguous" if ambiguous_ui_targets and not ui_base_url else "resolved",
    }
def _read_project_prd_text(project: str, root: Path) -> str:
    try:
        from .__main__ import _load_project_prd_text

        aggregated = str(_load_project_prd_text(root, project) or "").strip()
        if aggregated:
            return aggregated
    except Exception:
        pass

    def _safe_read(path: Path) -> str:
        try:
            resolved = path.resolve()
            root_resolved = root.resolve()
            if root_resolved != resolved and root_resolved not in resolved.parents:
                return ""
            if not resolved.exists() or not resolved.is_file():
                return ""
            return resolved.read_text(encoding="utf-8", errors="replace").strip()
        except Exception:
            return ""

    try:
        from .enterprise_knowledge_center import _load_registry

        registry = _load_registry(project, root)
        ranked_sources: list[tuple[int, Path]] = []
        for source in registry.get("sources", []):
            if not isinstance(source, dict) or source.get("status") != "active":
                continue
            source_type = str(source.get("source_type") or "").strip().lower()
            filename = str(source.get("original_name") or "").strip().lower()
            score = 0
            if source_type == "prd":
                score = 100
            elif source_type == "mrd":
                score = 90
            elif source_type == "business_rules":
                score = 70
            elif source_type == "collaboration_document":
                score = 60
            elif source_type == "other_document":
                score = 40
            if any(token in filename for token in ("prd", "mrd", "requirement", "spec")):
                score += 10
            stored_path = str(source.get("stored_path") or "").strip()
            if score > 0 and stored_path:
                ranked_sources.append((score, root / stored_path))
        for _score, path in sorted(ranked_sources, key=lambda item: (-item[0], str(item[1]).lower())):
            text = _safe_read(path)
            if text:
                return text
    except Exception:
        pass

    input_dir = root / "platform_workspace" / project / "input"
    candidates: list[Path] = []
    for pattern in ("PRD*", "prd*", "*requirement*", "*Requirement*", "*spec*"):
        try:
            candidates.extend(path for path in input_dir.glob(pattern) if path.is_file())
        except Exception:
            continue
    seen: set[str] = set()
    for path in sorted(candidates, key=lambda item: str(item).lower()):
        key = str(path.resolve())
        if key in seen:
            continue
        seen.add(key)
        text = _safe_read(path)
        if text:
            return text
    return ""
def _predicted_campaign_binding(project: str, root: Path, body: dict[str, Any]) -> dict[str, str]:
    api_doc = str(body.get("api_doc") or body.get("api_doc_text") or "")
    manifest = body.get("source_manifest") if isinstance(body.get("source_manifest"), dict) else {}
    scope_id = str(body.get("scope_id") or "").strip()
    environment_ref = str(body.get("environment_ref") or body.get("target_environment") or "").strip()
    source_id = str(manifest.get("source_id") or "").strip()
    source_hash = str(manifest.get("source_hash") or "").strip().lower().removeprefix("sha256:")
    if not api_doc or not scope_id or not environment_ref or not source_id or len(source_hash) != 64:
        return {}
    try:
        from .__main__ import _load_schema_assets
        from .universal_api_parser import detect_format, parse_to_openapi
        from .v12_pipeline import _campaign_context

        prd_text = str(body.get("prd") or "")
        normalized_api_doc = api_doc
        from .api_doc_assets import enrich_api_spec_text

        normalized_api_doc = enrich_api_spec_text(root, project, normalized_api_doc) or normalized_api_doc
        if detect_format(normalized_api_doc) not in {"openapi3", "unknown"}:
            normalized = parse_to_openapi(normalized_api_doc)
            if normalized.get("paths"):
                normalized_api_doc = json.dumps(normalized, ensure_ascii=False, default=str)
        schema_text = _load_schema_assets(root, project)
        campaign, _store, _mode = _campaign_context(
            project,
            prd_text,
            normalized_api_doc,
            schema_text,
            str(body.get("base_url") or "").strip(),
            # The pipeline auto-scales the per-round budget to the candidate pool --
            # "drain the pool in ~2 rounds ... no env tuning", per _auto_scale_slice_budget --
            # and then takes min() with whatever is passed here. A hardcoded 100 therefore
            # overrode the auto-scaler downward and became the binding constraint: 652 of
            # 1189 obligations on a live target ended at OBLIGATION_BUDGET_REACHED, never
            # reaching a gate that could say anything about the system under test.
            #
            # Passing the module's own ceiling lets the auto-scaler govern, which is what
            # it exists for. Measured: budget 100 -> 800 (auto-scaled to the pool),
            # OBLIGATION_BUDGET_REACHED 652 -> 35, and CONTRACT_ORACLE_HARNESS_FAILED
            # 12 -> 0.
            {"slice_budget": _ABS_MAX_SLICE_BUDGET, "round_limit": 16},
            {
                "scope_id": scope_id,
                "environment_ref": environment_ref,
                "source_manifest": manifest,
            },
            root,
            api_doc,
        )
    except Exception:
        return {}
    # #region debug-point B:predicted-binding
    _dbg_report(
        hypothesis_id="B",
        msg="[DEBUG] predicted campaign binding",
        data={
            "body": _dbg_fingerprint_payload(body),
            "normalized_api_sha": hashlib.sha256(normalized_api_doc.encode("utf-8")).hexdigest() if normalized_api_doc else "",
            "normalized_api_len": len(normalized_api_doc),
            "schema_sha": hashlib.sha256(schema_text.encode("utf-8")).hexdigest() if schema_text else "",
            "schema_len": len(schema_text),
            "campaign_id": campaign.campaign_id,
            "scope_id": scope_id,
            "environment_ref": environment_ref,
            "source_hash": source_hash,
        },
    )
    return {
        "campaign_id": campaign.campaign_id,
        "scope_id": scope_id,
        "environment_ref": environment_ref,
        "source_hash": source_hash,
    }
def _maybe_issue_local_runtime_approval(project: str, root: Path, actor: dict[str, str], body: dict[str, Any], *, local_dev_mode: bool) -> str:
    if not local_dev_mode:
        return ""
    if str(body.get("execution_approval_id") or "").strip():
        return ""
    if str(body.get("base_url") or "").strip() == "":
        return ""
    execution_mode = str(body.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    if execution_mode not in {"safe_read_only", "approved_sandbox_write"}:
        return ""
    binding = _predicted_campaign_binding(project, root, body)
    if not binding:
        return ""
    try:
        from .execution_approvals import issue_execution_approval

        expires_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        approval = issue_execution_approval(
            project,
            root=root,
            campaign_id=binding["campaign_id"],
            scope_id=binding["scope_id"],
            environment_ref=binding["environment_ref"],
            source_hash=binding["source_hash"],
            target_base_url=str(body.get("base_url") or "").strip(),
            execution_mode=execution_mode,
            expires_at_utc=expires_at_utc,
            actor=actor,
        )
    except Exception:
        return ""
    return str(approval.get("approval_id") or "").strip()
def _prepare_v12_scan_body(project: str, root: Path, actor: dict[str, str], body: dict[str, Any], *, local_dev_mode: bool) -> dict[str, Any]:
    from .private_pilot_scan_context_contract import (
        default_scan_execution_mode,
        default_scan_ui_execution_requests,
        default_scan_test_data_contract,
        prepare_scan_body_for_campaign,
    )

    prepared = prepare_scan_body_for_campaign(project, root, body)
    if not str(prepared.get("prd") or "").strip():
        prd_text = _read_project_prd_text(project, root)
        if prd_text:
            prepared["prd"] = prd_text
    defaults = _resolve_scan_runtime_defaults(project, root, prepared)
    if defaults["scope_id"] and not str(prepared.get("scope_id") or "").strip():
        prepared["scope_id"] = defaults["scope_id"]
    if defaults["environment_ref"] and not str(prepared.get("environment_ref") or "").strip():
        prepared["environment_ref"] = defaults["environment_ref"]
    if defaults.get("environment_type") and not str(
        prepared.get("environment_type") or prepared.get("environment_kind") or prepared.get("environment_class") or ""
    ).strip():
        prepared["environment_type"] = defaults["environment_type"]
    if defaults.get("ui_base_url") and not str(prepared.get("ui_base_url") or "").strip():
        prepared["ui_base_url"] = str(defaults["ui_base_url"]).strip()
    if defaults.get("ui_base_url_source") and not str(prepared.get("ui_base_url_source") or "").strip():
        prepared["ui_base_url_source"] = str(defaults["ui_base_url_source"]).strip()
    if (
        str(defaults.get("ui_target_resolution") or "").strip() == "ambiguous"
        and not str(prepared.get("ui_base_url") or "").strip()
        and not (isinstance(prepared.get("ui_execution_requests"), list) and prepared.get("ui_execution_requests"))
    ):
        prepared["disable_ui_execution_autogen"] = True
        prepared["ui_target_resolution"] = {
            "status": "ambiguous",
            "reason": "MULTIPLE_FRONTEND_URLS_REQUIRE_UI_BASE_URL",
        }
    if str(prepared.get("base_url") or "").strip():
        if not str(prepared.get("execution_mode") or "").strip():
            prepared["execution_mode"] = default_scan_execution_mode(prepared)
        if not isinstance(prepared.get("test_data_contract"), dict):
            test_data_contract = default_scan_test_data_contract(prepared)
            if test_data_contract:
                prepared["test_data_contract"] = test_data_contract
        if not (isinstance(prepared.get("ui_execution_requests"), list) and prepared.get("ui_execution_requests")):
            ui_execution_requests = _load_followup_ui_execution_requests(project, root, prepared)
            if not ui_execution_requests:
                ui_execution_requests = default_scan_ui_execution_requests(prepared)
            if ui_execution_requests:
                prepared["ui_execution_requests"] = ui_execution_requests
        if not (isinstance(prepared.get("ui_test_data_requests"), list) and prepared.get("ui_test_data_requests")):
            ui_test_data_requests = _load_followup_ui_test_data_requests(project, root, prepared)
            if ui_test_data_requests:
                prepared["ui_test_data_requests"] = ui_test_data_requests
    approval_id = _maybe_issue_local_runtime_approval(project, root, actor, prepared, local_dev_mode=local_dev_mode)
    if approval_id:
        prepared["execution_approval_id"] = approval_id
    return prepared
def _load_followup_ui_execution_requests(project: str, root: Path, body: dict[str, Any]) -> list[dict[str, Any]]:
    if body.get("disable_ui_execution_autogen") is True:
        return []
    base_url = str(body.get("ui_base_url") or body.get("base_url") or "").strip()
    base_url_source = str(
        body.get("ui_base_url_source")
        or ("request_body.ui_base_url" if str(body.get("ui_base_url") or "").strip() else "request_body.base_url")
    ).strip()
    if not base_url:
        return []
    bridge = body.get("page_agent_bridge") if isinstance(body.get("page_agent_bridge"), dict) else {}
    bridge_url = str(bridge.get("url") or os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_URL") or "").strip()
    if not bridge_url:
        return []
    asset_path = root / "platform_workspace" / _safe_project_id(project) / "defect_discovery" / "ui_followup_execution_requests.json"
    if not asset_path.exists():
        return []
    try:
        payload = json.loads(asset_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
    severity_rank = {"P0": 0, "P1": 1, "P2": 2, "P3": 3}
    ranked = sorted(
        (dict(item) for item in items if isinstance(item, dict)),
        key=lambda item: (
            severity_rank.get(str(item.get("severity") or "P2").strip().upper(), 9),
            -float(item.get("confidence_score") or item.get("confidence") or 0.0),
            str(item.get("title") or ""),
        ),
    )
    requests: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in ranked[:5]:
        path = str(item.get("path") or "").strip() or "/"
        start_url = path if path.startswith(("http://", "https://")) else urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        title = str(item.get("title") or item.get("request_template_id") or "UI follow-up request").strip() or "UI follow-up request"
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        browser_plan = item.get("browser_plan") if isinstance(item.get("browser_plan"), dict) else {}
        request_id = str(item.get("request_template_id") or item.get("request_id") or "").strip()
        if not request_id:
            request_id = f"ui_followup_{len(requests) + 1}"
        if request_id in seen:
            continue
        seen.add(request_id)
        requests.append(
            {
                "request_id": request_id,
                "title": title,
                "provider": "page_agent",
                "task": str(item.get("task") or f"Re-open the candidate target and capture UI evidence for: {title}").strip(),
                "start_url": start_url,
                "execution_mode": "safe_read_only",
                "browser_plan": browser_plan
                or {
                    "execution_mode": "safe_read_only",
                    "steps": [
                        {"action": "goto", "url": path, "wait_until": "networkidle"},
                        {"action": "wait_for_load", "state": "networkidle"},
                        {"action": "screenshot", "full_page": True},
                    ],
                },
                "page_hints": [str(value).strip()[:500] for value in item.get("page_hints", []) if str(value).strip()],
                "success_criteria": dict(item.get("success_criteria") or {}) if isinstance(item.get("success_criteria"), dict) else {},
                "metadata": {
                    **metadata,
                    "auto_generated": True,
                    "request_origin": "private_pilot_service_followup_asset",
                    "bridge_mode": str(metadata.get("bridge_mode") or "page_agent_browser_plan"),
                    "resolved_start_url_base": base_url,
                    "resolved_start_url_base_source": base_url_source,
                    "resolved_path": path,
                },
            }
        )
    return requests
def _load_followup_ui_test_data_requests(project: str, root: Path, body: dict[str, Any]) -> list[dict[str, Any]]:
    if body.get("disable_ui_execution_autogen") is True:
        return []
    base_url = str(body.get("ui_base_url") or body.get("base_url") or "").strip()
    base_url_source = str(
        body.get("ui_base_url_source")
        or ("request_body.ui_base_url" if str(body.get("ui_base_url") or "").strip() else "request_body.base_url")
    ).strip()
    if not base_url:
        return []
    bridge = body.get("page_agent_bridge") if isinstance(body.get("page_agent_bridge"), dict) else {}
    bridge_url = str(bridge.get("url") or os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_URL") or "").strip()
    if not bridge_url:
        return []
    asset_path = root / "platform_workspace" / _safe_project_id(project) / "defect_discovery" / "ui_followup_test_data_requests.json"
    if not asset_path.exists():
        return []
    try:
        payload = json.loads(asset_path.read_text(encoding="utf-8") or "{}")
    except Exception:
        return []
    items = payload.get("items") if isinstance(payload, dict) and isinstance(payload.get("items"), list) else []
    requests: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        promotion = item.get("promotion") if isinstance(item.get("promotion"), dict) else {}
        browser_plan, request_origin, promoted_metadata = _resolve_followup_ui_test_data_browser_plan(metadata, item, promotion)
        if not browser_plan:
            continue
        path = str(item.get("path") or "").strip() or "/"
        start_url = path if path.startswith(("http://", "https://")) else urljoin(base_url.rstrip("/") + "/", path.lstrip("/"))
        request_id = str(item.get("request_template_id") or item.get("request_id") or "").strip() or "ui_test_data_followup"
        requests.append(
            {
                "request_id": request_id,
                "title": str(item.get("title") or request_id).strip(),
                "provider": "page_agent",
                "task": str(item.get("task") or "").strip(),
                "start_url": start_url,
                "execution_mode": "approved_sandbox_write",
                "browser_plan": {
                    **browser_plan,
                    "execution_mode": "approved_sandbox_write",
                    "write_approved": True,
                },
                "page_hints": [str(value).strip()[:500] for value in item.get("page_hints", []) if str(value).strip()],
                "success_criteria": dict(item.get("success_criteria") or {}) if isinstance(item.get("success_criteria"), dict) else {},
                "metadata": {
                    **promoted_metadata,
                    "auto_generated": True,
                    "request_origin": request_origin,
                    "bridge_mode": str(promoted_metadata.get("bridge_mode") or "page_agent_browser_plan"),
                    "resolved_start_url_base": base_url,
                    "resolved_start_url_base_source": base_url_source,
                    "resolved_path": path,
                },
            }
        )
    return requests[:3]
def _resolve_followup_ui_test_data_browser_plan(
    metadata: dict[str, Any],
    item: dict[str, Any],
    promotion: dict[str, Any],
) -> tuple[dict[str, Any], str, dict[str, Any]]:
    browser_plan = item.get("browser_plan") if isinstance(item.get("browser_plan"), dict) else {}
    if metadata.get("executable") is True and browser_plan:
        return browser_plan, "private_pilot_service_followup_test_data_asset", dict(metadata)
    approved_browser_plan = (
        promotion.get("approved_browser_plan") if isinstance(promotion.get("approved_browser_plan"), dict) else {}
    )
    confirmed_field_bindings = (
        promotion.get("confirmed_field_bindings") if isinstance(promotion.get("confirmed_field_bindings"), list) else []
    )
    approved_by = str(promotion.get("approved_by") or "").strip()
    if (
        str(promotion.get("status") or "").strip().lower() == "approved"
        and approved_by
        and confirmed_field_bindings
        and approved_browser_plan
    ):
        promoted_metadata = {
            **metadata,
            "executable": True,
            "promotion_status": "approved",
            "approved_by": approved_by,
        }
        return approved_browser_plan, "private_pilot_service_promoted_followup_test_data_asset", promoted_metadata
    return {}, "", dict(metadata)
def _has_campaign_id_mismatch(result: dict[str, Any]) -> bool:
    runtime_contract = result.get("runtime_contract") if isinstance(result, dict) else {}
    if not isinstance(runtime_contract, dict):
        return False
    requirements = runtime_contract.get("missing_requirements")
    if isinstance(requirements, list) and "EXECUTION_APPROVAL_CAMPAIGN_ID_MISMATCH" in {str(item) for item in requirements}:
        return True
    approval = runtime_contract.get("execution_approval")
    return isinstance(approval, dict) and str(approval.get("code") or "") == "EXECUTION_APPROVAL_CAMPAIGN_ID_MISMATCH"
def _issue_runtime_approval_for_result(
    project: str,
    root: Path,
    actor: dict[str, str],
    prepared_body: dict[str, Any],
    scan_result: dict[str, Any],
    *,
    local_dev_mode: bool,
) -> str:
    if not local_dev_mode:
        return ""
    campaign = scan_result.get("campaign") if isinstance(scan_result, dict) else {}
    if not isinstance(campaign, dict):
        return ""
    campaign_id = str(campaign.get("campaign_id") or "").strip()
    scope_id = str(campaign.get("scope_id") or "").strip()
    environment_ref = str(campaign.get("environment_ref") or "").strip()
    source_hash = str(campaign.get("source_hash") or "").strip().lower()
    base_url = str(prepared_body.get("base_url") or "").strip()
    execution_mode = str(prepared_body.get("execution_mode") or "safe_read_only").strip() or "safe_read_only"
    if (
        not campaign_id
        or not scope_id
        or not environment_ref
        or len(source_hash) != 64
        or not base_url
        or execution_mode not in {"safe_read_only", "approved_sandbox_write"}
    ):
        return ""
    try:
        from .execution_approvals import issue_execution_approval

        expires_at_utc = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() + 3600))
        approval = issue_execution_approval(
            project,
            root=root,
            campaign_id=campaign_id,
            scope_id=scope_id,
            environment_ref=environment_ref,
            source_hash=source_hash,
            target_base_url=base_url,
            execution_mode=execution_mode,
            expires_at_utc=expires_at_utc,
            actor=actor,
        )
    except Exception:
        return ""
    return str(approval.get("approval_id") or "").strip()
