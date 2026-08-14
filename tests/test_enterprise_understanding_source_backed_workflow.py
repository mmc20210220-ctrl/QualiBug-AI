from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from benchmark_evaluator.enterprise_understanding import build_product_snapshot as product_phase
from benchmark_evaluator.enterprise_understanding.build_product_snapshot import (
    ProductPhaseError,
    _git_blob_sha,
    build_isolated_product_snapshot,
)
from benchmark_evaluator.enterprise_understanding.run_source_backed_workflow import (
    run_source_backed_understanding_workflow,
)


SOURCE_REF = "docs/BUSINESS_RULES.md"
OCCURRENCE_ID = "occurrence:rules"
CANONICAL_SOURCE_ID = "source:rules"


def _write_manifest(root: Path, source_path: str, source_type: str = "business_rules") -> Path:
    source = root / source_path
    data = source.read_bytes()
    manifest = {
        "schema": "qualibug.enterprise-understanding-source-manifest.v1",
        "project_id": "ticketsla_d",
        "sources": [
            {
                "path": source_path,
                "source_type": source_type,
                "blob_sha": _git_blob_sha(data),
            }
        ],
        "product_phase_may_load_ground_truth": False,
    }
    path = root / "source_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def _valid_product_phase_receipt() -> dict:
    return {
        "status": "PASS",
        "receipt_fingerprint": "product:fingerprint",
        "source_manifest_external_refs_preserved": True,
        "source_identity_authority": "SOURCE_OCCURRENCE_REGISTRY",
        "source_occurrence_ref_by_id": {OCCURRENCE_ID: SOURCE_REF},
        "canonical_source_id_by_occurrence_id": {
            OCCURRENCE_ID: CANONICAL_SOURCE_ID
        },
        "content_identity_separate_from_source_occurrence": True,
        "interpretation_identity_separate_from_content_identity": True,
        "same_interpretation_content_parsed_once": True,
        "absolute_workspace_paths_persisted_as_identity": False,
        "content_asset_count": 1,
        "interpretation_asset_count": 1,
    }


def test_product_phase_reuses_existing_ingest_and_composition_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product_root = tmp_path / "product"
    workspace_root = tmp_path / "workspace"
    source = product_root / SOURCE_REF
    source.parent.mkdir(parents=True)
    source.write_text("# 规则\n只有OPEN状态的工单可以被分配。", encoding="utf-8")
    manifest = _write_manifest(product_root, SOURCE_REF)
    calls: dict[str, object] = {}

    def ingest(project, documents, *, root, actor):
        calls["ingest"] = {
            "project": project,
            "documents": [dict(row) for row in documents],
            "root": root,
            "actor": actor,
        }
        return {
            "ok": True,
            "created": [{"source_id": CANONICAL_SOURCE_ID}],
            "duplicates": [],
            "source_occurrences": [
                {
                    "source_occurrence_id": OCCURRENCE_ID,
                    "source_ref": SOURCE_REF,
                    "canonical_source_id": CANONICAL_SOURCE_ID,
                }
            ],
            "duplicate_source_occurrences": [],
            "content_asset_count": 1,
            "interpretation_asset_count": 1,
        }

    def build(project, root, options):
        calls["build"] = {"project": project, "root": root, "options": options}
        return {
            "asset_id": "asset:ticketsla",
            "enterprise_understanding_model": {
                "model_id": "model:ticketsla",
                "business_objects": [],
            },
        }

    def capture(**kwargs):
        calls["capture"] = kwargs
        output_path = Path(kwargs["output_path"])
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(
                {
                    "asset_id": "asset:ticketsla",
                    "source_inventory": [
                        {
                            "source_id": OCCURRENCE_ID,
                            "source_occurrence_id": OCCURRENCE_ID,
                            "canonical_source_id": CANONICAL_SOURCE_ID,
                            "external_ref": SOURCE_REF,
                        }
                    ],
                    "enterprise_understanding_model": {
                        "model_id": "model:ticketsla",
                        "business_objects": [],
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "receipt_fingerprint": "capture:fingerprint",
            "build_invoked": False,
            "ground_truth_loaded": False,
        }

    monkeypatch.setattr(product_phase, "capture_finalized_product_asset", capture)
    asset_output = tmp_path / "result" / "asset.json"
    receipt_output = tmp_path / "result" / "product_receipt.json"
    receipt = build_isolated_product_snapshot(
        project_id="ticketsla_d",
        product_root=product_root,
        workspace_root=workspace_root,
        manifest_path=manifest,
        asset_output_path=asset_output,
        receipt_output_path=receipt_output,
        authorities=(ingest, build),
    )

    assert calls["build"] == {
        "project": "ticketsla_d",
        "root": workspace_root.resolve(),
        "options": {"probe_limit": 0},
    }
    assert calls["ingest"]["actor"]["role"] == "project_owner"  # type: ignore[index]
    documents = calls["ingest"]["documents"]  # type: ignore[index]
    assert documents[0]["external_ref"] == SOURCE_REF  # type: ignore[index]
    assert documents[0]["filename"] == "BUSINESS_RULES.md"  # type: ignore[index]
    assert calls["capture"]["project_id"] == "ticketsla_d"  # type: ignore[index]
    assert receipt["composition_authority"].endswith(
        "composition.build_enterprise_business_knowledge_asset"
    )
    assert receipt["source_occurrence_ref_by_id"] == {
        OCCURRENCE_ID: SOURCE_REF
    }
    assert receipt["canonical_source_id_by_occurrence_id"] == {
        OCCURRENCE_ID: CANONICAL_SOURCE_ID
    }
    assert receipt["source_manifest_external_refs_preserved"] is True
    assert receipt["source_identity_authority"] == "SOURCE_OCCURRENCE_REGISTRY"
    assert receipt["probe_limit"] == 0
    assert receipt["ground_truth_loaded"] is False
    assert receipt["ground_truth_path_received"] is False
    assert receipt["hidden_answer_key_accessed"] is False


def test_product_phase_rejects_ground_truth_as_a_source(tmp_path: Path) -> None:
    product_root = tmp_path / "product"
    workspace_root = tmp_path / "workspace"
    source = product_root / "docs" / "ground_truth.json"
    source.parent.mkdir(parents=True)
    source.write_text("{}", encoding="utf-8")
    manifest = _write_manifest(product_root, "docs/ground_truth.json", "other")

    with pytest.raises(ProductPhaseError, match="ground_truth_path_in_product_sources"):
        build_isolated_product_snapshot(
            project_id="ticketsla_d",
            product_root=product_root,
            workspace_root=workspace_root,
            manifest_path=manifest,
            asset_output_path=tmp_path / "asset.json",
            receipt_output_path=tmp_path / "receipt.json",
            authorities=(lambda *args, **kwargs: {}, lambda *args, **kwargs: {}),
        )


def test_failed_product_phase_never_parses_ground_truth(tmp_path: Path) -> None:
    product_root = tmp_path / "product"
    product_root.mkdir()
    manifest = product_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "qualibug.enterprise-understanding-source-manifest.v1",
                "project_id": "ticketsla_d",
                "sources": [
                    {
                        "path": "docs/rules.md",
                        "source_type": "business_rules",
                        "blob_sha": "x",
                    }
                ],
                "product_phase_may_load_ground_truth": False,
            }
        ),
        encoding="utf-8",
    )
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text("not-json", encoding="utf-8")
    captured: dict[str, object] = {}

    def runner(command, **kwargs):
        captured["command"] = command
        captured["env"] = kwargs["env"]
        return subprocess.CompletedProcess(command, 7, stdout="blocked", stderr="failure")

    receipt = run_source_backed_understanding_workflow(
        project_id="ticketsla_d",
        product_root=product_root,
        workspace_root=tmp_path / "workspace",
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=tmp_path / "output",
        process_runner=runner,
        environment={
            "PATH": "/bin",
            "QUALIBUG_HIDDEN_BUG_REGISTRY": "/private/bugs.json",
            "QUALIBUG_GROUND_TRUTH_PATH": "/private/ground_truth.json",
        },
    )

    assert receipt["status"] == "BLOCKED_PRODUCT_PHASE_FAILED"
    assert receipt["ground_truth_loaded_after_product_phase"] is False
    assert receipt["source_identity_validated_before_ground_truth_load"] is False
    assert receipt["hidden_ground_truth_entered_product_runtime"] is False
    assert str(ground_truth) not in "\n".join(captured["command"])  # type: ignore[arg-type]
    assert "QUALIBUG_HIDDEN_BUG_REGISTRY" not in captured["env"]  # type: ignore[operator]
    assert "QUALIBUG_GROUND_TRUTH_PATH" not in captured["env"]  # type: ignore[operator]
    assert not any(
        "GROUND_TRUTH" in str(key).upper()
        for key in captured["env"]  # type: ignore[union-attr]
    )
    assert captured["env"]["QUALIBUG_EVALUATOR_PRIVATE_INPUT_ACCESS_ALLOWED"] == "0"  # type: ignore[index]
    assert not (tmp_path / "output" / "evaluation").exists()


def test_successful_product_phase_is_scored_only_after_identity_and_child_exit(
    tmp_path: Path,
) -> None:
    product_root = tmp_path / "product"
    product_root.mkdir()
    manifest = product_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "qualibug.enterprise-understanding-source-manifest.v1",
                "project_id": "ticketsla_d",
                "sources": [
                    {
                        "path": SOURCE_REF,
                        "source_type": "business_rules",
                        "blob_sha": "x",
                    }
                ],
                "product_phase_may_load_ground_truth": False,
            }
        ),
        encoding="utf-8",
    )
    ground_truth = tmp_path / "ground_truth.json"
    ground_truth.write_text(
        json.dumps(
            {
                "schema": "qualibug.enterprise-understanding-ground-truth.v1",
                "project_id": "ticketsla_d",
                "scope_complete": False,
                "minimum_profile": {"business_objects": 1},
                "business_objects": [
                    {
                        "ground_truth_id": "gt:ticket",
                        "canonical_name": "Ticket",
                        "criticality": "P0",
                        "source_refs": [SOURCE_REF],
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
        ),
        encoding="utf-8",
    )

    def runner(command, **kwargs):
        del kwargs
        asset_path = Path(command[command.index("--asset-output") + 1])
        product_receipt_path = Path(command[command.index("--receipt-output") + 1])
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text(
            json.dumps(
                {
                    "source_inventory": [
                        {
                            "source_id": OCCURRENCE_ID,
                            "source_occurrence_id": OCCURRENCE_ID,
                            "canonical_source_id": CANONICAL_SOURCE_ID,
                            "external_ref": SOURCE_REF,
                            "status": "active",
                        }
                    ],
                    "enterprise_understanding_model": {
                        "business_objects": [
                            {
                                "object_id": "object:ticket",
                                "name": "Ticket",
                                "status": "CONFIRMED",
                                "evidence": [{"source_id": SOURCE_REF}],
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
            ),
            encoding="utf-8",
        )
        product_receipt_path.write_text(
            json.dumps(_valid_product_phase_receipt()),
            encoding="utf-8",
        )
        return subprocess.CompletedProcess(command, 0, stdout="PASS", stderr="")

    receipt = run_source_backed_understanding_workflow(
        project_id="ticketsla_d",
        product_root=product_root,
        workspace_root=tmp_path / "workspace",
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=tmp_path / "output",
        process_runner=runner,
        environment={"PATH": "/bin"},
    )

    assert receipt["status"] == "PASS"
    assert receipt["source_identity_validated_before_ground_truth_load"] is True
    assert receipt["source_identity_authority"] == "SOURCE_OCCURRENCE_REGISTRY"
    assert receipt["source_occurrence_count"] == 1
    assert receipt["canonical_source_count"] == 1
    assert receipt["ground_truth_loaded_after_product_phase"] is True
    assert receipt["product_phase_command_contains_ground_truth"] is False
    assert receipt["hidden_ground_truth_entered_product_runtime"] is False
    assert receipt["next_repair_target"] == ""
    assert (tmp_path / "output" / "evaluation" / "metric_summary.json").exists()
