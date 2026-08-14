from __future__ import annotations

import json

from ai_test_asset_center.formal_evidence_projection import (
    project_formal_evidence,
)


def _result(*, include_finding: bool = True) -> dict:
    finding = (
        {
            "finding_id": "finding_ui_1",
            "canonical_defect_id": "defect_ui_1",
            "title": "Approved state is not rendered",
            "category": "ui_state_consistency",
            "risk_family": "state",
            "surface": "UI",
            "severity": "high",
            "status": "confirmed",
        }
        if include_finding
        else None
    )
    return {
        "schema_version": "qualibug.discovery-runtime.v3",
        "findings": [finding] if finding else [],
        "experiment_execution": {
            "results": {
                "obl_ui_1": {
                    "experiment_id": "exp_ui_1",
                    "obligation_id": "obl_ui_1",
                    "status": "EXECUTED",
                    "reason_code": "",
                    "elapsed_ms": 125,
                    "steps": [{
                        "step_id": "step_approve",
                        "phase": "treatment",
                        "operation_ref": "bir_op_approve",
                        "actor_ref": "actor_admin",
                        "method": "POST",
                        "path": "/orders/123/approve",
                        "status_code": 200,
                        "body": {"access_token": "must-never-enter-evidence-graph"},
                        "headers": {"authorization": "Bearer secret"},
                    }],
                    "observer_receipts": [{
                        "schema_version": "qualibug.observer-receipt.v1",
                        "receipt_id": "obs_ui_1",
                        "campaign_id": "campaign_1",
                        "execution_id": "execution_1",
                        "observer_id": "ui_render_state",
                        "status": "OBSERVED",
                        "reason_code": "",
                        "evidence": {
                            "screenshot_path": "/private/raw/screenshot.png",
                            "dom": "raw-dom-must-not-enter-projection",
                        },
                    }],
                    "contract_evidence_receipts": [{
                        "receipt_id": "contract_1",
                        "schema_version": "qualibug.contract-evidence.v1",
                        "status": "OBSERVED",
                        "reason_code": "",
                        "payload": {"secret": "must-not-enter-projection"},
                    }],
                    "oracle_verdict": {
                        "status": "VIOLATION",
                        "verdict": "confirmed_violation",
                        "reason_codes": ["UI_STATE_NOT_RENDERED"],
                        "assertions": [{
                            "assertion_id": "assertion_1",
                            "status": "VIOLATION",
                            "actual": {"raw": "must-not-enter-projection"},
                        }],
                    },
                    "finding": finding,
                },
            },
        },
        "evidence_graphs": [],
        "execution_trace_summaries": [],
        "ui_findings": [],
    }


def test_execution_receipts_become_redacted_formal_evidence_graph() -> None:
    projected = project_formal_evidence(_result())

    assert len(projected["evidence_graphs"]) == 1
    assert len(projected["execution_trace_summaries"]) == 1
    assert [row["finding_id"] for row in projected["ui_findings"]] == [
        "finding_ui_1"
    ]
    receipt = projected["formal_evidence_projection_receipt"]
    assert receipt["status"] == "PROJECTED"
    assert receipt["new_findings_created"] == 0
    assert receipt["raw_payloads_included"] is False

    graph = projected["evidence_graphs"][0]
    assert graph["coverage"] == {
        "step_count": 1,
        "observer_receipt_count": 1,
        "contract_evidence_receipt_count": 1,
        "oracle_present": True,
        "existing_finding_present": True,
    }
    graph_json = json.dumps(graph, ensure_ascii=False, sort_keys=True)
    for forbidden in (
        "must-never-enter-evidence-graph",
        "Bearer secret",
        "raw-dom-must-not-enter-projection",
        "/private/raw/screenshot.png",
        "must-not-enter-projection",
    ):
        assert forbidden not in graph_json


def test_projection_never_promotes_evidence_into_a_new_finding() -> None:
    projected = project_formal_evidence(_result(include_finding=False))

    assert projected["findings"] == []
    assert projected["ui_findings"] == []
    assert projected["formal_evidence_projection_receipt"][
        "new_findings_created"
    ] == 0
    assert projected["evidence_graphs"][0]["coverage"][
        "existing_finding_present"
    ] is False


def test_public_runtime_exports_full_quality_projection_chain() -> None:
    from ai_test_asset_center import discovery_runtime
    from ai_test_asset_center.discovery_runtime_quality_projection import (
        run_experiment_candidate,
    )

    # discovery_runtime adds scan-stage progress marking around the quality
    # projection entry, so it is a distinct wrapper, not a raw re-export.
    assert discovery_runtime.run_experiment_candidate is not run_experiment_candidate
