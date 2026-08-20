"""Customer Delivery Gate v2 with explicit cleanup and reproduction authority.

Outcome-aware Delivery Gate mechanics remain in
``_customer_delivery_gate_v2_outcome_mechanics``.  This facade tightens two
operational truth boundaries:

* once a business write was accepted, a bare ``cleanup_status=NOT_REQUIRED``
  field is not proof that cleanup was legitimately unnecessary; and
* reproduction steps use adapter-specific exact schemas so non-HTTP evidence
  produced by the canonical builder can be validated without weakening the
  historical HTTP receipt contract.
"""
from __future__ import annotations

from typing import Any

from . import _customer_delivery_gate_v2_outcome_mechanics as _outcome
from ._customer_delivery_gate_v2_outcome_mechanics import *  # noqa: F401,F403

_core = _outcome._core
_original_cleanup_gate_decision = _core._cleanup_gate_decision


def __getattr__(name: str) -> Any:
    return getattr(_outcome, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_outcome)))


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


_HTTP_REPRODUCTION_STEP_FIELDS = {
    "phase",
    "step_id",
    "actor_ref",
    "operation_ref",
    "method",
    "path",
    "path_template",
    "status_code",
    "observation_receipt_id",
    "request_body_fingerprint",
    "request_semantics_fingerprint",
    "mutation_class",
    "mutation_selector",
    "mutation_operator",
    "response_fingerprint",
}
_NON_HTTP_REPRODUCTION_STEP_FIELDS = (
    _HTTP_REPRODUCTION_STEP_FIELDS
    | {"adapter", "operation_locator", "invocation_outcome"}
)


def validate_reproduction_receipt(
    receipt: dict[str, Any],
) -> dict[str, Any]:
    """Validate HTTP and non-HTTP reproduction steps with exact schemas.

    HTTP keeps the historical field set and fingerprint payload byte-for-byte.
    Non-HTTP accepts only the three identity fields emitted by the canonical
    builder and recomputes request semantics from that adapter identity.
    """

    row = _dict(receipt)
    required = {
        "schema_version",
        "receipt_id",
        "receipt_fingerprint",
        "campaign_id",
        "obligation_id",
        "experiment_id",
        "execution_id",
        "evidence_id",
        "status",
        "reason_code",
        "oracle_receipt_id",
        "step_observations",
        "source_refs",
    }
    _core._strict_fields(
        row,
        required,
        code="reproduction_receipt_fields_invalid",
    )
    if row.get("schema_version") != _core.REPRODUCTION_RECEIPT_SCHEMA:
        raise _core.DeliveryGateV2Error("reproduction_receipt_schema_invalid")
    if not all(
        _text(row.get(field))
        for field in (
            "campaign_id",
            "obligation_id",
            "experiment_id",
            "execution_id",
            "evidence_id",
            "oracle_receipt_id",
        )
    ):
        raise _core.DeliveryGateV2Error("reproduction_receipt_identity_missing")
    status = _text(row.get("status"))
    if status not in {"REPRODUCED", "NOT_REPRODUCED"}:
        raise _core.DeliveryGateV2Error("reproduction_receipt_status_invalid")
    steps = row.get("step_observations")
    if not isinstance(steps, list) or not isinstance(row.get("source_refs"), list):
        raise _core.DeliveryGateV2Error("reproduction_receipt_content_invalid")

    for raw_step in steps:
        step = _dict(raw_step)
        fields = set(step)
        if fields == _HTTP_REPRODUCTION_STEP_FIELDS:
            is_http_step = True
        elif fields == _NON_HTTP_REPRODUCTION_STEP_FIELDS:
            is_http_step = False
        else:
            raise _core.DeliveryGateV2Error(
                "reproduction_request_semantics_fields_invalid"
            )

        required_step_fields = (
            (
                "phase",
                "step_id",
                "actor_ref",
                "operation_ref",
                "method",
                "path",
                "path_template",
                "observation_receipt_id",
                "mutation_class",
            )
            if is_http_step
            else (
                "phase",
                "step_id",
                "actor_ref",
                "operation_ref",
                "adapter",
                "operation_locator",
                "invocation_outcome",
                "observation_receipt_id",
                "mutation_class",
            )
        )
        if (
            not all(_text(step.get(field)) for field in required_step_fields)
            or (
                not is_http_step
                and _text(step.get("adapter")) == "http_api"
            )
            or not _core._is_sha256(step.get("request_body_fingerprint"))
            or not _core._is_sha256(step.get("request_semantics_fingerprint"))
            or not _core._is_sha256(step.get("response_fingerprint"))
        ):
            raise _core.DeliveryGateV2Error(
                "reproduction_request_semantics_invalid"
            )

        if is_http_step:
            expected_request_semantics = _core._fingerprint({
                "operation_ref": _text(step.get("operation_ref")),
                "method": _text(step.get("method")).upper(),
                "path_template": _text(step.get("path_template")),
                "mutation_class": _text(step.get("mutation_class")),
                "mutation_selector": _text(step.get("mutation_selector")),
                "mutation_operator": _text(step.get("mutation_operator")),
                "request_body_fingerprint": _text(
                    step.get("request_body_fingerprint")
                ),
            })
        else:
            expected_request_semantics = _core._fingerprint({
                "adapter": _text(step.get("adapter")),
                "operation_ref": _text(step.get("operation_ref")),
                "operation_locator": _text(step.get("operation_locator")),
                "invocation_outcome": _text(step.get("invocation_outcome")),
                "mutation_class": _text(step.get("mutation_class")),
                "mutation_selector": _text(step.get("mutation_selector")),
                "mutation_operator": _text(step.get("mutation_operator")),
                "request_body_fingerprint": _text(
                    step.get("request_body_fingerprint")
                ),
            })
        if (
            _text(step.get("request_semantics_fingerprint"))
            != expected_request_semantics
        ):
            raise _core.DeliveryGateV2Error(
                "reproduction_request_semantics_fingerprint_invalid"
            )

    phases = {
        _text(_dict(value).get("phase"))
        for value in steps
        if isinstance(value, dict)
    }
    if (
        status == "REPRODUCED"
        and ("treatment" not in phases or _text(row.get("reason_code")))
    ):
        raise _core.DeliveryGateV2Error(
            "reproduction_receipt_semantics_invalid"
        )
    if status == "NOT_REPRODUCED" and not _text(row.get("reason_code")):
        raise _core.DeliveryGateV2Error("reproduction_receipt_reason_missing")
    unsigned = {
        key: value
        for key, value in row.items()
        if key not in {"receipt_id", "receipt_fingerprint"}
    }
    expected = _core._seal(
        unsigned,
        prefix="reproduction_",
        id_field="receipt_id",
        fingerprint_field="receipt_fingerprint",
    )
    if row != expected:
        raise _core.DeliveryGateV2Error(
            "reproduction_receipt_fingerprint_invalid"
        )
    return dict(expected)


def _cleanup_gate_decision(
    *,
    execution: dict[str, Any],
    contracts: list[dict[str, Any]],
) -> tuple[str, list[str], str]:
    """Accepted writes require a typed cleanup evidence contract.

    The historical gate admitted ``accepted_non_cleanup_write_count > 0`` with
    no cleanup contract whenever the operational receipt alone said
    ``NOT_REQUIRED``.  That made an operational summary self-authorize the
    absence of restoration evidence.  Zero-write executions remain allowed to
    prove NOT_REQUIRED operationally; any accepted write must instead carry a
    formal cleanup contract whose detailed semantics are still evaluated by the
    existing authority below.
    """

    operational = _dict(_dict(execution).get("operational_receipt"))
    accepted_writes = int(
        operational.get("accepted_non_cleanup_write_count") or 0
    )
    cleanup_contracts = [
        row
        for row in _list(contracts)
        if isinstance(row, dict) and _text(row.get("kind")) == "cleanup"
    ]
    if accepted_writes > 0 and not cleanup_contracts:
        return (
            "HARNESS_FAILED",
            ["CLEANUP_EVIDENCE_INCOMPLETE"],
            "INCOMPLETE",
        )
    return _original_cleanup_gate_decision(
        execution=execution,
        contracts=contracts,
    )


# The historical builders resolve these helpers from the private mechanics
# module. Point every layer at the same strict authorities so direct/private
# paths cannot retain either legacy shortcut.
_core.validate_reproduction_receipt = validate_reproduction_receipt
_outcome.validate_reproduction_receipt = validate_reproduction_receipt
_outcome._core.validate_reproduction_receipt = validate_reproduction_receipt
_core._cleanup_gate_decision = _cleanup_gate_decision
_outcome._core._cleanup_gate_decision = _cleanup_gate_decision

# Rebind the established public callables from the outcome-aware layer.
build_customer_delivery_gate_receipt_v2 = (
    _outcome.build_customer_delivery_gate_receipt_v2
)
validate_customer_delivery_gate_receipt_v2 = (
    _outcome.validate_customer_delivery_gate_receipt_v2
)
validate_customer_delivery_gate_bundle = (
    _outcome.validate_customer_delivery_gate_bundle
)

__all__ = sorted(
    {
        *[
            name
            for name in dir(_outcome)
            if not name.startswith("__")
        ],
        "_cleanup_gate_decision",
        "build_customer_delivery_gate_receipt_v2",
        "validate_reproduction_receipt",
        "validate_customer_delivery_gate_receipt_v2",
        "validate_customer_delivery_gate_bundle",
    }
)
