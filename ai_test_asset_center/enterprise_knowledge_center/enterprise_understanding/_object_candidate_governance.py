"""Promotion gate for business-object candidates."""
from __future__ import annotations

from collections import Counter
from typing import Any

from ._object_candidate_collection import collect_object_candidates
from ._object_role_evidence import (
    HARD_NON_OBJECT_ROLES,
    accepted_facts,
    negative_role_index,
)
from .schema import as_list, new_unknown, stable_id, text, unique_text

ACCEPTED_OBJECT_STATUSES = frozenset(
    {
        "ACCEPTED",
        "ACCEPTED_BY_SOURCE_ALIAS",
        "ACCEPTED_SURFACE_FORM_IDENTITY_PENDING",
        "ACCEPTED_WITH_ROLE_COLLISION_OVERRIDE",
    }
)


def govern_object_candidates(asset: dict[str, Any]) -> dict[str, Any]:
    collected = collect_object_candidates(asset)
    candidates = dict(collected["candidates"])
    ignored_inputs = [
        dict(row) for row in as_list(collected.get("ignored_inputs")) if isinstance(row, dict)
    ]
    rejected_fact_mentions = [
        dict(row)
        for row in as_list(collected.get("rejected_fact_mentions"))
        if isinstance(row, dict)
    ]
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
        elif source_backed and bool(row.get("requires_identity_review")):
            row.update(
                status="ACCEPTED_SURFACE_FORM_IDENTITY_PENDING",
                reason_code="SOURCE_ATTESTED_OBJECT_SURFACE_IDENTITY_UNRESOLVED",
                automatic_identity_union_allowed=False,
                requires_identity_review=True,
            )
            accepted.add(key)
        elif source_backed:
            row.update(status="ACCEPTED", reason_code="SOURCE_BACKED_BUSINESS_ROLE")
            accepted.add(key)
        elif row.get("derived_only"):
            row.update(
                status="PENDING_DERIVED_ONLY",
                reason_code="DERIVED_OBJECT_WITHOUT_SOURCE_AUTHORITY",
            )
        elif row.get("technical_roles") and not row.get("positive_roles"):
            row.update(
                status="PENDING_TECHNICAL_ONLY",
                reason_code="TECHNICAL_ASSET_IS_NOT_BUSINESS_OBJECT",
            )
        elif set(row.get("positive_roles") or []) == {"TERM_ALIAS_ENDPOINT"}:
            row.update(
                status="PENDING_ALIAS_ONLY",
                reason_code="ALIAS_ENDPOINT_WITHOUT_OBJECT_USE",
            )
        else:
            row.update(
                status="PENDING_SOURCE_EVIDENCE",
                reason_code="BUSINESS_OBJECT_SOURCE_EVIDENCE_MISSING",
            )

    accepted_alias_fact_ids: set[str] = set()
    accepted_alias_edge_ids: set[str] = set()
    accepted_alias_edges: list[dict[str, Any]] = []
    changed = True
    while changed:
        changed = False
        for edge in as_list(collected.get("alias_edges")):
            if not isinstance(edge, dict):
                continue
            left, right = text(edge.get("left")), text(edge.get("right"))
            if left not in accepted and right not in accepted:
                continue
            edge_id = text(edge.get("edge_id"))
            if edge_id and edge_id not in accepted_alias_edge_ids:
                accepted_alias_edge_ids.add(edge_id)
                accepted_alias_edges.append(dict(edge))
            if edge.get("fact_id"):
                accepted_alias_fact_ids.add(text(edge.get("fact_id")))
            for key in (left, right):
                if key not in candidates:
                    continue
                row = candidates[key]
                collision = sorted(
                    set(row.get("negative_roles") or []) & HARD_NON_OBJECT_ROLES
                )
                if collision and not row.get("explicit_object_authority"):
                    row.update(
                        status="CONFLICTED",
                        reason_code="ALIASED_OBJECT_ROLE_COLLISION",
                    )
                    if row not in conflicts:
                        conflicts.append(row)
                    continue
                if key in accepted:
                    if row.get("requires_identity_review"):
                        row.update(
                            status="ACCEPTED_BY_SOURCE_ALIAS",
                            reason_code="SOURCE_BACKED_ALIAS_TO_RECOGNIZED_OBJECT",
                            identity_resolution_eligible=True,
                            requires_identity_review=False,
                            automatic_identity_union_allowed=True,
                        )
                    continue
                row.update(
                    status="ACCEPTED_BY_SOURCE_ALIAS",
                    reason_code="SOURCE_BACKED_ALIAS_TO_RECOGNIZED_OBJECT",
                    identity_resolution_eligible=True,
                    requires_identity_review=False,
                    automatic_identity_union_allowed=True,
                )
                accepted.add(key)
                changed = True

    rows = sorted(
        candidates.values(),
        key=lambda row: (text(row.get("status")), text(row.get("comparison_key"))),
    )
    surface_identity_reviews = [
        row
        for row in rows
        if bool(row.get("requires_identity_review"))
        and not bool(row.get("identity_resolution_eligible"))
    ]
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
    for row in surface_identity_reviews:
        unknowns.append(
            new_unknown(
                "BUSINESS_OBJECT_SURFACE_IDENTITY_UNRESOLVED",
                (
                    f"源资料使用了业务对象词面“{' / '.join(as_list(row.get('labels')))}”；"
                    "对象类型已确认，但它与复合声明对象的身份关系未被字符串包含关系自动合并。"
                ),
                related_objects=[
                    *as_list(row.get("labels")),
                    *as_list(row.get("surface_parent_labels")),
                ],
                evidence=as_list(row.get("evidence")),
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="SOURCE_ATTESTED_OBJECT_SURFACE_IDENTITY_UNRESOLVED",
                details={
                    "candidate_id": row.get("candidate_id"),
                    "surface_parent_keys": row.get("surface_parent_keys") or [],
                    "automatic_identity_union_allowed": False,
                    "required_identity_authority": (
                        "source-declared TERM_ALIAS or operator identity review"
                    ),
                },
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

    ignored_reason_counts = dict(
        sorted(Counter(text(row.get("reason_code")) for row in ignored_inputs).items())
    )
    rejected_reason_counts = dict(
        sorted(
            Counter(
                text(row.get("reason_code")) for row in rejected_fact_mentions
            ).items()
        )
    )
    if ignored_inputs:
        unknowns.append(
            new_unknown(
                "DERIVED_OBJECT_ASSET_NOT_TYPE_AUTHORITY",
                (
                    f"忽略{len(ignored_inputs)}个产品派生对象输入；派生词汇或模型候选"
                    "不能反向证明自身是业务对象。"
                ),
                related_objects=[row.get("label") for row in ignored_inputs[:40]],
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="DERIVED_OBJECT_ASSET_IS_NOT_TYPE_AUTHORITY",
                details={
                    "ignored_input_count": len(ignored_inputs),
                    "reason_distribution": ignored_reason_counts,
                    "automatic_type_guess_allowed": False,
                },
            )
        )
    if rejected_fact_mentions:
        unknowns.append(
            new_unknown(
                "BUSINESS_OBJECT_FACT_SLOT_REJECTED",
                (
                    f"拒绝{len(rejected_fact_mentions)}个不满足对象槽位契约的实体引用；"
                    "原始证据保留，但不会进入正式对象空间。"
                ),
                related_objects=[
                    row.get("label") for row in rejected_fact_mentions[:40]
                ],
                evidence=[
                    evidence
                    for row in rejected_fact_mentions[:20]
                    for evidence in as_list(row.get("evidence"))
                ],
                severity="P1",
                blocks_formal_understanding=False,
                reason_code="BUSINESS_OBJECT_FACT_SLOT_NOT_TYPE_AUTHORITY",
                details={
                    "rejected_fact_mention_count": len(rejected_fact_mentions),
                    "reason_distribution": rejected_reason_counts,
                    "raw_entity_mentions_used_as_object_authority": False,
                },
            )
        )

    accepted_labels = unique_text(
        label for key in accepted for label in as_list(candidates[key].get("labels"))
    )
    identity_resolution_eligible = sorted(
        key
        for key in accepted
        if bool(candidates[key].get("identity_resolution_eligible"))
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
            "identity_resolution_eligible_candidate_count": len(
                identity_resolution_eligible
            ),
            "pending_candidate_count": len(pending),
            "type_conflict_count": len(conflicts),
            "role_collision_review_count": len(reviews),
            "surface_form_identity_review_count": len(surface_identity_reviews),
            "technical_only_candidate_count": len(technical_pending),
            "accepted_alias_edge_count": len(accepted_alias_edges),
            "ignored_derived_input_count": len(ignored_inputs),
            "rejected_fact_mention_count": len(rejected_fact_mentions),
            "ignored_input_reason_distribution": ignored_reason_counts,
            "rejected_fact_mention_reason_distribution": rejected_reason_counts,
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
            else "confirm source surface identities through TERM_ALIAS or identity review"
            if surface_identity_reviews
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
        "identity_resolution_eligible_comparison_keys": identity_resolution_eligible,
        "accepted_alias_fact_ids": sorted(accepted_alias_fact_ids),
        "accepted_alias_edges": sorted(
            accepted_alias_edges, key=lambda row: text(row.get("edge_id"))
        ),
        "ignored_inputs": ignored_inputs,
        "rejected_fact_mentions": rejected_fact_mentions,
        "unknowns": unknowns,
        "gate": gate,
        "object_type_authority": "SOURCE_BACKED_BUSINESS_ROLE",
        "identity_fusion_authority": "enterprise_identity_resolution",
        "technical_artifacts_are_business_objects": False,
        "derived_object_assets_used_as_authority": False,
        "raw_entity_mentions_used_as_object_authority": False,
        "surface_form_identity_union_allowed": False,
        "industry_vocabulary_used": False,
        "fuzzy_similarity_used": False,
        "model_self_confirmation_used": False,
    }


__all__ = ["ACCEPTED_OBJECT_STATUSES", "govern_object_candidates"]
