"""Execute approved database Observer drafts in their real Experiment phases.

BEFORE runs after fixtures and runtime binding validation but before any control/treatment transport.
AFTER runs after all business transport and before cleanup. The final phase-aggregate Observer
consumes only those typed receipts and has no direct-query fallback. Single-query readback and
phase aggregation intentionally use different Observer IDs, so registration order cannot change
runtime authority.
"""
from __future__ import annotations

import hashlib
import json
import re
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from .database_observer_runtime import (
    ADAPTER,
    EVIDENCE_KEY,
    OBSERVER_ID as DIRECT_READBACK_OBSERVER_ID,
    execute_database_observer_contract,
)
from .observer_contracts_base import _receipt, register_observer, registered_observer_ids

PHASE_AGGREGATE_OBSERVER_ID = "approved_database_phase_aggregate"
PHASE_RECEIPT_SCHEMA = "qualibug.database-observer-phase-receipt.v1"
AGGREGATE_SCHEMA = "qualibug.database-observer-phase-aggregate.v1"
_DRAFT_SCHEMA = "qualibug.database-observer-execution-draft.v1"
_PLACEHOLDER = re.compile(r"^\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


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


def _dedupe(rows: Iterable[Any], key: str) -> list[dict[str, Any]]:
    output: dict[str, dict[str, Any]] = {}
    for raw in rows:
        if not isinstance(raw, dict):
            continue
        identity = _text(raw.get(key))
        if identity:
            output[identity] = dict(raw)
    return list(output.values())


def _materialize(value: Any, runtime_bindings: dict[str, Any]) -> Any:
    if isinstance(value, dict):
        return {
            key: _materialize(child, runtime_bindings)
            for key, child in value.items()
        }
    if isinstance(value, list):
        return [_materialize(child, runtime_bindings) for child in value]
    if isinstance(value, str):
        match = _PLACEHOLDER.fullmatch(value.strip())
        if match:
            return runtime_bindings.get(match.group(1))
    return value


def _path_get(value: Any, parts: list[str]) -> Any:
    current = value
    for part in parts:
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return None
    return current


def _treatment_request(
    exp: dict[str, Any], runtime_bindings: dict[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    body: dict[str, Any] = {}
    parameters = dict(runtime_bindings)
    for raw in _list(exp.get("treatment_plan")):
        step = _dict(raw)
        materialized_body = _materialize(step.get("body"), runtime_bindings)
        if isinstance(materialized_body, dict):
            body.update(materialized_body)
        for source in (step.get("query"), step.get("path_parameters")):
            materialized = _materialize(source, runtime_bindings)
            if isinstance(materialized, dict):
                parameters.update(materialized)
    return body, parameters


def _latest_treatment_response(
    steps_out: list[dict[str, Any]],
) -> dict[str, Any]:
    for raw in reversed(steps_out):
        row = _dict(raw)
        phase = _text(row.get("phase")).lower()
        if not phase.startswith("treatment"):
            continue
        response = _dict(row.get("response"))
        body = response.get("body") if "body" in response else row.get("body")
        if isinstance(body, dict):
            return body
    return {}


def _runtime_values(
    *,
    exp: dict[str, Any],
    draft: dict[str, Any],
    runtime_bindings: dict[str, Any],
    steps_out: list[dict[str, Any]],
) -> dict[str, Any]:
    body, parameters = _treatment_request(exp, runtime_bindings)
    response = _latest_treatment_response(steps_out)
    values: dict[str, Any] = {
        "request_body": body,
        "request_parameters": parameters,
        "response_body": response,
    }
    for source in _list(draft.get("identity_value_sources")):
        source_text = _text(source)
        parts = source_text.split(".")
        value: Any = None
        if parts[:2] == ["request", "body"]:
            value = _path_get(body, parts[2:])
        elif parts[:2] == ["request", "parameter"]:
            value = _path_get(parameters, parts[2:])
        elif parts[:2] == ["response", "body"]:
            value = _path_get(response, parts[2:])
        leaf = parts[-1] if parts else ""
        if value in (None, "") and leaf in runtime_bindings:
            value = runtime_bindings[leaf]
        if value not in (None, ""):
            values[source_text] = value
    return values


def _phase_drafts(exp: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    target = _text(phase).upper()
    return [
        deepcopy(row)
        for row in _list(exp.get("database_observer_execution_drafts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) == _DRAFT_SCHEMA
        and _text(row.get("observation_phase")).upper() == target
        and bool(row.get("required"))
    ]


def execute_database_observer_phase(
    exp: dict[str, Any],
    *,
    phase: str,
    root: Path,
    project: str,
    runtime_contract: dict[str, Any],
    runtime_bindings: dict[str, Any],
    observations: dict[str, Any],
    steps_out: list[dict[str, Any]],
    campaign_id: str,
    execution_id: str,
) -> dict[str, Any]:
    """Execute every required draft for one true phase and store typed receipts."""
    target = _text(phase).upper()
    if target not in {"BEFORE", "AFTER"}:
        raise ValueError("database_observer_phase_invalid")
    drafts = _phase_drafts(exp, target)
    if not drafts:
        return {
            "schema": PHASE_RECEIPT_SCHEMA,
            "phase": target,
            "status": "NOT_APPLICABLE",
            "required_draft_count": 0,
            "observed_draft_count": 0,
            "blocked": False,
            "reason_code": "",
        }

    receipts: list[dict[str, Any]] = []
    for draft in drafts:
        receipt = execute_database_observer_contract(
            _dict(draft.get("database_observer_contract")),
            root=root,
            project=project,
            runtime_values=_runtime_values(
                exp=exp,
                draft=draft,
                runtime_bindings=runtime_bindings,
                steps_out=steps_out,
            ),
            runtime_contract=runtime_contract,
            connection_ref=_text(draft.get("database_connection_ref")),
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
        receipt = {
            **receipt,
            "source_observer_id": DIRECT_READBACK_OBSERVER_ID,
            "phase_receipt_id": _fingerprint(
                {
                    "draft_id": draft.get("draft_id"),
                    "phase": target,
                    "receipt_id": receipt.get("receipt_id"),
                }
            )[:32],
            "draft_id": draft.get("draft_id"),
            "observer_contract_ref": draft.get("observer_contract_ref"),
            "observation_phase": target,
            "required": True,
            "executed_before_cleanup": True,
            "oracle_verdict_emitted": False,
        }
        receipts.append(receipt)

    current_draft_ids = {_text(item.get("draft_id")) for item in drafts}
    existing = [
        dict(row)
        for row in _list(
            observations.get("approved_database_observer_phase_receipts")
        )
        if isinstance(row, dict)
        and not (
            _text(row.get("observation_phase")) == target
            and _text(row.get("draft_id")) in current_draft_ids
        )
    ]
    observations["approved_database_observer_phase_receipts"] = _dedupe(
        [*existing, *receipts], "phase_receipt_id"
    )
    observed_count = sum(
        1 for row in receipts if _text(row.get("status")).upper() == "OBSERVED"
    )
    complete = observed_count == len(drafts)
    reason = "" if complete else f"DATABASE_OBSERVER_{target}_PHASE_INCOMPLETE"
    summary = {
        "schema": PHASE_RECEIPT_SCHEMA,
        "phase": target,
        "status": "OBSERVED" if complete else "INDETERMINATE",
        "required_draft_count": len(drafts),
        "observed_draft_count": observed_count,
        "receipt_count": len(receipts),
        "blocked": target == "BEFORE" and not complete,
        "reason_code": reason,
        "query_execution_count": len(receipts),
        "write_attempt_count": 0,
        "oracle_verdict_count": 0,
        "executed_before_transport": target == "BEFORE",
        "executed_after_transport_before_cleanup": target == "AFTER",
    }
    observations.setdefault("approved_database_observer_phase_summaries", {})[
        target.lower()
    ] = summary
    if not complete:
        observations["harness_error"] = True
    return summary


def _expected_drafts(exp: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(exp.get("database_observer_execution_drafts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) == _DRAFT_SCHEMA
        and bool(row.get("required"))
    ]


def aggregate_database_observer_phase_receipts(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate true phase receipts; this handler has no database-query path."""
    env = _dict(envelope)
    experiment = _dict(env.get("experiment"))
    drafts = _expected_drafts(experiment)
    if not drafts:
        return _receipt(
            observer_id=PHASE_AGGREGATE_OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="DATABASE_OBSERVER_PHASE_DRAFTS_MISSING",
            evidence={
                "schema": AGGREGATE_SCHEMA,
                "required_phase_count": 0,
                "observed_phase_count": 0,
                "missing_required_phases": [],
                "finalizer_database_requery_count": 0,
                "query_execution_count": 0,
                "write_attempt_count": 0,
                "oracle_verdict_emitted": False,
                "direct_query_fallback_allowed": False,
            },
            campaign_id=_text(env.get("campaign_id")),
            execution_id=_text(env.get("execution_id")),
        )
    observations = _dict(env.get("observations"))
    receipts = [
        dict(row)
        for row in _list(
            observations.get("approved_database_observer_phase_receipts")
        )
        if isinstance(row, dict)
    ]
    by_key = {
        (
            _text(row.get("draft_id")),
            _text(row.get("observation_phase")).upper(),
        ): row
        for row in receipts
        if _text(row.get("draft_id")) and _text(row.get("observation_phase"))
    }
    missing: list[dict[str, str]] = []
    phase_rows: list[dict[str, Any]] = []
    for draft in drafts:
        key = (
            _text(draft.get("draft_id")),
            _text(draft.get("observation_phase")).upper(),
        )
        receipt = by_key.get(key)
        if not receipt or _text(receipt.get("status")).upper() != "OBSERVED":
            missing.append({"draft_id": key[0], "phase": key[1]})
            continue
        payload = _dict(_dict(receipt.get("evidence")).get(EVIDENCE_KEY))
        phase_rows.append(
            {
                "draft_id": key[0],
                "phase": key[1],
                "observer_contract_ref": draft.get("observer_contract_ref"),
                "receipt_id": receipt.get("receipt_id"),
                "source_observer_id": DIRECT_READBACK_OBSERVER_ID,
                "snapshot": payload,
                "snapshot_fingerprint": payload.get("row_fingerprint"),
                "oracle_verdict_emitted": False,
            }
        )
    evidence = {
        "schema": AGGREGATE_SCHEMA,
        "approved_database_snapshots": phase_rows,
        "required_phase_count": len(drafts),
        "observed_phase_count": len(phase_rows),
        "missing_required_phases": missing,
        "before_phase_count": sum(
            1 for row in phase_rows if row["phase"] == "BEFORE"
        ),
        "after_phase_count": sum(
            1 for row in phase_rows if row["phase"] == "AFTER"
        ),
        "phase_pair_complete": not missing,
        "finalizer_database_requery_count": 0,
        "query_execution_count": len(phase_rows),
        "write_attempt_count": 0,
        "oracle_verdict_emitted": False,
        "cleanup_state_used_as_after_snapshot": False,
        "direct_query_fallback_allowed": False,
    }
    if missing:
        return _receipt(
            observer_id=PHASE_AGGREGATE_OBSERVER_ID,
            status="INDETERMINATE",
            reason_code="DATABASE_OBSERVER_REQUIRED_PHASE_RECEIPT_MISSING",
            evidence=evidence,
            campaign_id=_text(env.get("campaign_id")),
            execution_id=_text(env.get("execution_id")),
        )
    return _receipt(
        observer_id=PHASE_AGGREGATE_OBSERVER_ID,
        status="OBSERVED",
        evidence=evidence,
        campaign_id=_text(env.get("campaign_id")),
        execution_id=_text(env.get("execution_id")),
    )


def install_experiment_database_observer() -> str:
    """Register phase aggregation under its own stable Observer authority."""
    if PHASE_AGGREGATE_OBSERVER_ID in registered_observer_ids():
        return PHASE_AGGREGATE_OBSERVER_ID
    return register_observer(
        PHASE_AGGREGATE_OBSERVER_ID,
        surface="database_read_only",
        adapter=ADAPTER,
        handler=aggregate_database_observer_phase_receipts,
        evidence_keys=("approved_database_snapshots",),
    )


__all__ = [
    "AGGREGATE_SCHEMA",
    "PHASE_AGGREGATE_OBSERVER_ID",
    "PHASE_RECEIPT_SCHEMA",
    "aggregate_database_observer_phase_receipts",
    "execute_database_observer_phase",
    "install_experiment_database_observer",
]
