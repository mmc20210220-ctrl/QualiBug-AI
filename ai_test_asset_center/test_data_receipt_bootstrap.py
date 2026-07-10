from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .enterprise_test_data_receipts import issue_test_data_receipt
from .grounded_probe_executor import _execute_auto_fixture_requests
from .parameter_fuzzer import ParameterFuzzer
from .real_id_resolver import normalize_path_placeholders, path_has_placeholders
from .route_catalog_builder import RouteCatalogBuilder


def bootstrap_test_data_receipts_for_campaign(
    *,
    project: str,
    root: Path,
    base_url: str,
    api_doc_text: str,
    campaign: dict[str, Any] | None,
    selected_slices: list[dict[str, Any]] | None,
    contract: dict[str, Any] | None,
    timeout: float = 10.0,
) -> dict[str, Any]:
    current = dict(contract or {})
    strategy = str(current.get("strategy") or "").strip()
    if strategy != "create_disposable":
        return {"status": "skipped", "reason": "strategy_not_create_disposable", "contract": current}
    if current.get("write_approved") is not True:
        return {"status": "skipped", "reason": "write_not_approved", "contract": current}
    if not str(base_url or "").strip():
        return {"status": "skipped", "reason": "base_url_missing", "contract": current}

    campaign = dict(campaign or {})
    campaign_id = str(campaign.get("campaign_id") or current.get("campaign_id") or "").strip()
    scope_id = str(campaign.get("scope_id") or current.get("scope_id") or "").strip()
    environment_ref = str(campaign.get("environment_ref") or current.get("environment_ref") or "").strip()
    if not campaign_id or not scope_id or not environment_ref:
        return {"status": "skipped", "reason": "campaign_identity_incomplete", "contract": current}
    from .sandbox_write_executor import is_production_environment, is_test_or_sandbox_environment

    if is_production_environment(environment_ref):
        return {"status": "blocked", "reason": "production_environment_blocked", "contract": current}
    if not is_test_or_sandbox_environment(environment_ref):
        return {"status": "blocked", "reason": "environment_not_recognized_nonprod", "contract": current}
    if str(current.get("creation_receipt_ref") or "").strip() and str(current.get("cleanup_receipt_ref") or "").strip():
        merged = dict(current)
        merged.setdefault("campaign_id", campaign_id)
        merged.setdefault("scope_id", scope_id)
        merged.setdefault("environment_ref", environment_ref)
        return {"status": "ready", "reason": "existing_receipts_reused", "contract": merged}

    routes = [item.to_dict() for item in RouteCatalogBuilder().build(str(api_doc_text or ""))]
    probe = _select_bootstrap_probe(routes, selected_slices or [], campaign_id)
    if not probe:
        return {"status": "blocked", "reason": "bootstrap_probe_not_found", "contract": current}

    control_attempts = _login_control_header_candidates(base_url, routes, project, root)
    if not control_attempts:
        return {"status": "blocked", "reason": "control_actor_login_failed", "contract": current, "probe": probe}

    disposable_scope_ref = str(current.get("disposable_scope_ref") or scope_id).strip()
    input_dir = Path(root) / "platform_workspace" / str(project) / "input"
    attempt_traces: list[dict[str, Any]] = []
    setup_receipts: list[dict[str, Any]] = []
    cleanup_receipts: list[dict[str, Any]] = []
    setup_ok: dict[str, Any] = {}
    cleanup_ok: dict[str, Any] = {}
    for attempt in control_attempts:
        control_headers = dict(attempt.get("headers") or {})
        runtime_config = {
            "qualibug_auto_create_test_data": True,
            "api_doc_text": str(api_doc_text or ""),
            "default_headers": dict(control_headers),
            "fixture_headers": dict(control_headers),
        }
        if input_dir.exists():
            runtime_config["project_input_dir"] = str(input_dir)

        setup_receipts = _execute_auto_fixture_requests(runtime_config, str(base_url).rstrip("/"), probe, "setup_requests", timeout)
        cleanup_receipts = _execute_auto_fixture_requests(runtime_config, str(base_url).rstrip("/"), probe, "cleanup_requests", timeout)
        setup_ok = _first_accepted(setup_receipts)
        cleanup_ok = _first_accepted(cleanup_receipts)
        attempt_traces.append(
            {
                "credential_profile": str(attempt.get("credential_profile") or ""),
                "setup_accepted": bool(setup_ok),
                "cleanup_accepted": bool(cleanup_ok),
                "setup_receipts": setup_receipts,
                "cleanup_receipts": cleanup_receipts,
            }
        )
        if setup_ok and cleanup_ok:
            break
    if not setup_ok or not cleanup_ok:
        return {
            "status": "blocked",
            "reason": "bootstrap_fixture_requests_not_accepted",
            "contract": current,
            "probe": probe,
            "setup_receipts": setup_receipts,
            "cleanup_receipts": cleanup_receipts,
            "control_attempts": attempt_traces,
        }

    actor = {"name": "QualiBug", "role": "sandbox_operator"}
    creation = issue_test_data_receipt(
        project,
        root=root,
        kind="creation",
        campaign_id=campaign_id,
        scope_id=scope_id,
        environment_ref=environment_ref,
        actor=actor,
        data_scope_ref=disposable_scope_ref,
        operation_ref=str(setup_ok.get("path") or setup_ok.get("purpose") or "bootstrap_setup")[:240],
    )
    cleanup = issue_test_data_receipt(
        project,
        root=root,
        kind="cleanup",
        campaign_id=campaign_id,
        scope_id=scope_id,
        environment_ref=environment_ref,
        actor=actor,
        operation_ref=str(cleanup_ok.get("path") or cleanup_ok.get("purpose") or "bootstrap_cleanup")[:240],
    )
    merged = dict(current)
    merged.update(
        {
            "strategy": "create_disposable",
            "write_approved": True,
            "campaign_id": campaign_id,
            "scope_id": scope_id,
            "environment_ref": environment_ref,
            "disposable_scope_ref": disposable_scope_ref,
            "creation_receipt_ref": creation["receipt_id"],
            "cleanup_receipt_ref": cleanup["receipt_id"],
        }
    )
    return {
        "status": "ready",
        "reason": "bootstrap_receipts_issued",
        "contract": merged,
        "probe": probe,
        "setup_receipts": setup_receipts,
        "cleanup_receipts": cleanup_receipts,
        "control_attempts": attempt_traces,
        "creation_receipt": creation,
        "cleanup_receipt": cleanup,
    }


def _select_bootstrap_probe(routes: list[dict[str, Any]], selected_slices: list[dict[str, Any]], campaign_id: str) -> dict[str, Any]:
    selected_paths: list[str] = []
    for item in selected_slices:
        if not isinstance(item, dict):
            continue
        for endpoint in item.get("endpoints") or []:
            path = normalize_path_placeholders(str(endpoint or "").strip())
            if path.startswith("/") and path not in selected_paths:
                selected_paths.append(path)

    route_paths = [normalize_path_placeholders(str(item.get("path") or "").strip()) for item in routes if str(item.get("path") or "").strip().startswith("/")]
    placeholder_path = next((path for path in selected_paths if path_has_placeholders(path)), "")
    if not placeholder_path:
        placeholder_path = next((path for path in route_paths if path_has_placeholders(path)), "")
    if placeholder_path:
        return {
            "candidate_id": f"QBBOOTSTRAP_{str(campaign_id or 'campaign')[:24]}",
            "risk_type": "anonymous_auth_boundary_probe",
            "execution_policy": "read_only_safe",
            "endpoint": {"method": "GET", "path": placeholder_path},
            "probe_plan": {
                "auth_boundary": {
                    "actor": "anonymous",
                    "credential_profile": "no_credentials",
                    "expected_status": [401, 403, 404],
                }
            },
        }

    post_path = next((path for path in selected_paths if path in route_paths and _route_has_method(routes, path, "POST")), "")
    if not post_path:
        post_path = next((path for path in route_paths if _route_has_method(routes, path, "POST")), "")
    if post_path:
        return {
            "candidate_id": f"QBBOOTSTRAP_{str(campaign_id or 'campaign')[:24]}",
            "risk_type": "conservation_probe",
            "execution_policy": "disposable_sandbox_required",
            "endpoint": {"method": "POST", "path": post_path},
            "probe_plan": {
                "mutation": {
                    "mutation_kind": "bootstrap_disposable_fixture",
                    "field_selector": "resource",
                    "value": 1,
                }
            },
        }
    return {}


def _route_has_method(routes: list[dict[str, Any]], path: str, method: str) -> bool:
    expected_path = normalize_path_placeholders(path)
    expected_method = str(method or "").upper()
    return any(
        normalize_path_placeholders(str(item.get("path") or "")) == expected_path
        and str(item.get("method") or "").upper() == expected_method
        for item in routes
        if isinstance(item, dict)
    )


def _login_control_header_candidates(base_url: str, routes: list[dict[str, Any]], project: str, root: Path) -> list[dict[str, Any]]:
    route = _login_route(routes)
    login_path = str(route.get("path") or "").strip()
    if not login_path:
        return []
    credentials = _test_credentials(project, root)
    if not credentials:
        return []
    attempts: list[dict[str, Any]] = []
    for account in credentials:
        fuzzer = ParameterFuzzer(base_url, allow_write=True)
        body_template = _login_body_template(account, route)
        secret = str(account.get("password") or account.get("pass") or "").strip()
        if not secret:
            continue
        if fuzzer.login(
            email=str(account.get("email") or account.get("username") or account.get("account") or "").strip(),
            password=secret,
            login_path=login_path,
            body_template=body_template,
        ):
            token = str(getattr(fuzzer, "_token", "") or "").strip()
            if token:
                attempts.append(
                    {
                        "credential_profile": str(account.get("profile") or account.get("role") or account.get("email") or ""),
                        "headers": {"Authorization": f"Bearer {token}"},
                    }
                )
    return attempts


def _login_route(routes: list[dict[str, Any]]) -> dict[str, Any]:
    for route in routes:
        if not isinstance(route, dict):
            continue
        method = str(route.get("method") or "").upper()
        path = normalize_path_placeholders(str(route.get("path") or ""))
        fingerprint = " ".join(
            [
                path,
                str(route.get("summary") or ""),
                str(route.get("operation_id") or route.get("operationId") or ""),
            ]
        ).lower()
        if method == "POST" and "login" in fingerprint and path.startswith("/"):
            normalized = dict(route)
            normalized["path"] = path
            return normalized
    return {}


def _test_credentials(project: str, root: Path) -> list[dict[str, Any]]:
    from .enterprise_pilot_runtime import load_project_test_credentials

    return load_project_test_credentials(project, root)


def _login_body_template(account: dict[str, Any], route: dict[str, Any]) -> dict[str, Any]:
    body: dict[str, Any] = {}
    fields = route.get("body_properties")
    names = [str(name) for name in fields.keys()] if isinstance(fields, dict) else []
    for name in names:
        lname = name.lower()
        if lname in {"password", "pass"}:
            body[name] = str(account.get("password") or account.get("pass") or "")
        elif lname in {"email", "username", "account", "mobile", "phone"}:
            body[name] = str(
                account.get(name)
                or account.get(lname)
                or account.get("email")
                or account.get("username")
                or account.get("account")
                or account.get("mobile")
                or account.get("phone")
                or ""
            )
    if not body:
        body = {
            "email": str(account.get("email") or account.get("username") or account.get("account") or ""),
            "password": str(account.get("password") or account.get("pass") or ""),
        }
    return body


def _first_accepted(receipts: list[dict[str, Any]]) -> dict[str, Any]:
    for item in receipts:
        if isinstance(item, dict) and item.get("status") == "executed" and item.get("accepted") is True:
            return item
    return {}
