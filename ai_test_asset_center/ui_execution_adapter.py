"""Generic UI execution adapter with explicit provider boundaries.

This adapter keeps Playwright-based browser execution as the runtime baseline
and reserves a provider slot for future page-agent integration. It never
simulates successful execution when a provider is unavailable.

Provider-produced finding dictionaries are NOT formal findings. They have not
passed the typed observer, assertion, Contract Oracle, reproduction, or
customer Delivery Gate chain. They are retained only as ``provider_clues``;
formal UI findings are produced by ``ui_formal_surface`` from source-bound
success criteria and receipted execution evidence.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Any


_SUPPORTED_PROVIDERS = {"playwright_browser_plan", "page_agent"}


def _safe_id(value: Any, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _source_refs(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in _as_list(value) if isinstance(item, dict)]


def normalize_ui_execution_requests(value: Any) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(value), start=1):
        raw = _as_dict(item)
        provider = str(raw.get("provider") or "page_agent").strip().lower()
        if provider not in _SUPPORTED_PROVIDERS:
            provider = "page_agent"
        request_id = _safe_id(raw.get("request_id") or raw.get("id"), f"ui_request_{index}")
        requests.append({
            "request_id": request_id,
            "title": str(raw.get("title") or raw.get("task") or request_id).strip()[:200],
            "provider": provider,
            "task": str(raw.get("task") or "").strip(),
            "start_url": str(raw.get("start_url") or raw.get("url") or "").strip(),
            "execution_mode": str(raw.get("execution_mode") or "safe_read_only").strip(),
            "browser_plan": _as_dict(raw.get("browser_plan")),
            "success_criteria": _as_dict(raw.get("success_criteria")),
            "source_refs": _source_refs(raw.get("source_refs")),
            "actor_ref": str(raw.get("actor_ref") or "").strip(),
            "severity": str(raw.get("severity") or "").strip(),
            "page_hints": [str(item).strip()[:500] for item in _as_list(raw.get("page_hints")) if str(item).strip()],
            "metadata": _as_dict(raw.get("metadata")),
        })
    return requests


def _contract_projection(request: dict[str, Any]) -> dict[str, Any]:
    """Fields whose authority comes from the caller's source-bound request."""
    return {
        "success_criteria": dict(_as_dict(request.get("success_criteria"))),
        "source_refs": _source_refs(request.get("source_refs")),
        "actor_ref": str(request.get("actor_ref") or "").strip(),
        "severity": str(request.get("severity") or "").strip(),
        "execution_mode": str(request.get("execution_mode") or "safe_read_only").strip(),
    }


def _blocked_request_result(request: dict[str, Any], reason: str, *, root: Path | None = None) -> dict[str, Any]:
    return {
        "request_id": request.get("request_id", ""),
        "title": request.get("title", ""),
        "provider": request.get("provider", ""),
        "task": request.get("task", ""),
        "start_url": request.get("start_url", ""),
        **_contract_projection(request),
        "status": "blocked",
        "reason": reason,
        "execution_status": "not_executed",
        "confirmation_status": "blocked",
        "artifact_dir": "" if root is None else "",
        "metadata": _as_dict(request.get("metadata")),
        "created_data": {},
        "artifacts": [],
        "provider_clues": [],
        "findings": [],
        "duration_ms": 0,
    }


def _page_agent_request_result(
    project_id: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from .page_agent_bridge import PageAgentBridgeError, execute_page_agent_request

    try:
        result = execute_page_agent_request(
            project_id,
            request,
            runtime_contract,
            root=root,
            run_id=run_id,
            execution_context=execution_context,
        )
    except PageAgentBridgeError as exc:
        return {
            **_blocked_request_result(request, str(exc), root=root),
            "integration_hint": "Configure page_agent_bridge.url in request metadata, campaign_context, or QUALIBUG_PAGE_AGENT_BRIDGE_URL.",
        }
    provider_clues = [
        dict(item)
        for item in _as_list(_as_dict(result).get("findings"))
        if isinstance(item, dict)
    ]
    metadata = _as_dict(result.get("metadata")) or _as_dict(request.get("metadata"))
    return {
        **result,
        **_contract_projection(request),
        "metadata": metadata,
        # Provider output remains useful for triage but cannot bypass formal authority.
        "provider_clues": provider_clues,
        "findings": [],
    }


def _playwright_request_result(
    project_id: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    from .browser_execution import BrowserExecutionError, execute_browser_plan

    plan = _as_dict(request.get("browser_plan"))
    if not plan:
        return _blocked_request_result(request, "UI_BROWSER_PLAN_MISSING", root=root)
    # The request-level mode is authoritative unless the source-bound plan declares the
    # same value. A mismatch is refused by browser plan validation rather than silently
    # choosing the more permissive one.
    request_mode = str(request.get("execution_mode") or "safe_read_only").strip()
    plan_mode = str(plan.get("execution_mode") or request_mode).strip()
    if plan_mode != request_mode:
        return _blocked_request_result(request, "UI_EXECUTION_MODE_MISMATCH", root=root)
    plan = {**plan, "execution_mode": request_mode}
    if request.get("start_url"):
        steps = _as_list(plan.get("steps"))
        if steps and not _as_dict(steps[0]).get("url"):
            steps = [dict(_as_dict(step)) for step in steps]
            steps[0]["url"] = request["start_url"]
            plan = {**plan, "steps": steps}
    try:
        result = execute_browser_plan(
            project_id,
            plan,
            runtime_contract,
            root=root,
            run_id=f"{run_id}_{request['request_id']}",
        )
    except BrowserExecutionError as exc:
        return _blocked_request_result(request, str(exc), root=root)
    return {
        "request_id": request["request_id"],
        "title": request.get("title", ""),
        "provider": "playwright_browser_plan",
        "task": request.get("task", ""),
        "start_url": request.get("start_url", ""),
        **_contract_projection(request),
        "metadata": _as_dict(request.get("metadata")),
        "created_data": {},
        "status": str(result.get("status") or "blocked"),
        "reason": str(result.get("reason") or ""),
        "execution_status": str(result.get("execution_status") or "not_executed"),
        "confirmation_status": str(result.get("confirmation_status") or "candidate"),
        "artifact_dir": str(result.get("artifact_dir") or ""),
        "artifacts": [
            {
                "artifact_type": "trace",
                "ref": str(result.get("trace_ref") or ""),
            },
            {
                "artifact_type": "screenshot",
                "ref": str(result.get("screenshot_ref") or ""),
            },
            {
                "artifact_type": "har",
                "ref": str(result.get("har_ref") or ""),
            },
        ],
        "steps": _as_list(result.get("steps")),
        "console": _as_list(result.get("console")),
        "network": _as_list(result.get("network")),
        "provider_clues": [],
        "findings": [],
        "duration_ms": int(result.get("duration_ms") or 0),
    }


def execute_ui_execution_requests(
    project_id: str,
    requests: Any,
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str = "",
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_ui_execution_requests(requests)
    if not normalized:
        return {
            "status": "not_requested",
            "requested": 0,
            "executed": 0,
            "failed": 0,
            "blocked": 0,
            "provider_distribution": {},
            "results": [],
            "findings": [],
            "provider_clues": [],
            "artifacts": [],
            "duration_ms": 0,
        }
    started = time.time()
    provider_distribution: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    executed = 0
    failed = 0
    blocked = 0
    execution_id = _safe_id(run_id, "ui_execution")
    for request in normalized:
        provider = str(request.get("provider") or "page_agent")
        provider_distribution[provider] = provider_distribution.get(provider, 0) + 1
        if provider == "playwright_browser_plan":
            result = _playwright_request_result(project_id, request, runtime_contract, root=root, run_id=execution_id)
        else:
            result = _page_agent_request_result(
                project_id,
                request,
                runtime_contract,
                root=root,
                run_id=execution_id,
                execution_context=execution_context,
            )
        status = str(result.get("status") or "")
        if status == "executed":
            executed += 1
        elif status == "failed":
            failed += 1
        else:
            blocked += 1
        for artifact in _as_list(result.get("artifacts")):
            ref = str(_as_dict(artifact).get("ref") or "").strip()
            if not ref:
                continue
            artifacts.append({
                "request_id": request.get("request_id", ""),
                "provider": provider,
                "artifact_type": str(_as_dict(artifact).get("artifact_type") or "ui_artifact"),
                "ref": ref,
            })
        results.append(result)
    overall_status = "completed"
    if blocked and not executed and not failed:
        overall_status = "blocked"
    elif failed and executed:
        overall_status = "partial"
    elif failed and not executed:
        overall_status = "failed"
    elif executed and blocked:
        overall_status = "partial"
    provider_clues = [
        dict(item)
        for result in results
        for item in _as_list(_as_dict(result).get("provider_clues"))
        if isinstance(item, dict)
    ]
    return {
        "status": overall_status,
        "requested": len(normalized),
        "executed": executed,
        "failed": failed,
        "blocked": blocked,
        "provider_distribution": provider_distribution,
        "results": results,
        # Formal findings are created only after observer/assertion/oracle/gate.
        "findings": [],
        "provider_clues": provider_clues,
        "artifacts": artifacts,
        "duration_ms": int((time.time() - started) * 1000),
    }
