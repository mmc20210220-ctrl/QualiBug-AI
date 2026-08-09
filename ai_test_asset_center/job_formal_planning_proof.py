"""Project receipt-backed ASYNC_JOB planning evidence from the formal scan result.

This module is deliberately a projection over the existing Behavior IR, Test Obligation,
Planner and Experiment Compiler authorities.  It does not create a Job-specific planner,
compiler, finding authority or receipt store, and it never upgrades runtime-integrity
compilation into a claim that a real Job platform was executed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .artifact_redactor import write_json_redacted
from .product_scan_mainline import _safe_project
from .scan_post_hooks import register_scan_post_hook

SCHEMA_VERSION = "qualibug.job-formal-planning-proof.v1"
HOOK_NAME = "job_formal_planning_proof"
TEMPLATE = "source_declared_async_job_execution"
PROTOCOL_ID = f"process:{TEMPLATE}"
INVARIANT_KIND = "async_job_runtime_integrity_contract"


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _fingerprint(value: Any) -> str:
    material = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


def _v12(scan_result: dict[str, Any]) -> dict[str, Any]:
    nested = _dict(scan_result.get("v12"))
    return nested or scan_result


def _job_operations(v12: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(v12.get("behavior_ir")).get("operations"))
        if isinstance(row, dict)
        and _text(row.get("operation_kind")).upper() == "ASYNC_JOB"
    ]


def _job_invariants(v12: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(v12.get("behavior_ir")).get("invariants"))
        if isinstance(row, dict)
        and _text(_dict(row.get("expression")).get("kind")) == INVARIANT_KIND
    ]


def _job_obligations(v12: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(_dict(v12.get("test_obligations")).get("obligations"))
        if isinstance(row, dict)
        and _text(_dict(row.get("property")).get("template")) == TEMPLATE
    ]


def _all_experiments(v12: dict[str, Any]) -> list[dict[str, Any]]:
    compile_pack = _dict(v12.get("experiment_compile"))
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for collection_name in ("all_experiments", "experiments"):
        for raw in _list(compile_pack.get(collection_name)):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            experiment_id = _text(row.get("experiment_id"))
            identity = experiment_id or _fingerprint(row)
            if identity in seen:
                continue
            seen.add(identity)
            rows.append(row)
    return rows


def _job_experiments(v12: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for row in _all_experiments(v12):
        lineage = _dict(row.get("async_job_lineage_receipt"))
        template = _text(_dict(row.get("property")).get("template"))
        treatment = [
            dict(step)
            for step in _list(row.get("treatment_plan"))
            if isinstance(step, dict)
        ]
        if (
            lineage
            or template == TEMPLATE
            or any(_text(step.get("intent")) == TEMPLATE for step in treatment)
        ):
            rows.append(row)
    return rows


def _selected_obligation_ids(v12: dict[str, Any]) -> set[str]:
    return {
        _text(row.get("obligation_id"))
        for row in _list(_dict(v12.get("obligation_plan")).get("selected"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }


def _compile_status(experiment: dict[str, Any]) -> str:
    return _text(
        _dict(experiment.get("compile_receipt")).get("status")
        or experiment.get("compile_status")
    ).upper()


def _proof_rows(v12: dict[str, Any]) -> list[dict[str, Any]]:
    selected_ids = _selected_obligation_ids(v12)
    rows: list[dict[str, Any]] = []
    for experiment in _job_experiments(v12):
        lineage = _dict(experiment.get("async_job_lineage_receipt"))
        obligation_id = _text(
            experiment.get("obligation_id") or lineage.get("obligation_id")
        )
        treatment = [
            dict(step)
            for step in _list(experiment.get("treatment_plan"))
            if isinstance(step, dict)
        ]
        treatment_step_ids = [
            _text(step.get("step_id") or step.get("id"))
            for step in treatment
            if _text(step.get("step_id") or step.get("id"))
        ]
        job_treatment_present = any(
            _text(step.get("step_id") or step.get("id")) == "job_treatment_1"
            and _text(step.get("method")).upper() == "JOB"
            and _text(step.get("intent")) == TEMPLATE
            for step in treatment
        )
        rows.append(
            {
                "job_asset_id": _text(lineage.get("job_asset_id")),
                "operation_id": _text(lineage.get("operation_id")),
                "behavior_id": _text(lineage.get("behavior_id")),
                "invariant_id": _text(lineage.get("invariant_id")),
                "obligation_id": obligation_id,
                "experiment_id": _text(
                    experiment.get("experiment_id") or lineage.get("experiment_id")
                ),
                "protocol_id": _text(lineage.get("protocol_id")) or PROTOCOL_ID,
                "compile_status": _compile_status(experiment),
                "selected": obligation_id in selected_ids,
                "treatment_step_ids": treatment_step_ids,
                "job_treatment_present": job_treatment_present,
                "runtime_integrity_only": bool(
                    _dict(experiment.get("assertion")).get("runtime_integrity_only")
                    or _dict(experiment.get("property")).get("runtime_integrity_only")
                ),
                "formal_business_finding_eligible": bool(
                    _dict(experiment.get("assertion")).get(
                        "formal_business_finding_eligible"
                    )
                    or _dict(experiment.get("property")).get(
                        "formal_business_finding_eligible"
                    )
                ),
                "identity_complete": bool(lineage.get("identity_complete")),
                "identity_drift": bool(lineage.get("identity_drift")),
                "lineage_fingerprint": _text(lineage.get("fingerprint")),
                "compile_lineage_fingerprint": _text(
                    _dict(experiment.get("compile_receipt")).get(
                        "async_job_lineage_fingerprint"
                    )
                ),
            }
        )
    return rows


def build_job_formal_planning_proof(
    scan_result: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build one immutable, redacted planning proof from existing scan structures."""
    result = _dict(scan_result)
    v12 = _v12(result)
    operations = _job_operations(v12)
    invariants = _job_invariants(v12)
    obligations = _job_obligations(v12)
    rows = _proof_rows(v12)
    selected_ids = _selected_obligation_ids(v12)

    compiled_rows = [row for row in rows if row["compile_status"] == "COMPILED"]
    selected_rows = [row for row in compiled_rows if row["selected"]]
    complete_rows = [
        row
        for row in selected_rows
        if row["identity_complete"]
        and not row["identity_drift"]
        and row["job_treatment_present"]
        and row["protocol_id"] == PROTOCOL_ID
        and row["runtime_integrity_only"]
        and not row["formal_business_finding_eligible"]
        and row["lineage_fingerprint"]
        and row["lineage_fingerprint"] == row["compile_lineage_fingerprint"]
    ]

    if not obligations and not rows:
        status = "NOT_REQUESTED"
        first_terminal_reason = "ASYNC_JOB_OBLIGATION_NOT_GENERATED"
    elif not compiled_rows:
        status = "BLOCKED"
        first_terminal_reason = "ASYNC_JOB_EXPERIMENT_NOT_COMPILED"
    elif not selected_rows:
        status = "COMPILED_NOT_SELECTED"
        first_terminal_reason = "ASYNC_JOB_OBLIGATION_NOT_SELECTED"
    elif any(row["identity_drift"] for row in selected_rows):
        status = "FAILED_SAFE"
        first_terminal_reason = "ASYNC_JOB_LINEAGE_IDENTITY_DRIFT"
    elif any(not row["job_treatment_present"] for row in selected_rows):
        status = "FAILED_SAFE"
        first_terminal_reason = "ASYNC_JOB_PROTOCOL_TREATMENT_NOT_MATERIALIZED"
    elif len(complete_rows) == len(selected_rows):
        status = "PASS"
        first_terminal_reason = ""
    else:
        status = "PARTIAL"
        first_terminal_reason = "ASYNC_JOB_PLANNING_PROOF_INCOMPLETE"

    proof: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "first_terminal_reason": first_terminal_reason,
        "scan_identity": {
            "scan_id": _text(result.get("scan_id")),
            "project": _text(result.get("project")),
            "run_id": _text(_dict(v12.get("mainline_run")).get("run_id")),
            "campaign_id": _text(
                _dict(v12.get("mainline_run")).get("campaign_id")
            ),
            "mainline_contract_fingerprint": _text(
                _dict(v12.get("mainline_run")).get("contract_fingerprint")
            ),
        },
        "metrics": {
            "async_job_operation_count": len(operations),
            "job_runtime_invariant_count": len(invariants),
            "job_obligation_count": len(obligations),
            "job_experiment_count": len(rows),
            "compiled_job_experiment_count": len(compiled_rows),
            "selected_job_obligation_count": len(
                {
                    row["obligation_id"] for row in selected_rows if row["obligation_id"]
                }
            ),
            "selected_obligation_pool_count": len(selected_ids),
            "complete_lineage_count": len(complete_rows),
            "lineage_drift_count": sum(1 for row in rows if row["identity_drift"]),
            "job_treatment_materialized_count": sum(
                1 for row in rows if row["job_treatment_present"]
            ),
            "formal_business_finding_eligible_count": sum(
                1 for row in rows if row["formal_business_finding_eligible"]
            ),
            "new_findings_created_by_projection": 0,
        },
        "experiments": rows,
        "claim_boundary": {
            "job_asset_to_compiled_experiment": (
                "PROVEN_BY_FORMAL_SCAN_RESULT" if status == "PASS" else "NOT_PROVEN"
            ),
            "post_api_v1_scan": "NOT_ATTESTED_BY_RESULT_PROJECTION",
            "real_job_platform_runtime": "NOT_MEASURED",
            "job_true_completed": "NOT_MEASURED",
            "job_business_side_effect_oracle": "NOT_MEASURED",
            "job_bug_discovery": "NOT_MEASURED",
        },
        "safety": {
            "creates_findings": False,
            "contains_raw_request_or_response_bodies": False,
            "contains_credentials": False,
            "contains_benchmark_ground_truth": False,
            "parallel_planner_or_compiler_created": False,
        },
    }
    unsigned = dict(proof)
    proof["proof_fingerprint"] = _fingerprint(unsigned)
    proof["proof_id"] = f"job_planning_proof_{proof['proof_fingerprint'][:24]}"
    return proof


def attach_job_formal_planning_proof(
    scan_result: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    """Attach and persist the proof without changing finding or execution authority."""
    if not isinstance(scan_result, dict):
        return scan_result
    projected = dict(scan_result)
    proof = build_job_formal_planning_proof(projected)
    safe_project = _safe_project(project or _text(projected.get("project")))
    output_dir = Path(root) / "platform_outputs" / safe_project / "job_02_proof"
    proof_path = output_dir / "job_planning_proof.json"
    write_json_redacted(proof_path, proof)
    proof_ref = str(proof_path.relative_to(Path(root))).replace("\\", "/")
    projected["job_planning_proof"] = proof
    projected["job_planning_proof_ref"] = proof_ref

    # _scan_impl persists scan_result.json before post-hooks run. Re-write the same
    # product artifact through the existing redaction authority so a POST /api/v1/scan
    # has a durable proof even though the customer HTTP envelope remains intentionally
    # compact. This does not alter any execution, finding or evaluator record.
    # Sharded store keeps the same content; big keys stay in scan_result.parts/.
    scan_result_path = Path(root) / "platform_outputs" / safe_project / "scan_result.json"
    from .scan_result_store import write_scan_result

    write_scan_result(scan_result_path, projected)
    return projected


def install_job_formal_planning_proof() -> None:
    register_scan_post_hook(HOOK_NAME, attach_job_formal_planning_proof)


install_job_formal_planning_proof()


__all__ = [
    "SCHEMA_VERSION",
    "HOOK_NAME",
    "build_job_formal_planning_proof",
    "attach_job_formal_planning_proof",
    "install_job_formal_planning_proof",
]
