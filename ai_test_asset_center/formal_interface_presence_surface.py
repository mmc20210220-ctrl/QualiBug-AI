"""Formal interface-presence contract for source-declared read operations.

A source document that declares an endpoint the deployed target does not
implement is a real, reproducible documentation/implementation-drift defect
(AGENTS.md: a runtime-observed bug with no written source rule is still a bug).
The product previously only recognized this as a *control-arm failure* — the
control arm of an authorization/validation obligation hit a framework-level
``Cannot METHOD`` 404 and the experiment was buried as
``BLOCKED_CONTROL_ARM_NOT_PROVEN``, so the drift defect could never become
customer-deliverable.

This surface closes the four-link reachability chain for that defect class:

* **risk family** ``interface_contract`` — an operation the source declares;
* **assertion kind** ``interface_presence`` — the declared interface must resolve
  to a non-framework HTTP response (a registered route), judged by media type;
* **observer** ``source_http_interface_presence_reader`` — reads the executed
  step's status code and response content-type;
* **protocol** ``(interface_contract, source_declared_interface_presence)`` —
  one governed GET/HEAD read against the declared route, no control arm, no
  write, no cleanup.

Scope is intentionally read-only (GET/HEAD). A write endpoint cannot be probed
for existence without sending the write itself, which would cross the governed
write boundary for a mere presence check; write-endpoint drift remains on the
existing control-arm detection channel. This is a safety bound, not a hidden
ceiling: it is receipted as such and can be widened later with a governed
presence mechanism.

The ``interface_present`` verdict is fail-closed: transport never attempted,
a 5xx (route exists but broke), or a missing content-type seal INDETERMINATE —
never a fabricated PASS and never a fabricated defect. A business 4xx
(application/json, e.g. "sku not found") proves the route is registered and is
a PASS; only the framework default 404 (text/html / ``Cannot METHOD``) is the
VIOLATION.
"""
from __future__ import annotations

import re
from typing import Any

from .sandbox_write_executor_base import _content_type, framework_route_not_found

OBSERVER_ID = "source_http_interface_presence_reader"
EVIDENCE_KEY = "source_http_interface_presence"
ASSERTION_KIND = "interface_presence"
RISK_FAMILY = "interface_contract"
PROTOCOL_TEMPLATE = "source_declared_interface_presence"
SURFACE = "http_route_presence"
ADAPTER = "http_api"
_SAFE_METHODS = frozenset({"GET", "HEAD"})

# A route-existence probe needs a concrete path, but the declared path may carry
# a placeholder (``/api/products/{id}``). The placeholder value is irrelevant to
# the framework-vs-business 404 distinction: an unregistered route returns
# ``Cannot METHOD`` for *any* concrete value, while a registered route returns a
# business 4xx/2xx. This sentinel is a product-internal probe identity, never a
# customer business value and never fabricated evidence — the probed response is
# still the target's own.
_PRESENCE_PROBE_SENTINEL = "qbg_interface_presence"
_PLACEHOLDER_RE = re.compile(r"\{[^}/]+\}|:[A-Za-z_][A-Za-z0-9_]*")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _int_status(value: Any) -> int:
    if isinstance(value, bool):
        return 0
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def presence_probe_path(path: str) -> str:
    """Materialize declared path placeholders with the probe sentinel.

    Purely structural: the sentinel stands in for any ``{token}`` / ``:token``
    so the route-existence read has a concrete URL. The declared (placeholder)
    path is preserved separately on the assertion for the finding title.
    """
    return _PLACEHOLDER_RE.sub(_PRESENCE_PROBE_SENTINEL, _text(path))


def _observe_interface_presence(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    treatment = _dict(envelope.get("treatment_observation"))
    control = _dict(envelope.get("control_observation"))
    obs = treatment if _int_status(treatment.get("status_code")) > 0 else control
    status_code = _int_status(obs.get("status_code"))
    content_type = _content_type(obs.get("headers"))
    obs_body = obs.get("body")
    evidence = {
        EVIDENCE_KEY: {
            "status_code": status_code,
            "content_type": content_type,
            "framework_route_not_found": framework_route_not_found(
                status_code, content_type, obs_body
            ),
            "interface_present": (
                status_code > 0
                and not framework_route_not_found(status_code, content_type, obs_body)
            ),
        }
    }
    if status_code <= 0:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="INTERFACE_PRESENCE_NOT_ATTEMPTED",
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_interface_presence(envelope: dict[str, Any]) -> dict[str, Any]:
    """Tri-state evaluator: declared interface must resolve to a registered route.

    A framework-level 404 (text/html, ``Cannot METHOD``) is a VIOLATION of the
    presence contract; any other non-5xx response (2xx, or a business 4xx such
    as 401/403/404 in application/json) is a PASS. 5xx / no transport seal
    INDETERMINATE — a broken route is not evidence of a missing route, and an
    unattempted read must never read as verified.
    """
    spec = _dict(envelope.get("spec"))
    obs = _dict(envelope.get("observations"))
    evidence = _dict(obs.get(EVIDENCE_KEY))
    method = _text(spec.get("method"))
    path = _text(spec.get("path"))
    expected = {"interface_implemented": True, "method": method, "path": path}
    status_code = _int_status(evidence.get("status_code"))
    content_type = _text(evidence.get("content_type"))
    if status_code <= 0:
        return {
            "passed": None,
            "reason_code": "INTERFACE_PRESENCE_NOT_ATTEMPTED",
            "expected": expected,
            "actual": {"status_code": status_code, "content_type": content_type},
        }
    if evidence.get("framework_route_not_found") is True:
        return {
            "passed": False,
            "reason_code": "DECLARED_INTERFACE_NOT_IMPLEMENTED",
            "expected": expected,
            "actual": {
                "status_code": status_code,
                "content_type": content_type,
                "framework_route_not_found": True,
            },
        }
    if 100 <= status_code < 500:
        return {
            "passed": True,
            "reason_code": "",
            "expected": expected,
            "actual": {
                "status_code": status_code,
                "content_type": content_type,
                "framework_route_not_found": False,
            },
        }
    return {
        "passed": None,
        "reason_code": "INTERFACE_PRESENCE_INDETERMINATE",
        "expected": expected,
        "actual": {"status_code": status_code, "content_type": content_type},
    }


def _compile_interface_presence_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    property_spec = _dict(envelope.get("property_spec"))
    method = _text(operation.get("method")).upper()
    if method not in _SAFE_METHODS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "interface_presence_requires_get_or_head",
        }
    declared_path = _text(
        operation.get("path") or operation.get("raw_path")
        or operation.get("normalized_path") or operation.get("path_template")
    )
    if not declared_path:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "interface_presence_path_missing",
        }
    actor = _text(
        envelope.get("treatment_actor_ref")
        or envelope.get("control_actor_ref")
        or property_spec.get("actor_ref")
    )
    if not actor:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "interface_presence_actor",
        }
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [{
            "step_id": "treatment_1",
            "actor_ref": actor,
            "operation_ref": operation_ref,
            "path": presence_probe_path(declared_path),
            "intent": "source_declared_interface_presence_probe",
            "protocol_step": "interface_presence_read",
            "property_template": PROTOCOL_TEMPLATE,
        }],
        "observers": [{"observer_id": OBSERVER_ID}],
        "assertion": {
            "kind": ASSERTION_KIND,
            "method": method,
            "path": declared_path,
            "property": dict(property_spec),
        },
    }


def install_formal_interface_presence_surface() -> dict[str, str]:
    """Register observer, assertion kind, family and protocol idempotently.

    Order is load-bearing: the observer must be registered before the assertion
    kind (whose kind-to-evidence contract validates the evidence key is
    produced), and the family before the protocol. No target I/O happens here.
    """
    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_observe_interface_presence,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID

    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_interface_presence,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    from .test_obligation import canonical_risk_families, register_risk_family

    if RISK_FAMILY not in canonical_risk_families():
        installed["risk_family"] = register_risk_family(
            RISK_FAMILY,
            relation_types={"declares"},
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
            compiler=_compile_interface_presence_protocol,
            observers=(OBSERVER_ID,),
            assertion_kind=ASSERTION_KIND,
            emits_control=False,
            per_step_evidence=False,
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
    "install_formal_interface_presence_surface",
    "presence_probe_path",
]
