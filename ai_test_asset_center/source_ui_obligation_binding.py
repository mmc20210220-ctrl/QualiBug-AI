"""Compile source-bound UI invariants into the single formal UI obligation family.

The generic invariant compiler does not know that ``ui_source_expectation`` must preserve the
registered ``source_declared_ui_expectation`` protocol template. Left alone, it classifies the
invariant as validation and creates a status-code experiment. This extension removes that
misclassified variant and emits exactly one UI obligation from the already exact IR identities.

Read-only UI obligations need no cleanup. Governed interactive UI obligations delegate cleanup
to the registered UI observer, which executes the source-declared browser compensation and
requires a content-addressed cleanup-equivalence receipt before Oracle eligibility. The generic
HTTP finalizer must not run a second compensation or label the write as cleanup-free.
"""
from __future__ import annotations

import copy
from typing import Any

from . import discovery_runtime_planning as _planning
from . import obligation_compiler as _compiler
from .formal_ui_surface import (
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
)
from .professional_ui_interaction_cleanup import (
    CLEANUP_RECEIPT_SCHEMA,
    INTERACTIVE_ACTIONS,
)
from .test_obligation import dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_source_ui_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_for_source_ui"
_WRITE_MODE = "approved_sandbox_write"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique_source_refs(*rows: dict[str, Any]) -> list[dict[str, Any]]:
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


def _ui_invariants(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == "ui_source_expectation"
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
        and _dict(row.get("ui_request"))
    ]


def _request_mode(request: dict[str, Any]) -> str:
    plan = _dict(request.get("browser_plan"))
    return _text(
        request.get("execution_mode")
        or plan.get("execution_mode")
        or "safe_read_only"
    )


def _interaction_actions(request: dict[str, Any]) -> list[str]:
    return [
        _text(row.get("action")).lower()
        for row in _list(_dict(request.get("browser_plan")).get("steps"))
        if isinstance(row, dict)
        and _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
    ]


def _cleanup_authority(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return property metadata and the generic-finalizer cleanup requirement."""
    actions = _interaction_actions(request)
    mode = _request_mode(request)
    if mode == _WRITE_MODE and actions:
        authority = {
            "mode": "observer_managed_browser_cleanup",
            "observer_id": OBSERVER_ID,
            "receipt_schema": CLEANUP_RECEIPT_SCHEMA,
            "equivalence_required": True,
            "generic_http_cleanup_must_not_run": True,
        }
        requirement = {
            "required": False,
            "mode": "observer_managed_browser_cleanup",
            "delegated": True,
            "observer_id": OBSERVER_ID,
            "receipt_schema": CLEANUP_RECEIPT_SCHEMA,
            "reason": "formal_ui_observer_requires_cleanup_equivalence_before_verdict",
        }
        return authority, requirement
    return (
        {
            "mode": "not_required_read_only",
            "observer_id": OBSERVER_ID,
            "equivalence_required": False,
        },
        {
            "required": False,
            "mode": "not_required_read_only",
            "reason": "read_only_ui_contract",
        },
    )


def compile_obligations_with_source_ui(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
) -> dict[str, Any]:
    """Run the established compiler, replace only misclassified UI invariant rows."""
    baseline = dict(base_compile(behavior_ir))
    ui_invariants = _ui_invariants(behavior_ir)
    if not ui_invariants:
        baseline["source_ui_obligation_receipt"] = {
            "schema_version": "qualibug.source-ui-obligation-binding.v1",
            "status": "NOT_REQUESTED",
            "invariant_count": 0,
            "obligation_count": 0,
            "interactive_obligation_count": 0,
            "read_only_obligation_count": 0,
            "misclassified_obligation_count_removed": 0,
        }
        return baseline

    invariant_ids = {_text(row.get("id")) for row in ui_invariants}
    baseline_obligations = [
        dict(row)
        for row in _list(baseline.get("obligations"))
        if isinstance(row, dict)
    ]
    retained = [
        row
        for row in baseline_obligations
        if _text(_dict(row.get("property")).get("invariant_ref")) not in invariant_ids
    ]
    removed = len(baseline_obligations) - len(retained)

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
    interactive_count = 0
    read_only_count = 0

    for invariant in ui_invariants:
        invariant_ref = _text(invariant.get("id"))
        operation_refs = [
            _text(value)
            for value in _list(invariant.get("operation_refs"))
            if _text(value) in operations
        ]
        actor_ref = _text(invariant.get("ui_actor_ref"))
        if len(operation_refs) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_UI_OPERATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        operation_ref = operation_refs[0]
        operation = operations[operation_ref]
        actor = actors.get(actor_ref)
        if actor is None:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_UI_ACTOR_IDENTITY_NOT_FOUND",
            })
            continue
        if _text(operation.get("method")).upper() not in {"GET", "HEAD", "OPTIONS"}:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_UI_PREREQUISITE_WRITE_NOT_ALLOWED",
            })
            continue
        matching_relations = [
            row
            for row in relations
            if _text(row.get("relation_type")) == "observes"
            and _text(row.get("operation_ref")) == operation_ref
            and invariant_ref in {
                _text(row.get("from_ref")),
                _text(row.get("to_ref")),
            }
            and _text(row.get("actor_ref")) == actor_ref
        ]
        if len(matching_relations) != 1:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_UI_RELATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        request = copy.deepcopy(_dict(invariant.get("ui_request")))
        cleanup_authority, cleanup_requirement = _cleanup_authority(request)
        is_interactive = cleanup_authority.get("equivalence_required") is True
        interactive_count += int(is_interactive)
        read_only_count += int(not is_interactive)
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "invariant_ref": invariant_ref,
            "expression": copy.deepcopy(_dict(invariant.get("expression"))),
            "operation_ref": operation_ref,
            "operation_path_prefix": _text(operation.get("path")),
            "actor_ref": actor_ref,
            "ui_contract_id": _text(invariant.get("ui_contract_id")),
            "ui_request": request,
            "ui_execution_mode": _request_mode(request),
            "ui_interaction_actions": _interaction_actions(request),
            "ui_cleanup_authority": cleanup_authority,
            "ui_expectation_actions": [
                _text(value)
                for value in _list(invariant.get("ui_expectation_actions"))
                if _text(value)
            ],
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
            cleanup_requirement=cleanup_requirement,
            source_refs=_unique_source_refs(
                invariant,
                operation,
                actor,
                matching_relations[0],
            ),
            relation_refs=[_text(matching_relations[0].get("id"))],
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
            "id": "compile_gap_" + _text(row.get("invariant_ref")).removeprefix("bir_")[:16],
            "code": _text(row.get("reason_code")),
            "gap_type": "formal_ui_obligation_not_compiled",
            "subject_ref": _text(row.get("invariant_ref")),
            "risk_family": RISK_FAMILY,
            "status": "unsupported",
            "description": "Exact formal UI invariant could not become one obligation",
        })

    families = sorted({
        _text(row.get("risk_family"))
        for row in obligations
        if _text(row.get("risk_family"))
    })
    baseline.update({
        "obligations": obligations,
        "obligation_count": len(obligations),
        "coverage_gaps": gaps,
        "by_family": {
            family: sum(
                1
                for row in obligations
                if _text(row.get("risk_family")) == family
            )
            for family in families
        },
        "source_ui_obligation_receipt": {
            "schema_version": "qualibug.source-ui-obligation-binding.v1",
            "status": "COMPILED" if additions else "BLOCKED",
            "invariant_count": len(ui_invariants),
            "obligation_count": len(additions),
            "interactive_obligation_count": interactive_count,
            "read_only_obligation_count": read_only_count,
            "interactive_cleanup_authority": "formal_ui_observer_receipt",
            "interactive_cleanup_receipt_schema": CLEANUP_RECEIPT_SCHEMA,
            "misclassified_obligation_count_removed": removed,
            "skipped_count": len(skipped),
            "skipped_reason_counts": {
                reason: sum(
                    1
                    for row in skipped
                    if _text(row.get("reason_code")) == reason
                )
                for reason in sorted({
                    _text(row.get("reason_code")) for row in skipped
                })
                if reason
            },
        },
    })
    return baseline


def install_source_ui_obligation_binding() -> None:
    """Install on both direct compiler calls and the planning module's imported symbol."""
    if getattr(_planning, _INSTALL_MARKER, False):
        return
    original = getattr(
        _planning,
        _ORIGINAL_MARKER,
        _planning.compile_obligations_from_behavior_ir,
    )
    setattr(_planning, _ORIGINAL_MARKER, original)

    def compile_with_ui(behavior_ir: dict[str, Any]) -> dict[str, Any]:
        return compile_obligations_with_source_ui(
            behavior_ir,
            base_compile=original,
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_ui
    _compiler.compile_obligations_from_behavior_ir = compile_with_ui
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "compile_obligations_with_source_ui",
    "install_source_ui_obligation_binding",
]
