"""Compile source-resolved separation-of-duties invariants into existing auth experiments."""
from __future__ import annotations

import hashlib
from typing import Any

from . import obligation_compiler_base as _base


def _text(value: Any) -> str: return str(value or "").strip()
def _list(value: Any) -> list[Any]: return value if isinstance(value, list) else []
def _dict(value: Any) -> dict[str, Any]: return value if isinstance(value, dict) else {}


def _credential(actor: dict[str, Any]) -> str:
    return _text(actor.get("credential_identity_ref") or actor.get("account_ref"))


def _executable(actor: dict[str, Any]) -> bool:
    secret = _text(actor.get("credential_secret_ref"))
    return bool(_text(actor.get("account_ref")) and secret and not secret.startswith("secret_ref:actor:"))


def _gap(invariant_id: str, reason: str) -> dict[str, Any]:
    digest = hashlib.sha256(f"{invariant_id}|{reason}".encode()).hexdigest()[:16]
    return {"id": f"compile_gap_{digest}", "code": "BLOCKED_SOD_RUNTIME_BINDING", "gap_type": "segregation_of_duties_runtime_binding_unresolved", "subject_ref": invariant_id, "reason": reason, "status": "unsupported"}


def compile_sod_obligations(behavior_ir: dict[str, Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    ir = _dict(behavior_ir)
    actors = [_dict(row) for row in _list(ir.get("actors")) if isinstance(row, dict)]
    relations = [_dict(row) for row in _list(ir.get("relations")) if isinstance(row, dict)]
    actors_by_role: dict[str, list[dict[str, Any]]] = {}
    for actor in actors:
        if _executable(actor): actors_by_role.setdefault(_text(actor.get("role_key") or actor.get("role")).casefold(), []).append(actor)
    permits = {(_text(row.get("actor_ref") or row.get("from_ref")), _text(row.get("operation_ref"))) for row in relations if _text(row.get("relation_type")) == "permits" and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}}
    operation_aliases: dict[str, str] = {}
    for operation in _list(ir.get("operations")):
        if not isinstance(operation, dict):
            continue
        operation_id = _text(operation.get("id"))
        for alias in [operation_id, operation.get("operation_id"), operation.get("canonical_operation_id"), *_list(operation.get("source_operation_refs"))]:
            if _text(alias) and operation_id:
                operation_aliases[_text(alias)] = operation_id
    obligations: list[dict[str, Any]] = []; gaps: list[dict[str, Any]] = []
    for invariant in _list(ir.get("invariants")):
        if not isinstance(invariant, dict): continue
        expression = _dict(invariant.get("expression"))
        if _text(expression.get("kind")) != "segregation_of_duties": continue
        policy = next((_dict(row) for row in _list(expression.get("operands")) if isinstance(row, dict)), {})
        invariant_id = _text(invariant.get("id"))
        setup_role = _text(policy.get("setup_role")).casefold(); guarded_role = _text(policy.get("guarded_role")).casefold()
        setup_op = operation_aliases.get(_text(policy.get("setup_operation_ref")), _text(policy.get("setup_operation_ref")))
        guarded_op = operation_aliases.get(_text(policy.get("guarded_operation_ref")), _text(policy.get("guarded_operation_ref")))
        setup_actors = actors_by_role.get(setup_role, []); guarded_actors = actors_by_role.get(guarded_role, [])
        conflict_pairs = [(left, right) for left in setup_actors for right in guarded_actors if _text(left.get("id")) != _text(right.get("id")) and _credential(left) and _credential(left) == _credential(right) and (_text(left.get("id")), setup_op) in permits and (_text(right.get("id")), guarded_op) in permits]
        if not conflict_pairs:
            gaps.append(_gap(invariant_id, "shared_credential_role_pair_unresolved")); continue
        for setup_actor, treatment_actor in conflict_pairs:
            controls = [actor for actor in guarded_actors if _credential(actor) and _credential(actor) != _credential(treatment_actor) and (_text(actor.get("id")), guarded_op) in permits]
            if not controls:
                gaps.append(_gap(invariant_id, "independent_guarded_actor_unresolved")); continue
            control = sorted(controls, key=lambda row: _text(row.get("id")))[0]
            obligations.append(_base.make_obligation(
                risk_family="authorization",
                subject_refs=[invariant_id, guarded_op, _text(control.get("id")), _text(treatment_actor.get("id")), _text(setup_actor.get("id"))],
                property_spec={
                    "template": "authorization_control_treatment", "operation_ref": guarded_op,
                    "control_actor_ref": _text(control.get("id")), "treatment_actor_ref": _text(treatment_actor.get("id")),
                    "fixture_owner_actor_ref": _text(setup_actor.get("id")), "owner_actor_ref": _text(setup_actor.get("id")), "setup_operation_ref": setup_op,
                    "sod_invariant_ref": invariant_id, "sod_policy_id": _text(policy.get("policy_id")),
                    "require_same_resource": True, "require_ownership_evidence": True,
                },
                required_actors=[_text(control.get("id")), _text(treatment_actor.get("id")), _text(setup_actor.get("id"))],
                required_operations=[guarded_op, setup_op], required_fixtures=["owned_resource"],
                required_observers=["http_response", "actor_identity"], cleanup_requirement={"required": True},
                source_refs=[dict(row) for row in _list(invariant.get("source_refs")) if isinstance(row, dict)],
                relation_refs=[], confidence=float(invariant.get("confidence") or 0.9),
            ))
    return obligations, gaps


__all__ = ["compile_sod_obligations"]
