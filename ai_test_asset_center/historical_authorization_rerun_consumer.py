"""Consume historical authorization rerun plans through the existing mainline.

The consumer never replays a stored experiment.  It re-resolves the current source,
runtime target and approval, prepares a fresh run/campaign, builds the normal discovery
plan, narrows that in-memory plan to the exact current obligation identity, and hands the
result to the existing experiment-candidate runner.  Ordinary scan_result.json files are
not written; only content-addressed remediation receipts are persisted.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from .adaptive_discovery_planner import build_agent_intent_plan
from .adaptive_planning_history import (
    build_planning_budget_receipt,
    finalize_planning_budget_receipt,
    load_prior_planning_history_receipt,
)
from .customer_delivery_gate_v2 import CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
from .discovery_mainline import (
    DiscoveryMainlineInputs,
    DiscoveryPlanningBundle,
    run_discovery_mainline,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .discovery_runtime import build_discovery_plan, run_experiment_candidate
from .experiment_runtime_support import run_environment_preflight
from .historical_authorization_rerun_plan import (
    DEFAULT_PLAN_RELATIVE_PATH,
    validate_historical_authorization_rerun_plan,
)
from .obligation_attempt_ledger import validate_obligation_attempt_ledger
from .private_pilot_json_io import _read_json_object, _write_json_object_atomic
from .scan_impl_prepare import prepare_scan_before_pipeline
from . import historical_authorization_rerun_plan as _rerun_planner


CONSUMPTION_SCHEMA = "qualibug.historical-authorization-rerun-consumption.v1"
REMEDIATION_RECEIPT_SCHEMA = "qualibug.historical-authorization-remediation-receipt.v1"
DEFAULT_CONSUMPTION_RELATIVE_PATH = (
    Path("platform_outputs") / "historical_authorization_rerun_consumption.json"
)
_REQUEST_OUTCOMES = {
    "NOT_EXECUTED",
    "SKIPPED_NOT_READY",
    "BLOCKED_BINDING_DRIFT",
    "BLOCKED_APPROVAL",
    "RECOMPILE_FAILED",
    "RECOMPILE_BLOCKED",
    "EXECUTION_INCONCLUSIVE",
    "CURRENT_DEFECT_REPRODUCED",
    "CURRENT_DEFECT_NOT_REPRODUCED",
    "CONTRADICTION",
}
_BINDING_FIELDS = (
    "scope_id",
    "environment_ref",
    "environment_type",
    "target_base_url",
    "execution_mode",
    "source_binding_status",
    "source_id",
    "source_hash",
)


class HistoricalAuthorizationRerunConsumptionError(ValueError):
    """A rerun request cannot be consumed without weakening current authority."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _compile_status(experiment: dict[str, Any]) -> tuple[str, str]:
    receipt = _dict(experiment.get("compile_receipt"))
    status = _text(receipt.get("status")).upper()
    reason = _text(receipt.get("reason_code") or receipt.get("detail"))
    return status, reason


def _filtered_experiment_rows(value: Any, required_ids: set[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _list(value):
        row = _dict(raw)
        obligation_id = _text(row.get("obligation_id"))
        expanded_from = _text(row.get("expanded_from_obligation_id"))
        if (
            obligation_id in required_ids
            or expanded_from in required_ids
            or any(obligation_id.startswith(required + "__v_") for required in required_ids)
        ):
            rows.append(dict(row))
    return rows


def _targeted_planning_bundle(
    full_plan: DiscoveryPlanningBundle,
    *,
    required_obligation_ids: Iterable[str],
    inputs: DiscoveryMainlineInputs,
) -> DiscoveryPlanningBundle:
    """Narrow one normal compiled plan without bypassing compiler/preflight gates."""
    required = sorted({_text(value) for value in required_obligation_ids if _text(value)})
    if not required:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_target_obligation_missing"
        )
    if len(required) > 20:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_target_obligation_limit_exceeded"
        )
    obligations = [
        dict(row)
        for row in _list(full_plan.obligations.get("obligations"))
        if isinstance(row, dict)
    ]
    obligations_by_id: dict[str, dict[str, Any]] = {}
    for row in obligations:
        obligation_id = _text(row.get("obligation_id"))
        if not obligation_id or obligation_id in obligations_by_id:
            raise HistoricalAuthorizationRerunConsumptionError(
                f"current_obligation_identity_invalid:{obligation_id or 'MISSING'}"
            )
        obligations_by_id[obligation_id] = row
    missing = sorted(set(required) - set(obligations_by_id))
    if missing:
        raise HistoricalAuthorizationRerunConsumptionError(
            "current_target_obligation_not_found:" + ",".join(missing)
        )

    all_by_obligation = _dict(full_plan.experiments.get("by_obligation"))
    experiments_by_obligation: dict[str, dict[str, Any]] = {}
    selected_rows: list[dict[str, Any]] = []
    compile_outcomes: list[dict[str, str]] = []
    original_plan = _dict(full_plan.experiments.get("obligation_plan"))
    original_rows = {
        _text(_dict(row).get("obligation_id")): dict(_dict(row))
        for row in (
            _list(original_plan.get("selected"))
            + _list(original_plan.get("pending_next_round"))
        )
        if _text(_dict(row).get("obligation_id"))
    }
    for obligation_id in required:
        experiment = _dict(all_by_obligation.get(obligation_id))
        if not experiment:
            raise HistoricalAuthorizationRerunConsumptionError(
                f"current_target_experiment_missing:{obligation_id}"
            )
        experiments_by_obligation[obligation_id] = dict(experiment)
        compile_status, compile_reason = _compile_status(experiment)
        compile_outcomes.append({
            "obligation_id": obligation_id,
            "status": compile_status or "MISSING",
            "reason": compile_reason,
        })
        if compile_status == "COMPILED":
            existing = original_rows.get(obligation_id, {})
            selected_rows.append({
                "obligation_id": obligation_id,
                "risk_family": _text(obligations_by_id[obligation_id].get("risk_family")),
                "path_prefix": _text(existing.get("path_prefix")),
                "operation_key": _text(existing.get("operation_key")),
                "score": float(existing.get("score") or 1.0),
                "experiment_id": _text(experiment.get("experiment_id")),
            })

    targeted_plan = {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "budget": len(required),
        "history_status": "TARGETED_REMEDIATION",
        "cold_start_reason": "",
        "formal_yield_status": "NOT_APPLICABLE",
        "historical_receipt_ids": [],
        "selected": selected_rows,
        "pending_next_round": [],
        "selected_count": len(selected_rows),
        "pending_count": 0,
        "pending_dedup_removed": 0,
        "pending_truncated": 0,
        "pending_truncation_reason": "",
        "family_coverage": {},
        "path_prefix_coverage": {},
        "operation_coverage": {},
        "stop_condition": "historical_authorization_target_scope_exhausted",
    }
    targeted_obligations = [obligations_by_id[value] for value in required]
    targeted_intents = build_agent_intent_plan(
        targeted_plan,
        obligations=targeted_obligations,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=full_plan.behavior_ir,
    )
    runtime_contract = dict(_dict(full_plan.experiments.get("runtime_contract")))
    targeted_preflight = run_environment_preflight(
        root=inputs.root,
        project=inputs.project,
        base_url=_text(runtime_contract.get("approved_base_url")),
        obligation_plan=targeted_plan,
        behavior_ir=full_plan.behavior_ir,
        runtime_contract=runtime_contract,
    )
    budget_receipt = finalize_planning_budget_receipt(
        build_planning_budget_receipt(len(required)),
        consumed_budget=len(selected_rows),
        stop_condition="historical_authorization_target_scope_exhausted",
    )
    required_set = set(required)
    experiments = dict(full_plan.experiments)
    experiments.update({
        "experiments": _filtered_experiment_rows(
            full_plan.experiments.get("experiments"), required_set
        ),
        "blocked_experiments": _filtered_experiment_rows(
            full_plan.experiments.get("blocked_experiments"), required_set
        ),
        "all_experiments": _filtered_experiment_rows(
            full_plan.experiments.get("all_experiments"), required_set
        ),
        "by_obligation": experiments_by_obligation,
        "obligation_plan": targeted_plan,
        "agent_intent_plan": targeted_intents,
        "planning_budget_receipt": budget_receipt,
        "preflight_receipt": targeted_preflight,
        "runtime_interface_discovery_enabled": False,
        "runtime_interface_discovery_plan": {},
        "_planning_budget": len(required),
        "historical_authorization_remediation_selection": {
            "schema_version": "qualibug.historical-authorization-remediation-selection.v1",
            "status": "BOUND",
            "required_obligation_ids": required,
            "compiled_selected_count": len(selected_rows),
            "compile_outcomes": compile_outcomes,
            "other_obligation_execution_allowed": False,
        },
    })
    obligation_pack = dict(full_plan.obligations)
    obligation_pack["obligations"] = targeted_obligations
    obligation_pack["historical_authorization_target_scope"] = {
        "required_obligation_ids": required,
        "other_obligation_execution_allowed": False,
    }
    return DiscoveryPlanningBundle(
        mainline_run=full_plan.mainline_run,
        behavior_ir=full_plan.behavior_ir,
        obligations=obligation_pack,
        experiments=experiments,
    )


def _run_targeted_mainline(
    *,
    project_id: str,
    root: Path,
    request: dict[str, Any],
    binding: dict[str, Any],
) -> dict[str, Any]:
    predecessor = _dict(request.get("predecessor"))
    obligation_id = _text(predecessor.get("obligation_id"))
    context = {
        "scope_id": _text(binding.get("scope_id")),
        "environment_ref": _text(binding.get("environment_ref")),
        "environment_type": _text(binding.get("environment_type")),
        "source_manifest": {
            "source_id": _text(binding.get("source_id")),
            "source_hash": _text(binding.get("source_hash")),
        },
        "approved_base_url": _text(binding.get("target_base_url")),
        "execution_approval_id": _text(_dict(request.get("approval")).get("approval_id")),
        "execution_mode": "safe_read_only",
        "campaign_rerun_key": "historical_authorization:" + _text(request.get("request_id")),
        "campaign_rerun_reason": "recompile quarantined historical authorization finding",
        "runtime_interface_discovery_enabled": False,
        "agent_semantic_linking_enabled": False,
        "historical_authorization_remediation_request": {
            "request_id": _text(request.get("request_id")),
            "request_fingerprint": _text(request.get("request_fingerprint")),
            "predecessor_finding_id": _text(predecessor.get("finding_id")),
            "predecessor_obligation_id": obligation_id,
        },
    }
    prepared = prepare_scan_before_pipeline(
        project_id,
        root,
        base_url=_text(binding.get("target_base_url")),
        save_report=False,
        campaign_context=context,
    )
    if prepared.get("status") != "ready":
        early = _dict(prepared.get("result"))
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_prepare_failed:"
            + _text(early.get("error") or early.get("customer_output_status") or "UNKNOWN")
        )

    from .pipeline_runtime import _runtime_contract
    from .policy_registry import strategy_fingerprint
    from .policy_wiring import get_effective_policy_strategy
    from .system_behavior_space_context import (
        reset_behavior_space_context,
        set_behavior_space_context,
    )
    from .v12_pipeline import (
        _behavior_slice_settings,
        _build_mainline_campaign,
        _campaign_candidate,
        _normalize_executable_api_document,
        _reset_pipeline_har_entries,
    )

    submitted_api_spec_text = _text(prepared.get("api_doc_text"))
    normalized_api_spec_text, normalization = _normalize_executable_api_document(
        submitted_api_spec_text
    )
    if _text(normalization.get("status")).upper() == "FAILED_SAFE":
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_api_normalization_failed:"
            + _text(normalization.get("reason") or normalization.get("error_type"))
        )
    prepared_context = dict(_dict(prepared.get("context")))
    prepared_context["_campaign_api_spec_text"] = normalized_api_spec_text
    runtime_contract = _runtime_contract(
        prepared_context,
        _text(prepared.get("approved_base_url")),
        submitted_api_spec_text,
    )
    if _text(runtime_contract.get("status")) != "approved":
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_runtime_contract_blocked:"
            + ",".join(_text(value) for value in _list(runtime_contract.get("missing_requirements")))
        )
    prepared_context["_runtime_contract"] = runtime_contract
    prepared_context.setdefault("policy_id", _text(prepared_context.get("policy_version")))
    prepared_context.setdefault(
        "strategy_fingerprint",
        strategy_fingerprint(get_effective_policy_strategy()),
    )
    prepared_context.setdefault(
        "adaptive_planning_history_receipt",
        load_prior_planning_history_receipt(root, project_id),
    )
    candidate = _campaign_candidate(
        project_id,
        _text(prepared.get("prd_text")),
        normalized_api_spec_text,
        _text(prepared.get("schema_text")),
        _text(runtime_contract.get("approved_base_url")),
        _behavior_slice_settings(),
        prepared_context,
        root,
        submitted_api_spec_text,
    )
    prepared_context["campaign_id"] = candidate.campaign_id
    inputs = DiscoveryMainlineInputs(
        project=project_id,
        root=root,
        prd_text=_text(prepared.get("prd_text")),
        api_spec_text=normalized_api_spec_text,
        db_schema_text=_text(prepared.get("schema_text")),
        approved_base_url=_text(runtime_contract.get("approved_base_url")),
        campaign_context=prepared_context,
        existing_findings=(),
    )

    def _build_targeted_plan(
        mainline_inputs: DiscoveryMainlineInputs,
        campaign_handle: Any,
    ) -> DiscoveryPlanningBundle:
        return _targeted_planning_bundle(
            build_discovery_plan(mainline_inputs, campaign_handle),
            required_obligation_ids=[obligation_id],
            inputs=mainline_inputs,
        )

    token = set_behavior_space_context(project_id, root)
    try:
        _reset_pipeline_har_entries()
        return run_discovery_mainline(
            inputs,
            build_campaign=_build_mainline_campaign,
            build_plan=_build_targeted_plan,
            legacy_runner=None,
            experiment_runner=run_experiment_candidate,
        )
    finally:
        reset_behavior_space_context(token)


def _fresh_authority(
    project_id: str,
    *,
    root: Path,
    request: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], list[str]]:
    fresh_binding = _rerun_planner._runtime_binding(project_id, root)
    fresh_approval = _rerun_planner._approval_projection(
        project_id,
        root=root,
        binding=fresh_binding,
    )
    expected_binding = _dict(request.get("current_runtime_binding"))
    drift = [
        field
        for field in _BINDING_FIELDS
        if _text(fresh_binding.get(field)) != _text(expected_binding.get(field))
    ]
    expected_approval = _dict(request.get("approval"))
    if (
        _text(fresh_approval.get("status"))
        != _text(expected_approval.get("status"))
        or _text(fresh_approval.get("approval_id"))
        != _text(expected_approval.get("approval_id"))
    ):
        drift.append("approval")
    return fresh_binding, fresh_approval, drift


def _base_receipt(
    *,
    request: dict[str, Any],
    plan_fingerprint: str,
    project_id: str,
    status: str,
    reason: str,
    binding: dict[str, Any],
    approval: dict[str, Any],
    completed_at_utc: str,
) -> dict[str, Any]:
    payload = {
        "schema_version": REMEDIATION_RECEIPT_SCHEMA,
        "status": status,
        "reason": _text(reason),
        "completed_at_utc": completed_at_utc,
        "project_id": project_id,
        "plan_fingerprint": plan_fingerprint,
        "request_id": _text(request.get("request_id")),
        "request_fingerprint": _text(request.get("request_fingerprint")),
        "predecessor": dict(_dict(request.get("predecessor"))),
        "current_binding_fingerprint": _fingerprint(binding),
        "approval_id": _text(approval.get("approval_id")),
        "successor": {
            "run_id": "",
            "campaign_id": "",
            "mainline_contract_fingerprint": "",
            "ledger_fingerprint": "",
            "obligation_id": "",
            "experiment_id": "",
            "execution_id": "",
            "terminal_stage": "",
            "terminal_status": "",
            "reason_code": "",
            "gate_receipt_id": "",
            "gate_schema_version": "",
            "finding_id": "",
            "delivery_occurrence_finding_ids": [],
            "canonical_defect_ids": [],
        },
        "historical_quarantine_supersession_allowed": False,
        "historical_finding_republication_allowed": False,
        "successor_publication_requires_normal_gate_v2": True,
        "source_artifacts_modified": False,
        "ordinary_scan_result_modified": False,
    }
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return payload


def _receipt_from_result(
    *,
    request: dict[str, Any],
    plan_fingerprint: str,
    project_id: str,
    binding: dict[str, Any],
    approval: dict[str, Any],
    result: dict[str, Any],
    completed_at_utc: str,
) -> dict[str, Any]:
    try:
        mainline = validate_mainline_run_contract(_dict(result.get("mainline_run")))
        ledger = validate_obligation_attempt_ledger(
            _dict(result.get("obligation_attempt_ledger"))
        )
    except Exception as exc:
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="CONTRADICTION",
            reason=f"SUCCESSOR_AUTHORITY_INVALID:{type(exc).__name__}:{exc}",
            binding=binding,
            approval=approval,
            completed_at_utc=completed_at_utc,
        )
    predecessor = _dict(request.get("predecessor"))
    if (
        mainline["run_id"] == _text(predecessor.get("run_id"))
        or mainline["campaign_id"] == _text(predecessor.get("campaign_id"))
    ):
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="CONTRADICTION",
            reason="SUCCESSOR_IDENTITY_REUSED",
            binding=binding,
            approval=approval,
            completed_at_utc=completed_at_utc,
        )
    obligation_id = _text(predecessor.get("obligation_id"))
    attempts = [
        _dict(value)
        for value in _list(ledger.get("attempts"))
        if _text(_dict(value).get("obligation_id")) == obligation_id
    ]
    if len(attempts) != 1 or int(ledger.get("selected_count") or 0) != 1:
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="CONTRADICTION",
            reason="SUCCESSOR_TARGET_SCOPE_INVALID",
            binding=binding,
            approval=approval,
            completed_at_utc=completed_at_utc,
        )
    attempt = attempts[0]
    gate = _dict(attempt.get("gate_receipt"))
    terminal_stage = _text(attempt.get("terminal_stage"))
    terminal_status = _text(attempt.get("terminal_status")).upper()
    gate_v2 = gate.get("schema_version") == CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
    if terminal_stage == "compile":
        status = "RECOMPILE_BLOCKED"
    elif terminal_stage != "gate" or not gate_v2:
        status = "EXECUTION_INCONCLUSIVE"
    elif terminal_status == "DELIVERABLE":
        status = "CURRENT_DEFECT_REPRODUCED"
    elif terminal_status == "REJECTED":
        status = "CURRENT_DEFECT_NOT_REPRODUCED"
    else:
        status = "EXECUTION_INCONCLUSIVE"
    formal = _dict(result.get("formal_count_projection"))
    registry = _dict(result.get("canonical_defect_registry"))
    successor = {
        "run_id": mainline["run_id"],
        "campaign_id": mainline["campaign_id"],
        "mainline_contract_fingerprint": mainline["contract_fingerprint"],
        "ledger_fingerprint": _text(ledger.get("ledger_fingerprint")),
        "obligation_id": obligation_id,
        "experiment_id": _text(attempt.get("experiment_id")),
        "execution_id": _text(attempt.get("execution_id")),
        "terminal_stage": terminal_stage,
        "terminal_status": terminal_status,
        "reason_code": _text(attempt.get("reason_code")),
        "gate_receipt_id": _text(attempt.get("gate_receipt_id")),
        "gate_schema_version": _text(gate.get("schema_version")),
        "finding_id": _text(attempt.get("finding_id")),
        "delivery_occurrence_finding_ids": list(
            formal.get("delivery_occurrence_finding_ids") or []
        ),
        "canonical_defect_ids": list(registry.get("canonical_defect_ids") or []),
    }
    payload = {
        "schema_version": REMEDIATION_RECEIPT_SCHEMA,
        "status": status,
        "reason": "",
        "completed_at_utc": completed_at_utc,
        "project_id": project_id,
        "plan_fingerprint": plan_fingerprint,
        "request_id": _text(request.get("request_id")),
        "request_fingerprint": _text(request.get("request_fingerprint")),
        "predecessor": dict(predecessor),
        "current_binding_fingerprint": _fingerprint(binding),
        "approval_id": _text(approval.get("approval_id")),
        "successor": successor,
        "historical_quarantine_supersession_allowed": status in {
            "CURRENT_DEFECT_REPRODUCED",
            "CURRENT_DEFECT_NOT_REPRODUCED",
        },
        "historical_finding_republication_allowed": False,
        "successor_publication_requires_normal_gate_v2": True,
        "source_artifacts_modified": False,
        "ordinary_scan_result_modified": False,
    }
    payload["receipt_fingerprint"] = _fingerprint(payload)
    return payload


def _consume_request(
    request: dict[str, Any],
    *,
    plan_fingerprint: str,
    root: Path,
    execute: bool,
    completed_at_utc: str,
) -> dict[str, Any]:
    project_id = _text(request.get("project_id"))
    binding = dict(_dict(request.get("current_runtime_binding")))
    approval = dict(_dict(request.get("approval")))
    if _text(request.get("status")) != "READY_FOR_CONTROLLED_RECOMPILE":
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="SKIPPED_NOT_READY",
            reason="REQUEST_NOT_READY_FOR_CONTROLLED_RECOMPILE",
            binding=binding,
            approval=approval,
            completed_at_utc=completed_at_utc,
        )
    if not execute:
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="NOT_EXECUTED",
            reason="EXPLICIT_EXECUTE_FLAG_REQUIRED",
            binding=binding,
            approval=approval,
            completed_at_utc=completed_at_utc,
        )
    fresh_binding, fresh_approval, drift = _fresh_authority(
        project_id,
        root=root,
        request=request,
    )
    if drift:
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="BLOCKED_BINDING_DRIFT",
            reason="CURRENT_AUTHORITY_DRIFT:" + ",".join(sorted(set(drift))),
            binding=fresh_binding,
            approval=fresh_approval,
            completed_at_utc=completed_at_utc,
        )
    if (
        _text(fresh_approval.get("status")) != "CURRENT_APPROVAL_FOUND"
        or not _text(fresh_approval.get("approval_id"))
    ):
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="BLOCKED_APPROVAL",
            reason=_text(fresh_approval.get("code")) or "CURRENT_APPROVAL_REQUIRED",
            binding=fresh_binding,
            approval=fresh_approval,
            completed_at_utc=completed_at_utc,
        )
    try:
        result = _run_targeted_mainline(
            project_id=project_id,
            root=root,
            request=request,
            binding=fresh_binding,
        )
    except Exception as exc:
        return _base_receipt(
            request=request,
            plan_fingerprint=plan_fingerprint,
            project_id=project_id,
            status="RECOMPILE_FAILED",
            reason=f"{type(exc).__name__}:{exc}",
            binding=fresh_binding,
            approval=fresh_approval,
            completed_at_utc=completed_at_utc,
        )
    return _receipt_from_result(
        request=request,
        plan_fingerprint=plan_fingerprint,
        project_id=project_id,
        binding=fresh_binding,
        approval=fresh_approval,
        result=result,
        completed_at_utc=completed_at_utc,
    )


def consume_historical_authorization_rerun_plan(
    plan: dict[str, Any],
    *,
    root: str | Path,
    execute: bool = False,
    request_ids: Iterable[str] | None = None,
    generated_at_utc: str | None = None,
) -> dict[str, Any]:
    validated = validate_historical_authorization_rerun_plan(plan)
    resolved_root = Path(root).expanduser().resolve()
    selected_ids = {_text(value) for value in (request_ids or []) if _text(value)}
    requests = [
        dict(_dict(request))
        for project in _list(validated.get("projects"))
        for request in _list(_dict(project).get("requests"))
        if not selected_ids or _text(_dict(request).get("request_id")) in selected_ids
    ]
    requests.sort(key=lambda value: _text(value.get("request_id")))
    missing = sorted(selected_ids - {_text(value.get("request_id")) for value in requests})
    if missing:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_rerun_request_not_found:" + ",".join(missing)
        )
    completed_at = _text(generated_at_utc) or _utc_now()
    receipts = [
        _consume_request(
            request,
            plan_fingerprint=_text(validated.get("plan_fingerprint")),
            root=resolved_root,
            execute=execute,
            completed_at_utc=completed_at,
        )
        for request in requests
    ]
    status_counts = {
        status: sum(receipt["status"] == status for receipt in receipts)
        for status in sorted(_REQUEST_OUTCOMES)
    }
    status = (
        "CONTRADICTION"
        if status_counts["CONTRADICTION"]
        else "BLOCKED"
        if any(
            status_counts[value]
            for value in (
                "BLOCKED_BINDING_DRIFT",
                "BLOCKED_APPROVAL",
                "RECOMPILE_FAILED",
                "RECOMPILE_BLOCKED",
                "EXECUTION_INCONCLUSIVE",
            )
        )
        else "COMPLETED"
        if any(
            status_counts[value]
            for value in (
                "CURRENT_DEFECT_REPRODUCED",
                "CURRENT_DEFECT_NOT_REPRODUCED",
            )
        )
        else "NOT_EXECUTED"
    )
    payload = {
        "schema_version": CONSUMPTION_SCHEMA,
        "generated_at_utc": completed_at,
        "root": str(resolved_root),
        "plan_fingerprint": _text(validated.get("plan_fingerprint")),
        "execution_requested": bool(execute),
        "status": status,
        "request_count": len(receipts),
        "status_counts": status_counts,
        "historical_findings_republished": False,
        "source_artifacts_modified": False,
        "ordinary_scan_results_modified": False,
        "receipts": receipts,
    }
    payload["consumption_fingerprint"] = _fingerprint(payload)
    return validate_historical_authorization_rerun_consumption(payload)


def _validate_receipt(receipt: dict[str, Any]) -> None:
    required = {
        "schema_version", "status", "reason", "completed_at_utc", "project_id",
        "plan_fingerprint", "request_id", "request_fingerprint", "predecessor",
        "current_binding_fingerprint", "approval_id", "successor",
        "historical_quarantine_supersession_allowed",
        "historical_finding_republication_allowed",
        "successor_publication_requires_normal_gate_v2",
        "source_artifacts_modified", "ordinary_scan_result_modified",
        "receipt_fingerprint",
    }
    if set(receipt) != required or receipt.get("schema_version") != REMEDIATION_RECEIPT_SCHEMA:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_remediation_receipt_fields_invalid"
        )
    if receipt.get("status") not in _REQUEST_OUTCOMES:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_remediation_receipt_status_invalid"
        )
    if (
        receipt.get("historical_finding_republication_allowed") is not False
        or receipt.get("successor_publication_requires_normal_gate_v2") is not True
        or receipt.get("source_artifacts_modified") is not False
        or receipt.get("ordinary_scan_result_modified") is not False
    ):
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_remediation_policy_invalid"
        )
    successor = _dict(receipt.get("successor"))
    supersession = receipt.get("historical_quarantine_supersession_allowed") is True
    terminal_statuses = {
        "CURRENT_DEFECT_REPRODUCED",
        "CURRENT_DEFECT_NOT_REPRODUCED",
    }
    if supersession != (receipt.get("status") in terminal_statuses):
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_remediation_supersession_invalid"
        )
    if supersession and (
        successor.get("gate_schema_version") != CUSTOMER_DELIVERY_GATE_RECEIPT_SCHEMA
        or not _text(successor.get("run_id"))
        or not _text(successor.get("campaign_id"))
        or not _text(successor.get("ledger_fingerprint"))
        or _text(successor.get("terminal_stage")) != "gate"
    ):
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_remediation_terminal_authority_invalid"
        )
    observed = _text(receipt.get("receipt_fingerprint"))
    expected = _fingerprint(
        {key: value for key, value in receipt.items() if key != "receipt_fingerprint"}
    )
    if not observed or observed != expected:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_remediation_receipt_fingerprint_invalid"
        )


def validate_historical_authorization_rerun_consumption(
    report: dict[str, Any],
) -> dict[str, Any]:
    row = _dict(report)
    required = {
        "schema_version", "generated_at_utc", "root", "plan_fingerprint",
        "execution_requested", "status", "request_count", "status_counts",
        "historical_findings_republished", "source_artifacts_modified",
        "ordinary_scan_results_modified", "receipts", "consumption_fingerprint",
    }
    if set(row) != required or row.get("schema_version") != CONSUMPTION_SCHEMA:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_rerun_consumption_fields_invalid"
        )
    if (
        not isinstance(row.get("execution_requested"), bool)
        or row.get("historical_findings_republished") is not False
        or row.get("source_artifacts_modified") is not False
        or row.get("ordinary_scan_results_modified") is not False
    ):
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_rerun_consumption_policy_invalid"
        )
    receipts = [_dict(value) for value in _list(row.get("receipts"))]
    for receipt in receipts:
        _validate_receipt(receipt)
        if _text(receipt.get("plan_fingerprint")) != _text(row.get("plan_fingerprint")):
            raise HistoricalAuthorizationRerunConsumptionError(
                "historical_authorization_rerun_consumption_plan_mismatch"
            )
    request_ids = [_text(value.get("request_id")) for value in receipts]
    if request_ids != sorted(set(request_ids)):
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_rerun_consumption_requests_invalid"
        )
    expected_counts = {
        status: sum(value.get("status") == status for value in receipts)
        for status in sorted(_REQUEST_OUTCOMES)
    }
    if (
        int(row.get("request_count") or 0) != len(receipts)
        or row.get("status_counts") != expected_counts
    ):
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_rerun_consumption_summary_invalid"
        )
    observed = _text(row.get("consumption_fingerprint"))
    expected = _fingerprint(
        {key: value for key, value in row.items() if key != "consumption_fingerprint"}
    )
    if not observed or observed != expected:
        raise HistoricalAuthorizationRerunConsumptionError(
            "historical_authorization_rerun_consumption_fingerprint_invalid"
        )
    return dict(row)


def resolve_consumption_path(
    output: str | Path | None,
    *,
    root: str | Path,
) -> Path:
    resolved_root = Path(root).expanduser().resolve()
    if output is None or _text(output) in {"", "default"}:
        return resolved_root / DEFAULT_CONSUMPTION_RELATIVE_PATH
    path = Path(output).expanduser()
    return path.resolve() if path.is_absolute() else (resolved_root / path).resolve()


def write_historical_authorization_rerun_consumption(
    report: dict[str, Any],
    *,
    output: str | Path | None,
    root: str | Path,
) -> Path:
    validated = validate_historical_authorization_rerun_consumption(report)
    resolved_root = Path(root).expanduser().resolve()
    destination = resolve_consumption_path(output, root=resolved_root)
    _write_json_object_atomic(destination, validated)
    for receipt in validated["receipts"]:
        project_id = _text(receipt.get("project_id"))
        request_id = _text(receipt.get("request_id"))
        if not project_id or not request_id:
            continue
        receipt_path = (
            resolved_root
            / "platform_outputs"
            / project_id
            / "historical_authorization_remediation"
            / request_id
            / "remediation_receipt.json"
        )
        _write_json_object_atomic(receipt_path, receipt)
    return destination


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Consume a historical authorization rerun plan through the current "
            "compiler/executor mainline. No traffic is sent without --execute."
        )
    )
    parser.add_argument("--root", default=".", help="QualiBug root directory.")
    parser.add_argument(
        "--plan",
        default=str(DEFAULT_PLAN_RELATIVE_PATH),
        help="Historical authorization rerun plan JSON.",
    )
    parser.add_argument(
        "--request",
        action="append",
        default=[],
        help="Consume only this request ID. Repeat for multiple requests.",
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="Explicitly authorize controlled safe-read-only execution.",
    )
    parser.add_argument("--output", default="default", help="Consumption report JSON path.")
    parser.add_argument(
        "--stdout-only",
        action="store_true",
        help="Print without writing remediation receipts.",
    )
    parser.add_argument("--compact", action="store_true", help="Print compact JSON.")
    args = parser.parse_args(argv)
    root = Path(args.root).expanduser().resolve()
    plan_path = Path(args.plan).expanduser()
    if not plan_path.is_absolute():
        plan_path = root / plan_path
    try:
        plan = _read_json_object(plan_path.resolve())
        report = consume_historical_authorization_rerun_plan(
            plan,
            root=root,
            execute=args.execute,
            request_ids=args.request,
        )
        if not args.stdout_only:
            write_historical_authorization_rerun_consumption(
                report,
                output=args.output,
                root=root,
            )
    except Exception as exc:
        print(json.dumps({
            "ok": False,
            "error": f"{type(exc).__name__}:{exc}",
            "execution_requested": bool(args.execute),
            "historical_findings_republished": False,
            "source_artifacts_modified": False,
            "ordinary_scan_results_modified": False,
        }, ensure_ascii=False, indent=None if args.compact else 2, sort_keys=True))
        return 2
    print(json.dumps(
        report,
        ensure_ascii=False,
        indent=None if args.compact else 2,
        sort_keys=True,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CONSUMPTION_SCHEMA",
    "DEFAULT_CONSUMPTION_RELATIVE_PATH",
    "HistoricalAuthorizationRerunConsumptionError",
    "REMEDIATION_RECEIPT_SCHEMA",
    "consume_historical_authorization_rerun_plan",
    "main",
    "resolve_consumption_path",
    "validate_historical_authorization_rerun_consumption",
    "write_historical_authorization_rerun_consumption",
]
