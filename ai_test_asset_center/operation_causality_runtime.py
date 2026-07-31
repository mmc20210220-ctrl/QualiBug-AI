"""Bind exact request correlation values to the executed treatment operation.

The module wraps existing phase and sequential-plan entrypoints. It does not send
requests or query a database itself. BEFORE freezes the source-declared request-body
value; the plan wrapper verifies the value actually sent; AFTER reuses that actual
value for the approved relation predicate. Only fingerprints enter formal receipts.
"""
from __future__ import annotations

import functools
import hashlib
import json
import sys
from copy import deepcopy
from typing import Any

from .database_relation_delta_causality_projection import ASSERTION_KIND

PREFLIGHT_RECEIPT_SCHEMA = "qualibug.operation-causality-preflight-receipt.v1"
TRANSPORT_RECEIPT_SCHEMA = "qualibug.operation-causality-transport-receipt.v1"
PREFLIGHT_KEY = "operation_causality_preflight_receipts"
TRANSPORT_KEY = "operation_causality_transport_receipts"
_PRIVATE_VALUES_KEY = "_operation_causality_runtime_values"
_PHASE_INSTALL_MARKER = "__qualibug_operation_causality_phase_runtime_v1__"
_PLAN_INSTALL_MARKER = "__qualibug_operation_causality_plan_runtime_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _materialize(value: Any, bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {key: _materialize(child, bindings) for key, child in value.items()}
    if isinstance(value, list):
        return [_materialize(child, bindings) for child in value]
    if isinstance(value, str):
        token = value.strip()
        if token.startswith("{") and token.endswith("}"):
            name = token[1:-1].strip()
            if name in bindings:
                return bindings[name]
    return value


def _path_get(value: Any, parts: list[str]) -> Any:
    current = value
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _path_set(value: dict[str, Any], parts: list[str], replacement: Any) -> None:
    current = value
    for part in parts[:-1]:
        child = current.get(part)
        if not isinstance(child, dict):
            child = {}
            current[part] = child
        current = child
    if parts:
        current[parts[-1]] = replacement


def _causal_assertions(exp: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(exp.get("assertions"))
        if isinstance(row, dict) and _text(row.get("kind")) == ASSERTION_KIND
    ]


def _causal_contract(assertion: dict[str, Any]) -> dict[str, Any]:
    return _dict(assertion.get("causal_attribution_contract"))


def _treatment_step(exp: dict[str, Any], operation_ref: str) -> dict[str, Any]:
    rows = [
        dict(row)
        for row in _list(exp.get("treatment_plan"))
        if isinstance(row, dict)
        and _text(row.get("operation_ref")) == operation_ref
    ]
    return rows[0] if len(rows) == 1 else {}


def _body_value(source: str, body: Any) -> Any:
    prefix = "request.body."
    if not source.startswith(prefix):
        return None
    return _path_get(body, [part for part in source[len(prefix):].split(".") if part])


def _receipt_id(prefix: str, payload: dict[str, Any]) -> str:
    return f"{prefix}_{_fingerprint(payload)[:24]}"


def prepare_operation_causality_preflight(
    *,
    exp: dict[str, Any],
    runtime_bindings: dict[str, Any],
    observations: dict[str, Any],
    campaign_id: str,
    execution_id: str,
) -> list[dict[str, Any]]:
    """Freeze source-declared request values before transport."""
    receipts: list[dict[str, Any]] = []
    private_values = _dict(observations.get(_PRIVATE_VALUES_KEY))
    for assertion in _causal_assertions(exp):
        causal = _causal_contract(assertion)
        operation_ref = _text(causal.get("operation_ref"))
        value_source = _text(causal.get("value_source"))
        scope_fp = _text(causal.get("causal_scope_fingerprint"))
        step = _treatment_step(exp, operation_ref)
        body = _materialize(step.get("body"), runtime_bindings) if step else None
        value = _body_value(value_source, body)
        source_fp = _fingerprint(value)[:20] if value not in (None, "") else ""
        status = "BOUND" if source_fp else "INDETERMINATE"
        reason_code = "" if source_fp else "OPERATION_CAUSAL_PREFLIGHT_VALUE_MISSING"
        payload = {
            "schema": PREFLIGHT_RECEIPT_SCHEMA,
            "assertion_id": _text(assertion.get("assertion_id")),
            "causal_scope_fingerprint": scope_fp,
            "operation_ref": operation_ref,
            "treatment_step_id": _text(causal.get("treatment_step_id") or step.get("step_id")),
            "value_source": value_source,
            "source_value_fingerprint": source_fp,
            "campaign_id": _text(campaign_id),
            "execution_id": _text(execution_id),
            "status": status,
            "reason_code": reason_code,
            "raw_causal_value_retained": False,
            "transport_reached": False,
            "timestamp_window_attribution_used": False,
        }
        payload["receipt_id"] = _receipt_id("causal_preflight", payload)
        receipts.append(payload)
        if source_fp:
            private_values[scope_fp] = {
                "value_source": value_source,
                "value": value,
                "source_value_fingerprint": source_fp,
                "stage": "PREFLIGHT",
            }
    observations[PREFLIGHT_KEY] = receipts
    observations[_PRIVATE_VALUES_KEY] = private_values
    return receipts


def finalize_operation_causality_transport(
    *,
    exp: dict[str, Any],
    result: dict[str, Any],
    observations: dict[str, Any],
    campaign_id: str,
    execution_id: str,
) -> list[dict[str, Any]]:
    """Prove the preflight value is the value used by the actual treatment step."""
    preflight = {
        _text(row.get("causal_scope_fingerprint")): dict(row)
        for row in _list(observations.get(PREFLIGHT_KEY))
        if isinstance(row, dict) and _text(row.get("causal_scope_fingerprint"))
    }
    bodies = _dict(result.get("request_bodies_for_cleanup"))
    steps = [
        dict(row)
        for row in _list(result.get("steps"))
        if isinstance(row, dict)
    ]
    private_values = _dict(observations.get(_PRIVATE_VALUES_KEY))
    receipts: list[dict[str, Any]] = []

    for assertion in _causal_assertions(exp):
        causal = _causal_contract(assertion)
        operation_ref = _text(causal.get("operation_ref"))
        value_source = _text(causal.get("value_source"))
        scope_fp = _text(causal.get("causal_scope_fingerprint"))
        candidates = [
            row
            for row in steps
            if _text(row.get("phase")).lower() == "treatment"
            and _text(row.get("operation_ref")) == operation_ref
        ]
        step = candidates[0] if len(candidates) == 1 else {}
        step_id = _text(step.get("step_id") or step.get("subject_id"))
        body = bodies.get(step_id)
        value = _body_value(value_source, body)
        actual_fp = _fingerprint(value)[:20] if value not in (None, "") else ""
        prior = _dict(preflight.get(scope_fp))
        preflight_fp = _text(prior.get("source_value_fingerprint"))
        governance = _dict(step.get("governance_receipt"))
        transport_receipt_id = _text(
            governance.get("receipt_id") or step.get("request_body_fingerprint")
        )
        request_semantics_fp = _text(step.get("request_semantics_fingerprint"))
        status_code = int(step.get("status_code") or 0)
        exact_step = len(candidates) == 1
        match = bool(
            exact_step
            and actual_fp
            and preflight_fp
            and actual_fp == preflight_fp
            and transport_receipt_id
            and request_semantics_fp
            and status_code > 0
        )
        if not exact_step:
            reason_code = (
                "OPERATION_CAUSAL_TRANSPORT_STEP_MISSING"
                if len(candidates) < 1
                else "OPERATION_CAUSAL_TRANSPORT_STEP_AMBIGUOUS"
            )
        elif not actual_fp:
            reason_code = "OPERATION_CAUSAL_TRANSPORT_VALUE_MISSING"
        elif actual_fp != preflight_fp:
            reason_code = "OPERATION_CAUSAL_SOURCE_VALUE_DRIFT"
        elif not transport_receipt_id or not request_semantics_fp or status_code <= 0:
            reason_code = "OPERATION_CAUSAL_TRANSPORT_EVIDENCE_INCOMPLETE"
        else:
            reason_code = ""
        payload = {
            "schema": TRANSPORT_RECEIPT_SCHEMA,
            "assertion_id": _text(assertion.get("assertion_id")),
            "causal_scope_fingerprint": scope_fp,
            "operation_ref": operation_ref,
            "treatment_step_id": step_id,
            "value_source": value_source,
            "preflight_value_fingerprint": preflight_fp,
            "transport_value_fingerprint": actual_fp,
            "source_value_fingerprint_match": match,
            "request_semantics_fingerprint": request_semantics_fp,
            "transport_receipt_id": transport_receipt_id,
            "status_code": status_code,
            "campaign_id": _text(campaign_id),
            "execution_id": _text(execution_id),
            "status": "ATTRIBUTED" if match else "INDETERMINATE",
            "reason_code": reason_code,
            "transport_reached": status_code > 0,
            "raw_causal_value_retained": False,
            "timestamp_window_attribution_used": False,
        }
        payload["receipt_id"] = _receipt_id("causal_transport", payload)
        receipts.append(payload)
        if actual_fp:
            private_values[scope_fp] = {
                "value_source": value_source,
                "value": value,
                "source_value_fingerprint": actual_fp,
                "stage": "TRANSPORT",
            }
    observations[TRANSPORT_KEY] = receipts
    observations[_PRIVATE_VALUES_KEY] = private_values
    return receipts


def _exp_with_actual_causal_values(
    exp: dict[str, Any], observations: dict[str, Any]
) -> dict[str, Any]:
    output = deepcopy(exp)
    private_values = _dict(observations.get(_PRIVATE_VALUES_KEY))
    if not private_values:
        return output
    plans = [
        dict(row)
        for row in _list(output.get("treatment_plan"))
        if isinstance(row, dict)
    ]
    by_operation = {
        _text(row.get("operation_ref")): row for row in plans if _text(row.get("operation_ref"))
    }
    for assertion in _causal_assertions(output):
        causal = _causal_contract(assertion)
        scope_fp = _text(causal.get("causal_scope_fingerprint"))
        stored = _dict(private_values.get(scope_fp))
        value_source = _text(causal.get("value_source"))
        value = stored.get("value")
        operation_ref = _text(causal.get("operation_ref"))
        step = by_operation.get(operation_ref)
        if not step or value in (None, "") or not value_source.startswith("request.body."):
            continue
        body = deepcopy(step.get("body")) if isinstance(step.get("body"), dict) else {}
        _path_set(
            body,
            [part for part in value_source[len("request.body."):].split(".") if part],
            value,
        )
        step["body"] = body
    output["treatment_plan"] = plans
    return output


def install_operation_causality_runtime() -> None:
    """Install phase and transport wrappers on the existing executor mainline."""
    from . import database_observer_experiment_runtime as phase_runtime
    from . import experiment_plan_executor as plan_runtime

    phase_original = getattr(phase_runtime, "execute_database_observer_phase", None)
    if callable(phase_original) and not getattr(
        phase_original, _PHASE_INSTALL_MARKER, False
    ):
        @functools.wraps(phase_original)
        def phase_wrapped(
            exp: dict[str, Any],
            *args: Any,
            **kwargs: Any,
        ) -> dict[str, Any]:
            phase = _text(kwargs.get("phase") or (args[0] if args else "")).upper()
            observations = kwargs.get("observations")
            runtime_bindings = kwargs.get("runtime_bindings")
            if not isinstance(observations, dict):
                return phase_original(exp, *args, **kwargs)
            if phase == "BEFORE" and _causal_assertions(exp):
                prepare_operation_causality_preflight(
                    exp=exp,
                    runtime_bindings=(runtime_bindings if isinstance(runtime_bindings, dict) else {}),
                    observations=observations,
                    campaign_id=_text(kwargs.get("campaign_id")),
                    execution_id=_text(kwargs.get("execution_id")),
                )
            effective_exp = (
                _exp_with_actual_causal_values(exp, observations)
                if phase == "AFTER"
                else exp
            )
            result = phase_original(effective_exp, *args, **kwargs)
            if phase == "AFTER":
                observations.pop(_PRIVATE_VALUES_KEY, None)
            return result

        setattr(phase_wrapped, _PHASE_INSTALL_MARKER, True)
        setattr(phase_wrapped, "__qualibug_original__", phase_original)
        phase_runtime.execute_database_observer_phase = phase_wrapped
        executor = sys.modules.get(f"{__package__}.experiment_executor")
        if executor is not None:
            executor.execute_database_observer_phase = phase_wrapped

    plan_original = getattr(plan_runtime, "execute_non_barrier_plans", None)
    if callable(plan_original) and not getattr(plan_original, _PLAN_INSTALL_MARKER, False):
        @functools.wraps(plan_original)
        def plan_wrapped(*args: Any, **kwargs: Any) -> dict[str, Any]:
            result = plan_original(*args, **kwargs)
            observations = kwargs.get("observations")
            if isinstance(result, dict) and isinstance(observations, dict):
                exp = kwargs.get("experiment")
                if not isinstance(exp, dict):
                    # The existing plan executor receives plans separately. Recover the
                    # exact causal assertions preloaded by the phase preflight.
                    exp = {
                        "assertions": observations.get("operation_causality_assertions", []),
                    }
                if not _causal_assertions(exp):
                    exp = _dict(observations.get("operation_causality_experiment"))
                if _causal_assertions(exp):
                    finalize_operation_causality_transport(
                        exp=exp,
                        result=result,
                        observations=observations,
                        campaign_id=_text(
                            kwargs.get("resolved_campaign_id") or kwargs.get("campaign_id")
                        ),
                        execution_id=_text(kwargs.get("resolved_execution_id")),
                    )
            return result

        setattr(plan_wrapped, _PLAN_INSTALL_MARKER, True)
        setattr(plan_wrapped, "__qualibug_original__", plan_original)
        plan_runtime.execute_non_barrier_plans = plan_wrapped
        executor = sys.modules.get(f"{__package__}.experiment_executor")
        if executor is not None:
            executor.execute_non_barrier_plans = plan_wrapped


def attach_operation_causality_experiment(
    exp: dict[str, Any], observations: dict[str, Any]
) -> None:
    """Expose only the causal slice needed by the plan wrapper."""
    causal = _causal_assertions(exp)
    if causal:
        observations["operation_causality_assertions"] = deepcopy(causal)
        observations["operation_causality_experiment"] = {
            "assertions": deepcopy(causal),
            "treatment_plan": deepcopy(_list(exp.get("treatment_plan"))),
        }


__all__ = [
    "PREFLIGHT_KEY",
    "TRANSPORT_KEY",
    "attach_operation_causality_experiment",
    "finalize_operation_causality_transport",
    "install_operation_causality_runtime",
    "prepare_operation_causality_preflight",
]
