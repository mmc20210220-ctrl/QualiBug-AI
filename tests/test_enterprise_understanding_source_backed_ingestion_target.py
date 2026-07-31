from __future__ import annotations

import json
import subprocess
from pathlib import Path

from benchmark_evaluator.enterprise_understanding.run_source_backed_workflow import (
    run_source_backed_understanding_workflow,
)


SOURCE_REF = "projects/source-backed-document-demo/input/rules.md"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def _ground_truth() -> dict:
    return {
        "schema": "qualibug.enterprise-understanding-ground-truth.v1",
        "project_id": "source-backed-document-demo",
        "scope_complete": False,
        "business_objects": [
            {
                "ground_truth_id": "gt:object:ticket",
                "canonical_name": "工单",
                "criticality": "P0",
                "source_refs": ["source:rules"],
                "annotation_status": "CONFIRMED",
            }
        ],
        "actors": [],
        "operations": [],
        "object_relations": [],
        "lifecycles": [],
        "state_transitions": [],
        "business_rules": [],
        "business_behaviors": [],
        "conflicts": [],
        "expected_unknowns": [],
        "bug_dependencies": [],
    }


def _asset() -> dict:
    locator = "rules.md#line=1"
    return {
        "source_inventory": [
            {
                "source_id": "source:rules",
                "external_ref": SOURCE_REF,
                "status": "active",
            }
        ],
        "document_structure_assets": {
            "source_count": 1,
            "items": [
                {
                    "source_id": "source:rules",
                    "filename": "rules.md",
                    "format": "markdown",
                    "blocks": [
                        {
                            "block_id": "block:1",
                            "type": "PARAGRAPH",
                            "region": "body",
                            "order": 1,
                            "text": "客服可以查看工单",
                            "source_hash": "sha256:rules",
                            "source_locator": locator,
                            "evidence_address": {
                                "source_locator": locator,
                                "address_kind": "EXACT_SOURCE_LOCATOR",
                            },
                        }
                    ],
                    "structure_receipt": {"status": "COMPLETE"},
                    "evidence_closure_receipt": {
                        "status": "PASS",
                        "source_id": "source:rules",
                        "filename": "rules.md",
                        "source_hash": "sha256:rules",
                        "formal_authority_block_count": 1,
                        "source_hash_bound_block_count": 1,
                        "traceable_authority_block_count": 1,
                        "exact_address_authority_block_count": 1,
                        "untraceable_authority_block_count": 0,
                        "weak_address_authority_block_count": 0,
                        "locator_conflict_count": 0,
                    },
                    "ingestion_pipeline_receipt": {"status": "PASS"},
                    "unsupported_content": [],
                }
            ],
        },
        "enterprise_understanding_model": {
            "business_objects": [
                {
                    "object_id": "object:ticket",
                    "name": "工单",
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": "source:rules"}],
                }
            ],
            "actors": [],
            "operations": [],
            "object_relations": [],
            "lifecycles": [],
            "rules": [],
            "business_behaviors": [],
            "unknowns": [],
            "conflicts": [],
        },
    }


def _valid_product_receipt() -> dict:
    return {
        "status": "PASS",
        "receipt_fingerprint": "receipt:1",
        "source_manifest_external_refs_preserved": True,
        "source_identity_authority": "SOURCE_INVENTORY_EXTERNAL_REF",
        "source_ref_by_source_id": {"source:rules": SOURCE_REF},
        "absolute_workspace_paths_persisted_as_identity": False,
    }


def test_source_backed_receipt_surfaces_document_ground_truth_target(tmp_path) -> None:
    product_root = tmp_path / "product"
    workspace_root = tmp_path / "workspace"
    output = tmp_path / "output"
    product_root.mkdir()
    workspace_root.mkdir()
    manifest = tmp_path / "manifest.json"
    ground_truth = tmp_path / "ground_truth.json"
    _write_json(
        manifest,
        {
            "schema": "qualibug.enterprise-understanding-source-manifest.v1",
            "project_id": "source-backed-document-demo",
            "sources": [
                {
                    "path": SOURCE_REF,
                    "source_type": "business_rules",
                    "blob_sha": "x",
                }
            ],
            "product_phase_may_load_ground_truth": False,
        },
    )
    _write_json(ground_truth, _ground_truth())

    def fake_product_phase(command, **kwargs):
        del kwargs
        asset_path = Path(command[command.index("--asset-output") + 1])
        receipt_path = Path(command[command.index("--receipt-output") + 1])
        _write_json(asset_path, _asset())
        _write_json(receipt_path, _valid_product_receipt())
        return subprocess.CompletedProcess(command, 0, stdout="product-pass", stderr="")

    receipt = run_source_backed_understanding_workflow(
        project_id="source-backed-document-demo",
        product_root=product_root,
        workspace_root=workspace_root,
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=output,
        process_runner=fake_product_phase,
        environment={"GROUND_TRUTH_SECRET": "must-not-leak", "SAFE": "1"},
    )

    assert receipt["status"] == "PASS"
    assert receipt["source_identity_validated_before_ground_truth_load"] is True
    assert receipt["source_identity_authority"] == "SOURCE_INVENTORY_EXTERNAL_REF"
    assert receipt["source_ref_count"] == 1
    assert receipt["ground_truth_loaded_after_product_phase"] is True
    assert receipt["hidden_ground_truth_entered_product_runtime"] is False
    assert receipt["next_ingestion_repair_target"] == (
        "DOCUMENT_STRUCTURE_GROUND_TRUTH_NOT_DECLARED"
    )
    assert receipt["document_ground_truth_measurement_status"] == "NOT_DECLARED"
    assert "GROUND_TRUTH_SECRET" in receipt[
        "product_phase_environment_removed_sensitive_keys"
    ]
    assert (output / "evaluation" / "ingestion_metric_summary.json").exists()


def test_invalid_product_source_identity_blocks_before_ground_truth_is_opened(
    tmp_path,
) -> None:
    product_root = tmp_path / "product"
    workspace_root = tmp_path / "workspace"
    output = tmp_path / "output"
    product_root.mkdir()
    workspace_root.mkdir()
    manifest = tmp_path / "manifest.json"
    ground_truth = tmp_path / "ground_truth.json"
    _write_json(
        manifest,
        {
            "schema": "qualibug.enterprise-understanding-source-manifest.v1",
            "project_id": "source-backed-document-demo",
            "sources": [
                {
                    "path": SOURCE_REF,
                    "source_type": "business_rules",
                    "blob_sha": "x",
                }
            ],
            "product_phase_may_load_ground_truth": False,
        },
    )
    ground_truth.write_text("this is deliberately invalid JSON", encoding="utf-8")

    def fake_product_phase(command, **kwargs):
        del kwargs
        asset_path = Path(command[command.index("--asset-output") + 1])
        receipt_path = Path(command[command.index("--receipt-output") + 1])
        _write_json(asset_path, _asset())
        _write_json(
            receipt_path,
            {
                "status": "PASS",
                "receipt_fingerprint": "receipt:invalid",
                "source_manifest_external_refs_preserved": False,
            },
        )
        return subprocess.CompletedProcess(command, 0, stdout="product-pass", stderr="")

    receipt = run_source_backed_understanding_workflow(
        project_id="source-backed-document-demo",
        product_root=product_root,
        workspace_root=workspace_root,
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=output,
        process_runner=fake_product_phase,
    )

    assert receipt["status"] == "BLOCKED_PRODUCT_SOURCE_IDENTITY_INVALID"
    assert receipt["reason_code"] == "PRODUCT_SOURCE_REFERENCES_NOT_PRESERVED"
    assert receipt["source_identity_validated_before_ground_truth_load"] is False
    assert receipt["ground_truth_loaded_after_product_phase"] is False
    assert receipt["hidden_ground_truth_entered_product_runtime"] is False
    assert not (output / "evaluation").exists()
