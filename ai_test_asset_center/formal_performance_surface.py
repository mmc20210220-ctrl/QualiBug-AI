"""Source-declared latency budgets on the formal experiment mainline.

The first performance increment measures repeated, governed, read-only HTTP operations. It does
not claim load capacity, throughput, concurrency or long-duration stability. A contract must
explicitly declare its sample count, percentile, latency threshold, expected status class and
maximum error rate. The protocol emits the exact number of sequential read steps; the observer
summarizes only those step receipts.

A single slow response is never a defect. Missing samples, missing duration evidence, retries or
an incomplete execution set are INDETERMINATE. ``duration_ms`` is accepted only when the transport
reports exactly one attempt: a retried duration includes client backoff and cannot be attributed
to the target. Raw response bodies and headers are never copied into performance evidence.
"""
from __future__ import annotations

import copy
import math
from collections import Counter
from typing import Any

OBSERVER_ID = "source_http_latency_series_reader"
EVIDENCE_KEY = "source_http_latency_series"
ASSERTION_KIND = "source_latency_budget"
RISK_FAMILY = "performance_latency"
PROTOCOL_TEMPLATE = "source_declared_latency_budget"
SURFACE = "http_latency_series"
ADAPTER = "http_api"
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_ALLOWED_PERCENTILES = frozenset({"p50", "p90", "p95", "p99", "max"})
_MIN_SAMPLES = 3
_MAX_SAMPLES = 20
_MAX_WARMUPS = 3
_MAX_THRESHOLD_MS = 120_000


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _declared_contract(spec: dict[str, Any]) -> dict[str, Any]:
    row = _dict(spec)
    contract = copy.deepcopy(_dict(row.get("performance_contract")))
    if contract:
        return contract
    return copy.deepcopy(_dict(_dict(row.get("property")).get("performance_contract")))


def _validated_contract(contract: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    row = copy.deepcopy(_dict(contract))
    try:
        sample_count = int(row.get("sample_count"))
        warmup_count = int(row.get("warmup_count") or 0)
        threshold_ms = float(row.get("max_latency_ms"))
        max_error_rate = float(row.get("max_error_rate"))
        expected_status_class = int(row.get("expected_status_class"))
    except (TypeError, ValueError):
        return None, "PERFORMANCE_CONTRACT_NUMERIC_FIELD_INVALID"
    percentile = _text(row.get("percentile")).lower()
    if sample_count < _MIN_SAMPLES or sample_count > _MAX_SAMPLES:
        return None, "PERFORMANCE_SAMPLE_COUNT_INVALID"
    if warmup_count < 0 or warmup_count > _MAX_WARMUPS:
        return None, "PERFORMANCE_WARMUP_COUNT_INVALID"
    if percentile not in _ALLOWED_PERCENTILES:
        return None, "PERFORMANCE_PERCENTILE_INVALID"
    if threshold_ms <= 0 or threshold_ms > _MAX_THRESHOLD_MS:
        return None, "PERFORMANCE_LATENCY_THRESHOLD_INVALID"
    if max_error_rate < 0 or max_error_rate > 1:
        return None, "PERFORMANCE_ERROR_RATE_INVALID"
    if expected_status_class not in {2, 3, 4, 5}:
        return None, "PERFORMANCE_STATUS_CLASS_INVALID"
    row.update({
        "sample_count": sample_count,
        "warmup_count": warmup_count,
        "percentile": percentile,
        "max_latency_ms": threshold_ms,
        "max_error_rate": max_error_rate,
        "expected_status_class": expected_status_class,
    })
    return row, ""


def _compile_performance_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    contract, reason = _validated_contract(_declared_contract(property_spec))
    if contract is None:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": reason or "source_declared_performance_contract_missing",
        }
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    actor_ref = _text(
        envelope.get("treatment_actor_ref")
        or envelope.get("control_actor_ref")
        or property_spec.get("actor_ref")
    )
    method = _text(operation.get("method")).upper()
    if not operation_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "performance_operation_missing",
        }
    if method not in _SAFE_METHODS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_TARGET_POLICY",
            "detail": "performance_first_increment_requires_get_or_head",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "performance_actor_missing",
        }

    treatment_plan: list[dict[str, Any]] = []
    for index in range(int(contract["warmup_count"])):
        treatment_plan.append({
            "step_id": f"performance_warmup_{index + 1}",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "source_declared_latency_warmup",
            "protocol_step": "performance_warmup",
        })
    for index in range(int(contract["sample_count"])):
        treatment_plan.append({
            "step_id": f"performance_sample_{index + 1}",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": "source_declared_latency_sample",
            "protocol_step": "performance_sample",
        })
    assertion_property = copy.deepcopy(property_spec)
    assertion_property["performance_contract"] = contract
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": treatment_plan,
        "observers": [{"observer_id": OBSERVER_ID}],
        "assertion": {
            "kind": ASSERTION_KIND,
            "property": assertion_property,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
            "rule_id": _text(property_spec.get("invariant_ref") or property_spec.get("rule_id")),
        },
        "per_step_evidence": True,
    }


def _nearest_rank(values: list[float], percentile: str) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("performance_samples_empty")
    if percentile == "max":
        return ordered[-1]
    quantile = {
        "p50": 0.50,
        "p90": 0.90,
        "p95": 0.95,
        "p99": 0.99,
    }[percentile]
    rank = max(1, math.ceil(quantile * len(ordered)))
    return ordered[rank - 1]


def _transport_attempt_count(step: dict[str, Any]) -> int | None:
    """Return the transport's declared attempt count; missing is untrustworthy."""
    raw = _dict(step.get("raw"))
    value = raw.get("_attempts")
    if isinstance(value, bool):
        return None
    try:
        attempts = int(value)
    except (TypeError, ValueError):
        return None
    return attempts if attempts > 0 else None


def _performance_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    contract, reason = _validated_contract(_declared_contract(spec))
    if contract is None:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason or "PERFORMANCE_CONTRACT_NOT_DECLARED",
            evidence={},
        )
    steps = [
        dict(row)
        for row in _list(envelope.get("execution_steps"))
        if isinstance(row, dict)
        and _text(row.get("step_id")).startswith("performance_sample_")
    ]
    expected_count = int(contract["sample_count"])
    durations: list[float] = []
    status_codes: list[int] = []
    missing_duration = 0
    missing_attempt_count = 0
    retried_sample_count = 0
    for step in steps:
        attempts = _transport_attempt_count(step)
        if attempts is None:
            missing_attempt_count += 1
            continue
        if attempts != 1:
            retried_sample_count += 1
            continue
        raw_duration = step.get("duration_ms")
        try:
            duration = float(raw_duration)
        except (TypeError, ValueError):
            missing_duration += 1
            continue
        if duration < 0:
            missing_duration += 1
            continue
        durations.append(duration)
        try:
            status_codes.append(int(step.get("status_code") or 0))
        except (TypeError, ValueError):
            status_codes.append(0)

    evidence_base = {
        "contract_id": _text(contract.get("contract_id")),
        "expected_sample_count": expected_count,
        "observed_sample_count": len(steps),
        "duration_sample_count": len(durations),
        "missing_duration_count": missing_duration,
        "missing_attempt_count": missing_attempt_count,
        "retried_sample_count": retried_sample_count,
        "percentile": _text(contract.get("percentile")),
        "percentile_method": "nearest_rank",
        "max_latency_ms": float(contract.get("max_latency_ms")),
        "max_error_rate": float(contract.get("max_error_rate")),
        "expected_status_class": int(contract.get("expected_status_class")),
        "raw_response_payloads_included": False,
        "headers_included": False,
        "transport_backoff_included": False,
    }
    if (
        len(steps) != expected_count
        or missing_duration
        or missing_attempt_count
        or retried_sample_count
        or len(durations) != expected_count
    ):
        reason_code = (
            "PERFORMANCE_RETRIED_TRANSPORT_UNTRUSTWORTHY"
            if retried_sample_count
            else "PERFORMANCE_TRANSPORT_ATTEMPT_COUNT_MISSING"
            if missing_attempt_count
            else "PERFORMANCE_SAMPLE_SET_INCOMPLETE"
        )
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={EVIDENCE_KEY: evidence_base},
        )

    expected_class = int(contract["expected_status_class"])
    error_count = sum(
        1
        for status in status_codes
        if status <= 0 or status // 100 != expected_class
    )
    error_rate = error_count / expected_count
    observed_percentile = _nearest_rank(durations, _text(contract["percentile"]))
    evidence = {
        EVIDENCE_KEY: {
            **evidence_base,
            "latency_samples_ms": durations,
            "latency_min_ms": min(durations),
            "latency_max_ms": max(durations),
            "latency_mean_ms": sum(durations) / len(durations),
            "observed_percentile_ms": observed_percentile,
            "status_class_counts": dict(sorted(Counter(
                str(status // 100) if status > 0 else "transport_error"
                for status in status_codes
            ).items())),
            "error_count": error_count,
            "observed_error_rate": error_rate,
            "coverage_complete": True,
            "measurement_semantics": "sequential_get_or_head_single_attempt_samples",
        }
    }
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_latency_budget(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    expected = {
        "percentile": _text(observation.get("percentile")),
        "max_latency_ms": observation.get("max_latency_ms"),
        "max_error_rate": observation.get("max_error_rate"),
        "expected_status_class": observation.get("expected_status_class"),
        "sample_count": observation.get("expected_sample_count"),
    }
    actual = {
        "observed_percentile_ms": observation.get("observed_percentile_ms"),
        "observed_error_rate": observation.get("observed_error_rate"),
        "duration_sample_count": observation.get("duration_sample_count"),
        "status_class_counts": _dict(observation.get("status_class_counts")),
        "coverage_complete": observation.get("coverage_complete") is True,
        "measurement_semantics": _text(observation.get("measurement_semantics")),
        "percentile_method": _text(observation.get("percentile_method")),
    }
    if not actual["coverage_complete"]:
        return {
            "passed": None,
            "reason_code": "PERFORMANCE_MEASUREMENT_INCOMPLETE",
            "expected": expected,
            "actual": actual,
        }
    try:
        latency_failed = float(actual["observed_percentile_ms"]) > float(expected["max_latency_ms"])
        error_failed = float(actual["observed_error_rate"]) > float(expected["max_error_rate"])
    except (TypeError, ValueError):
        return {
            "passed": None,
            "reason_code": "PERFORMANCE_MEASUREMENT_UNJUDGEABLE",
            "expected": expected,
            "actual": actual,
        }
    return {
        "passed": not (latency_failed or error_failed),
        "reason_code": "",
        "expected": expected,
        "actual": {
            **actual,
            "latency_budget_exceeded": latency_failed,
            "error_rate_budget_exceeded": error_failed,
        },
    }


def install_formal_performance_surface() -> dict[str, str]:
    """Install observer, assertion, risk family and protocol idempotently."""

    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_performance_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_latency_budget,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    from .test_obligation import canonical_risk_families, register_risk_family

    if RISK_FAMILY not in canonical_risk_families():
        installed["risk_family"] = register_risk_family(
            RISK_FAMILY,
            relation_types={"observes"},
            protocol_template=PROTOCOL_TEMPLATE,
            observers=[OBSERVER_ID],
            assertion_kind=ASSERTION_KIND,
        )
    else:
        installed["risk_family"] = RISK_FAMILY

    from .experiment_protocol_registry import register_family_protocol, registered_family_protocols

    protocol_id = f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"
    if protocol_id not in set(registered_family_protocols()):
        register_family_protocol(
            RISK_FAMILY,
            PROTOCOL_TEMPLATE,
            compiler=_compile_performance_protocol,
            observers=(OBSERVER_ID,),
            assertion_kind=ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=True,
        )
    installed["protocol"] = protocol_id
    return installed


__all__ = [
    "ADAPTER",
    "ASSERTION_KIND",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "PROTOCOL_TEMPLATE",
    "RISK_FAMILY",
    "install_formal_performance_surface",
]
