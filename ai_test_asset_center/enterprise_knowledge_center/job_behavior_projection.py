"""Project governed Job assets into the existing Business Behavior IR v1.

This is a post-enrichment closure step: the normal enterprise-understanding builder runs
first, Job discovery appends ASYNC_JOB operations, and this module then appends strictly
governed Job behaviors to the same ``business_behaviors`` collection.  Candidate or unsafe
Jobs remain visible in a Job coverage ledger and never block unrelated system behavior.
"""
from __future__ import annotations

import hashlib
import json
from functools import wraps
from pathlib import Path
from typing import Any, Iterable

from .enterprise_understanding.gate import assess_understanding_model
from .enterprise_understanding.schema import (
    BEHAVIOR_GATE_SCHEMA,
    BEHAVIOR_SCHEMA,
    IMPLEMENTATION_BINDING_GATE_SCHEMA,
    IMPLEMENTATION_BINDING_SCHEMA,
    as_dict,
    as_list,
    dedupe_evidence,
    stable_id,
    text,
    unique_text,
)
from ..job_platform_contract import ASYNC_OPERATION_KIND, JOB_ASSET_SCHEMA

JOB_BEHAVIOR_GATE_SCHEMA = "qualibug.async-job-behavior-gate.v1"
JOB_COVERAGE_LEDGER_SCHEMA = "qualibug.async-job-coverage-ledger.v1"
JOB_LINEAGE_SCHEMA = "qualibug.async-job-lineage-receipt.v1"
_INSTALL_MARKER = "_qualibug_job_behavior_projection_installed"


def _dicts(value: Any) -> list[dict[str, Any]]:
    return [row for row in as_list(value) if isinstance(row, dict)]


def _fingerprint(value: Any) -> str:
    blob = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _source_channels(asset: dict[str, Any]) -> set[str]:
    channels = {
        text(value).upper()
        for value in as_list(asset.get("evidence_channels"))
        if text(value)
    }
    for row in _dicts(asset.get("evidence")):
        explicit = text(row.get("source_kind") or row.get("kind")).upper()
        if explicit:
            channels.add(explicit)
        if text(row.get("connector_id")) or text(row.get("external_ref")).lower().startswith(
            "job_platform:"
        ):
            channels.add("JOB_PLATFORM")
        locator = text(row.get("source_locator") or row.get("locator"))
        suffix = Path(locator.split("#", 1)[0]).suffix.lower()
        if suffix in {".java", ".kt", ".kts", ".groovy", ".py", ".js", ".ts", ".tsx", ".go", ".cs"}:
            channels.add("SOURCE_CODE")
        if suffix in {".md", ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".csv"}:
            channels.add("BUSINESS_DOCUMENT")
        derivation = text(row.get("derivation")).lower()
        if "runtime" in derivation or "observ" in derivation:
            channels.add("RUNTIME")
    if as_dict(asset.get("operator_governance_receipt")):
        channels.add("OPERATOR_GOVERNANCE")
    return channels


def _confirmation_basis(asset: dict[str, Any]) -> str:
    authority = as_dict(asset.get("fact_authority"))
    declared = text(authority.get("implementation_confirmation_basis"))
    if declared:
        return declared
    channels = _source_channels(asset) - {"", "SOURCE_ASSET"}
    if "OPERATOR_GOVERNANCE" in channels:
        return "EXPLICIT_OPERATOR_GOVERNANCE"
    if len(channels) >= 2:
        return "CROSS_SOURCE_IMPLEMENTATION_EVIDENCE"
    return "SINGLE_SOURCE_IMPLEMENTATION_EVIDENCE"


def _operation_index(operations: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for operation in operations:
        if not isinstance(operation, dict):
            continue
        contract = as_dict(operation.get("async_contract"))
        job_asset_ref = text(contract.get("job_asset_ref"))
        if job_asset_ref and text(operation.get("operation_kind")) == ASYNC_OPERATION_KIND:
            result.setdefault(job_asset_ref, []).append(operation)
    return result


def _actor_ids(asset: dict[str, Any], operation: dict[str, Any]) -> list[str]:
    return unique_text(
        [
            *as_list(asset.get("actor_refs")),
            *as_list(operation.get("actor_refs")),
            *as_list(as_dict(operation.get("async_contract")).get("actor_refs")),
        ]
    )


def _connector_ids(asset: dict[str, Any], operation: dict[str, Any]) -> list[str]:
    contract = as_dict(operation.get("async_contract"))
    runtime = as_dict(contract.get("runtime"))
    values = [
        asset.get("connector_id"),
        contract.get("connector_id"),
        runtime.get("connector_id"),
        *as_list(asset.get("connector_identity_candidates")),
    ]
    for evidence in _dicts(asset.get("evidence")):
        values.append(evidence.get("connector_id"))
    return unique_text(values)


def _job_behavior(
    asset: dict[str, Any],
    operation: dict[str, Any],
    *,
    actor_index: set[str],
) -> tuple[dict[str, Any], list[str], str]:
    job_asset_id = text(asset.get("job_asset_id"))
    operation_id = text(operation.get("operation_id"))
    behavior_asset = as_dict(asset.get("behavior"))
    contract = as_dict(operation.get("async_contract"))
    runtime = as_dict(contract.get("runtime"))
    trigger = as_dict(contract.get("trigger"))
    testability = as_dict(contract.get("testability"))
    evidence = dedupe_evidence(_dicts(asset.get("evidence")))
    actor_refs = _actor_ids(asset, operation)
    object_refs = unique_text(
        [*as_list(behavior_asset.get("object_refs")), *as_list(operation.get("object_refs"))]
    )
    connector_ids = _connector_ids(asset, operation)
    write_set = unique_text(as_list(contract.get("write_set")))
    terminal_states = unique_text(as_list(runtime.get("terminal_states")))
    success_states = unique_text(as_list(runtime.get("success_states")))
    predicates = _dicts(behavior_asset.get("selection_predicates"))
    condition_combinator = text(
        behavior_asset.get("condition_combinator")
        or asset.get("condition_combinator")
    ).upper()
    if len(predicates) <= 1:
        condition_combinator = "SINGLE_CONDITION"

    unresolved = unique_text(
        [
            "ASYNC_JOB_OPERATION_NOT_BOUND" if not operation_id else "",
            "ASYNC_JOB_ACTOR_NOT_BOUND"
            if len(actor_refs) != 1 or actor_refs[0] not in actor_index
            else "",
            "ASYNC_JOB_OBJECT_SCOPE_NOT_RESOLVED" if not object_refs else "",
            "ASYNC_JOB_CONNECTOR_IDENTITY_UNRESOLVED" if len(connector_ids) != 1 else "",
            "ASYNC_JOB_PLATFORM_JOB_ID_MISSING" if not text(contract.get("platform_job_id")) else "",
            "ASYNC_JOB_TRIGGER_CONTRACT_UNRESOLVED"
            if text(trigger.get("type")).upper() in {"", "UNKNOWN"}
            else "",
            "ASYNC_JOB_SUCCESS_STATE_CONTRACT_UNRESOLVED"
            if not terminal_states or not success_states
            else "",
            "ASYNC_JOB_NOT_EXECUTION_READY"
            if text(testability.get("execution_status")) != "EXECUTION_READY"
            else "",
            "ASYNC_JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED"
            if write_set or text(testability.get("safety_level")) != "READ_ONLY"
            else "",
            "ASYNC_JOB_SOURCE_EVIDENCE_MISSING" if not evidence else "",
            "BEHAVIOR_CONDITION_COMBINATOR_UNRESOLVED"
            if len(predicates) > 1 and condition_combinator not in {"AND", "OR"}
            else "",
        ]
    )
    basis = _confirmation_basis(asset)
    confirmed = not unresolved and basis in {
        "EXPLICIT_OPERATOR_GOVERNANCE",
        "CROSS_SOURCE_IMPLEMENTATION_EVIDENCE",
    }
    status = "INCOMPLETE" if unresolved else "CONFIRMED" if confirmed else "CANDIDATE"
    if len(predicates) > 1 and condition_combinator not in {"AND", "OR"}:
        condition_combinator = "UNRESOLVED"
    behavior_id = stable_id("business_behavior", "async_job", job_asset_id, operation_id)
    behavior = {
        "schema": BEHAVIOR_SCHEMA,
        "behavior_id": behavior_id,
        "behavior_family_id": stable_id(
            "behavior_family", "async_job", operation_id, object_refs, actor_refs
        ),
        "source_kind": "ASYNC_JOB_ASSET",
        "source_refs": unique_text(
            [job_asset_id, *[row.get("source_id") for row in evidence]]
        ),
        "actor_refs": actor_refs,
        "operation_ref": operation_id,
        "object_refs": object_refs,
        "trigger": trigger,
        "preconditions": predicates,
        "state_preconditions": [],
        "expected_effects": unique_text(
            [
                text(row.get("expression") or row.get("statement") or row.get("effect"))
                for row in _dicts(behavior_asset.get("expected_effects"))
            ]
        ),
        "state_effects": [],
        "data_effects": [],
        "permission_decision": "ALLOW" if confirmed else "UNSPECIFIED",
        "permission_authority": (
            "SOURCE_DECLARED_TEST_TRIGGER" if confirmed else "UNRESOLVED"
        ),
        "exceptions": [],
        "compensations": unique_text(as_list(behavior_asset.get("compensation_paths"))),
        "async_runtime": {
            "platform_type": contract.get("platform_type"),
            "platform_job_id": contract.get("platform_job_id"),
            "connector_id": connector_ids[0] if len(connector_ids) == 1 else "",
            "terminal_states": terminal_states,
            "success_states": success_states,
            "runtime_integrity_only": True,
            "formal_business_finding_eligible": False,
        },
        "safety": {
            "safety_level": testability.get("safety_level"),
            "write_set_empty": not write_set,
            "write_set": write_set,
        },
        "testability": dict(testability),
        "condition_combinator": condition_combinator,
        "confirmation_basis": basis,
        "unresolved_semantics": unresolved,
        "status": status,
        "candidate_only": status != "CONFIRMED",
        "formal_business_rule": status == "CONFIRMED",
        "formal_business_finding_eligible": False,
        "evidence": evidence,
        "job_lineage": {
            "job_asset_id": job_asset_id,
            "operation_id": operation_id,
        },
    }
    primary_reason = unresolved[0] if unresolved else (
        "" if confirmed else "ASYNC_JOB_BEHAVIOR_NOT_CONFIRMED"
    )
    return behavior, unresolved, primary_reason


def project_job_behaviors(
    asset: dict[str, Any],
    model: dict[str, Any],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], dict[str, Any]]:
    """Return Job behaviors, one coverage row per asset, lineage receipts and gate."""
    operations = _dicts(model.get("operations"))
    operation_index = _operation_index(operations)
    actor_index = {
        text(row.get("actor_id"))
        for row in _dicts(model.get("actors"))
        if text(row.get("actor_id"))
    }
    actor_index.update(
        text(row.get("id"))
        for row in _dicts(model.get("actors"))
        if text(row.get("id"))
    )
    behaviors: list[dict[str, Any]] = []
    ledger: list[dict[str, Any]] = []
    lineages: list[dict[str, Any]] = []

    for job_asset in _dicts(asset.get("job_assets")):
        job_asset_id = text(job_asset.get("job_asset_id"))
        matches = operation_index.get(job_asset_id, [])
        if len(matches) != 1:
            reason = "ASYNC_JOB_OPERATION_NOT_BOUND" if not matches else "ASYNC_JOB_OPERATION_IDENTITY_AMBIGUOUS"
            ledger.append(
                {
                    "schema": JOB_COVERAGE_LEDGER_SCHEMA,
                    "job_asset_id": job_asset_id,
                    "status": "BLOCKED",
                    "reason_code": reason,
                    "operation_candidates": [row.get("operation_id") for row in matches],
                    "formal_obligation_eligible": False,
                }
            )
            continue
        behavior, unresolved, reason = _job_behavior(
            job_asset, matches[0], actor_index=actor_index
        )
        behaviors.append(behavior)
        ledger.append(
            {
                "schema": JOB_COVERAGE_LEDGER_SCHEMA,
                "job_asset_id": job_asset_id,
                "operation_id": behavior.get("operation_ref"),
                "behavior_id": behavior.get("behavior_id"),
                "status": behavior.get("status"),
                "reason_code": reason,
                "unresolved_semantics": unresolved,
                "formal_obligation_eligible": behavior.get("status") == "CONFIRMED",
            }
        )
        lineage_payload = {
            "schema": JOB_LINEAGE_SCHEMA,
            "job_asset_id": job_asset_id,
            "operation_id": behavior.get("operation_ref"),
            "behavior_id": behavior.get("behavior_id"),
            "obligation_id": "",
            "experiment_id": "",
            "protocol_id": "",
            "source_receipt_ids": unique_text(
                [
                    row.get("receipt_id")
                    for row in _dicts(job_asset.get("evidence"))
                    if text(row.get("receipt_id"))
                ]
            ),
            "identity_complete": bool(
                job_asset_id and behavior.get("operation_ref") and behavior.get("behavior_id")
            ),
            "identity_drift": False,
        }
        lineage_payload["fingerprint"] = _fingerprint(lineage_payload)
        lineages.append(lineage_payload)

    counts = {
        status: sum(1 for row in behaviors if text(row.get("status")) == status)
        for status in ("CONFIRMED", "CANDIDATE", "INCOMPLETE", "CONFLICTED")
    }
    covered_ids = {text(row.get("job_asset_id")) for row in ledger if text(row.get("job_asset_id"))}
    job_asset_count = len(_dicts(asset.get("job_assets")))
    gate_status = (
        "PASS"
        if counts["CONFIRMED"]
        else "PARTIAL_ASYNC_JOB_BEHAVIOR"
        if behaviors or ledger
        else "NOT_REQUESTED"
    )
    gate = {
        "schema": JOB_BEHAVIOR_GATE_SCHEMA,
        "status": gate_status,
        "entry_allowed": counts["CONFIRMED"] > 0,
        "metrics": {
            "job_asset_count": job_asset_count,
            "job_asset_coverage_count": len(covered_ids),
            "job_asset_coverage_rate": round(len(covered_ids) / job_asset_count, 4)
            if job_asset_count
            else 1.0,
            "confirmed_job_behavior_count": counts["CONFIRMED"],
            "candidate_job_behavior_count": counts["CANDIDATE"],
            "incomplete_job_behavior_count": counts["INCOMPLETE"],
            "conflicted_job_behavior_count": counts["CONFLICTED"],
            "lineage_receipt_count": len(lineages),
        },
        "runtime_integrity_only": True,
        "formal_business_finding_eligible": False,
        "customer_manual_job_maintenance_required": False,
    }
    return behaviors, ledger, lineages, gate


def _job_implementation_binding(behavior: dict[str, Any]) -> dict[str, Any]:
    runtime = as_dict(behavior.get("async_runtime"))
    return {
        "schema": IMPLEMENTATION_BINDING_SCHEMA,
        "binding_id": stable_id(
            "behavior_implementation_binding", behavior.get("behavior_id"), "async_job"
        ),
        "behavior_ref": behavior.get("behavior_id"),
        "behavior_status": behavior.get("status"),
        "operation_ref": behavior.get("operation_ref"),
        "object_refs": unique_text(as_list(behavior.get("object_refs"))),
        "api_operation_bindings": [],
        "ui_action_bindings": [],
        "condition_observer_bindings": [],
        "effect_observer_bindings": [],
        "response_observer_bindings": [],
        "job_operation_binding": {
            "platform_type": runtime.get("platform_type"),
            "platform_job_id": runtime.get("platform_job_id"),
            "connector_id": runtime.get("connector_id"),
            "terminal_states": as_list(runtime.get("terminal_states")),
            "success_states": as_list(runtime.get("success_states")),
        },
        "status": "BOUND",
        "scenario_planning_ready": True,
        "execution_ready": False,
        "request_payload_compiled": False,
        "expected_assertion_compiled": True,
        "runtime_integrity_only": True,
        "formal_business_finding_eligible": False,
        "automatic_endpoint_fallback_allowed": False,
        "token_overlap_is_authoritative": False,
        "evidence": dedupe_evidence(_dicts(behavior.get("evidence"))),
    }


def refresh_job_behavior_projection(asset: dict[str, Any]) -> dict[str, Any]:
    """Append governed Job behaviors after Job enrichment and refresh model metrics."""
    model = as_dict(asset.get("enterprise_understanding_model"))
    if not model:
        return asset
    job_behaviors, ledger, lineages, job_gate = project_job_behaviors(asset, model)
    non_job_behaviors = [
        dict(row)
        for row in _dicts(model.get("business_behaviors"))
        if text(row.get("source_kind")) != "ASYNC_JOB_ASSET"
    ]
    model["business_behaviors"] = [*non_job_behaviors, *job_behaviors]
    model["async_job_behavior_coverage_ledger"] = ledger
    model["async_job_lineage_receipts"] = lineages
    model["async_job_behavior_gate"] = job_gate

    existing_bindings = [
        dict(row)
        for row in _dicts(model.get("behavior_implementation_bindings"))
        if text(row.get("behavior_ref"))
        not in {text(job.get("behavior_id")) for job in job_behaviors}
    ]
    job_bindings = [
        _job_implementation_binding(row)
        for row in job_behaviors
        if text(row.get("status")) == "CONFIRMED"
    ]
    all_bindings = [*existing_bindings, *job_bindings]
    model["behavior_implementation_bindings"] = all_bindings
    existing_gate = as_dict(model.get("implementation_binding_gate"))
    existing_metrics = as_dict(existing_gate.get("metrics"))
    ready = sum(1 for row in all_bindings if row.get("scenario_planning_ready") is True)
    total = len(all_bindings)
    implementation_status = (
        "PASS"
        if total and ready == total
        else "PARTIAL_IMPLEMENTATION_BINDING"
        if total
        else text(existing_gate.get("status")) or "NO_BEHAVIOR_IMPLEMENTATION_BINDING"
    )
    model["implementation_binding_gate"] = {
        **existing_gate,
        "schema": IMPLEMENTATION_BINDING_GATE_SCHEMA,
        "status": implementation_status,
        "entry_allowed": implementation_status == "PASS",
        "scenario_planning_allowed": implementation_status == "PASS",
        "execution_allowed": False,
        "metrics": {
            **existing_metrics,
            "behavior_binding_count": total,
            "scenario_ready_binding_count": ready,
            "scenario_ready_rate": round(ready / total, 4) if total else 0.0,
            "async_job_binding_count": len(job_bindings),
        },
    }

    behavior_gate = as_dict(model.get("behavior_ir_gate"))
    behavior_metrics = as_dict(behavior_gate.get("metrics"))
    statuses = {
        status: sum(
            1
            for row in _dicts(model.get("business_behaviors"))
            if text(row.get("status")) == status
        )
        for status in ("CONFIRMED", "CANDIDATE", "INCOMPLETE", "CONFLICTED")
    }
    model["behavior_ir_gate"] = {
        **behavior_gate,
        "schema": BEHAVIOR_GATE_SCHEMA,
        "metrics": {
            **behavior_metrics,
            "behavior_count": len(_dicts(model.get("business_behaviors"))),
            "confirmed_behavior_count": statuses["CONFIRMED"],
            "candidate_behavior_count": statuses["CANDIDATE"],
            "incomplete_behavior_count": statuses["INCOMPLETE"],
            "conflicted_behavior_count": statuses["CONFLICTED"],
            "async_job_behavior_count": len(job_behaviors),
        },
        "async_job_gate_ref": JOB_BEHAVIOR_GATE_SCHEMA,
    }
    model["evidence_index"] = dedupe_evidence(
        [
            *_dicts(model.get("evidence_index")),
            *[
                evidence
                for behavior in job_behaviors
                for evidence in _dicts(behavior.get("evidence"))
            ],
        ]
    )
    summary = as_dict(model.get("source_summary"))
    summary.update(
        {
            "business_behavior_count": len(_dicts(model.get("business_behaviors"))),
            "confirmed_behavior_count": statuses["CONFIRMED"],
            "candidate_behavior_count": statuses["CANDIDATE"],
            "incomplete_behavior_count": statuses["INCOMPLETE"],
            "conflicted_behavior_count": statuses["CONFLICTED"],
            "async_job_behavior_count": len(job_behaviors),
            "confirmed_async_job_behavior_count": statuses["CONFIRMED"]
            - sum(
                1
                for row in non_job_behaviors
                if text(row.get("status")) == "CONFIRMED"
            ),
        }
    )
    model["source_summary"] = summary
    gate = assess_understanding_model(
        model,
        upstream_gate=as_dict(asset.get("enterprise_comprehension_gate")),
    )
    model["gate"] = gate
    model["metrics"] = {
        **as_dict(gate.get("metrics")),
        "async_job_behavior_status": job_gate.get("status"),
        "confirmed_async_job_behavior_count": as_dict(job_gate.get("metrics")).get(
            "confirmed_job_behavior_count", 0
        ),
        "async_job_asset_coverage_rate": as_dict(job_gate.get("metrics")).get(
            "job_asset_coverage_rate", 1.0
        ),
    }
    asset["enterprise_understanding_model"] = model
    return asset


def install_job_behavior_projection():
    """Wrap the already Job-enriched knowledge builder; no new builder authority."""
    from . import _api
    from ._common import ROOT, _safe_project_id
    from ._job_assets import _persist_job_enrichment

    current = _api.build_enterprise_business_knowledge_asset
    if getattr(current, _INSTALL_MARKER, False):
        return current
    original = current

    @wraps(original)
    def wrapped(
        project_id: str = "real_project_demo",
        root: Path | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        resolved_root = root or ROOT
        project = _safe_project_id(project_id)
        asset = original(project, resolved_root, options or {})
        refreshed = refresh_job_behavior_projection(asset)
        _persist_job_enrichment(refreshed, project_id=project, root=resolved_root)
        return refreshed

    setattr(wrapped, _INSTALL_MARKER, True)
    wrapped._qualibug_original_builder = original  # type: ignore[attr-defined]
    _api.build_enterprise_business_knowledge_asset = wrapped
    return wrapped


__all__ = [
    "JOB_BEHAVIOR_GATE_SCHEMA",
    "JOB_COVERAGE_LEDGER_SCHEMA",
    "JOB_LINEAGE_SCHEMA",
    "project_job_behaviors",
    "refresh_job_behavior_projection",
    "install_job_behavior_projection",
]
