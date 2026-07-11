"""Contract-based oracles with explicit activation requirements.

Business oracles may only fire when control, treatment, fixtures, and observers
required by the experiment contract are present. Heuristic-only signals are
downgraded to internal clues and must not enter customer-delivery.
"""
from __future__ import annotations

import re
from typing import Any

from .assertion_dsl import evaluate_assertion


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def activation_requirements_met(experiment: dict[str, Any], evidence: dict[str, Any]) -> tuple[bool, list[str]]:
    missing: list[str] = []
    exp = _dict(experiment)
    ev = _dict(evidence)
    if _list(exp.get("control_plan")) and not ev.get("control_succeeded") and not ev.get("control_observation"):
        missing.append("control_evidence")
    if _list(exp.get("treatment_plan")) and not ev.get("treatment_observation") and not ev.get("treatment_result"):
        missing.append("treatment_evidence")
    for observer in _list(exp.get("observers")):
        oid = _text(_dict(observer).get("observer_id"))
        if oid and oid not in set(_text(x) for x in _list(ev.get("observer_ids"))) and oid not in ev:
            missing.append(f"observer:{oid}")
    if ev.get("harness_error"):
        missing.append("harness_error_present")
    return (not missing), missing


def mark_as_internal_clue(finding: dict[str, Any], *, reason: str) -> dict[str, Any]:
    row = dict(finding)
    row["customer_delivery_status"] = "clue"
    row["gate_passed"] = False
    row["oracle_tier"] = "internal_clue"
    row["oracle_demotion_reason"] = _text(reason)
    return row


def evaluate_contract_oracle(
    *,
    experiment: dict[str, Any],
    evidence: dict[str, Any],
) -> dict[str, Any]:
    """Evaluate contract assertions; harness errors never become defects."""
    ok, missing = activation_requirements_met(experiment, evidence)
    if _dict(evidence).get("harness_error"):
        return {
            "verdict": "harness_failure",
            "customer_deliverable": False,
            "missing_requirements": missing or ["harness_error"],
            "assertions": [],
        }
    if not ok:
        return {
            "verdict": "blocked_experiment",
            "customer_deliverable": False,
            "missing_requirements": missing,
            "assertions": [],
        }

    assertion_results = []
    for assertion in _list(experiment.get("assertions")):
        if not isinstance(assertion, dict):
            continue
        result = evaluate_assertion(assertion, observations=evidence, source_refs=list(experiment.get("source_refs") or []))
        assertion_results.append(result)

    # Heuristic dual-2xx concurrency/idempotency without business effect → clue only
    family = ""
    for assertion in _list(experiment.get("assertions")):
        family = _text(_dict(assertion).get("kind"))
        if family:
            break
    if family in {"concurrency", "idempotency"}:
        if evidence.get("dual_2xx") and evidence.get("effect_count") is None and evidence.get("invariant_held") is None:
            return {
                "verdict": "executed_clue",
                "customer_deliverable": False,
                "missing_requirements": ["business_effect_or_final_invariant"],
                "assertions": assertion_results,
                "demotion_reason": "http_status_heuristic_insufficient",
            }

    failed = [item for item in assertion_results if not item.get("passed")]
    if not assertion_results:
        return {
            "verdict": "executed_clue",
            "customer_deliverable": False,
            "missing_requirements": ["typed_assertion"],
            "assertions": [],
        }
    if failed:
        return {
            "verdict": "customer_deliverable_defect_candidate",
            "customer_deliverable": True,
            "missing_requirements": [],
            "assertions": assertion_results,
            "failed_assertions": failed,
        }
    return {
        "verdict": "property_held",
        "customer_deliverable": False,
        "missing_requirements": [],
        "assertions": assertion_results,
    }


HEURISTIC_BUSINESS_ORACLE_NAMES = frozenset({
    "concurrencyoracle",
    "concurrency",
    "idempotencyoracle",
    "idempotency",
    "permissionoracle",
    "privacyoracle",
    "stateoracle",
    "moneyoracle",
    "inventoryoracle",
    "workfloworacle",
    "quotaoracle",
    "tenantisolationoracle",
})


def contract_effect_evidence_present(evidence: dict[str, Any]) -> bool:
    """True when evidence carries a business effect or final invariant observation."""
    ev = _dict(evidence)
    return (
        ev.get("effect_count") is not None
        or ev.get("invariant_held") is not None
        or ev.get("business_effect_observed") is True
        or ev.get("final_state_observation") is not None
    )


def heuristic_status_only_signal(evidence: dict[str, Any]) -> bool:
    """HTTP dual-2xx / multi-success without contract effect is clue-only."""
    ev = _dict(evidence)
    if contract_effect_evidence_present(ev):
        return False
    return bool(
        ev.get("dual_2xx")
        or ev.get("multi_success_http")
        or (ev.get("effect_count") is None and ev.get("invariant_held") is None)
    )


def scenario_has_contract_activation(scenario: dict[str, Any]) -> bool:
    """Scenario may produce customer-deliverable business-oracle defects only with contract."""
    sc = _dict(scenario)
    if sc.get("contract_oracle_enabled") is True:
        return True
    exp = _dict(sc.get("experiment_contract") or sc.get("experiment"))
    if _list(exp.get("assertions")) and (_list(exp.get("control_plan")) or _list(exp.get("treatment_plan"))):
        return True
    evidence = _dict(sc.get("contract_evidence") or sc.get("evidence"))
    has_control = bool(evidence.get("control_succeeded") or evidence.get("authorized_control") or evidence.get("control_observation"))
    return has_control and contract_effect_evidence_present(evidence)


def _source_grounded_permission_bypass(finding: dict[str, Any]) -> bool:
    """Return True for direct, source-declared authorization bypass evidence."""
    row = _dict(finding)
    oracle = _dict(row.get("oracle"))
    if _text(oracle.get("oracle_name") or oracle.get("name")).lower() != "permissionoracle":
        return False
    if _text(oracle.get("violated_rule")).lower() != "unauthorized_access":
        return False
    if row.get("blocked_by_safety_boundary"):
        return False
    source_kinds = {
        _text(_dict(ref).get("kind")).lower()
        for ref in _list(row.get("source_refs"))
        if isinstance(ref, dict)
    }
    if not source_kinds.intersection({"permission_matrix", "api_permission_contract"}):
        return False
    evidence = _dict(row.get("evidence"))
    request = _text(evidence.get("request"))
    if not request or "{" in request or re.search(r"/:[A-Za-z_]", request):
        return False
    snapshot = _dict(row.get("before_after_snapshot"))
    observed = _dict(snapshot.get("after"))
    try:
        status = int(observed.get("status_code") or 0)
    except (TypeError, ValueError):
        return False
    if not 200 <= status < 300:
        return False
    method = _text(observed.get("method")).upper()
    body = observed.get("body")
    if method in {"GET", "HEAD"} and body in (None, {}, []):
        return False
    authenticated = bool(evidence.get("actor_token_present"))
    login = _dict(snapshot.get("before"))
    try:
        login_status = int(login.get("status_code") or 0)
    except (TypeError, ValueError):
        login_status = 0
    authenticated = authenticated or (
        _text(login.get("action")).lower().startswith("login")
        and 200 <= login_status < 300
    )
    return authenticated


def demote_heuristic_business_oracle_finding(finding: dict[str, Any]) -> dict[str, Any]:
    """Downgrade legacy heuristic business oracle hits to internal clues."""
    row = _dict(finding)
    oracle = _dict(row.get("oracle"))
    name = _text(oracle.get("oracle_name") or oracle.get("name") or row.get("category")).lower()
    compact = name.replace(" ", "")
    evidence = _dict(row.get("evidence") or row.get("raw_evidence"))
    if oracle.get("oracle_tier") == "internal_clue" or oracle.get("customer_deliverable") is False:
        return mark_as_internal_clue(row, reason=_text(oracle.get("demotion_reason") or "oracle_marked_non_deliverable"))
    matched = compact in HEURISTIC_BUSINESS_ORACLE_NAMES or any(
        token in compact for token in HEURISTIC_BUSINESS_ORACLE_NAMES
    )
    if not matched:
        return row
    if _source_grounded_permission_bypass(row):
        return row
    has_control = bool(evidence.get("control_succeeded") or evidence.get("authorized_control") or evidence.get("control_observation"))
    has_effect = contract_effect_evidence_present(evidence)
    # Status-only concurrency/idempotency heuristics never enter customer delivery.
    if "concurr" in compact or "idempot" in compact:
        if not has_control or not has_effect or heuristic_status_only_signal(evidence):
            return mark_as_internal_clue(row, reason="heuristic_business_oracle_without_contract")
    elif not has_control or not has_effect:
        return mark_as_internal_clue(row, reason="heuristic_business_oracle_without_contract")
    return row
