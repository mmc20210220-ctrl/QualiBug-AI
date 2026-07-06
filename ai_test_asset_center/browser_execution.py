"""Policy-gated browser execution with Playwright evidence capture.

The adapter intentionally does not infer click paths from generic UI text.
Callers supply a source-bound plan. Safe-read-only mode permits observation
only; interactive steps require an approved sandbox-write contract.
"""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse, urlunparse


_READ_ONLY_ACTIONS = {"goto", "expect_text", "expect_url", "wait_for_load", "screenshot"}
_INTERACTIVE_ACTIONS = {"click", "fill", "check", "select_option", "press"}
_SENSITIVE_QUERY_KEYS = {"token", "access_token", "api_key", "apikey", "password", "secret"}


class BrowserExecutionError(RuntimeError):
    """A browser plan cannot safely execute."""


def _safe_project(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def _safe_run_id(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or f"browser_{int(time.time() * 1000)}"


def _redact_url(value: str) -> str:
    parsed = urlparse(value)
    if not parsed.query:
        return value
    query = [(key, "<REDACTED>" if key.lower() in _SENSITIVE_QUERY_KEYS else item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)]
    return urlunparse(parsed._replace(query=urlencode(query)))


def _as_steps(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        raise BrowserExecutionError("browser_steps_required")
    steps = [dict(item) for item in value if isinstance(item, dict)]
    if not steps:
        raise BrowserExecutionError("browser_steps_required")
    return steps


def validate_browser_plan(plan: dict[str, Any], runtime_contract: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(plan, dict):
        raise BrowserExecutionError("browser_plan_invalid")
    if str((runtime_contract or {}).get("status") or "") != "approved":
        raise BrowserExecutionError("browser_runtime_contract_not_approved")
    base_url = str((runtime_contract or {}).get("approved_base_url") or "").strip()
    if not base_url:
        raise BrowserExecutionError("browser_target_missing")
    execution_mode = str(plan.get("execution_mode") or "safe_read_only")
    if execution_mode not in {"safe_read_only", "approved_sandbox_write"}:
        raise BrowserExecutionError("browser_execution_mode_invalid")
    if execution_mode == "approved_sandbox_write" and plan.get("write_approved") is not True:
        raise BrowserExecutionError("browser_write_approval_missing")
    normalized_steps: list[dict[str, Any]] = []
    for position, raw in enumerate(_as_steps(plan), start=1):
        action = str(raw.get("action") or "").strip().lower()
        if action not in _READ_ONLY_ACTIONS | _INTERACTIVE_ACTIONS:
            raise BrowserExecutionError(f"browser_action_unsupported:{action or position}")
        if execution_mode == "safe_read_only" and action not in _READ_ONLY_ACTIONS:
            raise BrowserExecutionError(f"browser_interaction_requires_approval:{action}")
        target = str(raw.get("url") or "").strip()
        if action == "goto":
            if not target:
                raise BrowserExecutionError("browser_goto_url_missing")
            resolved = urljoin(base_url.rstrip("/") + "/", target)
            if not resolved.startswith(base_url.rstrip("/")):
                raise BrowserExecutionError("browser_target_outside_approved_base_url")
            raw["url"] = resolved
        elif action in _INTERACTIVE_ACTIONS | {"expect_text"}:
            if not str(raw.get("selector") or "").strip():
                raise BrowserExecutionError(f"browser_selector_missing:{action}")
        elif action == "expect_url" and not str(raw.get("pattern") or raw.get("url") or "").strip():
            raise BrowserExecutionError("browser_url_expectation_missing")
        raw["action"] = action
        raw["step_index"] = position
        normalized_steps.append(raw)
    return {"execution_mode": execution_mode, "base_url": base_url.rstrip("/"), "steps": normalized_steps}


def execute_browser_plan(
    project_id: str,
    plan: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str = "",
) -> dict[str, Any]:
    """Execute a validated plan and persist Playwright trace/screenshot assets.

    Missing Playwright is a clear blocked result, never a simulated execution.
    """
    validated = validate_browser_plan(plan, runtime_contract)
    project = _safe_project(project_id)
    execution_id = _safe_run_id(run_id)
    artifact_dir = Path(root) / "platform_workspace" / project / "browser_runs" / execution_id
    artifact_dir.mkdir(parents=True, exist_ok=True)
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return {
            "status": "blocked",
            "reason": "BROWSER_RUNTIME_UNAVAILABLE",
            "execution_status": "not_executed",
            "confirmation_status": "blocked",
            "artifact_dir": str(artifact_dir.relative_to(Path(root))),
            "steps": [],
        }

    started = time.time()
    receipts: list[dict[str, Any]] = []
    console: list[dict[str, str]] = []
    network: list[dict[str, Any]] = []
    trace_path = artifact_dir / "trace.zip"
    screenshot_path = artifact_dir / "final.png"
    status = "executed"
    reason = ""
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            context = browser.new_context()
            context.tracing.start(screenshots=True, snapshots=True, sources=True)
            page = context.new_page()
            page.on("console", lambda message: console.append({"type": message.type, "text": message.text[:4000]}))
            page.on("response", lambda response: network.append({"url": _redact_url(response.url), "status": response.status, "method": response.request.method}))
            for step in validated["steps"]:
                action = step["action"]
                receipt: dict[str, Any] = {"step_index": step["step_index"], "action": action, "started_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
                if action == "goto":
                    response = page.goto(step["url"], wait_until=str(step.get("wait_until") or "networkidle"), timeout=int(step.get("timeout_ms") or 30_000))
                    receipt.update({"url": _redact_url(step["url"]), "status": response.status if response else 0})
                elif action == "expect_text":
                    page.locator(step["selector"]).filter(has_text=str(step.get("text") or "")).first.wait_for(timeout=int(step.get("timeout_ms") or 10_000))
                elif action == "expect_url":
                    page.wait_for_url(str(step.get("pattern") or step.get("url")), timeout=int(step.get("timeout_ms") or 10_000))
                elif action == "wait_for_load":
                    page.wait_for_load_state(str(step.get("state") or "networkidle"), timeout=int(step.get("timeout_ms") or 30_000))
                elif action == "screenshot":
                    output = artifact_dir / f"step_{step['step_index']}.png"
                    page.screenshot(path=str(output), full_page=bool(step.get("full_page", True)))
                    receipt["screenshot"] = output.name
                elif action == "click":
                    page.locator(step["selector"]).click(timeout=int(step.get("timeout_ms") or 10_000))
                elif action == "fill":
                    page.locator(step["selector"]).fill(str(step.get("value") or ""), timeout=int(step.get("timeout_ms") or 10_000))
                elif action == "check":
                    page.locator(step["selector"]).check(timeout=int(step.get("timeout_ms") or 10_000))
                elif action == "select_option":
                    page.locator(step["selector"]).select_option(str(step.get("value") or ""), timeout=int(step.get("timeout_ms") or 10_000))
                elif action == "press":
                    page.locator(step["selector"]).press(str(step.get("key") or "Enter"), timeout=int(step.get("timeout_ms") or 10_000))
                receipts.append(receipt)
            page.screenshot(path=str(screenshot_path), full_page=True)
            context.tracing.stop(path=str(trace_path))
            context.close()
            browser.close()
    except Exception as exc:
        status = "failed"
        reason = f"{type(exc).__name__}:{str(exc)[:300]}"
    result = {
        "status": status,
        "reason": reason,
        "execution_status": "executed" if status == "executed" else "failed",
        "confirmation_status": "candidate",
        "execution_mode": validated["execution_mode"],
        "artifact_dir": str(artifact_dir.relative_to(Path(root))),
        "trace_ref": str(trace_path.relative_to(Path(root))) if trace_path.exists() else "",
        "screenshot_ref": str(screenshot_path.relative_to(Path(root))) if screenshot_path.exists() else "",
        "steps": receipts,
        "console": console,
        "network": network,
        "duration_ms": int((time.time() - started) * 1000),
    }
    (artifact_dir / "browser_execution.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return result
