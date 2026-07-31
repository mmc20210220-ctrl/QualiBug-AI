"""Bind explicit event-delivery contracts to exact Behavior IR identities.

Contracts arrive from enterprise assets or the strict scan overlay. One contract becomes one
invariant only when a single operation and a single executable actor are proven. Write
operations are allowed here because execution/cleanup governance is owned downstream; this
binder never marks a write safe or reversible.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from . import behavior_ir as _bir
from .formal_event_binding_identity_bridge import (
    project_formal_event_binding_identities,
)

BINDING_RECEIPT_SCHEMA = "qualibug.source-event-contract-binding.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    return "bir_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _source_refs(contract: dict[str, Any]) -> list[dict[str, Any]]:
    refs = [
        copy.deepcopy(row)
        for row in _list(contract.get("source_refs"))
        if isinstance(row, dict) and _text(row.get("source_id"))
    ]
    if refs:
        return refs
    source_id = _text(contract.get("source_id"))
    if not source_id:
        return []
    return [{
        "source_id": source_id,
        "version": _text(contract.get("source_version")),
        "locator": _text(contract.get("source_locator")),
        "kind": "formal_event_contract",
        "quote_hash": _text(contract.get("quote_hash")),
    }]


def _contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        copy.deepcopy(row)
        for row in _list(_dict(asset).get("event_formal_contracts"))
        if isinstance(row, dict)
    ]
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        contract_id = _text(row.get("contract_id"))
        if contract_id:
            deduped.setdefault(contract_id, row)
    return list(deduped.values())


def _resolve_operation(
    contract: dict[str, Any],
    operations: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    explicit = _text(contract.get("operation_ref") or contract.get("operation_id"))
    method = _text(contract.get("method") or contract.get("http_method")).upper()
    path = _text(contract.get("operation_path") or contract.get("api_path") or contract.get("endpoint"))
    if explicit:
        candidates = [
            row
            for row in operations
            if explicit in {
                _text(row.get("id")),
                _text(row.get("operation_id")),
                *[_text(value) for value in _list(row.get("source_operation_refs"))],
            }
        ]
    elif method and path:
        candidates = [
            row
            for row in operations
            if _text(row.get("method")).upper() == method
            and _bir._path_shape(row.get("path")) == _bir._path_shape(path)
        ]
    else:
        return None, "FORMAL_EVENT_OPERATION_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, (
            "FORMAL_EVENT_OPERATION_AMBIGUOUS"
            if len(candidates) > 1
            else "FORMAL_EVENT_OPERATION_NOT_FOUND"
        )
    return candidates[0], ""


def _actor_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role") or actor.get("role_key")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    return bool(
        secret_ref
        and not secret_ref.lower().startswith("secret_ref:actor:")
        and (
            actor.get("runtime_bound") is True
            or _text(actor.get("account_ref"))
        )
    )


def _resolve_actor(
    contract: dict[str, Any],
    actors: list[dict[str, Any]],
) -> tuple[dict[str, Any] | None, str]:
    explicit = _text(contract.get("actor_ref") or contract.get("actor_id"))
    role = _text(contract.get("actor_role") or contract.get("role")).casefold()
    if explicit:
        candidates = [row for row in actors if _text(row.get("id")) == explicit]
    elif role:
        candidates = [
            row
            for row in actors
            if role in {
                _text(row.get("role")).casefold(),
                _text(row.get("role_key")).casefold(),
            }
        ]
        executable = [row for row in candidates if _actor_executable(row)]
        if executable:
            candidates = executable
    else:
        return None, "FORMAL_EVENT_ACTOR_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, (
            "FORMAL_EVENT_ACTOR_AMBIGUOUS"
            if len(candidates) > 1
            else "FORMAL_EVENT_ACTOR_NOT_FOUND"
        )
    if not _actor_executable(candidates[0]):
        return None, "FORMAL_EVENT_ACTOR_NOT_EXECUTABLE"
    return candidates[0], ""


def _gap(contract: dict[str, Any], reason_code: str) -> dict[str, Any]:
    contract_id = _text(contract.get("contract_id")) or "unknown_event_contract"
    return _bir._fact_node(
        node_id=_stable_id("gap", "formal_event_contract", contract_id, reason_code),
        typed_fields={
            "gap_type": "formal_event_contract_not_executable",
            "reason_code": reason_code,
            "contract_id": contract_id,
            "description": reason_code,
        },
        source_refs=_source_refs(contract),
        confidence=1.0,
        derivation="explicit",
        status="unsupported",
    )


def bind_source_event_contracts(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model = copy.deepcopy(_dict(behavior_ir))
    contracts = _contracts(_dict(asset))
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    actors = [row for row in _list(model.get("actors")) if isinstance(row, dict)]
    existing = {
        _text(row.get("event_contract_id"))
        for row in _list(model.get("invariants"))
        if isinstance(row, dict) and _text(row.get("event_contract_id"))
    }
    relation_ids = {
        _text(row.get("id"))
        for row in _list(model.get("relations"))
        if isinstance(row, dict)
    }
    bound = 0
    gaps = 0
    reason_counts: dict[str, int] = {}

    for contract in contracts:
        contract_id = _text(contract.get("contract_id"))
        if not contract_id or contract_id in existing:
            continue
        refs = _source_refs(contract)
        operation, operation_reason = _resolve_operation(contract, operations)
        actor, actor_reason = _resolve_actor(contract, actors)
        reason = (
            "FORMAL_EVENT_SOURCE_REF_MISSING"
            if not refs
            else operation_reason or actor_reason
        )
        if reason:
            model.setdefault("coverage_gaps", []).append(_gap(contract, reason))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            gaps += 1
            continue
        assert operation is not None and actor is not None
        operation_ref = _text(operation.get("id"))
        actor_ref = _text(actor.get("id"))
        invariant_id = _stable_id("inv", "formal_event_contract", contract_id)
        invariant = _bir._fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": _text(contract.get("title")) or contract_id,
                "expression": {
                    "kind": "event_delivery_contract",
                    "operator": "must_match_declared_event_delivery",
                    "operands": [],
                    "raw": _text(contract.get("title")) or contract_id,
                },
                "operation_refs": [operation_ref],
                "source_rule_refs": [contract_id],
                "event_contract_id": contract_id,
                "event_contract": copy.deepcopy(contract),
                "event_actor_ref": actor_ref,
                "binding_status": "source_identity_bound",
            },
            source_refs=refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
        relation = _bir._relation_node(
            relation_type="produces",
            from_ref=operation_ref,
            to_ref=invariant_id,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
            preconditions=[{
                "kind": "source_declared_event_observer",
                "observer_path": _text(contract.get("observer_path")),
            }],
            effects=[{
                "kind": "event_delivery",
                "event_type": _text(contract.get("expected_event_type")),
                "min_count": int(contract.get("expected_min_count") or 0),
                "max_count": int(contract.get("expected_max_count") or 0),
            }],
            source_refs=refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
            source_relationship_ref=contract_id,
        )
        model.setdefault("invariants", []).append(invariant)
        if _text(relation.get("id")) not in relation_ids:
            model.setdefault("relations", []).append(relation)
            relation_ids.add(_text(relation.get("id")))
        existing.add(contract_id)
        bound += 1

    receipt = {
        "schema_version": BINDING_RECEIPT_SCHEMA,
        "status": "BOUND" if bound else "BLOCKED" if contracts else "NOT_REQUESTED",
        "contract_count": len(contracts),
        "bound_invariant_count": bound,
        "coverage_gap_count": gaps,
        "reason_counts": dict(sorted(reason_counts.items())),
        "binding_basis": "exact_source_identity_only",
    }
    model["source_event_contract_binding_receipt"] = receipt
    model, identity_receipt = project_formal_event_binding_identities(model, asset)
    receipt["binding_identity_status"] = identity_receipt.get("status")
    receipt["binding_identity_required"] = bool(
        identity_receipt.get("identity_required")
    )
    receipt["binding_identity_bound_count"] = int(
        identity_receipt.get("bound_count") or 0
    )
    receipt["binding_identity_blocked_count"] = int(
        identity_receipt.get("blocked_count") or 0
    )
    model["source_event_contract_binding_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError(
            "source_event_contract_binding_invalid:" + ",".join(errors[:12])
        )
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


__all__ = ["bind_source_event_contracts"]
