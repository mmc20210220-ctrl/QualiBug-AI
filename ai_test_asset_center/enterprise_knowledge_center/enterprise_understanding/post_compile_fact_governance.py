"""Second-pass governance for the single compiled business-fact ledger."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
from .atomic_claim_projection import project_atomic_claim_facts
from .explicit_fact_semantic_normalization import (
    normalize_explicit_business_fact_semantics,
)
from .identity_evidence_policy import apply_identity_evidence_policy
from .typed_fact_authority import retire_duplicate_compatibility_typed_facts
from .typed_fact_conflicts import reconcile_typed_fact_conflicts
from .typed_relation_projection import project_typed_object_relations


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_typed_fact_values(asset: dict[str, Any]) -> dict[str, Any]:
    """Close one unambiguous typed-value contract on the existing fact.

    ``business-fact-ledger.v2`` may carry cardinality in the established top-level
    ``value`` field, in one atomic claim, or both. This boundary canonicalizes one
    unique source-backed value into both the compatibility field and the formal
    ``quantity_constraints`` slot. It never selects among competing values.
    """
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    projected = 0
    formal_slot_projected = 0
    ambiguous_ids: list[str] = []
    for fact in facts:
        fact_type = _text(fact.get("fact_type")).upper()
        if fact_type != "CARDINALITY_CONSTRAINT":
            continue

        values: list[dict[str, Any]] = []
        top_level = _dict(fact.get("value"))
        if top_level:
            values.append(dict(top_level))
        values.extend(
            dict(_dict(claim.get("value")))
            for claim in _list(fact.get("claims"))
            if isinstance(claim, dict)
            and _text(claim.get("claim_type")).upper() == fact_type
            and _dict(claim.get("value"))
        )
        values.extend(
            dict(row)
            for row in _list(fact.get("quantity_constraints"))
            if isinstance(row, dict) and row
        )
        unique = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str): value
            for value in values
        }
        if len(unique) == 1:
            value = dict(next(iter(unique.values())))
            if _dict(fact.get("value")) != value:
                fact["value"] = value
                projected += 1
            constraints = [
                dict(row)
                for row in _list(fact.get("quantity_constraints"))
                if isinstance(row, dict) and row
            ]
            identities = {
                json.dumps(row, ensure_ascii=False, sort_keys=True, default=str)
                for row in constraints
            }
            identity = json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
            if identity not in identities:
                constraints.append(dict(value))
                formal_slot_projected += 1
            fact["quantity_constraints"] = constraints
            fact["typed_value_projection"] = {
                "status": "PASS",
                "source": "single_source_backed_typed_value",
                "compatibility_value_projected": True,
                "formal_quantity_constraint_projected": True,
                "automatic_winner_used": False,
            }
        elif len(unique) > 1:
            fact_id = _text(fact.get("fact_id"))
            fact["status"] = "PENDING"
            fact["formal_promotion_allowed"] = False
            ambiguities = [
                _text(value) for value in _list(fact.get("ambiguities")) if _text(value)
            ]
            if "TYPED_VALUE_MULTIPLE_CLAIMS" not in ambiguities:
                ambiguities.append("TYPED_VALUE_MULTIPLE_CLAIMS")
            fact["ambiguities"] = ambiguities
            fact["typed_value_projection"] = {
                "status": "AMBIGUOUS",
                "candidate_count": len(unique),
                "automatic_winner_used": False,
            }
            if fact_id:
                ambiguous_ids.append(fact_id)
    ledger["items"] = facts
    asset["business_fact_ledger"] = ledger
    asset["typed_fact_value_projection_receipt"] = {
        "schema": "qualibug.typed-fact-value-projection.v1",
        "status": "BLOCKED" if ambiguous_ids else "PASS",
        "compatibility_value_projected_fact_count": projected,
        "formal_quantity_constraint_projected_fact_count": formal_slot_projected,
        "ambiguous_fact_ids": ambiguous_ids,
        "automatic_winner_used": False,
        "parallel_value_authority_created": False,
    }
    if ambiguous_ids:
        gate = _dict(asset.get("enterprise_comprehension_gate"))
        gate["status"] = "BLOCKED_TYPED_FACT_VALUE_AMBIGUOUS"
        gate["entry_allowed"] = False
        gate["required_operator_action"] = (
            "resolve multiple source-backed typed values before rule projection"
        )
        asset["enterprise_comprehension_gate"] = gate
        gaps = [
            dict(row)
            for row in _list(asset.get("coverage_gaps"))
            if isinstance(row, dict)
            and _text(row.get("kind")) != "BLOCKED_TYPED_FACT_VALUE_AMBIGUOUS"
        ]
        gaps.append(
            {
                "kind": "BLOCKED_TYPED_FACT_VALUE_AMBIGUOUS",
                "gap_type": "typed_fact_value_multiple_claims",
                "source_id": "*",
                "fact_ids": ambiguous_ids,
                "operator_action": gate["required_operator_action"],
            }
        )
        asset["coverage_gaps"] = gaps
    return asset


def govern_compiled_business_facts(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path,
) -> dict[str, Any]:
    """Close typed authority, normalize coordinates, and reconcile one fact ledger.

    Structure-first compilation and the compatibility parser can temporarily describe
    the same formal typed fact. Exact duplicate compatibility shells are retired first;
    real cross-statement/cross-locator facts remain independent. Only then may semantic
    normalization, atomic projection, typed values/relations, identities, and conflicts
    consume the ledger. No second fact authority or downstream result patch is created.
    """
    asset = retire_duplicate_compatibility_typed_facts(asset)
    asset = normalize_explicit_business_fact_semantics(asset)
    asset = project_atomic_claim_facts(asset)
    asset = _normalize_typed_fact_values(asset)
    asset = project_typed_object_relations(asset)
    asset = apply_identity_evidence_policy(asset)
    asset = reconcile_chinese_business_fact_conflicts(
        asset,
        project_id=project_id,
        root=root,
    )
    asset = reconcile_typed_fact_conflicts(
        asset,
        project_id=project_id,
        root=root,
    )
    receipt = dict(asset.get("identity_evidence_policy_receipt") or {})
    receipt.update(
        {
            "second_pass_after_structure_compilation": True,
            "typed_fact_authority_closed_before_projection": True,
            "duplicate_compatibility_typed_shells_retired": True,
            "explicit_fact_semantic_coordinates_normalized": True,
            "atomic_claims_materialized_in_existing_ledger": True,
            "typed_relations_projected_into_existing_object_graph": True,
            "typed_value_contract_normalized": True,
            "cardinality_formal_slot_closed": True,
            "conflict_authority_reapplied": True,
            "typed_fact_conflicts_reconciled": True,
            "parallel_identity_engine_created": False,
        }
    )
    asset["identity_evidence_policy_receipt"] = receipt
    governance = dict(asset.get("governance") or {})
    governance.update(
        {
            "business_fact_two_pass_identity_governance": True,
            "typed_fact_authority_retirement_precedes_all_projections": True,
            "typed_fact_authority_retirement_selects_cross_statement": False,
            "typed_fact_authority_retirement_selects_cross_locator": False,
            "explicit_fact_coordinate_normalization_precedes_atomic_projection": True,
            "explicit_fact_coordinate_normalization_discovers_new_facts": False,
            "atomic_data_effects_become_bindable_facts": True,
            "typed_object_relations_use_existing_object_graph": True,
            "typed_atomic_value_projection_is_single_boundary": True,
            "typed_atomic_value_projection_never_selects_multiple_claims": True,
            "cardinality_value_and_quantity_constraint_share_one_authority": True,
            "identity_policy_runs_after_structure_fact_compilation": True,
            "conflict_authority_reapplied_after_structure_fact_compilation": True,
            "typed_fact_conflicts_use_existing_operator_authority": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["govern_compiled_business_facts"]
