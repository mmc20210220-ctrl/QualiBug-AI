from __future__ import annotations

from ai_test_asset_center import experiment_batch_executor
from ai_test_asset_center import experiment_compiler  # noqa: F401 - installs formal bridge
from ai_test_asset_center.runtime_materialization_batch_lineage import (
    attach_batch_materialization_lineage,
)


def _experiment() -> dict:
    lineage = {
        "materialization_id": "runtime_materialization_ship_order",
        "runtime_plan_id": "runtime_plan_ship_order",
        "scenario_ir_id": "scenario_ir_ship_order",
        "execution_contract_id": "execution_contract_ship_order",
    }
    return {
        "experiment_id": "experiment_ship_order",
        "obligation_id": "obligation_ship_order",
        "runtime_materialization_contract": {
            "authority_fingerprint": "authority-fingerprint-ship-order",
            "authority": {
                "knowledge_asset_id": "knowledge_asset_customer",
                "behavior_ir_id": "behavior_ir_customer",
                "lineage": lineage,
            },
        },
    }


def test_batch_wrapper_is_installed_on_existing_execution_authority() -> None:
    assert getattr(
        experiment_batch_executor.execute_selected_experiments,
        "__qualibug_runtime_materialization_batch_lineage_v1__",
        False,
    ) is True


def test_parameter_binding_early_block_keeps_materialization_lineage() -> None:
    result = attach_batch_materialization_lineage(
        {
            "results": [
                {
                    "experiment_id": "experiment_ship_order",
                    "obligation_id": "obligation_ship_order",
                    "status": "BLOCKED",
                    "reason_code": "PARAMETER_BINDING_BLOCKED",
                    "execution_receipt": {
                        "status": "BLOCKED",
                        "reason_code": "PARAMETER_BINDING_BLOCKED",
                    },
                }
            ],
            "compile_results": {
                "obligation_ship_order": {
                    "status": "COMPILED",
                    "experiment_id": "experiment_ship_order",
                }
            },
            "execution_results": {
                "obligation_ship_order": {
                    "status": "BLOCKED",
                    "experiment_id": "experiment_ship_order",
                }
            },
            "gate_results": {},
            "findings": [],
        },
        experiments_by_obligation={
            "obligation_ship_order": _experiment(),
        },
    )

    outcome = result["results"][0]
    lineage = outcome["runtime_materialization_lineage"]
    assert lineage["materialization_id"] == "runtime_materialization_ship_order"
    assert lineage["runtime_plan_id"] == "runtime_plan_ship_order"
    assert outcome["execution_receipt"]["runtime_materialization_lineage"] == lineage
    assert result["compile_results"]["obligation_ship_order"][
        "runtime_materialization_lineage"
    ] == lineage
    assert result["execution_results"]["obligation_ship_order"][
        "runtime_materialization_lineage"
    ] == lineage


def test_delivery_and_observer_receipts_share_one_lineage() -> None:
    result = attach_batch_materialization_lineage(
        {
            "results": [
                {
                    "experiment_id": "experiment_ship_order",
                    "obligation_id": "obligation_ship_order",
                    "status": "EXECUTED",
                    "execution_receipt": {"status": "EXECUTED"},
                    "delivery_execution_receipt": {"status": "EXECUTED"},
                    "reproduction_receipt": {"status": "READY"},
                    "delivery_gate_receipt": {"status": "DELIVERABLE"},
                    "contract_evidence_receipts": [{"receipt_id": "contract-evidence-1"}],
                    "observer_receipts": [{"receipt_id": "observer-1"}],
                    "finding": {"finding_id": "finding-1"},
                }
            ],
            "findings": [
                {
                    "finding_id": "finding-1",
                    "experiment_id": "experiment_ship_order",
                    "obligation_id": "obligation_ship_order",
                }
            ],
            "compile_results": {},
            "execution_results": {},
            "gate_results": {},
        },
        experiments_by_obligation={
            "obligation_ship_order": _experiment(),
        },
    )

    outcome = result["results"][0]
    lineage = outcome["runtime_materialization_lineage"]
    assert outcome["delivery_execution_receipt"]["runtime_materialization_lineage"] == lineage
    assert outcome["reproduction_receipt"]["runtime_materialization_lineage"] == lineage
    assert outcome["delivery_gate_receipt"]["runtime_materialization_lineage"] == lineage
    assert outcome["contract_evidence_receipts"][0][
        "runtime_materialization_lineage"
    ] == lineage
    assert outcome["observer_receipts"][0]["runtime_materialization_lineage"] == lineage
    assert outcome["finding"]["runtime_materialization_lineage"] == lineage
    assert result["findings"][0]["runtime_materialization_lineage"] == lineage
