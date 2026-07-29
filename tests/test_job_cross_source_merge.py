from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center.job_asset_governance import (
    merge_cross_source_job_assets,
    normalize_job_definition_with_governance,
    to_async_operation_with_governance,
)
from ai_test_asset_center.enterprise_knowledge_center.job_behavior_projection import (
    project_job_behaviors,
)


def _platform_source() -> dict:
    return {
        "source_id": "platform-export",
        "source_kind": "JOB_PLATFORM",
        "source_locator": "xxl-job://conn-job/jobs/report-daily",
        "connector_id": "conn-job",
        "external_ref": "job_platform:xxl_job",
    }


def _code_source() -> dict:
    return {
        "source_id": "code-source",
        "source_kind": "SOURCE_CODE",
        "source_locator": "src/ReportJob.java#L20-L60",
        "quote": '@XxlJob("report-daily")',
    }


def _platform_job(*, cron: str = "0 0 2 * * ?") -> dict:
    return normalize_job_definition_with_governance(
        {
            "platform_type": "xxl_job",
            "platform_job_id": "report-daily",
            "display_name": "日报聚合",
            "actor_refs": ["actor-job-runner"],
            "connector_id": "conn-job",
            "trigger": {
                "type": "CRON",
                "cron": cron,
                "manual_entry_ref": "adapter:trigger_job",
            },
            "runtime": {
                "trigger_ref": "adapter:trigger_job",
                "run_identity_ref": "adapter:get_job_run_id",
                "status_query_ref": "adapter:get_job_run",
                "terminal_states": ["SUCCESS", "FAILED"],
                "success_states": ["SUCCESS"],
                "connector_id": "conn-job",
            },
            "behavior": {
                "selection_predicates": [{"expression": "report_date = current_date"}],
                "object_refs": ["daily_report"],
                "read_set": ["daily_report.source_rows"],
                "write_set": [],
            },
            "source_refs": [_platform_source()],
        }
    )


def _code_job(*, cron: str = "") -> dict:
    trigger = {"type": "MANUAL", "manual_entry_ref": "adapter:trigger_job"}
    if cron:
        trigger = {"type": "CRON", "cron": cron}
    return normalize_job_definition_with_governance(
        {
            "platform_type": "xxl_job",
            "platform_job_id": "report-daily",
            "display_name": "report-daily",
            "service": "report-service",
            "handler": "ReportJob.handle",
            "actor_refs": ["actor-job-runner"],
            "trigger": trigger,
            "behavior": {
                "selection_predicates": [{"expression": "report_date = current_date"}],
                "object_refs": ["daily_report"],
                "read_set": ["daily_report.source_rows"],
                "write_set": [],
                "expected_effects": [
                    {"expression": "job reaches declared success terminal"}
                ],
            },
            "source_refs": [_code_source()],
        }
    )


def _model(operation: dict) -> dict:
    return {
        "operations": [operation],
        "actors": [
            {
                "actor_id": "actor-job-runner",
                "role": "job_runner",
                "account_ref": "job-runner-account",
                "credential_secret_ref": "secret://test/job-runner",
                "runtime_bound": True,
                "evidence": [_platform_source()],
            }
        ],
    }


def test_platform_and_code_views_merge_into_one_execution_ready_job_asset() -> None:
    platform_asset = _platform_job()
    code_asset = _code_job()
    assert platform_asset["job_asset_id"] != code_asset["job_asset_id"]

    merged = merge_cross_source_job_assets([platform_asset, code_asset])

    assert len(merged) == 1
    asset = merged[0]
    assert asset["platform_job_id"] == "report-daily"
    assert asset["display_name"] == "日报聚合"
    assert set(asset["display_name_variants"]) == {"日报聚合", "report-daily"}
    assert asset["display_name_conflict_policy"] == "NON_AUTHORITATIVE_VARIANT"
    assert asset["identity"]["handler"] == "ReportJob.handle"
    assert asset["identity"]["service"] == "report-service"
    assert asset["connector_id"] == "conn-job"
    assert asset["runtime"]["success_states"] == ["SUCCESS"]
    assert asset["testability"]["execution_status"] == "EXECUTION_READY"
    assert set(asset["evidence_channels"]) == {"JOB_PLATFORM", "SOURCE_CODE"}
    assert asset["source_fact_conflicts"] == []
    assert asset["fact_authority"]["implementation_confirmation_basis"] == (
        "CROSS_SOURCE_IMPLEMENTATION_EVIDENCE"
    )
    assert len(asset["merged_source_job_asset_ids"]) == 2


def test_automatically_merged_job_projects_to_confirmed_existing_behavior() -> None:
    asset = merge_cross_source_job_assets([_platform_job(), _code_job()])[0]
    operation = to_async_operation_with_governance(asset)

    behaviors, ledger, lineages, gate = project_job_behaviors(
        {"job_assets": [asset]},
        _model(operation),
    )

    assert len(behaviors) == 1
    behavior = behaviors[0]
    assert behavior["schema"] == "qualibug.enterprise-business-behavior.v1"
    assert behavior["status"] == "CONFIRMED"
    assert behavior["candidate_only"] is False
    assert behavior["formal_business_rule"] is True
    assert behavior["formal_business_finding_eligible"] is False
    assert ledger[0]["formal_obligation_eligible"] is True
    assert lineages[0]["identity_complete"] is True
    assert gate["status"] == "PASS"


def test_conflicting_cron_values_are_retained_and_block_automatic_promotion() -> None:
    merged = merge_cross_source_job_assets(
        [_platform_job(cron="0 0 2 * * ?"), _code_job(cron="0 0 3 * * ?")]
    )

    assert len(merged) == 1
    asset = merged[0]
    assert any(
        row.get("field") == "trigger.cron"
        for row in asset["source_fact_conflicts"]
    )
    assert asset["fact_authority"]["implementation_confirmation_basis"] == (
        "CONFLICTED_SOURCE_EVIDENCE"
    )
    assert asset["fact_authority"]["runtime_integrity_behavior_eligible"] is False
