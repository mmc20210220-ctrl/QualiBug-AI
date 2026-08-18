"""Project enterprise-understanding API binding authority into runtime Behavior IR.

Only exact, source-backed identity is admitted. No text similarity, ranking, or
candidate-only binding may become runtime operation authority.
"""
from __future__ import annotations

from typing import Any


def _text(value: Any) -> str:
    return str(value or "").strip()


def _rows(value: Any) -> list[dict[str, Any]]:
    return [
        row
        for row in (value if isinstance(value, list) else [])
        if isinstance(row, dict)
    ]


def _unique(values: list[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _behavior_fact_refs(behavior: dict[str, Any]) -> list[str]:
    refs: list[str] = []
    for item in behavior.get("source_refs") or []:
        if isinstance(item, str):
            value = _text(item)
            if value.startswith("fact"):
                refs.append(value)
        elif isinstance(item, dict):
            for key in ("fact_id", "fact_ref", "ref", "id"):
                value = _text(item.get(key))
                if value.startswith("fact"):
                    refs.append(value)
                    break
    for item in behavior.get("evidence") or []:
        if isinstance(item, dict):
            value = _text(item.get("fact_id"))
            if value:
                refs.append(value)
    return _unique(refs)


def _runtime_operation_index(
    model: dict[str, Any],
) -> tuple[dict[str, set[str]], dict[tuple[str, str], set[str]]]:
    aliases: dict[str, set[str]] = {}
    transports: dict[tuple[str, str], set[str]] = {}
    for operation in _rows(model.get("operations")):
        operation_id = _text(operation.get("id"))
        if not operation_id:
            continue
        for alias in _unique(
            [
                operation_id,
                _text(operation.get("operation_id")),
                *[
                    _text(value)
                    for value in (operation.get("source_operation_refs") or [])
                ],
            ]
        ):
            aliases.setdefault(alias, set()).add(operation_id)
        method = _text(operation.get("method")).upper()
        path = _text(operation.get("path") or operation.get("raw_path"))
        if method and path:
            transports.setdefault((method, path), set()).add(operation_id)
    return aliases, transports


def _resolve_api_binding(
    api_binding: dict[str, Any],
    *,
    aliases: dict[str, set[str]],
    transports: dict[tuple[str, str], set[str]],
) -> set[str]:
    if api_binding.get("authoritative") is not True:
        return set()
    if _text(api_binding.get("status")) not in {"BOUND", "BOUND_CHANNEL_ONLY"}:
        return set()
    if _text(api_binding.get("derivation")) == "token_overlap_diagnostic":
        return set()

    candidates: set[str] = set()
    identity_declared = False
    for key in ("interface_id", "operation_id"):
        value = _text(api_binding.get(key))
        if not value:
            continue
        identity_declared = True
        candidates.update(aliases.get(value, set()))
    method = _text(api_binding.get("method")).upper()
    path = _text(api_binding.get("path"))
    if method and path:
        identity_declared = True
        candidates.update(transports.get((method, path), set()))
    return candidates if identity_declared else set()


def project_enterprise_implementation_authority(
    model: dict[str, Any], asset: dict[str, Any] | None
) -> dict[str, Any]:
    """Attach one exact authoritative implementation operation to matching invariants.

    Shared fact identity is the only bridge from enterprise-understanding Business
    Behavior IR to runtime Behavior IR. Ambiguous or conflicting bindings remain
    fail-closed.
    """
    if not isinstance(model, dict) or not isinstance(asset, dict):
        return model
    enterprise = asset.get("enterprise_understanding_model")
    if not isinstance(enterprise, dict):
        return model

    aliases, transports = _runtime_operation_index(model)
    binding_by_behavior: dict[str, list[dict[str, Any]]] = {}
    for binding in _rows(enterprise.get("behavior_implementation_bindings")):
        behavior_ref = _text(
            binding.get("behavior_ref") or binding.get("source_behavior_ref")
        )
        if behavior_ref:
            binding_by_behavior.setdefault(behavior_ref, []).append(binding)

    fact_ops: dict[str, set[str]] = {}
    ambiguous_facts: set[str] = set()
    for behavior in _rows(enterprise.get("business_behaviors")):
        behavior_id = _text(behavior.get("behavior_id"))
        fact_refs = _behavior_fact_refs(behavior)
        if not behavior_id or not fact_refs:
            continue
        resolved: set[str] = set()
        for binding in binding_by_behavior.get(behavior_id, []):
            if _text(binding.get("status")) == "CONFLICTED":
                ambiguous_facts.update(fact_refs)
                continue
            for api_binding in _rows(binding.get("api_operation_bindings")):
                resolved.update(
                    _resolve_api_binding(
                        api_binding,
                        aliases=aliases,
                        transports=transports,
                    )
                )
        if len(resolved) == 1:
            operation_id = next(iter(resolved))
            for fact_ref in fact_refs:
                fact_ops.setdefault(fact_ref, set()).add(operation_id)
        elif len(resolved) > 1:
            ambiguous_facts.update(fact_refs)

    projected = 0
    ambiguous = 0
    conflicts = 0
    for invariant in _rows(model.get("invariants")):
        fact_refs = _unique(
            [_text(value) for value in (invariant.get("fact_refs") or [])]
        )
        if not fact_refs:
            continue
        candidates: set[str] = set()
        if any(fact_ref in ambiguous_facts for fact_ref in fact_refs):
            ambiguous += 1
            continue
        for fact_ref in fact_refs:
            candidates.update(fact_ops.get(fact_ref, set()))
        if len(candidates) != 1:
            if len(candidates) > 1:
                ambiguous += 1
            continue
        operation_id = next(iter(candidates))
        existing = _unique(
            [_text(value) for value in (invariant.get("operation_refs") or [])]
        )
        if existing and operation_id not in existing:
            conflicts += 1
            continue
        if operation_id not in existing:
            invariant["operation_refs"] = [*existing, operation_id]
            projected += 1
        invariant["implementation_authority"] = {
            "schema": "qualibug.runtime-implementation-authority.v1",
            "status": "BOUND",
            "operation_ref": operation_id,
            "fact_refs": fact_refs,
            "derivation": "enterprise_behavior_fact_identity_exact_api_binding",
            "fuzzy_binding_used": False,
        }

    model["enterprise_implementation_authority_receipt"] = {
        "schema": "qualibug.enterprise-implementation-authority-projection.v1",
        "status": "PASS",
        "projected_invariant_count": projected,
        "ambiguous_invariant_count": ambiguous,
        "conflicting_invariant_count": conflicts,
        "fuzzy_binding_used": False,
        "candidate_only_binding_promoted": False,
    }
    return model
