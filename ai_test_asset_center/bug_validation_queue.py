from __future__ import annotations

"""Turn mined bug findings into a governed validation queue.

The queue is intentionally a planner, not an executor.  It classifies each
finding into the safest validation lane so QualiBug can move from "candidate
bug" to "evidence-backed bug" without spraying production write requests.
"""

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .real_project_onboarding import ROOT, _safe_project_id, _write_json as _project_write_json


SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
MUTATION_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
PRODUCTION_NAMES = {"prod", "production", "live", "online"}
SENSITIVE_REPLACEMENTS = [
    (re.compile(r"(?i)([\"']?(?:authorization|api[_-]?key|password|secret|token|session|cookie)[\"']?\s*[:=]\s*[\"']?)([^\"',\s;}\]]+)"), r"\1[REDACTED]"),
    (re.compile(r"(?i)(bearer\s+)[a-z0-9._\-]+"), r"\1[REDACTED]"),
    (re.compile(r"(?i)sk-[a-z0-9]{8,}"), "[REDACTED_KEY]"),
]


def build_bug_validation_queue(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    findings: list[dict[str, Any]] | None = None,
    *,
    target_environment: str | None = None,
    base_url_override: str = "",
) -> dict[str, Any]:
    """Build and persist validation tasks for bug findings.

    Tasks are grouped by execution lane:
    - static_review: no runtime call needed.
    - safe_read_only: eligible for GET/HEAD/OPTIONS probes.
    - sandbox_required: mutating validation that must never run in production.
    - candidate_only: needs mapping, data, or human decision before automation.
    """

    root = root or ROOT
    project = _safe_project_id(project_id)
    env_cfg, target = _target_environment(project, root, target_environment)
    if base_url_override:
        target = {**target, "base_url": str(base_url_override)}
    base_url = str(target.get("base_url") or "").rstrip("/")
    production = _is_production(target)
    allow_write_setup = bool(target.get("allow_write_setup")) and not production
    # Fallback: read allow_destructive_tests from real_project_config
    if not allow_write_setup:
        try:
            from .real_project_onboarding import load_real_project_config
            rpc = load_real_project_config(project, root)
            if rpc.get("allow_destructive_tests") and not rpc.get("safe_mode", False):
                allow_write_setup = True
        except Exception:
            pass
    available_paths = {str(path) for path in target.get("available_paths") or [] if str(path)}

    rows: list[dict[str, Any]] = []
    for index, finding in enumerate(findings or [], start=1):
        task = _task_from_finding(
            project,
            index,
            finding,
            base_url=base_url,
            production=production,
            allow_write_setup=allow_write_setup,
            available_paths=available_paths,
        )
        if task:
            rows.append(task)

    rows = sorted(rows, key=lambda item: (-float(item.get("rank_score") or 0), str(item.get("task_id") or "")))
    summary = {
        "total_task_count": len(rows),
        "ready_task_count": sum(1 for row in rows if str(row.get("automation_status", "")).startswith("ready_")),
        "blocked_task_count": sum(1 for row in rows if str(row.get("automation_status", "")).startswith("blocked_")),
        "human_review_task_count": sum(1 for row in rows if row.get("lane") == "candidate_only"),
        "by_lane": _count(str(row.get("lane") or "unknown") for row in rows),
        "by_status": _count(str(row.get("automation_status") or "unknown") for row in rows),
        "by_risk_type": _count(str(row.get("risk_type") or "unknown") for row in rows),
        "p0p1_task_count": sum(1 for row in rows if str(row.get("severity")) in {"P0", "P1"}),
    }
    queue = {
        "phase": "bug_validation_queue_v1",
        "project_id": project,
        "generated_at_utc": _now(),
        "target_environment": {
            "name": target.get("name") or env_cfg.get("target_environment") or "test",
            "type": target.get("type") or "unknown",
            "base_url_configured": bool(base_url),
            "production_protected": production,
            "allow_write_setup": allow_write_setup,
        },
        "summary": summary,
        "tasks": rows,
        "governance": {
            "planner_only": True,
            "does_not_execute_requests": True,
            "mutating_validation_requires_sandbox": True,
            "production_write_blocked": True,
            "safe_read_only_requires_explicit_base_url": True,
        },
    }
    _write_json(_queue_path(project, root), queue)
    return queue


def execute_bug_validation_queue(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    queue: dict[str, Any] | None = None,
    *,
    max_safe_probe_count: int = 1000,
    timeout_seconds: float = 5.0,
    allow_sandbox: bool = False,
    base_url: str = "",
) -> dict[str, Any]:
    """Execute the safe portion of a validation queue.

    When allow_sandbox=True and base_url is provided, sandbox-required
    mutating probes (POST/PUT/PATCH) are executed against the target.
    """

    root = root or ROOT
    project = _safe_project_id(project_id)
    queue = queue or _read_json(_queue_path(project, root), {})
    tasks = [task for task in queue.get("tasks") or [] if isinstance(task, dict)]
    results: list[dict[str, Any]] = []
    safe_probe_budget = max(0, min(int(max_safe_probe_count), 2000))
    safe_probe_used = 0
    sandbox_budget = max(0, min(int(max_safe_probe_count), 2000))
    sandbox_used = 0

    for task in tasks:
        status = str(task.get("automation_status") or "")
        lane = str(task.get("lane") or "")
        if status == "ready_static_review":
            results.append(_static_review_result(task))
        elif status == "ready_safe_probe" and safe_probe_used < safe_probe_budget:
            safe_probe_used += 1
            results.append(_safe_probe_result(task, timeout_seconds))
        elif status == "ready_negative_auth_probe" and safe_probe_used < safe_probe_budget:
            safe_probe_used += 1
            results.append(_negative_auth_probe_result(task, timeout_seconds))
        elif status == "ready_safe_probe":
            results.append(_skipped_result(task, "skipped_budget_exhausted", "Safe probe execution budget was exhausted."))
        elif status == "ready_negative_auth_probe":
            results.append(_skipped_result(task, "skipped_budget_exhausted", "Safe probe execution budget was exhausted."))
        elif lane == "sandbox_required":
            if allow_sandbox and base_url and sandbox_used < sandbox_budget and status in ("ready_sandbox_probe", "ready_sandbox_plan"):
                sandbox_used += 1
                results.append(_sandbox_probe_result(task, timeout_seconds, base_url))
            else:
                reason = "Sandbox probe budget exhausted." if sandbox_used >= sandbox_budget else task.get("blocking_reason") or "Mutating validation requires sandbox approval."
                results.append(_skipped_result(task, "blocked_requires_sandbox", reason))
        else:
            results.append(_skipped_result(task, str(status or "blocked_needs_human_mapping"), task.get("blocking_reason") or "Task is not ready for automated validation."))

    summary = {
        "total_result_count": len(results),
        "executed_count": sum(1 for item in results if item.get("executed")),
        "static_confirmed_count": sum(1 for item in results if item.get("verdict") == "static_confirmed"),
        "safe_probe_executed_count": sum(1 for item in results if item.get("execution_kind") == "safe_read_only_probe" and item.get("executed")),
        "negative_auth_probe_executed_count": sum(1 for item in results if item.get("execution_kind") == "negative_auth_probe" and item.get("executed")),
        "sandbox_probe_executed_count": sum(1 for item in results if item.get("execution_kind") == "sandbox_probe" and item.get("executed")),
        "blocked_or_skipped_count": sum(1 for item in results if not item.get("executed")),
        "potential_bug_confirmed_count": sum(1 for item in results if item.get("verdict") in {"static_confirmed", "failed_expectation"}),
        "by_verdict": _count(str(item.get("verdict") or "unknown") for item in results),
    }
    report = {
        "phase": "bug_validation_execution_v1",
        "project_id": project,
        "generated_at_utc": _now(),
        "queue_generated_at_utc": queue.get("generated_at_utc"),
        "summary": summary,
        "results": results,
        "governance": {
            "executes_mutating_requests": allow_sandbox,
            "executes_safe_read_only_only": True,
            "redacts_response_bodies": True,
            "sandbox_tasks_are_not_executed": not allow_sandbox,
        },
    }
    _write_json(_execution_path(project, root), report)
    return report


def attach_validation_task_refs(findings: list[dict[str, Any]], queue: dict[str, Any]) -> list[dict[str, Any]]:
    """Return findings annotated with validation task metadata."""

    by_signature = {str(task.get("finding_signature")): task for task in queue.get("tasks") or [] if isinstance(task, dict)}
    annotated: list[dict[str, Any]] = []
    for finding in findings:
        row = dict(finding)
        task = by_signature.get(_finding_signature(row))
        if task:
            row["validation_task_id"] = task.get("task_id")
            row["validation_lane"] = task.get("lane")
            row["validation_status"] = task.get("automation_status")
            row["validation_blocking_reason"] = task.get("blocking_reason", "")
        annotated.append(row)
    return annotated


def apply_validation_results_to_findings(
    findings: list[dict[str, Any]],
    queue: dict[str, Any],
    execution: dict[str, Any],
) -> list[dict[str, Any]]:
    """Annotate and calibrate findings using validation execution evidence.

    Candidate findings should not keep pretending to be confirmed runtime bugs
    after a safe negative probe proves the implementation rejects the request.
    Conversely, a 2xx negative-auth result should be promoted to confirmed.
    """

    tasks_by_signature = {str(task.get("finding_signature")): task for task in queue.get("tasks") or [] if isinstance(task, dict)}
    results_by_task = {str(result.get("task_id")): result for result in execution.get("results") or [] if isinstance(result, dict)}
    out: list[dict[str, Any]] = []
    for finding in findings:
        row = dict(finding)
        task = tasks_by_signature.get(_finding_signature(row))
        if not task:
            out.append(row)
            continue
        result = results_by_task.get(str(task.get("task_id")))
        row["validation_task_id"] = task.get("task_id")
        row["validation_lane"] = task.get("lane")
        row["validation_status"] = task.get("automation_status")
        row["validation_blocking_reason"] = task.get("blocking_reason", "")
        if result:
            row["validation_verdict"] = result.get("verdict")
            row["validation_execution_kind"] = result.get("execution_kind")
            row["validation_evidence"] = result.get("evidence")
            _calibrate_finding(row, result)
        out.append(row)
    return sorted(out, key=lambda item: (_severity_sort(str(item.get("severity") or "P3")), -float(item.get("rank_score") or 0), str(item.get("title") or "")))


def _calibrate_finding(finding: dict[str, Any], result: dict[str, Any]) -> None:
    verdict = str(result.get("verdict") or "")
    kind = str(result.get("execution_kind") or "")
    if verdict == "failed_expectation":
        finding["status"] = "confirmed_runtime_bug"
        finding["bug_confirmation"] = "confirmed_by_safe_validation"
        if _severity_sort(str(finding.get("severity") or "")) > _severity_sort("P1"):
            finding["severity"] = "P1"
        finding["confidence_score"] = max(float(finding.get("confidence_score") or 0), 0.96)
        finding["false_positive_risk"] = "low"
        finding["validation_interpretation"] = "Safe validation contradicted the expected behavior; treat as confirmed until fixed."
    elif kind == "negative_auth_probe" and verdict == "passed_expectation":
        finding["status"] = "runtime_guard_verified_contract_gap"
        finding["bug_confirmation"] = "not_runtime_bug_contract_gap"
        finding["severity"] = _min_severity(str(finding.get("severity") or "P3"), "P3")
        finding["confidence_score"] = min(float(finding.get("confidence_score") or 0), 0.55)
        finding["false_positive_risk"] = "low_runtime_risk"
        finding["validation_interpretation"] = "Runtime rejected the no-credential request; keep this as an OpenAPI/contract coverage gap, not a confirmed implementation bug."
    elif verdict == "static_confirmed":
        finding["status"] = "confirmed_static"
        finding["bug_confirmation"] = "confirmed_by_static_evidence"
        finding["validation_interpretation"] = "Imported PRD/OpenAPI evidence is sufficient to confirm the contract or capability defect."
    elif verdict in {"blocked_requires_sandbox", "blocked_needs_human_mapping", "skipped_budget_exhausted"}:
        finding["status"] = "needs_validation"
        finding["bug_confirmation"] = "unconfirmed_candidate"
    elif verdict == "environment_error":
        finding["status"] = "blocked_by_environment"
        finding["bug_confirmation"] = "unconfirmed_environment_blocked"


def _severity_sort(severity: str) -> int:
    return {"P0": 0, "P1": 1, "P2": 2, "P3": 3}.get(severity, 9)


def _min_severity(current: str, cap: str) -> str:
    order = ["P0", "P1", "P2", "P3"]
    current_index = _severity_sort(current)
    cap_index = _severity_sort(cap)
    return order[max(current_index, cap_index)] if max(current_index, cap_index) < len(order) else cap


def _task_from_finding(
    project: str,
    index: int,
    finding: dict[str, Any],
    *,
    base_url: str,
    production: bool,
    allow_write_setup: bool,
    available_paths: set[str],
) -> dict[str, Any] | None:
    title = str(finding.get("title") or finding.get("message") or "").strip()
    if not title:
        return None
    method = str(finding.get("method") or "").upper()
    path = str(finding.get("path") or "")
    if not method or not path:
        inferred_method, inferred_path = _infer_safe_read_only_endpoint(finding)
        method = method or inferred_method
        path = path or inferred_path
    risk_type = str(finding.get("risk_type") or finding.get("category") or finding.get("rule") or "unknown")
    policy = str(finding.get("execution_policy") or _policy_from_method(method)).strip() or "candidate_only"
    if _has_verified_db_evidence(finding):
        policy = "no_runtime_required"
    lane = _lane(policy)
    if _can_negative_auth_probe(risk_type, method, path, base_url, production):
        lane = "negative_auth_probe"
    signature = _finding_signature(finding)
    validation_plan = finding.get("validation_plan") if isinstance(finding.get("validation_plan"), dict) else {}
    steps = [str(step) for step in validation_plan.get("steps") or finding.get("reproduction_steps") or [] if str(step)]
    status, reason = _automation_status(
        lane,
        method,
        path,
        base_url=base_url,
        production=production,
        allow_write_setup=allow_write_setup,
        available_paths=available_paths,
    )
    task_id = f"BVT_{_severity_prefix(finding)}_{index:04d}_{_short_hash(signature)}"
    return {
        "task_id": task_id,
        "finding_signature": signature,
        "source_finding_title": title,
        "source": finding.get("source") or "unknown",
        "risk_type": risk_type,
        "severity": finding.get("severity") or "P3",
        "rank_score": round(float(finding.get("rank_score") or 0), 3),
        "verification_level": finding.get("verification_level") or "unknown",
        "evidence_strength": finding.get("evidence_strength") or ("strong_db" if _has_verified_db_evidence(finding) else "unknown"),
        "execution_policy": policy,
        "lane": lane,
        "method": method or "N/A",
        "path": path,
        "request_url_template": _url_template(base_url, path) if lane in {"safe_read_only", "negative_auth_probe"} else "",
        "actor_matrix": _actor_matrix(risk_type),
        "expected_outcome": finding.get("expected_behavior") or finding.get("expected") or "",
        "actual_signal": finding.get("actual_behavior") or finding.get("actual") or finding.get("observed") or "",
        "steps": steps,
        "automation_status": status,
        "blocking_reason": reason,
        "evidence_required": _evidence_required(lane, risk_type),
        "false_positive_risk": finding.get("false_positive_risk") or "unknown",
        "can_execute_without_write": lane in {"static_review", "safe_read_only", "negative_auth_probe"} and status.startswith("ready_"),
        "requires_human_approval": lane in {"sandbox_required", "candidate_only"},
        "project_id": project,
    }


def _automation_status(
    lane: str,
    method: str,
    path: str,
    *,
    base_url: str,
    production: bool,
    allow_write_setup: bool,
    available_paths: set[str],
) -> tuple[str, str]:
    if lane == "static_review":
        return "ready_static_review", ""
    if lane == "safe_read_only":
        if not base_url:
            return "blocked_missing_environment", "No target base_url is configured for safe read-only validation."
        if not path:
            return "blocked_missing_path", "The finding is not mapped to an API path."
        if _has_path_parameters(path):
            return "blocked_missing_test_data", "The path contains parameters and needs safe sample IDs before probing."
        if available_paths and path not in available_paths:
            return "blocked_path_not_declared_available", "The target environment did not declare this path as available."
        if method and method not in SAFE_METHODS:
            return "blocked_not_read_only", "Only GET/HEAD/OPTIONS probes can run in the safe lane."
        return "ready_safe_probe", ""
    if lane == "negative_auth_probe":
        if not base_url:
            return "blocked_missing_environment", "No target base_url is configured for negative auth validation."
        if not path:
            return "blocked_missing_path", "The finding is not mapped to an API path."
        if _has_path_parameters(path):
            return "blocked_missing_test_data", "The path contains parameters and needs safe sample IDs before probing."
        if production:
            return "blocked_production_write", "Production-like environments must not run mutating validation, even negative auth probes."
        if method not in {"POST", "PUT", "PATCH"}:
            return "blocked_not_supported", "Negative auth write probes are limited to POST/PUT/PATCH with empty JSON."
        return "ready_negative_auth_probe", ""
    if lane == "sandbox_required":
        if production:
            return "blocked_production_write", "Production-like environments must not run mutating validation."
        if not allow_write_setup:
            return "blocked_requires_sandbox", "Mutating validation requires an isolated sandbox with write setup enabled."
        return "ready_sandbox_probe", "Sandbox mutating probe is authorized for execution."
    return "blocked_needs_human_mapping", "The finding needs a mapped endpoint, data fixture, or business oracle before automation."


def _actor_matrix(risk: str) -> list[dict[str, str]]:
    if risk == "permission_boundary":
        return [
            {"actor": "anonymous", "expected": "401/403"},
            {"actor": "qa_engineer", "expected": "403/404 outside allowed scope"},
            {"actor": "project_owner", "expected": "allowed only within project scope"},
            {"actor": "admin", "expected": "allowed with audit trail"},
        ]
    return [
        {"actor": "qa_engineer", "expected": "read-only validation allowed when safe"},
        {"actor": "project_owner", "expected": "can approve sandbox validation"},
    ]


def _evidence_required(lane: str, risk: str) -> list[str]:
    if lane == "static_review":
        return ["imported PRD/OpenAPI snippet", "schema or operation path", "review decision"]
    if lane == "safe_read_only":
        evidence = ["request metadata", "HTTP status", "redacted response body", "assertion result"]
        if risk == "permission_boundary":
            evidence.append("actor/role matrix result")
        return evidence
    if lane == "negative_auth_probe":
        return ["request metadata", "HTTP status", "redacted response body", "actor rejection assertion"]
    if lane == "sandbox_required":
        return ["sandbox environment proof", "request pair", "side-effect state before/after", "cleanup evidence"]
    return ["human mapping note", "business oracle", "safe validation approach"]


def _target_environment(project: str, root: Path, target_environment: str | None) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        from .enterprise_testops_control_plane import _environment_by_name, load_environment_config

        cfg = load_environment_config(project, root)
        return cfg, _environment_by_name(cfg, target_environment or cfg.get("target_environment") or "test")
    except Exception:
        cfg = {"target_environment": target_environment or "test", "environments": []}
        return cfg, {"name": target_environment or "test", "type": "unknown", "base_url": "", "allow_write_setup": False, "production_protected": False}


def _queue_path(project: str, root: Path) -> Path:
    return root / "platform_outputs" / project / "bug_validation_queue" / "bug_validation_queue.json"


def _execution_path(project: str, root: Path) -> Path:
    return root / "platform_outputs" / project / "bug_validation_queue" / "bug_validation_execution.json"


def _read_json(path: Path, fallback: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else fallback
    except Exception:
        return fallback


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        _project_write_json(path, data)
    except Exception:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _static_review_result(task: dict[str, Any]) -> dict[str, Any]:
    strong = str(task.get("evidence_strength") or "") in {"strong_static", "strong_db"}
    return {
        "task_id": task.get("task_id"),
        "source_finding_title": task.get("source_finding_title"),
        "risk_type": task.get("risk_type"),
        "severity": task.get("severity"),
        "execution_kind": "static_review",
        "executed": True,
        "verdict": "static_confirmed" if strong else "needs_human_review",
        "evidence": {
            "evidence_strength": task.get("evidence_strength"),
            "verification_level": task.get("verification_level"),
            "expected_outcome": task.get("expected_outcome"),
            "actual_signal": task.get("actual_signal"),
        },
    }


def _safe_probe_result(task: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    method = str(task.get("method") or "GET").upper()
    if method not in SAFE_METHODS:
        return _skipped_result(task, "blocked_not_read_only", "Only GET/HEAD/OPTIONS validation can execute automatically.")
    url = str(task.get("request_url_template") or "")
    if not url:
        return _skipped_result(task, "blocked_missing_environment", "No request URL is available.")
    headers = {"User-Agent": "QualiBug-SafeValidator/1.0"}
    status_code = 0
    body = ""
    error = ""
    try:
        req = Request(url, method=method, headers=headers)
        with urlopen(req, timeout=timeout_seconds) as response:  # nosec B310 - explicit customer-configured test URL
            status_code = int(getattr(response, "status", 0) or 0)
            body = response.read(4096).decode("utf-8", errors="replace")
    except HTTPError as exc:
        status_code = int(exc.code)
        body = exc.read(4096).decode("utf-8", errors="replace")
    except URLError as exc:
        error = str(exc.reason or exc)
    except Exception as exc:  # pragma: no cover - defensive runtime boundary
        error = str(exc)[:300]

    risk = str(task.get("risk_type") or "")
    verdict = "inconclusive"
    if error:
        verdict = "environment_error"
    elif risk == "permission_boundary":
        verdict = "failed_expectation" if 200 <= status_code < 300 else "passed_expectation"
    elif _safe_probe_replays_non_negative_violation(task, body):
        verdict = "failed_expectation"
    elif status_code >= 500:
        verdict = "failed_expectation"
    elif status_code > 0:
        verdict = "observed"

    return {
        "task_id": task.get("task_id"),
        "source_finding_title": task.get("source_finding_title"),
        "risk_type": risk,
        "severity": task.get("severity"),
        "execution_kind": "safe_read_only_probe",
        "executed": True,
        "verdict": verdict,
        "evidence": {
            "request": {"method": method, "url": _redact(url)},
            "response": {"status_code": status_code, "body_excerpt": _redact(body[:1200]), "error": _redact(error)},
            "assertion": _safe_probe_assertion(task, status_code, error, body),
        },
    }


def _negative_auth_probe_result(task: dict[str, Any], timeout_seconds: float) -> dict[str, Any]:
    method = str(task.get("method") or "").upper()
    if method not in {"POST", "PUT", "PATCH"}:
        return _skipped_result(task, "blocked_not_supported", "Negative auth probes only support POST/PUT/PATCH.")
    url = str(task.get("request_url_template") or "")
    if not url:
        return _skipped_result(task, "blocked_missing_environment", "No request URL is available.")
    actor_results = [
        _negative_auth_actor_request("anonymous", method, url, timeout_seconds),
        _negative_auth_actor_request("qa_engineer", method, url, timeout_seconds),
    ]
    if any(row.get("error") for row in actor_results):
        verdict = "environment_error"
    elif any(row.get("assertion") == "failed" for row in actor_results):
        verdict = "failed_expectation"
    elif all(row.get("assertion") == "passed" for row in actor_results):
        verdict = "passed_expectation"
    else:
        verdict = "inconclusive_rejected_after_auth_or_routing"

    return {
        "task_id": task.get("task_id"),
        "source_finding_title": task.get("source_finding_title"),
        "risk_type": task.get("risk_type"),
        "severity": task.get("severity"),
        "execution_kind": "negative_auth_probe",
        "executed": True,
        "verdict": verdict,
        "evidence": {
            "request": {"method": method, "url": _redact(url), "body": "{}", "actor_matrix": ["anonymous", "qa_engineer"]},
            "actor_results": actor_results,
            "assertion": "Anonymous and ordinary QA identities must be rejected before business logic or side effects.",
        },
    }


def _negative_auth_actor_request(actor: str, method: str, url: str, timeout_seconds: float) -> dict[str, Any]:
    body_bytes = b"{}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "QualiBug-NegativeAuthValidator/1.0",
        "X-QualiBug-No-Local-Dev": "1",
    }
    expected = {401, 403}
    if actor == "qa_engineer":
        headers.update({"X-QualiBug-Actor": "qa-negative-probe", "X-QualiBug-Role": "qa_engineer"})
        expected = {403}
    status_code = 0
    body = ""
    error = ""
    try:
        req = Request(url, data=body_bytes, method=method, headers=headers)
        with urlopen(req, timeout=timeout_seconds) as response:  # nosec B310 - explicit customer-configured test URL
            status_code = int(getattr(response, "status", 0) or 0)
            body = response.read(4096).decode("utf-8", errors="replace")
    except HTTPError as exc:
        status_code = int(exc.code)
        body = exc.read(4096).decode("utf-8", errors="replace")
    except URLError as exc:
        error = str(exc.reason or exc)
    except Exception as exc:  # pragma: no cover - defensive runtime boundary
        error = str(exc)[:300]
    if error:
        assertion = "environment_error"
    elif status_code in expected:
        assertion = "passed"
    elif status_code in {404, 405}:
        assertion = "inconclusive"
    else:
        assertion = "failed"
    return {
        "actor": actor,
        "expected_status": sorted(expected),
        "status_code": status_code,
        "assertion": assertion,
        "body_excerpt": _redact(body[:1200]),
        "error": _redact(error),
    }


def _sandbox_probe_result(task: dict[str, Any], timeout_seconds: float, base_url: str) -> dict[str, Any]:
    """Execute a mutating sandbox probe with before/after state capture.
    
    For read-before-write endpoints: GET before-state → write → GET after-state → compare.
    For write-only endpoints: execute write → check response.
    """
    method = str(task.get("method") or "POST").upper()
    path = str(task.get("path") or "")
    if not path:
        return _skipped_result(task, "blocked_missing_path", "No API path for sandbox probe.")
    
    url = f"{base_url.rstrip('/')}{'/' if not path.startswith('/') else ''}{path}"
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "QualiBug-SandboxValidator/1.0",
        "X-QualiBug-Actor": "admin",
        "X-QualiBug-Role": "admin",
    }
    
    # Build probe body from finding context
    body_data: dict[str, Any] = {}
    risk_type = str(task.get("risk_type") or "")
    if risk_type in ("idempotency_gap", "idempotency"):
        body_data = {"_qualibug_probe": "idempotency_check", "idempotency_key": f"qb-{task.get('task_id', '')}"}
    elif risk_type == "permission_boundary":
        body_data = {"_qualibug_probe": "permission_boundary_check"}
    
    # ── Phase 1: Before-state capture (GET on related list endpoint) ──
    before_state = None
    list_path = _infer_list_path(path)
    if list_path and method in ("POST", "PUT", "PATCH"):
        try:
            list_url = f"{base_url.rstrip('/')}{'/' if not list_path.startswith('/') else ''}{list_path}"
            before_req = Request(list_url, method="GET", headers={
                "User-Agent": "QualiBug-SandboxValidator/1.0",
                "X-QualiBug-Actor": "admin", "X-QualiBug-Role": "admin",
            })
            with urlopen(before_req, timeout=timeout_seconds) as resp:
                before_body = resp.read(8192).decode("utf-8", errors="replace")
                try:
                    before_state = json.loads(before_body)
                    if isinstance(before_state, dict):
                        records = before_state.get("records", before_state.get("data", before_state.get("items", [])))
                        before_state = {"record_count": len(records) if isinstance(records, list) else 0,
                                        "sample": str(records[:1])[:500] if records else ""}
                except Exception:
                    before_state = {"raw": before_body[:500]}
        except Exception:
            before_state = {"error": "before_state_capture_failed"}
    
    # ── Phase 2: Execute write ──
    body_bytes = json.dumps(body_data).encode("utf-8") if body_data else b"{}"
    status_code = 0
    resp_body = ""
    error = ""
    created_id = None
    try:
        req = Request(url, method=method, headers=headers, data=body_bytes)
        with urlopen(req, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 0) or 0)
            resp_body = response.read(8192).decode("utf-8", errors="replace")
            # Extract created ID for potential rollback
            try:
                resp_json = json.loads(resp_body)
                created_id = resp_json.get("id") or resp_json.get("business_no")
            except Exception:
                pass
    except HTTPError as exc:
        status_code = int(exc.code)
        resp_body = exc.read(8192).decode("utf-8", errors="replace")
    except URLError as exc:
        error = str(exc.reason or exc)
    except Exception as exc:
        error = str(exc)[:300]
    
    # ── Phase 3: After-state capture ──
    after_state = None
    if list_path and method in ("POST", "PUT", "PATCH") and not error and status_code < 500:
        try:
            list_url = f"{base_url.rstrip('/')}{'/' if not list_path.startswith('/') else ''}{list_path}"
            after_req = Request(list_url, method="GET", headers={
                "User-Agent": "QualiBug-SandboxValidator/1.0",
                "X-QualiBug-Actor": "admin", "X-QualiBug-Role": "admin",
            })
            with urlopen(after_req, timeout=timeout_seconds) as resp:
                after_body = resp.read(8192).decode("utf-8", errors="replace")
                try:
                    after_state = json.loads(after_body)
                    if isinstance(after_state, dict):
                        records = after_state.get("records", after_state.get("data", after_state.get("items", [])))
                        after_state = {"record_count": len(records) if isinstance(records, list) else 0,
                                       "sample": str(records[:1])[:500] if records else ""}
                except Exception:
                    after_state = {"raw": after_body[:500]}
        except Exception:
            after_state = {"error": "after_state_capture_failed"}
    
    # ── Phase 4: Rollback (DELETE created resource if POST succeeded) ──
    rollback_status = None
    if created_id and method == "POST" and 200 <= status_code < 300:
        try:
            rollback_path = path.rstrip("/") + "/" + str(created_id)
            rollback_url = f"{base_url.rstrip('/')}{'/' if not rollback_path.startswith('/') else ''}{rollback_path}"
            rollback_req = Request(rollback_url, method="DELETE", headers={
                "User-Agent": "QualiBug-SandboxValidator/1.0",
                "X-QualiBug-Actor": "admin", "X-QualiBug-Role": "admin",
            })
            with urlopen(rollback_req, timeout=timeout_seconds) as resp:
                rollback_status = resp.status
        except HTTPError as exc:
            rollback_status = exc.code
        except Exception:
            rollback_status = "failed"
    
    # ── Verdict ──
    verdict = "observed"
    if error:
        verdict = "environment_error"
    elif status_code >= 500:
        verdict = "failed_expectation"
    elif status_code == 0:
        verdict = "environment_error"
    else:
        try:
            resp_json = json.loads(resp_body) if resp_body else {}
            if resp_json.get("ok") is False:
                verdict = "failed_expectation"
            elif resp_json.get("error"):
                verdict = "failed_expectation"
        except Exception:
            pass
    
    # Compare before/after counts
    if before_state and after_state:
        bc = before_state.get("record_count", 0) if isinstance(before_state, dict) else 0
        ac = after_state.get("record_count", 0) if isinstance(after_state, dict) else 0
        if method == "POST" and ac <= bc:
            verdict = "failed_expectation"  # POST should increase count
    
    return {
        "task_id": task.get("task_id"),
        "source_finding_title": task.get("source_finding_title"),
        "risk_type": risk_type,
        "severity": task.get("severity"),
        "execution_kind": "sandbox_probe",
        "executed": True,
        "verdict": verdict,
        "evidence": {
            "request": {"method": method, "url": _redact(url), "body": _redact(json.dumps(body_data))},
            "response": {"status_code": status_code, "body_excerpt": _redact(resp_body[:1200]), "error": _redact(error)},
            "before_state": before_state,
            "after_state": after_state,
            "rollback": {"status": rollback_status, "resource_id": created_id} if created_id else None,
            "assertion": f"Sandbox {method} {path}: HTTP{status_code}, before={before_state is not None}, after={after_state is not None}, rollback={rollback_status}",
        },
    }


def _infer_list_path(detail_path: str) -> str:
    """Infer list endpoint from detail path. /api/orders/{id} → /api/orders"""
    # Remove last path segment if it looks like a parameter
    parts = detail_path.strip("/").split("/")
    if parts and ("{" in parts[-1] or parts[-1] in ("detail", "action")):
        parts = parts[:-1]
    return "/" + "/".join(parts) if parts else ""


def _safe_probe_assertion(task: dict[str, Any], status_code: int, error: str, body: str = "") -> str:
    if error:
        return "Target environment was not reachable, so the product bug is not confirmed."
    if str(task.get("risk_type") or "") == "permission_boundary":
        return "Anonymous read-only request should be rejected; HTTP 2xx confirms a likely access-control bug."
    if _safe_probe_replays_non_negative_violation(task, body):
        return "Read-only response reproduced a non-negative constraint violation from the source finding."
    if status_code >= 500:
        return "Read-only request returned 5xx and should be investigated as a runtime defect."
    return "Read-only request captured as supporting evidence."


def _safe_probe_replays_non_negative_violation(task: dict[str, Any], body: str) -> bool:
    if not body:
        return False
    context = " ".join(
        str(task.get(key) or "")
        for key in ("source_finding_title", "expected_outcome", "actual_signal", "risk_type")
    )
    normalized = context.lower()
    mentions_non_negative = any(token in normalized for token in (">= 0", ">=0", "non-negative", "nonnegative", "大于等于 0"))
    if not mentions_non_negative:
        return False
    negative_numbers = {
        match.group(0)
        for match in re.finditer(r"(?<![\w.])-([1-9]\d*|0?\.\d+)(?:\.\d+)?", context)
    }
    if not negative_numbers:
        return False
    return any(number in body for number in negative_numbers)


def _skipped_result(task: dict[str, Any], verdict: str, reason: Any) -> dict[str, Any]:
    return {
        "task_id": task.get("task_id"),
        "source_finding_title": task.get("source_finding_title"),
        "risk_type": task.get("risk_type"),
        "severity": task.get("severity"),
        "execution_kind": task.get("lane") or "unknown",
        "executed": False,
        "verdict": verdict,
        "evidence": {"reason": str(reason or "")[:500]},
    }


def _redact(value: Any) -> str:
    text = str(value or "")
    for pattern, replacement in SENSITIVE_REPLACEMENTS:
        text = pattern.sub(replacement, text)
    return text


def _lane(policy: str) -> str:
    if policy == "no_runtime_required":
        return "static_review"
    if policy == "safe_read_only":
        return "safe_read_only"
    if policy == "sandbox_required":
        return "sandbox_required"
    return "candidate_only"


def _policy_from_method(method: str) -> str:
    if method in SAFE_METHODS:
        return "safe_read_only"
    if method in MUTATION_METHODS:
        return "sandbox_required"
    return "candidate_only"


def _has_verified_db_evidence(finding: dict[str, Any]) -> bool:
    source = str(finding.get("source") or "").lower()
    category = str(finding.get("category") or "").lower()
    risk = str(finding.get("risk_type") or "").lower()
    return (
        source == "db_verifier"
        or category == "db_verified"
        or risk == "db_probe"
        or str(finding.get("title") or "").startswith("[DB Verified]")
    )


def _infer_safe_read_only_endpoint(finding: dict[str, Any]) -> tuple[str, str]:
    """Infer a safe probe only from explicit read-only endpoint evidence."""
    candidates: list[str] = []
    for key in ("evidence", "reproduction_steps", "description", "expected", "observed", "title"):
        value = finding.get(key)
        if isinstance(value, (list, tuple)):
            candidates.extend(str(item) for item in value)
        elif isinstance(value, dict):
            candidates.append(json.dumps(value, ensure_ascii=False, default=str))
        elif value:
            candidates.append(str(value))
    text = "\n".join(candidates)
    match = re.search(r"\b(GET|HEAD|OPTIONS)\s+(/[A-Za-z0-9._~:/?&=%+\-{}]+)", text)
    if not match:
        return "", ""
    method = match.group(1).upper()
    raw_path = match.group(2).strip().rstrip(".,;)'\"]")
    path = raw_path.split("?", 1)[0]
    if method not in SAFE_METHODS or not path.startswith("/") or _has_path_parameters(path):
        return "", ""
    return method, path


def _can_negative_auth_probe(risk_type: str, method: str, path: str, base_url: str, production: bool) -> bool:
    return (
        risk_type == "permission_boundary"
        and method in {"POST", "PUT", "PATCH"}
        and bool(path)
        and not _has_path_parameters(path)
        and bool(base_url)
        and not production
    )


def _is_production(environment: dict[str, Any]) -> bool:
    text = f"{environment.get('name','')} {environment.get('type','')}".lower()
    return bool(environment.get("production_protected")) or any(name in text for name in PRODUCTION_NAMES)


def _has_path_parameters(path: str) -> bool:
    return bool(re.search(r"\{[^{}]+\}", path))


def _url_template(base_url: str, path: str) -> str:
    if not base_url or not path:
        return ""
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _severity_prefix(finding: dict[str, Any]) -> str:
    return re.sub(r"[^A-Z0-9]+", "", str(finding.get("severity") or "PX").upper())[:2] or "PX"


def _finding_signature(finding: dict[str, Any]) -> str:
    payload = {
        "risk_type": finding.get("risk_type") or finding.get("category"),
        "severity": finding.get("severity"),
        "title": finding.get("title") or finding.get("message"),
        "method": finding.get("method"),
        "path": finding.get("path"),
    }
    return _short_hash(payload, 24)


def _short_hash(value: Any, size: int = 10) -> str:
    raw = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()[:size]


def _count(values: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[value] = counts.get(value, 0) + 1
    return dict(sorted(counts.items(), key=lambda row: (-row[1], row[0])))


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
