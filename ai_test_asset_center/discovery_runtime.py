"""Single-authority discovery planning and experiment-candidate runtime."""
from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from .adaptive_discovery_planner import plan_obligation_round
from .behavior_ir import build_behavior_ir_from_knowledge_asset
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
    build_formal_id_consistency,
)
from .experiment_compiler import compile_experiments
from .experiment_executor import execute_selected_experiments
from .fixture_dag import attach_fixture_dag_to_experiments
from .obligation_attempt_ledger import build_obligation_attempt_ledger
from .obligation_compiler import compile_obligations_from_behavior_ir


RUNTIME_SCHEMA = "qualibug.discovery-runtime.v1"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


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

    spec = parse_to_openapi(text)
    if not isinstance(spec, dict):
        raise MainlineContractError("api_spec_parse_result_invalid")
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise MainlineContractError("api_spec_paths_missing")
    operations: list[dict[str, Any]] = []
    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue
        for method, raw_operation in methods.items():
            normalized_method = _text(method).upper()
            if normalized_method not in {"GET", "POST", "PUT", "PATCH", "DELETE"}:
                continue
            operation = _dict(raw_operation)
            operations.append({
                "method": normalized_method,
                "path": _text(path),
                "operation_id": _text(operation.get("operationId"))
                or f"{normalized_method.lower()}:{_text(path)}",
                "source_id": "api_spec",
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
            str(submitted_source_text or ""),
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
        mainline_run=_contract(inputs, _text(campaign.campaign_id)),
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
        finding_id = _text(gate.get("finding_id"))
        if finding_id not in findings_by_id:
            raise MainlineContractError(
                f"deliverable_gate_finding_missing:{finding_id or 'MISSING'}"
            )
    return deliverable, candidates, shadow


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
    deliverable, candidates, shadow = _authority_findings(
        raw_findings=[
            dict(row)
            for row in _list(batch.get("findings"))
            if isinstance(row, dict)
        ],
        gate_results=gate_results,
        contract=plan.mainline_run,
    )
    formal = build_formal_count_projection(
        findings=deliverable,
        candidate_findings=candidates,
    )
    formal_ids = list(formal["formal_finding_ids"])
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
            "results": list(batch.get("results") or []),
        },
        "obligation_attempt_ledger": ledger,
        "formal_count_projection": formal,
        "formal_id_consistency": build_formal_id_consistency(
            delivery_gate_ids=formal_ids,
            formal_projection_ids=formal_ids,
            product_projection_ids=formal_ids,
        ),
        "findings": deliverable,
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
    """Expose a frozen legacy comparison without granting product authority."""

    if plan.mainline_run["customer_outputs_published"]:
        raise MainlineContractError("legacy_champion_operational_forbidden")
    if not isinstance(legacy_result, dict):
        raise MainlineContractError("legacy_champion_result_invalid")
    if _text(legacy_result.get("error")):
        raise RuntimeError(f"legacy_champion_failed:{_text(legacy_result.get('error'))}")
    selected_rows = _selected_rows(plan)
    compile_results = {
        _text(row.get("obligation_id")): {
            "status": "DEFERRED",
            "reason_code": "LEGACY_CHAMPION_NON_OBLIGATION_TRACE",
            "experiment_id": _text(row.get("experiment_id")),
            "cost_coverage_status": "UNKNOWN",
        }
        for row in selected_rows
    }
    ledger = build_obligation_attempt_ledger(
        mainline_run=plan.mainline_run,
        selected=selected_rows,
        compile_results=compile_results,
        execution_results={},
        gate_results={},
    )
    shadow = [
        {
            **dict(row),
            "finding_class": "shadow",
            "shadow_origin": "legacy_champion",
            "mainline_run": {
                "contract_fingerprint": plan.mainline_run["contract_fingerprint"],
            },
        }
        for row in _list(legacy_result.get("findings"))
        if isinstance(row, dict)
    ]
    formal = build_formal_count_projection(findings=[], candidate_findings=[])
    result = {
        **legacy_result,
        "schema_version": RUNTIME_SCHEMA,
        "v12_version": "3.0-mainline-legacy-comparison",
        "mainline_run": dict(plan.mainline_run),
        "campaign": _finalize_campaign(campaign_handle, ledger),
        "obligation_attempt_ledger": ledger,
        "formal_count_projection": formal,
        "formal_id_consistency": build_formal_id_consistency(
            delivery_gate_ids=[],
            formal_projection_ids=[],
            product_projection_ids=[],
        ),
        "findings": [],
        "candidate_findings": [],
        "shadow_findings": shadow,
    }
    result["discovery_funnel"] = build_funnel(result)
    return result
