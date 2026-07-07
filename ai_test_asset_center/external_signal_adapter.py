"""Generic external signal adapter with explicit provider boundaries.

This adapter accepts results produced by external tools and normalizes them
into a single QualiBug-facing contract. It never fabricates execution success:
if a report is missing or unreadable, the request is blocked or failed.
"""
from __future__ import annotations

import importlib.util
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


_SUPPORTED_PROVIDERS = {
    "external_report",
    "schemathesis",
    "restler",
    "open_telemetry",
    "data_diff",
    "debezium",
    "soda_core",
}


def _safe_id(value: Any, default: str) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or default


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _safe_project(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return text or "unscoped"


def normalize_external_signal_requests(value: Any) -> list[dict[str, Any]]:
    requests: list[dict[str, Any]] = []
    for index, item in enumerate(_as_list(value), start=1):
        raw = _as_dict(item)
        provider = str(raw.get("provider") or "external_report").strip().lower()
        if provider not in _SUPPORTED_PROVIDERS:
            provider = "external_report"
        metadata = _as_dict(raw.get("metadata"))
        execution_mode = str(raw.get("execution_mode") or "").strip()
        if not execution_mode:
            execution_mode = "import_only"
        requests.append({
            "request_id": _safe_id(raw.get("request_id") or raw.get("id"), f"external_signal_{index}"),
            "title": str(raw.get("title") or raw.get("task") or provider).strip()[:200],
            "provider": provider,
            "signal_type": str(raw.get("signal_type") or provider).strip()[:100],
            "execution_mode": execution_mode,
            "report_path": str(raw.get("report_path") or metadata.get("report_path") or "").strip(),
            "db_diff_report_path": str(raw.get("db_diff_report_path") or metadata.get("db_diff_report_path") or "").strip(),
            "invariant_report_path": str(raw.get("invariant_report_path") or metadata.get("invariant_report_path") or "").strip(),
            "report_format": str(raw.get("report_format") or metadata.get("report_format") or "json").strip().lower(),
            "schema_path": str(raw.get("schema_path") or metadata.get("schema_path") or "").strip(),
            "base_url": str(raw.get("base_url") or metadata.get("base_url") or "").strip(),
            "max_failures": raw.get("max_failures") if raw.get("max_failures") is not None else metadata.get("max_failures"),
            "wait_for_schema": raw.get("wait_for_schema") if raw.get("wait_for_schema") is not None else metadata.get("wait_for_schema"),
            "success_criteria": _as_dict(raw.get("success_criteria")),
            "metadata": metadata,
        })
    return requests


def _blocked_request_result(request: dict[str, Any], reason: str) -> dict[str, Any]:
    return {
        "request_id": request.get("request_id", ""),
        "title": request.get("title", ""),
        "provider": request.get("provider", ""),
        "signal_type": request.get("signal_type", ""),
        "status": "blocked",
        "reason": reason,
        "execution_status": "not_executed",
        "confirmation_status": "blocked",
        "artifact_dir": "",
        "artifacts": [],
        "findings": [],
        "duration_ms": 0,
    }


def _severity(value: Any) -> str:
    text = str(value or "").strip().upper()
    if text in {"P0", "P1", "P2", "P3"}:
        return text
    return "P2"


def _confidence(value: Any, default: float = 0.5) -> float:
    try:
        score = float(value)
    except Exception:
        score = default
    return max(0.0, min(1.0, score))


def _resolve_report_path(path_text: str, *, root: Path) -> Path:
    path = Path(path_text)
    if path.is_absolute():
        return path
    return root / path


def _load_json_report(request: dict[str, Any], *, root: Path) -> tuple[dict[str, Any] | list[Any], str]:
    report_path = str(request.get("report_path") or "").strip()
    if not report_path:
        raise FileNotFoundError("EXTERNAL_SIGNAL_REPORT_MISSING")
    path = _resolve_report_path(report_path, root=root)
    if not path.exists():
        raise FileNotFoundError("EXTERNAL_SIGNAL_REPORT_NOT_FOUND")
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def _load_json_report_path(path_text: str, *, root: Path) -> tuple[dict[str, Any] | list[Any], str]:
    path = _resolve_report_path(path_text, root=root)
    if not path.exists():
        raise FileNotFoundError("DATA_DIFF_REPORT_NOT_FOUND")
    return json.loads(path.read_text(encoding="utf-8")), str(path)


def _finding_items(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("findings", "candidate_findings", "signals", "issues", "alerts"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_finding(
    request: dict[str, Any],
    finding: dict[str, Any],
    *,
    report_ref: str,
) -> dict[str, Any]:
    provider = str(request.get("provider") or "external_report")
    evidence = _as_dict(finding.get("evidence"))
    evidence.setdefault("provider", provider)
    evidence.setdefault("report_ref", report_ref)
    trace_id = str(finding.get("trace_id") or evidence.get("trace_id") or "").strip()
    if trace_id:
        evidence["trace_id"] = trace_id
    return {
        "severity": _severity(finding.get("severity") or finding.get("priority")),
        "title": str(finding.get("title") or finding.get("name") or request.get("title") or provider).strip()[:200],
        "category": str(finding.get("category") or "external_signal").strip()[:100],
        "source": str(finding.get("source") or f"external_signal:{provider}"),
        "method": str(finding.get("method") or "").upper().strip(),
        "path": str(finding.get("path") or "").strip(),
        "request_body": finding.get("request_body"),
        "description": str(
            finding.get("description")
            or finding.get("message")
            or finding.get("reason")
            or f"{provider} imported signal"
        ).strip()[:1000],
        "confidence_score": _confidence(
            finding.get("confidence_score") or finding.get("confidence") or finding.get("tool_confidence"),
            0.5,
        ),
        "execution_status": "imported",
        "confirmation_status": str(finding.get("confirmation_status") or "candidate"),
        "external_signal_provider": provider,
        "external_signal_request_id": request.get("request_id", ""),
        "evidence": evidence,
        "before_after_snapshot": _as_dict(finding.get("before_after_snapshot")),
        "business_invariant_evaluation": _as_dict(finding.get("business_invariant_evaluation")),
        "db_evidence": _as_dict(finding.get("db_evidence")),
        "failed_fields": [str(item) for item in _as_list(finding.get("failed_fields")) if str(item).strip()],
    }


def _first_snapshot(value: Any) -> dict[str, Any]:
    if isinstance(value, list) and value and isinstance(value[0], dict):
        return dict(value[0])
    if isinstance(value, dict):
        return dict(value)
    return {}


def _business_operation_for_finding(finding: dict[str, Any]) -> str:
    method = str(finding.get("method") or "").upper().strip()
    path = str(finding.get("path") or "").strip()
    if method and path:
        return f"{method} {path}".strip()
    return ""


def _invariant_result_items(payload: dict[str, Any] | list[Any]) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("results", "checks", "violations", "items", "findings"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
    return []


def _normalize_invariant_evaluation(payload: dict[str, Any] | list[Any], *, provider: str, report_ref: str) -> tuple[dict[str, Any], list[str]]:
    items = _invariant_result_items(payload)
    failed_items: list[dict[str, Any]] = []
    normalized: list[dict[str, Any]] = []
    failed_fields: list[str] = []
    for item in items:
        verdict_text = str(item.get("verdict") or item.get("status") or item.get("outcome") or "").strip().lower()
        failed = verdict_text in {"failed", "fail", "violation", "violated", "error"}
        passed = verdict_text in {"passed", "pass", "ok", "success"}
        if failed:
            normalized_verdict = "failed"
        elif passed:
            normalized_verdict = "passed"
        else:
            normalized_verdict = "inconclusive"
        row_failed_fields = [str(x) for x in _as_list(item.get("failed_fields") or item.get("fields")) if str(x).strip()]
        failed_fields.extend(row_failed_fields)
        normalized_item = {
            "kind": str(item.get("kind") or item.get("rule") or item.get("check") or "business_invariant").strip(),
            "name": str(item.get("name") or item.get("title") or item.get("rule") or "invariant").strip(),
            "verdict": normalized_verdict,
            "reason": str(item.get("reason") or item.get("message") or item.get("detail") or "").strip(),
            "failed_fields": row_failed_fields,
        }
        if item.get("expected") is not None:
            normalized_item["expected"] = item.get("expected")
        if item.get("actual") is not None:
            normalized_item["actual"] = item.get("actual")
        normalized.append(normalized_item)
        if failed:
            failed_items.append(normalized_item)
    if failed_items:
        reason = "; ".join(filter(None, [str(item.get("reason") or item.get("name") or "").strip() for item in failed_items[:3]]))
        evaluation = {
            "verdict": "failed",
            "reason": reason or f"{provider} reported invariant violations",
            "confidence": 0.9,
            "checked_count": len(normalized),
            "results": normalized,
            "report_ref": report_ref,
        }
        return evaluation, list(dict.fromkeys(failed_fields))[:30]
    if normalized:
        evaluation = {
            "verdict": "passed",
            "reason": f"{provider} checks passed",
            "confidence": 0.72,
            "checked_count": len(normalized),
            "results": normalized,
            "report_ref": report_ref,
        }
        return evaluation, []
    return {
        "verdict": "inconclusive",
        "reason": f"{provider} invariant report contained no actionable results",
        "confidence": 0.35,
        "checked_count": 0,
        "results": [],
        "report_ref": report_ref,
    }, []


def _db_evidence_from_data_diff_payload(payload: dict[str, Any], *, business_operation: str = "") -> dict[str, Any] | None:
    diffs = [item for item in _as_list(payload.get("diffs")) if isinstance(item, dict)]
    anomalies = [item for item in diffs if item.get("added_rows") or item.get("removed_rows") or item.get("modified_rows")]
    if not anomalies:
        return None
    first_diff = anomalies[0]
    before = _first_snapshot(payload.get("before_snapshots") or payload.get("before") or payload.get("before_db_snapshot"))
    after = _first_snapshot(payload.get("after_snapshots") or payload.get("after") or payload.get("after_db_snapshot"))
    return {
        "before_db_snapshot": before,
        "after_db_snapshot": after,
        "db_assertion": str(first_diff.get("detail") or first_diff.get("assertion") or "数据库前后快照存在差异"),
        "business_operation": business_operation,
        "table": str(first_diff.get("table") or payload.get("table") or ""),
    }


def _data_diff_findings(payload: dict[str, Any], request: dict[str, Any], *, report_ref: str) -> list[dict[str, Any]]:
    diffs = [item for item in _as_list(payload.get("diffs")) if isinstance(item, dict)]
    anomalies = [item for item in diffs if item.get("added_rows") or item.get("removed_rows") or item.get("modified_rows")]
    findings: list[dict[str, Any]] = []
    for index, diff in enumerate(anomalies, start=1):
        business_operation = str(payload.get("business_operation") or request.get("title") or request.get("signal_type") or "").strip()
        db_evidence = {
            "before_db_snapshot": _first_snapshot(payload.get("before_snapshots") or payload.get("before") or payload.get("before_db_snapshot")),
            "after_db_snapshot": _first_snapshot(payload.get("after_snapshots") or payload.get("after") or payload.get("after_db_snapshot")),
            "db_assertion": str(diff.get("detail") or diff.get("assertion") or "数据库前后快照存在差异"),
            "business_operation": business_operation,
            "table": str(diff.get("table") or payload.get("table") or ""),
        }
        findings.append(_normalize_finding(request, {
            "title": str(diff.get("title") or f"DB diff anomaly #{index}").strip(),
            "severity": diff.get("severity") or "P1",
            "category": "data_integrity",
            "source": "external_signal:data_diff",
            "description": str(diff.get("detail") or diff.get("assertion") or "database side-effect delta observed").strip(),
            "confidence_score": diff.get("confidence_score") or 0.84,
            "confirmation_status": "candidate",
            "db_evidence": db_evidence,
            "evidence": {"provider": "data_diff", "report_ref": report_ref},
        }, report_ref=report_ref))
    return findings


def _bool_env_installed(module_name: str) -> bool:
    return importlib.util.find_spec(module_name) is not None


def _artifact_root(root: Path, project_id: str, run_id: str, request_id: str) -> Path:
    return (
        root
        / "platform_workspace"
        / _safe_project(project_id)
        / "defect_discovery"
        / "external_signals"
        / _safe_id(run_id, "external_signal_run")
        / _safe_id(request_id, "external_signal_request")
    )


def _source_content_from_context(execution_context: dict[str, Any] | None, *, root: Path) -> str:
    context = _as_dict(execution_context)
    explicit = str(context.get("api_spec_text") or "").strip()
    if explicit:
        return explicit
    manifest = _as_dict(context.get("source_manifest"))
    if not manifest:
        return ""
    try:
        from .private_pilot_scan_context_contract import load_source_content_from_manifest

        return str(load_source_content_from_manifest(str(context.get("project") or ""), root, manifest) or "")
    except Exception:
        return ""


def _materialize_schema_file(
    request: dict[str, Any],
    *,
    root: Path,
    artifact_dir: Path,
    execution_context: dict[str, Any] | None,
) -> str:
    schema_path = str(request.get("schema_path") or "").strip()
    if schema_path:
        return str(_resolve_report_path(schema_path, root=root))
    content = _source_content_from_context(execution_context, root=root)
    if not content:
        return ""
    suffix = ".json" if content.lstrip().startswith(("{", "[")) else ".yaml"
    materialized = artifact_dir / f"schemathesis_schema{suffix}"
    materialized.write_text(content, encoding="utf-8")
    return str(materialized)


def _junit_findings(report_path: str, request: dict[str, Any]) -> list[dict[str, Any]]:
    path = Path(report_path)
    if not path.exists():
        return []
    root = ET.fromstring(path.read_text(encoding="utf-8"))
    findings: list[dict[str, Any]] = []
    for testcase in root.iter("testcase"):
        failure = testcase.find("failure")
        error = testcase.find("error")
        issue = failure if failure is not None else error
        if issue is None:
            continue
        name = str(testcase.attrib.get("name") or request.get("title") or "schemathesis").strip()
        classname = str(testcase.attrib.get("classname") or "").strip()
        title = name if not classname else f"{classname}: {name}"
        method = ""
        path = ""
        match = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+([^\s:]+)", f"{classname} {name}")
        if match:
            method = str(match.group(1) or "").upper()
            path = str(match.group(2) or "")
        findings.append({
            "severity": "P1",
            "title": title[:200],
            "category": "api_contract_fuzz",
            "source": "external_signal:schemathesis",
            "description": str(issue.attrib.get("message") or issue.text or "Schemathesis failure").strip()[:2000],
            "confidence_score": 0.78,
            "confirmation_status": "candidate",
            "method": method,
            "path": path,
            "evidence": {
                "tool": "schemathesis",
                "junit_report": report_path,
                "testcase": name,
                "classname": classname,
            },
        })
    return findings


def _schemathesis_request_result(
    project_id: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
    execution_context: dict[str, Any] | None,
) -> dict[str, Any]:
    if str(request.get("report_path") or "").strip():
        return _import_report_request_result(request, root=root)
    if not _bool_env_installed("schemathesis"):
        return _blocked_request_result(request, "SCHEMATHESIS_NOT_INSTALLED")
    approved_base_url = str(request.get("base_url") or runtime_contract.get("approved_base_url") or "").strip()
    if not approved_base_url:
        return _blocked_request_result(request, "SCHEMATHESIS_BASE_URL_MISSING")
    artifact_dir = _artifact_root(root, project_id, run_id, str(request.get("request_id") or "schemathesis"))
    artifact_dir.mkdir(parents=True, exist_ok=True)
    schema_file = _materialize_schema_file(request, root=root, artifact_dir=artifact_dir, execution_context=execution_context)
    if not schema_file:
        return _blocked_request_result(request, "SCHEMATHESIS_SCHEMA_MISSING")
    junit_path = artifact_dir / "schemathesis.junit.xml"
    stdout_path = artifact_dir / "schemathesis.stdout.txt"
    stderr_path = artifact_dir / "schemathesis.stderr.txt"
    command = [
        sys.executable,
        "-m",
        "schemathesis",
        "run",
        schema_file,
        "--url",
        approved_base_url,
        "--report",
        "junit",
        "--report-junit-path",
        str(junit_path),
    ]
    max_failures = request.get("max_failures")
    if max_failures is not None:
        try:
            failures = int(max_failures)
        except Exception:
            failures = 0
        if failures > 0:
            command.extend(["--max-failures", str(failures)])
    wait_for_schema = request.get("wait_for_schema")
    if wait_for_schema is not None:
        try:
            wait_seconds = int(wait_for_schema)
        except Exception:
            wait_seconds = 0
        if wait_seconds > 0:
            command.extend(["--wait-for-schema", str(wait_seconds)])
    started = time.time()
    completed = subprocess.run(
        command,
        cwd=str(root),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stdout_path.write_text(completed.stdout or "", encoding="utf-8")
    stderr_path.write_text(completed.stderr or "", encoding="utf-8")
    findings = [
        _normalize_finding(request, item, report_ref=str(junit_path))
        for item in _junit_findings(str(junit_path), request)
    ]
    status = "imported"
    reason = ""
    execution_status = "completed"
    confirmation_status = "candidate" if findings else "no_findings"
    if completed.returncode != 0 and not junit_path.exists():
        status = "failed"
        execution_status = "failed"
        confirmation_status = "failed"
        reason = f"SCHEMATHESIS_RUN_FAILED:{completed.returncode}"
    return {
        "request_id": request.get("request_id", ""),
        "title": request.get("title", ""),
        "provider": "schemathesis",
        "signal_type": request.get("signal_type", ""),
        "status": status,
        "reason": reason,
        "execution_status": execution_status,
        "confirmation_status": confirmation_status,
        "artifact_dir": str(artifact_dir),
        "artifacts": [
            {"artifact_type": "schemathesis_junit", "ref": str(junit_path)},
            {"artifact_type": "schemathesis_stdout", "ref": str(stdout_path)},
            {"artifact_type": "schemathesis_stderr", "ref": str(stderr_path)},
            {"artifact_type": "schemathesis_schema", "ref": schema_file},
        ],
        "findings": findings,
        "duration_ms": int((time.time() - started) * 1000),
        "command": command,
        "exit_code": int(completed.returncode),
    }


def _data_diff_request_result(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
    report_path = str(request.get("report_path") or request.get("db_diff_report_path") or "").strip()
    if not report_path:
        return _blocked_request_result(request, "DATA_DIFF_REPORT_MISSING")
    started = time.time()
    try:
        payload, report_ref = _load_json_report_path(report_path, root=root)
    except FileNotFoundError as exc:
        return _blocked_request_result(request, str(exc))
    except Exception as exc:
        return {
            **_blocked_request_result(request, f"DATA_DIFF_REPORT_PARSE_FAILED:{type(exc).__name__}"),
            "status": "failed",
            "execution_status": "failed",
            "confirmation_status": "failed",
        }
    if not isinstance(payload, dict):
        payload = {}
    findings = _data_diff_findings(payload, request, report_ref=report_ref)
    return {
        "request_id": request.get("request_id", ""),
        "title": request.get("title", ""),
        "provider": "data_diff",
        "signal_type": request.get("signal_type", ""),
        "status": "imported",
        "reason": "",
        "execution_status": "completed",
        "confirmation_status": "candidate" if findings else "no_findings",
        "artifact_dir": str(Path(report_ref).parent),
        "artifacts": [{"artifact_type": "data_diff_report", "ref": report_ref}],
        "findings": findings,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _invariant_request_result(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
    report_path = str(request.get("report_path") or request.get("invariant_report_path") or "").strip()
    if not report_path:
        return _blocked_request_result(request, "INVARIANT_REPORT_MISSING")
    started = time.time()
    try:
        payload, report_ref = _load_json_report_path(report_path, root=root)
    except FileNotFoundError as exc:
        return _blocked_request_result(request, str(exc))
    except Exception as exc:
        return {
            **_blocked_request_result(request, f"INVARIANT_REPORT_PARSE_FAILED:{type(exc).__name__}"),
            "status": "failed",
            "execution_status": "failed",
            "confirmation_status": "failed",
        }
    invariant_eval, failed_fields = _normalize_invariant_evaluation(payload, provider=str(request.get("provider") or "soda_core"), report_ref=report_ref)
    findings: list[dict[str, Any]] = []
    if invariant_eval.get("verdict") == "failed":
        findings.append(_normalize_finding(request, {
            "title": str(request.get("title") or "business invariant violation").strip(),
            "severity": "P1",
            "category": "business_invariant",
            "source": f"external_signal:{str(request.get('provider') or 'soda_core')}",
            "description": str(invariant_eval.get("reason") or "business invariant failed").strip(),
            "confidence_score": float(invariant_eval.get("confidence") or 0.9),
            "confirmation_status": "candidate",
            "business_invariant_evaluation": invariant_eval,
            "failed_fields": failed_fields,
        }, report_ref=report_ref))
    return {
        "request_id": request.get("request_id", ""),
        "title": request.get("title", ""),
        "provider": request.get("provider", ""),
        "signal_type": request.get("signal_type", ""),
        "status": "imported",
        "reason": "",
        "execution_status": "completed",
        "confirmation_status": "candidate" if findings else "no_findings",
        "artifact_dir": str(Path(report_ref).parent),
        "artifacts": [{"artifact_type": "invariant_report", "ref": report_ref}],
        "findings": findings,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _import_report_request_result(request: dict[str, Any], *, root: Path) -> dict[str, Any]:
    started = time.time()
    report_format = str(request.get("report_format") or "json").strip().lower()
    if report_format != "json":
        return _blocked_request_result(request, f"EXTERNAL_SIGNAL_REPORT_FORMAT_UNSUPPORTED:{report_format}")
    try:
        payload, report_ref = _load_json_report(request, root=root)
    except FileNotFoundError as exc:
        return _blocked_request_result(request, str(exc))
    except Exception as exc:
        return {
            **_blocked_request_result(request, f"EXTERNAL_SIGNAL_REPORT_PARSE_FAILED:{type(exc).__name__}"),
            "status": "failed",
            "execution_status": "failed",
            "confirmation_status": "failed",
        }
    findings = [_normalize_finding(request, item, report_ref=report_ref) for item in _finding_items(payload)]
    return {
        "request_id": request.get("request_id", ""),
        "title": request.get("title", ""),
        "provider": request.get("provider", ""),
        "signal_type": request.get("signal_type", ""),
        "status": "imported",
        "reason": "",
        "execution_status": "completed",
        "confirmation_status": "candidate" if findings else "no_findings",
        "artifact_dir": str(Path(report_ref).parent),
        "artifacts": [{"artifact_type": "external_signal_report", "ref": report_ref}],
        "findings": findings,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _json_or_text(raw: str) -> Any:
    try:
        return json.loads(raw)
    except Exception:
        return raw[:5000]


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _redact(item) for key, item in list(value.items())[:50]}
    if isinstance(value, list):
        return [_redact(item) for item in value[:25]]
    text = str(value)
    return text[:1000] + "..." if len(text) > 1000 else value


def _trace_before_after_snapshot(trace: dict[str, Any]) -> dict[str, Any]:
    steps = trace.get("steps") if isinstance(trace.get("steps"), list) else []
    runtime_steps = [step for step in steps if isinstance(step, dict) and isinstance(step.get("response"), dict)]
    if not runtime_steps:
        return {}

    def _snapshot(step: dict[str, Any]) -> dict[str, Any]:
        response = step.get("response") if isinstance(step.get("response"), dict) else {}
        return {
            "method": str(step.get("method") or "").upper(),
            "path": str(step.get("path") or ""),
            "status_code": int(response.get("status_code") or step.get("status") or 0),
            "body": response.get("body"),
        }

    before_step = runtime_steps[0]
    after_step = runtime_steps[-1]
    return {"before": _snapshot(before_step), "after": _snapshot(after_step)}


def _finding_request_target(finding: dict[str, Any]) -> tuple[str, str]:
    method = str(finding.get("method") or "").upper().strip()
    path = str(finding.get("path") or "").strip()
    evidence = _as_dict(finding.get("evidence"))
    if method and path.startswith("/"):
        return method, path
    request_text = str(evidence.get("request") or evidence.get("testcase") or finding.get("title") or "").strip()
    match = re.search(r"\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\s+([^\s:]+)", request_text)
    if match:
        return str(match.group(1) or "").upper(), str(match.group(2) or "")
    return "", ""


def _runtime_replay_request_result(
    request: dict[str, Any],
    finding: dict[str, Any],
    *,
    approved_base_url: str,
) -> dict[str, Any]:
    method, path = _finding_request_target(finding)
    if not method or not path.startswith("/"):
        return {
            "status": "blocked",
            "reason": "RUNTIME_REPLAY_TARGET_MISSING",
            "method": method,
            "path": path,
        }
    evidence = _as_dict(finding.get("evidence"))
    body = finding.get("request_body")
    if body is None:
        body = evidence.get("request_body")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8") if body not in (None, "", [], {}) and method not in {"GET", "HEAD"} else None
    headers = {"Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    started = time.time()
    url = approved_base_url.rstrip("/") + path
    trace: dict[str, Any] = {"steps": [], "errors": []}
    try:
        req = urllib.request.Request(url, method=method, data=data, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            raw = response.read(300_000).decode("utf-8", errors="replace")
            status = int(response.status)
            response_body = _json_or_text(raw)
            response_headers = dict(response.headers.items())
    except urllib.error.HTTPError as exc:
        raw = exc.read(300_000).decode("utf-8", errors="replace") if exc.fp else ""
        status = int(exc.code)
        response_body = _json_or_text(raw)
        response_headers = dict(exc.headers.items()) if exc.headers else {}
    except Exception as exc:
        return {
            "status": "failed",
            "reason": f"RUNTIME_REPLAY_FAILED:{type(exc).__name__}",
            "method": method,
            "path": path,
            "duration_ms": int((time.time() - started) * 1000),
        }
    trace["steps"].append({
        "method": method,
        "path": path,
        "status": status,
        "response": {"status_code": status, "headers": _redact(response_headers), "body": _redact(response_body)},
    })
    before_after_snapshot = _trace_before_after_snapshot(trace)
    if status >= 500:
        replay_outcome = "server_error_observed"
    elif status >= 400:
        replay_outcome = "client_or_guardrail_rejection"
    else:
        replay_outcome = "request_completed"
    return {
        "status": "executed",
        "reason": replay_outcome,
        "method": method,
        "path": path,
        "http_status": status,
        "before_after_snapshot": before_after_snapshot,
        "trace": trace,
        "duration_ms": int((time.time() - started) * 1000),
    }


def _apply_runtime_replay(
    request: dict[str, Any],
    result: dict[str, Any],
    runtime_contract: dict[str, Any],
) -> dict[str, Any]:
    execution_mode = str(request.get("execution_mode") or "import_only").strip().lower()
    if execution_mode not in {"runtime_replay", "runtime_replay_with_evidence"}:
        return {"status": "not_requested", "requested": 0, "replayed": 0, "failed": 0, "blocked": 0, "results": []}
    approved_base_url = str(request.get("base_url") or runtime_contract.get("approved_base_url") or "").strip()
    findings = [item for item in _as_list(result.get("findings")) if isinstance(item, dict)]
    if not approved_base_url or not findings:
        return {"status": "blocked", "requested": len(findings), "replayed": 0, "failed": 0, "blocked": len(findings), "results": []}
    replay_results: list[dict[str, Any]] = []
    replayed = 0
    failed = 0
    blocked = 0
    for finding in findings:
        replay = _runtime_replay_request_result(request, finding, approved_base_url=approved_base_url)
        finding["runtime_replay"] = replay
        evidence = _as_dict(finding.get("evidence"))
        evidence["runtime_replay"] = {
            "status": replay.get("status"),
            "reason": replay.get("reason"),
            "http_status": replay.get("http_status"),
            "target": f"{replay.get('method', '')} {replay.get('path', '')}".strip(),
        }
        finding["evidence"] = evidence
        snapshot = _as_dict(replay.get("before_after_snapshot"))
        if snapshot and not _as_dict(finding.get("before_after_snapshot")):
            finding["before_after_snapshot"] = snapshot
        status = str(replay.get("status") or "")
        if status == "executed":
            replayed += 1
        elif status == "failed":
            failed += 1
        else:
            blocked += 1
        replay_results.append(replay)
    overall = "completed"
    if blocked and not replayed and not failed:
        overall = "blocked"
    elif failed and replayed:
        overall = "partial"
    elif failed and not replayed:
        overall = "failed"
    elif replayed and blocked:
        overall = "partial"
    return {
        "status": overall,
        "requested": len(findings),
        "replayed": replayed,
        "failed": failed,
        "blocked": blocked,
        "results": replay_results,
    }


def _attach_db_evidence_bridge(request: dict[str, Any], result: dict[str, Any], *, root: Path) -> dict[str, Any]:
    report_path = str(request.get("db_diff_report_path") or "").strip()
    findings = [item for item in _as_list(result.get("findings")) if isinstance(item, dict)]
    if not report_path or not findings:
        return {"status": "not_requested", "attached": 0, "blocked": 0, "reason": ""}
    try:
        payload, report_ref = _load_json_report_path(report_path, root=root)
    except FileNotFoundError as exc:
        return {"status": "blocked", "attached": 0, "blocked": len(findings), "reason": str(exc)}
    except Exception as exc:
        return {"status": "failed", "attached": 0, "blocked": len(findings), "reason": f"DATA_DIFF_REPORT_PARSE_FAILED:{type(exc).__name__}"}
    payload_dict = payload if isinstance(payload, dict) else {}
    attached = 0
    for finding in findings:
        if _as_dict(finding.get("db_evidence")):
            continue
        db_evidence = _db_evidence_from_data_diff_payload(
            payload_dict,
            business_operation=_business_operation_for_finding(finding),
        )
        if not db_evidence:
            continue
        finding["db_evidence"] = db_evidence
        evidence = _as_dict(finding.get("evidence"))
        evidence["db_diff_report"] = report_ref
        finding["evidence"] = evidence
        attached += 1
    return {"status": "completed" if attached else "blocked", "attached": attached, "blocked": len(findings) - attached, "reason": ""}


def _attach_invariant_bridge(request: dict[str, Any], result: dict[str, Any], *, root: Path) -> dict[str, Any]:
    report_path = str(request.get("invariant_report_path") or "").strip()
    findings = [item for item in _as_list(result.get("findings")) if isinstance(item, dict)]
    if not report_path or not findings:
        return {"status": "not_requested", "attached": 0, "blocked": 0, "reason": ""}
    try:
        payload, report_ref = _load_json_report_path(report_path, root=root)
    except FileNotFoundError as exc:
        return {"status": "blocked", "attached": 0, "blocked": len(findings), "reason": str(exc)}
    except Exception as exc:
        return {"status": "failed", "attached": 0, "blocked": len(findings), "reason": f"INVARIANT_REPORT_PARSE_FAILED:{type(exc).__name__}"}
    invariant_eval, failed_fields = _normalize_invariant_evaluation(payload, provider=str(request.get("provider") or "soda_core"), report_ref=report_ref)
    if invariant_eval.get("verdict") == "inconclusive":
        return {"status": "blocked", "attached": 0, "blocked": len(findings), "reason": "INVARIANT_REPORT_NOT_ACTIONABLE"}
    attached = 0
    for finding in findings:
        if _as_dict(finding.get("business_invariant_evaluation")):
            continue
        finding["business_invariant_evaluation"] = invariant_eval
        if failed_fields and not _as_list(finding.get("failed_fields")):
            finding["failed_fields"] = failed_fields
        evidence = _as_dict(finding.get("evidence"))
        evidence["invariant_report"] = report_ref
        finding["evidence"] = evidence
        attached += 1
    return {"status": "completed" if attached else "blocked", "attached": attached, "blocked": len(findings) - attached, "reason": ""}


def execute_external_signal_requests(
    project_id: str,
    requests: Any,
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str = "",
    execution_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    normalized = normalize_external_signal_requests(requests)
    if not normalized:
        return {
            "status": "not_requested",
            "requested": 0,
            "imported": 0,
            "failed": 0,
            "blocked": 0,
            "provider_distribution": {},
            "results": [],
            "findings": [],
            "artifacts": [],
            "duration_ms": 0,
        }
    started = time.time()
    provider_distribution: dict[str, int] = {}
    results: list[dict[str, Any]] = []
    artifacts: list[dict[str, Any]] = []
    imported = 0
    failed = 0
    blocked = 0
    replayed = 0
    replay_failed = 0
    replay_blocked = 0
    for request in normalized:
        provider = str(request.get("provider") or "external_report")
        provider_distribution[provider] = provider_distribution.get(provider, 0) + 1
        if provider == "schemathesis":
            result = _schemathesis_request_result(
                project_id,
                request,
                runtime_contract,
                root=root,
                run_id=run_id,
                execution_context=execution_context,
            )
        elif provider == "data_diff":
            result = _data_diff_request_result(request, root=root)
        elif provider == "soda_core":
            result = _invariant_request_result(request, root=root)
        else:
            result = _import_report_request_result(request, root=root)
        replay_summary = _apply_runtime_replay(request, result, runtime_contract)
        result["runtime_replay"] = replay_summary
        db_evidence_bridge = _attach_db_evidence_bridge(request, result, root=root)
        result["db_evidence_bridge"] = db_evidence_bridge
        invariant_bridge = _attach_invariant_bridge(request, result, root=root)
        result["invariant_bridge"] = invariant_bridge
        replayed += int(replay_summary.get("replayed") or 0)
        replay_failed += int(replay_summary.get("failed") or 0)
        replay_blocked += int(replay_summary.get("blocked") or 0)
        status = str(result.get("status") or "")
        if status == "imported":
            imported += 1
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
                "artifact_type": str(_as_dict(artifact).get("artifact_type") or "external_signal_report"),
                "ref": ref,
            })
        results.append(result)
    overall_status = "completed"
    if blocked and not imported and not failed:
        overall_status = "blocked"
    elif failed and imported:
        overall_status = "partial"
    elif failed and not imported:
        overall_status = "failed"
    elif imported and blocked:
        overall_status = "partial"
    return {
        "status": overall_status,
        "requested": len(normalized),
        "imported": imported,
        "failed": failed,
        "blocked": blocked,
        "provider_distribution": provider_distribution,
        "results": results,
        "findings": [item for result in results for item in _as_list(_as_dict(result).get("findings")) if isinstance(item, dict)],
        "artifacts": artifacts,
        "runtime_replay_summary": {
            "replayed": replayed,
            "failed": replay_failed,
            "blocked": replay_blocked,
        },
        "duration_ms": int((time.time() - started) * 1000),
    }
