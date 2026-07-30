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


def test_product_phase_reuses_existing_ingest_and_composition_authorities(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    product_root = tmp_path / "product"
    workspace_root = tmp_path / "workspace"
    source = product_root / "docs" / "BUSINESS_RULES.md"
    source.parent.mkdir(parents=True)
    source.write_text("# 规则\n只有OPEN状态的工单可以被分配。", encoding="utf-8")
    manifest = _write_manifest(product_root, "docs/BUSINESS_RULES.md")
    calls: dict[str, object] = {}

    def ingest(project, paths, *, root, actor, source_type_hints):
        calls["ingest"] = {
            "project": project,
            "paths": [str(path) for path in paths],
            "root": root,
            "actor": actor,
            "hints": source_type_hints,
        }
        return {"ok": True, "created": [{"source_id": "source:rules"}], "duplicates": []}

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
        Path(kwargs["output_path"]).write_text(
            json.dumps(
                {
                    "asset_id": "asset:ticketsla",
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
    assert calls["capture"]["project_id"] == "ticketsla_d"  # type: ignore[index]
    assert receipt["composition_authority"].endswith(
        "composition.build_enterprise_business_knowledge_asset"
    )
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
                "sources": [{"path": "docs/rules.md", "source_type": "business_rules", "blob_sha": "x"}],
                "product_phase_may_load_ground_truth": False,
            }
        ),
        encoding="utf-8",
    )
    # Intentionally invalid JSON. The workflow must not parse it after product failure.
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
    assert receipt["hidden_ground_truth_entered_product_runtime"] is False
    assert str(ground_truth) not in "\n".join(captured["command"])  # type: ignore[arg-type]
    assert "QUALIBUG_HIDDEN_BUG_REGISTRY" not in captured["env"]  # type: ignore[operator]
    assert "QUALIBUG_GROUND_TRUTH_PATH" not in captured["env"]  # type: ignore[operator]
    assert not (tmp_path / "output" / "evaluation").exists()


def test_successful_product_phase_is_scored_only_after_child_exit(tmp_path: Path) -> None:
    product_root = tmp_path / "product"
    product_root.mkdir()
    manifest = product_root / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "qualibug.enterprise-understanding-source-manifest.v1",
                "project_id": "ticketsla_d",
                "sources": [{"path": "docs/rules.md", "source_type": "business_rules", "blob_sha": "x"}],
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
                        "source_refs": ["docs/rules.md"],
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
        asset_path = Path(command[command.index("--asset-output") + 1])
        product_receipt_path = Path(command[command.index("--receipt-output") + 1])
        asset_path.parent.mkdir(parents=True, exist_ok=True)
        asset_path.write_text(
            json.dumps(
                {
                    "enterprise_understanding_model": {
                        "business_objects": [
                            {
                                "object_id": "object:ticket",
                                "name": "Ticket",
                                "status": "CONFIRMED",
                                "evidence": [{"source_id": "docs/rules.md"}],
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
                    }
                }
            ),
            encoding="utf-8",
        )
        product_receipt_path.write_text(
            json.dumps({"receipt_fingerprint": "product:fingerprint"}),
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
    assert receipt["ground_truth_loaded_after_product_phase"] is True
    assert receipt["product_phase_command_contains_ground_truth"] is False
    assert receipt["hidden_ground_truth_entered_product_runtime"] is False
    assert receipt["next_repair_target"] == ""
    assert (tmp_path / "output" / "evaluation" / "metric_summary.json").exists()
