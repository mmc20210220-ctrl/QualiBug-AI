"""External bridge for page-agent style UI execution.

The bridge is intentionally transport-based so QualiBug can delegate page-agent
tasks without coupling runtime ownership of the browser to the scanner
process. Callers must provide a configured bridge endpoint; otherwise the task
remains blocked rather than simulated.
"""
from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


class PageAgentBridgeError(RuntimeError):
    """The external page-agent bridge cannot execute safely."""


def _safe_id(value: Any, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or default


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _bridge_config(request: dict[str, Any], execution_context: dict[str, Any] | None) -> dict[str, Any]:
    metadata = _as_dict(request.get("metadata"))
    candidates = [
        _as_dict(metadata.get("page_agent_bridge")),
        _as_dict((execution_context or {}).get("page_agent_bridge")),
    ]
    config = next((item for item in candidates if item), {})
    url = str(
        config.get("url")
        or os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_URL")
        or ""
    ).strip()
    token = str(
        config.get("token")
        or os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_TOKEN")
        or ""
    ).strip()
    timeout_ms = int(config.get("timeout_ms") or os.environ.get("QUALIBUG_PAGE_AGENT_BRIDGE_TIMEOUT_MS") or 120000)
    return {
        "url": url,
        "token": token,
        "timeout_ms": max(timeout_ms, 1000),
        "provider": str(config.get("provider") or "external_http").strip() or "external_http",
    }


def execute_page_agent_request(
    project_id: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    config = _bridge_config(request, execution_context)
    if not config["url"]:
        raise PageAgentBridgeError("PAGE_AGENT_BRIDGE_URL_MISSING")

    project = _safe_id(project_id, "unscoped")
    request_id = _safe_id(request.get("request_id"), "ui_request")
    artifact_dir = Path(root) / "platform_workspace" / project / "page_agent_runs" / _safe_id(run_id, "page_agent") / request_id
    artifact_dir.mkdir(parents=True, exist_ok=True)

    payload = {
        "project_id": project_id,
        "request_id": request_id,
        "title": str(request.get("title") or ""),
        "task": str(request.get("task") or ""),
        "start_url": str(request.get("start_url") or ""),
        "execution_mode": str(request.get("execution_mode") or "safe_read_only"),
        "browser_plan": _as_dict(request.get("browser_plan")),
        "page_hints": _as_list(request.get("page_hints")),
        "success_criteria": _as_dict(request.get("success_criteria")),
        "metadata": _as_dict(request.get("metadata")),
        "runtime_contract": {
            "status": str(runtime_contract.get("status") or ""),
            "approved_base_url": str(runtime_contract.get("approved_base_url") or ""),
            "execution_mode": str(runtime_contract.get("execution_mode") or runtime_contract.get("approved_execution_mode") or ""),
        },
    }
    (artifact_dir / "bridge_request.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    headers = {"Content-Type": "application/json"}
    if config["token"]:
        headers["Authorization"] = f"Bearer {config['token']}"
    started = time.time()
    try:
        http_request = urllib.request.Request(
            config["url"],
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(http_request, timeout=config["timeout_ms"] / 1000.0) as response:
            raw_body = response.read().decode("utf-8", errors="replace")
            status_code = getattr(response, "status", 200) or 200
    except urllib.error.HTTPError as exc:
        raw_body = exc.read().decode("utf-8", errors="replace")
        status_code = int(exc.code or 500)
    except urllib.error.URLError as exc:
        raise PageAgentBridgeError(f"PAGE_AGENT_BRIDGE_UNREACHABLE:{exc.reason}") from exc
    except Exception as exc:  # pragma: no cover - defensive IO guard
        raise PageAgentBridgeError(f"PAGE_AGENT_BRIDGE_ERROR:{type(exc).__name__}") from exc

    response_payload: dict[str, Any]
    try:
        parsed = json.loads(raw_body or "null")
        response_payload = parsed if isinstance(parsed, dict) else {"raw": raw_body}
    except json.JSONDecodeError:
        response_payload = {"raw": raw_body}

    normalized = {
        "bridge_status_code": status_code,
        "provider": "page_agent",
        "bridge_provider": config["provider"],
        "status": str(response_payload.get("status") or ("executed" if status_code < 400 else "failed")),
        "reason": str(response_payload.get("reason") or ""),
        "execution_status": str(response_payload.get("execution_status") or ("executed" if status_code < 400 else "failed")),
        "confirmation_status": str(response_payload.get("confirmation_status") or "candidate"),
        "current_url": str(response_payload.get("current_url") or ""),
        "history": _as_list(response_payload.get("history")),
        "console": _as_list(response_payload.get("console")),
        "network": _as_list(response_payload.get("network")),
        "artifacts": _as_list(response_payload.get("artifacts")),
        "findings": _as_list(response_payload.get("findings")),
        "created_data": _as_dict(response_payload.get("created_data")),
        "duration_ms": int(response_payload.get("duration_ms") or int((time.time() - started) * 1000)),
    }
    if status_code >= 400 and normalized["status"] == "executed":
        normalized["status"] = "failed"
        normalized["execution_status"] = "failed"
    (artifact_dir / "bridge_response.json").write_text(json.dumps({**normalized, "raw_response": response_payload}, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return {
        "request_id": request_id,
        "title": str(request.get("title") or ""),
        "provider": "page_agent",
        "task": str(request.get("task") or ""),
        "start_url": str(request.get("start_url") or ""),
        "artifact_dir": str(artifact_dir.relative_to(Path(root))),
        **normalized,
    }
