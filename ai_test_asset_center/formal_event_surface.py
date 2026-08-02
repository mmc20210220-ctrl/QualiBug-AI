"""Source-declared asynchronous event observation on the formal experiment chain.

The first event increment deliberately avoids pretending that one universal Kafka/RabbitMQ
client exists. Customers declare a read-only event observation GET path on the already approved
test target. A governed business operation triggers the behavior; this observer polls the
relative path for a bounded source-declared window and extracts only declared identity/type/
correlation fields.

Formal properties:

* adapter availability is declared, never inferred from a broker hostname or installed driver;
* only a relative GET path on ``approved_base_url`` is contacted;
* every event must expose a declared stable event id so repeated polls are not mistaken for
  duplicate deliveries;
* exactly-once cannot PASS before the full observation window closes;
* raw event payloads, correlation values and event ids never enter the receipt;
* transport failure or incomplete coverage is INDETERMINATE, never PASS or a defect.
"""
from __future__ import annotations

import copy
import hashlib
import json
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

OBSERVER_ID = "source_event_delivery_reader"
EVIDENCE_KEY = "source_event_delivery_observation"
ASSERTION_KIND = "source_event_delivery_contract"
RISK_FAMILY = "event_delivery_consistency"
PROTOCOL_TEMPLATE = "source_declared_event_observation"
SURFACE = "event_stream"
ADAPTER = "event_observer_http"
_MAX_WINDOW_MS = 30_000
_MAX_EVENTS = 200


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    blob = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:24]


def _scalar(value: Any) -> bool:
    return value is not None and isinstance(value, (str, int, float, bool))


def _extract_path(value: Any, path: str) -> Any:
    token = _text(path)
    if token in {"", "$"}:
        return value
    if token.startswith("$."):
        token = token[2:]
    current = value
    for part in [item for item in token.split(".") if item]:
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _declared_event_contract(spec: dict[str, Any]) -> dict[str, Any]:
    row = _dict(spec)
    contract = copy.deepcopy(_dict(row.get("event_contract")))
    if contract:
        return contract
    return copy.deepcopy(_dict(_dict(row.get("property")).get("event_contract")))


def _validate_observer_path(path: str) -> bool:
    value = _text(path)
    return bool(
        value.startswith("/")
        and not value.startswith("//")
        and "://" not in value
        and "#" not in value
        and ".." not in value.split("?")[0].split("/")
    )


def _compile_event_protocol(envelope: dict[str, Any]) -> dict[str, Any]:
    property_spec = _dict(envelope.get("property_spec"))
    contract = _declared_event_contract(property_spec)
    operation = _dict(envelope.get("operation"))
    operation_ref = _text(envelope.get("operation_ref"))
    actor_ref = _text(
        envelope.get("treatment_actor_ref")
        or envelope.get("control_actor_ref")
        or property_spec.get("actor_ref")
    )
    if not contract:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "source_declared_event_contract_missing",
        }
    if not operation_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OPERATION",
            "detail": "event_trigger_operation_missing",
        }
    if not actor_ref:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ACTOR",
            "detail": "event_trigger_actor_missing",
        }
    if not _validate_observer_path(_text(contract.get("observer_path"))):
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_OBSERVER",
            "detail": "event_observer_relative_path_invalid",
        }
    required = {
        "events_path": _text(contract.get("events_path")),
        "event_id_field": _text(contract.get("event_id_field")),
        "event_type_field": _text(contract.get("event_type_field")),
        "correlation_field": _text(contract.get("correlation_field")),
        "correlation_query_parameter": _text(contract.get("correlation_query_parameter")),
        "expected_event_type": _text(contract.get("expected_event_type")),
    }
    missing = [key for key, value in required.items() if not value]
    if missing:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "event_contract_fields_missing:" + ",".join(missing),
        }
    correlation_source = _dict(contract.get("correlation_source"))
    if not _scalar(contract.get("correlation_value")) and not (
        _text(correlation_source.get("location"))
        and _text(correlation_source.get("path"))
    ):
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "event_correlation_source_missing",
        }
    try:
        minimum = int(contract.get("expected_min_count"))
        maximum = int(contract.get("expected_max_count"))
        window_ms = int(contract.get("observation_window_ms"))
    except (TypeError, ValueError):
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": "event_count_or_window_not_integer",
        }
    if minimum < 0 or maximum < minimum or maximum > _MAX_EVENTS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": "event_expected_count_range_invalid",
        }
    if window_ms <= 0 or window_ms > _MAX_WINDOW_MS:
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_ASSERTION",
            "detail": "event_observation_window_invalid",
        }

    method = _text(operation.get("method")).upper()
    trigger_body = copy.deepcopy(contract.get("trigger_body"))
    if trigger_body is None:
        trigger_body = copy.deepcopy(operation.get("request_example"))
    if method in {"POST", "PUT", "PATCH"} and not isinstance(trigger_body, dict):
        return {
            "status": "BLOCKED",
            "reason_code": "BLOCKED_MISSING_BINDING",
            "detail": "event_trigger_requires_source_request_example",
        }

    step: dict[str, Any] = {
        "step_id": "treatment_1",
        "actor_ref": actor_ref,
        "operation_ref": operation_ref,
        "intent": "trigger_source_declared_event",
        "protocol_step": "event_trigger",
    }
    if isinstance(trigger_body, dict):
        step["body"] = trigger_body
    assertion_property = copy.deepcopy(property_spec)
    assertion_property["event_contract"] = contract
    return {
        "status": "COMPILED",
        "control_plan": [],
        "treatment_plan": [step],
        "observers": [{"observer_id": OBSERVER_ID}],
        "assertion": {
            "kind": ASSERTION_KIND,
            "property": assertion_property,
            "invariant_ref": _text(property_spec.get("invariant_ref")),
            "rule_id": _text(property_spec.get("invariant_ref") or property_spec.get("rule_id")),
        },
    }


def _correlation_value(contract: dict[str, Any], observations: dict[str, Any]) -> Any:
    if _scalar(contract.get("correlation_value")):
        return contract.get("correlation_value")
    source = _dict(contract.get("correlation_source"))
    location = _text(source.get("location")).lower()
    path = _text(source.get("path"))
    treatment = _dict(observations.get("treatment_observation"))
    if location in {"treatment_response", "response"}:
        return _extract_path(treatment.get("body"), path)
    if location in {"treatment_request", "request"}:
        governance = _dict(treatment.get("governance_receipt"))
        request_body = governance.get("materialized_request_body")
        if request_body is None:
            request_body = contract.get("trigger_body")
        return _extract_path(request_body, path)
    return None


def _event_rows(body: Any, contract: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    raw_events = _extract_path(body, _text(contract.get("events_path")))
    if not isinstance(raw_events, list):
        return [], "EVENT_COLLECTION_NOT_LIST"
    rows: list[dict[str, Any]] = []
    for raw in raw_events[:_MAX_EVENTS]:
        if not isinstance(raw, dict):
            continue
        event_id = _extract_path(raw, _text(contract.get("event_id_field")))
        event_type = _extract_path(raw, _text(contract.get("event_type_field")))
        correlation = _extract_path(raw, _text(contract.get("correlation_field")))
        timestamp = _extract_path(raw, _text(contract.get("timestamp_field"))) if _text(contract.get("timestamp_field")) else None
        if not (_scalar(event_id) and _scalar(event_type) and _scalar(correlation)):
            continue
        rows.append({
            "event_id": event_id,
            "event_type": event_type,
            "correlation": correlation,
            "timestamp_present": timestamp not in (None, ""),
        })
    if len(raw_events) > _MAX_EVENTS:
        return rows, "EVENT_COLLECTION_TRUNCATED"
    return rows, ""


def _observer_token(
    *,
    root: Path,
    project: str,
    base_url: str,
    actor_ref: str,
) -> str:
    from .experiment_executor import load_actor_tokens

    tokens = load_actor_tokens(root, project, base_url=base_url)
    return _text(_dict(tokens).get(actor_ref))


def _poll_event_endpoint(
    *,
    base_url: str,
    path: str,
    query_parameter: str,
    correlation_value: Any,
    actor_token: str,
    contract: dict[str, Any],
) -> dict[str, Any]:
    from .sandbox_write_executor import _http_request

    window_ms = min(_MAX_WINDOW_MS, int(contract.get("observation_window_ms") or 0))
    interval_ms = int(contract.get("poll_interval_ms") or 500)
    interval_ms = min(5_000, max(100, interval_ms))
    deadline = time.monotonic() + (window_ms / 1000.0)
    unique: dict[str, dict[str, Any]] = {}
    poll_count = 0
    successful_polls = 0
    status_codes: list[int] = []
    errors: list[str] = []
    truncated = False
    while True:
        poll_count += 1
        separator = "&" if "?" in path else "?"
        url = (
            base_url.rstrip("/")
            + path
            + separator
            + urlencode({query_parameter: str(correlation_value)})
        )
        response = _http_request(
            "GET",
            url,
            token=actor_token,
            timeout=min(5.0, max(0.5, deadline - time.monotonic())),
            max_retries=0,
        )
        status = int(response.get("status") or 0)
        status_codes.append(status)
        if 200 <= status < 300:
            successful_polls += 1
            rows, row_error = _event_rows(response.get("body"), contract)
            if row_error == "EVENT_COLLECTION_TRUNCATED":
                truncated = True
            elif row_error:
                errors.append(row_error)
            for row in rows:
                unique.setdefault(str(row["event_id"]), row)
        else:
            errors.append(_text(response.get("error")) or f"HTTP_{status or 0}")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            break
        time.sleep(min(interval_ms / 1000.0, remaining))
    return {
        "events": list(unique.values()),
        "poll_count": poll_count,
        "successful_polls": successful_polls,
        "status_codes": status_codes,
        "errors": errors[:10],
        "truncated": truncated,
        "observation_window_completed": True,
    }


def _event_observer_handler(envelope: dict[str, Any]) -> dict[str, Any]:
    from .observer_contracts_base import _receipt

    exp = _dict(envelope.get("experiment"))
    assertion = _dict(envelope.get("assertion"))
    spec = _dict(assertion.get("property")) or _dict(envelope.get("property"))
    contract = _declared_event_contract(spec)
    observations = _dict(envelope.get("observations"))
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

    if not contract:
        return indeterminate("EVENT_CONTRACT_NOT_DECLARED")
    if not (root_value and project and runtime_contract):
        return indeterminate("EVENT_RUNTIME_CONTEXT_MISSING")
    if _text(runtime_contract.get("status")) != "approved":
        return indeterminate("EVENT_RUNTIME_CONTRACT_NOT_APPROVED")
    if ADAPTER not in {
        _text(value) for value in _list(runtime_contract.get("declared_adapters"))
    }:
        return indeterminate("EVENT_OBSERVER_ADAPTER_NOT_DECLARED")
    observer_path = _text(contract.get("observer_path"))
    if not _validate_observer_path(observer_path):
        return indeterminate("EVENT_OBSERVER_PATH_INVALID")
    correlation = _correlation_value(contract, observations)
    if not _scalar(correlation):
        return indeterminate("EVENT_CORRELATION_VALUE_NOT_PROVEN")

    base_url = _text(runtime_contract.get("approved_base_url"))
    actor_ref = _text(spec.get("actor_ref") or contract.get("actor_ref"))
    token = _observer_token(
        root=Path(root_value),
        project=project,
        base_url=base_url,
        actor_ref=actor_ref,
    )
    if contract.get("observer_requires_actor_token") is True and not token:
        return indeterminate("EVENT_OBSERVER_ACTOR_TOKEN_MISSING")

    polled = _poll_event_endpoint(
        base_url=base_url,
        path=observer_path,
        query_parameter=_text(contract.get("correlation_query_parameter")),
        correlation_value=correlation,
        actor_token=token,
        contract=contract,
    )
    events = [row for row in _list(polled.get("events")) if isinstance(row, dict)]
    correlated = [
        row for row in events if str(row.get("correlation")) == str(correlation)
    ]
    expected_type = _text(contract.get("expected_event_type"))
    mismatched_types = sorted({
        _text(row.get("event_type"))
        for row in correlated
        if _text(row.get("event_type")) != expected_type
    })
    evidence = {
        EVIDENCE_KEY: {
            "contract_id": _text(contract.get("contract_id")),
            "expected_event_type": expected_type,
            "expected_min_count": int(contract.get("expected_min_count") or 0),
            "expected_max_count": int(contract.get("expected_max_count") or 0),
            "observed_correlated_count": len(correlated),
            "observed_event_types": sorted({_text(row.get("event_type")) for row in correlated}),
            "mismatched_event_types": mismatched_types,
            "event_id_fingerprints": sorted(_fingerprint(row.get("event_id")) for row in correlated),
            "timestamp_present_count": sum(1 for row in correlated if row.get("timestamp_present") is True),
            "correlation_fingerprint": _fingerprint(correlation),
            "poll_count": int(polled.get("poll_count") or 0),
            "successful_poll_count": int(polled.get("successful_polls") or 0),
            "status_codes": [int(value or 0) for value in _list(polled.get("status_codes"))],
            "observer_errors": [_text(value) for value in _list(polled.get("errors")) if _text(value)],
            "observation_window_completed": polled.get("observation_window_completed") is True,
            "coverage_complete": (
                polled.get("observation_window_completed") is True
                and int(polled.get("successful_polls") or 0) > 0
                and polled.get("truncated") is not True
                and not _list(polled.get("errors"))
            ),
            "raw_event_payloads_included": False,
        }
    }
    if int(polled.get("successful_polls") or 0) <= 0:
        return _receipt(
            observer_id=OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="EVENT_OBSERVER_NOT_REACHED",
            evidence=evidence,
        )
    return _receipt(
        observer_id=OBSERVER_ID,
        status="OBSERVED",
        reason_code="",
        evidence=evidence,
    )


def _evaluate_event_delivery(envelope: dict[str, Any]) -> dict[str, Any]:
    observation = _dict(_dict(envelope.get("observations")).get(EVIDENCE_KEY))
    expected = {
        "event_type": _text(observation.get("expected_event_type")),
        "min_count": int(observation.get("expected_min_count") or 0),
        "max_count": int(observation.get("expected_max_count") or 0),
        "full_observation_window_required": True,
    }
    actual_count = int(observation.get("observed_correlated_count") or 0)
    actual = {
        "count": actual_count,
        "event_types": _list(observation.get("observed_event_types")),
        "mismatched_event_types": _list(observation.get("mismatched_event_types")),
        "observation_window_completed": observation.get("observation_window_completed") is True,
        "coverage_complete": observation.get("coverage_complete") is True,
    }
    if not actual["observation_window_completed"] or not actual["coverage_complete"]:
        return {
            "passed": None,
            "reason_code": "EVENT_OBSERVATION_COVERAGE_INCOMPLETE",
            "expected": expected,
            "actual": actual,
        }
    violated = bool(
        actual_count < expected["min_count"]
        or actual_count > expected["max_count"]
        or actual["mismatched_event_types"]
    )
    return {
        "passed": not violated,
        "reason_code": "" if violated or not violated else "EVENT_DELIVERY_UNJUDGEABLE",
        "expected": expected,
        "actual": actual,
    }


def _install_runtime_context_bridge() -> None:
    """Retired: runtime context injection is first-class inside
    ``experiment_executor.execute_one_experiment``. Kept only as a no-op so a
    stale caller cannot re-introduce the method replacement."""
    return None


def install_formal_event_surface() -> dict[str, str]:
    """Install event adapter, observer, assertion, risk family and protocol idempotently."""

    from . import adapter_capability as capabilities

    capabilities.DECLARATION_REQUIRED.setdefault(
        ADAPTER,
        "runtime_contract.declared_adapters[]",
    )
    capabilities.ADAPTER_TO_OBSERVATION_SURFACE.setdefault(ADAPTER, SURFACE)
    capabilities.ADAPTER_TO_CAPABILITY.setdefault(ADAPTER, "event_stream_read")

    from .assertion_dsl_base import register_assertion_kind, registered_assertion_kinds
    from .observer_contracts_base import OBSERVER_REGISTRY, register_observer

    installed: dict[str, str] = {}
    if OBSERVER_ID not in OBSERVER_REGISTRY:
        installed["observer"] = register_observer(
            OBSERVER_ID,
            surface=SURFACE,
            adapter=ADAPTER,
            handler=_event_observer_handler,
            evidence_keys=(EVIDENCE_KEY,),
        )
    else:
        installed["observer"] = OBSERVER_ID
    if ASSERTION_KIND not in set(registered_assertion_kinds()):
        installed["assertion"] = register_assertion_kind(
            ASSERTION_KIND,
            evaluator=_evaluate_event_delivery,
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
            compiler=_compile_event_protocol,
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
    "install_formal_event_surface",
]
