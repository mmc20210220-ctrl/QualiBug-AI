"""Adaptive planner facade preserving executable Coverage Units.

The current mainline planner is preserved byte-for-byte in
``adaptive_discovery_planner_base``.  This facade repairs one readiness gap:
a Coverage Unit can have a representative that compiled but failed the later
pre-transport binding proof while another variant in the same unit is already
COMPILED and transport-executable.  Selection must use that executable variant
rather than suppressing the whole semantic surface.
"""
from __future__ import annotations

from typing import Any

from . import adaptive_discovery_planner_base as _base

for _name in dir(_base):
    if not _name.startswith("__"):
        globals().setdefault(_name, getattr(_base, _name))

_ORIGINAL_PLAN_COVERAGE_UNIT_ROUND = _base.plan_coverage_unit_round


def _compile_status(
    obligation_id: str,
    *,
    obligations_by_id: dict[str, dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> str:
    experiment = _base._dict(experiments_by_obligation.get(obligation_id))
    status = _base._text(
        _base._dict(experiment.get("compile_receipt")).get("status")
    ).upper()
    if not status:
        status = _base._text(experiment.get("compile_status")).upper()
    if not status:
        status = _base._text(
            _base._dict(obligations_by_id.get(obligation_id)).get("compile_status")
        ).upper()
    return status


def _transport_executable(
    obligation_id: str,
    *,
    obligations_by_id: dict[str, dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> bool:
    obligation = _base._dict(obligations_by_id.get(obligation_id))
    experiment = _base._dict(experiments_by_obligation.get(obligation_id))
    if _compile_status(
        obligation_id,
        obligations_by_id=obligations_by_id,
        experiments_by_obligation=experiments_by_obligation,
    ) != "COMPILED":
        return False
    # The pre-transport gate stamps both rows on failure. Treat either explicit
    # False as authoritative; an absent flag preserves legacy behavior because
    # older callers/tests may invoke the planner before this receipt exists.
    if obligation.get("pre_transport_executable") is False:
        return False
    if experiment.get("pre_transport_executable") is False:
        return False
    return True


def promote_executable_coverage_unit_representatives(
    units: list[dict[str, Any]],
    *,
    obligations_by_id: dict[str, dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Return copied units with a deterministic executable representative.

    Original representative wins whenever it is executable. Otherwise choose
    among executable variants by highest source confidence, then smallest
    obligation id. No executable variant => preserve the original unit so the
    established planner continues to fail closed.
    """
    output: list[dict[str, Any]] = []
    promotions: list[dict[str, str]] = []
    for raw in units:
        if not isinstance(raw, dict):
            continue
        unit = dict(raw)
        original = _base._text(unit.get("representative_obligation_id"))
        if original and _transport_executable(
            original,
            obligations_by_id=obligations_by_id,
            experiments_by_obligation=experiments_by_obligation,
        ):
            output.append(unit)
            continue

        candidates: list[str] = []
        for value in _base._list(unit.get("obligation_ids")):
            oid = _base._text(value)
            if not oid or oid == original:
                continue
            if _transport_executable(
                oid,
                obligations_by_id=obligations_by_id,
                experiments_by_obligation=experiments_by_obligation,
            ):
                candidates.append(oid)
        if not candidates:
            output.append(unit)
            continue

        candidates.sort(
            key=lambda oid: (
                -_base._num(
                    _base._dict(obligations_by_id.get(oid)).get("confidence"),
                    0.0,
                ),
                oid,
            )
        )
        promoted = candidates[0]
        unit["representative_obligation_id"] = promoted
        output.append(unit)
        promotions.append({
            "coverage_unit_id": _base._text(unit.get("coverage_unit_id")),
            "from_obligation_id": original,
            "to_obligation_id": promoted,
            "reason": "representative_pre_transport_not_executable",
        })
    return output, promotions


def plan_coverage_unit_round(
    units: list[dict[str, Any]],
    *,
    obligations_by_id: dict[str, dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any] | None = None,
    budget: int = 600,
    historical_yield: dict[str, float] | None = None,
    historical_receipt_ids: list[str] | None = None,
    cold_start_reason: str = "NO_MATCHING_HISTORY",
    covered_keys: set[str] | None = None,
    type_minimum_guarantees: dict[str, int] | None = None,
    learned_boost_index: dict[str, Any] | None = None,
) -> dict[str, Any]:
    promoted_units, promotions = promote_executable_coverage_unit_representatives(
        units,
        obligations_by_id=obligations_by_id,
        experiments_by_obligation=experiments_by_obligation,
    )
    plan = _ORIGINAL_PLAN_COVERAGE_UNIT_ROUND(
        promoted_units,
        obligations_by_id=obligations_by_id,
        experiments_by_obligation=experiments_by_obligation,
        behavior_ir=behavior_ir,
        budget=budget,
        historical_yield=historical_yield,
        historical_receipt_ids=historical_receipt_ids,
        cold_start_reason=cold_start_reason,
        covered_keys=covered_keys,
        type_minimum_guarantees=type_minimum_guarantees,
        learned_boost_index=learned_boost_index,
    )
    plan["executable_representative_promotion"] = {
        "schema_version": "qualibug.executable-representative-promotion.v1",
        "promotion_count": len(promotions),
        "promotions": promotions[:100],
        "authority": "compiled_and_pre_transport_executable_variant_only",
    }
    return plan


# Any base helper that resolves this function dynamically receives the governed
# implementation while every other planner mechanism remains unchanged.
_base.plan_coverage_unit_round = plan_coverage_unit_round
