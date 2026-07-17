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

    assert main_lines < 1700
    assert patch_lines < 500
