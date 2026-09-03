from __future__ import annotations

from pathlib import Path

import benchmark_evaluator.product_quality.current_product_audit as audit


ROOT = Path(__file__).resolve().parents[1]


def test_review_truth_loads_only_after_all_selected_product_captures(
    monkeypatch,
    tmp_path: Path,
) -> None:
    calls: list[str] = []

    def fake_capture(repo_root, output_root, sample_id, source_specs):
        assert repo_root == ROOT
        assert output_root == tmp_path
        assert source_specs == audit.SAMPLE_SPECS[sample_id]
        calls.append(f"capture:{sample_id}")
        return (
            {
                "sample_id": sample_id,
                "status": "CAPTURED",
                "measurement_status": "PENDING_HUMAN_REVIEW",
            },
            {"findings": []},
            {"obligations": [], "test_designs": []},
        )

    def fake_load(repo_root):
        assert repo_root == ROOT
        calls.append("load_review_truth")
        return {"anchors": []}

    monkeypatch.setattr(audit, "_capture_product_sample", fake_capture)
    monkeypatch.setattr(audit, "_load_review_anchors", fake_load)

    summary = audit.capture_current_product_audit(
        root=ROOT,
        output_dir=tmp_path,
        samples=["object_source_conflict", "benchmark_mall", "warehouse_e"],
    )

    assert calls == [
        "capture:object_source_conflict",
        "capture:benchmark_mall",
        "capture:warehouse_e",
        "load_review_truth",
    ]
    assert summary["status"] == "CAPTURED"
    assert summary["review_truth_loaded_after_all_product_capture"] is True
    assert summary["self_scored_model_quality"] is False
    assert summary["human_scoring_required"] is True
