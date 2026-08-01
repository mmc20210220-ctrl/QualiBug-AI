from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark_evaluator.enterprise_understanding.ticketsla_object_baseline import (
    PROJECT_ID,
    SOURCE_SPECS,
    TicketSLAObjectBaselineError,
    _git_blob_sha,
    run_ticketsla_object_baseline,
)


def _write_sources(root: Path) -> dict[str, str]:
    payloads = {
        "projects/ticketsla_d/input/BUSINESS_RULES.md": "# TicketSLA\nTicket 是工单。\n",
        "projects/ticketsla_d/input/TEST_ACCOUNTS.md": "# Accounts\nCUSTOMER Alice\n",
        "projects/ticketsla_d/input/openapi.yaml": "openapi: 3.0.3\npaths: {}\n",
    }
    hashes: dict[str, str] = {}
    for relative, content in payloads.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        blob = content.encode("utf-8")
        path.write_bytes(blob)
        hashes[relative] = _git_blob_sha(blob)
    return hashes


def _object_ground_truth(hashes: dict[str, str]) -> dict:
    return {
        "schema": "qualibug.enterprise-business-object-ground-truth.v1",
        "project_id": PROJECT_ID,
        "annotation_scope": "CLOSED_WORLD_SOURCE_LABELS",
        "ground_truth_generated_from_product_output": False,
        "source_snapshot": [
            {"path": relative, "blob_sha": hashes[relative]}
            for relative, _source_type in SOURCE_SPECS
        ],
        "labels": [
            {
                "ground_truth_id": "gt:ticket",
                "canonical_label": "Ticket",
                "expected_business_object": True,
                "semantic_roles": ["BUSINESS_OBJECT"],
                "source_refs": ["BUSINESS_RULES.md"],
            }
        ],
    }


def _product_asset() -> dict:
    return {
        "asset_id": "asset:ticketsla",
        "enterprise_understanding_model": {
            "model_id": "model:ticketsla",
            "business_objects": [
                {"object_id": "object:ticket", "name": "Ticket"}
            ],
            "actors": [],
            "operations": [],
            "object_relations": [],
            "lifecycles": [],
            "rules": [],
            "business_behaviors": [],
        },
        "business_object_recognition": {
            "candidates": [
                {
                    "candidate_id": "candidate:ticket",
                    "comparison_key": "ticket",
                    "labels": ["Ticket"],
                    "status": "ACCEPTED",
                }
            ],
            "accepted_comparison_keys": ["ticket"],
            "unknowns": [],
        },
    }


def test_real_baseline_loads_ground_truth_only_after_product_capture(
    tmp_path: Path,
) -> None:
    hashes = _write_sources(tmp_path)
    object_truth = _object_ground_truth(hashes)
    asset = _product_asset()
    calls: list[str] = []
    captured_options: list[dict] = []

    def ingestor(project_id, paths, *, root, actor, source_type_hints):
        calls.append("ingest")
        assert project_id == PROJECT_ID
        assert root == tmp_path.resolve()
        assert actor["role"] == "qa_lead"
        assert len(paths) == 3
        assert set(source_type_hints.values()) == {
            "business_rules",
            "config",
            "openapi",
        }
        return {
            "ok": True,
            "transaction_status": "COMMITTED",
            "created": [],
            "duplicates": [{"reason": "same_content_hash"}],
            "errors": [],
            "source_count": 3,
        }

    def builder(project_id, root, options):
        calls.append("build")
        assert project_id == PROJECT_ID
        assert root == tmp_path.resolve()
        captured_options.append(dict(options))
        return asset

    def capturer(*, project_id, root, output_path):
        calls.append("capture")
        assert project_id == PROJECT_ID
        assert root == tmp_path.resolve()
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asset), encoding="utf-8")
        return {
            "project_id": PROJECT_ID,
            "build_invoked": False,
            "ground_truth_loaded": False,
            "product_asset_rewritten": False,
        }

    def object_ground_truth_loader(_path):
        calls.append("load_object_ground_truth")
        return object_truth

    def evaluator(business_object_ground_truth, product_asset):
        calls.append("evaluate")
        assert business_object_ground_truth is object_truth
        assert product_asset["asset_id"] == "asset:ticketsla"
        return {
            "status": "MEASURED",
            "metrics": {
                "object_type_precision": 1.0,
                "object_type_recall": 1.0,
                "object_type_f1": 1.0,
            },
            "false_positive_objects": [],
            "false_negative_objects": [],
        }

    summary = run_ticketsla_object_baseline(
        root=tmp_path,
        ingestor=ingestor,
        builder=builder,
        capturer=capturer,
        business_object_ground_truth_loader=object_ground_truth_loader,
        evaluator=evaluator,
    )

    assert calls == [
        "ingest",
        "build",
        "capture",
        "load_object_ground_truth",
        "evaluate",
    ]
    assert captured_options == [{"probe_limit": 0}]
    assert summary["status"] == "PASS"
    assert summary["business_object_measurement_status"] == "MEASURED"
    assert summary["business_object_metrics"]["object_type_f1"] == 1.0
    assert summary["ground_truth_loaded_after_product_capture"] is True
    assert summary["ground_truth_entered_product_runtime"] is False
    assert summary["ground_truth_passed_to_ingestion"] is False
    assert summary["ground_truth_passed_to_composition"] is False
    assert summary["ground_truth_passed_to_capture"] is False
    assert summary["parallel_product_builder_created"] is False
    assert (
        tmp_path
        / "evaluator_outputs"
        / PROJECT_ID
        / "business_object_baseline"
        / "baseline_summary.json"
    ).is_file()


def test_source_snapshot_drift_blocks_scoring_after_capture(tmp_path: Path) -> None:
    hashes = _write_sources(tmp_path)
    object_truth = _object_ground_truth(hashes)
    object_truth["source_snapshot"][0]["blob_sha"] = "0" * 40
    asset = _product_asset()
    calls: list[str] = []

    def ingestor(*_args, **_kwargs):
        calls.append("ingest")
        return {
            "ok": True,
            "transaction_status": "COMMITTED",
            "errors": [],
            "source_count": 3,
        }

    def builder(*_args, **_kwargs):
        calls.append("build")
        return asset

    def capturer(*, output_path, **_kwargs):
        calls.append("capture")
        target = Path(output_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asset), encoding="utf-8")
        return {"ground_truth_loaded": False}

    def object_gt(_path):
        calls.append("load_object_ground_truth")
        return object_truth

    def evaluator(*_args, **_kwargs):
        calls.append("evaluate")
        raise AssertionError("drifted source snapshot must not be scored")

    summary = run_ticketsla_object_baseline(
        root=tmp_path,
        ingestor=ingestor,
        builder=builder,
        capturer=capturer,
        business_object_ground_truth_loader=object_gt,
        evaluator=evaluator,
    )

    assert calls == [
        "ingest",
        "build",
        "capture",
        "load_object_ground_truth",
    ]
    assert summary["status"] == "BLOCKED"
    assert summary["reason_code"] == "TICKETSLA_PUBLIC_SOURCE_SNAPSHOT_DRIFT"
    assert summary["source_snapshot_verification"]["status"] == "BLOCKED"
    assert summary["source_snapshot_verification"]["drift"][0]["path"].endswith(
        "BUSINESS_RULES.md"
    )
    assert summary["ground_truth_entered_product_runtime"] is False


def test_ingestion_failure_blocks_before_builder_and_ground_truth(tmp_path: Path) -> None:
    _write_sources(tmp_path)
    calls: list[str] = []

    def ingestor(*_args, **_kwargs):
        calls.append("ingest")
        return {
            "ok": False,
            "transaction_status": "BLOCKED",
            "errors": [{"code": "SOURCE_FORMAL_PARSE_BLOCKED"}],
        }

    def forbidden(*_args, **_kwargs):
        calls.append("forbidden")
        raise AssertionError("downstream stage must not run")

    with pytest.raises(
        TicketSLAObjectBaselineError,
        match="ticketsla_source_ingestion_blocked",
    ):
        run_ticketsla_object_baseline(
            root=tmp_path,
            ingestor=ingestor,
            builder=forbidden,
            capturer=forbidden,
            business_object_ground_truth_loader=forbidden,
            evaluator=forbidden,
        )

    assert calls == ["ingest"]
