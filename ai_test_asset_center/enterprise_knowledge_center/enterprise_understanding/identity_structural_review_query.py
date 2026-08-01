"""Read adapter for current and completed structural identity review tasks."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._chinese_business_authority_decision import load_authority_decision_ledger
from .._common import ROOT, _safe_project_id
from .identity_structural_review import (
    DECISION_KIND,
    project_identity_structural_review_queue,
)
from .schema import as_dict, as_list, text


def get_identity_structural_review_queue(
    project_id: str,
    root: Path | None = None,
    *,
    rebuild_if_missing: bool = True,
) -> dict[str, Any]:
    """Return live candidates, or the persisted completed queue after a merge."""
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    from ..composition import (
        build_enterprise_business_knowledge_asset,
        load_enterprise_business_knowledge_asset,
    )

    asset = load_enterprise_business_knowledge_asset(project, resolved_root)
    if not isinstance(asset, dict) and rebuild_if_missing:
        asset = build_enterprise_business_knowledge_asset(project, resolved_root)
    if not isinstance(asset, dict):
        raise KeyError("identity_structural_review_asset_missing")

    ledger = load_authority_decision_ledger(project, resolved_root)
    asset["identity_structural_review_decisions"] = [
        dict(row)
        for row in as_list(ledger.get("decisions"))
        if isinstance(row, dict)
        and text(row.get("decision_kind")) == DECISION_KIND
    ]
    model = as_dict(asset.get("enterprise_understanding_model"))
    current_candidates = [
        row
        for row in as_list(model.get("identity_structural_candidates"))
        if isinstance(row, dict) and text(row.get("candidate_id"))
    ]
    if current_candidates:
        return project_identity_structural_review_queue(asset, model)

    persisted = as_dict(
        asset.get("enterprise_identity_structural_review_queue")
        or model.get("identity_structural_review_queue")
        or as_dict(
            asset.get("enterprise_identity_structural_review_receipt")
            or model.get("identity_structural_review_receipt")
        ).get("review_queue")
    )
    if persisted:
        return persisted
    return project_identity_structural_review_queue(asset, model)


__all__ = ["get_identity_structural_review_queue"]
