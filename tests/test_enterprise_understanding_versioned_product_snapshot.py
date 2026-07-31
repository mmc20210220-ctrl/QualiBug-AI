from __future__ import annotations

import json
from pathlib import Path

import benchmark_evaluator.enterprise_understanding.build_versioned_product_snapshot as versioned_module
from benchmark_evaluator.enterprise_understanding.build_product_snapshot import (
    SOURCE_MANIFEST_SCHEMA,
    _git_blob_sha,
    _resolve_sources,
)


def _write_manifest(path: Path, project: str, source_path: str, source_ref: str, blob_sha: str):
    path.write_text(
        json.dumps(
            {
                "schema": SOURCE_MANIFEST_SCHEMA,
                "project_id": project,
                "product_phase_may_load_ground_truth": False,
                "sources": [
                    {
                        "path": source_path,
                        "source_ref": source_ref,
                        "source_type": "business_rules",
                        "blob_sha": blob_sha,
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_manifest_separates_fixture_path_from_stable_source_identity(tmp_path):
    product = tmp_path / "product"
    product.mkdir()
    source = product / "fixtures" / "v1.md"
    source.parent.mkdir(parents=True)
    source.write_text("同一请求不得重复成功。", encoding="utf-8")
    manifest = {
        "schema": SOURCE_MANIFEST_SCHEMA,
        "project_id": "p",
        "product_phase_may_load_ground_truth": False,
        "sources": [
            {
                "path": "fixtures/v1.md",
                "source_ref": "online-docs/business-rules.md",
                "source_type": "business_rules",
                "blob_sha": _git_blob_sha(source.read_bytes()),
            }
        ],
    }

    documents, receipts = _resolve_sources(manifest, product)

    assert documents[0]["file_path"] == str(source.resolve())
    assert documents[0]["external_ref"] == "online-docs/business-rules.md"
    assert receipts[0]["path"] == "fixtures/v1.md"
    assert receipts[0]["source_ref"] == "online-docs/business-rules.md"
    assert receipts[0]["fixture_path_separate_from_source_identity"] is True


def test_two_phase_builder_reuses_product_authorities_without_ground_truth(
    monkeypatch, tmp_path
):
    project = "implicit-versioned"
    product = tmp_path / "product"
    workspace = tmp_path / "workspace"
    output = tmp_path / "output"
    product.mkdir()
    output.mkdir()
    first_source = product / "fixtures" / "v1.md"
    second_source = product / "fixtures" / "v2.md"
    first_source.parent.mkdir(parents=True)
    first_source.write_text("同一请求不得重复成功。", encoding="utf-8")
    second_source.write_text("同一请求不得重复成功；旧数量规则已删除。", encoding="utf-8")
    stable_ref = "online-docs/business-rules.md"
    manifest_one = output / "manifest-v1.json"
    manifest_two = output / "manifest-v2.json"
    _write_manifest(
        manifest_one,
        project,
        "fixtures/v1.md",
        stable_ref,
        _git_blob_sha(first_source.read_bytes()),
    )
    _write_manifest(
        manifest_two,
        project,
        "fixtures/v2.md",
        stable_ref,
        _git_blob_sha(second_source.read_bytes()),
    )

    ingest_calls = []
    build_calls = []

    def ingest(project_id, documents, *, root, actor):
        ingest_calls.append(
            {
                "project_id": project_id,
                "documents": documents,
                "root": root,
                "actor": actor,
            }
        )
        version = len(ingest_calls)
        return {
            "ok": True,
            "source_occurrences": [
                {
                    "source_occurrence_id": f"occurrence:v{version}",
                    "source_ref": documents[0]["external_ref"],
                    "canonical_source_id": f"canonical:v{version}",
                    "version": version,
                }
            ],
            "duplicate_source_occurrences": [],
        }

    def build(project_id, root, options):
        build_calls.append((project_id, root, dict(options)))
        phase = len(build_calls)
        return {
            "asset_id": f"asset:v{phase}",
            "enterprise_understanding_model": {"model_id": f"model:v{phase}"},
            "implicit_rule_governance_carry_forward_receipt": {
                "captured_before_base_rebuild": True,
                "prior_rule_library_reused": False,
                "restored_field_count": 0 if phase == 1 else 1,
            },
        }

    captured_assets = []

    def capture(*, project_id, root, output_path):
        captured_assets.append((project_id, root, Path(output_path)))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text("{}", encoding="utf-8")
        return {"status": "PASS", "output_path": str(output_path)}

    monkeypatch.setattr(versioned_module, "capture_finalized_product_asset", capture)

    receipt = versioned_module.build_versioned_product_snapshot(
        project_id=project,
        product_root=product,
        workspace_root=workspace,
        phase_one_manifest_path=manifest_one,
        phase_two_manifest_path=manifest_two,
        phase_one_asset_output_path=output / "asset-v1.json",
        final_asset_output_path=output / "asset-v2.json",
        receipt_output_path=output / "receipt.json",
        authorities=(ingest, build),
    )

    assert receipt["status"] == "PASS"
    assert receipt["composition_invocation_count"] == 2
    assert receipt["ground_truth_loaded"] is False
    assert receipt["ground_truth_path_received"] is False
    assert receipt["hidden_answer_key_accessed"] is False
    assert len(ingest_calls) == 2
    assert len(build_calls) == 2
    assert all(call[2] == {"probe_limit": 0} for call in build_calls)
    assert all(
        call["documents"][0]["external_ref"] == stable_ref
        for call in ingest_calls
    )
    transition = receipt["source_version_transitions"][0]
    assert transition["source_ref"] == stable_ref
    assert transition["phase_one_source_occurrence_id"] == "occurrence:v1"
    assert transition["phase_two_source_occurrence_id"] == "occurrence:v2"
    assert transition["phase_one_version"] == 1
    assert transition["phase_two_version"] == 2
    assert transition["source_occurrence_supersession_authority"] is True
    assert receipt["phase_two_governance_carry_forward_receipt"][
        "prior_rule_library_reused"
    ] is False
    assert len(captured_assets) == 2
