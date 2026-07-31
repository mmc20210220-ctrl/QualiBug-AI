"""Build two source versions through the existing product authorities.

This product-only subprocess proves source-version lifecycle without receiving Ground
Truth. Phase one ingests and builds one finalized asset. Phase two re-ingests changed
bytes under the same stable source occurrence reference and calls the same composition
root again. The source-occurrence authority performs supersession; the composition root
carries only durable implicit-rule governance from the prior finalized asset.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any, Callable

from benchmark_evaluator.scored_run_comparison import _fingerprint

from .build_product_snapshot import (
    PRODUCT_PHASE_RECEIPT_SCHEMA,
    ProductPhaseError,
    _assert_clean_project_workspace,
    _default_product_authorities,
    _load_manifest,
    _occurrence_rows,
    _resolve_sources,
)
from .capture_product_asset import capture_finalized_product_asset

VERSIONED_PRODUCT_PHASE_SCHEMA = (
    "qualibug.enterprise-understanding-versioned-product-phase.v1"
)


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _manifest_project(manifest: dict[str, Any], project: str, label: str) -> None:
    declared = str(manifest.get("project_id") or "").strip()
    if declared and declared != project:
        raise ProductPhaseError(
            f"{label}_source_manifest_project_mismatch:{declared}:{project}"
        )


def _ingest_phase(
    *,
    project: str,
    workspace: Path,
    source_documents: list[dict[str, Any]],
    source_receipts: list[dict[str, Any]],
    ingest: Callable[..., dict[str, Any]],
    phase: str,
) -> dict[str, Any]:
    receipt = ingest(
        project,
        source_documents,
        root=workspace,
        actor={
            "name": "enterprise_understanding_versioned_evaluator",
            "role": "project_owner",
        },
    )
    if not isinstance(receipt, dict) or not bool(receipt.get("ok")):
        raise ProductPhaseError(
            f"{phase}_public_source_ingest_failed:"
            + json.dumps(receipt, ensure_ascii=False, sort_keys=True, default=str)[:1000]
        )
    occurrences = _occurrence_rows(receipt)
    by_ref = {
        str(row.get("source_ref") or ""): dict(row)
        for row in occurrences
        if str(row.get("source_ref") or "").strip()
    }
    expected_refs = {str(row.get("source_ref") or "") for row in source_receipts}
    if set(by_ref) != expected_refs:
        raise ProductPhaseError(
            f"{phase}_source_manifest_occurrences_not_preserved:"
            + json.dumps(
                {"expected": sorted(expected_refs), "persisted": sorted(by_ref)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    if any(
        not str(row.get("source_occurrence_id") or "").strip()
        or not str(row.get("canonical_source_id") or row.get("source_id") or "").strip()
        for row in by_ref.values()
    ):
        raise ProductPhaseError(f"{phase}_source_occurrence_identity_incomplete")
    return {
        "ingest_receipt": receipt,
        "occurrences_by_ref": by_ref,
        "source_receipts": source_receipts,
    }


def _assert_version_transition(
    phase_one: dict[str, Any], phase_two: dict[str, Any]
) -> list[dict[str, Any]]:
    before = phase_one["occurrences_by_ref"]
    after = phase_two["occurrences_by_ref"]
    if set(before) != set(after):
        raise ProductPhaseError(
            "versioned_manifest_source_ref_set_changed:"
            + json.dumps(
                {"phase_one": sorted(before), "phase_two": sorted(after)},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
    first_receipts = {
        str(row.get("source_ref") or ""): row
        for row in phase_one["source_receipts"]
    }
    second_receipts = {
        str(row.get("source_ref") or ""): row
        for row in phase_two["source_receipts"]
    }
    transitions: list[dict[str, Any]] = []
    for source_ref in sorted(before):
        old = before[source_ref]
        new = after[source_ref]
        old_version = int(old.get("version") or 0)
        new_version = int(new.get("version") or 0)
        old_occurrence = str(old.get("source_occurrence_id") or "")
        new_occurrence = str(new.get("source_occurrence_id") or "")
        old_blob = str(first_receipts[source_ref].get("blob_sha") or "")
        new_blob = str(second_receipts[source_ref].get("blob_sha") or "")
        if old_blob == new_blob:
            raise ProductPhaseError(
                f"versioned_source_blob_did_not_change:{source_ref}"
            )
        if old_occurrence == new_occurrence:
            raise ProductPhaseError(
                f"versioned_source_occurrence_identity_did_not_change:{source_ref}"
            )
        if new_version <= old_version:
            raise ProductPhaseError(
                f"versioned_source_occurrence_version_did_not_advance:{source_ref}:{old_version}:{new_version}"
            )
        transitions.append(
            {
                "source_ref": source_ref,
                "phase_one_fixture_path": first_receipts[source_ref].get("path"),
                "phase_two_fixture_path": second_receipts[source_ref].get("path"),
                "phase_one_blob_sha": old_blob,
                "phase_two_blob_sha": new_blob,
                "phase_one_source_occurrence_id": old_occurrence,
                "phase_two_source_occurrence_id": new_occurrence,
                "phase_one_canonical_source_id": old.get("canonical_source_id")
                or old.get("source_id"),
                "phase_two_canonical_source_id": new.get("canonical_source_id")
                or new.get("source_id"),
                "phase_one_version": old_version,
                "phase_two_version": new_version,
                "source_occurrence_supersession_authority": True,
            }
        )
    return transitions


def _assert_asset(asset: Any, phase: str) -> dict[str, Any]:
    if not isinstance(asset, dict) or not isinstance(
        asset.get("enterprise_understanding_model"), dict
    ):
        raise ProductPhaseError(
            f"{phase}_composition_root_did_not_return_understanding_model"
        )
    return asset


def build_versioned_product_snapshot(
    *,
    project_id: str,
    product_root: str | Path,
    workspace_root: str | Path,
    phase_one_manifest_path: str | Path,
    phase_two_manifest_path: str | Path,
    phase_one_asset_output_path: str | Path,
    final_asset_output_path: str | Path,
    receipt_output_path: str | Path,
    authorities: tuple[
        Callable[..., dict[str, Any]], Callable[..., dict[str, Any]]
    ]
    | None = None,
) -> dict[str, Any]:
    """Build v1 then v2 without exposing evaluator truth to either product phase."""

    project = str(project_id or "").strip()
    if not project:
        raise ProductPhaseError("project_id_required")
    product = Path(product_root).resolve()
    workspace = Path(workspace_root).resolve()
    phase_one_manifest_file = Path(phase_one_manifest_path).resolve()
    phase_two_manifest_file = Path(phase_two_manifest_path).resolve()
    if not product.is_dir():
        raise ProductPhaseError(f"product_root_missing:{product}")
    workspace.mkdir(parents=True, exist_ok=True)
    _assert_clean_project_workspace(workspace, project)

    manifest_one = _load_manifest(phase_one_manifest_file)
    manifest_two = _load_manifest(phase_two_manifest_file)
    _manifest_project(manifest_one, project, "phase_one")
    _manifest_project(manifest_two, project, "phase_two")
    documents_one, receipts_one = _resolve_sources(manifest_one, product)
    documents_two, receipts_two = _resolve_sources(manifest_two, product)
    ingest, build = authorities or _default_product_authorities()

    phase_one = _ingest_phase(
        project=project,
        workspace=workspace,
        source_documents=documents_one,
        source_receipts=receipts_one,
        ingest=ingest,
        phase="phase_one",
    )
    asset_one = _assert_asset(
        build(project, workspace, {"probe_limit": 0}), "phase_one"
    )
    capture_one = capture_finalized_product_asset(
        project_id=project,
        root=workspace,
        output_path=phase_one_asset_output_path,
    )

    phase_two = _ingest_phase(
        project=project,
        workspace=workspace,
        source_documents=documents_two,
        source_receipts=receipts_two,
        ingest=ingest,
        phase="phase_two",
    )
    transitions = _assert_version_transition(phase_one, phase_two)
    asset_two = _assert_asset(
        build(project, workspace, {"probe_limit": 0}), "phase_two"
    )
    capture_two = capture_finalized_product_asset(
        project_id=project,
        root=workspace,
        output_path=final_asset_output_path,
    )

    carry = (
        asset_two.get("implicit_rule_governance_carry_forward_receipt")
        if isinstance(
            asset_two.get("implicit_rule_governance_carry_forward_receipt"), dict
        )
        else {}
    )
    if carry.get("captured_before_base_rebuild") is not True:
        raise ProductPhaseError(
            "phase_two_implicit_rule_governance_not_captured_before_base_rebuild"
        )
    if carry.get("prior_rule_library_reused") is not False:
        raise ProductPhaseError(
            "phase_two_prior_rule_library_was_reused_as_current_authority"
        )

    receipt = {
        "schema_version": VERSIONED_PRODUCT_PHASE_SCHEMA,
        "product_phase_schema_version": PRODUCT_PHASE_RECEIPT_SCHEMA,
        "status": "PASS",
        "project_id": project,
        "phase_one_manifest_path": str(phase_one_manifest_file),
        "phase_two_manifest_path": str(phase_two_manifest_file),
        "phase_one_manifest_fingerprint": _fingerprint(manifest_one),
        "phase_two_manifest_fingerprint": _fingerprint(manifest_two),
        "phase_one_asset_fingerprint": _fingerprint(asset_one),
        "phase_two_asset_fingerprint": _fingerprint(asset_two),
        "source_version_transitions": transitions,
        "source_identity_authority": "SOURCE_OCCURRENCE_REGISTRY",
        "source_occurrence_supersession_used": True,
        "composition_authority": (
            "ai_test_asset_center.enterprise_knowledge_center.composition."
            "build_enterprise_business_knowledge_asset"
        ),
        "composition_invocation_count": 2,
        "phase_two_governance_carry_forward_receipt": carry,
        "phase_one_capture_receipt": capture_one,
        "phase_two_capture_receipt": capture_two,
        "ground_truth_loaded": False,
        "ground_truth_path_received": False,
        "hidden_answer_key_accessed": False,
        "product_model_can_self_label_true_or_false": False,
        "phase_one_asset_output_path": str(Path(phase_one_asset_output_path).resolve()),
        "final_asset_output_path": str(Path(final_asset_output_path).resolve()),
    }
    receipt["receipt_fingerprint"] = _fingerprint(receipt)
    _write_json(Path(receipt_output_path).resolve(), receipt)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Build two public source versions through the existing product mainline."
        )
    )
    parser.add_argument("--project", required=True)
    parser.add_argument("--product-root", required=True)
    parser.add_argument("--workspace-root", required=True)
    parser.add_argument("--phase-one-manifest", required=True)
    parser.add_argument("--phase-two-manifest", required=True)
    parser.add_argument("--phase-one-asset-output", required=True)
    parser.add_argument("--final-asset-output", required=True)
    parser.add_argument("--receipt-output", required=True)
    args = parser.parse_args(argv)
    try:
        receipt = build_versioned_product_snapshot(
            project_id=args.project,
            product_root=args.product_root,
            workspace_root=args.workspace_root,
            phase_one_manifest_path=args.phase_one_manifest,
            phase_two_manifest_path=args.phase_two_manifest,
            phase_one_asset_output_path=args.phase_one_asset_output,
            final_asset_output_path=args.final_asset_output,
            receipt_output_path=args.receipt_output,
        )
    except ProductPhaseError as exc:
        print(
            json.dumps(
                {"status": "BLOCKED", "reason": str(exc)}, ensure_ascii=False
            )
        )
        return 2
    print(json.dumps({"status": "PASS", "receipt": receipt}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "VERSIONED_PRODUCT_PHASE_SCHEMA",
    "build_versioned_product_snapshot",
]
