"""Compile exact latency-budget invariants into formal performance obligations.

The generic invariant compiler does not preserve the registered latency protocol template. This
extension removes only the generic obligation for the same invariant and emits one GET/HEAD
performance obligation with exact operation, actor, relation and source identities.
"""
from __future__ import annotations

import copy
from typing import Any

from . import discovery_runtime_planning as _planning
from . import obligation_compiler as _compiler
from .formal_performance_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
)
from .test_obligation import canonical_risk_families, dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_source_performance_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_for_source_performance"


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


def _performance_invariants(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == "latency_budget_contract"
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
        and _dict(row.get("performance_contract"))
    ]


def compile_obligations_with_source_performance(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    baseline = dict(base_compile(behavior_ir, **kwargs))
    invariants = _performance_invariants(behavior_ir)
    if not invariants:
        by_family = dict(baseline.get("by_family") or {})
        for family in canonical_risk_families():
            by_family.setdefault(family, 0)
        baseline["by_family"] = dict(sorted(by_family.items()))
        baseline["source_performance_obligation_receipt"] = {
            "schema_version": "qualibug.source-performance-obligation-binding.v1",
            "status": "NOT_REQUESTED",
            "invariant_count": 0,
            "obligation_count": 0,
            "misclassified_obligation_count_removed": 0,
            "complete_family_vector": True,
        }
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
    relations = [
        row
        for row in _list(_dict(behavior_ir).get("relations"))
        if isinstance(row, dict)
    ]
    additions: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []

    for invariant in invariants:
        invariant_ref = _text(invariant.get("id"))
        operation_refs = [
            _text(value)
            for value in _list(invariant.get("operation_refs"))
            if _text(value) in operations
        ]
        actor_ref = _text(invariant.get("performance_actor_ref"))
        if len(operation_refs) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_PERFORMANCE_OPERATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        operation_ref = operation_refs[0]
        operation = operations[operation_ref]
        actor = actors.get(actor_ref)
        if actor is None:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_PERFORMANCE_ACTOR_IDENTITY_NOT_FOUND",
            })
            continue
        if _text(operation.get("method")).upper() not in {"GET", "HEAD"}:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_PERFORMANCE_GET_OR_HEAD_REQUIRED",
            })
            continue
        matching = [
            row
            for row in relations
            if _text(row.get("relation_type")) == "observes"
            and _text(row.get("from_ref")) == invariant_ref
            and _text(row.get("to_ref")) == operation_ref
            and _text(row.get("operation_ref")) == operation_ref
            and _text(row.get("actor_ref")) == actor_ref
        ]
        if len(matching) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_PERFORMANCE_RELATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        contract = copy.deepcopy(_dict(invariant.get("performance_contract")))
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "invariant_ref": invariant_ref,
            "expression": copy.deepcopy(_dict(invariant.get("expression"))),
            "operation_ref": operation_ref,
            "operation_path_prefix": _text(operation.get("path")),
            "actor_ref": actor_ref,
            "performance_contract_id": _text(invariant.get("performance_contract_id")),
            "performance_contract": contract,
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
            subject_refs=[invariant_ref, operation_ref, actor_ref],
            property_spec=property_spec,
            required_actors=[actor_ref],
            required_operations=[operation_ref],
            required_observers=[OBSERVER_ID],
            cleanup_requirement={
                "required": False,
                "reason": "read_only_sequential_latency_sampling",
            },
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
            "id": "compile_gap_perf_" + _text(row.get("invariant_ref")).removeprefix("bir_")[:16],
            "code": _text(row.get("reason_code")),
            "gap_type": "formal_performance_obligation_not_compiled",
            "subject_ref": _text(row.get("invariant_ref")),
            "risk_family": RISK_FAMILY,
            "status": "unsupported",
            "description": "Exact latency invariant could not become one obligation",
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
        "source_performance_obligation_receipt": {
            "schema_version": "qualibug.source-performance-obligation-binding.v1",
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
            "complete_family_vector": True,
            "load_capacity_claimed": False,
        },
    })
    return baseline


def install_source_performance_obligation_binding() -> None:
    """Wrap the current compiler authority, preserving UI and event extensions."""
    if getattr(_planning, _INSTALL_MARKER, False):
        return
    original = getattr(
        _planning,
        _ORIGINAL_MARKER,
        _planning.compile_obligations_from_behavior_ir,
    )
    setattr(_planning, _ORIGINAL_MARKER, original)

    def compile_with_performance(behavior_ir: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return compile_obligations_with_source_performance(
            behavior_ir,
            base_compile=original,
            **kwargs,
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_performance
    _compiler.compile_obligations_from_behavior_ir = compile_with_performance
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "compile_obligations_with_source_performance",
    "install_source_performance_obligation_binding",
]
