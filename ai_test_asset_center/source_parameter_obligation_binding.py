"""Compile parameter-bound invariants into obligations; degradation channel.

The generic invariant compiler does not preserve the registered parameter-scale
protocol template.  This extension removes only the generic obligations for the
same invariants and emits one GET/HEAD parameter-scale obligation with exact
operation, actor, relation and source identities (mirroring
``source_performance_obligation_binding``).

In addition, when NO parameter-bound contract exists for a GET/HEAD operation
that declares an integer query parameter with no upper bound, the degradation
channel emits a generic resource-protection obligation (``derivation=
generic_resource_protection``): escalating magnitudes are injected and the
verdict is runtime-observed only — a probe that cannot complete, or an
accepted probe whose response time scales unboundedly with magnitude.  No
business rule is inferred; the operation identity is source-declared, the
parameter is source-declared, and the anomaly is observed.  The channel is a
receipted, capped budget, never a silent truncation.
"""
from __future__ import annotations

import copy
import functools
import hashlib
import json
from typing import Any

from . import discovery_runtime_planning as _planning
from . import obligation_compiler as _compiler
from .formal_parameter_scale_surface import (
    CONTRACT_KIND,
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
)
from .test_obligation import canonical_risk_families, dedupe_obligations, make_obligation

_INSTALL_MARKER = "_qualibug_source_parameter_obligation_binding_installed"
_ORIGINAL_MARKER = "_qualibug_original_compile_obligations_before_parameter_scale"

# Degradation-channel budget (product-owned, receipted, operator-visible).
_GENERIC_CHANNEL_MAX_OBLIGATIONS = 4
_GENERIC_CHANNEL_CONFIDENCE = 0.6


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _digest(*parts: Any) -> str:
    canonical = json.dumps(parts, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


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


def _parameter_invariants(behavior_ir: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_dict(behavior_ir).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == "parameter_scale_budget_contract"
        and _text(row.get("status")) not in {"conflicting", "unsupported", "unknown"}
        and _dict(row.get("performance_contract"))
    ]


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
            or bool(_text(actor.get("account_ref")))
        )
    )


def _resolve_single_actor(
    operation: dict[str, Any],
    actors: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """Exactly one executable actor for an operation; None otherwise."""
    candidates = [row for row in actors if isinstance(row, dict)]
    if len(candidates) == 1:
        actor = candidates[0]
        return actor if _actor_executable(actor) else None
    required_roles = [
        _text(role).strip()
        for role in _list(operation.get("required_roles"))
        if _text(role).strip()
    ]
    for role in required_roles:
        matched = [
            row
            for row in candidates
            if role.casefold()
            in {
                _text(row.get("role") or "").casefold(),
                _text(row.get("role_key") or "").casefold(),
                _text(row.get("name") or "").casefold(),
            }
        ]
        executable = [row for row in matched if _actor_executable(row)]
        if len(executable) == 1:
            return executable[0]
    return None


def _integer_query_parameters(operation: dict[str, Any]) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in _list(operation.get("parameters")):
        if not isinstance(row, dict):
            continue
        location = _text(row.get("in") or row.get("location") or "query").lower()
        if location not in {"query", ""}:
            continue
        schema = _dict(row.get("schema"))
        param_type = _text(schema.get("type")).lower()
        if param_type not in {"integer", "int", "int32", "int64", "number"}:
            continue
        name = _text(row.get("name") or row.get("key") or row.get("param_name"))
        if not name:
            continue
        output.append({"name": name, "schema": schema})
    return output


def _generic_parameter_scale_obligations(
    behavior_ir: dict[str, Any],
    existing_obligations: list[dict[str, Any]],
    covered_keys: set[tuple[str, str]],
    *,
    cap: int = _GENERIC_CHANNEL_MAX_OBLIGATIONS,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Degradation channel: unbounded integer query params on GET/HEAD ops.

    Returns ``(obligations, skips)`` — every candidate that does not qualify
    is receipted with a reason code (never a silent truncation).
    """
    operations = [
        row for row in _list(_dict(behavior_ir).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("method")).upper() in {"GET", "HEAD"}
        and _text(row.get("status")) not in {"unsupported", "unknown"}
        and "{" not in _text(row.get("path"))
    ]
    actors = [row for row in _list(_dict(behavior_ir).get("actors")) if isinstance(row, dict)]
    covered_operations: set[str] = set()
    for obligation in existing_obligations:
        if not isinstance(obligation, dict):
            continue
        if _text(obligation.get("risk_family")) == RISK_FAMILY:
            for op_ref in _list(obligation.get("required_operations")):
                if _text(op_ref):
                    covered_operations.add(_text(op_ref))
    additions: list[dict[str, Any]] = []
    skips: list[dict[str, Any]] = []
    for operation in operations:
        if len(additions) >= cap:
            skips.append({
                "operation_ref": _text(operation.get("id")),
                "reason_code": "GENERIC_CHANNEL_BUDGET_EXHAUSTED",
            })
            continue
        operation_ref = _text(operation.get("id"))
        if not operation_ref:
            skips.append({
                "operation_ref": _text(operation.get("id")),
                "reason_code": "GENERIC_CHANNEL_OPERATION_IDENTITY_MISSING",
            })
            continue
        if operation_ref in covered_operations:
            skips.append({
                "operation_ref": operation_ref,
                "reason_code": "GENERIC_CHANNEL_OPERATION_ALREADY_COVERED",
            })
            continue
        actor = _resolve_single_actor(operation, actors)
        if actor is None:
            skips.append({
                "operation_ref": operation_ref,
                "reason_code": "GENERIC_CHANNEL_EXECUTABLE_ACTOR_UNRESOLVED",
            })
            continue
        for param in _integer_query_parameters(operation):
            if len(additions) >= cap:
                skips.append({
                    "operation_ref": operation_ref,
                    "parameter_name": _text(param.get("name")),
                    "reason_code": "GENERIC_CHANNEL_BUDGET_EXHAUSTED",
                })
                break
            schema = _dict(param.get("schema"))
            if schema.get("maximum") is not None:
                skips.append({
                    "operation_ref": operation_ref,
                    "parameter_name": _text(param.get("name")),
                    "reason_code": "GENERIC_CHANNEL_BOUND_DECLARED",
                })
                continue
            name = _text(param.get("name"))
            if not name:
                skips.append({
                    "operation_ref": operation_ref,
                    "reason_code": "GENERIC_CHANNEL_PARAMETER_NAME_MISSING",
                })
                continue
            key = (operation_ref, name)
            if key in covered_keys:
                skips.append({
                    "operation_ref": operation_ref,
                    "parameter_name": name,
                    "reason_code": "GENERIC_CHANNEL_PARAMETER_ALREADY_COVERED",
                })
                continue
            contract_id = "generic_psb_" + _digest(operation_ref, name)
            contract: dict[str, Any] = {
                "schema_version": "qualibug.formal-performance-contract.v1",
                "contract_kind": CONTRACT_KIND,
                "contract_id": contract_id,
                "source_refs": [],
                "method": _text(operation.get("method")).upper(),
                "operation_path": _text(operation.get("path")),
                "parameter_name": name,
                "declared_min": None,
                "declared_max": None,
                "status": "accepted",
                "derivation": "generic_resource_protection",
                "origin": "generic_resource_protection_channel",
                "confidence": _GENERIC_CHANNEL_CONFIDENCE,
            }
            actor_ref = _text(actor.get("id"))
            property_spec = {
                "template": PROTOCOL_TEMPLATE,
                "invariant_ref": "",
                "expression": {
                    "kind": "parameter_scale_budget_contract",
                    "operator": "must_not_exhaust_resources_on_large_input",
                    "operands": [name],
                    "raw": f"generic resource protection for query parameter {name}",
                },
                "operation_ref": operation_ref,
                "operation_path_prefix": _text(operation.get("path")),
                "actor_ref": actor_ref,
                "performance_contract_id": contract_id,
                "performance_contract": contract,
                "claim_derivation": "generic_resource_protection",
                "field_rule_binding": {
                    "rule_id": contract_id,
                    "rule_fingerprint": contract_id,
                    "rule_type": RISK_FAMILY,
                    "required_field_ids": [],
                    "typed_expression": {
                        "kind": "parameter_scale_budget_contract",
                    },
                    "operation_id": operation_ref,
                },
            }
            additions.append(make_obligation(
                risk_family=RISK_FAMILY,
                subject_refs=[operation_ref, actor_ref],
                property_spec=property_spec,
                required_actors=[actor_ref],
                required_operations=[operation_ref],
                required_observers=[OBSERVER_ID],
                cleanup_requirement={
                    "required": False,
                    "reason": "read_only_parameter_scale_probes",
                },
                source_refs=_source_refs(operation, actor),
                confidence=_GENERIC_CHANNEL_CONFIDENCE,
            ))
            covered_keys.add(key)
    return additions, skips


def compile_obligations_with_source_parameter_scale(
    behavior_ir: dict[str, Any],
    *,
    base_compile: Any,
    **kwargs: Any,
) -> dict[str, Any]:
    baseline = dict(base_compile(behavior_ir, **kwargs))
    invariants = _parameter_invariants(behavior_ir)
    baseline_rows = [
        dict(row)
        for row in _list(baseline.get("obligations"))
        if isinstance(row, dict)
    ]
    invariant_ids = {_text(row.get("id")) for row in invariants}
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
    covered_keys: set[tuple[str, str]] = set()

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
                "reason_code": "PARAMETER_SCALE_OPERATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        operation_ref = operation_refs[0]
        operation = operations[operation_ref]
        actor = actors.get(actor_ref)
        if actor is None:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "PARAMETER_SCALE_ACTOR_IDENTITY_NOT_FOUND",
            })
            continue
        if _text(operation.get("method")).upper() not in {"GET", "HEAD"}:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "PARAMETER_SCALE_GET_OR_HEAD_REQUIRED",
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
                "reason_code": "PARAMETER_SCALE_RELATION_IDENTITY_NOT_UNIQUE",
            })
            continue
        contract = copy.deepcopy(_dict(invariant.get("performance_contract")))
        parameter_name = _text(contract.get("parameter_name"))
        if not parameter_name:
            skipped.append({
                "invariant_ref": invariant_ref,
                "reason_code": "PARAMETER_SCALE_CONTRACT_PARAMETER_MISSING",
            })
            continue
        property_spec = {
            "template": PROTOCOL_TEMPLATE,
            "invariant_ref": invariant_ref,
            "expression": copy.deepcopy(_dict(invariant.get("expression"))),
            "operation_ref": operation_ref,
            "operation_path_prefix": _text(operation.get("path")),
            "actor_ref": actor_ref,
            "performance_contract_id": _text(invariant.get("performance_contract_id")),
            "performance_contract": contract,
            "claim_derivation": _text(contract.get("derivation")) or "explicit",
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
                "reason": "read_only_parameter_scale_probes",
            },
            source_refs=_source_refs(invariant, operation, actor, matching[0]),
            relation_refs=[_text(matching[0].get("id"))],
            confidence=min(
                float(invariant.get("confidence") or 1.0),
                float(operation.get("confidence") or 1.0),
                float(actor.get("confidence") or 1.0),
            ),
        ))
        covered_keys.add((operation_ref, parameter_name))

    # Degradation channel: generic resource-protection obligations for
    # GET/HEAD integer query parameters with no declared upper bound that no
    # existing performance obligation covers.
    generic_additions, generic_skips = _generic_parameter_scale_obligations(
        behavior_ir,
        [*retained, *additions],
        covered_keys,
    )

    obligations = dedupe_obligations([*retained, *additions, *generic_additions])
    gaps = [
        dict(row)
        for row in _list(baseline.get("coverage_gaps"))
        if isinstance(row, dict)
        and _text(row.get("subject_ref")) not in invariant_ids
    ]
    for row in skipped:
        gaps.append({
            "id": "compile_gap_psb_" + _text(row.get("invariant_ref")).removeprefix("bir_")[:16],
            "code": _text(row.get("reason_code")),
            "gap_type": "parameter_scale_obligation_not_compiled",
            "subject_ref": _text(row.get("invariant_ref")),
            "risk_family": RISK_FAMILY,
            "status": "unsupported",
            "description": "Exact parameter-bound invariant could not become one obligation",
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
        "source_parameter_obligation_receipt": {
            "schema_version": "qualibug.source-parameter-obligation-binding.v1",
            "status": "COMPILED" if additions or generic_additions else "BLOCKED",
            "invariant_count": len(invariants),
            "obligation_count": len(additions),
            "generic_resource_protection_obligation_count": len(generic_additions),
            "generic_channel_budget": {
                "cap": _GENERIC_CHANNEL_MAX_OBLIGATIONS,
                "reason_code": "operator_visible_receipted_budget",
            },
            "generic_channel_skip_count": len(generic_skips),
            "generic_channel_skip_reason_counts": {
                reason: sum(
                    1 for row in generic_skips if _text(row.get("reason_code")) == reason
                )
                for reason in sorted({
                    _text(row.get("reason_code")) for row in generic_skips
                })
                if reason
            },
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


def install_source_parameter_obligation_binding() -> None:
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
    def compile_with_parameter_scale(behavior_ir: dict[str, Any], **kwargs: Any) -> dict[str, Any]:
        return compile_obligations_with_source_parameter_scale(
            behavior_ir,
            base_compile=original,
            **kwargs,
        )

    _planning.compile_obligations_from_behavior_ir = compile_with_parameter_scale
    _compiler.compile_obligations_from_behavior_ir = compile_with_parameter_scale
    setattr(_planning, _INSTALL_MARKER, True)


__all__ = [
    "compile_obligations_with_source_parameter_scale",
    "install_source_parameter_obligation_binding",
]
