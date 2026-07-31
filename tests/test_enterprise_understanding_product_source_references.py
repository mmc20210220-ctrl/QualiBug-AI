from __future__ import annotations

import json
from pathlib import Path

import pytest

import benchmark_evaluator.enterprise_understanding.build_product_snapshot as product_snapshot


def _write_manifest(product_root: Path, relative: str, data: bytes) -> Path:
    source = product_root / relative
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(data)
    manifest = {
        "schema": product_snapshot.SOURCE_MANIFEST_SCHEMA,
        "project_id": "source-ref-demo",
        "product_phase_may_load_ground_truth": False,
        "sources": [
            {
                "path": relative,
                "source_type": "business_rules",
                "blob_sha": product_snapshot._git_blob_sha(data),
            }
        ],
    }
    path = product_root / "source_manifest.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def test_product_phase_persists_manifest_path_as_external_ref(tmp_path, monkeypatch) -> None:
    product_root = tmp_path / "product"
    workspace = tmp_path / "workspace"
    product_root.mkdir()
    relative = "projects/source-ref-demo/input/BUSINESS_RULES.md"
    manifest_path = _write_manifest(product_root, relative, b"# Business Rules\n")
    asset_output = tmp_path / "asset.json"
    receipt_output = tmp_path / "receipt.json"
    captured_documents: list[dict] = []

    def fake_ingest(project_id, documents, *, root, actor):
        assert project_id == "source-ref-demo"
        assert root == workspace.resolve()
        assert actor["role"] == "project_owner"
        captured_documents.extend(dict(row) for row in documents)
        return {
            "ok": True,
            "created": [
                {
                    "source_id": "src_runtime_version",
                    "external_ref": documents[0]["external_ref"],
                }
            ],
            "duplicates": [],
        }

    def fake_build(project_id, root, options):
        assert project_id == "source-ref-demo"
        assert root == workspace.resolve()
        assert options == {"probe_limit": 0}
        return {"enterprise_understanding_model": {"business_objects": []}}

    def fake_capture(*, project_id, root, output_path):
        Path(output_path).write_text(
            json.dumps(
                {
                    "source_inventory": [
                        {
                            "source_id": "src_runtime_version",
                            "external_ref": relative,
                        }
                    ],
                    "enterprise_understanding_model": {"business_objects": []},
                }
            ),
            encoding="utf-8",
        )
        return {"receipt_fingerprint": "capture-receipt"}

    monkeypatch.setattr(product_snapshot, "capture_finalized_product_asset", fake_capture)

    receipt = product_snapshot.build_isolated_product_snapshot(
        project_id="source-ref-demo",
        product_root=product_root,
        workspace_root=workspace,
        manifest_path=manifest_path,
        asset_output_path=asset_output,
        receipt_output_path=receipt_output,
        authorities=(fake_ingest, fake_build),
    )

    assert captured_documents == [
        {
            "file_path": str((product_root / relative).resolve()),
            "filename": "BUSINESS_RULES.md",
            "source_type": "business_rules",
            "external_ref": relative,
            "tags": ["source-backed-benchmark"],
        }
    ]
    assert receipt["source_ref_by_source_id"] == {
        "src_runtime_version": relative
    }
    assert receipt["source_manifest_external_refs_preserved"] is True
    assert receipt["absolute_workspace_paths_persisted_as_identity"] is False
    assert json.loads(receipt_output.read_text(encoding="utf-8"))[
        "source_identity_authority"
    ] == "SOURCE_INVENTORY_EXTERNAL_REF"


def test_product_phase_fails_before_build_when_ingest_drops_source_ref(
    tmp_path,
    monkeypatch,
) -> None:
    product_root = tmp_path / "product"
    workspace = tmp_path / "workspace"
    product_root.mkdir()
    manifest_path = _write_manifest(
        product_root,
        "projects/source-ref-demo/input/BUSINESS_RULES.md",
        b"# Business Rules\n",
    )
    build_called = False

    def fake_ingest(project_id, documents, *, root, actor):
        del project_id, documents, root, actor
        return {
            "ok": True,
            "created": [{"source_id": "src_runtime_version", "external_ref": ""}],
            "duplicates": [],
        }

    def fake_build(project_id, root, options):
        nonlocal build_called
        del project_id, root, options
        build_called = True
        return {"enterprise_understanding_model": {}}

    monkeypatch.setattr(
        product_snapshot,
        "capture_finalized_product_asset",
        lambda **kwargs: {"receipt_fingerprint": "unused"},
    )

    with pytest.raises(
        product_snapshot.ProductPhaseError,
        match="source_manifest_external_ref_not_preserved",
    ):
        product_snapshot.build_isolated_product_snapshot(
            project_id="source-ref-demo",
            product_root=product_root,
            workspace_root=workspace,
            manifest_path=manifest_path,
            asset_output_path=tmp_path / "asset.json",
            receipt_output_path=tmp_path / "receipt.json",
            authorities=(fake_ingest, fake_build),
        )

    assert build_called is False


@pytest.mark.parametrize(
    "source_ref",
    ["../secret.md", "/absolute/secret.md", "projects/./rules.md"],
)
def test_manifest_source_ref_must_be_workspace_independent(source_ref) -> None:
    with pytest.raises(product_snapshot.ProductPhaseError, match="not_portable"):
        product_snapshot._portable_source_ref(source_ref)
