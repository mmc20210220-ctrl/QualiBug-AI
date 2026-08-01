"""Experiment execution facade with outcome-finding collection lineage.

The private mechanics retains transport, authorization causality, and compatibility behavior.
This public authority applies the same sealed campaign/experiment lineage to every fanned-out
finding and clears the entire collection when authorization delivery evidence is invalid.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

from . import _experiment_executor_single_finding_mechanics as _core
from ._experiment_executor_single_finding_mechanics import *  # noqa: F401,F403

_original_execute_one_experiment = _core.execute_one_experiment


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _seal_collection_lineage(result: dict[str, Any]) -> dict[str, Any]:
    output = deepcopy(_dict(result))
    if _text(output.get("reason_code")) == "AUTHORIZATION_DELIVERY_EVIDENCE_INVALID":
        output["finding"] = None
        output["findings"] = []
        return output
    receipt = _dict(output.get("authorization_causality_receipt"))
    findings = [
        dict(row)
        for row in _list(output.get("findings"))
        if isinstance(row, dict)
    ]
    if not findings:
        if isinstance(output.get("finding"), dict):
            output["findings"] = [dict(output["finding"])]
        return output
    if _text(receipt.get("status")).upper() != "PASSED":
        output["findings"] = findings
        output["finding"] = findings[0]
        return output
    sealed: list[dict[str, Any]] = []
    for finding in findings:
        row = dict(finding)
        for field in (
            "campaign_id",
            "obligation_id",
            "experiment_id",
            "execution_id",
        ):
            expected = _text(receipt.get(field))
            current = _text(row.get(field))
            if not expected:
                raise _core.AuthorizationDeliveryGateError(
                    f"authorization_delivery_finding_lineage_missing:{field}"
                )
            if current and current != expected:
                raise _core.AuthorizationDeliveryGateError(
                    f"authorization_delivery_finding_lineage_mismatch:{field}"
                )
            row[field] = expected
        sealed.append(row)
    output["findings"] = sealed
    output["finding"] = sealed[0]
    return output


def execute_one_experiment(*args: Any, **kwargs: Any) -> dict[str, Any]:
    return _seal_collection_lineage(
        _dict(_original_execute_one_experiment(*args, **kwargs))
    )


__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
