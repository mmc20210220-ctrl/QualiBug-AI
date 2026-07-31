from pathlib import Path

import ai_test_asset_center.experiment_outcome_finalizer as finalizer
from ai_test_asset_center.process_step_execution import ProcessStepLedger
from ai_test_asset_center.process_step_semantic_view import ProcessStepSemanticView


ROOT = Path(__file__).resolve().parents[1]


def _ledger() -> ProcessStepLedger:
    ledger = ProcessStepLedger(
        experiment_id="experiment-1",
        fixture_id="fixture-1",
        campaign_id="campaign-1",
        run_id="run-1",
        obligation_id="obligation-1",
        protocol_id="protocol-1",
        required_step_ids=["step-1", "step-2"],
    )
    for ordinal in (1, 2):
        ledger.record_step_execution(
            step_id=f"step-{ordinal}",
            phase="treatment",
            operation_ref=f"operation-{ordinal}",
            actor_ref="actor-1",
            request_receipt_id=f"request-{ordinal}",
            response_receipt_id=f"response-{ordinal}",
            status_code=200,
            final_status="EXECUTED",
            mutation_occurred=True,
        )
    return ledger


def _observations() -> dict:
    return {
        "process_step_ledger": _ledger(),
        "observer_receipts": [
            {
                "receipt_id": "observation-1",
                "step_id": "step-1",
                "target_reached": True,
            },
            {
                "receipt_id": "observation-2",
                "step_id": "step-2",
                "target_reached": True,
            },
        ],
        "oracle_invocation_receipts": [
            {"receipt_id": "oracle-1", "step_id": "step-1"},
            {"receipt_id": "oracle-2", "step_id": "step-2"},
        ],
        "cleanup_execution_receipts": [
            {"receipt_id": "cleanup-1", "step_id": "step-1"},
            {"receipt_id": "cleanup-2", "step_id": "step-2"},
        ],
    }


def test_public_finalizer_file_contains_no_legacy_broadcast_loop() -> None:
    source = (
        ROOT / "ai_test_asset_center/experiment_outcome_finalizer.py"
    ).read_text(encoding="utf-8")

    assert "_targets = [_osid] if _osid else list(_executed_steps)" not in source
    assert "for _sid in _executed_steps" not in source
    assert "_write_steps or list(_executed_steps)" not in source


def test_legacy_implementation_is_moved_not_duplicated() -> None:
    facade = ROOT / "ai_test_asset_center/experiment_outcome_finalizer.py"
    core = ROOT / "ai_test_asset_center/experiment_outcome_finalizer_core.py"

    assert facade.exists()
    assert core.exists()
    assert facade.stat().st_size < core.stat().st_size
    assert "from . import experiment_outcome_finalizer_core as _core" in facade.read_text(
        encoding="utf-8"
    )


def test_exact_scope_proxy_hides_only_legacy_append_api() -> None:
    ledger = _ledger()
    proxy = finalizer._ExactScopeFinalizerLedger(ledger)

    assert hasattr(proxy, "all_rows")
    assert hasattr(proxy, "append_scoped_receipt_ref")
    assert not hasattr(proxy, "append_receipt_ref")


def test_observer_adapter_merges_existing_exact_receipts(monkeypatch) -> None:
    observations = {
        "observation_receipts": [
            {"receipt_id": "existing", "step_id": "step-2"}
        ],
        "process_step_observation_receipts": [
            {"receipt_id": "process", "step_id": "step-3"}
        ],
    }

    monkeypatch.setattr(
        finalizer,
        "_original_observe_experiment_requirements",
        lambda *args, **kwargs: [
            {"receipt_id": "generated", "step_id": "step-1"},
            {"receipt_id": "existing", "step_id": "step-2"},
        ],
    )

    merged = finalizer._observe_experiment_requirements_exact(
        {}, observations=observations
    )

    assert [row["receipt_id"] for row in merged] == [
        "generated",
        "existing",
        "process",
    ]
    assert observations["observer_receipts"] == merged


def test_oracle_adapter_publishes_verdict_for_semantic_sync(monkeypatch) -> None:
    observations: dict = {}
    verdict = {
        "receipt_id": "oracle-1",
        "step_id": "step-1",
        "target_reached": True,
    }
    monkeypatch.setattr(
        finalizer,
        "_original_evaluate_contract_oracle",
        lambda *args, **kwargs: verdict,
    )

    result = finalizer._evaluate_contract_oracle_exact(
        experiment={}, evidence=observations
    )

    assert result is verdict
    assert observations["oracle_verdict"] is verdict


def test_facade_seals_exact_scope_and_restores_semantic_view(monkeypatch) -> None:
    observations = _observations()
    captured: dict = {}

    def fake_core(*args, **kwargs):
        proxy = kwargs["observations"]["process_step_ledger"]
        captured["proxy"] = proxy
        captured["rows"] = proxy.all_rows()
        assert not hasattr(proxy, "append_receipt_ref")
        return {"status": "EXECUTED"}

    monkeypatch.setattr(finalizer._core, "finalize_experiment_execution", fake_core)

    result = finalizer.finalize_experiment_execution(observations=observations)

    assert result == {"status": "EXECUTED"}
    assert isinstance(captured["proxy"], finalizer._ExactScopeFinalizerLedger)
    assert isinstance(observations["process_step_ledger"], ProcessStepSemanticView)
    assert observations["process_step_ledger_view"] == "semantic_completion"

    rows = {row["step_id"]: row for row in captured["rows"]}
    assert rows["step-1"]["scoped_observation_receipt_ids"] == ["observation-1"]
    assert rows["step-2"]["scoped_observation_receipt_ids"] == ["observation-2"]
    assert rows["step-1"]["scoped_oracle_receipt_ids"] == ["oracle-1"]
    assert rows["step-2"]["scoped_oracle_receipt_ids"] == ["oracle-2"]
    assert rows["step-1"]["scoped_cleanup_receipt_ids"] == ["cleanup-1"]
    assert rows["step-2"]["scoped_cleanup_receipt_ids"] == ["cleanup-2"]
