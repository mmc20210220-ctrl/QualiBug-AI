"""Generic, non-fabricating breadth-loss ledger for risk families.

Standing instruction (原则14): 广度损失必须可见.  Every risk family that
*cannot* fire for a given target must be reported with an honest gap reason
rather than silently dropping to zero obligations.  This module is the single
upstream place that enumerates ALL known risk families for a target and records
applicable vs NOT_REQUESTED-with-reason.

It is pure and honest:

* It reads the actual ``compile_obligations_from_behavior_ir`` result (the
  compiled obligations + any binding receipts).  It never invents families,
  obligations, or reasons.
* The family set is the authoritative ``canonical_risk_families()`` (the 10
  literal families + any runtime-registered families such as
  ``compatibility`` / ``event_delivery_consistency`` / ``ui_state_consistency``
  installed by the formal-surface bindings).  This is the same set the
  obligation taxonomy uses, so the ledger can never silently drift from it.
* For a non-applicable family the gap reason is the documented precondition
  that family requires to fire.  These preconditions are product-internal
  coverage metadata (deployment/contract identity), NOT customer/business
  data — they describe what each product family needs to become applicable,
  and they carry no industry or business-specific terms (原则6).

Integration: ``discovery_runtime_planning.build_discovery_plan`` builds this
ledger from the fully-enriched ``obligations`` list (the authoritative set at
that point) and attaches it to the returned ``DiscoveryPlanningBundle`` dict as
``family_coverage_ledger`` (after ``obligations=``), so every discovery plan
carries it through ``**obligation_pack`` — no fork, no duplicate mechanism.
"""

from __future__ import annotations

from typing import Any

from .test_obligation import canonical_risk_families


# Product-internal coverage metadata: what each known risk family requires to
# become applicable for a target.  This is NOT business/industry logic — it is
# the documented precondition of each product family's obligation binding, used
# only to annotate an honest breadth-loss gap.  No customer terms, entity
# names, or benchmark answers appear here (原则6).
_FAMILY_PRECONDITIONS: dict[str, dict[str, str]] = {
    "compatibility": {
        "gap_reason_code": "NO_COMPARISON_SURFACES",
        "gap_reason": "requires >=2 comparison surfaces (operator-declared or OpenAPI servers[])",
    },
    "event_delivery_consistency": {
        "gap_reason_code": "NO_EVENT_DELIVERY_CONTRACT",
        "gap_reason": "requires event-delivery contract (actor identity + event-delivery surface) in source material",
    },
    "ui_state_consistency": {
        "gap_reason_code": "NO_SOURCE_UI_EXPECTATION",
        "gap_reason": "requires a source-declared UI plan with expect_text/expect_url steps + playwright adapter",
    },
    "interface_contract": {
        "gap_reason_code": "NO_INTERFACE_PRESENCE",
        "gap_reason": "requires a declared interface/operation whose presence can be audited",
    },
    "performance_latency": {
        "gap_reason_code": "NO_PARAMETER_SCALE_CONTRACT",
        "gap_reason": "requires a parameter-scale or performance contract derived from the API spec",
    },
    "stability_reliability": {
        "gap_reason_code": "NO_STABILITY_PROBE_SURFACE",
        "gap_reason": "requires a runtime stability probe surface (repeatable operation)",
    },
    "authorization": {
        "gap_reason_code": "NO_AUTHORIZATION_CONTRACT",
        "gap_reason": "requires declared permit/deny relations or anonymous-reachable surfaces",
    },
    "isolation": {
        "gap_reason_code": "NO_ISOLATION_CONTRACT",
        "gap_reason": "requires a multi-tenant / actor-isolation contract in the source material",
    },
    "state": {
        "gap_reason_code": "NO_STATE_MACHINE",
        "gap_reason": "requires an extracted state machine / forbidden-state-transition contract",
    },
    "conservation": {
        "gap_reason_code": "NO_CONSERVATION_INVARIANT",
        "gap_reason": "requires a business conservation invariant (e.g. amount conservation) in the source",
    },
    "idempotency": {
        "gap_reason_code": "NO_IDEMPOTENT_OPERATION",
        "gap_reason": "requires an idempotency-relevant operation (create/submit/pay) in the source",
    },
    "concurrency": {
        "gap_reason_code": "NO_CONCURRENT_MUTATION",
        "gap_reason": "requires a concurrency-relevant operation (concurrent mutate) in the source",
    },
    "validation": {
        "gap_reason_code": "NO_VALIDATION_CONTRACT",
        "gap_reason": "requires a field/enum/constraint validation contract in the source",
    },
    "visibility": {
        "gap_reason_code": "NO_VISIBILITY_CONTRACT",
        "gap_reason": "requires a visibility / role-based-field contract in the source",
    },
    "temporal": {
        "gap_reason_code": "NO_TEMPORAL_CONTRACT",
        "gap_reason": "requires a temporal/ordering contract (e.g. date_order) in the source",
    },
    "privacy": {
        "gap_reason_code": "NO_PRIVACY_CONTRACT",
        "gap_reason": "requires a sensitive-field / privacy contract in the source",
    },
    "message_chain": {
        "gap_reason_code": "NO_MESSAGE_CHAIN_CONTRACT",
        "gap_reason": "requires a state machine with actor identity + event-delivery surface",
    },
}

_FALLBACK_GAP = {
    "gap_reason_code": "PRECONDITION_NOT_MET",
    "gap_reason": "precondition for this family was not met by the target's source material",
}


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _find_receipt_status(compile_result: dict[str, Any], family: str) -> str:
    """Best-effort: surface a binding receipt status if one clearly belongs to
    this family. Heuristic only — never used to fabricate applicability."""
    result = _dict(compile_result)
    family_token = family.lower()
    for key, val in result.items():
        k = _text(key).lower()
        if not k.endswith("_obligation_receipt") and not k.endswith("_receipt"):
            continue
        if family_token in k:
            return _text(_dict(val).get("status"))
        inner = _dict(val)
        if _text(inner.get("risk_family") or inner.get("family")).lower() == family_token:
            return _text(inner.get("status"))
    return ""


def build_family_coverage_ledger(compile_result: dict[str, Any] | None) -> dict[str, Any]:
    """Enumerate every known risk family for a target and record applicable vs
    honest-GAP-with-reason.

    Parameters
    ----------
    compile_result:
        The dict returned by ``compile_obligations_from_behavior_ir`` (or any
        dict carrying an ``obligations`` list).  Safe against missing keys.

    Returns
    -------
    dict with schema ``qualibug.family-coverage-ledger.v1``:
        ``entries`` lists one row per known family with
        ``applicable`` / ``obligation_count`` / ``status`` /
        ``gap_reason_code`` / ``gap_reason``.
    """
    result = _dict(compile_result)
    obligations = _list(result.get("obligations"))

    known_families = list(canonical_risk_families())
    # Defensive: include any family present in obligations but absent from the
    # canonical set, so the ledger never under-reports applicability.
    seen = set(known_families)
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        fam = _text(obl.get("risk_family"))
        if fam and fam not in seen:
            known_families.append(fam)
            seen.add(fam)

    counts: dict[str, int] = {}
    for obl in obligations:
        if not isinstance(obl, dict):
            continue
        fam = _text(obl.get("risk_family"))
        if fam:
            counts[fam] = counts.get(fam, 0) + 1

    entries: list[dict[str, Any]] = []
    for family in known_families:
        obligation_count = int(counts.get(family, 0))
        applicable = obligation_count > 0
        if applicable:
            entries.append({
                "risk_family": family,
                "applicable": True,
                "obligation_count": obligation_count,
                "status": "APPLIED",
                "gap_reason_code": "",
                "gap_reason": "",
                "receipt_status": _find_receipt_status(result, family),
            })
        else:
            pre = _FAMILY_PRECONDITIONS.get(family, _FALLBACK_GAP)
            entries.append({
                "risk_family": family,
                "applicable": False,
                "obligation_count": 0,
                "status": "NOT_REQUESTED",
                "gap_reason_code": pre["gap_reason_code"],
                "gap_reason": pre["gap_reason"],
                "receipt_status": _find_receipt_status(result, family),
            })

    applicable_count = sum(1 for e in entries if e["applicable"])
    not_applicable_count = len(entries) - applicable_count
    return {
        "schema_version": "qualibug.family-coverage-ledger.v1",
        "families_total": len(entries),
        "families_applicable": applicable_count,
        "families_not_applicable": not_applicable_count,
        "entries": entries,
        "summary": (
            f"{applicable_count}/{len(entries)} risk families applicable to this target; "
            f"{not_applicable_count} honestly not applicable (breadth loss is visible, not dropped)."
        ),
    }
