from __future__ import annotations

import inspect
import sys
from pathlib import Path


def test_public_understanding_packages_load_after_legacy_file_renames() -> None:
    """The product mainline imports stable packages, never renamed implementation files."""
    from ai_test_asset_center.enterprise_knowledge_center import composition
    from ai_test_asset_center.enterprise_knowledge_center import (
        _chinese_business_comprehension as chinese_comprehension,
    )
    from ai_test_asset_center.enterprise_knowledge_center.enterprise_understanding import (
        builder,
        integration,
    )

    assert callable(chinese_comprehension.analyze_chinese_business_source)
    assert callable(chinese_comprehension.build_chinese_first_comprehension)
    assert callable(builder.build_enterprise_understanding_model)
    assert callable(integration._parsed_sources_for_context)
    assert callable(integration.enrich_asset_with_enterprise_understanding)

    assert (
        composition.enrich_asset_with_enterprise_understanding
        is integration.enrich_asset_with_enterprise_understanding
    )
    assert composition._parsed_sources_for_context is integration._parsed_sources_for_context

    # Importing each public package executes its compatibility loader. These modules
    # must therefore already exist and be loadable before the composition root runs.
    package_root = Path(composition.__file__).resolve().parent
    expected_files = {
        package_root / "_chinese_business_comprehension_extractor_v1.py",
        package_root / "enterprise_understanding" / "builder_legacy_v1.py",
        package_root / "enterprise_understanding" / "integration_legacy_v1.py",
    }
    assert all(path.is_file() for path in expected_files)
    assert any(
        name.endswith("._chinese_business_comprehension_extractor_v1")
        for name in sys.modules
    )
    assert any(name.endswith("._semantic_projection_builder_v1") for name in sys.modules)
    assert any(
        name.endswith("._enterprise_understanding_integration_v1")
        for name in sys.modules
    )


def test_product_authorities_depend_only_on_public_package_surfaces() -> None:
    from ai_test_asset_center.enterprise_knowledge_center import composition
    from benchmark_evaluator.enterprise_understanding import build_product_snapshot

    composition_source = inspect.getsource(composition)
    snapshot_source = inspect.getsource(build_product_snapshot)

    for private_filename in (
        "_chinese_business_comprehension_extractor_v1",
        "builder_legacy_v1",
        "integration_legacy_v1",
    ):
        assert private_filename not in composition_source
        assert private_filename not in snapshot_source

    assert "enterprise_understanding.integration" in composition_source
    assert "enterprise_knowledge_center.composition" in snapshot_source
