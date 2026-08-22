"""Cross-surface compatibility observation on the formal experiment chain.

A *compatibility* defect is a contract/behavior divergence between two declared
surfaces of the SAME system (e.g. dev vs staging vs prod, or two declared API
versions). The product frames this family as ``comparison_first`` /
``compatibility_matrix`` (see defect_family_registry.py), so the obligation is
born only when at least two comparison surfaces are declared.

This module registers the observer, assertion kind, risk family and protocol
idempotently. It deliberately avoids pretending a universal "version broker"
exists: the two surfaces are EXPLICITLY declared (operator / target
declaration), never inferred from a hostname or installed driver. Every read is
read-only (GET/HEAD) so a compatibility probe can never mutate a target.

Formal properties:

* surfaces are declared, never inferred;
* every probe is read-only (GET/HEAD) on an approved surface;
* the observation compares two real responses and reports a verdict;
* transport failure or an insufficient surface count is INDETERMINATE, never
  PASS or a fabricated defect.
"""
from __future__ import annotations

import copy
from typing import Any

OBSERVER_ID = "compatibility_response_diff"
EVIDENCE_KEY = "compatibility_diff_observation"
ASSERTION_KIND = "compatibility_response_diff"
RISK_FAMILY = "compatibility"
PROTOCOL_TEMPLATE = "compatibility_matrix"
SURFACE = "compatibility_matrix"
ADAPTER = "http_api"
_SAFE_METHODS = frozenset({"GET", "HEAD"})


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _scalar(value: Any) -> bool:
    return value is not None and isinstance(value, (str, int, float, bool))


def _status_class(status: Any) -> int:
    try:
        return int(status or 0) // 100
    except (TypeError, ValueError):
        return 0


def _body_schema_keys(body: Any) -> set[str]:
    """Stable set of top-level response keys used for a shallow compatibility diff."""
    if isinstance(body, dict):
        return set(body.keys())
    if isinstance(body, list) and body and isinstance(body[0], dict):
        return set(body[0].keys())
    return set()


def _http_get(base_url: str, path: str, method: str, token: str, *, timeout: float = 5.0) -> dict[str, Any]:
    """Read-only HTTP GET/HEAD against one declared surface.

    Isolated as a single call site so tests can monkey-patch transport without
    touching the sandbox executor. Mirrors the event observer's lazy import.
    """
    from .sandbox_write_executor import _http_request

    url = base_url.rstrip("/") + "/" + _text(path).lstrip("/")
    return _http_request(method, url, token=token, timeout=timeout, max_retries=0)


def _observe_surface(surface: dict[str, Any], *, path: str, method: str) -> dict[str, Any]:
    base_url = _text(_dict(surface).get("base_url"))
    token = _text(_dict(surface).get("token"))
    if not base_url:
        return {"reachable": False, "reason": "SURFACE_BASE_URL_MISSING"}
    try:
        response = _http_get(base_url, path, method, token)
    except Exception as exc:  # transport failure must never become a defect
        return {"reachable": False, "reason": "TRANSPORT_ERROR:" + _text(str(exc))[:120]}
    status = int(response.get("status") or 0)
    return {
        "reachable": 200 <= status < 600,
        "status": status,
        "status_class": _status_class(status),
        "schema_keys": sorted(_body_schema_keys(response.get("body"))),
        "error": _text(response.get("error")),
    }


def _compile_compatibility_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    surfaces = _list(property_spec.get("compatibility_surfaces"))
    path = _text(property_spec.get("path") or _text(_dict(envelope.get("operation")).get("path")))
    if len(surfaces) < 2:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "compatibility_requires_two_surfaces",
        }
    if not path:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "compatibility_path_missing",
        }
    step: dict[str, Any] = {
        "step_id": "treatment_1",
        "intent": "compatibility_cross_surface_compare",
        "protocol_step": "compatibility_compare",
    }
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [step],
        "observers": [{"observer_id": OBSERVER_ID}],
        "assertion": {
            "kind": ASSERTION_KIND,
            "property": copy.deepcopy(property_spec),
            "invariant_ref": _text(property_spec.get("invariant_ref") or property_spec.get("rule_id")),
        },
    }


def _compatibility_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    surfaces = _list(spec.get("compatibility_surfaces"))
    operation = _dict(spec.get("operation")) or {}
    method = _text(spec.get("method") or operation.get("method")).upper() or "GET"
    path = _text(spec.get("path") or operation.get("path"))

    def indeterminate(reason_code: str, **detail: Any) -> dict[str, Any]:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code=reason_code,
            evidence={"detail": detail},
        )

    if len(surfaces) < 2:
        return indeterminate("COMPAT_SURFACES_INSUFFICIENT", surface_count=len(surfaces))
    if not path:
        return indeterminate("COMPAT_PATH_MISSING")
    if method not in _SAFE_METHODS:
        return indeterminate("COMPAT_METHOD_NOT_READONLY", method=method)

    observed = [_observe_surface(surface, path=path, method=method) for surface in surfaces[:2]]
    reachable = [row for row in observed if _dict(row).get("reachable") is True]
    evidence = {
        EVIDENCE_KEY: {
            "method": method,
            "path": path,
            "surface_count": len(surfaces),
            "observed_count": len(reachable),
            "surfaces": [
                {
                    "status": int(_dict(row).get("status") or 0),
                    "status_class": int(_dict(row).get("status_class") or 0),
                    "schema_keys": _list(_dict(row).get("schema_keys")),
                    "reachable": _dict(row).get("reachable") is True,
                }
                for row in observed
            ],
        }
    }
    if len(reachable) < 2:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="COMPAT_INSUFFICIENT_REACHABLE_SURFACES",
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_compatibility(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    if not observation:
        return {
            "passed": None,
            "reason_code": "COMPAT_OBSERVATION_MISSING",
            "expected": {},
            "actual": {},
        }
    surfaces = _list(observation.get("surfaces"))
    reachable = [row for row in surfaces if _dict(row).get("reachable") is True]
    if len(reachable) < 2:
        return {
            "passed": None,
            "reason_code": "COMPAT_SURFACES_NOT_BOTH_REACHABLE",
            "expected": {"both_reachable": True},
            "actual": {"reachable_count": len(reachable)},
        }
    a, b = reachable[0], reachable[1]
    status_class_a = int(_dict(a).get("status_class") or 0)
    status_class_b = int(_dict(b).get("status_class") or 0)
    keys_a = set(_list(_dict(a).get("schema_keys")))
    keys_b = set(_list(_dict(b).get("schema_keys")))
    expected = {
        "status_class_equal": True,
        "response_schema_keys_equal": True,
    }
    actual = {
        "status_class_a": status_class_a,
        "status_class_b": status_class_b,
        "status_class_equal": status_class_a == status_class_b,
        "schema_key_count_a": len(keys_a),
        "schema_key_count_b": len(keys_b),
        "schema_keys_only_on_a": sorted(keys_a - keys_b),
        "schema_keys_only_on_b": sorted(keys_b - keys_a),
        "response_schema_keys_equal": keys_a == keys_b,
    }
    violated = not (actual["status_class_equal"] and actual["response_schema_keys_equal"])
    reason_code = ""
    if violated:
        reason_code = "COMPAT_SURFACE_DIVERGENCE"
    return {
        "passed": not violated,
        "reason_code": reason_code,
        "expected": expected,
        "actual": actual,
    }


def install_formal_compatibility_surface() -> dict[str, str]:
    """Install compatibility adapter, observer, assertion, risk family and protocol idempotently."""

    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_compatibility_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_compatibility,
            required_evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["assertion"] = ASSERTION_KIND

    from .test_obligation import canonical_risk_families, register_risk_family

    if RISK_FAMILY not in canonical_risk_families():
        installed["risk_family"] = register_risk_family(
            RISK_FAMILY,
            relation_types={"produces", "observes"},
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
            compiler=_compile_compatibility_protocol,
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
    "SURFACE",
    "install_formal_compatibility_surface",
]
