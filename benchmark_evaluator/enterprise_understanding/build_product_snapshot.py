"""Build one evaluator product snapshot through the existing product authorities.

This module is executed as a dedicated product-only subprocess. It reads one public source
manifest, calls the existing knowledge ingestion API and the single explicit composition root,
then captures the persisted finalized asset. It never accepts or loads a Ground Truth path.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Callable

from benchmark_evaluator.scored_run_comparison import _fingerprint

from .capture_product_asset import capture_finalized_product_asset

SOURCE_MANIFEST_SCHEMA = "qualibug.enterprise-understanding-source-manifest.v1"
PRODUCT_PHASE_RECEIPT_SCHEMA = "qualibug.enterprise-understanding-product-phase.v1"


class ProductPhaseError(RuntimeError):
    """The isolated product phase could not produce a trustworthy finalized asset."""


def _git_blob_sha(data: bytes) -> str:
    header = f"blob {len(data)}\0".encode("utf-8")
    return hashlib.sha1(header + data).hexdigest()


def _portable_source_ref(value: Any) -> str:
    """Return one workspace-independent source reference from the public manifest."""
    reference = str(value or "").replace("\\", "/").strip()
    if not reference:
        raise ProductPhaseError("source_manifest_path_empty")
    path = Path(reference)
    if path.is_absolute() or reference.startswith("/"):
        raise ProductPhaseError(f"source_manifest_path_not_portable:{reference}")
    parts = [part for part in reference.split("/") if part]
    if not parts or any(part in {".", ".."} for part in parts):
        raise ProductPhaseError(f"source_manifest_path_not_portable:{reference}")
    return "/".join(parts)


def _load_manifest(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ProductPhaseError(f"source_manifest_missing:{path}") from exc
    except json.JSONDecodeError as exc:
        raise ProductPhaseError(f"source_manifest_invalid_json:{path}:{exc}") from exc
    if not isinstance(value, dict):
        raise ProductPhaseError("source_manifest_root_not_object")
    if str(value.get("schema") or "") != SOURCE_MANIFEST_SCHEMA:
        raise ProductPhaseError("source_manifest_schema_invalid")
    if value.get("product_phase_may_load_ground_truth") is not False:
        raise ProductPhaseError("source_manifest_ground_truth_boundary_not_fail_closed")
    sources = value.get("sources")
    if not isinstance(sources, list) or not sources:
        raise ProductPhaseError("source_manifest_sources_empty")
    return value


def _resolve_sources(
    manifest: dict[str, Any], product_root: Path
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Resolve public sources into canonical ingestion envelopes.

    ``external_ref`` is the stable source identity declared by the manifest. The absolute
    workspace path is transport-only and is never persisted as enterprise source identity.
    """
    excluded = {
        _portable_source_ref(value)
        for value in manifest.get("excluded_from_product_phase") or []
        if str(value or "").strip()
    }
    documents: list[dict[str, Any]] = []
    receipts: list[dict[str, Any]] = []
    seen_paths: set[Path] = set()
    seen_refs: set[str] = set()
    for index, raw in enumerate(manifest.get("sources") or []):
        if not isinstance(raw, dict):
            raise ProductPhaseError(f"source_manifest_entry_not_object:{index}")
        source_ref = _portable_source_ref(raw.get("path"))
        source_type = str(raw.get("source_type") or "").strip()
        expected_sha = str(raw.get("blob_sha") or "").strip()
        if not source_type or not expected_sha:
            raise ProductPhaseError(f"source_manifest_entry_incomplete:{index}")
        if source_ref in excluded or "ground_truth" in source_ref.lower():
            raise ProductPhaseError(f"ground_truth_path_in_product_sources:{source_ref}")
        resolved = (product_root / source_ref).resolve()
        try:
            resolved.relative_to(product_root)
        except ValueError as exc:
            raise ProductPhaseError(f"source_outside_product_root:{source_ref}") from exc
        if resolved in seen_paths or source_ref in seen_refs:
            raise ProductPhaseError(f"duplicate_source_path:{source_ref}")
        if not resolved.is_file():
            raise ProductPhaseError(f"source_file_missing:{source_ref}")
        data = resolved.read_bytes()
        actual_sha = _git_blob_sha(data)
        if actual_sha != expected_sha:
            raise ProductPhaseError(
                f"source_blob_sha_mismatch:{source_ref}:expected={expected_sha}:actual={actual_sha}"
            )
        seen_paths.add(resolved)
        seen_refs.add(source_ref)
        documents.append(
            {
                "file_path": str(resolved),
                "filename": resolved.name,
                "source_type": source_type,
                "external_ref": source_ref,
                "tags": ["source-backed-benchmark"],
            }
        )
        receipts.append(
            {
                "path": source_ref,
                "source_ref": source_ref,
                "source_type": source_type,
                "blob_sha": actual_sha,
                "size": len(data),
                "source_identity_authority": "SOURCE_MANIFEST_EXTERNAL_REF",
                "absolute_workspace_path_persisted_as_identity": False,
            }
        )
    return documents, receipts


def _assert_clean_project_workspace(workspace_root: Path, project_id: str) -> None:
    candidates = (
        workspace_root / "platform_workspace" / project_id,
        workspace_root / "platform_outputs" / project_id,
    )
    dirty = [str(path) for path in candidates if path.exists()]
    if dirty:
        raise ProductPhaseError(
            "product_phase_workspace_not_clean:" + ",".join(dirty)
        )


def _default_product_authorities():
    # Delayed imports are deliberate: importing evaluator modules does not load product runtime.
    from ai_test_asset_center.enterprise_knowledge_center._crud import (
        ingest_enterprise_knowledge_documents,
    )
    from ai_test_asset_center.enterprise_knowledge_center.composition import (
        build_enterprise_business_knowledge_asset,
    )

    return ingest_enterprise_knowledge_documents, build_enterprise_business_knowledge_asset


def build_isolated_product_snapshot(
    *,
    project_id: str,
    product_root: str | Path,
    workspace_root: str | Path,
    manifest_path: str | Path,
    asset_output_path: str | Path,
    receipt_output_path: str | Path,
    authorities: tuple[Callable[..., dict[str, Any]], Callable[..., dict[str, Any]]] | None = None,
) -> dict[str, Any]:
    """Ingest public sources, build through the existing mainline and capture the final asset."""
    project = str(project_id or "").strip()
    if not project:
        raise ProductPhaseError("project_id_required")
    product = Path(product_root).resolve()
    workspace = Path(workspace_root).resolve()
    manifest_file = Path(manifest_path).resolve()
    if not product.is_dir():
        raise ProductPhaseError(f"product_root_missing:{product}")
    workspace.mkdir(parents=True, exist_ok=True)
    _assert_clean_project_workspace(workspace, project)

    manifest = _load_manifest(manifest_file)
    manifest_project = str(manifest.get("project_id") or "").strip()
    if manifest_project and manifest_project != project:
        raise ProductPhaseError(
            f"source_manifest_project_mismatch:{manifest_project}:{project}"
        )
    source_documents, source_receipts = _resolve_sources(manifest, product)
    ingest, build = authorities or _default_product_authorities()
    ingest_receipt = ingest(
        project,
        source_documents,
        root=workspace,
        actor={"name": "enterprise_understanding_evaluator", "role": "project_owner"},
    )
    if not isinstance(ingest_receipt, dict) or not bool(ingest_receipt.get("ok")):
        raise ProductPhaseError(
            "public_source_ingest_failed:"
            + json.dumps(ingest_receipt, ensure_ascii=False, sort_keys=True, default=str)[:1000]
        )
    created = [
        dict(row)
        for row in ingest_receipt.get("created") or []
        if isinstance(row, dict)
    ]
    source_ref_by_id = {
        str(row.get("source_id") or ""): str(row.get("external_ref") or "")
        for row in created
        if str(row.get("source_id") or "")
    }
    expected_refs = {row["source_ref"] for row in source_receipts}
    persisted_refs = {value for value in source_ref_by_id.values() if value}
    if persisted_refs != expected_refs:
        raise ProductPhaseError(
            "source_manifest_external_ref_not_preserved:"
            + json.dumps(
                {
                    "expected": sorted(expected_refs),
                    "persisted": sorted(persisted_refs),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )

    asset = build(project, workspace, {"probe_limit": 0})
    if not isinstance(asset, dict) or not isinstance(
        asset.get("enterprise_understanding_model"), dict
    ):
        raise ProductPhaseError("composition_root_did_not_return_understanding_model")
    capture_receipt = capture_finalized_product_asset(
        project_id=project,
        root=workspace,
        output_path=asset_output_path,
    )
    receipt = {
        "schema_version": PRODUCT_PHASE_RECEIPT_SCHEMA,
        "status": "PASS",
        "project_id": project,
        "source_manifest_path": str(manifest_file),
        "source_manifest_fingerprint": _fingerprint(manifest),
        "source_receipts": source_receipts,
        "source_ref_by_source_id": source_ref_by_id,
        "source_identity_authority": "SOURCE_INVENTORY_EXTERNAL_REF",
        "source_manifest_external_refs_preserved": True,
        "absolute_workspace_paths_persisted_as_identity": False,
        "ingest_created_count": len(created),
        "ingest_duplicate_count": len(ingest_receipt.get("duplicates") or []),
        "composition_authority": (
            "ai_test_asset_center.enterprise_knowledge_center.composition."
            "build_enterprise_business_knowledge_asset"
        ),
        "probe_limit": 0,
        "ground_truth_loaded": False,
        "ground_truth_path_received": False,
        "hidden_answer_key_accessed": False,
        "capture_receipt": capture_receipt,
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    target = Path(receipt_output_path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Build one isolated enterprise-understanding product snapshot from public sources."
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--asset-output", required=True)
    parser.add_argument("--receipt-output", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = build_isolated_product_snapshot(
            project_id=args.project,
            product_root=args.product_root,
            workspace_root=args.workspace_root,
            manifest_path=args.manifest,
            asset_output_path=args.asset_output,
            receipt_output_path=args.receipt_output,
        )
    except ProductPhaseError as exc:
        print(json.dumps({"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "PASS", "receipt": receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SOURCE_MANIFEST_SCHEMA",
    "PRODUCT_PHASE_RECEIPT_SCHEMA",
    "ProductPhaseError",
    "build_isolated_product_snapshot",
]
