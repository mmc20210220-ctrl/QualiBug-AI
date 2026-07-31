"""Execute approved database relation aggregate drafts in true Experiment phases."""
from __future__ import annotations

import functools
import hashlib
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any, Iterable

from .database_observer_experiment_runtime import _runtime_values
from .database_relation_observer_runtime import (
    ADAPTER,
    EVIDENCE_KEY,
    OBSERVER_ID as DIRECT_RELATION_OBSERVER_ID,
    execute_database_relation_observer_contract,
    install_approved_database_relation_observer,
)
from .observer_contracts_base import _receipt, register_observer, registered_observer_ids

PHASE_AGGREGATE_OBSERVER_ID = "approved_database_relation_phase_aggregate"
PHASE_RECEIPT_SCHEMA = "qualibug.database-relation-observer-phase-receipt.v1"
AGGREGATE_SCHEMA = "qualibug.database-relation-observer-phase-aggregate.v1"
DRAFT_SCHEMA = "qualibug.database-relation-observer-execution-draft.v1"
_RECEIPT_KEY = "approved_database_relation_phase_receipts"
_INSTALL_MARKER = "__qualibug_database_relation_phase_execution_v1__"


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    # Phase execution mutates the caller-owned observations bag. Returning a copy here
    # silently discards typed receipts before Assertion DSL evaluation.
    return value if isinstance(value, dict) else {}


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


def _optional_pair(payload: dict[str, Any], pair_id: Any) -> dict[str, Any]:
    row = dict(payload)
    normalized = _text(pair_id)
    if normalized:
        row["relation_pair_id"] = normalized
    return row


def _phase_drafts(exp: dict[str, Any], phase: str) -> list[dict[str, Any]]:
    target = _text(phase).upper()
    return [
        deepcopy(row)
        for row in _list(exp.get("database_relation_observer_execution_drafts"))
        if isinstance(row, dict)
        and _text(row.get("schema")) == DRAFT_SCHEMA
        and _text(row.get("observation_phase")).upper() == target
        and row.get("required") is True
    ]


def execute_database_relation_observer_phase(
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
    target = _text(phase).upper()
    if target not in {"BEFORE", "AFTER"}:
        raise ValueError("database_relation_observer_phase_invalid")
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
        receipt = execute_database_relation_observer_contract(
            _dict(draft.get("database_relation_observer_contract")),
            aggregate_requests=[
                dict(row)
                for row in _list(draft.get("aggregate_requests"))
                if isinstance(row, dict)
            ],
            root=root,
            project=project,
            runtime_values=_runtime_values(
                exp=exp,
                draft={
                    **draft,
                    "identity_value_sources": _list(
                        draft.get("identity_value_sources")
                    ),
                },
                runtime_bindings=runtime_bindings,
                steps_out=steps_out,
            ),
            runtime_contract=runtime_contract,
            connection_ref=_text(draft.get("database_connection_ref")),
            campaign_id=campaign_id,
            execution_id=execution_id,
        )
        relation_pair_id = _text(draft.get("relation_pair_id"))
        receipt = _optional_pair(
            {
                **receipt,
                "source_observer_id": DIRECT_RELATION_OBSERVER_ID,
                "phase_receipt_id": _fingerprint(
                    {
                        "draft_id": draft.get("draft_id"),
                        "relation_pair_id": relation_pair_id,
                        "phase": target,
                        "receipt_id": receipt.get("receipt_id"),
                    }
                )[:32],
                "draft_id": draft.get("draft_id"),
                "relation_observer_contract_ref": draft.get(
                    "relation_observer_contract_ref"
                ),
                "root_observer_contract_ref": draft.get(
                    "root_observer_contract_ref"
                ),
                "observation_phase": target,
                "required": True,
                "executed_before_cleanup": True,
                "oracle_verdict_emitted": False,
            },
            relation_pair_id,
        )
        receipts.append(receipt)

    current_ids = {_text(row.get("draft_id")) for row in drafts}
    existing = [
        dict(row)
        for row in _list(observations.get(_RECEIPT_KEY))
        if isinstance(row, dict)
        and not (
            _text(row.get("observation_phase")).upper() == target
            and _text(row.get("draft_id")) in current_ids
        )
    ]
    observations[_RECEIPT_KEY] = _dedupe(
        [*existing, *receipts],
        "phase_receipt_id",
    )
    observed_count = sum(
        1
        for row in receipts
        if _text(row.get("status")).upper() == "OBSERVED"
    )
    complete = observed_count == len(drafts)
    reason = (
        ""
        if complete
        else f"DATABASE_RELATION_OBSERVER_{target}_PHASE_INCOMPLETE"
    )
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
    observations.setdefault(
        "approved_database_relation_phase_summaries", {}
    )[target.lower()] = summary
    if not complete:
        observations["harness_error"] = True
    return summary


def _expected_drafts(experiment: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        dict(row)
        for row in _list(
            experiment.get("database_relation_observer_execution_drafts")
        )
        if isinstance(row, dict)
        and _text(row.get("schema")) == DRAFT_SCHEMA
        and row.get("required") is True
    ]


def aggregate_database_relation_phase_receipts(
    envelope: dict[str, Any],
) -> dict[str, Any]:
    """Aggregate phase facts without selecting a winner among duplicate receipts."""
    env = _dict(envelope)
    experiment = _dict(env.get("experiment"))
    drafts = _expected_drafts(experiment)
    observations = _dict(env.get("observations"))
    receipts = [
        dict(row)
        for row in _list(observations.get(_RECEIPT_KEY))
        if isinstance(row, dict)
    ]
    groups: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in receipts:
        key = (
            _text(row.get("draft_id")),
            _text(row.get("observation_phase")).upper(),
        )
        if all(key):
            groups.setdefault(key, []).append(row)

    missing: list[dict[str, Any]] = []
    duplicates: list[dict[str, Any]] = []
    snapshots: list[dict[str, Any]] = []
    for draft in drafts:
        key = (
            _text(draft.get("draft_id")),
            _text(draft.get("observation_phase")).upper(),
        )
        pair_id = _text(draft.get("relation_pair_id"))
        matches = groups.get(key, [])
        if len(matches) > 1:
            duplicates.append(
                _optional_pair(
                    {
                        "draft_id": key[0],
                        "phase": key[1],
                        "receipt_count": len(matches),
                        "receipt_ids": sorted(
                            _text(row.get("receipt_id"))
                            for row in matches
                            if _text(row.get("receipt_id"))
                        ),
                    },
                    pair_id,
                )
            )
            continue
        receipt = matches[0] if matches else {}
        if not receipt or _text(receipt.get("status")).upper() != "OBSERVED":
            missing.append(
                _optional_pair(
                    {"draft_id": key[0], "phase": key[1]},
                    pair_id,
                )
            )
            continue
        payload = _dict(_dict(receipt.get("evidence")).get(EVIDENCE_KEY))
        snapshots.append(
            _optional_pair(
                {
                    "draft_id": key[0],
                    "phase": key[1],
                    "relation_observer_contract_ref": draft.get(
                        "relation_observer_contract_ref"
                    ),
                    "root_observer_contract_ref": draft.get(
                        "root_observer_contract_ref"
                    ),
                    "receipt_id": receipt.get("receipt_id"),
                    "phase_receipt_id": receipt.get("phase_receipt_id"),
                    "source_observer_id": DIRECT_RELATION_OBSERVER_ID,
                    "snapshot": payload,
                    "snapshot_fingerprint": payload.get("aggregate_fingerprint"),
                    "campaign_id": receipt.get("campaign_id"),
                    "execution_id": receipt.get("execution_id"),
                    "oracle_verdict_emitted": False,
                },
                _text(receipt.get("relation_pair_id")) or pair_id,
            )
        )

    complete = bool(drafts) and not missing and not duplicates
    evidence = {
        "schema": AGGREGATE_SCHEMA,
        "approved_database_relation_snapshots": snapshots,
        _RECEIPT_KEY: receipts,
        "required_phase_count": len(drafts),
        "observed_phase_count": len(snapshots),
        "missing_required_phases": missing,
        "duplicate_phase_receipts": duplicates,
        "phase_complete": complete,
        "automatic_receipt_winner_count": 0,
        "finalizer_database_requery_count": 0,
        "query_execution_count": len(snapshots),
        "write_attempt_count": 0,
        "oracle_verdict_emitted": False,
        "cleanup_state_used_as_after_snapshot": False,
        "direct_query_fallback_allowed": False,
    }
    reason_code = ""
    if not drafts:
        reason_code = "DATABASE_RELATION_PHASE_DRAFTS_MISSING"
    elif duplicates:
        reason_code = "DATABASE_RELATION_PHASE_RECEIPT_DUPLICATE"
    elif missing:
        reason_code = "DATABASE_RELATION_REQUIRED_PHASE_RECEIPT_MISSING"
    return _receipt(
        observer_id=PHASE_AGGREGATE_OBSERVER_ID,
        status="OBSERVED" if complete else "INDETERMINATE",
        reason_code=reason_code,
        evidence=evidence,
        campaign_id=_text(env.get("campaign_id")),
        execution_id=_text(env.get("execution_id")),
    )


def install_database_relation_phase_observer() -> str:
    install_approved_database_relation_observer()
    if PHASE_AGGREGATE_OBSERVER_ID in registered_observer_ids():
        return PHASE_AGGREGATE_OBSERVER_ID
    return register_observer(
        PHASE_AGGREGATE_OBSERVER_ID,
        surface="database_relation_read_only",
        adapter=ADAPTER,
        handler=aggregate_database_relation_phase_receipts,
        evidence_keys=(
            "approved_database_relation_snapshots",
            _RECEIPT_KEY,
        ),
    )


def install_database_relation_phase_execution() -> None:
    """Extend the one existing phase executor; do not create another executor."""
    from . import database_observer_experiment_runtime as base

    original = getattr(base, "execute_database_observer_phase", None)
    if not callable(original) or getattr(original, _INSTALL_MARKER, False):
        return

    @functools.wraps(original)
    def wrapped(
        exp: dict[str, Any],
        *args: Any,
        **kwargs: Any,
    ) -> dict[str, Any]:
        root_summary = original(exp, *args, **kwargs)
        phase = kwargs.get("phase") or (args[0] if args else "")
        observations = kwargs.get("observations")
        if not isinstance(observations, dict):
            raise ValueError("database_relation_observer_observations_missing")
        relation_summary = execute_database_relation_observer_phase(
            exp,
            phase=_text(phase),
            root=kwargs.get("root"),
            project=kwargs.get("project"),
            runtime_contract=_dict(kwargs.get("runtime_contract")),
            runtime_bindings=_dict(kwargs.get("runtime_bindings")),
            observations=observations,
            steps_out=[
                dict(row)
                for row in _list(kwargs.get("steps_out"))
                if isinstance(row, dict)
            ],
            campaign_id=_text(kwargs.get("campaign_id")),
            execution_id=_text(kwargs.get("execution_id")),
        )
        if _text(relation_summary.get("status")) == "NOT_APPLICABLE":
            return root_summary
        root_ok = _text(root_summary.get("status")) in {
            "OBSERVED",
            "NOT_APPLICABLE",
        }
        relation_ok = _text(relation_summary.get("status")) == "OBSERVED"
        target = _text(phase).upper()
        return {
            **dict(root_summary),
            "schema": "qualibug.combined-database-observer-phase-receipt.v1",
            "phase": target,
            "status": (
                "OBSERVED" if root_ok and relation_ok else "INDETERMINATE"
            ),
            "blocked": target == "BEFORE" and not (root_ok and relation_ok),
            "reason_code": (
                ""
                if root_ok and relation_ok
                else _text(root_summary.get("reason_code"))
                or _text(relation_summary.get("reason_code"))
                or f"DATABASE_OBSERVER_{target}_PHASE_INCOMPLETE"
            ),
            "root_observer_summary": dict(root_summary),
            "relation_observer_summary": dict(relation_summary),
            "query_execution_count": (
                int(root_summary.get("query_execution_count") or 0)
                + int(relation_summary.get("query_execution_count") or 0)
            ),
            "write_attempt_count": 0,
            "oracle_verdict_count": 0,
        }

    setattr(wrapped, _INSTALL_MARKER, True)
    setattr(wrapped, "__qualibug_original__", original)
    base.execute_database_observer_phase = wrapped
    executor = sys.modules.get(f"{__package__}.experiment_executor")
    if executor is not None:
        executor.execute_database_observer_phase = wrapped


__all__ = [
    "AGGREGATE_SCHEMA",
    "DRAFT_SCHEMA",
    "PHASE_AGGREGATE_OBSERVER_ID",
    "aggregate_database_relation_phase_receipts",
    "execute_database_relation_observer_phase",
    "install_database_relation_phase_execution",
    "install_database_relation_phase_observer",
]
