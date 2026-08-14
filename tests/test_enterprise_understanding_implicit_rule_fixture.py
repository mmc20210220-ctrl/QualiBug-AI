from __future__ import annotations

import hashlib
import json
from pathlib import Path

from ai_test_asset_center.enterprise_knowledge_center._parsing import _parse_source
from ai_test_asset_center.enterprise_knowledge_center.document_ingestion import (
    build_document_structure_ir,
)
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.interface_runtime_contracts import (
    install_interface_runtime_contract_parser,
)
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
IDEMPOTENCY = "同一付款请求不得重复成功扣款；重复提交时业务成功效果最多发生一次。"


def _git_blob_sha(path: Path) -> str:
    payload = path.read_bytes()
    # The frozen SHAs are the *git blob* SHAs. With core.autocrlf=true a Windows
    # checkout materializes text files with CRLF, but git stores the normalized
    # LF bytes in the blob. Hash the LF-normalized content so the frozen git
    # blobs match on every platform without depending on the checkout's EOL.
    payload = payload.replace(b"\r\n", b"\n")
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
    assert operation["description"] == IDEMPOTENCY
    assert "同一付款请求" not in content
    assert "不得重复" not in content
    assert "requestBody" not in operation
    assert '"required"' not in content
    assert '"properties"' not in content


def test_openapi_parser_decodes_operation_prose_without_creating_text_rule():
    install_interface_runtime_contract_parser()
    path = FIXTURE / "payment_api.openapi.json"
    parsed = _parse_source(
        path.read_bytes(),
        path.name,
        "openapi",
        "source:payment-api",
    )

    assert IDEMPOTENCY not in parsed["text"]
    assert parsed["rules"] == []
    assert len(parsed["operations"]) == 1
    operation = parsed["operations"][0]
    assert operation["interface_id"] == "api:POST:/payments"
    assert operation["operation_id"] == "submitPayment"
    assert operation["openapi_description"] == IDEMPOTENCY
    assert IDEMPOTENCY in operation["source_excerpt"]
    assert operation["source_excerpt_authority"] == (
        "OPENAPI_OPERATION_SUMMARY_DESCRIPTION"
    )


def test_openapi_document_ir_keeps_structure_but_excludes_prose_from_plain_text():
    path = FIXTURE / "payment_api.openapi.json"
    structure = build_document_structure_ir(
        path.read_bytes(),
        filename=path.name,
        source_id="source:payment-api",
        declared_mime="application/json",
        legacy_text=path.read_text(encoding="utf-8"),
    )

    assert IDEMPOTENCY not in str(structure.get("plain_text") or "")
    operation_blocks = [
        row
        for row in structure.get("blocks") or []
        if isinstance(row, dict) and row.get("node_kind") == "OPENAPI_OPERATION"
    ]
    assert len(operation_blocks) == 1
    operation_block = operation_blocks[0]
    assert operation_block["json_pointer"] == "/paths/~1payments/post"
    assert operation_block["excluded_from_plain_text_projection"] is True
    assert operation_block["structure_evidence"]["business_semantics_added"] is False
