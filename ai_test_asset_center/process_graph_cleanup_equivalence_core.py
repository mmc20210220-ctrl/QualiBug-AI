"""Per-source-step cleanup execution and equivalence aggregation for graphs.

Every graph write is evaluated by the existing single-write cleanup equivalence
engine using that node's own WriteReversibilityProof and governance snapshots.
This module only scopes evidence and aggregates formal receipts; it never
reimplements business-state comparison.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

from . import cleanup_equivalence_core as _equivalence_core
from .cleanup_execution_receipt import build_cleanup_execution_receipt
from .process_graph_reversibility import (
    GRAPH_REVERSIBILITY_SCHEMA,
    is_process_graph_reversibility_proof,
)


GRAPH_CLEANUP_EXECUTION_SET_SCHEMA = (
    "qualibug.process-graph-cleanup-execution-set.v1"
)
GRAPH_CLEANUP_EQUIVALENCE_SCHEMA = (
    "qualibug.process-graph-cleanup-equivalence-receipt.v1"
)
GRAPH_ENVIRONMENT_RESTORATION_PENDING = (
    "qualibug.environment-restoration-receipt.v1"
)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _stable_hash(value: Any) -> str:
    raw = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _rows_by_identity(
    rows: list[dict[str, Any]],
    *,
    key: str,
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in rows:
        row = _dict(raw)
        identity = _text(row.get(key))
        if identity:
            result.setdefault(identity, []).append(row)
    return result


def _source_steps(steps_out: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in steps_out:
        row = _dict(raw)
        if _text(row.get("phase")) != "treatment":
            continue
        step_id = _text(row.get("step_id") or row.get("subject_id"))
        if step_id and isinstance(row.get("governance_receipt"), dict):
            result.setdefault(step_id, []).append(row)
    return result


def _cleanup_steps(steps_out: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in steps_out:
        row = _dict(raw)
        if _text(row.get("phase")) != "cleanup":
            continue
        source_step_id = _text(
            row.get("compensates_step_id") or row.get("source_step_id")
        )
        if source_step_id:
            result.setdefault(source_step_id, []).append(row)
    return result


def _graph_cleanup_receipts(
    observations: dict[str, Any],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for raw in _list(observations.get("process_graph_cleanup_receipts")):
        row = _dict(raw)
        source_step_id = _text(_dict(row.get("evidence")).get("source_step_id"))
        if source_step_id:
            result.setdefault(source_step_id, []).append(row)
    return result


def _observation_from_governance(
    governance: dict[str, Any],
    key: str,
    *,
    phase: str,
    fallback_path: str = "",
) -> dict[str, Any]:
    observed = _dict(governance.get(key))
    status = int(observed.get("status") or observed.get("status_code") or 0)
    if not observed or status <= 0:
        return {}
    return {
        "status_code": status,
        "body": observed.get("body"),
        "path": _text(
            governance.get("observation_path") or fallback_path
        ),
        "phase": phase,
        "source": "process_graph_governance",
    }


def _cleanup_status_for_step(
    graph_receipts: list[dict[str, Any]],
    cleanup_rows: list[dict[str, Any]],
) -> str:
    if len(cleanup_rows) == 1:
        return "completed"
    statuses = {
        _text(row.get("status")).upper()
        for row in graph_receipts
        if _text(row.get("status"))
    }
    if statuses == {"NOT_REQUIRED"}:
        return "not_required"
    if "FAILED" in statuses or "BLOCKED" in statuses:
        return "blocked"
    return "not_attempted"


def _pending_environment_receipt(
    *,
    experiment_id: str,
    campaign_id: str,
    graph_proof_id: str,
    cleanup_execution_set_id: str,
) -> dict[str, Any]:
    receipt_id = "envr_" + _stable_hash(
        {
            "experiment_id": experiment_id,
            "campaign_id": campaign_id,
            "graph_proof_id": graph_proof_id,
            "cleanup_execution_set_id": cleanup_execution_set_id,
        }
    )[:32]
    return {
        "schema_version": GRAPH_ENVIRONMENT_RESTORATION_PENDING,
        "receipt_id": receipt_id,
        "experiment_id": experiment_id,
        "campaign_id": campaign_id,
        "database_cleanup_receipt_ids": [],
        "api_cleanup_receipt_ids": [],
        "fixture_receipt_ids": [],
        "created_rows_remaining": 0,
        "modified_rows_not_restored": 0,
        "deleted_rows_not_restored": 0,
        "cleanup_failures": [],
        "baseline_comparison": {
            "relevant_tables_match": False,
            "relevant_fields_match": False,
        },
        "environment_restored": False,
        "final_status": "PENDING_EQUIVALENCE",
        "authority": "process_graph_cleanup_equivalence",
        "graph_proof_id": graph_proof_id,
    }


def finalize_process_graph_cleanup_equivalence_inputs(
    *,
    exp: dict[str, Any],
    result: dict[str, Any],
    resolved_campaign_id: str,
    runtime_bindings: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build one CER and one equivalence input bundle per graph write step."""
    output = dict(result)
    observations = _dict(output.get("observations"))
    steps_out = [
        row for row in _list(output.get("steps_out")) if isinstance(row, dict)
    ]
    proof = _dict(exp.get("write_reversibility_proof"))
    contract = _dict(exp.get("process_graph_write_contract"))
    if not is_process_graph_reversibility_proof(proof):
        return output

    write_step_ids = [
        _text(value)
        for value in _list(proof.get("write_step_ids"))
        if _text(value)
    ]
    step_proofs = _dict(proof.get("step_proofs_by_id"))
    cleanup_contracts = {
        _text(row.get("source_step_id")): row
        for row in _list(contract.get("cleanup_steps"))
        if isinstance(row, dict) and _text(row.get("source_step_id"))
    }
    source_rows = _source_steps(steps_out)
    cleanup_rows = _cleanup_steps(steps_out)
    graph_receipts = _graph_cleanup_receipts(observations)

    node_execution_receipts: dict[str, dict[str, Any]] = {}
    step_inputs: dict[str, dict[str, Any]] = {}
    cleanup_failures = int(output.get("cleanup_failures") or 0)
    runtime_values = dict(runtime_bindings or {})

    for source_step_id in write_step_ids:
        node_proof = _dict(step_proofs.get(source_step_id))
        cleanup_contract = _dict(cleanup_contracts.get(source_step_id))
        sources = source_rows.get(source_step_id, [])
        cleanups = cleanup_rows.get(source_step_id, [])
        graph_rows = graph_receipts.get(source_step_id, [])
        source = sources[0] if len(sources) == 1 else {}
        cleanup_row = cleanups[0] if len(cleanups) == 1 else {}
        cleanup_status = _cleanup_status_for_step(graph_rows, cleanups)

        node_cer = build_cleanup_execution_receipt(
            experiment_id=(
                f"{_text(exp.get('experiment_id'))}::{source_step_id}"
            ),
            proof_id=_text(node_proof.get("proof_id")),
            cleanup_plan=([cleanup_contract] if cleanup_contract else []),
            steps_out=([cleanup_row] if cleanup_row else []),
            cleanup_failures=(
                0
                if cleanup_status in {"completed", "not_required"}
                else 1
            ),
            cleanup_status=cleanup_status,
            proof=node_proof,
            adapter_cleanup_receipts=[],
        )
        node_cer["source_step_id"] = source_step_id
        node_cer["system_ref"] = _text(cleanup_contract.get("system_ref"))
        node_execution_receipts[source_step_id] = node_cer

        source_governance = _dict(source.get("governance_receipt"))
        cleanup_governance = _dict(cleanup_row.get("governance_receipt"))
        before_observation = _observation_from_governance(
            source_governance,
            "before",
            phase="before",
            fallback_path=_text(source.get("observation_path")),
        )
        after_write_observation = _observation_from_governance(
            source_governance,
            "after",
            phase="after_write",
            fallback_path=_text(source.get("observation_path")),
        )
        after_cleanup_observation = _observation_from_governance(
            cleanup_governance,
            "after",
            phase="after_cleanup",
            fallback_path=_text(cleanup_row.get("observation_path")),
        )
        step_inputs[source_step_id] = {
            "source_step_id": source_step_id,
            "system_ref": _text(cleanup_contract.get("system_ref")),
            "proof": node_proof,
            "before_observation": before_observation,
            "after_write_observation": after_write_observation,
            "after_cleanup_observation": after_cleanup_observation,
            "runtime_bindings": runtime_values,
            "cleanup_execution_receipt": node_cer,
            "source_step_identity_valid": len(sources) == 1,
            "cleanup_step_identity_valid": (
                len(cleanups) == 1
                or _text(node_cer.get("status")).upper() == "NOT_REQUIRED"
            ),
        }

        ledger = observations.get("process_step_ledger")
        receipt_id = _text(node_cer.get("receipt_id"))
        if (
            receipt_id
            and ledger is not None
            and hasattr(ledger, "append_scoped_receipt_ref")
        ):
            ledger.append_scoped_receipt_ref(
                step_id=source_step_id,
                field="cleanup_receipt_ids",
                receipt_id=receipt_id,
                receipt_step_id=source_step_id,
            )

    source_receipt_ids = [
        _text(node_execution_receipts[step_id].get("receipt_id"))
        for step_id in write_step_ids
        if _text(node_execution_receipts[step_id].get("receipt_id"))
    ]
    execution_set_id = "pgces_" + _stable_hash(
        {
            "proof_id": _text(proof.get("proof_id")),
            "write_step_ids": write_step_ids,
            "source_receipt_ids": source_receipt_ids,
        }
    )[:24]
    accepted_statuses = {"ACCEPTED", "NOT_REQUIRED"}
    all_accepted = bool(write_step_ids) and all(
        _text(node_execution_receipts[step_id].get("status")).upper()
        in accepted_statuses
        for step_id in write_step_ids
    )
    aggregate = {
        "schema_version": GRAPH_CLEANUP_EXECUTION_SET_SCHEMA,
        "receipt_id": execution_set_id,
        "experiment_id": _text(exp.get("experiment_id")),
        "proof_id": _text(proof.get("proof_id")),
        "process_graph_write_contract_id": _text(contract.get("contract_id")),
        "write_step_ids": list(write_step_ids),
        "cleanup_order": list(_list(contract.get("cleanup_order"))),
        "attempted": any(
            bool(row.get("attempted"))
            for row in node_execution_receipts.values()
        ),
        "transport_reached": any(
            bool(row.get("transport_reached"))
            for row in node_execution_receipts.values()
        ),
        "succeeded": all_accepted and cleanup_failures == 0,
        "status": "ACCEPTED" if all_accepted and cleanup_failures == 0 else "BLOCKED",
        "status_code": 200 if all_accepted and cleanup_failures == 0 else 0,
        "source_receipt_ids": source_receipt_ids,
        "step_cleanup_execution_receipts_by_id": node_execution_receipts,
        "step_inputs_by_id": step_inputs,
        "fingerprint": "",
    }
    aggregate["fingerprint"] = _stable_hash(
        {
            "receipt_id": execution_set_id,
            "proof_id": aggregate["proof_id"],
            "write_step_ids": write_step_ids,
            "source_receipt_ids": source_receipt_ids,
            "status": aggregate["status"],
        }
    )[:32]
    environment_receipt = _pending_environment_receipt(
        experiment_id=_text(exp.get("experiment_id")),
        campaign_id=resolved_campaign_id,
        graph_proof_id=_text(proof.get("proof_id")),
        cleanup_execution_set_id=execution_set_id,
    )
    environment_receipt["api_cleanup_receipt_ids"] = list(
        source_receipt_ids
    )
    aggregate["environment_restoration_receipt"] = environment_receipt

    observations["cleanup_execution_receipt"] = aggregate
    # The execution bundle consumes one graph-scoped receipt. Per-node receipts
    # remain explicitly addressable and are bound to their source ledger rows.
    observations["cleanup_execution_receipts"] = [aggregate]
    observations["cleanup_execution_receipt_ids"] = [execution_set_id]
    observations["process_graph_step_cleanup_execution_receipts"] = [
        node_execution_receipts[step_id] for step_id in write_step_ids
    ]
    observations["process_graph_step_cleanup_execution_receipts_by_id"] = (
        node_execution_receipts
    )
    observations["process_graph_cleanup_equivalence_inputs_by_step"] = (
        step_inputs
    )
    observations["environment_restoration_receipt"] = environment_receipt
    observations["environment_restored"] = False

    output["observations"] = observations
    output["steps_out"] = steps_out
    output["cleanup_failures"] = cleanup_failures
    return output


def _update_environment_receipt(
    execution_set: dict[str, Any],
    *,
    equivalence_status: str,
    reason_codes: list[str],
    step_receipt_ids: list[str],
) -> None:
    environment = _dict(
        execution_set.get("environment_restoration_receipt")
    )
    if not environment:
        return
    environment["cleanup_equivalence_receipt_ids"] = list(
        step_receipt_ids
    )
    if equivalence_status == "EQUIVALENT":
        environment.update(
            {
                "created_rows_remaining": 0,
                "modified_rows_not_restored": 0,
                "deleted_rows_not_restored": 0,
                "cleanup_failures": [],
                "baseline_comparison": {
                    "relevant_tables_match": True,
                    "relevant_fields_match": True,
                },
                "environment_restored": True,
                "final_status": "ENVIRONMENT_RESTORED",
            }
        )
        return
    environment.update(
        {
            "created_rows_remaining": 1,
            "cleanup_failures": [
                {
                    "reason": (
                        "process_graph_cleanup_not_equivalent"
                        if equivalence_status == "NOT_EQUIVALENT"
                        else "process_graph_cleanup_equivalence_indeterminate"
                    ),
                    "reason_codes": list(reason_codes),
                }
            ],
            "baseline_comparison": {
                "relevant_tables_match": False,
                "relevant_fields_match": False,
            },
            "environment_restored": False,
            "final_status": (
                "ENVIRONMENT_DIRTY"
                if equivalence_status == "NOT_EQUIVALENT"
                else "CLEANUP_FAILED"
            ),
        }
    )


def evaluate_process_graph_cleanup_equivalence(
    *,
    proof: dict[str, Any],
    cleanup_execution_receipt: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate every graph write with the existing single-write engine."""
    graph_proof = _dict(proof)
    execution_set = _dict(cleanup_execution_receipt)
    write_step_ids = [
        _text(value)
        for value in _list(graph_proof.get("write_step_ids"))
        if _text(value)
    ]
    inputs = _dict(execution_set.get("step_inputs_by_id"))
    proof_steps = _dict(graph_proof.get("step_proofs_by_id"))
    node_receipts: dict[str, dict[str, Any]] = {}
    reason_codes: list[str] = []

    scope_valid = bool(
        is_process_graph_reversibility_proof(graph_proof)
        and _text(execution_set.get("schema_version"))
        == GRAPH_CLEANUP_EXECUTION_SET_SCHEMA
        and write_step_ids
        and set(inputs) == set(write_step_ids)
        and set(proof_steps) == set(write_step_ids)
    )
    if scope_valid:
        for source_step_id in write_step_ids:
            row = _dict(inputs.get(source_step_id))
            if (
                row.get("source_step_identity_valid") is not True
                or row.get("cleanup_step_identity_valid") is not True
            ):
                node_receipts[source_step_id] = {
                    "schema_version": "qualibug.cleanup-equivalence-receipt.v1",
                    "receipt_id": "ceq_"
                    + _stable_hash(
                        {
                            "proof_id": _text(
                                _dict(proof_steps.get(source_step_id)).get(
                                    "proof_id"
                                )
                            ),
                            "source_step_id": source_step_id,
                            "reason": "step_identity_invalid",
                        }
                    )[:32],
                    "proof_id": _text(
                        _dict(proof_steps.get(source_step_id)).get("proof_id")
                    ),
                    "equivalence_status": "INDETERMINATE",
                    "reason_code": "PROCESS_GRAPH_STEP_EVIDENCE_IDENTITY_INVALID",
                    "detail": f"source_step={source_step_id}",
                    "source_step_id": source_step_id,
                }
                continue
            node_receipt = _equivalence_core.evaluate_cleanup_equivalence(
                proof=_dict(proof_steps.get(source_step_id)),
                before_observation=_dict(row.get("before_observation")),
                after_write_observation=_dict(
                    row.get("after_write_observation")
                ),
                after_cleanup_observation=_dict(
                    row.get("after_cleanup_observation")
                ),
                runtime_bindings=_dict(row.get("runtime_bindings")),
                cleanup_execution_receipt=_dict(
                    row.get("cleanup_execution_receipt")
                ),
            )
            node_receipt["source_step_id"] = source_step_id
            node_receipt["system_ref"] = _text(row.get("system_ref"))
            node_receipts[source_step_id] = node_receipt
    else:
        reason_codes.append("PROCESS_GRAPH_EQUIVALENCE_SCOPE_INVALID")

    statuses = [
        _text(node_receipts.get(step_id, {}).get("equivalence_status")).upper()
        for step_id in write_step_ids
    ]
    if not scope_valid or len(node_receipts) != len(write_step_ids):
        aggregate_status = "INDETERMINATE"
    elif any(status == "NOT_EQUIVALENT" for status in statuses):
        aggregate_status = "NOT_EQUIVALENT"
    elif any(
        status not in {"EQUIVALENT", "NOT_APPLICABLE"}
        for status in statuses
    ):
        aggregate_status = "INDETERMINATE"
    else:
        aggregate_status = "EQUIVALENT"

    for step_id in write_step_ids:
        reason = _text(node_receipts.get(step_id, {}).get("reason_code"))
        if reason:
            reason_codes.append(f"{step_id}:{reason}")
    reason_codes = list(dict.fromkeys(reason_codes))
    step_receipt_ids = [
        _text(node_receipts.get(step_id, {}).get("receipt_id"))
        for step_id in write_step_ids
        if _text(node_receipts.get(step_id, {}).get("receipt_id"))
    ]
    aggregate_receipt = {
        "schema_version": GRAPH_CLEANUP_EQUIVALENCE_SCHEMA,
        "receipt_id": "pgceq_"
        + _stable_hash(
            {
                "proof_id": _text(graph_proof.get("proof_id")),
                "execution_set_id": _text(execution_set.get("receipt_id")),
                "step_receipt_ids": step_receipt_ids,
                "status": aggregate_status,
            }
        )[:32],
        "proof_id": _text(graph_proof.get("proof_id")),
        "process_graph_write_contract_id": _text(
            graph_proof.get("process_graph_write_contract_id")
        ),
        "cleanup_execution_receipt_id": _text(
            execution_set.get("receipt_id")
        ),
        "write_step_ids": list(write_step_ids),
        "cleanup_order": list(_list(graph_proof.get("cleanup_order"))),
        "equivalence_status": aggregate_status,
        "reason_code": (
            ""
            if aggregate_status == "EQUIVALENT"
            else (
                "PROCESS_GRAPH_CLEANUP_NOT_EQUIVALENT"
                if aggregate_status == "NOT_EQUIVALENT"
                else "PROCESS_GRAPH_CLEANUP_EQUIVALENCE_INDETERMINATE"
            )
        ),
        "detail": ";".join(reason_codes),
        "step_equivalence_receipts_by_id": node_receipts,
        "step_equivalence_receipt_ids": step_receipt_ids,
        "equivalent_step_count": sum(
            status in {"EQUIVALENT", "NOT_APPLICABLE"}
            for status in statuses
        ),
        "not_equivalent_step_count": sum(
            status == "NOT_EQUIVALENT" for status in statuses
        ),
        "indeterminate_step_count": sum(
            status not in {"EQUIVALENT", "NOT_APPLICABLE", "NOT_EQUIVALENT"}
            for status in statuses
        ),
        "fingerprint": "",
    }
    aggregate_receipt["fingerprint"] = _stable_hash(
        {
            "receipt_id": aggregate_receipt["receipt_id"],
            "proof_id": aggregate_receipt["proof_id"],
            "equivalence_status": aggregate_status,
            "step_receipt_ids": step_receipt_ids,
        }
    )[:32]
    _update_environment_receipt(
        execution_set,
        equivalence_status=aggregate_status,
        reason_codes=reason_codes,
        step_receipt_ids=step_receipt_ids,
    )
    return aggregate_receipt


__all__ = [
    "GRAPH_CLEANUP_EXECUTION_SET_SCHEMA",
    "GRAPH_CLEANUP_EQUIVALENCE_SCHEMA",
    "finalize_process_graph_cleanup_equivalence_inputs",
    "evaluate_process_graph_cleanup_equivalence",
]
