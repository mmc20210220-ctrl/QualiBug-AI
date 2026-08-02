"""Source-declared UI observation on the formal experiment chain.

This is deliberately narrower than "AI looks at a screenshot and decides whether the page is
right".  A formal UI verdict is possible only when enterprise material supplies an executable
browser plan with an explicit ``expect_text`` or ``expect_url`` step.  The existing Playwright
adapter executes that plan; this module turns its result into a typed observer receipt and a
registered assertion verdict.

The adapter's provider-authored ``findings`` are never consumed.  They remain candidate clues.
Only the source-declared expectation, the governed execution result and the contract Oracle can
produce a formal finding.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
from typing import Any

OBSERVER_ID = "ui_source_expectation_reader"
EVIDENCE_KEY = "ui_source_expectation"
ASSERTION_KIND = "ui_source_expectation"
RISK_FAMILY = "ui_state_consistency"
PROTOCOL_TEMPLATE = "source_declared_ui_expectation"
SURFACE = "ui_rendered_state"
ADAPTER = "ui_browser"
_SUPPORTED_EXPECTATIONS = frozenset({"expect_text", "expect_url"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _declared(spec: dict[str, Any], key: str) -> Any:
    row = _dict(spec)
    if key in row:
        return row[key]
    return _dict(row.get("property")).get(key)


def _declared_ui_request(spec: dict[str, Any]) -> dict[str, Any]:
    request = copy.deepcopy(_dict(_declared(spec, "ui_request")))
    if request:
        return request
    # A split representation is accepted only when every execution-bearing field is still
    # explicitly present.  Nothing here invents selectors, text, URLs or click paths.
    plan = copy.deepcopy(_dict(_declared(spec, "ui_browser_plan")))
    start_url = _text(_declared(spec, "ui_start_url"))
    provider = _text(_declared(spec, "ui_provider"))
    if not (plan and start_url and provider):
        return {}
    return {
        "request_id": _text(_declared(spec, "ui_request_id")) or "formal_ui_request",
        "title": _text(_declared(spec, "ui_title")) or "Source-declared UI expectation",
        "provider": provider,
        "start_url": start_url,
        "execution_mode": _text(_declared(spec, "ui_execution_mode")) or "safe_read_only",
        "browser_plan": plan,
        "success_criteria": copy.deepcopy(_dict(_declared(spec, "ui_success_criteria"))),
        "metadata": {"source_declared": True},
    }


def _plan_steps(request: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(request).get("browser_plan", {}).get("steps"))
        if isinstance(row, dict)
    ]


def _expectation_descriptor(step: dict[str, Any]) -> dict[str, Any]:
    action = _text(step.get("action")).lower()
    descriptor: dict[str, Any] = {"action": action}
    if action == "expect_text":
        descriptor.update({
            "selector": _text(step.get("selector")),
            "text": _text(step.get("text")),
            "locator_intent_fingerprint": (
                _fingerprint(step.get("locator_intent"))
                if isinstance(step.get("locator_intent"), dict)
                else ""
            ),
        })
    elif action == "expect_url":
        descriptor["pattern"] = _text(step.get("pattern") or step.get("url"))
    return descriptor


def _compile_ui_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    request = _declared_ui_request(property_spec)
    if not request:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "source_declared_ui_request_missing",
        }
    if _text(request.get("provider")).lower() != "playwright_browser_plan":
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_UNSUPPORTED_ADAPTER",
            "detail": "formal_ui_requires_playwright_browser_plan",
        }
    steps = _plan_steps(request)
    expectations = [
        row for row in steps if _text(row.get("action")).lower() in _SUPPORTED_EXPECTATIONS
    ]
    if not expectations:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": "ui_plan_has_no_source_declared_expectation",
        }
    operation_ref = _text(envelope.get("operation_ref"))
    actor_ref = _text(
        envelope.get("treatment_actor_ref")
        or envelope.get("control_actor_ref")
        or property_spec.get("actor_ref")
    )
    if not operation_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "ui_business_prerequisite_operation_missing",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "ui_business_prerequisite_actor_missing",
        }

    assertion_property = copy.deepcopy(property_spec)
    assertion_property["ui_request"] = request
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "establish_source_declared_ui_precondition",
            "protocol_step": "ui_business_prerequisite",
        }],
        "observers": [{"observer_id": OBSERVER_ID}],
        "assertion": {
            "kind": ASSERTION_KIND,
            "property": assertion_property,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
            "rule_id": _text(property_spec.get("invariant_ref") or property_spec.get("rule_id")),
            "ui_expectation_count": len(expectations),
        },
    }


def _timeout_expectation_failure(reason: str, failed_step: dict[str, Any]) -> bool:
    if _text(failed_step.get("action")).lower() not in _SUPPORTED_EXPECTATIONS:
        return False
    normalized = _text(reason).lower()
    return "timeout" in normalized or "timed out" in normalized


def _execute_ui_requests(
    project: str,
    request: dict[str, Any],
    runtime_contract: dict[str, Any],
    *,
    root: Path,
    run_id: str,
) -> dict[str, Any]:
    from .ui_execution_adapter import execute_ui_execution_requests

    return execute_ui_execution_requests(
        project,
        [request],
        runtime_contract,
        root=root,
        run_id=run_id,
    )


def _ui_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    exp = _dict(envelope.get("experiment"))
    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    request = _declared_ui_request(spec)
    context = _dict(exp.get("_observer_runtime_context"))
    root_value = _text(context.get("root"))
    project = _text(context.get("project"))
    runtime_contract = _dict(context.get("runtime_contract"))

    def indeterminate(reason_code: str, **detail: Any) -> dict[str, Any]:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={"detail": detail},
        )

    if not request:
        return indeterminate("UI_SOURCE_REQUEST_NOT_DECLARED")
    if not (root_value and project and runtime_contract):
        return indeterminate(
            "UI_RUNTIME_CONTEXT_MISSING",
            missing=[
                key
                for key, value in (
                    ("root", root_value),
                    ("project", project),
                    ("runtime_contract", runtime_contract),
                )
                if not value
            ],
        )
    if _text(runtime_contract.get("status")) != "approved":
        return indeterminate("UI_RUNTIME_CONTRACT_NOT_APPROVED")

    execution = _execute_ui_requests(
        project,
        request,
        runtime_contract,
        root=Path(root_value),
        run_id=_text(envelope.get("execution_id")) or _text(exp.get("execution_id")),
    )
    result = next(
        (dict(row) for row in _list(execution.get("results")) if isinstance(row, dict)),
        {},
    )
    if not result:
        return indeterminate(
            "UI_EXECUTION_RESULT_MISSING",
            execution_status=_text(execution.get("status")),
        )

    planned_steps = _plan_steps(request)
    completed_steps = [
        dict(row) for row in _list(result.get("steps")) if isinstance(row, dict)
    ]
    status = _text(result.get("status")).lower()
    reason = _text(result.get("reason"))
    expectation_satisfied: bool | None = None
    violation_observed = False
    failed_step_index = 0
    failed_step: dict[str, Any] = {}

    if status == "executed":
        # Browser execution aborts on the first unsatisfied expectation.  Reaching the end
        # therefore proves every source-declared expect_text / expect_url step completed.
        expectation_satisfied = True
    elif status == "failed":
        failed_step_index = len(completed_steps) + 1
        if 1 <= failed_step_index <= len(planned_steps):
            failed_step = planned_steps[failed_step_index - 1]
        if _timeout_expectation_failure(reason, failed_step):
            expectation_satisfied = False
            violation_observed = True

    expectations = [
        row
        for row in planned_steps
        if _text(row.get("action")).lower() in _SUPPORTED_EXPECTATIONS
    ]
    evidence = {
        EVIDENCE_KEY: {
            "provider": _text(result.get("provider") or request.get("provider")),
            "request_id": _text(result.get("request_id") or request.get("request_id")),
            "execution_status": status,
            "expectation_satisfied": expectation_satisfied,
            "violation_observed": violation_observed,
            "expectation_count": len(expectations),
            "planned_step_count": len(planned_steps),
            "completed_step_count": len(completed_steps),
            "failed_step_index": failed_step_index,
            "failed_expectation": _expectation_descriptor(failed_step) if failed_step else {},
            "declared_expectations": [_expectation_descriptor(row) for row in expectations],
            "failure_type": reason.split(":", 1)[0] if reason else "",
            "duration_ms": int(result.get("duration_ms") or execution.get("duration_ms") or 0),
            "source_request_fingerprint": _fingerprint(request),
            "artifact_fingerprints": [
                _fingerprint(_dict(row).get("ref"))
                for row in _list(result.get("artifacts"))
                if _text(_dict(row).get("ref"))
            ],
            # Explicitly count but never ingest provider-authored candidate findings.
            "provider_candidate_finding_count": len(_list(result.get("findings"))),
            "provider_findings_consumed": False,
        }
    }
    if expectation_satisfied is None:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=(
                "UI_EXPECTATION_RESULT_UNPROVEN"
                if status == "failed"
                else "UI_EXECUTION_NOT_COMPLETED"
            ),
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_ui_expectation(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    expected = {
        "declared_expectations": _list(observation.get("declared_expectations")),
        "all_source_expectations_must_complete": True,
    }
    satisfied = observation.get("expectation_satisfied")
    if satisfied is True:
        return {
            "passed": True,
            "reason_code": "",
            "expected": expected,
            "actual": {
                "completed_step_count": int(observation.get("completed_step_count") or 0),
                "expectation_satisfied": True,
            },
        }
    if satisfied is False and observation.get("violation_observed") is True:
        return {
            "passed": False,
            "reason_code": "",
            "expected": expected,
            "actual": {
                "expectation_satisfied": False,
                "failed_step_index": int(observation.get("failed_step_index") or 0),
                "failed_expectation": _dict(observation.get("failed_expectation")),
                "failure_type": _text(observation.get("failure_type")),
            },
        }
    return {
        "passed": None,
        "reason_code": "UI_EXPECTATION_RESULT_UNPROVEN",
        "expected": expected,
        "actual": {
            "execution_status": _text(observation.get("execution_status")),
            "expectation_satisfied": satisfied,
        },
    }


def _install_runtime_context_bridge() -> None:
    """Retired: runtime context injection is first-class inside
    ``experiment_executor.execute_one_experiment``. Kept only as a no-op so a
    stale caller cannot re-introduce the method replacement."""
    return None


def install_formal_ui_surface() -> dict[str, str]:
    """Install the observer, assertion, protocol and risk-family links idempotently."""

    # ``ui_browser`` is target-facing and is therefore never a baseline capability.  It is
    # accepted only when the runtime contract explicitly lists it in declared_adapters.
    from . import adapter_capability as _adapter_capability

    _adapter_capability.DECLARATION_REQUIRED.setdefault(
        ADAPTER,
        "runtime_contract.declared_adapters[]",
    )

    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_ui_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID

    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_ui_expectation,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    from .test_obligation import canonical_risk_families, register_risk_family

    if RISK_FAMILY not in canonical_risk_families():
        installed["risk_family"] = register_risk_family(
            RISK_FAMILY,
            relation_types={"observes", "produces", "transitions"},
            protocol_template=PROTOCOL_TEMPLATE,
            observers=[OBSERVER_ID],
            assertion_kind=ASSERTION_KIND,
        )
    else:
        installed["risk_family"] = RISK_FAMILY

    from .experiment_protocol_registry import (
        register_family_protocol,
        registered_family_protocols,
    )

    registered = set(registered_family_protocols())
    for family in (RISK_FAMILY, "visibility", "state"):
        protocol_id = f"{family}:{PROTOCOL_TEMPLATE}"
        if protocol_id not in registered:
            register_family_protocol(
                family,
                PROTOCOL_TEMPLATE,
                compiler=_compile_ui_protocol,
                observers=(OBSERVER_ID,),
                assertion_kind=ASSERTION_KIND,
                emits_control=False,
                per_step_evidence=False,
            )
        installed[f"protocol:{family}"] = protocol_id

    return installed


__all__ = [
    "ADAPTER",
    "ASSERTION_KIND",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "PROTOCOL_TEMPLATE",
    "RISK_FAMILY",
    "install_formal_ui_surface",
]
