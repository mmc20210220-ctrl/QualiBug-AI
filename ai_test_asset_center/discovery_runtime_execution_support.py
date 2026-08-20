"""Compatibility facade with lossless pending-round continuation authority.

The current execution-support implementation is preserved byte-for-byte in
``discovery_runtime_execution_support_base``. Only pending continuation is
overridden: the public pending preview may remain bounded while in-process
scheduling retains every eligible, not-yet-processed identity.
"""
from __future__ import annotations

from typing import Any

from . import discovery_runtime_execution_support_base as _base
from .adaptive_discovery_planner import _obligation_view_from_compiled_experiment
from .recall_pending_continuation_authority import (
    consume_pending_obligation_rounds as _exact_consume_pending_obligation_rounds,
)

# Preserve the complete historical module surface, including private helpers
# imported by discovery_runtime_execution and compatibility tests.
for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _continuation_obligation_universe(
    *,
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    obligation_plan: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return the planner-visible obligation universe for continuation rounds.

    Round-one intent binding already accepts compiler-expanded obligation ids
    that exist only in ``experiments_by_obligation``. A deferred compiled-only
    id must therefore remain planner-visible in round 2+ as well; otherwise it
    is present in the pending queue but disappears while ``remaining_obligations``
    is built. Reconstruct the exact same source-backed obligation view used by
    ``build_agent_intent_plan`` and carry coverage-unit metadata from the plan
    or compiled experiment. No new semantic fields are invented.
    """
    merged: list[dict[str, Any]] = []
    by_id: dict[str, dict[str, Any]] = {}
    for raw in obligations:
        if not isinstance(raw, dict):
            continue
        oid = _text(raw.get("obligation_id"))
        if not oid or oid in by_id:
            continue
        row = dict(raw)
        by_id[oid] = row
        merged.append(row)

    plan_metadata: dict[str, dict[str, Any]] = {}
    unit_by_obligation_id: dict[str, str] = {}
    for key in ("selected", "pending_next_round", "selected_units"):
        for raw in _list(_dict(obligation_plan).get(key)):
            if not isinstance(raw, dict):
                continue
            oid = _text(raw.get("obligation_id"))
            if oid:
                plan_metadata[oid] = dict(raw)
            unit_id = _text(raw.get("coverage_unit_id"))
            if not unit_id:
                continue
            if oid:
                unit_by_obligation_id[oid] = unit_id
            for member in _list(raw.get("obligation_ids")):
                member_id = _text(member)
                if member_id:
                    unit_by_obligation_id[member_id] = unit_id

    for raw_key, raw_experiment in _dict(experiments_by_obligation).items():
        oid = _text(raw_key)
        experiment = _dict(raw_experiment)
        if not oid or oid in by_id or not experiment:
            continue
        view = _obligation_view_from_compiled_experiment(experiment, oid)
        if view is None:
            continue

        parent_id = _text(
            experiment.get("expanded_from_obligation_id")
            or experiment.get("representative_obligation_id")
        )
        parent = by_id.get(parent_id) or {}
        for field in (
            "confidence",
            "subject_refs",
            "property",
            "pre_transport_executable",
            "canonical_obligation_key",
        ):
            if field in parent and field not in view:
                view[field] = parent[field]

        metadata = plan_metadata.get(oid) or {}
        unit_id = _text(
            metadata.get("coverage_unit_id")
            or experiment.get("coverage_unit_id")
            or unit_by_obligation_id.get(oid)
            or parent.get("coverage_unit_id")
        )
        if unit_id:
            view["coverage_unit_id"] = unit_id
        canonical_key = _text(
            metadata.get("canonical_obligation_key")
            or experiment.get("canonical_obligation_key")
            or parent.get("canonical_obligation_key")
        )
        if canonical_key:
            view["canonical_obligation_key"] = canonical_key

        by_id[oid] = view
        merged.append(view)

    return merged


def _consume_pending_obligation_rounds(*, obligations, experiments_by_obligation, obligation_plan, **kwargs):
    """Use one authoritative candidate universe for round one and continuation."""
    continuation_obligations = _continuation_obligation_universe(
        obligations=[dict(row) for row in _list(obligations) if isinstance(row, dict)],
        experiments_by_obligation=dict(_dict(experiments_by_obligation)),
        obligation_plan=dict(_dict(obligation_plan)),
    )
    return _exact_consume_pending_obligation_rounds(
        obligation_plan=obligation_plan,
        obligations=continuation_obligations,
        experiments_by_obligation=experiments_by_obligation,
        **kwargs,
    )
