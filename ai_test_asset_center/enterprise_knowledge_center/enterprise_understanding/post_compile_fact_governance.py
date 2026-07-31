"""Second-pass governance for the single compiled business-fact ledger."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._chinese_business_conflicts import reconcile_chinese_business_fact_conflicts
from .identity_evidence_policy import apply_identity_evidence_policy
from .typed_fact_conflicts import reconcile_typed_fact_conflicts


def govern_compiled_business_facts(
    asset: dict[str, Any],
    *,
    project_id: str,
    root: Path,
) -> dict[str, Any]:
    """Classify identities and reconcile final typed facts through existing authority.

    The first understanding pass discovers source-backed terms and rules. Structure-first
    compilation upgrades that same ledger. This second pass never extracts from text; it
    classifies identity evidence, reapplies the legacy conflict authority, then checks
    typed condition/formula/cardinality slots through the same durable operator ledger.
    """
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
            "identity_policy_runs_after_structure_fact_compilation": True,
            "conflict_authority_reapplied_after_structure_fact_compilation": True,
            "typed_fact_conflicts_use_existing_operator_authority": True,
        }
    )
    asset["governance"] = governance
    return asset


__all__ = ["govern_compiled_business_facts"]
