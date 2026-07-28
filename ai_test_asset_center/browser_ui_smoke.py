from __future__ import annotations

"""Minimal browser UI evidence collector.

This module intentionally implements the first browser layer as a non-blocking
smoke probe rather than a full visual-AI oracle. It provides the evidence shape
needed by the command center while keeping Playwright optional for private pilot
installs.
"""

import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

E_BROWSER_UI_DISABLED = "E_BROWSER_UI_DISABLED"
E_BROWSER_UI_NO_TARGET = "E_BROWSER_UI_NO_TARGET"
E_BROWSER_UI_PLAYWRIGHT_NOT_INSTALLED = "E_BROWSER_UI_PLAYWRIGHT_NOT_INSTALLED"
E_BROWSER_UI_RUNTIME_ERROR = "E_BROWSER_UI_RUNTIME_ERROR"
BROWSER_UI_SCHEMA_VERSION = "browser-ui-smoke-v1"


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on", "y"}


def is_browser_ui_enabled() -> bool:
    return _truthy(os.environ.get("QUALIBUG_BROWSER_UI_SMOKE", "0"))


def _normalize_url(base_url: str, path: str = "/") -> str:
    base = str(base_url or "").strip()
    if not base:
        return ""
    clean_path = "/" + str(path or "/").strip().lstrip("/")
    if base.endswith("/"):
        return urljoin(base, clean_path.lstrip("/"))
    return urljoin(base + "/", clean_path.lstrip("/"))


def _evidence_dir(root: Path, project: str) -> Path:
    path = root / "platform_outputs" / project / "browser_ui_smoke"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _base_report(*, enabled: bool, reason_code: str = "", message: str = "") -> dict[str, Any]:
    return {
        "schema_version": BROWSER_UI_SCHEMA_VERSION,
        "enabled": enabled,
        "status": "disabled" if not enabled else "pending",
        "reason_code": reason_code,
        "message": message,
        "page_count": 0,
        "reachable_page_count": 0,
        "console_error_count": 0,
        "network_error_count": 0,
        "screenshot_count": 0,
        "pages": [],
        "evidence_files": [],
    }


def disabled_report(reason_code: str = E_BROWSER_UI_DISABLED, message: str = "Browser UI smoke probe is disabled.") -> dict[str, Any]:
    return _base_report(enabled=False, reason_code=reason_code, message=message)


def derive_ui_targets(base_url: str, *, explicit_paths: list[str] | None = None) -> list[str]:
    paths = explicit_paths or ["/"]
    targets: list[str] = []
    for path in paths:
        url = _normalize_url(base_url, path)
        if url and url not in targets:
            targets.append(url)
    return targets


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")


def run_browser_ui_smoke(
    *,
    project: str,
    root: Path,
    base_url: str = "",
    paths: list[str] | None = None,
    enabled: bool | None = None,
    timeout_ms: int = 10_000,
) -> dict[str, Any]:
    """Run a minimal Playwright smoke probe and persist browser evidence.

    The probe captures:
    - page reachability and title;
    - console errors;
    - failed network requests;
    - one screenshot per visited page.

    ``paths`` is accepted only from the caller. The collector never invents routes
    from API endpoints or page text; a formal caller supplies source-declared page
    paths, while ordinary smoke use retains the root-page default.

    It returns a stable report even when Playwright is unavailable, so callers can
    surface the exact blocker instead of silently omitting UI coverage.
    """
    should_run = is_browser_ui_enabled() if enabled is None else bool(enabled)
    if not should_run:
        return disabled_report()

    targets = derive_ui_targets(base_url, explicit_paths=paths)
    if not targets:
        return disabled_report(E_BROWSER_UI_NO_TARGET, "Browser UI smoke probe requires a UI base_url or target URL.")

    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except Exception:
        return disabled_report(
            E_BROWSER_UI_PLAYWRIGHT_NOT_INSTALLED,
            "Playwright is not installed. Install the browser extra and run playwright install to enable UI evidence.",
        )

    out_dir = _evidence_dir(root, project)
    run_id = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    pages: list[dict[str, Any]] = []
    evidence_files: list[str] = []
    console_error_count = 0
    network_error_count = 0
    reachable_page_count = 0

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True, record_har_path=str(out_dir / f"ui_smoke_{run_id}.har"))
            for index, target in enumerate(targets):
                page = context.new_page()
                console_errors: list[dict[str, Any]] = []
                network_errors: list[dict[str, Any]] = []

                page.on(
                    "console",
                    lambda msg, bucket=console_errors: bucket.append({"type": msg.type, "text": msg.text})
                    if msg.type in {"error", "warning"}
                    else None,
                )
                page.on(
                    "requestfailed",
                    lambda req, bucket=network_errors: bucket.append(
                        {
                            "url": req.url,
                            "method": req.method,
                            "failure": (req.failure or {}).get("errorText", "request_failed") if isinstance(req.failure, dict) else str(req.failure or "request_failed"),
                        }
                    ),
                )

                status_code = 0
                ok = False
                title = ""
                screenshot_path = out_dir / f"ui_smoke_{run_id}_{index}.png"
                error = ""
                started = time.perf_counter()
                try:
                    response = page.goto(target, wait_until="networkidle", timeout=timeout_ms)
                    status_code = int(response.status) if response is not None else 0
                    ok = status_code == 0 or status_code < 400
                    if ok:
                        reachable_page_count += 1
                    title = page.title()[:300]
                    page.screenshot(path=str(screenshot_path), full_page=True)
                    evidence_files.append(str(screenshot_path))
                except Exception as exc:
                    error = str(exc)[:500]
                    try:
                        page.screenshot(path=str(screenshot_path), full_page=True)
                        evidence_files.append(str(screenshot_path))
                    except Exception:
                        pass
                finally:
                    duration_ms = int((time.perf_counter() - started) * 1000)
                    console_error_count += len(console_errors)
                    network_error_count += len(network_errors)
                    pages.append(
                        {
                            "url": target,
                            "reachable": ok,
                            "status_code": status_code,
                            "title": title,
                            "duration_ms": duration_ms,
                            "screenshot_path": str(screenshot_path) if screenshot_path.exists() else "",
                            "console_errors": console_errors,
                            "network_errors": network_errors,
                            "error": error,
                        }
                    )
                    page.close()
            context.close()
            browser.close()
    except Exception as exc:
        report = disabled_report(E_BROWSER_UI_RUNTIME_ERROR, str(exc)[:500])
        report["status"] = "error"
        _write_json(out_dir / "browser_ui_smoke_report.json", report)
        return report

    har_path = out_dir / f"ui_smoke_{run_id}.har"
    if har_path.exists():
        evidence_files.append(str(har_path))

    status = "passed" if reachable_page_count == len(targets) and console_error_count == 0 and network_error_count == 0 else "needs_review"
    report = {
        "schema_version": BROWSER_UI_SCHEMA_VERSION,
        "enabled": True,
        "status": status,
        "reason_code": "",
        "message": "Browser UI smoke probe completed.",
        "page_count": len(targets),
        "reachable_page_count": reachable_page_count,
        "console_error_count": console_error_count,
        "network_error_count": network_error_count,
        "screenshot_count": sum(1 for p in pages if p.get("screenshot_path")),
        "pages": pages,
        "evidence_files": evidence_files,
    }
    _write_json(out_dir / "browser_ui_smoke_report.json", report)
    return report


def attach_browser_ui_health(
    result: dict[str, Any],
    *,
    project: str,
    root: Path,
    base_url: str = "",
    paths: list[str] | None = None,
    enabled: bool | None = None,
) -> dict[str, Any]:
    """Attach browser UI health without mutating the scan's original failure semantics.

    Formal callers may provide source-declared ``paths``. Ordinary callers omit it and
    retain the historical root-page smoke behavior.
    """
    updated = dict(result or {})
    report = run_browser_ui_smoke(
        project=project,
        root=root,
        base_url=base_url,
        paths=paths,
        enabled=enabled,
    )
    updated["browser_ui_health"] = report
    coverage_gaps = list(updated.get("coverage_gaps") or []) if isinstance(updated.get("coverage_gaps"), list) else []
    if not report.get("enabled"):
        gap = {
            "family": "ui_visual",
            "reason_code": report.get("reason_code") or E_BROWSER_UI_DISABLED,
            "message": report.get("message") or "Browser UI smoke probe is disabled.",
        }
        if gap not in coverage_gaps:
            coverage_gaps.append(gap)
    elif report.get("status") != "passed":
        gap = {
            "family": "ui_visual",
            "reason_code": "E_BROWSER_UI_NEEDS_REVIEW",
            "message": "Browser UI smoke probe found console/network/page reachability signals that need review.",
        }
        if gap not in coverage_gaps:
            coverage_gaps.append(gap)
    updated["coverage_gaps"] = coverage_gaps
    return updated
