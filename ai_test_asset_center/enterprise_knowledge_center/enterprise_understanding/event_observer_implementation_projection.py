"""Project source-declared event contracts into governed effect observers.

The business action remains the already governed API ActionSurface. Event contracts
are observation authority only. This projection reuses the existing formal-event
operation/actor resolver and observer adapter; it never infers a topic, broker or path.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

from ...formal_event_surface import (
    ADAPTER,
    ASSERTION_KIND,
    OBSERVER_ID,
    PROTOCOL_TEMPLATE,
    RISK_FAMILY,
    SURFACE,
)
from ...source_event_contract_binding import (
    _contracts as _source_event_contracts,
    _resolve_actor as _resolve_event_actor,
    _resolve_operation as _resolve_event_operation,
    _source_refs as _event_source_refs,
)
from .schema import as_dict, as_list, stable_id, text, unique_text


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _operation_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            **row,
            "id": text(row.get("interface_id")),
            "source_operation_refs": unique_text(
                [
                    row.get("interface_id"),
                    row.get("operation_id"),
                    *as_list(row.get("source_operation_refs")),
                ]
            ),
        }
        for row in _dicts(asset.get("interfaces"))
        if text(row.get("interface_id"))
    ]


def _credential_rows(asset: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    containers = [
        asset,
        as_dict(asset.get("environment_contract")),
        as_dict(asset.get("runtime_environment")),
        as_dict(asset.get("project_configuration")),
    ]
    for container in containers:
        for key in ("credential_refs", "test_account_refs"):
            rows.extend(_dicts(container.get(key)))
    return rows


def _actor_rows(asset: dict[str, Any], behaviors: list[dict[str, Any]]) -> list[dict[str, Any]]:
    source_rows = [
        *_dicts(asset.get("actors")),
        *_dicts(asset.get("roles")),
        *_dicts(asset.get("business_actors")),
        *_dicts(as_dict(asset.get("enterprise_understanding_model")).get("actors")),
    ]
    credentials = _credential_rows(asset)
    known_refs = unique_text(
        actor
        for behavior in behaviors
        for actor in as_list(behavior.get("actor_refs"))
    )
    result: dict[str, dict[str, Any]] = {}
    for raw in source_rows:
        actor_id = text(
            raw.get("id")
            or raw.get("actor_id")
            or raw.get("role_id")
            or raw.get("actor_ref")
        )
        if not actor_id:
            continue
        result[actor_id] = {
            **raw,
            "id": actor_id,
            "role": raw.get("role") or raw.get("name") or raw.get("actor_name"),
            "role_key": raw.get("role_key") or raw.get("canonical_name"),
            "credential_secret_ref": text(
                raw.get("credential_secret_ref")
                or raw.get("secret_ref")
                or raw.get("credential_ref")
            ),
            "runtime_bound": bool(
                raw.get("runtime_bound")
                or raw.get("account_ref")
                or raw.get("credential_ref")
                or raw.get("secret_ref")
            ),
        }
    for actor_ref in known_refs:
        actor = dict(result.get(actor_ref) or {"id": actor_ref, "role": actor_ref})
        matches = [
            row
            for row in credentials
            if actor_ref
            in unique_text(
                [row.get("actor_ref"), row.get("role"), *as_list(row.get("roles"))]
            )
        ]
        if len(matches) == 1:
            credential = matches[0]
            credential_ref = text(
                credential.get("credential_secret_ref")
                or credential.get("secret_ref")
                or credential.get("credential_ref")
            )
            actor["credential_secret_ref"] = credential_ref
            actor["account_ref"] = credential.get("account_ref") or credential_ref
            actor["runtime_bound"] = bool(credential_ref or actor.get("account_ref"))
        result[actor_ref] = actor
    return list(result.values())


def _event_slot(
    *,
    binding: dict[str, Any],
    behavior: dict[str, Any],
    contract: dict[str, Any],
    interface_id: str,
    actor_ref: str,
) -> dict[str, Any]:
    contract_id = text(contract.get("contract_id"))
    slot_ref = stable_id(
        "event_effect_observer_slot",
        binding.get("binding_id"),
        behavior.get("behavior_id"),
        contract_id,
    )
    observer_binding_id = stable_id(
        "observer_binding",
        binding.get("binding_id"),
        slot_ref,
        "SOURCE_EVENT_DELIVERY_OBSERVER",
        contract_id,
    )
    observer = {
        "observer_binding_id": observer_binding_id,
        "binding_kind": "SOURCE_EVENT_DELIVERY_OBSERVER",
        "observer_id": OBSERVER_ID,
        "surface": SURFACE,
        "adapter": ADAPTER,
        "assertion_kind": ASSERTION_KIND,
        "risk_family": RISK_FAMILY,
        "protocol_template": PROTOCOL_TEMPLATE,
        "event_contract_ref": contract_id,
        "interface_id": interface_id,
        "actor_ref": actor_ref,
        "observer_path": contract.get("observer_path"),
        "expected_event_type": contract.get("expected_event_type"),
        "expected_min_count": contract.get("expected_min_count"),
        "expected_max_count": contract.get("expected_max_count"),
        "observation_window_ms": contract.get("observation_window_ms"),
        "correlation_source": as_dict(contract.get("correlation_source")),
        "event_contract": dict(contract),
        "status": "BOUND",
        "authoritative": True,
        "runtime_supported": True,
        "derivation": "EXACT_FORMAL_EVENT_CONTRACT_IDENTITY",
        "automatic_topic_inference_allowed": False,
        "automatic_broker_selection_allowed": False,
        "evidence": _event_source_refs(contract),
    }
    return {
        "slot_ref": slot_ref,
        "purpose": "EFFECT_OBSERVER",
        "source_field_candidate": text(contract.get("expected_event_type")),
        "status": "BOUND",
        "runtime_observer_available": True,
        "request_contract_field_available": False,
        "object_table_identity_confirmed": False,
        "api_request_field_is_runtime_observer": False,
        "formal_event_contract_ref": contract_id,
        "bindings": [observer],
    }


def _unknown_identity(row: dict[str, Any]) -> str:
    return stable_id(
        "implementation_binding_unknown",
        row.get("kind"),
        row.get("behavior_ref"),
        row.get("slot_ref"),
        row.get("event_contract_ref"),
        row.get("reason_code"),
    )


def project_formal_event_observers(
    asset: dict[str, Any],
    behaviors: Iterable[dict[str, Any]],
    bindings: Iterable[dict[str, Any]],
    unknowns: Iterable[dict[str, Any]],
    conflicts: Iterable[dict[str, Any]],
    gate: dict[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, Any],
]:
    """Attach exact formal event observers and recompute admission truth."""
    behavior_rows = [dict(row) for row in behaviors if isinstance(row, dict)]
    behavior_by_id = {
        text(row.get("behavior_id")): row
        for row in behavior_rows
        if text(row.get("behavior_id"))
    }
    operation_rows = _operation_rows(asset)
    actor_rows = _actor_rows(asset, behavior_rows)
    contracts = [
        row
        for row in _source_event_contracts(asset)
        if text(row.get("status") or "accepted").lower()
        not in {"conflicting", "unsupported", "unknown", "rejected"}
    ]
    conflict_rows = [dict(row) for row in conflicts if isinstance(row, dict)]
    unknown_rows = [
        dict(row)
        for row in unknowns
        if isinstance(row, dict)
    ]

    # Event observation is an additive implementation surface.  When the source
    # declares no formal Event Contract, this projector has no authority to
    # recompute or downgrade the already-governed HTTP/UI/DB binding gate.
    # Preserve the upstream binding truth exactly and only expose zero-valued
    # Event metrics so downstream diagnostics remain explicit and idempotent.
    if not contracts:
        preserved_gate = dict(gate)
        preserved_metrics = dict(as_dict(preserved_gate.get("metrics")))
        preserved_metrics.update(
            {
                "formal_event_contract_count": 0,
                "formal_event_observer_binding_count": 0,
                "formal_event_contract_bound_count": 0,
            }
        )
        preserved_gate["metrics"] = preserved_metrics
        return (
            [dict(row) for row in bindings if isinstance(row, dict)],
            unknown_rows,
            conflict_rows,
            preserved_gate,
        )

    projected: list[dict[str, Any]] = []
    bound_contract_ids: set[str] = set()

    for raw_binding in bindings:
        if not isinstance(raw_binding, dict):
            continue
        binding = dict(raw_binding)
        behavior_id = text(binding.get("behavior_ref"))
        behavior = behavior_by_id.get(behavior_id) or {}
        interface_id = text(binding.get("primary_api_interface_ref"))
        behavior_actors = {
            text(value) for value in as_list(behavior.get("actor_refs")) if text(value)
        }
        event_slots: list[dict[str, Any]] = []
        event_unknowns: list[dict[str, Any]] = []
        if interface_id:
            for contract in contracts:
                operation, operation_reason = _resolve_event_operation(
                    contract, operation_rows
                )
                if operation_reason or text(as_dict(operation).get("id")) != interface_id:
                    continue

                # An explicit actor ref scopes the contract before runtime resolution.
                # A broken contract for actor A must not block actor B's behavior on
                # the same operation.
                declared_actor = text(
                    contract.get("actor_ref") or contract.get("actor_id")
                )
                if behavior_actors and declared_actor and declared_actor not in behavior_actors:
                    continue

                actor, actor_reason = _resolve_event_actor(contract, actor_rows)
                contract_actor = text(as_dict(actor).get("id"))
                if actor_reason:
                    event_unknowns.append(
                        {
                            "kind": "IMPLEMENTATION_EVENT_OBSERVER_ACTOR_UNRESOLVED",
                            "reason_code": actor_reason,
                            "behavior_ref": behavior_id,
                            "interface_refs": [interface_id],
                            "event_contract_ref": contract.get("contract_id"),
                            "blocks_scenario_planning": True,
                            "automatic_resolution_allowed": False,
                        }
                    )
                    continue
                if behavior_actors and contract_actor not in behavior_actors:
                    continue
                if not behavior_actors:
                    event_unknowns.append(
                        {
                            "kind": "IMPLEMENTATION_EVENT_OBSERVER_BEHAVIOR_ACTOR_UNRESOLVED",
                            "reason_code": "IMPLEMENTATION_EVENT_OBSERVER_BEHAVIOR_ACTOR_UNRESOLVED",
                            "behavior_ref": behavior_id,
                            "interface_refs": [interface_id],
                            "event_contract_ref": contract.get("contract_id"),
                            "blocks_scenario_planning": True,
                            "automatic_resolution_allowed": False,
                        }
                    )
                    continue
                event_slots.append(
                    _event_slot(
                        binding=binding,
                        behavior=behavior,
                        contract=contract,
                        interface_id=interface_id,
                        actor_ref=contract_actor,
                    )
                )
                bound_contract_ids.add(text(contract.get("contract_id")))

        existing_effects = _dicts(binding.get("effect_observer_bindings"))
        binding["effect_observer_bindings"] = list(
            {
                text(row.get("slot_ref")): row
                for row in [*existing_effects, *event_slots]
                if text(row.get("slot_ref"))
            }.values()
        )
        binding["formal_event_observer_bindings"] = [
            candidate
            for slot in event_slots
            for candidate in _dicts(slot.get("bindings"))
        ]
        unknown_rows.extend(event_unknowns)

        condition_ready = all(
            text(row.get("status")) == "BOUND"
            for row in _dicts(binding.get("condition_observer_bindings"))
        )
        effect_ready = any(
            text(row.get("status")) == "BOUND"
            for row in _dicts(binding.get("effect_observer_bindings"))
        ) or any(
            bool(row.get("authoritative"))
            and text(row.get("status")) in {"BOUND", "BOUND_CHANNEL_ONLY"}
            for row in _dicts(binding.get("response_observer_bindings"))
        )
        behavior_ready = text(behavior.get("status")) == "CONFIRMED"
        if len(_dicts(behavior.get("preconditions"))) > 1:
            behavior_ready = behavior_ready and text(
                behavior.get("condition_combinator")
            ) not in {"", "UNRESOLVED"}
        action_ready = int(binding.get("authoritative_api_interface_count") or 0) == 1
        has_conflict = any(
            text(row.get("behavior_ref")) == behavior_id
            and text(row.get("status")) == "UNRESOLVED"
            for row in conflict_rows
        )
        ready = bool(
            action_ready
            and condition_ready
            and effect_ready
            and behavior_ready
            and not has_conflict
            and not event_unknowns
        )
        binding["scenario_planning_ready"] = ready
        if ready:
            binding["status"] = "BOUND"
        projected.append(binding)

    ready_behaviors = {
        text(row.get("behavior_ref"))
        for row in projected
        if bool(row.get("scenario_planning_ready"))
    }
    unknown_rows = [
        row
        for row in unknown_rows
        if not (
            text(row.get("kind")) == "IMPLEMENTATION_EFFECT_OBSERVER_UNRESOLVED"
            and text(row.get("behavior_ref")) in ready_behaviors
        )
    ]
    deduped: dict[str, dict[str, Any]] = {}
    for raw in unknown_rows:
        row = dict(raw)
        unknown_id = text(row.get("unknown_id")) or _unknown_identity(row)
        row["unknown_id"] = unknown_id
        deduped[unknown_id] = row
    deduped_unknowns = list(deduped.values())

    counts: dict[str, int] = defaultdict(int)
    for binding in projected:
        counts[text(binding.get("status")) or "UNKNOWN"] += 1
    ready_count = len(ready_behaviors)
    if conflict_rows or counts["CONFLICTED"]:
        status = "BLOCKED_IMPLEMENTATION_BINDING_CONFLICT"
    elif projected and ready_count == len(projected):
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
            "scenario_ready_binding_count": ready_count,
            "bound_binding_count": counts["BOUND"],
            "partial_binding_count": counts["PARTIAL"],
            "unbound_binding_count": counts["UNBOUND"],
            "ambiguous_binding_count": counts["AMBIGUOUS"],
            "conflicted_binding_count": counts["CONFLICTED"],
            "implementation_binding_unknown_count": len(deduped_unknowns),
            "formal_event_contract_count": len(contracts),
            "formal_event_observer_binding_count": sum(
                len(_dicts(row.get("formal_event_observer_bindings")))
                for row in projected
            ),
            "formal_event_contract_bound_count": len(bound_contract_ids),
            "scenario_ready_rate": round(ready_count / len(projected), 4)
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
            "formal_event_observer_authority_enabled": True,
            "event_contract_cannot_replace_action_surface": True,
            "event_topic_or_broker_inference_allowed": False,
        }
    )
    return projected, deduped_unknowns, conflict_rows, result_gate


__all__ = ["project_formal_event_observers"]
