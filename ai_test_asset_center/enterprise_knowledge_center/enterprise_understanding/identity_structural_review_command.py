"""Transactional command boundary for structural identity review mutations."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._common import ROOT, _safe_project_id
from ..transaction_lock import knowledge_transaction
from .identity_structural_review import (
    record_identity_structural_review_decision as _record_review_decision,
)
from .schema import as_dict, text


def _actor_identity(actor: Any) -> dict[str, str]:
    row = as_dict(actor)
    return {
        "name": text(
            row.get("name")
            or row.get("username")
            or row.get("actor_id")
            or row.get("id")
        ),
        "role": text(row.get("role")),
        "tenant_id": text(row.get("tenant_id") or row.get("tenant")),
    }


def record_identity_structural_review_decision(
    project_id: str,
    *,
    candidate_id: str,
    action: str,
    actor: Any,
    root: Path | None = None,
    canonical_entity_id: str = "",
    rationale: str = "",
    rebuild: bool = True,
) -> dict[str, Any]:
    """Serialize shared-ledger append and canonical knowledge rebuild per project."""
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    actor_row = _actor_identity(actor)
    if not actor_row["name"]:
        raise ValueError("identity_structural_review_actor_required")
    with knowledge_transaction(
        resolved_root,
        project,
        operation="identity_structural_review_decision",
        actor=actor_row,
    ):
        result = _record_review_decision(
            project,
            candidate_id=candidate_id,
            action=action,
            actor=actor_row,
            root=resolved_root,
            canonical_entity_id=canonical_entity_id,
            rationale=rationale,
            rebuild=rebuild,
        )
    result["knowledge_transaction_serialized"] = True
    result["knowledge_transaction_operation"] = (
        "identity_structural_review_decision"
    )
    return result


__all__ = ["record_identity_structural_review_decision"]
