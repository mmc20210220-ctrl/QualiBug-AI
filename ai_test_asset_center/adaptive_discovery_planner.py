"""Adaptive discovery planner — obligation coverage and information gain."""
from __future__ import annotations

from typing import Any


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _num(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def score_obligation(
    obligation: dict[str, Any],
    *,
    covered_keys: set[str],
    historical_yield: dict[str, float] | None = None,
) -> float:
    """Score for formal TP information gain — never trust model self-confidence alone."""
    obl = _dict(obligation)
    family = _text(obl.get("risk_family"))
    hist = _dict(historical_yield)
    risk_priority = min(
        2.0,
        max(
            0.05,
            _num(hist.get(f"risk:{family}"), _num(obl.get("risk_priority"), 1.0)),
        ),
    )
    key = f"{family}|{','.join(_list(obl.get('subject_refs'))[:3])}"
    novelty = 1.0 if key not in covered_keys else 0.15
    source_confidence = min(0.95, max(0.05, _num(obl.get("confidence"), 0.5)))
    predicted_compile = _num(hist.get(f"compile:{family}"), 0.7)
    predicted_exec = _num(hist.get(f"exec:{family}"), 0.6)
    predicted_yield = _num(hist.get(f"formal_yield:{family}"), 0.2)
    # Do not treat model confidence as formal yield.
    information_gain = novelty * (0.4 + 0.6 * predicted_yield)
    expected_cost = max(0.05, _num(hist.get(f"cost:{family}"), 0.2))
    return (
        risk_priority
        * novelty
        * source_confidence
        * predicted_compile
        * predicted_exec
        * max(0.05, predicted_yield)
        * information_gain
        / expected_cost
    )


def plan_obligation_round(
    obligations: list[dict[str, Any]],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]] | None = None,
    budget: int = 20,
    historical_yield: dict[str, float] | None = None,
    covered_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Select obligations that are compiled and maximize information gain."""
    experiments = dict(experiments_by_obligation or {})
    covered = set(covered_keys or [])
    ranked: list[dict[str, Any]] = []
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        oid = _text(obl.get("obligation_id"))
        exp = _dict(experiments.get(oid))
        receipt = _dict(exp.get("compile_receipt"))
        if _text(receipt.get("status")) != "COMPILED" and _text(obl.get("compile_status")) != "COMPILED":
            continue
        score = score_obligation(obl, covered_keys=covered, historical_yield=historical_yield)
        ranked.append({
            "obligation_id": oid,
            "risk_family": _text(obl.get("risk_family")),
            "score": round(score, 6),
            "experiment_id": _text(exp.get("experiment_id")),
        })
    ranked.sort(key=lambda item: (-item["score"], item["obligation_id"]))

    # Minimum coverage quotas by family
    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    family_counts: dict[str, int] = {}
    for item in ranked:
        family = item["risk_family"]
        if family_counts.get(family, 0) < 1 and len(selected) < budget:
            selected.append(item)
            selected_ids.add(item["obligation_id"])
            family_counts[family] = family_counts.get(family, 0) + 1
    for item in ranked:
        if len(selected) >= budget:
            break
        if item["obligation_id"] in selected_ids:
            continue
        selected.append(item)
        selected_ids.add(item["obligation_id"])
        family = item["risk_family"]
        family_counts[family] = family_counts.get(family, 0) + 1

    pending = [item for item in ranked if item["obligation_id"] not in selected_ids]
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "budget": budget,
        "selected": selected,
        "pending_next_round": pending[:200],
        "selected_count": len(selected),
        "pending_count": len(pending),
        "family_coverage": family_counts,
        "stop_condition": "budget_exhausted" if pending else "in_scope_obligations_scheduled",
    }
