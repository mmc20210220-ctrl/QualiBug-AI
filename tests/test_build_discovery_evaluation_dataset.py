from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.build_discovery_evaluation_dataset import (
    build_dataset_from_external,
    _parse_external_target,
)


def _args(**overrides):
    class NS:
        pass

    args = NS()
    args.output_root = overrides["output_root"]
    args.dataset_id = "external-scaffold-test"
    args.dataset_version = "v-test-1"
    args.environment_type = "sandbox"
    args.reset_method = "POST"
    args.reset_path = "/__reset"
    args.observation_path = "/__state"
    args.test_accounts_ref = None
    args.external_target = overrides["external_target"]
    args.suite_root = None
    args.held_in = None
    args.held_out = None
    args.clean = None
    args.seeded_base_url = None
    args.clean_base_url = None
    return args


def _touch(path: Path, payload: dict | str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, dict):
        path.write_text(json.dumps(payload), encoding="utf-8")
    else:
        path.write_text(str(payload), encoding="utf-8")
    return path


def test_parse_external_target_rejects_clean_with_gt() -> None:
    with pytest.raises(Exception):
        _parse_external_target("t|ind|held_out|clean|a.json|b.md|gt.json|http://x")


def test_external_scaffolding_keeps_gt_out_of_runtime(tmp_path: Path) -> None:
    openapi = _touch(tmp_path / "api" / "openapi.yaml", "openapi: 3.0.0\npaths: {}\n")
    prd = _touch(tmp_path / "api" / "PRD.md", "# prd\n")
    gt = _touch(tmp_path / "private" / "bugs.json", {"bugs": []})
    output = tmp_path / "out"

    def target(tid: str, industry: str, split: str, expectation: str, gt_path: str = "") -> str:
        return "|".join(
            [
                tid,
                industry,
                split,
                expectation,
                str(openapi),
                str(prd),
                gt_path,
                "http://127.0.0.1:18080",
            ]
        )

    args = _args(
        output_root=str(output),
        external_target=[
            target("held-in-1", "ecommerce", "held_in", "seeded_defects", str(gt)),
            target("held-out-1", "saas", "held_out", "seeded_defects", str(gt)),
            target("held-out-2", "mes", "held_out", "seeded_defects", str(gt)),
            target("held-out-3", "finance", "held_out", "seeded_defects", str(gt)),
            target("clean-1", "ecommerce", "held_out", "clean", ""),
        ],
    )
    manifest_path = build_dataset_from_external(args)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema_version"] == "qualibug.discovery-evaluation-dataset.v1"
    seeded = [t for t in manifest["targets"] if t["expectation"] == "seeded_defects"]
    assert all(str(gt) == t["evaluator"]["ground_truth_ref"] for t in seeded)
    for target_row in manifest["targets"]:
        for ref_key in ("input_bundle_ref", "fixture_snapshot_ref", "context_artifact_ref"):
            artifact = json.loads(Path(target_row["runtime"][ref_key]).read_text(encoding="utf-8"))
            dumped = json.dumps(artifact, ensure_ascii=False).lower()
            assert "ground_truth" not in dumped
            assert "bugs.json" not in dumped
