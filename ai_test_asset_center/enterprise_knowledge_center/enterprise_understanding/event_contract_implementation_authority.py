"""Validate formal event contracts before they can satisfy implementation observers."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from ...scan_event_contract_overlay import _normalized_contract
from ...source_event_contract_binding import _contracts as _source_event_contracts
from .schema import as_dict, as_list, stable_id, text, unique_text

_REJECTED_STATUSES = frozenset(
    {"conflicting", "unsupported", "unknown", "rejected"}
)
_GAP_TYPE = "formal_event_contract_invalid_for_implementation_binding"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _candidate_key(row: dict[str, Any]) -> str:
    return text(row.get("contract_id") or row.get("id")) or stable_id(
        "event_contract_candidate", row
    )


def _candidate_contracts(asset: dict[str, Any]) -> list[dict[str, Any]]:
    # Retained candidates preserve invalid source material for correction. Current
    # formal contracts are applied afterwards and therefore replace the same
    # contract identity when an operator fixes it between pipeline runs.
    merged: dict[str, dict[str, Any]] = {}
    for row in [
        *_dicts(asset.get("event_formal_contract_candidates")),
        *_source_event_contracts(asset),
    ]:
        merged[_candidate_key(row)] = dict(row)
    return list(merged.values())


def _operation_matches(
    contract: dict[str, Any],
    *,
    interface_id: str,
    interfaces: dict[str, dict[str, Any]],
) -> bool:
    interface = interfaces.get(interface_id) or {}
    operation_ref = text(contract.get("operation_ref") or contract.get("operation_id"))
    if operation_ref:
        return operation_ref in {
            interface_id,
            text(interface.get("operation_id")),
        }
    method = text(contract.get("method") or contract.get("http_method")).upper()
    path = text(
        contract.get("operation_path")
        or contract.get("api_path")
        or contract.get("endpoint")
    )
    return bool(
        method
        and path
        and method == text(interface.get("method")).upper()
        and path == text(interface.get("path"))
    )


def _validation_unknown(
    *,
    behavior_ref: str,
    binding_ref: str,
    interface_id: str,
    contract: dict[str, Any],
    gaps: list[dict[str, Any]],
) -> dict[str, Any]:
    contract_ref = text(contract.get("contract_id") or contract.get("id"))
    reasons = unique_text(row.get("reason_code") for row in gaps)
    return {
        "unknown_id": stable_id(
            "implementation_binding_unknown",
            "IMPLEMENTATION_EVENT_CONTRACT_INVALID",
            behavior_ref,
            binding_ref,
            interface_id,
            contract_ref,
            reasons,
        ),
        "kind": "IMPLEMENTATION_EVENT_CONTRACT_INVALID",
        "reason_code": "IMPLEMENTATION_EVENT_CONTRACT_INVALID",
        "behavior_ref": behavior_ref,
        "implementation_binding_ref": binding_ref,
        "interface_refs": [interface_id],
        "event_contract_ref": contract_ref,
        "event_contract_validation_reasons": reasons,
        "blocks_scenario_planning": True,
        "automatic_resolution_allowed": False,
        "execution_allowed": False,
    }


def prepare_formal_event_contract_authority(
    asset: dict[str, Any], model: dict[str, Any]
) -> list[dict[str, Any]]:
    """Retain candidates, expose only validated contracts, and scope invalid rows."""
    candidates = [
        row
        for row in _candidate_contracts(asset)
        if text(row.get("status") or "accepted").lower()
        not in _REJECTED_STATUSES
    ]
    valid: dict[str, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    for index, raw in enumerate(candidates, start=1):
        contract, gaps = _normalized_contract(raw, index=index)
        if contract is None:
            failures.append({"contract": dict(raw), "gaps": gaps})
            continue
        contract_ref = text(contract.get("contract_id"))
        if contract_ref in valid:
            failures.append(
                {
                    "contract": dict(raw),
                    "gaps": [
                        {
                            "gap_type": "scan_event_contract_not_source_bound",
                            "reason_code": "FORMAL_EVENT_CONTRACT_ID_DUPLICATE",
                            "contract_id": contract_ref,
                            "status": "unsupported",
                        }
                    ],
                }
            )
            continue
        valid[contract_ref] = contract

    asset["event_formal_contract_candidates"] = [dict(row) for row in candidates]
    asset["event_formal_contracts"] = list(valid.values())
    asset["event_formal_contract_validation_failures"] = [
        {
            "contract_id": text(
                row["contract"].get("contract_id") or row["contract"].get("id")
            ),
            "reason_codes": unique_text(
                gap.get("reason_code") for gap in row["gaps"]
            ),
            "contract": dict(row["contract"]),
        }
        for row in failures
    ]
    gaps = [
        dict(row)
        for row in as_list(asset.get("coverage_gaps"))
        if isinstance(row, dict) and text(row.get("gap_type")) != _GAP_TYPE
    ]
    gaps.extend(
        {
            "kind": "FORMAL_EVENT_CONTRACT_INVALID",
            "gap_type": _GAP_TYPE,
            "source_id": text(
                as_dict(as_list(row["contract"].get("source_refs"))[0]).get(
                    "source_id"
                )
            )
            if as_list(row["contract"].get("source_refs"))
            else text(row["contract"].get("source_id")),
            "event_contract_ref": text(
                row["contract"].get("contract_id") or row["contract"].get("id")
            ),
            "reason_codes": unique_text(
                gap.get("reason_code") for gap in row["gaps"]
            ),
            "blocks_automatic_event_observer_binding": True,
            "execution_allowed": False,
            "operator_action": (
                "correct the source-declared event contract; do not infer missing event "
                "fields, observer path, actor, correlation, count or time window"
            ),
        }
        for row in failures
    )
    asset["coverage_gaps"] = gaps

    interfaces = {
        text(row.get("interface_id")): row
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    }
    behaviors = {
        text(row.get("behavior_id")): row
        for row in _dicts(model.get("business_behaviors"))
        if text(row.get("behavior_id"))
    }
    unknowns: list[dict[str, Any]] = []
    for binding in _dicts(model.get("behavior_implementation_bindings")):
        binding_ref = text(binding.get("binding_id"))
        behavior_ref = text(binding.get("behavior_ref"))
        interface_id = text(binding.get("primary_api_interface_ref"))
        behavior = behaviors.get(behavior_ref) or {}
        actor_refs = {
            text(value) for value in as_list(behavior.get("actor_refs")) if text(value)
        }
        if not interface_id or not actor_refs:
            continue
        for failure in failures:
            contract = failure["contract"]
            declared_actor = text(
                contract.get("actor_ref") or contract.get("actor_id")
            )
            if not declared_actor or declared_actor not in actor_refs:
                continue
            if not _operation_matches(
                contract,
                interface_id=interface_id,
                interfaces=interfaces,
            ):
                continue
            unknowns.append(
                _validation_unknown(
                    behavior_ref=behavior_ref,
                    binding_ref=binding_ref,
                    interface_id=interface_id,
                    contract=contract,
                    gaps=failure["gaps"],
                )
            )
    return unknowns


def apply_event_contract_validation_failures(
    bindings: Iterable[dict[str, Any]],
    unknowns: Iterable[dict[str, Any]],
    conflicts: Iterable[dict[str, Any]],
    gate: dict[str, Any],
    validation_unknowns: Iterable[dict[str, Any]],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Fail only exactly scoped bindings and recompute implementation gate truth."""
    validation_rows = [
        dict(row) for row in validation_unknowns if isinstance(row, dict)
    ]

    # Validation is a subtractive Event-specific authority.  With no failed
    # Event Contract there is nothing to subtract, so preserve the upstream
    # implementation-binding gate instead of re-deriving unrelated HTTP/UI/DB
    # readiness from an optional projection input.
    if not validation_rows:
        return (
            [dict(row) for row in bindings if isinstance(row, dict)],
            [dict(row) for row in unknowns if isinstance(row, dict)],
            [dict(row) for row in conflicts if isinstance(row, dict)],
            dict(gate),
        )

    blocked = {
        text(row.get("implementation_binding_ref"))
        for row in validation_rows
        if text(row.get("implementation_binding_ref"))
    }
    projected: list[dict[str, Any]] = []
    for raw in bindings:
        if not isinstance(raw, dict):
            continue
        binding = dict(raw)
        if text(binding.get("binding_id")) in blocked:
            binding["scenario_planning_ready"] = False
            if text(binding.get("status")) == "BOUND":
                binding["status"] = "PARTIAL"
            binding["formal_event_contract_validation_blocked"] = True
        projected.append(binding)

    all_unknowns = list(
        {
            text(row.get("unknown_id")): dict(row)
            for row in [
                *[dict(row) for row in unknowns if isinstance(row, dict)],
                *validation_rows,
            ]
            if text(row.get("unknown_id"))
        }.values()
    )
    conflict_rows = [dict(row) for row in conflicts if isinstance(row, dict)]
    counts: dict[str, int] = defaultdict(int)
    for binding in projected:
        counts[text(binding.get("status")) or "UNKNOWN"] += 1
    ready = sum(
        1 for row in projected if bool(row.get("scenario_planning_ready"))
    )
    if conflict_rows or counts["CONFLICTED"]:
        status = "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT"
    elif projected and ready == len(projected):
        status = "PASS"
    elif projected:
        status = "PARTIAL_IMPLEMENTATION_BINDING"
    else:
        status = "NO_BEHAVIOR_IMPLEMENTATION_BINDING"
    result_gate = dict(gate)
    metrics = dict(as_dict(result_gate.get("metrics")))
    metrics.update(
        {
            "behavior_binding_count": len(projected),
            "scenario_ready_binding_count": ready,
            "bound_binding_count": counts["BOUND"],
            "partial_binding_count": counts["PARTIAL"],
            "unbound_binding_count": counts["UNBOUND"],
            "ambiguous_binding_count": counts["AMBIGUOUS"],
            "conflicted_binding_count": counts["CONFLICTED"],
            "implementation_binding_unknown_count": len(all_unknowns),
            "formal_event_contract_validation_failure_count": len(
                validation_rows
            ),
            "formal_event_contract_validation_blocked_binding_count": len(blocked),
            "scenario_ready_rate": round(ready / len(projected), 4)
            if projected
            else 0.0,
        }
    )
    result_gate.update(
        {
            "status": status,
            "entry_allowed": status == "PASS",
            "scenario_planning_allowed": status == "PASS",
            "execution_allowed": False,
            "metrics": metrics,
            "formal_event_contract_validation_required": True,
            "invalid_event_contract_can_satisfy_effect_observer": False,
        }
    )
    return projected, all_unknowns, conflict_rows, result_gate


__all__ = [
    "prepare_formal_event_contract_authority",
    "apply_event_contract_validation_failures",
]
