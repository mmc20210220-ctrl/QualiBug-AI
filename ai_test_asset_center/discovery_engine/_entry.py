"""Entry points: run_discovery, run_generic_probes."""
from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any
from ._engine import AutonomousDiscoveryEngine  # noqa: F401

logger = logging.getLogger(__name__)

from ._common import *  # noqa: F401,F403
from ._budget import *  # noqa: F401,F403
from ._engine import *  # noqa: F401,F403



def run_discovery(prd_path: str = None, api_path: str = None,
                  base_url: str | None = None) -> dict:
    """便捷入口"""
    project_id = str(os.environ.get("QUALIBUG_DEFAULT_PROJECT_ID") or "default_project").strip() or "default_project"
    repo_root = Path(__file__).resolve().parents[1]

    def _resolve_input_path(candidates: list[str]) -> str:
        search_dirs = [
            repo_root / "platform_workspace" / project_id / "input",
            repo_root / "platform_inputs" / project_id,
            repo_root / "projects" / project_id / "input",
            repo_root / "input",
        ]
        for directory in search_dirs:
            if not directory.exists() or not directory.is_dir():
                continue
            for candidate in candidates:
                path = directory / candidate
                if path.exists() and path.is_file():
                    return str(path)
        return ""

    if prd_path is None:
        prd_path = _resolve_input_path(["PRD.md", "prd.md", "requirements.md", "business_requirements.md"])
    if api_path is None:
        api_path = _resolve_input_path(["openapi.json", "openapi.yaml", "openapi.yml", "API_SPEC.md", "API.md"])

    prd = Path(prd_path).read_text(encoding="utf-8") if prd_path and Path(prd_path).is_file() else ""
    api = Path(api_path).read_text(encoding="utf-8") if api_path and Path(api_path).is_file() else ""

    engine = AutonomousDiscoveryEngine(base_url=base_url, project_id=project_id, root=repo_root)
    return engine.discover(prd, api)


if __name__ == "__main__":
    result = run_discovery()
    print(f"\n{'='*60}")
    print(json.dumps(result, indent=2, ensure_ascii=False, default=str))


# ── Generic Runtime Probe Executor ────────────────────────

def run_generic_probes(
    *,
    project: str = "",
    base_url: str = "",
    api_spec_text: str = "",
    probe_plan: list[dict[str, Any]] | None = None,
    max_probes: int = 50,
) -> dict[str, Any]:
    """Execute runtime API probes against the target system.
    
    Uses adaptive probe templates when available, falling back to basic
    endpoint probing from the OpenAPI spec.
    """
    import urllib.request, urllib.error

    findings: list[dict[str, Any]] = []
    total = 0
    confirmed = 0
    falsified = 0
    blocked = 0

    # Use probe plan if available, otherwise build basic endpoint probes
    probes = probe_plan if probe_plan else _build_basic_probes(api_spec_text)
    probes = probes[:max_probes]

    for probe in probes:
        total += 1
        method = str(probe.get("method", "GET"))
        path = str(probe.get("path", "/"))
        url = base_url.rstrip("/") + "/" + path.lstrip("/")
        expected_status = probe.get("expected_status", 200)
        data = probe.get("body")

        try:
            req = urllib.request.Request(url, method=method)
            req.add_header("Accept", "application/json")
            req.add_header("Content-Type", "application/json")
            # Pass role/actor headers if specified
            actor = probe.get("actor", "")
            if actor and actor != "anonymous":
                req.add_header("X-Role", actor)
            if data:
                req.data = json.dumps(data).encode("utf-8")

            with urllib.request.urlopen(req, timeout=10) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                body = resp.read().decode("utf-8", errors="replace")
                try:
                    body_json = json.loads(body)
                except Exception:
                    body_json = {"raw": body[:500]}

        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = dict(e.headers) if e.headers else {}
            try:
                body_json = json.loads(e.read().decode("utf-8", errors="replace"))
            except Exception:
                body_json = {"error": str(e)[:200]}

        except Exception as e:
            blocked += 1
            findings.append({
                "oracle_id": probe.get("id", f"P{total}"),
                "verdict": "blocked",
                "expected": str(probe.get("expected", "")),
                "actual": str(e)[:200],
            })
            continue

        verdict = "confirmed" if status != expected_status else "falsified"
        if verdict == "confirmed":
            confirmed += 1
            # Extract trace ID from response
            from ai_test_asset_center.behavior_semantic_mapper import extract_trace_id
            trace_id = extract_trace_id(resp_headers, body_json)

            findings.append({
                "severity": probe.get("severity", "P2"),
                "title": f"[Runtime Probe] {method} {path}: expected {expected_status}, got {status}",
                "category": probe.get("risk_type", "runtime_probe"),
                "description": json.dumps(body_json, ensure_ascii=False)[:300],
                "confidence_score": 0.85,
                "source": "runtime_probe",
                "method": method,
                "path": path,
                "validation_evidence": {
                    "response_headers": {k: str(v) for k, v in resp_headers.items()},
                    "response_body": body_json,
                    "http_status": status,
                    "expected_status": expected_status,
                },
                "evidence_hint": f"Trace ID: {trace_id}" if trace_id else "",
            })
        else:
            falsified += 1

    return {
        "summary": {"total_probes": total, "confirmed": confirmed, "falsified": falsified, "blocked": blocked},
        "findings": findings,
        "confirmed_findings": [f for f in findings if f.get("verdict") != "blocked"],
    }


def _build_basic_probes(api_spec_text: str) -> list[dict[str, Any]]:
    """Build basic endpoint probes from OpenAPI spec as fallback."""
    probes = []
    try:
        spec = json.loads(api_spec_text)
        paths = spec.get("paths", {})
        for path, methods in paths.items():
            for method in ["get", "post", "put", "delete", "patch"]:
                if method in methods:
                    probes.append({
                        "id": f"{method.upper()}-{path}",
                        "method": method.upper(),
                        "path": path,
                        "expected_status": 200 if method == "get" else 201,
                        "severity": "P2",
                        "risk_type": "contract_validation",
                    })
    except Exception:
        pass
    return probes
