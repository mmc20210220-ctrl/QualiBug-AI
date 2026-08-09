"""Compile source-bound message-chain invariants into executable obligations.

The established obligation compiler remains the base authority. This extension
removes only its generic/misclassified obligation for the same chain invariant
and emits one registered ``event_delivery_consistency`` obligation on the
``message_chain_verification`` protocol with the exact trigger operation,
actor, relation and cleanup identities. Runtime event surfaces (the
degradation channel) bind the same way but with ``derivation=runtime-observed``
and ``channel=runtime_observation`` -- visible in the receipt, never silent.
"""
from __future__ import annotations

import copy
import hashlib
import functools
from typing import Any

from . import discovery_runtime_planning as _planning
from . import obligation_compiler as _compiler
from . import behavior_ir as _bir
from .message_chain_surface import OBSERVER_ID, PROTOCOL_TEMPLATE, RISK_FAMILY
from .obligation_compiler_base import _cleanup_requirement
from .source_event_contract_binding import _resolve_actor, _resolve_operation
from .test_obligation import canonical_risk_families, dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_message_chain_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_for_message_chain"
_EXPRESSION_KIND = "message_chain_consistency"
BINDING_RECEIPT_SCHEMA = "qualibug.message-chain-binding.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    return "bir_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _source_refs(*rows: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in rows:
        for ref in _list(_dict(row).get("source_refs")):
            if not isinstance(ref, dict):
                continue
            key = repr(sorted(ref.items()))
            if key in seen:
                continue
            seen.add(key)
            output.append(copy.deepcopy(ref))
    return output[:8]


def _chain_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    """Chain contracts (source-bound) plus runtime event surfaces (implicit)."""
    rows = [
        copy.deepcopy(row)
        for row in [
            *_list(_dict(asset).get("message_chain_contracts")),
            *_list(_dict(asset).get("runtime_event_surfaces")),
        ]
        if isinstance(row, dict)
    ]
    deduped: dict[str, dict[str, Any]] = {}
    for row in rows:
        contract_id = _text(row.get("contract_id") or row.get("surface_id"))
        if contract_id:
            deduped.setdefault(contract_id, row)
    return list(deduped.values())


def _is_runtime_surface_row(contract: dict[str, Any]) -> bool:
    return bool(
        _text(contract.get("derivation")) == "runtime-observed"
        or _text(contract.get("schema_version")).startswith(
            "qualibug.runtime-event-surface"
        )
        or _text(contract.get("surface_id"))
    )


def _gap(contract: dict[str, Any], reason_code: str) -> dict[str, Any]:
    contract_id = _text(
        contract.get("contract_id") or contract.get("surface_id")
    ) or "unknown_message_chain"
    return _bir._fact_node(
        node_id=_stable_id("gap", "message_chain_contract", contract_id, reason_code),
        typed_fields={
            "gap_type": "message_chain_contract_not_executable",
            "reason_code": reason_code,
            "contract_id": contract_id,
            "description": reason_code,
        },
        source_refs=_source_refs(contract),
        confidence=1.0,
        derivation="explicit",
        status="unsupported",
    )


def bind_source_message_chains(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    model = copy.deepcopy(_dict(behavior_ir))
    chains = _chain_rows(_dict(asset))
    operations = [row for row in _list(model.get("operations")) if isinstance(row, dict)]
    actors = [row for row in _list(model.get("actors")) if isinstance(row, dict)]
    existing = {
        _text(row.get("message_chain_contract_id"))
        for row in _list(model.get("invariants"))
        if isinstance(row, dict) and _text(row.get("message_chain_contract_id"))
    }
    relation_ids = {
        _text(row.get("id"))
        for row in _list(model.get("relations"))
        if isinstance(row, dict)
    }
    bound = 0
    gaps = 0
    reason_counts: dict[str, int] = {}

    for contract in chains:
        contract_id = _text(contract.get("contract_id") or contract.get("surface_id"))
        if not contract_id or contract_id in existing:
            continue
        operation, operation_reason = _resolve_operation(contract, operations)
        actor, actor_reason = _resolve_actor(contract, actors)
        refs = _source_refs(contract)
        is_runtime = _is_runtime_surface_row(contract)
        if is_runtime:
            reason = operation_reason or actor_reason
        else:
            reason = (
                "FORMAL_CHAIN_SOURCE_REF_MISSING"
                if not refs
                else operation_reason or actor_reason
            )
        if reason:
            model.setdefault("coverage_gaps", []).append(_gap(contract, reason))
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            gaps += 1
            continue
        assert operation is not None and actor is not None
        # Stamp the channel so raw runtime surface rows and source contracts
        # both carry an explicit, receipted channel on the invariant.
        chain_contract = copy.deepcopy(contract)
        chain_contract["derivation"] = (
            "runtime-observed" if is_runtime else _text(contract.get("derivation")) or "explicit"
        )
        chain_contract["channel"] = (
            "runtime_observation"
            if is_runtime
            else _text(contract.get("channel")) or "source_contract"
        )
        operation_ref = _text(operation.get("id"))
        actor_ref = _text(actor.get("id"))
        invariant_id = _stable_id("inv", "message_chain_contract", contract_id)
        invariant = _bir._fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": _text(contract.get("title")) or contract_id,
                "expression": {
                    "kind": _EXPRESSION_KIND,
                    "operator": "must_match_declared_message_chain",
                    "operands": [],
                    "raw": _text(contract.get("title")) or contract_id,
                },
                "operation_refs": [operation_ref],
                "source_rule_refs": [contract_id],
                "message_chain_contract_id": contract_id,
                "message_chain": chain_contract,
                "event_actor_ref": actor_ref,
                "binding_status": "source_identity_bound",
            },
            source_refs=refs,
            confidence=float(contract.get("confidence") or 1.0),
            derivation=chain_contract["derivation"],
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
                "kind": "message_chain_delivery",
                "event_name": _text(contract.get("event_name")),
                "min_count": int(contract.get("expected_min_count") or 0),
                "max_count": contract.get("expected_max_count"),
            }],
            source_refs=refs,
            confidence=float(contract.get("confidence") or 1.0),
            derivation=_text(contract.get("derivation")) or "explicit",
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
        "status": "BOUND" if bound else "BLOCKED" if chains else "NOT_REQUESTED",
        "contract_count": len(chains),
        "bound_invariant_count": bound,
        "coverage_gap_count": gaps,
        "reason_counts": dict(sorted(reason_counts.items())),
        "binding_basis": "exact_source_identity_only",
        "runtime_observation_surface_count": sum(
            1 for row in chains if _is_runtime_surface_row(row)
        ),
    }
    model["message_chain_binding_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError(
            "message_chain_binding_invalid:" + ",".join(errors[:12])
        )
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


def _chain_invariants(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == _EXPRESSION_KIND
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
        and _dict(row.get("message_chain"))
    ]


def compile_obligations_with_message_chain(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    baseline = dict(base_compile(behavior_ir, **kwargs))
    invariants = _chain_invariants(behavior_ir)
    if not invariants:
        baseline["message_chain_obligation_receipt"] = {
            "schema_version": "qualibug.message-chain-obligation-binding.v1",
            "status": "NOT_REQUESTED",
            "invariant_count": 0,
            "obligation_count": 0,
            "misclassified_obligation_count_removed": 0,
            "complete_family_vector": True,
        }
        by_family = dict(baseline.get("by_family") or {})
        for family in canonical_risk_families():
            by_family.setdefault(family, 0)
        baseline["by_family"] = dict(sorted(by_family.items()))
        return baseline

    invariant_ids = {_text(row.get("id")) for row in invariants}
    baseline_rows = [
        dict(row)
        for row in _list(baseline.get("obligations"))
        if isinstance(row, dict)
    ]
    retained = [
        row
        for row in baseline_rows
        if _text(_dict(row.get("property")).get("invariant_ref")) not in invariant_ids
    ]
    removed = len(baseline_rows) - len(retained)
    operations = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    actors = {
        _text(row.get("id")): row
        for row in _list(_dict(behavior_ir).get("actors"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    operation_rows = list(operations.values())
    relations = [
        row
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict)
    ]
    additions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for invariant in invariants:
        invariant_ref = _text(invariant.get("id"))
        contract = copy.deepcopy(_dict(invariant.get("message_chain")))
        operation_refs = [
            _text(value)
            for value in _list(invariant.get("operation_refs"))
            if _text(value) in operations
        ]
        actor_ref = _text(invariant.get("event_actor_ref"))
        if len(operation_refs) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "MESSAGE_CHAIN_OPERATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        operation_ref = operation_refs[0]
        operation = operations[operation_ref]
        actor = actors.get(actor_ref)
        if actor is None:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "MESSAGE_CHAIN_ACTOR_IDENTITY_NOT_FOUND",
            })
            continue
        matching = [
            row
            for row in relations
            if _text(row.get("relation_type")) == "produces"
            and _text(row.get("operation_ref")) == operation_ref
            and _text(row.get("from_ref")) == operation_ref
            and _text(row.get("to_ref")) == invariant_ref
            and _text(row.get("actor_ref")) == actor_ref
        ]
        if len(matching) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "MESSAGE_CHAIN_RELATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "invariant_ref": invariant_ref,
            "expression": copy.deepcopy(_dict(invariant.get("expression"))),
            "operation_ref": operation_ref,
            "operation_path_prefix": _text(operation.get("path")),
            "actor_ref": actor_ref,
            "message_chain_contract_id": _text(contract.get("contract_id")),
            "message_chain": contract,
            "channel": _text(contract.get("channel")) or "source_contract",
            "derivation": _text(contract.get("derivation")) or "explicit",
            "field_rule_binding": {
                "rule_id": invariant_ref,
                "rule_fingerprint": invariant_ref,
                "rule_type": RISK_FAMILY,
                "required_field_ids": [],
                "typed_expression": copy.deepcopy(_dict(invariant.get("expression"))),
                "operation_id": operation_ref,
            },
        }
        additions.append(make_obligation(
            risk_family=RISK_FAMILY,
            subject_refs=list(dict.fromkeys([invariant_ref, operation_ref, actor_ref])),
            property_spec=property_spec,
            required_actors=[actor_ref],
            required_operations=[operation_ref],
            required_observers=[OBSERVER_ID],
            cleanup_requirement=_cleanup_requirement(
                operation,
                operation_rows,
                relations,
            ),
            source_refs=_source_refs(invariant, operation, actor, matching[0]),
            relation_refs=[_text(matching[0].get("id"))],
            confidence=min(
                float(invariant.get("confidence") or 1.0),
                float(operation.get("confidence") or 1.0),
                float(actor.get("confidence") or 1.0),
            ),
        ))

    obligations = dedupe_obligations([*retained, *additions])
    gaps = [
        dict(row)
        for row in _list(baseline.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("subject_ref")) not in invariant_ids
    ]
    for row in skipped:
        gaps.append({
            "id": "compile_gap_message_chain_" + _text(row.get("invariant_ref")).removeprefix("bir_")[:16],
            "code": _text(row.get("reason_code")),
            "gap_type": "message_chain_obligation_not_compiled",
            "subject_ref": _text(row.get("invariant_ref")),
            "risk_family": RISK_FAMILY,
            "status": "unsupported",
            "description": "Exact message-chain invariant could not become one obligation",
        })

    families = {
        _text(value) for value in canonical_risk_families() if _text(value)
    }
    families.update(
        _text(row.get("risk_family"))
        for row in obligations
        if _text(row.get("risk_family"))
    )
    families.update(
        _text(value) for value in dict(baseline.get("by_family") or {}) if _text(value)
    )
    baseline.update({
        "obligations": obligations,
        "obligation_count": len(obligations),
        "coverage_gaps": gaps,
        "by_family": {
            family: sum(1 for row in obligations if _text(row.get("risk_family")) == family)
            for family in sorted(families)
        },
        "message_chain_obligation_receipt": {
            "schema_version": "qualibug.message-chain-obligation-binding.v1",
            "status": "COMPILED" if additions else "BLOCKED",
            "invariant_count": len(invariants),
            "obligation_count": len(additions),
            "misclassified_obligation_count_removed": removed,
            "skipped_count": len(skipped),
            "skipped_reason_counts": {
                reason: sum(
                    1 for row in skipped if _text(row.get("reason_code")) == reason
                )
                for reason in sorted({_text(row.get("reason_code")) for row in skipped})
                if reason
            },
            "runtime_observation_obligation_count": sum(
                1
                for row in additions
                if _text(_dict(row.get("property")).get("channel")) == "runtime_observation"
            ),
            "complete_family_vector": True,
        },
    })
    return baseline


def install_message_chain_obligation_binding() -> None:
    """Wrap the current compiler authority, preserving outer install markers."""
    if getattr(_planning, _INSTALL_MARKER, False):
        return
    original = getattr(
        _planning,
        _ORIGINAL_MARKER,
        _planning.compile_obligations_from_behavior_ir,
    )
    setattr(_planning, _ORIGINAL_MARKER, original)

    @functools.wraps(original)
    def compile_with_message_chain(
        behavior_ir: dict[str, Any],
        **kwargs: Any,
    ) -> dict[str, Any]:
        return compile_obligations_with_message_chain(
            behavior_ir,
            base_compile=original,
            **kwargs,
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_message_chain
    _compiler.compile_obligations_from_behavior_ir = compile_with_message_chain
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "bind_source_message_chains",
    "compile_obligations_with_message_chain",
    "install_message_chain_obligation_binding",
]
