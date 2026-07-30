from __future__ import annotations

from ai_test_asset_center.database_observer_experiment_runtime import (
    PHASE_AGGREGATE_OBSERVER_ID,
    install_experiment_database_observer,
)
from ai_test_asset_center.database_observer_runtime import (
    OBSERVER_ID as DIRECT_READBACK_OBSERVER_ID,
    install_approved_database_observer,
)
from ai_test_asset_center.observer_contracts_base import (
    OBSERVER_REGISTRY,
    _REGISTERED_OBSERVER_HANDLERS,
    observe_experiment_requirements,
    validate_observer_declarations,
)


def _draft() -> dict:
    return {
        "schema": "qualibug.database-observer-execution-draft.v1",
        "draft_id": "draft:orders:after",
        "observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "required": True,
    }


def _phase_receipt() -> dict:
    return {
        "receipt_id": "direct-readback-receipt",
        "observer_id": DIRECT_READBACK_OBSERVER_ID,
        "status": "OBSERVED",
        "reason_code": "",
        "draft_id": "draft:orders:after",
        "observer_contract_ref": "observer:orders",
        "observation_phase": "AFTER",
        "evidence": {
            "approved_database_snapshot": {
                "row_count": 1,
                "rows": [{"id": "o-1", "status": "PAID"}],
                "row_fingerprint": "row-fingerprint",
                "oracle_verdict_emitted": False,
            }
        },
    }


def test_direct_then_aggregate_registration_keeps_distinct_handlers() -> None:
    direct = install_approved_database_observer()
    aggregate = install_experiment_database_observer()

    assert direct == DIRECT_READBACK_OBSERVER_ID
    assert aggregate == PHASE_AGGREGATE_OBSERVER_ID
    assert direct != aggregate
    assert direct in _REGISTERED_OBSERVER_HANDLERS
    assert aggregate in _REGISTERED_OBSERVER_HANDLERS
    assert (
        _REGISTERED_OBSERVER_HANDLERS[direct]
        is not _REGISTERED_OBSERVER_HANDLERS[aggregate]
    )
    assert OBSERVER_REGISTRY[direct]["adapter"] == "db_sql"
    assert OBSERVER_REGISTRY[aggregate]["adapter"] == "db_sql"


def test_aggregate_dispatch_uses_phase_receipts_without_direct_runtime_inputs() -> None:
    install_approved_database_observer()
    install_experiment_database_observer()
    experiment = {
        "observers": [{"observer_id": PHASE_AGGREGATE_OBSERVER_ID}],
        "database_observer_execution_drafts": [_draft()],
    }

    receipts = observe_experiment_requirements(
        experiment,
        observations={
            "approved_database_observer_phase_receipts": [_phase_receipt()]
        },
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["observer_id"] == PHASE_AGGREGATE_OBSERVER_ID
    assert receipt["status"] == "OBSERVED"
    assert receipt["evidence"]["finalizer_database_requery_count"] == 0
    assert receipt["evidence"]["direct_query_fallback_allowed"] is False
    assert receipt["evidence"]["approved_database_snapshots"][0]["snapshot"][
        "rows"
    ] == [{"id": "o-1", "status": "PAID"}]


def test_aggregate_without_phase_drafts_never_falls_back_to_query() -> None:
    install_experiment_database_observer()
    receipts = observe_experiment_requirements(
        {"observers": [{"observer_id": PHASE_AGGREGATE_OBSERVER_ID}]},
        observations={},
        campaign_id="campaign-1",
        execution_id="execution-1",
    )

    assert len(receipts) == 1
    receipt = receipts[0]
    assert receipt["status"] == "INDETERMINATE"
    assert receipt["reason_code"] == "DATABASE_OBSERVER_PHASE_DRAFTS_MISSING"
    assert receipt["evidence"]["finalizer_database_requery_count"] == 0
    assert receipt["evidence"]["query_execution_count"] == 0
    assert receipt["evidence"]["direct_query_fallback_allowed"] is False


def test_phase_aggregate_requires_db_sql_compile_authority() -> None:
    install_experiment_database_observer()
    declared = [{"observer_id": PHASE_AGGREGATE_OBSERVER_ID}]

    reason, detail = validate_observer_declarations(
        declared,
        risk_family="business_integrity",
        available_adapters={"http_api"},
        require_authorization_comparison=False,
    )
    assert reason == "BLOCKED_UNSUPPORTED_ADAPTER"
    assert detail == "db_sql"

    reason, detail = validate_observer_declarations(
        declared,
        risk_family="business_integrity",
        available_adapters={"http_api", "db_sql"},
        require_authorization_comparison=False,
    )
    assert reason == ""
    assert detail == ""
