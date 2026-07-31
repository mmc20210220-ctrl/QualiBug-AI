"""Transactional operator workflow for enterprise identity measurement.

The workflow validates blind annotations against the current manifest, persists only
externally supplied Ground Truth or quality policy, rebuilds through the canonical
knowledge composition root, and restores the previous file if rebuilding fails.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._common import ROOT, _safe_project_id
from .identity_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    QUALITY_POLICY_SCHEMA,
    _quality_policy,
    evaluate_identity_resolution,
)
from .identity_benchmark_repository import (
    append_identity_benchmark_audit,
    identity_benchmark_paths,
    load_identity_benchmark_audit,
    load_identity_ground_truth,
    load_identity_quality_policy,
    payload_fingerprint,
    restore_identity_benchmark_file,
    save_identity_ground_truth,
    save_identity_quality_policy,
    snapshot_identity_benchmark_file,
)
from .schema import as_dict, as_list, text

WORKSPACE_SCHEMA = "qualibug.enterprise-identity-benchmark-workspace.v1"


def _actor(actor: Any) -> dict[str, str]:
    row = as_dict(actor)
    name = text(row.get("name") or row.get("username") or row.get("actor_id"))
    if not name:
        raise ValueError("identity_benchmark_actor_required")
    return {
        "name": name,
        "role": text(row.get("role")),
        "tenant_id": text(row.get("tenant_id") or row.get("tenant")),
    }


def _composition():
    from ..composition import (
        build_enterprise_business_knowledge_asset,
        load_enterprise_business_knowledge_asset,
    )

    return build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset


def _asset(project: str, root: Path, *, rebuild: bool = False) -> dict[str, Any]:
    build, load = _composition()
    asset = build(project, root) if rebuild else load(project, root)
    if not isinstance(asset, dict):
        asset = build(project, root)
    if not isinstance(asset, dict):
        raise RuntimeError("enterprise_identity_benchmark_asset_unavailable")
    return asset


def _ground_truth_summary(payload: dict[str, Any]) -> dict[str, Any]:
    clusters = [dict(row) for row in as_list(payload.get("clusters")) if isinstance(row, dict)]
    mention_refs = {
        text(value)
        for row in clusters
        for value in [
            *as_list(row.get("member_refs")),
            *as_list(row.get("mention_refs")),
            *as_list(row.get("member_mention_ids")),
        ]
        if text(value)
    }
    return {
        "present": bool(payload),
        "schema": text(payload.get("schema")),
        "manifest_id": text(payload.get("manifest_id") or payload.get("annotation_manifest_id")),
        "annotation_scope": text(payload.get("annotation_scope")),
        "cluster_count": len(clusters),
        "annotated_mention_count": len(mention_refs),
        "fingerprint": payload_fingerprint(payload) if payload else "",
    }


def get_identity_benchmark_workspace(
    project_id: str,
    root: Path | None = None,
    *,
    rebuild: bool = False,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    asset = _asset(project, resolved_root, rebuild=rebuild)
    ground_truth = load_identity_ground_truth(project, resolved_root)
    quality_policy = load_identity_quality_policy(project, resolved_root)
    audit = load_identity_benchmark_audit(project, resolved_root)
    manifest = as_dict(asset.get("enterprise_identity_annotation_manifest"))
    benchmark = as_dict(asset.get("enterprise_identity_benchmark"))
    model = as_dict(asset.get("enterprise_understanding_model"))
    return {
        "schema": WORKSPACE_SCHEMA,
        "project_id": project,
        "manifest": manifest,
        "benchmark": benchmark,
        "identity_gate": as_dict(asset.get("enterprise_identity_gate")),
        "identity_quality_gate": as_dict(
            benchmark.get("quality_gate")
            or model.get("identity_quality_gate")
        ),
        "quality_policy": quality_policy,
        "ground_truth_summary": _ground_truth_summary(ground_truth),
        "audit": {
            **audit,
            "events": as_list(audit.get("events"))[-100:],
        },
        "workflow": {
            "annotation_is_blind": True,
            "ground_truth_requires_current_manifest": True,
            "ground_truth_is_closed_world": True,
            "rebuild_is_transactional": True,
            "product_output_may_be_ground_truth": False,
        },
    }


def _current_manifest(asset: dict[str, Any]) -> dict[str, Any]:
    manifest = as_dict(asset.get("enterprise_identity_annotation_manifest"))
    if not text(manifest.get("manifest_id")):
        raise ValueError("identity_annotation_manifest_unavailable")
    return manifest


def import_identity_ground_truth(
    project_id: str,
    ground_truth: dict[str, Any],
    *,
    manifest_id: str,
    actor: dict[str, Any],
    root: Path | None = None,
    rebuild: bool = True,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    operator = _actor(actor)
    payload = dict(as_dict(ground_truth))
    if text(payload.get("schema")) != GROUND_TRUTH_SCHEMA:
        raise ValueError("identity_ground_truth_schema_invalid")
    if text(payload.get("annotation_scope")) != ANNOTATION_SCOPE:
        raise ValueError("identity_ground_truth_annotation_scope_invalid")
    if bool(payload.get("ground_truth_generated_from_product_output")):
        raise ValueError("product_output_cannot_be_identity_ground_truth")

    current_asset = _asset(project, resolved_root)
    manifest = _current_manifest(current_asset)
    supplied_manifest_id = text(
        manifest_id
        or payload.get("manifest_id")
        or payload.get("annotation_manifest_id")
    )
    current_manifest_id = text(manifest.get("manifest_id"))
    if not supplied_manifest_id:
        raise ValueError("identity_ground_truth_manifest_id_required")
    if supplied_manifest_id != current_manifest_id:
        raise ValueError("identity_ground_truth_manifest_stale")
    payload["manifest_id"] = current_manifest_id

    resolution = as_dict(current_asset.get("enterprise_identity_resolution"))
    if not resolution:
        raise ValueError("enterprise_identity_resolution_unavailable")
    quality_policy = load_identity_quality_policy(project, resolved_root)
    preflight = evaluate_identity_resolution(
        resolution,
        payload,
        quality_policy=quality_policy,
    )
    if text(preflight.get("status")) != "MEASURED":
        raise ValueError(
            text(preflight.get("reason_code"))
            or "identity_ground_truth_not_measurable"
        )

    path = identity_benchmark_paths(project, resolved_root)["ground_truth"]
    previous = snapshot_identity_benchmark_file(path)
    fingerprint = payload_fingerprint(payload)
    save_identity_ground_truth(project, payload, resolved_root)
    try:
        fresh_asset = _asset(project, resolved_root, rebuild=rebuild)
        benchmark = as_dict(fresh_asset.get("enterprise_identity_benchmark"))
        if text(benchmark.get("status")) != "MEASURED":
            raise RuntimeError("identity_ground_truth_rebuild_not_measured")
    except Exception as exc:
        restore_identity_benchmark_file(path, previous)
        append_identity_benchmark_audit(
            project,
            {
                "event": "identity_ground_truth_import_rolled_back",
                "actor": operator,
                "manifest_id": current_manifest_id,
                "payload_fingerprint": fingerprint,
                "error_type": type(exc).__name__,
            },
            resolved_root,
        )
        raise

    append_identity_benchmark_audit(
        project,
        {
            "event": "identity_ground_truth_imported",
            "actor": operator,
            "manifest_id": current_manifest_id,
            "payload_fingerprint": fingerprint,
            "benchmark_id": benchmark.get("benchmark_id"),
            "benchmark_status": benchmark.get("status"),
            "quality_gate_status": as_dict(benchmark.get("quality_gate")).get("status"),
        },
        resolved_root,
    )
    return get_identity_benchmark_workspace(project, resolved_root)


def update_identity_quality_policy(
    project_id: str,
    quality_policy: dict[str, Any],
    *,
    actor: dict[str, Any],
    root: Path | None = None,
    rebuild: bool = True,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    operator = _actor(actor)
    payload = dict(as_dict(quality_policy))
    if text(payload.get("schema")) != QUALITY_POLICY_SCHEMA:
        raise ValueError("identity_quality_policy_schema_invalid")

    synthetic_benchmark = {
        "status": "MEASURED",
        "metrics": {
            "pairwise_precision": 1.0,
            "pairwise_recall": 1.0,
            "pairwise_f1": 1.0,
            "exact_cluster_match_rate": 1.0,
            "overmerge_rate": 0.0,
            "undermerge_rate": 0.0,
            "identity_error_unknown_coverage_rate": 1.0,
            "silent_identity_error_count": 0,
        },
    }
    validation_gate = _quality_policy(synthetic_benchmark, payload)
    if text(validation_gate.get("status")) == "INVALID_IDENTITY_QUALITY_POLICY":
        raise ValueError("identity_quality_policy_invalid")

    path = identity_benchmark_paths(project, resolved_root)["quality_policy"]
    previous = snapshot_identity_benchmark_file(path)
    fingerprint = payload_fingerprint(payload)
    save_identity_quality_policy(project, payload, resolved_root)
    try:
        _asset(project, resolved_root, rebuild=rebuild)
    except Exception as exc:
        restore_identity_benchmark_file(path, previous)
        append_identity_benchmark_audit(
            project,
            {
                "event": "identity_quality_policy_update_rolled_back",
                "actor": operator,
                "payload_fingerprint": fingerprint,
                "error_type": type(exc).__name__,
            },
            resolved_root,
        )
        raise

    workspace = get_identity_benchmark_workspace(project, resolved_root)
    append_identity_benchmark_audit(
        project,
        {
            "event": "identity_quality_policy_updated",
            "actor": operator,
            "payload_fingerprint": fingerprint,
            "quality_gate_status": as_dict(
                workspace.get("identity_quality_gate")
            ).get("status"),
            "enforced": bool(payload.get("enforce")),
        },
        resolved_root,
    )
    return get_identity_benchmark_workspace(project, resolved_root)


__all__ = [
    "WORKSPACE_SCHEMA",
    "get_identity_benchmark_workspace",
    "import_identity_ground_truth",
    "update_identity_quality_policy",
]
