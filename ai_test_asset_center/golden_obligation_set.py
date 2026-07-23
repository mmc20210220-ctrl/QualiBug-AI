"""Golden obligation set for regression testing.

P0-6: Select a fixed set of 20 obligations (5 causal + 5 conservation +
5 state_transition + 5 authorization) that are most likely to execute
successfully. Used for regression validation of the executable pipeline.

Selection criteria:
- Operation is bound (has resolved path)
- Observer exists (effect observation path available)
- Minimal fixture dependencies
- Compiled successfully
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any


GOLDEN_SET_SCHEMA = "qualibug.golden-obligation-set.v1"

# Target counts per risk_family
GOLDEN_FAMILY_TARGETS: dict[str, int] = {
    "causal_postcondition": 5,
    "conservation": 5,
    "state_transition": 5,
    "authorization": 5,
}

# Fallback families if primary targets cannot be met
_FALLBACK_FAMILIES = ("state", "idempotency", "consistency", "isolation", "validation")


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _readiness_score(
    obligation: dict[str, Any],
    experiment: dict[str, Any],
) -> float:
    """Score how ready an obligation is for execution (higher = better)."""
    score = 0.0
    obl = _dict(obligation)
    exp = _dict(experiment)

    # Compiled experiment exists
    receipt = _dict(exp.get("compile_receipt"))
    if _text(receipt.get("status")).upper() == "COMPILED":
        score += 10.0
    elif _text(obl.get("compile_status")).upper() == "COMPILED":
        score += 8.0

    # Has resolved operation path
    has_path = False
    for plan_key in ("treatment_plan", "control_plan"):
        for step in _list(exp.get(plan_key)):
            if isinstance(step, dict) and _text(step.get("path")):
                has_path = True
                break
        if has_path:
            break
    if has_path:
        score += 5.0

    # Has actor bound
    has_actor = False
    for plan_key in ("treatment_plan", "control_plan"):
        for step in _list(exp.get(plan_key)):
            if isinstance(step, dict) and _text(step.get("actor_ref")):
                has_actor = True
                break
        if has_actor:
            break
    if has_actor:
        score += 3.0

    # Minimal fixture dependencies
    fixture_dag = _dict(exp.get("fixture_dag"))
    fixture_steps = _list(fixture_dag.get("steps"))
    if not fixture_steps:
        score += 4.0  # No fixtures needed
    elif len(fixture_steps) <= 2:
        score += 2.0

    # Not blocked
    if _text(fixture_dag.get("status")).upper() != "BLOCKED":
        score += 2.0

    # Has assertions
    assertions = _list(exp.get("assertions") or obl.get("assertions"))
    if assertions:
        score += 1.0

    # Confidence from obligation
    try:
        conf = float(obl.get("confidence") or 0.5)
        score += conf * 2.0
    except (TypeError, ValueError):
        pass

    return score


def select_golden_set(
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    *,
    family_targets: dict[str, int] | None = None,
) -> dict[str, Any]:
    """Select a fixed golden set of obligations for regression testing.

    Returns a receipt with the selected obligation IDs and metadata.
    """
    targets = dict(family_targets or GOLDEN_FAMILY_TARGETS)
    experiments = dict(experiments_by_obligation or {})

    # Group obligations by risk_family
    by_family: dict[str, list[tuple[float, dict[str, Any]]]] = {}
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        if not oid:
            continue
        family = _text(obl.get("risk_family"))
        if not family:
            continue
        exp = _dict(experiments.get(oid))
        score = _readiness_score(obl, exp)
        by_family.setdefault(family, []).append((score, obl))

    # Sort each family by readiness score (descending)
    for family in by_family:
        by_family[family].sort(key=lambda item: (-item[0], _text(item[1].get("obligation_id"))))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    family_counts: dict[str, int] = {}

    # Select from primary targets
    for family, target_count in sorted(targets.items(), key=lambda kv: -kv[1]):
        candidates = by_family.get(family, [])
        count = 0
        for score, obl in candidates:
            if count >= target_count:
                break
            oid = _text(obl.get("obligation_id"))
            if oid in selected_ids:
                continue
            selected.append({
                "obligation_id": oid,
                "risk_family": family,
                "readiness_score": round(score, 2),
                "experiment_id": _text(_dict(experiments.get(oid)).get("experiment_id")),
            })
            selected_ids.add(oid)
            count += 1
        family_counts[family] = count

    # Fill shortfall from fallback families
    total_target = sum(targets.values())
    if len(selected) < total_target:
        remaining = total_target - len(selected)
        for family in _FALLBACK_FAMILIES:
            if remaining <= 0:
                break
            candidates = by_family.get(family, [])
            for score, obl in candidates:
                if remaining <= 0:
                    break
                oid = _text(obl.get("obligation_id"))
                if oid in selected_ids:
                    continue
                selected.append({
                    "obligation_id": oid,
                    "risk_family": family,
                    "readiness_score": round(score, 2),
                    "experiment_id": _text(_dict(experiments.get(oid)).get("experiment_id")),
                })
                selected_ids.add(oid)
                family_counts[family] = family_counts.get(family, 0) + 1
                remaining -= 1

    return {
        "schema_version": GOLDEN_SET_SCHEMA,
        "golden_obligation_ids": sorted(selected_ids),
        "golden_obligations": selected,
        "selected_count": len(selected),
        "family_counts": family_counts,
        "family_targets": targets,
        "total_obligations_available": sum(len(v) for v in by_family.values()),
    }


def load_golden_set(path: Path) -> dict[str, Any] | None:
    """Load a persisted golden set from JSON."""
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and data.get("schema_version") == GOLDEN_SET_SCHEMA:
            return data
    except (OSError, json.JSONDecodeError):
        pass
    return None


def save_golden_set(receipt: dict[str, Any], path: Path) -> None:
    """Persist a golden set to JSON for regression reuse."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
