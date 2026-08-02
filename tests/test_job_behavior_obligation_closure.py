from __future__ import annotations

from copy import deepcopy

from ai_test_asset_center.behavior_ir import empty_behavior_ir
from ai_test_asset_center.enterprise_knowledge_center.job_asset_governance import (
    normalize_job_definition_with_governance,
    to_async_operation_with_governance,
)
from ai_test_asset_center.enterprise_knowledge_center.job_behavior_projection import (
    project_job_behaviors,
    refresh_job_behavior_projection,
)
from ai_test_asset_center.experiment_compiler import compile_experiments
from ai_test_asset_center.job_async_protocol import (
    TEMPLATE_ASYNC_JOB_EXECUTION,
    register_job_async_protocol,
)
from ai_test_asset_center.source_job_contract_binding import (
    INVARIANT_KIND,
    bind_source_job_contracts,
)
from ai_test_asset_center.source_job_obligation_binding import compile_job_obligations


def _platform_evidence() -> dict:
    return {
        "source_id": "job-platform-export",
        "source_kind": "JOB_PLATFORM",
        "source_locator": "xxl-job://conn-job/jobs/report-daily",
        "connector_id": "conn-job",
        "external_ref": "job_platform:xxl_job",
        "quote": "report-daily",
    }


def _code_evidence() -> dict:
    return {
        "source_id": "source-code",
        "source_kind": "SOURCE_CODE",
        "source_locator": "src/ReportJob.java#L20-L60",
        "quote": '@XxlJob("report-daily")',
    }


def _raw_job(*, write: bool = False, single_source: bool = False) -> dict:
    evidence = [_platform_evidence()]
    if not single_source:
        evidence.append(_code_evidence())
    behavior = {
        "selection_predicates": [{"expression": "report_date = current_date"}],
        "object_refs": ["daily_report"],
        "read_set": ["daily_report.source_rows"],
        "write_set": ["daily_report.status"] if write else [],
        "expected_effects": [{"expression": "job reaches declared success terminal"}],
        "process_steps": [
            {
                "step_id": "aggregate-report",
                "name": "aggregate report",
                "read_set": ["daily_report.source_rows"],
                "write_set": ["daily_report.status"] if write else [],
            }
        ],
    }
    raw = {
        "platform_type": "xxl_job",
        "platform_job_id": "report-daily",
        "display_name": "Daily report aggregation",
        "service": "report-service",
        "handler": "ReportJob.handle",
        "actor_refs": ["actor-job-runner"],
        "connector_id": "conn-job",
        "trigger": {
            "type": "MANUAL",
            "manual_entry_ref": "adapter:trigger_job",
        },
        "runtime": {
            "trigger_ref": "adapter:trigger_job",
            "run_identity_ref": "adapter:get_job_run_id",
            "status_query_ref": "adapter:get_job_run",
            "step_query_ref": "adapter:list_job_steps",
            "log_query_ref": "adapter:get_job_log",
            "terminal_states": ["SUCCESS", "FAILED"],
            "success_states": ["SUCCESS"],
            "connector_id": "conn-job",
        },
        "behavior": behavior,
        "source_refs": evidence,
    }
    if write:
        raw["cleanup"] = {}
    return raw


def _enterprise_actor() -> dict:
    return {
        "schema": "qualibug.enterprise-business-actor.v1",
        "actor_id": "actor-job-runner",
        "name": "Job runner",
        "role": "job_runner",
        "role_key": "job_runner",
        "account_ref": "job-runner-account",
        "credential_secret_ref": "secret://test/job-runner",
        "runtime_bound": True,
        "evidence": [_platform_evidence()],
    }


def _knowledge_asset(job_asset: dict, operation: dict) -> dict:
    return {
        "enterprise_comprehension_gate": {"entry_allowed": True, "status": "PASS"},
        "job_assets": [job_asset],
        "enterprise_understanding_model": {
            "schema": "qualibug.enterprise-business-understanding-model.v1",
            "language_contract": "CHINESE_SOURCE_TEXT_IS_FACT_AUTHORITY",
            "quality_claim": "MODEL_COMPLETENESS_PROJECTION_NOT_RECALL",
            "business_objects": [
                {
                    "schema": "qualibug.enterprise-business-object.v1",
                    "object_id": "daily_report",
                    "name": "daily_report",
                    "evidence": [_code_evidence()],
                }
            ],
            "actors": [_enterprise_actor()],
            "operations": [operation],
            "object_relations": [],
            "lifecycles": [],
            "processes": [],
            "rules": [],
            "decision_matrix_row_ledger": [],
            "business_behaviors": [],
            "behavior_conflicts": [],
            "behavior_ir_gate": {
                "schema": "qualibug.enterprise-business-behavior-gate.v1",
                "status": "NOT_BUILT",
                "entry_allowed": False,
                "metrics": {},
            },
            "behavior_implementation_bindings": [],
            "implementation_binding_unknowns": [],
            "implementation_binding_conflicts": [],
            "implementation_evidence_index": [],
            "implementation_binding_gate": {
                "schema": "qualibug.business-behavior-implementation-binding-gate.v1",
                "status": "NOT_BUILT",
                "entry_allowed": False,
                "scenario_planning_allowed": False,
                "execution_allowed": False,
                "metrics": {},
            },
            "unknowns": [],
            "conflicts": [],
            "evidence_index": [_platform_evidence(), _code_evidence()],
            "source_summary": {},
            "metrics": {},
            "gate": {
                "schema": "qualibug.enterprise-understanding-model-gate.v1",
                "status": "NOT_BUILT",
                "entry_allowed": False,
                "critical_unknowns": [],
            },
        },
    }


def _confirmed_asset() -> tuple[dict, dict, dict]:
    job_asset = normalize_job_definition_with_governance(_raw_job())
    operation = to_async_operation_with_governance(job_asset)
    asset = _knowledge_asset(job_asset, operation)
    refreshed = refresh_job_behavior_projection(asset)
    behavior = next(
        row
        for row in refreshed["enterprise_understanding_model"]["business_behaviors"]
        if row["source_kind"] == "ASYNC_JOB_ASSET"
    )
    return refreshed, job_asset, behavior


def test_cross_source_read_only_job_becomes_confirmed_existing_behavior() -> None:
    job_asset = normalize_job_definition_with_governance(_raw_job())
    operation = to_async_operation_with_governance(job_asset)
    asset = _knowledge_asset(job_asset, operation)

    behaviors, ledger, lineages, gate = project_job_behaviors(
        asset,
        asset["enterprise_understanding_model"],
    )

    assert len(behaviors) == 1
    behavior = behaviors[0]
    assert behavior["schema"] == "qualibug.enterprise-business-behavior.v1"
    assert behavior["status"] == "CONFIRMED"
    assert behavior["formal_business_rule"] is True
    assert behavior["formal_business_finding_eligible"] is False
    assert behavior["operation_ref"] == operation["operation_id"]
    assert behavior["actor_refs"] == ["actor-job-runner"]
    assert behavior["async_runtime"]["connector_id"] == "conn-job"
    assert behavior["async_runtime"]["success_states"] == ["SUCCESS"]
    assert ledger[0]["formal_obligation_eligible"] is True
    assert lineages[0]["identity_complete"] is True
    assert gate["entry_allowed"] is True
    assert gate["metrics"]["job_asset_coverage_rate"] == 1.0


def test_single_source_job_stays_candidate_and_cannot_create_formal_obligation() -> None:
    job_asset = normalize_job_definition_with_governance(_raw_job(single_source=True))
    operation = to_async_operation_with_governance(job_asset)
    asset = _knowledge_asset(job_asset, operation)

    behaviors, ledger, _lineages, gate = project_job_behaviors(
        asset,
        asset["enterprise_understanding_model"],
    )

    assert behaviors[0]["status"] == "CANDIDATE"
    assert behaviors[0]["formal_business_rule"] is False
    assert ledger[0]["reason_code"] == "ASYNC_JOB_BEHAVIOR_NOT_CONFIRMED"
    assert ledger[0]["formal_obligation_eligible"] is False
    assert gate["entry_allowed"] is False


def test_write_job_is_visible_but_incomplete_before_canonical_binding() -> None:
    job_asset = normalize_job_definition_with_governance(_raw_job(write=True))
    operation = to_async_operation_with_governance(job_asset)
    asset = _knowledge_asset(job_asset, operation)

    behaviors, ledger, _lineages, _gate = project_job_behaviors(
        asset,
        asset["enterprise_understanding_model"],
    )

    assert behaviors[0]["status"] == "INCOMPLETE"
    assert "ASYNC_JOB_WRITE_CLEANUP_EXECUTION_NOT_CLOSED" in behaviors[0][
        "unresolved_semantics"
    ]
    assert ledger[0]["formal_obligation_eligible"] is False


def test_confirmed_job_binds_to_canonical_operation_actor_invariant_and_relation() -> None:
    asset, _job_asset, behavior = _confirmed_asset()
    canonical, receipt = bind_source_job_contracts(empty_behavior_ir(), asset)

    job_operations = [
        row for row in canonical["operations"] if row.get("operation_kind") == "ASYNC_JOB"
    ]
    job_invariants = [
        row
        for row in canonical["invariants"]
        if row.get("expression", {}).get("kind") == INVARIANT_KIND
    ]
    assert len(job_operations) == 1
    assert len(job_invariants) == 1
    invariant = job_invariants[0]
    assert invariant["source_job_asset_id"] == behavior["job_lineage"]["job_asset_id"]
    assert invariant["source_behavior_id"] == behavior["behavior_id"]
    assert invariant["formal_business_finding_eligible"] is False
    assert any(
        row.get("relation_type") == "observes"
        and row.get("from_ref") == job_operations[0]["id"]
        and row.get("to_ref") == invariant["id"]
        for row in canonical["relations"]
    )
    assert receipt["status"] == "BOUND"
    assert receipt["bound_invariant_count"] == 1


def test_job_invariant_generates_existing_process_obligation_with_lineage() -> None:
    register_job_async_protocol()
    asset, _job_asset, behavior = _confirmed_asset()
    canonical, _receipt = bind_source_job_contracts(empty_behavior_ir(), asset)
    result = compile_job_obligations(
        canonical,
        {"obligations": [], "coverage_gaps": [], "count": 0, "gap_count": 0},
    )

    assert result["count"] == 1
    obligation = result["obligations"][0]
    assert obligation["schema_version"] == "qualibug.test-obligation.v1"
    assert obligation["risk_family"] == "process"
    assert obligation["compile_status"] == "PENDING"
    assert obligation["property"]["template"] == TEMPLATE_ASYNC_JOB_EXECUTION
    assert obligation["property"]["source_behavior_id"] == behavior["behavior_id"]
    assert obligation["property"]["runtime_integrity_only"] is True
    assert obligation["property"]["formal_business_finding_eligible"] is False
    assert obligation["async_job_lineage_receipt"]["identity_complete"] is True
    assert result["source_job_obligation_binding_receipt"]["status"] == "BOUND"


def test_existing_compiler_dispatches_job_protocol_to_job_treatment_step() -> None:
    register_job_async_protocol()
    asset, _job_asset, _behavior = _confirmed_asset()
    canonical, _receipt = bind_source_job_contracts(empty_behavior_ir(), asset)
    obligations = compile_job_obligations(
        canonical,
        {"obligations": [], "coverage_gaps": [], "count": 0, "gap_count": 0},
    )["obligations"]

    compiled = compile_experiments(
        obligations,
        behavior_ir=canonical,
        environment_type="sandbox",
        available_adapters={"http_api"},
    )

    assert compiled["compiled_count"] == 1
    experiment = compiled["experiments"][0]
    assert experiment["compile_receipt"]["status"] == "COMPILED"
    assert experiment["risk_family"] == "process"
    assert len(experiment["treatment_plan"]) == 1
    step = experiment["treatment_plan"][0]
    assert step["step_id"] == "job_treatment_1"
    assert step["operation_ref"] == obligations[0]["required_operations"][0]
    assert step["actor_ref"] == obligations[0]["required_actors"][0]
    assert step["method"] == "JOB"
    assert step["intent"] == "source_declared_async_job_execution"
    assert experiment["assertions"][0]["property"][
        "formal_business_finding_eligible"
    ] is False


def test_non_job_obligation_is_preserved_by_job_binding() -> None:
    baseline_obligation = {
        "schema_version": "qualibug.test-obligation.v1",
        "obligation_id": "obl-existing",
        "risk_family": "validation",
        "declared_risk_family": "validation",
        "risk_family_resolution": {
            "declared": "validation",
            "canonical": "validation",
            "registered": True,
            "reason_code": "",
        },
        "subject_refs": ["inv-existing"],
        "property": {"invariant_ref": "inv-existing"},
        "required_actors": [],
        "required_operations": [],
        "required_fixtures": [],
        "required_observers": [],
        "cleanup_requirement": {},
        "source_refs": [],
        "relation_refs": [],
        "confidence": 1.0,
        "compile_status": "PENDING",
        "block_reason": "",
    }
    result = compile_job_obligations(
        empty_behavior_ir(),
        {
            "obligations": [deepcopy(baseline_obligation)],
            "coverage_gaps": [],
            "count": 1,
            "gap_count": 0,
        },
    )
    assert result["obligations"] == [baseline_obligation]
