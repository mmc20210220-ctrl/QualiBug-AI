# -*- coding: utf-8 -*-
"""Zero-transport governance blocks must not seal HARNESS_CONNECTION_FAILED."""
from __future__ import annotations

import time
from unittest.mock import patch

from ai_test_asset_center.experiment_outcome_finalizer import finalize_experiment_execution


def test_finalize_seals_blocked_when_before_observed_write_not_attempted() -> None:
    steps_out = [
        {
            "phase": "control",
            "step_id": "control_1",
            "method": "POST",
            "path": "/api/orders/ord-1/confirm",
            "status_code": 0,
            "error": "governed_write_identity_unobservable",
            "operation_ref": "bir_example",
            "governance_receipt": {
                "status": "blocked",
                "reason": "governed_write_identity_unobservable",
                "write_request_attempt_count": 0,
                "http_attempt_count": 1,
                "before": {"status": 404, "body": {"error": "not_found"}},
                "write": {
                    "status": 0,
                    "error": "governed_write_identity_unobservable",
                },
                "after": {},
            },
        }
    ]
    soft_verdict = {
        "verdict": "indeterminate",
        "customer_deliverable_candidate": False,
        "failed_assertions": [],
        "field_oracle_traces": [],
        "missing_requirements": [],
    }
    with patch(
        "ai_test_asset_center.experiment_outcome_finalizer.evaluate_contract_oracle",
        return_value=soft_verdict,
    ):
        result = finalize_experiment_execution(
            exp={
                "experiment_id": "exp_gov_block",
                "obligation_id": "obl_gov_block",
                "campaign_id": "cmp_gov_block",
                "execution_id": "run_gov_block",
                "assertions": [{"kind": "http_status_class"}],
                "source_refs": [{"source_id": "src_x", "ref": "rule"}],
                "safety_contract": {"governed_write": True},
                "control_plan": [{"step_id": "control_1"}],
                "treatment_plan": [],
            },
            steps_out=steps_out,
            observations={},
            contract_evidence_receipts=[],
            fixture_receipts=[],
            binding_materialization_receipts=[],
            pre_transport_block_reasons=[],
            cleanup_failures=0,
            runtime_bindings={},
            ops={},
            actors={},
            eid="exp_gov_block",
            oid="obl_gov_block",
            campaign_id="cmp_gov_block",
            resolved_campaign_id="cmp_gov_block",
            resolved_execution_id="run_gov_block",
            started=time.time(),
        )
    assert result["status"] == "BLOCKED"
    assert result["reason_code"] == "BLOCKED_MISSING_BINDING"
    assert "governed_write_identity_unobservable" in result["detail"]
    assert result["status"] != "HARNESS_FAILURE"
