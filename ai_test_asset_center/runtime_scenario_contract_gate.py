from __future__ import annotations

"""Safety gate for customer-provided runtime scenario contracts.

The gate is deliberately small and conservative. Read-only scenarios are allowed
when they only contain GET/HEAD steps. Write-capable scenarios require all of:

- execution_policy is approved_sandbox_write or runtime_approved;
- test_data_contract.write_approved is true;
- at least one cleanup step is supplied for every write scenario;
- cleanup steps are customer-supplied and path-bound.
"""

from typing import Any

READ_ONLY_METHODS = {"GET", "HEAD"}
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
WRITE_POLICIES = {"approved_sandbox_write", "runtime_approved"}


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _method(value: Any) -> str:
    return str(value or "").upper().strip()


def _path(value: Any) -> str:
    return str(value or "").strip()


def _steps(value: Any) -> list[dict[str, Any]]:
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _has_cleanup(row: dict[str, Any]) -> bool:
    cleanup = _steps(row.get("cleanup_steps") or row.get("cleanup"))
    return any(_method(item.get("method") or item.get("api_method")) in READ_ONLY_METHODS | WRITE_METHODS and _path(item.get("path") or item.get("api_path")).startswith("/") for item in cleanup)


def runtime_scenario_contract_gaps(context: dict[str, Any]) -> list[dict[str, str]]:
    contract = _as_dict(context.get("runtime_scenario_contract"))
    if not contract:
        return []
    policy = str(contract.get("execution_policy") or "safe_read_only").strip()
    actor = _as_dict(contract.get("actor"))
    scenarios = _steps(contract.get("scenarios"))
    gaps: list[dict[str, str]] = []

    if policy not in {"safe_read_only", "approved_sandbox_write", "runtime_approved"}:
        gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_POLICY_INVALID", "detail": "runtime_scenario_contract.execution_policy is not allowed."})
    if not str(actor.get("id") or actor.get("name") or actor.get("actor") or "").strip():
        gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_ACTOR_MISSING", "detail": "runtime_scenario_contract requires an explicit customer-approved actor."})
    if not scenarios:
        gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_STEPS_MISSING", "detail": "runtime_scenario_contract requires at least one scenario with source-bound steps."})

    test_data = _as_dict(context.get("test_data_contract"))
    write_approved = test_data.get("write_approved") is True
    for index, row in enumerate(scenarios):
        steps = _steps(row.get("steps"))
        if not steps:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_STEPS_MISSING", "detail": f"scenario[{index}] has no executable steps."})
            continue
        step_methods = [_method(item.get("method") or item.get("api_method")) for item in steps]
        step_paths = [_path(item.get("path") or item.get("api_path")) for item in steps]
        if any(not method or method not in READ_ONLY_METHODS | WRITE_METHODS for method in step_methods):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_METHOD_INVALID", "detail": f"scenario[{index}] contains an invalid HTTP method."})
        if any(not path.startswith("/") for path in step_paths):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "RUNTIME_SCENARIO_PATH_INVALID", "detail": f"scenario[{index}] contains a non-source-bound path."})
        has_write = any(method in WRITE_METHODS for method in step_methods)
        if policy == "safe_read_only" and has_write:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "WRITE_STEP_NOT_ALLOWED_IN_READ_ONLY_POLICY", "detail": f"scenario[{index}] contains a write step under safe_read_only policy."})
        if has_write and policy not in WRITE_POLICIES:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "WRITE_POLICY_REQUIRED", "detail": f"scenario[{index}] contains a write step but execution_policy is not write-capable."})
        if has_write and not write_approved:
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "WRITE_APPROVAL_MISSING", "detail": "Write-capable runtime scenarios require test_data_contract.write_approved=true."})
        if has_write and not _has_cleanup(row):
            gaps.append({"kind": "RUNTIME_SCENARIO_CONTRACT_GAP", "code": "CLEANUP_CONTRACT_MISSING", "detail": f"scenario[{index}] contains write steps but no cleanup_steps contract."})

    return gaps
