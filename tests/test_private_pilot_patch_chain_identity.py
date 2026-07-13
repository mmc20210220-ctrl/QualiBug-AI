from __future__ import annotations

import pytest


def _runtime_callable_snapshot() -> dict[str, object]:
    from ai_test_asset_center import __main__ as scanner
    from ai_test_asset_center import business_state_graph
    from ai_test_asset_center import display_ready_formatter
    from ai_test_asset_center import oracle_engine
    from ai_test_asset_center import private_pilot_service
    from ai_test_asset_center import regression_runner
    from ai_test_asset_center import regression_suite_builder
    from ai_test_asset_center import semantic_scenario_generator
    from ai_test_asset_center import v12_pipeline

    handler = private_pilot_service.PrivatePilotHandler
    return {
        "scan": scanner.scan,
        "partition": private_pilot_service._partition_delivery_tracks,
        "normalizer": private_pilot_service._normalize_command_center_envelope,
        "continuous_loop": private_pilot_service._continuous_scan_loop,
        "handle_scan": handler._handle_v12_scan,
        "handle_continuous": handler._handle_continuous_start,
        "handle_get_credentials": handler._handle_get_service_credentials,
        "handle_save_credentials": handler._handle_save_service_credentials,
        "render_report": handler._render_report_html,
        "do_get": handler.do_GET,
        "run_v12": v12_pipeline.run_v12_pipeline,
        "schedule_slices": v12_pipeline._schedule_behavior_slices,
        "confirmed_oracle": v12_pipeline._confirmed_oracle_finding,
        "graph_build": business_state_graph.BusinessStateGraphBuilder.build,
        "graph_contract": business_state_graph.BusinessStateGraphBuilder.behavior_contract,
        "scenario_invariant": semantic_scenario_generator.SemanticScenarioGenerator._invariant_from_meta,
        "oracle_evaluate": oracle_engine.OracleEngine.evaluate,
        "evidence_build": oracle_engine.EvidenceGraphBuilder.build,
        "regression_judge": regression_runner._judge_probe,
        "regression_reverify": regression_runner._reverify_confirmed_findings,
        "regression_history": regression_runner._append_regression_history,
        "regression_loader": regression_suite_builder._load_confirmed_findings_regression_probes,
        "regression_normalize": regression_suite_builder._normalize_probe,
        "format_details": display_ready_formatter._build_technical_details,
        "format_finding": display_ready_formatter._format_single_finding,
    }


def test_runtime_patch_chain_is_idempotent_and_exactly_restorable() -> None:
    from ai_test_asset_center.private_pilot_entrypoint import (
        install_runtime_patches,
        restore_deployment_contract_patch,
        runtime_patch_chain_status,
    )

    restore_deployment_contract_patch()
    baseline = _runtime_callable_snapshot()

    install_runtime_patches()
    installed_once = _runtime_callable_snapshot()
    assert installed_once != baseline
    assert runtime_patch_chain_status()["patched"] is True
    assert runtime_patch_chain_status()["drifted_callables"] == []

    install_runtime_patches()
    assert _runtime_callable_snapshot() == installed_once

    restore_deployment_contract_patch()
    assert _runtime_callable_snapshot() == baseline
    assert runtime_patch_chain_status()["patched"] is False

    install_runtime_patches()
    restore_deployment_contract_patch()
    assert _runtime_callable_snapshot() == baseline


def test_runtime_patch_install_failure_rolls_back_exact_callable_identity(
    monkeypatch,
) -> None:
    import ai_test_asset_center.private_pilot_entrypoint as entrypoint

    entrypoint.restore_deployment_contract_patch()
    baseline = _runtime_callable_snapshot()

    def fail_install(*_args, **_kwargs):
        raise RuntimeError("injected-regression-oracle-install-failure")

    monkeypatch.setattr(entrypoint, "install_regression_oracle_patch", fail_install)
    with pytest.raises(
        RuntimeError,
        match="injected-regression-oracle-install-failure",
    ):
        entrypoint.install_runtime_patches()

    assert _runtime_callable_snapshot() == baseline
    status = entrypoint.runtime_patch_chain_status()
    assert status["patched"] is False
    assert status["declared_installed"] is False
