"""Adaptive discovery planner — obligation coverage and information gain."""
from __future__ import annotations

import hashlib
import json
from typing import Any

from .observed_product_scan_protocol import find_evaluator_private_context_paths


AGENT_INTENT_PLAN_SCHEMA = "qualibug.agent-intent-plan.v1"


class AgentIntentError(ValueError):
    """An Agent proposed intent outside the compiled Behavior IR authority."""


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
    predicted_compile = _num(hist.get(f"compile:{family}"), 1.0)
    predicted_exec = _num(hist.get(f"exec:{family}"), 1.0)
    measured_yield = hist.get(f"formal_yield:{family}")
    measured_cost = hist.get(f"cost:{family}")
    # Cold start intentionally omits yield and cost factors. Neutral factors
    # preserve source risk, confidence, novelty, and observed executability
    # without fabricating commercial yield or spend.
    yield_factor = (
        max(0.05, _num(measured_yield))
        if measured_yield is not None
        else 1.0
    )
    information_gain = (
        novelty * (0.4 + 0.6 * _num(measured_yield))
        if measured_yield is not None
        else novelty
    )
    expected_cost = (
        max(0.05, _num(measured_cost))
        if measured_cost is not None
        else 1.0
    )
    return (
        risk_priority
        * novelty
        * source_confidence
        * predicted_compile
        * predicted_exec
        * yield_factor
        * information_gain
        / expected_cost
    )


def plan_obligation_round(
    obligations: list[dict[str, Any]],
    *,
    experiments_by_obligation: dict[str, dict[str, Any]] | None = None,
    budget: int = 20,
    historical_yield: dict[str, float] | None = None,
    historical_receipt_ids: list[str] | None = None,
    cold_start_reason: str = "NO_MATCHING_HISTORY",
    covered_keys: set[str] | None = None,
) -> dict[str, Any]:
    """Select obligations that are compiled and maximize information gain."""
    if isinstance(budget, bool) or not isinstance(budget, int) or budget <= 0:
        raise ValueError("obligation_budget_invalid")
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

    # Family-fair selection within the fixed budget: do not let abundant
    # authorization/validation monopolize a cold-start window. Soft-cap is
    # floor(budget / distinct_families); remaining slots fill by score.
    families_present: list[str] = []
    seen_families: set[str] = set()
    for item in ranked:
        family = item["risk_family"]
        if family and family not in seen_families:
            seen_families.add(family)
            families_present.append(family)
    soft_cap = max(1, budget // max(1, len(families_present)))

    selected: list[dict[str, Any]] = []
    selected_ids: set[str] = set()
    family_counts: dict[str, int] = {}

    def _try_add(item: dict[str, Any]) -> bool:
        oid = item["obligation_id"]
        if len(selected) >= budget or oid in selected_ids:
            return False
        selected.append(item)
        selected_ids.add(oid)
        family = item["risk_family"]
        family_counts[family] = family_counts.get(family, 0) + 1
        return True

    for item in ranked:
        if family_counts.get(item["risk_family"], 0) < 1:
            _try_add(item)

    progressed = True
    while len(selected) < budget and progressed:
        progressed = False
        for family in families_present:
            if len(selected) >= budget:
                break
            if family_counts.get(family, 0) >= soft_cap:
                continue
            for item in ranked:
                if item["risk_family"] != family or item["obligation_id"] in selected_ids:
                    continue
                if _try_add(item):
                    progressed = True
                break

    for item in ranked:
        if len(selected) >= budget:
            break
        _try_add(item)

    pending = [item for item in ranked if item["obligation_id"] not in selected_ids]
    return {
        "schema_version": "qualibug.adaptive-obligation-plan.v1",
        "budget": budget,
        "history_status": "OBSERVED" if historical_yield else "COLD_START",
        "cold_start_reason": "" if historical_yield else _text(cold_start_reason),
        "formal_yield_status": (
            "MEASURED"
            if any(
                str(key).startswith("formal_yield:")
                for key in _dict(historical_yield)
            )
            else "NOT_MEASURED"
        ),
        "historical_receipt_ids": [
            _text(value)
            for value in _list(historical_receipt_ids)
            if _text(value)
        ],
        "selected": selected,
        "pending_next_round": pending[:200],
        "selected_count": len(selected),
        "pending_count": len(pending),
        "family_coverage": family_counts,
        "stop_condition": "budget_exhausted" if pending else "in_scope_obligations_scheduled",
    }


def _source_refs(*values: Any) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for value in values:
        for raw in _list(value):
            if not isinstance(raw, dict):
                continue
            row = dict(raw)
            key = json.dumps(
                row,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            )
            if key in seen:
                continue
            seen.add(key)
            result.append(row)
    return result


def build_agent_intent_plan(
    adaptive_plan: dict[str, Any],
    *,
    obligations: list[dict[str, Any]],
    experiments_by_obligation: dict[str, dict[str, Any]],
    behavior_ir: dict[str, Any],
) -> dict[str, Any]:
    """Bind planner intent to existing IR nodes and compiled experiment receipts."""

    private_paths = find_evaluator_private_context_paths({
        "adaptive_plan": adaptive_plan,
        "obligations": obligations,
        "experiments": experiments_by_obligation,
        "behavior_ir": behavior_ir,
    })
    if private_paths:
        raise AgentIntentError(
            "evaluator_private_context_forbidden:" + ",".join(private_paths)
        )
    if adaptive_plan.get("schema_version") != "qualibug.adaptive-obligation-plan.v1":
        raise AgentIntentError("adaptive_plan_schema_invalid")
    obligations_by_id: dict[str, dict[str, Any]] = {}
    for row in obligations:
        oid = _text(_dict(row).get("obligation_id"))
        if not oid or oid in obligations_by_id:
            raise AgentIntentError(f"obligation_identity_invalid:{oid or 'missing'}")
        obligations_by_id[oid] = dict(row)
    operations = {
        _text(_dict(row).get("id")): dict(row)
        for row in _list(behavior_ir.get("operations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    actors = {
        _text(_dict(row).get("id"))
        for row in _list(behavior_ir.get("actors"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    relations = {
        _text(_dict(row).get("id"))
        for row in _list(behavior_ir.get("relations"))
        if isinstance(row, dict) and _text(row.get("id"))
    }
    intents: list[dict[str, Any]] = []
    for planned in _list(adaptive_plan.get("selected")):
        row = _dict(planned)
        obligation_id = _text(row.get("obligation_id"))
        obligation = obligations_by_id.get(obligation_id)
        if obligation is None:
            raise AgentIntentError(f"unknown_obligation:{obligation_id or 'missing'}")
        experiment = _dict(experiments_by_obligation.get(obligation_id))
        experiment_id = _text(experiment.get("experiment_id"))
        if (
            not experiment_id
            or experiment_id != _text(row.get("experiment_id"))
            or _text(_dict(experiment.get("compile_receipt")).get("status"))
            != "COMPILED"
        ):
            raise AgentIntentError(
                f"compiled_experiment_mismatch:{obligation_id}"
            )
        operation_refs = sorted({
            _text(value)
            for value in _list(obligation.get("required_operations"))
            if _text(value)
        })
        actor_refs = sorted({
            _text(value)
            for value in _list(obligation.get("required_actors"))
            if _text(value)
        })
        relation_refs = sorted({
            _text(value)
            for value in _list(obligation.get("relation_refs"))
            if _text(value)
        })
        unknown_operations = sorted(set(operation_refs) - set(operations))
        unknown_actors = sorted(set(actor_refs) - actors)
        unknown_relations = sorted(set(relation_refs) - relations)
        if unknown_operations or unknown_actors or unknown_relations:
            raise AgentIntentError(
                "behavior_ir_reference_invalid:"
                + json.dumps({
                    "operations": unknown_operations,
                    "actors": unknown_actors,
                    "relations": unknown_relations,
                }, sort_keys=True)
            )
        observers = [
            dict(value)
            for value in _list(experiment.get("observers"))
            if isinstance(value, dict)
        ]
        observer_refs = sorted({
            _text(value.get("observer_id")) for value in observers
            if _text(value.get("observer_id"))
        })
        adapters = sorted({
            _text(value.get("adapter")) for value in observers
            if _text(value.get("adapter"))
        })
        if not observer_refs or not adapters:
            raise AgentIntentError(f"observer_authority_missing:{obligation_id}")
        source_refs = _source_refs(
            obligation.get("source_refs"),
            experiment.get("source_refs"),
            *[operations[ref].get("source_refs") for ref in operation_refs],
        )
        if not source_refs:
            raise AgentIntentError(f"source_authority_missing:{obligation_id}")
        material = f"{obligation_id}:{experiment_id}:{','.join(operation_refs)}"
        intents.append({
            "intent_id": "intent_" + hashlib.sha256(
                material.encode("utf-8")
            ).hexdigest()[:20],
            "status": "VERIFIED",
            "semantic_authority": "behavior_ir",
            "obligation_id": obligation_id,
            "experiment_id": experiment_id,
            "risk_family": _text(obligation.get("risk_family")),
            "operation_refs": operation_refs,
            "actor_refs": actor_refs,
            "relation_refs": relation_refs,
            "observer_refs": observer_refs,
            "execution_adapters": adapters,
            "source_refs": source_refs,
            "planner_score": _num(row.get("score")),
        })
    pending_ids = [
        _text(_dict(row).get("obligation_id"))
        for row in _list(adaptive_plan.get("pending_next_round"))
        if _text(_dict(row).get("obligation_id"))
    ]
    unknown_pending = sorted(set(pending_ids) - set(obligations_by_id))
    if unknown_pending:
        raise AgentIntentError(
            "unknown_pending_obligation:" + ",".join(unknown_pending)
        )
    payload = {
        "schema_version": AGENT_INTENT_PLAN_SCHEMA,
        "status": "VERIFIED",
        "generator": "adaptive_discovery_agent",
        "behavior_ir_model_id": _text(behavior_ir.get("model_id")),
        "semantic_authority": "behavior_ir",
        "intent_count": len(intents),
        "pending_count": len(pending_ids),
        "pending_obligation_ids": pending_ids,
        "intents": intents,
    }
    payload["intent_plan_fingerprint"] = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()
    return payload
