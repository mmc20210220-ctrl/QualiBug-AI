"""Versioned project campaign and evaluator-submission API projections.

This module is deliberately storage- and transport-neutral.  It reads runtime
artifacts, applies the formal delivery gate, emits identity-continuity views,
and persists only redacted contracts.  It never opens evaluator ground truth.
"""
from __future__ import annotations

import hashlib
import json
import time
import uuid
from pathlib import Path
from typing import Any

from .artifact_redactor import write_json_redacted
from .canonical_defect_registry import (
    CanonicalDefectRegistryError,
    build_defect_identity_consistency,
    canonical_representative_findings,
    validate_canonical_defect_registry,
    validate_defect_identity_consistency,
)
from .discovery_mainline_contract import validate_mainline_run_contract
from .discovery_quality_projection import (
    attach_quality_projection_to_scan_result,
    build_formal_count_projection,
)
from .obligation_attempt_ledger import (
    ObligationAttemptLedgerError,
    validate_obligation_attempt_ledger,
)
from .formal_delivery_authority import (
    FormalDeliveryAuthorityError,
    build_formal_delivery_authority_receipt,
    validate_formal_delivery_authority_receipt,
)
from .target_policy import build_target_policy_decision


CAMPAIGN_SCHEMA = "qualibug.project-campaign.v1"
CAMPAIGN_VIEW_SCHEMA = "qualibug.project-campaign-view.v1"
SUBMISSION_SCHEMA = "qualibug.discovery-evaluation-submission.v2"
CAMPAIGN_STATES = {"draft", "ready", "running", "partial", "blocked", "failed_safe", "completed"}
EXECUTION_STATES = {"completed", "partial", "blocked", "failed_safe", "not_executed"}
PIPELINE_HEALTH_STATES = {"OK", "DEGRADED", "BLOCKED", "FAILED_SAFE"}


class CampaignContractError(ValueError):
    """A public campaign contract could not be constructed safely."""


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _canonical_bytes(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str).encode("utf-8")


def _fingerprinted(payload: dict[str, Any]) -> dict[str, Any]:
    result = dict(payload)
    result.pop("fingerprint", None)
    result["fingerprint"] = hashlib.sha256(_canonical_bytes(result)).hexdigest()
    return result


def structured_error(
    *,
    stage: str,
    code: str,
    identity: dict[str, Any] | None = None,
    retryability: str = "after_operator_action",
    operator_action: str,
) -> dict[str, Any]:
    return {
        "schema_version": "qualibug.structured-error.v1",
        "stage": _text(stage),
        "code": _text(code),
        "identity": dict(identity or {}),
        "retryability": _text(retryability),
        "operator_action": _text(operator_action),
    }


def _scan_result_path(root: Path, project_id: str) -> Path:
    return root / "platform_outputs" / project_id / "scan_result.json"


def load_scan_result(root: Path, project_id: str) -> dict[str, Any]:
    from .scan_result_store import load_scan_result as _load_scan_result_store

    path = _scan_result_path(root, project_id)
    if not path.is_file():
        raise CampaignContractError(f"scan result not found for project {project_id!r}: {path}")
    try:
        value = _load_scan_result_store(path, keys=None)
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignContractError(f"invalid scan result for project {project_id!r}: {exc}") from exc
    if not isinstance(value, dict):
        raise CampaignContractError(f"scan result for project {project_id!r} must be an object")
    return value


def _v12(scan_result: dict[str, Any]) -> dict[str, Any]:
    return _dict(scan_result.get("v12"))


def _experiment_execution(scan_result: dict[str, Any]) -> dict[str, Any]:
    return _dict(
        scan_result.get("experiment_execution")
        or _v12(scan_result).get("experiment_execution")
    )


def _trace_occurrence_ids(
    projected: dict[str, Any],
    occurrence_ids: list[str],
) -> list[str]:
    """Bind trace scope to formal delivery occurrences without partial subsets.

    Multi-round campaigns may only expose a subset of deliverable finding ids
    through ``build_identity_traces`` (latest-round selected experiments). A
    partial trace scope must not be injected against the full formal occurrence
    set — that falsely fails defect-identity consistency. Empty or incomplete
    coverage falls back to the formal occurrence authority.
    """

    occurrence_set = {
        _text(value) for value in occurrence_ids if _text(value)
    }
    if not occurrence_set:
        return []
    trace_ids = sorted({
        _text(item.get("finding_id"))
        for item in build_identity_traces(projected)
        if _text(item.get("finding_id")) in occurrence_set
    })
    if set(trace_ids) != occurrence_set:
        return sorted(occurrence_set)
    return trace_ids


def _campaign(scan_result: dict[str, Any]) -> dict[str, Any]:
    return _dict(scan_result.get("campaign") or _v12(scan_result).get("campaign"))


def _campaign_status(scan_result: dict[str, Any]) -> str:
    campaign = _campaign(scan_result)
    raw = _text(campaign.get("campaign_status") or campaign.get("status")).lower()
    health = _text(_dict(scan_result.get("pipeline_health")).get("status")).upper()
    execution = _experiment_execution(scan_result)
    if health == "FAILED_SAFE":
        return "failed_safe"
    if health == "BLOCKED" or raw == "blocked":
        return "blocked"
    if health == "DEGRADED" or int(execution.get("blocked_count") or 0) > 0:
        return "partial"
    if raw in {"running", "active", "scanning", "in_progress"}:
        return "running"
    if raw in {"draft", "ready", "completed"}:
        return raw
    return "completed" if scan_result else "draft"


def _execution_status(scan_result: dict[str, Any]) -> str:
    execution = _experiment_execution(scan_result)
    selected = int(execution.get("selected_count") or 0)
    executed = int(execution.get("executed_count") or 0)
    blocked = int(execution.get("blocked_count") or 0)
    harness_failures = int(execution.get("harness_failure_count") or 0)
    if harness_failures:
        return "failed_safe"
    if selected == 0:
        return "not_executed"
    if executed == selected and blocked == 0:
        return "completed"
    if executed > 0:
        return "partial"
    return "blocked" if blocked else "not_executed"


def create_campaign(root: Path, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    campaign_id = _text(body.get("campaign_id")) or f"cmp_{uuid.uuid4().hex}"
    target_url = _text(body.get("approved_base_url") or body.get("target_url") or body.get("base_url"))
    environment_type = _text(body.get("environment_type") or body.get("environment_kind"))
    environment_ref = _text(body.get("environment_ref") or body.get("target_id"))
    read_only = bool(body.get("read_only"))
    policy = build_target_policy_decision(
        requested_base_url=target_url,
        approved_base_url=_text(body.get("approved_base_url")),
        environment_type=environment_type,
        environment_ref=environment_ref,
        execution_mode="safe_read_only" if read_only else "approved_sandbox_write",
        runtime_status="approved",
    )
    ready = bool(policy.get("read_allowed") if read_only else policy.get("write_allowed"))
    contract = _fingerprinted({
        "schema_version": CAMPAIGN_SCHEMA,
        "campaign_id": campaign_id,
        "project_id": project_id,
        "status": "ready" if ready else "draft",
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "target_policy_decision": policy,
        "blocking_codes": list(policy.get("blocking_codes") or []),
        "runtime_input": {
            "environment_type": environment_type,
            "environment_ref": environment_ref,
            "approved_base_url": target_url,
            "read_only": read_only,
            "source_snapshot_hash": _text(body.get("source_snapshot_hash")),
            "policy_version": _text(body.get("policy_version")),
        },
    })
    path = root / "platform_outputs" / project_id / "campaigns" / campaign_id / "campaign.json"
    redaction_receipt = write_json_redacted(path, contract)
    return {**contract, "artifact_ref": str(path), "redaction_receipt": redaction_receipt}


def load_created_campaign(root: Path, project_id: str, campaign_id: str) -> dict[str, Any]:
    path = root / "platform_outputs" / project_id / "campaigns" / campaign_id / "campaign.json"
    if not path.is_file():
        raise CampaignContractError(f"campaign contract not found: {campaign_id}")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CampaignContractError(f"invalid campaign contract {campaign_id}: {exc}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != CAMPAIGN_SCHEMA:
        raise CampaignContractError(f"unsupported campaign contract: {campaign_id}")
    return value


def _selected_experiments(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    execution = _experiment_execution(scan_result)
    rows: list[dict[str, Any]] = []
    for item in _list(execution.get("results")):
        if not isinstance(item, dict):
            continue
        status = _text(item.get("status")).upper()
        rows.append({
            "candidate_id": _text(item.get("candidate_id")),
            "slice_id": _text(item.get("slice_id")),
            "experiment_id": _text(item.get("experiment_id")),
            "obligation_id": _text(item.get("obligation_id")),
            "execution_id": _text(item.get("execution_id")),
            "evidence_id": _text(item.get("evidence_id")),
            "finding_id": _text(_dict(item.get("finding")).get("id") or _dict(item.get("finding")).get("finding_id")),
            "campaign_id": _text(item.get("campaign_id")),
            "status": status,
            "reason_code": _text(item.get("reason_code")),
            "detail": _text(item.get("detail")),
            "elapsed_ms": int(item.get("elapsed_ms") or 0),
            "execution_receipt": _dict(item.get("execution_receipt")),
            "has_receipt": isinstance(item.get("execution_receipt"), dict),
        })
    return rows


def build_identity_traces(scan_result: dict[str, Any]) -> list[dict[str, Any]]:
    v12 = _v12(scan_result)
    campaign_id = _text(_campaign(scan_result).get("campaign_id"))
    obligations = {
        _text(item.get("obligation_id")): item
        for item in _list(_dict(v12.get("test_obligations")).get("obligations"))
        if isinstance(item, dict) and _text(item.get("obligation_id"))
    }
    findings = [
        item for item in _list(scan_result.get("findings") or v12.get("findings"))
        if isinstance(item, dict)
    ]
    finding_by_experiment = {
        _text(item.get("experiment_id")): item
        for item in findings if _text(item.get("experiment_id"))
    }
    traces: list[dict[str, Any]] = []
    for result in _selected_experiments(scan_result):
        experiment_id = result["experiment_id"]
        obligation_id = result["obligation_id"]
        obligation = _dict(obligations.get(obligation_id))
        finding = _dict(finding_by_experiment.get(experiment_id))
        subject_refs = [item for item in _list(obligation.get("subject_refs")) if isinstance(item, dict)]
        slice_id = _text(result.get("slice_id") or obligation.get("slice_id"))
        if not slice_id:
            slice_id = next((_text(item.get("slice_id") or item.get("behavior_slice_id")) for item in subject_refs if _text(item.get("slice_id") or item.get("behavior_slice_id"))), "")
        evidence = _dict(finding.get("evidence"))
        trace = {
            "candidate_id": _text(result.get("candidate_id") or finding.get("candidate_id")),
            "slice_id": slice_id,
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "execution_id": _text(result.get("execution_id") or result["execution_receipt"].get("execution_id")),
            "evidence_id": _text(result.get("evidence_id") or finding.get("evidence_id") or evidence.get("evidence_id")),
            "finding_id": _text(result.get("finding_id") or finding.get("id") or finding.get("finding_id") or finding.get("bug_id")),
            "campaign_id": _text(result.get("campaign_id") or finding.get("campaign_id")) or campaign_id,
            "execution_status": result["status"],
            "reason_code": result["reason_code"],
            "has_execution_receipt": result["has_receipt"],
        }
        missing = [key for key in ("slice_id", "obligation_id", "experiment_id", "execution_id", "evidence_id", "finding_id") if not trace.get(key)]
        trace["identity_complete"] = len(missing) == 0
        trace["missing_identity_fields"] = missing
        traces.append(trace)
    return traces


def _validated_attempt_ledger_for_submission(
    projected: dict[str, Any],
    *,
    formal_ids: list[str],
) -> dict[str, Any] | None:
    raw = (
        projected.get("obligation_attempt_ledger")
        or _v12(projected).get("obligation_attempt_ledger")
    )
    if raw is None:
        raise CampaignContractError(
            "formal_delivery_attempt_ledger_missing"
        )
    if not isinstance(raw, dict):
        raise CampaignContractError(
            "formal_delivery_attempt_ledger_not_object"
        )
    try:
        return validate_obligation_attempt_ledger(raw)
    except ObligationAttemptLedgerError as exc:
        raise CampaignContractError(
            f"formal_delivery_attempt_ledger_invalid:{exc}"
        ) from exc


def _extend_projected_identity_consistency(
    projected: dict[str, Any],
    *,
    extra_occurrence_scopes: dict[str, list[str]] | None = None,
    extra_canonical_scopes: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    base = _dict(projected.get("defect_identity_consistency"))
    try:
        validated = validate_defect_identity_consistency(
            base,
            required_occurrence_scopes={
                "delivery_gate_ids",
                "registry_occurrence_ids",
                "formal_projection_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "product_projection_ids",
            },
        )
        return build_defect_identity_consistency(
            occurrence_scopes={
                **{
                    name: list(values)
                    for name, values in _dict(
                        validated.get("occurrence_scopes")
                    ).items()
                },
                **dict(extra_occurrence_scopes or {}),
            },
            canonical_scopes={
                **{
                    name: list(values)
                    for name, values in _dict(
                        validated.get("canonical_scopes")
                    ).items()
                },
                **dict(extra_canonical_scopes or {}),
            },
        )
    except CanonicalDefectRegistryError as exc:
        raise CampaignContractError(
            f"PIPELINE_DEGRADED_IDENTITY_MISMATCH:{exc}"
        ) from exc


def _validate_redacted_evaluation_submission(value: Any) -> None:
    envelope = _dict(value)
    if envelope.get("schema_version") != SUBMISSION_SCHEMA:
        raise CampaignContractError("persisted_submission_schema_invalid")
    observed_fingerprint = _text(envelope.get("fingerprint"))
    unsigned = {
        key: item for key, item in envelope.items() if key != "fingerprint"
    }
    expected_fingerprint = hashlib.sha256(
        _canonical_bytes(unsigned)
    ).hexdigest()
    if not observed_fingerprint or observed_fingerprint != expected_fingerprint:
        raise CampaignContractError(
            "persisted_submission_fingerprint_invalid_after_redaction"
        )
    mainline = validate_mainline_run_contract(_dict(envelope.get("mainline_run")))
    scan_result = _dict(envelope.get("scan_result"))
    findings = [
        item for item in _list(scan_result.get("findings"))
        if isinstance(item, dict)
    ]
    if len(findings) != len(_list(scan_result.get("findings"))):
        raise CampaignContractError("persisted_submission_findings_invalid")
    delivery_occurrences = [
        item for item in _list(scan_result.get("delivery_occurrences"))
        if isinstance(item, dict)
    ]
    if len(delivery_occurrences) != len(
        _list(scan_result.get("delivery_occurrences"))
    ):
        raise CampaignContractError(
            "persisted_submission_delivery_occurrences_invalid"
        )
    ledger = _dict(scan_result.get("obligation_attempt_ledger"))
    embedded_authority = validate_formal_delivery_authority_receipt(
        _dict(envelope.get("formal_delivery_authority"))
    )
    rebuilt_authority = build_formal_delivery_authority_receipt(
        mainline_run=mainline,
        findings=delivery_occurrences,
        obligation_attempt_ledger=ledger,
    )
    if rebuilt_authority != embedded_authority:
        raise CampaignContractError(
            "persisted_submission_formal_authority_mismatch"
        )
    if _dict(scan_result.get("formal_delivery_authority")) != embedded_authority:
        raise CampaignContractError(
            "persisted_submission_authority_copy_mismatch"
        )
    try:
        registry = validate_canonical_defect_registry(
            _dict(envelope.get("canonical_defect_registry")),
            mainline_run=mainline,
            deliverable_occurrences=delivery_occurrences,
            obligation_attempt_ledger=ledger,
        )
        if _dict(scan_result.get("canonical_defect_registry")) != registry:
            raise CanonicalDefectRegistryError(
                "persisted_submission_registry_copy_mismatch"
            )
        canonical_findings = canonical_representative_findings(
            registry,
            deliverable_occurrences=delivery_occurrences,
        )
        if findings != canonical_findings:
            raise CanonicalDefectRegistryError(
                "persisted_submission_canonical_findings_mismatch"
            )
        consistency = validate_defect_identity_consistency(
            _dict(envelope.get("defect_identity_consistency")),
            required_occurrence_scopes={
                "delivery_gate_ids",
                "formal_authority_occurrence_ids",
                "registry_occurrence_ids",
                "evaluator_submission_occurrence_ids",
            },
            required_canonical_scopes={
                "canonical_registry_ids",
                "formal_projection_ids",
                "evaluator_submission_ids",
            },
        )
    except CanonicalDefectRegistryError as exc:
        raise CampaignContractError(
            f"persisted_submission_canonical_authority_invalid:{exc}"
        ) from exc
    rebuilt_projection = build_formal_count_projection(
        findings=delivery_occurrences,
        candidate_findings=[],
        obligation_attempt_ledger=ledger,
        mainline_run=mainline,
        canonical_defect_registry=registry,
    )
    if (
        _dict(scan_result.get("formal_count_projection"))
        != rebuilt_projection
        or _dict(envelope.get("formal_count_projection"))
        != rebuilt_projection
    ):
        raise CampaignContractError(
            "persisted_submission_formal_projection_copy_mismatch"
        )
    if (
        _dict(scan_result.get("defect_identity_consistency"))
        != consistency
    ):
        raise CampaignContractError(
            "persisted_submission_identity_consistency_copy_mismatch"
        )


def build_campaign_view(root: Path, project_id: str, campaign_id: str = "") -> dict[str, Any]:
    scan_result = load_scan_result(root, project_id)
    projected = attach_quality_projection_to_scan_result(scan_result)
    mainline_run = validate_mainline_run_contract(
        projected.get("mainline_run") or _v12(projected).get("mainline_run")
    )
    if not mainline_run["customer_outputs_published"]:
        raise CampaignContractError("customer_output_not_authorized")
    campaign = _campaign(projected)
    actual_campaign_id = _text(campaign.get("campaign_id"))
    if campaign_id and actual_campaign_id and campaign_id != actual_campaign_id:
        raise CampaignContractError(
            f"campaign {campaign_id!r} is not the current runtime campaign {actual_campaign_id!r}"
        )
    health = _dict(projected.get("pipeline_health") or _dict(projected.get("discovery_funnel")).get("pipeline_health"))
    health_status = _text(health.get("status")).upper() or "BLOCKED"
    if health_status not in PIPELINE_HEALTH_STATES:
        health_status = "BLOCKED"
    status = _campaign_status(projected)
    if status not in CAMPAIGN_STATES:
        status = "blocked"
    execution_status = _execution_status(projected)
    if execution_status not in EXECUTION_STATES:
        execution_status = "failed_safe"
    selected = _selected_experiments(projected)
    traces = build_identity_traces(projected)
    occurrence_ids = list(
        _dict(projected.get("formal_count_projection")).get(
            "delivery_occurrence_finding_ids"
        )
        or []
    )
    trace_occurrence_ids = _trace_occurrence_ids(projected, occurrence_ids)
    defect_identity_consistency = _extend_projected_identity_consistency(
        projected,
        extra_occurrence_scopes={
            "trace_ledger_occurrence_ids": trace_occurrence_ids,
        },
    )
    if not defect_identity_consistency["consistent"]:
        raise CampaignContractError("PIPELINE_DEGRADED_COUNT_MISMATCH")
    return _fingerprinted({
        "schema_version": CAMPAIGN_VIEW_SCHEMA,
        "campaign_id": actual_campaign_id,
        "project_id": project_id,
        "status": status,
        "pipeline_health": health_status,
        "execution_status": execution_status,
        "campaign": campaign,
        "selected_experiment_count": len(selected),
        "selected_experiments": selected,
        "every_selected_experiment_has_receipt": bool(selected) and all(item["has_receipt"] for item in selected),
        "identity_trace_count": len(traces),
        "complete_identity_trace_count": sum(1 for item in traces if item.get("identity_complete")),
        "identity_traces": traces,
        "mainline_run": mainline_run,
        "canonical_defect_registry": _dict(
            projected.get("canonical_defect_registry")
        ),
        "formal_count_projection": _dict(projected.get("formal_count_projection")),
        "defect_identity_consistency": defect_identity_consistency,
        "finding_classification": _dict(projected.get("finding_classification")),
        "obligation_execution_projection": _dict(projected.get("obligation_execution_projection")),
        "run_delivery_readiness": _dict(projected.get("run_delivery_readiness")),
        "release_gate": _dict(projected.get("release_gate")),
        "commercial_readiness": _dict(projected.get("commercial_readiness")),
        "external_evaluation": _dict(projected.get("external_evaluation")),
        "target_policy_decision": _dict(_dict(_v12(projected).get("runtime_contract")).get("target_policy_decision")),
        "source_fingerprints": _dict(_dict(projected.get("obligation_execution_projection")).get("fingerprints")),
    })


def campaign_slices(view: dict[str, Any]) -> list[dict[str, Any]]:
    traces = build_identity_traces_from_view(view)
    return [
        {
            "slice_id": item.get("slice_id"),
            "obligation_id": item.get("obligation_id"),
            "experiment_id": item.get("experiment_id"),
            "execution_status": item.get("execution_status"),
            "reason_code": item.get("reason_code"),
            "receipt_present": item.get("has_execution_receipt"),
        }
        for item in traces
    ]


def build_identity_traces_from_view(view: dict[str, Any]) -> list[dict[str, Any]]:
    # Views persist only summary counts. Re-load traces through the caller when
    # full identity rows are required; this helper handles pre-attached rows.
    return [item for item in _list(view.get("identity_traces")) if isinstance(item, dict)]


def finding_rows(view: dict[str, Any], classification: str) -> list[dict[str, Any]]:
    normalized = _text(classification).lower() or "deliverable"
    if normalized not in {"deliverable", "candidate", "rejected", "shadow"}:
        raise CampaignContractError(
            "classification must be deliverable, candidate, rejected, or shadow"
        )
    projection = _dict(view.get("finding_classification"))
    rows: list[dict[str, Any]] = []
    for item in _list(projection.get(normalized)):
        if not isinstance(item, dict):
            continue
        row = dict(item)
        identity = _text(
            row.get("canonical_defect_id")
            if normalized == "deliverable"
            else ""
        ) or _text(
            row.get("id")
            or row.get("finding_id")
            or row.get("bug_id")
            or row.get("risk_id")
        )
        if not identity:
            raise CampaignContractError(
                f"finding identity missing for classification {normalized}"
            )
        row["id"] = identity
        if normalized == "deliverable":
            row["canonical_defect_id"] = identity
        row["finding_id"] = identity
        row["finding_class"] = normalized
        rows.append(row)
    return rows


def finding_resource(view: dict[str, Any], finding_id: str) -> tuple[str, dict[str, Any]]:
    for classification in ("deliverable", "candidate", "rejected", "shadow"):
        for item in finding_rows(view, classification):
            identity = _text(item.get("id") or item.get("finding_id") or item.get("bug_id"))
            if identity == finding_id:
                return classification, item
    raise CampaignContractError(f"finding not found: {finding_id}")


def build_evaluation_submission(root: Path, project_id: str, body: dict[str, Any]) -> dict[str, Any]:
    scan_result = load_scan_result(root, project_id)
    projected = attach_quality_projection_to_scan_result(scan_result)
    v12 = _v12(projected)
    mainline_run = validate_mainline_run_contract(
        projected.get("mainline_run") or v12.get("mainline_run")
    )
    requested_mode = _text(body.get("evaluation_mode") or mainline_run["evaluation_mode"]).lower()
    if requested_mode != mainline_run["evaluation_mode"]:
        raise CampaignContractError("evaluation_mode_mainline_contract_mismatch")
    if not mainline_run["product_evaluation_submission_published"]:
        raise CampaignContractError("product_evaluation_submission_not_authorized")
    operational = _dict(projected.get("operational_metrics") or v12.get("operational_metrics"))
    required_operational = (
        "wall_clock_seconds",
        "estimated_cost_usd",
        "request_count",
        "production_http_requests",
        "cleanup_failures",
        "safety_incidents",
        "dirty_test_environments",
        "execution_success_rate",
        "engine_success_rate",
        "duplicate_rate",
    )
    metrics = {key: operational.get(key) for key in required_operational}
    if metrics["wall_clock_seconds"] is None and v12.get("total_duration_ms") is not None:
        metrics["wall_clock_seconds"] = float(v12.get("total_duration_ms") or 0) / 1000.0
    pipeline_health = _dict(projected.get("pipeline_health") or _dict(projected.get("discovery_funnel")).get("pipeline_health"))
    if metrics["cleanup_failures"] is None:
        metrics["cleanup_failures"] = pipeline_health.get("cleanup_failure_count")
    submission_id = f"evalsub_{uuid.uuid4().hex}"
    run_id = mainline_run["run_id"]
    requested_run_id = _text(body.get("run_id"))
    if requested_run_id and requested_run_id != run_id:
        raise CampaignContractError("run_id_mainline_contract_mismatch")
    policy_id = _text(
        projected.get("policy_id")
        or _dict(projected.get("policy")).get("policy_id")
        or _campaign(projected).get("policy_id")
        or mainline_run.get("policy_version")
    )
    if not policy_id:
        raise CampaignContractError("policy_id_missing")
    requested_policy_id = _text(body.get("policy_id"))
    if requested_policy_id and requested_policy_id != policy_id:
        raise CampaignContractError("policy_id_runtime_contract_mismatch")
    canonical_findings = [
        item for item in _list(projected.get("findings")) if isinstance(item, dict)
    ]
    delivery_occurrences = [
        item
        for item in _list(
            projected.get("delivery_occurrences")
            or v12.get("delivery_occurrences")
        )
        if isinstance(item, dict)
    ]
    canonical_ids = list(
        _dict(projected.get("formal_count_projection")).get(
            "canonical_defect_ids"
        )
        or []
    )
    occurrence_ids = list(
        _dict(projected.get("formal_count_projection")).get(
            "delivery_occurrence_finding_ids"
        )
        or []
    )
    evaluator_submission_ids = sorted({
        _text(item.get("canonical_defect_id"))
        for item in canonical_findings
        if _text(item.get("canonical_defect_id"))
    })
    evaluator_submission_occurrence_ids = sorted({
        _text(item.get("finding_id") or item.get("id"))
        for item in delivery_occurrences
        if _text(item.get("finding_id") or item.get("id"))
    })
    trace_occurrence_ids = _trace_occurrence_ids(projected, occurrence_ids)
    defect_identity_consistency = _extend_projected_identity_consistency(
        projected,
        extra_occurrence_scopes={
            "formal_authority_occurrence_ids": occurrence_ids,
            "evaluator_submission_occurrence_ids": (
                evaluator_submission_occurrence_ids
            ),
            "trace_ledger_occurrence_ids": trace_occurrence_ids,
        },
        extra_canonical_scopes={
            "evaluator_submission_ids": evaluator_submission_ids,
        },
    )
    if not defect_identity_consistency["consistent"]:
        raise CampaignContractError("PIPELINE_DEGRADED_COUNT_MISMATCH")
    validated_attempt_ledger = _validated_attempt_ledger_for_submission(
        projected,
        formal_ids=occurrence_ids,
    )
    try:
        canonical_registry = validate_canonical_defect_registry(
            _dict(projected.get("canonical_defect_registry")),
            mainline_run=mainline_run,
            deliverable_occurrences=delivery_occurrences,
            obligation_attempt_ledger=validated_attempt_ledger,
        )
        if canonical_registry["canonical_defect_ids"] != canonical_ids:
            raise CanonicalDefectRegistryError(
                "campaign_canonical_projection_mismatch"
            )
        if canonical_representative_findings(
            canonical_registry,
            deliverable_occurrences=delivery_occurrences,
        ) != canonical_findings:
            raise CanonicalDefectRegistryError(
                "campaign_canonical_findings_mismatch"
            )
        formal_delivery_authority = build_formal_delivery_authority_receipt(
            mainline_run=mainline_run,
            findings=delivery_occurrences,
            obligation_attempt_ledger=validated_attempt_ledger,
        )
    except (FormalDeliveryAuthorityError, CanonicalDefectRegistryError) as exc:
        raise CampaignContractError(
            f"formal_delivery_authority_invalid:{exc}"
        ) from exc
    formal_count_projection = build_formal_count_projection(
        findings=delivery_occurrences,
        candidate_findings=[],
        obligation_attempt_ledger=validated_attempt_ledger,
        mainline_run=mainline_run,
        canonical_defect_registry=canonical_registry,
    )
    envelope = _fingerprinted({
        "schema_version": SUBMISSION_SCHEMA,
        "submission_id": submission_id,
        "run_id": run_id,
        "campaign_id": mainline_run["campaign_id"],
        "project_id": project_id,
        "policy_id": policy_id,
        "evaluation_mode": requested_mode,
        "created_at_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ground_truth_included": False,
        "measurement_status": "NOT_MEASURED",
        "pipeline_health": pipeline_health,
        "operational_metrics": metrics,
        "scan_result": {
            # Canonical representatives are the evaluator scoring unit;
            # occurrences remain the complete auditable evidence scope.
            "findings": canonical_findings,
            "delivery_occurrences": delivery_occurrences,
            "candidate_findings": [],
            "obligation_attempt_ledger": validated_attempt_ledger,
            "canonical_defect_registry": canonical_registry,
            "formal_count_projection": formal_count_projection,
            "defect_identity_consistency": defect_identity_consistency,
            "formal_delivery_authority": formal_delivery_authority,
        },
        "mainline_run": mainline_run,
        "canonical_defect_registry": canonical_registry,
        "formal_count_projection": formal_count_projection,
        "defect_identity_consistency": defect_identity_consistency,
        "formal_delivery_authority": formal_delivery_authority,
        "source_fingerprints": _dict(_dict(projected.get("obligation_execution_projection")).get("fingerprints")),
    })
    output = root / "platform_outputs" / project_id / "evaluation_submissions" / f"{submission_id}.json"
    redaction_receipt = write_json_redacted(
        output,
        envelope,
        post_redaction_validator=_validate_redacted_evaluation_submission,
    )
    return {
        **envelope,
        "artifact_ref": str(output),
        "redaction_receipt": redaction_receipt,
        "operator_action": "Submit this envelope to the evaluator-owned service; only its signed receipt can change measurement status.",
    }
