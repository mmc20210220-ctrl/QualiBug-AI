"""Bind confirmed enterprise Job behaviors to exact canonical Behavior IR identities.

The enterprise Business Behavior IR remains semantic authority.  This binder adds only the
canonical ASYNC_JOB operation, runtime-integrity invariant and exact actor relation needed by
the existing obligation compiler.  Candidate, unsafe or ambiguous Jobs become coverage gaps.
"""
from __future__ import annotations

import copy
import hashlib
from typing import Any

from . import behavior_ir as _bir
from .job_async_protocol import TEMPLATE_ASYNC_JOB_EXECUTION
from .job_platform_contract import ASYNC_OPERATION_KIND

BINDING_RECEIPT_SCHEMA = "qualibug.source-job-contract-binding.v1"
INVARIANT_KIND = "async_job_runtime_integrity_contract"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _stable_id(*parts: Any) -> str:
    raw = "|".join(_text(part) for part in parts if _text(part))
    return "bir_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def _evidence(value: dict[str, Any]) -> list[dict[str, Any]]:
    return [copy.deepcopy(row) for row in _list(value.get("evidence")) if isinstance(row, dict)]


def _enterprise_model(asset: dict[str, Any]) -> dict[str, Any]:
    return _dict(asset.get("enterprise_understanding_model"))


def _confirmed_job_behaviors(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        row
        for row in _list(_enterprise_model(asset).get("business_behaviors"))
        if isinstance(row, dict)
        and _text(row.get("source_kind")) == "ASYNC_JOB_ASSET"
        and _text(row.get("status")) == "CONFIRMED"
        and row.get("formal_business_rule") is True
        and row.get("formal_business_finding_eligible") is False
    ]


def _enterprise_operations(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("operation_id")): row
        for row in _list(_enterprise_model(asset).get("operations"))
        if isinstance(row, dict) and _text(row.get("operation_id"))
    }


def _job_assets(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        _text(row.get("job_asset_id")): row
        for row in _list(asset.get("job_assets"))
        if isinstance(row, dict) and _text(row.get("job_asset_id"))
    }


def _enterprise_actors(asset: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for row in _list(_enterprise_model(asset).get("actors")):
        if not isinstance(row, dict):
            continue
        for identity in (row.get("actor_id"), row.get("id")):
            if _text(identity):
                result[_text(identity)] = row
    return result


def _resolve_or_add_actor(
    model: dict[str, Any],
    enterprise_actor_ref: str,
    enterprise_actors: dict[str, dict[str, Any]],
    source_refs: list[dict[str, Any]],
) -> tuple[str, str]:
    candidates: list[dict[str, Any]] = []
    for row in _list(model.get("actors")):
        if not isinstance(row, dict):
            continue
        identities = {
            _text(row.get("id")),
            _text(row.get("actor_id")),
            _text(row.get("source_actor_ref")),
            _text(row.get("account_ref")),
            _text(row.get("role_key")),
        }
        if enterprise_actor_ref in identities:
            candidates.append(row)
    if len(candidates) == 1:
        return _text(candidates[0].get("id")), ""
    if len(candidates) > 1:
        return "", "ASYNC_JOB_ACTOR_IDENTITY_AMBIGUOUS"

    enterprise_actor = _dict(enterprise_actors.get(enterprise_actor_ref))
    if not enterprise_actor:
        return "", "ASYNC_JOB_ACTOR_NOT_BOUND"
    actor_refs = _evidence(enterprise_actor) or source_refs
    if not actor_refs:
        return "", "ASYNC_JOB_ACTOR_SOURCE_EVIDENCE_MISSING"
    actor_id = _stable_id("actor", "async_job", enterprise_actor_ref)
    actor = _bir._fact_node(
        node_id=actor_id,
        typed_fields={
            "actor_id": enterprise_actor_ref,
            "source_actor_ref": enterprise_actor_ref,
            "role": _text(enterprise_actor.get("role")),
            "role_key": _text(enterprise_actor.get("role_key")),
            "account_ref": _text(enterprise_actor.get("account_ref")),
            "credential_secret_ref": _text(
                enterprise_actor.get("credential_secret_ref")
                or enterprise_actor.get("secret_ref")
            ),
            "runtime_bound": bool(enterprise_actor.get("runtime_bound")),
        },
        source_refs=actor_refs,
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    model.setdefault("actors", []).append(actor)
    return actor_id, ""


def _resolve_or_add_operation(
    model: dict[str, Any],
    enterprise_operation: dict[str, Any],
    job_asset: dict[str, Any],
    source_refs: list[dict[str, Any]],
    actor_ref: str,
) -> tuple[str, str]:
    enterprise_operation_id = _text(enterprise_operation.get("operation_id"))
    job_asset_id = _text(job_asset.get("job_asset_id"))
    candidates = [
        row
        for row in _list(model.get("operations"))
        if isinstance(row, dict)
        and (
            enterprise_operation_id
            in {
                _text(row.get("id")),
                _text(row.get("operation_id")),
                *[_text(value) for value in _list(row.get("source_operation_refs"))],
            }
            or job_asset_id == _text(_dict(row.get("async_contract")).get("job_asset_ref"))
        )
    ]
    if len(candidates) == 1:
        candidate = candidates[0]
        if _text(candidate.get("operation_kind")) not in {"", ASYNC_OPERATION_KIND}:
            return "", "ASYNC_JOB_OPERATION_KIND_CONFLICT"
        candidate["operation_kind"] = ASYNC_OPERATION_KIND
        candidate["async_contract"] = copy.deepcopy(
            _dict(enterprise_operation.get("async_contract"))
        )
        actor_refs = [
            _text(value) for value in _list(candidate.get("actor_refs")) if _text(value)
        ]
        if actor_ref not in actor_refs:
            actor_refs.append(actor_ref)
        candidate["actor_refs"] = actor_refs
        return _text(candidate.get("id")), ""
    if len(candidates) > 1:
        return "", "ASYNC_JOB_OPERATION_IDENTITY_AMBIGUOUS"
    if not source_refs:
        return "", "ASYNC_JOB_SOURCE_EVIDENCE_MISSING"

    contract = copy.deepcopy(_dict(enterprise_operation.get("async_contract")))
    operation_ref = _stable_id("operation", "async_job", job_asset_id, enterprise_operation_id)
    operation = _bir._fact_node(
        node_id=operation_ref,
        typed_fields={
            "operation_id": enterprise_operation_id,
            "source_operation_refs": [enterprise_operation_id, job_asset_id],
            "method": "JOB",
            "path": "",
            "service": _text(_dict(job_asset.get("identity")).get("service")),
            "adapter": "job_platform",
            "read_write": "read",
            "operation_kind": ASYNC_OPERATION_KIND,
            "entity_refs": [
                _text(value)
                for value in _list(_dict(job_asset.get("behavior")).get("object_refs"))
                if _text(value)
            ],
            "actor_refs": [actor_ref],
            "async_contract": contract,
            "request_schema": {},
            "response_schema": {},
        },
        source_refs=source_refs,
        confidence=1.0,
        derivation="explicit",
        status="accepted",
    )
    model.setdefault("operations", []).append(operation)
    return operation_ref, ""


def _gap(
    *,
    job_asset_id: str,
    behavior_id: str,
    reason_code: str,
    source_refs: list[dict[str, Any]],
) -> dict[str, Any]:
    return _bir._fact_node(
        node_id=_stable_id("gap", "async_job", job_asset_id, behavior_id, reason_code),
        typed_fields={
            "gap_type": "async_job_contract_not_executable",
            "reason_code": reason_code,
            "job_asset_id": job_asset_id,
            "source_behavior_id": behavior_id,
            "description": reason_code,
        },
        source_refs=source_refs,
        confidence=1.0,
        derivation="explicit",
        status="unsupported",
    )


def bind_source_job_contracts(
    behavior_ir: dict[str, Any],
    asset: dict[str, Any] | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Bind every confirmed Job behavior or emit an exact coverage gap."""
    model = copy.deepcopy(_dict(behavior_ir))
    knowledge = _dict(asset)
    behaviors = _confirmed_job_behaviors(knowledge)
    operations = _enterprise_operations(knowledge)
    job_assets = _job_assets(knowledge)
    actors = _enterprise_actors(knowledge)
    existing_invariants = {
        _text(row.get("source_behavior_id"))
        for row in _list(model.get("invariants"))
        if isinstance(row, dict) and _text(row.get("source_behavior_id"))
    }
    relation_ids = {
        _text(row.get("id"))
        for row in _list(model.get("relations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    bound = 0
    gaps = 0
    reason_counts: dict[str, int] = {}

    for behavior in behaviors:
        behavior_id = _text(behavior.get("behavior_id"))
        if behavior_id in existing_invariants:
            continue
        lineage = _dict(behavior.get("job_lineage"))
        job_asset_id = _text(lineage.get("job_asset_id"))
        enterprise_operation_id = _text(behavior.get("operation_ref"))
        source_refs = _evidence(behavior)
        job_asset = _dict(job_assets.get(job_asset_id))
        enterprise_operation = _dict(operations.get(enterprise_operation_id))
        actor_candidates = [
            _text(value) for value in _list(behavior.get("actor_refs")) if _text(value)
        ]
        reason = ""
        if not source_refs:
            reason = "ASYNC_JOB_SOURCE_EVIDENCE_MISSING"
        elif not job_asset:
            reason = "ASYNC_JOB_ASSET_IDENTITY_NOT_FOUND"
        elif not enterprise_operation:
            reason = "ASYNC_JOB_OPERATION_NOT_BOUND"
        elif len(actor_candidates) != 1:
            reason = "ASYNC_JOB_ACTOR_NOT_BOUND"

        actor_ref = ""
        operation_ref = ""
        if not reason:
            actor_ref, reason = _resolve_or_add_actor(
                model,
                actor_candidates[0],
                actors,
                source_refs,
            )
        if not reason:
            operation_ref, reason = _resolve_or_add_operation(
                model,
                enterprise_operation,
                job_asset,
                source_refs,
                actor_ref,
            )
        if reason:
            model.setdefault("coverage_gaps", []).append(
                _gap(
                    job_asset_id=job_asset_id,
                    behavior_id=behavior_id,
                    reason_code=reason,
                    source_refs=source_refs,
                )
            )
            reason_counts[reason] = reason_counts.get(reason, 0) + 1
            gaps += 1
            continue

        invariant_id = _stable_id("invariant", "async_job", behavior_id, operation_ref)
        invariant = _bir._fact_node(
            node_id=invariant_id,
            typed_fields={
                "description": _text(job_asset.get("display_name")) or job_asset_id,
                "expression": {
                    "kind": INVARIANT_KIND,
                    "operator": "must_reach_source_declared_success_terminal",
                    "operands": [],
                    "raw": _text(job_asset.get("display_name")) or job_asset_id,
                },
                "operation_refs": [operation_ref],
                "source_rule_refs": [behavior_id, job_asset_id],
                "source_job_asset_id": job_asset_id,
                "source_behavior_id": behavior_id,
                "job_actor_ref": actor_ref,
                "async_contract": copy.deepcopy(
                    _dict(enterprise_operation.get("async_contract"))
                ),
                "protocol_template": TEMPLATE_ASYNC_JOB_EXECUTION,
                "binding_status": "source_identity_bound",
                "runtime_integrity_only": True,
                "formal_business_finding_eligible": False,
            },
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
        relation = _bir._relation_node(
            relation_type="observes",
            from_ref=operation_ref,
            to_ref=invariant_id,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
            preconditions=[
                {
                    "kind": "source_declared_async_job_runtime",
                    "template": TEMPLATE_ASYNC_JOB_EXECUTION,
                }
            ],
            effects=[
                {
                    "kind": "job_runtime_integrity",
                    "formal_business_finding_eligible": False,
                }
            ],
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
            source_relationship_ref=behavior_id,
        )
        model.setdefault("invariants", []).append(invariant)
        if _text(relation.get("id")) not in relation_ids:
            model.setdefault("relations", []).append(relation)
            relation_ids.add(_text(relation.get("id")))
        existing_invariants.add(behavior_id)
        bound += 1

    receipt = {
        "schema_version": BINDING_RECEIPT_SCHEMA,
        "status": "BOUND" if bound else "BLOCKED" if behaviors else "NOT_REQUESTED",
        "confirmed_job_behavior_count": len(behaviors),
        "bound_invariant_count": bound,
        "coverage_gap_count": gaps,
        "reason_counts": dict(sorted(reason_counts.items())),
        "binding_basis": "exact_governed_job_asset_operation_actor_identity",
        "runtime_integrity_only": True,
        "formal_business_finding_eligible": False,
    }
    model["source_job_contract_binding_receipt"] = receipt
    errors = _bir.validate_behavior_ir(model, require_explicit_relations=True)
    if errors:
        raise _bir.BehaviorIRError(
            "source_job_contract_binding_invalid:" + ",".join(errors[:12])
        )
    model["model_id"] = _bir._content_addressed_id(model)
    return model, receipt


__all__ = [
    "BINDING_RECEIPT_SCHEMA",
    "INVARIANT_KIND",
    "bind_source_job_contracts",
]
