"""Bind explicit enterprise UI contracts to Behavior IR identities.

The enterprise parser preserves source-authored UI contracts on ``ui_design_specs``. This
module admits one into authoritative Behavior IR only when all three identities are exact:

* one safe API prerequisite operation;
* one executable actor;
* one explicit Playwright request containing an expectation.

There is no token-overlap fallback. Ambiguity or missing identity becomes a named coverage gap,
not a guessed invariant.
"""
from __future__ import annotations

import copy
import hashlib
import json
from typing import Any

# NOTE: ``behavior_ir`` is imported lazily inside the functions that use it
# (see _resolve_operation / _gap / bind_source_ui_contracts). A top-level
# ``from . import behavior_ir`` creates a circular import: behavior_ir ->
# enterprise_understanding -> binding_identity_projection -> this module,
# which fails during collection of enterprise-knowledge tests.
BINDING_RECEIPT_SCHEMA = "qualibug.source-ui-contract-binding.v1"
_EXPECTATION_ACTIONS = frozenset({"expect_text", "expect_url"})
_SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


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
        dict(row)
        for row in _list(contract.get("source_refs"))
        if isinstance(row, dict)
    ]
    if refs:
        return refs
    return [{
        "source_id": _text(contract.get("source_id")) or "ui_design_specs",
        "version": "",
        "locator": _text(contract.get("source_locator")),
        "kind": "formal_ui_contract",
        "quote_hash": "",
    }]


def _contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key in ("ui_design_specs", "ui_specs"):
        for spec in _list(_dict(asset).get(key)):
            if not isinstance(spec, dict):
                continue
            for contract in _list(spec.get("formal_ui_contracts")):
                if isinstance(contract, dict):
                    row = copy.deepcopy(contract)
                    row.setdefault("ui_spec_id", _text(spec.get("ui_spec_id")))
                    rows.append(row)
    for contract in _list(_dict(asset).get("ui_formal_contracts")):
        if isinstance(contract, dict):
            rows.append(copy.deepcopy(contract))
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
    from . import behavior_ir as _bir

    explicit = _text(contract.get("operation_ref") or contract.get("operation_id"))
    method = _text(contract.get("method") or contract.get("http_method")).upper()
    path = _text(
        contract.get("operation_path")
        or contract.get("api_path")
        or contract.get("endpoint")
    )
    candidates = list(operations)
    if explicit:
        candidates = [
            row
            for row in candidates
            if explicit in {
                _text(row.get("id")),
                _text(row.get("operation_id")),
                *[_text(value) for value in _list(row.get("source_operation_refs"))],
            }
        ]
    elif method and path:
        candidates = [
            row
            for row in candidates
            if _text(row.get("method")).upper() == method
            and _bir._path_shape(row.get("path")) == _bir._path_shape(path)
        ]
    else:
        return None, "FORMAL_UI_OPERATION_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, (
            "FORMAL_UI_OPERATION_AMBIGUOUS"
            if len(candidates) > 1
            else "FORMAL_UI_OPERATION_NOT_FOUND"
        )
    operation = candidates[0]
    if _text(operation.get("method")).upper() not in _SAFE_METHODS:
        return None, "FORMAL_UI_PREREQUISITE_WRITE_NOT_ALLOWED"
    return operation, ""


def _actor_executable(actor: dict[str, Any]) -> bool:
    role = _text(actor.get("role") or actor.get("role_key")).lower()
    if role in {"anonymous", "public"}:
        return True
    secret_ref = _text(actor.get("credential_secret_ref") or actor.get("secret_ref"))
    return bool(
        actor.get("runtime_bound") is True
        and secret_ref
        and not secret_ref.lower().startswith("secret_ref:actor:")
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
        return None, "FORMAL_UI_ACTOR_IDENTITY_MISSING"
    if len(candidates) != 1:
        return None, (
            "FORMAL_UI_ACTOR_AMBIGUOUS"
            if len(candidates) > 1
            else "FORMAL_UI_ACTOR_NOT_FOUND"
        )
    actor = candidates[0]
    if not _actor_executable(actor):
        return None, "FORMAL_UI_ACTOR_NOT_EXECUTABLE"
    return actor, ""


def _validated_request(contract: dict[str, Any]) -> tuple[dict[str, Any] | None, str]:
    request = copy.deepcopy(_dict(contract.get("ui_request")))
    plan = _dict(request.get("browser_plan"))
    steps = [dict(row) for row in _list(plan.get("steps")) if isinstance(row, dict)]
    if _text(request.get("provider")).lower() != "playwright_browser_plan":
        return None, "FORMAL_UI_PROVIDER_NOT_SOURCE_DECLARED"
    if not _text(request.get("start_url")):
        return None, "FORMAL_UI_START_URL_MISSING"
    if not steps:
        return None, "FORMAL_UI_BROWSER_PLAN_MISSING"
    if not any(
        _text(row.get("action")).lower() in _EXPECTATION_ACTIONS
        for row in steps
    ):
        return None, "FORMAL_UI_EXPECTATION_MISSING"
    request["browser_plan"] = {**plan, "steps": steps}
    return request, ""


def _gap(
    contract: dict[str, Any],
    *,
    reason_code: str,
    detail: str = "",
) -> dict[str, Any]:
    from . import behavior_ir as _bir

    contract_id = _text(contract.get("contract_id")) or "unknown_ui_contract"
    return _bir._fact_node(
        node_id=_stable_id("gap", "formal_ui_contract", contract_id, reason_code),
        typed_fields={
            "gap_type": "formal_ui_contract_not_executable",
            "reason_code": reason_code,
            "contract_id": contract_id,
            "ui_spec_id": _text(contract.get("ui_spec_id")),
            "description": detail or reason_code,
        },
        source_refs=_source_refs(contract),
        confidence=1.0,
        derivation="explicit",
        status="unsupported",
    )


def bind_source_ui_contracts(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Add exact formal-UI invariants and relations to an already built IR."""
    from . import behavior_ir as _bir

    model = copy.deepcopy(_dict(behavior_ir))
    contracts = _contracts(_dict(asset))
    operations = [
        row for row in _list(model.get("operations")) if isinstance(row, dict)
    ]
    actors = [row for row in _list(model.get("actors")) if isinstance(row, dict)]
    existing_invariants = {
        _text(row.get("ui_contract_id"))
        for row in _list(model.get("invariants"))
        if isinstance(row, dict) and _text(row.get("ui_contract_id"))
    }
    existing_relation_ids = {
        _text(row.get("id"))
        for row in _list(model.get("relations"))
        if isinstance(row, dict)
    }
    bound = 0
    gap_count = 0
    reason_counts: dict[str, int] = {}

    for contract in contracts:
        contract_id = _text(contract.get("contract_id"))
        if not contract_id or contract_id in existing_invariants:
            continue
        request, request_reason = _validated_request(contract)
        operation, operation_reason = _resolve_operation(contract, operations)
        actor, actor_reason = _resolve_actor(contract, actors)
        reason = request_reason or operation_reason or actor_reason
        if reason:
            model.setdefault("coverage_gaps", []).append(
                _gap(contract, reason_code=reason)
            )
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            gap_count += 1
            continue
        assert request is not None and operation is not None and actor is not None
        operation_ref = _text(operation.get("id"))
        actor_ref = _text(actor.get("id"))
        invariant_id = _stable_id("inv", "formal_ui_contract", contract_id)
        source_refs = _source_refs(contract)
        expectation_actions = [
            _text(row.get("action")).lower()
            for row in _list(_dict(request.get("browser_plan")).get("steps"))
            if isinstance(row, dict)
            and _text(row.get("action")).lower() in _EXPECTATION_ACTIONS
        ]
        invariant = _bir._fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": _text(contract.get("title")) or contract_id,
                "expression": {
                    "kind": "ui_source_expectation",
                    "operator": "must_render_source_expectation",
                    "operands": [],
                    "raw": _text(contract.get("title")) or contract_id,
                },
                "operation_refs": [operation_ref],
                "source_rule_refs": [contract_id],
                "ui_contract_id": contract_id,
                "ui_request": request,
                "ui_actor_ref": actor_ref,
                "ui_expectation_actions": expectation_actions,
                "binding_status": "source_identity_bound",
            },
            source_refs=source_refs,
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
                "kind": "source_declared_ui_page",
                "start_url": _text(request.get("start_url")),
            }],
            effects=[],
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
            source_relationship_ref=contract_id,
        )
        model.setdefault("invariants", []).append(invariant)
        if _text(relation.get("id")) not in existing_relation_ids:
            model.setdefault("relations", []).append(relation)
            existing_relation_ids.add(_text(relation.get("id")))
        existing_invariants.add(contract_id)
        bound += 1

    receipt = {
        "schema_version": BINDING_RECEIPT_SCHEMA,
        "status": "BOUND" if bound else "NO_EXECUTABLE_CONTRACTS" if contracts else "NOT_REQUESTED",
        "contract_count": len(contracts),
        "bound_invariant_count": bound,
        "coverage_gap_count": gap_count,
        "reason_counts": dict(sorted(reason_counts.items())),
        "binding_basis": "exact_source_identity_only",
    }
    model["source_ui_contract_binding_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError(
            "source_ui_contract_binding_invalid:" + ",".join(errors[:12])
        )
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


__all__ = ["bind_source_ui_contracts"]
