from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from benchmark_evaluator.enterprise_understanding.capture_product_asset import (
    ProductAssetCaptureError,
    capture_finalized_product_asset,
)
from benchmark_evaluator.enterprise_understanding.ground_truth import load_ground_truth


FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "benchmark_evaluator"
    / "enterprise_understanding"
    / "fixtures"
    / "ticketsla_d"
    / "ground_truth.json"
)


def test_ticketsla_ground_truth_is_source_backed_and_fail_visible() -> None:
    ground_truth = load_ground_truth(FIXTURE)

    assert ground_truth["validation_receipt"]["status"] == "PASS"
    assert ground_truth["scope_complete"] is False
    assert len(ground_truth["business_objects"]) == 10
    assert len(ground_truth["actors"]) == 4
    assert len(ground_truth["operations"]) == 12
    assert len(ground_truth["object_relations"]) == 7
    assert len(ground_truth["state_transitions"]) == 6
    assert len(ground_truth["business_behaviors"]) == 25
    assert len(ground_truth["bug_dependencies"]) == 15
    assert ground_truth["validation_receipt"]["model_writeback_allowed"] is False
    assert ground_truth["validation_receipt"]["generated_from_current_model"] is False

    all_ids = {
        row["ground_truth_id"]
        for collection in (
            "business_objects",
            "actors",
            "operations",
            "object_relations",
            "state_transitions",
            "business_behaviors",
            "bug_dependencies",
        )
        for row in ground_truth[collection]
    }
    behavior_ids = {
        row["ground_truth_id"] for row in ground_truth["business_behaviors"]
    }
    assert len(all_ids) == 79
    assert all(
        set(row["required_ground_truth_ids"]).issubset(behavior_ids)
        for row in ground_truth["bug_dependencies"]
    )
    assert all(
        row.get("source_refs") or row.get("source_locators")
        for collection in (
            "business_objects",
            "actors",
            "operations",
            "object_relations",
            "state_transitions",
            "business_behaviors",
            "bug_dependencies",
        )
        for row in ground_truth[collection]
    )


def test_capture_uses_finalized_load_authority_without_building(tmp_path: Path) -> None:
    asset = {
        "asset_id": "asset:ticketsla",
        "enterprise_understanding_model": {
            "model_id": "model:ticketsla",
            "business_objects": [{"object_id": "object:ticket", "name": "Ticket"}],
            "actors": [],
            "operations": [],
            "object_relations": [],
            "lifecycles": [],
            "rules": [],
            "business_behaviors": [],
        },
    }
    before = deepcopy(asset)
    calls: list[tuple[str, Path]] = []

    def loader(project_id: str, root: Path):
        calls.append((project_id, root))
        return asset

    output = tmp_path / "ticketsla.asset.json"
    receipt = capture_finalized_product_asset(
        project_id="ticketsla_d",
        root=tmp_path,
        output_path=output,
        loader=loader,
    )
    snapshot = json.loads(output.read_text(encoding="utf-8"))

    assert calls == [("ticketsla_d", tmp_path.resolve())]
    assert asset == before
    assert receipt["build_invoked"] is False
    assert receipt["ground_truth_loaded"] is False
    assert receipt["product_asset_rewritten"] is False
    assert snapshot["enterprise_understanding_model"] == asset[
        "enterprise_understanding_model"
    ]
    assert snapshot["_enterprise_understanding_evaluator_snapshot"] == receipt


def test_capture_fails_when_no_finalized_asset_exists(tmp_path: Path) -> None:
    with pytest.raises(
        ProductAssetCaptureError,
        match="finalized_enterprise_understanding_asset_missing:ticketsla_d",
    ):
        capture_finalized_product_asset(
            project_id="ticketsla_d",
            root=tmp_path,
            output_path=tmp_path / "missing.json",
            loader=lambda _project, _root: None,
        )

    assert not (tmp_path / "missing.json").exists()
