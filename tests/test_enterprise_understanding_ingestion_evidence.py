from __future__ import annotations

from copy import deepcopy

from benchmark_evaluator.enterprise_understanding import (
    measure_ingestion_evidence,
    run_benchmark,
)


def _source_item(
    source_id: str,
    filename: str,
    detected_format: str,
    *,
    formal: int = 2,
    traceable: int = 2,
    exact: int = 2,
    structure_status: str = "COMPLETE",
    evidence_status: str = "PASS",
    unsupported: list[dict] | None = None,
    conflicts: int = 0,
) -> dict:
    return {
        "source_id": source_id,
        "filename": filename,
        "format": detected_format,
        "plain_text": "业务规则",
        "blocks": [
            {
                "block_id": f"{source_id}:block:{index}",
                "type": "PARAGRAPH",
                "region": "body",
                "text": f"规则{index}",
                "source_locator": f"{filename}#block={index};chars=0-3",
            }
            for index in range(1, formal + 1)
        ],
        "structure_receipt": {
            "status": structure_status,
            "unsupported_content_count": len(unsupported or []),
        },
        "evidence_closure_receipt": {
            "status": evidence_status,
            "source_id": source_id,
            "filename": filename,
            "formal_authority_block_count": formal,
            "source_hash_bound_block_count": formal,
            "traceable_authority_block_count": traceable,
            "exact_address_authority_block_count": exact,
            "untraceable_authority_block_count": max(0, formal - traceable),
            "weak_address_authority_block_count": max(0, traceable - exact),
            "locator_conflict_count": conflicts,
        },
        "ingestion_pipeline_receipt": {"status": "PASS"},
        "parsing_plan": {"status": "PASS"},
        "adapter_receipts": [{"adapter_name": f"{detected_format}_adapter"}],
        "unsupported_content": unsupported or [],
    }


def _asset(items: list[dict], active_source_ids: list[str]) -> dict:
    return {
        "source_inventory": [
            {"source_id": source_id, "status": "active"}
            for source_id in active_source_ids
        ],
        "sources": [
            {"source_id": source_id, "filename": f"{source_id}.dat"}
            for source_id in active_source_ids
        ],
        "document_structure_assets": {
            "schema": "qualibug.enterprise-document-structure-assets.v1",
            "source_count": len(items),
            "items": items,
            "errors": [],
        },
        "enterprise_understanding_model": {
            "business_objects": [
                {
                    "object_id": "object:ticket",
                    "name": "工单",
                    "status": "CONFIRMED",
                    "evidence": [{"source_id": active_source_ids[0]}],
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
        },
    }


def _ground_truth(source_id: str) -> dict:
    return {
        "schema": "qualibug.enterprise-understanding-ground-truth.v1",
        "project_id": "document-benchmark",
        "scope_complete": False,
        "business_objects": [
            {
                "ground_truth_id": "gt:object:ticket",
                "canonical_name": "工单",
                "criticality": "P0",
                "source_refs": [source_id],
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


def test_exact_receipts_pass_integrity_but_do_not_self_certify_structure_recall() -> None:
    asset = _asset(
        [
            _source_item("source:rules", "rules.docx", "docx"),
            _source_item("source:api", "openapi.yaml", "openapi"),
        ],
        ["source:rules", "source:api"],
    )

    result = measure_ingestion_evidence(asset)
    summary = result["summary"]

    assert result["status"] == "PASS"
    assert summary["source_structure_coverage_rate"] == 1.0
    assert summary["ingestion_acceptance_rate"] == 1.0
    assert summary["source_traceability_rate"] == 1.0
    assert summary["exact_address_rate"] == 1.0
    assert summary["receipt_integrity_gate_pass"] is True
    assert summary["five_of_five_readiness_status"] == (
        "RECEIPT_INTEGRITY_PASS_GROUND_TRUTH_RECALL_NOT_MEASURED"
    )
    assert summary["structure_block_recall_measured"] is False
    assert summary["table_reconstruction_recall_measured"] is False
    assert summary["product_receipts_are_not_ground_truth"] is True


def test_weak_address_is_visible_and_never_counted_as_exact() -> None:
    asset = _asset(
        [_source_item("source:rules", "rules.docx", "docx", exact=1)],
        ["source:rules"],
    )

    result = measure_ingestion_evidence(asset)

    assert result["summary"]["exact_address_rate"] == 0.5
    assert result["summary"]["receipt_integrity_gate_pass"] is False
    assert result["summary"]["highest_impact_gap"] == "DOCUMENT_EVIDENCE_ADDRESS_WEAK"
    assert result["evidence_address_analysis"]["weak_address_authority_block_count"] == 1


def test_active_source_without_document_structure_cannot_be_hidden_by_other_sources() -> None:
    asset = _asset(
        [_source_item("source:rules", "rules.docx", "docx")],
        ["source:rules", "source:missing"],
    )

    result = measure_ingestion_evidence(asset)

    assert result["summary"]["source_structure_coverage_rate"] == 0.5
    assert result["summary"]["ingestion_acceptance_rate"] == 0.5
    assert result["summary"]["highest_impact_gap"] == "DOCUMENT_SOURCE_STRUCTURE_MISSING"
    assert result["structure_loss_analysis"]["missing_document_structure_source_ids"] == [
        "source:missing"
    ]


def test_pass_receipt_with_critical_gap_is_reported_as_silent_loss_risk() -> None:
    critical_gap = {
        "reason_code": "PDF_TABLE_REGION_NOT_CELL_PARSED",
        "count": 1,
        "blocks_formal_understanding": True,
    }
    asset = _asset(
        [
            _source_item(
                "source:rules",
                "rules.pdf",
                "pdf",
                unsupported=[critical_gap],
            )
        ],
        ["source:rules"],
    )

    result = measure_ingestion_evidence(asset)
    loss = result["structure_loss_analysis"]

    assert result["summary"]["receipt_integrity_gate_pass"] is False
    assert result["summary"]["highest_impact_gap"] == (
        "DOCUMENT_STRUCTURE_CRITICAL_CONTENT_UNRESOLVED"
    )
    assert loss["silent_loss_risk_source_count"] == 1
    assert loss["silent_loss_risk_sources"][0]["reasons"] == [
        "STRUCTURE_PASS_WITH_CRITICAL_GAPS"
    ]


def test_existing_benchmark_emits_ingestion_outputs_without_mutating_product_asset(
    tmp_path,
) -> None:
    asset = _asset(
        [_source_item("source:rules", "rules.docx", "docx")],
        ["source:rules"],
    )
    before = deepcopy(asset)

    result = run_benchmark(
        _ground_truth("source:rules"),
        asset,
        output_dir=str(tmp_path),
    )

    assert result["ingestion_evidence_measurement"]["status"] == "PASS"
    assert result["workflow_receipt"]["product_ingestion_receipts_are_ground_truth"] is False
    assert result["next_ingestion_repair_target"] == "NONE"
    assert asset == before
    assert (tmp_path / "ingestion_metric_summary.json").exists()
    assert (tmp_path / "evidence_address_analysis.json").exists()
    assert (tmp_path / "structure_loss_analysis.json").exists()
    assert (tmp_path / "format_coverage_analysis.json").exists()
    report = (tmp_path / "report.md").read_text(encoding="utf-8")
    assert "多源接入与证据定位回执测量" in report
    assert "不能由产品自证为100%" in report
