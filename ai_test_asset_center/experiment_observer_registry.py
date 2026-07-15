"""Typed experiment observers; declarations alone are never evidence."""
from __future__ import annotations

import hashlib
import json
from typing import Any


SCHEMA_VERSION = "qualibug.experiment-observation.v1"
SUPPORTED_SURFACES = frozenset({"http_api", "db_snapshot"})


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _receipt_id(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return "obs_" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:20]


def _blocked(
    *,
    experiment_id: str,
    observer_id: str,
    surface: str,
    operation_ref: str,
    resource_ref: str,
    reason_code: str,
) -> dict[str, Any]:
    canonical = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "observer_id": observer_id,
        "surface": surface,
        "operation_ref": operation_ref,
        "resource_ref": resource_ref,
        "status": "BLOCKED",
        "reason_code": reason_code,
        "observation": {},
    }
    return {**canonical, "receipt_id": _receipt_id(canonical)}


def collect_experiment_observations(
    experiment: dict[str, Any],
    *,
    http_events: list[dict[str, Any]],
    db_observations: list[dict[str, Any]],
    available_surfaces: set[str] | None = None,
) -> dict[str, Any]:
    """Bind observer declarations to matching runtime facts."""
    exp = _dict(experiment)
    experiment_id = _text(exp.get("experiment_id"))
    available = set(available_surfaces or set())
    receipts: list[dict[str, Any]] = []
    for declaration in _list(exp.get("observers")):
        if not isinstance(declaration, dict):
            continue
        observer_id = _text(declaration.get("observer_id"))
        surface = _text(declaration.get("surface"))
        operation_ref = _text(
            declaration.get("operation_ref")
            or declaration.get("requested_operation_ref")
        )
        resource_ref = _text(
            declaration.get("resource_ref")
            or declaration.get("requested_resource_ref")
        )
        requested_phase = _text(declaration.get("phase"))
        if not observer_id:
            continue
        if surface not in SUPPORTED_SURFACES or surface not in available:
            receipts.append(
                _blocked(
                    experiment_id=experiment_id,
                    observer_id=observer_id,
                    surface=surface,
                    operation_ref=operation_ref,
                    resource_ref=resource_ref,
                    reason_code="OBSERVER_SURFACE_UNAVAILABLE",
                )
            )
            continue
        if surface == "http_api":
            if not operation_ref:
                receipts.append(
                    _blocked(
                        experiment_id=experiment_id,
                        observer_id=observer_id,
                        surface=surface,
                        operation_ref=operation_ref,
                        resource_ref=resource_ref,
                        reason_code="OBSERVER_TARGET_UNDECLARED",
                    )
                )
                continue
            matches = [
                event
                for event in http_events
                if isinstance(event, dict)
                and _text(event.get("operation_ref")) == operation_ref
                and (
                    not requested_phase
                    or _text(event.get("phase")) == requested_phase
                )
                and (
                    not resource_ref
                    or not _text(event.get("resource_ref"))
                    or _text(event.get("resource_ref")) == resource_ref
                )
            ]
        else:
            if not resource_ref:
                receipts.append(
                    _blocked(
                        experiment_id=experiment_id,
                        observer_id=observer_id,
                        surface=surface,
                        operation_ref=operation_ref,
                        resource_ref=resource_ref,
                        reason_code="OBSERVER_TARGET_UNDECLARED",
                    )
                )
                continue
            matches = [
                observation
                for observation in db_observations
                if isinstance(observation, dict)
                and _text(observation.get("resource_ref")) == resource_ref
            ]
        if len(matches) != 1:
            receipts.append(
                _blocked(
                    experiment_id=experiment_id,
                    observer_id=observer_id,
                    surface=surface,
                    operation_ref=operation_ref,
                    resource_ref=resource_ref,
                    reason_code=(
                        "OBSERVER_MATCHING_EVENT_MISSING"
                        if not matches
                        else "OBSERVER_MATCHING_EVENT_AMBIGUOUS"
                    ),
                )
            )
            continue
        canonical = {
            "schema_version": SCHEMA_VERSION,
            "experiment_id": experiment_id,
            "observer_id": observer_id,
            "surface": surface,
            "operation_ref": operation_ref,
            "resource_ref": resource_ref,
            "status": "OBSERVED",
            "reason_code": "",
            "observation": dict(matches[0]),
        }
        receipts.append({**canonical, "receipt_id": _receipt_id(canonical)})
    blocked_count = sum(1 for receipt in receipts if receipt.get("status") != "OBSERVED")
    return {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "status": "COMPLETE" if receipts and blocked_count == 0 else "BLOCKED",
        "receipts": receipts,
        "observed_count": len(receipts) - blocked_count,
        "blocked_count": blocked_count,
    }
