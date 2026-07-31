"""Promotion gate for business-object candidates."""
from __future__ import annotations

from typing import Any

from ._object_candidate_collection import collect_object_candidates
from ._object_role_evidence import (
    HARD_NON_OBJECT_ROLES,
    accepted_facts,
    negative_role_index,
)
from .schema import as_list, new_unknown, stable_id, text, unique_text

ACCEPTED_OBJECT_STATUSES = frozenset(
    {"ACCEPTED", "ACCEPTED_BY_SOURCE_ALIAS", "ACCEPTED_WITH_ROLE_COLLISION_OVERRIDE"}
)


def govern_object_candidates(asset: dict[str, Any]) -> dict[str, Any]:
    collected = collect_object_candidates(asset)
    candidates = dict(collected["candidates"])
    negative = negative_role_index(asset, accepted_facts(asset))
    accepted: set[str] = set()
    conflicts: list[dict[str, Any]] = []
    reviews: list[dict[str, Any]] = []

    for key, row in candidates.items():
        row["negative_roles"] = sorted(negative.get(key, set()))
        collision = sorted(set(row["negative_roles"]) & HARD_NON_OBJECT_ROLES)
        source_backed = bool(row.get("source_backed_business_authority"))
        explicit = bool(row.get("explicit_object_authority"))
        if source_backed and collision and not explicit:
            row.update(status="CONFLICTED", reason_code="BUSINESS_OBJECT_ROLE_COLLISION")
            conflicts.append(row)
        elif source_backed and collision and explicit:
            row.update(
                status="ACCEPTED_WITH_ROLE_COLLISION_OVERRIDE",
                reason_code="EXPLICIT_OBJECT_AUTHORITY_WITH_ROLE_COLLISION",
            )
            accepted.add(key)
            reviews.append(row)
        elif source_backed:
            row.update(status="ACCEPTED", reason_code="SOURCE_BACKED_BUSINESS_ROLE")
            accepted.add(key)
        elif row.get("derived_only"):
            row.update(status="PENDING_DERIVED_ONLY", reason_code="DERIVED_OBJECT_WITHOUT_SOURCE_AUTHORITY")
        elif row.get("technical_roles") and not row.get("positive_roles"):
            row.update(status="PENDING_TECHNICAL_ONLY", reason_code="TECHNICAL_ASSET_IS_NOT_BUSINESS_OBJECT")
        elif set(row.get("positive_roles") or []) == {"TERM_ALIAS_ENDPOINT"}:
            row.update(status="PENDING_ALIAS_ONLY", reason_code="ALIAS_ENDPOINT_WITHOUT_OBJECT_USE")
        else:
            row.update(status="PENDING_SOURCE_EVIDENCE", reason_code="BUSINESS_OBJECT_SOURCE_EVIDENCE_MISSING")

    accepted_alias_fact_ids: set[str] = set()
    changed = True
    while changed:
        changed = False
        for edge in collected["alias_edges"]:
            left, right = edge["left"], edge["right"]
            if left not in accepted and right not in accepted:
                continue
            if edge.get("fact_id"):
                accepted_alias_fact_ids.add(edge["fact_id"])
            for key in (left, right):
                if key in accepted or key not in candidates:
                    continue
                row = candidates[key]
                collision = sorted(
                    set(row.get("negative_roles") or []) & HARD_NON_OBJECT_ROLES
                )
                if collision and not row.get("explicit_object_authority"):
                    row.update(status="CONFLICTED", reason_code="ALIASED_OBJECT_ROLE_COLLISION")
                    if row not in conflicts:
                        conflicts.append(row)
                    continue
                row.update(
                    status="ACCEPTED_BY_SOURCE_ALIAS",
                    reason_code="SOURCE_BACKED_ALIAS_TO_RECOGNIZED_OBJECT",
                )
                accepted.add(key)
                changed = True

    rows = sorted(
        candidates.values(),
        key=lambda row: (text(row.get("status")), text(row.get("comparison_key"))),
    )
    pending = [row for row in rows if text(row.get("status")).startswith("PENDING_")]
    unknowns: list[dict[str, Any]] = []
    for row in conflicts:
        unknowns.append(
            new_unknown(
                "BUSINESS_OBJECT_TYPE_CONFLICT",
                (
                    f"候选“{' / '.join(as_list(row.get('labels')))}”同时被资料标为业务对象"
                    f"和{'、'.join(as_list(row.get('negative_roles')))}，不能自动晋升。"
                ),
                related_objects=as_list(row.get("labels")),
                evidence=as_list(row.get("evidence")),
                severity="P0",
                blocks_formal_understanding=True,
                reason_code=text(row.get("reason_code")),
                details={
                    "candidate_id": row.get("candidate_id"),
                    "positive_roles": row.get("positive_roles"),
                    "negative_roles": row.get("negative_roles"),
                    "automatic_type_guess_allowed": False,
                },
            )
        )
    for row in reviews:
        unknowns.append(
            new_unknown(
                "BUSINESS_OBJECT_ROLE_COLLISION_REVIEW",
                (
                    f"源声明对象“{' / '.join(as_list(row.get('labels')))}”还被用作"
                    f"{'、'.join(as_list(row.get('negative_roles')))}；保留对象身份但要求复核槽位。"
                ),
                related_objects=as_list(row.get("labels")),
                evidence=as_list(row.get("evidence")),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="EXPLICIT_OBJECT_AUTHORITY_WITH_ROLE_COLLISION",
            )
        )
    technical_pending = [
        row for row in pending if text(row.get("status")) == "PENDING_TECHNICAL_ONLY"
    ]
    if technical_pending:
        unknowns.append(
            new_unknown(
                "TECHNICAL_ASSET_WITHOUT_BUSINESS_OBJECT",
                (
                    f"存在{len(technical_pending)}个技术候选，但没有源声明业务用途；"
                    "表和字段父级不会自动升为业务对象。"
                ),
                related_objects=[
                    label
                    for row in technical_pending[:40]
                    for label in as_list(row.get("labels"))
                ],
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="TECHNICAL_ASSET_IS_NOT_BUSINESS_OBJECT",
                details={"automatic_table_to_object_promotion_allowed": False},
            )
        )

    accepted_labels = unique_text(
        label for key in accepted for label in as_list(candidates[key].get("labels"))
    )
    status = (
        "BLOCKED_BUSINESS_OBJECT_TYPE_CONFLICT"
        if conflicts
        else "PARTIAL_BUSINESS_OBJECT_RECOGNITION"
        if pending or reviews
        else "PASS"
    )
    gate = {
        "schema": "qualibug.enterprise-business-object-recognition-gate.v1",
        "status": status,
        "entry_allowed": not conflicts,
        "identity_resolution_allowed": not conflicts,
        "automatic_type_guess_allowed": False,
        "automatic_table_to_object_promotion_allowed": False,
        "metrics": {
            "candidate_count": len(rows),
            "accepted_candidate_count": len(
                [row for row in rows if text(row.get("status")) in ACCEPTED_OBJECT_STATUSES]
            ),
            "accepted_label_count": len(accepted_labels),
            "pending_candidate_count": len(pending),
            "type_conflict_count": len(conflicts),
            "role_collision_review_count": len(reviews),
            "technical_only_candidate_count": len(technical_pending),
        },
        "critical_conflicts": [
            row for row in unknowns if bool(row.get("blocks_formal_understanding"))
        ],
        "unknowns": unknowns,
        "required_operator_action": (
            "resolve business-object versus actor/action/state slot conflicts"
            if conflicts
            else "bind pending technical or derived candidates to source-backed business facts"
            if pending
            else "review explicit object role collisions"
            if reviews
            else ""
        ),
    }
    recognition_id = stable_id(
        "business_object_recognition",
        asset.get("asset_id"),
        accepted_labels,
        [(row.get("candidate_id"), row.get("status")) for row in rows],
    )
    return {
        "schema": "qualibug.enterprise-business-object-recognition.v1",
        "recognition_id": recognition_id,
        "candidates": rows,
        "accepted_labels": accepted_labels,
        "accepted_comparison_keys": sorted(accepted),
        "accepted_alias_fact_ids": sorted(accepted_alias_fact_ids),
        "unknowns": unknowns,
        "gate": gate,
        "object_type_authority": "SOURCE_BACKED_BUSINESS_ROLE",
        "identity_fusion_authority": "enterprise_identity_resolution",
        "technical_artifacts_are_business_objects": False,
        "industry_vocabulary_used": False,
        "fuzzy_similarity_used": False,
        "model_self_confirmation_used": False,
    }


__all__ = ["ACCEPTED_OBJECT_STATUSES", "govern_object_candidates"]
