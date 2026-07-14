"""Single-authority discovery planning and experiment-candidate runtime."""
from __future__ import annotations

import hashlib
import json
import re
import time
from pathlib import Path
from typing import Any

from .adaptive_discovery_planner import plan_obligation_round
from .behavior_ir import build_behavior_ir_from_knowledge_asset
from .canonical_defect_registry import (
    CanonicalDefectRegistryError,
    build_canonical_defect_registry,
    build_defect_identity_consistency,
    canonical_representative_findings,
)
from .customer_delivery_gate import (
    build_customer_delivery_gate_receipt,
    customer_delivery_rejection_reasons,
)
from .discovery_funnel import build_funnel
from .discovery_mainline import (
    DiscoveryMainlineInputs,
    DiscoveryPlanningBundle,
)
from .discovery_mainline_contract import (
    MainlineContractError,
    MainlineRunContract,
    build_mainline_run_contract,
)
from .discovery_quality_projection import (
    build_formal_count_projection,
    validated_delivery_gate_finding_ids,
)
from .experiment_compiler import compile_experiments
from .experiment_executor import execute_selected_experiments
from .fixture_dag import attach_fixture_dag_to_experiments
from .formal_delivery_scope import formal_customer_deliverable_findings
from .formal_delivery_authority import build_formal_delivery_authority_receipt
from .obligation_attempt_ledger import build_obligation_attempt_ledger
from .obligation_compiler import compile_obligations_from_behavior_ir
from .operational_receipts import (
    aggregate_execution_operational_receipts,
    build_execution_operational_receipt_from_counts,
)


RUNTIME_SCHEMA = "qualibug.discovery-runtime.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _governed_write_block_reason(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    normalized = text
    if normalized.lower().startswith("runtimeerror:"):
        normalized = normalized.split(":", 1)[1].strip()
    for prefix in (
        "write_cleanup_operation_not_declared",
        "identity_mutation_requires_disposable_fixture",
        "protected_runtime_identity_mutation_blocked",
        "governed_write_blocked:",
        "multi_write_executor_missing_per_write_governance_hook",
        "invalid_governed_write_event:",
        "DELETE_SAFETY_GUARD",
    ):
        if normalized == prefix or normalized.startswith(prefix):
            return normalized.split("\n", 1)[0][:240]
    return ""


def _legacy_execution_terminal(
    *,
    cleanup_failed: bool,
    observation_receipt_ids: list[str],
    trace_errors: list[Any],
    skipped_reasons: list[str],
    trace_present: bool,
) -> tuple[str, str]:
    """Classify a legacy attempt without hiding policy blocks as failures.

    Cleanup compensation failure after real target observations is not a
    harness crash: the attempt executed. Preserve the cleanup reason for the
    delivery gate instead of mislabeling the terminal as ``HARNESS_FAILED``.
    """
    if observation_receipt_ids:
        if cleanup_failed:
            return "EXECUTED", "CLEANUP_COMPENSATION_FAILED"
        return "EXECUTED", ""
    if cleanup_failed:
        return "HARNESS_FAILED", "CLEANUP_COMPENSATION_FAILED"
    if trace_errors:
        for raw_error in trace_errors:
            block = _governed_write_block_reason(raw_error)
            if not block and str(raw_error or "").startswith("failed_after_retries:"):
                block = _governed_write_block_reason(
                    str(raw_error).split(":", 1)[1]
                )
            if block:
                reason = re.sub(r"[^A-Za-z0-9]+", "_", block).strip("_").upper()
                return (
                    "BLOCKED",
                    reason if reason.startswith("BLOCKED_") else f"BLOCKED_{reason}",
                )
    if skipped_reasons:
        for raw_reason in skipped_reasons:
            reason = re.sub(r"[^A-Za-z0-9]+", "_", _text(raw_reason)).strip("_").upper()
            if reason:
                return (
                    "BLOCKED",
                    reason if reason.startswith("BLOCKED_") else f"BLOCKED_{reason}",
                )
        return "BLOCKED", "LEGACY_EXECUTION_BLOCKED"
    if trace_errors:
        return "HARNESS_FAILED", "LEGACY_EXECUTION_ERROR"
    if trace_present:
        return "BLOCKED", "LEGACY_EXECUTION_BLOCKED"
    return "BLOCKED", "LEGACY_EXECUTION_RECEIPT_MISSING"


def _operational_summary_from_attempt_ledger(
    ledger: dict[str, Any],
) -> dict[str, Any]:
    attempts = [
        row
        for row in _list(_dict(ledger).get("attempts"))
        if isinstance(row, dict)
    ]
    execution_attempts = [
        row
        for row in attempts
        if any(
            _text(stage.get("stage")) == "execution"
            for stage in _list(row.get("stages"))
            if isinstance(stage, dict)
        )
    ]
    receipts = [
        dict(row["operational_receipt"])
        for row in execution_attempts
        if isinstance(row.get("operational_receipt"), dict)
    ]
    summary = aggregate_execution_operational_receipts(receipts)
    missing = [
        _text(row.get("obligation_id"))
        for row in execution_attempts
        if not isinstance(row.get("operational_receipt"), dict)
    ]
    return {
        **summary,
        "complete": not missing and len(receipts) == len(execution_attempts),
        "missing_obligation_ids": missing,
    }


def _legacy_experiment_execution_batch(
    *,
    selected_rows: list[dict[str, Any]],
    execution_results: dict[str, dict[str, Any]],
    normalized_findings: list[dict[str, Any]],
    campaign_id: str,
) -> dict[str, Any]:
    """Project legacy adapter attempts into experiment_execution.results."""

    finding_by_obligation = {
        _text(item.get("obligation_id")): item
        for item in normalized_findings
        if _text(item.get("obligation_id"))
    }
    results: list[dict[str, Any]] = []
    executed_count = 0
    blocked_count = 0
    harness_failure_count = 0
    for row in selected_rows:
        obligation_id = _text(row.get("obligation_id"))
        exec_row = _dict(execution_results.get(obligation_id))
        finding = finding_by_obligation.get(obligation_id)
        status = _text(exec_row.get("status")).upper() or "BLOCKED"
        if status == "EXECUTED":
            executed_count += 1
        elif status == "HARNESS_FAILED":
            harness_failure_count += 1
        elif status == "BLOCKED":
            blocked_count += 1
        operational_receipt = _dict(exec_row.get("operational_receipt"))
        execution_id = _text(exec_row.get("execution_id"))
        experiment_id = _text(row.get("experiment_id"))
        results.append({
            "schema_version": "qualibug.experiment-execution.v1",
            "candidate_id": _text(row.get("candidate_id")),
            "slice_id": _text(row.get("behavior_slice_id")),
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "evidence_id": _text(finding.get("evidence_id")) if finding else "",
            "campaign_id": campaign_id,
            "status": status,
            "reason_code": _text(exec_row.get("reason_code")),
            "detail": "",
            "elapsed_ms": 0,
            "finding": finding if finding and status == "EXECUTED" else None,
            "execution_receipt": {
                **operational_receipt,
                "execution_id": execution_id,
                "status": status,
                "reason_code": _text(exec_row.get("reason_code")),
                "obligation_id": obligation_id,
                "experiment_id": experiment_id,
                "campaign_id": campaign_id,
            },
        })
    return {
        "selected_count": len(selected_rows),
        "scheduled_count": len(selected_rows),
        "executed_count": executed_count,
        "blocked_count": blocked_count,
        "harness_failure_count": harness_failure_count,
        "cleanup_failures": 0,
        "every_experiment_has_receipt": bool(selected_rows),
        "results": results,
    }


def _campaign_object(handle: Any) -> Any:
    campaign = _dict(handle).get("campaign") if isinstance(handle, dict) else handle
    if campaign is None or not _text(getattr(campaign, "campaign_id", "")):
        raise MainlineContractError("mainline_campaign_object_missing")
    return campaign


def _campaign_store(handle: Any) -> Any:
    store = _dict(handle).get("store") if isinstance(handle, dict) else None
    if store is None or not callable(getattr(store, "save", None)):
        raise MainlineContractError("mainline_campaign_store_missing")
    return store


def _api_operations(
    api_spec_text: str,
    *,
    submitted_source_text: str = "",
) -> list[dict[str, Any]]:
    text = _text(api_spec_text)
    if not text:
        raise MainlineContractError("api_spec_text_missing")
    from .universal_api_parser import parse_to_openapi

    operations: list[dict[str, Any]] = []
    source_documents = [("api_spec", text)]
    submitted = _text(submitted_source_text)
    if submitted and submitted != text:
        source_documents.append(("submitted_api_spec", submitted))

    for source_id, source_text in source_documents:
        spec = parse_to_openapi(source_text)
        if not isinstance(spec, dict):
            raise MainlineContractError(
                f"api_spec_parse_result_invalid:{source_id}"
            )
        paths = spec.get("paths")
        if not isinstance(paths, dict):
            raise MainlineContractError(f"api_spec_paths_missing:{source_id}")
        for path, methods in paths.items():
            if not isinstance(methods, dict):
                continue
            for method, raw_operation in methods.items():
                normalized_method = _text(method).upper()
                if normalized_method not in {
                    "GET",
                    "POST",
                    "PUT",
                    "PATCH",
                    "DELETE",
                }:
                    continue
                operation = _dict(raw_operation)
                operations.append({
                    "method": normalized_method,
                    "path": _text(path),
                    "operation_id": _text(operation.get("operationId"))
                    or f"{normalized_method.lower()}:{_text(path)}",
                    "source_id": source_id,
                    "summary": _text(operation.get("summary")),
                    "description": _text(operation.get("description")),
                    "tags": list(operation.get("tags") or []),
                    "side_effect_class": (
                        "write"
                        if normalized_method in {"POST", "PUT", "PATCH", "DELETE"}
                        else "read"
                    ),
                    "parameters": list(operation.get("parameters") or []),
                    "request_schema": _dict(operation.get("requestBody")),
                    "response_schema": _dict(operation.get("responses")),
                })
    if not operations:
        for match in re.finditer(
            r"(?im)^(?:\s*#{1,6}\s*)?(GET|POST|PUT|PATCH|DELETE)\s+(/\S+)",
            submitted or text,
        ):
            method = match.group(1).upper()
            path = match.group(2).strip().rstrip("`").rstrip(",").rstrip(")")
            operations.append({
                "method": method,
                "path": path,
                "operation_id": f"{method.lower()}:{path}",
                "source_id": "api_spec",
                "summary": "",
                "description": "",
                "tags": [],
                "side_effect_class": (
                    "write" if method in {"POST", "PUT", "PATCH", "DELETE"} else "read"
                ),
                "parameters": [],
                "request_schema": {},
                "response_schema": {},
            })
    if not operations:
        raise MainlineContractError("api_spec_operations_missing")
    return operations


def _runtime_actors(root: Path, project: str, context: dict[str, Any]) -> list[dict[str, Any]]:
    actors: list[dict[str, Any]] = []
    accounts_path = root / "platform_inputs" / project / "test_accounts.json"
    payload: Any = {}
    if accounts_path.exists():
        try:
            payload = json.loads(accounts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise MainlineContractError(
                f"test_actor_catalog_invalid:{type(exc).__name__}"
            ) from exc
    rows: list[Any]
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        raw_rows = payload.get("accounts") or payload.get("actors") or payload.get("users")
        if raw_rows is None:
            rows = [
                {**value, "account_ref": key}
                for key, value in payload.items()
                if isinstance(value, dict)
                and key not in {"schema", "schema_version", "meta"}
            ]
        elif isinstance(raw_rows, list):
            rows = raw_rows
        else:
            raise MainlineContractError("test_actor_catalog_rows_invalid")
    else:
        raise MainlineContractError("test_actor_catalog_root_invalid")
    for row in rows:
        if not isinstance(row, dict):
            raise MainlineContractError("test_actor_catalog_row_invalid")
        role = _text(row.get("role") or row.get("name") or row.get("id"))
        if not role:
            raise MainlineContractError("test_actor_role_missing")
        account_ref = _text(
            row.get("account_ref")
            or row.get("email")
            or row.get("username")
            or row.get("id")
            or role
        )
        actors.append({
            "role": role,
            "account_ref": account_ref,
            "tenant": row.get("tenant") or row.get("scope"),
            "secret_ref": f"secret_ref:test_accounts:{account_ref}",
            "status": _text(row.get("status") or "active"),
        })
    scenario_actor = _dict(_dict(context.get("runtime_scenario_contract")).get("actor"))
    declared_role = _text(
        scenario_actor.get("role")
        or scenario_actor.get("name")
        or scenario_actor.get("id")
    )
    if declared_role and not any(_text(row.get("role")) == declared_role for row in actors):
        actors.append({
            "role": declared_role,
            "secret_ref": f"secret_ref:context:{declared_role}",
            "status": "active",
        })
    return actors


def _contract(inputs: DiscoveryMainlineInputs, campaign_id: str) -> MainlineRunContract:
    context = inputs.campaign_context
    return build_mainline_run_contract(
        mainline_authority=_text(context.get("mainline_authority")),
        run_id=_text(context.get("run_id")),
        campaign_id=campaign_id,
        target_id=_text(context.get("target_id")),
        environment_id=_text(context.get("environment_id")),
        policy_version=_text(context.get("policy_version")),
        evaluation_mode=_text(context.get("evaluation_mode")),
    )


def build_discovery_plan(
    inputs: DiscoveryMainlineInputs,
    campaign_handle: Any,
) -> DiscoveryPlanningBundle:
    """Compile one immutable Behavior IR -> Obligation -> Experiment plan."""

    campaign = _campaign_object(campaign_handle)
    contract = _contract(inputs, _text(campaign.campaign_id))
    if contract["mainline_authority"] == "legacy_champion":
        # The frozen champion has its own behavior-slice planner. Requiring the
        # unpromoted candidate compiler here would let candidate parse/compile
        # failures block the selected champion before its runner starts.
        return DiscoveryPlanningBundle(
            mainline_run=contract,
            behavior_ir={},
            obligations={"obligations": []},
            experiments={},
        )
    from .enterprise_knowledge_center import build_enterprise_business_knowledge_asset

    asset = build_enterprise_business_knowledge_asset(inputs.project, inputs.root)
    operations = _api_operations(
        inputs.api_spec_text,
        submitted_source_text=_text(
            inputs.campaign_context.get("_source_verification_text")
        ),
    )
    behavior_ir = build_behavior_ir_from_knowledge_asset(
        asset,
        project_id=inputs.project,
        source_snapshot_hash=_text(
            _dict(inputs.campaign_context.get("source_manifest")).get("source_hash")
        ),
        api_operations=operations,
        runtime_actors=_runtime_actors(
            inputs.root,
            inputs.project,
            inputs.campaign_context,
        ),
    )
    obligation_pack = compile_obligations_from_behavior_ir(behavior_ir)
    obligations = [
        dict(row)
        for row in _list(obligation_pack.get("obligations"))
        if isinstance(row, dict)
    ]
    environment_type = _text(
        inputs.campaign_context.get("environment_type")
        or inputs.campaign_context.get("environment_kind")
    ).lower()
    experiment_pack = compile_experiments(
        obligations,
        behavior_ir=behavior_ir,
        environment_type=environment_type,
        policy_version=_text(inputs.campaign_context.get("policy_version")),
    )
    experiment_pack = attach_fixture_dag_to_experiments(
        experiment_pack,
        behavior_ir=behavior_ir,
    )
    all_experiments = [
        dict(row)
        for row in (
            _list(experiment_pack.get("experiments"))
            + _list(experiment_pack.get("blocked_experiments"))
        )
        if isinstance(row, dict)
    ]
    by_obligation = {
        _text(row.get("obligation_id")): row
        for row in all_experiments
        if _text(row.get("obligation_id"))
    }
    budget = int(getattr(campaign, "slice_budget", 0) or 0)
    if budget <= 0:
        raise MainlineContractError("obligation_budget_invalid")
    obligation_plan = plan_obligation_round(
        obligations,
        experiments_by_obligation=by_obligation,
        budget=budget,
    )
    return DiscoveryPlanningBundle(
        mainline_run=contract,
        behavior_ir=behavior_ir,
        obligations={**obligation_pack, "obligations": obligations},
        experiments={
            **experiment_pack,
            "all_experiments": all_experiments,
            "by_obligation": by_obligation,
            "obligation_plan": obligation_plan,
            "runtime_contract": dict(
                _dict(inputs.campaign_context.get("_runtime_contract"))
            ),
            "knowledge_asset_id": _text(asset.get("asset_id")),
        },
    )


def _selected_rows(plan: DiscoveryPlanningBundle) -> list[dict[str, Any]]:
    experiments = _dict(plan.experiments.get("by_obligation"))
    rows: list[dict[str, Any]] = []
    for obligation in _list(plan.obligations.get("obligations")):
        if not isinstance(obligation, dict):
            continue
        obligation_id = _text(obligation.get("obligation_id"))
        experiment = _dict(experiments.get(obligation_id))
        rows.append({
            **obligation,
            "candidate_id": _text(obligation.get("candidate_id")) or obligation_id,
            "experiment_id": _text(experiment.get("experiment_id")),
            "adapter": "http_api",
            "planning_round": 1,
            "operation_refs": list(obligation.get("required_operations") or []),
            "actor_refs": list(obligation.get("required_actors") or []),
            "behavior_ir_refs": list(obligation.get("relation_refs") or []),
        })
    return rows


def _manual_terminal_receipts(
    *,
    selected_rows: list[dict[str, Any]],
    plan: DiscoveryPlanningBundle,
    runtime_contract: dict[str, Any],
    compile_results: dict[str, dict[str, Any]],
    execution_results: dict[str, dict[str, Any]],
) -> None:
    experiments = _dict(plan.experiments.get("by_obligation"))
    obligation_plan = _dict(plan.experiments.get("obligation_plan"))
    scheduled_ids = {
        _text(row.get("obligation_id"))
        for row in _list(obligation_plan.get("selected"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    pending_ids = {
        _text(row.get("obligation_id"))
        for row in _list(obligation_plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    runtime_approved = (
        _text(runtime_contract.get("status")) == "approved"
        and bool(_text(runtime_contract.get("approved_base_url")))
    )
    for row in selected_rows:
        obligation_id = _text(row.get("obligation_id"))
        if obligation_id in compile_results:
            continue
        experiment = _dict(experiments.get(obligation_id))
        compile_receipt = _dict(experiment.get("compile_receipt"))
        compile_status = _text(compile_receipt.get("status")).upper()
        if compile_status == "BLOCKED":
            compile_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": _text(compile_receipt.get("reason_code"))
                or "BLOCKED_COMPILE",
                "detail": _text(
                    compile_receipt.get("detail")
                    or compile_receipt.get("reason_detail")
                ),
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in pending_ids:
            compile_results[obligation_id] = {
                "status": "DEFERRED",
                "reason_code": "OBLIGATION_BUDGET_REACHED",
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
        elif obligation_id in scheduled_ids and not runtime_approved:
            compile_results[obligation_id] = {
                "status": "COMPILED",
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
            execution_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": "BLOCKED_RUNTIME_TARGET",
                "experiment_id": _text(experiment.get("experiment_id")),
                "cost_coverage_status": "UNKNOWN",
            }
        else:
            raise MainlineContractError(
                f"obligation_terminal_receipt_missing:{obligation_id}"
            )


def _authority_findings(
    *,
    raw_findings: list[dict[str, Any]],
    gate_results: dict[str, dict[str, Any]],
    contract: MainlineRunContract,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    deliverable: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    shadow: list[dict[str, Any]] = []
    findings_by_id: dict[str, dict[str, Any]] = {}
    for item in raw_findings:
        finding_id = _text(item.get("finding_id") or item.get("id"))
        if not finding_id:
            raise MainlineContractError("experiment_finding_id_missing")
        row = {
            **item,
            "id": finding_id,
            "finding_id": finding_id,
            "mainline_run": {
                "contract_fingerprint": contract["contract_fingerprint"],
            },
        }
        findings_by_id[finding_id] = row
        gate = _dict(gate_results.get(_text(row.get("obligation_id"))))
        if not gate:
            raise MainlineContractError(
                f"finding_gate_receipt_missing:{finding_id}"
            )
        if not contract["customer_outputs_published"]:
            shadow.append({
                **row,
                "finding_class": "shadow",
                "shadow_origin": "delivery_gate",
                "semantic_delivery_gate_status": _text(
                    gate.get("semantic_status") or gate.get("status")
                ).upper(),
                "delivery_gate_receipt_id": _text(
                    gate.get("gate_receipt_id") or gate.get("receipt_id")
                ),
            })
        elif _text(gate.get("status")).upper() == "DELIVERABLE":
            deliverable.append(row)
        else:
            candidates.append({
                **row,
                "gate_passed": False,
                "customer_delivery_status": "candidate",
                "customer_delivery_gate_reasons": list(
                    gate.get("reason_codes") or [_text(gate.get("reason_code"))]
                ),
            })
    for gate in gate_results.values():
        if _text(gate.get("status")).upper() != "DELIVERABLE":
            continue
        finding_id = _text(
            _dict(gate.get("identity")).get("finding_id")
            or gate.get("finding_id")
        )
        if finding_id not in findings_by_id:
            raise MainlineContractError(
                f"deliverable_gate_finding_missing:{finding_id or 'MISSING'}"
            )
    return deliverable, candidates, shadow


def _project_gate_results_for_authority(
    *,
    gate_results: dict[str, dict[str, Any]],
    contract: MainlineRunContract,
) -> dict[str, dict[str, Any]]:
    """Project semantic gates into the selected authority's terminal scope."""
    projected = {
        _text(obligation_id): dict(receipt)
        for obligation_id, receipt in gate_results.items()
        if _text(obligation_id) and isinstance(receipt, dict)
    }
    # Semantic Gate receipts are immutable. Shadow publication is projected by
    # `_authority_findings`; it must never rewrite a Gate status or fingerprint.
    return projected


def _finalize_campaign(handle: Any, ledger: dict[str, Any]) -> dict[str, Any]:
    campaign = _campaign_object(handle)
    campaign.record_obligation_attempt_ledger(ledger)
    _campaign_store(handle).save(campaign)
    return {
        **campaign.public_contract(),
        "campaign_mode": _text(_dict(handle).get("mode")),
    }


def run_experiment_candidate(
    inputs: DiscoveryMainlineInputs,
    campaign_handle: Any,
    plan: DiscoveryPlanningBundle,
) -> dict[str, Any]:
    """Execute only the obligation/experiment authority selected by the plan."""

    started = time.time()
    runtime_contract = dict(_dict(plan.experiments.get("runtime_contract")))
    obligation_plan = _dict(plan.experiments.get("obligation_plan"))
    scheduled = [
        dict(row)
        for row in _list(obligation_plan.get("selected"))
        if isinstance(row, dict)
    ]
    runtime_approved = (
        _text(runtime_contract.get("status")) == "approved"
        and bool(_text(runtime_contract.get("approved_base_url")))
    )
    if runtime_approved and scheduled:
        batch = execute_selected_experiments(
            scheduled,
            experiments_by_obligation=dict(
                _dict(plan.experiments.get("by_obligation"))
            ),
            behavior_ir=plan.behavior_ir,
            root=inputs.root,
            project=inputs.project,
            base_url=_text(runtime_contract.get("approved_base_url")),
            runtime_contract=runtime_contract,
            mainline_run=plan.mainline_run,
            campaign_id=plan.mainline_run["campaign_id"],
        )
    else:
        batch = {
            "selected_count": 0,
            "executed_count": 0,
            "blocked_count": 0,
            "harness_failure_count": 0,
            "cleanup_failures": 0,
            "findings": [],
            "results": [],
            "compile_results": {},
            "execution_results": {},
            "gate_results": {},
            "every_experiment_has_receipt": True,
        }
    compile_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("compile_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    execution_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("execution_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    gate_results = {
        _text(key): dict(value)
        for key, value in _dict(batch.get("gate_results")).items()
        if _text(key) and isinstance(value, dict)
    }
    gate_results = _project_gate_results_for_authority(
        gate_results=gate_results,
        contract=plan.mainline_run,
    )
    selected_rows = _selected_rows(plan)
    _manual_terminal_receipts(
        selected_rows=selected_rows,
        plan=plan,
        runtime_contract=runtime_contract,
        compile_results=compile_results,
        execution_results=execution_results,
    )
    ledger = build_obligation_attempt_ledger(
        mainline_run=plan.mainline_run,
        selected=selected_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    operational_summary = _operational_summary_from_attempt_ledger(ledger)
    deliverable, candidates, shadow = _authority_findings(
        raw_findings=[
            dict(row)
            for row in _list(batch.get("findings"))
            if isinstance(row, dict)
        ],
        gate_results=gate_results,
        contract=plan.mainline_run,
    )
    authority_occurrences = (
        deliverable
        if plan.mainline_run["customer_outputs_published"]
        else formal_customer_deliverable_findings(
            shadow,
            obligation_attempt_ledger=ledger,
        )
    )
    canonical_registry = build_canonical_defect_registry(
        mainline_run=plan.mainline_run,
        deliverable_occurrences=authority_occurrences,
        obligation_attempt_ledger=ledger,
    )
    canonical_findings = canonical_representative_findings(
        canonical_registry,
        deliverable_occurrences=authority_occurrences,
    )
    formal_delivery_authority = build_formal_delivery_authority_receipt(
        mainline_run=plan.mainline_run,
        findings=authority_occurrences,
        obligation_attempt_ledger=ledger,
    )
    formal = build_formal_count_projection(
        findings=authority_occurrences,
        candidate_findings=candidates,
        obligation_attempt_ledger=ledger,
        mainline_run=plan.mainline_run,
        canonical_defect_registry=canonical_registry,
    )
    occurrence_ids = list(formal["delivery_occurrence_finding_ids"])
    canonical_ids = list(formal["canonical_defect_ids"])
    defect_identity_consistency = build_defect_identity_consistency(
        occurrence_scopes={
            "delivery_gate_ids": validated_delivery_gate_finding_ids(ledger),
            "registry_occurrence_ids": list(
                canonical_registry["delivery_occurrence_finding_ids"]
            ),
            "formal_projection_occurrence_ids": occurrence_ids,
        },
        canonical_scopes={
            "canonical_registry_ids": list(
                canonical_registry["canonical_defect_ids"]
            ),
            "formal_projection_ids": canonical_ids,
            "product_projection_ids": sorted(
                _text(item.get("canonical_defect_id"))
                for item in canonical_findings
                if _text(item.get("canonical_defect_id"))
            ),
        },
    )
    result: dict[str, Any] = {
        "schema_version": RUNTIME_SCHEMA,
        "v12_version": "3.0-mainline",
        "enabled": True,
        "mainline_run": dict(plan.mainline_run),
        "runtime_contract": runtime_contract,
        "campaign": _finalize_campaign(campaign_handle, ledger),
        "behavior_ir": dict(plan.behavior_ir),
        "test_obligations": dict(plan.obligations),
        "experiment_compile": {
            key: value
            for key, value in plan.experiments.items()
            if key not in {"by_obligation", "runtime_contract"}
        },
        "obligation_plan": dict(obligation_plan),
        "experiment_execution": {
            "selected_count": len(selected_rows),
            "scheduled_count": len(scheduled),
            "executed_count": int(batch.get("executed_count") or 0),
            "blocked_count": int(batch.get("blocked_count") or 0),
            "harness_failure_count": int(batch.get("harness_failure_count") or 0),
            "cleanup_failures": int(batch.get("cleanup_failures") or 0),
            "every_experiment_has_receipt": bool(ledger.get("complete")),
            "operational_receipt_summary": operational_summary,
            "results": list(batch.get("results") or []),
        },
        "operational_receipt_summary": operational_summary,
        "obligation_attempt_ledger": ledger,
        "canonical_defect_registry": canonical_registry,
        "formal_delivery_authority": formal_delivery_authority,
        "formal_count_projection": formal,
        "defect_identity_consistency": defect_identity_consistency,
        "delivery_occurrences": authority_occurrences,
        "findings": (
            canonical_findings
            if plan.mainline_run["customer_outputs_published"]
            else []
        ),
        "evaluator_canonical_findings": (
            canonical_findings
            if plan.mainline_run["private_evaluator_observation_allowed"]
            else []
        ),
        "candidate_findings": candidates,
        "shadow_findings": shadow,
        "external_findings": [],
        "ui_findings": [],
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "behavior_slice_ledger": {},
        "phases": {
            "behavior_ir": {
                "status": "completed",
                "operation_count": len(_list(plan.behavior_ir.get("operations"))),
            },
            "obligation_generation": {
                "status": "completed",
                "selected": len(selected_rows),
            },
            "execution": {
                "status": "completed",
                "executed": int(batch.get("executed_count") or 0),
                "observed_http_request_count": int(
                    operational_summary.get("observed_http_request_count") or 0
                ),
                "production_http_requests": int(
                    operational_summary.get("production_http_requests") or 0
                ),
                "scenario_attempts": int(
                    operational_summary.get("scenario_attempts") or 0
                ),
                "accepted_write_count": int(
                    operational_summary.get("accepted_write_count") or 0
                ),
                "operational_receipt_complete": bool(
                    operational_summary.get("complete")
                ),
                "blocked": sum(
                    1
                    for row in ledger["attempts"]
                    if _text(row.get("terminal_status")).upper()
                    in {"BLOCKED", "DEFERRED"}
                ),
            },
            "oracle": {
                "status": "completed",
                "total_evaluated": len(gate_results),
                "violations_found": len(deliverable),
            },
        },
        "auto_har": {
            "status": "receipt_backed",
            "entry_count": int(batch.get("executed_count") or 0),
        },
        "total_duration_ms": int((time.time() - started) * 1000),
    }
    result["discovery_funnel"] = build_funnel(result)
    return result


def adapt_legacy_champion_result(
    inputs: DiscoveryMainlineInputs,
    campaign_handle: Any,
    plan: DiscoveryPlanningBundle,
    legacy_result: dict[str, Any],
) -> dict[str, Any]:
    """Project the selected legacy champion into the common attempt authority.

    This is an adapter for an explicitly selected authority, not an error-time
    fallback.  It derives terminal receipts only from legacy behavior-slice
    selections, redacted execution traces, and runtime-backed findings.
    """

    if not isinstance(legacy_result, dict):
        raise MainlineContractError("legacy_champion_result_invalid")
    if _text(legacy_result.get("error")):
        raise RuntimeError(f"legacy_champion_failed:{_text(legacy_result.get('error'))}")

    contract = plan.mainline_run
    campaign_id = _text(_dict(legacy_result.get("campaign")).get("campaign_id"))
    ledger_campaign_id = _text(
        _dict(legacy_result.get("behavior_slice_ledger")).get("campaign_id")
    )
    observed_campaign_ids = {
        value for value in (campaign_id, ledger_campaign_id) if value
    }
    if not observed_campaign_ids:
        raise MainlineContractError("legacy_champion_campaign_identity_missing")
    if observed_campaign_ids != {contract["campaign_id"]}:
        raise MainlineContractError("legacy_champion_campaign_identity_mismatch")

    behavior_slices = {
        _text(row.get("slice_id") or row.get("behavior_slice_id")): dict(row)
        for row in _list(legacy_result.get("behavior_slices"))
        if isinstance(row, dict)
        and _text(row.get("slice_id") or row.get("behavior_slice_id"))
    }
    traces = [
        dict(row)
        for row in _list(legacy_result.get("execution_trace_summaries"))
        if isinstance(row, dict)
    ]
    raw_findings = [
        dict(row)
        for row in _list(legacy_result.get("findings"))
        if isinstance(row, dict)
    ]
    selected_slice_ids = [
        _text(value)
        for value in _list(
            _dict(legacy_result.get("behavior_slice_ledger")).get(
                "selected_slice_ids"
            )
        )
        if _text(value)
    ]
    if len(selected_slice_ids) != len(set(selected_slice_ids)):
        raise MainlineContractError("legacy_selected_slice_identity_duplicate")

    def stable_id(prefix: str, *parts: Any) -> str:
        canonical = json.dumps(
            parts,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return f"{prefix}_{hashlib.sha256(canonical.encode('utf-8')).hexdigest()[:32]}"

    def fingerprint(value: Any) -> str:
        canonical = json.dumps(
            value,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            default=str,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def trace_view(trace: dict[str, Any]) -> dict[str, Any]:
        scenario = _dict(trace.get("scenario"))
        execution = _dict(trace.get("execution_trace"))
        sandbox = _dict(execution.get("sandbox_write"))
        source_operational = _dict(execution.get("operational_receipt"))
        redacted_steps = [
            {
                "method": _text(step.get("method")).upper(),
                "path": _text(step.get("path")),
                "status": step.get("status"),
                "skipped_reason": _text(step.get("skipped_reason")),
            }
            for step in _list(execution.get("steps"))
            if isinstance(step, dict)
        ]
        if not source_operational:
            # Legacy redacted traces sometimes carry sandbox audit counters without
            # an embedded operational receipt (common on read isolation probes that
            # still resolve/bind entities through governed writes). Synthesize a
            # fingerprint-free count receipt from the redacted steps so the common
            # attempt ledger remains complete instead of aborting the whole run.
            cleanup_status = _text(_dict(sandbox.get("cleanup")).get("status")).upper()
            audit_count = int(sandbox.get("audit_record_count") or 0)
            cleanup_failed = cleanup_status in {
                "FAILED",
                "INCOMPLETE",
                "CLEANUP_INCOMPLETE",
                "CLEANUP_NOT_SUCCEEDED",
                "NOT_REVERSIBLE",
            }
            http_count = sum(
                1
                for step in redacted_steps
                if step["method"] and step["path"] and not step["skipped_reason"]
            )
            source_operational = {
                "scenario_attempt_count": 1,
                "http_request_attempt_count": http_count,
                "production_http_request_count": 0,
                "accepted_write_count": audit_count,
                "accepted_non_cleanup_write_count": audit_count,
                "accepted_cleanup_write_count": 0,
                "cleanup_attempted_count": audit_count if audit_count else 0,
                "cleanup_completed_count": (
                    0 if cleanup_failed else (audit_count if audit_count else 0)
                ),
                "cleanup_failure_count": int(cleanup_failed and audit_count > 0),
            }
        return {
            "scenario_id": _text(scenario.get("id") or execution.get("scenario_id")),
            "behavior_slice_id": _text(scenario.get("behavior_slice_id")),
            "discovery_round": int(scenario.get("discovery_round") or 0),
            "actor_role": _text(execution.get("actor_role")),
            "steps": redacted_steps,
            "errors": [_text(value) for value in _list(execution.get("errors")) if _text(value)],
            "precondition_not_met_count": len(
                _list(execution.get("precondition_not_met"))
            ),
            "sandbox_write": {
                "status": _text(sandbox.get("status")),
                "cleanup_status": _text(
                    _dict(sandbox.get("cleanup")).get(
                        "status"
                    )
                ),
                "audit_record_count": int(
                    sandbox.get("audit_record_count")
                    or 0
                ),
            },
            "operational_receipt": source_operational,
            "oracle_results": [
                {
                    "oracle_name": _text(row.get("oracle_name") or row.get("name")),
                    "passed": row.get("passed"),
                    "verdict": _text(row.get("verdict")),
                }
                for row in _list(trace.get("oracle_results"))
                if isinstance(row, dict)
            ],
        }

    def finding_view(finding: dict[str, Any]) -> dict[str, Any]:
        raw = _dict(finding.get("raw_evidence"))
        request = _dict(raw.get("request_raw"))
        response = _dict(raw.get("response_raw"))
        replay = _dict(finding.get("reproduction"))
        har = _dict(replay.get("har_evidence"))
        return {
            "title": _text(finding.get("title")),
            "category": _text(finding.get("category")),
            "severity": _text(finding.get("severity")),
            "behavior_slice_id": _text(
                finding.get("behavior_slice_id") or finding.get("slice_id")
            ),
            "expected": _text(finding.get("expected")),
            "actual": _text(finding.get("actual")),
            "timestamp": _text(finding.get("timestamp") or raw.get("timestamp")),
            "method": _text(replay.get("method") or request.get("method")).upper(),
            "path": _text(replay.get("path") or request.get("path")),
            "status_code": response.get("status_code") or har.get("status_code"),
            "has_real_evidence": bool(raw.get("has_real_evidence") or har),
            "execution_status": _text(finding.get("execution_status")),
            "confirmation_status": _text(finding.get("confirmation_status")),
            "bug_status": _text(finding.get("bug_status")),
            "gate_passed": bool(finding.get("gate_passed")),
        }

    trace_views = [trace_view(row) for row in traces]
    used_trace_indexes: set[int] = set()

    def matching_trace_index(finding: dict[str, Any]) -> int | None:
        raw_trace = _dict(_dict(finding.get("raw_evidence")).get("execution_trace"))
        scenario_id = _text(finding.get("scenario_id") or raw_trace.get("scenario_id"))
        slice_id = _text(finding.get("behavior_slice_id") or finding.get("slice_id"))
        preferred = [
            index
            for index, row in enumerate(trace_views)
            if scenario_id and row["scenario_id"] == scenario_id
        ]
        if not preferred:
            preferred = [
                index
                for index, row in enumerate(trace_views)
                if slice_id and row["behavior_slice_id"] == slice_id
            ]
        if not preferred:
            return None
        return next(
            (index for index in preferred if index not in used_trace_indexes),
            None,
        )

    units: list[dict[str, Any]] = []
    represented_slices: set[str] = set()
    for finding_index, finding in enumerate(raw_findings):
        trace_index = matching_trace_index(finding)
        if trace_index is not None:
            used_trace_indexes.add(trace_index)
        slice_id = _text(finding.get("behavior_slice_id") or finding.get("slice_id"))
        if not slice_id and trace_index is not None:
            slice_id = trace_views[trace_index]["behavior_slice_id"]
        if not slice_id:
            slice_id = stable_id(
                "legacy_slice",
                contract["campaign_id"],
                "finding",
                finding_index,
                finding_view(finding),
            )
        represented_slices.add(slice_id)
        units.append({
            "kind": "finding",
            "index": finding_index,
            "slice_id": slice_id,
            "finding": finding,
            "trace_index": trace_index,
        })
    for trace_index, trace in enumerate(traces):
        if trace_index in used_trace_indexes:
            continue
        view = trace_views[trace_index]
        slice_id = view["behavior_slice_id"] or stable_id(
            "legacy_slice",
            contract["campaign_id"],
            "trace",
            trace_index,
            view["scenario_id"],
        )
        represented_slices.add(slice_id)
        units.append({
            "kind": "trace",
            "index": trace_index,
            "slice_id": slice_id,
            "finding": None,
            "trace_index": trace_index,
        })
    for slice_index, slice_id in enumerate(selected_slice_ids):
        if slice_id in represented_slices:
            continue
        units.append({
            "kind": "selected_slice",
            "index": slice_index,
            "slice_id": slice_id,
            "finding": None,
            "trace_index": None,
        })

    selected_rows: list[dict[str, Any]] = []
    compile_results: dict[str, dict[str, Any]] = {}
    execution_results: dict[str, dict[str, Any]] = {}
    gate_results: dict[str, dict[str, Any]] = {}
    normalized_findings: list[dict[str, Any]] = []
    finding_terminal: dict[str, tuple[str, list[str]]] = {}

    for unit_index, unit in enumerate(units):
        raw_finding = unit["finding"]
        trace_index = unit["trace_index"]
        trace = traces[trace_index] if trace_index is not None else None
        view = trace_views[trace_index] if trace_index is not None else {}
        slice_id = unit["slice_id"]
        finding_receipt_view = finding_view(raw_finding) if raw_finding else {}
        unit_material = {
            "kind": unit["kind"],
            "index": unit["index"],
            "slice_id": slice_id,
            "trace": view,
            "finding": finding_receipt_view,
        }
        candidate_id = stable_id(
            "candidate", contract["campaign_id"], unit_material
        )
        obligation_id = stable_id(
            "obligation", contract["campaign_id"], unit_material
        )
        experiment_id = stable_id("experiment", obligation_id)
        scenario_id = _text(_dict(view).get("scenario_id"))
        actor_role = _text(_dict(view).get("actor_role"))
        discovery_round = int(_dict(view).get("discovery_round") or 0)
        # Isolation scenarios commonly emit one TenantIsolationOracle finding and
        # one PermissionOracle finding for the same scenario_id. Execution / ops
        # receipt identity must stay unique per adapter unit or aggregation fails
        # with operational_receipt_id_duplicate and the whole mainline collapses.
        execution_id = stable_id(
            "execution",
            contract["campaign_id"],
            {
                "scenario_id": scenario_id,
                "actor_role": actor_role,
                "discovery_round": discovery_round,
                "obligation_id": obligation_id,
                "unit_kind": unit["kind"],
                "unit_index": unit["index"],
            },
        )
        input_fingerprint = fingerprint({
            "campaign_id": contract["campaign_id"],
            "slice_id": slice_id,
            "scenario_id": scenario_id,
            "actor_role": actor_role,
            "discovery_round": discovery_round,
            "kind": unit["kind"],
        })

        slice_row = _dict(behavior_slices.get(slice_id))
        source_refs = [
            dict(row)
            for row in (
                _list(raw_finding.get("source_refs")) if raw_finding else []
            )
            if isinstance(row, dict)
        ] or [
            dict(row)
            for row in _list(slice_row.get("source_refs"))
            if isinstance(row, dict)
        ]
        steps = _list(_dict(view).get("steps"))
        operation_refs = [
            _text(value)
            for value in _list(slice_row.get("endpoints"))
            if _text(value)
        ]
        operation_refs.extend(
            f"{_text(step.get('method')).upper()} {_text(step.get('path'))}".strip()
            for step in steps
            if isinstance(step, dict)
            and (_text(step.get("method")) or _text(step.get("path")))
        )
        actor_refs = [
            value
            for value in (
                actor_role,
                _text(_dict(_dict(raw_finding or {}).get("evidence")).get("actor")),
            )
            if value
        ]
        selected_rows.append({
            "candidate_id": candidate_id,
            "source_refs": source_refs,
            "risk_family": _text(
                _dict(raw_finding or {}).get("category") or slice_row.get("kind")
            ),
            "operation_refs": list(dict.fromkeys(operation_refs)),
            "actor_refs": list(dict.fromkeys(actor_refs)),
            "adapter": "legacy_champion_receipt_adapter",
            "planning_round": discovery_round or 1,
            "behavior_slice_id": slice_id,
            "behavior_ir_refs": [],
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "input_fingerprint": input_fingerprint,
        })

        has_materialized_scenario = trace is not None or raw_finding is not None
        if not has_materialized_scenario:
            compile_results[obligation_id] = {
                "status": "BLOCKED",
                "reason_code": "LEGACY_SCENARIO_RECEIPT_MISSING",
                "experiment_id": experiment_id,
                "receipt_id": stable_id("compile", obligation_id, "blocked"),
                "input_fingerprint": input_fingerprint,
                "cost_coverage_status": "UNKNOWN",
            }
            continue
        compile_results[obligation_id] = {
            "status": "COMPILED",
            "reason_code": "",
            "experiment_id": experiment_id,
            "receipt_id": stable_id("compile", obligation_id),
            "input_fingerprint": input_fingerprint,
            "cost_coverage_status": "UNKNOWN",
        }

        observation_receipt_ids: list[str] = []
        for step_index, step in enumerate(steps):
            if not isinstance(step, dict):
                continue
            try:
                status_code = int(step.get("status") or 0)
            except (TypeError, ValueError):
                status_code = 0
            if status_code <= 0:
                continue
            observation_receipt_ids.append(
                stable_id(
                    "observation",
                    execution_id,
                    step_index,
                    _text(step.get("method")).upper(),
                    _text(step.get("path")),
                    status_code,
                )
            )
        if raw_finding and not observation_receipt_ids:
            raw = _dict(raw_finding.get("raw_evidence"))
            request = _dict(raw.get("request_raw"))
            response = _dict(raw.get("response_raw"))
            replay = _dict(raw_finding.get("reproduction"))
            har = _dict(replay.get("har_evidence"))
            method = _text(replay.get("method") or request.get("method")).upper()
            path = _text(replay.get("path") or request.get("path"))
            has_response = bool(
                response.get("status_code")
                or response.get("body")
                or har.get("status_code")
                or har.get("response_body")
                or _dict(raw.get("db_snapshot")).get("status") == "captured"
            )
            if method and path and has_response and bool(raw.get("has_real_evidence") or har):
                observation_receipt_ids.append(
                    stable_id(
                        "observation",
                        execution_id,
                        method,
                        path,
                        response.get("status_code") or har.get("status_code"),
                    )
                )

        sandbox = _dict(_dict(view).get("sandbox_write"))
        cleanup_status = _text(sandbox.get("cleanup_status")).upper()
        cleanup_failed = cleanup_status in {
            "FAILED",
            "INCOMPLETE",
            "CLEANUP_INCOMPLETE",
            "CLEANUP_NOT_SUCCEEDED",
            "NOT_REVERSIBLE",
        }
        trace_errors = _list(_dict(view).get("errors"))
        skipped_reasons = [
            _text(step.get("skipped_reason"))
            for step in steps
            if isinstance(step, dict)
            and _text(step.get("skipped_reason"))
        ]
        if int(_dict(view).get("precondition_not_met_count") or 0) > 0:
            skipped_reasons.append("PRECONDITION_NOT_MET")
        execution_status, execution_reason = _legacy_execution_terminal(
            cleanup_failed=cleanup_failed,
            observation_receipt_ids=observation_receipt_ids,
            trace_errors=trace_errors,
            skipped_reasons=skipped_reasons,
            trace_present=trace is not None,
        )

        source_operational = _dict(_dict(view).get("operational_receipt"))
        if trace is not None and not source_operational:
            raise MainlineContractError(
                f"legacy_operational_receipt_missing:{scenario_id or obligation_id}"
            )
        if trace is None and raw_finding is not None:
            source_operational = {
                "scenario_attempt_count": 1,
                "http_request_attempt_count": int(bool(observation_receipt_ids)),
                "production_http_request_count": 0,
                "accepted_write_count": 0,
                "accepted_non_cleanup_write_count": 0,
                "accepted_cleanup_write_count": 0,
                "cleanup_attempted_count": 0,
                "cleanup_completed_count": 0,
                "cleanup_failure_count": 0,
            }
        cleanup_failure_count = int(
            source_operational.get("cleanup_failure_count") or 0
        )
        cleanup_attempted_count = int(
            source_operational.get("cleanup_attempted_count") or 0
        )
        cleanup_completed_count = int(
            source_operational.get("cleanup_completed_count") or 0
        )
        operational_receipt = build_execution_operational_receipt_from_counts(
            receipt_id=stable_id("operational", execution_id),
            execution_status=execution_status,
            scenario_attempt_count=int(
                source_operational.get("scenario_attempt_count") or 1
            ),
            http_request_attempt_count=int(
                source_operational.get("http_request_attempt_count") or 0
            ),
            production_http_request_count=int(
                source_operational.get("production_http_request_count") or 0
            ),
            accepted_non_cleanup_write_count=int(
                source_operational.get("accepted_non_cleanup_write_count") or 0
            ),
            accepted_cleanup_write_count=int(
                source_operational.get("accepted_cleanup_write_count") or 0
            ),
            cleanup_status=(
                "FAILED"
                if cleanup_failure_count
                else "COMPLETED"
                if cleanup_attempted_count
                else "NOT_REQUIRED"
            ),
            cleanup_attempted_count=cleanup_attempted_count,
            cleanup_completed_count=cleanup_completed_count,
            cleanup_failure_count=cleanup_failure_count,
        )

        oracle_rows = _list(_dict(view).get("oracle_results"))
        oracle_receipt_id = (
            stable_id("oracle", execution_id, oracle_rows or finding_receipt_view)
            if oracle_rows or raw_finding
            else ""
        )
        execution_results[obligation_id] = {
            "status": execution_status,
            "reason_code": execution_reason,
            "experiment_id": experiment_id,
            "execution_id": execution_id,
            "receipt_id": execution_id,
            "observation_receipt_ids": observation_receipt_ids,
            "oracle_receipt_id": oracle_receipt_id,
            "output_fingerprint": fingerprint({
                "trace": view,
                "finding": finding_receipt_view,
            }),
            "cost_coverage_status": "UNKNOWN",
            "operational_receipt": operational_receipt,
        }

        normalized_finding: dict[str, Any] | None = None
        if raw_finding is not None:
            existing_finding_id = _text(
                raw_finding.get("finding_id")
                or raw_finding.get("id")
                or raw_finding.get("bug_id")
            )
            finding_id = existing_finding_id or stable_id(
                "finding",
                contract["campaign_id"],
                _text(raw_finding.get("evidence_id")),
                unit_material,
            )
            evidence_id = _text(raw_finding.get("evidence_id")) or stable_id(
                "evidence", execution_id, finding_id
            )
            normalized_finding = {
                **raw_finding,
                "id": finding_id,
                "finding_id": finding_id,
                "candidate_id": candidate_id,
                "behavior_slice_id": slice_id,
                "slice_id": slice_id,
                "obligation_id": obligation_id,
                "experiment_id": experiment_id,
                "execution_id": execution_id,
                "evidence_id": evidence_id,
                "campaign_id": contract["campaign_id"],
                "mainline_run": {
                    "contract_fingerprint": contract["contract_fingerprint"],
                },
            }
            evidence = dict(_dict(normalized_finding.get("evidence")))
            evidence["evidence_id"] = evidence_id
            evidence["execution_id"] = execution_id
            normalized_finding["evidence"] = evidence
            normalized_findings.append(normalized_finding)

        if execution_status != "EXECUTED":
            if normalized_finding is not None:
                rejection_reasons = customer_delivery_rejection_reasons(
                    normalized_finding
                )
                finding_terminal[normalized_finding["finding_id"]] = (
                    execution_status,
                    list(
                        dict.fromkeys(
                            [execution_reason, *rejection_reasons]
                        )
                    ),
                )
            continue

        gate_receipt = build_customer_delivery_gate_receipt(
            normalized_finding,
            obligation_id=obligation_id,
            execution_id=execution_id,
        )
        gate = {
            **gate_receipt,
            "receipt_id": _text(gate_receipt.get("gate_receipt_id")),
        }
        gate_results[obligation_id] = gate
        if normalized_finding is not None:
            gate_status = _text(gate.get("status")).upper()
            gate_reasons = [
                _text(reason)
                for reason in _list(gate.get("reason_codes"))
                if _text(reason)
            ]
            finding_terminal[normalized_finding["finding_id"]] = (
                gate_status,
                gate_reasons,
            )

    ledger = build_obligation_attempt_ledger(
        mainline_run=contract,
        selected=selected_rows,
        compile_results=compile_results,
        execution_results=execution_results,
        gate_results=gate_results,
    )
    operational_summary = _operational_summary_from_attempt_ledger(ledger)
    deliverable: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    shadow: list[dict[str, Any]] = []
    for finding in normalized_findings:
        status, reasons = finding_terminal.get(
            finding["finding_id"],
            ("BLOCKED", ["LEGACY_TERMINAL_RECEIPT_MISSING"]),
        )
        if not contract["customer_outputs_published"]:
            shadow.append({
                **finding,
                "finding_class": "shadow",
                "shadow_origin": "legacy_champion",
                "delivery_gate_status": status,
                "delivery_gate_reasons": reasons,
            })
        elif status == "DELIVERABLE":
            obligation_id = _text(finding.get("obligation_id"))
            gate = dict(gate_results.get(obligation_id) or {})
            deliverable.append({
                **finding,
                "gate_passed": True,
                "customer_delivery_status": "defect",
                "customer_delivery_gate_reasons": [],
                "delivery_gate_receipt": gate,
                "delivery_gate_receipt_id": _text(
                    gate.get("gate_receipt_id") or gate.get("receipt_id")
                ),
            })
        else:
            candidates.append({
                **finding,
                "finding_class": "candidate",
                "gate_passed": False,
                "customer_delivery_status": "candidate",
                "customer_delivery_gate_reasons": reasons,
            })
    authority_occurrences = (
        deliverable
        if contract["customer_outputs_published"]
        else formal_customer_deliverable_findings(
            shadow,
            obligation_attempt_ledger=ledger,
        )
    )
    canonical_registry: dict[str, Any] | None = None
    canonical_findings: list[dict[str, Any]] = []
    canonical_error = ""
    try:
        canonical_registry = build_canonical_defect_registry(
            mainline_run=contract,
            deliverable_occurrences=authority_occurrences,
            obligation_attempt_ledger=ledger,
        )
        canonical_findings = canonical_representative_findings(
            canonical_registry,
            deliverable_occurrences=authority_occurrences,
        )
    except CanonicalDefectRegistryError as exc:
        canonical_error = str(exc)
        candidates.extend({
            **finding,
            "finding_class": "candidate",
            "customer_delivery_status": "candidate",
            "canonical_identity_status": "LEGACY_IDENTITY_UNRESOLVED",
            "canonical_identity_error": canonical_error,
        } for finding in authority_occurrences)
    formal = build_formal_count_projection(
        findings=authority_occurrences,
        candidate_findings=candidates,
        obligation_attempt_ledger=ledger,
        mainline_run=contract,
        canonical_defect_registry=canonical_registry,
    )
    formal_delivery_authority = build_formal_delivery_authority_receipt(
        mainline_run=contract,
        findings=authority_occurrences,
        obligation_attempt_ledger=ledger,
    )
    if canonical_registry is not None:
        defect_identity_consistency = build_defect_identity_consistency(
            occurrence_scopes={
                "delivery_gate_ids": validated_delivery_gate_finding_ids(ledger),
                "registry_occurrence_ids": list(
                    canonical_registry["delivery_occurrence_finding_ids"]
                ),
                "formal_projection_occurrence_ids": list(
                    formal["delivery_occurrence_finding_ids"]
                ),
            },
            canonical_scopes={
                "canonical_registry_ids": list(
                    canonical_registry["canonical_defect_ids"]
                ),
                "formal_projection_ids": list(formal["canonical_defect_ids"]),
                "product_projection_ids": sorted(
                    _text(item.get("canonical_defect_id"))
                    for item in canonical_findings
                    if _text(item.get("canonical_defect_id"))
                ),
            },
        )
    else:
        defect_identity_consistency = {
            "schema_version": "qualibug.defect-identity-consistency.v1",
            "consistent": False,
            "status": "BLOCKED_CANONICAL_IDENTITY",
            "reason": canonical_error or "canonical_registry_missing",
        }
    terminal_counts = _dict(ledger.get("terminal_status_counts"))
    auto_har_entries: list[dict[str, Any]] = []
    emitted_trace_indexes: set[int] = set()
    for unit in units:
        trace_index = unit.get("trace_index")
        if isinstance(trace_index, int) and trace_index not in emitted_trace_indexes:
            emitted_trace_indexes.add(trace_index)
            for step in _list(trace_views[trace_index].get("steps")):
                if not isinstance(step, dict):
                    continue
                try:
                    status_code = int(step.get("status") or 0)
                except (TypeError, ValueError):
                    status_code = 0
                if status_code <= 0:
                    continue
                auto_har_entries.append({
                    "request": {
                        "method": _text(step.get("method")).upper(),
                        "path": _text(step.get("path")),
                    },
                    "response": {"status_code": status_code},
                })
        elif trace_index is None and isinstance(unit.get("finding"), dict):
            view = finding_view(unit["finding"])
            try:
                status_code = int(view.get("status_code") or 0)
            except (TypeError, ValueError):
                status_code = 0
            if view["method"] and view["path"] and status_code > 0:
                auto_har_entries.append({
                    "request": {
                        "method": view["method"],
                        "path": view["path"],
                    },
                    "response": {"status_code": status_code},
                })
    experiment_execution = _legacy_experiment_execution_batch(
        selected_rows=selected_rows,
        execution_results=execution_results,
        normalized_findings=normalized_findings,
        campaign_id=contract["campaign_id"],
    )
    result = {
        **legacy_result,
        "schema_version": RUNTIME_SCHEMA,
        "v12_version": "3.0-mainline-legacy-adapter",
        "mainline_run": dict(contract),
        "campaign": _finalize_campaign(campaign_handle, ledger),
        "experiment_execution": experiment_execution,
        "obligation_attempt_ledger": ledger,
        "canonical_defect_registry": canonical_registry,
        "formal_delivery_authority": formal_delivery_authority,
        "operational_receipt_summary": operational_summary,
        "phases": {
            **_dict(legacy_result.get("phases")),
            "execution": {
                **_dict(_dict(legacy_result.get("phases")).get("execution")),
                "observed_http_request_count": int(
                    operational_summary.get("observed_http_request_count") or 0
                ),
                "production_http_requests": int(
                    operational_summary.get("production_http_requests") or 0
                ),
                "scenario_attempts": int(
                    operational_summary.get("scenario_attempts") or 0
                ),
                "accepted_write_count": int(
                    operational_summary.get("accepted_write_count") or 0
                ),
                "operational_receipt_complete": bool(
                    operational_summary.get("complete")
                ),
            },
        },
        "formal_count_projection": formal,
        "defect_identity_consistency": defect_identity_consistency,
        "delivery_occurrences": authority_occurrences,
        "findings": (
            canonical_findings if contract["customer_outputs_published"] else []
        ),
        "evaluator_canonical_findings": (
            canonical_findings
            if contract["private_evaluator_observation_allowed"]
            else []
        ),
        "candidate_findings": candidates,
        "shadow_findings": shadow,
        "auto_har": {
            "status": "receipt_backed",
            "entry_count": len(auto_har_entries),
            "entries": auto_har_entries,
            "redaction_contract": "method_path_status_only",
        },
        "execution_observability": [
            dict(row)
            for row in _list(
                _dict(_dict(legacy_result.get("phases")).get("execution")).get(
                    "observability"
                )
            )
            if isinstance(row, dict)
        ],
        "legacy_champion_adapter": {
            "status": "completed",
            "selected_attempt_count": len(selected_rows),
            "executed_attempt_count": sum(
                1
                for row in ledger["attempts"]
                if any(
                    stage.get("stage") == "execution"
                    and stage.get("status") == "EXECUTED"
                    for stage in _list(row.get("stages"))
                    if isinstance(stage, dict)
                )
            ),
            "blocked_attempt_count": int(terminal_counts.get("BLOCKED") or 0),
            "harness_failure_count": int(
                terminal_counts.get("HARNESS_FAILED") or 0
            ),
            "receipt_source": (
                "behavior_slice_selection+redacted_execution_trace+runtime_finding"
            ),
        },
    }
    result["discovery_funnel"] = build_funnel(result)
    return result
