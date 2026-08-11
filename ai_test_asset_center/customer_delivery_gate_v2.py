"""Customer Delivery Gate v2 with explicit cleanup authority.

Outcome-aware Delivery Gate mechanics remain in
``_customer_delivery_gate_v2_outcome_mechanics``.  This facade tightens one
operational truth boundary: once a business write was accepted, a bare
``cleanup_status=NOT_REQUIRED`` field is not proof that cleanup was legitimately
unnecessary.  A formal cleanup Contract Evidence receipt must explain the write
as completed, unchanged/not-required, or accepted residue before a finding can
be delivered.
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


# The historical builder resolves this helper from the private mechanics module.
# Point both layers at the same strict cleanup authority so direct/private paths
# cannot retain the operational-self-authorization shortcut.
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
        "validate_customer_delivery_gate_receipt_v2",
        "validate_customer_delivery_gate_bundle",
    }
)
