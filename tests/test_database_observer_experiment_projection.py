from __future__ import annotations

from ai_test_asset_center.database_observer_experiment_projection import (
    OBSERVER_ID,
    READBACK_HANDLER_ID,
    project_database_observers_to_experiment_pack,
)
from ai_test_asset_center.runtime_materialization_experiment_bridge import _CAPTURED_ASSET


def _draft(phase: str) -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": f"draft:orders:{phase.lower()}",
        "runtime_materialization_ref": "materialization:orders",
        "runtime_plan_ref": "plan:orders",
        "observer_handler_id": READBACK_HANDLER_ID,
        "observer_contract_ref": "observer:orders",
        "observation_phase": phase,
        "database_observer_contract": {
            "schema": "qualibug.database-observer-contract.v1",
            "observer_id": "observer:orders",
            "status": "READY_FOR_RUNTIME_CONNECTION_BINDING",
            "runtime_observer_authoritative": True,
            "read_only": True,
            "mutation_allowed": False,
            "write_target_allowed": False,
            "oracle_authority_allowed": False,
        },
        "required": True,
        "runtime_connection_bound": False,
        "query_executed": False,
        "oracle_verdict_emitted": False,
    }


def _pack() -> dict:
    return {
        "experiments": [
            {
                "experiment_id": "experiment:orders",
                "obligation_id": "obligation:orders",
                "compile_receipt": {"status": "COMPILED"},
                "runtime_materialization_contract": {
                    "materialization_id": "materialization:orders"
                },
                "observers": [{"observer_id": "business_effect"}],
            }
        ],
        "blocked_experiments": [],
        "block_reason_counts": {},
    }


def _capture(drafts: list[dict], declared_count: object | None = None) -> dict:
    return {
        "materializations": [
            {
                "materialization_id": "materialization:orders",
                "database_observer_execution_draft_count": (
                    len(drafts) if declared_count is None else declared_count
                ),
                "database_observer_execution_drafts": drafts,
            }
        ]
    }


def test_binds_only_exact_materialization_drafts_and_freezes_fingerprint() -> None:
    token = _CAPTURED_ASSET.set(_capture([_draft("BEFORE"), _draft("AFTER")]))
    try:
        result = project_database_observers_to_experiment_pack(_pack())
    finally:
        _CAPTURED_ASSET.reset(token)

    assert result["compiled_count"] == 1
    experiment = result["experiments"][0]
    assert [
        row["observation_phase"]
        for row in experiment["database_observer_execution_drafts"]
    ] == ["BEFORE", "AFTER"]
    assert experiment["database_observer_execution_draft_fingerprint"]
    assert experiment["database_observer_finalizer_must_not_requery"] is True
    observers = {row["observer_id"]: row for row in experiment["observers"]}
    assert set(observers) == {"business_effect", OBSERVER_ID}
    assert observers[OBSERVER_ID]["direct_readback_observer_id"] == READBACK_HANDLER_ID
    assert OBSERVER_ID != READBACK_HANDLER_ID
    receipt = experiment["compile_receipt"]
    assert receipt["database_observer_execution_draft_count"] == 2
    assert receipt["database_observer_phase_receipts_required"] is True
    assert receipt["database_observer_phase_aggregate_id"] == OBSERVER_ID
    assert receipt["database_observer_direct_readback_id"] == READBACK_HANDLER_ID
    assert receipt["database_observer_finalizer_requery_allowed"] is False
    projection = result["database_observer_experiment_projection"]
    assert projection["execution_draft_count"] == 2
    assert projection["phase_aggregate_observer_id"] == OBSERVER_ID
    assert projection["direct_readback_observer_id"] == READBACK_HANDLER_ID
    assert projection["observer_registration_order_affects_authority"] is False
    assert projection["runtime_query_execution_count"] == 0
    assert projection["second_compiler_created"] is False


def test_declared_draft_count_drift_blocks_experiment() -> None:
    token = _CAPTURED_ASSET.set(_capture([_draft("AFTER")], declared_count=2))
    try:
        result = project_database_observers_to_experiment_pack(_pack())
    finally:
        _CAPTURED_ASSET.reset(token)

    assert result["experiments"] == []
    assert result["blocked_count"] == 1
    blocked = result["blocked_experiments"][0]
    assert blocked["compile_receipt"]["reason_code"] == (
        "BLOCKED_APPROVED_DATABASE_OBSERVER_DRAFT_INVALID"
    )
    assert blocked["compile_receipt"]["database_observer_detail"] == {
        "materialization_id": "materialization:orders",
        "expected_draft_count": 2,
        "valid_draft_count": 1,
        "automatic_draft_recovery_allowed": False,
    }


def test_malformed_draft_count_and_lineage_fail_closed_without_throwing() -> None:
    pack = _pack()
    pack["experiments"][0]["runtime_materialization_contract"] = {
        "authority": {"lineage": "not-a-dict"},
        "materialization_id": "materialization:orders",
    }
    token = _CAPTURED_ASSET.set(
        _capture([_draft("AFTER")], declared_count="not-an-integer")
    )
    try:
        result = project_database_observers_to_experiment_pack(pack)
    finally:
        _CAPTURED_ASSET.reset(token)

    assert result["experiments"] == []
    detail = result["blocked_experiments"][0]["compile_receipt"][
        "database_observer_detail"
    ]
    assert detail["expected_draft_count"] is None
    assert detail["valid_draft_count"] == 1


def test_materialization_without_database_drafts_is_not_modified() -> None:
    token = _CAPTURED_ASSET.set(_capture([]))
    try:
        result = project_database_observers_to_experiment_pack(_pack())
    finally:
        _CAPTURED_ASSET.reset(token)

    experiment = result["experiments"][0]
    assert experiment["database_observer_projection_status"] == "NOT_APPLICABLE"
    assert "database_observer_execution_drafts" not in experiment
    assert {row["observer_id"] for row in experiment["observers"]} == {
        "business_effect"
    }
