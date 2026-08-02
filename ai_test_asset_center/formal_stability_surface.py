"""Formal short-window reliability contracts for source-declared reads.

This first stability increment repeats one exact GET/HEAD operation and judges two
source-declared budgets:

* maximum failed samples (transport failure or non-2xx);
* maximum samples that required more than one transport attempt.

Retries are evidence here, unlike latency measurement where their backoff makes
duration untrustworthy. The scope is intentionally narrow: sequential short-window
read reliability only, not long-running soak, load, concurrency, recovery, failover
or disaster tolerance. Raw payloads, headers and tokens never enter receipts.
"""
from __future__ import annotations

import copy
from collections import Counter
from typing import Any

OBSERVER_ID = "source_http_read_stability_reader"
EVIDENCE_KEY = "source_http_read_stability"
ASSERTION_KIND = "source_read_stability_budget"
RISK_FAMILY = "stability_reliability"
PROTOCOL_TEMPLATE = "source_declared_read_stability"
SURFACE = "http_read_stability"
ADAPTER = "http_api"
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_MIN_SAMPLES = 5
_MAX_SAMPLES = 20


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _declared_contract(spec: dict[str, Any]) -> dict[str, Any]:
    row = _dict(spec)
    direct = copy.deepcopy(_dict(row.get("stability_contract")))
    if direct:
        return direct
    return copy.deepcopy(_dict(_dict(row.get("property")).get("stability_contract")))


def _validated_contract(
    contract: dict[str, Any],
) -> tuple[dict[str, Any] | None, str]:
    row = copy.deepcopy(_dict(contract))
    try:
        sample_count = int(row.get("sample_count"))
        max_failed = int(row.get("max_failed_samples"))
        max_retried = int(row.get("max_retried_samples"))
        expected_class = int(row.get("expected_status_class"))
    except (TypeError, ValueError):
        return None, "STABILITY_CONTRACT_NUMERIC_FIELD_INVALID"
    if not _MIN_SAMPLES <= sample_count <= _MAX_SAMPLES:
        return None, "STABILITY_SAMPLE_COUNT_INVALID"
    if not 0 <= max_failed <= sample_count:
        return None, "STABILITY_FAILED_SAMPLE_BUDGET_INVALID"
    if not 0 <= max_retried <= sample_count:
        return None, "STABILITY_RETRY_SAMPLE_BUDGET_INVALID"
    if expected_class != 2:
        return None, "STABILITY_SUCCESS_STATUS_CLASS_REQUIRED"
    row.update({
        "sample_count": sample_count,
        "max_failed_samples": max_failed,
        "max_retried_samples": max_retried,
        "expected_status_class": 2,
    })
    return row, ""


def _compile_stability_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    contract, reason = _validated_contract(_declared_contract(property_spec))
    if contract is None:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": reason or "source_declared_stability_contract_missing",
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
            "detail": "stability_operation_missing",
        }
    if _text(operation.get("method")).upper() not in _SAFE_METHODS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_TARGET_POLICY",
            "detail": "stability_first_increment_requires_get_or_head",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "stability_actor_missing",
        }
    assertion_property = copy.deepcopy(property_spec)
    assertion_property["stability_contract"] = contract
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": f"stability_sample_{index + 1}",
                "actor_ref": actor_ref,
                "operation_ref": operation_ref,
                "intent": "source_declared_read_stability_sample",
                "protocol_step": "stability_sample",
            }
            for index in range(int(contract["sample_count"]))
        ],
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


def _attempt_count(step: dict[str, Any]) -> int | None:
    value = _dict(step.get("raw")).get("_attempts")
    if isinstance(value, bool):
        return None
    try:
        attempts = int(value)
    except (TypeError, ValueError):
        return None
    return attempts if attempts > 0 else None


def _stability_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    contract, reason = _validated_contract(_declared_contract(spec))
    if contract is None:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason or "STABILITY_CONTRACT_NOT_DECLARED",
            evidence={},
        )
    steps = [
        dict(row)
        for row in _list(envelope.get("execution_steps"))
        if isinstance(row, dict)
        and _text(row.get("step_id")).startswith("stability_sample_")
    ]
    expected_count = int(contract["sample_count"])
    missing_attempts = 0
    invalid_statuses = 0
    failed = 0
    retried = 0
    status_classes: Counter[str] = Counter()
    attempt_distribution: Counter[str] = Counter()
    for step in steps:
        attempts = _attempt_count(step)
        if attempts is None:
            missing_attempts += 1
        else:
            attempt_distribution[str(attempts)] += 1
            if attempts > 1:
                retried += 1
        raw_status = step.get("status_code")
        if isinstance(raw_status, bool):
            invalid_statuses += 1
            continue
        try:
            status = int(raw_status)
        except (TypeError, ValueError):
            invalid_statuses += 1
            continue
        status_class = status // 100 if status > 0 else 0
        status_classes[str(status_class) if status_class else "transport_error"] += 1
        if status_class != 2:
            failed += 1

    evidence = {
        EVIDENCE_KEY: {
            "contract_id": _text(contract.get("contract_id")),
            "expected_sample_count": expected_count,
            "observed_sample_count": len(steps),
            "missing_attempt_count": missing_attempts,
            "invalid_status_count": invalid_statuses,
            "failed_sample_count": failed,
            "retried_sample_count": retried,
            "max_failed_samples": int(contract["max_failed_samples"]),
            "max_retried_samples": int(contract["max_retried_samples"]),
            "expected_status_class": 2,
            "status_class_counts": dict(sorted(status_classes.items())),
            "transport_attempt_distribution": dict(
                sorted(attempt_distribution.items())
            ),
            "coverage_complete": (
                len(steps) == expected_count
                and missing_attempts == 0
                and invalid_statuses == 0
            ),
            "measurement_semantics": (
                "short_window_sequential_get_or_head_reliability"
            ),
            "raw_response_payloads_included": False,
            "headers_included": False,
            "tokens_included": False,
        }
    }
    if len(steps) != expected_count or missing_attempts or invalid_statuses:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="STABILITY_SAMPLE_SET_INCOMPLETE",
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_stability_budget(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    expected = {
        "sample_count": observation.get("expected_sample_count"),
        "max_failed_samples": observation.get("max_failed_samples"),
        "max_retried_samples": observation.get("max_retried_samples"),
        "expected_status_class": 2,
    }
    actual = {
        "observed_sample_count": observation.get("observed_sample_count"),
        "failed_sample_count": observation.get("failed_sample_count"),
        "retried_sample_count": observation.get("retried_sample_count"),
        "status_class_counts": _dict(observation.get("status_class_counts")),
        "transport_attempt_distribution": _dict(
            observation.get("transport_attempt_distribution")
        ),
        "coverage_complete": observation.get("coverage_complete") is True,
        "measurement_semantics": _text(observation.get("measurement_semantics")),
    }
    if not actual["coverage_complete"]:
        return {
            "passed": None,
            "reason_code": "STABILITY_MEASUREMENT_INCOMPLETE",
            "expected": expected,
            "actual": actual,
        }
    try:
        failed_exceeded = int(actual["failed_sample_count"]) > int(
            expected["max_failed_samples"]
        )
        retried_exceeded = int(actual["retried_sample_count"]) > int(
            expected["max_retried_samples"]
        )
    except (TypeError, ValueError):
        return {
            "passed": None,
            "reason_code": "STABILITY_MEASUREMENT_UNJUDGEABLE",
            "expected": expected,
            "actual": actual,
        }
    return {
        "passed": not (failed_exceeded or retried_exceeded),
        "reason_code": "",
        "expected": expected,
        "actual": {
            **actual,
            "failed_sample_budget_exceeded": failed_exceeded,
            "retried_sample_budget_exceeded": retried_exceeded,
        },
    }


def install_formal_stability_surface() -> dict[str, str]:
    """Install observer, assertion, family and protocol idempotently."""

    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_stability_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_stability_budget,
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
            compiler=_compile_stability_protocol,
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
    "install_formal_stability_surface",
]
