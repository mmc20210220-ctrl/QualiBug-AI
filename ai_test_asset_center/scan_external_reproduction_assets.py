"""External reproduction asset materialization for product scans.

Extracted from ``__main__``. Symbols are re-exported from ``__main__``
for compatibility with existing tests and callers.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from .product_scan_mainline import _as_dict, _first_text, _safe_project, _sha256


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    """Persist JSON only after unified recursive redaction + secret scan."""
    from .artifact_redactor import ArtifactSecretLeakError, write_json_redacted

    try:
        write_json_redacted(path, payload)
    except ArtifactSecretLeakError as exc:
        import sys as _sys

        print(
            f"[scan] FAILED_SAFE artifact secret scan blocked write to {path}: {exc}",
            file=_sys.stderr,
        )
        raise


def _write_markdown(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(text or ""), encoding="utf-8")

def _external_candidate_id(item: dict[str, Any], index: int = 0) -> str:
    value = str(item.get("candidate_id") or item.get("risk_id") or item.get("finding_id") or "").strip()
    if value:
        return value
    fingerprint = _sha256(
        f"{item.get('title') or ''}|{item.get('method') or item.get('_api_method') or ''}|{item.get('path') or item.get('_api_path') or ''}"
    )[:16]
    return f"EXT_{index or 0}_{fingerprint}"


def _external_reproduction_observation(item: dict[str, Any], *, candidate_id: str) -> dict[str, Any]:
    runtime_replay = _as_dict(item.get("runtime_replay"))
    raw_evidence = _as_dict(item.get("raw_evidence"))
    request_raw = _as_dict(raw_evidence.get("request_raw"))
    response_raw = _as_dict(raw_evidence.get("response_raw"))
    method = str(item.get("method") or item.get("_api_method") or runtime_replay.get("method") or request_raw.get("method") or "GET").upper().strip()
    path = str(item.get("path") or item.get("_api_path") or runtime_replay.get("path") or request_raw.get("path") or "/").strip() or "/"
    body = request_raw.get("body", item.get("request_body"))
    status_code = runtime_replay.get("http_status")
    if status_code is None:
        status_code = response_raw.get("status_code")
    body_binding = {}
    if body not in (None, "", [], {}):
        body_binding = {"bound": True, "source": "external_runtime_request_body"}
    response_payload = response_raw.get("body")
    if response_payload is None:
        response_payload = _as_dict(runtime_replay.get("trace")).get("body")
    verification = {
        "verdict": "validated_candidate",
        "reason": str(item.get("actual") or item.get("actual_behavior") or _as_dict(item.get("business_invariant_evaluation")).get("reason") or item.get("description") or "").strip(),
        "confidence": round(min(max(float(_as_dict(item.get("evidence_quality")).get("score") or item.get("confidence_score") or 88) / 100.0, 0.0), 0.99), 2),
    }
    response: dict[str, Any] = {}
    if status_code is not None:
        try:
            response["status_code"] = int(status_code)
        except Exception:
            pass
    if response_payload is not None:
        response["payload"] = response_payload
    obs = {
        "candidate_id": candidate_id,
        "risk_type": str(item.get("category") or "external_signal_violation").strip() or "external_signal_violation",
        "method": method,
        "path": path,
        "request": {"method": method, "path": path, "body": body},
        "response": response,
        "verification": verification,
        "responses": [],
        "fixture_receipts": [],
        "cleanup_receipts": [],
        "snapshots": {"before": [], "after": []},
    }
    if body_binding:
        obs["request"]["body_runtime_binding"] = body_binding
    if response:
        obs["responses"] = [{
            "attempt": 1,
            "step": 1,
            "method": method,
            "path": path,
            "status_code": response.get("status_code"),
            "payload": response.get("payload"),
            "runtime_binding": {"bound": True, "source": "external_runtime_response"},
            "request_body_runtime_binding": body_binding or {"bound": True, "source": "external_runtime_request"},
        }]
    return obs


def _render_external_repro_ps1(findings: list[dict[str, Any]]) -> str:
    lines = [
        "# QualiBug external validated candidate reproduction script",
        "$ErrorActionPreference = 'Stop'",
        'if (-not $env:BASE_URL) { throw "Please set BASE_URL before running this script." }',
        "",
    ]
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or item.get("_api_method") or "GET").upper().strip() or "GET"
        path = str(item.get("path") or item.get("_api_path") or "/").strip() or "/"
        body = _as_dict(_as_dict(item.get("raw_evidence")).get("request_raw")).get("body", item.get("request_body"))
        title = str(item.get("title") or "").replace("'", "''")
        lines.append(f"Write-Host 'Finding {index}: {title}'")
        lines.append(f"$targetUrl = \"$env:BASE_URL{path}\"")
        if body not in (None, "", [], {}):
            payload = json.dumps(body, ensure_ascii=False, default=str).replace("'", "''")
            lines.append(f"$payload = @'\n{payload}\n'@")
            lines.append(f"curl.exe -sS -X {method} \"$targetUrl\" -H \"Content-Type: application/json\" --data-raw $payload")
        else:
            lines.append(f"curl.exe -sS -X {method} \"$targetUrl\"")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _render_external_regression_pytest(project: str, findings: list[dict[str, Any]]) -> str:
    lines = [
        "from __future__ import annotations",
        "",
        "import os",
        "import requests",
        "",
        "",
        f'PROJECT_ID = {json.dumps(project, ensure_ascii=False)}',
        "",
        "",
        "def _base_url() -> str:",
        '    base = os.environ.get("BASE_URL", "").rstrip("/")',
        '    if not base:',
        '        raise AssertionError("BASE_URL environment variable is required")',
        "    return base",
        "",
    ]
    for index, item in enumerate(findings, start=1):
        if not isinstance(item, dict):
            continue
        method = str(item.get("method") or item.get("_api_method") or "GET").upper().strip() or "GET"
        path = str(item.get("path") or item.get("_api_path") or "/").strip() or "/"
        body = _as_dict(_as_dict(item.get("raw_evidence")).get("request_raw")).get("body", item.get("request_body"))
        expected_status = _as_dict(item.get("runtime_replay")).get("http_status")
        function_name = re.sub(r"[^A-Za-z0-9_]+", "_", f"test_external_repro_{index}_{method}_{path}").strip("_").lower() or f"test_external_repro_{index}"
        lines.extend([
            f"def {function_name}() -> None:",
            f"    url = _base_url() + {json.dumps(path, ensure_ascii=False)}",
        ])
        if body not in (None, "", [], {}):
            lines.append(f"    payload = {json.dumps(body, ensure_ascii=False, default=str)}")
            lines.append(f"    response = requests.request({json.dumps(method)}, url, json=payload, timeout=15)")
        else:
            lines.append(f"    response = requests.request({json.dumps(method)}, url, timeout=15)")
        if expected_status is not None:
            lines.append(f"    assert response.status_code == {int(expected_status)}")
        else:
            lines.append("    assert response.status_code >= 100")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _materialize_external_reproduction_assets(
    *,
    project: str,
    root: Path,
    scan_id: str,
    items: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    eligible: list[dict[str, Any]] = []
    packaged_rows: list[dict[str, Any]] = []
    observations: list[dict[str, Any]] = []
    report_findings: list[dict[str, Any]] = []
    ledger_entries: list[dict[str, Any]] = []
    for index, value in enumerate(items if isinstance(items, list) else [], start=1):
        if not isinstance(value, dict):
            continue
        row = dict(value)
        if str(row.get("confirmation_status") or "").strip().lower() != "validated_candidate":
            eligible.append(row)
            continue
        candidate_id = _external_candidate_id(row, index)
        row["candidate_id"] = candidate_id
        row.setdefault("finding_id", candidate_id)
        row.setdefault("confidence", float(row.get("confidence_score") or 0.88))
        row.setdefault("reason", str(row.get("actual") or row.get("actual_behavior") or _as_dict(row.get("business_invariant_evaluation")).get("reason") or row.get("description") or "").strip())
        obs = _external_reproduction_observation(row, candidate_id=candidate_id)
        observations.append(obs)
        ledger_entries.append({
            "candidate_id": candidate_id,
            "customer_ready": bool(obs.get("response")),
            "readiness_level": "customer_ready_candidate" if obs.get("response") else "validated_candidate_without_target_status",
            "fixture_setup": {"accepted_count": 0},
            "snapshots": {"accepted_count": 0},
            "cleanup": {"accepted_count": 0},
            "gap_types": [] if obs.get("response") else ["missing_target_http_status"],
            "verdict": "validated_candidate",
        })
        report_findings.append({
            "finding_id": row.get("finding_id"),
            "candidate_id": candidate_id,
            "title": row.get("title"),
            "risk_type": row.get("category") or "external_signal_violation",
            "method": row.get("method") or row.get("_api_method"),
            "path": row.get("path") or row.get("_api_path"),
            "confidence": row.get("confidence"),
            "evidence_grade": row.get("evidence_grade"),
            "evidence_strength_score": row.get("evidence_strength_score"),
            "reason": row.get("reason"),
            "violated_invariants": row.get("violated_invariants") or [],
            "delta_summary": row.get("delta_summary") or {},
            "source_refs": row.get("source_refs") or [],
            "customer_triage": row.get("customer_triage") or {},
            "evidence_package": row.get("evidence_package") or {},
        })
        packaged_rows.append(row)
        eligible.append(row)
    if not report_findings:
        return eligible, {
            "status": "empty",
            "finding_count": 0,
            "customer_ready_reproduction_count": 0,
        }
    output_dir = root / "platform_outputs" / _safe_project(project) / "defect_discovery"
    workspace_dir = root / "platform_workspace" / _safe_project(project) / "defect_discovery"
    generated_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    report = {
        "project_id": project,
        "created_at": generated_at,
        "scan_id": scan_id,
        "findings": report_findings,
        "write_observations": observations,
        "runtime_evidence_probe_ledger": {"entries": ledger_entries},
    }
    try:
        from .grounded_probe_executor import _build_runtime_customer_reproduction_pack, _render_runtime_customer_reproduction_pack_markdown
        from .runtime_reproduction_asset_linker import link_reproduction_assets
    except Exception as exc:
        return eligible, {"status": "failed", "reason": f"external_reproduction_asset_import_failed:{type(exc).__name__}"}
    pack = _build_runtime_customer_reproduction_pack(report)
    pack_json_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_runtime_customer_reproduction_pack.json"
    pack_md_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_runtime_customer_reproduction_pack.md"
    repro_ps1_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_validated_bug_repro.ps1"
    regression_pytest_ref = f"platform_workspace/{_safe_project(project)}/defect_discovery/external_validated_bug_regression_pytest.py"
    outputs = {
        "repro_ps1": str(output_dir / "external_validated_bug_repro.ps1"),
        "regression_pytest": str(output_dir / "external_validated_bug_regression_pytest.py"),
        "execution_report": str(output_dir / "external_runtime_customer_reproduction_pack.json"),
        "execution_report_md": str(output_dir / "external_runtime_customer_reproduction_pack.md"),
    }
    report["runtime_customer_reproduction_pack"] = pack
    report["outputs"] = outputs
    report = link_reproduction_assets(report)
    pack = report.get("runtime_customer_reproduction_pack") if isinstance(report.get("runtime_customer_reproduction_pack"), dict) else pack
    pack_json_path = workspace_dir / "external_runtime_customer_reproduction_pack.json"
    pack_md_path = workspace_dir / "external_runtime_customer_reproduction_pack.md"
    repro_ps1_path = workspace_dir / "external_validated_bug_repro.ps1"
    regression_pytest_path = workspace_dir / "external_validated_bug_regression_pytest.py"
    output_dir.mkdir(parents=True, exist_ok=True)
    workspace_dir.mkdir(parents=True, exist_ok=True)
    _write_json(pack_json_path, pack)
    _write_json(output_dir / "external_runtime_customer_reproduction_pack.json", pack)
    pack_md_text = _render_runtime_customer_reproduction_pack_markdown(pack)
    pack_md_path.write_text(pack_md_text, encoding="utf-8")
    (output_dir / "external_runtime_customer_reproduction_pack.md").write_text(pack_md_text, encoding="utf-8")
    repro_text = _render_external_repro_ps1(packaged_rows)
    repro_ps1_path.write_text(repro_text, encoding="utf-8")
    (output_dir / "external_validated_bug_repro.ps1").write_text(repro_text, encoding="utf-8")
    pytest_text = _render_external_regression_pytest(project, packaged_rows)
    regression_pytest_path.write_text(pytest_text, encoding="utf-8")
    (output_dir / "external_validated_bug_regression_pytest.py").write_text(pytest_text, encoding="utf-8")
    findings_by_id = {str(f.get("candidate_id") or ""): f for f in (report.get("findings") or []) if isinstance(f, dict)}
    for row in eligible:
        cid = str(row.get("candidate_id") or "")
        linked = findings_by_id.get(cid) or {}
        if isinstance(linked.get("evidence_package"), dict):
            row["evidence_package"] = linked["evidence_package"]
        if isinstance(linked.get("reproduction_artifact_links"), list):
            row["reproduction_artifact_links"] = linked["reproduction_artifact_links"]
    return eligible, {
        "status": "materialized",
        "generated_at_utc": generated_at,
        "finding_count": len(report_findings),
        "customer_ready_reproduction_count": int(pack.get("customer_ready_reproduction_count") or 0),
        "runtime_customer_reproduction_pack_ref": pack_json_ref,
        "runtime_customer_reproduction_pack_md_ref": pack_md_ref,
        "repro_ps1_ref": repro_ps1_ref,
        "regression_pytest_ref": regression_pytest_ref,
        "reproduction_artifact_index": report.get("reproduction_artifact_index") if isinstance(report.get("reproduction_artifact_index"), dict) else {},
        "runtime_customer_reproduction_pack": pack,
    }


from .scan_commercial_assets import (  # noqa: F401
    _external_priority,
    _write_markdown,
    _commercial_priority,
    _commercial_finding_customer_ready,
    _commercial_candidate_id,
    _commercial_finding_reason,
    _commercial_runtime_observation,
    _build_materialized_commercial_assets,
    _materialize_commercial_assets,
    _materialize_external_commercial_assets,
)


