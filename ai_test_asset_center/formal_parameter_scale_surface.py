"""Executable parameter-scale performance contracts (REPORT-008 class).

Where the latency-budget surface repeats the SAME request, this surface varies
one source-declared scaling query parameter and observes the consequences.
The contract names the parameter and (when the source declares them) its
bounds; the protocol injects probe values (baseline, declared maximum, one
value above the maximum, one escalated value), and the observer reads each
probe's response time and status from the governed execution steps.

Judgments, all observable and source-grounded:

* Source-declared upper bound: an above-bound probe that the target ACCEPTS
  with 2xx violates the declared bound (``PARAMETER_BOUND_NOT_ENFORCED``).
  An optional co-declared latency budget must hold on every probe
  (``PARAMETER_LATENCY_BUDGET_EXCEEDED``).  Above-bound rejection is the
  clean outcome.
* Degradation channel (no declared bound, ``derivation=generic_resource_protection``):
  an escalated probe that cannot complete while the baseline completes is
  resource exhaustion at input magnitude; an escalated probe accepted with
  2xx whose response time is >= 10x the baseline at >= 100x the magnitude is
  an observed unbounded-parameter scaling anomaly.  Both are runtime-observed
  evidence (reproducible, receipted, read-only), never a written-rule claim.

Discipline mirrors ``formal_performance_surface``: a single slow response is
never a defect; missing samples, missing duration evidence, retries or an
incomplete execution set are INDETERMINATE.  ``duration_ms`` is accepted only
when the transport reports exactly one attempt.  Raw response bodies and
headers never enter performance evidence.
"""
from __future__ import annotations

import copy
import math
from typing import Any
from urllib.parse import parse_qs, urlsplit

OBSERVER_ID = "source_http_parameter_scale_reader"
EVIDENCE_KEY = "source_http_parameter_scale"
ASSERTION_KIND = "source_parameter_scale_budget"
RISK_FAMILY = "performance_latency"
PROTOCOL_TEMPLATE = "source_declared_parameter_scale_budget"
SURFACE = "http_parameter_scale"
ADAPTER = "http_api"
CONTRACT_KIND = "parameter_scale_budget"
_SAFE_METHODS = frozenset({"GET", "HEAD"})

# Product-owned measurement methodology (never business facts): probe ceilings,
# generic magnitudes, anomaly ratios.
_ESCALATION_CEILING = 100_000
_GENERIC_MAGNITUDES = (1, 10, 100, 1000, 10000)
_ANOMALY_LATENCY_RATIO = 10.0
_ANOMALY_MAGNITUDE_RATIO = 100.0
_MAX_LATENCY_MS = 120_000


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


def _validated_contract(
    contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    row = copy.deepcopy(_dict(contract))
    if _text(row.get("contract_kind")) not in {"", CONTRACT_KIND}:
        return None, "PARAMETER_SCALE_CONTRACT_KIND_INVALID"
    parameter_name = _text(row.get("parameter_name"))
    if not parameter_name:
        return None, "PARAMETER_SCALE_CONTRACT_PARAMETER_MISSING"
    declared_min: int | None = None
    declared_max: int | None = None
    if row.get("declared_min") is not None:
        try:
            declared_min = int(row.get("declared_min"))
        except (TypeError, ValueError):
            return None, "PARAMETER_SCALE_CONTRACT_BOUND_INVALID"
    if row.get("declared_max") is not None:
        try:
            declared_max = int(row.get("declared_max"))
        except (TypeError, ValueError):
            return None, "PARAMETER_SCALE_CONTRACT_BOUND_INVALID"
    if declared_max is not None and declared_max < 0:
        return None, "PARAMETER_SCALE_CONTRACT_BOUND_INVALID"
    if (
        declared_min is not None
        and declared_max is not None
        and declared_min >= declared_max
    ):
        return None, "PARAMETER_SCALE_CONTRACT_BOUND_INVALID"
    max_latency_ms: float | None = None
    if row.get("max_latency_ms") is not None:
        try:
            max_latency_ms = float(row.get("max_latency_ms"))
        except (TypeError, ValueError):
            return None, "PARAMETER_SCALE_CONTRACT_LATENCY_INVALID"
        if not 0 < max_latency_ms <= _MAX_LATENCY_MS:
            return None, "PARAMETER_SCALE_CONTRACT_LATENCY_INVALID"
    row.update({
        "parameter_name": parameter_name,
        "declared_min": declared_min,
        "declared_max": declared_max,
    })
    if max_latency_ms is not None:
        row["max_latency_ms"] = max_latency_ms
    return row, ""


def _probe_values(
    contract: dict[str, Any],
) -> list[tuple[int, str]]:
    """(value, role) pairs, deduplicated, preserving escalation order."""
    declared_max = contract.get("declared_max")
    declared_min = contract.get("declared_min")
    if declared_max is None:
        return [
            (value, "baseline" if index == 0 else "generic_escalation")
            for index, value in enumerate(_GENERIC_MAGNITUDES)
        ]
    baseline = declared_min if (declared_min is not None and declared_min >= 1) else 1
    planned = [
        (baseline, "baseline"),
        (declared_max, "declared_max"),
        (declared_max + 1, "above_bound"),
        (min(declared_max * 10, _ESCALATION_CEILING), "escalation"),
    ]
    output: list[tuple[int, str]] = []
    seen: set[int] = set()
    for value, role in planned:
        if value in seen:
            continue
        seen.add(value)
        output.append((value, role))
    return output


def _compile_parameter_scale_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    contract, reason = _validated_contract(_declared_contract(property_spec))
    if contract is None:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": reason or "source_declared_parameter_scale_contract_missing",
        }
    operation = _dict(envelope.get("operation"))
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
            "detail": "parameter_scale_operation_missing",
        }
    if _text(operation.get("method")).upper() not in _SAFE_METHODS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_TARGET_POLICY",
            "detail": "parameter_scale_requires_get_or_head",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "parameter_scale_actor_missing",
        }
    parameter_name = _text(contract.get("parameter_name"))
    generic = contract.get("declared_max") is None
    treatment_plan: list[dict[str, Any]] = []
    for index, (value, role) in enumerate(_probe_values(contract), start=1):
        treatment_plan.append({
            "step_id": f"param_scale_probe_{index}",
            "actor_ref": actor_ref,
            "operation_ref": operation_ref,
            "intent": (
                "generic_parameter_scale_probe"
                if generic
                else "source_declared_parameter_scale_probe"
            ),
            "protocol_step": "parameter_scale_probe",
            "query": {parameter_name: str(value)},
            "probe_value": value,
            "probe_role": role,
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
            "rule_id": _text(
                property_spec.get("invariant_ref") or property_spec.get("rule_id")
            ),
        },
        "per_step_evidence": True,
    }


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


def _probe_value_from_path(path: str, parameter_name: str) -> int | None:
    """The value actually injected is the materialized query value."""
    query = urlsplit(_text(path)).query
    values = parse_qs(query).get(parameter_name, [])
    if not values:
        return None
    try:
        return int(values[0])
    except (TypeError, ValueError):
        return None


def _parameter_scale_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    contract, reason = _validated_contract(_declared_contract(spec))
    if contract is None:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason or "PARAMETER_SCALE_CONTRACT_NOT_DECLARED",
            evidence={},
        )
    steps = [
        dict(row)
        for row in _list(envelope.get("execution_steps"))
        if isinstance(row, dict)
        and _text(row.get("step_id")).startswith("param_scale_probe_")
    ]
    expected_count = len(_probe_values(contract))
    parameter_name = _text(contract.get("parameter_name"))
    probes: list[dict[str, Any]] = []
    missing_attempt_count = 0
    missing_value_count = 0
    invalid_status_count = 0
    retried_sample_count = 0
    for step in steps:
        attempts = _transport_attempt_count(step)
        if attempts is None:
            missing_attempt_count += 1
            value = _probe_value_from_path(_text(step.get("path")), parameter_name)
            duration = None
        else:
            if attempts != 1:
                retried_sample_count += 1
                duration = None
            else:
                raw_duration = step.get("duration_ms")
                try:
                    parsed_duration = float(raw_duration)
                except (TypeError, ValueError):
                    parsed_duration = None
                duration = (
                    parsed_duration
                    if parsed_duration is not None and parsed_duration >= 0
                    else None
                )
            value = _probe_value_from_path(_text(step.get("path")), parameter_name)
        if value is None:
            missing_value_count += 1
        raw_status = step.get("status_code")
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            invalid_status_count += 1
            status = 0
        probes.append({
            "value": value,
            "role": _text(step.get("probe_role")) or _text(step.get("intent")),
            "status_code": status,
            "accepted": 200 <= status < 300,
            "transport_failed": status <= 0,
            "duration_ms": duration,
            "retried": attempts is not None and attempts > 1,
            "missing_attempt_count": attempts is None,
        })

    evidence_base = {
        "contract_id": _text(contract.get("contract_id")),
        "parameter_name": parameter_name,
        "declared_min": contract.get("declared_min"),
        "declared_max": contract.get("declared_max"),
        "max_latency_ms": contract.get("max_latency_ms"),
        "expected_probe_count": expected_count,
        "observed_probe_count": len(probes),
        "missing_attempt_count": missing_attempt_count,
        "missing_value_count": missing_value_count,
        "invalid_status_count": invalid_status_count,
        "retried_sample_count": retried_sample_count,
        "measurement_semantics": (
            "sequential_get_or_head_parameter_scale_probes_single_attempt"
        ),
        "raw_response_payloads_included": False,
        "headers_included": False,
    }
    coverage_complete = (
        len(probes) == expected_count
        and missing_attempt_count == 0
        and missing_value_count == 0
        and invalid_status_count == 0
    )
    if not coverage_complete:
        reason_code = (
            "PARAMETER_SCALE_SAMPLE_SET_INCOMPLETE"
            if len(probes) != expected_count
            else "PARAMETER_SCALE_TRANSPORT_ATTEMPT_COUNT_MISSING"
            if missing_attempt_count
            else "PARAMETER_SCALE_PROBE_VALUE_MISSING"
            if missing_value_count
            else "PARAMETER_SCALE_STATUS_INVALID"
        )
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={EVIDENCE_KEY: {**evidence_base, "probes": probes}},
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence={EVIDENCE_KEY: {
            **evidence_base,
            "probes": probes,
            "coverage_complete": True,
        }},
    )


def _single_attempt_durations(
    observation: dict[str, Any],
) -> list[tuple[int, float]]:
    output: list[tuple[int, float]] = []
    for probe in _list(observation.get("probes")):
        if not isinstance(probe, dict):
            continue
        value = probe.get("value")
        duration = probe.get("duration_ms")
        if value is None or duration is None or probe.get("retried") is True:
            continue
        try:
            output.append((int(value), float(duration)))
        except (TypeError, ValueError):
            continue
    return output


def _evaluate_parameter_scale_budget(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    contract = _validated_contract({
        "contract_kind": CONTRACT_KIND,
        "parameter_name": observation.get("parameter_name"),
        "declared_min": observation.get("declared_min"),
        "declared_max": observation.get("declared_max"),
        "max_latency_ms": observation.get("max_latency_ms"),
    })[0]
    probes = [dict(row) for row in _list(observation.get("probes")) if isinstance(row, dict)]
    expected = {
        "parameter_name": observation.get("parameter_name"),
        "declared_min": observation.get("declared_min"),
        "declared_max": observation.get("declared_max"),
        "max_latency_ms": observation.get("max_latency_ms"),
        "probe_count": observation.get("expected_probe_count"),
        "measurement_semantics": _text(observation.get("measurement_semantics")),
    }
    actual = {
        "probes": probes,
        "coverage_complete": observation.get("coverage_complete") is True,
    }
    if not actual["coverage_complete"]:
        return {
            "passed": None,
            "reason_code": "PARAMETER_SCALE_MEASUREMENT_INCOMPLETE",
            "expected": expected,
            "actual": actual,
        }
    if contract is None or not probes:
        return {
            "passed": None,
            "reason_code": "PARAMETER_SCALE_MEASUREMENT_UNJUDGEABLE",
            "expected": expected,
            "actual": actual,
        }
    declared_max = contract.get("declared_max")
    declared_min = contract.get("declared_min")
    max_latency_ms = contract.get("max_latency_ms")

    baseline = next(
        (row for row in probes if _text(row.get("role")) == "baseline"),
        None,
    )

    # ── Case A: source-declared upper bound ──
    if declared_max is not None:
        above_bound = [
            row
            for row in probes
            if row.get("value") is not None and int(row["value"]) > int(declared_max)
        ]
        accepted_above = [row for row in above_bound if row.get("accepted") is True]
        if accepted_above:
            return {
                "passed": False,
                "reason_code": "PARAMETER_BOUND_NOT_ENFORCED",
                "expected": {
                    **expected,
                    "above_bound_values_must_be_rejected": [
                        row.get("value") for row in above_bound
                    ],
                },
                "actual": {
                    **actual,
                    "accepted_above_bound_values": [
                        {
                            "value": row.get("value"),
                            "status_code": row.get("status_code"),
                            "duration_ms": row.get("duration_ms"),
                        }
                        for row in accepted_above
                    ],
                    "boundary_enforcement_observation": True,
                },
            }
        if max_latency_ms is not None:
            for value, duration in _single_attempt_durations(observation):
                if duration > float(max_latency_ms):
                    return {
                        "passed": False,
                        "reason_code": "PARAMETER_LATENCY_BUDGET_EXCEEDED",
                        "expected": {
                            **expected,
                            "max_latency_ms": max_latency_ms,
                        },
                        "actual": {
                            **actual,
                            "latency_budget_exceeded_at_value": value,
                            "observed_duration_ms": duration,
                        },
                    }
        return {
            "passed": True,
            "reason_code": "PARAMETER_BOUND_ENFORCED",
            "expected": expected,
            "actual": {
                **actual,
                "above_bound_rejected": [
                    row.get("value") for row in above_bound
                ],
            },
        }

    # ── Case B: degradation channel (no declared bound) ──
    if baseline is None or baseline.get("accepted") is not True:
        return {
            "passed": None,
            "reason_code": "PARAMETER_SCALE_BASELINE_NOT_ESTABLISHED",
            "expected": expected,
            "actual": actual,
        }
    baseline_duration = baseline.get("duration_ms")
    exhausted = [
        row
        for row in probes
        if row.get("transport_failed") is True
        and row.get("value") is not None
        and int(row["value"]) > int(baseline["value"] or 0)
    ]
    if exhausted:
        return {
            "passed": False,
            "reason_code": "RESOURCE_EXHAUSTION_AT_INPUT_MAGNITUDE",
            "expected": {
                **expected,
                "baseline_value": baseline.get("value"),
                "baseline_status_code": baseline.get("status_code"),
            },
            "actual": {
                **actual,
                "transport_failed_probes": [
                    {
                        "value": row.get("value"),
                        "status_code": row.get("status_code"),
                        "duration_ms": row.get("duration_ms"),
                    }
                    for row in exhausted
                ],
                "runtime_observed_evidence": True,
                "claim_derivation": "generic_resource_protection",
            },
        }
    baseline_value = int(baseline["value"] or 0)
    durations = _single_attempt_durations(observation)
    if (
        baseline_duration is not None
        and baseline_value > 0
        and len(durations) >= 2
    ):
        largest = max(durations, key=lambda pair: pair[0])
        value, duration = largest
        if (
            value >= baseline_value * _ANOMALY_MAGNITUDE_RATIO
            and duration >= float(baseline_duration) * _ANOMALY_LATENCY_RATIO
        ):
            return {
                "passed": False,
                "reason_code": "UNBOUNDED_PARAMETER_SCALING_OBSERVED",
                "expected": {
                    **expected,
                    "anomaly_definition": {
                        "magnitude_ratio": _ANOMALY_MAGNITUDE_RATIO,
                        "latency_ratio": _ANOMALY_LATENCY_RATIO,
                        "methodology": True,
                    },
                },
                "actual": {
                    **actual,
                    "baseline_value": baseline_value,
                    "baseline_duration_ms": baseline_duration,
                    "largest_probe_value": value,
                    "largest_probe_duration_ms": duration,
                    "runtime_observed_evidence": True,
                    "claim_derivation": "generic_resource_protection",
                },
            }
    return {
        "passed": True,
        "reason_code": "NO_SCALING_ANOMALY_OBSERVED",
        "expected": expected,
        "actual": {
            **actual,
            "baseline_value": baseline_value,
            "baseline_duration_ms": baseline_duration,
            "observed_durations_by_value": durations,
        },
    }


def install_formal_parameter_scale_surface() -> dict[str, str]:
    """Install observer, assertion and protocol idempotently."""
    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_parameter_scale_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_parameter_scale_budget,
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

    from .experiment_protocol_registry import (
        register_family_protocol,
        registered_family_protocols,
    )

    protocol_id = f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"
    if protocol_id not in set(registered_family_protocols()):
        register_family_protocol(
            RISK_FAMILY,
            PROTOCOL_TEMPLATE,
            compiler=_compile_parameter_scale_protocol,
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
    "CONTRACT_KIND",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "PROTOCOL_TEMPLATE",
    "RISK_FAMILY",
    "SURFACE",
    "_compile_parameter_scale_protocol",
    "_evaluate_parameter_scale_budget",
    "_parameter_scale_observer_handler",
    "_probe_values",
    "install_formal_parameter_scale_surface",
]
