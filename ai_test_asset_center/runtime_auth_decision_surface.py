"""Doc-less authorization signal: non-deterministic auth enforcement.

档位 D (runtime-probe contract derivation) already emits
``authorization_formal_contracts`` for a read-only endpoint whose *anonymous*
repeated samples returned BOTH a 2xx (granted) and a 401/403 (denied). That row
is a genuine, observable access-control defect — an attacker can exploit the
flaky decision and legitimate callers get inconsistent access — and it asserts
ONLY the *inconsistency*, never that the endpoint "should" be protected or
public (原则6: no invented business/industry semantics).

This module closes the four-link chain for that signal the same way
``formal_stability_surface`` does for source-declared reliability: it registers
an observer, an assertion kind and a (family, template) protocol — all
additive, none of it a new risk family. The obligation re-issues the anonymous
GET a short window of times and the assertion judges whether the auth decision
stayed consistent.

Discipline (identical to the derivation layer and to AGENTS.md 原则 2/6/7/14):
- The experiment RE-VERIFIES the observed inconsistency under a controlled,
  repeatable request pattern. It does not carry the probe's raw samples as the
  verdict — the controlled re-issue is the Oracle.
- A consistently-closed endpoint (always 401/403) or consistently-open endpoint
  (always 2xx) is NOT a defect under this signal: we only ever assert the
  *mix*. Nothing about what "should" be protected is invented.
- Incomplete coverage (fewer than the required samples) is INDETERMINATE, never
  a silent PASS or a fabricated VIOLATION.
- The risk family is the existing ``authorization`` family — this is an additive
  protocol on it, not a fork (原则 10).
"""
from __future__ import annotations

from collections import Counter
from typing import Any

OBSERVER_ID = "runtime_auth_decision_reader"
EVIDENCE_KEY = "runtime_auth_decision_consistency"
ASSERTION_KIND = "runtime_auth_decision_consistency"
RISK_FAMILY = "authorization"
PROTOCOL_TEMPLATE = "runtime_auth_decision_consistency"
SURFACE = "http_auth_decision"
ADAPTER = "http_api"
_SAFE_METHODS = frozenset({"GET", "HEAD"})
_AUTH_SAMPLE_COUNT = 5


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _contract(spec: dict[str, Any]) -> dict[str, Any]:
    row = _dict(spec)
    direct = _dict(row.get("auth_contract"))
    if direct:
        return direct
    return _dict(_dict(row.get("property")).get("auth_contract"))


def _validated_contract(contract: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    """The contract this protocol verifies.

    The sample budget is the protocol's own, not the probe's. A doc-less signal
    is re-verified under a fixed short window; the probe's observed sample count
    is carried for traceability only.
    """
    row = _dict(contract)
    method = _text(row.get("method")).upper()
    path = _text(row.get("operation_path"))
    if method not in _SAFE_METHODS:
        return None, "AUTH_CONTRACT_METHOD_NOT_GET_OR_HEAD"
    if not path.startswith("/"):
        return None, "AUTH_CONTRACT_PATH_INVALID"
    return {
        "method": method,
        "operation_path": path,
        "sample_count": _AUTH_SAMPLE_COUNT,
        "observed_statuses": list(_list(row.get("observed_statuses"))),
    }, ""


def _compile_runtime_auth_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    contract, reason = _validated_contract(_contract(property_spec))
    if contract is None:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": reason or "auth_contract_missing",
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
            "detail": "auth_operation_missing",
        }
    if _text(operation.get("method")).upper() not in _SAFE_METHODS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_TARGET_POLICY",
            "detail": "auth_signal_requires_get_or_head",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "auth_anonymous_actor_missing",
        }
    assertion_property = _dict(property_spec)
    assertion_property["auth_contract"] = contract
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [
            {
                "step_id": f"auth_sample_{index + 1}",
                "actor_ref": actor_ref,
                "operation_ref": operation_ref,
                "intent": "docless_auth_decision_sample",
                "protocol_step": "auth_sample",
                "property_template": PROTOCOL_TEMPLATE,
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


def _sample_statuses(envelope: dict[str, Any]) -> list[int]:
    steps = [
        dict(row)
        for row in _list(envelope.get("execution_steps"))
        if isinstance(row, dict)
        and _text(row.get("step_id")).startswith("auth_sample_")
    ]
    out: list[int] = []
    for step in steps:
        raw = step.get("status_code")
        if isinstance(raw, bool):
            continue
        try:
            status = int(raw)
        except (TypeError, ValueError):
            continue
        out.append(status)
    return out


def _runtime_auth_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    contract, reason = _validated_contract(_contract(spec))
    if contract is None:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason or "AUTH_CONTRACT_NOT_DECLARED",
            evidence={},
        )
    expected_count = int(contract["sample_count"])
    statuses = _sample_statuses(envelope)
    status_classes: Counter[str] = Counter()
    granted = 0
    denied = 0
    for status in statuses:
        cls = status // 100 if status > 0 else 0
        status_classes[str(cls) if cls else "transport_error"] += 1
        if 200 <= status < 300:
            granted += 1
        elif status in {401, 403}:
            denied += 1
    complete = len(statuses) == expected_count
    evidence = {
        EVIDENCE_KEY: {
            "contract_id": _text(contract.get("contract_id")),
            "method": _text(contract.get("method")),
            "operation_path": _text(contract.get("operation_path")),
            "expected_sample_count": expected_count,
            "observed_sample_count": len(statuses),
            "granted_sample_count": granted,
            "denied_sample_count": denied,
            "status_class_counts": dict(sorted(status_classes.items())),
            "auth_decision_consistent": not (granted and denied),
            "coverage_complete": complete,
            "measurement_semantics": (
                "short_window_anonymous_repeated_read_auth_decision"
            ),
            "raw_response_payloads_included": False,
            "headers_included": False,
            "tokens_included": False,
        }
    }
    if not complete:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="AUTH_SAMPLE_SET_INCOMPLETE",
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_runtime_auth_consistency(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    expected = {
        "method": _text(observation.get("method")),
        "operation_path": _text(observation.get("operation_path")),
        "expected_sample_count": observation.get("expected_sample_count"),
        "signal": "auth_decision_non_deterministic",
    }
    actual = {
        "observed_sample_count": observation.get("observed_sample_count"),
        "granted_sample_count": observation.get("granted_sample_count"),
        "denied_sample_count": observation.get("denied_sample_count"),
        "status_class_counts": _dict(observation.get("status_class_counts")),
        "auth_decision_consistent": observation.get("auth_decision_consistent"),
        "coverage_complete": observation.get("coverage_complete") is True,
    }
    if not actual["coverage_complete"]:
        return {
            "passed": None,
            "reason_code": "AUTH_MEASUREMENT_INCOMPLETE",
            "expected": expected,
            "actual": actual,
        }
    inconsistent = bool(actual["granted_sample_count"]) and bool(
        actual["denied_sample_count"]
    )
    return {
        # Inconsistent auth enforcement across anonymous reads = VIOLATION.
        # A consistently-closed (always 401/403) or consistently-open
        # (always 2xx) endpoint is NOT a defect under this doc-less signal.
        "passed": not inconsistent,
        "reason_code": "" if inconsistent else "AUTH_DECISION_CONSISTENT",
        "expected": expected,
        "actual": {**actual, "auth_decision_inconsistent": inconsistent},
    }


def install_runtime_auth_decision_surface() -> dict[str, str]:
    """Install observer + assertion kind + protocol idempotently.

    The ``authorization`` risk family is already registered (canonical), so this
    module adds ONLY a new protocol template, a new observer and a new assertion
    kind on top of it — no fork (原则 10).
    """
    from .assertion_dsl_base import (
        register_assertion_kind,
        registered_assertion_kinds,
    )
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_runtime_auth_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_runtime_auth_consistency,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    from .experiment_protocol_registry import (
        register_family_protocol,
        registered_family_protocols,
    )

    protocol_id = f"{RISK_FAMILY}:{PROTOCOL_TEMPLATE}"
    if protocol_id not in set(registered_family_protocols()):
        register_family_protocol(
            RISK_FAMILY,
            PROTOCOL_TEMPLATE,
            compiler=_compile_runtime_auth_protocol,
            observers=(OBSERVER_ID,),
            assertion_kind=ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=True,
        )
    installed["protocol"] = protocol_id
    return installed


__all__ = [
    "ASSERTION_KIND",
    "EVIDENCE_KEY",
    "OBSERVER_ID",
    "PROTOCOL_TEMPLATE",
    "RISK_FAMILY",
    "install_runtime_auth_decision_surface",
]
