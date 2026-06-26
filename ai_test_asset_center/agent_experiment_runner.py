from __future__ import annotations

"""Scenario compiler and evidence dispatcher for the persistent Agent Loop.

Phase75 closes the gap between a planned experiment and a reproducible, safe
execution packet.  It deliberately does not invent a second control plane:
all scenario identity, receipt state and executor events are persisted through
``agent_discovery_loop``'s canonical SQLite ledger.

The compiler accepts only document-backed or explicitly configured sandbox
work.  It emits a deterministic precondition/mutation/verification packet,
but never calls a target while compiling.  Execution delegates to the existing
disposable-sandbox contract executor, which retains the project's environment,
approval and safety-gate checks.
"""

import hashlib
import json
import time
from pathlib import Path
from typing import Any
import copy

from .concurrency_async_sandbox import _http
from .real_project_onboarding import _join_url

from .agent_discovery_loop import (
    build_agent_discovery_loop,
    load_agent_discovery_experiments,
    record_agent_discovery_evidence,
    record_agent_discovery_experiment_result,
    upsert_agent_discovery_experiment,
)
from .document_contract_fuzzing import compile_document_contracts, execute_document_contracts
from .real_project_onboarding import ROOT, _safe_project_id, config_paths, load_real_project_config

PHASE = "phase75_agent_experiment_runner"
_WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
_OWNED_IDENTIFIER_TERMS = ("code", "no", "number", "serial", "reference", "externalref", "external_ref")
_FOREIGN_REFERENCE_TERMS = ("material", "customer", "supplier", "warehouse", "routing", "bom", "equipment", "order", "parent")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _hash(value: Any, length: int = 24) -> str:
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8", errors="replace")).hexdigest()[:length]


def _redact(value: Any, limit: int = 6000) -> Any:
    if isinstance(value, dict):
        clean: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key).lower()
            if any(token in key_text for token in ("password", "token", "authorization", "api_key", "secret", "cookie")):
                clean[str(key)] = "[REDACTED]"
            else:
                clean[str(key)] = _redact(item, limit)
        return clean
    if isinstance(value, list):
        return [_redact(item, limit) for item in value[:100]]
    return str(value or "")[:limit]


def _output_dir(project: str, root: Path) -> Path:
    return root / "platform_outputs" / project / "agent_discovery_loop"


def _input_texts(project: str, root: Path) -> tuple[str, str]:
    input_dir = config_paths(project, root)["input_dir"]
    prd_path = input_dir / "prd.md"
    prd = prd_path.read_text(encoding="utf-8", errors="replace") if prd_path.exists() else ""
    documents = [path for path in input_dir.glob("*.md") if path.name.lower() not in {"prd.md", "readme.md"}]
    if not documents:
        return prd, ""

    def score(path: Path) -> tuple[int, int]:
        text = path.read_text(encoding="utf-8", errors="replace")
        return (sum(text.upper().count(method) for method in ("GET", "POST", "PUT", "PATCH", "DELETE")), len(text))

    api_path = max(documents, key=score)
    return prd, api_path.read_text(encoding="utf-8", errors="replace")


def _reference_hints(body: Any) -> list[dict[str, str]]:
    if not isinstance(body, dict):
        return []
    hints: list[dict[str, str]] = []
    for key, value in body.items():
        name = str(key)
        lower = name.lower()
        if lower.endswith("id") or lower.endswith("_id") or lower.endswith("code") or lower.endswith("_code"):
            relation = "foreign_reference" if any(term in lower for term in _FOREIGN_REFERENCE_TERMS) else "owned_or_reference"
            hints.append({"field": name, "relation": relation, "configured_value_present": str(value not in {None, ""}).lower()})
    return hints[:30]



def _agent_section(cfg: dict[str, Any]) -> dict[str, Any]:
    for key in ("agent_discovery_loop", "agent_loop", "autonomous_discovery"):
        value = cfg.get(key)
        if isinstance(value, dict):
            return value
    return {}


def _fixture_catalog(cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    section = _agent_section(cfg)
    rows = section.get("fixture_catalog") or section.get("sandbox_fixture_catalog") or []
    catalog: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        fixture_id = str(row.get("fixture_id") or row.get("id") or "").strip()
        if fixture_id:
            catalog[fixture_id] = copy.deepcopy(row)
    return catalog


def _fixture_bindings(cfg: dict[str, Any]) -> dict[str, dict[str, str]]:
    section = _agent_section(cfg)
    raw = section.get("fixture_bindings") or {}
    result: dict[str, dict[str, str]] = {}
    if not isinstance(raw, dict):
        return result
    for field, value in raw.items():
        if isinstance(value, str):
            result[str(field)] = {"fixture_id": value, "context_key": str(field)}
        elif isinstance(value, dict):
            fixture_id = str(value.get("fixture_id") or value.get("fixture") or "").strip()
            context_key = str(value.get("context_key") or value.get("capture") or field).strip()
            if fixture_id:
                result[str(field)] = {"fixture_id": fixture_id, "context_key": context_key}
    return result


def _ordered_fixtures(catalog: dict[str, dict[str, Any]], requested: set[str]) -> tuple[list[dict[str, Any]], list[str]]:
    ordered: list[dict[str, Any]] = []
    missing: list[str] = []
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(fixture_id: str) -> None:
        if fixture_id in visited:
            return
        if fixture_id in visiting:
            missing.append(f"fixture_dependency_cycle:{fixture_id}")
            return
        fixture = catalog.get(fixture_id)
        if not fixture:
            missing.append(f"fixture_not_configured:{fixture_id}")
            return
        visiting.add(fixture_id)
        for dep in fixture.get("depends_on") or []:
            visit(str(dep))
        visiting.remove(fixture_id)
        visited.add(fixture_id)
        ordered.append(fixture)

    for fixture_id in sorted(requested):
        visit(fixture_id)
    return ordered, sorted(set(missing))


def _fixture_plan_for_scenario(scenario: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    bindings = _fixture_bindings(cfg)
    catalog = _fixture_catalog(cfg)
    field_bindings: list[dict[str, str]] = []
    requested: set[str] = set()
    missing: list[str] = []
    for hint in scenario.get("fixture_reference_hints") or []:
        if not isinstance(hint, dict) or str(hint.get("relation")) != "foreign_reference":
            continue
        field = str(hint.get("field") or "")
        binding = bindings.get(field)
        if not binding:
            missing.append(f"fixture_binding_missing:{field}")
            continue
        requested.add(binding["fixture_id"])
        field_bindings.append({"field": field, **binding})
    ordered, dependency_errors = _ordered_fixtures(catalog, requested)
    missing.extend(dependency_errors)
    return {
        "required": bool(field_bindings),
        "ready": not missing,
        "field_bindings": field_bindings,
        "fixtures": ordered,
        "blocking_reasons": sorted(set(missing)),
    }


def _accepted(response: dict[str, Any]) -> bool:
    status = response.get("status_code")
    if status is None or not (200 <= int(status) < 300):
        return False
    payload = response.get("payload")
    return not (isinstance(payload, dict) and (payload.get("success") is False or payload.get("ok") is False))


def _dotted(value: Any, path: str) -> Any:
    current = value
    for part in str(path or "").replace("$.", "").split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        elif isinstance(current, list) and part.isdigit() and int(part) < len(current):
            current = current[int(part)]
        else:
            return None
    return current


def _render_fixture_value(value: Any, context: dict[str, Any], run_key: str) -> Any:
    if isinstance(value, dict):
        return {str(k): _render_fixture_value(v, context, run_key) for k, v in value.items()}
    if isinstance(value, list):
        return [_render_fixture_value(item, context, run_key) for item in value]
    if not isinstance(value, str):
        return value
    rendered = value.replace("${run_key}", run_key)
    for key, item in context.items():
        rendered = rendered.replace("${fixture." + str(key) + "}", str(item))
    return rendered


def _fixture_headers(cfg: dict[str, Any], fixture: dict[str, Any]) -> dict[str, str]:
    role = str(fixture.get("role") or cfg.get("default_role") or "")
    role_headers = cfg.get("role_headers") or {}
    raw = role_headers.get(role) if isinstance(role_headers, dict) else None
    return {str(k): str(v) for k, v in raw.items()} if isinstance(raw, dict) else {}


def _execute_fixture_plan(cfg: dict[str, Any], plan: dict[str, Any], run_key: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Execute explicit fixture templates after the existing executor approved sandbox use."""
    context: dict[str, Any] = {}
    receipts: list[dict[str, Any]] = []
    if not plan.get("ready"):
        return context, [{"status": "blocked", "reasons": plan.get("blocking_reasons") or []}]
    base_url = str(cfg.get("base_url") or "").rstrip("/")
    for fixture in plan.get("fixtures") or []:
        fixture_id = str(fixture.get("fixture_id") or fixture.get("id") or "fixture")
        method = str(fixture.get("method") or "POST").upper()
        path = str(fixture.get("path") or "")
        headers = _fixture_headers(cfg, fixture)
        if method not in _WRITE_METHODS or not path.startswith("/") or not headers:
            receipts.append({"fixture_id": fixture_id, "status": "blocked", "reason": "fixture_requires_write_path_and_authorised_role_headers"})
            return context, receipts
        body = _render_fixture_value(fixture.get("body") or {}, context, run_key)
        response = _http(_join_url(base_url, path), method, body=body, headers=headers)
        receipt = {"fixture_id": fixture_id, "method": method, "path": path, "status_code": response.get("status_code"), "accepted": _accepted(response)}
        receipts.append(receipt)
        if not _accepted(response):
            receipt["reason"] = "fixture_creation_failed"
            return context, receipts
        captures = fixture.get("captures") or fixture.get("capture") or {}
        if not isinstance(captures, dict):
            captures = {}
        for key, path_expr in captures.items():
            captured = _dotted(response.get("payload"), str(path_expr))
            if captured in {None, ""}:
                receipt.setdefault("missing_captures", []).append(str(key))
            else:
                context[str(key)] = captured
        if receipt.get("missing_captures"):
            receipt["reason"] = "fixture_capture_missing"
            return context, receipts
    return context, receipts


def _prepared_contract(contract: dict[str, Any], fixture_plan: dict[str, Any], context: dict[str, Any]) -> dict[str, Any]:
    prepared = copy.deepcopy(contract)
    body = prepared.get("sample_body")
    if not isinstance(body, dict):
        return prepared
    preserve: list[str] = []
    for binding in fixture_plan.get("field_bindings") or []:
        field = str(binding.get("field") or "")
        context_key = str(binding.get("context_key") or field)
        if field and context_key in context:
            body[field] = context[context_key]
            preserve.append(field)
    prepared["sample_body"] = body
    prepared["preserve_fixture_fields"] = preserve
    return prepared

def _scenario_for_contract(item: dict[str, Any], contract: dict[str, Any]) -> dict[str, Any]:
    kind = str(contract.get("kind") or "document_business_constraint")
    method = str(contract.get("method") or "GET").upper()
    body = contract.get("sample_body") if isinstance(contract.get("sample_body"), dict) else {}
    mutation = contract.get("mutation") if isinstance(contract.get("mutation"), dict) else {}
    contract_id = str(contract.get("contract_id") or item.get("source_ref") or "")
    fixture_mode = "no_fixture_required"
    steps: list[dict[str, Any]] = []
    verification: dict[str, Any] = {"expected": contract.get("expected") or "documented constraint is enforced"}

    if kind == "duplicate_business_key":
        fixture_mode = "create_owned_resource_then_repeat"
        steps = [
            {"phase": "fixture", "operation": "create_valid_resource", "method": method, "path": contract.get("path"), "body_source": "sample_body_with_run_namespace"},
            {"phase": "mutation", "operation": "repeat_same_owned_business_key", "method": method, "path": contract.get("path"), "body_source": "same_fixture_payload"},
        ]
        verification["observation"] = "second_non_2xx"
    elif kind == "replay_idempotency":
        fixture_mode = "idempotent_write_pair"
        steps = [
            {"phase": "mutation", "operation": "submit_with_generated_idempotency_key", "method": method, "path": contract.get("path"), "body_source": "sample_body_with_run_namespace"},
            {"phase": "mutation", "operation": "repeat_same_idempotency_key", "method": method, "path": contract.get("path"), "body_source": "same_payload"},
        ]
        verification["observation"] = "same_business_identity"
    elif kind == "role_boundary" and method == "GET":
        fixture_mode = "read_only"
        steps = [{"phase": "mutation", "operation": "unauthorised_role_read", "method": method, "path": contract.get("path"), "body_source": "none"}]
        verification["observation"] = "non_2xx_for_unauthorised_role"
    else:
        steps = [{"phase": "mutation", "operation": "submit_documented_invalid_input", "method": method, "path": contract.get("path"), "body_source": "sample_body_with_document_mutation"}]
        verification["observation"] = "non_2xx"

    policy = "safe_read_only" if method == "GET" else "sandbox_required"
    return {
        "scenario_id": f"SCN_{_hash([item.get('item_id'), contract_id, kind], 28)}",
        "ledger_item_id": item.get("item_id"),
        "contract_id": contract_id,
        "title": item.get("title"),
        "risk_type": item.get("risk_type"),
        "severity": item.get("severity"),
        "execution_policy": policy,
        "scenario_kind": kind,
        "fixture_mode": fixture_mode,
        "fixture_reference_hints": _reference_hints(body),
        "preconditions": {
            "sandbox_must_be_disposable": method in _WRITE_METHODS,
            "approved_sandbox_execution": method in _WRITE_METHODS,
            "sample_body_present": bool(body) or method == "GET",
            "path_parameters_must_be_configured": "{" in str(contract.get("path") or ""),
        },
        "steps": steps,
        "document_mutation": mutation,
        "verification": verification,
        "cleanup": {
            "strategy": "sandbox_reset" if method in _WRITE_METHODS else "none",
            "never_attempted_outside_disposable_sandbox": method in _WRITE_METHODS,
        },
        "contract": contract,
        "governance": {
            "no_target_request_during_compilation": True,
            "formal_bug_requires_runtime_evidence_and_human_verdict": True,
            "static_or_llm_text_cannot_confirm_bug": True,
        },
    }


def compile_agent_experiment_pack(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile ledger experiments into reproducible packets without execution."""
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = dict(options or {})
    max_experiments = max(1, min(int(options.get("max_experiments") or 24), 250))
    loop = build_agent_discovery_loop(project, root, {"actor": "agent_experiment_compiler", "max_next_actions": max_experiments})
    cfg = load_real_project_config(project, root)
    prd, api_text = _input_texts(project, root)
    compiled = compile_document_contracts(prd, api_text) if api_text else {"contracts": []}
    contracts_by_id = {str(row.get("contract_id")): row for row in (compiled.get("contracts") or []) if isinstance(row, dict)}

    experiments: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in loop.get("items") or []:
        if len(experiments) >= max_experiments:
            break
        if not isinstance(item, dict) or item.get("source") != "document_contract_fuzzing":
            continue
        if str(item.get("state")) not in {"BLOCKED_BY_APPROVAL", "READY_FOR_READONLY"}:
            continue
        contract_id = str(item.get("source_ref") or "")
        contract = contracts_by_id.get(contract_id)
        if not contract:
            skipped.append({"item_id": item.get("item_id"), "reason": "source_contract_no_longer_available"})
            continue
        scenario = _scenario_for_contract(item, contract)
        scenario["fixture_plan"] = _fixture_plan_for_scenario(scenario, cfg)
        experiment_state = "COMPILED" if scenario["execution_policy"] == "safe_read_only" else "BLOCKED_BY_APPROVAL"
        if scenario["execution_policy"] == "sandbox_required" and not scenario["fixture_plan"].get("ready"):
            experiment_state = "BLOCKED_BY_FIXTURE"
        experiment = upsert_agent_discovery_experiment(
            project,
            str(item.get("item_id")),
            scenario,
            experiment_type="document_business_scenario",
            state=experiment_state,
            root=root,
            actor="agent_experiment_compiler",
        )
        experiments.append({
            "experiment_id": experiment.get("experiment_id"),
            "item_id": item.get("item_id"),
            "contract_id": contract_id,
            "state": experiment.get("state"),
            "scenario": scenario,
        })

    report = {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "summary": {
            "compiled_experiment_count": len(experiments),
            "sandbox_experiment_count": sum(1 for row in experiments if row["scenario"].get("execution_policy") == "sandbox_required"),
            "safe_read_experiment_count": sum(1 for row in experiments if row["scenario"].get("execution_policy") == "safe_read_only"),
            "fixture_ready_count": sum(1 for row in experiments if (row["scenario"].get("fixture_plan") or {}).get("ready")),
            "fixture_blocked_count": sum(1 for row in experiments if row.get("state") == "BLOCKED_BY_FIXTURE"),
            "skipped_count": len(skipped),
        },
        "experiments": experiments,
        "skipped": skipped,
        "governance": {
            "single_canonical_state_store": "agent_discovery_loop.sqlite3",
            "compilation_makes_zero_target_requests": True,
            "writes_remain_blocked_without_existing_sandbox_approval": True,
            "unknown_bug_total_not_used": True,
        },
    }
    out = _output_dir(project, root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "compiled_experiment_pack.json").write_text(json.dumps(_redact(report), ensure_ascii=False, indent=2), encoding="utf-8")
    return report


def _find_experiment(pack: dict[str, Any], contract_id: str) -> dict[str, Any] | None:
    for row in pack.get("experiments") or []:
        if str(row.get("contract_id")) == str(contract_id):
            return row
    return None


def run_agent_experiment_pack(
    project_id: str = "real_project_demo",
    root: Path | None = None,
    options: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Execute a selected pack only through the existing sandbox executor.

    No safety condition is duplicated or loosened here; the existing document
    executor is the only component that decides whether a write may occur.
    """
    root = root or ROOT
    project = _safe_project_id(project_id)
    options = dict(options or {})
    pack = compile_agent_experiment_pack(project, root, options)
    cfg = load_real_project_config(project, root)
    # Ask the existing executor to validate every sandbox gate before a
    # fixture template is allowed to issue its first request.  An empty plan
    # makes no target request and returns the same blockers used for contracts.
    precheck = execute_document_contracts({"contracts": []}, cfg, options=options)
    receipts: list[dict[str, Any]] = []
    promoted: list[dict[str, Any]] = []
    executions: list[dict[str, Any]] = []
    for row in pack.get("experiments") or []:
        contract_id = str(row.get("contract_id") or "")
        experiment_id = str(row.get("experiment_id") or "")
        item_id = str(row.get("item_id") or "")
        scenario = row.get("scenario") if isinstance(row.get("scenario"), dict) else {}
        if precheck.get("status") == "blocked" or not bool((precheck.get("safety") or {}).get("safe_to_proceed")):
            gate_blockers = list(precheck.get("blockers") or [])
            if not bool((precheck.get("safety") or {}).get("safe_to_proceed")):
                gate_blockers.append("shared_safety_boundary_blocked")
            execution = {"status": "blocked", "blockers": sorted(set(gate_blockers)), "findings": [], "summary": {}}
            fixture_receipts: list[dict[str, Any]] = []
        elif row.get("state") == "BLOCKED_BY_FIXTURE":
            execution = {"status": "blocked", "blockers": (scenario.get("fixture_plan") or {}).get("blocking_reasons") or [], "findings": [], "summary": {}}
            fixture_receipts = []
        else:
            run_key = f"loop-{_hash([project, experiment_id, _now()], 12)}"
            fixture_context, fixture_receipts = _execute_fixture_plan(cfg, scenario.get("fixture_plan") or {}, run_key)
            fixture_failed = any(receipt.get("status") == "blocked" or not receipt.get("accepted", True) for receipt in fixture_receipts)
            if fixture_failed:
                execution = {"status": "blocked", "blockers": ["fixture_precondition_failed"], "findings": [], "summary": {}}
            else:
                prepared = _prepared_contract(scenario.get("contract") or {}, scenario.get("fixture_plan") or {}, fixture_context)
                execution = execute_document_contracts({"contracts": [prepared]}, cfg, options={**options, "max_contracts": 1})
        related_findings = [finding for finding in (execution.get("findings") or []) if isinstance(finding, dict) and str(finding.get("contract_id") or "") == contract_id]
        result = {
            "status": execution.get("status"),
            "contract_id": contract_id,
            "finding_count": len(related_findings),
            "findings": related_findings,
            "blockers": execution.get("blockers") or [],
            "execution_summary": execution.get("summary") or {},
            "fixture_receipts": fixture_receipts,
        }
        state = "BLOCKED" if execution.get("status") == "blocked" else ("EVIDENCE_CAPTURED" if related_findings else "EXECUTED")
        receipt = record_agent_discovery_experiment_result(project, experiment_id, result, state=state, root=root)
        receipts.append(receipt)
        executions.append(result)
        for finding in related_findings:
            evidence = {
                "evidence_strength": "runtime_strong",
                "contract_id": contract_id,
                "scenario_id": scenario.get("scenario_id"),
                "expected": finding.get("expected"),
                "actual": finding.get("actual"),
                "fixture_receipts": fixture_receipts,
                "evidence": finding.get("evidence") or {},
            }
            promoted.append(record_agent_discovery_evidence(project, item_id, evidence, root=root, actor="agent_experiment_runner"))
    execution = {
        "status": "blocked" if (precheck.get("status") == "blocked" or not bool((precheck.get("safety") or {}).get("safe_to_proceed"))) else "completed",
        "blockers": list(precheck.get("blockers") or []) + ([] if bool((precheck.get("safety") or {}).get("safe_to_proceed")) else ["shared_safety_boundary_blocked"]),
        "summary": {"executed_experiment_count": sum(1 for row in executions if row.get("status") == "completed"), "finding_count": sum(int(row.get("finding_count") or 0) for row in executions)},
        "results": executions,
    }

    result = {
        "phase": PHASE,
        "project_id": project,
        "generated_at_utc": _now(),
        "execution": execution,
        "receipt_count": len(receipts),
        "evidence_capture_count": len(promoted),
        "experiments": receipts,
        "governance": {
            "delegates_safety_to_document_contract_executor": True,
            "runtime_evidence_still_requires_human_verdict": True,
            "does_not_auto_confirm_or_create_regression_guard": True,
        },
    }
    out = _output_dir(project, root)
    out.mkdir(parents=True, exist_ok=True)
    (out / "agent_experiment_execution_receipt.json").write_text(json.dumps(_redact(result), ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def load_agent_experiment_pack(project_id: str = "real_project_demo", root: Path | None = None) -> dict[str, Any] | None:
    root = root or ROOT
    project = _safe_project_id(project_id)
    path = _output_dir(project, root) / "compiled_experiment_pack.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def list_agent_experiment_receipts(project_id: str = "real_project_demo", root: Path | None = None) -> list[dict[str, Any]]:
    return load_agent_discovery_experiments(project_id, root)
