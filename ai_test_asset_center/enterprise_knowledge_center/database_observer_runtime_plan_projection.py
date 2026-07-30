"""Project approved database Observer contracts into non-executable Runtime Plans.

Generic DATABASE_FIELD_SNAPSHOT rows are replaced only when their exact table/field belongs to an
operator-approved Observer binding. Derived templates, unknowns and relationships are rebuilt from
the current contract set, so a revoked or drift-invalidated Observer cannot leave an accepted
Runtime Plan edge behind.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .enterprise_understanding.schema import (
    as_dict,
    as_list,
    stable_id,
    text,
    unique_text,
)

DATABASE_OBSERVER_RUNTIME_PLAN_PROJECTION_SCHEMA = (
    "qualibug.database-observer-runtime-plan-projection.v1"
)
_APPROVED_DERIVATION = "operator_approved_database_observer_contract"
_HANDLER_ID = "approved_database_readback"
_MISSING_CONTRACT_REASON = (
    "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING"
)


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _dedupe(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        identity = text(raw.get(key))
        if identity:
            output[identity] = dict(raw)
    return list(output.values())


def _approved_bindings(binding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for collection in (
        "condition_observer_bindings",
        "effect_observer_bindings",
    ):
        for slot in _dicts(binding.get(collection)):
            for raw in _dicts(slot.get("bindings")):
                if not (
                    text(raw.get("binding_kind")) == "DATABASE_FIELD"
                    and text(raw.get("derivation")) == _APPROVED_DERIVATION
                    and bool(raw.get("authoritative"))
                    and text(raw.get("observer_id"))
                ):
                    continue
                row = dict(raw)
                row["purpose"] = text(slot.get("purpose"))
                row["slot_ref"] = text(slot.get("slot_ref"))
                rows.append(row)
    return rows


def _phase(rows: Iterable[dict[str, Any]]) -> str:
    purposes = {text(row.get("purpose")) for row in rows}
    return (
        "BEFORE_AND_AFTER"
        if purposes.intersection({"EFFECT_OBSERVER", "STATE_EFFECT_OBSERVER"})
        else "BEFORE"
    )


def _sanitized_contract(contract: dict[str, Any]) -> dict[str, Any]:
    row = deepcopy(contract)
    row.update(
        {
            "runtime_connection_bound": False,
            "runtime_values_materialized": False,
            "database_connection_opened": False,
            "query_executed": False,
            "raw_sql_retained": False,
            "secret_values_retained": False,
            "oracle_verdict_emitted": False,
        }
    )
    return row


def _template(
    *,
    plan: dict[str, Any],
    observer_id: str,
    bindings: list[dict[str, Any]],
    contract: dict[str, Any],
) -> dict[str, Any]:
    phase = _phase(bindings)
    return {
        "template_id": stable_id(
            "runtime_oracle_template",
            plan.get("plan_id"),
            "APPROVED_DATABASE_OBSERVER",
            observer_id,
            phase,
        ),
        "template_kind": "APPROVED_DATABASE_OBSERVER_SNAPSHOT",
        "observer_handler_id": _HANDLER_ID,
        "observer_contract_ref": observer_id,
        "adapter": "db_sql",
        "surface": "database_read_only",
        "phase": phase,
        "interface_id": contract.get("interface_id"),
        "database_table_ref": contract.get("database_table_id"),
        "database_table_name": contract.get("database_table_name"),
        "database_schema_name": contract.get("database_schema_name"),
        "projection": deepcopy(
            as_list(as_dict(contract.get("query_plan")).get("projection"))
        ),
        "selected_identity_key": deepcopy(
            as_list(contract.get("selected_identity_key"))
        ),
        "identity_predicates": deepcopy(
            as_list(contract.get("identity_predicates"))
        ),
        "database_observer_contract": _sanitized_contract(contract),
        "source_slot_refs": unique_text(row.get("slot_ref") for row in bindings),
        "field_binding_refs": unique_text(
            row.get("field_binding_id") for row in bindings
        ),
        "mapping_decision_refs": unique_text(
            row.get("mapping_decision_id") for row in bindings
        ),
        "database_connection_ref": "",
        "runtime_connection_binding_required": True,
        "runtime_identity_values_required": True,
        "query_template_compiled": True,
        "parameterized_query_required": True,
        "raw_sql_compiled": False,
        "database_connection_opened": False,
        "query_executed": False,
        "concrete_assertion_compiled": False,
        "oracle_verdict_emitted": False,
        "write_target_allowed": False,
        "mutation_allowed": False,
    }


def _generic_snapshot_replaced(
    template: dict[str, Any], approved: list[dict[str, Any]]
) -> bool:
    if text(template.get("template_kind")) != "DATABASE_FIELD_SNAPSHOT":
        return False
    field_ref = text(template.get("field_ref"))
    if field_ref and field_ref in {
        text(row.get("field_id")) for row in approved if text(row.get("field_id"))
    }:
        return True
    table_ref = text(template.get("table_ref") or template.get("table"))
    field = text(template.get("field"))
    return any(
        table_ref
        and field
        and table_ref
        in {text(row.get("table_id")), text(row.get("table"))}
        and field == text(row.get("field"))
        for row in approved
    )


def _refresh_snapshot_template(plan: dict[str, Any]) -> None:
    templates = _dicts(as_dict(plan.get("oracle_query_templates")).get("templates"))
    snapshot = dict(as_dict(plan.get("snapshot_template")))
    snapshot.update(
        {
            "before_oracle_template_refs": unique_text(
                row.get("template_id")
                for row in templates
                if text(row.get("phase")) in {"BEFORE", "BEFORE_AND_AFTER"}
            ),
            "after_oracle_template_refs": unique_text(
                row.get("template_id")
                for row in templates
                if text(row.get("phase")) in {"AFTER", "BEFORE_AND_AFTER"}
            ),
            "approved_database_observer_templates_projected": True,
            "snapshot_queries_executed": False,
            "snapshots_materialized": False,
        }
    )
    plan["snapshot_template"] = snapshot


def _refresh_relationships(asset: dict[str, Any], plans: list[dict[str, Any]]) -> None:
    by_id = {text(row.get("plan_id")): row for row in plans}
    relationships: list[dict[str, Any]] = []
    for raw in _dicts(asset.get("relationships")):
        row = dict(raw)
        plan_id = ""
        if text(row.get("relation")) == "execution_contract_to_runtime_plan":
            plan_id = text(row.get("to"))
        elif text(row.get("relation")) == "runtime_plan_to_interface":
            plan_id = text(row.get("from"))
        plan = by_id.get(plan_id)
        if plan is not None:
            accepted = text(plan.get("status")) == "TEMPLATE_READY"
            row["status"] = "accepted" if accepted else "candidate"
            row["confidence"] = 1.0 if accepted else 0.0
        relationships.append(row)
    asset["relationships"] = relationships
    asset["runtime_plan_relationships"] = [
        row
        for row in relationships
        if text(row.get("relation"))
        in {"execution_contract_to_runtime_plan", "runtime_plan_to_interface"}
    ]


def _refresh_gaps(asset: dict[str, Any], missing_count: int) -> None:
    gaps = [
        row
        for row in _dicts(asset.get("coverage_gaps"))
        if text(row.get("kind")) != _MISSING_CONTRACT_REASON
    ]
    if missing_count:
        gaps.append(
            {
                "kind": _MISSING_CONTRACT_REASON,
                "gap_type": "approved_database_observer_contract_missing_at_runtime_plan",
                "source_id": "*",
                "missing_contract_count": missing_count,
                "blocks_runtime_plan": True,
                "execution_allowed": False,
                "operator_action": (
                    "rebuild and re-approve the current database Observer mapping; do not "
                    "reuse an older Runtime Plan template"
                ),
            }
        )
    asset["coverage_gaps"] = gaps


def project_database_observers_into_runtime_plans(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Replace scoped generic snapshots with current approved Observer contracts."""
    contracts = {
        text(row.get("observer_id")): row
        for row in _dicts(asset.get("database_observer_contracts"))
        if text(row.get("observer_id"))
        and text(row.get("status")) == "READY_FOR_RUNTIME_CONNECTION_BINDING"
        and bool(row.get("runtime_observer_authoritative"))
    }
    bindings = {
        text(row.get("binding_id")): row
        for row in _dicts(
            asset.get("behavior_implementation_bindings")
            or model.get("behavior_implementation_bindings")
        )
        if text(row.get("binding_id"))
    }
    unknowns = [
        row
        for row in _dicts(asset.get("runtime_plan_unknowns"))
        if text(row.get("reason_code")) != _MISSING_CONTRACT_REASON
    ]
    plans: list[dict[str, Any]] = []
    projected_count = 0
    approved_binding_count = 0
    missing_count = 0

    for raw_plan in _dicts(asset.get("runtime_plans")):
        plan = dict(raw_plan)
        binding = bindings.get(text(plan.get("implementation_binding_ref"))) or {}
        approved = _approved_bindings(binding)
        approved_binding_count += len(approved)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for row in approved:
            grouped.setdefault(text(row.get("observer_id")), []).append(row)

        oracle = dict(as_dict(plan.get("oracle_query_templates")))
        retained = [
            row
            for row in _dicts(oracle.get("templates"))
            if text(row.get("template_kind"))
            != "APPROVED_DATABASE_OBSERVER_SNAPSHOT"
            and not _generic_snapshot_replaced(row, approved)
        ]
        exact_templates: list[dict[str, Any]] = []
        plan_missing = False
        for observer_id, rows in grouped.items():
            contract = contracts.get(observer_id)
            if not contract:
                unknowns.append(
                    {
                        "unknown_id": stable_id(
                            "runtime_plan_unknown",
                            plan.get("plan_id"),
                            _MISSING_CONTRACT_REASON,
                            observer_id,
                        ),
                        "kind": _MISSING_CONTRACT_REASON,
                        "reason_code": _MISSING_CONTRACT_REASON,
                        "runtime_plan_ref": plan.get("plan_id"),
                        "implementation_binding_ref": plan.get(
                            "implementation_binding_ref"
                        ),
                        "observer_contract_ref": observer_id,
                        "blocks_runtime_plan": True,
                        "execution_allowed": False,
                    }
                )
                missing_count += 1
                plan_missing = True
                continue
            exact_templates.append(
                _template(
                    plan=plan,
                    observer_id=observer_id,
                    bindings=rows,
                    contract=contract,
                )
            )
            projected_count += 1

        oracle.update(
            {
                "templates": _dedupe(
                    [*retained, *exact_templates], "template_id"
                ),
                "approved_database_observer_template_count": len(exact_templates),
                "database_observer_handler_id": _HANDLER_ID,
                "database_queries_executable": False,
                "runtime_connection_binding_required": bool(exact_templates),
                "runtime_identity_values_required": bool(exact_templates),
            }
        )
        plan["oracle_query_templates"] = oracle
        if plan_missing:
            plan["status"] = "INCOMPLETE"
            plan["formal_runtime_plan"] = False
        plan.update(
            {
                "database_queries_executable": False,
                "database_connections_opened": False,
                "database_observer_runtime_templates_projected": bool(
                    exact_templates
                ),
                "database_observer_runtime_template_count": len(exact_templates),
            }
        )
        _refresh_snapshot_template(plan)
        plans.append(plan)

    plans = _dedupe(plans, "plan_id")
    unknowns = _dedupe(unknowns, "unknown_id")
    asset["runtime_plans"] = plans
    asset["runtime_plan_unknowns"] = unknowns
    model["runtime_plans"] = [dict(row) for row in plans]
    model["runtime_plan_unknowns"] = [dict(row) for row in unknowns]
    _refresh_relationships(asset, plans)
    model["runtime_plan_relationships"] = [
        dict(row) for row in asset.get("runtime_plan_relationships") or []
    ]
    _refresh_gaps(asset, missing_count)

    ready = sum(1 for row in plans if text(row.get("status")) == "TEMPLATE_READY")
    incomplete = sum(1 for row in plans if text(row.get("status")) == "INCOMPLETE")
    gate = dict(as_dict(asset.get("runtime_plan_gate")))
    status = (
        "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
        if incomplete
        else "PASS"
        if plans
        else text(gate.get("status")) or "NO_RUNTIME_PLAN_COMPILED"
    )
    gate.update(
        {
            "status": status,
            "entry_allowed": status == "PASS",
            "runtime_plan_ready": status == "PASS",
            "execution_allowed": False,
        }
    )
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "runtime_plan_count": len(plans),
            "ready_runtime_plan_count": ready,
            "incomplete_runtime_plan_count": incomplete,
            "approved_database_observer_binding_count": approved_binding_count,
            "approved_database_observer_runtime_template_count": projected_count,
            "missing_database_observer_contract_count": missing_count,
        }
    )
    gate["metrics"] = metrics
    asset["runtime_plan_gate"] = gate
    model["runtime_plan_gate"] = dict(gate)

    projection = {
        "schema": DATABASE_OBSERVER_RUNTIME_PLAN_PROJECTION_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not approved_binding_count
            else "PARTIAL"
            if missing_count
            else "COMPLETE"
        ),
        "approved_observer_contract_count": len(contracts),
        "approved_observer_binding_count": approved_binding_count,
        "runtime_template_count": projected_count,
        "missing_contract_count": missing_count,
        "runtime_connection_open_count": 0,
        "query_execution_count": 0,
        "oracle_verdict_count": 0,
        "raw_sql_compiled": False,
        "secret_value_retained": False,
        "derived_templates_rebuilt_from_current_contracts": True,
        "stale_runtime_plan_authority_retained": False,
    }
    asset["database_observer_runtime_plan_projection"] = projection
    projected_summary = {
        "runtime_plan_status": status,
        "runtime_plan_ready": status == "PASS",
        "runtime_plan_count": len(plans),
        "runtime_plan_incomplete_count": incomplete,
        "runtime_plan_unknown_count": len(unknowns),
        "database_observer_runtime_template_count": projected_count,
        "database_observer_runtime_template_gap_count": missing_count,
        "runtime_execution_allowed": False,
    }
    summary = dict(as_dict(asset.get("summary")))
    summary.update(projected_summary)
    asset["summary"] = summary
    source_summary = dict(as_dict(model.get("source_summary")))
    source_summary.update(projected_summary)
    model["source_summary"] = source_summary
    model_metrics = dict(as_dict(model.get("metrics")))
    model_metrics.update(projected_summary)
    model["metrics"] = model_metrics
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_database_templates_use_approved_observer_contracts": True,
            "generic_database_snapshots_cannot_replace_approved_observers": True,
            "runtime_database_templates_retain_no_connection_secrets": True,
            "runtime_database_templates_do_not_emit_oracle_verdicts": True,
            "runtime_database_queries_remain_non_executable_until_materialized": True,
            "runtime_database_plan_relationships_follow_current_plan_status": True,
            "runtime_database_templates_rebuilt_from_current_contracts": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "DATABASE_OBSERVER_RUNTIME_PLAN_PROJECTION_SCHEMA",
    "project_database_observers_into_runtime_plans",
]
