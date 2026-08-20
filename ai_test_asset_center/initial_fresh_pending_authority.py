"""Derive the first exact fresh continuation authority from planning inputs.

The adaptive plan may expose only a bounded ``pending_next_round`` preview.
At the execution boundary the full source obligation universe, compiled map and
Behavior IR still exist, so the exact fresh identity set can be derived before
any cross-round persistence depends on the preview. Subsequent rounds carry the
explicit ``fresh_pending_pool`` and never need this derivation again.
"""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _compile_status(experiment: Any, obligation: Any) -> str:
    exp = _dict(experiment)
    status = _text(_dict(exp.get("compile_receipt")).get("status")).upper()
    if not status:
        status = _text(exp.get("compile_status")).upper()
    if not status:
        status = _text(_dict(obligation).get("compile_status")).upper()
    return status


def _eligible(
    obligation: dict[str, Any],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> bool:
    oid = _text(obligation.get("obligation_id"))
    return (
        bool(oid)
        and obligation.get("pre_transport_executable") is not False
        and _compile_status(experiments_by_obligation.get(oid), obligation)
        == "COMPILED"
    )


def _pool_row(
    obligation: dict[str, Any],
    *,
    preview_by_id: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    oid = _text(obligation.get("obligation_id"))
    preview = preview_by_id.get(oid) or {}
    row: dict[str, Any] = {"obligation_id": oid}
    for key in (
        "coverage_unit_id",
        "canonical_obligation_key",
        "risk_family",
        "not_in_plan_reason",
    ):
        value = preview.get(key)
        if value in (None, "", [], {}):
            value = obligation.get(key)
        if value not in (None, "", [], {}):
            row[key] = value
    return row


def _obligation_fresh_rows(
    *,
    plan: dict[str, Any],
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    selected_ids = {
        _text(row.get("obligation_id"))
        for row in _list(plan.get("selected"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    preview = [
        dict(row)
        for row in _list(plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    preview_by_id = {
        _text(row.get("obligation_id")): row for row in preview
    }
    obligations_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    eligible_ids = {
        oid
        for oid, obligation in obligations_by_id.items()
        if oid not in selected_ids
        and _eligible(obligation, experiments_by_obligation)
    }
    # Preserve planner preview order first; omitted source rows follow source
    # obligation order. Follow-on planning reranks the complete fresh set, so
    # the authority here is the exact membership, not a second ranking policy.
    ordered_ids: list[str] = []
    for row in preview:
        oid = _text(row.get("obligation_id"))
        if oid in eligible_ids and oid not in ordered_ids:
            ordered_ids.append(oid)
    for raw in obligations:
        oid = _text(_dict(raw).get("obligation_id"))
        if oid in eligible_ids and oid not in ordered_ids:
            ordered_ids.append(oid)
    return [
        _pool_row(obligations_by_id[oid], preview_by_id=preview_by_id)
        for oid in ordered_ids
    ]


def _coverage_unit_fresh_rows(
    *,
    plan: dict[str, Any],
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> list[dict[str, Any]]:
    from .coverage_unit_registry import build_coverage_units

    obligations_by_id = {
        _text(row.get("obligation_id")): dict(row)
        for row in obligations
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    }
    unit_pack = build_coverage_units(obligations, behavior_ir=behavior_ir)
    units = [
        dict(row)
        for row in _list(unit_pack.get("coverage_units"))
        if isinstance(row, dict) and _text(row.get("coverage_unit_id"))
    ]
    selected_unit_ids = {
        _text(row.get("coverage_unit_id"))
        for row in [
            *_list(plan.get("selected_units")),
            *_list(plan.get("selected")),
        ]
        if isinstance(row, dict) and _text(row.get("coverage_unit_id"))
    }
    preview = [
        dict(row)
        for row in _list(plan.get("pending_next_round"))
        if isinstance(row, dict) and _text(row.get("obligation_id"))
    ]
    preview_by_id = {
        _text(row.get("obligation_id")): row for row in preview
    }
    row_by_unit: dict[str, dict[str, Any]] = {}
    for unit in units:
        unit_id = _text(unit.get("coverage_unit_id"))
        if unit_id in selected_unit_ids:
            continue
        rep_id = _text(unit.get("representative_obligation_id"))
        representative = obligations_by_id.get(rep_id)
        if representative is None or not _eligible(
            representative, experiments_by_obligation
        ):
            continue
        row = _pool_row(representative, preview_by_id=preview_by_id)
        row["coverage_unit_id"] = unit_id
        canonical_key = _text(unit.get("canonical_obligation_key"))
        if canonical_key:
            row["canonical_obligation_key"] = canonical_key
        row_by_unit[unit_id] = row

    ordered_units: list[str] = []
    for preview_row in preview:
        unit_id = _text(preview_row.get("coverage_unit_id"))
        if unit_id in row_by_unit and unit_id not in ordered_units:
            ordered_units.append(unit_id)
    for unit in units:
        unit_id = _text(unit.get("coverage_unit_id"))
        if unit_id in row_by_unit and unit_id not in ordered_units:
            ordered_units.append(unit_id)
    return [row_by_unit[unit_id] for unit_id in ordered_units]


def seed_initial_fresh_pending_authority(
    *,
    obligation_plan: dict[str, Any],
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Attach exact first-round fresh identity only when membership is provable.

    Existing ``fresh_pending_pool`` is immutable resume authority and wins
    immediately. A true first planning handoff has no retry/deferred resume pool,
    so source inputs plus ``pending_count`` can prove exact fresh membership.
    Legacy mixed resumes do not contain enough information to distinguish
    already-completed unselected source obligations from omitted fresh work; they
    stay in legacy reconstruction mode rather than being falsely sealed exact.
    """
    plan = dict(_dict(obligation_plan))
    if "fresh_pending_pool" in plan:
        return plan

    has_legacy_resume_pools = bool(
        _list(plan.get("blocked_retry_pool"))
        or _list(plan.get("budget_deferred_pool"))
    )
    planner_pending_count = int(plan.get("pending_count") or 0)
    if has_legacy_resume_pools:
        plan["fresh_pending_authority_receipt"] = {
            "schema_version": "qualibug.fresh-pending-authority.v1",
            "status": "LEGACY_FALLBACK",
            "reason": "mixed_legacy_resume_pools_without_exact_fresh_membership",
            "planner_pending_count": planner_pending_count,
            "blocked_retry_pool_count": len(
                _list(plan.get("blocked_retry_pool"))
            ),
            "budget_deferred_pool_count": len(
                _list(plan.get("budget_deferred_pool"))
            ),
        }
        return plan

    source_obligations = [
        dict(row) for row in obligations if isinstance(row, dict)
    ]
    experiments = {
        _text(key): dict(value)
        for key, value in _dict(experiments_by_obligation).items()
        if _text(key) and isinstance(value, dict)
    }
    if _text(plan.get("plan_authority")).lower() == "coverage_unit":
        try:
            fresh_rows = _coverage_unit_fresh_rows(
                plan=plan,
                obligations=source_obligations,
                experiments_by_obligation=experiments,
                behavior_ir=_dict(behavior_ir),
            )
        except Exception as exc:
            plan["fresh_pending_authority_receipt"] = {
                "schema_version": "qualibug.fresh-pending-authority.v1",
                "status": "LEGACY_FALLBACK",
                "reason": f"coverage_unit_rebuild_failed:{type(exc).__name__}",
            }
            return plan
    else:
        fresh_rows = _obligation_fresh_rows(
            plan=plan,
            obligations=source_obligations,
            experiments_by_obligation=experiments,
        )

    if len(fresh_rows) != planner_pending_count:
        plan["fresh_pending_authority_receipt"] = {
            "schema_version": "qualibug.fresh-pending-authority.v1",
            "status": "LEGACY_FALLBACK",
            "reason": "fresh_membership_count_mismatch",
            "derived_fresh_count": len(fresh_rows),
            "planner_pending_count": planner_pending_count,
        }
        return plan

    plan["fresh_pending_pool"] = fresh_rows
    plan["fresh_pending_pool_count"] = len(fresh_rows)
    plan["fresh_pending_authority_receipt"] = {
        "schema_version": "qualibug.fresh-pending-authority.v1",
        "status": "SEALED",
        "authority": "source_planning_universe_before_preview_reconstruction",
        "fresh_pending_pool_count": len(fresh_rows),
        "planner_pending_count": planner_pending_count,
    }
    return plan


__all__ = ["seed_initial_fresh_pending_authority"]
