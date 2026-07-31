from __future__ import annotations

import json
import subprocess
from pathlib import Path

from benchmark_evaluator.enterprise_understanding.run_source_backed_workflow import (
    run_source_backed_understanding_workflow,
)


def _manifest(product_root: Path) -> Path:
    path = product_root / "manifest.json"
    path.write_text(
        json.dumps(
            {
                "schema": "qualibug.enterprise-understanding-source-manifest.v1",
                "project_id": "quarantine_contract",
                "sources": [
                    {
                        "path": "docs/rules.md",
                        "source_type": "business_rules",
                        "blob_sha": "not-read-before-product-runner",
                    }
                ],
                "product_phase_may_load_ground_truth": False,
            }
        ),
        encoding="utf-8",
    )
    return path


def _run_with_tamper(tmp_path: Path, *, as_directory: bool) -> tuple[dict, Path, bytes]:
    product_root = tmp_path / "product"
    product_root.mkdir()
    manifest = _manifest(product_root)
    ground_truth = product_root / "private" / "ground_truth.json"
    ground_truth.parent.mkdir()
    original = b'{"private":"original"}\n'
    ground_truth.write_bytes(original)

    def runner(command, **kwargs):
        del kwargs
        assert ground_truth.exists() is False
        if as_directory:
            ground_truth.mkdir()
            (ground_truth / "forged.json").write_text("{}", encoding="utf-8")
        else:
            ground_truth.write_text('{"private":"forged"}', encoding="utf-8")
        return subprocess.CompletedProcess(command, 0, stdout="PASS", stderr="")

    receipt = run_source_backed_understanding_workflow(
        project_id="quarantine_contract",
        product_root=product_root,
        workspace_root=tmp_path / "workspace",
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=tmp_path / "output",
        process_runner=runner,
        environment={"PATH": "/bin"},
    )
    return receipt, ground_truth, original


def test_product_runner_cannot_see_ground_truth_path_and_original_is_restored(
    tmp_path: Path,
) -> None:
    product_root = tmp_path / "product"
    product_root.mkdir()
    manifest = _manifest(product_root)
    ground_truth = product_root / "private" / "ground_truth.json"
    ground_truth.parent.mkdir()
    original = b'{"private":"answer-key"}\n'
    ground_truth.write_bytes(original)
    observed: dict[str, object] = {}

    def runner(command, **kwargs):
        observed["command"] = list(command)
        observed["ground_truth_exists"] = ground_truth.exists()
        observed["environment"] = dict(kwargs["env"])
        return subprocess.CompletedProcess(command, 7, stdout="blocked", stderr="failure")

    receipt = run_source_backed_understanding_workflow(
        project_id="quarantine_contract",
        product_root=product_root,
        workspace_root=tmp_path / "workspace",
        source_manifest_path=manifest,
        ground_truth_path=ground_truth,
        output_dir=tmp_path / "output",
        process_runner=runner,
        environment={"PATH": "/bin", "QUALIBUG_GROUND_TRUTH_PATH": "/private/key"},
    )

    assert observed["ground_truth_exists"] is False
    assert str(ground_truth) not in "\n".join(observed["command"])  # type: ignore[arg-type]
    assert "QUALIBUG_GROUND_TRUTH_PATH" not in observed["environment"]  # type: ignore[operator]
    assert ground_truth.is_file()
    assert ground_truth.read_bytes() == original
    assert receipt["status"] == "BLOCKED_PRODUCT_PHASE_FAILED"
    assert receipt["ground_truth_original_path_absent_during_product_phase"] is True
    assert receipt["ground_truth_restored_after_product_phase"] is True
    assert receipt["ground_truth_recreated_by_product_phase"] is False
    assert receipt["ground_truth_private_bytes_held_in_parent_memory"] is True
    assert receipt["ground_truth_private_disk_copy_created"] is False
    assert receipt["ground_truth_private_quarantine_path_disclosed_to_product"] is False
    assert receipt["product_phase_filesystem_ground_truth_access_allowed"] is False
    assert receipt["ground_truth_loaded_after_product_phase"] is False


def test_product_phase_recreating_ground_truth_file_blocks_evaluator(
    tmp_path: Path,
) -> None:
    receipt, ground_truth, original = _run_with_tamper(tmp_path, as_directory=False)

    assert receipt["status"] == "BLOCKED_PRODUCT_PHASE_PRIVATE_INPUT_PATH_TAMPERED"
    assert receipt["reason_code"] == "PRODUCT_PHASE_RECREATED_GROUND_TRUTH_PATH"
    assert receipt["ground_truth_recreated_by_product_phase"] is True
    assert receipt["ground_truth_restored_after_product_phase"] is True
    assert receipt["ground_truth_private_bytes_held_in_parent_memory"] is True
    assert receipt["ground_truth_private_disk_copy_created"] is False
    assert receipt["ground_truth_loaded_after_product_phase"] is False
    assert ground_truth.is_file()
    assert ground_truth.read_bytes() == original
    assert not (tmp_path / "output" / "evaluation").exists()


def test_product_phase_recreating_ground_truth_directory_still_restores_original(
    tmp_path: Path,
) -> None:
    receipt, ground_truth, original = _run_with_tamper(tmp_path, as_directory=True)

    assert receipt["status"] == "BLOCKED_PRODUCT_PHASE_PRIVATE_INPUT_PATH_TAMPERED"
    assert receipt["reason_code"] == "PRODUCT_PHASE_RECREATED_GROUND_TRUTH_PATH"
    assert receipt["ground_truth_recreated_by_product_phase"] is True
    assert receipt["ground_truth_restored_after_product_phase"] is True
    assert receipt["ground_truth_private_bytes_held_in_parent_memory"] is True
    assert receipt["ground_truth_private_disk_copy_created"] is False
    assert ground_truth.is_file()
    assert ground_truth.read_bytes() == original
    assert not (tmp_path / "output" / "evaluation").exists()
