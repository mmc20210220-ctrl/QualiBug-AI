from __future__ import annotations

import json
from pathlib import Path

import pytest

from ai_test_asset_center.campaign_api_contract import (
    CampaignContractError,
    build_campaign_view,
    build_evaluation_submission,
    create_campaign,
)
from ai_test_asset_center.discovery_mainline_contract import build_mainline_run_contract
from ai_test_asset_center.enterprise_knowledge_center import _parse_source


def _deliverable() -> dict:
    return {
        "id": "finding-source-derived",
        "finding_id": "finding-source-derived",
        "evidence_id": "evidence-source",
        "execution_id": "exec-source",
        "experiment_id": "exp-source",
        "obligation_id": "obl-source",
        "slice_id": "slice-source",
        "candidate_id": "cand-source",
        "title": "Source-derived authorization invariant violated",
        "gate_passed": True,
        "customer_delivery_status": "defect",
        "bug_status": "reproduced",
        "confirmation_status": "confirmed",
        "execution_status": "executed",
        "evidence_quality": {"level": "validated", "score": 95, "can_reproduce": True},
        "evidence_status": {
            "semantic_verdict": "SEMANTIC_CONFIRMED",
            "business_evidence_status": "VALIDATED",
            "final_review_status": "CUSTOMER_READY",
            "missing_requirements": [],
        },
        "raw_evidence": {
            "has_real_evidence": True,
            "timestamp": "2026-07-10T00:00:00Z",
            "request_raw": {"method": "GET", "path": "/source-derived-resource"},
            "response_raw": {"status_code": 200, "body": {"visible": True}},
        },
        "reproduction": {
            "method": "GET",
            "path": "/source-derived-resource",
            "is_synthetic": False,
            "har_evidence": {"status_code": 200, "response_body": {"visible": True}},
        },
        "expected": "access denied",
        "actual": "access granted",
        "timestamp": "2026-07-10T00:00:00Z",
    }


def _write_scan(root: Path, *, evaluation_mode: str = "operational") -> None:
    output = root / "platform_outputs" / "project-a"
    output.mkdir(parents=True)
    mainline_run = build_mainline_run_contract(
        mainline_authority="experiment_candidate",
        run_id="scan-source-derived",
        campaign_id="cmp-source-derived",
        target_id="qa-target-a",
        environment_id="qa-target-a",
        policy_version="v2",
        evaluation_mode=evaluation_mode,
    )
    deliverable = _deliverable()
    deliverable["mainline_run"] = {
        "contract_fingerprint": mainline_run["contract_fingerprint"]
    }
    rejected = {
        "id": "rejected",
        "finding_id": "rejected",
        "title": "not executed",
        "mainline_run": {"contract_fingerprint": mainline_run["contract_fingerprint"]},
    }
    candidate = {
        "id": "candidate",
        "finding_id": "candidate",
        "title": "needs observer",
        "mainline_run": {"contract_fingerprint": mainline_run["contract_fingerprint"]},
    }
    payload = {
        "scan_id": "scan-source-derived",
        "mainline_run": mainline_run,
        "campaign": {
            "campaign_id": "cmp-source-derived",
            "campaign_status": "completed",
            "project_id": "project-a",
            "environment_ref": "qa-target-a",
        },
        "pipeline_health": {"status": "DEGRADED", "cleanup_failure_count": 0},
        "findings": [deliverable, rejected],
        "candidate_findings": [candidate],
        "v12": {
            "mainline_run": mainline_run,
            "total_duration_ms": 1200,
            "test_obligations": {
                "count": 1,
                "obligations": [{"obligation_id": "obl-source", "subject_refs": []}],
            },
            "experiment_execution": {
                "selected_count": 1,
                "executed_count": 1,
                "blocked_count": 0,
                "harness_failure_count": 0,
                "results": [{
                    "candidate_id": "cand-source",
                    "slice_id": "slice-source",
                    "obligation_id": "obl-source",
                    "experiment_id": "exp-source",
                    "execution_id": "exec-source",
                    "evidence_id": "evidence-source",
                    "campaign_id": "cmp-source-derived",
                    "status": "EXECUTED",
                    "reason_code": "",
                    "execution_receipt": {
                        "status": "EXECUTED",
                        "execution_id": "exec-source",
                        "evidence_id": "evidence-source",
                    },
                    "finding": {"id": "finding-source-derived"},
                }],
            },
            "runtime_contract": {
                "target_policy_decision": {"schema_version": "qualibug.target-policy-decision.v1"}
            },
        },
    }
    (output / "scan_result.json").write_text(json.dumps(payload), encoding="utf-8")


def test_campaign_creation_requires_explicit_environment_identity_and_exact_url(tmp_path: Path) -> None:
    ready = create_campaign(tmp_path, "project-a", {
        "target_url": "https://qa.example.test",
        "approved_base_url": "https://qa.example.test",
        "environment_type": "qa",
        "environment_ref": "qa-target-a",
    })
    assert ready["status"] == "ready"
    assert ready["target_policy_decision"]["write_allowed"] is True
    assert Path(ready["artifact_ref"]).is_file()

    unknown = create_campaign(tmp_path, "project-a", {
        "target_url": "https://qa.example.test",
        "approved_base_url": "https://qa.example.test",
        "environment_ref": "qa-target-a",
    })
    assert unknown["status"] == "draft"
    assert "UNKNOWN_ENVIRONMENT" in unknown["blocking_codes"]


def test_campaign_view_rejects_flags_only_formal_claim_and_keeps_identity_trace(tmp_path: Path) -> None:
    _write_scan(tmp_path)
    view = build_campaign_view(tmp_path, "project-a", "cmp-source-derived")
    assert view["status"] == "partial"
    assert view["pipeline_health"] == "DEGRADED"
    assert view["execution_status"] == "completed"
    assert view["formal_count_projection"]["formal_customer_deliverable_count"] == 0
    assert view["finding_classification"]["counts"] == {
        "deliverable": 0,
        "candidate": 1,
        "rejected": 2,
        "shadow": 0,
    }
    assert view["every_selected_experiment_has_receipt"] is True
    assert view["complete_identity_trace_count"] == 1


def test_evaluation_submission_rejects_flags_only_scope_without_attempt_ledger(
    tmp_path: Path,
) -> None:
    _write_scan(tmp_path)
    with pytest.raises(
        CampaignContractError,
        match="formal_delivery_attempt_ledger_missing",
    ):
        build_evaluation_submission(
            tmp_path,
            "project-a",
            {"evaluation_mode": "operational"},
        )


def test_product_submission_rejects_shadow_run(tmp_path: Path) -> None:
    _write_scan(tmp_path, evaluation_mode="shadow")

    with pytest.raises(
        CampaignContractError,
        match="product_evaluation_submission_not_authorized",
    ):
        build_evaluation_submission(
            tmp_path,
            "project-a",
            {"evaluation_mode": "shadow"},
        )


def test_parser_receipt_records_bad_yaml_without_silently_dropping_source() -> None:
    parsed = _parse_source(b"openapi: [broken", "contract.yaml", "openapi", "src-yaml")
    receipt = parsed["parser_receipt"]
    assert receipt["schema_version"] == "qualibug.parser-receipt.v1"
    assert receipt["detected_format"] == "yaml"
    assert receipt["parser_status"] == "degraded"
    assert receipt["fidelity"] == "degraded"
    assert receipt["text_hash"]
    assert receipt["errors"][0]["code"] == "YAML_PARSE_FAILED"


def test_parser_receipt_records_markdown_without_operations() -> None:
    parsed = _parse_source(b"# API\nNo method or path is declared.", "contract.md", "markdown_api", "src-md")
    assert parsed["parser_receipt"]["parser_status"] == "degraded"
    assert parsed["parser_receipt"]["errors"][0]["code"] == "MARKDOWN_API_NO_OPERATIONS"


def test_active_discovery_modules_have_no_fixed_vertical_routes_or_benchmark_ids() -> None:
    root = Path(__file__).resolve().parents[1]
    active = (
        "system_behavior_space.py",
        "private_pilot_system_behavior_space_patch.py",
        "reasoner_prompt.py",
        "runtime_binding_graph.py",
        "experiment_compiler.py",
        "fixture_dag.py",
        "v12_pipeline.py",
    )
    forbidden = (
        "benchmark_mall",
        "/api/orders",
        "/api/products",
        "/api/coupons",
        "/api/cart",
        "/api/payments",
        "/api/refunds",
    )
    for filename in active:
        source = (root / "ai_test_asset_center" / filename).read_text(encoding="utf-8")
        for token in forbidden:
            assert token not in source, f"{filename} contains fixed active-route token {token}"
