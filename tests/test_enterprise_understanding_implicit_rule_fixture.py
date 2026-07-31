from __future__ import annotations

import hashlib
from pathlib import Path

from benchmark_evaluator.enterprise_understanding.implicit_rules import (
    load_implicit_rule_ground_truth,
)


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = (
    ROOT
    / "benchmark_evaluator"
    / "enterprise_understanding"
    / "fixtures"
    / "implicit_rules_v1"
)


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    header = f"blob {len(payload)}\0".encode("utf-8")
    return hashlib.sha1(header + payload).hexdigest()


def test_frozen_implicit_rule_ground_truth_references_exact_git_blobs():
    ground_truth = load_implicit_rule_ground_truth(FIXTURE / "ground_truth.json")

    assert ground_truth["validation_receipt"]["status"] == "PASS"
    assert ground_truth["validation_receipt"]["candidate_universe_complete"] is True
    assert ground_truth["validation_receipt"]["positive_rule_count"] == 3
    assert ground_truth["validation_receipt"]["hard_negative_rule_count"] == 1

    for source in ground_truth["source_snapshot"]:
        source_path = ROOT / source["path"]
        assert source_path.exists()
        assert _git_blob_sha(source_path) == source["blob_sha"]

    statuses = {
        row["ground_truth_id"]: row["expected_status"]
        for row in ground_truth["rules"]
    }
    assert statuses["gt:implicit-v1:retired-cardinality"] == "STALE"
    assert statuses["gt:implicit-v1:example-field-hard-negative"] == "ABSENT"
