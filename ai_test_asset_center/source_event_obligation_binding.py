"""Compile source-bound event invariants into formal event-delivery obligations.

The established obligation compiler remains the base authority. This extension removes only
its generic/misclassified obligation for the same event invariant and emits one registered
event family obligation with exact operation, actor, relation, cleanup and durable binding
identities.
"""
from __future__ import annotations

import copy
from typing import Any

from . import discovery_runtime_planning as _planning
from . import obligation_compiler as _compiler
from .formal_event_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
)
from .obligation_compiler_base import _cleanup_requirement
from .test_obligation import canonical_risk_families, dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_source_event_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_for_source_event"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


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


def _event_invariants(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == "event_delivery_contract"
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
        and _dict(row.get("event_contract"))
    ]


def compile_obligations_with_source_event(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    baseline = dict(base_compile(behavior_ir, **kwargs))
    invariants = _event_invariants(behavior_ir)
    identity_receipt = _dict(
        _dict(behavior_ir).get("formal_event_binding_identity_receipt")
    )
    identity_required = bool(identity_receipt.get("identity_required"))
    if not invariants:
        baseline["source_event_obligation_receipt"] = {
            "schema_version": "qualibug.source-event-obligation-binding.v1",
            "status": "NOT_REQUESTED",
            "invariant_count": 0,
            "obligation_count": 0,
            "misclassified_obligation_count_removed": 0,
            "binding_identity_required": identity_required,
            "binding_identity_obligation_count": 0,
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
    identity_obligation_count = 0

    for invariant in invariants:
        invariant_ref = _text(invariant.get("id"))
        formal_identity = copy.deepcopy(
            _dict(invariant.get("formal_event_binding_identity"))
        )
        invariant_identity_required = bool(
            invariant.get("event_binding_identity_required")
        )
        if invariant_identity_required and (
            _text(invariant.get("event_binding_identity_status")) != "BOUND"
            or _text(formal_identity.get("status")) != "BOUND"
        ):
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": _text(
                    invariant.get("event_binding_identity_reason_code")
                ) or "FORMAL_EVENT_BINDING_IDENTITY_NOT_CLOSED",
            })
            continue
        operation_refs = [
            _text(value)
            for value in _list(invariant.get("operation_refs"))
            if _text(value) in operations
        ]
        actor_ref = _text(invariant.get("event_actor_ref"))
        if len(operation_refs) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_EVENT_OPERATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        operation_ref = operation_refs[0]
        operation = operations[operation_ref]
        actor = actors.get(actor_ref)
        if actor is None:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_EVENT_ACTOR_IDENTITY_NOT_FOUND",
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
                "reason_code": "FORMAL_EVENT_RELATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        event_contract = copy.deepcopy(_dict(invariant.get("event_contract")))
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "invariant_ref": invariant_ref,
            "expression": copy.deepcopy(_dict(invariant.get("expression"))),
            "operation_ref": operation_ref,
            "operation_path_prefix": _text(operation.get("path")),
            "actor_ref": actor_ref,
            "event_contract_id": _text(invariant.get("event_contract_id")),
            "event_contract": event_contract,
            "formal_event_binding_identity": formal_identity,
            "observer_binding_ref": formal_identity.get("observer_binding_ref"),
            "action_surface_binding_ref": formal_identity.get(
                "action_surface_binding_ref"
            ),
            "implementation_binding_ref": formal_identity.get(
                "implementation_binding_ref"
            ),
            "runtime_plan_ref": formal_identity.get("runtime_plan_ref"),
            "runtime_materialization_ref": formal_identity.get(
                "runtime_materialization_ref"
            ),
            "field_rule_binding": {
                "rule_id": invariant_ref,
                "rule_fingerprint": invariant_ref,
                "rule_type": RISK_FAMILY,
                "required_field_ids": [],
                "typed_expression": copy.deepcopy(_dict(invariant.get("expression"))),
                "operation_id": operation_ref,
            },
        }
        subject_refs = [invariant_ref, operation_ref, actor_ref]
        subject_refs.extend(
            _text(formal_identity.get(key))
            for key in (
                "implementation_binding_ref",
                "action_surface_binding_ref",
                "observer_binding_ref",
                "runtime_plan_ref",
                "runtime_materialization_ref",
            )
            if _text(formal_identity.get(key))
        )
        additions.append(make_obligation(
            risk_family=RISK_FAMILY,
            subject_refs=list(dict.fromkeys(subject_refs)),
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
        if formal_identity:
            identity_obligation_count += 1

    obligations = dedupe_obligations([*retained, *additions])
    gaps = [
        dict(row)
        for row in _list(baseline.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("subject_ref")) not in invariant_ids
    ]
    for row in skipped:
        gaps.append({
            "id": "compile_gap_event_" + _text(row.get("invariant_ref")).removeprefix("bir_")[:16],
            "code": _text(row.get("reason_code")),
            "gap_type": "formal_event_obligation_not_compiled",
            "subject_ref": _text(row.get("invariant_ref")),
            "risk_family": RISK_FAMILY,
            "status": "unsupported",
            "description": "Exact formal event invariant could not become one obligation",
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
        "source_event_obligation_receipt": {
            "schema_version": "qualibug.source-event-obligation-binding.v1",
            "status": "COMPILED" if additions else "BLOCKED",
            "invariant_count": len(invariants),
            "obligation_count": len(additions),
            "misclassified_obligation_count_removed": removed,
            "skipped_count": len(skipped),
            "skipped_reason_counts": {
                reason: sum(1 for row in skipped if _text(row.get("reason_code")) == reason)
                for reason in sorted({_text(row.get("reason_code")) for row in skipped})
                if reason
            },
            "binding_identity_required": identity_required,
            "binding_identity_status": identity_receipt.get("status"),
            "binding_identity_obligation_count": identity_obligation_count,
            "runtime_overlay_event_invariant_count": int(
                identity_receipt.get("runtime_overlay_event_invariant_count") or 0
            ),
            "complete_family_vector": True,
        },
    })
    return baseline


def install_source_event_obligation_binding() -> None:
    """Wrap the current compiler authority, preserving UI and all prior extensions."""
    if getattr(_planning, _INSTALL_MARKER, False):
        return
    original = getattr(
        _planning,
        _ORIGINAL_MARKER,
        _planning.compile_obligations_from_behavior_ir,
    )
    setattr(_planning, _ORIGINAL_MARKER, original)

    def compile_with_event(behavior_ir: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return compile_obligations_with_source_event(
            behavior_ir,
            base_compile=original,
            **kwargs,
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_event
    _compiler.compile_obligations_from_behavior_ir = compile_with_event
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "compile_obligations_with_source_event",
    "install_source_event_obligation_binding",
]
