"""Operator entry points for blind identity annotation packages.

The operator is intentionally thin: package construction and partition comparison are
pure functions, while the existing benchmark workflow remains the only authority that
persists Ground Truth, rebuilds enterprise understanding and records measurements.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .._common import ROOT, _safe_project_id
from .identity_annotation_tasks import (
    build_identity_annotation_task_package,
    compile_identity_annotation_submissions,
)
from .identity_benchmark_workflow import import_identity_ground_truth
from .schema import as_dict, text

IMPORT_RESULT_SCHEMA = "qualibug.enterprise-identity-annotation-import-result.v1"


def _composition():
    from ..composition import (
        build_enterprise_business_knowledge_asset,
        load_enterprise_business_knowledge_asset,
    )

    return build_enterprise_business_knowledge_asset, load_enterprise_business_knowledge_asset


def _asset(project: str, root: Path) -> dict[str, Any]:
    build, load = _composition()
    asset = load(project, root)
    if not isinstance(asset, dict):
        asset = build(project, root)
    if not isinstance(asset, dict):
        raise RuntimeError("enterprise_identity_annotation_asset_unavailable")
    try:
        build_identity_annotation_task_package(asset)
    except ValueError:
        asset = build(project, root)
    if not isinstance(asset, dict):
        raise RuntimeError("enterprise_identity_annotation_asset_unavailable")
    return asset


def get_identity_annotation_task_package(
    project_id: str,
    root: Path | None = None,
) -> dict[str, Any]:
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    return build_identity_annotation_task_package(_asset(project, resolved_root))


def compile_and_import_identity_annotations(
    project_id: str,
    payload: dict[str, Any],
    *,
    actor: dict[str, Any],
    root: Path | None = None,
) -> dict[str, Any]:
    """Compile blind submissions and import only when their partition is resolved."""
    project = _safe_project_id(project_id)
    resolved_root = root or ROOT
    package = get_identity_annotation_task_package(project, resolved_root)
    primary = as_dict(
        payload.get("primary_submission")
        or payload.get("submission")
        or payload.get("primary")
    )
    if not primary:
        raise ValueError("identity_primary_annotation_submission_required")
    secondary = as_dict(
        payload.get("secondary_submission") or payload.get("secondary")
    )
    adjudication = as_dict(
        payload.get("adjudication_submission") or payload.get("adjudication")
    )
    if adjudication and not secondary:
        raise ValueError("identity_adjudication_requires_secondary_submission")

    compilation = compile_identity_annotation_submissions(
        package,
        primary,
        secondary_submission=secondary or None,
        adjudication_submission=adjudication or None,
    )
    if text(compilation.get("status")) != "READY":
        return {
            "schema": IMPORT_RESULT_SCHEMA,
            "status": "REVIEW_REQUIRED",
            "project_id": project,
            "task_package_id": package.get("task_package_id"),
            "manifest_id": package.get("manifest_id"),
            "compilation": compilation,
            "workspace": {},
            "ground_truth_imported": False,
        }

    ground_truth = as_dict(compilation.get("ground_truth"))
    workspace = import_identity_ground_truth(
        project,
        ground_truth,
        manifest_id=text(package.get("manifest_id")),
        actor=actor,
        root=resolved_root,
        rebuild=True,
    )
    return {
        "schema": IMPORT_RESULT_SCHEMA,
        "status": "IMPORTED",
        "project_id": project,
        "task_package_id": package.get("task_package_id"),
        "manifest_id": package.get("manifest_id"),
        "compilation": compilation,
        "workspace": workspace,
        "ground_truth_imported": True,
    }


__all__ = [
    "IMPORT_RESULT_SCHEMA",
    "compile_and_import_identity_annotations",
    "get_identity_annotation_task_package",
]
