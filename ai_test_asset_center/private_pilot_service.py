from __future__ import annotations

"""Private-network HTTP entrypoint for the QualiBug pilot runtime.

The service binds to localhost by default. In private-cloud deployments, a
trusted reverse proxy or enterprise SSO gateway should authenticate users and
inject the actor/role headers documented below. The service never accepts raw
credential values; connectors only receive secret references.
"""

import json
import os
import time
import traceback
import uuid
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from .enterprise_pilot_runtime import (
    build_enterprise_pilot_overview,
    list_pilot_tasks,
    operate_enterprise_pilot_runtime,
)
from . import db_persistence as db_persist
from . import jwt_auth
from .real_project_onboarding import ROOT, _safe_project_id


CONFIG_MANAGER_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
KNOWLEDGE_MANAGER_ROLES = {"knowledge_admin", "project_owner", "qa_lead", "admin"}
SETTINGS_MANAGER_ROLES = {"project_owner", "security_owner", "testops_admin", "admin"}
PROJECT_SCOPE_HEADER = "X-QualiBug-Project-Scopes"

# #region debug-point Z:debug-client
_DBG_ENV_CACHE: tuple[str, str] | None = None


def _dbg_env() -> tuple[str, str]:
    global _DBG_ENV_CACHE
    if _DBG_ENV_CACHE is not None:
        return _DBG_ENV_CACHE
    url = "http://127.0.0.1:7777/event"
    session_id = "command-center-502"
    env_path = Path(__file__).resolve().parents[1] / ".dbg" / "command-center-502.env"
    try:
        if env_path.exists():
            content = env_path.read_text(encoding="utf-8")
            url = next((line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith("DEBUG_SERVER_URL=")), url)
            session_id = next((line.split("=", 1)[1].strip() for line in content.splitlines() if line.startswith("DEBUG_SESSION_ID=")), session_id)
    except Exception:
        pass
    _DBG_ENV_CACHE = (url, session_id)
    return _DBG_ENV_CACHE


def _dbg_report(*, hypothesis_id: str, msg: str, data: dict[str, Any] | None = None, run_id: str = "pre-fix", trace_id: str = "") -> None:
    try:
        url, session_id = _dbg_env()
        payload = {
            "sessionId": session_id,
            "runId": run_id,
            "hypothesisId": hypothesis_id,
            "location": "ai_test_asset_center/private_pilot_service.py",
            "msg": msg,
            "data": data or {},
            "traceId": trace_id,
            "ts": int(time.time() * 1000),
        }
        raw = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        req = urllib.request.Request(url, data=raw, headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=0.2).read()
    except Exception:
        pass

# #endregion
def _current_tenant() -> str:
    return os.environ.get("QUALIBUG_TENANT", "default")

def _tenant_from_headers(headers: dict) -> str:
    """Resolve tenant from request headers (Bearer JWT or API key)."""
    # 1. Bearer JWT
    auth = headers.get("Authorization") or headers.get("authorization") or ""
    if auth.startswith("Bearer "):
        from . import jwt_auth as _ja
        payload = _ja.verify_token(auth[7:])
        if payload:
            return str(payload.get("sub", ""))
    # 2. API Key
    api_key = headers.get("X-API-Key") or headers.get("x-api-key") or ""
    if api_key:
        from . import db_persistence as _dp
        try:
            root = _root()
            tid = _dp.verify_api_key(root, api_key)
            if tid: return tid
        except Exception:
            pass
    return _current_tenant()

_TENANT = _current_tenant()
KNOWLEDGE_INGEST_SOURCE_TYPES = (
    "prd",
    "mrd",
    "openapi",
    "postman",
    "database_schema",
    "permission_matrix",
    "historical_bug",
    "ticket",
    "feishu_document",
    "confluence_document",
    "collaboration_document",
    "other_document",
)
KNOWLEDGE_INGEST_TEXT_EXTENSIONS = (
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".html",
    ".htm",
    ".yaml",
    ".yml",
    ".json",
    ".csv",
    ".sql",
    ".xml",
)
KNOWLEDGE_INGEST_BINARY_EXTENSIONS = (".pdf", ".docx")
KNOWLEDGE_INGEST_EXTENSIONS = KNOWLEDGE_INGEST_TEXT_EXTENSIONS + KNOWLEDGE_INGEST_BINARY_EXTENSIONS
ONBOARD_DOCUMENT_EXTENSIONS = (".md", ".markdown", ".txt", ".pdf", ".docx", ".html", ".htm")
ONBOARD_OPENAPI_EXTENSIONS = (".yaml", ".yml", ".json")


def _extensions_label(items: tuple[str, ...]) -> str:
    return " ".join(items)


def _extensions_accept(items: tuple[str, ...]) -> str:
    return ",".join(items)


def _root() -> Path:
    configured = os.environ.get("QUALIBUG_PRIVATE_ROOT", "").strip()
    return Path(configured).resolve() if configured else ROOT


def _actor(headers: Any) -> dict[str, str] | None:
    name = str(headers.get("X-QualiBug-Actor") or headers.get("x-qualibug-actor") or "").strip()
    role = str(headers.get("X-QualiBug-Role") or headers.get("x-qualibug-role") or "").strip()
    if not name or not role:
        return None
    return {"name": name[:120], "role": role[:64]}


def _truthy_env(name: str, default: str = "") -> bool:
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _parse_project_scopes(raw: str) -> tuple[set[str], bool]:
    items = [item.strip() for item in str(raw or "").replace(";", ",").split(",") if item.strip()]
    wildcard = any(item == "*" for item in items)
    return {_safe_project_id(item) for item in items if item != "*"}, wildcard


def _load_real_project_discovery_payload(root: Path, project_id: str) -> dict[str, Any] | None:
    project = _safe_project_id(project_id)
    candidate = root / "platform_outputs" / project / "real_project" / "real_project_defect_data.json"
    if not candidate.exists():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8") or "null")
    except Exception:
        return None
    return payload if isinstance(payload, dict) else None


def _write_env_local(updates: dict[str, str]) -> Path:
    configured = os.environ.get("QUALIBUG_ENV_LOCAL_PATH", "").strip()
    env_path = Path(configured).expanduser().resolve() if configured else Path(__file__).resolve().parents[1] / ".env.local"
    lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else [
        "# Local-only QualiBug LLM credentials.",
        "# This file is ignored by git. Do not share it.",
        "",
    ]
    keys = set(updates)
    written: set[str] = set()
    new_lines: list[str] = []
    for line in lines:
        raw = line.strip()
        key = raw.split("=", 1)[0].strip().upper() if "=" in raw and not raw.startswith("#") else ""
        if key in keys:
            new_lines.append(f"{key}={updates[key]}")
            written.add(key)
        else:
            new_lines.append(line)
    if new_lines and new_lines[-1].strip():
        new_lines.append("")
    for key in sorted(keys - written):
        new_lines.append(f"{key}={updates[key]}")
    env_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    return env_path


def _known_project_exists(root: Path, project: str) -> bool:
    project = _safe_project_id(project)
    candidates = (
        root / "platform_inputs" / project / "real_project_config.json",
        root / "platform_outputs" / project,
        root / "platform_workspace" / project,
    )
    return any(path.exists() for path in candidates)


def _project_output_dir_for_import(root: Path, project_id: str) -> tuple[str, Path]:
    safe_project_id = _safe_project_id(project_id)
    output_dir = (root / "platform_outputs" / safe_project_id).resolve()
    platform_outputs = (root / "platform_outputs").resolve()
    if platform_outputs not in output_dir.parents and output_dir != platform_outputs:
        raise ValueError("project output path escaped platform_outputs")
    return safe_project_id, output_dir


def _knowledge_asset_sources(asset: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    inventory = asset.get("source_inventory") or asset.get("sources") or []
    if not isinstance(inventory, list):
        return rows
    for item in inventory:
        if not isinstance(item, dict):
            continue
        stored_path = str(item.get("stored_path") or item.get("path") or "")
        path = root / stored_path if stored_path and not Path(stored_path).is_absolute() else Path(stored_path) if stored_path else None
        size = path.stat().st_size if path and path.exists() and path.is_file() else int(item.get("size_bytes") or 0)
        rows.append({
            "source_id": str(item.get("source_id") or item.get("id") or ""),
            "filename": str(item.get("original_name") or item.get("filename") or item.get("name") or ""),
            "source_type": str(item.get("source_type") or item.get("type") or ""),
            "status": str(item.get("status") or "active"),
            "size_bytes": size,
            "uploaded_at": str(item.get("created_at_utc") or item.get("uploaded_at") or item.get("created_at") or ""),
            "version": item.get("version", 1),
            "parse_status": str((item.get("parse") or {}).get("parse_status") or item.get("parse_status") or ""),
        })
    return rows


def _normalize_frontend_page_path(path: str) -> str:
    clean = "/" + str(path or "/").strip().strip("/")
    return {
        "/materials": "/knowledge",
        "/evidence": "/findings",
    }.get(clean, clean)


def _synchronize_scan_aggregates(report: dict[str, Any]) -> dict[str, Any]:
    """Make every scan view derive its counts from the final calibrated list.

    Health, semantic and validation stages may replace the discovery list after
    the autonomous pipeline has built its executive summary. Keeping the
    aggregation here prevents release and management views from under-reporting
    the final risk population.
    """
    stage2 = dict(report.get("stage2_discovery") or {})
    findings = [item for item in (stage2.get("findings") or []) if isinstance(item, dict)]
    stage2["findings"] = findings
    stage2["total_findings"] = len(findings)
    severities = sorted({str(item.get("severity") or "unknown") for item in findings})
    stage2["by_severity"] = {
        severity: sum(1 for item in findings if str(item.get("severity") or "unknown") == severity)
        for severity in severities
    }
    report["stage2_discovery"] = stage2

    executive = dict(report.get("executive_summary") or {})
    executive["total_bugs_found"] = len(findings)
    executive["critical_bugs"] = sum(1 for item in findings if str(item.get("severity") or "") == "P0")
    executive["high_priority_bugs"] = sum(1 for item in findings if str(item.get("severity") or "") == "P1")

    stage3 = dict(report.get("stage3_impact_analysis") or {})
    analyses = [item for item in (stage3.get("analyses") or []) if isinstance(item, dict)]
    stage3["analyses"] = analyses
    stage3["total_analyses"] = len(analyses)
    stage3["llm_powered"] = sum(1 for item in analyses if item.get("source") == "llm_evidence_impact")
    stage3["heuristic"] = len(analyses) - int(stage3["llm_powered"])
    report["stage3_impact_analysis"] = stage3
    executive["impact_analyses"] = len(analyses)
    executive["llm_powered_analyses"] = int(stage3["llm_powered"])
    report["executive_summary"] = executive
    return report


def _extend_stage3_impact_analysis(report: dict[str, Any], analyses: list[dict[str, Any]]) -> None:
    """Append post-pipeline impact notes without discarding LLM assessments."""
    if not analyses:
        return
    stage3 = dict(report.get("stage3_impact_analysis") or {})
    existing = [item for item in (stage3.get("analyses") or []) if isinstance(item, dict)]
    existing.extend(item for item in analyses if isinstance(item, dict))
    stage3["analyses"] = existing
    stage3["total_analyses"] = len(existing)
    stage3["llm_powered"] = sum(1 for item in existing if item.get("source") == "llm_evidence_impact")
    stage3["heuristic"] = sum(1 for item in existing if item.get("source") != "llm_evidence_impact")
    report["stage3_impact_analysis"] = stage3


class PrivatePilotHandler(BaseHTTPRequestHandler):
    server_version = "QualiBugPrivatePilot/1.0"

    def _root(self) -> Path:
        configured = getattr(self.server, "qualibug_private_root", None)
        return Path(configured).resolve() if configured else _root()

    def _json(self, body: Any, status: int = 200) -> None:
        try:
            raw = json.dumps(body, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        except (ConnectionAbortedError, ConnectionResetError, OSError):
            pass  # client disconnected
        except Exception as exc:
            _dbg_report(
                hypothesis_id="A",
                msg=f"[DEBUG] json-response-failed status={status}",
                data={"exc_type": type(exc).__name__, "exc": str(exc)},
            )
            raise

    def _html(self, body: str, status: int = 200) -> None:
        raw = body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _project(self) -> str:
        query = parse_qs(urlparse(self.path).query)
        return _safe_project_id((query.get("project") or [""])[0])

    def _request_tenant(self) -> str:
        return _tenant_from_headers(dict(self.headers))

    def _body(self) -> dict[str, Any]:
        size = int(self.headers.get("Content-Length", "0") or 0)
        if not size:
            return {}
        if size > 2_000_000:
            raise ValueError("Request body exceeds the private service limit.")
        raw = self.rfile.read(size)
        if not raw:
            return {}
        # Try UTF-8 first, then latin-1, then raw bytes
        for encoding in ("utf-8", "utf-8-sig", "latin-1"):
            try:
                parsed = json.loads(raw.decode(encoding))
                return parsed if isinstance(parsed, dict) else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
        return {}

    def _require_actor(self) -> dict[str, str] | None:
        actor = _actor(self.headers)
        if actor is None:
            server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
            local_dev_actor_allowed = (
                _truthy_env("QUALIBUG_LOCAL_DEV_ACTOR", "1")
                and server_host in {"127.0.0.1", "localhost", "::1"}
                and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
                and str(self.headers.get("X-QualiBug-No-Local-Dev") or "").strip() != "1"
            )
            if local_dev_actor_allowed:
                return {
                    "name": os.environ.get("QUALIBUG_LOCAL_ACTOR", "local_dev")[:120],
                    "role": os.environ.get("QUALIBUG_LOCAL_ROLE", "project_owner")[:64],
                }
        if actor is None:
            self._json(
                {
                    "ok": False,
                    "error": "MISSING_TRUSTED_ACTOR",
                    "message": "The private service requires trusted X-QualiBug-Actor and X-QualiBug-Role headers, unless localhost-only local development actor mode is enabled.",
                },
                401,
            )
        return actor

    def _require_role(self, actor: dict[str, str], allowed: set[str], action: str) -> bool:
        if actor.get("role") in allowed:
            return True
        self._json(
            {
                "ok": False,
                "error": "FORBIDDEN",
                "message": f"{action} requires one of: {', '.join(sorted(allowed))}.",
            },
            403,
        )
        return False

    def _require_project_scope(self, project: str) -> bool:
        """Require an explicit trusted project scope outside localhost dev mode.

        Actor and role headers establish *who* is calling, but they do not by
        themselves establish which customer/project data the caller may read or
        change.  In a public/private-cloud binding, the trusted reverse proxy
        must inject a comma-separated allow-list (or ``*`` for an explicitly
        authorized platform operator) through ``X-QualiBug-Project-Scopes``.

        The localhost-only development fallback remains intentionally narrow:
        it is available only while public binding is disabled, matching the
        existing local actor fallback used by the self-contained pilot demo.
        """
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        scopes, wildcard = _parse_project_scopes(raw)
        if wildcard or _safe_project_id(project) in scopes:
            return True

        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        )
        if local_development:
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_SCOPE_FORBIDDEN",
                "message": f"Requested project is outside the trusted {PROJECT_SCOPE_HEADER} allow-list.",
            },
            403,
        )
        return False

    def _project_list_scope_filter(self) -> tuple[set[str], bool]:
        server_host = str(getattr(self.server, "server_address", ("", 0))[0] or "")
        local_development = (
            server_host in {"127.0.0.1", "localhost", "::1"}
            and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1"
        )
        if local_development:
            return set(), True
        raw = str(self.headers.get(PROJECT_SCOPE_HEADER) or self.headers.get(PROJECT_SCOPE_HEADER.lower()) or "")
        return _parse_project_scopes(raw)

    def _require_known_project(self, project: str, root: Path) -> bool:
        project = _safe_project_id(project)
        if _known_project_exists(root, project):
            return True
        self._json(
            {
                "ok": False,
                "error": "PROJECT_NOT_FOUND",
                "message": f"项目 '{project}' 不存在，请先选择有效项目。",
            },
            404,
        )
        return False

    def _load_scan_history(self, project: str, root: Path) -> dict[str, Any]:
        """Load scan history from disk."""
        import json as _json
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        if not history_path.exists():
            return {"ok": True, "history": []}
        try:
            return {"ok": True, "history": _json.loads(history_path.read_text(encoding="utf-8"))}
        except Exception:
            return {"ok": True, "history": []}

    def _list_project_inputs(self, project: str, root: Path) -> dict[str, Any]:
        """List project input files as knowledge sources from disk."""
        import json as _json, os as _os, time as _time
        input_dir = root / "platform_inputs" / project
        sources = []
        if input_dir.exists():
            config_path = input_dir / "real_project_config.json"
            source_dir = input_dir
            if config_path.exists():
                try:
                    config = _json.loads(config_path.read_text(encoding="utf-8"))
                    src = config.get("source_dataset", "")
                    if src and _os.path.isdir(src):
                        source_dir = Path(src)
                except Exception:
                    pass
            for f in sorted(source_dir.iterdir()):
                if f.is_file() and not f.name.startswith("."):
                    ext = f.suffix.lower()
                    if ext in (".md",):
                        stype = "PRD" if "prd" in f.name.lower() else "\u4e1a\u52a1\u6587\u6863"
                    elif ext in (".yaml", ".yml", ".json"):
                        stype = "OpenAPI" if "openapi" in f.name.lower() else "\u89c4\u8303\u6587\u4ef6"
                    elif ext == ".sql":
                        stype = "\u6570\u636e\u5e93 Schema"
                    else:
                        stype = "\u4e1a\u52a1\u6587\u6863"
                    sources.append({
                        "source_id": f"input-{f.name}",
                        "filename": f.name,
                        "source_type": stype,
                        "status": "active",
                        "size_bytes": f.stat().st_size,
                        "created_at_utc": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(f.stat().st_mtime)),
                        "uploaded_at": _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(f.stat().st_mtime)),
                    })
        return {"ok": True, "sources": sources}

    def _render_onboard(self, project: str, root: Path) -> None:
        """Render a minimal project onboarding page."""
        from .product_ui import product_shell, section

        known = _known_project_exists(root, project)
        status = "Known project" if known else "Project has not been imported yet"
        body = section(
            "Project onboarding",
            "Import PRD, OpenAPI and business documents, then configure the target environment before running discovery.",
            f"<p class='text-muted'>{status}</p><p><a class='btn btn-primary' href='/materials?project={project}'>Open materials</a> <a class='btn btn-secondary' href='/settings?project={project}'>Open settings</a></p>",
            section_id="onboarding",
        )
        page = product_shell(
            title="Project onboarding",
            project_id=project,
            active="",
            eyebrow="Onboarding",
            headline="Start project onboarding",
            description="Complete the minimum inputs required for grounded discovery.",
            body=body,
        )
        self._html(page)

    def _render_findings(self, project: str, root: Path) -> None:
        """Render a minimal findings page for the private pilot server."""
        from .product_ui import product_shell, section

        body = section(
            "Findings",
            "Open the product frontend for the full evidence chain and remediation workflow.",
            f"<p><a class='btn btn-primary' href='/findings?project={project}'>Open findings</a> <a class='btn btn-secondary' href='/evidence?project={project}'>Open evidence</a></p>",
            section_id="findings",
        )
        page = product_shell(
            title="Findings",
            project_id=project,
            active="findings",
            eyebrow="Evidence",
            headline="Validated findings",
            description="Customer-safe summary of validated product risks.",
            body=body,
        )
        self._html(page)

    def _llm_available(self) -> bool:
        return self._llm_health()["available"]

    def _llm_health(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningConfig
            cfg = ReasoningConfig.from_env()
            configured = cfg.enabled
        except Exception as exc:
            return {"configured": False, "available": False, "status": "failed", "label": "Failed", "error": str(exc)[:160]}
        if not configured:
            return {"configured": False, "available": False, "status": "offline", "label": "Not configured"}
        forced_status = os.environ.get("QUALIBUG_LLM_HEALTH_STATUS", "").strip().lower()
        if forced_status in {"online", "failed"}:
            return {"configured": True, "available": forced_status == "online", "status": forced_status, "label": "Verified online" if forced_status == "online" else "Verification failed"}
        last_status = os.environ.get("QUALIBUG_LLM_LAST_HEALTH_STATUS", "").strip().lower()
        if last_status in {"online", "failed"}:
            return {
                "configured": True,
                "available": last_status == "online",
                "status": last_status,
                "label": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_LABEL", "Verified online" if last_status == "online" else "Verification failed"),
                "error": os.environ.get("QUALIBUG_LLM_LAST_HEALTH_ERROR", ""),
            }
        return self._verify_llm_connectivity()

    def _verify_llm_connectivity(self) -> dict[str, Any]:
        try:
            from .llm_reasoning import ReasoningClient, ReasoningConfig

            cfg = ReasoningConfig.from_env()
            if not cfg.enabled:
                result = {"configured": False, "available": False, "status": "offline", "label": "Not configured", "error": "Missing LLM_BASE_URL, LLM_API_KEY or LLM_MODEL."}
            else:
                cfg.timeout_seconds = int(os.environ.get("LLM_HEALTH_TIMEOUT_SECONDS", "15"))
                client = ReasoningClient(cfg)
                client.health_check()
                result = {"configured": True, "available": True, "status": "online", "label": "Verified online"}
        except Exception as exc:
            result = {"configured": True, "available": False, "status": "failed", "label": "Verification failed", "error": str(exc)[:300]}
        os.environ["QUALIBUG_LLM_LAST_HEALTH_STATUS"] = str(result["status"])
        os.environ["QUALIBUG_LLM_LAST_HEALTH_LABEL"] = str(result["label"])
        if result.get("error"):
            os.environ["QUALIBUG_LLM_LAST_HEALTH_ERROR"] = str(result["error"])
        else:
            os.environ.pop("QUALIBUG_LLM_LAST_HEALTH_ERROR", None)
        return result

    def do_GET(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        project = self._project()
        root = self._root()
        if parsed.path in {"/health", "/api/health"}:
            import platform, sys
            llm_health = self._llm_health()
            try:
                from .bug_knowledge_graph import EnterprisePatternLibrary
                lib = EnterprisePatternLibrary()
                pattern_count = lib.stats().get("total_patterns", 0)
            except Exception:
                pattern_count = 0
            return self._json(
                {
                    "ok": True,
                    "service": "qualibug_private_pilot",
                    "version": "phase61",
                    "private_root": str(root),
                    "private_root_exists": root.exists(),
                    "public_bind_allowed": os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") == "1",
                    "python_version": sys.version.split()[0],
                    "platform": platform.system(),
                    "llm_available": llm_health["available"],
                    "llm_status": llm_health,
                    "pattern_library_patterns": pattern_count,
                }
            )
        # Every non-health route requires a trusted actor. `_require_actor()`
        # keeps the narrowly-scoped localhost development fallback, but only
        # when public binding is disabled and the caller has not explicitly
        # requested a negative-auth probe. This prevents public/private-cloud
        # GET endpoints from silently serving project data to anonymous users.
        actor = self._require_actor()
        if actor is None:
            return
        if not self._require_project_scope(project):
            return
        if parsed.path == "/onboard":
            return self._render_onboard(project, root)
        if parsed.path in {"/", "/dashboard"}:
            build_enterprise_pilot_overview(project, root)
            path = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            fallback = "<h1>Enterprise pilot dashboard is not generated yet.</h1>"
            return self._html(path.read_text(encoding="utf-8") if path.exists() else fallback)
        if parsed.path == "/knowledge":
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset, render_enterprise_business_knowledge_center

            asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
            return self._html(render_enterprise_business_knowledge_center(project, root, asset))
        if parsed.path == "/benchmark":
            from .enterprise_testops_control_plane import render_multi_industry_benchmark_report, run_multi_industry_benchmark

            report = run_multi_industry_benchmark(project, root)
            return self._html(render_multi_industry_benchmark_report(report))
        if parsed.path == "/release":
            from .release_risk_dashboard import build_release_risk_dashboard, render_release_risk_dashboard_html

            dashboard = build_release_risk_dashboard(project, root)
            return self._html(render_release_risk_dashboard_html(dashboard))
        if parsed.path == "/settings":
            return self._render_settings(project, root)
        if parsed.path == "/findings":
            return self._render_findings(project, root)
        if parsed.path == "/api/v1/projects":
            # Try DB first, fallback to file system
            try:
                db_persist.init_db(root)
                items = db_persist.list_projects(root, _tenant_from_headers(dict(self.headers)))
            except Exception:
                items = []
            if not items:
                scopes, wildcard = self._project_list_scope_filter()
                for d in sorted(root.glob("platform_outputs/*")):
                    if d.is_dir():
                        if not wildcard and d.name not in scopes:
                            continue
                        items.append({"project_id": d.name, "customer_name": d.name, "project_name": d.name})
            return self._json({"ok": True, "data": items})
        # Bridge: serve V12 results in legacy format for Dashboard/Findings
        if parsed.path.startswith("/api/v1/projects/") and parsed.path.endswith("/command-center"):
            pid = parsed.path.split("/")[4] if len(parsed.path.split("/")) >= 5 else ""
            import urllib.parse; pid = urllib.parse.unquote(pid)
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
                _dbg_report(
                    hypothesis_id="C",
                    msg="[DEBUG] command-center built",
                    data={"project_id": pid, "elapsed_ms": int((time.perf_counter() - started) * 1000), "keys": list(payload.keys())[:16]},
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
        return self._json({"ok": False, "error": "NOT_FOUND"}, 404)


    def _render_report_html(self, project: str, root: Path) -> None:
        """Generate a standalone HTML report."""
        import json as _json, time as _time
        report_path = root / "platform_outputs" / project / "pipeline_reports" / "latest_pipeline_report.json"
        history_path = root / "platform_outputs" / project / "pipeline_reports" / "scan_history.json"
        report = {}
        if report_path.exists():
            try: report = _json.loads(report_path.read_text(encoding="utf-8"))
            except: pass
        history = []
        if history_path.exists():
            try: history = _json.loads(history_path.read_text(encoding="utf-8"))
            except: pass

        s2 = report.get("stage2_discovery", {})
        s1 = report.get("stage1_industry", {})
        s3 = report.get("stage3_impact_analysis", {})
        findings = s2.get("findings", [])
        analyses = s3.get("analyses", [])

        # Build HTML
        f_rows = ""
        for f in findings:
            sev = f.get("severity", "?")
            sev_color = "#dc2626" if sev in ("P0","P1") else "#d97706" if sev == "P2" else "#2563eb"
            f_rows += f"""<tr><td style="color:{sev_color};font-weight:700">{sev}</td><td>{f.get("title","-")}</td><td>{f.get("category","-")}</td><td>{f.get("confidence_score","-")}</td><td>{f.get("evidence","-")[:200]}</td></tr>"""

        html = f"""<!DOCTYPE html><html lang="zh"><head><meta charset="UTF-8">
<title>QualiBug Bug 鎵弿鎶ュ憡 - {project}</title>
<style>body{{font-family:-apple-system,BlinkMacSystemFont,sans-serif;max-width:900px;margin:40px auto;padding:0 20px;color:#1e293b;background:#f8fafc}}
h1{{font-size:24px;border-bottom:2px solid #3b82f6;padding-bottom:12px}}
h2{{font-size:18px;margin-top:32px;color:#334155}}
table{{width:100%;border-collapse:collapse;margin:16px 0;font-size:13px}}
th,td{{text-align:left;padding:8px 12px;border-bottom:1px solid #e2e8f0}}
th{{background:#f1f5f9;font-weight:700;color:#475569}}
.metric{{display:inline-block;text-align:center;padding:16px 24px;border-radius:8px;margin:8px;background:#fff;box-shadow:0 1px 3px rgba(0,0,0,.1)}}
.metric strong{{display:block;font-size:28px;color:#3b82f6}}
.metric span{{font-size:11px;color:#94a3b8}}
.footer{{margin-top:32px;font-size:11px;color:#94a3b8;border-top:1px solid #e2e8f0;padding-top:12px}}</style></head><body>
<h1>QualiBug AI 路 Bug 鎵弿鎶ュ憡</h1>
<p>椤圭洰: <strong>{project}</strong> 路 鐢熸垚鏃堕棿: <strong>{_time.strftime("%Y-%m-%d %H:%M:%S")}</strong></p>
<p>瀵硅薄: {s1.get("object_count",0)} 路 瀵硅薄鏁? {s1.get("object_count",0)} 路 椋庨櫓鍩? {s1.get("risk_count",0)}</p>
<div style="margin:20px 0">
<div class="metric"><span>鎬诲彂鐜?/span><strong>{len(findings)}</strong></div>
<div class="metric"><span>P0/P1</span><strong>{sum(1 for f in findings if str(f.get("severity","")) in ("P0","P1"))}</strong></div>
<div class="metric"><span>LLM 鍒嗘瀽</span><strong>{s3.get("llm_powered",0)}</strong></div>
<div class="metric"><span>瑕嗙洊</span><strong>{s3.get("total_analyses",0)}/{max(1,len(findings))}</strong></div>
</div>
<h2>Bug 鍙戠幇鍒楄〃</h2>
<table><tr><th>涓ラ噸搴?/th><th>鏍囬</th><th>绫诲埆</th><th>缃俊搴?/th><th>璇佹嵁</th></tr>{f_rows}</table>
<h2>鎵弿鍘嗗彶 (鏈€杩?10 娆?</h2>
<table><tr><th>鏃堕棿</th><th>鐘舵€?/th><th>鍙戠幇</th><th>P0/P1</th><th>瀵硅薄</th></tr>"""

        for h in history[-10:]:
            html += f"<tr><td>{h.get('timestamp_utc','-')}</td><td>{h.get('status','-')}</td><td>{h.get('total_findings',0)}</td><td>{h.get('p0p1_count',0)}</td><td>{h.get('industry','-')[:30]}</td></tr>"

        html += f"""</table>
<div class="footer">QualiBug AI Enterprise Edition 路 绉佹湁鍖栭儴缃?路 娴嬭瘯鐜鎵弿 路 缁濅笉瑙︾鐢熶骇鏁版嵁</div>
</body></html>"""

        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Disposition", "inline")
        self.end_headers()
        self.wfile.write(html.encode("utf-8"))

    def _render_settings(self, project: str, root: Path) -> None:
        """Render a minimal settings page for the private pilot server."""
        from .product_ui import product_shell, section, h

        llm_health = self._llm_health()
        llm_status = str(llm_health.get("status") or "offline")
        llm_label = str(llm_health.get("label") or "Not configured")
        body = section(
            "System settings",
            "Use the product frontend for full customer, topology, connector and LLM configuration.",
            f"<p>LLM status: <strong>{h(llm_label)}</strong> ({h(llm_status)})</p><p><a class='btn btn-primary' href='/settings?project={project}'>Open settings</a></p>",
            section_id="settings",
        )
        page = product_shell(
            title="System settings",
            project_id=project,
            active="settings",
            eyebrow="Settings",
            headline="System configuration",
            description="Secrets are never rendered back to the browser.",
            body=body,
            llm_status=llm_status,
        )
        self._html(page)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        root = self._root()
        # Auth & tenant routes — no actor required
        if parsed.path in ("/api/auth/login", "/api/tenants/create"):
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
                return self._json({
                    "ok": True,
                    "token": token,
                    "tenant_id": auth_result["tenant_id"],
                    "role": auth_result.get("role") or "admin",
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
        try:
            body = self._body()
            project = _safe_project_id(str(body.get("project_id") or self._project()))
            if not self._require_project_scope(project):
                return
            if parsed.path == "/api/knowledge/ingest":
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge source ingestion"):
                    return
                return self._handle_ingest(project, body, root, actor)
            elif parsed.path.startswith("/api/knowledge/delete"):
                if not self._require_role(actor, KNOWLEDGE_MANAGER_ROLES, "knowledge source deletion"):
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
                return self._handle_v12_scan(project, root, actor, body)
            elif parsed.path == "/api/v1/continuous/status":
                return self._json(_get_continuous_state(root, project))
            elif parsed.path == "/api/v1/spectrum/status":
                return self._get_spectrum_status(project, root)
            elif parsed.path == "/api/v1/db-test":
                return self._handle_db_test(body)
            elif parsed.path == "/api/v1/services/credentials":
                if self.command == "GET":
                    return self._handle_get_service_credentials(project, root)
                return self._handle_save_service_credentials(project, root, body)
            elif parsed.path == "/api/settings/save":
                if not self._require_role(actor, SETTINGS_MANAGER_ROLES, "system settings update"):
                    return
                return self._handle_settings_save(body)
            elif parsed.path == "/api/connectors/register":
                result = operate_enterprise_pilot_runtime(project, "register_connector", body, root, actor)
                return self._json({"ok": True, "message": "Connector registered."})
            else:
                return self._json({"ok": False, "error": "NOT_FOUND"}, 404)
            return self._json(result)
        except PermissionError as exc:
            return self._json({"ok": False, "error": "FORBIDDEN", "message": str(exc)}, 403)
        except (ValueError, KeyError) as exc:
            return self._json({"ok": False, "error": "BAD_REQUEST", "message": str(exc)}, 400)
        except Exception as exc:  # pragma: no cover - defensive private-service boundary
            return self._json({"ok": False, "error": "INTERNAL_ERROR", "message": str(exc)[:300]}, 500)

    def _handle_ingest(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Handle document ingestion via API with verbatim byte storage."""
        import base64
        from .document_change_watcher import ingest_document
        from .enterprise_knowledge_center import ingest_enterprise_knowledge_documents

        if not self._require_known_project(project, root):
            return
        doc_type = str(body.get("type") or body.get("doc_type") or "prd").strip().lower() or "prd"
        filename = Path(str(body.get("filename") or body.get("name") or f"{doc_type}.md")).name or f"{doc_type}.md"
        content_b64 = str(body.get("content") or body.get("data") or "")
        if not content_b64:
            return self._json({"ok": False, "error": "MISSING_CONTENT", "message": "Missing base64 encoded file content."}, 400)

        # Decode once and persist the original bytes so PDF/DOCX uploads are not
        # corrupted by an unnecessary UTF-8 text round-trip.
        try:
            raw = base64.b64decode(content_b64, validate=True)
        except Exception:
            return self._json({"ok": False, "error": "DECODE_FAILED", "message": "Base64 解码失败，请检查文件内容。"}, 400)

        # Save to project input dir
        input_dir = root / "platform_workspace" / project / "input"
        input_dir.mkdir(parents=True, exist_ok=True)
        out_path = input_dir / filename
        out_path.write_bytes(raw)

        # Run document intelligence pipeline
        from .document_change_watcher import ingest_document as _ingest
        doc_info = _ingest(str(out_path))

        # Ingest into knowledge center 鈥?must pass document envelope dicts
        try:
            ingest_result = ingest_enterprise_knowledge_documents(project, [{"file_path": str(out_path), "filename": filename, "source_type": doc_type}], root=root, actor=actor)
            if isinstance(ingest_result, dict) and ingest_result.get("ok") is False:
                try:
                    out_path.unlink(missing_ok=True)
                except Exception:
                    pass
                errors = ingest_result.get("errors") if isinstance(ingest_result.get("errors"), list) else []
                first_error = errors[0].get("error") if errors and isinstance(errors[0], dict) else "unknown"
                message = "资料导入失败：" + str(first_error)
                return self._json({"ok": False, "error": "INGEST_FAILED", "message": message}, 500)
            knowledge_updated = True
            created = ingest_result.get("created") if isinstance(ingest_result, dict) and isinstance(ingest_result.get("created"), list) else []
            duplicates = ingest_result.get("duplicates") if isinstance(ingest_result, dict) and isinstance(ingest_result.get("duplicates"), list) else []
            source_id = ""
            ingest_status = "created"
            if created and isinstance(created[0], dict):
                source_id = str(created[0].get("source_id") or "")
            elif duplicates and isinstance(duplicates[0], dict):
                source_id = str(duplicates[0].get("source_id") or "")
                ingest_status = "duplicate"
            # Clear caches so dashboard picks up new data
            try:
                knowledge_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
                if knowledge_cache.exists():
                    knowledge_cache.unlink()
                dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
                if dash_html.exists():
                    dash_html.unlink()
            except Exception:
                pass

            # ── Validate before auto-scan ──
            # Trigger auto-scan for ALL meaningful project documents
            # (PRD, DB schema, business rules, configs etc. all contain bug-relevant info)
            auto_scan_types = {"openapi", "prd", "markdown_api", "db_design", "business_rules",
                              "test_data", "config", "deploy", "ui_design", "collaboration_document",
                              "mobile_android", "mobile_ios"}
            auto_scan_reason = ""
            should_auto_scan = doc_type in auto_scan_types
            if should_auto_scan and doc_type in ("openapi", "markdown_api"):
                # Parse the uploaded file to verify it has real API endpoints
                try:
                    from .universal_api_parser import parse_to_openapi
                    parsed = parse_to_openapi(str(out_path))
                    paths = parsed.get("paths", {})
                    if not paths:
                        should_auto_scan = False
                        auto_scan_reason = "文件未检测到有效的API端点定义，跳过自动检测。请确认文件格式正确。"
                    # Even a single endpoint is valid — always trigger if parseable
                except Exception:
                    auto_scan_reason = "文件解析失败，跳过自动检测。"

            if should_auto_scan and doc_type == "prd":
                # PRD changes of any size can introduce bugs (e.g., a single
                # logic rule change can break payment calculation). Always trigger.
                auto_scan_reason = "PRD 已更新，自动触发检测。"

            # ── Auto-trigger scan (validated) ──
            if should_auto_scan:
                import threading as _threading
                _scan_root = root
                _scan_project = project
                def _auto_scan():
                    try:
                        import time as _time
                        _time.sleep(2)  # brief delay for file system sync
                        from .__main__ import scan as _scan_fn
                        _scan_result = _scan_fn(_scan_project, _scan_root, save_report=True)
                        _update_continuous_state(_scan_root, _scan_project, _scan_result)
                    except Exception:
                        pass
                _threading.Thread(target=_auto_scan, daemon=True).start()
                ingest_status = f"{ingest_status}_auto_scanning"
            elif auto_scan_reason:
                ingest_status = f"{ingest_status}_scan_skipped"
        except Exception:
            knowledge_updated = False
            source_id = ""
            ingest_status = "saved_only"

        return self._json({
            "ok": True,
            "source_id": source_id,
            "ingest_status": ingest_status,
            "auto_scan": "triggered" if "auto_scanning" in ingest_status else ("skipped" if "scan_skipped" in ingest_status else "not_applicable"),
            "auto_scan_reason": auto_scan_reason or "",
            "filename": filename,
            "doc_type": doc_type,
            "size_bytes": len(raw),
            "path": str(out_path),
            "storage_mode": "verbatim_bytes",
            "supported_source_types": list(KNOWLEDGE_INGEST_SOURCE_TYPES),
            "supported_extensions": list(KNOWLEDGE_INGEST_EXTENSIONS),
            "doc_info": doc_info,
            "knowledge_updated": knowledge_updated,
            "message": f"'{filename}' imported." if knowledge_updated else "File saved but knowledge index was not updated.",
        })

    def _handle_delete(self, project: str, body: dict[str, Any], root: Path, actor: dict[str, str]) -> None:
        """Delete a knowledge source by source_id."""
        from .enterprise_knowledge_center import delete_enterprise_knowledge_source
        source_id = str(body.get("source_id") or "").strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID", "message": "Missing source_id."}, 400)
        try:
            result = delete_enterprise_knowledge_source(project, source_id, root, actor)
        except KeyError:
            return self._json({"ok": False, "error": "NOT_FOUND", "message": f"Source {source_id} was not found or already deleted."}, 404)
        try:
            asset_cache = root / "platform_workspace" / project / "defect_discovery" / "enterprise_business_knowledge_asset.json"
            if asset_cache.exists(): asset_cache.unlink()
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
        except Exception: pass
        filename = str(result.get("original_name") or source_id)
        return self._json({
            "ok": True,
            "source_id": source_id,
            "filename": filename,
            "removed_paths": result.get("removed_paths") or [],
            "message": f"'{filename}' permanently deleted.",
        })

    def _handle_v12_scan(self, project: str, root: Path, actor: dict[str, str], body: dict[str, Any]) -> None:
        try:
            from .__main__ import scan
            api_doc = str(body.get("api_doc") or body.get("api_doc_text") or "")
            base_url = str(body.get("base_url") or "").strip()
            if not api_doc:
                p = root / "platform_outputs" / project / "api_spec.md"
                if p.exists(): api_doc = p.read_text(encoding="utf-8")
            # Auto-build API doc + base_url from connectors
            if not api_doc or not base_url:
                from .enterprise_pilot_runtime import load_connector_registry
                reg = load_connector_registry(project, root)
                connectors = reg.get("connectors", [])
                enabled = [c for c in connectors if c.get("enabled")]
                if enabled:
                    if not base_url:
                        for c in enabled:
                            ep = c.get("endpoint_ref", "")
                            if ep and (ep.startswith("http://") or ep.startswith("https://")):
                                base_url = ep; break
                    if not api_doc:
                        lines = []
                        for c in enabled:
                            ep = c.get("endpoint_ref", "")
                            if ep:
                                lines.append(f"| POST | {ep}/api/orders | 鍒涘缓 | product_id,quantity |")
                                lines.append(f"| POST | {ep}/api/orders/{{id}}/pay | 鏀粯 | amount |")
                                lines.append(f"| POST | {ep}/api/orders/{{id}}/cancel | 鍙栨秷 | |")
                                lines.append(f"| POST | {ep}/api/orders/{{id}}/refund | 閫€娆?| |")
                                lines.append(f"| POST | {ep}/api/register | 娉ㄥ唽 | username,password,role |")
                                lines.append(f"| GET | {ep}/api/admin/stats | 绠＄悊缁熻 | |")
                                lines.append(f"| GET | {ep}/api/audit-logs | 瀹¤鏃ュ織 | |")
                                lines.append(f"| GET | {ep}/api/products | 鍟嗗搧鍒楄〃 | |")
                        if lines:
                            api_doc = "\n".join(lines)
                # Fallback
                if not api_doc and base_url:
                    api_doc = f"| POST | {base_url}/api/orders | 鍒涘缓 |\n| GET | {base_url}/api/products | 鍟嗗搧鍒楄〃 |\n| GET | {base_url}/api/admin/stats | 绠＄悊缁熻 |"

            result = scan(project=project, root=root, prd_text=str(body.get("prd","")),
                          api_doc_text=api_doc, base_url=base_url, multi_layer=bool(base_url))
            # Persist to DB
            try:
                db_persist.init_db(root)
                # Extract findings from report
                report_path = root / "platform_outputs" / project / "intelligence_report.json"
                report_data = {}
                if report_path.exists():
                    import json as _jr
                    report_data = _jr.loads(report_path.read_text(encoding="utf-8"))
                findings_list = report_data.get("real_findings") or report_data.get("bug_scores") or []
                enriched = dict(result)
                enriched["findings"] = [
                    {"title": str(f.get("title") or f.get("description", ""))[:120],
                     "severity": str(f.get("severity", "P1")),
                     "category": str(f.get("category", "")),
                     "description": str(f.get("description", ""))[:500],
                     "confidence_score": float(f.get("confidence_score") or f.get("score") or 0)}
                    for f in (findings_list if isinstance(findings_list, list) else [])
                    if isinstance(f, dict)
                ]
                db_persist.save_scan(root, self._request_tenant(), project, enriched)
            except Exception:
                pass
            # Increment scan counter
            try:
                import json as _j2, time as _t
                counter_path = root / "platform_outputs" / project / "scan_counter.json"
                c = {"count": 1, "first_scan_at": _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())}
                if counter_path.exists():
                    try: c = _j2.loads(counter_path.read_text(encoding="utf-8"))
                    except: pass
                c["count"] = c.get("count", 0) + 1
                c["last_scan_at"] = _t.strftime('%Y-%m-%dT%H:%M:%SZ', _t.gmtime())
                counter_path.write_text(_j2.dumps(c, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            # Also write raw findings for frontend Dashboard/Findings compatibility
            try:
                # Re-read from evaluation result
                out_dir = root / "platform_outputs" / project
                eval_json = out_dir / "intelligence_report.json"
                if eval_json.exists():
                    import json as _j2
                    existing = _j2.loads(eval_json.read_text(encoding="utf-8"))
                    # Merge raw findings — only set raw_total once (first scan), never decrement
                    old_raw = existing.get("raw_total", 0)
                    new_raw = result.get("total_findings", 0)
                    existing["raw_total"] = max(old_raw, new_raw) if old_raw else new_raw
                    # Preserve real_findings across scans
                    if not existing.get("real_findings"):
                        existing["real_findings"] = existing.get("bug_scores", [])
                    existing["layers"] = result.get("layers", {})
                    # Merge DB verification findings
                    db_finds = result.get("db_findings")
                    if isinstance(db_finds, list) and db_finds:
                        existing["db_verification"] = {"findings": db_finds, "total": len(db_finds)}
                    e2e_finds = result.get("e2e_findings")
                    if isinstance(e2e_finds, list) and e2e_finds:
                        existing.setdefault("e2e_findings", []).extend(e2e_finds)
                    deep_finds = result.get("deep_findings")
                    if isinstance(deep_finds, list) and deep_finds:
                        existing.setdefault("deep_findings", []).extend(deep_finds)
                    ui_finds = result.get("ui_findings")
                    if isinstance(ui_finds, list) and ui_finds:
                        existing.setdefault("ui_findings", []).extend(ui_finds)
                    eval_json.write_text(_j2.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")
            except Exception:
                pass
            # Save spectrum result to disk for Dashboard polling
            if result.get("spectrum"):
                spectrum_dir = root / "platform_outputs" / project / "spectrum"
                spectrum_dir.mkdir(parents=True, exist_ok=True)
                import json as _json_save
                _json_save.dump(result["spectrum"], (spectrum_dir / "spectrum_result.json").open("w", encoding="utf-8"),
                               ensure_ascii=False, default=str)

            # Update continuous state for manual scans too
            _update_continuous_state(root, project, result)

            return self._json({"ok": True, "scan_id": result.get("scan_id",""), "grade": result.get("grade",""),
                "score": result.get("score",0), "coverage": result.get("coverage",0),
                "total_findings": result.get("total_findings",0), "total_ms": result.get("total_ms",0),
                "layers": result.get("layers",{}),
                "spectrum": result.get("spectrum", {}),
                "auto_har": result.get("auto_har", {}),})
        except Exception as e:
            return self._json({"ok": False, "error": "V12_SCAN_FAILED", "message": str(e)[:500]}, 500)

    def _load_v12_report(self, project_id: str, root: Path) -> dict[str, Any]:
        import json

        report_path = root / "platform_outputs" / project_id / "intelligence_report.json"
        if not report_path.exists():
            report_path = root / "platform_outputs" / project_id / "v12_report.json"
        if not report_path.exists():
            return {}
        try:
            payload = json.loads(report_path.read_text(encoding="utf-8") or "{}")
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def _v12_findings(self, report: dict[str, Any], enterprise_docs: list[dict] | None = None) -> list[dict[str, Any]]:
        raw_items = report.get("real_findings") or report.get("findings") or report.get("bug_scores") or []
        if not isinstance(raw_items, list):
            return []
        docs = enterprise_docs or []
        findings: list[dict[str, Any]] = []
        for index, item in enumerate(raw_items):
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or item.get("bug_title") or item.get("description") or f"V12 finding {index + 1}")
            severity = str(item.get("severity") or "P1")
            # Extract API path from title/description
            import re
            path_match = re.search(r'(/api/[\w/{}]+)', str(item.get("description", "")) or title)
            api_path = path_match.group(1) if path_match else ""
            method_match = re.search(r'\b(POST|GET|PUT|DELETE|PATCH)\b', str(item.get("description", "")) or title, re.IGNORECASE)
            api_method = (method_match.group(1) if method_match else "").upper()
            # Match relevant enterprise documents
            matched = self._match_docs_for_finding(title, docs) if docs else []
            findings.append({
                "risk_id": str(item.get("risk_id") or item.get("bug_id") or item.get("evidence_id") or f"v12_{index}"),
                "title": title,
                "technical_title": title,
                "severity": severity.lower() if severity.lower() in {"critical", "high", "medium", "low"} else severity,
                "status": "confirmed",
                "risk_type": str(item.get("category") or item.get("risk_type") or "v12_discovery"),
                "summary": str(item.get("summary") or item.get("description") or title),
                "business_impact": str(item.get("business_impact") or item.get("description") or title),
                "suggested_action": str(item.get("suggested_action") or item.get("expected") or "Review the evidence and assign remediation."),
                "confidence_score": float(item.get("confidence_score") or item.get("score") or 0.75),
                "reproducibility_score": float(item.get("reproducibility_score") or 0.75),
                "affected_business_flow": {"name": str(item.get("flow") or item.get("category") or "system")},
                "affected_modules": [str(item.get("module") or item.get("category") or (api_path.split("/")[2] if len(api_path.split("/")) > 2 else "") or self._extract_module(title, str(item.get("description", ""))))],
                "affected_roles": [],
                "first_seen_at": str(item.get("first_seen_at") or report.get("generated_at_utc") or ""),
                "last_verified_at": str(item.get("last_verified_at") or report.get("generated_at_utc") or ""),
                "_doc_refs": matched,
                "_api_path": api_path,
                "_api_method": api_method,
            })
        return findings

    def _load_enterprise_docs(self, project_id: str, root: Path) -> list[dict]:
        """Load enterprise knowledge documents for evidence association."""
        doc_path = root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json"
        if not doc_path.exists():
            return []
        try:
            import json
            asset = json.loads(doc_path.read_text(encoding="utf-8"))
            sources = asset.get("source_inventory", [])
            return [{"source_id": s.get("source_id",""), "display_name": s.get("display_name", s.get("filename","")),
                     "type": s.get("type",""), "excerpt": s.get("summary", "")[:200]} for s in sources if s.get("source_id")]
        except Exception:
            return []

    def _load_knowledge_summary(self, project_id: str, root: Path) -> dict[str, Any]:
        """Load a compact business-facing summary for the dashboard."""
        doc_path = root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json"
        if not doc_path.exists():
            return {}
        try:
            asset = json.loads(doc_path.read_text(encoding="utf-8"))
        except Exception:
            return {}
        summary = asset.get("summary")
        if not isinstance(summary, dict):
            return {}
        return {
            "active_source_count": int(summary.get("active_source_count") or 0),
            "rule_count": int(summary.get("rule_count") or 0),
            "risk_domain_count": int(summary.get("risk_domain_count") or 0),
            "oracle_count": int(summary.get("oracle_count") or 0),
            "business_object_count": int(summary.get("business_object_count") or 0),
            "state_machine_count": int(summary.get("state_machine_count") or 0),
            "knowledge_ready": bool(summary.get("knowledge_ready")),
        }

    def _extract_module(self, title: str, description: str) -> str:
        """Extract a meaningful module name from title/description content."""
        import re
        text = (title + " " + description).lower()
        # Known business modules
        for mod, keywords in [
            ("orders", ["order", "订单"]),
            ("payments", ["pay", "支付"]),
            ("users", ["user", "用户", "role", "admin", "register", "注册"]),
            ("products", ["product", "产品"]),
            ("inventory", ["inventory", "库存"]),
            ("permissions", ["permission", "权限", "auth", "认证"]),
            ("refunds", ["refund", "退款"]),
            ("notifications", ["notif", "通知"]),
        ]:
            if any(kw in text for kw in keywords):
                return mod
        return "system"

    def _match_docs_for_finding(self, title: str, docs: list[dict]) -> list[dict]:
        """Match enterprise documents to a finding by keyword overlap."""
        if not docs: return []
        title_lower = title.lower()
        matched = []
        for doc in docs:
            doc_text = f"{doc.get('display_name','')} {doc.get('excerpt','')} {doc.get('type','')}".lower()
            # Simple keyword match
            keywords = ["订单", "order", "支付", "pay", "退款", "refund", "取消", "cancel",
                       "权限", "permission", "admin", "注册", "register", "审计", "audit",
                       "库存", "inventory", "安全", "security"]
            score = sum(1 for kw in keywords if kw in title_lower and kw in doc_text)
            if score > 0:
                matched.append({**doc, "relevance": score})
        return sorted(matched, key=lambda m: -m.get("relevance", 0))[:3]

    def _build_command_center(self, project_id: str, root: Path) -> dict:
        report = self._load_v12_report(project_id, root)
        enterprise_docs = self._load_enterprise_docs(project_id, root)
        knowledge_summary = self._load_knowledge_summary(project_id, root)
        discovery_payload = _load_real_project_discovery_payload(root, project_id) or self._auto_discovery_payload(project_id, root, report)
        risks = self._v12_findings(report, enterprise_docs)
        risks.extend(self._load_db_findings(root, project_id))
        risks.extend(self._load_perf_regressions(root, project_id))
        risks.extend(self._load_spectrum_findings(root, project_id))
        risks.extend(self._load_multi_layer_findings(root, project_id))
        # Load DB verification findings
        db_verify = report.get("db_verification", {})
        if isinstance(db_verify.get("findings"), list):
            for f in db_verify["findings"]:
                f.setdefault("risk_type", "db_verification")
                f.setdefault("defect_family", "data_integrity")
            risks.extend(db_verify["findings"])
        # E2E flow findings
        e2e = report.get("e2e_findings", [])
        if isinstance(e2e, list):
            for f in e2e:
                f.setdefault("risk_type", "e2e_flow")
                f.setdefault("defect_family", "business_flow")
            risks.extend(e2e)
        # Deep verifier findings
        deep = report.get("deep_findings", [])
        if isinstance(deep, list):
            for f in deep:
                f.setdefault("risk_type", "deep_verifier")
                f.setdefault("defect_family", "deep_test")
            risks.extend(deep)
        # Frontend UI findings
        ui = report.get("ui_findings", [])
        if isinstance(ui, list):
            for f in ui:
                f.setdefault("risk_type", "frontend_ui")
                f.setdefault("defect_family", "ui")
            risks.extend(ui)
        total = len(risks)
        p0 = sum(1 for item in risks if str(item.get("severity") or "").upper() in {"P0", "CRITICAL"})
        p1 = sum(1 for item in risks if str(item.get("severity") or "").upper() in {"P1", "HIGH"})
        ai_points = int(report.get("raw_total") or report.get("total_findings") or total)
        scan_counter = self._scan_counter(project_id, root)
        scan_meta = {
            "scan_id": str(report.get("scan_id") or ""),
            "run_count": int(scan_counter.get("count") or 0),
            "first_scan_at": str(scan_counter.get("first_scan_at") or ""),
            "last_scan_at": str(scan_counter.get("last_scan_at") or report.get("generated_at_utc") or ""),
            "total_ms": int(report.get("total_ms") or 0),
            "total_findings": int(report.get("total_findings") or total),
            "grade": str(report.get("grade") or report.get("system_grade") or ""),
            "score": float(report.get("score") or report.get("overall_score") or 0),
            "report_path": str(report.get("report_path") or ""),
        }
        data = {
            "project_id": project_id,
            "project_name": project_id,
            "industry": "multi_layer",
            "updated_at": scan_meta["last_scan_at"] or str(report.get("generated_at_utc") or ""),
            "live_map": {"status": "completed" if report else "idle"},
            "scan_meta": scan_meta,
            "risks": risks,
            "value_metrics": {"evidence_trust_score": 0.8 if total else 0, "ai_equivalent_test_points": ai_points},
            "business_flow_summary": {"total": ai_points},
            "executive_summary": {
                "total_findings": total,
                "total_bugs_found": total,
                "critical_bugs": p0,
                "high_priority_bugs": p1,
                "llm_powered_analyses": ai_points,
                "system_grade": report.get("system_grade", ""),
                "overall_score": report.get("overall_score", 0),
            },
        }
        if knowledge_summary:
            data["knowledge_summary"] = knowledge_summary
        if isinstance(discovery_payload.get("continuous_discovery_campaign"), dict):
            data["continuous_discovery_campaign"] = discovery_payload["continuous_discovery_campaign"]
        if isinstance(discovery_payload.get("metrics"), dict):
            data["continuous_discovery_metrics"] = {
                key: value
                for key, value in discovery_payload["metrics"].items()
                if str(key).startswith("continuous_discovery_") or str(key) == "doc_completeness"
            }
        spectrum = self._load_spectrum_status_payload(root, project_id)
        if spectrum:
            data["spectrum"] = spectrum
        return {
            "ok": True,
            "data": data,
        }

    @staticmethod
    def _load_db_findings(root: Path, project_id: str) -> list[dict]:
        report = root / "platform_outputs" / project_id / "scan_result.json"
        if not report.exists():
            return []
        try:
            data = json.loads(report.read_text(encoding="utf-8"))
            db_findings = data.get("db_verification", {}).get("findings", [])
            for f in db_findings:
                f.setdefault("risk_type", "db_snapshot")
                f.setdefault("defect_family", "data_integrity")
                f.setdefault("confidence_score", 0.95)
            return db_findings
        except Exception:
            return []

    @staticmethod
    def _load_perf_regressions(root: Path, project_id: str) -> list[dict]:
        perf_file = root / "platform_outputs" / project_id / "performance" / "baseline.json"
        if not perf_file.exists():
            return []
        try:
            history = json.loads(perf_file.read_text(encoding="utf-8"))
            if not isinstance(history, list) or len(history) < 2:
                return []
            regressions = history[-1].get("regressions", []) if history else []
            findings = []
            for r in (regressions if isinstance(regressions, list) else []):
                if isinstance(r, dict):
                    findings.append({
                        "risk_id": "perf_reg_" + r.get("metric", "unknown"),
                        "title": r.get("detail", ""),
                        "severity": r.get("severity", "P2"),
                        "risk_type": "performance_regression",
                        "defect_family": "performance",
                        "confidence_score": 0.90,
                        "source": "performance_baseline",
                    })
            return findings
        except Exception:
            return []

    @staticmethod
    def _load_spectrum_findings(root: Path, project_id: str) -> list[dict]:
        spectrum = root / "platform_outputs" / project_id / "spectrum" / "spectrum_result.json"
        if not spectrum.exists():
            return []
        try:
            data = json.loads(spectrum.read_text(encoding="utf-8"))
            findings = []
            caps = data.get("capabilities", [])
            for cap in (caps if isinstance(caps, list) else []):
                # Skip test_gen — these are test case templates, not real bugs
                if cap.get("id") == "test_gen":
                    continue
                for f in (cap.get("findings", []) if isinstance(cap, dict) else []):
                    if isinstance(f, dict) and f.get("bug_id"):
                        findings.append({
                            "risk_id": f.get("bug_id", ""),
                            "title": f"[全频谱] {f.get('title', '')}",
                            "severity": f.get("severity", "P2"),
                            "risk_type": f"spectrum_{cap.get('id', 'unknown')}",
                            "defect_family": "spectrum",
                            "confidence_score": float(f.get("confidence", 0.85)),
                            "summary": str(f.get("description", f.get("title", ""))),
                            "source": "full_spectrum",
                        })
            return findings
        except Exception:
            return []

    @staticmethod
    def _load_spectrum_status_payload(root: Path, project_id: str) -> dict:
        result_file = root / "platform_outputs" / project_id / "spectrum" / "spectrum_result.json"
        ts_file = root / "platform_outputs" / project_id / "spectrum" / "spectrum_timestamp.txt"
        if not result_file.exists():
            return {"status": "not_run", "message": "尚未运行全频谱检测", "summary": {"total_findings": 0}, "capabilities": []}
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            capabilities = result.get("capabilities")
            summary = result.get("summary")
            return {
                "status": "completed",
                "last_run": ts_file.read_text(encoding="utf-8").strip() if ts_file.exists() else "",
                "summary": summary if isinstance(summary, dict) else {"total_findings": 0},
                "capabilities": capabilities if isinstance(capabilities, list) else [],
            }
        except Exception:
            return {"status": "error", "message": "无法读取检测结果", "summary": {"total_findings": 0}, "capabilities": []}

    @staticmethod
    @staticmethod
    def _load_multi_layer_findings(root: Path, project_id: str) -> list[dict]:
        scan_file = root / "platform_outputs" / project_id / "scan_result.json"
        if not scan_file.exists():
            return []
        try:
            data = json.loads(scan_file.read_text(encoding="utf-8"))
            layers = data.get("layers", {})
            findings = []
            for layer_name, layer_data in layers.items():
                if not isinstance(layer_data, dict):
                    continue
                details = layer_data.get("findings_details", [])
                if details:
                    for i, fd in enumerate(details):
                        findings.append({
                            "risk_id": f"layer_{layer_name}_{i}",
                            "title": f"[{layer_name.upper()}] {fd.get('title', '')}",
                            "severity": fd.get("severity", "P2"),
                            "risk_type": f"multi_layer_{layer_name}",
                            "defect_family": "multi_layer",
                            "confidence_score": 0.85,
                            "summary": fd.get("description", ""),
                            "source": f"multi_layer_{layer_name}",
                        })
                else:
                    count = layer_data.get("findings", 0)
                    if count > 0:
                        findings.append({
                            "risk_id": f"layer_{layer_name}",
                            "title": f"[{layer_name.upper()}] {count} 个{layer_name}层发现（详情需运行扫描获取）",
                            "severity": "P2",
                            "risk_type": f"multi_layer_{layer_name}",
                            "defect_family": "multi_layer",
                            "confidence_score": 0.70,
                            "source": "multi_layer",
                        })
            return findings
        except Exception:
            return []

    def _scan_counter(self, project_id: str, root: Path) -> dict:
        """Track how many times V12 scan has run for this project."""
        import json, time
        counter_path = root / "platform_outputs" / project_id / "scan_counter.json"
        if counter_path.exists():
            try:
                return json.loads(counter_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        return {"count": 1, "first_scan_at": time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}

    def _auto_discovery_payload(self, project_id: str, root: Path, report: dict[str, Any]) -> dict:
        """Auto-generate continuous discovery payload — tracks convergence across rounds."""
        import time
        now = time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())
        real_findings = report.get("real_findings") or report.get("bug_scores") or []
        if isinstance(real_findings, list):
            real_findings = [item for item in real_findings if isinstance(item, dict)]
        else:
            real_findings = []
        total_findings = len(real_findings)
        raw_total = int(report.get("raw_total") or report.get("total_findings") or total_findings)

        # Track convergence: compare with previous scan findings
        prev_titles = self._previous_finding_titles(project_id, root)
        # Build current titles from report — match the DB storage format
        current_titles = set()
        for f in real_findings:
            t = str(f.get("title") or f.get("description", ""))[:120]
            if t: current_titles.add(t.lower())  # Normalize for matching
        new_count = len(current_titles - prev_titles)
        confirmed_count = len(current_titles & prev_titles)
        resolved_count = max(0, raw_total - total_findings)

        verified = sum(1 for f in real_findings if max(float(f.get("confidence_score", 0)), float(f.get("score", 0)), float(f.get("confidence", 0))) > 0.5)
        blocked = sum(1 for f in real_findings if str(f.get("severity", "")).upper() in ("P0", "CRITICAL"))

        scan_counter = self._scan_counter(project_id, root)
        current_run = scan_counter.get("count", total_findings // 3 or 1)
        total_discovered = len(prev_titles | current_titles)  # All unique findings ever
        pending = max(0, raw_total - total_discovered)

        return {
            "project_id": project_id,
            "generated_at_utc": now,
            "continuous_discovery_campaign": {
                "summary": {
                    "campaign_state": "in_progress",
                    "run_count": current_run,
                    "coverage_ledger_entry_count": raw_total,
                    "validated_frontier_count": confirmed_count + new_count,
                    "remaining_actionable_frontier_count": pending,
                    "blocked_frontier_count": blocked,
                    "revalidation_queue_size": max(0, total_findings - verified),
                    "can_stop_now": pending == 0 and new_count == 0,
                    "frontier_burn_down_count": confirmed_count,
                    "frontier_burn_down_rate": round(confirmed_count / max(1, total_discovered), 2),
                    "current_run_validated_yield": new_count,
                    "marginal_validated_yield_threshold": max(1, raw_total // 5),
                    "new_this_round": new_count,
                    "confirmed": confirmed_count,
                    "total_discovered": total_discovered,
                },
                "coverage_ledger": {
                    "entries": [
                        {"last_status": "validated" if str(f.get("title",""))[:80] in prev_titles else "new",
                         "frontier": {"title": str(f.get("title", f.get("description", "")))[:60]},
                         "last_blocker_reason": ""}
                        for f in real_findings[:10]
                    ],
                    "status_counts": {"validated": confirmed_count, "new": new_count, "blocked": blocked, "pending": pending},
                },
                "recommended_frontier": [
                    {"title": str(f.get("title", f.get("description", "")))[:60],
                     "value_score": int(float(f.get("confidence_score", f.get("score", 1))) * 10)}
                    for f in real_findings[:5]
                ],
            },
            "metrics": {
                "continuous_discovery_coverage": round(total_discovered / max(1, raw_total) * 100, 1),
                "continuous_discovery_total": raw_total,
                "continuous_discovery_verified": total_discovered,
                "continuous_discovery_new": new_count,
                "continuous_discovery_confirmed": confirmed_count,
                "continuous_discovery_blocked": blocked,
                "doc_completeness": self._doc_completeness_score(project_id, root),
            },
        }

    def _previous_finding_titles(self, project_id: str, root: Path) -> set:
        """Read previous scan findings from DB for convergence tracking."""
        try:
            db_persist.init_db(root)
            import sqlite3
            db_path = root / db_persist.DB_FILENAME
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row
            # Try both the project_id and normalized version
            rows = conn.execute(
                "SELECT title FROM findings WHERE tenant_id IN (?, ?) AND project_id IN (?, ?) ORDER BY created_at",
                (self._request_tenant(), "default", project_id, str(project_id).replace('科技',''))
            ).fetchall()
            conn.close()
            return {r["title"][:120].lower() for r in rows}
        except Exception:
            return set()

    def _doc_completeness_score(self, project_id: str, root: Path) -> int:
        """Score 0-100 based on uploaded enterprise documents knowledge richness."""
        try:
            kc_path = root / "platform_outputs" / project_id / "enterprise_knowledge_center" / "enterprise_business_knowledge_asset.json"
            if not kc_path.exists():
                return 0
            import json as _jk
            kc = _jk.loads(kc_path.read_text(encoding="utf-8"))
            sources = len(kc.get("source_inventory") or kc.get("sources") or [])
            rules = len(kc.get("rule_library") or [])
            states = len(kc.get("state_machines") or [])
            roles = len(kc.get("roles") or [])
            interfaces = len(kc.get("interfaces") or [])
            score = min(100, sources * 15 + rules * 8 + states * 5 + roles * 3 + interfaces * 3)
            return max(0, score)
        except Exception as e:
            print(f"ERROR in _doc_completeness_score: {e}")
            return 0

    def _handle_db_test(self, body: dict[str, Any]) -> None:
        """Validate that a database DSN was provided without echoing secrets."""
        dsn = str(body.get("dsn") or "").strip()
        if not dsn:
            return self._json({"ok": False, "error": "MISSING_DSN", "message": "Missing DSN."}, 400)
        scheme = dsn.split(":", 1)[0].lower() if ":" in dsn else "unknown"
        return self._json({"ok": True, "message": "DSN accepted for validation.", "db_type": scheme})

    # ── Multi-Service Credential Management ──

    def _handle_get_service_credentials(self, project: str, root: Path) -> None:
        """Return current multi-service credential configuration."""
        config_path = root / "platform_workspace" / project / "multi_service_config.json"
        services = []
        try:
            if config_path.exists():
                data = json.loads(config_path.read_text(encoding="utf-8"))
                services = data.get("services", [])
        except Exception:
            pass
        return self._json({"project": project, "services": services})

    def _handle_save_service_credentials(self, project: str, root: Path, body: dict) -> None:
        """Save credentials for a single service, merging into multi_service_config.json."""
        service_data = body.get("service", {})
        if not isinstance(service_data, dict) or not service_data.get("name"):
            return self._json({"ok": False, "error": "MISSING_NAME",
                              "message": "service.name is required"}, 400)
        previous_name = str(body.get("previous_name") or "").strip()
        config_path = root / "platform_workspace" / project / "multi_service_config.json"
        config: dict = {}
        if config_path.exists():
            try:
                config = json.loads(config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                pass
        config.setdefault("services", [])
        config.setdefault("project_name", project)
        config.setdefault("cross_service_contracts", [])
        config.setdefault("external_integrations", [])

        # Upsert: update existing or append new
        name = service_data["name"]
        updated = False
        for i, svc in enumerate(config["services"]):
            if svc.get("name") in {name, previous_name}:
                existing = dict(svc)
                existing["name"] = name
                existing["base_url"] = service_data.get("base_url", "")
                existing["enabled"] = bool(service_data.get("enabled", True))
                # Build auth section — include all role accounts
                auth = {
                    "type": service_data.get("auth_type", "password_login"),
                    "login_api": service_data.get("login_api", "/auth/login"),
                }
                # Multi-role accounts (new)
                role_accounts = service_data.get("role_accounts") or []
                for ra in role_accounts:
                    if isinstance(ra, dict) and ra.get("role") and ra.get("username"):
                        auth.setdefault(ra["role"], {})
                        auth[ra["role"]]["username"] = ra["username"]
                        auth[ra["role"]]["password"] = ra.get("password", "")
                # Legacy single admin (backward compat)
                if not role_accounts:
                    if service_data.get("admin_user"):
                        auth.setdefault("admin", {})
                        auth["admin"]["username"] = service_data["admin_user"]
                    if service_data.get("admin_pass"):
                        auth.setdefault("admin", {})
                        auth["admin"]["password"] = service_data["admin_pass"]
                if service_data.get("bearer_token"):
                    auth["bearer_token"] = service_data["bearer_token"]
                if service_data.get("api_key"):
                    auth["api_key"] = service_data["api_key"]
                existing["auth"] = auth
                for legacy_key in ("login_api", "auth_type", "admin_user", "admin_pass", "bearer_token", "api_key"):
                    existing.pop(legacy_key, None)

                # Build db section
                if any(service_data.get(k) for k in ("db_host", "db_name")):
                    existing["db"] = {
                        "host": service_data.get("db_host", ""),
                        "port": int(service_data.get("db_port", 3306)),
                        "name": service_data.get("db_name", ""),
                        "user": service_data.get("db_user", ""),
                        "password": service_data.get("db_pass", ""),
                    }
                else:
                    existing.pop("db", None)
                config["services"][i] = existing
                updated = True
                break

        if not updated:
            auth = {
                "type": service_data.get("auth_type", "password_login"),
                "login_api": service_data.get("login_api", "/auth/login"),
            }
            # Multi-role accounts
            role_accounts = service_data.get("role_accounts") or []
            for ra in role_accounts:
                if isinstance(ra, dict) and ra.get("role") and ra.get("username"):
                    auth[ra["role"]] = {
                        "username": ra["username"],
                        "password": ra.get("password", ""),
                    }
            # Legacy single admin fallback
            if not role_accounts and service_data.get("admin_user"):
                auth["admin"] = {
                    "username": service_data["admin_user"],
                    "password": service_data.get("admin_pass", ""),
                }
            if service_data.get("bearer_token"):
                auth["bearer_token"] = service_data["bearer_token"]
            if service_data.get("api_key"):
                auth["api_key"] = service_data["api_key"]
            svc = {
                "name": name, "base_url": service_data.get("base_url", ""),
                "enabled": service_data.get("enabled", True),
                "description": "", "depends_on": [], "exposes_to": [],
                "auth": auth,
            }
            if any(service_data.get(k) for k in ("db_host", "db_name")):
                svc["db"] = {
                    "host": service_data.get("db_host", ""),
                    "port": int(service_data.get("db_port", 3306)),
                    "name": service_data.get("db_name", ""),
                    "user": service_data.get("db_user", ""),
                    "password": service_data.get("db_pass", ""),
                }
            config["services"].append(svc)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")

        # Reload credential manager if running
        auth_check: dict[str, Any] = {}
        try:
            from .enterprise_credential_manager import EnterpriseCredentialManager
            mgr = EnterpriseCredentialManager(project, root)
            mgr.load_from_file(config_path)
            mgr.load_from_env()
            login_results = mgr.login_all_services()
            auth_roles = login_results.get(name) or {}
            auth_check = {
                "service": name,
                "roles": auth_roles,
                "all_ok": bool(auth_roles) and all(bool(ok) for ok in auth_roles.values()),
            }
        except Exception:
            pass

        return self._json({
            "ok": True,
            "service": name,
            "services_count": len(config["services"]),
            "auth_check": auth_check,
        })

    def _get_spectrum_status(self, project: str, root: Path) -> None:
        """Get the latest full-spectrum scan result."""
        result_file = root / "platform_outputs" / project / "spectrum" / "spectrum_result.json"
        ts_file = root / "platform_outputs" / project / "spectrum" / "spectrum_timestamp.txt"
        if not result_file.exists():
            return self._json({"ok": True, "status": "not_run", "message": "尚未运行全频谱检测"})
        try:
            result = json.loads(result_file.read_text(encoding="utf-8"))
            last_run = ts_file.read_text(encoding="utf-8").strip() if ts_file.exists() else ""
            return self._json({"ok": True, "status": "completed", "last_run": last_run, **result})
        except Exception:
            return self._json({"ok": True, "status": "error", "message": "无法读取检测结果"})

    def _handle_reanalyze(self, project: str, root: Path, actor: dict[str, str]) -> None:
        """Rebuild knowledge center with fresh data."""
        try:
            from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset
            build_enterprise_business_knowledge_asset(project, root)
            build_enterprise_pilot_overview(project, root)
            dash_html = root / "platform_outputs" / project / "enterprise_pilot_runtime" / "enterprise_pilot_center.html"
            if dash_html.exists(): dash_html.unlink()
            return self._json({"ok": True, "message": "Knowledge base reanalysis completed."})
        except Exception as e:
            return self._json({"ok": False, "error": "REANALYZE_FAILED", "message": str(e)[:300]}, 500)

    def _handle_preview(self, project: str, body_or_source: dict[str, Any] | str, root: Path) -> None:
        """Return file content for preview. Supports both POST body and GET query param."""
        source_id = ""
        if isinstance(body_or_source, dict):
            source_id = str(body_or_source.get("source_id") or "").strip()
        else:
            source_id = str(body_or_source).strip()
        if not source_id:
            return self._json({"ok": False, "error": "MISSING_SOURCE_ID"}, 400)
        try:
            from .enterprise_knowledge_center import _load_registry
            registry = _load_registry(project, root)
            for s in registry.get("sources", []):
                if s.get("source_id") == source_id:
                    stored_path = str(s.get("stored_path") or "").strip()
                    if not stored_path:
                        break
                    src_path = (root / stored_path).resolve()
                    root_resolved = root.resolve()
                    if root_resolved != src_path and root_resolved not in src_path.parents:
                        return self._json({"ok": False, "error": "INVALID_STORED_PATH"}, 400)
                    if src_path.exists():
                        text = src_path.read_text(encoding="utf-8", errors="replace")[:50000]
                        return self._json({"ok": True, "source_id": source_id, "filename": s.get("original_name",""), "content": text})
            return self._json({"ok": False, "error": "NOT_FOUND", "message": "File not found."}, 404)
        except Exception as e:
            return self._json({"ok": False, "error": "PREVIEW_FAILED", "message": str(e)[:300]}, 500)

    def _handle_settings_save(self, body: dict[str, Any]) -> None:
        """Apply LLM settings for a customer-local private service."""
        updates = {}
        for key in ["llm_base_url", "llm_model", "llm_temperature", "llm_api_key"]:
            if key in body and body[key]:
                updates[key.upper()] = str(body[key])
        if updates:
            _write_env_local(updates)
        for key, val in updates.items():
            os.environ[key] = val
        if updates:
            try:
                from .llm_reasoning import reset_client
                reset_client()
            except Exception:
                pass
        # Clear forced/cached health status before re-verification so a newly
        # verified key is reflected by /health and Settings immediately.
        for _key in ("QUALIBUG_LLM_HEALTH_STATUS", "QUALIBUG_LLM_LAST_HEALTH_STATUS", "QUALIBUG_LLM_LAST_HEALTH_LABEL", "QUALIBUG_LLM_LAST_HEALTH_ERROR"):
            os.environ.pop(_key, None)
        llm_health = self._verify_llm_connectivity() if updates else self._llm_health()
        return self._json({
            "ok": True,
            "llm_available": llm_health["available"],
            "llm_status": llm_health["status"],
            "llm_status_label": llm_health["label"],
            "llm_error": llm_health.get("error", ""),
            "message": "LLM settings were saved to .env.local for this private deployment.",
        })

    def log_message(self, fmt: str, *args: object) -> None:
        return


def run_private_pilot_service(root: Path | None = None, host: str | None = None, port: int | None = None) -> ThreadingHTTPServer:
    root = root or _root()
    host = host or os.environ.get("QUALIBUG_BIND_HOST", "127.0.0.1")
    if host in {"0.0.0.0", "::"} and os.environ.get("QUALIBUG_ALLOW_PUBLIC_BIND") != "1":
        raise ValueError("Public binding is disabled by default. Set QUALIBUG_ALLOW_PUBLIC_BIND=1 only behind a trusted reverse proxy.")
    selected_port = int(os.environ.get("QUALIBUG_PORT", "8088")) if port is None else int(port)
    server = ThreadingHTTPServer((host, selected_port), PrivatePilotHandler)
    server.qualibug_private_root = root
    return server


# ── Continuous discovery state management ─────────────────────────────

_CONTINUOUS_STATE_FILE = "continuous_discovery_state.json"

def _update_continuous_state(root: Path, project: str, scan_result: dict) -> None:
    """Track continuous discovery coverage state after each auto-scan."""
    state_dir = root / "platform_workspace" / project / "defect_discovery"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_file = state_dir / _CONTINUOUS_STATE_FILE

    state: dict[str, Any] = {}
    if state_file.exists():
        try:
            state = json.loads(state_file.read_text(encoding="utf-8")) or {}
        except Exception:
            state = {}

    total_findings = scan_result.get("total_findings", 0)
    coverage = scan_result.get("coverage", 0)
    grade = scan_result.get("grade", "C")
    total_ms = scan_result.get("total_ms", 0)

    # Track scan runs
    runs = state.get("runs", [])
    runs.append({
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "findings": total_findings,
        "coverage": coverage,
        "grade": grade,
        "duration_ms": total_ms,
    })
    # Keep last 50 runs
    runs = runs[-50:]

    # Convergence detection
    recent = runs[-5:] if len(runs) >= 5 else runs
    findings_values = [r["findings"] for r in recent]
    max_findings = max(findings_values) if findings_values else 0
    stable = len(set(findings_values[-3:])) == 1 if len(findings_values) >= 3 else False
    converged = stable and coverage >= 0.7 and max_findings > 0

    state["runs"] = runs
    state["status"] = "scanning" if not converged else "converged"
    state["converged"] = converged
    state["last_scan"] = runs[-1]["timestamp"] if runs else ""
    state["total_runs"] = len(runs)

    state_file.write_text(json.dumps(state, ensure_ascii=False, default=str), encoding="utf-8")


def _get_continuous_state(root: Path, project: str) -> dict[str, Any]:
    """Get the current continuous discovery state."""
    state_file = root / "platform_workspace" / project / "defect_discovery" / _CONTINUOUS_STATE_FILE
    if not state_file.exists():
        return {
            "status": "idle",
            "converged": False,
            "runs": [],
            "total_runs": 0,
            "message": "尚未运行过持续检测。上传文档后将自动开始。"
        }
    try:
        state = json.loads(state_file.read_text(encoding="utf-8"))
        runs = state.get("runs", [])
        last_run = runs[-1] if runs else {}
        return {
            "status": state.get("status", "idle"),
            "converged": state.get("converged", False),
            "runs": runs[-10:],
            "total_runs": state.get("total_runs", len(runs)),
            "last_scan": state.get("last_scan", ""),
            "last_findings": last_run.get("findings", 0),
            "last_coverage": last_run.get("coverage", 0),
            "message": (
                "持续检测覆盖已收敛，系统自动暂停。上传新文档后将自动恢复。"
                if state.get("converged") else
                "持续检测进行中，系统检测到新的覆盖空间。"
                if runs else
                "等待首次扫描..."
            ),
        }
    except Exception:
        return {"status": "error", "message": "无法读取持续检测状态。"}


def main() -> int:
    server = run_private_pilot_service()
    print(f"QualiBug private pilot service: http://{server.server_address[0]}:{server.server_address[1]}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    # Load .env and .env.local for LLM configuration
    try:
        from dotenv import load_dotenv
        from pathlib import Path as _P
        _proj_root = _P(__file__).resolve().parent.parent
        for _name in (".env", ".env.local"):
            _ep = _proj_root / _name
            if _ep.exists():
                load_dotenv(_ep, override=True)
    except ImportError:
        pass
    raise SystemExit(main())
