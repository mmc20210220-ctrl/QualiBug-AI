from __future__ import annotations

"""Cross-stage, redacted trace ledger for discovery-harness evolution.

The ledger is derived only from observed pipeline artifacts. It never invents
requests, responses, bugs, or evaluator outcomes, and it intentionally stores
status/lineage summaries instead of raw request bodies, response bodies,
credentials, or customer text.
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .customer_delivery_gate import (
    customer_delivery_rejection_reasons,
    is_customer_deliverable_defect,
)
from .discovery_funnel import build_pipeline_health


TRACE_LEDGER_SCHEMA = "qualibug.discovery-trace-ledger.v1"


class DiscoveryTraceError(ValueError):
    """Observed trace artifacts are structurally invalid."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _required(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise DiscoveryTraceError(f"missing required trace context: {name}")
    return text


def _stable_id(prefix: str, *parts: Any) -> str:
    payload = "\x1f".join(str(part or "") for part in parts)
    return f"{prefix}_{hashlib.sha256(payload.encode('utf-8')).hexdigest()[:24]}"


def _normalized_path(value: Any) -> str:
    path = str(value or "").strip()
    if not path:
        return ""
    path = re.sub(r"^https?://[^/]+", "", path, flags=re.IGNORECASE)
    path = path.split("?", 1)[0]
    path = re.sub(r"/[0-9]+(?=/|$)", "/{id}", path)
    path = re.sub(
        r"/[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}(?=/|$)",
        "/{id}",
        path,
        flags=re.IGNORECASE,
    )
    path = re.sub(r"/:([A-Za-z_][A-Za-z0-9_]*)", r"/{\1}", path)
    return path


def _source_kinds(source_refs: list[Any]) -> list[str]:
    return sorted(
        {
            str(_dict(item).get("source_type") or _dict(item).get("kind") or "").strip()
            for item in source_refs
            if str(_dict(item).get("source_type") or _dict(item).get("kind") or "").strip()
        }
    )


def _finding_key(item: dict[str, Any]) -> tuple[str, str]:
    return (
        str(item.get("behavior_slice_id") or "").strip(),
        str(item.get("evidence_id") or "").strip(),
    )


def _finding_matches(
    finding: dict[str, Any],
    *,
    slice_id: str,
    evidence_id: str,
) -> bool:
    finding_slice, finding_evidence = _finding_key(finding)
    if evidence_id and finding_evidence:
        return evidence_id == finding_evidence
    return bool(slice_id and finding_slice == slice_id)


def _execution_graph_key(graph: dict[str, Any]) -> tuple[str, str, str]:
    scenario = _dict(graph.get("scenario"))
    execution = _dict(graph.get("execution_trace"))
    return (
        str(scenario.get("behavior_slice_id") or "").strip(),
        str(execution.get("scenario_id") or scenario.get("id") or "").strip(),
        str(execution.get("actor_role") or "").strip(),
    )


def _error_code(value: Any) -> str:
    text = str(value or "").strip().lower()
    if "missing_runtime_path_binding" in text:
        return "MISSING_RUNTIME_PATH_BINDING"
    if "timeout" in text or "timed out" in text:
        return "TIMEOUT"
    if "connection" in text or "unreachable" in text:
        return "CONNECTION_FAILURE"
    if "auth" in text or "token" in text or "credential" in text:
        return "AUTHENTICATION_FAILURE"
    if "nan" in text or "invalid input" in text or "validation" in text:
        return "INVALID_TEST_INPUT"
    return "UNCLASSIFIED_EXECUTION_ERROR"


def _execution_summary(graph: dict[str, Any]) -> dict[str, Any]:
    execution = _dict(graph.get("execution_trace"))
    steps = [_dict(item) for item in _list(execution.get("steps"))]
    statuses = [int(item.get("status") or 0) for item in steps]
    http_statuses = [status for status in statuses if status > 0]
    skipped_reasons = sorted(
        {str(item.get("skipped_reason") or "").strip() for item in steps if str(item.get("skipped_reason") or "").strip()}
    )
    methods = sorted({str(item.get("method") or "").upper() for item in steps if str(item.get("method") or "").strip()})
    paths = sorted({_normalized_path(item.get("path")) for item in steps if _normalized_path(item.get("path"))})
    error_codes = sorted({_error_code(item) for item in _list(execution.get("errors")) if str(item).strip()})
    sandbox_write = _dict(execution.get("sandbox_write"))
    cleanup = _dict(sandbox_write.get("cleanup"))
    planned_write_steps = [
        item
        for item in steps
        if str(item.get("method") or "").upper() in {"POST", "PUT", "PATCH", "DELETE"}
        and not str(item.get("action") or "").lower().startswith("login")
        and not str(item.get("path") or "").lower().endswith("/login")
    ]
    executed_write_steps = [
        item
        for item in planned_write_steps
        if int(item.get("status") or _dict(item.get("response")).get("status_code") or 0) > 0
    ]
    audit_records = [item for item in _list(sandbox_write.get("audit_records")) if isinstance(item, dict)]
    try:
        declared_audit_count = int(sandbox_write.get("audit_record_count") or 0)
    except (TypeError, ValueError):
        declared_audit_count = 0
    governed_write_receipt_count = (
        len(audit_records)
        or max(0, declared_audit_count)
        or (1 if sandbox_write.get("audit_path") else 0)
    )
    return {
        "trace_observed": bool(graph),
        "scenario_id": str(execution.get("scenario_id") or _dict(graph.get("scenario")).get("id") or ""),
        "step_count": len(steps),
        "http_step_count": len(http_statuses),
        "zero_status_step_count": sum(1 for status in statuses if status == 0),
        "http_status_classes": sorted({f"{status // 100}xx" for status in http_statuses}),
        "has_4xx": any(400 <= status < 500 for status in http_statuses),
        "has_5xx": any(status >= 500 for status in http_statuses),
        "methods": methods,
        "normalized_paths": paths,
        "skipped_reasons": skipped_reasons,
        "error_count": len(_list(execution.get("errors"))),
        "error_codes": error_codes,
        "precondition_failure_count": len(_list(execution.get("precondition_not_met"))),
        "sandbox_write_status": str(sandbox_write.get("status") or ""),
        "cleanup_status": str(cleanup.get("status") or ""),
        "cleanup_receipt_present": bool(cleanup.get("receipt_ref")),
        "audit_path_present": bool(sandbox_write.get("audit_path")),
        "planned_write_step_count": len(planned_write_steps),
        "write_step_count": len(executed_write_steps),
        "governed_write_receipt_count": governed_write_receipt_count,
        "actor_role_present": bool(execution.get("actor_role")),
    }


def _verification_summary(graph: dict[str, Any], matched_findings: list[dict[str, Any]]) -> dict[str, Any]:
    oracle_results = [_dict(item) for item in _list(graph.get("oracle_results"))]
    rejection_codes = sorted(
        {
            reason
            for finding in matched_findings
            for reason in customer_delivery_rejection_reasons(finding)
        }
    )
    return {
        "oracle_evaluated_count": len(oracle_results),
        "oracle_failure_votes": sum(1 for item in oracle_results if item.get("passed") is False),
        "oracle_pass_votes": sum(1 for item in oracle_results if item.get("passed") is True),
        "oracle_names": sorted(
            {str(item.get("oracle_name") or item.get("oracle") or "") for item in oracle_results if str(item.get("oracle_name") or item.get("oracle") or "")}
        ),
        "layers_triggered": sorted({str(item) for item in _list(graph.get("layers_triggered")) if str(item).strip()}),
        "evidence_id_present": bool(graph.get("evidence_id")),
        "matched_finding_count": len(matched_findings),
        "formal_defect_count": sum(1 for item in matched_findings if is_customer_deliverable_defect(item)),
        "rejection_codes": rejection_codes,
    }


def _failure_signatures(
    *,
    generation: dict[str, Any],
    selection: dict[str, Any],
    execution: dict[str, Any],
    verification: dict[str, Any],
    blocked_reason: str,
) -> list[str]:
    signatures: set[str] = set()
    if generation["endpoint_count"] == 0:
        signatures.add("ENDPOINT_BINDING_MISSING")
    if generation["source_ref_count"] == 0:
        signatures.add("SOURCE_GROUNDING_MISSING")
    if not selection["selected"]:
        signatures.add("CANDIDATE_NOT_SELECTED")
    if blocked_reason.startswith("missing_runtime_path_binding") or any(
        reason.startswith("missing_runtime_path_binding") for reason in execution["skipped_reasons"]
    ):
        signatures.add("RUNTIME_PATH_BINDING_MISSING")
    if selection["selected"] and execution["step_count"] == 0:
        signatures.add("SELECTED_WITHOUT_EXECUTION_TRACE")
    if execution["step_count"] > 0 and execution["http_step_count"] == 0:
        signatures.add("NO_HTTP_EXECUTION")
    if execution["zero_status_step_count"]:
        signatures.add("ZERO_STATUS_NON_EXECUTION")
    if execution["error_count"]:
        signatures.add("EXECUTION_ERROR")
    if execution["precondition_failure_count"]:
        signatures.add("PRECONDITION_NOT_MET")
    if execution["has_5xx"]:
        signatures.add("TARGET_5XX_REQUIRES_CONTROL")
    if execution["sandbox_write_status"] and execution["sandbox_write_status"] not in {"completed", "clean", "read_only"}:
        signatures.add("SANDBOX_WRITE_INCOMPLETE")
    if execution["write_step_count"] > execution["governed_write_receipt_count"]:
        signatures.add("MULTI_WRITE_AUDIT_INCOMPLETE")
    if execution["cleanup_status"] and execution["cleanup_status"] not in {
        "completed", "success", "succeeded", "not_required",
        "not_reversible", "not_applicable", "n/a",
    }:
        signatures.add("CLEANUP_FAILED")
    if execution["cleanup_status"] == "not_reversible":
        signatures.add("CLEANUP_NOT_REVERSIBLE")
    if (
        execution["cleanup_status"]
        and execution["cleanup_status"] not in {"not_reversible", "not_applicable", "n/a", "not_required", "completed", "success", "succeeded"}
        and not execution["cleanup_receipt_present"]
    ):
        signatures.add("CLEANUP_RECEIPT_MISSING")
    invalid_execution = bool(
        execution["trace_observed"]
        and (
            execution["zero_status_step_count"]
            or (execution["step_count"] > 0 and execution["http_step_count"] == 0)
            or execution["precondition_failure_count"]
        )
    )
    if invalid_execution and verification["oracle_failure_votes"]:
        signatures.add("ORACLE_CONFIRMED_NON_EXECUTION")
    if verification["formal_defect_count"] and invalid_execution:
        signatures.add("FORMAL_DEFECT_FROM_NON_EXECUTION")
    if verification["formal_defect_count"] and not execution["trace_observed"]:
        signatures.add("FORMAL_DEFECT_TRACE_MISSING")
    if verification["formal_defect_count"] and "CLEANUP_FAILED" in signatures:
        signatures.add("FORMAL_DEFECT_WITH_CLEANUP_FAILURE")
    rejection_codes = set(verification["rejection_codes"])
    if "CLEANUP_EVIDENCE_MISSING" in rejection_codes:
        signatures.add("CLEANUP_EVIDENCE_MISSING")
    if "EVIDENCE_QUALITY_NOT_VALIDATED" in rejection_codes or "BUSINESS_EVIDENCE_NOT_VALIDATED" in rejection_codes:
        signatures.add("EVIDENCE_GATE_INCOMPLETE")
    if "MISSING_REAL_REPLAY_ASSET" in rejection_codes or "MISSING_CUSTOMER_FACING_HARD_EVIDENCE" in rejection_codes:
        signatures.add("REPLAY_EVIDENCE_MISSING")
    if verification["oracle_failure_votes"] and verification["formal_defect_count"] == 0 and not invalid_execution:
        signatures.add("VALID_VIOLATION_NOT_PROMOTED")
    return sorted(signatures)


def _trace_outcome(signatures: list[str], verification: dict[str, Any]) -> str:
    invalid = {"ORACLE_CONFIRMED_NON_EXECUTION", "FORMAL_DEFECT_FROM_NON_EXECUTION", "FORMAL_DEFECT_WITH_CLEANUP_FAILURE"}
    if invalid.intersection(signatures):
        return "invalid_promotion_or_verification"
    if verification["formal_defect_count"]:
        return "customer_deliverable_defect"
    if "VALID_VIOLATION_NOT_PROMOTED" in signatures:
        return "valid_violation_held_back"
    if signatures:
        return "harness_or_execution_failure"
    if verification["oracle_pass_votes"]:
        return "valid_success_control"
    return "unresolved"


def build_discovery_trace_ledger(
    v12_result: dict[str, Any],
    *,
    run_id: str,
    policy_id: str,
    target_id: str,
    project_id: str,
    industry: str,
    evaluation_mode: str,
) -> dict[str, Any]:
    """Join generation, selection, execution, verification, and formal stages."""

    if not isinstance(v12_result, dict):
        raise DiscoveryTraceError("v12_result must be an object")
    run_id = _required(run_id, "run_id")
    policy_id = _required(policy_id, "policy_id")
    target_id = _required(target_id, "target_id")
    project_id = _required(project_id, "project_id")
    industry = _required(industry, "industry")
    evaluation_mode = _required(evaluation_mode, "evaluation_mode")

    slices = [_dict(item) for item in _list(v12_result.get("behavior_slices"))]
    slice_by_id = {
        str(item.get("slice_id") or "").strip(): item
        for item in slices
        if str(item.get("slice_id") or "").strip()
    }
    if not slice_by_id:
        raise DiscoveryTraceError("v12_result.behavior_slices is empty; candidate lineage is not observable")
    ledger = _dict(v12_result.get("behavior_slice_ledger"))
    phases = _dict(v12_result.get("phases"))
    scenario_generation = _dict(phases.get("scenario_generation"))
    execution_phase = _dict(phases.get("execution"))
    selected_ids = {
        str(item) for item in _list(scenario_generation.get("selected_slice_ids") or ledger.get("selected_slice_ids")) if str(item).strip()
    }
    attempted_ids = {str(item) for item in _list(ledger.get("attempted_slice_ids")) if str(item).strip()}
    blocked_by_slice = {
        str(_dict(item).get("behavior_slice_id") or ""): str(_dict(item).get("reason") or "")
        for item in _list(_dict(execution_phase.get("skip_telemetry")).get("blocked_samples"))
        if str(_dict(item).get("behavior_slice_id") or "")
    }
    findings = [_dict(item) for item in _list(v12_result.get("findings"))]
    graphs = [_dict(item) for item in _list(v12_result.get("evidence_graphs"))]
    observed_execution_keys = {
        _execution_graph_key(item)
        for item in graphs
        if any(_execution_graph_key(item))
    }
    for summary in _list(v12_result.get("execution_trace_summaries")):
        if not isinstance(summary, dict):
            continue
        key = _execution_graph_key(summary)
        if not any(key) or key in observed_execution_keys:
            continue
        graphs.append(summary)
        observed_execution_keys.add(key)
    graphs_by_slice: dict[str, list[dict[str, Any]]] = {}
    for graph in graphs:
        scenario = _dict(graph.get("scenario"))
        slice_id = str(scenario.get("behavior_slice_id") or "").strip()
        if slice_id:
            graphs_by_slice.setdefault(slice_id, []).append(graph)

    traces: list[dict[str, Any]] = []
    for slice_id, behavior_slice in slice_by_id.items():
        slice_graphs = graphs_by_slice.get(slice_id) or [{}]
        for graph_index, graph in enumerate(slice_graphs):
            scenario = _dict(graph.get("scenario"))
            evidence_id = str(graph.get("evidence_id") or "").strip()
            matched_findings = [
                item
                for item in findings
                if _finding_matches(item, slice_id=slice_id, evidence_id=evidence_id)
            ]
            endpoints = [str(item) for item in _list(behavior_slice.get("endpoints")) if str(item).strip()]
            generation = {
                "origin": str(behavior_slice.get("_hypothesis_origin") or behavior_slice.get("source") or "source_compiler"),
                "hypothesis_id": str(behavior_slice.get("_hypothesis_id") or ""),
                "family": str(behavior_slice.get("_hypothesis_family") or behavior_slice.get("kind") or "unclassified"),
                "source_ref_count": len(_list(behavior_slice.get("source_refs"))),
                "source_kinds": _source_kinds(_list(behavior_slice.get("source_refs"))),
                "endpoint_count": len(endpoints),
                "endpoint_shapes": sorted({_normalized_path(item) for item in endpoints if _normalized_path(item)}),
                "bound_method": str(behavior_slice.get("_bound_method") or "").upper(),
                "bound_path_shape": _normalized_path(behavior_slice.get("_bound_path")),
            }
            selection = {
                # The final campaign ledger includes attempts from earlier
                # rounds while scenario_generation commonly contains only the
                # latest round. Treat either as observed selection.
                "selected": slice_id in selected_ids or slice_id in attempted_ids,
                "attempted_in_campaign": slice_id in attempted_ids,
                "priority": float(behavior_slice.get("priority") or 0),
                "selection_origin": str(behavior_slice.get("_selection_origin") or ""),
            }
            execution = _execution_summary(graph)
            verification = _verification_summary(graph, matched_findings)
            blocked_reason = blocked_by_slice.get(slice_id, "")
            signatures = _failure_signatures(
                generation=generation,
                selection=selection,
                execution=execution,
                verification=verification,
                blocked_reason=blocked_reason,
            )
            scenario_id = execution["scenario_id"] or f"slice-only-{graph_index}"
            traces.append(
                {
                    "trace_id": _stable_id("TRACE", run_id, slice_id, scenario_id, evidence_id),
                    "run_id": run_id,
                    "policy_id": policy_id,
                    "target_id": target_id,
                    "project_id": project_id,
                    "industry": industry,
                    "evaluation_mode": evaluation_mode,
                    "campaign_id": str(ledger.get("campaign_id") or ""),
                    "discovery_round": int(scenario.get("discovery_round") or ledger.get("round") or 0),
                    "behavior_slice_id": slice_id,
                    "scenario_id": execution["scenario_id"],
                    "evidence_id": evidence_id,
                    "generation": generation,
                    "selection": selection,
                    "execution": execution,
                    "verification": verification,
                    "blocked_reason_code": blocked_reason.split(":", 1)[0] if blocked_reason else "",
                    "failure_signatures": signatures,
                    "outcome": _trace_outcome(signatures, verification),
                }
            )

    health = build_pipeline_health(v12_result)
    signature_counts: dict[str, int] = {}
    outcome_counts: dict[str, int] = {}
    for trace in traces:
        outcome = str(trace.get("outcome") or "")
        outcome_counts[outcome] = outcome_counts.get(outcome, 0) + 1
        for signature in trace.get("failure_signatures") or []:
            signature_counts[signature] = signature_counts.get(signature, 0) + 1
    return {
        "schema_version": TRACE_LEDGER_SCHEMA,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "run_id": run_id,
        "policy_id": policy_id,
        "target_id": target_id,
        "project_id": project_id,
        "industry": industry,
        "evaluation_mode": evaluation_mode,
        "campaign_id": str(ledger.get("campaign_id") or ""),
        "pipeline_health": health,
        "trace_count": len(traces),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "failure_signature_counts": dict(sorted(signature_counts.items(), key=lambda item: (-item[1], item[0]))),
        "aggregate_stage_events": {
            "dropped_no_endpoint": sum(
                int(_dict(item).get("dropped_no_endpoint") or 0)
                for key, item in _dict(v12_result.get("mainline_unification")).items()
                if key != "error" and isinstance(item, dict)
            ),
            "execution_reason_counts": dict(
                _dict(_dict(execution_phase.get("skip_telemetry")).get("reason_counts"))
            ),
            "cleanup_failure_reasons": dict(
                _dict(_dict(execution_phase.get("skip_telemetry")).get("cleanup_failure_reasons"))
            ),
        },
        "redaction_contract": {
            "raw_request_bodies_persisted": False,
            "raw_response_bodies_persisted": False,
            "credentials_persisted": False,
            "ground_truth_persisted": False,
        },
        "traces": traces,
    }


def persist_trace_ledger(ledger: dict[str, Any], output_root: Path | str) -> Path:
    if ledger.get("schema_version") != TRACE_LEDGER_SCHEMA:
        raise DiscoveryTraceError("cannot persist unsupported trace ledger schema")
    safe = re.compile(r"[^A-Za-z0-9_.-]+")
    target = safe.sub("_", _required(ledger.get("target_id"), "target_id"))
    run_id = safe.sub("_", _required(ledger.get("run_id"), "run_id"))
    path = Path(output_root) / target / f"{run_id}.trace-ledger.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(ledger, ensure_ascii=False, indent=2)
    if path.exists():
        if json.loads(path.read_text(encoding="utf-8")) != json.loads(payload):
            raise DiscoveryTraceError(f"immutable trace ledger already exists with different content: {path}")
        return path
    temporary = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temporary.write_text(payload, encoding="utf-8")
    os.replace(temporary, path)
    return path
