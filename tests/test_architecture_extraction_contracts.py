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

    assert main_lines < 5000
    assert patch_lines < 750
