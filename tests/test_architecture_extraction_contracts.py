from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_product_scan_mainline_is_reexported_from_canonical_scan_entrypoint() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import product_scan_mainline as binding

    assert scan_module._bind_discovery_mainline_identity is (
        binding._bind_discovery_mainline_identity
    )
    assert scan_module._apply_scan_execution_defaults is (
        binding._apply_scan_execution_defaults
    )
    assert scan_module.CanonicalProductScopeError is binding.CanonicalProductScopeError


def test_system_behavior_space_patch_imports_extracted_scenario_enricher() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    enricher_source = (
        ROOT
        / "ai_test_asset_center"
        / "system_behavior_space_scenario_enricher.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_scenario_enricher" in patch_source
    assert "def _enrich_system_behavior_scenario(" in enricher_source
    assert "def _enrich_system_behavior_scenario(" not in patch_source
    assert "register_scenario_enricher" in patch_source


def test_system_behavior_space_patch_imports_extracted_oracle_helpers() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    oracle_source = (
        ROOT / "ai_test_asset_center" / "system_behavior_space_oracle.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_oracle" in patch_source
    assert "def _direct_system_promise_oracle_result(" in oracle_source
    assert "def _direct_system_promise_oracle_result(" not in patch_source
    assert "register_oracle_evaluate_hook" in patch_source


def test_system_behavior_space_patch_imports_extracted_delivery_helpers() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    delivery_source = (
        ROOT / "ai_test_asset_center" / "system_behavior_space_delivery.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_delivery" in patch_source
    assert "def _attach_system_behavior_to_finding(" in delivery_source
    assert "def _attach_system_behavior_to_finding(" not in patch_source
    assert "register_finding_enricher" in patch_source


def test_commercial_assets_are_reexported_from_canonical_scan_entrypoint() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import scan_commercial_assets as commercial

    assert scan_module._materialize_commercial_assets is (
        commercial._materialize_commercial_assets
    )
    assert scan_module._materialize_external_commercial_assets is (
        commercial._materialize_external_commercial_assets
    )


def test_ui_and_external_followup_assets_are_reexported() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import scan_external_reproduction_assets as external
    from ai_test_asset_center import scan_ui_followup_assets as ui

    assert scan_module._materialize_ui_followup_assets is (
        ui._materialize_ui_followup_assets
    )
    assert scan_module._materialize_external_reproduction_assets is (
        external._materialize_external_reproduction_assets
    )


def test_system_behavior_space_patch_imports_extracted_slice_helpers() -> None:
    patch_source = (
        ROOT
        / "ai_test_asset_center"
        / "private_pilot_system_behavior_space_patch.py"
    ).read_text(encoding="utf-8")
    slices_source = (
        ROOT / "ai_test_asset_center" / "system_behavior_space_slices.py"
    ).read_text(encoding="utf-8")

    assert "system_behavior_space_slices" in patch_source
    assert "def _attach_system_behavior_slices(" in slices_source
    assert "def _attach_system_behavior_slices(" not in patch_source
    assert "register_bsg_build_hook" in patch_source


def test_source_finding_and_ui_verification_helpers_are_reexported() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import scan_finding_postprocess as findings
    from ai_test_asset_center import scan_source_runtime as source
    from ai_test_asset_center import scan_ui_candidate_verification as ui_verify

    assert scan_module._source_manifest is source._source_manifest
    assert scan_module._runtime_contract is source._runtime_contract
    assert scan_module._classify_findings is findings._classify_findings
    assert scan_module._verify_ui_candidate_findings is (
        ui_verify._verify_ui_candidate_findings
    )


def test_scan_outcome_and_cleanup_helpers_are_reexported() -> None:
    from ai_test_asset_center import __main__ as scan_module
    from ai_test_asset_center import experiment_cleanup as cleanup
    from ai_test_asset_center import experiment_executor as executor
    from ai_test_asset_center import experiment_fixture_materializer as fixtures
    from ai_test_asset_center import experiment_runtime_support as runtime
    from ai_test_asset_center import scan_customer_ready_artifacts as ready
    from ai_test_asset_center import scan_execution_outcome as outcome

    assert scan_module._persist_customer_ready_static_artifacts is (
        ready._persist_customer_ready_static_artifacts
    )
    assert scan_module._blocked_result is outcome._blocked_result
    assert scan_module._apply_coverage_honesty_guard is (
        outcome._apply_coverage_honesty_guard
    )
    assert executor._cleanup_restores_governed_write is (
        cleanup._cleanup_restores_governed_write
    )
    assert executor.preflight_experiment_executable is (
        runtime.preflight_experiment_executable
    )
    assert executor._run_http_step is runtime._run_http_step
    assert executor.load_actor_tokens is runtime.load_actor_tokens
    assert executor.materialize_experiment_fixtures is (
        fixtures.materialize_experiment_fixtures
    )
    from ai_test_asset_center import scan_impl_prepare as prepare

    assert scan_module.prepare_scan_before_pipeline is (
        prepare.prepare_scan_before_pipeline
    )


def test_extracted_modules_remain_under_architecture_budget_threshold() -> None:
    main_lines = sum(
        1 for _ in open(ROOT / "ai_test_asset_center" / "__main__.py", encoding="utf-8")
    )
    patch_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "private_pilot_system_behavior_space_patch.py",
            encoding="utf-8",
        )
    )
    executor_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_executor.py",
            encoding="utf-8",
        )
    )
    support_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_runtime_support.py",
            encoding="utf-8",
        )
    )
    prepare_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "scan_impl_prepare.py",
            encoding="utf-8",
        )
    )
    fixture_lines = sum(
        1
        for _ in open(
            ROOT / "ai_test_asset_center" / "experiment_fixture_materializer.py",
            encoding="utf-8",
        )
    )

    assert main_lines < 1000
    assert patch_lines < 500
    assert executor_lines < 2800
    assert support_lines < 700
    assert prepare_lines < 350
    assert fixture_lines < 600
