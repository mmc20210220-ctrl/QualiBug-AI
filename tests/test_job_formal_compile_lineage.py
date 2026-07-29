from __future__ import annotations

from ai_test_asset_center import discovery_runtime_planning as planning
from ai_test_asset_center.behavior_ir import (
    _fact_node,
    _relation_node,
    empty_behavior_ir,
)
from ai_test_asset_center.job_async_protocol import register_job_async_protocol
from ai_test_asset_center.source_job_contract_binding import INVARIANT_KIND
from ai_test_asset_center.source_job_obligation_binding import (
    compile_job_obligations,
    install_source_job_obligation_binding,
)


def _source_ref() -> dict:
    return {
        "source_id": "job-platform-export",
        "locator": "xxl-job://conn-job/jobs/report-daily",
        "kind": "job_platform",
        "receipt_id": "source-receipt-job-1",
    }


def _canonical_job_ir() -> dict:
    source_refs = [_source_ref()]
    actor_ref = "bir_actor_job_runner"
    operation_ref = "bir_operation_report_daily"
    invariant_ref = "bir_invariant_report_daily"
    ir = empty_behavior_ir()
    ir["actors"] = [
        _fact_node(
            node_id=actor_ref,
            typed_fields={
                "role": "job_runner",
                "account_ref": "job-runner-account",
                "credential_secret_ref": "secret_ref:test_accounts:job-runner-account",
                "runtime_bound": True,
            },
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
    ]
    ir["operations"] = [
        _fact_node(
            node_id=operation_ref,
            typed_fields={
                "operation_kind": "ASYNC_JOB",
                "method": "JOB",
                "path": "",
                "adapter": "job_platform",
                "read_write": "read",
                "actor_refs": [actor_ref],
                "async_contract": {
                    "job_asset_ref": "job-asset-report-daily",
                    "platform_type": "xxl_job",
                    "platform_job_id": "report-daily",
                    "connector_id": "conn-job",
                    "runtime": {
                        "connector_id": "conn-job",
                        "terminal_states": ["SUCCESS", "FAILED"],
                        "success_states": ["SUCCESS"],
                    },
                    "write_set": [],
                    "testability": {
                        "execution_status": "EXECUTION_READY",
                        "safety_level": "READ_ONLY",
                    },
                },
            },
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
    ]
    ir["invariants"] = [
        _fact_node(
            node_id=invariant_ref,
            typed_fields={
                "description": "report Job reaches a declared success terminal",
                "expression": {
                    "kind": INVARIANT_KIND,
                    "operator": "must_reach_source_declared_success_terminal",
                    "operands": [],
                    "raw": "report-daily",
                },
                "operation_refs": [operation_ref],
                "source_job_asset_id": "job-asset-report-daily",
                "source_behavior_id": "business-behavior-report-daily",
                "job_actor_ref": actor_ref,
                "async_contract": {
                    "job_asset_ref": "job-asset-report-daily",
                    "platform_type": "xxl_job",
                    "platform_job_id": "report-daily",
                    "connector_id": "conn-job",
                    "runtime": {
                        "connector_id": "conn-job",
                        "terminal_states": ["SUCCESS", "FAILED"],
                        "success_states": ["SUCCESS"],
                    },
                    "write_set": [],
                    "testability": {
                        "execution_status": "EXECUTION_READY",
                        "safety_level": "READ_ONLY",
                    },
                },
                "runtime_integrity_only": True,
                "formal_business_finding_eligible": False,
            },
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
    ]
    ir["relations"] = [
        _relation_node(
            relation_type="observes",
            from_ref=operation_ref,
            to_ref=invariant_ref,
            operation_ref=operation_ref,
            actor_ref=actor_ref,
            preconditions=[],
            effects=[{"kind": "job_runtime_integrity"}],
            source_refs=source_refs,
            confidence=1.0,
            derivation="explicit",
            status="accepted",
        )
    ]
    return ir


def test_installed_formal_compiler_seals_asset_to_experiment_lineage() -> None:
    register_job_async_protocol()
    install_source_job_obligation_binding()
    behavior_ir = _canonical_job_ir()
    obligation_pack = compile_job_obligations(
        behavior_ir,
        {"obligations": [], "coverage_gaps": [], "count": 0, "gap_count": 0},
    )

    compiled = planning.compile_experiments(
        obligation_pack["obligations"],
        behavior_ir=behavior_ir,
        environment_type="sandbox",
        available_adapters={"http_api"},
    )

    assert compiled["count"] == 1
    experiment = compiled["experiments"][0]
    lineage = experiment["async_job_lineage_receipt"]
    assert lineage["job_asset_id"] == "job-asset-report-daily"
    assert lineage["behavior_id"] == "business-behavior-report-daily"
    assert lineage["obligation_id"] == experiment["obligation_id"]
    assert lineage["experiment_id"] == experiment["experiment_id"]
    assert lineage["identity_complete"] is True
    assert lineage["identity_drift"] is False
    assert lineage["protocol_id"] == "process:source_declared_async_job_execution"
    assert experiment["compile_receipt"]["async_job_lineage_fingerprint"] == (
        lineage["fingerprint"]
    )
