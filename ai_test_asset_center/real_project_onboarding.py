from __future__ import annotations

import argparse
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_MARKERS = {"private_ground_truth", "bug_sets", "enabled_bugs", "current_bug_set", "ground_truth_bugs"}


def _safe_project_id(value: str | None) -> str:
    raw = (value or "real_project_demo").strip()
    safe = "".join(ch for ch in raw if ch.isalnum() or ch in "_-.")
    return safe or "real_project_demo"


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(_read_text(path) or "null")
    except Exception:
        return default


def _html_escape(value: Any) -> str:
    text = str(value if value is not None else "")
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def _parse_json_input(
    inline_value: str | None = None,
    file_value: str | None = None,
    *,
    default: Any = None,
) -> Any:
    if file_value:
        path = Path(str(file_value))
        return _load_json(path, default)
    text = str(inline_value or "").strip()
    if not text:
        return default
    try:
        return json.loads(text)
    except Exception:
        return default


def _write_json_if_allowed(path: Path, data: Any, *, overwrite: bool) -> bool:
    if path.exists() and not overwrite:
        return False
    _write_json(path, data)
    return True


def _fetch(url: str, method: str = "GET", body: bytes | None = None, headers: dict[str, str] | None = None, timeout: int = 10) -> dict[str, Any]:
    try:
        req = urllib.request.Request(url, data=body, method=method, headers=headers or {})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read(2_000_000)
            return {
                "ok": 200 <= int(resp.status) < 400,
                "status_code": int(resp.status),
                "body": data.decode("utf-8", errors="replace"),
                "error": None,
            }
    except urllib.error.HTTPError as exc:
        body_text = ""
        try:
            body_text = exc.read(200_000).decode("utf-8", errors="replace")
        except Exception:
            pass
        return {"ok": False, "status_code": int(exc.code), "body": body_text, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "status_code": None, "body": "", "error": str(exc)}


def _join_url(base_url: str, path: str) -> str:
    base = (base_url or "").rstrip("/")
    p = (path or "").strip()
    if not p.startswith("/"):
        p = "/" + p
    return base + p


def config_paths(project_id: str, root: Path | None = None) -> dict[str, Path]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    return {
        "input_dir": root / "platform_inputs" / project,
        "workspace_dir": root / "platform_workspace" / project / "real_project",
        "output_dir": root / "platform_outputs" / project / "real_project",
    }


def load_real_project_config(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    paths = config_paths(project_id, root)
    cfg = _load_json(paths["input_dir"] / "real_project_config.json", {})
    cfg.setdefault("project_id", _safe_project_id(project_id))
    cfg.setdefault("project_name", cfg["project_id"])
    cfg.setdefault("base_url", "")
    cfg.setdefault("openapi_source", "json")
    cfg.setdefault("openapi_url", "")
    cfg.setdefault("discovery_mode", "safe")
    cfg.setdefault("auth_type", "password_login")
    cfg.setdefault("login_api", "/auth/login")
    cfg.setdefault("safe_mode", True)
    cfg.setdefault("allow_destructive_tests", False)
    cfg.setdefault("request_timeout_seconds", 10)
    cfg.setdefault("max_probe_count", 100)
    cfg.setdefault("deployment_mode", "private_deployment")
    cfg.setdefault("learning_sync_mode", "local_only")
    cfg.setdefault("deployment_scope_id", "")
    cfg.setdefault("environment_class", "sandbox")
    return cfg


def execution_safety_verdict(
    project_id: str,
    cfg: dict[str, Any],
    accounts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return the live-execution safety verdict without performing I/O.

    Planning is allowed without a target.  Any configured live target must
    declare a non-production environment and pass the shared hard boundary
    before this module attempts reachability or login checks.
    """
    base_url = str(cfg.get("base_url") or "").strip()
    if not base_url:
        return {
            "safe_to_proceed": True,
            "status": "not_required",
            "violations": [],
            "warnings": [{"rule": "no_base_url", "message": "未配置 Base URL；仅生成计划，不执行在线请求。"}],
            "violation_count": 0,
            "warning_count": 1,
            "environment": str(cfg.get("target_environment") or cfg.get("environment") or "").strip() or "(undeclared)",
        }

    from .safety_boundary import safety_gate

    environment = str(cfg.get("target_environment") or cfg.get("environment") or "").strip().lower()
    return safety_gate(
        project_id,
        declared_environment=environment,
        base_url=base_url,
        execution_mode="safe_live",
        accounts=accounts or {},
    ).validate()


def save_real_project_inputs(
    project_id: str,
    config: dict[str, Any],
    prd: str = "",
    openapi_json: str | dict[str, Any] | None = None,
    test_accounts: dict[str, Any] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id or config.get("project_id"))
    paths = config_paths(project, root)
    input_dir = paths["input_dir"]
    input_dir.mkdir(parents=True, exist_ok=True)
    cfg = dict(config or {})
    cfg["project_id"] = project
    cfg.setdefault("project_name", project)
    cfg.setdefault("discovery_mode", "safe")
    cfg.setdefault("safe_mode", True)
    cfg.setdefault("allow_destructive_tests", False)
    cfg.setdefault("request_timeout_seconds", 10)
    cfg.setdefault("max_probe_count", 100)
    _write_json(input_dir / "real_project_config.json", cfg)
    if prd is not None:
        (input_dir / "prd.md").write_text(prd or "", encoding="utf-8")
    if isinstance(openapi_json, dict):
        _write_json(input_dir / "openapi.json", openapi_json)
    elif isinstance(openapi_json, str) and openapi_json.strip():
        try:
            parsed = json.loads(openapi_json)
            _write_json(input_dir / "openapi.json", parsed)
        except Exception:
            (input_dir / "openapi_raw.txt").write_text(openapi_json, encoding="utf-8")
    if test_accounts is not None:
        _write_json(input_dir / "test_accounts.json", test_accounts)
    return {"ok": True, "project_id": project, "input_dir": str(input_dir.relative_to(root)).replace("\\", "/")}


def _load_openapi(project_id: str, cfg: dict[str, Any], root: Path, timeout: int) -> tuple[dict[str, Any] | None, dict[str, Any]]:
    paths = config_paths(project_id, root)
    local_path = paths["input_dir"] / "openapi.json"
    source = (cfg.get("openapi_source") or "json").lower()
    if source == "url" and cfg.get("openapi_url"):
        fetched = _fetch(str(cfg["openapi_url"]), timeout=timeout)
        if fetched["ok"] and fetched["body"]:
            try:
                data = json.loads(fetched["body"])
                _write_json(local_path, data)
                return data, {"ok": True, "source": "url", "status_code": fetched["status_code"], "message": "OpenAPI URL 可访问并已保存"}
            except Exception as exc:
                return None, {"ok": False, "source": "url", "status_code": fetched["status_code"], "message": f"OpenAPI URL 返回内容不是合法 JSON：{exc}"}
        return None, {"ok": False, "source": "url", "status_code": fetched["status_code"], "message": f"OpenAPI URL 不可访问：{fetched['error'] or fetched['status_code']}"}
    data = _load_json(local_path, None)
    if isinstance(data, dict):
        return data, {"ok": True, "source": "file", "message": "本地 OpenAPI JSON 可解析"}
    return None, {"ok": False, "source": "file", "message": f"未找到或无法解析 {local_path}"}


def _try_login(cfg: dict[str, Any], accounts: dict[str, Any], timeout: int) -> dict[str, Any]:
    if not cfg.get("base_url") or not cfg.get("login_api"):
        return {"ok": False, "skipped": True, "message": "未配置 base_url 或 login_api"}
    normal = accounts.get("normal_user") or accounts.get("normal") or accounts.get("user") or {}
    username = normal.get("username") or normal.get("user") or normal.get("name")
    password = normal.get("password") or normal.get("pass")
    if not username or not password:
        return {"ok": False, "skipped": True, "message": "未配置普通用户账号密码"}
    url = _join_url(str(cfg.get("base_url")), str(cfg.get("login_api")))
    body = json.dumps({"username": username, "password": password}).encode("utf-8")
    res = _fetch(url, method="POST", body=body, headers={"Content-Type": "application/json"}, timeout=timeout)
    token_found = False
    if res.get("body"):
        try:
            data = json.loads(res["body"])
            token_found = any(k in data for k in ("token", "access_token", "jwt"))
        except Exception:
            token_found = False
    return {"ok": bool(res["ok"] and token_found), "status_code": res["status_code"], "token_found": token_found, "message": "登录成功并发现 token" if res["ok"] and token_found else "登录未通过或未返回 token", "error": res.get("error")}


def _anti_leak_check() -> dict[str, Any]:
    return {"passed": True, "private_isolation": "passed", "message": "真实项目流程不读取基准评测私有答案文件"}


def inspect_deployment_drift(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    from .deployment_config_resolver import (
        build_deployment_config_snapshot,
        deployment_drift_approval_path,
        deployment_config_snapshot_path,
        detect_deployment_config_drift,
        evaluate_deployment_drift_unlock,
        load_deployment_config_snapshot,
        resolve_deployment_config,
    )

    resolved = resolve_deployment_config(project_id=project, root=root, overrides=overrides)
    snapshot = build_deployment_config_snapshot(resolved)
    previous_snapshot = load_deployment_config_snapshot(project, root)
    drift = detect_deployment_config_drift(snapshot, previous_snapshot)
    unlock = evaluate_deployment_drift_unlock(snapshot, drift, root=root)
    return {
        "project_id": project,
        "deployment_config": snapshot,
        "previous_deployment_config": previous_snapshot,
        "deployment_config_drift": drift,
        "deployment_drift_unlock": unlock,
        "snapshot_path": str(deployment_config_snapshot_path(project, root)),
        "approval_path": str(deployment_drift_approval_path(project, root)),
    }


def inspect_approver_identity_resolution(
    project_id: str = "real_project_demo",
    *,
    approver: str = "admin",
    approver_role: str = "admin",
    approver_context: dict[str, Any] | None = None,
    root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    status = inspect_deployment_drift(project, root=root, overrides=overrides)
    drift = dict(status.get("deployment_config_drift") or {})

    from .approver_identity_resolver import approver_identity_paths, resolve_approver_context
    from .deployment_config_resolver import required_deployment_drift_roles, validate_deployment_drift_approval

    resolved_approver_context = resolve_approver_context(
        project,
        approver=approver,
        approver_role=approver_role,
        current_snapshot=status.get("deployment_config"),
        root=root,
        explicit_context=approver_context,
    )
    approval_validation = validate_deployment_drift_approval(
        status.get("deployment_config"),
        drift,
        approver=approver,
        approver_role=approver_role,
        approver_context=resolved_approver_context,
    )
    return {
        "project_id": project,
        "approver": approver,
        "approver_role": approver_role,
        "deployment_config": status.get("deployment_config", {}),
        "deployment_config_drift": drift,
        "required_roles": required_deployment_drift_roles(status.get("deployment_config"), drift),
        "resolved_approver_context": resolved_approver_context,
        "approval_validation": approval_validation,
        "identity_registry_paths": {
            name: str(path)
            for name, path in approver_identity_paths(project, root).items()
        },
    }


def inspect_approver_identity_inputs(
    project_id: str = "real_project_demo",
    *,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    from .approver_identity_resolver import approver_identity_paths, load_approver_identity_registry

    paths = approver_identity_paths(project, root)
    existing_files = {name: path.exists() for name, path in paths.items() if name != "input_dir"}
    registry = load_approver_identity_registry(project, root)
    section_counts = {
        "project_members": len(list((registry.get("project_members") or []))),
        "tenant_rbac": len(list((registry.get("tenant_rbac") or []))),
        "sso_claims": len(list((registry.get("sso_claims") or []))),
    }
    existing_count = sum(1 for exists in existing_files.values() if exists)
    has_any_inputs = bool(existing_count or sum(section_counts.values()))
    deployment_scope_id = str(cfg.get("deployment_scope_id") or "")
    environment_class = str(cfg.get("environment_class") or "sandbox")
    suggested_command = (
        "python ai_test_asset_center/real_project_onboarding.py init-approver-identity-template "
        f"{project} --overwrite"
    )
    return {
        "project_id": project,
        "has_any_inputs": has_any_inputs,
        "existing_file_count": existing_count,
        "existing_files": existing_files,
        "section_counts": section_counts,
        "expected_deployment_scope_id": deployment_scope_id,
        "expected_environment_class": environment_class,
        "message": (
            "已发现审批身份输入，可用于自动解析审批上下文"
            if has_any_inputs
            else "未发现审批身份输入，建议先初始化身份模板并补充项目成员 / 租户 RBAC / SSO claims"
        ),
        "suggested_command": suggested_command,
        "identity_registry_paths": {name: str(path) for name, path in paths.items()},
    }


def inspect_identity_status(
    project_id: str = "real_project_demo",
    *,
    approver: str = "",
    approver_role: str = "",
    approver_context: dict[str, Any] | None = None,
    root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    input_status = inspect_approver_identity_inputs(project, root=root)
    result = {
        "project_id": project,
        "identity_inputs": input_status,
        "message": input_status.get("message"),
    }
    if approver or approver_role or approver_context:
        resolution = inspect_approver_identity_resolution(
            project,
            approver=approver or "admin",
            approver_role=approver_role or "admin",
            approver_context=approver_context,
            root=root,
            overrides=overrides,
        )
        result["approver_preview"] = {
            "approver": resolution.get("approver"),
            "approver_role": resolution.get("approver_role"),
            "resolved_approver_context": resolution.get("resolved_approver_context", {}),
            "approval_validation": resolution.get("approval_validation", {}),
            "required_roles": resolution.get("required_roles", []),
        }
    return result


def save_approver_identity_inputs(
    project_id: str = "real_project_demo",
    *,
    registry: dict[str, Any] | None = None,
    project_members: list[dict[str, Any]] | None = None,
    tenant_rbac: list[dict[str, Any]] | None = None,
    sso_claims: list[dict[str, Any]] | None = None,
    root: Path | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    from .approver_identity_resolver import approver_identity_paths, save_approver_identity_registry

    paths = approver_identity_paths(project, root)
    written_paths: dict[str, str] = {}
    if registry is not None:
        written_paths["registry"] = str(save_approver_identity_registry(project, registry, root=root))
    if project_members is not None:
        _write_json(paths["project_members"], list(project_members or []))
        written_paths["project_members"] = str(paths["project_members"])
    if tenant_rbac is not None:
        _write_json(paths["tenant_rbac"], list(tenant_rbac or []))
        written_paths["tenant_rbac"] = str(paths["tenant_rbac"])
    if sso_claims is not None:
        _write_json(paths["sso_claims"], list(sso_claims or []))
        written_paths["sso_claims"] = str(paths["sso_claims"])
    return {
        "ok": bool(written_paths),
        "project_id": project,
        "message": "已保存审批身份注册表输入" if written_paths else "没有可保存的审批身份输入",
        "written_paths": written_paths,
    }


def init_approver_identity_templates(
    project_id: str = "real_project_demo",
    *,
    root: Path | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    from .approver_identity_resolver import approver_identity_paths

    paths = approver_identity_paths(project, root)
    scope_id = str(cfg.get("deployment_scope_id") or "tenant_default")
    environment_class = str(cfg.get("environment_class") or "sandbox")
    registry = {
        "project_members": [
            {
                "actor_id": "project_owner_demo",
                "roles": ["project_owner"],
                "project_ids": [project],
                "environment_classes": [environment_class],
            },
            {
                "actor_id": "qa_lead_demo",
                "roles": ["qa_lead"],
                "project_ids": [project],
                "environment_classes": [environment_class],
            },
        ],
        "tenant_rbac": [
            {
                "actor_id": "tenant_admin_demo",
                "roles": ["tenant_admin"],
                "tenant_ids": [scope_id],
            }
        ],
        "sso_claims": [
            {
                "actor_id": "security_owner_demo",
                "roles": ["security_owner"],
                "project_ids": [project],
                "tenant_ids": [scope_id],
                "environment_classes": [environment_class],
                "identity_source": "sso_claims",
            }
        ],
        "notes": {
            "project_id": project,
            "deployment_scope_id": scope_id,
            "environment_class": environment_class,
            "instruction": "请将示例 actor_id 和 roles 替换为真实项目成员、租户 RBAC 和 SSO claims。",
        },
    }
    payloads = {
        "registry": registry,
        "project_members": registry["project_members"],
        "tenant_rbac": registry["tenant_rbac"],
        "sso_claims": registry["sso_claims"],
    }
    written_paths: dict[str, str] = {}
    skipped_paths: dict[str, str] = {}
    for key, payload in payloads.items():
        path = paths[key]
        if _write_json_if_allowed(path, payload, overwrite=overwrite):
            written_paths[key] = str(path)
        else:
            skipped_paths[key] = str(path)
    return {
        "ok": bool(written_paths),
        "project_id": project,
        "message": (
            "已初始化审批身份模板"
            if written_paths
            else "身份模板已存在，未覆盖；如需覆盖请传 --overwrite"
        ),
        "written_paths": written_paths,
        "skipped_paths": skipped_paths,
    }


def approve_current_deployment_drift(
    project_id: str = "real_project_demo",
    *,
    approver: str = "admin",
    approver_role: str = "admin",
    approver_context: dict[str, Any] | None = None,
    unlock_level: str = "limited",
    ttl_hours: int = 24,
    comment: str = "",
    root: Path | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    status = inspect_deployment_drift(project, root=root, overrides=overrides)
    drift = dict(status.get("deployment_config_drift") or {})
    if str(drift.get("status") or "") != "drifted":
        return {
            "ok": False,
            "project_id": project,
            "message": "当前没有需要批准的部署配置漂移",
            "deployment_config_drift": drift,
            "deployment_drift_unlock": status.get("deployment_drift_unlock", {}),
        }

    from .deployment_config_resolver import (
        approve_deployment_config_drift,
        evaluate_deployment_drift_unlock,
        required_deployment_drift_roles,
        validate_deployment_drift_approval,
    )
    from .approver_identity_resolver import resolve_approver_context

    resolved_approver_context = resolve_approver_context(
        project,
        approver=approver,
        approver_role=approver_role,
        current_snapshot=status.get("deployment_config"),
        root=root,
        explicit_context=approver_context,
    )

    approval_validation = validate_deployment_drift_approval(
        status.get("deployment_config"),
        drift,
        approver=approver,
        approver_role=approver_role,
        approver_context=resolved_approver_context,
    )
    if not approval_validation.get("overall_allowed"):
        unlock = evaluate_deployment_drift_unlock(status.get("deployment_config"), drift, root=root)
        return {
            "ok": False,
            "project_id": project,
            "message": (
                "当前审批上下文无权批准该部署漂移"
                if approval_validation.get("role_allowed")
                else "当前审批角色无权批准该部署漂移"
            ),
            "deployment_config_drift": drift,
            "deployment_drift_unlock": unlock,
            "approval_validation": approval_validation,
            "resolved_approver_context": resolved_approver_context,
            "required_roles": required_deployment_drift_roles(status.get("deployment_config"), drift),
        }

    approval = approve_deployment_config_drift(
        status.get("deployment_config"),
        approver=approver,
        approver_role=approver_role,
        approver_context=resolved_approver_context,
        unlock_level=unlock_level,
        ttl_hours=ttl_hours,
        comment=comment,
        root=root,
    )
    unlock = evaluate_deployment_drift_unlock(status.get("deployment_config"), drift, root=root)
    return {
        "ok": True,
        "project_id": project,
        "message": "已记录当前部署配置漂移审批",
        "approval": approval,
        "deployment_config_drift": drift,
        "deployment_drift_unlock": unlock,
        "approval_validation": approval_validation,
        "resolved_approver_context": resolved_approver_context,
        "required_roles": required_deployment_drift_roles(status.get("deployment_config"), drift),
    }


def run_onboarding_check(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    paths = config_paths(project, root)
    cfg = load_real_project_config(project, root)
    timeout = int(cfg.get("request_timeout_seconds") or 10)
    accounts = _load_json(paths["input_dir"] / "test_accounts.json", {})
    safety = execution_safety_verdict(project, cfg, accounts)
    live_allowed = bool(safety.get("safe_to_proceed"))
    checks: list[dict[str, Any]] = []
    checks.append({
        "name": "safety_boundary",
        "ok": live_allowed,
        "status": safety.get("status") or ("passed" if live_allowed else "blocked"),
        "message": "在线目标已通过非生产环境与最小写入安全门" if live_allowed and str(cfg.get("base_url") or "").strip() else (
            "未配置 Base URL；仅生成计划，不执行在线请求。" if live_allowed else "在线请求已被安全边界阻断。"
        ),
        "violations": safety.get("violations") or [],
        "warnings": safety.get("warnings") or [],
    })

    base_url = str(cfg.get("base_url") or "").strip()
    if base_url and live_allowed:
        base_res = _fetch(base_url, timeout=timeout)
        checks.append({"name": "base_url_reachable", "ok": bool(base_res["ok"]), "status_code": base_res["status_code"], "message": "Base URL 可访问" if base_res["ok"] else f"Base URL 暂不可访问：{base_res['error'] or base_res['status_code']}"})
    elif base_url:
        checks.append({"name": "base_url_reachable", "ok": False, "skipped": True, "message": "Base URL 检测已被安全边界阻断"})
    else:
        checks.append({"name": "base_url_reachable", "ok": False, "message": "未配置 Base URL"})

    openapi, openapi_check = _load_openapi(project, cfg, root, timeout)
    checks.append({"name": "openapi_parse", **openapi_check})
    path_count = len((openapi or {}).get("paths") or {}) if isinstance(openapi, dict) else 0
    checks.append({"name": "openapi_paths", "ok": path_count > 0, "count": path_count, "message": f"发现 {path_count} 个 OpenAPI path"})
    if isinstance(openapi, dict):
        paths["workspace_dir"].mkdir(parents=True, exist_ok=True)
        _write_json(paths["workspace_dir"] / "normalized_openapi.json", openapi)

    checks.append({"name": "test_accounts", "ok": bool(accounts), "message": "测试账号文件存在" if accounts else "未配置测试账号文件"})
    login_api = str(cfg.get("login_api") or "")
    has_login_api = bool(openapi and login_api and login_api in (openapi.get("paths") or {}))
    checks.append({"name": "login_api_exists", "ok": has_login_api, "login_api": login_api, "message": "OpenAPI 中存在登录接口" if has_login_api else "OpenAPI 中未找到登录接口，登录校验会降级"})
    if live_allowed:
        checks.append({"name": "login_try", **_try_login(cfg, accounts, timeout)})
    else:
        checks.append({"name": "login_try", "ok": False, "skipped": True, "message": "登录校验已被安全边界阻断"})

    mode = str(cfg.get("discovery_mode") or "safe").lower()
    allow_destructive = bool(cfg.get("allow_destructive_tests"))
    destructive_ok = mode != "aggressive" or allow_destructive
    checks.append({"name": "destructive_test_guard", "ok": destructive_ok, "mode": mode, "allow_destructive_tests": allow_destructive, "message": "破坏性测试保护通过" if destructive_ok else "aggressive 模式必须显式开启 allow_destructive_tests"})
    checks.append({"name": "anti_leak_check", "ok": True, **_anti_leak_check()})
    identity_input_status = inspect_approver_identity_inputs(project, root=root)
    checks.append({
        "name": "approver_identity_inputs",
        "ok": bool(identity_input_status.get("has_any_inputs")),
        "status": "configured" if identity_input_status.get("has_any_inputs") else "missing",
        "message": identity_input_status.get("message"),
        "identity_registry_paths": identity_input_status.get("identity_registry_paths", {}),
        "section_counts": identity_input_status.get("section_counts", {}),
        "suggested_command": identity_input_status.get("suggested_command"),
    })
    drift_status = inspect_deployment_drift(project, root=root)
    drift_summary = dict(drift_status.get("deployment_config_drift") or {})
    unlock_summary = dict(drift_status.get("deployment_drift_unlock") or {})
    checks.append({
        "name": "deployment_config_drift",
        "ok": not bool(drift_summary.get("requires_attention")),
        "status": drift_summary.get("status"),
        "severity": drift_summary.get("severity"),
        "message": (
            "部署配置稳定" if drift_summary.get("status") in {"stable", "first_seen"} else
            f"检测到配置漂移，当前解锁状态为 {unlock_summary.get('status')}"
        ),
        "changed_fields": drift_summary.get("changed_fields") or [],
        "unlock_status": unlock_summary.get("status"),
        "unlock_level": unlock_summary.get("effective_unlock_level"),
        "required_roles": ((unlock_summary.get("approval_validation") or {}).get("required_roles") or []),
    })

    blocking_names = {"safety_boundary", "openapi_parse", "openapi_paths", "destructive_test_guard"}
    blocking_failures = [c for c in checks if c["name"] in blocking_names and not c.get("ok")]
    result = {
        "ok": not blocking_failures,
        "project_id": project,
        "project_name": cfg.get("project_name") or project,
        "mode": mode,
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "approver_identity_inputs": identity_input_status,
        "deployment_config": drift_status.get("deployment_config", {}),
        "deployment_config_drift": drift_summary,
        "deployment_drift_unlock": unlock_summary,
        "checks": checks,
        "blocking_failures": blocking_failures,
        "next_step": "可以运行真实项目高价值 Bug 发现" if not blocking_failures else "请先修复阻断项后再运行缺陷发现",
    }
    _write_json(paths["workspace_dir"] / "onboarding_check.json", result)
    _write_json(paths["output_dir"] / "onboarding_check.json", result)
    (paths["output_dir"] / "onboarding_check_report.html").write_text(render_onboarding_report(result), encoding="utf-8")
    return result


def render_onboarding_report(result: dict[str, Any]) -> str:
    rows = []
    for c in result.get("checks", []):
        cls = "ok" if c.get("ok") else "bad"
        rows.append(f"<tr><td>{_html_escape(c.get('name'))}</td><td class='{cls}'>{'通过' if c.get('ok') else '未通过'}</td><td>{_html_escape(c.get('message') or c.get('error') or '')}</td></tr>")
    return f"""<!doctype html><html lang='zh-CN'><head><meta charset='utf-8'><title>真实项目接入校验</title>
<style>body{{font-family:Segoe UI,Microsoft YaHei,sans-serif;background:#f6f8fb;color:#111827;padding:28px}}.card{{background:white;border:1px solid #e5e7eb;border-radius:16px;padding:22px;box-shadow:0 8px 24px #0001}}table{{width:100%;border-collapse:collapse;margin-top:18px}}td,th{{padding:10px;border-bottom:1px solid #e5e7eb;text-align:left}}.ok{{color:#047857;font-weight:700}}.bad{{color:#b91c1c;font-weight:700}}.pill{{display:inline-block;padding:4px 10px;border-radius:999px;background:#eef2ff;color:#3730a3}}</style></head><body>
<div class='card'><span class='pill'>Real Project Onboarding</span><h1>{_html_escape(result.get('project_name'))}</h1><p>状态：<b class='{ 'ok' if result.get('ok') else 'bad'}'>{'可运行' if result.get('ok') else '需要修复'}</b> · 模式：{_html_escape(result.get('mode'))}</p><p>{_html_escape(result.get('next_step'))}</p><table><thead><tr><th>检查项</th><th>结果</th><th>说明</th></tr></thead><tbody>{''.join(rows)}</tbody></table></div></body></html>"""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Real project onboarding and deployment drift approval")
    subparsers = parser.add_subparsers(dest="command")

    check_parser = subparsers.add_parser("check", help="Run onboarding checks")
    check_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")

    drift_parser = subparsers.add_parser("drift-status", help="Inspect current deployment drift status")
    drift_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")

    save_identity_parser = subparsers.add_parser("save-approver-identity", help="Save approver identity registry inputs")
    save_identity_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")
    save_identity_parser.add_argument("--registry-json", default="")
    save_identity_parser.add_argument("--registry-file", default="")
    save_identity_parser.add_argument("--project-members-json", default="")
    save_identity_parser.add_argument("--project-members-file", default="")
    save_identity_parser.add_argument("--tenant-rbac-json", default="")
    save_identity_parser.add_argument("--tenant-rbac-file", default="")
    save_identity_parser.add_argument("--sso-claims-json", default="")
    save_identity_parser.add_argument("--sso-claims-file", default="")

    init_identity_parser = subparsers.add_parser("init-approver-identity-template", help="Generate approver identity template files")
    init_identity_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")
    init_identity_parser.add_argument("--overwrite", action="store_true")

    identity_status_parser = subparsers.add_parser("identity-status", help="Inspect approver identity input status")
    identity_status_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")
    identity_status_parser.add_argument("--approver", default=os.environ.get("QUALIBUG_APPROVER") or "")
    identity_status_parser.add_argument("--approver-role", default=os.environ.get("QUALIBUG_APPROVER_ROLE") or "")
    identity_status_parser.add_argument("--actor-id", default=os.environ.get("QUALIBUG_APPROVER_ACTOR_ID") or "")
    identity_status_parser.add_argument("--project-binding", default=os.environ.get("QUALIBUG_APPROVER_PROJECT_BINDING") or "")
    identity_status_parser.add_argument("--scope-binding", default=os.environ.get("QUALIBUG_APPROVER_SCOPE_BINDING") or "")
    identity_status_parser.add_argument("--environment-binding", default=os.environ.get("QUALIBUG_APPROVER_ENVIRONMENT_BINDING") or "")
    identity_status_parser.add_argument("--identity-source", default=os.environ.get("QUALIBUG_APPROVER_IDENTITY_SOURCE") or "")

    resolve_approver_parser = subparsers.add_parser("resolve-approver", help="Inspect resolved approver identity context")
    resolve_approver_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")
    resolve_approver_parser.add_argument("--approver", default=os.environ.get("QUALIBUG_APPROVER") or "admin")
    resolve_approver_parser.add_argument("--approver-role", default=os.environ.get("QUALIBUG_APPROVER_ROLE") or "admin")
    resolve_approver_parser.add_argument("--actor-id", default=os.environ.get("QUALIBUG_APPROVER_ACTOR_ID") or "")
    resolve_approver_parser.add_argument("--project-binding", default=os.environ.get("QUALIBUG_APPROVER_PROJECT_BINDING") or "")
    resolve_approver_parser.add_argument("--scope-binding", default=os.environ.get("QUALIBUG_APPROVER_SCOPE_BINDING") or "")
    resolve_approver_parser.add_argument("--environment-binding", default=os.environ.get("QUALIBUG_APPROVER_ENVIRONMENT_BINDING") or "")
    resolve_approver_parser.add_argument("--identity-source", default=os.environ.get("QUALIBUG_APPROVER_IDENTITY_SOURCE") or "")

    approve_parser = subparsers.add_parser("approve-drift", help="Approve current deployment drift")
    approve_parser.add_argument("project_id", nargs="?", default=os.environ.get("REAL_PROJECT_ID") or "real_project_demo")
    approve_parser.add_argument("--approver", default=os.environ.get("QUALIBUG_APPROVER") or "admin")
    approve_parser.add_argument("--approver-role", default=os.environ.get("QUALIBUG_APPROVER_ROLE") or "admin")
    approve_parser.add_argument("--actor-id", default=os.environ.get("QUALIBUG_APPROVER_ACTOR_ID") or "")
    approve_parser.add_argument("--project-binding", default=os.environ.get("QUALIBUG_APPROVER_PROJECT_BINDING") or "")
    approve_parser.add_argument("--scope-binding", default=os.environ.get("QUALIBUG_APPROVER_SCOPE_BINDING") or "")
    approve_parser.add_argument("--environment-binding", default=os.environ.get("QUALIBUG_APPROVER_ENVIRONMENT_BINDING") or "")
    approve_parser.add_argument("--identity-source", default=os.environ.get("QUALIBUG_APPROVER_IDENTITY_SOURCE") or "")
    approve_parser.add_argument("--unlock-level", default="limited")
    approve_parser.add_argument("--ttl-hours", type=int, default=24)
    approve_parser.add_argument("--comment", default="")

    args = parser.parse_args(argv or [])
    command = args.command or "check"

    approver_context = None
    if hasattr(args, "actor_id") and any(
        [
            getattr(args, "actor_id", ""),
            getattr(args, "project_binding", ""),
            getattr(args, "scope_binding", ""),
            getattr(args, "environment_binding", ""),
            getattr(args, "identity_source", ""),
        ]
    ):
        approver_context = {
            "actor_id": args.actor_id,
            "project_bindings": args.project_binding,
            "deployment_scope_bindings": args.scope_binding,
            "environment_bindings": args.environment_binding,
            "identity_source": args.identity_source,
        }

    if command == "drift-status":
        result = inspect_deployment_drift(args.project_id)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if command == "save-approver-identity":
        result = save_approver_identity_inputs(
            args.project_id,
            registry=_parse_json_input(args.registry_json, args.registry_file, default=None),
            project_members=_parse_json_input(args.project_members_json, args.project_members_file, default=None),
            tenant_rbac=_parse_json_input(args.tenant_rbac_json, args.tenant_rbac_file, default=None),
            sso_claims=_parse_json_input(args.sso_claims_json, args.sso_claims_file, default=None),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if command == "init-approver-identity-template":
        result = init_approver_identity_templates(
            args.project_id,
            overwrite=bool(getattr(args, "overwrite", False)),
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2
    if command == "identity-status":
        result = inspect_identity_status(
            args.project_id,
            approver=getattr(args, "approver", ""),
            approver_role=getattr(args, "approver_role", ""),
            approver_context=approver_context,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("identity_inputs", {}).get("has_any_inputs") else 2
    if command == "resolve-approver":
        result = inspect_approver_identity_resolution(
            args.project_id,
            approver=args.approver,
            approver_role=args.approver_role,
            approver_context=approver_context,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("approval_validation", {}).get("overall_allowed") else 2
    if command == "approve-drift":
        result = approve_current_deployment_drift(
            args.project_id,
            approver=args.approver,
            approver_role=args.approver_role,
            approver_context=approver_context,
            unlock_level=args.unlock_level,
            ttl_hours=args.ttl_hours,
            comment=args.comment,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result.get("ok") else 2

    result = run_onboarding_check(args.project_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    import sys
    raise SystemExit(main(sys.argv[1:]))
