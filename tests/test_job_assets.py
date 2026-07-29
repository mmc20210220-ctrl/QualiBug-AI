from __future__ import annotations

from ai_test_asset_center.enterprise_knowledge_center._job_assets import (
    discover_job_definitions_from_text,
    enrich_job_assets,
)
from ai_test_asset_center.job_platform_contract import (
    ASYNC_OPERATION_KIND,
    normalize_job_definition,
    to_async_operation,
)


def _evidence() -> list[dict]:
    return [
        {
            "source_id": "source:job",
            "source_locator": "CloseOrderJob.java#L10-L40",
            "quote": "@XxlJob closeExpiredOrders",
        }
    ]


def _complete_definition() -> dict:
    return {
        "platform_type": "xxl_job",
        "platform_job_id": "close_expired_orders",
        "display_name": "关闭超时订单",
        "handler": "closeExpiredOrders",
        "trigger": {"type": "MANUAL", "manual_entry_ref": "/job/run"},
        "runtime": {
            "run_identity_ref": "$.job_run_id",
            "status_query_ref": "/job/runs/{job_run_id}",
            "step_query_ref": "/job/runs/{job_run_id}/steps",
            "terminal_states": ["SUCCESS", "FAILED"],
        },
        "behavior": {
            "object_refs": ["order", "inventory"],
            "selection_predicates": [
                {"expression": "order.status == PENDING_PAYMENT"},
                {"expression": "order.created_at <= now - 30m"},
            ],
            "process_steps": [
                {
                    "name": "release inventory",
                    "write_set": ["inventory.available_qty", "inventory.reserved_qty"],
                },
                {
                    "name": "cancel order",
                    "write_set": ["order.status"],
                },
            ],
            "read_set": ["order.status", "inventory.available_qty"],
            "write_set": [
                "order.status",
                "inventory.available_qty",
                "inventory.reserved_qty",
            ],
            "expected_effects": [
                {"expression": "order.status_after == CANCELLED"},
            ],
        },
        "cleanup": {
            "mode": "RESTORE",
            "cleanup_ref": "/test-fixtures/{fixture_id}/restore",
            "verification_ref": "/test-fixtures/{fixture_id}",
        },
        "source_refs": _evidence(),
    }


def test_job_definition_requires_source_identity_and_evidence() -> None:
    try:
        normalize_job_definition({"platform_job_id": ""}, source_refs=[])
    except ValueError as exc:
        assert str(exc) == "job_definition_id_missing"
    else:
        raise AssertionError("missing Job identity must fail closed")

    try:
        normalize_job_definition({"platform_job_id": "job-1"}, source_refs=[])
    except ValueError as exc:
        assert str(exc) == "job_definition_source_evidence_missing"
    else:
        raise AssertionError("source-free Job definition must fail closed")


def test_complete_source_backed_job_becomes_execution_ready() -> None:
    asset = normalize_job_definition(_complete_definition())
    testability = asset["testability"]

    assert asset["schema"] == "qualibug.job-asset.v1"
    assert testability["execution_status"] == "EXECUTION_READY"
    assert testability["safety_level"] == "REVERSIBLE_WRITE"
    assert testability["trigger_ready"] is True
    assert testability["identity_ready"] is True
    assert testability["cleanup_ready"] is True
    assert asset["customer_effort"]["manual_job_creation_required"] is False


def test_write_job_without_cleanup_is_unsafe_not_auto_repaired() -> None:
    definition = _complete_definition()
    definition["cleanup"] = {}
    asset = normalize_job_definition(definition)

    assert asset["testability"]["execution_status"] == "UNSAFE"
    assert asset["testability"]["cleanup_ready"] is False
    assert (
        asset["testability"]["safety_level"]
        == "UNSAFE_FOR_AUTONOMOUS_EXECUTION"
    )


def test_job_asset_maps_into_existing_operation_schema() -> None:
    asset = normalize_job_definition(_complete_definition())
    operation = to_async_operation(asset)

    assert operation["schema"] == "qualibug.enterprise-business-operation.v1"
    assert operation["operation_kind"] == ASYNC_OPERATION_KIND
    assert operation["object_refs"] == ["inventory", "order"]
    assert operation["async_contract"]["job_asset_ref"] == asset["job_asset_id"]
    assert operation["async_contract"]["write_set"]


def test_static_discovery_extracts_declared_framework_entrypoints_only() -> None:
    text = """
    public class Jobs {
      @XxlJob("closeExpiredOrders")
      public void closeExpiredOrders() {}

      @Scheduled(cron = "0 */5 * * * ?")
      public void refreshReport() {}
    }
    """
    source = {
        "source_id": "source-code-1",
        "original_name": "Jobs.java",
    }
    definitions = discover_job_definitions_from_text(text, source=source)

    assert {row["platform_type"] for row in definitions} == {
        "xxl_job",
        "spring_scheduler",
    }
    assert all(row["source_refs"] for row in definitions)
    assert all(row["behavior"]["expected_effects"] == [] for row in definitions)


def test_enrichment_merges_async_operation_without_parallel_model(tmp_path) -> None:
    existing_operation = {
        "schema": "qualibug.enterprise-business-operation.v1",
        "operation_id": "business_operation:existing",
        "name": "查询订单",
        "evidence": [{"source_id": "source:api"}],
    }
    asset = {
        "summary": {},
        "coverage_gaps": [],
        "source_inventory": [],
        "enterprise_understanding_model": {
            "schema": "qualibug.enterprise-business-understanding-model.v1",
            "operations": [existing_operation],
            "metrics": {},
        },
    }
    enriched = enrich_job_assets(
        asset,
        project_id="job-test",
        root=tmp_path,
        options={"job_definitions": [_complete_definition()]},
    )

    model_operations = enriched["enterprise_understanding_model"]["operations"]
    assert len(model_operations) == 2
    assert any(row.get("operation_kind") == ASYNC_OPERATION_KIND for row in model_operations)
    assert enriched["job_asset_summary"]["manual_job_editor_present"] is False
    assert (
        enriched["job_asset_summary"]["customer_effort_contract"][
            "manual_job_creation"
        ]
        == 0
    )


def test_missing_runtime_contract_only_blocks_dependent_job_capability(tmp_path) -> None:
    raw = {
        "platform_type": "spring_scheduler",
        "platform_job_id": "source:refreshReport",
        "display_name": "refreshReport",
        "trigger": {"type": "CRON", "cron": "0 */5 * * * ?"},
        "source_refs": _evidence(),
    }
    asset = {
        "summary": {},
        "coverage_gaps": [{"kind": "OTHER_EXISTING_GAP"}],
        "source_inventory": [],
        "enterprise_understanding_model": {"operations": [], "metrics": {}},
    }
    enriched = enrich_job_assets(
        asset,
        project_id="job-test",
        root=tmp_path,
        options={"job_definitions": [raw]},
    )

    kinds = {row["kind"] for row in enriched["coverage_gaps"]}
    assert "OTHER_EXISTING_GAP" in kinds
    assert "JOB_RUN_IDENTITY_CONTRACT_UNRESOLVED" in kinds
    assert "JOB_FIXTURE_CONTRACT_UNRESOLVED" in kinds
    assert enriched["job_assets"][0]["testability"]["execution_status"] == "PARTIALLY_EXECUTABLE"
