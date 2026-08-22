"""Canonical HTTP routing for the private-pilot service.

Browser authentication uses an HttpOnly cookie. The token is never returned to
production JavaScript. Session status and logout are first-class authenticated
routes, and logout revokes every outstanding JWT/cookie version for the tenant.
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import db_persistence as db_persist
from . import jwt_auth
from .campaign_api_contract import CampaignContractError, structured_error
from .error_codes import ProductError
from .enterprise_pilot_runtime import operate_enterprise_pilot_runtime
from .private_pilot_command_center_envelope import normalize_command_center_envelope
from .private_pilot_continuous import _get_continuous_state
from .private_pilot_debug_client import _dbg_report
from .private_pilot_project_assets import _knowledge_asset_sources
from .product_logging import get_logger
from .real_project_onboarding import _safe_project_id

_http_logger = get_logger("qualibug.http")


def _svc():
    from . import private_pilot_service as service

    return service


def _normalize_command_center_envelope(payload: dict[str, Any]) -> dict[str, Any]:
    return normalize_command_center_envelope(payload)


# ── command-center 结果缓存 ─────────────────────────────────────────────────
# command-center 每次请求都要重新组装分片 store（实测 40MB scan_result.json
# 分片组装 ~10s，knowledge_asset.json ~2s）并全量重算投影，且同一页面会并发
# 触发多次。这里按「项目数据指纹」缓存最终脱敏后的 payload：指纹只在底层
# 数据文件（scan_result / evidence bundle / knowledge asset）发生变化时才变，
# 因此不会返回陈旧数据；指纹变化即失效，无需 TTL 猜测。
#
# 缓存只存「脱敏后、交付前」的最终 payload —— 它是纯只读、无共享可变子树的
# 结果，绝不缓存中间可被调用方就地修改的对象。
_COMMAND_CENTER_CACHE: dict[str, tuple[str, float, dict[str, Any]]] = {}
_COMMAND_CENTER_CACHE_LOCK = threading.Lock()
_COMMAND_CENTER_CACHE_MAX_ENTRIES = 64
# 文件指纹覆盖了最重的 scan_result / evidence bundle / knowledge asset，但
# command-center 还会读 SQLite（累积 finding 修复状态、knowledge_docs），其变化
# 不体现在文件 mtime 上。TTL 作为兜底：DB 变化最迟在该时间后生效，避免返回
# 长期陈旧数据。
_COMMAND_CENTER_CACHE_TTL_SECONDS = 30.0
# per-key 单飞锁：同一 project 的并发请求只允许一个真正构建，其余等待后命中缓存，
# 避免并发 miss 时多线程同时重组分片 store 造成磁盘争抢（实测并发时单请求
# 会从 ~10s 恶化到数百秒）。
_COMMAND_CENTER_BUILD_LOCKS: dict[str, threading.Lock] = {}
_COMMAND_CENTER_BUILD_LOCKS_GUARD = threading.Lock()


def _command_center_build_lock(cache_key: str) -> threading.Lock:
    with _COMMAND_CENTER_BUILD_LOCKS_GUARD:
        lock = _COMMAND_CENTER_BUILD_LOCKS.get(cache_key)
        if lock is None:
            lock = threading.Lock()
            _COMMAND_CENTER_BUILD_LOCKS[cache_key] = lock
        return lock


def _project_data_fingerprint(root: Path, project: str) -> str:
    """项目数据指纹：由 command-center 读取目录的最新 mtime + 文件数 + 大小构成。

    只统计真正的数据源（scan_result / intelligence_report / knowledge asset /
    evidence bundle），不扫描 delivery_packages 等无关大目录，避免指纹计算
    本身变成瓶颈（实测 660 文件全量 walk ~0.1s，可接受）。
    """
    latest_mtime_ns = 0
    file_count = 0
    total_bytes = 0
    scan_roots = [
        root / "platform_outputs" / project,
        root / "platform_workspace" / project,
    ]
    for base in scan_roots:
        if not base.is_dir():
            continue
        for dirpath, dirnames, filenames in os.walk(base):
            # 跳过明显与 command-center 数据无关的重量级目录。
            dirnames[:] = [d for d in dirnames if d not in {"delivery_packages"}]
            for filename in filenames:
                path = Path(dirpath) / filename
                try:
                    st = path.stat()
                except OSError:
                    continue
                file_count += 1
                total_bytes += st.st_size
                if st.st_mtime_ns > latest_mtime_ns:
                    latest_mtime_ns = st.st_mtime_ns
    return f"{latest_mtime_ns}:{file_count}:{total_bytes}"


def _text(value: Any) -> str:
    return str(value or "").strip()


class HttpRoutingMixin:
    def _init_request_context(self) -> None:
        self._qualibug_req_start = time.time()
        self._qualibug_corr_id = uuid.uuid4().hex[:12]

    def _cookie_flags(self, *, max_age: int | None = None) -> str:
        flags = ["HttpOnly", "SameSite=Strict", "Path=/"]
        if os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1":
            flags.append("Secure")
        if max_age is not None:
            flags.append(f"Max-Age={max_age}")
        return "; ".join(flags)

    def _clear_session_cookie(self) -> dict[str, str]:
        return {
            "Set-Cookie": (
                "qualibug_token=; " + self._cookie_flags(max_age=0)
            )
        }

    def _authority_decision_error(
        self,
        exc: Exception,
        *,
        project: str,
    ) -> Any:
        detail = str(exc or "").strip()
        if isinstance(exc, PermissionError):
            return self._json(
                {"ok": False, "error": "FORBIDDEN", "message": detail},
                403,
            )
        if isinstance(exc, KeyError):
            return self._json(
                {
                    "ok": False,
                    "error": "AUTHORITY_DECISION_NOT_FOUND",
                    "message": detail,
                },
                404,
            )
        if isinstance(exc, ValueError):
            return self._json(
                {
                    "ok": False,
                    "error": "AUTHORITY_DECISION_BAD_REQUEST",
                    "message": detail,
                },
                400,
            )
        _dbg_report(
            hypothesis_id="AUTHORITY_DECISION",
            msg="[ERROR] authority decision route failed",
            data={
                "project_id": project,
                "path": _text(getattr(self, "path", "")),
                "exc_type": type(exc).__name__,
            },
            trace_id=_text(getattr(self, "_qualibug_corr_id", "")),
        )
        return self._json(
            {
                "ok": False,
                "error": "AUTHORITY_DECISION_INTERNAL_ERROR",
                "message": "权限裁决资源暂时不可用。",
            },
            500,
        )

    def _handle_authority_decision_get(
        self,
        project: str,
        root: Path,
    ) -> Any:
        if not self._require_known_project(project, root):
            return None
        from .enterprise_knowledge_center._chinese_business_authority_decision import (
            list_operator_authority_decisions,
        )

        try:
            result = list_operator_authority_decisions(project, root=root)
        except Exception as exc:
            return self._authority_decision_error(exc, project=project)
        return self._json({"ok": True, "data": result})

    def _handle_authority_decision_post(
        self,
        project: str,
        root: Path,
        actor: dict[str, Any],
        body: dict[str, Any],
    ) -> Any:
        if not self._require_known_project(project, root):
            return None
        if not self._require_role(
            actor,
            {"knowledge_admin", "project_owner", "qa_lead", "admin"},
            "operator authority decision",
        ):
            return None
        from .enterprise_knowledge_center._chinese_business_authority_decision import (
            ACTION_LEAVE_UNRESOLVED,
            ACTION_SELECT_FACT,
            record_operator_authority_decision,
        )

        action = _text(body.get("action")).upper()
        try:
            if action not in {ACTION_SELECT_FACT, ACTION_LEAVE_UNRESOLVED}:
                raise ValueError(
                    "authority_decision_action_invalid_use_SELECT_FACT_or_LEAVE_UNRESOLVED"
                )
            result = record_operator_authority_decision(
                project,
                conflict_id=_text(body.get("conflict_id")),
                action=action,
                actor=actor,
                root=root,
                selected_fact_id=_text(body.get("selected_fact_id")),
                rationale=_text(body.get("rationale"))[:2000],
                document_version=_text(body.get("document_version"))[:200],
                rebuild=True,
            )
        except Exception as exc:
            return self._authority_decision_error(exc, project=project)
        return self._json({"ok": True, "action": action, "data": result}, 201)

    def _serve_public_frontend(self, parsed: Any, root: Path) -> Any:
        aliases = {
            "/knowledge": "/materials",
            "/benchmark": "/coverage",
            "/onboard": "/products",
        }
        if parsed.path in aliases:
            target = aliases[parsed.path]
            if parsed.query:
                target = f"{target}?{parsed.query}"
            self.send_response(302)
            self.send_header("Location", target)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return None
        return self._serve_frontend(parsed, root)

    def _health(self, root: Path) -> Any:
        from . import private_pilot_service as service
        from .private_pilot_health_contract import build_private_pilot_health_payload

        patch_source = _text(
            getattr(service, "_DEPLOYMENT_CONTRACT_PATCH_SOURCE", "")
        ) or "ai_test_asset_center.private_pilot_http_routing"
        payload = build_private_pilot_health_payload(
            self,
            fallback_root=root,
            patch_source=patch_source,
        )
        if not isinstance(payload, dict):
            return self._json(
                {"ok": False, "status": "unhealthy", "error": "HEALTH_INVALID"},
                503,
            )
        healthy = payload.get("ok") is True and _text(
            payload.get("status")
        ).lower() not in {"blocked", "failed", "unhealthy"}
        return self._json(payload, 200 if healthy else 503)

    def _route_project(self, parsed: Any) -> tuple[str, list[str]]:
        parts = [unquote(part) for part in parsed.path.split("/") if part]
        project = ""
        if len(parts) >= 4 and parts[:3] == ["api", "v1", "projects"]:
            project = parts[3]
        return project, parts

    def _handle_project_list(self, root: Path) -> Any:
        tenant_id = self._request_tenant()
        return self._json(
            {"ok": True, "data": db_persist.list_projects(root, tenant_id)}
        )

    def _handle_command_center(self, project: str, root: Path) -> Any:
        trace_id = uuid.uuid4().hex
        started = time.perf_counter()
        # 缓存 key 含租户：结果经过 actor/ACL 过滤，必须按租户隔离。
        tenant_id = self._request_tenant()
        cache_key = f"{tenant_id}:{project}"

        def _cache_hit(fingerprint: str) -> dict[str, Any] | None:
            with _COMMAND_CENTER_CACHE_LOCK:
                cached = _COMMAND_CENTER_CACHE.get(cache_key)
            if (
                cached is not None
                and cached[0] == fingerprint
                and (time.monotonic() - cached[1]) < _COMMAND_CENTER_CACHE_TTL_SECONDS
            ):
                return cached[2]
            return None

        fingerprint = _project_data_fingerprint(root, project)
        cached_payload = _cache_hit(fingerprint)
        if cached_payload is not None:
            _dbg_report(
                hypothesis_id="COMMAND_CENTER",
                msg="[DEBUG] command-center cache hit",
                data={
                    "project_id": project,
                    "elapsed_ms": int((time.perf_counter() - started) * 1000),
                },
                trace_id=trace_id,
            )
            return self._json(cached_payload)

        # 单飞：同一 project 并发请求只有一个构建，其余在锁外自旋等待缓存。
        build_lock = _command_center_build_lock(cache_key)
        with build_lock:
            # 获取锁后二次检查：上一个持有锁的请求可能已写入缓存。
            cached_payload = _cache_hit(fingerprint)
            if cached_payload is not None:
                _dbg_report(
                    hypothesis_id="COMMAND_CENTER",
                    msg="[DEBUG] command-center cache hit (after build lock)",
                    data={
                        "project_id": project,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    },
                    trace_id=trace_id,
                )
                return self._json(cached_payload)
            try:
                payload = self._build_command_center(project, root)
                if not isinstance(payload, dict):
                    raise TypeError("command-center payload must be an object")
                from .display_ready_formatter import sanitize_customer_evidence_payload

                sanitized = sanitize_customer_evidence_payload(payload)
                if not isinstance(sanitized, dict):
                    raise TypeError("sanitized command-center payload must be an object")
                normalized = _normalize_command_center_envelope(sanitized)
                _dbg_report(
                    hypothesis_id="COMMAND_CENTER",
                    msg="[DEBUG] command-center customer payload ready",
                    data={
                        "project_id": project,
                        "elapsed_ms": int((time.perf_counter() - started) * 1000),
                    },
                    trace_id=trace_id,
                )
                with _COMMAND_CENTER_CACHE_LOCK:
                    if len(_COMMAND_CENTER_CACHE) >= _COMMAND_CENTER_CACHE_MAX_ENTRIES:
                        _COMMAND_CENTER_CACHE.clear()
                    _COMMAND_CENTER_CACHE[cache_key] = (
                        fingerprint,
                        time.monotonic(),
                        normalized,
                    )
                return self._json(normalized)
            except Exception as exc:
                _dbg_report(
                    hypothesis_id="COMMAND_CENTER",
                    msg="[ERROR] command-center blocked before delivery",
                    data={
                        "project_id": project,
                        "exc_type": type(exc).__name__,
                    },
                    trace_id=trace_id,
                )
                import traceback as _traceback
                _http_logger.error(
                    "command-center delivery blocked: project=%s exc=%s %s",
                    project,
                    type(exc).__name__,
                    exc,
                    exc_info=True,
                )
                return self._json(
                    {
                        "ok": False,
                        "error": "COMMAND_CENTER_DELIVERY_BLOCKED",
                        "message": "客户证据脱敏或交付格式校验失败，原始数据未返回。",
                    },
                    500,
                )

    def do_GET(self) -> None:  # noqa: N802
        self._init_request_context()
        parsed = urlparse(self.path)
        root = self._root()
        if not parsed.path.startswith("/api") and parsed.path != "/health":
            return self._serve_public_frontend(parsed, root)
        if parsed.path in {"/health", "/api/health"}:
            return self._health(root)

        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        if parsed.path == "/api/auth/session":
            principal = self._principal()
            return self._json(
                {
                    "ok": True,
                    "authenticated": True,
                    "tenant_id": principal["tenant_id"],
                    "username": principal["name"],
                    "role": principal["role"],
                    "auth_type": principal.get("auth_type", ""),
                }
            )
        route_project, route_parts = self._route_project(parsed)
        if parsed.path == "/api/v1/projects":
            return self._handle_project_list(root)

        try:
            project = _safe_project_id(route_project or self._project())
        except ValueError:
            return self._json({"ok": False, "error": "PROJECT_NOT_FOUND"}, 404)
        if not self._require_project_scope(project):
            return None

        authority_route = (
            len(route_parts) == 5
            and route_parts[:3] == ["api", "v1", "projects"]
            and route_parts[4] == "authority-decisions"
        )
        if authority_route:
            return self._handle_authority_decision_get(project, root)
        if (
            len(route_parts) >= 5
            and route_parts[:3] == ["api", "v1", "projects"]
            and route_parts[4] == "campaigns"
        ):
            return self._handle_campaign_get(
                project,
                route_parts[5:],
                parse_qs(parsed.query),
                root,
            )
        if (
            parsed.path.startswith("/api/v1/projects/")
            and parsed.path.endswith("/command-center")
        ):
            return self._handle_command_center(project, root)
        if parsed.path == "/api/connectors/list":
            from .enterprise_pilot_runtime import load_connector_registry

            registry = load_connector_registry(project, root)
            return self._json(
                {"ok": True, "connectors": registry.get("connectors", [])}
            )
        if parsed.path == "/api/control-plane/overview":
            from .enterprise_testops_control_plane import (
                build_enterprise_testops_control_plane,
                load_enterprise_testops_control_plane,
            )

            control_plane = load_enterprise_testops_control_plane(
                project, root
            ) or build_enterprise_testops_control_plane(project, root)
            return self._json({"ok": True, "control_plane": control_plane})
        if parsed.path == "/api/knowledge/asset":
            from .enterprise_knowledge_center import (
                build_enterprise_business_knowledge_asset,
                load_enterprise_business_knowledge_asset,
            )

            asset = load_enterprise_business_knowledge_asset(
                project, root
            ) or build_enterprise_business_knowledge_asset(project, root)
            if not isinstance(asset, dict):
                raise TypeError("knowledge asset must be an object")
            input_files = self._list_project_inputs(project, root)
            existing = _knowledge_asset_sources(asset, root)
            inputs = (
                input_files.get("sources", [])
                if isinstance(input_files, dict)
                and isinstance(input_files.get("sources"), list)
                else []
            )
            merged: dict[str, dict[str, Any]] = {}
            for item in [*existing, *inputs]:
                if not isinstance(item, dict):
                    continue
                key = _text(
                    item.get("source_id")
                    or item.get("id")
                    or item.get("filename")
                )
                if key:
                    merged.setdefault(key, dict(item))
            asset["sources"] = list(merged.values())
            summary = asset.get("summary")
            if not isinstance(summary, dict):
                summary = {}
                asset["summary"] = summary
            summary["active_source_count"] = len(asset["sources"])
            from .connector_acl_authority import filter_connector_asset_for_actor

            asset = filter_connector_asset_for_actor(
                project,
                asset,
                actor={**actor, "project_id": project} if actor else actor,
                root=root,
            )
            return self._json({"ok": True, "knowledge_asset": asset})
        if parsed.path == "/api/knowledge/preview":
            source_id = (parse_qs(parsed.query).get("source_id") or [""])[0]
            return self._handle_preview(
                project,
                {"source_id": source_id},
                root,
                actor,
            )
        if parsed.path == "/api/evidence/artifact":
            artifact_ref = (parse_qs(parsed.query).get("ref") or [""])[0]
            return self._handle_evidence_artifact(project, artifact_ref, root)
        if parsed.path == "/api/v1/services/credentials":
            if not self._require_role(
                actor,
                _svc().CONFIG_MANAGER_ROLES,
                "service credential read",
            ):
                return None
            return self._handle_get_service_credentials(project, root)
        if parsed.path == "/api/v1/project/metadata":
            return self._handle_get_project_metadata(project, root)
        if parsed.path == "/api/v1/scan/preflight":
            return self._handle_scan_preflight(project, root)
        if parsed.path in {
            "/api/tenants/create",
            "/api/auth/password/reset",
            "/api/auth/logout",
        }:
            return self._json({"ok": False, "error": "METHOD_NOT_ALLOWED"}, 405)
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)

    def _handle_public_auth_post(self, parsed: Any, root: Path) -> Any:
        try:
            body = self._body()
        except ValueError as exc:
            return self._json(
                {"ok": False, "error": "BAD_REQUEST", "message": str(exc)},
                400,
            )
        db_persist.init_db(root)
        if parsed.path == "/api/auth/login":
            username = _text(body.get("username") or body.get("api_key"))
            password = str(body.get("password") or "")
            account = db_persist.authenticate_tenant(root, username, password)
            if not account:
                return self._json(
                    {"ok": False, "error": "INVALID_CREDENTIALS"},
                    401,
                )
            token = jwt_auth.create_token(
                _text(account.get("tenant_id")),
                _text(account.get("role")) or "viewer",
                username=_text(account.get("username")),
                session_version=int(account.get("session_version") or 1),
            )
            return self._json(
                {
                    "ok": True,
                    "tenant_id": account["tenant_id"],
                    "username": account.get("username") or "",
                    "role": account.get("role") or "viewer",
                    "session_transport": "httponly_cookie",
                },
                extra_headers={
                    "Set-Cookie": (
                        f"qualibug_token={token}; {self._cookie_flags()}"
                    )
                },
            )
        if parsed.path == "/api/auth/password/reset":
            result = db_persist.reset_tenant_password(
                root,
                tenant_id=_text(body.get("tenant_id") or body.get("workspace_id")),
                username=_text(body.get("username")),
                current_password=str(body.get("current_password") or ""),
                new_password=str(
                    body.get("new_password") or body.get("password") or ""
                ),
            )
            if result.get("ok") is not True:
                error = _text(result.get("error")) or "RESET_DENIED"
                status = (
                    400
                    if error
                    in {
                        "MISSING_FIELDS",
                        "PASSWORD_TOO_SHORT",
                        "RESET_AUTH_REQUIRED",
                    }
                    else 403
                )
                return self._json(
                    {
                        "ok": False,
                        "error": error,
                        "message": "密码变更需要正确的工作区、账号和当前密码。",
                    },
                    status,
                )
            return self._json(
                {
                    "ok": True,
                    "tenant_id": result["tenant_id"],
                    "username": result["username"],
                    "session_revoked": True,
                    "api_key_revoked": result.get("api_key_revoked") is True,
                },
                extra_headers=self._clear_session_cookie(),
            )
        if parsed.path == "/api/tenants/create":
            tenant_id = _text(body.get("tenant_id"))
            name = _text(body.get("name"))
            username = _text(body.get("username"))
            password = str(body.get("password") or "")
            if not tenant_id or not name or not username or not password:
                return self._json(
                    {"ok": False, "error": "MISSING_FIELDS"},
                    400,
                )
            bootstrap_token = _text(
                self.headers.get("X-QualiBug-Bootstrap-Token")
                or body.get("bootstrap_token")
            )
            tenant_result = db_persist.create_tenant(
                root,
                tenant_id,
                name,
                username=username,
                password=password,
                provisioning_token=bootstrap_token,
            )
            if tenant_result.get("ok") is not True:
                error = _text(tenant_result.get("error")) or "TENANT_CREATE_FAILED"
                status = (
                    403
                    if error
                    in {
                        "TENANT_PROVISIONING_DISABLED",
                        "TENANT_BOOTSTRAP_TOKEN_REQUIRED",
                    }
                    else 409
                    if error in {"TENANT_EXISTS", "USERNAME_EXISTS"}
                    else 400
                )
                return self._json({"ok": False, "error": error}, status)
            project_result = db_persist.create_project(
                root,
                tenant_result["tenant_id"],
                tenant_result["tenant_id"],
                name,
            )
            if project_result.get("ok") is not True:
                return self._json(
                    {"ok": False, "error": "INITIAL_PROJECT_CREATE_FAILED"},
                    500,
                )
            return self._json(
                {
                    "ok": True,
                    "tenant_id": tenant_result["tenant_id"],
                    "username": tenant_result["username"],
                    "role": tenant_result["role"],
                },
                201,
            )
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        self._init_request_context()
        parsed = urlparse(self.path)
        root = self._root()
        if parsed.path in {
            "/api/auth/login",
            "/api/tenants/create",
            "/api/auth/password/reset",
        }:
            return self._handle_public_auth_post(parsed, root)

        actor = self._require_actor()
        if actor is None or self._require_tenant(root) is None:
            return None
        if parsed.path == "/api/auth/logout":
            tenant_id = self._request_tenant()
            db_persist.revoke_tenant_sessions(root, tenant_id)
            return self._json(
                {"ok": True, "logged_out": True},
                extra_headers=self._clear_session_cookie(),
            )
        try:
            body = self._body()
            route_project, route_parts = self._route_project(parsed)
            project = _safe_project_id(
                _text(body.get("project_id") or route_project or self._project())
            )
            if not self._require_project_scope(project):
                return None
            authority_route = (
                len(route_parts) == 5
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4] == "authority-decisions"
            )
            if authority_route:
                return self._handle_authority_decision_post(
                    project, root, actor, body
                )
            if parsed.path == "/api/v1/evaluations/submissions":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "evaluation submission",
                ):
                    return None
                from .campaign_api_contract import build_evaluation_submission

                return self._json(
                    {
                        "ok": True,
                        "data": build_evaluation_submission(root, project, body),
                    },
                    201,
                )
            if (
                len(route_parts) == 5
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4] == "campaigns"
            ):
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "campaign creation",
                ):
                    return None
                from .campaign_api_contract import create_campaign

                return self._json(
                    {"ok": True, "data": create_campaign(root, project, body)},
                    201,
                )
            if (
                len(route_parts) == 7
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4] == "campaigns"
                and route_parts[6] in {"run", "resume"}
            ):
                from .campaign_api_contract import load_created_campaign

                campaign = load_created_campaign(root, project, route_parts[5])
                if campaign.get("status") != "ready":
                    raise CampaignContractError(
                        "campaign is not ready; resolve target policy blockers"
                    )
                runtime_input = (
                    campaign.get("runtime_input")
                    if isinstance(campaign.get("runtime_input"), dict)
                    else {}
                )
                return self._handle_v12_scan(
                    project,
                    root,
                    actor,
                    {
                        **body,
                        **runtime_input,
                        "project_id": project,
                        "campaign_id": route_parts[5],
                        "target_policy_decision": campaign.get(
                            "target_policy_decision"
                        ),
                    },
                )
            if (
                len(route_parts) == 6
                and route_parts[:3] == ["api", "v1", "projects"]
                and route_parts[4:] == ["environment", "preflight"]
            ):
                return self._handle_scan_preflight(project, root, body)
            if parsed.path == "/api/knowledge/ingest":
                if not self._require_role(
                    actor,
                    _svc().KNOWLEDGE_MANAGER_ROLES,
                    "knowledge source ingestion",
                ):
                    return None
                return self._handle_ingest(project, body, root, actor)
            if parsed.path.startswith("/api/knowledge/delete"):
                if not self._require_role(
                    actor,
                    _svc().KNOWLEDGE_MANAGER_ROLES,
                    "knowledge source deletion",
                ):
                    return None
                return self._handle_delete(project, body, root, actor)
            if parsed.path == "/api/environment/config":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "environment configuration",
                ):
                    return None
                from .enterprise_testops_control_plane import save_environment_config

                result = save_environment_config(
                    project,
                    body.get("payload") or body,
                    root,
                    actor,
                )
                dashboard = (
                    root
                    / "platform_outputs"
                    / project
                    / "enterprise_pilot_runtime"
                    / "enterprise_pilot_center.html"
                )
                dashboard.unlink(missing_ok=True)
                return self._json(result)
            if parsed.path == "/api/v1/scan":
                return self._handle_v12_scan(project, root, actor, body)
            if parsed.path == "/api/v1/scan/cancel":
                return self._handle_v12_scan_cancel(project, root, actor)
            if parsed.path == "/api/v1/scan/preflight":
                return self._handle_scan_preflight(project, root, body)
            if parsed.path == "/api/v1/continuous/status":
                return self._json(_get_continuous_state(root, project))
            if parsed.path == "/api/v1/continuous/start":
                return self._handle_continuous_start(project, root, actor, body)
            if parsed.path == "/api/v1/continuous/stop":
                return self._handle_continuous_stop(project, root)
            if parsed.path == "/api/v1/spectrum/status":
                return self._get_spectrum_status(project, root)
            if parsed.path == "/api/v1/db-test":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "database connection test",
                ):
                    return None
                return self._handle_db_test(body)
            if parsed.path == "/api/v1/replay":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "finding replay",
                ):
                    return None
                return self._handle_replay(project, root, body)
            if (
                parsed.path.startswith("/api/v1/projects/")
                and parsed.path.endswith("/regression/run")
            ):
                return self._handle_regression_run(project, root, body)
            if parsed.path == "/api/v1/services/credentials":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "service credential update",
                ):
                    return None
                return self._handle_save_service_credentials(project, root, body)
            if parsed.path == "/api/v1/project/metadata":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "project metadata update",
                ):
                    return None
                return self._handle_save_project_metadata(project, root, body)
            if parsed.path == "/api/settings/save":
                return self._handle_settings_save(body)
            if parsed.path == "/api/connectors/register":
                if not self._require_role(
                    actor,
                    _svc().CONFIG_MANAGER_ROLES,
                    "connector registration",
                ):
                    return None
                result = operate_enterprise_pilot_runtime(
                    project,
                    "register_connector",
                    body,
                    root,
                    actor,
                )
                if not isinstance(result, dict) or result.get("ok") is not True:
                    return self._json(
                        {
                            "ok": False,
                            "error": "CONNECTOR_REGISTRATION_FAILED",
                            "result": result if isinstance(result, dict) else {},
                        },
                        500,
                    )
                return self._json(
                    {"ok": True, "message": "Connector registered.", "result": result}
                )
            return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
        except CampaignContractError as exc:
            error = structured_error(
                stage="campaign_api",
                code="CAMPAIGN_CONTRACT_BLOCKED",
                identity={"project_id": locals().get("project", "")},
                retryability="after_operator_action",
                operator_action=str(exc),
            )
            return self._json({"ok": False, "error": error}, 409)
        except PermissionError:
            return self._json({"ok": False, "error": "FORBIDDEN"}, 403)
        except (ValueError, KeyError) as exc:
            return self._json(
                {"ok": False, "error": "BAD_REQUEST", "message": str(exc)},
                400,
            )
        except ProductError as exc:
            return self._json(
                {
                    "ok": False,
                    "error": exc.code,
                    "error_code": exc.code,
                    "message": exc.user_message,
                    "severity": exc.severity,
                    "support_hint": (
                        "如问题持续，请运行 qualibug-doctor --export-bundle "
                        "并发送给技术支持"
                    ),
                },
                500,
            )
        except Exception as exc:
            _dbg_report(
                hypothesis_id="HTTP_POST",
                msg="[ERROR] protected route failed",
                data={"path": parsed.path, "exc_type": type(exc).__name__},
                trace_id=_text(getattr(self, "_qualibug_corr_id", "")),
            )
            return self._json(
                {
                    "ok": False,
                    "error": "INTERNAL_ERROR",
                    "message": "请求处理失败，请使用关联日志排查。",
                },
                500,
            )
