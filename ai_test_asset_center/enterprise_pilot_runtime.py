from __future__ import annotations

"""Phase60: minimal enterprise pilot runtime.

This module turns the Phase59 control plane into an operable private-cloud pilot
without introducing a second business model or a distributed workflow stack.
It owns only project runtime configuration, connector references, a persistent
local task queue, independent approvals, audit records and pilot operating
metrics. Business inference, Probe/Oracle generation, evidence, triage and
release risk remain owned by existing modules.

Safety boundaries:
- connector credentials are references (``vault:`` / ``env:``), never values;
- remote connector fetching is deliberately out of scope; exports are ingested
  through the existing, versioned enterprise knowledge center;
- production-like discovery and write execution are never executed here;
- test data setup is plan-only and requires independent approval;
- all project state is path-scoped by a sanitized project id and audit-chained.
"""

import argparse
import hashlib
import html
import json
import logging
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Any, Iterable

from .enterprise_knowledge_center import (
    build_enterprise_business_knowledge_asset,
    ingest_enterprise_knowledge_documents,
    load_enterprise_business_knowledge_asset,
)
from .enterprise_testops_control_plane import (
    _is_production,
    build_enterprise_testops_control_plane,
    build_environment_health_report,
    build_test_data_orchestration,
    load_environment_config,
)
from .version import DEFAULT_PRIVATE_PILOT_PORT

def _run_real_project_discovery_stub(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Stub for removed real_project_defect_discovery module."""
    return {"status": "not_available", "reason": "module_retired"}


_DEFAULT_DISCOVERY_RUNNER = _run_real_project_discovery_stub
_ACTIVE_DISCOVERY_RUNNER = _run_real_project_discovery_stub


def set_real_project_discovery_runner(runner: Any | None) -> None:
    """Bind the active discovery runner without replacing this module's symbol."""
    global _ACTIVE_DISCOVERY_RUNNER
    _ACTIVE_DISCOVERY_RUNNER = runner if callable(runner) else _DEFAULT_DISCOVERY_RUNNER


def get_real_project_discovery_runner() -> Any:
    return _ACTIVE_DISCOVERY_RUNNER


def run_real_project_discovery(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Stable public entry; active implementation is selected via setter."""
    return _ACTIVE_DISCOVERY_RUNNER(*args, **kwargs)
from .project_runtime_primitives import (
    PROJECT_ROOT as ROOT,
    safe_project_id as _safe_project_id,
    write_json_artifact as _project_write_json,
)
from .release_risk_dashboard import build_release_risk_dashboard
from .product_ui import _icon, callout, detail_list, empty_state, h, metric_card, product_shell, section, status_badge, table

PHASE = "phase60_enterprise_pilot_runtime"
_LOGGER = logging.getLogger(__name__)
CONFIG_MANAGERS = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}


def _scan_history_table(project: str, dashboard_path: Path) -> str:
    """Build scan history table HTML."""
    import json as _json
    from .product_ui import h, status_badge, table
    history_file = dashboard_path.parent.parent / "pipeline_reports" / "scan_history.json"
    if not history_file.exists():
        return table(["时间", "状态", "发现", "P0/P1", "对象", "LLM分析"], [], "暂无扫描记录。")
    try:
        history = _json.loads(history_file.read_text(encoding="utf-8"))
        rows = []
        for entry in reversed(history[-10:]):
            rows.append([
                h(str(entry.get("timestamp_utc", "-"))[:19]),
                status_badge(entry.get("status", "unknown")),
                str(entry.get("total_findings", 0)),
                str(entry.get("p0p1_count", 0)),
                h(str(entry.get("industry", "-"))[:20]),
                f"{entry.get('llm_powered_analyses',0)}/{max(1,entry.get('total_findings',1))}",
            ])
        return table(["时间", "状态", "发现", "P0/P1", "对象", "LLM分析"], rows, "暂无扫描记录。")
    except Exception:
        return table(["时间", "状态", "发现", "P0/P1", "对象", "LLM分析"], [], "加载扫描历史失败。")
JOB_REQUEST_ROLES = CONFIG_MANAGERS | {"qa_engineer"}
APPROVAL_ROLES = {"project_owner", "qa_lead", "security_owner", "testops_admin", "admin"}
SECURITY_APPROVER_ROLES = {"security_owner", "admin"}
TERMINAL_JOB_STATES = {"succeeded", "failed", "blocked", "cancelled"}
ACTIVE_JOB_STATES = {"waiting_approval", "queued", "running"}
JOB_TYPES = {
    "control_plane_refresh",
    "environment_health",
    "safe_discovery_plan",
    "real_project_discovery",
    "release_gate",
    "sandbox_data_setup_plan",
}
CONNECTOR_KINDS = {
    "file_export": "collaboration_document",
    "confluence_export": "confluence_document",
    "feishu_export": "feishu_document",
    "jira_export": "ticket",
    "zentao_export": "ticket",
    "gitlab_diff": "collaboration_document",
    "openapi_contract": "openapi",
    "http_api": "http_api_service",
    "database": "postgresql",
}
REF_PREFIXES = ("vault:", "env:", "secret_ref:")
SENSITIVE_VALUE_RE = re.compile(r"(?i)(password|token|api[_-]?key|secret)\s*[:=]\s*[^\s,;]+")
SECRET_REFERENCE_RE = re.compile(r"^(?:vault|env|secret_ref):[A-Za-z0-9_.:/-]{1,220}$")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 16) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:length]


def _redact(value: Any, limit: int = 2400) -> str:
    text = str(value or "")
    text = SENSITIVE_VALUE_RE.sub(lambda match: match.group(1) + "=[REDACTED]", text)
    text = re.sub(r"(?i)(authorization\s*[:=]\s*)[^\s,;]+", r"\1[REDACTED]", text)
    text = re.sub(r"(?<!\d)(1[3-9]\d{9})(?!\d)", "[MOBILE_REDACTED]", text)
    return text[:limit]


def _actor(actor: dict[str, Any] | None, allowed: set[str] | None = None) -> dict[str, str]:
    actor = actor if isinstance(actor, dict) else {}
    result = {
        "name": str(actor.get("name") or actor.get("actor") or "unknown")[:120],
        "role": str(actor.get("role") or "viewer")[:64],
    }
    if allowed is not None and result["role"] not in allowed:
        raise PermissionError("当前角色没有执行此操作的权限")
    return result


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    workspace = root / "platform_workspace" / project / "enterprise_pilot_runtime"
    output = root / "platform_outputs" / project / "enterprise_pilot_runtime"
    return {
        "workspace": workspace,
        "output": output,
        "config": workspace / "pilot_runtime_config.json",
        "connectors": workspace / "connector_registry.json",
        "jobs": workspace / "task_queue.json",
        "approvals": workspace / "execution_approvals.json",
        "audit": workspace / "runtime_audit_log.jsonl",
        "overview": output / "enterprise_pilot_overview.json",
        "scorecard": output / "pilot_success_scorecard.json",
        "manifest": output / "private_deployment_manifest.json",
        "dashboard": output / "enterprise_pilot_center.html",
    }


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + ".tmp")
    try:
        _project_write_json(temp, value)
    except Exception:
        temp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temp.replace(path)


def _read_json(path: Path, fallback: Any) -> Any:
    if not path.exists():
        return fallback
    try:
        return json.loads(path.read_text(encoding="utf-8") or "null")
    except Exception:
        return fallback


def _default_config(project: str) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "organization_id": f"org_{project}",
        "workspace_id": f"workspace_{project}",
        "project_id": project,
        "private_mode": True,
        "deployment": {
            "bind_host": "127.0.0.1",
            "port": DEFAULT_PRIVATE_PILOT_PORT,
            "auth_mode": "trusted_reverse_proxy",
            "identity_header": "X-QualiBug-Actor",
            "role_header": "X-QualiBug-Role",
            "allow_public_bind": False,
        },
        "members": [
            {"name": "project_owner", "role": "project_owner"},
            {"name": "qa_lead", "role": "qa_lead"},
            {"name": "security_owner", "role": "security_owner"},
            {"name": "qa_engineer", "role": "qa_engineer"},
        ],
        "policies": {
            "default_execution_mode": "environment_governed",
            "declared_nonproduction_execution_mode": "approved_sandbox_write",
            "production_execution_mode": "safe_read_only",
            "max_attempts": 2,
            "retention_days": 30,
            "production_write_blocked": True,
            "unknown_environment_write_blocked": True,
            "independent_approval_required": False,
            "authorization_basis": "source_bound_nonproduction_campaign",
            "allow_network_read_only": False,
        },
        "updated_at_utc": _now(),
    }


def load_pilot_runtime_config(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    current = _default_config(project)
    saved = _read_json(_paths(project, root)["config"], {})
    if isinstance(saved, dict):
        for key in ("organization_id", "workspace_id", "private_mode", "deployment", "members", "policies", "updated_at_utc"):
            if key in saved:
                current[key] = saved[key]
    current["project_id"] = project
    return current


def _validate_deployment(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value if isinstance(value, dict) else {})
    host = str(result.get("bind_host") or "127.0.0.1")
    if host in {"0.0.0.0", "::"} and not bool(result.get("allow_public_bind")):
        raise ValueError("私有化运行时默认禁止公开绑定；如需反向代理，请保持 allow_public_bind=false")
    result["bind_host"] = host
    result["port"] = max(1, min(65535, int(result.get("port") or DEFAULT_PRIVATE_PILOT_PORT)))
    result["auth_mode"] = str(result.get("auth_mode") or "trusted_reverse_proxy")
    result["identity_header"] = str(result.get("identity_header") or "X-QualiBug-Actor")
    result["role_header"] = str(result.get("role_header") or "X-QualiBug-Role")
    result["allow_public_bind"] = bool(result.get("allow_public_bind", False))
    return result


def save_pilot_runtime_config(project_id: str, patch: dict[str, Any], root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor, CONFIG_MANAGERS)
    config = load_pilot_runtime_config(project, root)
    for key in ("organization_id", "workspace_id", "private_mode", "members", "policies"):
        if key in patch:
            config[key] = patch[key]
    if "deployment" in patch:
        config["deployment"] = _validate_deployment(patch["deployment"])
    config["updated_at_utc"] = _now()
    _write_json(_paths(project, root)["config"], config)
    _append_audit(project, root, "runtime_config_saved", clean_actor, {"workspace_id": config["workspace_id"], "private_mode": config["private_mode"]})
    return config


def _default_registry(project: str) -> dict[str, Any]:
    return {
        "phase": PHASE,
        "project_id": project,
        "connectors": [],
        "test_profile": {},
        "updated_at_utc": _now(),
    }


def load_connector_registry(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    saved = _read_json(_paths(project, root)["connectors"], {})
    default = _default_registry(project)
    if isinstance(saved, dict):
        if isinstance(saved.get("connectors"), list):
            default["connectors"] = [row for row in saved["connectors"] if isinstance(row, dict)]
        if isinstance(saved.get("test_profile"), dict):
            default["test_profile"] = saved["test_profile"]
        default["updated_at_utc"] = str(saved.get("updated_at_utc") or default["updated_at_utc"])
    return default


def _has_login_material(row: Any) -> bool:
    if not isinstance(row, dict):
        return False
    identity = str(
        row.get("email")
        or row.get("username")
        or row.get("account")
        or row.get("mobile")
        or row.get("phone")
        or ""
    ).strip()
    password = str(row.get("password") or row.get("pass") or "").strip()
    return bool(identity and password)


def _bind_preflight_test_credentials(
    project: str,
    root: Path,
    diagnostics_config: dict[str, Any],
) -> str:
    """Bind preflight to the same project credential catalog as runtime."""

    configured = ordered_test_credentials({"test_profile": diagnostics_config})
    if any(_has_login_material(row) for row in configured):
        return "connector_registry.test_profile"

    loaded = load_project_test_credentials(project, root)
    usable = [dict(row) for row in loaded if _has_login_material(row)]
    if not usable:
        return "none"

    diagnostics_config["test_credentials"] = usable
    _LOGGER.info(
        "preflight_test_credentials_bound project=%s source=%s count=%d",
        project,
        "project_test_credential_catalog",
        len(usable),
    )
    return "project_test_credential_catalog"


def ordered_test_credentials(config: dict[str, Any] | None) -> list[dict[str, Any]]:
    source = config if isinstance(config, dict) else {}
    profile = source.get("test_profile") if isinstance(source.get("test_profile"), dict) else source
    if not isinstance(profile, dict):
        return []
    credentials = profile.get("test_credentials")
    preferred_order = profile.get("test_credential_order")
    order_lookup = {
        str(name).strip(): index
        for index, name in enumerate(preferred_order)
        if str(name).strip()
    } if isinstance(preferred_order, list) else {}

    rows: list[tuple[tuple[int, int, int, int, str], dict[str, Any]]] = []
    if isinstance(credentials, dict):
        iterator = list(credentials.items())
    elif isinstance(credentials, list):
        iterator = [
            (
                str(item.get("profile") or item.get("credential_id") or item.get("name") or f"credential_{index}"),
                item,
            )
            for index, item in enumerate(credentials)
            if isinstance(item, dict)
        ]
    else:
        iterator = []

    for index, (name, value) in enumerate(iterator):
        if not isinstance(value, dict):
            continue
        profile_name = str(name or value.get("profile") or value.get("credential_id") or f"credential_{index}").strip()
        explicit_default = any(value.get(flag) is True for flag in ("default", "primary", "is_default", "is_primary"))
        priority_raw = value.get("priority", value.get("order", value.get("rank")))
        try:
            numeric_priority = int(priority_raw)
        except (TypeError, ValueError):
            numeric_priority = 10_000
        explicit_order = order_lookup.get(profile_name, 10_000)
        sort_key = (
            0 if explicit_default else 1,
            explicit_order,
            numeric_priority,
            index,
            profile_name.lower(),
        )
        rows.append((sort_key, {"profile": profile_name, **dict(value)}))

    rows.sort(key=lambda item: item[0])
    return [item for _, item in rows]


def load_project_test_credentials(
    project_id: str,
    root: Path | None = None,
) -> list[dict[str, Any]]:
    """Load the ordered credential catalog from every supported project source."""
    root = Path(root or ROOT)
    project = _safe_project_id(project_id)
    registry = load_connector_registry(project, root)
    merged = ordered_test_credentials(registry)
    seen: set[str] = set()

    def identity(row: dict[str, Any]) -> str:
        return str(
            row.get("email")
            or row.get("username")
            or row.get("account")
            or row.get("mobile")
            or row.get("phone")
            or row.get("profile")
            or ""
        ).strip().lower()

    ordered: list[dict[str, Any]] = []
    for row in merged:
        key = identity(row)
        if key and key not in seen:
            seen.add(key)
            ordered.append(dict(row))

    paths = (
        root / "platform_inputs" / project / "test_accounts.json",
        root / "platform_workspace" / project / "input" / "test_accounts.json",
        root / "projects" / project / "input" / "test_accounts.json",
    )
    for path in paths:
        if not path.is_file():
            continue
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise RuntimeError(
                f"test_account_catalog_invalid:{path}:{type(exc).__name__}:{exc}"
            ) from exc
        if isinstance(payload, dict):
            # A container key must be unwrapped first. {"accounts": [...]} is the
            # shape the ingest API and the frontend both write, and the dict-of-dicts
            # comprehension below silently skips it because the value is a list --
            # yielding zero credentials for a file that plainly holds eight accounts.
            # load_actor_tokens already unwraps these same three keys; this path did
            # not, so the two loaders disagreed about the same file.
            container = (
                payload.get("accounts")
                or payload.get("actors")
                or payload.get("users")
            )
            if isinstance(container, list):
                rows = [
                    {
                        "profile": str(
                            value.get("profile")
                            or value.get("name")
                            or value.get("role")
                            or f"credential_{index}"
                        ),
                        **dict(value),
                    }
                    for index, value in enumerate(container)
                    if isinstance(value, dict)
                ]
            else:
                rows = [
                    {"profile": str(name), **dict(value)}
                    for name, value in payload.items()
                    if isinstance(value, dict)
                ]
        elif isinstance(payload, list):
            rows = [
                {
                    "profile": str(
                        value.get("profile")
                        or value.get("name")
                        or value.get("role")
                        or f"credential_{index}"
                    ),
                    **dict(value),
                }
                for index, value in enumerate(payload)
                if isinstance(value, dict)
            ]
        else:
            raise RuntimeError(f"test_account_catalog_invalid:{path}:expected_object_or_list")
        for row in rows:
            key = identity(row)
            if key and key not in seen:
                seen.add(key)
                ordered.append(row)

    # The JSON catalog commonly holds bearer tokens rather than passwords, and a
    # token is a snapshot that expires. When no row carries a password, fall back
    # to the operator's own TEST_ACCOUNTS.md so a login can actually be performed.
    # Without this, a project whose accounts are documented and working reads as
    # "no usable credentials" the moment its stored tokens go stale.
    if not any(row.get("password") or row.get("pass") for row in ordered):
        try:
            from .experiment_runtime_support import _parse_test_accounts_md

            for index, account in enumerate(_parse_test_accounts_md(root, project) or []):
                if not isinstance(account, dict):
                    continue
                if not (account.get("password") and (account.get("email") or account.get("username"))):
                    continue
                row = {
                    "profile": str(
                        account.get("profile")
                        or account.get("role")
                        or account.get("email")
                        or f"md_credential_{index}"
                    ),
                    **dict(account),
                }
                key = identity(row)
                if not key:
                    continue
                if key in seen:
                    # Merge, do not skip. The JSON row for this same identity is the
                    # one that lacks a password; dropping the markdown row as a
                    # duplicate would discard the only usable secret and leave the
                    # catalog exactly as unusable as before.
                    for existing in ordered:
                        if identity(existing) != key:
                            continue
                        if not (existing.get("password") or existing.get("pass")):
                            existing["password"] = account.get("password")
                            existing.setdefault("email", account.get("email"))
                            existing["credential_origin"] = "test_accounts_md_merge"
                        break
                    continue
                seen.add(key)
                ordered.append(row)
        except Exception as exc:
            # Never let a malformed markdown table break credential loading for a
            # project whose JSON catalog was fine.
            print(
                f"  [WARN] {project}: TEST_ACCOUNTS.md credential fallback skipped "
                f"({type(exc).__name__}: {exc})",
                flush=True,
            )
    return ordered


def _validate_ref(value: Any, name: str, allow_blank: bool = True) -> str:
    text = str(value or "").strip()
    if not text and allow_blank:
        return ""
    if not text.startswith(REF_PREFIXES):
        raise ValueError(f"{name} 只能保存 vault:/env:/secret_ref: 引用，不能保存明文凭证")
    if (
        len(text) > 300
        or SENSITIVE_VALUE_RE.search(text)
        or "://" in text
        or "@" in text
        or not SECRET_REFERENCE_RE.match(text)
    ):
        raise ValueError(f"{name} 不是有效的凭证引用")
    return text


def _validate_endpoint(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if text.startswith("https://") or text.startswith("http://127.0.0.1") or text.startswith("http://localhost"):
        return text[:500]
    raise ValueError("连接器 endpoint_ref 仅允许 HTTPS，或本地演示地址")


def register_enterprise_connector(project_id: str, payload: dict[str, Any], root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor, CONFIG_MANAGERS)
    kind = str(payload.get("kind") or "").strip()
    if kind not in CONNECTOR_KINDS:
        raise ValueError("不支持的企业连接器类型")
    registry = load_connector_registry(project, root)
    connector_id = str(payload.get("connector_id") or f"conn_{_hash({'project': project, 'kind': kind, 'name': payload.get('display_name')}, 18)}")
    connector_id = re.sub(r"[^a-zA-Z0-9_.-]", "_", connector_id)[:96]
    if not connector_id:
        raise ValueError("connector_id 不合法")
    previous = next((row for row in registry["connectors"] if row.get("connector_id") == connector_id), None)
    system_name = str(payload.get("system_name") or (previous.get("system_name") if isinstance(previous, dict) else "") or "").strip()[:80]
    module_name = str(payload.get("module_name") or (previous.get("module_name") if isinstance(previous, dict) else "") or "").strip()[:80]
    record = {
        "connector_id": connector_id,
        "kind": kind,
        "display_name": str(payload.get("display_name") or kind)[:160],
        "system_name": system_name,
        "module_name": module_name,
        "enabled": bool(payload.get("enabled", True)),
        "read_only": True,
        "sync_mode": "manual_export",
        "source_type": CONNECTOR_KINDS[kind],
        "credential_ref": _validate_ref(payload.get("credential_ref"), "credential_ref"),
        "endpoint_ref": _validate_endpoint(payload.get("endpoint_ref")),
        "external_ref": str(payload.get("external_ref") or "")[:500],
        "project_scope": project,
        "last_sync_at_utc": "",
        "last_sync_status": "not_synced",
        "created_at_utc": _now(),
        "created_by": clean_actor,
    }
    if previous:
        previous.update(record)
        outcome = "updated"
    else:
        registry["connectors"].append(record)
        outcome = "created"
    registry["updated_at_utc"] = _now()
    _write_json(_paths(project, root)["connectors"], registry)
    _append_audit(project, root, "connector_registered", clean_actor, {"connector_id": connector_id, "kind": kind, "outcome": outcome})
    return {"ok": True, "outcome": outcome, "connector": record}


def sync_connector_export(project_id: str, connector_id: str, documents: Iterable[dict[str, Any]], root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    """Ingest manually exported connector content through Phase58.

    Raw export content is sent directly to the knowledge center and is not saved
    in this runtime's registry/audit payload. This keeps the runtime thin and
    avoids another document store.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor, CONFIG_MANAGERS)
    registry = load_connector_registry(project, root)
    connector = next((row for row in registry["connectors"] if row.get("connector_id") == connector_id), None)
    if not connector or connector.get("project_scope") != project:
        raise KeyError("未找到当前项目连接器")
    if not connector.get("enabled"):
        raise ValueError("连接器已禁用")
    prepared: list[dict[str, Any]] = []
    for index, document in enumerate(documents or []):
        if not isinstance(document, dict):
            continue
        row = dict(document)
        row.setdefault("filename", f"{connector_id}_export_{index + 1}.txt")
        row.setdefault("source_type", connector["source_type"])
        row.setdefault("external_ref", connector.get("external_ref") or connector_id)
        row.setdefault("tags", ["connector", connector["kind"], connector_id])
        prepared.append(row)
    if not prepared:
        raise ValueError("连接器同步需要至少一份导出资料")
    ingestion = ingest_enterprise_knowledge_documents(project, prepared, root=root, actor=clean_actor)
    asset = build_enterprise_business_knowledge_asset(project, root)
    connector["last_sync_at_utc"] = _now()
    connector["last_sync_status"] = "succeeded" if not ingestion.get("errors") else "partial"
    connector["last_sync_summary"] = {"created": len(ingestion.get("created") or []), "duplicates": len(ingestion.get("duplicates") or []), "errors": len(ingestion.get("errors") or [])}
    registry["updated_at_utc"] = _now()
    _write_json(_paths(project, root)["connectors"], registry)
    _append_audit(project, root, "connector_export_synced", clean_actor, {"connector_id": connector_id, **connector["last_sync_summary"]})
    return {"ok": not ingestion.get("errors"), "ingestion": ingestion, "knowledge_summary": asset.get("summary", {}), "connector": connector}


def _load_jobs(project: str, root: Path) -> dict[str, Any]:
    saved = _read_json(_paths(project, root)["jobs"], {})
    return {"phase": PHASE, "project_id": project, "jobs": [row for row in saved.get("jobs", []) if isinstance(row, dict)] if isinstance(saved, dict) else [], "updated_at_utc": str(saved.get("updated_at_utc") or _now()) if isinstance(saved, dict) else _now()}


def _save_jobs(project: str, root: Path, jobs: dict[str, Any]) -> None:
    jobs["updated_at_utc"] = _now()
    _write_json(_paths(project, root)["jobs"], jobs)


def _load_approvals(project: str, root: Path) -> dict[str, Any]:
    saved = _read_json(_paths(project, root)["approvals"], {})
    return {"phase": PHASE, "project_id": project, "approvals": [row for row in saved.get("approvals", []) if isinstance(row, dict)] if isinstance(saved, dict) else [], "updated_at_utc": str(saved.get("updated_at_utc") or _now()) if isinstance(saved, dict) else _now()}


def _save_approvals(project: str, root: Path, values: dict[str, Any]) -> None:
    values["updated_at_utc"] = _now()
    _write_json(_paths(project, root)["approvals"], values)


def _append_audit(project: str, root: Path, event: str, actor: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    path = _paths(project, root)["audit"]
    path.parent.mkdir(parents=True, exist_ok=True)
    previous = ""
    if path.exists():
        try:
            lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
            if lines:
                previous = str(json.loads(lines[-1]).get("event_hash") or "")
        except Exception:
            previous = ""
    entry = {"at_utc": _now(), "event": event, "actor": actor, "payload": {"summary": _redact(json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str), 6000)}, "previous_event_hash": previous}
    entry["event_hash"] = _hash(entry, 64)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return entry


def _verify_audit(project: str, root: Path) -> dict[str, Any]:
    path = _paths(project, root)["audit"]
    if not path.exists():
        return {"valid": True, "event_count": 0, "reason": "empty"}
    previous = ""
    count = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            expected = str(row.pop("event_hash", ""))
            if row.get("previous_event_hash") != previous or _hash(row, 64) != expected:
                return {"valid": False, "event_count": count, "reason": "hash_chain_invalid"}
            previous = expected
            count += 1
    except Exception as exc:
        return {"valid": False, "event_count": count, "reason": str(exc)[:200]}
    return {"valid": True, "event_count": count, "tail_hash": previous}


def _environment(project: str, root: Path, requested: str | None) -> dict[str, Any]:
    config = load_environment_config(project, root)
    target = str(requested or config.get("target_environment") or "test")
    environments = [row for row in config.get("environments") or [] if isinstance(row, dict)]
    result = next((row for row in environments if str(row.get("name")) == target), None)
    if result is None:
        raise ValueError("目标测试环境不存在")
    return result


def _needs_approval(job_type: str, environment: dict[str, Any], execution_mode: str) -> tuple[bool, str]:
    protected = _is_production(environment)
    if job_type == "sandbox_data_setup_plan":
        return True, "隔离测试数据准备计划需要独立审批"
    if protected and job_type != "environment_health":
        return True, "生产类环境的任务需要安全负责人审批"
    if execution_mode != "safe_read_only":
        return True, "非只读执行模式需要独立审批"
    return False, ""


def enqueue_pilot_task(project_id: str, job_type: str, payload: dict[str, Any] | None = None, root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor, JOB_REQUEST_ROLES)
    if job_type not in JOB_TYPES:
        raise ValueError("不支持的试点任务类型")
    payload = dict(payload or {})
    environment = _environment(project, root, payload.get("target_environment"))
    execution_mode = str(payload.get("execution_mode") or "safe_read_only")
    if execution_mode not in {"safe_read_only", "sandbox_plan_only"}:
        raise ValueError("当前私有试点运行时只支持 safe_read_only 或 sandbox_plan_only")
    if _is_production(environment) and execution_mode != "safe_read_only":
        raise PermissionError("生产类环境禁止任何写入或造数执行")
    dedupe_payload = {"project": project, "job_type": job_type, "environment": environment.get("name"), "execution_mode": execution_mode, "connector_id": payload.get("connector_id"), "scope": payload.get("scope")}
    idempotency_key = str(payload.get("idempotency_key") or _hash(dedupe_payload, 32))
    queue = _load_jobs(project, root)
    existing = next((row for row in queue["jobs"] if row.get("idempotency_key") == idempotency_key and row.get("status") in ACTIVE_JOB_STATES | {"succeeded"}), None)
    if existing:
        return {"ok": True, "deduplicated": True, "job": existing}
    needs_approval, reason = _needs_approval(job_type, environment, execution_mode)
    job_id = f"job_{_hash({'project': project, 'type': job_type, 'key': idempotency_key, 'at': _now()}, 20)}"
    job = {
        "job_id": job_id,
        "project_id": project,
        "organization_id": load_pilot_runtime_config(project, root).get("organization_id"),
        "job_type": job_type,
        "target_environment": environment.get("name"),
        "execution_mode": execution_mode,
        "status": "waiting_approval" if needs_approval else "queued",
        "approval_required": needs_approval,
        "approval_reason": reason,
        "requested_by": clean_actor,
        "requested_at_utc": _now(),
        "idempotency_key": idempotency_key,
        "attempts": 0,
        "max_attempts": int(load_pilot_runtime_config(project, root).get("policies", {}).get("max_attempts") or 2),
        "payload": {"scope": str(payload.get("scope") or "")[:240], "target_environment": environment.get("name"), "execution_mode": execution_mode},
        "result": None,
        "error": "",
    }
    queue["jobs"].append(job)
    _save_jobs(project, root, queue)
    if needs_approval:
        approvals = _load_approvals(project, root)
        approval = {"approval_id": f"approval_{_hash({'job': job_id}, 18)}", "job_id": job_id, "project_id": project, "status": "pending", "requested_by": clean_actor, "requested_at_utc": _now(), "required_reviewer_role": "security_owner" if _is_production(environment) else "qa_lead_or_security_owner", "reason": reason}
        approvals["approvals"].append(approval)
        _save_approvals(project, root, approvals)
        job["approval_id"] = approval["approval_id"]
        _save_jobs(project, root, queue)
    _append_audit(project, root, "pilot_task_requested", clean_actor, {"job_id": job_id, "type": job_type, "environment": environment.get("name"), "approval_required": needs_approval})
    return {"ok": True, "deduplicated": False, "job": job}


def approve_pilot_task(project_id: str, job_id: str, decision: str, root: Path | None = None, actor: dict[str, Any] | None = None, note: str = "") -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor, APPROVAL_ROLES)
    if decision not in {"approved", "rejected"}:
        raise ValueError("审批决定仅支持 approved 或 rejected")
    queue = _load_jobs(project, root)
    job = next((row for row in queue["jobs"] if row.get("job_id") == job_id), None)
    if not job:
        raise KeyError("未找到任务")
    if not job.get("approval_required"):
        raise ValueError("该任务不需要审批")
    if job.get("requested_by", {}).get("name") == clean_actor["name"]:
        raise PermissionError("提交人与审批人必须分离")
    env = _environment(project, root, str(job.get("target_environment")))
    if _is_production(env) and clean_actor["role"] not in SECURITY_APPROVER_ROLES:
        raise PermissionError("生产类环境任务必须由 security_owner 或 admin 审批")
    approvals = _load_approvals(project, root)
    approval = next((row for row in approvals["approvals"] if row.get("job_id") == job_id and row.get("status") == "pending"), None)
    if not approval:
        raise ValueError("未找到待审批记录")
    approval.update({"status": decision, "reviewed_by": clean_actor, "reviewed_at_utc": _now(), "note": _redact(note, 800)})
    job["status"] = "queued" if decision == "approved" else "cancelled"
    job["approval_decision"] = decision
    _save_approvals(project, root, approvals)
    _save_jobs(project, root, queue)
    _append_audit(project, root, "pilot_task_approval", clean_actor, {"job_id": job_id, "decision": decision, "environment": env.get("name")})
    return {"ok": True, "job": job, "approval": approval}


def _run_job(job: dict[str, Any], project: str, root: Path) -> dict[str, Any]:
    target = str(job.get("target_environment") or "test")
    environment = _environment(project, root, target)
    if _is_production(environment) and job.get("job_type") not in {"environment_health"}:
        return {"status": "blocked", "reason": "生产类环境仅允许健康检查；禁止直接发现、造数和写入执行", "network_requests": 0}
    task = str(job.get("job_type"))
    if task == "control_plane_refresh":
        control = build_enterprise_testops_control_plane(project, root, {"target_environment": target})
        return {"status": "succeeded", "control_phase": control.get("phase"), "probe_count": len(control.get("permission_probes") or []), "network_requests": 0}
    if task == "environment_health":
        report = build_environment_health_report(project, root, {"target_environment": target, "perform_network_check": False})
        return {"status": "succeeded", "target_testable": report.get("target_testable"), "health_status": report.get("target_health_status"), "network_requests": 0}
    if task == "safe_discovery_plan":
        control = build_enterprise_testops_control_plane(project, root, {"target_environment": target})
        probes = control.get("permission_probes") or []
        asset = load_enterprise_business_knowledge_asset(project, root) or build_enterprise_business_knowledge_asset(project, root)
        return {"status": "succeeded", "run_mode": "safe_plan_only", "network_requests": 0, "candidate_probe_count": len(probes), "oracle_count": len(asset.get("oracle_library") or []), "note": "执行计划交给现有受控发现引擎；运行时不会隐式发起写请求。"}
    if task == "real_project_discovery":
        result = run_real_project_discovery(project, root)
        probes_payload = result.get("probes") or {}
        probes = probes_payload.get("items") if isinstance(probes_payload, dict) else probes_payload
        return {
            "status": "succeeded",
            "run_mode": "real_project_discovery",
            "network_requests": int(result.get("network_requests") or 0),
            "issue_count": len(result.get("issues") or []),
            "probe_count": len(probes or []),
            "business_outcome_finding_count": result.get("business_outcome_finding_count", 0),
            "business_reconciliation_finding_count": result.get("business_reconciliation_finding_count", 0),
            "business_invariant_finding_count": result.get("business_invariant_finding_count", 0),
            "multi_source_reasoning_finding_count": result.get("multi_source_reasoning_finding_count", 0),
            "business_lifecycle_finding_count": result.get("business_lifecycle_finding_count", 0),
            "consistency_isolation_finding_count": result.get("consistency_isolation_finding_count", 0),
            "output_dir": str(result.get("output_dir") or ""),
        }
    if task == "release_gate":
        dashboard = build_release_risk_dashboard(project, root)
        return {"status": "succeeded", "release_decision": (dashboard.get("release_risk") or {}).get("decision"), "network_requests": 0}
    if task == "sandbox_data_setup_plan":
        plan = build_test_data_orchestration(project, root, {"target_environment": target})
        return {"status": "succeeded", "run_mode": "sandbox_plan_only", "network_requests": 0, "automatic_preparation_ratio": plan.get("automatic_preparation_ratio"), "step_count": len(plan.get("data_preparation_steps") or []), "note": "仅生成隔离测试数据准备计划，未执行 API/数据库写入。"}
    return {"status": "failed", "reason": "unsupported_job_type", "network_requests": 0}


def run_next_pilot_task(project_id: str, root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    clean_actor = _actor(actor, JOB_REQUEST_ROLES)
    queue = _load_jobs(project, root)
    job = next((row for row in queue["jobs"] if row.get("status") == "queued"), None)
    if not job:
        return {"ok": True, "idle": True, "message": "没有可执行任务"}
    job["status"] = "running"
    job["claimed_by"] = clean_actor
    job["started_at_utc"] = _now()
    job["attempts"] = int(job.get("attempts") or 0) + 1
    _save_jobs(project, root, queue)
    _append_audit(project, root, "pilot_task_started", clean_actor, {"job_id": job.get("job_id"), "type": job.get("job_type")})
    try:
        result = _run_job(job, project, root)
        job["result"] = result
        job["status"] = str(result.get("status") or "failed")
        job["finished_at_utc"] = _now()
        job["error"] = str(result.get("reason") or "")[:600]
    except Exception as exc:
        job["error"] = _redact(str(exc), 600)
        job["status"] = "failed" if int(job.get("attempts") or 0) >= int(job.get("max_attempts") or 1) else "queued"
        job["finished_at_utc"] = _now()
    _save_jobs(project, root, queue)
    _append_audit(project, root, "pilot_task_finished", clean_actor, {"job_id": job.get("job_id"), "status": job.get("status"), "attempts": job.get("attempts")})
    return {"ok": job.get("status") == "succeeded", "idle": False, "job": job}


def list_pilot_tasks(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    queue = _load_jobs(project, root)
    summary = {state: sum(1 for row in queue["jobs"] if row.get("status") == state) for state in sorted({str(row.get("status")) for row in queue["jobs"]})}
    return {"phase": PHASE, "project_id": project, "summary": summary, "jobs": queue["jobs"]}


def _scorecard(project: str, root: Path, overview: dict[str, Any]) -> dict[str, Any]:
    jobs = overview.get("tasks", {}).get("jobs") or []
    connectors = overview.get("connectors", {}).get("connectors") or []
    control = overview.get("control_plane") or {}
    knowledge = overview.get("knowledge_asset") or {}
    succeeded = sum(1 for item in jobs if item.get("status") == "succeeded")
    terminal = sum(1 for item in jobs if item.get("status") in TERMINAL_JOB_STATES)
    asset_ready = bool(knowledge.get("summary", {}).get("knowledge_ready", True) if isinstance(knowledge.get("summary"), dict) else knowledge)
    connector_ready = sum(1 for item in connectors if item.get("last_sync_status") == "succeeded")
    health_ready = bool((control.get("environment_health") or {}).get("target_testable"))
    score = round(min(100.0, 20 * int(asset_ready) + 15 * min(1, connector_ready) + 20 * int(health_ready) + 25 * (succeeded / max(1, terminal)) + 20 * int(overview.get("audit", {}).get("valid", False))), 1)
    return {"phase": PHASE, "project_id": project, "generated_at_utc": _now(), "pilot_readiness_score": score, "metrics": {"knowledge_asset_ready": asset_ready, "synced_connector_count": connector_ready, "environment_ready": health_ready, "task_success_rate": round(succeeded / max(1, terminal), 3), "audit_chain_valid": bool(overview.get("audit", {}).get("valid"))}, "claim_boundary": "试点评分衡量接入、治理与受控运行准备度，不代表生产缺陷发现率或零缺陷保证。"}


def build_enterprise_pilot_overview(project_id: str = "real_project_demo", root: Path | None = None, rebuild_control: bool = False) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    config = load_pilot_runtime_config(project, root)
    knowledge = load_enterprise_business_knowledge_asset(project, root)
    if knowledge is None:
        try:
            knowledge = build_enterprise_business_knowledge_asset(project, root)
        except Exception as exc:
            knowledge = {"summary": {"knowledge_ready": False, "error": _redact(str(exc), 300)}}
    control = None
    if rebuild_control:
        try:
            control = build_enterprise_testops_control_plane(project, root)
        except Exception as exc:
            control = {"phase": "unavailable", "error": _redact(str(exc), 300)}
    if control is None:
        from .enterprise_testops_control_plane import load_enterprise_testops_control_plane
        control = load_enterprise_testops_control_plane(project, root) or {}
    overview = {
        "phase": PHASE,
        "project_id": project,
        "organization_id": config.get("organization_id"),
        "workspace_id": config.get("workspace_id"),
        "generated_at_utc": _now(),
        "runtime_config": {"private_mode": config.get("private_mode"), "deployment": config.get("deployment"), "member_count": len(config.get("members") or []), "policies": config.get("policies")},
        "connectors": load_connector_registry(project, root),
        "tasks": list_pilot_tasks(project, root),
        "approvals": _load_approvals(project, root),
        "knowledge_asset": knowledge,
        "control_plane": control,
        "audit": _verify_audit(project, root),
        "integration_contract": {"reuses": ["enterprise_knowledge_center", "enterprise_testops_control_plane", "release_risk_dashboard", "real_project_defect_discovery"], "runtime_owns": ["project_scope", "connector_registry", "task_queue", "approval_registry", "private_service", "pilot_scorecard"]},
    }
    overview["pilot_scorecard"] = _scorecard(project, root, overview)
    paths = _paths(project, root)
    _write_json(paths["overview"], overview)
    _write_json(paths["scorecard"], overview["pilot_scorecard"])
    _write_json(paths["manifest"], {"phase": PHASE, "project_id": project, "private_deployment": config.get("deployment"), "storage_paths": {"workspace": str(paths["workspace"].relative_to(root)).replace("\\", "/"), "outputs": str(paths["output"].relative_to(root)).replace("\\", "/")}, "security_boundary": ["localhost_default", "trusted_reverse_proxy_identity", "credential_references_only", "production_write_blocked", "audit_hash_chain"]})
    _render_dashboard(overview, paths["dashboard"])
    return overview


def _render_dashboard(overview: dict[str, Any], path: Path) -> None:
    """Render the shared enterprise product shell for the pilot runtime.

    The runtime still owns no business model.  This view only turns the current
    project-scoped overview into a usable operating surface and leaves all
    mutations in the existing audited runtime APIs.
    """
    score = overview.get("pilot_scorecard") or {}
    metrics = score.get("metrics") or {}
    tasks = list((overview.get("tasks") or {}).get("jobs") or [])
    connectors = list((overview.get("connectors") or {}).get("connectors") or [])
    approvals = list((overview.get("approvals") or {}).get("approvals") or [])
    knowledge = overview.get("knowledge_asset") or {}
    knowledge_summary = knowledge.get("summary") if isinstance(knowledge, dict) else {}
    knowledge_summary = knowledge_summary if isinstance(knowledge_summary, dict) else {}
    control = overview.get("control_plane") if isinstance(overview.get("control_plane"), dict) else {}
    environment = control.get("environment_health") if isinstance(control, dict) else {}
    environment = environment if isinstance(environment, dict) else {}
    defect_quality = control.get("defect_quality_report") if isinstance(control, dict) else {}
    defect_quality = defect_quality if isinstance(defect_quality, dict) else {}
    task_summary = (overview.get("tasks") or {}).get("summary") or {}
    queued = int(task_summary.get("queued") or 0) + int(task_summary.get("waiting_approval") or 0)
    succeeded = int(task_summary.get("succeeded") or 0)
    readiness = score.get("pilot_readiness_score", 0)
    readiness_value = max(0.0, min(100.0, float(readiness or 0)))
    task_success_rate = metrics.get("task_success_rate", 0)
    env_testable = bool(environment.get("target_testable"))
    high_confidence = ((defect_quality.get("summary") or {}).get("high_confidence_count") or 0)
    source_count = knowledge_summary.get("active_source_count", 0)
    project = str(overview.get("project_id") or "real_project_demo")
    cfg = overview.get("runtime_config", {})
    # Get base_url from environment config (not runtime_config)
    env_base_url = ""
    env_timeout = "10"
    # Read env config JSON directly (avoids import issues in pilot runtime)
    # Derive root from path: path = root / platform_outputs / project / enterprise_pilot_runtime / enterprise_pilot_center.html
    _root_from_path = path.parent.parent.parent.parent if "platform_outputs" in str(path) else Path(".")
    env_config_path = _root_from_path / "platform_workspace" / project / "enterprise_testops_control_plane" / "environment_config.json"
    if env_config_path.exists():
        try:
            import json as _json
            env_data = _json.loads(env_config_path.read_text(encoding="utf-8"))
            target_name = str(env_data.get("target_environment", "test"))
            for e in env_data.get("environments", []):
                if isinstance(e, dict) and e.get("name") == target_name:
                    env_base_url = str(e.get("base_url") or "")
                    env_timeout = str(e.get("request_timeout_seconds") or "10")
                    break
        except Exception:
            pass

    # Try to load pipeline report for live findings count
    pipeline_findings_count = 0
    pipeline_p0p1_count = 0
    try:
        import json as _json
        pipeline_report_path = path.parent.parent / "pipeline_reports" / "latest_pipeline_report.json"
        if pipeline_report_path.exists():
            pr = _json.loads(pipeline_report_path.read_text(encoding="utf-8"))
            s2 = pr.get("stage2_discovery", {})
            all_f = s2.get("findings", [])
            pipeline_findings_count = len(all_f)
            pipeline_p0p1_count = sum(1 for f in all_f if str(f.get("severity","")) in ("P0","P1"))
    except Exception:
        pass

    cards = "".join([
        metric_card("试点准备度", f"{readiness_value:.0f}/100", "资料·环境·审批·审计综合评分", "success" if readiness_value >= 75 else "warning", "overview"),
        metric_card("企业资料", source_count, f"{knowledge_summary.get('rule_count', 0)} 条规则 · {knowledge_summary.get('oracle_count', 0)} 个 Oracle", "default", "knowledge"),
        metric_card("待处理任务", queued, "含待审批与排队", "warning" if queued else "default", "runtime"),
        metric_card("高置信Bug", pipeline_findings_count if pipeline_findings_count else high_confidence, f"P0/P1: {pipeline_p0p1_count}" if pipeline_findings_count else "环境问题已在去噪层分流", "danger" if pipeline_findings_count else ("danger" if high_confidence else "success"), "risk"),
    ])
    # Add action buttons if scan was run
    findings_link = ""
    if pipeline_findings_count > 0:
        findings_link = (
            f"<div style='display:flex;gap:8px;margin:0 0 18px;flex-wrap:wrap'>"
            f"<a class='btn btn-secondary' href='/findings?project={project}'><i>{_icon('bug')}</i>查看 {pipeline_findings_count} 个 Bug 详情</a>"
            f"<a class='btn btn-outline' href='/api/report/html?project={project}' target='_blank'><i>{_icon('file')}</i>下载 HTML 报告</a>"
            f"</div>"
        )
    # Readiness banner
    readiness_banner = ""
    if readiness_value < 60:
        readiness_banner = (
            f"<div class='callout callout-warning' style='margin-bottom:18px'>"
            f"<i>{_icon('shield')}</i><div><strong>项目准备度: {readiness_value:.0f}/100</strong>"
            f"<p>建议完成 <a href='/onboard?project={project}' style='color:var(--primary)'>项目接入向导</a> 后再启动扫描。</p></div></div>"
        )

    task_rows = []
    for job in tasks[-12:][::-1]:
        task_rows.append([
            h(job.get("job_type") or "-").replace("_", " "),
            h(job.get("target_environment") or "-"),
            status_badge(job.get("status") or "unknown"),
            status_badge(job.get("execution_mode") or "safe_read_only"),
            h(job.get("created_at_utc") or "-"),
        ])
    connector_rows = []
    for item in connectors[:12]:
        connector_rows.append([
            h(item.get("display_name") or item.get("connector_id") or "-"),
            h(item.get("kind") or "-"),
            status_badge(item.get("last_sync_status") or "未同步"),
            f"<code>{h(item.get('credential_ref') or '无凭证引用')}</code>",
        ])
    approval_rows = []
    for approval in approvals[-8:][::-1]:
        approval_rows.append([
            f"<code>{h(str(approval.get('job_id') or '-'))}</code>",
            status_badge(approval.get("status") or "pending"),
            h(approval.get("required_reviewer_role") or "-"),
            h((approval.get("reviewer") or {}).get("name") or "待分配"),
        ])
    source_distribution = knowledge_summary.get("source_type_distribution") or {}
    source_items = [(str(kind), count) for kind, count in list(source_distribution.items())[:8]] if isinstance(source_distribution, dict) else []
    detail_items = [
        ("企业资料", knowledge_summary.get("active_source_count", 0)),
        ("业务规则", knowledge_summary.get("rule_count", 0)),
        ("业务 Oracle", knowledge_summary.get("oracle_count", 0)),
        ("可执行 Probe", knowledge_summary.get("generated_probe_count", 0)),
    ]
    env_name = environment.get("target_environment") or "未选择"
    env_status = "可测" if env_testable else "待修复"
    deployment = (overview.get("runtime_config") or {}).get("deployment") or {}
    policies = (overview.get("runtime_config") or {}).get("policies") or {}

    readiness_body = (
        "<div class='split'>"
        "<div class='subtle-card'><h3>企业试点准备度</h3>"
        f"<div style='display:flex;align-items:end;gap:8px'><strong style='font-size:34px;line-height:1'>{readiness_value:.0f}</strong><span style='color:#64748b;padding-bottom:4px'>/100</span></div>"
        f"<div class='progress {'success' if readiness_value >= 75 else 'warning'}' style='margin-top:13px'><i style='width:{readiness_value:.1f}%'></i></div>"
        "<p class='micro'>评分用于评估资料、连接器、环境、受控任务与审计链是否具备试点条件，不代表生产缺陷发现率。</p></div>"
        "<div class='subtle-card'><h3>运行边界</h3>" + detail_list([
            ("目标环境", env_name),
            ("环境状态", env_status),
            ("默认模式", policies.get("default_execution_mode") or "environment_governed"),
            ("公开绑定", "已禁止" if not deployment.get("allow_public_bind") else "受控代理"),
        ]) + "</div></div>"
    )

    knowledge_body = (
        "<div class='split'><div>" + detail_list(detail_items) + "</div>"
        "<div class='subtle-card'><h3>资料来源分布</h3>"
        + ("<ul class='inline-list'>" + "".join(f"<li>{h(kind)} · {h(count)}</li>" for kind, count in source_items) + "</ul>" if source_items else empty_state("尚未接入资料", "接入 PRD、OpenAPI、权限矩阵、历史 Bug 等资料后会在此形成可追溯知识资产。"))
        + "<p class='micro'>原始资料不进入风险报告或证据包，只保留来源版本、摘要和可追溯关系。</p></div></div>"
    )

    body = (
        f"<div class='metric-grid'>{cards}</div>"
        + (findings_link if findings_link else "")
        + (readiness_banner if readiness_banner else "")
        + (
            f"<section class='onboarding-hero'>"
            f"<div class='onboarding-inner'>"
            f"<i>{_icon('spark')}</i>"
            f"<h2>还没有导入企业资料</h2>"
            f"<p>导入 PRD、OpenAPI、需求文档等资料后，QualiBug 会自动提取业务规则、生成业务 Oracle 并开始挖掘高价值 Bug。</p>"
            f"<div class='onboarding-steps'>"
            f"<div class='onboard-step'><span>1</span><strong>导入资料</strong><small>上传 PRD / OpenAPI / MRD</small></div>"
            f"<div class='onboard-arrow'>→</div>"
            f"<div class='onboard-step'><span>2</span><strong>自动推理</strong><small>业务规则 + 对象模型 + Oracle</small></div>"
            f"<div class='onboard-arrow'>→</div>"
            f"<div class='onboard-step'><span>3</span><strong>发现 Bug</strong><small>跨系统验证 + 证据报告</small></div>"
            f"</div>"
            f"<a class='btn btn-primary btn-lg' href='/knowledge?project={project}'>"
            f"<i>{_icon('assets')}</i>导入企业资料，开始使用"
            f"</a>"
            f"</div></section>"
            if source_count == 0 else ""
        )
        + section("试点总览", "先确认试点能否受控运行，再扩大到真实业务模块。", readiness_body, section_id="overview")
        + section("⚙️ 测试环境配置", "配置目标测试环境的连接地址和基本信息。",
            f"<form class='env-form' onsubmit='saveEnvConfig(event)'>"
            f"<div class='env-fields'>"
            f"<label>目标环境名称 <input name='target_environment' value='{h(env_name)}' placeholder='test'></label>"
            f"<label>测试环境地址 <input name='base_url' value='{h(env_base_url)}' placeholder='http://test-api.internal:8080'></label>"
            f"<label>请求超时(秒) <input name='timeout' type='number' value='{h(env_timeout)}' placeholder='10'></label>"
            f"</div>"
            f"<button type='submit' class='btn btn-primary'>保存环境配置</button>"
            f"<span class='env-msg' id='env-msg'></span>"
            f"</form>",
            section_id="environment-config")
        + section("受控任务编排", "所有运行动作均受项目隔离、角色审批和生产保护策略约束。", table(["任务类型", "目标环境", "状态", "执行模式", "创建时间"], task_rows, "尚未创建任务。可先从环境检查和控制平面刷新开始。"), section_id="runtime")
        + section("企业知识资产", "资料会自动归并为规则、接口、数据依赖和高价值风险，而不是另建人工知识包。", knowledge_body, section_id="knowledge")
        + section("连接器与同步", "连接器仅保存受控凭证引用；同步后的资料进入版本化知识资产。", table(["连接器", "类型", "同步状态", "凭证引用"], connector_rows, "尚未登记连接器。"), section_id="assets")
        + section("扫描历史", "最近 20 次扫描记录。", _scan_history_table(project, path), section_id="history")
        + section("环境与运行保护", "当前环境必须可测；生产/生产类环境的造数、写入、补偿和破坏性测试会被运行时拦截。", callout("环境状态：" + str(env_status), "当前目标环境为 " + str(env_name) + "。安全策略不会因 UI 操作而被绕过。", "success" if env_testable else "warning", "security"), section_id="environment")
        + section("独立审批", "提交人与审批人分离，风险数据准备任务必须由具备角色的其他成员审批。", table(["任务编号", "审批状态", "要求角色", "审批人"], approval_rows, "当前没有待审批任务。"), section_id="risk")
        + section("发布与审计", "审计哈希链、证据包、缺陷可信度和发布门禁继续复用现有业务质量链路。", callout("审计链：" + ("有效" if (overview.get("audit") or {}).get("valid") else "待检查"), "平台不会把环境不可达、账号失效或测试前置缺失伪装为高价值业务 Bug。", "success" if (overview.get("audit") or {}).get("valid") else "warning", "shield"), section_id="release")
    )
    rendered = product_shell(
        title="企业试点运营中心",
        project_id=str(overview.get("project_id") or "real_project_demo"),
        active="overview",
        eyebrow="Private pilot runtime",
        headline="把高价值业务 Bug 挖掘，带进可受控的企业试点流程。",
        description="从资料接入、环境健康、审批分离到证据与发布门禁，所有动作遵循同一份安全策略与审计链。",
        body=body,
        payload=overview,
        environment_label="私有化受控运行",
        page_hint="企业试点运营中心",
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(rendered, encoding="utf-8")

def operate_enterprise_pilot_runtime(project_id: str, action: str, payload: dict[str, Any] | None = None, root: Path | None = None, actor: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    payload = payload or {}
    if action == "view":
        return {"ok": True, "overview": build_enterprise_pilot_overview(project, root)}
    if action == "save_config":
        return {"ok": True, "config": save_pilot_runtime_config(project, payload, root, actor)}
    if action == "register_connector":
        return register_enterprise_connector(project, payload, root, actor)
    if action == "sync_connector_export":
        return sync_connector_export(project, str(payload.get("connector_id") or ""), payload.get("documents") or [], root, actor)
    if action == "enqueue":
        return enqueue_pilot_task(project, str(payload.get("job_type") or ""), payload, root, actor)
    if action == "approve":
        return approve_pilot_task(project, str(payload.get("job_id") or ""), str(payload.get("decision") or ""), root, actor, str(payload.get("note") or ""))
    if action == "run_next":
        return run_next_pilot_task(project, root, actor)
    if action == "tasks":
        return {"ok": True, "tasks": list_pilot_tasks(project, root)}
    raise ValueError("不支持的企业试点运行时动作")


def run_enterprise_pilot_demo() -> dict[str, Any]:
    """Safe local proof for Stage4 workflow; no remote traffic or writes."""
    with tempfile.TemporaryDirectory() as temp:
        root = Path(temp)
        project = "phase60_pilot_demo"
        owner = {"name": "owner", "role": "project_owner"}
        qa = {"name": "qa", "role": "qa_engineer"}
        lead = {"name": "lead", "role": "qa_lead"}
        security = {"name": "security", "role": "security_owner"}
        save_pilot_runtime_config(project, {"organization_id": "demo_org", "workspace_id": "demo_workspace"}, root, owner)
        from .enterprise_testops_control_plane import save_environment_config
        save_environment_config(project, {"target_environment": "test", "environments": [
            {"name": "test", "type": "system_test", "base_url": "", "account_pool": "test", "database_ref": "", "mock_enabled": True, "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": True, "production_protected": False},
            {"name": "prod-like", "type": "production_like", "base_url": "", "account_pool": "masked", "database_ref": "", "mock_enabled": False, "message_queue": {"enabled": False, "ref": ""}, "third_party": [], "allow_write_setup": False, "production_protected": True},
        ]}, root, owner)
        connector = register_enterprise_connector(project, {"kind": "confluence_export", "display_name": "需求文档导出", "credential_ref": "vault:confluence_ro", "external_ref": "confluence:space/demo"}, root, owner)["connector"]
        sync = sync_connector_export(project, connector["connector_id"], [
            {"filename": "PRD.md", "text": "客户只能访问本租户订单。订单支付后必须生成财务流水并扣减库存。订单 created -> paid -> shipped。", "source_type": "prd"},
            {"filename": "openapi.json", "text": json.dumps({"openapi": "3.0.3", "info": {"title": "Pilot", "version": "1"}, "paths": {"/orders/{order_id}": {"get": {"summary": "订单详情", "responses": {"200": {"description": "ok"}}}}, "/payments": {"post": {"summary": "支付订单", "responses": {"200": {"description": "ok"}}}}}}, ensure_ascii=False), "source_type": "openapi"},
        ], root, owner)
        refresh = enqueue_pilot_task(project, "control_plane_refresh", {"target_environment": "test"}, root, qa)
        health = enqueue_pilot_task(project, "environment_health", {"target_environment": "test"}, root, qa)
        discovery = enqueue_pilot_task(project, "safe_discovery_plan", {"target_environment": "test"}, root, qa)
        setup = enqueue_pilot_task(project, "sandbox_data_setup_plan", {"target_environment": "test", "execution_mode": "sandbox_plan_only"}, root, qa)
        approval_separation = False
        try:
            approve_pilot_task(project, setup["job"]["job_id"], "approved", root, qa)
        except PermissionError:
            approval_separation = True
        approve_pilot_task(project, setup["job"]["job_id"], "approved", root, lead)
        protected = enqueue_pilot_task(project, "safe_discovery_plan", {"target_environment": "prod-like"}, root, qa)
        approve_pilot_task(project, protected["job"]["job_id"], "approved", root, security)
        results = []
        for _ in range(8):
            result = run_next_pilot_task(project, root, lead)
            if result.get("idle"):
                break
            results.append(result)
        overview = build_enterprise_pilot_overview(project, root)
        protected_job = next((row for row in list_pilot_tasks(project, root)["jobs"] if row.get("job_id") == protected["job"]["job_id"]), {})
        return {
            "phase": PHASE,
            "passed": bool(sync.get("ok") and approval_separation and overview.get("audit", {}).get("valid") and any(row.get("job", {}).get("status") == "succeeded" for row in results) and protected_job.get("status") == "blocked"),
            "sync_created": len(sync.get("ingestion", {}).get("created") or []),
            "task_statuses": [row.get("job", {}).get("status") for row in results],
            "protected_job_status": protected_job.get("status"),
            "network_requests": sum(int((row.get("job", {}).get("result") or {}).get("network_requests") or 0) for row in results),
            "scorecard": overview.get("pilot_scorecard"),
            "audit": overview.get("audit"),
        }


def _cli() -> int:
    parser = argparse.ArgumentParser(description="QualiBug enterprise pilot runtime")
    parser.add_argument("--project", default="real_project_demo")
    parser.add_argument("--root", default="")
    parser.add_argument("--demo", action="store_true")
    parser.add_argument("--overview", action="store_true")
    parser.add_argument("--run-next", action="store_true")
    args = parser.parse_args()
    root = Path(args.root).resolve() if args.root else ROOT
    if args.demo:
        print(json.dumps(run_enterprise_pilot_demo(), ensure_ascii=False, indent=2))
        return 0
    if args.run_next:
        print(json.dumps(run_next_pilot_task(args.project, root, {"name": "cli_operator", "role": "qa_lead"}), ensure_ascii=False, indent=2))
        return 0
    print(json.dumps(build_enterprise_pilot_overview(args.project, root, rebuild_control=not args.overview), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(_cli())
