from __future__ import annotations

"""Phase94A-D + Phase95A unified bug-discovery probe expander.

The expander deliberately focuses on core bug discovery: it adds state-machine,
multi-step, mutation, race and authorization-boundary probes to a customer-input grounded plan.  All
added probes keep strict source refs and still require runtime evidence before
any finding is validated.
"""

from pathlib import Path
from typing import Any

from .openapi_spec_utils import load_openapi_from_input
from .business_state_machine_explorer import generate_illegal_state_path_probes
from .business_flow_combo_executor import generate_flow_probe_surrogates, compose_multistep_business_flows
from .high_value_business_mutation_probe_generator import generate_high_value_mutation_probes
from .concurrency_race_probe_planner import generate_concurrency_race_probes
from .auth_boundary_probe_generator import generate_auth_boundary_probes


def _endpoint_key(probe: dict[str, Any]) -> tuple[str, str, str, str]:
    ep = probe.get("endpoint") or {}
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    mut = plan.get("mutation") if isinstance(plan.get("mutation"), dict) else {}
    illegal = plan.get("illegal_transition") if isinstance(plan.get("illegal_transition"), dict) else {}
    race = plan.get("concurrency") if isinstance(plan.get("concurrency"), dict) else {}
    flow = plan.get("flow_scenario") if isinstance(plan.get("flow_scenario"), dict) else {}
    discriminator = "|".join([
        str(mut.get("mutation_kind") or ""),
        str(illegal.get("from_state") or "") + ">" + str(illegal.get("attempt_action") or ""),
        str(race.get("parallel_attempts") or "") + ":" + str(plan.get("race_family") or ""),
        str(flow.get("strategy") or ""),
    ])
    return (str(probe.get("risk_type") or ""), str(ep.get("method") or "").upper(), str(ep.get("path") or ""), discriminator)


def expand_bug_discovery_probes(
    plan: dict[str, Any],
    *,
    input_dir: str | Path | None = None,
    config: dict[str, Any] | None = None,
    max_added_per_phase: int = 40,
) -> dict[str, Any]:
    cfg = config or {}
    spec = load_openapi_from_input(input_dir or cfg.get("input_dir") or cfg.get("project_input_dir"))
    # Safety/default: expansion is an explicit core bug-discovery mode in the
    # executor, so legacy runtime validation keeps stable probe counts unless
    # the caller opts into Phase94 expansion.  Direct module use remains fully
    # available for planning/evaluation.
    enabled = bool(cfg.get("enable_phase94_bug_discovery_expansion"))
    if cfg.get("disable_phase94_bug_discovery_expansion") is True:
        enabled = False
    if not enabled:
        return {
            "engine": "bug_discovery_probe_expander_v4_phase94abcd_95a_grounded",
            "enabled": False,
            "reason": "no_customer_input_openapi_or_phase94_enable_flag",
            "original_probe_count": len(plan.get("probes") or []),
            "expanded_probe_count": len(plan.get("probes") or []),
            "added_probe_count": 0,
            "phase_summaries": {},
            "probes": [],
        }

    original = [p for p in (plan.get("probes") or []) if isinstance(p, dict)]
    existing = {_endpoint_key(p) for p in original}
    phase_outputs: dict[str, dict[str, Any]] = {}
    added: list[dict[str, Any]] = []

    phase_outputs["phase94a"] = generate_illegal_state_path_probes(plan, spec, max_probes=max_added_per_phase)
    _append_new(added, phase_outputs["phase94a"].get("probes") or [], existing)

    # Feed A-expanded probes into B/C/D so each phase can compound the actual
    # search space, not just the initial seed plan.
    plan_a = {**plan, "probes": original + added}
    phase_outputs["phase94b"] = generate_flow_probe_surrogates(plan_a, max_probes=max_added_per_phase)
    _append_new(added, phase_outputs["phase94b"].get("probes") or [], existing)

    plan_ab = {**plan, "probes": original + added}
    phase_outputs["phase94c"] = generate_high_value_mutation_probes(plan_ab, max_total=max_added_per_phase)
    _append_new(added, phase_outputs["phase94c"].get("probes") or [], existing)

    plan_abc = {**plan, "probes": original + added}
    phase_outputs["phase94d"] = generate_concurrency_race_probes(plan_abc, max_probes=max_added_per_phase)
    _append_new(added, phase_outputs["phase94d"].get("probes") or [], existing)

    plan_abcd = {**plan, "probes": original + added}
    phase_outputs["phase95a"] = generate_auth_boundary_probes(plan_abcd, max_total=max_added_per_phase)
    _append_new(added, phase_outputs["phase95a"].get("probes") or [], existing)

    by_phase: dict[str, int] = {}
    by_risk_type: dict[str, int] = {}
    p0_added = 0
    for probe in added:
        phase = str(((probe.get("probe_plan") or {}).get("phase") or "unknown")).lower()
        by_phase[phase] = by_phase.get(phase, 0) + 1
        risk = str(probe.get("risk_type") or "unknown")
        by_risk_type[risk] = by_risk_type.get(risk, 0) + 1
        if str((probe.get("probe_plan") or {}).get("bug_discovery_value") or "").upper() == "P0":
            p0_added += 1
    flows = compose_multistep_business_flows({**plan, "probes": original + added}, max_flows=max_added_per_phase)
    return {
        "engine": "bug_discovery_probe_expander_v4_phase94abcd_95a_grounded",
        "enabled": True,
        "input_openapi_used": bool(spec),
        "original_probe_count": len(original),
        "expanded_probe_count": len(original) + len(added),
        "added_probe_count": len(added),
        "added_by_phase": by_phase,
        "added_by_risk_type": by_risk_type,
        "added_p0_probe_count": p0_added,
        "phase_summaries": {k: _summary(v) for k, v in phase_outputs.items()},
        "multistep_flow_scenario_count": flows.get("flow_count", 0),
        "bug_discovery_improvement_evidence": {
            "risk_family_count_after_expansion": len(by_risk_type),
            "negative_auth_boundary_probe_count": by_phase.get("95a", 0),
            "new_high_value_probe_count": len(added),
            "new_p0_probe_count": p0_added,
            "new_multistep_flow_count": flows.get("flow_count", 0),
            "state_machine_count": ((phase_outputs.get("phase94a") or {}).get("state_machine_discovery") or {}).get("state_machine_count", 0),
        },
        "probes": added,
        "flow_scenarios": flows.get("scenarios") or [],
    }


def _append_new(out: list[dict[str, Any]], probes: list[dict[str, Any]], existing: set[tuple[str, str, str, str]]) -> None:
    for probe in probes:
        if not isinstance(probe, dict):
            continue
        key = _endpoint_key(probe)
        if key in existing:
            continue
        existing.add(key)
        out.append(probe)


def _summary(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "engine": payload.get("engine"),
        "generated_probe_count": payload.get("generated_probe_count", payload.get("flow_count", 0)),
        "generated_by_bug_value": payload.get("generated_by_bug_value") or payload.get("flow_bug_discovery_value_count") or {},
        "generated_by_risk_family": payload.get("generated_by_risk_family") or payload.get("generated_by_race_family") or {},
        "generated_by_mutation_kind": payload.get("generated_by_mutation_kind") or {},
        "improvement_claim": payload.get("improvement_claim") or {},
    }
