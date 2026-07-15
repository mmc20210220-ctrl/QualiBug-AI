from __future__ import annotations

"""Explicit disposable-sandbox validation for concurrent/idempotent operations.

This module is intentionally narrower than a general load-testing tool.  It
runs only when a project explicitly declares a disposable ``sandbox`` target,
allows destructive tests, supplies an approval id and requests execution at
call time.  Otherwise it emits a non-executing plan.

The goal is to turn a high-value class of enterprise incidents into evidence:
multiple retries of the *same logical request* create more than one durable
side effect, or an async terminal task exposes no required result artifact.
"""

import hashlib
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

from .real_project_onboarding import (
    ROOT,
    _join_url,
    _load_json,
    _safe_project_id,
    _write_json,
    config_paths,
    load_real_project_config,
)
from .safety_boundary import safety_gate

WRITE_METHODS = {"POST", "PUT", "PATCH"}
SENSITIVE_KEY_RE = re.compile(r"token|secret|password|authorization|cookie|api[_-]?key|session", re.I)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 20) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _paths(project_id: str, root: Path) -> dict[str, Path]:
    project = _safe_project_id(project_id)
    cfg = config_paths(project, root)
    workspace = root / "platform_workspace" / project / "concurrency_async_sandbox"
    output = root / "platform_outputs" / project / "concurrency_async_sandbox"
    return {**cfg, "workspace": workspace, "output": output, "plan": workspace / "concurrency_async_sandbox_plan.json", "run": workspace / "concurrency_async_sandbox_run.json"}


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): "<REDACTED>" if SENSITIVE_KEY_RE.search(str(key)) else _redact(item) for key, item in list(value.items())[:80]}
    if isinstance(value, list):
        return [_redact(item) for item in value[:50]]
    if isinstance(value, str):
        value = re.sub(r"(?i)(bearer\s+)[A-Za-z0-9._\-+/=]{8,}", r"\1<REDACTED>", value)
        return value[:1200]
    return value


def _section(cfg: dict[str, Any]) -> dict[str, Any]:
    raw = cfg.get("concurrency_async_sandbox") or cfg.get("concurrency_sandbox") or {}
    return raw if isinstance(raw, dict) else {}


def _contract_rows(cfg: dict[str, Any]) -> list[dict[str, Any]]:
    rows = _section(cfg).get("contracts") or _section(cfg).get("concurrency_contracts") or []
    return [dict(row) for row in rows if isinstance(row, dict)]


def _field(payload: Any, dotted: str) -> Any:
    current = payload
    for part in str(dotted or "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _substitute_run_key(value: Any, run_key: str) -> Any:
    if isinstance(value, dict):
        return {str(k): _substitute_run_key(v, run_key) for k, v in value.items()}
    if isinstance(value, list):
        return [_substitute_run_key(item, run_key) for item in value]
    if isinstance(value, str):
        return value.replace("{run_key}", run_key)
    return value


def _http(url: str, method: str, *, body: Any | None = None, headers: dict[str, Any] | None = None, timeout: int = 8) -> dict[str, Any]:
    safe_headers = {"Accept": "application/json"}
    for key, value in (headers or {}).items():
        if value is not None:
            safe_headers[str(key)] = str(value)
    raw = None
    if body is not None:
        raw = json.dumps(body, ensure_ascii=False).encode("utf-8")
        safe_headers.setdefault("Content-Type", "application/json")
    try:
        req = urllib.request.Request(url, data=raw, method=method, headers=safe_headers)
        with urllib.request.urlopen(req, timeout=max(1, min(int(timeout), 15))) as response:
            text = response.read(300_000).decode("utf-8", errors="replace")
            try:
                payload = json.loads(text) if text.strip() else None
            except Exception:
                payload = None
            return {"ok": 200 <= int(response.status) < 400, "status_code": int(response.status), "payload": payload, "error": None}
    except urllib.error.HTTPError as exc:
        text = ""
        try:
            text = exc.read(60_000).decode("utf-8", errors="replace")
        except Exception:
            pass
        try:
            payload = json.loads(text) if text.strip() else None
        except Exception:
            payload = None
        return {"ok": False, "status_code": int(exc.code), "payload": payload, "error": f"HTTP_{exc.code}"}
    except Exception as exc:
        return {"ok": False, "status_code": None, "payload": None, "error": type(exc).__name__}


def _contract_plan(contract: dict[str, Any], index: int) -> dict[str, Any]:
    method = str(contract.get("method") or "POST").upper()
    parallelism = max(2, min(int(contract.get("parallelism") or 2), 8))
    result_path = str(contract.get("result_path") or contract.get("observation_path") or "")
    result_field = str(contract.get("result_field") or contract.get("count_field") or "")
    reasons: list[str] = []
    if method not in WRITE_METHODS:
        reasons.append("method_must_be_post_put_or_patch")
    if not str(contract.get("path") or "").startswith("/"):
        reasons.append("write_path_required")
    if not result_path.startswith("/"):
        reasons.append("result_path_required")
    if not result_field:
        reasons.append("result_field_required")
    if not (contract.get("idempotency_header") or contract.get("idempotency_key_field")):
        reasons.append("idempotency_key_injection_required")
    return {
        "contract_id": str(contract.get("contract_id") or f"CONC_{index:02d}"),
        "title": str(contract.get("title") or "Concurrent idempotency validation"),
        "method": method,
        "path": str(contract.get("path") or ""),
        "parallelism": parallelism,
        "result_path": result_path,
        "result_field": result_field,
        "expected_max": int(contract.get("expected_max") if contract.get("expected_max") is not None else 1),
        "severity": str(contract.get("severity") or "P1").upper(),
        "ready": not reasons,
        "blocking_reasons": reasons,
        "execution_policy": "sandbox_required",
        "requires": ["target_environment=sandbox", "disposable_sandbox=true", "allow_destructive_tests=true", "approved_sandbox_execution=true", "approval_id"],
    }


def build_concurrency_async_sandbox_plan(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    cfg = load_real_project_config(project, root)
    section = _section(cfg)
    contracts = _contract_rows(cfg)
    plans = [_contract_plan(row, idx + 1) for idx, row in enumerate(contracts)]
    safety = safety_gate(
        project,
        declared_environment=str(cfg.get("target_environment") or cfg.get("environment") or ""),
        base_url=str(cfg.get("base_url") or ""),
        execution_mode="safe_live",
        accounts={},
    ).validate() if str(cfg.get("base_url") or "").strip() else {"safe_to_proceed": False, "violations": [{"rule": "no_base_url"}]}
    result = {
        "phase": "phase72_concurrency_async_sandbox",
        "project_id": project,
        "generated_at_utc": _now(),
        "contracts": plans,
        "summary": {"contract_count": len(plans), "ready_contract_count": sum(1 for row in plans if row["ready"]), "candidate_only": True},
        "safety_boundary": safety,
        "governance": {
            "writes_never_run_by_default": True,
            "requires_disposable_sandbox": True,
            "requires_explicit_approval_at_call_time": True,
            "same_logical_request_uses_one_idempotency_key": True,
            "evidence_redacts_request_headers_and_bodies": True,
            "formal_findings_require_observed_read_only_state": True,
        },
        "configuration": {"enabled": bool(section.get("enabled")), "disposable": bool(section.get("disposable")), "approval_id_present": bool(str(section.get("approval_id") or "").strip())},
    }
    paths = _paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["plan"], result)
    _write_json(paths["output"] / "concurrency_async_sandbox_plan.json", result)
    return result


def _execution_blockers(cfg: dict[str, Any], options: dict[str, Any]) -> list[str]:
    section = _section(cfg)
    blockers: list[str] = []
    if str(cfg.get("target_environment") or cfg.get("environment") or "").strip().lower() != "sandbox":
        blockers.append("target_environment_must_be_sandbox")
    if not bool(cfg.get("allow_destructive_tests")):
        blockers.append("allow_destructive_tests_must_be_true_in_project_config")
    if not bool(section.get("enabled")):
        blockers.append("concurrency_async_sandbox_not_enabled")
    if not bool(section.get("disposable")):
        blockers.append("disposable_sandbox_must_be_true")
    if not str(section.get("approval_id") or "").strip():
        blockers.append("approval_id_required")
    if not bool(options.get("approved_sandbox_execution")):
        blockers.append("approved_sandbox_execution_required_at_call_time")
    if not bool(options.get("execute")):
        blockers.append("execute_flag_required_at_call_time")
    return blockers


def _prepare_request(contract: dict[str, Any], run_key: str) -> tuple[Any, dict[str, Any]]:
    body = _substitute_run_key(contract.get("body") or {}, run_key)
    headers = _substitute_run_key(contract.get("headers") or {}, run_key)
    if not isinstance(headers, dict):
        headers = {}
    if contract.get("idempotency_header"):
        headers[str(contract["idempotency_header"])] = run_key
    if contract.get("idempotency_key_field") and isinstance(body, dict):
        body[str(contract["idempotency_key_field"])] = run_key
    return body, headers


def _finding(plan: dict[str, Any], observation: dict[str, Any], run_key: str) -> dict[str, Any]:
    return {
        "finding_id": f"CONC_FINDING_{_hash([plan.get('contract_id'), run_key, observation.get('observed')])}",
        "source": "concurrency_async_sandbox",
        "risk_type": "concurrent_idempotency_violation",
        "severity": plan.get("severity") if plan.get("severity") in {"P0", "P1", "P2", "P3"} else "P1",
        "title": f"并发重试导致重复业务副作用：{plan.get('title')}",
        "expected": f"同一幂等键的持久副作用数量应不大于 {plan.get('expected_max')}",
        "actual": f"观测到持久副作用数量为 {observation.get('observed')}",
        "confidence": 0.96,
        "evidence_strength": "runtime_strong",
        "evidence": {
            "contract_id": plan.get("contract_id"),
            "parallel_request_count": observation.get("parallel_request_count"),
            "status_distribution": observation.get("status_distribution"),
            "result_status": observation.get("result_status"),
            "result_field": plan.get("result_field"),
            "expected_max": plan.get("expected_max"),
            "observed": observation.get("observed"),
            "idempotency_key_hash": _hash(run_key, 24),
        },
        "execution_policy": "sandbox_evidence_confirmed",
        "governance": "disposable_sandbox_only",
    }


def run_concurrency_async_sandbox(project_id: str = "real_project_demo", root: Path | None = None, options: dict[str, Any] | None = None) -> dict[str, Any]:
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = dict(options or {})
    cfg = load_real_project_config(project, root)
    plan = build_concurrency_async_sandbox_plan(project, root)
    raw_contracts = _contract_rows(cfg)
    blockers = _execution_blockers(cfg, options)
    safety = plan.get("safety_boundary") or {}
    if not safety.get("safe_to_proceed"):
        blockers.append("shared_safety_boundary_blocked")
    executions: list[dict[str, Any]] = []
    findings: list[dict[str, Any]] = []
    if blockers:
        for row in plan.get("contracts") or []:
            executions.append({"contract_id": row.get("contract_id"), "status": "blocked_requires_sandbox", "blocking_reasons": sorted(set(blockers + list(row.get("blocking_reasons") or [])),), "execution_policy": "sandbox_required"})
    else:
        by_id = {str(item.get("contract_id") or f"CONC_{idx+1:02d}"): item for idx, item in enumerate(raw_contracts)}
        base_url = str(cfg.get("base_url") or "").rstrip("/")
        for plan_row in plan.get("contracts") or []:
            contract = by_id.get(str(plan_row.get("contract_id")))
            if not plan_row.get("ready") or not contract:
                executions.append({"contract_id": plan_row.get("contract_id"), "status": "blocked_invalid_contract", "blocking_reasons": plan_row.get("blocking_reasons") or ["contract_not_found"]})
                continue
            run_key = f"qb-{_hash([project, plan_row.get('contract_id'), _now(), time.monotonic()], 22)}"
            body, headers = _prepare_request(contract, run_key)
            request_url = _join_url(base_url, str(plan_row.get("path") or ""))
            timeout = min(max(int(contract.get("timeout_seconds") or 6), 1), 15)
            request_results: list[dict[str, Any]] = []
            with ThreadPoolExecutor(max_workers=int(plan_row.get("parallelism") or 2), thread_name_prefix="qualibug-sandbox") as executor:
                futures = [executor.submit(_http, request_url, str(plan_row.get("method") or "POST"), body=body, headers=headers, timeout=timeout) for _ in range(int(plan_row.get("parallelism") or 2))]
                for future in as_completed(futures):
                    request_results.append(future.result())
            result_path = str(plan_row.get("result_path") or "").replace("{run_key}", urllib.parse.quote(run_key, safe=""))
            observation_response = _http(_join_url(base_url, result_path), "GET", timeout=timeout)
            observed = _field(observation_response.get("payload"), str(plan_row.get("result_field") or ""))
            try:
                observed_number = int(observed)
            except Exception:
                observed_number = None
            status_distribution = dict(Counter(str(row.get("status_code")) for row in request_results))
            observation = {
                "parallel_request_count": len(request_results),
                "status_distribution": status_distribution,
                "result_status": observation_response.get("status_code"),
                "observed": observed_number,
            }
            status = "verified_no_violation"
            if not observation_response.get("ok") or observed_number is None:
                status = "needs_investigation_observation_unavailable"
            elif observed_number > int(plan_row.get("expected_max") or 1):
                status = "finding_confirmed"
                findings.append(_finding(plan_row, observation, run_key))
            executions.append({
                "contract_id": plan_row.get("contract_id"),
                "status": status,
                "request": {"method": plan_row.get("method"), "path": plan_row.get("path"), "parallelism": plan_row.get("parallelism"), "body": _redact(body), "headers": _redact(headers)},
                "observation": observation,
                "idempotency_key_hash": _hash(run_key, 24),
                "execution_policy": "approved_disposable_sandbox",
            })
    result = {
        "phase": "phase72_concurrency_async_sandbox",
        "project_id": project,
        "generated_at_utc": _now(),
        "plan": plan,
        "executions": executions,
        "findings": findings,
        "summary": {"execution_count": len(executions), "finding_count": len(findings), "blocked_count": sum(1 for item in executions if str(item.get("status", "")).startswith("blocked")), "strong_evidence_count": len(findings)},
        "governance": {"formal_findings_only_from_disposable_sandbox_observation": True, "writes_disabled_unless_all_explicit_gates_pass": True, "production_never_touched": True},
    }
    paths = _paths(project, root)
    paths["workspace"].mkdir(parents=True, exist_ok=True)
    paths["output"].mkdir(parents=True, exist_ok=True)
    _write_json(paths["run"], result)
    _write_json(paths["output"] / "concurrency_async_sandbox_run.json", result)
    return result


def load_concurrency_async_sandbox(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    data = _load_json(_paths(project, root)["run"], {})
    return data if isinstance(data, dict) and data else None
