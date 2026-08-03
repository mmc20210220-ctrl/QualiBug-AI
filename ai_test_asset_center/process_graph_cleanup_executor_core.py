"""System-aware compensation for governed process-graph writes.

The public cleanup entry remains ``experiment_cleanup_executor``. This module
consumes only a resolved process-graph write contract. Every cleanup is scoped
to one source step, system, actor credential, declared binding and readback
observer. It reuses the existing effectful-write predicate, governed transport,
restoration comparator, cleanup receipt, and environment receipt authorities.
"""
from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

from .contract_oracles import build_contract_evidence_receipt
from .experiment_cleanup import _cleanup_restores_governed_write
from .experiment_runtime_support import (
    _dict,
    _governance_audit_receipt_id,
    _list,
    _text,
    _unresolved_body_placeholders,
    _unresolved_path_placeholders,
)
from .process_graph_executor_support import scoped_actor_context
from .process_graph_runtime import resolve_graph_target_context
from .runtime_binding_materializer import (
    _cleanup_candidate,
    materialize_body_template,
    materialize_path,
)

GRAPH_CLEANUP_SCHEMA = "qualibug.process-graph-cleanup-execution.v1"
GRAPH_CLEANUP_BINDING_UNRESOLVED = "PROCESS_GRAPH_CLEANUP_BINDING_UNRESOLVED"
GRAPH_CLEANUP_SOURCE_STEP_UNRESOLVED = (
    "PROCESS_GRAPH_CLEANUP_SOURCE_STEP_UNRESOLVED"
)
GRAPH_CLEANUP_RESTORATION_FAILED = "PROCESS_GRAPH_CLEANUP_RESTORATION_FAILED"


def _response_value(value: Any, path: str) -> Any:
    token = _text(path)
    if token.startswith("$."):
        parts = [part for part in token[2:].split(".") if part]
    elif token.startswith("/"):
        parts = [
            part.replace("~1", "/").replace("~0", "~")
            for part in token.split("/")[1:]
        ]
    else:
        return None
    current = value
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        elif (
            isinstance(current, list)
            and part.isdigit()
            and int(part) < len(current)
        ):
            current = current[int(part)]
        else:
            return None
    return current


def _source_step(
    steps_out: list[dict[str, Any]], source_step_id: str
) -> dict[str, Any]:
    matches = [
        row
        for row in steps_out
        if isinstance(row, dict)
        and _text(row.get("step_id") or row.get("subject_id")) == source_step_id
        and _text(row.get("phase")) == "treatment"
        and isinstance(row.get("governance_receipt"), dict)
    ]
    return dict(matches[0]) if len(matches) == 1 else {}


def _cleanup_bindings(
    *,
    cleanup: dict[str, Any],
    source_step: dict[str, Any],
    runtime_bindings: dict[str, Any],
    original_request_body: Any,
) -> tuple[dict[str, Any], str]:
    # Compensation identity is scoped to the governed source step.  A global
    # runtime binding may legitimately contain the same canonical field name
    # from another object/system (the common case is ``id``).  Resolve every
    # declared cleanup binding first from its exact authority and only then
    # admit unrelated runtime values as non-overriding auxiliaries.
    bindings: dict[str, Any] = {}
    response_body = source_step.get("body")
    for index, raw in enumerate(_list(cleanup.get("binding_specs"))):
        spec = _dict(raw)
        target = _text(spec.get("target") or spec.get("target_field"))
        source = _text(spec.get("source"))
        source_path = _text(spec.get("source_path") or spec.get("json_path"))
        source_field = _text(
            spec.get("canonical_field_id") or spec.get("source_field")
        )
        if not target or source not in {
            "write_response",
            "original_request",
            "runtime_binding",
        }:
            return {}, f"binding_{index + 1}_identity_invalid"
        if source == "runtime_binding":
            value = runtime_bindings.get(source_field or target)
        elif source == "original_request":
            value = _response_value(original_request_body, source_path)
        else:
            value = _response_value(response_body, source_path)
        if value in (None, "", [], {}):
            return {}, f"{target}:{source}:{source_path or source_field}"
        if target in bindings and bindings[target] != value:
            return {}, f"binding_conflict:{target}"
        bindings[target] = value

    for key, value in runtime_bindings.items():
        token = _text(key)
        if token and value not in (None, "", [], {}):
            bindings.setdefault(token, value)
    return bindings, ""


def _receipt(
    *,
    cleanup: dict[str, Any],
    source_step_id: str,
    status: str,
    reason_code: str,
    evidence: dict[str, Any],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
) -> dict[str, Any]:
    return build_contract_evidence_receipt(
        kind="cleanup",
        experiment_id=eid,
        obligation_id=oid,
        campaign_id=resolved_campaign_id,
        execution_id=resolved_execution_id,
        subject_id=_text(cleanup.get("step_id"))
        or f"cleanup_{source_step_id}",
        status=status,
        evidence={
            "schema_version": GRAPH_CLEANUP_SCHEMA,
            "source_step_id": source_step_id,
            "system_ref": _text(cleanup.get("system_ref")),
            "operation_ref": _text(cleanup.get("operation_ref")),
            "reason_code": reason_code,
            **evidence,
        },
    )


def _append_receipt(
    receipt: dict[str, Any],
    *,
    receipts: list[dict[str, Any]],
    contract_evidence_receipts: list[dict[str, Any]],
) -> None:
    receipts.append(receipt)
    contract_evidence_receipts.append(receipt)


def _failed_receipt(
    *,
    cleanup: dict[str, Any],
    source_step_id: str,
    reason_code: str,
    detail: str,
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
) -> dict[str, Any]:
    return _receipt(
        cleanup=cleanup,
        source_step_id=source_step_id,
        status="FAILED",
        reason_code=reason_code,
        evidence={
            "effectful_write_count": 1,
            "cleanup_write_count": 0,
            "detail": detail,
            "request_reached_transport": False,
        },
        eid=eid,
        oid=oid,
        resolved_campaign_id=resolved_campaign_id,
        resolved_execution_id=resolved_execution_id,
    )


def execute_process_graph_cleanup(
    *,
    exp: dict[str, Any],
    steps_out: list[dict[str, Any]],
    observations: dict[str, Any],
    contract_evidence_receipts: list[dict[str, Any]],
    request_bodies_for_cleanup: dict[str, Any],
    runtime_bindings: dict[str, Any],
    cleanup_failures: int,
    actors: dict[str, dict[str, Any]],
    tokens: dict[str, str],
    eid: str,
    oid: str,
    resolved_campaign_id: str,
    resolved_execution_id: str,
    campaign_id: str,
    root: Path,
    project: str,
    base_url: str,
    runtime_contract: dict[str, Any],
    execute_governed_control_write: Any,
    sandbox_write_allowed: Any,
) -> dict[str, Any]:
    """Compensate every effectful graph write in compiled reverse order."""
    contract = _dict(exp.get("process_graph_write_contract"))
    cleanup_steps = [
        dict(row)
        for row in _list(contract.get("cleanup_steps"))
        if isinstance(row, dict)
    ]
    receipts: list[dict[str, Any]] = []
    cleanup_rows: list[dict[str, Any]] = []

    for cleanup in cleanup_steps:
        source_step_id = _text(cleanup.get("source_step_id"))
        source = _source_step(steps_out, source_step_id)
        if not source:
            receipt = _receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                status="FAILED",
                reason_code=GRAPH_CLEANUP_SOURCE_STEP_UNRESOLVED,
                evidence={
                    "effectful_write_count": 0,
                    "cleanup_write_count": 0,
                    "request_reached_transport": False,
                },
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
            )
            _append_receipt(
                receipt,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            cleanup_failures += 1
            continue

        source_governance = _dict(source.get("governance_receipt"))
        source_write = _dict(source_governance.get("write"))
        source_status = int(
            source_write.get("status") or source.get("status_code") or 0
        )
        if not _cleanup_candidate(source):
            receipt = _receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                status="NOT_REQUIRED",
                reason_code="SOURCE_WRITE_NO_OBSERVED_EFFECT",
                evidence={
                    "effectful_write_count": 0,
                    "cleanup_write_count": 0,
                    "source_status_code": source_status,
                },
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
            )
            _append_receipt(
                receipt,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            continue

        original_request = request_bodies_for_cleanup.get(source_step_id)
        bindings, binding_error = _cleanup_bindings(
            cleanup=cleanup,
            source_step=source,
            runtime_bindings=runtime_bindings,
            original_request_body=original_request,
        )
        cleanup_path = materialize_path(_text(cleanup.get("path")), bindings)
        observer = _dict(cleanup.get("observer_operation"))
        observation_path = materialize_path(_text(observer.get("path")), bindings)
        unresolved_path = list(
            dict.fromkeys(
                [
                    *_unresolved_path_placeholders(cleanup_path),
                    *_unresolved_path_placeholders(observation_path),
                ]
            )
        )
        cleanup_body = (
            deepcopy(original_request)
            if cleanup.get("body_from_original_request") is True
            else materialize_body_template(cleanup.get("body"), bindings)
        )
        unresolved_body = _unresolved_body_placeholders(cleanup_body, bindings)
        if binding_error or unresolved_path or unresolved_body or not observation_path:
            detail = binding_error or (
                "path:" + ",".join(unresolved_path)
                if unresolved_path
                else "body:" + ",".join(unresolved_body)
                if unresolved_body
                else "observer_path_missing"
            )
            receipt = _failed_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                reason_code=GRAPH_CLEANUP_BINDING_UNRESOLVED,
                detail=detail,
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
            )
            _append_receipt(
                receipt,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            cleanup_failures += 1
            continue

        target = resolve_graph_target_context(
            runtime_contract=runtime_contract,
            system_ref=_text(cleanup.get("system_ref")),
            actor_ref=_text(cleanup.get("actor_ref")),
            base_url=base_url,
            require_write=True,
        )
        if _text(target.get("status")) != "READY":
            receipt = _failed_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                reason_code=_text(target.get("reason_code"))
                or "PROCESS_GRAPH_TARGET_NOT_APPROVED",
                detail=_text(target.get("detail")),
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
            )
            _append_receipt(
                receipt,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            cleanup_failures += 1
            continue

        scoped_actors, scoped_tokens, credential_error = scoped_actor_context(
            actors=actors,
            tokens=tokens,
            step=cleanup,
            credential_token_key=_text(target.get("credential_token_key")),
        )
        actor_ref = _text(cleanup.get("actor_ref"))
        actor = _dict(scoped_actors.get(actor_ref))
        token_key = _text(
            actor.get("credential_secret_ref") or actor.get("secret_ref")
        )
        token = _text(scoped_tokens.get(token_key)) if token_key else ""
        if credential_error:
            receipt = _failed_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                reason_code="PROCESS_GRAPH_TARGET_ACTOR_CREDENTIAL_UNRESOLVED",
                detail=credential_error,
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
            )
            _append_receipt(
                receipt,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            cleanup_failures += 1
            continue

        allowed, policy_reason = sandbox_write_allowed(
            root=root,
            project=project,
            runtime_contract=_dict(target.get("runtime_contract")),
            actor_token=token,
            actor_identity=_text(actor.get("role") or actor_ref),
        )
        if not allowed:
            receipt = _failed_receipt(
                cleanup=cleanup,
                source_step_id=source_step_id,
                reason_code="BLOCKED_TARGET_POLICY",
                detail=_text(policy_reason),
                eid=eid,
                oid=oid,
                resolved_campaign_id=resolved_campaign_id,
                resolved_execution_id=resolved_execution_id,
            )
            _append_receipt(
                receipt,
                receipts=receipts,
                contract_evidence_receipts=contract_evidence_receipts,
            )
            cleanup_failures += 1
            continue

        method = _text(cleanup.get("method")).upper()
        governed = execute_governed_control_write(
            root=root,
            project=project,
            base_url=_text(target.get("base_url")),
            runtime_contract=_dict(target.get("runtime_contract")),
            campaign_id=campaign_id,
            operation_phase="experiment_cleanup",
            actor_identity=_text(actor.get("role") or actor_ref),
            actor_token=token,
            method=method,
            path=cleanup_path,
            body=(
                cleanup_body
                if method in {"POST", "PUT", "PATCH"}
                else None
            ),
            observation_path=observation_path,
            # Cleanup compensates the experiment's own writes; restorable by
            # definition, so the protected-identity guard does not apply.
            restorable_identity_mutation=True,
        )
        cleanup_write = _dict(governed.get("write"))
        status_code = int(cleanup_write.get("status") or 0)
        restoration_verified = _cleanup_restores_governed_write(
            source_governance,
            governed,
        )
        audit_receipt_ids = sorted(
            {
                value
                for value in (
                    _governance_audit_receipt_id(source_governance),
                    _governance_audit_receipt_id(governed),
                )
                if value
            }
        )
        completed = bool(
            governed.get("accepted") is True
            and 200 <= status_code < 300
            and restoration_verified
            and audit_receipt_ids
        )
        reason_code = "" if completed else GRAPH_CLEANUP_RESTORATION_FAILED
        receipt = _receipt(
            cleanup=cleanup,
            source_step_id=source_step_id,
            status="COMPLETED" if completed else "FAILED",
            reason_code=reason_code,
            evidence={
                "method": method,
                "path": cleanup_path,
                "observation_path": observation_path,
                "source_status_code": source_status,
                "effectful_write_count": 1,
                "cleanup_write_count": (
                    1 if governed.get("accepted") is True else 0
                ),
                "status_code": status_code,
                "restoration_verified": restoration_verified,
                "state_unchanged": restoration_verified,
                "audit_receipt_ids": audit_receipt_ids,
                "target_policy_decision_id": _text(
                    _dict(target.get("target_policy_decision")).get(
                        "decision_id"
                    )
                ),
            },
            eid=eid,
            oid=oid,
            resolved_campaign_id=resolved_campaign_id,
            resolved_execution_id=resolved_execution_id,
        )
        _append_receipt(
            receipt,
            receipts=receipts,
            contract_evidence_receipts=contract_evidence_receipts,
        )
        cleanup_row = {
            "phase": "cleanup",
            "step_id": _text(cleanup.get("step_id")),
            "cleanup_subject_id": _text(cleanup.get("step_id")),
            "compensates_step_id": source_step_id,
            "operation_ref": _text(cleanup.get("operation_ref")),
            "actor_ref": actor_ref,
            "system_ref": _text(cleanup.get("system_ref")),
            "method": method,
            "path": cleanup_path,
            "observation_path": observation_path,
            "status_code": status_code,
            "governance_receipt": governed,
            "restoration_verified": restoration_verified,
        }
        cleanup_rows.append(cleanup_row)
        steps_out.append(cleanup_row)
        observations["cleanup_result"] = governed
        after = _dict(governed.get("after"))
        if after and int(after.get("status") or 0) > 0:
            observations["after_cleanup_observation"] = {
                "status_code": int(after.get("status") or 0),
                "body": after.get("body"),
                "path": observation_path,
                "phase": "after_cleanup",
                "source": "process_graph_cleanup_governance",
                "cleanup_step_id": _text(cleanup.get("step_id")),
                "source_step_id": source_step_id,
                "receipt_id": _text(receipt.get("receipt_id")),
            }
        if not completed:
            cleanup_failures += 1

        ledger = observations.get("process_step_ledger")
        receipt_id = _text(receipt.get("receipt_id"))
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

    observations["process_graph_cleanup_receipts"] = receipts
    observations["process_graph_cleanup_steps"] = cleanup_rows
    if cleanup_failures:
        observations["cleanup_status"] = "failed"
    elif cleanup_rows:
        observations["cleanup_status"] = "completed"
    else:
        observations["cleanup_status"] = "not_required"
    return {
        "steps_out": steps_out,
        "observations": observations,
        "contract_evidence_receipts": contract_evidence_receipts,
        "cleanup_failures": cleanup_failures,
        "process_graph_cleanup_receipts": receipts,
    }


def finalize_process_graph_cleanup_result(
    *,
    exp: dict[str, Any],
    result: dict[str, Any],
    resolved_campaign_id: str,
) -> dict[str, Any]:
    """Build existing cleanup and environment receipts from graph evidence."""
    from .cleanup_execution_receipt import (
        build_cleanup_execution_receipt,
        build_environment_restoration_receipt,
    )

    output = dict(result)
    observations = dict(_dict(output.get("observations")))
    steps_out = list(output.get("steps_out") or [])
    cleanup_failures = int(output.get("cleanup_failures") or 0)
    proof = _dict(exp.get("write_reversibility_proof"))
    cleanup_receipt = build_cleanup_execution_receipt(
        experiment_id=_text(exp.get("experiment_id")),
        proof_id=_text(proof.get("proof_id")),
        cleanup_plan=_list(exp.get("cleanup_plan")),
        steps_out=steps_out,
        cleanup_failures=cleanup_failures,
        cleanup_status=_text(observations.get("cleanup_status")),
        proof=proof,
        adapter_cleanup_receipts=_list(
            observations.get("adapter_cleanup_receipts")
        ),
    )
    observations["cleanup_execution_receipt"] = cleanup_receipt
    observations["cleanup_execution_receipts"] = [cleanup_receipt]
    cleanup_receipt_id = _text(cleanup_receipt.get("receipt_id"))
    observations["cleanup_execution_receipt_ids"] = (
        [cleanup_receipt_id] if cleanup_receipt_id else []
    )
    api_cleanup_ids = [
        _text(row.get("receipt_id"))
        for row in _list(observations.get("process_graph_cleanup_receipts"))
        if _text(_dict(row).get("receipt_id"))
    ]
    environment_receipt = build_environment_restoration_receipt(
        experiment_id=_text(exp.get("experiment_id")),
        campaign_id=resolved_campaign_id,
        database_cleanup_receipt_ids=[],
        api_cleanup_receipt_ids=api_cleanup_ids,
        fixture_receipt_ids=[
            _text(row.get("receipt_id"))
            for row in _list(observations.get("fixture_receipts"))
            if _text(_dict(row).get("receipt_id"))
        ],
        created_rows_remaining=(0 if cleanup_failures == 0 else cleanup_failures),
        cleanup_failures=(
            [
                {
                    "reason": "process_graph_cleanup_failure",
                    "count": cleanup_failures,
                }
            ]
            if cleanup_failures
            else []
        ),
        baseline_comparison={
            "relevant_tables_match": cleanup_failures == 0,
            "relevant_fields_match": cleanup_failures == 0,
        },
    )
    observations["environment_restoration_receipt"] = environment_receipt
    observations["environment_restored"] = bool(
        environment_receipt.get("environment_restored")
    )
    output["observations"] = observations
    output["steps_out"] = steps_out
    output["cleanup_failures"] = cleanup_failures
    return output


__all__ = [
    "GRAPH_CLEANUP_SCHEMA",
    "GRAPH_CLEANUP_BINDING_UNRESOLVED",
    "GRAPH_CLEANUP_SOURCE_STEP_UNRESOLVED",
    "GRAPH_CLEANUP_RESTORATION_FAILED",
    "execute_process_graph_cleanup",
    "finalize_process_graph_cleanup_result",
]
