"""Project exact operation-correlation scope onto database relation delta rules.

This is an additive strengthening layer. A normal database relation delta remains a
cross-table consistency assertion. Only an explicitly source-backed, approved
request correlation key is promoted to ``database_relation_causal_delta_conservation``.
No field name, timestamp window, response-generated identifier, or concurrent-row
winner is inferred.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any

from .database_relation_delta_experiment_projection import (
    ASSERTION_KIND as BASE_ASSERTION_KIND,
)

ASSERTION_KIND = "database_relation_causal_delta_conservation"
ATTRIBUTION_SCHEMA = "qualibug.operation-causal-attribution.v1"
PROJECTION_SCHEMA = "qualibug.database-relation-delta-causality-projection.v1"
_BLOCK_REASON = "BLOCKED_DATABASE_RELATION_CAUSALITY_BINDING_INVALID"
_REQUEST_BODY_PREFIX = "request.body."


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )


def _fingerprint(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _causal_spec(assertion: dict[str, Any]) -> dict[str, Any]:
    return _dict(
        assertion.get("causal_attribution")
        or _dict(assertion.get("structured_expression")).get("causal_attribution")
    )


def _source_refs(assertion: dict[str, Any], causal: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        deepcopy(row)
        for row in _list(causal.get("source_refs") or assertion.get("source_refs"))
        if isinstance(row, dict) and row
    ]
    return sorted(rows, key=_canonical)


def _treatment_step(experiment: dict[str, Any], operation_ref: str) -> tuple[dict[str, Any], str]:
    matches = [
        dict(row)
        for row in _list(experiment.get("treatment_plan"))
        if isinstance(row, dict)
        and _text(row.get("operation_ref")) == operation_ref
    ]
    if len(matches) != 1:
        return {}, (
            "DATABASE_RELATION_CAUSAL_OPERATION_MISSING"
            if len(matches) < 1
            else "DATABASE_RELATION_CAUSAL_OPERATION_AMBIGUOUS"
        )
    step = matches[0]
    if any(
        step.get(key) not in (None, "", False, [], {})
        for key in ("barrier_id", "barrier_group", "concurrency_group")
    ) or "barrier" in _text(step.get("protocol_step")).lower():
        return {}, "DATABASE_RELATION_CAUSAL_BARRIER_OPERATION_UNSUPPORTED"
    return step, ""


def _exact_allowed_field(
    contract: dict[str, Any],
    *,
    field_id: str,
    field_name: str,
) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(contract.get("allowed_child_fields"))
        if isinstance(row, dict)
        and _text(row.get("database_field_id")) == field_id
        and _text(row.get("database_field_name")) == field_name
    ]


def _block(
    experiment: dict[str, Any],
    *,
    assertion_id: str,
    reason_code: str,
    detail: dict[str, Any] | None = None,
) -> dict[str, Any]:
    row = deepcopy(experiment)
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_relation_causality_detail": {
                "assertion_id": assertion_id,
                "causality_reason_code": reason_code,
                "automatic_field_mapping_allowed": False,
                "automatic_operation_selection_allowed": False,
                "timestamp_window_attribution_allowed": False,
                **deepcopy(detail or {}),
            },
        }
    )
    row["compile_receipt"] = receipt
    row["compile_status"] = "BLOCKED"
    row["database_relation_causality_projection_status"] = "BLOCKED"
    return row


def project_database_relation_delta_causality(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    """Promote only exact request-body-correlated delta assertions."""
    pack = deepcopy(dict(experiment_pack or {}))
    compiled: list[dict[str, Any]] = []
    newly_blocked: list[dict[str, Any]] = []
    promoted_count = 0
    explicit_count = 0

    for raw_experiment in _list(pack.get("experiments")):
        if not isinstance(raw_experiment, dict):
            continue
        experiment = deepcopy(raw_experiment)
        assertions = [
            dict(row)
            for row in _list(experiment.get("assertions"))
            if isinstance(row, dict)
        ]
        drafts = [
            dict(row)
            for row in _list(experiment.get("database_relation_observer_execution_drafts"))
            if isinstance(row, dict)
        ]
        blocked: dict[str, Any] | None = None

        for assertion in assertions:
            if _text(assertion.get("kind")) != BASE_ASSERTION_KIND:
                continue
            causal = _causal_spec(assertion)
            if not causal:
                continue
            explicit_count += 1
            assertion_id = _text(assertion.get("assertion_id"))
            schema = _text(causal.get("schema"))
            operation_ref = _text(causal.get("operation_ref"))
            value_source = _text(causal.get("value_source"))
            field_id = _text(causal.get("child_database_field_id"))
            field_name = _text(causal.get("child_database_field_name"))
            mapping_decision_id = _text(causal.get("mapping_decision_id"))
            sources = _source_refs(assertion, causal)
            if schema != ATTRIBUTION_SCHEMA:
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_SCHEMA_INVALID",
                )
                break
            if not operation_ref:
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_OPERATION_MISSING",
                )
                break
            if not value_source.startswith(_REQUEST_BODY_PREFIX) or not _text(
                value_source[len(_REQUEST_BODY_PREFIX):]
            ):
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code=(
                        "DATABASE_RELATION_CAUSAL_RESPONSE_GENERATED_KEY_UNSUPPORTED"
                        if value_source.startswith("response.")
                        else "DATABASE_RELATION_CAUSAL_VALUE_SOURCE_UNSUPPORTED"
                    ),
                    detail={"value_source": value_source},
                )
                break
            if not (field_id and field_name and mapping_decision_id and sources):
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_APPROVED_BINDING_INCOMPLETE",
                )
                break
            step, step_reason = _treatment_step(experiment, operation_ref)
            if step_reason:
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code=step_reason,
                    detail={"operation_ref": operation_ref},
                )
                break

            before_id = _text(assertion.get("relation_before_draft_id"))
            after_id = _text(assertion.get("relation_after_draft_id"))
            candidates = [
                row
                for row in drafts
                if _text(row.get("draft_id")) in {before_id, after_id}
            ]
            if len(candidates) != 2 or {
                _text(row.get("observation_phase")).upper() for row in candidates
            } != {"BEFORE", "AFTER"}:
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_PHASE_DRAFT_PAIR_MISSING",
                )
                break

            contracts = [
                _dict(row.get("database_relation_observer_contract"))
                for row in candidates
            ]
            relation_refs = {
                _text(contract.get("relation_observer_id")) for contract in contracts
            }
            if len(relation_refs) != 1 or "" in relation_refs:
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_RELATION_SCOPE_MISMATCH",
                )
                break
            if any(
                len(_exact_allowed_field(contract, field_id=field_id, field_name=field_name)) != 1
                for contract in contracts
            ):
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_EXACT_FIELD_BINDING_MISSING",
                    detail={
                        "child_database_field_id": field_id,
                        "child_database_field_name": field_name,
                    },
                )
                break
            if any(
                any(
                    _text(_dict(predicate).get("predicate_kind"))
                    == "OPERATION_ATTRIBUTION"
                    for predicate in _list(contract.get("relation_predicates"))
                )
                for contract in contracts
            ):
                blocked = _block(
                    experiment,
                    assertion_id=assertion_id,
                    reason_code="DATABASE_RELATION_CAUSAL_PREDICATE_ALREADY_PRESENT",
                )
                break

            causal_contract = {
                "schema": ATTRIBUTION_SCHEMA,
                "status": "BOUND",
                "operation_ref": operation_ref,
                "treatment_step_id": _text(step.get("step_id")),
                "value_source": value_source,
                "child_database_field_id": field_id,
                "child_database_field_name": field_name,
                "mapping_decision_id": mapping_decision_id,
                "source_refs": sources,
                "attribution_mode": "EXACT_REQUEST_CORRELATION",
                "response_generated_identifier_allowed": False,
                "timestamp_window_attribution_allowed": False,
                "automatic_field_mapping": False,
                "automatic_operation_selection": False,
            }
            causal_scope_fingerprint = _fingerprint(
                {
                    "schema": PROJECTION_SCHEMA,
                    "base_relation_pair_id": _text(assertion.get("relation_pair_id")),
                    "causal_attribution": causal_contract,
                }
            )
            causal_contract["causal_scope_fingerprint"] = causal_scope_fingerprint

            for draft in candidates:
                contract = deepcopy(_dict(draft.get("database_relation_observer_contract")))
                predicates = [
                    dict(row)
                    for row in _list(contract.get("relation_predicates"))
                    if isinstance(row, dict)
                ]
                predicates.append(
                    {
                        "predicate_kind": "OPERATION_ATTRIBUTION",
                        "child_database_field_id": field_id,
                        "child_database_field_name": field_name,
                        "parent_database_field_name": "",
                        "operator": "=",
                        "value_source": value_source,
                        "mapping_decision_id": mapping_decision_id,
                        "operation_ref": operation_ref,
                    }
                )
                contract["relation_predicates"] = predicates
                contract["causal_attribution_contract"] = deepcopy(causal_contract)
                contract["causal_scope_fingerprint"] = causal_scope_fingerprint
                draft["database_relation_observer_contract"] = contract
                draft["causal_attribution_contract"] = deepcopy(causal_contract)
                draft["causal_scope_fingerprint"] = causal_scope_fingerprint
                draft["identity_value_sources"] = sorted(
                    {
                        *[
                            _text(value)
                            for value in _list(draft.get("identity_value_sources"))
                            if _text(value)
                        ],
                        value_source,
                    }
                )

            assertion["kind"] = ASSERTION_KIND
            assertion["causal_attribution_contract"] = deepcopy(causal_contract)
            assertion["causal_scope_fingerprint"] = causal_scope_fingerprint
            assertion["causal_attribution_required"] = True
            binding = _dict(assertion.get("database_relation_delta_binding"))
            binding.update(
                {
                    "causal_attribution_schema": ATTRIBUTION_SCHEMA,
                    "causal_scope_fingerprint": causal_scope_fingerprint,
                    "causal_mapping_decision_id": mapping_decision_id,
                    "causal_value_source": value_source,
                    "causal_operation_ref": operation_ref,
                    "causal_scope_exact": True,
                    "timestamp_window_attribution_used": False,
                }
            )
            assertion["database_relation_delta_binding"] = binding
            promoted_count += 1

        if blocked is not None:
            newly_blocked.append(blocked)
            continue
        experiment["assertions"] = assertions
        experiment["database_relation_observer_execution_drafts"] = drafts
        causal_assertions = [
            row for row in assertions if _text(row.get("kind")) == ASSERTION_KIND
        ]
        if causal_assertions:
            experiment["database_relation_causality_projection_status"] = "BOUND"
            receipt = _dict(experiment.get("compile_receipt"))
            receipt.update(
                {
                    "database_relation_causality_projection_status": "BOUND",
                    "database_relation_causal_assertion_count": len(causal_assertions),
                    "database_relation_causal_assertion_fingerprint": _fingerprint(
                        causal_assertions
                    ),
                    "timestamp_window_attribution_count": 0,
                    "automatic_causal_field_mapping_count": 0,
                }
            )
            experiment["compile_receipt"] = receipt
        else:
            experiment["database_relation_causality_projection_status"] = "NOT_APPLICABLE"
        compiled.append(experiment)

    existing_blocked = [
        dict(row)
        for row in _list(pack.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    all_blocked = [*existing_blocked, *newly_blocked]
    reason_counts = dict(_dict(pack.get("block_reason_counts")))
    if newly_blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(
            newly_blocked
        )
    pack.update(
        {
            "experiments": compiled,
            "blocked_experiments": all_blocked,
            "compiled_count": len(compiled),
            "blocked_count": len(all_blocked),
            "block_reason_counts": reason_counts,
            "database_relation_delta_causality_projection": {
                "schema": PROJECTION_SCHEMA,
                "status": "BLOCKED" if newly_blocked else "PASS",
                "explicit_causal_assertion_count": explicit_count,
                "promoted_causal_assertion_count": promoted_count,
                "newly_blocked_experiment_count": len(newly_blocked),
                "timestamp_window_attribution_count": 0,
                "response_generated_before_baseline_count": 0,
                "automatic_field_mapping_count": 0,
                "automatic_operation_selection_count": 0,
                "second_oracle_created": False,
                "contract_oracle_remains_authority": True,
            },
        }
    )
    return pack


__all__ = [
    "ASSERTION_KIND",
    "ATTRIBUTION_SCHEMA",
    "PROJECTION_SCHEMA",
    "project_database_relation_delta_causality",
]
