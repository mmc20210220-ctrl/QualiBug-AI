"""Bind source-declared short-window stability contracts to exact IR identities."""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from . import behavior_ir as _bir

BINDING_SCHEMA = "qualibug.source-stability-contract-binding.v1"
_SAFE_METHODS = frozenset({"GET", "HEAD"})


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
    refs = [copy.deepcopy(row) for row in _list(contract.get("source_refs")) if isinstance(row, dict) and _text(row.get("source_id"))]
    if refs:
        return refs
    source_id = _text(contract.get("source_id"))
    return [{
        "source_id": source_id,
        "version": _text(contract.get("source_version")),
        "locator": _text(contract.get("source_locator")),
        "kind": "formal_stability_contract",
        "quote_hash": _text(contract.get("quote_hash")),
    }] if source_id else []


def _resolve_operation(contract: dict[str, Any], operations: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    explicit = _text(contract.get("operation_ref") or contract.get("operation_id"))
    method = _text(contract.get("method") or contract.get("http_method")).upper()
    path = _text(contract.get("operation_path") or contract.get("api_path") or contract.get("endpoint"))
    if explicit:
        candidates = [row for row in operations if explicit in {
            _text(row.get("id")), _text(row.get("operation_id")),
            *[_text(value) for value in _list(row.get("source_operation_refs"))],
        }]
    elif method and path:
        candidates = [row for row in operations if _text(row.get("method")).upper() == method and _bir._path_shape(row.get("path")) == _bir._path_shape(path)]
    else:
        return None, "FORMAL_STABILITY_OPERATION_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, "FORMAL_STABILITY_OPERATION_AMBIGUOUS" if len(candidates) > 1 else "FORMAL_STABILITY_OPERATION_NOT_FOUND"
    if _text(candidates[0].get("method")).upper() not in _SAFE_METHODS:
        return None, "FORMAL_STABILITY_GET_OR_HEAD_REQUIRED"
    return candidates[0], ""


def _actor_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role") or actor.get("role_key")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    return bool(secret and not secret.lower().startswith("secret_ref:actor:") and (actor.get("runtime_bound") is True or _text(actor.get("account_ref"))))


def _resolve_actor(contract: dict[str, Any], actors: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str]:
    explicit = _text(contract.get("actor_ref") or contract.get("actor_id"))
    role = _text(contract.get("actor_role") or contract.get("role")).casefold()
    if explicit:
        candidates = [row for row in actors if _text(row.get("id")) == explicit]
    elif role:
        candidates = [row for row in actors if role in {_text(row.get("role")).casefold(), _text(row.get("role_key")).casefold()}]
        executable = [row for row in candidates if _actor_executable(row)]
        if executable:
            candidates = executable
    else:
        return None, "FORMAL_STABILITY_ACTOR_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, "FORMAL_STABILITY_ACTOR_AMBIGUOUS" if len(candidates) > 1 else "FORMAL_STABILITY_ACTOR_NOT_FOUND"
    if not _actor_executable(candidates[0]):
        return None, "FORMAL_STABILITY_ACTOR_NOT_EXECUTABLE"
    return candidates[0], ""


def _gap(contract: dict[str, Any], reason: str) -> dict[str, Any]:
    contract_id = _text(contract.get("contract_id")) or "unknown_stability_contract"
    return _bir._fact_node(
        node_id=_stable_id("gap", contract_id, reason),
        typed_fields={
            "gap_type": "formal_stability_contract_not_executable",
            "reason_code": reason,
            "contract_id": contract_id,
            "description": reason,
        },
        source_refs=_source_refs(contract),
        confidence=1.0,
        derivation="explicit",
        status="unsupported",
    )


def bind_source_stability_contracts(behavior_ir: dict[str, Any], asset: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any]]:
    model = copy.deepcopy(_dict(behavior_ir))
    contracts = [copy.deepcopy(row) for row in _list(_dict(asset).get("stability_formal_contracts")) if isinstance(row, dict)]
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    actors = [row for row in _list(model.get("actors")) if isinstance(row, dict)]
    existing = {_text(row.get("stability_contract_id")) for row in _list(model.get("invariants")) if isinstance(row, dict) and _text(row.get("stability_contract_id"))}
    relation_ids = {_text(row.get("id")) for row in _list(model.get("relations")) if isinstance(row, dict)}
    bound = 0
    gaps = 0
    reasons: dict[str, int] = {}
    for contract in contracts:
        contract_id = _text(contract.get("contract_id"))
        if not contract_id or contract_id in existing:
            continue
        refs = _source_refs(contract)
        operation, operation_reason = _resolve_operation(contract, operations)
        actor, actor_reason = _resolve_actor(contract, actors)
        reason = "FORMAL_STABILITY_SOURCE_REF_MISSING" if not refs else operation_reason or actor_reason
        if reason:
            model.setdefault("coverage_gaps", []).append(_gap(contract, reason))
            reasons[reason] = reasons.get(reason, 0) + 1
            gaps += 1
            continue
        assert operation is not None and actor is not None
        operation_ref = _text(operation.get("id"))
        actor_ref = _text(actor.get("id"))
        invariant_id = _stable_id("inv", "formal_stability_contract", contract_id)
        invariant = _bir._fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": _text(contract.get("title")) or contract_id,
                "expression": {
                    "kind": "read_stability_contract",
                    "operator": "must_meet_declared_read_reliability_budget",
                    "operands": [],
                    "raw": _text(contract.get("title")) or contract_id,
                },
                "operation_refs": [operation_ref],
                "source_rule_refs": [contract_id],
                "stability_contract_id": contract_id,
                "stability_contract": copy.deepcopy(contract),
                "stability_actor_ref": actor_ref,
                "binding_status": "source_identity_bound",
            },
            source_refs=refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
        relation = _bir._relation_node(
            relation_type="observes",
            from_ref=invariant_id,
            to_ref=operation_ref,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
            preconditions=[{
                "kind": "source_declared_short_window_reliability",
                "sample_count": int(contract.get("sample_count") or 0),
            }],
            effects=[],
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
        "schema_version": BINDING_SCHEMA,
        "status": "BOUND" if bound else "BLOCKED" if contracts else "NOT_REQUESTED",
        "contract_count": len(contracts),
        "bound_invariant_count": bound,
        "coverage_gap_count": gaps,
        "reason_counts": dict(sorted(reasons.items())),
        "binding_basis": "exact_source_identity_only",
        "long_duration_stability_claimed": False,
    }
    model["source_stability_contract_binding_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError("source_stability_contract_binding_invalid:" + ",".join(errors[:12]))
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


__all__ = ["bind_source_stability_contracts"]
