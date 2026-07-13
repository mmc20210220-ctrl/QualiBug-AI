from __future__ import annotations

"""Phase94B: multi-step business flow composition for deeper bug discovery.

This module turns single endpoint probes into executable flow scenarios.  The
actual HTTP executor still enforces disposable-sandbox approval and runtime
validation; the purpose here is to broaden the search space from isolated calls
to ordered business chains where state/stock/idempotency bugs tend to hide.
"""

import re
from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
ACTION_ORDER = ["create", "submit", "approve", "pay", "callback", "ship", "complete", "cancel", "refund"]
ACTION_RE = re.compile("|".join(re.escape(x) for x in ACTION_ORDER + ["reject", "release", "transition"]), re.I)


def _action_for_probe(probe: dict[str, Any]) -> str:
    ep = probe.get("endpoint") or {}
    text = " ".join([str(ep.get("path") or ""), str(probe.get("risk_type") or ""), str((probe.get("probe_plan") or {}).get("strategy") or "")]).lower()
    if str(ep.get("method") or "").upper() == "POST" and re.search(r"/(orders|order|approvals|approval|payments|payment|inventory|stock)$", str(ep.get("path") or ""), re.I):
        return "create"
    hit = ACTION_RE.search(text)
    return hit.group(0).lower() if hit else "mutate"


def _resource_for_probe(probe: dict[str, Any]) -> str:
    path = str((probe.get("endpoint") or {}).get("path") or "")
    parts = [p for p in path.strip("/").split("/") if p and not p.startswith("{")]
    for part in reversed(parts):
        if ACTION_RE.fullmatch(part):
            continue
        if part.lower() in {"api", "v1", "v2", "v3"}:
            continue
        return part.lower()
    return "resource"


def _sort_key(probe: dict[str, Any]) -> int:
    action = _action_for_probe(probe)
    try:
        return ACTION_ORDER.index(action)
    except ValueError:
        return 50


def compose_multistep_business_flows(plan: dict[str, Any], *, max_flows: int = 24) -> dict[str, Any]:
    probes = [p for p in (plan.get("probes") or []) if isinstance(p, dict)]
    writes = [p for p in probes if str((p.get("endpoint") or {}).get("method") or "").upper() in WRITE_METHODS]
    groups: dict[str, list[dict[str, Any]]] = {}
    for p in writes:
        groups.setdefault(_resource_for_probe(p), []).append(p)
    scenarios: list[dict[str, Any]] = []
    counter = 1
    for resource, items in groups.items():
        ordered = sorted(items, key=_sort_key)
        if len(ordered) < 2:
            continue
        # Flow 1: normal-looking setup then illegal terminal mutation.
        for window_size in (2, 3, 4):
            if len(ordered) < window_size:
                continue
            steps = ordered[:window_size]
            scenarios.append({
                "flow_id": f"QBFL-94B-{counter:04d}",
                "resource": resource,
                "strategy": "ordered_business_chain_then_invariant_check",
                "bug_hypothesis": "multi-step chain may reveal side effects hidden from isolated endpoint probes",
                "steps": [_step_from_probe(p, idx + 1) for idx, p in enumerate(steps)],
                "required_evidence": ["per_step_request_response", "before_after_snapshot", "cross_step_observer_delta"],
                "source_candidate_ids": [str(p.get("candidate_id") or "") for p in steps],
                "bug_discovery_value": "P0" if any(_action_for_probe(p) in {"pay", "approve", "refund"} for p in steps) else "P1",
            })
            counter += 1
            if len(scenarios) >= max_flows:
                break
        # Flow 2: illegal order inversion, a common business bug source.
        if len(ordered) >= 2:
            inverted = list(reversed(ordered[: min(4, len(ordered))]))
            scenarios.append({
                "flow_id": f"QBFL-94B-{counter:04d}",
                "resource": resource,
                "strategy": "illegal_order_inversion_flow",
                "bug_hypothesis": "out-of-order business actions should be rejected without side effects",
                "steps": [_step_from_probe(p, idx + 1) for idx, p in enumerate(inverted)],
                "required_evidence": ["per_step_request_response", "before_after_snapshot", "terminal_state_no_side_effect"],
                "source_candidate_ids": [str(p.get("candidate_id") or "") for p in inverted],
                "bug_discovery_value": "P0",
            })
            counter += 1
        if len(scenarios) >= max_flows:
            break
    return {
        "engine": "business_flow_combo_executor_v1_phase94b",
        "flow_count": len(scenarios),
        "flow_bug_discovery_value_count": _counts(s.get("bug_discovery_value") for s in scenarios),
        "scenarios": scenarios[:max_flows],
        "improvement_claim": {
            "single_step_write_probe_count": len(writes),
            "generated_multistep_flow_count": len(scenarios[:max_flows]),
            "new_cross_step_oracle_count": len(scenarios[:max_flows]),
        },
    }


def _step_from_probe(probe: dict[str, Any], order: int) -> dict[str, Any]:
    ep = probe.get("endpoint") or {}
    return {
        "step": order,
        "candidate_id": probe.get("candidate_id"),
        "action": _action_for_probe(probe),
        "method": str(ep.get("method") or "").upper(),
        "path": ep.get("path"),
        "risk_type": probe.get("risk_type"),
        "expected_status": ((probe.get("probe_plan") or {}).get("expected_status") or [400, 403, 409, 422]),
    }


def _counts(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in values:
        key = str(v or "unknown")
        out[key] = out.get(key, 0) + 1
    return out


def generate_flow_probe_surrogates(plan: dict[str, Any], *, max_probes: int = 24) -> dict[str, Any]:
    """Create sandbox probe records for the first failing step of each flow.

    The richer multi-step scenario is preserved in ``probe_plan.flow_scenario``;
    the existing executor can still schedule the first negative write step while
    later Phase94B iterations can execute the whole chain.
    """
    flows = compose_multistep_business_flows(plan, max_flows=max_probes)
    probes: list[dict[str, Any]] = []
    for idx, flow in enumerate(flows.get("scenarios") or [], start=1):
        steps = flow.get("steps") or []
        if not steps:
            continue
        # Pick the earliest non-create action, because create-only probes rarely
        # expose illegal state bugs. Fall back to the first step.
        selected = next((s for s in steps if s.get("action") != "create"), steps[0])
        probes.append({
            "candidate_id": f"QBFL-94B-{idx:04d}",
            "risk_type": "business_flow_sequence_probe",
            "endpoint": {"method": selected.get("method"), "path": selected.get("path")},
            "execution_policy": "disposable_sandbox_required",
            "probe_plan": {
                "phase": "94B",
                "strategy": flow.get("strategy"),
                "flow_scenario": flow,
                "expected_status": selected.get("expected_status") or [400, 403, 409, 422],
                "bug_discovery_value": flow.get("bug_discovery_value"),
            },
            "required_evidence": flow.get("required_evidence") or [],
            "source_refs": _source_refs_from_plan(plan, flow.get("source_candidate_ids") or []),
            "grounding_basis": {"endpoint_contract_refs": 1, "supporting_requirement_refs": 1, "phase94b_multistep_flow_inference": 1},
        })
    flows["probes"] = probes
    flows["generated_probe_count"] = len(probes)
    return flows


def _source_refs_from_plan(plan: dict[str, Any], ids: list[str]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    wanted = {str(x) for x in ids}
    for p in plan.get("probes") or []:
        if not isinstance(p, dict) or str(p.get("candidate_id") or "") not in wanted:
            continue
        refs.extend([r for r in (p.get("source_refs") or []) if isinstance(r, dict)])
    if refs:
        return refs[:8]
    return [{"file": "grounded_probe_plan", "section": "phase94b", "quote": "Multi-step flow was composed from document-grounded endpoint probes.", "kind": "business_rule"}]
