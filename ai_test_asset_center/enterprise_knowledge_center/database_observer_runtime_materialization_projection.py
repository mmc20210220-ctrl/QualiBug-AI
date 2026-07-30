"""Project exact database Observer Runtime Plan templates into execution drafts.

The Runtime Materialization remains non-sendable. This stage only freezes which approved contract
must be observed in which real execution phase. Effect observers use BEFORE+AFTER only when every
identity predicate is available from the request; response-derived identities (for example a
server-generated create ID) can truthfully support AFTER only. No connection is opened and no
identity value, secret, SQL statement or Oracle verdict is materialized here.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Iterable

from .enterprise_understanding.schema import as_dict, as_list, stable_id, text, unique_text

DATABASE_OBSERVER_RUNTIME_MATERIALIZATION_SCHEMA = (
    "qualibug.database-observer-runtime-materialization-projection.v1"
)
DRAFT_SCHEMA = "qualibug.database-observer-execution-draft.v1"
_TEMPLATE_KIND = "APPROVED_DATABASE_OBSERVER_SNAPSHOT"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [dict(row) for row in as_list(value) if isinstance(row, dict)]


def _dedupe(rows: Iterable[dict[str, Any]], key: str) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        identity = text(raw.get(key)) if isinstance(raw, dict) else ""
        if identity:
            output[identity] = dict(raw)
    return list(output.values())


def _effective_phases(template: dict[str, Any]) -> list[str]:
    declared = text(template.get("phase")).upper()
    predicates = _dicts(template.get("identity_predicates"))
    sources = unique_text(row.get("value_source") for row in predicates)
    response_identity = any(source.startswith("response.body.") for source in sources)
    request_identity_complete = bool(sources) and all(
        source.startswith(("request.body.", "request.parameter.")) for source in sources
    )
    if declared == "BEFORE_AND_AFTER":
        if response_identity:
            return ["AFTER"]
        return ["BEFORE", "AFTER"] if request_identity_complete else []
    if declared == "BEFORE":
        return ["BEFORE"] if request_identity_complete else []
    if declared == "AFTER":
        return ["AFTER"] if sources else []
    return []


def _draft(
    materialization: dict[str, Any], template: dict[str, Any], phase: str
) -> dict[str, Any]:
    contract = deepcopy(as_dict(template.get("database_observer_contract")))
    observer_id = text(template.get("observer_contract_ref"))
    return {
        "schema": DRAFT_SCHEMA,
        "draft_id": stable_id(
            "database_observer_execution_draft",
            materialization.get("materialization_id"),
            observer_id,
            phase,
        ),
        "runtime_materialization_ref": materialization.get("materialization_id"),
        "runtime_plan_ref": materialization.get("runtime_plan_ref"),
        "observer_handler_id": text(template.get("observer_handler_id"))
        or "approved_database_readback",
        "observer_contract_ref": observer_id,
        "observation_phase": phase,
        "database_observer_contract": contract,
        "database_connection_ref": text(template.get("database_connection_ref")),
        "identity_value_sources": unique_text(
            row.get("value_source")
            for row in _dicts(template.get("identity_predicates"))
        ),
        "projection": deepcopy(as_list(template.get("projection"))),
        "source_slot_refs": deepcopy(as_list(template.get("source_slot_refs"))),
        "mapping_decision_refs": deepcopy(as_list(template.get("mapping_decision_refs"))),
        "required": True,
        "runtime_identity_values_materialized": False,
        "runtime_connection_bound": False,
        "database_connection_opened": False,
        "query_executed": False,
        "query_parameterized": True,
        "raw_sql_retained": False,
        "predicate_values_retained": False,
        "secret_values_retained": False,
        "observer_receipt_produced": False,
        "oracle_verdict_emitted": False,
        "write_target_allowed": False,
        "mutation_allowed": False,
    }


def project_database_observer_runtime_materializations(
    asset: dict[str, Any], model: dict[str, Any]
) -> dict[str, Any]:
    """Attach current approved Observer phase drafts to current materializations."""
    plans = {
        text(row.get("plan_id")): row
        for row in _dicts(asset.get("runtime_plans") or model.get("runtime_plans"))
        if text(row.get("plan_id"))
    }
    materializations: list[dict[str, Any]] = []
    unknowns = [
        row
        for row in _dicts(asset.get("runtime_materialization_unknowns"))
        if text(row.get("reason_code"))
        != "RUNTIME_MATERIALIZATION_DATABASE_OBSERVER_PHASE_UNRESOLVED"
    ]
    draft_count = 0
    blocked_count = 0

    for raw in _dicts(asset.get("runtime_materializations")):
        row = dict(raw)
        plan = plans.get(text(row.get("runtime_plan_ref"))) or {}
        templates = [
            template
            for template in _dicts(as_dict(plan.get("oracle_query_templates")).get("templates"))
            if text(template.get("template_kind")) == _TEMPLATE_KIND
        ]
        drafts: list[dict[str, Any]] = []
        phase_errors: list[dict[str, Any]] = []
        for template in templates:
            phases = _effective_phases(template)
            if not phases:
                phase_errors.append(
                    {
                        "observer_contract_ref": template.get("observer_contract_ref"),
                        "declared_phase": template.get("phase"),
                        "identity_value_sources": unique_text(
                            item.get("value_source")
                            for item in _dicts(template.get("identity_predicates"))
                        ),
                    }
                )
                continue
            drafts.extend(_draft(row, template, phase) for phase in phases)

        drafts = _dedupe(drafts, "draft_id")
        row["database_observer_execution_drafts"] = drafts
        row["database_observer_execution_draft_count"] = len(drafts)
        row["database_observer_phase_execution_required"] = bool(drafts)
        row["database_observer_phase_executor"] = "existing_experiment_executor"
        row["database_observer_queries_executable"] = False
        row["database_observer_connection_opened"] = False
        row["database_observer_query_execution_count"] = 0
        row["database_observer_oracle_verdict_count"] = 0
        if phase_errors:
            reason = "RUNTIME_MATERIALIZATION_DATABASE_OBSERVER_PHASE_UNRESOLVED"
            row["status"] = "INCOMPLETE"
            row["formal_runtime_materialization"] = False
            row["database_observer_phase_status"] = "BLOCKED"
            row["unresolved_materialization_semantics"] = unique_text(
                [*as_list(row.get("unresolved_materialization_semantics")), reason]
            )
            unknowns.append(
                {
                    "unknown_id": stable_id(
                        "runtime_materialization_unknown",
                        row.get("materialization_id"),
                        reason,
                    ),
                    "kind": reason,
                    "reason_code": reason,
                    "runtime_materialization_ref": row.get("materialization_id"),
                    "runtime_plan_ref": row.get("runtime_plan_ref"),
                    "phase_errors": phase_errors,
                    "blocks_runtime_materialization": True,
                    "execution_allowed": False,
                }
            )
            blocked_count += 1
        else:
            row["database_observer_phase_status"] = (
                "DRAFT_READY" if drafts else "NOT_APPLICABLE"
            )
        draft_count += len(drafts)
        materializations.append(row)

    materializations = _dedupe(materializations, "materialization_id")
    unknowns = _dedupe(unknowns, "unknown_id")
    asset["runtime_materializations"] = materializations
    asset["runtime_materialization_unknowns"] = unknowns
    model["runtime_materializations"] = [dict(row) for row in materializations]
    model["runtime_materialization_unknowns"] = [dict(row) for row in unknowns]

    ready = sum(1 for row in materializations if text(row.get("status")) == "DRAFT_READY")
    incomplete = sum(
        1 for row in materializations if text(row.get("status")) == "INCOMPLETE"
    )
    gate = dict(as_dict(asset.get("runtime_materialization_gate")))
    status = (
        "BLOCKED_RUNTIME_MATERIALIZATION_INCOMPLETE"
        if incomplete
        else "PASS"
        if materializations
        else text(gate.get("status")) or "NO_RUNTIME_MATERIALIZATION_COMPILED"
    )
    gate.update(
        {
            "status": status,
            "entry_allowed": status == "PASS",
            "runtime_materialization_ready": status == "PASS",
            "execution_allowed": False,
        }
    )
    metrics = dict(as_dict(gate.get("metrics")))
    metrics.update(
        {
            "runtime_materialization_count": len(materializations),
            "ready_runtime_materialization_count": ready,
            "incomplete_runtime_materialization_count": incomplete,
            "database_observer_execution_draft_count": draft_count,
            "database_observer_phase_blocked_count": blocked_count,
        }
    )
    gate["metrics"] = metrics
    asset["runtime_materialization_gate"] = gate
    model["runtime_materialization_gate"] = dict(gate)

    asset["database_observer_runtime_materialization_projection"] = {
        "schema": DATABASE_OBSERVER_RUNTIME_MATERIALIZATION_SCHEMA,
        "status": (
            "NOT_APPLICABLE"
            if not draft_count and not blocked_count
            else "PARTIAL"
            if blocked_count
            else "COMPLETE"
        ),
        "execution_draft_count": draft_count,
        "blocked_phase_count": blocked_count,
        "runtime_connection_open_count": 0,
        "query_execution_count": 0,
        "oracle_verdict_count": 0,
        "response_identity_before_snapshot_fabricated": False,
        "raw_sql_retained": False,
        "secret_values_retained": False,
    }
    summary = dict(as_dict(asset.get("summary")))
    summary.update(
        {
            "database_observer_execution_draft_count": draft_count,
            "database_observer_phase_blocked_count": blocked_count,
            "runtime_materialization_status": status,
            "runtime_materialization_ready": status == "PASS",
        }
    )
    asset["summary"] = summary
    governance = dict(as_dict(asset.get("governance")))
    governance.update(
        {
            "database_observer_execution_phases_are_explicit": True,
            "response_derived_identity_cannot_fabricate_before_snapshot": True,
            "database_observer_materialization_opens_no_connection": True,
            "database_observer_materialization_retains_no_runtime_values": True,
            "database_observer_materialization_emits_no_oracle_verdict": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = [
    "DATABASE_OBSERVER_RUNTIME_MATERIALIZATION_SCHEMA",
    "DRAFT_SCHEMA",
    "project_database_observer_runtime_materializations",
]
