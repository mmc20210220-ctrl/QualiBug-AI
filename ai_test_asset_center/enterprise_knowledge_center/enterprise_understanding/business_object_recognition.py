"""Public business-object recognition authority.

Recognition classifies source mentions by business role. Cross-source sameness,
aliases and stable IDs remain owned by ``identity_resolution``.
"""
from __future__ import annotations

from typing import Any

from .._chinese_business_authority_decision import apply_authority_decisions_to_conflicts
from ._object_candidate_governance import govern_object_candidates
from ._object_narrative_preparation import prepare_narrative_declared_asset
from ._object_recognition_projection import (
    apply_recognition_to_model,
    project_asset_for_recognized_objects,
    publish_recognition_and_identity,
)
from ._object_source_conflicts import (
    business_object_source_conflicts,
    project_business_object_source_conflicts,
)
from ._object_distinctness_source_authority import (
    finalize_distinctness_source_recognition,
    prepare_distinctness_source_asset,
)


def recognize_business_objects(asset: dict[str, Any]) -> dict[str, Any]:
    # Object declaration conflicts are projected at the public object authority
    # boundary, then resolved only through the repository's existing durable
    # SELECT_FACT / LEAVE_UNRESOLVED operator ledger.
    governed = asset
    if not business_object_source_conflicts(governed):
        governed = project_business_object_source_conflicts(governed)
    # The operator authority ledger resolves only business-object declaration
    # conflicts here. Running it unconditionally also re-reconciles every other
    # cross-document conflict (modality/authorization/…) and mutates
    # ``enterprise_comprehension_gate``, which masks the model's own conflict
    # gate or clears a legitimately blocked upstream gate. Gate the invocation
    # on the presence of an actual object-declaration conflict.
    if business_object_source_conflicts(governed):
        governed = apply_authority_decisions_to_conflicts(
            governed,
            project_id=str(governed.get("project_id") or ""),
        )
    prepared, authority = prepare_distinctness_source_asset(governed)
    if (
        not authority.get("declared_labels")
        and not authority.get("structured_source_declaration_present")
    ):
        prepared, authority = prepare_narrative_declared_asset(governed)
    return finalize_distinctness_source_recognition(
        govern_object_candidates(prepared), authority
    )


__all__ = [
    "apply_recognition_to_model",
    "project_asset_for_recognized_objects",
    "publish_recognition_and_identity",
    "recognize_business_objects",
]
