"""Capture approved database relation contracts and bind them to matching Experiments."""
from __future__ import annotations

import functools
import hashlib
import json
from contextvars import ContextVar
from copy import deepcopy
from typing import Any

from .runtime_materialization_experiment_bridge import _experiment_operation_identity, _operation_identity

BRIDGE_SCHEMA = "qualibug.database-relation-experiment-bridge.v1"
_CAPTURED_RELATIONS: ContextVar[list[dict[str, Any]]] = ContextVar(
    "qualibug_database_relation_contracts", default=[]
)
_INSTALL_MARKER = "__qualibug_database_relation_capture_v1__"


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def capture_database_relation_contracts(asset: Any) -> None:
    if not isinstance(asset, dict):
        return
    rows = [
        deepcopy(row)
        for row in _list(asset.get("database_relation_observer_contracts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) == "qualibug.database-relation-observer-contract.v1"
        and _text(row.get("status")) == "READY_FOR_RUNTIME_CONNECTION_BINDING"
        and row.get("runtime_observer_authoritative") is True
        and row.get("read_only") is True
        and row.get("write_target_allowed") is False
        and row.get("oracle_authority_allowed") is False
    ]
    _CAPTURED_RELATIONS.set(rows)


def install_database_relation_asset_capture() -> None:
    """Capture only approved, secret-free relation contracts from the existing builder."""
    try:
        from . import enterprise_knowledge_center as center
    except Exception:
        return
    original = getattr(center, "build_enterprise_business_knowledge_asset", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(*args: Any, **kwargs: Any) -> Any:
        asset = original(*args, **kwargs)
        capture_database_relation_contracts(asset)
        return asset

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    center.build_enterprise_business_knowledge_asset = wrapped


def _root_observer_refs(experiment: dict[str, Any]) -> set[str]:
    return {
        _text(row.get("observer_contract_ref"))
        for row in _list(experiment.get("database_observer_execution_drafts"))
        if isinstance(row, dict) and _text(row.get("observer_contract_ref"))
    }


def attach_captured_database_relation_contracts(
    experiment_pack: dict[str, Any],
    *,
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attach all exact child relation contracts scoped to each experiment's root Observer."""
    pack = dict(experiment_pack or {})
    captured = [dict(row) for row in _CAPTURED_RELATIONS.get() if isinstance(row, dict)]
    experiments: list[dict[str, Any]] = []
    bound_contract_count = 0

    for raw in _list(pack.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = deepcopy(raw)
        method, path = _experiment_operation_identity(experiment, behavior_ir)
        root_refs = _root_observer_refs(experiment)
        matches = [
            deepcopy(contract)
            for contract in captured
            if _operation_identity(contract.get("method"), contract.get("path")) == (method, path)
            and _text(contract.get("root_observer_id")) in root_refs
        ]
        matches.sort(key=lambda row: _text(row.get("relation_observer_id")))
        experiment["database_relation_observer_contracts"] = matches
        experiment["database_relation_observer_contract_count"] = len(matches)
        fingerprint = _fingerprint(matches) if matches else ""
        experiment["database_relation_observer_contract_fingerprint"] = fingerprint
        receipt = _dict(experiment.get("compile_receipt"))
        receipt.update(
            {
                "database_relation_contract_bridge_status": "BOUND" if matches else "NOT_APPLICABLE",
                "database_relation_observer_contract_count": len(matches),
                "database_relation_observer_contract_fingerprint": fingerprint,
            }
        )
        experiment["compile_receipt"] = receipt
        bound_contract_count += len(matches)
        experiments.append(experiment)

    pack["experiments"] = experiments
    pack["database_relation_experiment_bridge"] = {
        "schema": BRIDGE_SCHEMA,
        "status": "NOT_APPLICABLE" if not captured else "PASS",
        "captured_contract_count": len(captured),
        "bound_contract_count": bound_contract_count,
        "full_enterprise_asset_copied": False,
        "raw_customer_source_copied": False,
        "secret_values_copied": False,
        "automatic_relation_selection_count": 0,
    }
    return pack


__all__ = [
    "BRIDGE_SCHEMA",
    "attach_captured_database_relation_contracts",
    "capture_database_relation_contracts",
    "install_database_relation_asset_capture",
]
