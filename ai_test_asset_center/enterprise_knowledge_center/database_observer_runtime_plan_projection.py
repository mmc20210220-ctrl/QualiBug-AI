"""Project approved database Observer contracts into non-executable Runtime Plans.

The generic Runtime Plan compiler can describe a DATABASE_FIELD_SNAPSHOT, but it does not retain
the operator decision, formal Observer contract, identity predicates or handler identity. This
stage replaces only those generic templates that belong to an approved Observer binding. It never
loads a connection, materializes a predicate value, executes SQL or compiles an Oracle verdict.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .enterprise_understanding.schema import as_dict, as_list, stable_id, text, unique_text

DATABASE_OBSERVER_RUNTIME_PLAN_PROJECTION_SCHEMA = (
    "qualibug.database-observer-runtime-plan-projection.v1"
)
_APPROVED_DERIVATION = "operator_approved_database_observer_contract"
_HANDLER_ID = "approved_database_readback"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _approved_bindings(binding: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for slot_collection in (
        "condition_observer_bindings",
        "effect_observer_bindings",
    ):
        for slot in _dicts(binding.get(slot_collection)):
            purpose = text(slot.get("purpose"))
            slot_ref = text(slot.get("slot_ref"))
            for raw in _dicts(slot.get("bindings")):
                if (
                    text(raw.get("binding_kind")) == "DATABASE_FIELD"
                    and text(raw.get("derivation")) == _APPROVED_DERIVATION
                    and bool(raw.get("authoritative"))
                    and text(raw.get("observer_id"))
                ):
                    row = dict(raw)
                    row["purpose"] = purpose
                    row["slot_ref"] = slot_ref
                    rows.append(row)
    return rows


def _phase(rows: Iterable[dict[str, Any]]) -> str:
    purposes = {text(row.get("purpose")) for row in rows}
    if purposes.intersection({"EFFECT_OBSERVER", "STATE_EFFECT_OBSERVER"}):
        return "BEFORE_AND_AFTER"
    return "BEFORE"


def _sanitized_contract(contract: dict[str, Any]) -> dict[str, Any]:
    """Retain executable structure but pin that no runtime secret/value is present."""
    row = deepcopy(contract)
    row["runtime_connection_bound"] = False
    row["runtime_values_materialized"] = False
    row["database_connection_opened"] = False
    row["query_executed"] = False
    row["raw_sql_retained"] = False
    row["secret_values_retained"] = False
    row["oracle_verdict_emitted"] = False
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
        "projection": deepcopy(as_list(as_dict(contract.get("query_plan")).get("projection"))),
        "selected_identity_key": deepcopy(as_list(contract.get("selected_identity_key"))),
        "identity_predicates": deepcopy(as_list(contract.get("identity_predicates"))),
        "database_observer_contract": _sanitized_contract(contract),
        "source_slot_refs": unique_text(row.get("slot_ref") for row in bindings),
        "field_binding_refs": unique_text(row.get("field_binding_id") for row in bindings),
        "mapping_decision_refs": unique_text(row.get("mapping_decision_id") for row in bindings),
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


def _dedupe(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        identity = text(raw.get(key))
        if identity:
            output[identity] = dict(raw)
    return list(output.values())


def _refresh_snapshot_template(plan: dict[str, Any]) -> None:
    templates = _dicts(as_dict(plan.get("oracle_query_templates")).get("templates"))
    before = unique_text(
        row.get("template_id")
        for row in templates
        if text(row.get("phase")) in {"BEFORE", "BEFORE_AND_AFTER"}
    )
    after = unique_text(
        row.get("template_id")
        for row in templates
        if text(row.get("phase")) in {"AFTER", "BEFORE_AND_AFTER"}
    )
    snapshot = dict(as_dict(plan.get("snapshot_template")))
    snapshot["before_oracle_template_refs"] = before
    snapshot["after_oracle_template_refs"] = after
    snapshot["approved_database_observer_templates_projected"] = True
    snapshot["snapshot_queries_executed"] = False
    snapshot["snapshots_materialized"] = False
    plan["snapshot_template"] = snapshot


def project_database_observers_into_runtime_plans(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Replace generic DB snapshots with exact approved Observer runtime templates."""
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
    unknowns = _dicts(asset.get("runtime_plan_unknowns"))
    projected: list[dict[str, Any]] = []
    projected_count = 0
    missing_count = 0

    for raw_plan in _dicts(asset.get("runtime_plans")):
        plan = dict(raw_plan)
        binding = bindings.get(text(plan.get("implementation_binding_ref"))) or {}
        approved = _approved_bindings(binding)
        by_observer: dict[str, list[dict[str, Any]]] = {}
        for row in approved:
            by_observer.setdefault(text(row.get("observer_id")), []).append(row)

        oracle = dict(as_dict(plan.get("oracle_query_templates")))
        templates = _dicts(oracle.get("templates"))
        approved_field_refs = {
            text(row.get("field_id")) for row in approved if text(row.get("field_id"))
        }
        approved_table_refs = {
            text(row.get("table_id")) for row in approved if text(row.get("table_id"))
        }
        retained = [
            row
            for row in templates
            if not (
                text(row.get("template_kind")) == "DATABASE_FIELD_SNAPSHOT"
                and (
                    text(row.get("table_ref")) in approved_table_refs
                    or text(row.get("field_ref")) in approved_field_refs
                    or text(row.get("field"))
                    in {text(item.get("field")) for item in approved}
                )
            )
            and text(row.get("template_kind"))
            != "APPROVED_DATABASE_OBSERVER_SNAPSHOT"
        ]
        exact_templates: list[dict[str, Any]] = []
        plan_missing = False
        for observer_id, rows in by_observer.items():
            contract = contracts.get(observer_id)
            if not contract:
                reason = "RUNTIME_PLAN_APPROVED_DATABASE_OBSERVER_CONTRACT_MISSING"
                unknowns.append(
                    {
                        "unknown_id": stable_id(
                            "runtime_plan_unknown", plan.get("plan_id"), reason, observer_id
                        ),
                        "kind": reason,
                        "reason_code": reason,
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

        oracle["templates"] = _dedupe([*retained, *exact_templates], "template_id")
        oracle["approved_database_observer_template_count"] = len(exact_templates)
        oracle["database_observer_handler_id"] = _HANDLER_ID
        oracle["database_queries_executable"] = False
        oracle["runtime_connection_binding_required"] = bool(exact_templates)
        oracle["runtime_identity_values_required"] = bool(exact_templates)
        plan["oracle_query_templates"] = oracle
        if plan_missing:
            plan["status"] = "INCOMPLETE"
            plan["formal_runtime_plan"] = False
        plan["database_queries_executable"] = False
        plan["database_connections_opened"] = False
        plan["database_observer_runtime_templates_projected"] = bool(exact_templates)
        plan["database_observer_runtime_template_count"] = len(exact_templates)
        _refresh_snapshot_template(plan)
        projected.append(plan)

    projected = _dedupe(projected, "plan_id")
    unknowns = _dedupe(unknowns, "unknown_id")
    asset["runtime_plans"] = projected
    asset["runtime_plan_unknowns"] = unknowns
    model["runtime_plans"] = [dict(row) for row in projected]
    model["runtime_plan_unknowns"] = [dict(row) for row in unknowns]

    ready = sum(1 for row in projected if text(row.get("status")) == "TEMPLATE_READY")
    incomplete = sum(1 for row in projected if text(row.get("status")) == "INCOMPLETE")
    gate = dict(as_dict(asset.get("runtime_plan_gate")))
    status = (
        "BLOCKED_RUNTIME_PLAN_INCOMPLETE"
        if incomplete
        else "PASS"
        if projected
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
            "runtime_plan_count": len(projected),
            "ready_runtime_plan_count": ready,
            "incomplete_runtime_plan_count": incomplete,
            "approved_database_observer_runtime_template_count": projected_count,
            "missing_database_observer_contract_count": missing_count,
        }
    )
    gate["metrics"] = metrics
    asset["runtime_plan_gate"] = gate
    model["runtime_plan_gate"] = dict(gate)

    asset["database_observer_runtime_plan_projection"] = {
        "schema": DATABASE_OBSERVER_RUNTIME_PLAN_PROJECTION_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not contracts and not projected_count
            else "PARTIAL"
            if missing_count
            else "COMPLETE"
        ),
        "approved_observer_contract_count": len(contracts),
        "runtime_template_count": projected_count,
        "missing_contract_count": missing_count,
        "runtime_connection_open_count": 0,
        "query_execution_count": 0,
        "oracle_verdict_count": 0,
        "raw_sql_compiled": False,
        "secret_value_retained": False,
    }
    summary = dict(as_dict(asset.get("summary")))
    summary.update(
        {
            "database_observer_runtime_template_count": projected_count,
            "database_observer_runtime_template_gap_count": missing_count,
        }
    )
    asset["summary"] = summary
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "runtime_database_templates_use_approved_observer_contracts": True,
            "generic_database_snapshots_cannot_replace_approved_observers": True,
            "runtime_database_templates_retain_no_connection_secrets": True,
            "runtime_database_templates_do_not_emit_oracle_verdicts": True,
            "runtime_database_queries_remain_non_executable_until_materialized": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "DATABASE_OBSERVER_RUNTIME_PLAN_PROJECTION_SCHEMA",
    "project_database_observers_into_runtime_plans",
]
