"""Blind human annotation tasks and Ground Truth compilation for identity quality.

This module lowers annotation cost without contaminating external Ground Truth with
product predictions. It packages exact source-occurrence mentions plus bounded source
context, validates complete closed-world submissions, compares annotation partitions
independently of annotator-local cluster names, and compiles deterministic Ground Truth.
"""
from __future__ import annotations

from collections import defaultdict
from typing import Any

from .._utils import _redact_text
from .identity_annotation_manifest import MANIFEST_SCHEMA
from .identity_benchmark import ANNOTATION_SCOPE, GROUND_TRUTH_SCHEMA
from .schema import as_dict, as_list, stable_id, text

TASK_PACKAGE_SCHEMA = "qualibug.enterprise-identity-annotation-task-package.v1"
SUBMISSION_SCHEMA = "qualibug.enterprise-identity-annotation-submission.v1"
COMPILATION_SCHEMA = "qualibug.enterprise-identity-annotation-compilation.v1"

_FORBIDDEN_PREDICTION_FIELDS = frozenset(
    {
        "entity_id",
        "cluster_id",
        "predicted_entity_id",
        "predicted_cluster_id",
        "canonical_label",
        "accepted_edge_refs",
        "identity_resolution_status",
        "comparison_keys",
    }
)


def _contains_forbidden_prediction_field(value: Any) -> bool:
    if isinstance(value, list):
        return any(_contains_forbidden_prediction_field(row) for row in value)
    if not isinstance(value, dict):
        return False
    if _FORBIDDEN_PREDICTION_FIELDS.intersection(value):
        return True
    return any(_contains_forbidden_prediction_field(row) for row in value.values())


def _bounded(value: Any, limit: int = 800) -> str:
    return _redact_text(value, limit=limit)


def _progress(total: int, completed: int, *, status: str = "") -> dict[str, Any]:
    bounded_total = max(0, int(total))
    bounded_completed = max(0, min(int(completed), bounded_total))
    return {
        "total_task_count": bounded_total,
        "completed_task_count": bounded_completed,
        "remaining_task_count": bounded_total - bounded_completed,
        "completion_rate": (
            round(bounded_completed / bounded_total, 6) if bounded_total else 1.0
        ),
        "status": status or (
            "COMPLETE"
            if bounded_completed == bounded_total
            else "IN_PROGRESS"
            if bounded_completed
            else "NOT_STARTED"
        ),
    }


def _context_rows(mention: dict[str, Any], *, maximum: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for raw in as_list(mention.get("evidence")):
        if not isinstance(raw, dict):
            continue
        quote = _bounded(
            raw.get("quote")
            or raw.get("verbatim_quote")
            or raw.get("source_excerpt")
        )
        source_id = text(raw.get("source_id") or mention.get("source_id"))
        locator = text(
            raw.get("source_locator")
            or raw.get("locator")
            or mention.get("source_locator")
        )
        key = (source_id, locator, quote)
        if key in seen:
            continue
        seen.add(key)
        rows.append(
            {
                "source_id": source_id,
                "source_locator": locator,
                "quote": quote,
                "quote_hash": text(raw.get("quote_hash")),
                "asset_ref": text(raw.get("asset_ref")),
                "derivation": text(raw.get("derivation")),
            }
        )
        if len(rows) >= maximum:
            break
    if not rows:
        rows.append(
            {
                "source_id": text(mention.get("source_id")),
                "source_locator": text(mention.get("source_locator")),
                "quote": "",
                "quote_hash": "",
                "asset_ref": text(mention.get("artifact_ref")),
                "derivation": text(mention.get("source_kind")),
            }
        )
    return rows


def build_identity_annotation_task_package(
    asset: dict[str, Any],
    *,
    batch_size: int = 40,
) -> dict[str, Any]:
    """Build a deterministic, prediction-free task package from the current asset."""
    manifest = as_dict(asset.get("enterprise_identity_annotation_manifest"))
    if text(manifest.get("schema")) != MANIFEST_SCHEMA:
        raise ValueError("identity_annotation_manifest_unavailable")
    manifest_id = text(manifest.get("manifest_id"))
    if not manifest_id:
        raise ValueError("identity_annotation_manifest_id_missing")
    resolution = as_dict(asset.get("enterprise_identity_resolution"))
    mention_index = {
        text(row.get("mention_id")): row
        for row in as_list(resolution.get("mentions"))
        if isinstance(row, dict) and text(row.get("mention_id"))
    }
    size = max(1, min(int(batch_size or 40), 200))
    tasks: list[dict[str, Any]] = []
    for position, raw in enumerate(as_list(manifest.get("mentions"))):
        if not isinstance(raw, dict):
            continue
        mention_ref = text(raw.get("mention_ref"))
        if not mention_ref:
            continue
        mention = as_dict(mention_index.get(mention_ref))
        batch_number = position // size + 1
        tasks.append(
            {
                "task_id": stable_id("identity_annotation_task", manifest_id, mention_ref),
                "batch_id": f"identity-annotation-batch:{batch_number:04d}",
                "mention_ref": mention_ref,
                "raw_label": raw.get("raw_label"),
                "source_id": raw.get("source_id"),
                "source_locator": raw.get("source_locator"),
                "role": raw.get("role"),
                "scope": as_dict(raw.get("scope")),
                "source_kind": raw.get("source_kind"),
                "artifact_type": raw.get("artifact_type"),
                "context": _context_rows(mention),
                "annotation_status": "UNLABELED",
                "annotation_cluster_ref": "",
                "annotation_note": "",
            }
        )

    batches: list[dict[str, Any]] = []
    for start in range(0, len(tasks), size):
        rows = tasks[start : start + size]
        if not rows:
            continue
        batches.append(
            {
                "batch_id": rows[0]["batch_id"],
                "position": len(batches) + 1,
                "task_count": len(rows),
                "task_refs": [row["task_id"] for row in rows],
                "mention_refs": [row["mention_ref"] for row in rows],
                "progress": _progress(len(rows), 0),
            }
        )

    mention_refs = [row["mention_ref"] for row in tasks]
    package_id = stable_id(
        "enterprise_identity_annotation_task_package",
        manifest_id,
        mention_refs,
    )
    batch_layout_id = stable_id(
        "enterprise_identity_annotation_batch_layout",
        package_id,
        size,
        [row["mention_refs"] for row in batches],
    )
    submission_template = {
        "schema": SUBMISSION_SCHEMA,
        "task_package_id": package_id,
        "manifest_id": manifest_id,
        "annotation_scope": ANNOTATION_SCOPE,
        "generated_from_product_output": False,
        "annotator": {"name": "", "role": "ANNOTATOR"},
        "progress": _progress(len(tasks), 0),
        "annotations": [
            {
                "mention_ref": row["mention_ref"],
                "annotation_status": "UNLABELED",
                "annotation_cluster_ref": "",
                "annotation_note": "",
            }
            for row in tasks
        ],
    }
    package = {
        "schema": TASK_PACKAGE_SCHEMA,
        "task_package_id": package_id,
        "batch_layout_id": batch_layout_id,
        "manifest_id": manifest_id,
        "annotation_scope": ANNOTATION_SCOPE,
        "task_count": len(tasks),
        "batch_size": size,
        "batch_count": len(batches),
        "progress": _progress(len(tasks), 0),
        "tasks": tasks,
        "batches": batches,
        "submission_template": submission_template,
        "review_modes": ["SINGLE_ANNOTATOR", "DOUBLE_BLIND", "ADJUDICATED"],
        "instructions": {
            "closed_world": True,
            "every_mention_requires_one_confirmed_cluster": True,
            "singleton_clusters_must_be_explicit": True,
            "cluster_names_are_annotator_local": True,
            "double_blind_agreement_compares_partition_not_cluster_names": True,
            "batch_layout_does_not_change_task_package_identity": True,
        },
        "contains_product_cluster_suggestions": False,
        "contains_predicted_entity_ids": False,
        "contains_similarity_candidates": False,
        "source_context_is_redacted": True,
        "is_ground_truth": False,
        "required_submission_schema": SUBMISSION_SCHEMA,
        "compiled_ground_truth_schema": GROUND_TRUTH_SCHEMA,
    }
    if _contains_forbidden_prediction_field(package):  # pragma: no cover
        raise AssertionError("identity_annotation_task_prediction_leak")
    return package


def _validate_submission(
    package: dict[str, Any], submission: dict[str, Any]
) -> tuple[dict[str, str], dict[str, str]]:
    if _contains_forbidden_prediction_field(submission):
        raise ValueError("product_prediction_fields_cannot_be_identity_annotation")
    if text(submission.get("schema")) != SUBMISSION_SCHEMA:
        raise ValueError("identity_annotation_submission_schema_invalid")
    if text(submission.get("task_package_id")) != text(package.get("task_package_id")):
        raise ValueError("identity_annotation_task_package_stale")
    if text(submission.get("manifest_id")) != text(package.get("manifest_id")):
        raise ValueError("identity_annotation_submission_manifest_stale")
    if text(submission.get("annotation_scope")) != ANNOTATION_SCOPE:
        raise ValueError("identity_annotation_submission_scope_invalid")
    if bool(submission.get("generated_from_product_output")):
        raise ValueError("product_output_cannot_be_identity_annotation")

    annotator = as_dict(submission.get("annotator"))
    annotator_name = text(
        annotator.get("name") or annotator.get("annotator_id") or annotator.get("username")
    )
    if not annotator_name:
        raise ValueError("identity_annotation_annotator_required")
    expected = {
        text(row.get("mention_ref"))
        for row in as_list(package.get("tasks"))
        if isinstance(row, dict) and text(row.get("mention_ref"))
    }
    assignments: dict[str, str] = {}
    seen_refs: set[str] = set()
    duplicate_refs: list[str] = []
    incomplete_refs: list[str] = []
    for raw in as_list(submission.get("annotations")):
        if not isinstance(raw, dict):
            continue
        mention_ref = text(raw.get("mention_ref"))
        if not mention_ref:
            continue
        if mention_ref in seen_refs:
            duplicate_refs.append(mention_ref)
            continue
        seen_refs.add(mention_ref)
        status = text(raw.get("annotation_status") or "CONFIRMED").upper()
        cluster_ref = text(raw.get("annotation_cluster_ref") or raw.get("cluster_ref"))
        if status != "CONFIRMED" or not cluster_ref:
            incomplete_refs.append(mention_ref)
            continue
        assignments[mention_ref] = cluster_ref
    if duplicate_refs:
        raise ValueError("identity_annotation_submission_duplicate_mentions")
    unknown = sorted(seen_refs - expected)
    missing = sorted(expected - seen_refs)
    if unknown:
        raise ValueError("identity_annotation_submission_unknown_mentions")
    if missing or incomplete_refs:
        raise ValueError("identity_annotation_submission_incomplete")
    return assignments, {
        "name": annotator_name,
        "role": text(annotator.get("role") or "ANNOTATOR").upper(),
    }


def _partition(assignments: dict[str, str]) -> dict[str, tuple[str, ...]]:
    groups: dict[str, list[str]] = defaultdict(list)
    for mention_ref, cluster_ref in assignments.items():
        groups[cluster_ref].append(mention_ref)
    membership: dict[str, tuple[str, ...]] = {}
    for members in groups.values():
        signature = tuple(sorted(set(members)))
        for mention_ref in signature:
            membership[mention_ref] = signature
    return membership


def _disagreements(
    primary: dict[str, str], secondary: dict[str, str]
) -> list[dict[str, Any]]:
    left = _partition(primary)
    right = _partition(secondary)
    grouped: dict[tuple[tuple[str, ...], tuple[str, ...]], set[str]] = {}
    for mention_ref in sorted(set(left) | set(right)):
        left_members = left.get(mention_ref, (mention_ref,))
        right_members = right.get(mention_ref, (mention_ref,))
        if left_members == right_members:
            continue
        key = (left_members, right_members)
        grouped.setdefault(key, set()).add(mention_ref)
    rows: list[dict[str, Any]] = []
    for (left_members, right_members), affected in sorted(grouped.items()):
        rows.append(
            {
                "disagreement_id": stable_id(
                    "identity_annotation_disagreement", left_members, right_members
                ),
                "affected_mention_refs": sorted(affected),
                "primary_cluster_member_refs": list(left_members),
                "secondary_cluster_member_refs": list(right_members),
                "reason_code": "ANNOTATOR_PARTITION_DISAGREEMENT",
            }
        )
    return rows


def _ground_truth(
    package: dict[str, Any],
    assignments: dict[str, str],
    *,
    annotators: list[dict[str, str]],
    review_status: str,
    disagreement_count: int,
) -> dict[str, Any]:
    groups: dict[str, list[str]] = defaultdict(list)
    for mention_ref, cluster_ref in assignments.items():
        groups[cluster_ref].append(mention_ref)
    member_groups = sorted(
        {tuple(sorted(set(members))) for members in groups.values()}
    )
    manifest_id = text(package.get("manifest_id"))
    clusters = [
        {
            "cluster_ref": stable_id(
                "enterprise_identity_ground_truth_cluster", manifest_id, members
            ),
            "annotation_status": "CONFIRMED",
            "member_refs": list(members),
        }
        for members in member_groups
    ]
    return {
        "schema": GROUND_TRUTH_SCHEMA,
        "benchmark_id": stable_id(
            "enterprise_identity_ground_truth", manifest_id, member_groups
        ),
        "manifest_id": manifest_id,
        "annotation_scope": ANNOTATION_SCOPE,
        "ground_truth_generated_from_product_output": False,
        "annotation_contract": "BLIND_SOURCE_OCCURRENCE_CLOSED_WORLD",
        "review_status": review_status,
        "annotators": annotators,
        "disagreement_count": disagreement_count,
        "task_package_id": package.get("task_package_id"),
        "clusters": clusters,
    }


def compile_identity_annotation_submissions(
    package: dict[str, Any],
    primary_submission: dict[str, Any],
    *,
    secondary_submission: dict[str, Any] | None = None,
    adjudication_submission: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compile one or two blind submissions, or return an adjudication queue."""
    if text(package.get("schema")) != TASK_PACKAGE_SCHEMA:
        raise ValueError("identity_annotation_task_package_schema_invalid")
    if adjudication_submission and not secondary_submission:
        raise ValueError("identity_adjudication_requires_secondary_submission")
    primary, primary_annotator = _validate_submission(package, primary_submission)
    if primary_annotator["role"] == "ADJUDICATOR":
        raise ValueError("identity_primary_annotator_role_invalid")
    annotators = [primary_annotator]
    disagreements: list[dict[str, Any]] = []
    selected = primary
    review_status = "SINGLE_ANNOTATOR"
    total = len(primary)

    if secondary_submission:
        secondary, secondary_annotator = _validate_submission(
            package, secondary_submission
        )
        if secondary_annotator["role"] == "ADJUDICATOR":
            raise ValueError("identity_secondary_annotator_role_invalid")
        if secondary_annotator["name"].casefold() == primary_annotator["name"].casefold():
            raise ValueError("identity_double_blind_annotators_must_differ")
        annotators.append(secondary_annotator)
        disagreements = _disagreements(primary, secondary)
        if disagreements and not adjudication_submission:
            return {
                "schema": COMPILATION_SCHEMA,
                "status": "REVIEW_REQUIRED",
                "task_package_id": package.get("task_package_id"),
                "manifest_id": package.get("manifest_id"),
                "annotation_scope": ANNOTATION_SCOPE,
                "review_status": "DOUBLE_BLIND_DISAGREED",
                "annotators": annotators,
                "progress": _progress(total, total, status="AWAITING_ADJUDICATION"),
                "disagreement_count": len(disagreements),
                "disagreements": disagreements,
                "ground_truth": {},
                "ground_truth_import_allowed": False,
            }
        if disagreements:
            selected, adjudicator = _validate_submission(
                package, as_dict(adjudication_submission)
            )
            if adjudicator["role"] != "ADJUDICATOR":
                raise ValueError("identity_adjudicator_role_required")
            existing_names = {row["name"].casefold() for row in annotators}
            if adjudicator["name"].casefold() in existing_names:
                raise ValueError("identity_adjudicator_must_be_independent")
            annotators.append(adjudicator)
            review_status = "ADJUDICATED"
        else:
            if adjudication_submission:
                raise ValueError("identity_adjudication_not_required")
            review_status = "DOUBLE_BLIND_AGREED"

    ground_truth = _ground_truth(
        package,
        selected,
        annotators=annotators,
        review_status=review_status,
        disagreement_count=len(disagreements),
    )
    return {
        "schema": COMPILATION_SCHEMA,
        "status": "READY",
        "task_package_id": package.get("task_package_id"),
        "manifest_id": package.get("manifest_id"),
        "annotation_scope": ANNOTATION_SCOPE,
        "review_status": review_status,
        "annotators": annotators,
        "progress": _progress(total, total),
        "annotated_mention_count": len(selected),
        "cluster_count": len(as_list(ground_truth.get("clusters"))),
        "disagreement_count": len(disagreements),
        "disagreements": disagreements,
        "ground_truth": ground_truth,
        "ground_truth_import_allowed": True,
    }


__all__ = [
    "COMPILATION_SCHEMA",
    "SUBMISSION_SCHEMA",
    "TASK_PACKAGE_SCHEMA",
    "build_identity_annotation_task_package",
    "compile_identity_annotation_submissions",
]
