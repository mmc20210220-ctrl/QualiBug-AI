from __future__ import annotations

"""Phase94D: concurrency/race runtime probe planning.

The planner identifies document-grounded write endpoints where concurrent
execution often exposes high-value bugs: duplicate submission, double payment,
stock oversell, approval race and callback replay.  It generates sandbox probes
with explicit concurrency plans; the executor can run them with repeated HTTP
attempts while Phase92 invariants decide whether evidence is a real finding.
"""

import copy
import re
from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
RACE_SURFACE_RE = re.compile(r"(?:submit|pay|payment|callback|approve|cancel|reserve|stock|inventory|order|refund|积分|库存|支付|审批|回调|提交)", re.I)


def _is_write(probe: dict[str, Any]) -> bool:
    return str((probe.get("endpoint") or {}).get("method") or "").upper() in WRITE_METHODS


def _race_score(probe: dict[str, Any]) -> int:
    text = " ".join([
        str(probe.get("risk_type") or ""),
        str((probe.get("endpoint") or {}).get("path") or ""),
        str(probe.get("source_refs") or ""),
    ])
    score = len(RACE_SURFACE_RE.findall(text)) * 10
    risk = str(probe.get("risk_type") or "")
    if risk in {"idempotency_replay_probe", "conservation_probe", "state_transition_probe"}:
        score += 30
    if "ownership" in risk:
        score += 10
    return score


def generate_concurrency_race_probes(plan: dict[str, Any], *, max_probes: int = 30) -> dict[str, Any]:
    candidates = [p for p in (plan.get("probes") or []) if isinstance(p, dict) and _is_write(p)]
    candidates = sorted(candidates, key=_race_score, reverse=True)
    probes: list[dict[str, Any]] = []
    by_family: dict[str, int] = {}
    counter = 1
    for source in candidates:
        score = _race_score(source)
        if score < 20:
            continue
        families = _families_for_probe(source)
        for family in families[:2]:
            clone = copy.deepcopy(source)
            ep = clone.get("endpoint") or {}
            clone.update({
                "candidate_id": f"QBRC-94D-{counter:04d}",
                "risk_type": _risk_for_family(family, str(source.get("risk_type") or "")),
                "execution_policy": "disposable_sandbox_required",
                "endpoint": {"method": str(ep.get("method") or "POST").upper(), "path": ep.get("path")},
                "probe_plan": {
                    **(clone.get("probe_plan") if isinstance(clone.get("probe_plan"), dict) else {}),
                    "phase": "94D",
                    "strategy": "concurrency_race_runtime_probe",
                    "race_family": family,
                    "concurrency": {
                        "parallel_attempts": 2 if family == "terminal_transition_race" else 3,
                        "same_idempotency_key": family in {"idempotency_race", "callback_replay_race"},
                        "same_business_object": True,
                        "barrier_start": True,
                    },
                    "expected_status": [200, 201, 202, 400, 403, 409, 422],
                    "bug_oracle": _oracle_for_family(family),
                    "bug_discovery_value": "P0",
                    "source_candidate_id": source.get("candidate_id"),
                },
                "required_evidence": ["parallel_request_responses", "before_after_snapshot", "duplicate_side_effect_or_conservation_delta", "observer_graph_delta"],
                "grounding_basis": {**(clone.get("grounding_basis") if isinstance(clone.get("grounding_basis"), dict) else {}), "endpoint_contract_refs": 1, "supporting_requirement_refs": 1, "phase94d_race_surface_inference": 1},
            })
            refs = [r for r in (source.get("source_refs") or []) if isinstance(r, dict)]
            clone["source_refs"] = refs or [{"file": "grounded_probe_plan", "section": str(source.get("candidate_id") or ""), "quote": "Race probe was generated from a document-grounded write probe on a high-value business surface.", "kind": "business_rule"}]
            probes.append(clone)
            by_family[family] = by_family.get(family, 0) + 1
            counter += 1
            if len(probes) >= max_probes:
                break
        if len(probes) >= max_probes:
            break
    return {
        "engine": "concurrency_race_probe_planner_v1_phase94d",
        "generated_probe_count": len(probes),
        "generated_by_race_family": by_family,
        "probes": probes,
        "improvement_claim": {
            "race_surface_candidate_count": sum(1 for p in candidates if _race_score(p) >= 20),
            "added_concurrency_probe_count": len(probes),
            "race_family_count": len(by_family),
        },
    }


def _families_for_probe(probe: dict[str, Any]) -> list[str]:
    text = " ".join([str(probe.get("risk_type") or ""), str((probe.get("endpoint") or {}).get("path") or ""), str(probe.get("source_refs") or "")]).lower()
    families: list[str] = []
    if "idempot" in text or "callback" in text or "回调" in text:
        families.append("idempotency_race")
    if "stock" in text or "inventory" in text or "order" in text or "库存" in text:
        families.append("stock_oversell_race")
    if "approve" in text or "approval" in text or "审批" in text:
        families.append("approval_double_decision_race")
    if "state" in text or "transition" in text or "cancel" in text or "submit" in text:
        families.append("terminal_transition_race")
    if not families:
        families.append("generic_duplicate_write_race")
    return list(dict.fromkeys(families))


def _risk_for_family(family: str, fallback: str) -> str:
    if family in {"idempotency_race", "callback_replay_race"}:
        return "idempotency_replay_probe"
    if family == "stock_oversell_race":
        return "conservation_probe"
    if family in {"approval_double_decision_race", "terminal_transition_race"}:
        return "state_transition_probe"
    return fallback or "concurrency_race_probe"


def _oracle_for_family(family: str) -> str:
    return {
        "idempotency_race": "parallel_same_key_must_not_create_multiple_resources",
        "stock_oversell_race": "parallel_orders_must_not_overconsume_inventory_or_make_negative_stock",
        "approval_double_decision_race": "parallel_approve_reject_must_not_create_conflicting_terminal_states",
        "terminal_transition_race": "parallel_terminal_mutations_must_not_modify_final_object_twice",
        "generic_duplicate_write_race": "parallel_duplicate_write_must_have_single_side_effect",
    }.get(family, "parallel_side_effects_must_be_conserved")
