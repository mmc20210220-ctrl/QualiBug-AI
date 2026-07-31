"""Public business-object recognition authority.

Recognition classifies source mentions by business role. Cross-source sameness,
aliases and stable IDs remain owned by ``identity_resolution``.
"""
from __future__ import annotations

from typing import Any

from ._object_candidate_governance import govern_object_candidates
from ._object_recognition_projection import (
    apply_recognition_to_model,
    project_asset_for_recognized_objects,
    publish_recognition_and_identity,
)


def recognize_business_objects(asset: dict[str, Any]) -> dict[str, Any]:
    return govern_object_candidates(asset)


__all__ = [
    "apply_recognition_to_model",
    "project_asset_for_recognized_objects",
    "publish_recognition_and_identity",
    "recognize_business_objects",
]
