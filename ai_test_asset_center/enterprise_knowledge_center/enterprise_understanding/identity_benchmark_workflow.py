"""Transactional operator workflow for enterprise identity measurement.

Blind annotations, durable quality policy, versioned measurements and exact evidence
errors all pass through the canonical knowledge composition root. Input and history
files roll back together when rebuild or snapshot persistence fails.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._common import ROOT, _safe_project_id
from .._utils import _now
from ..transaction_lock import knowledge_transaction
from .identity_benchmark import (
    ANNOTATION_SCOPE,
    GROUND_TRUTH_SCHEMA,
    QUALITY_POLICY_SCHEMA,
    _quality_policy,
    evaluate_identity_resolution,
)
from .identity_benchmark_regression import (
    build_identity_benchmark_snapshot,
    build_identity_error_queue,
    evaluate_identity_benchmark_regression,
)
from .identity_benchmark_repository import (
    append_identity_benchmark_audit,
    append_identity_benchmark_snapshot,
    identity_benchmark_paths,
    load_identity_benchmark_audit,
    load_identity_benchmark_history,
    load_identity_ground_truth,
    load_identity_quality_policy,
    payload_fingerprint,
    restore_identity_benchmark_file,
    save_identity_ground_truth,
    save_identity_quality_policy,
    snapshot_identity_benchmark_file,
)
from .schema import as_dict, as_list, text

WORKSPACE_SCHEMA = "qualibug.enterprise-identity-benchmark-workspace.v2"


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
    if isinstance(asset, dict) and not rebuild:
        manifest = as_dict(asset.get("enterprise_identity_annotation_manifest"))
        resolution = as_dict(asset.get("enterprise_identity_resolution"))
        if not text(manifest.get("manifest_id")) or not resolution:
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


def _snapshot_summary(snapshot: dict[str, Any]) -> dict[str, Any]:
    return {
        key: value
        for key, value in snapshot.items()
        if key != "errors"
    }


def _history_projection(
    asset: dict[str, Any],
    *,
    project: str,
    root: Path,
) -> dict[str, Any]:
    history = load_identity_benchmark_history(project, root)
    snapshots = [
        dict(row)
        for row in as_list(history.get("snapshots"))
        if isinstance(row, dict)
    ]
    benchmark = as_dict(asset.get("enterprise_identity_benchmark"))
    regression = as_dict(benchmark.get("regression"))
    baseline_id = text(regression.get("baseline_snapshot_id"))
    baseline = next(
        (
            row
            for row in reversed(snapshots)
            if text(row.get("snapshot_id")) == baseline_id
        ),
        {},
    )
    error_queue = build_identity_error_queue(benchmark, baseline)
    return {
        "schema": history.get("schema"),
        "snapshot_count": len(snapshots),
        "latest_snapshot": (
            _snapshot_summary(snapshots[-1]) if snapshots else {}
        ),
        "comparable_baseline": _snapshot_summary(baseline) if baseline else {},
        "snapshots": [_snapshot_summary(row) for row in snapshots[-50:]],
        "error_queue": error_queue,
        "comparison_contract": (
            "SAME_MANIFEST_ID_AND_SAME_EXTERNAL_GROUND_TRUTH_FINGERPRINT"
        ),
        "annotation_change_is_not_regression": True,
    }


def _record_snapshot(
    asset: dict[str, Any],
    *,
    project: str,
    root: Path,
    actor: dict[str, Any],
    trigger: str,
) -> dict[str, Any]:
    snapshot = build_identity_benchmark_snapshot(
        asset,
        trigger=trigger,
        actor=actor,
        recorded_at_utc=_now(),
    )
    if not snapshot:
        return {}
    return append_identity_benchmark_snapshot(project, snapshot, root)


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
    history = _history_projection(
        asset,
        project=project,
        root=resolved_root,
    )
    return {
        "schema": WORKSPACE_SCHEMA,
        "project_id": project,
        "manifest": manifest,
        "benchmark": benchmark,
        "regression": as_dict(benchmark.get("regression")),
        "identity_gate": as_dict(asset.get("enterprise_identity_gate")),
        "identity_quality_gate": as_dict(
            benchmark.get("quality_gate")
            or model.get("identity_quality_gate")
        ),
        "quality_policy": quality_policy,
        "ground_truth_summary": _ground_truth_summary(ground_truth),
        "history": history,
        "error_queue": as_dict(history.get("error_queue")),
        "audit": {
            **audit,
            "events": as_list(audit.get("events"))[-100:],
        },
        "workflow": {
            "annotation_is_blind": True,
            "ground_truth_requires_current_manifest": True,
            "ground_truth_is_closed_world": True,
            "rebuild_is_transactional": True,
            "history_is_versioned": True,
            "regression_requires_same_manifest_and_ground_truth": True,
            "knowledge_transaction_lease_reused": True,
            "product_output_may_be_ground_truth": False,
        },
    }


def _current_manifest(asset: dict[str, Any]) -> dict[str, Any]:
    manifest = as_dict(asset.get("enterprise_identity_annotation_manifest"))
    if not text(manifest.get("manifest_id")):
        raise ValueError("identity_annotation_manifest_unavailable")
    return manifest


def _validate_regression_policy(payload: dict[str, Any]) -> None:
    thresholds = as_dict(payload.get("regression_thresholds"))
    if not thresholds:
        if bool(payload.get("enforce_regression")):
            raise ValueError("identity_regression_thresholds_required_when_enforced")
        return
    metrics = {
        "pairwise_precision": 1.0,
        "pairwise_recall": 1.0,
        "pairwise_f1": 1.0,
        "exact_cluster_match_rate": 1.0,
        "overmerge_rate": 0.0,
        "undermerge_rate": 0.0,
        "identity_error_unknown_coverage_rate": 1.0,
        "silent_identity_error_count": 0,
    }
    synthetic_asset = {
        "enterprise_identity_annotation_manifest": {"manifest_id": "validation"},
        "enterprise_identity_benchmark_repository_receipt": {
            "ground_truth_fingerprint": "validation"
        },
        "enterprise_identity_benchmark_history": {
            "snapshots": [
                {
                    "schema": "qualibug.enterprise-identity-benchmark-snapshot.v1",
                    "snapshot_id": "validation-baseline",
                    "measurement_status": "MEASURED",
                    "manifest_id": "validation",
                    "ground_truth_fingerprint": "validation",
                    "metrics": metrics,
                }
            ]
        },
    }
    gate = evaluate_identity_benchmark_regression(
        synthetic_asset,
        {"status": "MEASURED", "metrics": metrics},
        payload,
    )
    if text(gate.get("status")) == "INVALID_IDENTITY_REGRESSION_POLICY":
        raise ValueError("identity_regression_policy_invalid")


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

    with knowledge_transaction(
        resolved_root,
        project,
        operation="import_identity_ground_truth",
        actor=operator,
    ):
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

        paths = identity_benchmark_paths(project, resolved_root)
        ground_truth_path = paths["ground_truth"]
        history_path = paths["history"]
        previous_ground_truth = snapshot_identity_benchmark_file(ground_truth_path)
        previous_history = snapshot_identity_benchmark_file(history_path)
        fingerprint = payload_fingerprint(payload)
        save_identity_ground_truth(project, payload, resolved_root)
        try:
            fresh_asset = _asset(project, resolved_root, rebuild=rebuild)
            benchmark = as_dict(fresh_asset.get("enterprise_identity_benchmark"))
            if text(benchmark.get("status")) != "MEASURED":
                raise RuntimeError("identity_ground_truth_rebuild_not_measured")
            snapshot = _record_snapshot(
                fresh_asset,
                project=project,
                root=resolved_root,
                actor=operator,
                trigger="GROUND_TRUTH_IMPORT",
            )
            if not snapshot:
                raise RuntimeError("identity_ground_truth_snapshot_not_recorded")
        except Exception as exc:
            restore_identity_benchmark_file(ground_truth_path, previous_ground_truth)
            restore_identity_benchmark_file(history_path, previous_history)
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
                "snapshot_id": snapshot.get("snapshot_id"),
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
    _validate_regression_policy(payload)

    with knowledge_transaction(
        resolved_root,
        project,
        operation="update_identity_quality_policy",
        actor=operator,
    ):
        paths = identity_benchmark_paths(project, resolved_root)
        policy_path = paths["quality_policy"]
        history_path = paths["history"]
        previous_policy = snapshot_identity_benchmark_file(policy_path)
        previous_history = snapshot_identity_benchmark_file(history_path)
        fingerprint = payload_fingerprint(payload)
        save_identity_quality_policy(project, payload, resolved_root)
        try:
            fresh_asset = _asset(project, resolved_root, rebuild=rebuild)
            snapshot = _record_snapshot(
                fresh_asset,
                project=project,
                root=resolved_root,
                actor=operator,
                trigger="QUALITY_POLICY_UPDATE",
            )
        except Exception as exc:
            restore_identity_benchmark_file(policy_path, previous_policy)
            restore_identity_benchmark_file(history_path, previous_history)
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

        benchmark = as_dict(fresh_asset.get("enterprise_identity_benchmark"))
        append_identity_benchmark_audit(
            project,
            {
                "event": "identity_quality_policy_updated",
                "actor": operator,
                "payload_fingerprint": fingerprint,
                "quality_gate_status": as_dict(
                    benchmark.get("quality_gate")
                ).get("status"),
                "regression_gate_status": as_dict(
                    benchmark.get("regression")
                ).get("status"),
                "enforced": bool(payload.get("enforce")),
                "regression_enforced": bool(payload.get("enforce_regression")),
                "snapshot_id": snapshot.get("snapshot_id") if snapshot else "",
            },
            resolved_root,
        )
    return get_identity_benchmark_workspace(project, resolved_root)


def run_identity_benchmark(
    project_id: str,
    *,
    actor: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    operator = _actor(actor)
    if not load_identity_ground_truth(project, resolved_root):
        raise ValueError("identity_ground_truth_required_before_remeasurement")

    with knowledge_transaction(
        resolved_root,
        project,
        operation="run_identity_benchmark",
        actor=operator,
    ):
        history_path = identity_benchmark_paths(project, resolved_root)["history"]
        previous_history = snapshot_identity_benchmark_file(history_path)
        try:
            fresh_asset = _asset(project, resolved_root, rebuild=True)
            benchmark = as_dict(fresh_asset.get("enterprise_identity_benchmark"))
            if text(benchmark.get("status")) != "MEASURED":
                raise ValueError(
                    text(benchmark.get("reason_code"))
                    or "identity_benchmark_not_measured"
                )
            snapshot = _record_snapshot(
                fresh_asset,
                project=project,
                root=resolved_root,
                actor=operator,
                trigger="MANUAL_REMEASURE",
            )
            if not snapshot:
                raise RuntimeError("identity_benchmark_snapshot_not_recorded")
        except Exception:
            restore_identity_benchmark_file(history_path, previous_history)
            raise

        append_identity_benchmark_audit(
            project,
            {
                "event": "identity_benchmark_remeasured",
                "actor": operator,
                "snapshot_id": snapshot.get("snapshot_id"),
                "benchmark_id": benchmark.get("benchmark_id"),
                "quality_gate_status": as_dict(
                    benchmark.get("quality_gate")
                ).get("status"),
                "regression_gate_status": as_dict(
                    benchmark.get("regression")
                ).get("status"),
            },
            resolved_root,
        )
    return get_identity_benchmark_workspace(project, resolved_root)


__all__ = [
    "WORKSPACE_SCHEMA",
    "get_identity_benchmark_workspace",
    "import_identity_ground_truth",
    "run_identity_benchmark",
    "update_identity_quality_policy",
]
