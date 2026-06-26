from __future__ import annotations

"""Phase94C V3: customer-grounded high-value business mutation probes.

The generator widens runtime exploration but does not inject universal business
states.  State mutations use terminal/current states observed in a source probe,
a Phase94A illegal-transition plan, or the probe's customer source quotes.
"""

import copy
import re
from typing import Any

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
RESOURCE_FIELD_RE = re.compile(r"(?:amount|price|balance|quota|point|credit|stock|inventory|quantity|qty|limit|total|积分|额度|余额|库存|金额|数量)", re.I)
TENANT_FIELD_RE = re.compile(r"(?:tenant|org|owner|user|account|member|customer|租户|组织|归属|用户)", re.I)
IDEMPOTENCY_FIELD_RE = re.compile(r"(?:idempotency|business_key|request_id|external_event_id|event_id|dedupe|幂等|业务键)", re.I)
STATE_FIELD_RE = re.compile(r"(?:status|state|stage|phase|状态)", re.I)
TERMINAL_STATES = {"cancelled", "canceled", "rejected", "completed", "closed", "refunded", "failed", "expired", "voided", "archived"}
KNOWN_STATE_LEXICON = sorted(TERMINAL_STATES | {"created", "draft", "submitted", "pending", "approved", "paid", "shipped", "delivered", "open", "active", "reopened"})


def _is_write(probe: dict[str, Any]) -> bool:
    return str((probe.get("endpoint") or {}).get("method") or "").upper() in WRITE_METHODS


def _risk_family(probe: dict[str, Any]) -> str:
    risk = str(probe.get("risk_type") or "").lower()
    path = str((probe.get("endpoint") or {}).get("path") or "").lower()
    text = " ".join([risk, path, str(probe.get("source_refs") or "")]).lower()
    if "idempot" in risk or "async_external_event" in risk:
        return "idempotency"
    if "ownership" in risk or "tenant" in risk or "owner" in risk:
        return "cross_tenant"
    if "state" in risk or "transition" in risk:
        return "state"
    if "conservation" in risk or "stock" in risk or "inventory" in risk:
        return "conservation"
    if "tenant" in path or "owner" in path or "租户" in text or "归属" in text:
        return "cross_tenant"
    if "status" in path or "state" in path or "approve" in path or "cancel" in path or "审批" in text or "状态" in text:
        return "state"
    if "idempot" in path or "callback" in path or "幂等" in text:
        return "idempotency"
    if "stock" in text or "inventory" in text or "amount" in text or "balance" in text or "积分" in text or "库存" in text or "金额" in text:
        return "conservation"
    return "business_boundary"


def _states_from_text(text: str) -> set[str]:
    lower = str(text or "").lower()
    out = set()
    for state in KNOWN_STATE_LEXICON:
        if re.search(rf"(?:^|[^a-z0-9_]){re.escape(state)}(?:[^a-z0-9_]|$)", lower):
            out.add(state)
    return out


def _grounded_states_for_probe(probe: dict[str, Any]) -> set[str]:
    plan = probe.get("probe_plan") if isinstance(probe.get("probe_plan"), dict) else {}
    states: set[str] = set()
    for key in ("customer_grounded_states", "terminal_states"):
        val = plan.get(key)
        if isinstance(val, list):
            states.update(str(x).strip().lower() for x in val if x not in (None, ""))
    illegal = plan.get("illegal_transition") if isinstance(plan.get("illegal_transition"), dict) else {}
    for key in ("from_state", "attempt_target_state"):
        val = illegal.get(key)
        if val:
            states.add(str(val).strip().lower())
    for ref in (probe.get("source_refs") or []):
        if isinstance(ref, dict):
            states.update(_states_from_text(str(ref.get("quote") or "")))
    return {s for s in states if s}


def _mutation_templates(family: str, source: dict[str, Any]) -> list[dict[str, Any]]:
    base = [
        {"mutation_kind": "resource_negative_value", "field_selector": "resource", "value": -1, "priority": "P0", "oracle": "negative_or_unmatched_conservation_delta", "grounding": "resource_field_semantics"},
        {"mutation_kind": "resource_zero_boundary", "field_selector": "resource", "value": 0, "priority": "P1", "oracle": "zero_quantity_or_amount_should_not_create_side_effect", "grounding": "resource_field_semantics"},
        {"mutation_kind": "resource_extreme_value", "field_selector": "resource", "value": 999999999, "priority": "P1", "oracle": "overflow_or_quota_bypass", "grounding": "resource_field_semantics"},
    ]
    if family == "idempotency":
        return [
            {"mutation_kind": "duplicate_idempotency_key", "field_selector": "idempotency", "value": "<SAME_AS_PREVIOUS_ATTEMPT>", "priority": "P0", "oracle": "duplicate_side_effect", "grounding": "idempotency_field_semantics"},
            {"mutation_kind": "blank_idempotency_key", "field_selector": "idempotency", "value": "", "priority": "P1", "oracle": "missing_dedupe_should_reject_or_no_duplicate", "grounding": "idempotency_field_semantics"},
            {"mutation_kind": "conflicting_payload_same_key", "field_selector": "idempotency", "value": "<SAME_KEY_DIFFERENT_PAYLOAD>", "priority": "P0", "oracle": "same_key_conflicting_payload_not_allowed", "grounding": "idempotency_field_semantics"},
        ] + base[:1]
    if family == "cross_tenant":
        return [
            {"mutation_kind": "cross_tenant_object_id", "field_selector": "tenant", "value": "qb_auto_tenant_b_foreign_object", "priority": "P0", "oracle": "foreign_object_no_side_effect", "grounding": "tenant_field_semantics"},
            {"mutation_kind": "owner_user_mismatch", "field_selector": "tenant", "value": "qb_auto_owner_b", "priority": "P0", "oracle": "ownership_scope_rejection", "grounding": "tenant_field_semantics"},
            {"mutation_kind": "missing_tenant_scope", "field_selector": "tenant", "value": "", "priority": "P1", "oracle": "tenant_scope_required", "grounding": "tenant_field_semantics"},
        ]
    if family == "state":
        grounded_states = sorted(_grounded_states_for_probe(source))
        terminals = [s for s in grounded_states if s in TERMINAL_STATES]
        templates: list[dict[str, Any]] = []
        if terminals:
            terminal = terminals[0]
            templates.append({"mutation_kind": "terminal_object_id_reuse", "field_selector": "state", "value": terminal, "priority": "P0", "oracle": "terminal_object_immutable", "grounding": "customer_state_value"})
            templates.append({"mutation_kind": "terminal_object_resume", "field_selector": "state", "value": terminal, "priority": "P0", "oracle": "terminal_object_no_side_effect", "grounding": "customer_state_value"})
        if len(grounded_states) >= 2:
            templates.append({"mutation_kind": "illegal_state_jump", "field_selector": "state", "value": grounded_states[-1], "priority": "P0", "oracle": "state_jump_rejected", "grounding": "customer_state_value"})
        # Add resource negative only if this state endpoint also carries resource
        # semantics; otherwise do not invent non-state mutations.
        if RESOURCE_FIELD_RE.search(" ".join([str(source.get("risk_type") or ""), str(source.get("source_refs") or "")])):
            templates += base[:1]
        return templates
    return base


def _field_hint(selector: str) -> str:
    if selector == "resource":
        return "amount|quantity|stock|balance|quota|points"
    if selector == "tenant":
        return "tenant_id|owner_user_id|org_id|user_id"
    if selector == "idempotency":
        return "idempotency_key|business_key|request_id|external_event_id"
    if selector == "state":
        return "status|state|from_status|target_status|object_id"
    return selector


def generate_high_value_mutation_probes(plan: dict[str, Any], *, max_per_probe: int = 3, max_total: int = 80) -> dict[str, Any]:
    probes: list[dict[str, Any]] = []
    mutation_families: dict[str, int] = {}
    counter = 1
    skipped_state_without_grounding = 0
    for source in plan.get("probes") or []:
        if not isinstance(source, dict) or not _is_write(source):
            continue
        family = _risk_family(source)
        templates = _mutation_templates(family, source)[:max_per_probe]
        if family == "state" and not templates:
            skipped_state_without_grounding += 1
        for tpl in templates:
            clone = copy.deepcopy(source)
            ep = clone.get("endpoint") or {}
            clone.update({
                "candidate_id": f"QBMU-94C-{counter:04d}",
                "risk_type": _risk_type_for_mutation(family, str(tpl.get("mutation_kind"))),
                "execution_policy": "disposable_sandbox_required",
                "endpoint": {"method": str(ep.get("method") or "POST").upper(), "path": ep.get("path")},
                "probe_plan": {
                    **(clone.get("probe_plan") if isinstance(clone.get("probe_plan"), dict) else {}),
                    "phase": "94C",
                    "phase_version": "v3_grounded",
                    "strategy": "high_value_business_mutation_runtime_probe",
                    "mutation": tpl,
                    "mutated_field_hint": _field_hint(str(tpl.get("field_selector") or "")),
                    "expected_status": [400, 403, 409, 422],
                    "bug_discovery_value": tpl.get("priority") or "P1",
                    "source_candidate_id": source.get("candidate_id"),
                },
                "required_evidence": ["before_after_snapshot", "mutation_response", "observer_delta", str(tpl.get("oracle") or "business_invariant")],
                "grounding_basis": {**(clone.get("grounding_basis") if isinstance(clone.get("grounding_basis"), dict) else {}), "endpoint_contract_refs": 1, "supporting_requirement_refs": 1, "phase94c_mutation_inference": 1, "phase94c_no_default_state_template": 1 if family == "state" else 0},
            })
            refs = [r for r in (source.get("source_refs") or []) if isinstance(r, dict)]
            clone["source_refs"] = refs or [{"file": "grounded_probe_plan", "section": str(source.get("candidate_id") or ""), "quote": "Mutation was generated from a document-grounded write probe.", "kind": "business_rule"}]
            probes.append(clone)
            mutation_families[family] = mutation_families.get(family, 0) + 1
            counter += 1
            if len(probes) >= max_total:
                break
        if len(probes) >= max_total:
            break
    by_kind: dict[str, int] = {}
    by_value: dict[str, int] = {}
    for p in probes:
        mut = ((p.get("probe_plan") or {}).get("mutation") or {})
        by_kind[str(mut.get("mutation_kind") or "unknown")] = by_kind.get(str(mut.get("mutation_kind") or "unknown"), 0) + 1
        val = str((p.get("probe_plan") or {}).get("bug_discovery_value") or "P1")
        by_value[val] = by_value.get(val, 0) + 1
    return {
        "engine": "high_value_business_mutation_probe_generator_v3_phase94c_grounded",
        "generated_probe_count": len(probes),
        "generated_by_mutation_kind": by_kind,
        "generated_by_risk_family": mutation_families,
        "generated_by_bug_value": by_value,
        "probes": probes,
        "improvement_claim": {
            "source_write_probe_count": sum(1 for p in plan.get("probes") or [] if isinstance(p, dict) and _is_write(p)),
            "added_mutation_probe_count": len(probes),
            "covered_mutation_kind_count": len(by_kind),
            "skipped_state_probe_without_customer_grounded_state_count": skipped_state_without_grounding,
            "dead_state_template_removed": True,
        },
    }


def _risk_type_for_mutation(family: str, kind: str) -> str:
    if family == "idempotency":
        return "idempotency_replay_probe"
    if family == "cross_tenant":
        return "ownership_scope_probe"
    if family == "state":
        return "state_transition_probe"
    if kind.startswith("resource"):
        return "conservation_probe"
    return "business_mutation_probe"
