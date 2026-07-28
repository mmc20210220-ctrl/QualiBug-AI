from __future__ import annotations

from ai_test_asset_center.adapter_capability import (
    observation_surfaces_for_adapters,
    resolve_available_adapters,
)
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    observe_experiment_requirements,
    validate_observer_receipt,
)


def test_process_ledger_is_a_product_owned_baseline_adapter(tmp_path) -> None:
    adapters = resolve_available_adapters(tmp_path, "observer-test")

    assert "http_api" in adapters
    assert "process_ledger" in adapters
    assert observation_surfaces_for_adapters(adapters)[
        "process_timeline"
    ] is True


def test_temporal_compilation_requires_non_http_process_timeline() -> None:
    from ai_test_asset_center import discovery_runtime  # noqa: F401
    from ai_test_asset_center import experiment_compiler_obligation

    compiled, reason, detail = (
        experiment_compiler_obligation.compile_observer_requirements(
            ["temporal_window"],
            risk_family="temporal",
            available_adapters={"http_api", "process_ledger"},
        )
    )

    assert reason == ""
    assert detail == ""
    assert [row["observer_id"] for row in compiled] == [
        "temporal_window",
        "process_timeline",
    ]
    assert OBSERVER_REGISTRY["process_timeline"] == {
        "surface": "process_timeline",
        "adapter": "process_ledger",
        "implemented": True,
        "evidence_keys": (
            "process_timeline",
            "step_timestamps",
            "required_steps_executed",
        ),
        "registered_at_runtime": True,
    }


def test_process_timeline_emits_content_addressed_formal_receipt() -> None:
    from ai_test_asset_center import discovery_runtime  # noqa: F401

    receipts = observe_experiment_requirements(
        {
            "experiment_id": "exp_process_timeline",
            "campaign_id": "campaign_process_timeline",
            "execution_id": "execution_process_timeline",
            "observers": [{"observer_id": "process_timeline"}],
            "assertions": [],
        },
        observations={
            "process_step_ledger_id": "psl_123",
            "process_step_ledger_hash": "a" * 64,
            "required_step_ids": ["step_1"],
            "planned_step_ids": ["step_1"],
            "executed_step_ids": ["step_1"],
            "transport_receipt_ids": ["transport_1"],
            "process_timeline": [{
                "step_id": "step_1",
                "event_type": "STEP_COMPLETED",
                "occurred_at": 123.5,
                "receipt_id": "transport_1",
            }],
        },
        campaign_id="campaign_process_timeline",
        execution_id="execution_process_timeline",
    )

    assert len(receipts) == 1
    receipt = validate_observer_receipt(receipts[0])
    assert receipt["observer_id"] == "process_timeline"
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["timeline_event_count"] == 1
    assert receipt["evidence"]["required_steps_executed"] is True


def test_process_timeline_without_real_events_is_indeterminate() -> None:
    from ai_test_asset_center import discovery_runtime  # noqa: F401

    receipt = observe_experiment_requirements(
        {
            "experiment_id": "exp_missing_timeline",
            "observers": [{"observer_id": "process_timeline"}],
            "assertions": [],
        },
        observations={},
    )[0]

    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "PROCESS_TIMELINE_MISSING"
