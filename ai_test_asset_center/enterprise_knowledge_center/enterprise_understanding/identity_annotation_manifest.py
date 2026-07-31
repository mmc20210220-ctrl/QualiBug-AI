"""Blind annotation manifest for enterprise identity Ground Truth.

The manifest exposes the closed-world source occurrence universe but deliberately
omits predicted entity ids, clusters, accepted edges and canonical-name choices.
It is an annotation input, never Ground Truth by itself.
"""
from __future__ import annotations

from typing import Any

from .schema import as_dict, as_list, stable_id, text

MANIFEST_SCHEMA = "qualibug.enterprise-identity-annotation-manifest.v1"
_BUSINESS_MENTION_TYPE = "BUSINESS_OBJECT"
_FORBIDDEN_PREDICTION_FIELDS = frozenset(
    {
        "entity_id",
        "cluster_id",
        "predicted_entity_id",
        "canonical_label",
        "accepted_edge_refs",
        "identity_resolution_status",
    }
)


def build_identity_annotation_manifest(result: dict[str, Any]) -> dict[str, Any]:
    mentions: list[dict[str, Any]] = []
    for raw in as_list(result.get("mentions")):
        if not isinstance(raw, dict):
            continue
        if text(raw.get("mention_type")) != _BUSINESS_MENTION_TYPE:
            continue
        mention_ref = text(raw.get("mention_id"))
        if not mention_ref:
            continue
        row = {
            "mention_ref": mention_ref,
            "raw_label": raw.get("raw_label"),
            "source_id": raw.get("source_id"),
            "source_locator": raw.get("source_locator"),
            "role": raw.get("role"),
            "scope": as_dict(raw.get("scope")),
            "source_kind": raw.get("source_kind"),
            "artifact_type": raw.get("artifact_type"),
            "annotation_status": "UNLABELED",
            "annotation_cluster_ref": "",
        }
        if _FORBIDDEN_PREDICTION_FIELDS.intersection(row):  # pragma: no cover
            raise AssertionError("identity_annotation_manifest_prediction_leak")
        mentions.append(row)
    mentions.sort(
        key=lambda row: (
            text(row.get("source_id")),
            text(row.get("source_locator")),
            text(row.get("role")),
            text(row.get("mention_ref")),
        )
    )
    return {
        "schema": MANIFEST_SCHEMA,
        "manifest_id": stable_id(
            "enterprise_identity_annotation_manifest",
            [row.get("mention_ref") for row in mentions],
        ),
        "annotation_scope": "CLOSED_WORLD_IDENTITY_MENTIONS",
        "mention_count": len(mentions),
        "mentions": mentions,
        "contains_product_cluster_suggestions": False,
        "contains_predicted_entity_ids": False,
        "is_ground_truth": False,
        "required_annotation_output_schema": (
            "qualibug.enterprise-identity-ground-truth.v1"
        ),
    }


def project_identity_annotation_manifest(
    asset: dict[str, Any], result: dict[str, Any]
) -> dict[str, Any]:
    manifest = build_identity_annotation_manifest(result)
    result["annotation_manifest"] = manifest
    asset["enterprise_identity_annotation_manifest"] = manifest
    summary = dict(as_dict(asset.get("summary")))
    summary["enterprise_identity_annotation_manifest_count"] = int(
        manifest.get("mention_count") or 0
    )
    asset["summary"] = summary
    return result


__all__ = [
    "MANIFEST_SCHEMA",
    "build_identity_annotation_manifest",
    "project_identity_annotation_manifest",
]
