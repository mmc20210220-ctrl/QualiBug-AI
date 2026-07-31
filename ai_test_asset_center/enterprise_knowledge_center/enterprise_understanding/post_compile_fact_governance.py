"""Second-pass governance for the single compiled business-fact ledger."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
from .identity_evidence_policy import apply_identity_evidence_policy
from .typed_fact_conflicts import reconcile_typed_fact_conflicts


def _text(value: Any) -> str:
    return str(value or "").strip()


def _dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _normalize_typed_fact_values(asset: dict[str, Any]) -> dict[str, Any]:
    """Project one unambiguous atomic value into the existing candidate contract.

    ``business-fact-ledger.v2`` stores typed values on atomic claims. Existing rule
    candidate validation consumes the established top-level ``value`` field. This
    normalization is the single compatibility boundary: it projects only one unique
    source-backed value and never selects among competing claims.
    """
    ledger = _dict(asset.get("business_fact_ledger"))
    facts = [dict(row) for row in _list(ledger.get("items")) if isinstance(row, dict)]
    projected = 0
    ambiguous_ids: list[str] = []
    for fact in facts:
        fact_type = _text(fact.get("fact_type")).upper()
        if fact_type != "CARDINALITY_CONSTRAINT" or _dict(fact.get("value")):
            continue
        values = [
            dict(_dict(claim.get("value")))
            for claim in _list(fact.get("claims"))
            if isinstance(claim, dict)
            and _text(claim.get("claim_type")).upper() == fact_type
            and _dict(claim.get("value"))
        ]
        unique = {
            json.dumps(value, ensure_ascii=False, sort_keys=True, default=str): value
            for value in values
        }
        if len(unique) == 1:
            fact["value"] = dict(next(iter(unique.values())))
            fact["typed_value_projection"] = {
                "status": "PASS",
                "source": "single_atomic_claim",
                "automatic_winner_used": False,
            }
            projected += 1
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
        "projected_fact_count": projected,
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
    """Classify identities and reconcile final typed facts through existing authority.

    The first understanding pass discovers source-backed terms and rules. Structure-first
    compilation upgrades that same ledger. This second pass never extracts from text; it
    normalizes atomic values, classifies identity evidence, reapplies the legacy conflict
    authority, then checks typed slots through the same durable operator ledger.
    """
    asset = _normalize_typed_fact_values(asset)
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
            "typed_value_contract_normalized": True,
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
            "typed_atomic_value_projection_is_single_boundary": True,
            "typed_atomic_value_projection_never_selects_multiple_claims": True,
            "identity_policy_runs_after_structure_fact_compilation": True,
            "conflict_authority_reapplied_after_structure_fact_compilation": True,
            "typed_fact_conflicts_use_existing_operator_authority": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["govern_compiled_business_facts"]
