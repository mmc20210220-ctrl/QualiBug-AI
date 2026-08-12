"""Fact first-loss facade with exact structural lineage authority.

The durable ledger mechanics live in ``_fact_first_loss_ledger_mechanics``.
This facade tightens only the fact-reference authority boundary:

accepted Fact receipt -> Business World Model evidence -> business behavior ->
authoritative implementation binding -> Behavior IR invariant/relation ->
obligation -> experiment.

When that authority is requested, pre-existing ``fact_refs`` are never trusted.
They are removed and rebuilt from the exact structural chain. Experiments inherit
only the fact identity already proven for their obligation. Every accepted fact
also receives a product-side diagnostic row, including the exact first structural
break that can be proven without hidden ground truth.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from . import _fact_first_loss_ledger_mechanics as _core
from ._fact_first_loss_ledger_mechanics import *  # noqa: F401,F403

_original_attach_fact_refs = _core.attach_fact_refs_to_planning_artifacts
_original_build_first_loss = _core.build_fact_first_loss_ledger


def __getattr__(name: str) -> Any:
    return getattr(_core, name)


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: Any) -> str:
    return str(value or "").strip()


def _unique(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in _list(values):
        token = _text(value)
        if token and token not in seen:
            seen.add(token)
            result.append(token)
    return result


def _accepted_fact_refs(ledger: dict[str, Any]) -> set[str]:
    return {
        _text(row.get("fact_ref"))
        for row in _list(ledger.get("items"))
        if isinstance(row, dict) and _text(row.get("fact_ref"))
    }


def _fact_structural_diagnostic(
    fact_ref: str,
    *,
    behavior_ir: dict[str, Any],
    knowledge_asset: dict[str, Any],
    authority_resolved_fact_refs: set[str],
    linked_fact_refs: set[str],
    ambiguous_ir_refs: set[str],
) -> dict[str, Any]:
    """Locate the earliest fact-specific structural break without text matching."""

    world = _dict(knowledge_asset.get("business_world_model"))
    evidence_refs = sorted({
        _text(row.get("evidence_ref"))
        for row in _list(world.get("evidence_registry"))
        if isinstance(row, dict)
        and _text(row.get("fact_id") or row.get("fact_ref")) == fact_ref
        and _text(row.get("evidence_ref"))
    })
    behavior_refs = sorted({
        _text(row.get("node_id"))
        for row in _list(world.get("behavior_nodes"))
        if isinstance(row, dict)
        and _text(row.get("node_id"))
        and set(_unique(row.get("evidence_refs"))) & set(evidence_refs)
    })
    invariant_refs = sorted({
        _text(row.get("id"))
        for row in _list(behavior_ir.get("invariants"))
        if isinstance(row, dict)
        and _text(row.get("id"))
        and (
            _text(row.get("business_behavior_ref")) in set(behavior_refs)
            # Production channel: the invariant node itself carries the exact
            # fact identity it was constructed from (rule.semantic_contract).
            or fact_ref in set(_unique(row.get("fact_refs")))
        )
    })
    relation_refs = sorted({
        _text(row.get("id"))
        for row in _list(behavior_ir.get("relations"))
        if isinstance(row, dict)
        and _text(row.get("id"))
        and {
            _text(row.get("from_ref")),
            _text(row.get("to_ref")),
        }
        & set(invariant_refs)
    })
    structural_refs = sorted({*invariant_refs, *relation_refs})
    ambiguous_refs = sorted(set(structural_refs) & ambiguous_ir_refs)

    if fact_ref in linked_fact_refs:
        status = "RESOLVED_LINKED"
        break_stage = ""
        reason_codes: list[str] = []
    elif fact_ref in authority_resolved_fact_refs:
        status = "RESOLVED_NOT_LINKED"
        break_stage = "OBLIGATION_NOT_GENERATED"
        reason_codes = ["FACT_AUTHORITY_RESOLVED_BUT_NO_OBLIGATION"]
    elif not evidence_refs:
        status = "UNRESOLVED"
        break_stage = "FACT_LINEAGE_UNRESOLVED"
        reason_codes = ["FACT_WORLD_MODEL_EVIDENCE_MISSING"]
    elif not behavior_refs:
        status = "UNRESOLVED"
        break_stage = "FACT_LINEAGE_UNRESOLVED"
        reason_codes = ["FACT_BEHAVIOR_NODE_MISSING"]
    elif not invariant_refs:
        status = "UNRESOLVED"
        break_stage = "FACT_LINEAGE_UNRESOLVED"
        reason_codes = ["FACT_BEHAVIOR_IR_INVARIANT_MISSING"]
    elif ambiguous_refs:
        status = "UNRESOLVED"
        break_stage = "FACT_LINEAGE_UNRESOLVED"
        reason_codes = ["FACT_IR_AUTHORITY_AMBIGUOUS"]
    else:
        status = "UNRESOLVED"
        break_stage = "FACT_LINEAGE_UNRESOLVED"
        reason_codes = ["FACT_TO_IR_AUTHORITY_UNRESOLVED"]

    return {
        "fact_ref": fact_ref,
        "status": status,
        "break_stage": break_stage,
        "reason_codes": reason_codes,
        "evidence_refs": evidence_refs,
        "behavior_refs": behavior_refs,
        "candidate_invariant_refs": invariant_refs,
        "candidate_relation_refs": relation_refs,
        "conflicting_or_ambiguous_refs": ambiguous_refs,
        "matching_authority": "exact_structural_identity_only",
        "heuristic_matching_used": False,
    }


def attach_fact_refs_to_planning_artifacts(
    *,
    obligations: list[dict[str, Any]],
    experiments: list[dict[str, Any]],
    fact_experimentability_ledger: dict[str, Any] | None = None,
    behavior_ir: dict[str, Any] | None = None,
    knowledge_asset: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Stamp only structurally proven fact identities in authoritative mode."""

    ledger = _dict(fact_experimentability_ledger)
    accepted_fact_refs = _accepted_fact_refs(ledger)
    authority_requested = bool(behavior_ir) or bool(knowledge_asset)
    if not authority_requested:
        return _original_attach_fact_refs(
            obligations=obligations,
            experiments=experiments,
            fact_experimentability_ledger=fact_experimentability_ledger,
            behavior_ir=behavior_ir,
            knowledge_asset=knowledge_asset,
        )

    reason_counts: Counter[str] = Counter()
    invariant_fact_refs: dict[str, tuple[str, ...]] = {}
    relation_fact_refs: dict[str, tuple[str, ...]] = {}
    ambiguous_ir_refs: set[str] = set()
    if not behavior_ir or not knowledge_asset or not accepted_fact_refs:
        reason_counts["FACT_LINEAGE_AUTHORITY_INPUT_MISSING"] += 1
    else:
        (
            invariant_fact_refs,
            relation_fact_refs,
            authority_reasons,
            ambiguous_ir_refs,
        ) = _core._canonical_fact_authority_by_ir_ref(
            behavior_ir=_dict(behavior_ir),
            knowledge_asset=_dict(knowledge_asset),
            accepted_fact_refs=accepted_fact_refs,
        )
        reason_counts.update(authority_reasons)

    # Formal fact refs are rebuilt from structural identity. A stale or
    # heuristic fact_refs field from any earlier producer is explicitly removed.
    stamped_obligations = 0
    linked_fact_refs: set[str] = set()
    obligation_fact_refs: dict[str, list[str]] = {}
    obligation_diagnostics: list[dict[str, Any]] = []
    unresolved_obligation_count = 0

    for obligation in obligations:
        if not isinstance(obligation, dict):
            continue
        obligation.pop("fact_refs", None)
        oid = _text(obligation.get("obligation_id"))
        prop = _dict(obligation.get("property"))
        structural_refs = {
            _text(prop.get("invariant_ref")),
            *(_unique(obligation.get("relation_refs"))),
            *(_unique(obligation.get("subject_refs"))),
        }
        structural_refs.discard("")
        reason_codes: list[str] = []
        refs: list[str] = []

        ambiguous = sorted(structural_refs & ambiguous_ir_refs)
        if ambiguous:
            reason_codes.append("OBLIGATION_FACT_AUTHORITY_AMBIGUOUS")
        else:
            structural_sets = [
                values
                for ref in sorted(structural_refs)
                for values in (
                    invariant_fact_refs.get(ref),
                    relation_fact_refs.get(ref),
                )
                if values
            ]
            distinct_sets = {values for values in structural_sets}
            if len(distinct_sets) == 1:
                refs = list(next(iter(distinct_sets)))
            elif len(distinct_sets) > 1:
                reason_codes.append("OBLIGATION_FACT_AUTHORITY_AMBIGUOUS")
            else:
                reason_codes.append("OBLIGATION_FACT_AUTHORITY_UNRESOLVED")

        if refs:
            refs = [ref for ref in refs if ref in accepted_fact_refs]
            if refs:
                obligation["fact_refs"] = refs
                obligation_fact_refs[oid] = refs
                linked_fact_refs.update(refs)
                stamped_obligations += 1
        if reason_codes:
            unresolved_obligation_count += 1
            reason_counts.update(reason_codes)
        obligation_diagnostics.append({
            "obligation_id": oid,
            "status": "RESOLVED" if refs else "UNRESOLVED",
            "fact_refs": refs,
            "structural_refs": sorted(structural_refs),
            "conflicting_or_ambiguous_refs": ambiguous,
            "reason_codes": reason_codes,
        })

    # Experiments may never widen lineage. They inherit only the exact identity
    # proven for their owning (or expanded-from) obligation.
    stamped_experiments = 0
    for experiment in experiments:
        if not isinstance(experiment, dict):
            continue
        experiment.pop("fact_refs", None)
        oid = _text(experiment.get("obligation_id"))
        parent_oid = _text(experiment.get("expanded_from_obligation_id")) or oid
        refs = obligation_fact_refs.get(oid) or obligation_fact_refs.get(parent_oid) or []
        if refs:
            experiment["fact_refs"] = list(refs)
            stamped_experiments += 1

    authority_resolved_fact_refs = {
        fact_ref
        for values in [*invariant_fact_refs.values(), *relation_fact_refs.values()]
        for fact_ref in values
        if fact_ref in accepted_fact_refs
    }
    diagnostics = [
        _fact_structural_diagnostic(
            fact_ref,
            behavior_ir=_dict(behavior_ir),
            knowledge_asset=_dict(knowledge_asset),
            authority_resolved_fact_refs=authority_resolved_fact_refs,
            linked_fact_refs=linked_fact_refs,
            ambiguous_ir_refs=ambiguous_ir_refs,
        )
        for fact_ref in sorted(accepted_fact_refs)
    ]
    unresolved_fact_refs = [
        row["fact_ref"] for row in diagnostics if row["status"] == "UNRESOLVED"
    ]
    unlinked_resolved_fact_refs = [
        row["fact_ref"]
        for row in diagnostics
        if row["status"] == "RESOLVED_NOT_LINKED"
    ]
    authority_status = (
        "PASS"
        if not unresolved_fact_refs and not unresolved_obligation_count
        else "BLOCKED_WITH_GAPS"
    )
    return {
        "schema_version": "qualibug.fact-ref-planning-attach.v2",
        "authority_status": authority_status,
        "authority": "canonical_business_world_model_to_behavior_ir_exact_identity",
        # The canonical chain now includes the production channel: invariants
        # constructed from fact-promoted rules carry the exact fact identity on
        # the IR node, so lineage resolves by construction, not by join.
        "production_fact_authority_channel_consumed": True,
        "heuristic_matching_enabled": False,
        "preexisting_fact_refs_trusted": False,
        "experiment_lineage_may_widen_obligation_lineage": False,
        "accepted_fact_count": len(accepted_fact_refs),
        "stamped_obligation_count": stamped_obligations,
        "stamped_experiment_count": stamped_experiments,
        "unresolved_obligation_count": unresolved_obligation_count,
        "authority_resolved_fact_refs": sorted(authority_resolved_fact_refs),
        "linked_fact_refs": sorted(linked_fact_refs),
        "unresolved_fact_refs": unresolved_fact_refs,
        "unlinked_resolved_fact_refs": unlinked_resolved_fact_refs,
        "fact_lineage_diagnostic_count": len(diagnostics),
        "fact_lineage_diagnostics": diagnostics,
        "obligation_lineage_diagnostics": obligation_diagnostics,
        "ambiguous_ir_refs": sorted(ambiguous_ir_refs),
        "reason_counts": dict(sorted(reason_counts.items())),
        "fact_experimentability_ledger_fingerprint": _text(
            ledger.get("ledger_fingerprint")
        ),
        "changes_compile_or_execution_decisions": False,
    }


def build_fact_first_loss_ledger(*args: Any, **kwargs: Any) -> dict[str, Any]:
    """Build first-loss rows and attach fact-specific structural diagnostics."""

    ledger = _original_build_first_loss(*args, **kwargs)
    lineage_receipt = _dict(kwargs.get("fact_lineage_receipt"))
    diagnostics = [
        row
        for row in _list(lineage_receipt.get("fact_lineage_diagnostics"))
        if isinstance(row, dict) and _text(row.get("fact_ref"))
    ]
    if not diagnostics:
        return ledger

    diagnostics_by_fact = {
        _text(row.get("fact_ref")): row for row in diagnostics
    }
    rows = [row for row in _list(ledger.get("items")) if isinstance(row, dict)]
    missing_diagnostics: list[str] = []
    for row in rows:
        fact_ref = _text(row.get("fact_ref"))
        diagnostic = _dict(diagnostics_by_fact.get(fact_ref))
        if not diagnostic:
            missing_diagnostics.append(fact_ref)
            continue
        row["lineage_authority_status"] = _text(diagnostic.get("status"))
        row["lineage_break_stage"] = _text(diagnostic.get("break_stage"))
        row["lineage_reason_codes"] = _unique(diagnostic.get("reason_codes"))
        row["lineage_evidence_refs"] = _unique(diagnostic.get("evidence_refs"))
        row["lineage_behavior_refs"] = _unique(diagnostic.get("behavior_refs"))
        row["lineage_candidate_invariant_refs"] = _unique(
            diagnostic.get("candidate_invariant_refs")
        )
        row["lineage_candidate_relation_refs"] = _unique(
            diagnostic.get("candidate_relation_refs")
        )
        row["lineage_conflicting_or_ambiguous_refs"] = _unique(
            diagnostic.get("conflicting_or_ambiguous_refs")
        )
        if (
            _text(diagnostic.get("status")) == "UNRESOLVED"
            and _text(row.get("first_loss_stage")) == "OBLIGATION_NOT_GENERATED"
        ):
            row["first_loss_stage"] = "FACT_LINEAGE_UNRESOLVED"
            row["first_loss_reason"] = (
                row["lineage_reason_codes"][0]
                if row["lineage_reason_codes"]
                else "FACT_TO_IR_AUTHORITY_UNRESOLVED"
            )
            row["blocker_owner"] = "diagnostic_lineage"

    stage_counts = dict(
        Counter(_text(row.get("first_loss_stage")) for row in rows)
    )
    conservation = dict(_dict(ledger.get("conservation")))
    issues = list(_list(conservation.get("issues")))
    expected_fact_refs = {
        _text(row.get("fact_ref")) for row in rows if _text(row.get("fact_ref"))
    }
    diagnostic_fact_refs = set(diagnostics_by_fact)
    if diagnostic_fact_refs != expected_fact_refs:
        issues.append(
            "fact_lineage_diagnostic_identity_not_conserved:"
            f"ledger={len(expected_fact_refs)};diagnostics={len(diagnostic_fact_refs)}"
        )
    if missing_diagnostics:
        issues.append(
            "fact_lineage_diagnostic_missing:"
            + ",".join(sorted(set(missing_diagnostics))[:32])
        )
    conservation["fact_lineage_diagnostic_coverage"] = (
        diagnostic_fact_refs == expected_fact_refs and not missing_diagnostics
    )
    conservation["issues"] = list(dict.fromkeys(issues))
    conservation["status"] = "PASS" if not conservation["issues"] else "FAILED"

    ledger["items"] = rows
    ledger["stage_counts"] = stage_counts
    ledger["fact_lineage_diagnostic_count"] = len(diagnostics)
    ledger["fact_lineage_diagnostics"] = diagnostics
    ledger["conservation"] = conservation
    ledger["ledger_fingerprint"] = _core._fingerprint(rows)
    return ledger


_core.attach_fact_refs_to_planning_artifacts = attach_fact_refs_to_planning_artifacts
_core.build_fact_first_loss_ledger = build_fact_first_loss_ledger

__all__ = sorted(
    name for name in globals() if not name.startswith("__") and name != "_core"
)
