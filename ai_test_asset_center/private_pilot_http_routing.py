"""HTTP GET/POST routing for PrivatePilotHandler."""
from __future__ import annotations

import os
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import db_persistence as db_persist
from . import jwt_auth
from .campaign_api_contract import CampaignContractError, structured_error
from .enterprise_pilot_runtime import operate_enterprise_pilot_runtime
from .private_pilot_command_center_envelope import normalize_command_center_envelope
from .private_pilot_continuous import _get_continuous_state
from .private_pilot_debug_client import _dbg_report
from .private_pilot_project_assets import _knowledge_asset_sources
from .real_project_onboarding import _safe_project_id


def _svc():
    from . import private_pilot_service as service

    return service


def _normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_command_center_envelope(payload)

class HttpRoutingMixin:
    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        project = self._project()
        root = self._root()
        # Serve the React console for any non-API path (public, before the auth
        # gate so /login is reachable). Retired server-rendered aliases redirect
        # onto first-class SPA routes; do not regenerate HTML pages here.
        if not parsed.path.startswith("/api") and parsed.path != "/health":
            _spa_aliases = {
                "/knowledge": "/materials",
                "/benchmark": "/coverage",
                "/onboard": "/products",
            }
            if parsed.path in _spa_aliases:
                target = _spa_aliases[parsed.path]
                if parsed.query:
                    target = f"{target}?{parsed.query}"
                self.send_response(302)
                self.send_header("Location", target)
                self.end_headers()
                return
            return self._serve_frontend(parsed, root)
        if parsed.path in {"/health", "/api/health"}:
            from . import private_pilot_service as _service
            from .private_pilot_health_contract import build_private_pilot_health_payload

            patch_source = str(
                getattr(_service, "_DEPLOYMENT_CONTRACT_PATCH_SOURCE", "")
                or "ai_test_asset_center.private_pilot_http_routing"
            )
            return self._json(
                build_private_pilot_health_payload(
                    self,
                    fallback_root=root,
                    patch_source=patch_source,
                )
            )
        # Every non-health route requires a trusted actor. `_require_actor()`
        # keeps the narrowly-scoped localhost development fallback, but only
        # when public binding is disabled and the caller has not explicitly
        # requested a negative-auth probe. This prevents public/private-cloud
        # GET endpoints from silently serving project data to anonymous users.
        actor = self._require_actor()
        if actor is None:
            return
        if self._require_tenant(root) is None:
            return
        _route_parts = [unquote(part) for part in parsed.path.split("/") if part]
        _route_project = (
            _route_parts[3]
            if len(_route_parts) >= 4 and _route_parts[:3] == ["api", "v1", "projects"]
            else ""
        )
        if _route_project:
            project = _safe_project_id(_route_project)
        if not self._require_project_scope(project):
            return
        if parsed.path == "/api/v1/projects":
            # Merge DB projects + filesystem-discovered projects (dedup by project_id)
            tenant_id = self._request_tenant()
            try:
                db_persist.init_db(root)
                items = db_persist.list_projects(root, tenant_id)
            except Exception:
                items = []
            # Always scan filesystem to discover projects not yet in DB
            scopes, wildcard = self._project_list_scope_filter()
            seen: set[str] = {str(it.get("project_id") or "") for it in items}
            for base_name in ("platform_outputs", "platform_workspace", "platform_inputs"):
                for d in sorted((root / base_name).glob("*")):
                    if not d.is_dir():
                        continue
                    if not wildcard and d.name not in scopes:
                        continue
                    if d.name in seen:
                        continue
                    seen.add(d.name)
                    items.append({
                        "project_id": d.name,
                        "customer_name": d.name,
                        "project_name": d.name,
                        "source": base_name,
                    })
            return self._json({"ok": True, "data": items})
        if len(_route_parts) >= 5 and _route_parts[:3] == ["api", "v1", "projects"] and _route_parts[4] == "campaigns":
            return self._handle_campaign_get(project, _route_parts[5:], parse_qs(parsed.query), root)
        # Bridge: serve V12 results in legacy format for Dashboard/Findings
        if parsed.path.startswith("/api/v1/projects/") and parsed.path.endswith("/command-center"):
            pid = parsed.path.split("/")[4] if len(parsed.path.split("/")) >= 5 else ""
            pid = urllib.parse.unquote(pid)
            trace_id = uuid.uuid4().hex
            started = time.perf_counter()
            _dbg_report(
                hypothesis_id="C",
                msg="[DEBUG] command-center enter",
                data={"project_id": pid, "path": parsed.path},
                trace_id=trace_id,
            )
            try:
                payload = self._build_command_center(pid, root)
                try:
                    from .display_ready_formatter import sanitize_customer_evidence_payload
                    payload = sanitize_customer_evidence_payload(payload)
                except Exception as sanitize_exc:
                    _dbg_report(
                        hypothesis_id="F",
                        msg="[WARN] command-center evidence response sanitize skipped",
                        data={"project_id": pid, "error": str(sanitize_exc)},
                        trace_id=trace_id,
                    )
                payload = _normalize_command_center_envelope(payload)
                _dbg_report(
                    hypothesis_id="C",
                    msg="[DEBUG] command-center built",
                    data={
                        "project_id": pid,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                        "keys": list(payload.keys())[:16],
                        "defect_count": len(((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("defects") or [])),
                        "clue_count": len(((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("clues") or [])),
                        "scan_meta": (((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("scan_meta") if isinstance((payload.get("data") if isinstance(payload.get("data"), dict) else payload).get("scan_meta"), dict) else {})),
                    },
                    trace_id=trace_id,
                )
                return self._json(payload)
            except BaseException as exc:
                _dbg_report(
                    hypothesis_id="B",
                    msg="[DEBUG] command-center exception",
                    data={"project_id": pid, "exc_type": type(exc).__name__, "exc": str(exc)},
                    trace_id=trace_id,
                )
                return self._json(
                    {"ok": False, "error": "COMMAND_CENTER_FAILED", "message": str(exc)},
                    500,
                )
        if parsed.path == "/api/tenants/create":
            return self._json({"ok": False, "error": "METHOD_NOT_ALLOWED", "message": "Use POST /api/tenants/create."}, 405)
        if parsed.path == "/api/auth/password/reset":
            return self._json({"ok": False, "error": "METHOD_NOT_ALLOWED", "message": "Use POST /api/auth/password/reset."}, 405)
        if parsed.path == "/api/connectors/list":
            from .enterprise_pilot_runtime import load_connector_registry
            registry = load_connector_registry(project, root)
            connectors = registry.get("connectors", [])
            return self._json({"ok": True, "connectors": connectors})
        if parsed.path == "/api/control-plane/overview":
            from .enterprise_testops_control_plane import build_enterprise_testops_control_plane, load_enterprise_testops_control_plane

            return self._json({"ok": True, "control_plane": load_enterprise_testops_control_plane(project, root) or build_enterprise_testops_control_plane(project, root)})
        if parsed.path == "/api/knowledge/asset":
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset

            asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
            # Also include project input files as knowledge sources
            input_files = self._list_project_inputs(project, root)
            existing_sources = _knowledge_asset_sources(asset, root)
            if not isinstance(existing_sources, list):
                existing_sources = []
            input_sources = input_files.get("sources", [])
            if not isinstance(input_sources, list):
                input_sources = []
            merged_by_key: dict[str, dict[str, Any]] = {}
            order: list[str] = []
            for item in list(existing_sources) + list(input_sources):
                if not isinstance(item, dict):
                    continue
                key = str(item.get("source_id") or item.get("id") or item.get("filename") or "")
                if not key:
                    continue
                current = merged_by_key.get(key)
                if current is None:
                    merged_by_key[key] = dict(item)
                    order.append(key)
                    continue
                incoming_uploaded_at = str(item.get("uploaded_at") or item.get("created_at_utc") or item.get("created_at") or "").strip()
                if not str(current.get("uploaded_at") or current.get("created_at_utc") or current.get("created_at") or "").strip() and incoming_uploaded_at:
                    current["uploaded_at"] = incoming_uploaded_at
                if int(current.get("size_bytes") or 0) <= 0 and int(item.get("size_bytes") or 0) > 0:
                    current["size_bytes"] = int(item.get("size_bytes") or 0)
            merged = [merged_by_key[k] for k in order]
            asset["sources"] = merged
            if not isinstance(asset.get("summary"), dict):
                asset["summary"] = {}
            asset["summary"]["active_source_count"] = len(asset["sources"])
            return self._json({"ok": True, "knowledge_asset": asset})
        if parsed.path == "/api/knowledge/preview":
            return self._handle_preview(project, {"source_id": parse_qs(parsed.query).get("source_id", [""])[0]}, root)
        if parsed.path == "/api/evidence/artifact":
            return self._handle_evidence_artifact(project, parse_qs(parsed.query).get("ref", [""])[0], root)
        # Settings-page read-back routes (P0-1 fix): these were previously only
        # declared inside do_POST behind a `if self.command == "GET"` guard that can
        # never be true there, so real GET requests fell through to 404. Wire them
        # into do_GET so the customer settings page can load saved config.
        if parsed.path == "/api/v1/services/credentials":
            return self._handle_get_service_credentials(project, root)
        if parsed.path == "/api/v1/project/metadata":
            return self._handle_get_project_metadata(project, root)
        if parsed.path == "/api/v1/scan/preflight":
            return self._handle_scan_preflight(project, root)
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        root = self._root()
        # Auth & tenant routes — no actor required
        if parsed.path in ("/api/auth/login", "/api/tenants/create", "/api/auth/password/reset"):
            try:
                body = self._body()
            except Exception:
                return self._json({"ok": False, "error": "BAD_REQUEST"}, 400)
            db_persist.init_db(root)
            if parsed.path == "/api/auth/login":
                username = str(body.get("username") or body.get("api_key") or "").strip()
                password = str(body.get("password") or "").strip()
                auth_result = db_persist.authenticate_tenant(root, username, password)
                if not auth_result:
                    return self._json({"ok": False, "error": "INVALID_CREDENTIALS"}, 401)
                token = jwt_auth.create_token(
                    str(auth_result["tenant_id"]),
                    str(auth_result.get("role") or "admin"),
                )
                _cookie_flags = "HttpOnly; SameSite=Lax; Path=/"
                if os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1":
                    _cookie_flags += "; Secure"
                return self._json({
                    "ok": True,
                    "token": token,
                    "tenant_id": auth_result["tenant_id"],
                    "role": auth_result.get("role") or "admin",
                }, extra_headers={"Set-Cookie": f"qualibug_token={token}; {_cookie_flags}"})
            if parsed.path == "/api/auth/password/reset":
                tid = str(body.get("tenant_id") or body.get("workspace_id") or "").strip()
                username = str(body.get("username") or "").strip()
                new_password = str(body.get("new_password") or body.get("password") or "")
                reset_result = db_persist.reset_tenant_password(
                    root,
                    tenant_id=tid,
                    username=username,
                    new_password=new_password,
                )
                if not reset_result.get("ok"):
                    error = str(reset_result.get("error") or "RESET_DENIED")
                    if error == "PASSWORD_TOO_SHORT":
                        return self._json({"ok": False, "error": error, "message": "密码长度至少 8 位"}, 400)
                    if error == "MISSING_FIELDS":
                        return self._json({"ok": False, "error": error, "message": "请填写完整重置信息"}, 400)
                    # Generic denial — do not reveal which field mismatched.
                    return self._json(
                        {"ok": False, "error": "RESET_DENIED", "message": "工作区或账号不匹配，无法重置密码"},
                        403,
                    )
                return self._json({
                    "ok": True,
                    "tenant_id": reset_result["tenant_id"],
                    "username": reset_result["username"],
                })
            if parsed.path == "/api/tenants/create":
                tid = str(body.get("tenant_id") or "").strip()
                name = str(body.get("name") or "").strip()
                username = str(body.get("username") or "").strip()
                password = str(body.get("password") or "").strip()
                role = str(body.get("role") or "admin").strip() or "admin"
                if not tid or not name or not username or not password:
                    return self._json({"ok": False, "error": "MISSING_FIELDS"}, 400)
                tenant_result = db_persist.create_tenant(
                    root,
                    tid,
                    name,
                    username=username,
                    password=password,
                    role=role,
                )
                if not tenant_result.get("ok"):
                    return self._json(tenant_result)
                db_persist.create_project(root, tid, tid, name)
                return self._json({"ok": True, "tenant_id": tid, "username": username, "role": role})
        actor = self._require_actor()
        if actor is None:
            return
        if self._require_tenant(root) is None:
            return
        try:
            body = self._body()
            route_project = ""
            route_parts = [unquote(part) for part in parsed.path.split("/") if part]
            if len(route_parts) >= 4 and route_parts[:3] == ["api", "v1", "projects"]:
                route_project = route_parts[3]
            project = _safe_project_id(str(body.get("project_id") or route_project or self._project()))
            if not self._require_project_scope(project):
                return
            if parsed.path == "/api/v1/evaluations/submissions":
                from .campaign_api_contract import build_evaluation_submission

                return self._json({"ok": True, "data": build_evaluation_submission(root, project, body)}, 201)
            if len(route_parts) == 5 and route_parts[:3] == ["api", "v1", "projects"] and route_parts[4] == "campaigns":
                from .campaign_api_contract import create_campaign

                return self._json({"ok": True, "data": create_campaign(root, project, body)}, 201)
            if (
                len(route_parts) == 7
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4] == "campaigns"
                and route_parts[6] in {"run", "resume"}
            ):
                from .campaign_api_contract import load_created_campaign

                campaign_contract = load_created_campaign(root, project, route_parts[5])
                if campaign_contract.get("status") != "ready":
                    raise CampaignContractError(
                        "campaign is not ready; resolve target_policy_decision.blocking_codes before execution"
                    )
                runtime_input = campaign_contract.get("runtime_input") if isinstance(campaign_contract.get("runtime_input"), dict) else {}
                scan_body = {
                    **body,
                    **runtime_input,
                    "project_id": project,
                    "campaign_id": route_parts[5],
                    "target_policy_decision": campaign_contract.get("target_policy_decision"),
                }
                return self._handle_v12_scan(project, root, actor, scan_body)
            if (
                len(route_parts) == 6
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4:] == ["environment", "preflight"]
            ):
                return self._handle_scan_preflight(project, root, body)
            if parsed.path == "/api/knowledge/ingest":
                if not self._require_role(actor, _svc().KNOWLEDGE_MANAGER_ROLES, "knowledge source ingestion"):
                    return
                return self._handle_ingest(project, body, root, actor)
            elif parsed.path.startswith("/api/knowledge/delete"):
                if not self._require_role(actor, _svc().KNOWLEDGE_MANAGER_ROLES, "knowledge source deletion"):
                    return
                return self._handle_delete(project, body, root, actor)
            elif parsed.path == "/api/environment/config":
                from .enterprise_testops_control_plane import save_environment_config
                result = save_environment_config(project, body.get("payload") or body, root, actor)
                # Clear dashboard cache so it picks up new env config
                dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
                if dash_html.exists(): dash_html.unlink()
                return self._json(result)
            elif parsed.path == "/api/v1/scan":
                # #region debug-point A0:scan-route-enter
                _dbg_report(
                    hypothesis_id="A0",
                    msg="[DEBUG] do_POST matched /api/v1/scan",
                    data={"project": project, "path": parsed.path, "body_keys": sorted(body.keys()) if isinstance(body, dict) else []},
                    trace_id=str(uuid.uuid4()),
                )
                # #endregion
                return self._handle_v12_scan(project, root, actor, body)
            elif parsed.path == "/api/v1/scan/preflight":
                return self._handle_scan_preflight(project, root)
            elif parsed.path == "/api/v1/continuous/status":
                return self._json(_get_continuous_state(root, project))
            elif parsed.path == "/api/v1/continuous/start":
                return self._handle_continuous_start(project, root, actor, body)
            elif parsed.path == "/api/v1/continuous/stop":
                return self._handle_continuous_stop(project, root)
            elif parsed.path == "/api/v1/spectrum/status":
                return self._get_spectrum_status(project, root)
            elif parsed.path == "/api/v1/db-test":
                return self._handle_db_test(body)
            elif parsed.path == "/api/v1/replay":
                return self._handle_replay(project, root, body)
            elif parsed.path.startswith("/api/v1/projects/") and parsed.path.endswith("/regression/run"):
                return self._handle_regression_run(project, root, body)
            elif parsed.path == "/api/v1/services/credentials":
                if self.command == "GET":
                    return self._handle_get_service_credentials(project, root)
                return self._handle_save_service_credentials(project, root, body)
            elif parsed.path == "/api/v1/project/metadata":
                if self.command == "GET":
                    return self._handle_get_project_metadata(project, root)
                return self._handle_save_project_metadata(project, root, body)
            elif parsed.path == "/api/settings/save":
                if not self._require_role(actor, _svc().SETTINGS_MANAGER_ROLES, "system settings update"):
                    return
                return self._handle_settings_save(body)
            elif parsed.path == "/api/connectors/register":
                result = operate_enterprise_pilot_runtime(project, "register_connector", body, root, actor)
                return self._json({"ok": True, "message": "Connector registered."})
            else:
                return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
            return self._json(result)
        except CampaignContractError as exc:
            error = structured_error(
                stage="campaign_api",
                code="CAMPAIGN_CONTRACT_BLOCKED",
                identity={"project_id": locals().get("project", "")},
                retryability="after_operator_action",
                operator_action=str(exc),
            )
            return self._json({"ok": False, "error": error}, 409)
        except PermissionError as exc:
            return self._json({"ok": False, "error": "FORBIDDEN", "message": str(exc)}, 403)
        except (ValueError, KeyError) as exc:
            return self._json({"ok": False, "error": "BAD_REQUEST", "message": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive private-service boundary
            return self._json({"ok": False, "error": "INTERNAL_ERROR", "message": str(exc)[:300]}, 500)
