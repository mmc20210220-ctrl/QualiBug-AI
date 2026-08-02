"""Compile source-bound UI invariants into the single formal UI obligation family.

The generic invariant compiler does not know that ``ui_source_expectation`` must preserve the
registered ``source_declared_ui_expectation`` protocol template. Left alone, it classifies the
invariant as validation and creates a status-code experiment. This extension removes that
misclassified variant and emits exactly one UI obligation from the already exact IR identities.

Read-only UI obligations need no cleanup. Governed interactive UI obligations delegate cleanup
to the registered UI observer, which executes source-declared browser compensation and requires
rendered plus source-selected persistent-state equivalence before Oracle eligibility. The
generic HTTP finalizer must not run a second compensation or label the write as cleanup-free.
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
from .professional_ui_interaction_privacy_guard import EVIDENCE_POLICY
from .professional_ui_persistent_cleanup_probe import (
    EQUIVALENCE_SCOPE,
    PERSISTENT_PROBE_PROPERTY,
)
from .test_obligation import dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_source_ui_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_for_source_ui"
_WRITE_MODE = "approved_sandbox_write"
_READ_ONLY_MODE = "safe_read_only"


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


def _request_modes(request: dict[str, Any]) -> tuple[str, str, str]:
    plan = _dict(request.get("browser_plan"))
    request_mode = _text(request.get("execution_mode"))
    plan_mode = _text(plan.get("execution_mode"))
    effective = request_mode or plan_mode or _READ_ONLY_MODE
    return request_mode, plan_mode, effective


def _request_mode(request: dict[str, Any]) -> str:
    return _request_modes(request)[2]


def _interaction_actions(request: dict[str, Any]) -> list[str]:
    return [
        _text(row.get("action")).lower()
        for row in _list(_dict(request.get("browser_plan")).get("steps"))
        if isinstance(row, dict)
        and _text(row.get("action")).lower() in INTERACTIVE_ACTIONS
    ]


def _persistent_probes(request: dict[str, Any]) -> list[dict[str, Any]]:
    plan = _dict(request.get("browser_plan"))
    return [
        copy.deepcopy(row)
        for row in _list(plan.get("state_probes"))
        if isinstance(row, dict)
        and _text(row.get("property")).lower() == PERSISTENT_PROBE_PROPERTY
    ]


def _cleanup_authority(request: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return property metadata and the generic-finalizer cleanup requirement."""
    actions = _interaction_actions(request)
    if not actions:
        return (
            {
                "mode": "not_required_read_only",
                "observer_id": OBSERVER_ID,
                "equivalence_required": False,
                "contract_complete": True,
            },
            {
                "required": False,
                "mode": "not_required_read_only",
                "reason": "read_only_ui_contract",
            },
        )

    plan = _dict(request.get("browser_plan"))
    contract = _dict(plan.get("interaction_contract"))
    request_mode, plan_mode, effective_mode = _request_modes(request)
    probes = _persistent_probes(request)
    incomplete_reasons: list[str] = []
    if effective_mode != _WRITE_MODE:
        incomplete_reasons.append("approved_sandbox_write_mode_required")
    if request_mode and plan_mode and request_mode != plan_mode:
        incomplete_reasons.append("request_plan_execution_mode_mismatch")
    expected_contract = {
        "cleanup_strategy": "browser_compensation",
        "equivalence": "source_declared_state_probes",
        "equivalence_scope": EQUIVALENCE_SCOPE,
        "target_scope": "approved_nonproduction_target",
        "evidence_policy": EVIDENCE_POLICY,
    }
    for key, expected in expected_contract.items():
        if _text(contract.get(key)) != expected:
            incomplete_reasons.append(f"interaction_contract_{key}_invalid")
    if not probes:
        incomplete_reasons.append("persistent_cleanup_probe_missing")

    authority = {
        "mode": "observer_managed_browser_cleanup",
        "observer_id": OBSERVER_ID,
        "receipt_schema": CLEANUP_RECEIPT_SCHEMA,
        "equivalence_required": True,
        "equivalence_scope": EQUIVALENCE_SCOPE,
        "persistent_probe_property": PERSISTENT_PROBE_PROPERTY,
        "persistent_probe_required": True,
        "persistent_probe_count": len(probes),
        "rendered_state_only_cleanup_accepted": False,
        "universal_backend_restoration_claimed": False,
        "evidence_policy": EVIDENCE_POLICY,
        "har_persisted": False,
        "trace_persisted": False,
        "generic_http_cleanup_must_not_run": True,
        "contract_complete": not incomplete_reasons,
        "incomplete_reasons": incomplete_reasons,
    }
    requirement = {
        "required": False,
        "mode": "observer_managed_browser_cleanup",
        "delegated": True,
        "observer_id": OBSERVER_ID,
        "receipt_schema": CLEANUP_RECEIPT_SCHEMA,
        "equivalence_scope": EQUIVALENCE_SCOPE,
        "persistent_probe_required": True,
        "persistent_probe_count": len(probes),
        "evidence_policy": EVIDENCE_POLICY,
        "reason": "formal_ui_observer_requires_cleanup_equivalence_before_verdict",
    }
    return authority, requirement


def compile_obligations_with_source_ui(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run the established compiler, replace only misclassified UI invariant rows."""
    baseline = dict(base_compile(behavior_ir, **kwargs))
    ui_invariants = _ui_invariants(behavior_ir)
    if not ui_invariants:
        baseline["source_ui_obligation_receipt"] = {
            "schema_version": "qualibug.source-ui-obligation-binding.v1",
            "status": "NOT_REQUESTED",
            "invariant_count": 0,
            "obligation_count": 0,
            "interactive_obligation_count": 0,
            "read_only_obligation_count": 0,
            "persistent_probe_count": 0,
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
    persistent_probe_count = 0

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
        if is_interactive and cleanup_authority.get("contract_complete") is not True:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "FORMAL_UI_INTERACTION_CLEANUP_AUTHORITY_INCOMPLETE",
            })
            continue
        interactive_count += int(is_interactive)
        read_only_count += int(not is_interactive)
        persistent_probe_count += int(
            cleanup_authority.get("persistent_probe_count") or 0
        )
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
            "persistent_probe_count": persistent_probe_count,
            "interactive_cleanup_authority": "formal_ui_observer_receipt",
            "interactive_cleanup_receipt_schema": CLEANUP_RECEIPT_SCHEMA,
            "interactive_cleanup_equivalence_scope": EQUIVALENCE_SCOPE,
            "interactive_persistent_probe_property": PERSISTENT_PROBE_PROPERTY,
            "interactive_evidence_policy": EVIDENCE_POLICY,
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

    def compile_with_ui(behavior_ir: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return compile_obligations_with_source_ui(
            behavior_ir,
            base_compile=original,
            **kwargs,
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_ui
    _compiler.compile_obligations_from_behavior_ir = compile_with_ui
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "compile_obligations_with_source_ui",
    "install_source_ui_obligation_binding",
]
