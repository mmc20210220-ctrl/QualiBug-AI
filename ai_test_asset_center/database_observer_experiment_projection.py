"""Bind captured database Observer execution drafts to already-compiled Experiments.

This is an additive projection after the existing Runtime Materialization bridge. It creates no
second compiler and no executor. Each Experiment receives only the drafts of its exact bound
materialization; the secret-free draft set is fingerprinted into the compile receipt. Missing or
malformed required drafts fail closed before transport.
"""
from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from typing import Any, Iterable

from .runtime_materialization_experiment_bridge import _CAPTURED_ASSET

PROJECTION_SCHEMA = "qualibug.database-observer-experiment-projection.v1"
DRAFT_SCHEMA = "qualibug.database-observer-execution-draft.v1"
OBSERVER_ID = "approved_database_readback"
_BLOCK_REASON = "BLOCKED_APPROVED_DATABASE_OBSERVER_DRAFT_INVALID"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return dict(value) if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return list(value) if isinstance(value, list) else []


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)


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


def _materialization_id(experiment: dict[str, Any]) -> str:
    contract = _dict(experiment.get("runtime_materialization_contract"))
    return _text(
        contract.get("materialization_id")
        or _dict(contract.get("authority")).get("lineage", {}).get("materialization_id")
        or _dict(experiment.get("compile_receipt")).get("runtime_materialization_id")
    )


def _captured_materializations() -> dict[str, dict[str, Any]]:
    capture = _CAPTURED_ASSET.get()
    return {
        _text(row.get("materialization_id")): dict(row)
        for row in _list(_dict(capture).get("materializations"))
        if isinstance(row, dict) and _text(row.get("materialization_id"))
    }


def _valid_draft(raw: Any, materialization_id: str) -> bool:
    row = _dict(raw)
    contract = _dict(row.get("database_observer_contract"))
    return bool(
        _text(row.get("schema")) == DRAFT_SCHEMA
        and _text(row.get("draft_id"))
        and _text(row.get("runtime_materialization_ref")) == materialization_id
        and _text(row.get("observer_handler_id")) == OBSERVER_ID
        and _text(row.get("observation_phase")) in {"BEFORE", "AFTER"}
        and _text(row.get("observer_contract_ref"))
        and _text(contract.get("schema")) == "qualibug.database-observer-contract.v1"
        and _text(contract.get("status")) == "READY_FOR_RUNTIME_CONNECTION_BINDING"
        and bool(contract.get("runtime_observer_authoritative"))
        and bool(contract.get("read_only"))
        and not bool(contract.get("mutation_allowed"))
        and not bool(contract.get("write_target_allowed"))
        and not bool(contract.get("oracle_authority_allowed"))
        and bool(row.get("required"))
        and not bool(row.get("runtime_connection_bound"))
        and not bool(row.get("query_executed"))
        and not bool(row.get("oracle_verdict_emitted"))
    )


def _observer_rows(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in _list(experiment.get("observers")):
        if isinstance(raw, str) and _text(raw):
            rows.append({"observer_id": _text(raw)})
        elif isinstance(raw, dict) and _text(raw.get("observer_id")):
            rows.append(dict(raw))
    return rows


def _blocked(experiment: dict[str, Any], detail: dict[str, Any]) -> dict[str, Any]:
    row = dict(experiment)
    receipt = _dict(row.get("compile_receipt"))
    receipt.update(
        {
            "status": "BLOCKED",
            "reason_code": _BLOCK_REASON,
            "database_observer_detail": detail,
        }
    )
    row.update(
        {
            "compile_receipt": receipt,
            "compile_status": "BLOCKED",
            "database_observer_projection_status": "BLOCKED",
        }
    )
    return row


def project_database_observers_to_experiment_pack(
    experiment_pack: dict[str, Any],
) -> dict[str, Any]:
    """Attach exact phase drafts and one typed Observer requirement to each Experiment."""
    pack = dict(experiment_pack or {})
    materializations = _captured_materializations()
    compiled: list[dict[str, Any]] = []
    newly_blocked: list[dict[str, Any]] = []
    draft_count = 0
    observer_experiment_count = 0

    for raw in _list(pack.get("experiments")):
        if not isinstance(raw, dict):
            continue
        experiment = dict(raw)
        materialization_id = _materialization_id(experiment)
        materialization = materializations.get(materialization_id)
        if not materialization:
            # The existing bridge owns missing-materialization blocking. Do not create a second
            # interpretation when this Experiment has no database phase authority to project.
            compiled.append(experiment)
            continue
        expected_count = int(
            materialization.get("database_observer_execution_draft_count") or 0
        )
        raw_drafts = _list(materialization.get("database_observer_execution_drafts"))
        if expected_count == 0 and not raw_drafts:
            experiment["database_observer_projection_status"] = "NOT_APPLICABLE"
            compiled.append(experiment)
            continue
        valid = [
            deepcopy(row)
            for row in raw_drafts
            if _valid_draft(row, materialization_id)
        ]
        valid = _dedupe(valid, "draft_id")
        if len(valid) != expected_count or not valid:
            newly_blocked.append(
                _blocked(
                    experiment,
                    {
                        "materialization_id": materialization_id,
                        "expected_draft_count": expected_count,
                        "valid_draft_count": len(valid),
                        "automatic_draft_recovery_allowed": False,
                    },
                )
            )
            continue

        fingerprint = _fingerprint(valid)
        observers = _observer_rows(experiment)
        observers.append(
            {
                "observer_id": OBSERVER_ID,
                "surface": "database_read_only",
                "adapter": "db_sql",
                "required": True,
                "phase_receipts_required": True,
            }
        )
        experiment["observers"] = _dedupe(observers, "observer_id")
        experiment["database_observer_execution_drafts"] = valid
        experiment["database_observer_execution_draft_fingerprint"] = fingerprint
        experiment["database_observer_projection_status"] = "BOUND"
        experiment["database_observer_phase_receipts_required"] = True
        experiment["database_observer_finalizer_must_not_requery"] = True
        receipt = _dict(experiment.get("compile_receipt"))
        receipt.update(
            {
                "database_observer_projection_status": "BOUND",
                "database_observer_execution_draft_count": len(valid),
                "database_observer_execution_draft_fingerprint": fingerprint,
                "database_observer_phase_receipts_required": True,
                "database_observer_finalizer_requery_allowed": False,
            }
        )
        experiment["compile_receipt"] = receipt
        compiled.append(experiment)
        observer_experiment_count += 1
        draft_count += len(valid)

    existing_blocked = [
        dict(row)
        for row in _list(pack.get("blocked_experiments"))
        if isinstance(row, dict)
    ]
    blocked = [*existing_blocked, *newly_blocked]
    reason_counts = dict(_dict(pack.get("block_reason_counts")))
    if newly_blocked:
        reason_counts[_BLOCK_REASON] = reason_counts.get(_BLOCK_REASON, 0) + len(
            newly_blocked
        )
    pack.update(
        {
            "experiments": compiled,
            "blocked_experiments": blocked,
            "compiled_count": len(compiled),
            "blocked_count": len(blocked),
            "block_reason_counts": reason_counts,
            "database_observer_experiment_projection": {
                "schema": PROJECTION_SCHEMA,
                "status": "BLOCKED" if newly_blocked else "PASS",
                "observer_experiment_count": observer_experiment_count,
                "execution_draft_count": draft_count,
                "newly_blocked_experiment_count": len(newly_blocked),
                "runtime_query_execution_count": 0,
                "oracle_verdict_count": 0,
                "secret_value_retained": False,
                "raw_sql_retained": False,
                "second_compiler_created": False,
                "existing_experiment_executor_remains_authority": True,
            },
        }
    )
    return pack


__all__ = [
    "DRAFT_SCHEMA",
    "OBSERVER_ID",
    "PROJECTION_SCHEMA",
    "project_database_observers_to_experiment_pack",
]
