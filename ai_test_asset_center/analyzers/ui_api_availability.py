from __future__ import annotations

"""P3-13 Bidirectional UI/API Availability Checker.

Detects two symmetric defect patterns:
  A. UI visible but API unavailable (P3-12 covered by HttpStatusOracle)
  B. API available but UI unreachable (P3-13 — this module's focus)

Detection strategy:
  - For each API endpoint in the spec, check whether its frontend page is reachable
  - Compare: API GET returns 2xx/3xx, but frontend page returns 4xx/5xx → P3-13
  - Compare: Frontend GET returns 2xx, but API returns 5xx → P3-12 (existing coverage)

Cross-layer evidence:
  - API response: status code, body excerpt
  - UI response: HTML page status code, title, body excerpt
  - mismatch kind: api_ok_ui_broken | ui_ok_api_broken | both_ok | both_broken
"""

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urljoin, urlparse


_SUCCESS_MIN = 200
_SUCCESS_MAX = 399
_CLIENT_ERROR_MIN = 400
_CLIENT_ERROR_MAX = 499
_SERVER_ERROR_MIN = 500

# Common frontend entry paths to probe for UI reachability
_DEFAULT_UI_PATHS = ["/", "/index.html", "/app", "/dashboard", "/login"]


@dataclass
class UIApiAvailabilityCheck:
    """Result of checking one API endpoint against its UI counterpart."""
    api_method: str
    api_path: str
    api_status: int
    api_body_preview: str
    ui_path: str
    ui_status: int
    ui_body_preview: str
    mismatch_kind: str = ""  # api_ok_ui_broken | ui_ok_api_broken | both_ok | both_broken | api_unreachable
    severity: str = "P2"
    evidence: dict = field(default_factory=dict)


def _safe_get(
    url: str, timeout: float = 8.0, headers: dict[str, str] | None = None
) -> tuple[int, str]:
    """HTTP GET returning (status_code, body_preview)."""
    req_headers = headers or {"User-Agent": "QualiBug-P3-13/1.0", "Accept": "text/html,application/json,*/*"}
    req = urllib.request.Request(url, headers=req_headers, method="GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4096).decode("utf-8", errors="replace")
            return int(resp.status), body[:2000]
    except urllib.error.HTTPError as exc:
        body = exc.read(4096).decode("utf-8", errors="replace") if exc.fp else ""
        return int(exc.code), body[:2000]
    except Exception:
        return 0, ""


def _is_html_response(body_preview: str) -> bool:
    """Heuristic: does the response body look like an HTML page?"""
    return bool(re.search(r"<!DOCTYPE\s+html|<html|<body|<div\b|<form\b", body_preview[:500], re.IGNORECASE))


def _is_api_response(body_preview: str) -> bool:
    """Heuristic: does the response body look like JSON API output?"""
    stripped = body_preview.strip()
    if stripped.startswith("{") or stripped.startswith("["):
        try:
            json.loads(stripped)
            return True
        except (json.JSONDecodeError, ValueError):
            pass
    return False


def _resolve_ui_paths(api_path: str, base_url: str) -> list[str]:
    """Map an API path to likely frontend UI paths.

    Example: /api/orders → ["/orders", "/app/orders", "/"]
    """
    candidates: list[str] = []
    # Strip /api prefix for SPA routes
    clean = api_path.strip("/")
    if clean.startswith("api/"):
        spa_route = "/" + clean[4:]
        candidates.append(spa_route)
    # Try the API path as-is (some apps serve HTML at /api/*)
    candidates.append("/" + clean)
    # Generic fallback paths
    candidates.extend(_DEFAULT_UI_PATHS)
    # Deduplicate while preserving order
    seen: set[str] = set()
    unique: list[str] = []
    parsed_base = urlparse(base_url)
    for c in candidates:
        full = urljoin(base_url.rstrip("/") + "/", c.lstrip("/"))
        if full not in seen:
            seen.add(full)
            unique.append(full)
    return unique


def check_api_ui_availability(
    api_method: str,
    api_path: str,
    base_url: str,
    *,
    timeout: float = 8.0,
    headers: dict[str, str] | None = None,
) -> UIApiAvailabilityCheck:
    """Check bidirectional availability for one API endpoint.

    Probes:
      1. API endpoint → records status
      2. UI paths mapped from API → records best status
      3. Classifies mismatch kind
    """
    parsed = urlparse(base_url)
    if not parsed.scheme or not parsed.netloc:
        return UIApiAvailabilityCheck(
            api_method=api_method, api_path=api_path, api_status=0,
            api_body_preview="", ui_path="", ui_status=0, ui_body_preview="",
            mismatch_kind="api_unreachable", severity="P3",
        )

    api_url = urljoin(base_url.rstrip("/") + "/", api_path.lstrip("/"))
    api_status, api_body = _safe_get(api_url, timeout, headers)

    ui_candidates = _resolve_ui_paths(api_path, base_url)
    best_ui_status = 0
    best_ui_body = ""
    best_ui_path = ""
    for ui_url in ui_candidates:
        ui_status, ui_body = _safe_get(ui_url, timeout, headers)
        if ui_status > 0:
            best_ui_status = ui_status
            best_ui_body = ui_body
            best_ui_path = ui_url
            break

    # Classify mismatch
    api_ok = _SUCCESS_MIN <= api_status <= _SUCCESS_MAX
    ui_ok = _SUCCESS_MIN <= best_ui_status <= _SUCCESS_MAX
    api_broken = api_status >= _SERVER_ERROR_MIN
    ui_broken = best_ui_status >= _CLIENT_ERROR_MIN

    if api_ok and ui_broken:
        mismatch_kind = "api_ok_ui_broken"
        severity = "P2"
    elif ui_ok and api_broken:
        mismatch_kind = "ui_ok_api_broken"
        severity = "P1"
    elif api_ok and ui_ok:
        mismatch_kind = "both_ok"
        severity = "P3"
    elif not api_ok and not ui_ok:
        mismatch_kind = "both_broken"
        severity = "P2"
    else:
        mismatch_kind = "api_unreachable"
        severity = "P3"

    return UIApiAvailabilityCheck(
        api_method=api_method,
        api_path=api_path,
        api_status=api_status,
        api_body_preview=api_body[:500],
        ui_path=best_ui_path,
        ui_status=best_ui_status,
        ui_body_preview=best_ui_body[:500],
        mismatch_kind=mismatch_kind,
        severity=severity,
        evidence={
            "api_url": api_url,
            "api_status": api_status,
            "ui_url": best_ui_path,
            "ui_status": best_ui_status,
            "api_is_html": _is_html_response(api_body),
            "ui_is_html": _is_html_response(best_ui_body),
            "api_is_json": _is_api_response(api_body),
        },
    )


def scan_api_ui_availability(
    api_spec_text: str,
    base_url: str,
    *,
    timeout: float = 8.0,
    headers: dict[str, str] | None = None,
) -> list[UIApiAvailabilityCheck]:
    """Scan all API endpoints in an OpenAPI spec for UI/API availability mismatches.

    Parses the spec for GET endpoints and checks each against frontend availability.
    Returns findings only for mismatches (api_ok_ui_broken and ui_ok_api_broken).
    """
    results: list[UIApiAvailabilityCheck] = []
    if not base_url:
        return results

    # Extract GET endpoints from spec
    endpoints: list[tuple[str, str]] = []
    try:
        spec = json.loads(api_spec_text)
        paths = spec.get("paths", {})
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, _details in methods.items():
                if method.upper() == "GET" and path.startswith("/"):
                    endpoints.append(("GET", path))
    except (json.JSONDecodeError, ValueError):
        # Fallback: regex extraction from markdown/text spec
        for match in re.finditer(
            r"^\s*(GET)\s+(/\S+)", api_spec_text, re.MULTILINE | re.IGNORECASE
        ):
            endpoints.append(("GET", match.group(2)))

    for method, path in endpoints:
        check = check_api_ui_availability(method, path, base_url, timeout=timeout, headers=headers)
        if check.mismatch_kind in ("api_ok_ui_broken", "ui_ok_api_broken", "both_broken"):
            results.append(check)

    return results


def build_findings_from_checks(
    checks: list[UIApiAvailabilityCheck],
) -> list[dict[str, Any]]:
    """Convert UIApiAvailabilityCheck results to QualiBug finding dicts."""
    findings: list[dict[str, Any]] = []
    for check in checks:
        if check.mismatch_kind == "both_ok":
            continue
        title = (
            f"API可用但UI不可达: {check.api_method} {check.api_path}"
            if check.mismatch_kind == "api_ok_ui_broken"
            else f"UI可达但API不可用: {check.api_method} {check.api_path}"
            if check.mismatch_kind == "ui_ok_api_broken"
            else f"API与UI均不可达: {check.api_method} {check.api_path}"
        )
        findings.append({
            "hypothesis_id": f"P3-13-{check.api_path.replace('/', '_').strip('_')}",
            "title": title[:200],
            "severity": check.severity,
            "verdict": "confirmed" if check.mismatch_kind != "both_ok" else "falsified",
            "expected": f"{'UI' if check.mismatch_kind == 'api_ok_ui_broken' else 'API'} should be available",
            "actual": (
                f"API={check.api_status}, UI={check.ui_status}"
            ),
            "confidence": 0.85,
            "evidence": check.evidence,
            "category": "ui_api_availability",
            "mismatch_kind": check.mismatch_kind,
            "_api_path": check.api_path,
            "_api_method": check.api_method,
            "risk_type": "ui_api_mismatch",
            "repro_path": check.api_path,
            "repro_method": "GET",
        })
    return findings
