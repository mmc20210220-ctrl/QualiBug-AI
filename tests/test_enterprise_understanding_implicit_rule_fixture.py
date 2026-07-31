from __future__ import annotations

import hashlib
import json
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
    assert statuses == {
        "gt:implicit-v1:idempotency": "ACTIVE",
        "gt:implicit-v1:cardinality-pending": "PENDING_VALIDATION",
        "gt:implicit-v1:retired-conservation": "STALE",
        "gt:implicit-v1:example-field-hard-negative": "ABSENT",
    }


def test_frozen_statuses_follow_current_execution_capabilities():
    ground_truth = load_implicit_rule_ground_truth(FIXTURE / "ground_truth.json")
    by_id = {
        row["ground_truth_id"]: row
        for row in ground_truth["rules"]
    }

    assert by_id["gt:implicit-v1:idempotency"]["execution_required"] is True
    assert by_id["gt:implicit-v1:cardinality-pending"]["execution_required"] is False
    assert by_id["gt:implicit-v1:retired-conservation"]["execution_required"] is False
    assert by_id["gt:implicit-v1:cardinality-pending"]["match"] == {
        "logical_form": "cardinality",
        "operator": "cardinality",
    }
    assert by_id["gt:implicit-v1:retired-conservation"]["match"] == {
        "logical_form": "conservationequation",
        "operator": "equationholds",
    }


def test_minimal_openapi_fixture_adds_no_unannotated_rule_candidates():
    path = FIXTURE / "payment_api.openapi.json"
    content = path.read_text(encoding="utf-8")
    document = json.loads(content)
    operation = document["paths"]["/payments"]["post"]

    assert operation["operationId"] == "submitPayment"
    assert operation["description"] == (
        "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"
    )
    assert "同一付款请求" not in content
    assert "不得重复" not in content
    assert "requestBody" not in operation
    assert '"required"' not in content
    assert '"properties"' not in content
