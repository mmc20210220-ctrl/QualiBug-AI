from __future__ import annotations

from ai_test_asset_center import discovery_runtime_planning as planning
from ai_test_asset_center.behavior_ir import _fact_node, _relation_node, empty_behavior_ir
from ai_test_asset_center.enterprise_knowledge_center import _api as knowledge_api
from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding.scenario_ir_asset_governance import (
    project_scenario_ir_asset_governance,
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
        "receipt_id": "source-receipt-job-scenario-gate",
    }


def _canonical_job_ir() -> dict:
    source_refs = [_source_ref()]
    actor_ref = "bir_actor_job_runner_scenario_gate"
    operation_ref = "bir_operation_report_daily_scenario_gate"
    invariant_ref = "bir_invariant_report_daily_scenario_gate"
    contract = {
        "job_asset_ref": "job-asset-report-daily-scenario-gate",
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
    }

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
                "async_contract": contract,
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
                "description": "read-only Job reaches its source-declared success terminal",
                "expression": {
                    "kind": INVARIANT_KIND,
                    "operator": "must_reach_source_declared_success_terminal",
                    "operands": [],
                    "raw": "report-daily",
                },
                "operation_refs": [operation_ref],
                "source_job_asset_id": "job-asset-report-daily-scenario-gate",
                "source_behavior_id": "business-behavior-report-daily-scenario-gate",
                "job_actor_ref": actor_ref,
                "async_contract": contract,
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


def test_scenario_gate_blocks_legacy_probes_but_not_canonical_job_compilation(
    monkeypatch,
) -> None:
    # Make the compatibility path visibly capable of producing a legacy probe,
    # then install the Scenario IR guard over that exact existing authority.
    monkeypatch.setattr(
        knowledge_api,
        "_probes_from_asset",
        lambda asset, max_count=140: [{"probe_id": "legacy-risk-probe"}],
    )
    gated_asset = {
        "scenario_planning_gate": {"scenario_planning_allowed": True},
        "scenario_ir_gate": {
            "entry_allowed": False,
            "status": "NO_SCENARIO_IR_COMPILED",
        },
        "scenario_execution_contract_gate": {"entry_allowed": False},
        "scenario_ir": [],
        "coverage_gaps": [],
    }
    project_scenario_ir_asset_governance(gated_asset, {})

    assert knowledge_api._probes_from_asset(gated_asset) == []

    # Job compilation is source-bound through canonical Behavior IR and the
    # existing obligation/protocol/compiler chain; it must not depend on the
    # blocked legacy probe compatibility path.
    register_job_async_protocol()
    install_source_job_obligation_binding()
    behavior_ir = _canonical_job_ir()
    obligation_pack = compile_job_obligations(
        behavior_ir,
        {"obligations": [], "coverage_gaps": [], "count": 0, "gap_count": 0},
    )
    assert obligation_pack["source_job_obligation_binding_receipt"]["status"] == "BOUND"
    assert obligation_pack["count"] == 1

    compiled = planning.compile_experiments(
        obligation_pack["obligations"],
        behavior_ir=behavior_ir,
        environment_type="sandbox",
        available_adapters={"http_api"},
    )

    assert compiled["count"] == 1
    experiment = compiled["experiments"][0]
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["treatment_plan"][0]["step_id"] == "job_treatment_1"
    assert experiment["treatment_plan"][0]["method"] == "JOB"
    assert experiment["assertion"]["runtime_integrity_only"] is True
    assert experiment["assertion"]["formal_business_finding_eligible"] is False
    assert experiment["async_job_lineage_receipt"]["identity_complete"] is True
    assert experiment["async_job_lineage_receipt"]["identity_drift"] is False
